"""Redis-backed policy cache integration (PR-D)."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.production.domain import PolicyCacheNamespace
from rai_gateway.provider_gateway.production.policy_cache import (
    PolicyCacheRevocationError,
    SharedPolicyCache,
    shared_policy_cache,
)


def _redis_available() -> bool:
    url = os.environ.get("REDIS_URL") or settings.redis_url
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
        client.ping()
        client.flushdb()
        return True
    except Exception:
        return False


@pytest.fixture()
def isolated_cache(monkeypatch) -> SharedPolicyCache:
    url = os.environ.get("REDIS_URL") or settings.redis_url
    if not _redis_available():
        pytest.skip("Redis not available")
    monkeypatch.setattr(settings, "enable_cache", True)
    monkeypatch.setattr(settings, "redis_url", url)
    return SharedPolicyCache()


def test_authoritative_expiration_not_extended_by_local_rehydrate(
    isolated_cache: SharedPolicyCache,
) -> None:
    key = f"runtime-{uuid.uuid4().hex[:8]}"
    isolated_cache.put(
        PolicyCacheNamespace.AUTHORITY_SNAPSHOT,
        key,
        {"authority_digest": "abc", "authority_revision": "1"},
        ttl_seconds=1,
        authority_revision="1",
        policy_revision="pol-1",
    )
    time.sleep(1.2)
    assert isolated_cache.get(PolicyCacheNamespace.AUTHORITY_SNAPSHOT, key) is None


def test_epoch_invalidation_evicts_other_replica_view(isolated_cache: SharedPolicyCache) -> None:
    key = f"runtime-{uuid.uuid4().hex[:8]}"
    isolated_cache.put(
        PolicyCacheNamespace.RUNTIME_IDENTITY,
        key,
        {"status": "ACTIVE"},
        ttl_seconds=120,
    )
    other = SharedPolicyCache()
    other.put(
        PolicyCacheNamespace.RUNTIME_IDENTITY,
        key,
        {"status": "ACTIVE"},
        ttl_seconds=120,
    )
    isolated_cache.mark_runtime_revoked(key, reason="credential_revoke")
    assert other.get(PolicyCacheNamespace.RUNTIME_IDENTITY, key) is None


def test_revoked_runtime_fail_closed(isolated_cache: SharedPolicyCache) -> None:
    rid = f"runtime-{uuid.uuid4().hex[:8]}"
    isolated_cache.mark_runtime_revoked(rid)
    with pytest.raises(PolicyCacheRevocationError):
        isolated_cache.fail_closed_if_revoked(rid)


def test_shared_singleton_smoke() -> None:
    shared_policy_cache.invalidate(
        PolicyCacheNamespace.AUTHORITY_SNAPSHOT, "test-smoke"
    )