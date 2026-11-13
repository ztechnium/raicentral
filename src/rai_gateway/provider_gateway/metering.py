"""Receipt finalization metadata from actual gateway executions.

Only digests, sizes, status and usage are recorded - never request or response
bodies.
"""

from __future__ import annotations

from typing import Any

from rai_gateway.provider_gateway.domain import UsageInfo


def build_execution_metadata(
    *,
    provider: str,
    operation: str | None,
    model: str | None,
    body_sha256: str,
    request_bytes: int,
    upstream_status: int | None,
    response_bytes: int | None,
    streamed: bool,
    outcome: str,
    usage: UsageInfo | None,
    error: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "provider": provider,
        "operation": operation,
        "model": model,
        "request_body_sha256": body_sha256,
        "request_bytes": request_bytes,
        "upstream_status": upstream_status,
        "response_bytes": response_bytes,
        "streamed": streamed,
        "outcome": outcome,
    }
    if error:
        meta["error"] = error
    if usage is not None and not usage.empty:
        merged = UsageInfo(
            provider=usage.provider or provider,
            model=usage.model or model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )
        meta["usage"] = merged.to_dict()
    return meta
