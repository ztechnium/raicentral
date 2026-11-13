"""Explicit header policy for the provider proxy.

Inbound headers are filtered through an *allow-list*; everything else - and in
particular any client-supplied provider credential - is dropped. Upstream
credentials are injected afterwards from server-side configuration.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# Always removed from inbound requests, even if an adapter asks to pass them.
STRIP_INBOUND = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
        "proxy-connection",
        "authorization",
        "api-key",
        "x-api-key",
        "x-goog-api-key",
        "x-goog-user-project",
        "x-rai-agent-key",
        "cookie",
        "forwarded",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)

# Safe to forward untouched.
PRESERVE_INBOUND = frozenset(
    {
        "traceparent",
        "tracestate",
        "x-request-id",
        "content-type",
        "accept",
        "user-agent",
    }
)

# Never relayed back to the client (we re-frame the body ourselves).
STRIP_OUTBOUND = frozenset(
    {
        "connection",
        "keep-alive",
        "transfer-encoding",
        "content-length",
        "content-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authenticate",
        "set-cookie",
    }
)


def filter_inbound_headers(
    headers: Mapping[str, str], *, extra_allowed: Iterable[str] = ()
) -> dict[str, str]:
    """Return lower-cased headers safe to send upstream."""
    allowed = PRESERVE_INBOUND | {h.lower() for h in extra_allowed}
    allowed -= STRIP_INBOUND
    return {k.lower(): v for k, v in headers.items() if k.lower() in allowed}


def inject_gateway_headers(
    safe: Mapping[str, str], credential_headers: Mapping[str, str]
) -> dict[str, str]:
    """Credential headers always win over anything inherited from the client."""
    merged = dict(safe)
    for key in list(merged):
        if key.lower() in {h.lower() for h in credential_headers}:
            del merged[key]
    merged.update(credential_headers)
    return merged


def filter_outbound_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in STRIP_OUTBOUND}
