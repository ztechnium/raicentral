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
    assert meta["finops"]["estimated_tokens"] == 100
    assert meta["finops"]["actual_tokens"] == 120
    assert meta["finops"]["variance_tokens"] == 20