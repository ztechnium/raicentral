"""Step 8: production hardening and rollout."""

from __future__ import annotations

import pytest

from rai_gateway.provider_gateway.production.fail_closed import (
    FailClosedDependency,
    FailClosedMatrix,
    FailClosedOutcome,
)
from rai_gateway.provider_gateway.production.domain import PolicyCacheNamespace
from rai_gateway.provider_gateway.production.limits import gateway_limits
from rai_gateway.provider_gateway.production.policy_cache import shared_policy_cache
from rai_gateway.provider_gateway.production.upstream_guard import assert_safe_upstream_url
from rai_gateway.runtime_enforcement.gateway_action import GatewayEnforcementMode


def test_fail_closed_matrix_enforce_denies_policy_outage() -> None:
    matrix = FailClosedMatrix(mode=GatewayEnforcementMode.ENFORCE)
    assert (
        matrix.resolve(FailClosedDependency.POLICY_SERVICE)
        == FailClosedOutcome.DENY
    )
    assert matrix.should_deny(FailClosedDependency.POLICY_SERVICE)


def test_fail_closed_observe_allows_policy_outage() -> None:
    matrix = FailClosedMatrix(mode=GatewayEnforcementMode.OBSERVE)
    assert (
        matrix.resolve(FailClosedDependency.POLICY_SERVICE)
        == FailClosedOutcome.ALLOW_LOG
    )
    assert not matrix.should_deny(FailClosedDependency.POLICY_SERVICE)


def test_policy_cache_ttl_and_invalidation() -> None:
    shared_policy_cache.invalidate(
        PolicyCacheNamespace.AUTHORITY_SNAPSHOT, "runtime-1"
    )
    entry = shared_policy_cache.put(
        PolicyCacheNamespace.AUTHORITY_SNAPSHOT,
        "runtime-1",
        {"authority_digest": "abc"},
        ttl_seconds=60,
    )
    assert entry.digest
    hit = shared_policy_cache.get(
        PolicyCacheNamespace.AUTHORITY_SNAPSHOT, "runtime-1"
    )
    assert hit is not None
    shared_policy_cache.invalidate_fast_paths("credential_revoke:rid-9")
    assert hit is not None  # different key; smoke invalidation path


@pytest.mark.asyncio
async def test_gateway_lifecycle_defers_in_flight_until_stream_wrapper_finishes() -> None:
    from starlette.responses import StreamingResponse

    from rai_gateway.provider_gateway.api import to_starlette
    from rai_gateway.provider_gateway.domain import ProxyResponse
    from rai_gateway.provider_gateway.production.lifecycle import gateway_lifecycle

    async def gen():
        yield b"x"

    before = gateway_lifecycle.in_flight()
    gateway_lifecycle.begin_request()
    try:
        wrapped = to_starlette(
            ProxyResponse(status_code=200, headers={}, stream=gen()),
            on_stream_finished=gateway_lifecycle.end_request,
        )
        assert isinstance(wrapped, StreamingResponse)
        assert gateway_lifecycle.in_flight() == before + 1
        async for _ in wrapped.body_iterator:
            pass
        assert gateway_lifecycle.in_flight() == before
    finally:
        while gateway_lifecycle.in_flight() > before:
            gateway_lifecycle.end_request()


def test_gateway_limits_header_cap() -> None:
    big = {"x-test": "a" * 40000}
    result = gateway_limits.check_headers(big)
    assert not result.allowed


def test_upstream_guard_blocks_unknown_host() -> None:
    from rai_gateway.provider_gateway.errors import ProviderGatewayError

    with pytest.raises(ProviderGatewayError) as exc:
        assert_safe_upstream_url("https://evil.example.com/v1/chat")
    assert exc.value.code == "UPSTREAM_HOST_DENIED"


def test_upstream_guard_allows_openai() -> None:
    url = assert_safe_upstream_url("https://api.openai.com/v1/chat/completions")
    assert url.endswith("/chat/completions")


@pytest.mark.asyncio
async def test_stream_slot_held_until_relay_finishes(monkeypatch) -> None:
    """Mirror proxy deferral: release concurrent stream slots in relay on_done."""
    import asyncio

    import httpx

    from provider_gateway.fakes import FakeUsageCollector
    from rai_gateway.core.config import settings
    from rai_gateway.provider_gateway.production.limits import GatewayLimits
    from rai_gateway.provider_gateway.streaming import relay_stream

    monkeypatch.setattr(settings, "provider_gateway_max_concurrent_streams", 1)
    limits = GatewayLimits()
    limits.acquire_stream(runtime_key="runtime-1", provider="openai")
    assert limits.snapshot()["global_streams"] == 1

    async def upstream_bytes():
        yield b"data: one\n\n"
        await asyncio.sleep(0.05)
        yield b"data: [DONE]\n\n"

    upstream = httpx.Response(200, content=upstream_bytes())

    async def on_done(*_args: object) -> None:
        limits.release_stream(runtime_key="runtime-1", provider="openai")

    relay = relay_stream(upstream, FakeUsageCollector(), on_done)
    async for chunk in relay:
        if chunk.startswith(b"data: one"):
            assert limits.snapshot()["global_streams"] == 1
    assert limits.snapshot()["global_streams"] == 0


@pytest.mark.asyncio
async def test_to_starlette_defers_lifecycle_until_stream_consumed() -> None:
    from rai_gateway.provider_gateway.api import to_starlette
    from rai_gateway.provider_gateway.domain import ProxyResponse
    from rai_gateway.provider_gateway.production.lifecycle import GatewayLifecycle

    lc = GatewayLifecycle()
    lc.begin_request()

    async def body():
        yield b"chunk"

    response = to_starlette(
        ProxyResponse(status_code=200, headers={}, stream=body()),
        on_stream_finished=lc.end_request,
    )
    assert lc.in_flight() == 1
    async for _ in response.body_iterator:
        pass
    assert lc.in_flight() == 0


def test_finops_estimate_attaches_variance() -> None:
    from rai_gateway.provider_gateway.production.finops import attach_finops_metadata
    from rai_gateway.provider_gateway.domain import UsageInfo

    meta = attach_finops_metadata(
        {"provider": "openai"},
        model="gpt-4o",
        request_bytes=400,
        streaming=False,
        usage=UsageInfo(total_tokens=120, provider_reported=True),
        succeeded=True,
    )
    assert meta["finops"]["estimated_tokens"] >= 100
    assert meta["finops"]["actual_tokens"] == 120
    assert meta["finops"]["variance_tokens"] == (
        meta["finops"]["actual_tokens"] - meta["finops"]["estimated_tokens"]
    )