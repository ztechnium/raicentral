"""OAuth authorization server for the RAI management MCP server."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy.orm import Session

from rai_gateway.auth.service import login_with_password, refresh_session
from rai_gateway.auth.tokens import hash_token
from rai_gateway.core.config import settings
from rai_gateway.core.database import SessionLocal
from rai_gateway.core.errors import RaiException

MCP_SCOPE_READ = "rai:read"
MCP_SCOPE_MANAGE = "rai:manage"
DEFAULT_MCP_SCOPES = (MCP_SCOPE_READ, MCP_SCOPE_MANAGE)

_CODE_TTL_SECONDS = 600
_REFRESH_TTL_SECONDS = settings.auth_refresh_token_days * 24 * 3600


@dataclass
class PendingAuthorization:
    client_id: str
    params: AuthorizationParams
    created_at: float = field(default_factory=time.time)


# The SDK token types are Pydantic models: extend them as models, not dataclasses.
class StoredAuthorizationCode(AuthorizationCode):
    access_token_raw: str
    refresh_token_raw: str


class StoredRefreshToken(RefreshToken):
    token_hash: str


class StoredAccessToken(AccessToken):
    pass


class RaiMcpOAuthProvider(
    OAuthAuthorizationServerProvider[
        StoredAuthorizationCode, StoredRefreshToken, StoredAccessToken
    ]
):
    """In-memory OAuth AS that issues the same user JWTs as /v1/auth/login."""

    def __init__(self, *, issuer_base: str, resource_url: str) -> None:
        self._issuer_base = issuer_base.rstrip("/")
        self._resource_url = resource_url
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._pending: dict[str, PendingAuthorization] = {}
        self._codes: dict[str, StoredAuthorizationCode] = {}
        self._refresh: dict[str, StoredRefreshToken] = {}
        self._access_by_token: dict[str, StoredAccessToken] = {}

    def login_path(self) -> str:
        return f"{settings.mcp_server_mount_path.rstrip('/')}/oauth/login"

    def _valid_scopes(self) -> list[str]:
        return [s.strip() for s in settings.mcp_oauth_scopes.split(",") if s.strip()]

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="client_id is required",
            )
        if not client_info.redirect_uris:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="At least one redirect_uri is required",
            )
        stored = client_info.model_copy(
            update={
                "client_id": client_info.client_id or secrets.token_urlsafe(16),
                "client_secret": client_info.client_secret or secrets.token_urlsafe(32),
                "client_id_issued_at": int(time.time()),
                "grant_types": client_info.grant_types
                or [
                    "authorization_code",
                    "refresh_token",
                ],
                "token_endpoint_auth_method": (
                    client_info.token_endpoint_auth_method or "client_secret_post"
                ),
            }
        )
        self._clients[stored.client_id] = stored

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pending_id = secrets.token_urlsafe(24)
        self._pending[pending_id] = PendingAuthorization(
            client_id=client.client_id,
            params=params,
        )
        query = urlencode({"pending_id": pending_id})
        return f"{self._issuer_base}{self.login_path()}?{query}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> StoredAuthorizationCode | None:
        stored = self._codes.get(authorization_code)
        if stored is None or stored.client_id != client.client_id:
            return None
        return stored

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: StoredAuthorizationCode,
    ) -> OAuthToken:
        self._codes.pop(authorization_code.code, None)
        expires_in = settings.auth_access_token_minutes * 60
        return OAuthToken(
            access_token=authorization_code.access_token_raw,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=authorization_code.refresh_token_raw,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> StoredRefreshToken | None:
        stored = self._refresh.get(hash_token(refresh_token))
        if stored is None or stored.client_id != client.client_id:
            return None
        return stored

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: StoredRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        db: Session = SessionLocal()
        try:
            # Rotates the underlying session, so logout, password changes and
            # deactivation end MCP access exactly as they do for the console.
            body = refresh_session(db, refresh_token=refresh_token.token)
        except RaiException as exc:
            raise TokenError(
                error="invalid_grant", error_description="invalid refresh token"
            ) from exc
        finally:
            db.close()

        self._refresh.pop(refresh_token.token_hash, None)
        access = str(body["access_token"])
        new_refresh = str(body["refresh_token"])
        await self._store_access_token(
            token=access,
            client_id=client.client_id,
            scopes=scopes or list(refresh_token.scopes),
            subject=refresh_token.subject,
        )
        self._refresh[hash_token(new_refresh)] = StoredRefreshToken(
            token=new_refresh,
            client_id=client.client_id,
            scopes=scopes or list(refresh_token.scopes),
            expires_at=int(time.time()) + _REFRESH_TTL_SECONDS,
            resource=refresh_token.resource,
            subject=refresh_token.subject,
            token_hash=hash_token(new_refresh),
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=settings.auth_access_token_minutes * 60,
            refresh_token=new_refresh,
            scope=" ".join(scopes or refresh_token.scopes),
        )

    async def load_access_token(self, token: str) -> StoredAccessToken | None:
        cached = self._access_by_token.get(token)
        if cached is not None:
            if cached.expires_at and cached.expires_at < time.time():
                self._access_by_token.pop(token, None)
                return None
            return cached
        from rai_gateway.auth.tokens import decode_user_access_token

        try:
            decode_user_access_token(token)
        except Exception:
            return None
        stored = StoredAccessToken(
            token=token,
            client_id="rai-mcp",
            scopes=list(DEFAULT_MCP_SCOPES),
            expires_at=None,
            resource=self._resource_url,
            subject=None,
        )
        self._access_by_token[token] = stored
        return stored

    async def revoke_token(self, token: StoredAccessToken | StoredRefreshToken) -> None:
        if isinstance(token, StoredAccessToken):
            self._access_by_token.pop(token.token, None)
        elif isinstance(token, StoredRefreshToken):
            self._refresh.pop(token.token_hash, None)

    async def _store_access_token(
        self, *, token: str, client_id: str, scopes: list[str], subject: str
    ) -> None:
        self._access_by_token[token] = StoredAccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            resource=self._resource_url,
            subject=subject,
        )

    def complete_password_login(
        self,
        *,
        pending_id: str,
        email: str,
        password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> str:
        pending = self._pending.pop(pending_id, None)
        if pending is None:
            raise AuthorizeError(
                error="access_denied", error_description="authorization request expired"
            )

        db: Session = SessionLocal()
        try:
            body = login_with_password(
                db,
                email=email,
                password=password,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        finally:
            db.close()

        access_token = str(body["access_token"])
        refresh_token = str(body["refresh_token"])
        subject = str(body.get("user", {}).get("email", email))
        scopes = list(pending.params.scopes or self._valid_scopes())
        code = secrets.token_urlsafe(32)
        stored = StoredAuthorizationCode(
            code=code,
            scopes=scopes,
            expires_at=time.time() + _CODE_TTL_SECONDS,
            client_id=pending.client_id,
            code_challenge=pending.params.code_challenge,
            redirect_uri=AnyUrl(str(pending.params.redirect_uri)),
            redirect_uri_provided_explicitly=pending.params.redirect_uri_provided_explicitly,
            resource=pending.params.resource,
            subject=subject,
            access_token_raw=access_token,
            refresh_token_raw=refresh_token,
        )
        self._codes[code] = stored
        self._refresh[hash_token(refresh_token)] = StoredRefreshToken(
            token=refresh_token,
            client_id=pending.client_id,
            scopes=scopes,
            expires_at=int(time.time()) + _REFRESH_TTL_SECONDS,
            resource=pending.params.resource,
            subject=subject,
            token_hash=hash_token(refresh_token),
        )
        return construct_redirect_uri(
            str(pending.params.redirect_uri),
            code=code,
            state=pending.params.state,
        )
