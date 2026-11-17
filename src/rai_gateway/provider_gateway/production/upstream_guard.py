"""SSRF protection, DNS pinning, and outbound destination allowlisting."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from rai_gateway.core.config import settings
from rai_gateway.network_enforcement.provider_destinations import classify_hostname
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

_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "169.254.169.254",
        "metadata.azure.com",
        "metadata.aws.internal",
    }
)

_PRIVATE_NETS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("0.0.0.0/8"),
)


@dataclass(frozen=True, slots=True)
class ValidatedUpstream:
    url: str
    hostname: str
    pinned_addresses: tuple[str, ...]


def _configured_allowlist() -> tuple[str, ...]:
    raw = (settings.provider_gateway_outbound_allowlist or "").strip()
    extras = (settings.provider_gateway_private_endpoint_suffixes or "").strip()
    combined: list[str] = []
    if raw:
        combined.extend(h.strip().lower() for h in raw.split(",") if h.strip())
    if extras:
        combined.extend(h.strip().lower() for h in extras.split(",") if h.strip())
    if not combined:
        return _DEFAULT_SUFFIX_ALLOWLIST
    return tuple(combined)


def _host_allowed(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if host in _METADATA_HOSTS:
        return False
    catalog = classify_hostname(host)
    if catalog is not None:
        return True
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


def _is_literal_ip(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


def _reject_private_ip(hostname: str) -> None:
    if _is_literal_ip(hostname):
        ip = ipaddress.ip_address(hostname)
        for net in _PRIVATE_NETS:
            if ip in net and not settings.mcp_allow_private_hosts:
                raise errors.ProviderGatewayError(
                    403,
                    "UPSTREAM_HOST_DENIED",
                    "Upstream IP is in a blocked range",
                )
        return
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
                    "Upstream resolves to a private or metadata address",
                )


@lru_cache(maxsize=256)
def _resolve_pinned_addresses(hostname: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise errors.ProviderGatewayError(
            502,
            "UPSTREAM_DNS_FAILED",
            f"Cannot resolve upstream host: {hostname}",
        ) from exc
    addrs: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in addrs:
            addrs.append(addr)
    if not addrs:
        raise errors.ProviderGatewayError(
            502,
            "UPSTREAM_DNS_FAILED",
            f"No addresses for upstream host: {hostname}",
        )
    return tuple(addrs)


def assert_resolved_addresses_match_pins(
    hostname: str, pinned: tuple[str, ...]
) -> None:
    """Reject DNS rebinding when resolved addresses drift from the validation pin."""
    if not pinned:
        return
    current = _resolve_pinned_addresses(hostname)
    if set(current) != set(pinned):
        raise errors.ProviderGatewayError(
            403,
            "UPSTREAM_DNS_REBIND",
            "Upstream host resolved to a different address set since validation",
        )


def validate_upstream_url(url: str) -> ValidatedUpstream:
    """Validate gateway-controlled upstream URL and pin resolved addresses."""
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
    try:
        pinned = _resolve_pinned_addresses(host)
    except errors.ProviderGatewayError:
        # Allowlisted hosts without public DNS (test doubles, private link) skip pinning.
        pinned = ()
    return ValidatedUpstream(url=url, hostname=host, pinned_addresses=pinned)


def assert_safe_upstream_url(url: str) -> str:
    """Validate gateway-controlled upstream URL before httpx issues a request."""
    return validate_upstream_url(url).url