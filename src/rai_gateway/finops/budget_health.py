"""Read-only budget health for the FinOps workspace.

Budgets are configured and enforced elsewhere (``risk_intelligence.budgets``).
This module only *reports* the current window of each enabled budget, plus a
transparent in-window run-rate projection. It never modifies budgets, windows or
enforcement state, and never sums budgets of different metrics together.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from rai_gateway.models.registry import AISystem
from rai_gateway.models.risk_intelligence import (
    RuntimeBudget,
    RuntimeBudgetCheck,
    RuntimeBudgetWindow,
)
from rai_gateway.risk_intelligence.domain import BudgetEffect, BudgetMetric

# Effects that actually stop or gate execution when a budget is exceeded.
BLOCKING_EFFECTS = frozenset(
    {
        BudgetEffect.DENY.value,
        BudgetEffect.REQUIRE_APPROVAL.value,
        BudgetEffect.SUSPEND_RUNTIME_IDENTITY.value,
    }
)

METRIC_UNITS: dict[str, str] = {
    BudgetMetric.ACTION_COUNT.value: "actions",
    BudgetMetric.TOOL_COUNT.value: "tool calls",
    BudgetMetric.TOKEN_USAGE.value: "tokens",
    BudgetMetric.COST.value: "currency",
    BudgetMetric.AMOUNT.value: "currency",
}

# A projection needs enough of the window observed to mean anything.
MIN_ELAPSED_FRACTION = 0.10
MIN_ELAPSED_SECONDS = 600
CHECK_LOOKBACK_DAYS = 30
_MAX_ROWS = 500


def project_window(
    *,
    usage: float,
    limit: float,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
) -> dict[str, Any]:
    """Linear run-rate projection *inside one budget window only*.

    rate = usage so far / elapsed time. The projection is never carried across
    the window boundary: usage resets there, so extrapolating further would
    invent a model nobody configured. Returns ``status: "insufficient_data"``
    rather than a number whenever the inputs cannot support one.
    """
    base = {
        "method": "linear_run_rate_within_window",
        "observation_seconds": None,
        "burn_rate_per_hour": None,
        "projected_window_usage": None,
        "projected_utilization_pct": None,
        "projected_exhaustion_at": None,
        "will_exceed_before_reset": None,
        "confidence": None,
        "status": "insufficient_data",
        "reason": None,
    }
    total = (window_end - window_start).total_seconds()
    elapsed = (min(now, window_end) - window_start).total_seconds()
    if total <= 0 or limit is None or limit <= 0:
        return {**base, "reason": "Budget has no positive limit or window."}
    if elapsed <= 0:
        return {**base, "reason": "Window has not started."}
    base["observation_seconds"] = int(elapsed)
    if usage <= 0:
        return {**base, "reason": "No usage recorded in this window yet."}
    if elapsed < max(MIN_ELAPSED_SECONDS, total * MIN_ELAPSED_FRACTION):
        return {
            **base,
            "reason": (
                "Less than 10% of the window (and under 10 minutes) has elapsed; "
                "a run rate this early is dominated by noise."
            ),
        }
    rate = usage / elapsed
    projected = rate * total
    exhaustion = None
    if usage >= limit:
        exhaustion = None  # already exceeded; exhaustion is in the past
    elif projected > limit:
        exhaustion = window_start + timedelta(seconds=limit / rate)
    fraction = elapsed / total
    return {
        **base,
        "status": "projected",
        "burn_rate_per_hour": round(rate * 3600, 8),
        "projected_window_usage": round(projected, 8),
        "projected_utilization_pct": round(projected / limit * 100, 1),
        "projected_exhaustion_at": exhaustion.isoformat() if exhaustion else None,
        "will_exceed_before_reset": bool(projected > limit),
        # Honest, coarse confidence: how much of the window the rate rests on.
        "confidence": "high" if fraction >= 0.5 else "medium" if fraction >= 0.25 else "low",
        "reason": (
            "Assumes the average rate so far continues until this window resets. "
            "Spikes, idle periods and a reset at "
            f"{window_end.isoformat()} are not modelled."
        ),
    }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def budget_health(
    db: Session,
    *,
    ai_system_id: UUID | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Current-window state for every enabled budget.

    Only windows containing ``now`` count. Historical windows are not pressure.
    A budget with no current window is reported as ``NO_ACTIVE_WINDOW`` rather
    than being assumed healthy. Budgets are never combined across metrics.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    query = (
        db.query(RuntimeBudget, AISystem.name)
        .outerjoin(AISystem, AISystem.id == RuntimeBudget.ai_system_id)
        .filter(RuntimeBudget.enabled.is_(True))
    )
    if ai_system_id:
        # Organization-wide budgets (no AI system) also constrain this system.
        query = query.filter(
            (RuntimeBudget.ai_system_id == ai_system_id)
            | (RuntimeBudget.ai_system_id.is_(None))
        )
    budgets = query.order_by(RuntimeBudget.name).limit(_MAX_ROWS).all()
    if not budgets:
        return {"as_of": now.isoformat(), "items": [], "summary": _summary([])}

    ids = [b.id for b, _ in budgets]
    windows: dict[Any, list[RuntimeBudgetWindow]] = {}
    for window in (
        db.query(RuntimeBudgetWindow)
        .filter(
            RuntimeBudgetWindow.budget_id.in_(ids),
            RuntimeBudgetWindow.window_start <= now,
            RuntimeBudgetWindow.window_end > now,
        )
        .all()
    ):
        windows.setdefault(window.budget_id, []).append(window)

    since = now - timedelta(days=CHECK_LOOKBACK_DAYS)
    evidence = {
        row.budget_id: row
        for row in db.query(
            RuntimeBudgetCheck.budget_id,
            func.count(RuntimeBudgetCheck.id).label("checks"),
            func.max(RuntimeBudgetCheck.created_at).label("last_at"),
        )
        .filter(
            RuntimeBudgetCheck.budget_id.in_(ids),
            RuntimeBudgetCheck.created_at >= since,
        )
        .group_by(RuntimeBudgetCheck.budget_id)
        .all()
    }
    intervened = {
        row.budget_id: int(row.n)
        for row in db.query(
            RuntimeBudgetCheck.budget_id, func.count(RuntimeBudgetCheck.id).label("n")
        )
        .filter(
            RuntimeBudgetCheck.budget_id.in_(ids),
            RuntimeBudgetCheck.created_at >= since,
            RuntimeBudgetCheck.effect.in_(BLOCKING_EFFECTS),
        )
        .group_by(RuntimeBudgetCheck.budget_id)
        .all()
    }

    items: list[dict[str, Any]] = []
    for budget, system_name in budgets:
        current = windows.get(budget.id) or [None]
        for window in current:
            items.append(
                _item(
                    budget,
                    system_name,
                    window,
                    now,
                    evidence.get(budget.id),
                    intervened.get(budget.id, 0),
                )
            )
    return {"as_of": now.isoformat(), "items": items, "summary": _summary(items)}


def _item(
    budget: RuntimeBudget,
    system_name: str | None,
    window: RuntimeBudgetWindow | None,
    now: datetime,
    evidence: Any,
    interventions: int,
) -> dict[str, Any]:
    limit = float(budget.limit_value)
    blocking = budget.effect_on_exceed in BLOCKING_EFFECTS
    item: dict[str, Any] = {
        "budget_id": str(budget.id),
        "name": budget.name,
        "metric": budget.metric,
        "unit": METRIC_UNITS.get(budget.metric, "units"),
        "scope_type": budget.scope_type,
        "scope_id": budget.scope_id,
        "ai_system_id": str(budget.ai_system_id) if budget.ai_system_id else None,
        "ai_system_name": system_name,
        "tool_name": budget.tool_name,
        "limit_value": limit,
        "window_seconds": budget.window_seconds,
        "warning_threshold": budget.warning_threshold,
        "effect_on_exceed": budget.effect_on_exceed,
        "enforcement_mode": budget.enforcement_mode,
        "blocking": blocking,
        "evidence": {
            "lookback_days": CHECK_LOOKBACK_DAYS,
            "checks": int(evidence.checks) if evidence else 0,
            "last_check_at": _utc(evidence.last_at).isoformat()
            if evidence and evidence.last_at
            else None,
            "blocking_interventions": interventions,
        },
        "window": None,
        "projection": None,
        "status": "NO_ACTIVE_WINDOW",
    }
    # Honest enforcement summary: configured vs. observed.
    if not blocking:
        item["enforcement_summary"] = "Monitor only: exceeding the limit is recorded, not blocked."
    elif item["evidence"]["checks"] == 0:
        item["enforcement_summary"] = (
            f"Configured to {budget.effect_on_exceed}, but no enforcement check was "
            f"recorded in the last {CHECK_LOOKBACK_DAYS} days."
        )
    else:
        item["enforcement_summary"] = (
            f"Configured to {budget.effect_on_exceed}; "
            f"{item['evidence']['checks']} checks recorded in the last "
            f"{CHECK_LOOKBACK_DAYS} days."
        )
    if window is None:
        return item
    start, end = _utc(window.window_start), _utc(window.window_end)
    usage = float(window.usage_value or 0.0)
    reserved = float(window.reserved_value or 0.0)
    item["window"] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "usage": usage,
        "reserved": reserved,
        "headroom": round(limit - usage - reserved, 8),
        "utilization_pct": round(usage / limit * 100, 1) if limit > 0 else None,
        "scope_id": window.scope_id,
    }
    item["status"] = window.status
    item["projection"] = project_window(
        usage=usage, limit=limit, window_start=start, window_end=end, now=now
    )
    return item


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts per budget (a budget with several windows counts at its worst)."""
    rank = {"EXCEEDED": 3, "WARNING": 2, "UNAVAILABLE": 1, "OK": 0}
    worst: dict[str, str] = {}
    for item in items:
        status = item["status"]
        prior = worst.get(item["budget_id"])
        if prior is None or rank.get(status, -1) > rank.get(prior, -1):
            worst[item["budget_id"]] = status
    counts = list(worst.values())
    return {
        "total": len(worst),
        "exceeded": counts.count("EXCEEDED"),
        "warning": counts.count("WARNING"),
        "healthy": counts.count("OK"),
        "unavailable": counts.count("UNAVAILABLE"),
        "no_active_window": counts.count("NO_ACTIVE_WINDOW"),
    }
