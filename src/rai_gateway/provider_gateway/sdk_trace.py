"""SDK request detection and tracing for provider-compatible routes.

Official Google SDKs (``@google/genai``) send the same request shapes to whatever base
URL they are given. The gateway therefore infers *which* Google API a call targets from
the request itself, so a developer only sets one base URL:

* ``/projects/<p>/locations/<l>/publishers/<pub>/models/<m>``  -> Vertex AI
* ``/models/<m>:generateContent`` (no projects/locations)      -> Gemini Developer API

Every call is logged with the detected API, project, location, model, operation, and the
SDK identity (``x-goog-api-client``) so requests can be traced end to end.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass

# One base URL for both Google APIs: <gateway>/providers/genai
UNIFIED_PREFIX = "/providers/genai"
VERTEX_PREFIX = "/providers/vertex-ai"
GEMINI_PREFIX = "/providers/google-ai"

_VERTEX_PATH = re.compile(
    r"/projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/"
    r"publishers/(?P<publisher>[^/]+)/models/(?P<model>[^/:]+)(?::(?P<op>[A-Za-z]+))?"
)
_GEMINI_PATH = re.compile(r"/models/(?P<model>[^/:]+)(?::(?P<op>[A-Za-z]+))?")

logger = logging.getLogger("rai_gateway.provider_gateway.sdk")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s sdk-trace %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@dataclass(frozen=True, slots=True)
class SdkRequestInfo:
    api: str  # "vertex" | "gemini" | "unknown"
    project: str | None = None
    location: str | None = None
    model: str | None = None
    operation: str | None = None

    @property
    def streaming(self) -> bool:
        return bool(self.operation and self.operation.lower().startswith("stream"))


def detect(path: str) -> SdkRequestInfo:
    """Infer the target API and its parameters from a request path."""
    match = _VERTEX_PATH.search(path)
    if match:
        return SdkRequestInfo(
            "vertex",
            match.group("project"),
            match.group("location"),
            match.group("model"),
            match.group("op"),
        )
    match = _GEMINI_PATH.search(path)
    if match:
        return SdkRequestInfo(
            "gemini", None, None, match.group("model"), match.group("op")
        )
    return SdkRequestInfo("unknown")


def resolve_unified_path(path: str) -> str:
    """Rewrite ``/providers/genai/...`` to the Vertex or Gemini provider mount."""
    if path != UNIFIED_PREFIX and not path.startswith(f"{UNIFIED_PREFIX}/"):
        return path
    rest = path[len(UNIFIED_PREFIX) :]
    info = detect(rest)
    prefix = VERTEX_PREFIX if info.api == "vertex" else GEMINI_PREFIX
    return f"{prefix}{rest}"


def _mask(secret: str | None) -> str:
    if not secret:
        return "-"
    return f"{secret[:10]}…" if len(secret) > 10 else "***"


def log_request(
    *,
    method: str,
    original_path: str,
    routed_path: str,
    headers: Mapping[str, str],
    client_host: str | None,
    request_id: str,
) -> SdkRequestInfo:
    """Emit one trace line for an inbound SDK call and return what was detected."""
    info = detect(routed_path)
    lowered = {k.lower(): v for k, v in headers.items()}
    cred = lowered.get("x-rai-agent-key") or (
        lowered.get("authorization", "")[7:]
        if lowered.get("authorization", "").lower().startswith("bearer ")
        else lowered.get("x-goog-api-key")
    )
    logger.info(
        "req=%s %s api=%s project=%s location=%s model=%s op=%s stream=%s "
        "sdk=%r ua=%r cred=%s client=%s path=%s routed=%s",
        request_id,
        method,
        info.api,
        info.project or "-",
        info.location or "-",
        info.model or "-",
        info.operation or "-",
        info.streaming,
        lowered.get("x-goog-api-client", "-"),
        lowered.get("user-agent", "-"),
        _mask(cred),
        client_host or "-",
        original_path,
        routed_path,
    )
    return info


def log_response(
    *, request_id: str, status: int, duration_ms: int, receipt_id: str | None
) -> None:
    logger.info(
        "req=%s done status=%s duration_ms=%s receipt=%s",
        request_id,
        status,
        duration_ms,
        receipt_id or "-",
    )
