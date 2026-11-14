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

_REDIS_KEY_PREFIX = "rai:policy"
_EPOCH_KEY = f"{_REDIS_KEY_PREFIX}:epoch"
_REVOKED_RUNTIME_KEY = f"{_REDIS_KEY_PREFIX}:revoked:runtime"
_REVOKED_DELEGATION_KEY = f"{_REDIS_KEY_PREFIX}:revoked:delegation"
_INVALIDATE_CHANNEL = f"{_REDIS_KEY_PREFIX}:invalidate"

_FAST_INVALIDATION_PREFIXES = (
    "credential_revoke:",
    "runtime_suspend:",
    "delegation_revoke:",
    "emergency:",
)


class PolicyCacheRevocationError(RuntimeError):
    """Raised when cached authority must not be used (revoked / suspended)."""


@dataclass
class CacheEntry:
    namespace: PolicyCacheNamespace
    key: str
    payload: dict[str, Any]
    digest: str
    stored_at: float
    ttl_seconds: float
    expires_at_unix: float
    cache_epoch: int = 0
    authority_revision: str | None = None
    policy_revision: str | None = None
    fast_invalidate: bool = False

    @property
    def expired(self) -> bool:
        if time.time() >= self.expires_at_unix:
            return True
        return time.monotonic() - self.stored_at > self.ttl_seconds

    def to_wire(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace.value,
            "key": self.key,
            "payload": self.payload,
            "digest": self.digest,
            "stored_at": self.stored_at,
            "ttl_seconds": self.ttl_seconds,
            "expires_at_unix": self.expires_at_unix,
            "cache_epoch": self.cache_epoch,
            "authority_revision": self.authority_revision,
            "policy_revision": self.policy_revision,
            "fast_invalidate": self.fast_invalidate,
        }

    @classmethod
    def from_wire(cls, wire: dict[str, Any]) -> CacheEntry:
        expires = float(wire.get("expires_at_unix") or 0)
        if expires <= 0:
            expires = time.time() + float(wire.get("ttl_seconds") or 1)
        payload = dict(wire.get("payload") or {})
        return cls(
            namespace=PolicyCacheNamespace(wire["namespace"]),
            key=str(wire["key"]),
            payload=payload,
            digest=str(wire["digest"]),
            stored_at=time.monotonic(),
            ttl_seconds=float(wire.get("ttl_seconds") or 1),
            expires_at_unix=expires,
            cache_epoch=int(wire.get("cache_epoch") or 0),
            authority_revision=wire.get("authority_revision")
            or payload.get("authority_revision"),
            policy_revision=wire.get("policy_revision")
            or payload.get("policy_revision"),
            fast_invalidate=bool(wire.get("fast_invalidate")),
        )


