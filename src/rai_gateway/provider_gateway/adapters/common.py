"""Shared helpers for provider protocol adapters."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from rai_gateway.provider_gateway.domain import UsageConfidence, UsageInfo

_JSON_CONTENT = re.compile(r"application/json", re.I)


def load_json(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def strip_route_prefix(path: str, prefix: str) -> str:
    if not path.startswith(prefix):
        return path
    suffix = path[len(prefix) :]
    if suffix and not suffix.startswith("/"):
        suffix = f"/{suffix}"
    return suffix or "/"


def declared_tool_names(body: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for entry in body.get("tools") or []:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
        elif entry.get("name"):
            names.append(str(entry["name"]))
    for entry in body.get("functions") or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    return names


def max_output_tokens(body: dict[str, Any]) -> int | None:
    for key in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
        value = body.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    generation_config = body.get("generationConfig")
    if isinstance(generation_config, dict):
        value = generation_config.get("maxOutputTokens")
        if isinstance(value, int) and value >= 0:
            return value
    return None


def normalize_usage(
    *,
    provider: str | None,
    model: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    confidence: UsageConfidence = UsageConfidence.UNKNOWN,
    provider_reported: bool = False,
) -> UsageInfo | None:
    if not any(
        v is not None
        for v in (
            input_tokens,
            output_tokens,
            total_tokens,
            cached_input_tokens,
            reasoning_tokens,
        )
    ):
        return None
    return UsageInfo(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=reasoning_tokens,
        confidence=confidence,
        provider_reported=provider_reported,
    )


def usage_from_mapping(
    usage: Mapping[str, Any] | None,
    *,
    provider: str,
    model: str | None = None,
    input_key: str = "input_tokens",
    output_key: str = "output_tokens",
    total_key: str = "total_tokens",
    cached_key: str | None = None,
) -> UsageInfo | None:
    if not usage:
        return None
    cached = usage.get(cached_key) if cached_key else None
    if cached is None:
        cached = usage.get("cache_read_input_tokens")
    if cached is None and "cache_creation_input_tokens" in usage:
        cached = usage.get("cache_creation_input_tokens")
    return normalize_usage(
        provider=provider,
        model=model,
        input_tokens=_int_or_none(usage.get(input_key)),
        output_tokens=_int_or_none(usage.get(output_key)),
        total_tokens=_int_or_none(usage.get(total_key)),
        cached_input_tokens=_int_or_none(cached),
        confidence=UsageConfidence.PROVIDER_REPORTED,
        provider_reported=True,
    )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def sse_json_lines_collector(
    *,
    on_event: Any,
) -> type:
    """Build a stream collector that parses SSE ``data:`` JSON lines."""

    class _Collector:
        def __init__(self) -> None:
            self._buffer = b""
            self._usage: UsageInfo | None = None

        def feed(self, chunk: bytes) -> None:
            self._buffer += chunk
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload in {b"[DONE]", b""}:
                    continue
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    parsed = on_event(event)
                    if parsed is not None:
                        self._usage = parsed

        def usage(self) -> UsageInfo | None:
            return self._usage

    return _Collector