"""Gateway FinOps bridge — catalog estimates, reservation metadata, settlement hooks."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.provider_gateway.domain import UsageInfo
from rai_gateway.runtime_enforcement.gateway_action import GatewayDecision


def _token_guess(request_bytes: int, streaming: bool) -> tuple[int, int]:
    est_in = max(1, request_bytes // 4)
    est_out = max(256, est_in // 2) if streaming else max(64, est_in // 4)
    if streaming:
        est_out = max(est_out, 512)
    return est_in, est_out


def estimate_for_gateway_execution(
    db: Session | None,
    *,
    provider: str | None,
    model: str | None,
    request_bytes: int,
    streaming: bool,
) -> dict[str, Any]:
    """Use RaiCentral pricing catalog when available; otherwise a bounded heuristic."""
    est_in, est_out = _token_guess(request_bytes, streaming)
    if db is None or not provider or not model:
        return {
            "estimated_input_tokens": est_in,
            "estimated_output_tokens": est_out,
            "estimated_tokens": est_in + est_out,
            "estimated_cost_usd": None,
            "pricing_version": "gateway-heuristic-v1",
            "cost_confidence": "low",
            "metering_source": "ESTIMATED",
        }
    try:
        from rai_gateway.finops.service import FinOpsService

        breakdown = FinOpsService(db).estimate_cost(
            provider=provider,
            model=model,
            input_tokens=est_in,
            output_tokens=est_out,
            tokens_are_estimated=True,
        )
        return {
            "estimated_input_tokens": est_in,
            "estimated_output_tokens": est_out,
            "estimated_tokens": est_in + est_out,
            "estimated_cost_usd": breakdown.get("estimated_cost"),
            "currency": breakdown.get("currency") or "USD",
            "pricing_entry_id": breakdown.get("pricing_entry_id"),
            "pricing_version": breakdown.get("catalog_version") or "finops-catalog",
            "cost_confidence": breakdown.get("cost_confidence") or "catalog",
            "metering_source": "ESTIMATED",
        }
    except Exception:
        return {
            "estimated_input_tokens": est_in,
            "estimated_output_tokens": est_out,
            "estimated_tokens": est_in + est_out,
            "estimated_cost_usd": None,
            "pricing_version": "gateway-heuristic-v1",
            "cost_confidence": "low",
            "metering_source": "ESTIMATED",
        }


def _reservation_from_decision(decision: GatewayDecision | None) -> str | None:
    if decision is None:
        return None
    pd = None
    if decision.enforcement_result and decision.enforcement_result.policy_decision:
        pd = decision.enforcement_result.policy_decision
    if not isinstance(pd, dict):
        return None
    for key in ("finops_reservation_id", "reservation_id"):
        raw = pd.get(key)
        if raw:
            return str(raw)
    estimate = pd.get("finops_estimate")
    if isinstance(estimate, dict) and estimate.get("reservation_id"):
        return str(estimate["reservation_id"])
    return None


def usage_for_settlement(usage: UsageInfo | None) -> dict[str, Any] | None:
    if usage is None or usage.empty:
        return None
    payload = usage.to_dict()
    payload["source"] = (
        "PROVIDER_RESPONSE" if usage.provider_reported else "GATEWAY_OBSERVED"
    )
    return payload


def attach_finops_metadata(
    base: dict[str, Any],
    *,
    model: str | None,
    request_bytes: int,
    streaming: bool,
    usage: UsageInfo | None,
    succeeded: bool,
    pre_estimate: dict[str, Any] | None = None,
    decision: GatewayDecision | None = None,
) -> dict[str, Any]:
    estimate = dict(
        pre_estimate
        or estimate_for_gateway_execution(
            None,
            provider=base.get("provider"),
            model=model,
            request_bytes=request_bytes,
            streaming=streaming,
        )
    )
    reservation_id = _reservation_from_decision(decision)
    actual_tokens = None
    if usage is not None and not usage.empty:
        actual_tokens = usage.total_tokens
    variance = None
    est_total = estimate.get("estimated_tokens")
    if actual_tokens is not None and est_total is not None:
        variance = actual_tokens - int(est_total)
    cost_variance = None
    if usage is not None and estimate.get("estimated_cost_usd") is not None:
        # Provider-reported cost may arrive via settlement; tokens drive variance here.
        cost_variance = None
    finops: dict[str, Any] = {
        **estimate,
        "actual_tokens": actual_tokens,
        "variance_tokens": variance,
        "cost_variance_usd": cost_variance,
        "succeeded": succeeded,
        "reservation_id": reservation_id,
    }
    usage_payload = usage_for_settlement(usage)
    if usage_payload:
        existing = base.get("usage")
        if isinstance(existing, dict):
            usage_payload = {**existing, **usage_payload}
        usage_payload.setdefault("provider", base.get("provider"))
        if model:
            usage_payload.setdefault("model", model)
        finops["usage"] = usage_payload
        base["usage"] = usage_payload
    base["finops"] = finops
    return base


def release_reservation(db: Session, reservation_id: str, *, reason: str) -> None:
    try:
        from rai_gateway.finops.service import FinOpsService

        FinOpsService(db).release(UUID(reservation_id), reason=reason)
        db.commit()
    except Exception:
        db.rollback()