@dataclass
class PolicyCacheStats:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    invalidations: int = 0
    redis_errors: int = 0
    revocation_checks: int = 0
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
            "revocation_checks": self.revocation_checks,
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
        self._local_epoch = 0
        self._local_revoked_runtime: set[str] = set()
        self._local_revoked_delegation: set[str] = set()

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

    def _sync_epoch(self) -> int:
        client = self._maybe_redis()
        if client is None:
            return self._local_epoch
        try:
            raw = client.get(_EPOCH_KEY)
            if raw is None:
                return self._local_epoch
            epoch = int(raw)
            if epoch > self._local_epoch:
                with self._lock:
                    self._local_epoch = epoch
                    self._purge_stale_local_entries(epoch)
            return max(self._local_epoch, epoch)
        except Exception:
            self._stats.redis_errors += 1
            return self._local_epoch

    def _purge_stale_local_entries(self, epoch: int) -> None:
        doomed = [
            k
            for k, entry in self._entries.items()
            if entry.cache_epoch < epoch or entry.expired
        ]
        for key in doomed:
            del self._entries[key]
            self._stats.evictions += 1

    def _bump_epoch(self) -> int:
        client = self._maybe_redis()
        with self._lock:
            if client is not None:
                try:
                    epoch = int(client.incr(_EPOCH_KEY))
                    self._local_epoch = epoch
                    client.publish(
                        _INVALIDATE_CHANNEL,
                        json.dumps({"epoch": epoch, "type": "epoch_bump"}),
                    )
                    return epoch
                except Exception:
                    self._stats.redis_errors += 1
            self._local_epoch += 1
            return self._local_epoch

    def _current_epoch(self) -> int:
        return self._sync_epoch()

    def put(
        self,
        namespace: PolicyCacheNamespace,
        key: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: float | None = None,
        fast_invalidate: bool = False,
        authority_revision: str | None = None,
        policy_revision: str | None = None,
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
        epoch = self._current_epoch()
        auth_rev = authority_revision or payload.get("authority_revision")
        pol_rev = policy_revision or payload.get("policy_revision")
        if auth_rev is not None:
            payload = {**payload, "authority_revision": auth_rev}
        if pol_rev is not None:
            payload = {**payload, "policy_revision": pol_rev}
        expires_at = time.time() + float(ttl)
        entry = CacheEntry(
            namespace=namespace,
            key=key,
            payload=payload,
            digest=self._digest(payload),
            stored_at=time.monotonic(),
            ttl_seconds=ttl,
            expires_at_unix=expires_at,
            cache_epoch=epoch,
            authority_revision=str(auth_rev) if auth_rev is not None else None,
            policy_revision=str(pol_rev) if pol_rev is not None else None,
            fast_invalidate=fast_invalidate,
        )
        sk = self._storage_key(namespace, key)
        with self._lock:
            self._entries[sk] = entry
        client = self._maybe_redis()
        if client is not None:
            try:
                client.setex(
                    f"{_REDIS_KEY_PREFIX}:entry:{sk}",
                    int(max(1, ttl)),
                    json.dumps(entry.to_wire()),
                )
            except Exception:
                self._stats.redis_errors += 1
        return entry

    def get(self, namespace: PolicyCacheNamespace, key: str) -> CacheEntry | None:
        sk = self._storage_key(namespace, key)
        epoch = self._current_epoch()
        with self._lock:
            entry = self._entries.get(sk)
            if entry is not None:
                if entry.expired or entry.cache_epoch < epoch:
                    del self._entries[sk]
                    self._stats.evictions += 1
                    entry = None
                elif entry is not None:
                    self._stats.hits += 1
                    return entry
        client = self._maybe_redis()
        if client is not None:
            try:
                raw = client.get(f"{_REDIS_KEY_PREFIX}:entry:{sk}")
                if raw:
                    wire = json.loads(raw)
                    entry = CacheEntry.from_wire(wire)
                    if entry.expired or entry.cache_epoch < epoch:
                        client.delete(f"{_REDIS_KEY_PREFIX}:entry:{sk}")
                        self._stats.evictions += 1
                    else:
                        with self._lock:
                            self._entries[sk] = entry
                        self._stats.hits += 1
                        return entry
            except Exception:
                self._stats.redis_errors += 1
        self._stats.misses += 1
        return None

    def invalidate(self, namespace: PolicyCacheNamespace | None, key: str) -> None:
        self._bump_epoch()
        if namespace is not None:
            sk = self._storage_key(namespace, key)
            with self._lock:
                self._entries.pop(sk, None)
            client = self._maybe_redis()
            if client is not None:
                try:
                    client.delete(f"{_REDIS_KEY_PREFIX}:entry:{sk}")
                except Exception:
                    self._stats.redis_errors += 1
            self._stats.invalidations += 1
            self._publish_invalidate(namespace=namespace.value, key=key)
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
                for pk in list(
                    client.scan_iter(match=f"{_REDIS_KEY_PREFIX}:entry:*{prefix}*", count=50)
                ):
                    client.delete(pk)
            except Exception:
                self._stats.redis_errors += 1
        self._stats.invalidations += len(doomed)
        self._publish_invalidate(key=prefix)

    def _publish_invalidate(
        self, *, namespace: str | None = None, key: str | None = None
    ) -> None:
        client = self._maybe_redis()
        if client is None:
            return
        try:
            client.publish(
                _INVALIDATE_CHANNEL,
                json.dumps(
                    {
                        "epoch": self._local_epoch,
                        "namespace": namespace,
                        "key": key,
                        "type": "invalidate",
                    }
                ),
            )
        except Exception:
            self._stats.redis_errors += 1

    def invalidate_fast_paths(self, reason: str) -> None:
        """Credential revoke, suspension, delegation revoke, emergency controls."""
        for prefix in _FAST_INVALIDATION_PREFIXES:
            if reason.startswith(prefix.rstrip(":")) or reason.startswith(prefix):
                self.invalidate(None, reason)
                return
        self.invalidate(None, reason)

    def mark_runtime_revoked(
        self, runtime_identity_id: str, *, reason: str | None = None
    ) -> None:
        rid = str(runtime_identity_id)
        with self._lock:
            self._local_revoked_runtime.add(rid)
        self.invalidate(PolicyCacheNamespace.RUNTIME_IDENTITY, rid)
        self.invalidate(PolicyCacheNamespace.AUTHORITY_SNAPSHOT, rid)
        client = self._maybe_redis()
        if client is not None:
            try:
                client.sadd(_REVOKED_RUNTIME_KEY, rid)
                if reason:
                    client.setex(
                        f"{_REDIS_KEY_PREFIX}:rev:runtime:{rid}",
                        86400 * 30,
                        reason,
                    )
            except Exception:
                self._stats.redis_errors += 1
        self.invalidate_fast_paths(f"credential_revoke:{rid}")

    def mark_runtime_suspended(
        self, runtime_identity_id: str, *, reason: str | None = None
    ) -> None:
        self.mark_runtime_revoked(
            runtime_identity_id,
            reason=reason or "runtime_suspend",
        )

    def mark_delegation_revoked(
        self, delegation_id: str, *, reason: str | None = None
    ) -> None:
        did = str(delegation_id)
        with self._lock:
            self._local_revoked_delegation.add(did)
        self.invalidate(PolicyCacheNamespace.DELEGATION, did)
        client = self._maybe_redis()
        if client is not None:
            try:
                client.sadd(_REVOKED_DELEGATION_KEY, did)
                if reason:
                    client.setex(
                        f"{_REDIS_KEY_PREFIX}:rev:delegation:{did}",
                        86400 * 30,
                        reason or "revoked",
                    )
            except Exception:
                self._stats.redis_errors += 1
        self.invalidate_fast_paths(f"delegation_revoke:{did}")

    def is_revoked(
        self,
        runtime_identity_id: str | None,
        *,
        delegation_id: str | None = None,
    ) -> bool:
        self._stats.revocation_checks += 1
        if runtime_identity_id and runtime_identity_id in self._local_revoked_runtime:
            return True
        if delegation_id and delegation_id in self._local_revoked_delegation:
            return True
        client = self._maybe_redis()
        if client is None:
            return False
        try:
            if runtime_identity_id and client.sismember(
                _REVOKED_RUNTIME_KEY, str(runtime_identity_id)
            ):
                with self._lock:
                    self._local_revoked_runtime.add(str(runtime_identity_id))
                return True
            if delegation_id and client.sismember(
                _REVOKED_DELEGATION_KEY, str(delegation_id)
            ):
                with self._lock:
                    self._local_revoked_delegation.add(str(delegation_id))
                return True
        except Exception:
            self._stats.redis_errors += 1
            return True  # fail closed when redis revocation state is unreadable
        return False

    def fail_closed_if_revoked(
        self,
        runtime_identity_id: str,
        *,
        delegation_id: str | None = None,
    ) -> None:
        if self.is_revoked(runtime_identity_id, delegation_id=delegation_id):
            raise PolicyCacheRevocationError(
                f"Runtime identity {runtime_identity_id} is revoked or suspended"
            )

    def stats(self) -> PolicyCacheStats:
        return self._stats


shared_policy_cache = SharedPolicyCache()