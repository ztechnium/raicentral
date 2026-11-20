"""OPENAI_COMPATIBLE_CHAT adapter for the onboarding synthetic probe.

Receives a fully resolved immutable snapshot plus an explicit secret and nonce. It does
not use the legacy provider factory, fallback chain, runtime-context resolver or global
settings. The destination is validated and *pinned*: the request connects to the
validated address, so DNS cannot change between validation and use. Redirects are
refused, TLS is verified, responses are bounded, and nothing is retried or repaired.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from rai_gateway.core.config import settings

_METADATA_NETS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fd00:ec2::/32"),
)
_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "169.254.169.254",
    }
)

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "nonce": {"type": "string"}},
    "required": ["ok", "nonce"],
    "additionalProperties": False,
}

OUTCOME_PASSED = "PASSED"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class DestinationRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PreparedRequest:
    url: str
    host_header: str
    sni_hostname: str
    body: bytes
    headers: dict[str, str]
    timeout_seconds: float


@dataclass(frozen=True)
class ProbeResult:
    outcome: str
    error_code: str | None
    latency_ms: int | None
    usage: dict[str, int] | None


# Test seams: production uses the system resolver and a real httpx transport.
def resolve_host(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def build_transport() -> httpx.BaseTransport | None:
    return None  # default httpx transport (system CA bundle, TLS verification on)


def _address_allowed(address: str, allow_private: bool) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in net for net in _METADATA_NETS):
        return False  # metadata endpoints are never reachable, even for private targets
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return False
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or getattr(ip, "is_site_local", False)
    ):
        return allow_private
    return True


def prepare_request(
    snapshot: dict[str, Any], secret: str, nonce: str
) -> PreparedRequest:
    """Validate the final URL and destination; build the exact outbound request."""
    base = str(snapshot["base_url"])
    final = f"{base.rstrip('/')}/chat/completions"
    parts = urlsplit(final)
    approved = urlsplit(base)
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.fragment
        or parts.query
        or parts.hostname != approved.hostname
        or parts.port != approved.port
        or (parts.hostname or "").lower() in _METADATA_HOSTS
    ):
        raise DestinationRejected("DESTINATION_REJECTED")
    host = parts.hostname or ""
    try:
        addresses = resolve_host(host)
    except OSError as exc:
        raise DestinationRejected("DNS_FAILED") from exc
    if not addresses or not all(
        _address_allowed(a, bool(snapshot.get("allow_private_network")))
        for a in addresses
    ):
        raise DestinationRejected("DESTINATION_REJECTED")
    address = addresses[0]
    literal = f"[{address}]" if ":" in address else address
    port = f":{parts.port}" if parts.port else ""
    pinned_url = f"https://{literal}{port}{parts.path}"

    mode = snapshot["structured_mode"]
    instruction = "Return exactly this JSON object and nothing else: " + json.dumps(
        {"ok": True, "nonce": nonce}
    )
    body: dict[str, Any] = {
        "model": snapshot["model"],
        "messages": [
            {
                "role": "system",
                "content": "You are a connectivity probe. Reply with JSON only.",
            },
            {"role": "user", "content": instruction},
        ],
        snapshot["token_param"]: int(snapshot["max_output_tokens"]),
        "stream": False,
    }
    if mode == "JSON_SCHEMA":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "onboarding_connection_probe",
                "strict": True,
                "schema": PROBE_SCHEMA,
            },
        }
    elif mode == "JSON_OBJECT":
        body["response_format"] = {"type": "json_object"}
    else:  # never downgrade or guess
        raise DestinationRejected("UNSUPPORTED_MODE")
    return PreparedRequest(
        url=pinned_url,
        host_header=host + port,
        sni_hostname=host,
        body=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Host": host + port,
        },
        timeout_seconds=float(snapshot.get("timeout_seconds", 30.0)),
    )


def _fail(code: str, started: float | None = None) -> ProbeResult:
    latency = None if started is None else int((time.monotonic() - started) * 1000)
    return ProbeResult(OUTCOME_FAILED, code, latency, None)


def _usage(body: dict[str, Any]) -> dict[str, int] | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    out = {
        k: int(usage[k])
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)
    }
    return out or None  # missing usage is unknown, never zero


def validate_response(
    raw: bytes, status: int, nonce: str, *, started: float
) -> ProbeResult:
    """Strict local validation. Refusals, truncation and bad shapes are failures."""
    latency = int((time.monotonic() - started) * 1000)
    if 300 <= status < 400:
        return _fail("REDIRECT_REJECTED", started)
    if status in (401, 403):
        return _fail("AUTH_REJECTED", started)
    if status == 404:
        return _fail("MODEL_OR_ENDPOINT_NOT_FOUND", started)
    if status == 429:
        return _fail("RATE_LIMITED", started)
    if 400 <= status < 500:
        return _fail("REQUEST_REJECTED", started)
    if status >= 500:
        return _fail("PROVIDER_ERROR", started)
    try:
        body = json.loads(raw.decode("utf-8"))
        choice = body["choices"][0]
        message = choice["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return _fail("INVALID_RESPONSE", started)
    usage = _usage(body) if isinstance(body, dict) else None
    if message.get("refusal"):
        return ProbeResult(OUTCOME_FAILED, "REFUSAL", latency, usage)
    if choice.get("finish_reason") == "length":
        return ProbeResult(OUTCOME_FAILED, "TRUNCATED_OUTPUT", latency, usage)
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return ProbeResult(OUTCOME_FAILED, "EMPTY_RESPONSE", latency, usage)
    try:
        parsed = json.loads(content)
    except ValueError:
        return ProbeResult(OUTCOME_FAILED, "INVALID_JSON", latency, usage)
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"ok", "nonce"}
        or parsed["ok"] is not True
        or not isinstance(parsed["nonce"], str)
    ):
        return ProbeResult(OUTCOME_FAILED, "SCHEMA_MISMATCH", latency, usage)
    if parsed["nonce"] != nonce:
        return ProbeResult(OUTCOME_FAILED, "NONCE_MISMATCH", latency, usage)
    return ProbeResult(OUTCOME_PASSED, None, latency, usage)


def send(
    prepared: PreparedRequest,
    nonce: str,
    *,
    transport_factory: Callable[[], httpx.BaseTransport | None] | None = None,
) -> ProbeResult:
    """Send once. No retries, no redirects, bounded response, explicit deadline."""
    max_bytes = settings.onboarding_ai_probe_max_response_bytes
    started = time.monotonic()
    deadline = started + prepared.timeout_seconds
    transport = (transport_factory or build_transport)()
    timeout = httpx.Timeout(
        connect=min(10.0, prepared.timeout_seconds),
        read=prepared.timeout_seconds,
        write=min(10.0, prepared.timeout_seconds),
        pool=5.0,
    )
    try:
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            verify=True,
            trust_env=False,  # no ambient proxies or CA overrides
        ) as client:
            request = client.build_request(
                "POST",
                prepared.url,
                headers=prepared.headers,
                content=prepared.body,
                extensions={"sni_hostname": prepared.sni_hostname},
            )
            response = client.send(request, stream=True)
            try:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        return _fail("RESPONSE_TOO_LARGE", started)
                    if time.monotonic() > deadline:
                        return ProbeResult(
                            OUTCOME_UNKNOWN, "DEADLINE_EXCEEDED", None, None
                        )
                    chunks.append(chunk)
                status = response.status_code
            finally:
                response.close()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return _fail("CONNECT_FAILED", started)  # nothing reached the provider
    except httpx.TimeoutException:
        return ProbeResult(OUTCOME_UNKNOWN, "TIMEOUT_AFTER_DISPATCH", None, None)
    except httpx.HTTPError:
        return ProbeResult(
            OUTCOME_UNKNOWN, "TRANSPORT_ERROR_AFTER_DISPATCH", None, None
        )
    return validate_response(b"".join(chunks), status, nonce, started=started)
