"""Streaming relay: passes upstream bytes through untouched while observing usage."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from rai_gateway.provider_gateway.adapters.base import StreamUsageCollector
from rai_gateway.provider_gateway.domain import UsageInfo


class StreamLifecyclePhase(StrEnum):
    REQUEST_ACCEPTED = "REQUEST_ACCEPTED"
    UPSTREAM_CONNECTED = "UPSTREAM_CONNECTED"
    FIRST_TOKEN = "FIRST_TOKEN"
    STREAM_CHUNK = "STREAM_CHUNK"
    STREAM_COMPLETED = "STREAM_COMPLETED"
    STREAM_ABORTED = "STREAM_ABORTED"


@dataclass
class StreamLifecycle:
    phases: list[str] = field(default_factory=list)
    time_to_first_token_ms: int | None = None
    total_duration_ms: int = 0
    client_disconnect: bool = False
    upstream_disconnect: bool = False
    usage_available: bool = False

    def record(self, phase: StreamLifecyclePhase) -> None:
        self.phases.append(phase.value)

    def to_dict(self) -> dict[str, object]:
        return {
            "phases": list(self.phases),
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "total_duration_ms": self.total_duration_ms,
            "client_disconnect": self.client_disconnect,
            "upstream_disconnect": self.upstream_disconnect,
            "usage_available": self.usage_available,
        }


# (outcome, usage, bytes_streamed, duration_ms, stream_lifecycle)
StreamDone = Callable[
    [str, UsageInfo | None, int, int, Mapping[str, object]], Awaitable[None]
]


async def relay_stream(
    response: httpx.Response,
    collector: StreamUsageCollector,
    on_done: StreamDone,
    *,
    lifecycle: StreamLifecycle | None = None,
    max_duration_seconds: float | None = None,
) -> AsyncIterator[bytes]:
    from rai_gateway.core.config import settings

    cap = (
        max_duration_seconds
        if max_duration_seconds is not None
        else settings.provider_gateway_max_stream_duration_seconds
    )
    started = time.monotonic()
    streamed = 0
    outcome = "completed"
    tracker = lifecycle or StreamLifecycle()
    tracker.record(StreamLifecyclePhase.UPSTREAM_CONNECTED)
    first_token_at: float | None = None
    try:
        async for chunk in response.aiter_bytes():
            if cap > 0 and (time.monotonic() - started) > cap:
                outcome = "cancelled"
                tracker.client_disconnect = True
                tracker.record(StreamLifecyclePhase.STREAM_ABORTED)
                break
            if chunk:
                if first_token_at is None:
                    first_token_at = time.monotonic()
                    tracker.record(StreamLifecyclePhase.FIRST_TOKEN)
                    tracker.time_to_first_token_ms = int(
                        (first_token_at - started) * 1000
                    )
                tracker.record(StreamLifecyclePhase.STREAM_CHUNK)
            streamed += len(chunk)
            try:
                collector.feed(chunk)
            except Exception:  # observation must never break the relay
                pass
            yield chunk
        tracker.record(StreamLifecyclePhase.STREAM_COMPLETED)
    except GeneratorExit:
        outcome = "cancelled"
        tracker.client_disconnect = True
        tracker.record(StreamLifecyclePhase.STREAM_ABORTED)
        raise
    except Exception:
        outcome = "error"
        tracker.upstream_disconnect = True
        tracker.record(StreamLifecyclePhase.STREAM_ABORTED)
        raise
    finally:
        tracker.total_duration_ms = int((time.monotonic() - started) * 1000)
        await response.aclose()
        try:
            usage = collector.usage()
        except Exception:
            usage = None
        tracker.usage_available = usage is not None and not usage.empty
        await on_done(
            outcome,
            usage,
            streamed,
            tracker.total_duration_ms,
            tracker.to_dict(),
        )