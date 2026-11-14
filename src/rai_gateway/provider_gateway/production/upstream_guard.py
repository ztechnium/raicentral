"""SSRF protection and outbound destination allowlisting."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway import errors

_DEFAULT_SUFFIX_ALLOWLIST = (
    "api.openai.com",
    "openai.azure.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "bedrock-runtime.",
    "amazonaws.com",
    "localhost",
    "127.0.0.1",
)

_PRIVATE_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
)


def _configured_allowlist() -> tuple[str, ...]:
    raw = (settings.provider_gateway_outbound_allowlist or "").strip()
    if not raw:
        return _DEFAULT_SUFFIX_ALLOWLIST
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def _host_allowed(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    for entry in _configured_allowlist():
        if entry.startswith("*."):
            suffix = entry[2:]
            if host == suffix or host.endswith(f".{suffix}"):
                return True
        elif entry.endswith("."):
            if host.endswith(entry.rstrip(".")) or host == entry.rstrip("."):
                return True
        elif host == entry or host.endswith(f".{entry}"):
            return True
    return False


def _reject_private_ip(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        for net in _PRIVATE_NETS:
            if ip in net and not settings.mcp_allow_private_hosts:
                raise errors.ProviderGatewayError(
                    403,
                    "UPSTREAM_HOST_DENIED",
                    "Upstream resolves to a private address",
                )


def assert_safe_upstream_url(url: str) -> str:
    """Validate gateway-controlled upstream URL before httpx issues a request."""
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise errors.ProviderGatewayError(
            502,
            "UPSTREAM_URL_INVALID",
            "Upstream URL must use http or https",
        )
    if parsed.scheme == "http" and not settings.mcp_allow_http:
        raise errors.ProviderGatewayError(
            403,
            "UPSTREAM_HTTP_DENIED",
            "Plain HTTP upstream is disabled",
        )
    host = parsed.hostname
    if not host:
        raise errors.ProviderGatewayError(
            502, "UPSTREAM_URL_INVALID", "Upstream URL missing host"
        )
    if not _host_allowed(host):
        raise errors.ProviderGatewayError(
            403,
            "UPSTREAM_HOST_DENIED",
            f"Host '{host}' is not on the gateway outbound allowlist",
        )
    _reject_private_ip(host)
    return url
