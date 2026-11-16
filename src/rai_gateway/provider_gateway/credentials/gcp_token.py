"""Short-lived Google OAuth access tokens for the Vertex AI upstream.

Sources, in order: inline service-account JSON, service-account key file, then the
GCE/GKE metadata server. Tokens are cached in-process until shortly before expiry.
The service-account key never leaves the gateway.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from jose import jwt

from rai_gateway.core.config import settings

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
_METADATA_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "service-accounts/default/token"
)
_REFRESH_SKEW_SECONDS = 120


class GcpTokenError(Exception):
    """No usable Google credential or the token exchange failed."""


class GcpAccessTokenProvider:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._by_account: dict[str, tuple[str, float]] = {}

    def get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._expires_at - _REFRESH_SKEW_SECONDS:
                return self._token
            info = self._service_account_info()
            if info is not None:
                token, ttl = self._exchange_service_account(info)
            else:
                token, ttl = self._from_metadata_server()
            self._token = token
            self._expires_at = time.time() + ttl
            return token

    def get_token_for_info(self, info: dict[str, Any]) -> str:
        """Token for an explicit service-account key (cached per client_email)."""
        email = str(info.get("client_email") or "")
        with self._lock:
            cached = self._by_account.get(email)
            if cached and time.time() < cached[1] - _REFRESH_SKEW_SECONDS:
                return cached[0]
            token, ttl = self._exchange_service_account(info)
            self._by_account[email] = (token, time.time() + ttl)
            return token

    def reset(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    @staticmethod
    def _service_account_info() -> dict[str, Any] | None:
        raw = (settings.gcp_service_account_json or "").strip()
        if raw:
            try:
                return json.loads(raw)
            except ValueError as exc:
                raise GcpTokenError("GCP_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
        path = (settings.google_application_credentials or "").strip()
        if path:
            try:
                return json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise GcpTokenError(
                    "GOOGLE_APPLICATION_CREDENTIALS is unreadable or invalid"
                ) from exc
        return None

    @staticmethod
    def _exchange_service_account(info: dict[str, Any]) -> tuple[str, float]:
        email = info.get("client_email")
        key = info.get("private_key")
        if not email or not key:
            raise GcpTokenError("Service account JSON lacks client_email/private_key")
        token_uri = info.get("token_uri") or _DEFAULT_TOKEN_URI
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": email,
                "scope": _SCOPE,
                "aud": token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            key,
            algorithm="RS256",
            headers={"kid": info["private_key_id"]} if info.get("private_key_id") else None,
        )
        try:
            resp = httpx.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise GcpTokenError(f"Token exchange failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise GcpTokenError(f"Token exchange rejected (HTTP {resp.status_code})")
        body = resp.json()
        return str(body["access_token"]), float(body.get("expires_in", 3600))

    @staticmethod
    def _from_metadata_server() -> tuple[str, float]:
        try:
            resp = httpx.get(
                _METADATA_URL, headers={"Metadata-Flavor": "Google"}, timeout=2.0
            )
        except httpx.HTTPError as exc:
            raise GcpTokenError(
                "No Google credentials: set GCP_SERVICE_ACCOUNT_JSON or "
                "GOOGLE_APPLICATION_CREDENTIALS (metadata server unreachable)"
            ) from exc
        if resp.status_code != 200:
            raise GcpTokenError(f"Metadata server returned HTTP {resp.status_code}")
        body = resp.json()
        return str(body["access_token"]), float(body.get("expires_in", 3600))


gcp_token_provider = GcpAccessTokenProvider()
