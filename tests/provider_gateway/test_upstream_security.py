"""Adversarial upstream destination tests (PR-E)."""

from __future__ import annotations

import pytest

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.errors import ProviderGatewayError
from rai_gateway.provider_gateway.production import upstream_guard
from rai_gateway.provider_gateway.production.upstream_guard import (
    assert_resolved_addresses_match_pins,
    validate_upstream_url,
)


def test_blocks_cloud_metadata_host() -> None:
    with pytest.raises(ProviderGatewayError) as exc:
        validate_upstream_url("https://metadata.google.internal/computeMetadata/v1/")
    assert exc.value.code == "UPSTREAM_HOST_DENIED"


def test_blocks_disallowed_host() -> None:
    with pytest.raises(ProviderGatewayError) as exc:
        validate_upstream_url("https://evil.example.com/v1/chat")
    assert exc.value.code == "UPSTREAM_HOST_DENIED"


def test_private_endpoint_suffix_allowed(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "provider_gateway_outbound_allowlist",
        "myaccount.privatelink.azure.com",
    )
    upstream_guard._resolve_pinned_addresses.cache_clear()
    monkeypatch.setattr(
        upstream_guard,
        "_resolve_pinned_addresses",
        lambda _host: ("10.20.30.40",),
    )
    validated = validate_upstream_url(
        "https://myaccount.privatelink.azure.com/openai/deployments/x/chat/completions"
    )
    assert validated.hostname.endswith("privatelink.azure.com")
    assert validated.pinned_addresses == ("10.20.30.40",)


def test_dns_rebind_detected(monkeypatch) -> None:
    host = "api.openai.com"
    upstream_guard._resolve_pinned_addresses.cache_clear()
    validated = validate_upstream_url(f"https://{host}/v1/chat/completions")
    pins = validated.pinned_addresses

    original = upstream_guard._resolve_pinned_addresses

    def fake_pin(hostname: str) -> tuple[str, ...]:
        if hostname == host:
            return ("198.51.100.9",)
        return original(hostname)

    monkeypatch.setattr(upstream_guard, "_resolve_pinned_addresses", fake_pin)
    with pytest.raises(ProviderGatewayError) as exc:
        assert_resolved_addresses_match_pins(host, pins)
    assert exc.value.code == "UPSTREAM_DNS_REBIND"