"""Request limits and concurrency guards for the provider gateway."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway import errors


@dataclass
class LimitCheckResult:
    allowed: bool
    reason: str | None = None


class GatewayLimits:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._global_streams = 0
        self._per_runtime: dict[str, int] = defaultdict(int)
        self._per_provider: dict[str, int] = defaultdict(int)
        self._rate_windows: dict[str, deque[float]] = defaultdict(deque)

    def check_headers(self, headers: dict[str, str]) -> LimitCheckResult:
        total = sum(len(k.encode()) + len(v.encode()) for k, v in headers.items())
        cap = settings.provider_gateway_max_header_bytes
        if total > cap:
            return LimitCheckResult(False, f"headers exceed {cap} bytes")
        return LimitCheckResult(True)

    def check_rate(self, runtime_key: str) -> LimitCheckResult:
        limit = settings.provider_gateway_rate_limit_per_runtime_per_minute
        if limit <= 0:
            return LimitCheckResult(True)
        now = time.monotonic()
        window = 60.0
        with self._lock:
            dq = self._rate_windows[runtime_key]
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= limit:
                return LimitCheckResult(False, "runtime rate limit exceeded")
            dq.append(now)
        return LimitCheckResult(True)

    def acquire_stream(
        self, *, runtime_key: str | None, provider: str | None
    ) -> LimitCheckResult:
        with self._lock:
            if self._global_streams >= settings.provider_gateway_max_concurrent_streams:
                return LimitCheckResult(False, "global concurrent stream limit")
            if runtime_key:
                if (
                    self._per_runtime[runtime_key]
                    >= settings.provider_gateway_max_concurrent_per_runtime
                ):
                    return LimitCheckResult(False, "runtime concurrent stream limit")
            if provider:
                if (
                    self._per_provider[provider]
                    >= settings.provider_gateway_max_concurrent_per_provider
                ):
                    return LimitCheckResult(False, "provider concurrent stream limit")
            self._global_streams += 1
            if runtime_key:
                self._per_runtime[runtime_key] += 1
            if provider:
                self._per_provider[provider] += 1
        return LimitCheckResult(True)

    def release_stream(
        self, *, runtime_key: str | None, provider: str | None
    ) -> None:
        with self._lock:
            self._global_streams = max(0, self._global_streams - 1)
            if runtime_key:
                self._per_runtime[runtime_key] = max(
                    0, self._per_runtime[runtime_key] - 1
                )
            if provider:
                self._per_provider[provider] = max(0, self._per_provider[provider] - 1)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "global_streams": self._global_streams,
                "active_runtimes": sum(1 for v in self._per_runtime.values() if v > 0),
                "active_providers": sum(
                    1 for v in self._per_provider.values() if v > 0
                ),
            }

    def raise_if_denied(self, result: LimitCheckResult) -> None:
        if result.allowed:
            return
        raise errors.ProviderGatewayError(
            429,
            "REQUEST_LIMITED",
            result.reason or "Request limited by gateway policy",
        )


gateway_limits = GatewayLimits()
