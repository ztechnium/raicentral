"""FinOps reservation and variance metadata on gateway receipts (Step 8 §6)."""

from __future__ import annotations

from typing import Any

from rai_gateway.provider_gateway.domain import UsageInfo


def estimate_before_execution(
    *,
    model: str | None,
    request_bytes: int,
    streaming: bool,
) -> dict[str, Any]:
    est_tokens = max(1, request_bytes // 4)
    if streaming:
        est_tokens = max(est_tokens, 512)
    return {
        "estimated_tokens": est_tokens,
        "estimated_cost_usd": None,
        "pricing_version": "gateway-heuristic-v1",
        "cost_confidence": "low",
    }


def attach_finops_metadata(
    base: dict[str, Any],
    *,
    model: str | None,
    request_bytes: int,
    streaming: bool,
    usage: UsageInfo | None,
    succeeded: bool,
) -> dict[str, Any]:
    estimate = estimate_before_execution(
        model=model, request_bytes=request_bytes, streaming=streaming
    )
    actual_tokens = None
    if usage is not None and not usage.empty:
        actual_tokens = usage.total_tokens
    variance = None
    if actual_tokens is not None and estimate["estimated_tokens"] is not None:
        variance = actual_tokens - int(estimate["estimated_tokens"])
    base["finops"] = {
        **estimate,
        "actual_tokens": actual_tokens,
        "variance_tokens": variance,
        "succeeded": succeeded,
    }
    return base
