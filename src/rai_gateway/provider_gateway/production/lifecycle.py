"""Graceful shutdown and connection draining for gateway pods."""

from __future__ import annotations

import threading
import time

from rai_gateway.core.config import settings


class GatewayLifecycle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._draining = False
        self._drain_started_at: float | None = None
        self._in_flight = 0

    @property
    def draining(self) -> bool:
        return self._draining

    def start_drain(self) -> None:
        with self._lock:
            self._draining = True
            self._drain_started_at = time.monotonic()

    def clear_drain(self) -> None:
        with self._lock:
            self._draining = False
            self._drain_started_at = None

    def accept_new_requests(self) -> bool:
        if not self._draining:
            return True
        if self._drain_started_at is None:
            return False
        elapsed = time.monotonic() - self._drain_started_at
        return elapsed < float(settings.provider_gateway_drain_seconds)

    def begin_request(self) -> None:
        with self._lock:
            self._in_flight += 1

    def end_request(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def readiness(self) -> dict[str, object]:
        return {
            "draining": self._draining,
            "in_flight": self.in_flight(),
            "accept_new_requests": self.accept_new_requests(),
        }


gateway_lifecycle = GatewayLifecycle()
