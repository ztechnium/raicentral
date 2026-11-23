"""Provider adapter for the onboarding synthetic probe (OpenAI-style, Anthropic, Gemini, Vertex).

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

from rai_gateway.core import ai_provider
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
    provider: str = "openai_compatible"


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


@dataclass(frozen=True)
class Generation:
    """A real (non-probe) structured-output request over the same pinned transport."""

    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str
    max_output_tokens: int
    timeout_seconds: float


def prepare_request(
    snapshot: dict[str, Any],
    secret: str,
    nonce: str,
    *,
    generation: Generation | None = None,
) -> PreparedRequest:
    """Validate the final URL and destination; build the exact outbound request."""
    base = str(snapshot["base_url"])
    provider = str(snapshot.get("provider") or "openai_compatible")
    if provider not in ai_provider.PROVIDERS:
        raise DestinationRejected("DESTINATION_REJECTED")
    path = ai_provider.request_path(
        provider,
        str(snapshot["model"]),
        auth=str(snapshot.get("auth") or "api_key"),
        project=snapshot.get("gcp_project_id"),
        location=snapshot.get("location"),
    )
    final = f"{base.rstrip('/')}{path}"
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
    if mode not in ai_provider.STRUCTURED_MODES:  # never downgrade or guess
        raise DestinationRejected("UNSUPPORTED_MODE")
    instruction = "Return exactly this JSON object and nothing else: " + json.dumps(
        {"ok": True, "nonce": nonce}
    )
    try:
        body = ai_provider.build_body(
            provider,
            model=str(snapshot["model"]),
            system=(
                generation.system
                if generation
                else "You are a connectivity probe. Reply with JSON only."
            ),
            user=generation.user if generation else instruction,
            max_output_tokens=(
                generation.max_output_tokens
                if generation
                else int(snapshot["max_output_tokens"])
            ),
            structured_mode=mode,
            token_param=str(snapshot.get("token_param") or "max_tokens"),
            schema_name=generation.schema_name if generation else "onboarding_connection_probe",
            schema=generation.schema if generation else PROBE_SCHEMA,
        )
    except ai_provider.AIProviderError as exc:
        raise DestinationRejected("UNSUPPORTED_MODE") from exc
    # The probe carries no temperature: the provider's own default applies.
    return PreparedRequest(
        url=pinned_url,
        host_header=host + port,
        sni_hostname=host,
        provider=provider,
        body=json.dumps(body).encode("utf-8"),
        headers={
            **ai_provider.auth_headers(
                provider, str(snapshot.get("auth") or "api_key"), secret or None
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Host": host + port,
        },
        timeout_seconds=(
            generation.timeout_seconds
            if generation
            else float(snapshot.get("timeout_seconds", 30.0))
        ),
    )


def _fail(code: str, started: float | None = None) -> ProbeResult:
    latency = None if started is None else int((time.monotonic() - started) * 1000)
    return ProbeResult(OUTCOME_FAILED, code, latency, None)


def validate_response(
    raw: bytes,
    status: int,
    nonce: str,
    *,
    started: float,
    provider: str = "openai_compatible",
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
        extracted = ai_provider.extract_response(provider, json.loads(raw.decode("utf-8")))
    except (ValueError, ai_provider.AIProviderError):
        return _fail("INVALID_RESPONSE", started)
    usage = extracted.usage
    if extracted.refused:
        return ProbeResult(OUTCOME_FAILED, "REFUSAL", latency, usage)
    if extracted.truncated:
        return ProbeResult(OUTCOME_FAILED, "TRUNCATED_OUTPUT", latency, usage)
    content = extracted.text
    if not isinstance(content, str) or not content.strip():
        return ProbeResult(OUTCOME_FAILED, "EMPTY_RESPONSE", latency, usage)
    text = content.strip()
    if text.startswith("```"):  # some models fence JSON when no JSON mode exists
        text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed = json.loads(text)
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


def http_failure_code(status: int) -> str | None:
    """Stable error code for a non-success HTTP status, else None."""
    if 300 <= status < 400:
        return "REDIRECT_REJECTED"
    if status in (401, 403):
        return "AUTH_REJECTED"
    if status == 404:
        return "MODEL_OR_ENDPOINT_NOT_FOUND"
    if status == 429:
        return "RATE_LIMITED"
    if 400 <= status < 500:
        return "REQUEST_REJECTED"
    if status >= 500:
        return "PROVIDER_ERROR"
    return None


@dataclass(frozen=True)
class _RoundTrip:
    failure: ProbeResult | None
    status: int
    body: bytes
    started: float


def _round_trip(
    prepared: PreparedRequest,
    max_bytes: int,
    transport_factory: Callable[[], httpx.BaseTransport | None] | None,
) -> _RoundTrip:
    """Send once. No retries, no redirects, bounded response, explicit deadline."""
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
                        return _RoundTrip(_fail("RESPONSE_TOO_LARGE", started), 0, b"", started)
                    if time.monotonic() > deadline:
                        return _RoundTrip(
                            ProbeResult(OUTCOME_UNKNOWN, "DEADLINE_EXCEEDED", None, None),
                            0,
                            b"",
                            started,
                        )
                    chunks.append(chunk)
                status = response.status_code
            finally:
                response.close()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        # nothing reached the provider
        return _RoundTrip(_fail("CONNECT_FAILED", started), 0, b"", started)
    except httpx.TimeoutException:
        return _RoundTrip(
            ProbeResult(OUTCOME_UNKNOWN, "TIMEOUT_AFTER_DISPATCH", None, None), 0, b"", started
        )
    except httpx.HTTPError:
        return _RoundTrip(
            ProbeResult(OUTCOME_UNKNOWN, "TRANSPORT_ERROR_AFTER_DISPATCH", None, None),
            0,
            b"",
            started,
        )
    return _RoundTrip(None, status, b"".join(chunks), started)


def send(
    prepared: PreparedRequest,
    nonce: str,
    *,
    transport_factory: Callable[[], httpx.BaseTransport | None] | None = None,
) -> ProbeResult:
    """Send the connection probe once and validate the reply."""
    trip = _round_trip(
        prepared, settings.onboarding_ai_probe_max_response_bytes, transport_factory
    )
    if trip.failure is not None:
        return trip.failure
    return validate_response(
        trip.body, trip.status, nonce, started=trip.started, provider=prepared.provider
    )


@dataclass(frozen=True)
class TextResult:
    outcome: str
    error_code: str | None
    text: str | None
    usage: dict[str, int] | None
    latency_ms: int | None


def send_text(
    prepared: PreparedRequest,
    *,
    max_bytes: int,
    transport_factory: Callable[[], httpx.BaseTransport | None] | None = None,
) -> TextResult:
    """Send one generation request; return the model text or a stable failure code."""
    trip = _round_trip(prepared, max_bytes, transport_factory)
    if trip.failure is not None:
        f = trip.failure
        return TextResult(f.outcome, f.error_code, None, None, f.latency_ms)
    latency = int((time.monotonic() - trip.started) * 1000)
    code = http_failure_code(trip.status)
    if code:
        return TextResult(OUTCOME_FAILED, code, None, None, latency)
    try:
        extracted = ai_provider.extract_response(
            prepared.provider, json.loads(trip.body.decode("utf-8"))
        )
    except (ValueError, ai_provider.AIProviderError):
        return TextResult(OUTCOME_FAILED, "INVALID_RESPONSE", None, None, latency)
    if extracted.refused:
        return TextResult(OUTCOME_FAILED, "REFUSAL", None, extracted.usage, latency)
    if extracted.truncated:
        return TextResult(OUTCOME_FAILED, "TRUNCATED_OUTPUT", None, extracted.usage, latency)
    if not extracted.text or not extracted.text.strip():
        return TextResult(OUTCOME_FAILED, "EMPTY_RESPONSE", None, extracted.usage, latency)
    return TextResult(OUTCOME_PASSED, None, extracted.text, extracted.usage, latency)
