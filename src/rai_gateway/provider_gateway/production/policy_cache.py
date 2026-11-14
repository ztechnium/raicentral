"""Shared signed enforcement state cache for the provider gateway data plane."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.production.domain import PolicyCacheNamespace

logger = logging.getLogger(__name__)

_FAST_INVALIDATION_PREFIXES = (
    "credential_revoke:",
    "runtime_suspend:",
    "delegation_revoke:",
    "emergency:",
)


@dataclass
class CacheEntry:
    namespace: PolicyCacheNamespace
    key: str
    payload: dict[str, Any]
    digest: str
    stored_at: float
    ttl_seconds: float
    fast_invalidate: bool = False

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.stored_at > self.ttl_seconds

    def to_wire(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace.value,
            "key": self.key,
            "payload": self.payload,
            "digest": self.digest,
            "stored_at": self.stored_at,
            "ttl_seconds": self.ttl_seconds,
            "fast_invalidate": self.fast_invalidate,
        }


@dataclass
class PolicyCacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    invalidations: int = 0
    redis_errors: int = 0
    backend: str = "memory"

    def healthy(self) -> bool:
        if self.backend == "degraded":
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "invalidations": self.invalidations,
            "redis_errors": self.redis_errors,
            "backend": self.backend,
            "healthy": self.healthy(),
        }


class SharedPolicyCache:
    """Process-local cache with optional Redis backing for horizontal scale."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, CacheEntry] = {}
        self._stats = PolicyCacheStats()
        self._redis = None
        self._redis_checked = False

    def _storage_key(self, namespace: PolicyCacheNamespace, key: str) -> str:
        return f"{namespace.value}:{key}"

    def _digest(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def _maybe_redis(self):
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        if not settings.enable_cache:
            self._stats.backend = "memory"
            return None
        try:
            import redis

            client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            client.ping()
            self._redis = client
            self._stats.backend = "redis"
        except Exception as exc:
            logger.warning("Policy cache redis unavailable: %s", exc)
            self._stats.backend = "memory"
            self._stats.redis_errors += 1
        return self._redis

    def put(
        self,
        namespace: PolicyCacheNamespace,
        key: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: float | None = None,
        fast_invalidate: bool = False,
    ) -> CacheEntry:
        ttl = (
            settings.provider_gateway_policy_cache_fast_invalidate_ttl_seconds
            if fast_invalidate
            else (
                ttl_seconds
                if ttl_seconds is not None
                else float(settings.provider_gateway_policy_cache_ttl_seconds)
            )
        )
        entry = CacheEntry(
            namespace=namespace,
            key=key,
            payload=payload,
            digest=self._digest(payload),
            stored_at=time.monotonic(),
            ttl_seconds=ttl,
            fast_invalidate=fast_invalidate,
        )
        sk = self._storage_key(namespace, key)
        with self._lock:
            self._entries[sk] = entry
        client = self._maybe_redis()
        if client is not None:
            try:
                client.setex(
                    f"rai:policy:{sk}",
                    int(max(1, ttl)),
                    json.dumps(entry.to_wire()),
                )
            except Exception:
                self._stats.redis_errors += 1
        return entry

    def get(self, namespace: PolicyCacheNamespace, key: str) -> CacheEntry | None:
        sk = self._storage_key(namespace, key)
        with self._lock:
            entry = self._entries.get(sk)
            if entry is not None and not entry.expired:
                self._stats.hits += 1
                return entry
            if entry is not None and entry.expired:
                del self._entries[sk]
                self._stats.evictions += 1
        client = self._maybe_redis()
        if client is not None:
            try:
                raw = client.get(f"rai:policy:{sk}")
                if raw:
                    wire = json.loads(raw)
                    entry = CacheEntry(
                        namespace=PolicyCacheNamespace(wire["namespace"]),
                        key=wire["key"],
                        payload=wire["payload"],
                        digest=wire["digest"],
                        stored_at=time.monotonic(),
                        ttl_seconds=float(wire["ttl_seconds"]),
                        fast_invalidate=bool(wire.get("fast_invalidate")),
                    )
                    with self._lock:
                        self._entries[sk] = entry
                    self._stats.hits += 1
                    return entry
            except Exception:
                self._stats.redis_errors += 1
        self._stats.misses += 1
        return None

    def invalidate(self, namespace: PolicyCacheNamespace | None, key: str) -> None:
        if namespace is not None:
            sk = self._storage_key(namespace, key)
            with self._lock:
                self._entries.pop(sk, None)
            client = self._maybe_redis()
            if client is not None:
                try:
                    client.delete(f"rai:policy:{sk}")
                except Exception:
                    self._stats.redis_errors += 1
            self._stats.invalidations += 1
            return
        prefix = key
        with self._lock:
            doomed = [
                k
                for k in list(self._entries)
                if k.endswith(f":{prefix}") or prefix in k
            ]
            for k in doomed:
                del self._entries[k]
        client = self._maybe_redis()
        if client is not None:
            try:
                for pk in list(client.scan_iter(match=f"rai:policy:*{prefix}*", count=50)):
                    client.delete(pk)
            except Exception:
                self._stats.redis_errors += 1
        self._stats.invalidations += len(doomed)

    def invalidate_fast_paths(self, reason: str) -> None:
        """Credential revoke, suspension, delegation revoke, emergency controls."""
        for prefix in _FAST_INVALIDATION_PREFIXES:
            if reason.startswith(prefix.rstrip(":")) or reason.startswith(prefix):
                self.invalidate(None, reason)
                return
        self.invalidate(None, reason)

    def stats(self) -> PolicyCacheStats:
        return self._stats


shared_policy_cache = SharedPolicyCache()
