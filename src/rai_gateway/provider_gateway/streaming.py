"""Streaming relay: passes upstream bytes through untouched while observing usage.

The relay never buffers the whole stream and never alters bytes. After the
stream ends - normally, by upstream error, or by client cancellation - the
``on_done`` callback is invoked exactly once so the receipt is always
finalized. No retry is ever attempted once the first byte has been sent.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx

from rai_gateway.provider_gateway.adapters.base import StreamUsageCollector
from rai_gateway.provider_gateway.domain import UsageInfo

# (outcome, usage, bytes_streamed, duration_ms); outcome: completed|cancelled|error
StreamDone = Callable[[str, UsageInfo | None, int, int], Awaitable[None]]


async def relay_stream(
    response: httpx.Response,
    collector: StreamUsageCollector,
    on_done: StreamDone,
) -> AsyncIterator[bytes]:
    started = time.monotonic()
    streamed = 0
    outcome = "completed"
    try:
        async for chunk in response.aiter_bytes():
            streamed += len(chunk)
            try:
                collector.feed(chunk)
            except Exception:  # observation must never break the relay
                pass
            yield chunk
    except GeneratorExit:
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        await response.aclose()
        try:
            usage = collector.usage()
        except Exception:
            usage = None
        await on_done(
            outcome, usage, streamed, int((time.monotonic() - started) * 1000)
        )
