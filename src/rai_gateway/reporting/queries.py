"""Authoritative read queries and aggregations for reporting.

This module queries source domain models without duplicating live state.
All queries MUST be organization-scoped for tenant isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import case, distinct, func, or_
from sqlalchemy.orm import Session

from rai_gateway.finops import cost_expr
from rai_gateway.models.approval import ApprovalRequest
from rai_gateway.models.evaluations import (
    AssuranceRecord,
    DeploymentGateEvaluation,
    EvaluationWaiver,
)
from rai_gateway.models.evidence import (
    EvidenceFinding,
    RuntimeConversation,
    RuntimeTrajectory,
)
from rai_gateway.models.finops import UsageReservation, UsageSettlement
from rai_gateway.models.governance import AIIncident, GovernanceException
from rai_gateway.models.metering import RuntimeMeteringSpan
from rai_gateway.models.registry import (
    Agent,
    AgentVersion,
    AISystem,
    RiskAssessment,
    RiskFinding,
)
from rai_gateway.models.risk_intelligence import (
    RuntimeBudget,
    RuntimeBudgetWindow,
    RuntimeRiskFinding,
)
from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt

if TYPE_CHECKING:
    from rai_gateway.reporting.schemas import ReportFilters

_SEVERITY_BUCKETS = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")


def _upper_in(column, values: list[str] | None):
    if not values:
        return None
    return func.upper(column).in_([value.upper() for value in values])


def _severity_rank(column):
    normalized = func.upper(column)
    return case(
        (normalized == "CRITICAL", 1),
        (normalized == "HIGH", 2),
        (normalized == "MEDIUM", 3),
        (normalized == "LOW", 4),
        (normalized == "INFO", 5),
        else_=6,
    )


def _known_cost(model):
    """Known money for a span or settlement row.

    Delegates to the single FinOps definition so Reports and FinOps reconcile:
    BILLED/METERED/ACTUAL/ESTIMATED rows contribute their amount, and rows with
    UNAVAILABLE/MIXED/NULL cost state contribute nothing (NULL, never 0).
    """
    return cost_expr.known_cost(model)


# ============================================================================
# Risk Intelligence Queries
# ============================================================================


def _scoped_risk_findings(db: Session, filters: ReportFilters):
    """Design-time findings belong to a system through their risk assessment."""
    query = (
        db.query(RiskFinding)
        .join(RiskAssessment, RiskFinding.risk_assessment_id == RiskAssessment.id)
        .join(AISystem, RiskAssessment.ai_system_id == AISystem.id)
    )
    severity = _upper_in(RiskFinding.severity, filters.risk_severity)
    if severity is not None:
        query = query.filter(severity)
    if filters.risk_category:
        query = query.filter(RiskFinding.category.in_(filters.risk_category))
    status = _upper_in(RiskFinding.status, filters.finding_status)
    if status is not None:
        query = query.filter(status)
    if filters.ai_system_id:
        query = query.filter(RiskAssessment.ai_system_id == filters.ai_system_id)
    if filters.period_start:
        query = query.filter(RiskFinding.created_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(RiskFinding.created_at <= filters.period_end)
    return query


def _scoped_runtime_risk_findings(db: Session, filters: ReportFilters):
    """Runtime findings shown on /risk/findings, scoped through the AI system."""
    query = db.query(RuntimeRiskFinding).join(
        AISystem, RuntimeRiskFinding.ai_system_id == AISystem.id
    )
    if filters.period_start:
        query = query.filter(RuntimeRiskFinding.created_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(RuntimeRiskFinding.created_at <= filters.period_end)
    severity = _upper_in(RuntimeRiskFinding.severity, filters.risk_severity)
    if severity is not None:
        query = query.filter(severity)
    status = _upper_in(RuntimeRiskFinding.status, filters.finding_status)
    if status is not None:
        query = query.filter(status)
    if filters.ai_system_id:
        query = query.filter(RuntimeRiskFinding.ai_system_id == filters.ai_system_id)
    return query


def _scoped_evidence_findings(db: Session, filters: ReportFilters):
    """Findings recorded on trajectories, scoped through the owning AI system."""
    query = (
        db.query(EvidenceFinding)
        .join(RuntimeTrajectory, EvidenceFinding.trajectory_id == RuntimeTrajectory.id)
        .join(AISystem, RuntimeTrajectory.ai_system_id == AISystem.id)
    )
    if filters.period_start:
        query = query.filter(EvidenceFinding.created_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(EvidenceFinding.created_at <= filters.period_end)
    severity = _upper_in(EvidenceFinding.severity, filters.risk_severity)
    if severity is not None:
        query = query.filter(severity)
    status = _upper_in(EvidenceFinding.status, filters.finding_status)
    if status is not None:
        query = query.filter(status)
    if filters.ai_system_id:
        query = query.filter(RuntimeTrajectory.ai_system_id == filters.ai_system_id)
    return query


def count_risk_findings(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count design-time and runtime risk findings with filters."""
    design = (
        _scoped_risk_findings(db, filters)
        .with_entities(func.count(RiskFinding.id))
        .scalar()
        or 0
    )
    runtime = count_runtime_risk_findings(db, filters)
    evidence = (
        _scoped_evidence_findings(db, filters)
        .with_entities(func.count(EvidenceFinding.id))
        .scalar()
        or 0
    )
    return int(design) + int(runtime) + int(evidence)


def count_runtime_risk_findings(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count runtime risk findings with filters."""
    return (
        _scoped_runtime_risk_findings(db, filters)
        .with_entities(func.count(RuntimeRiskFinding.id))
        .scalar()
        or 0
    )


def _empty_severity_distribution() -> dict[str, int]:
    return {bucket: 0 for bucket in _SEVERITY_BUCKETS}


def _add_severity_rows(distribution: dict[str, int], rows) -> None:
    for severity, count in rows:
        key = (severity or "").upper()
        if key in distribution:
            distribution[key] += int(count or 0)


def get_severity_distribution(
    db: Session,
    filters: ReportFilters,
) -> dict[str, int]:
    """Get risk finding severity distribution across design-time and runtime findings."""
    distribution = _empty_severity_distribution()
    design = (
        _scoped_risk_findings(db, filters)
        .with_entities(func.upper(RiskFinding.severity), func.count(RiskFinding.id))
        .group_by(func.upper(RiskFinding.severity))
        .all()
    )
    runtime = (
        _scoped_runtime_risk_findings(db, filters)
        .with_entities(
            func.upper(RuntimeRiskFinding.severity), func.count(RuntimeRiskFinding.id)
        )
        .group_by(func.upper(RuntimeRiskFinding.severity))
        .all()
    )
    evidence = (
        _scoped_evidence_findings(db, filters)
        .with_entities(
            func.upper(EvidenceFinding.severity), func.count(EvidenceFinding.id)
        )
        .group_by(func.upper(EvidenceFinding.severity))
        .all()
    )
    _add_severity_rows(distribution, design)
    _add_severity_rows(distribution, runtime)
    _add_severity_rows(distribution, evidence)
    return distribution


def get_finding_status_distribution(
    db: Session,
    filters: ReportFilters,
) -> dict[str, int]:
    """Count findings by status, including runtime findings."""
    distribution: dict[str, int] = {}

    def _add(rows) -> None:
        for status, count in rows:
            key = (status or "UNKNOWN").upper()
            distribution[key] = distribution.get(key, 0) + int(count or 0)

    _add(
        _scoped_risk_findings(db, filters)
        .with_entities(func.upper(RiskFinding.status), func.count(RiskFinding.id))
        .group_by(func.upper(RiskFinding.status))
        .all()
    )
    _add(
        _scoped_runtime_risk_findings(db, filters)
        .with_entities(
            func.upper(RuntimeRiskFinding.status), func.count(RuntimeRiskFinding.id)
        )
        .group_by(func.upper(RuntimeRiskFinding.status))
        .all()
    )
    _add(
        _scoped_evidence_findings(db, filters)
        .with_entities(
            func.upper(EvidenceFinding.status), func.count(EvidenceFinding.id)
        )
        .group_by(func.upper(EvidenceFinding.status))
        .all()
    )
    return distribution


def _merge_system_risk(rows, combined: dict[UUID, dict]) -> None:
    for system_id, name, finding_count, severity_order in rows:
        current = combined.setdefault(
            system_id,
            {"name": name, "count": 0, "order": 99},
        )
        current["name"] = name or current["name"]
        current["count"] += int(finding_count or 0)
        if severity_order is not None:
            current["order"] = min(current["order"], int(severity_order))


def get_risk_by_ai_system(
    db: Session,
    filters: ReportFilters,
    limit: int = 10,
) -> list[tuple[UUID, str, int, str]]:
    """Get AI systems ranked by design-time and runtime finding count.

    Returns: List of (ai_system_id, name, finding_count, highest_severity)
    """
    design_rank = _severity_rank(RiskFinding.severity)
    design = (
        db.query(
            AISystem.id,
            AISystem.name,
            func.count(RiskFinding.id).label("finding_count"),
            func.min(design_rank).label("highest_severity_order"),
        )
        .join(RiskAssessment, RiskAssessment.ai_system_id == AISystem.id)
        .join(RiskFinding, RiskFinding.risk_assessment_id == RiskAssessment.id)
    )
    design_status = _upper_in(RiskFinding.status, filters.finding_status)
    if design_status is not None:
        design = design.filter(design_status)
    design_severity = _upper_in(RiskFinding.severity, filters.risk_severity)
    if design_severity is not None:
        design = design.filter(design_severity)
    if filters.ai_system_id:
        design = design.filter(RiskAssessment.ai_system_id == filters.ai_system_id)
    design = design.group_by(AISystem.id, AISystem.name)

    runtime_rank = _severity_rank(RuntimeRiskFinding.severity)
    runtime = db.query(
        AISystem.id,
        AISystem.name,
        func.count(RuntimeRiskFinding.id).label("finding_count"),
        func.min(runtime_rank).label("highest_severity_order"),
    ).join(RuntimeRiskFinding, RuntimeRiskFinding.ai_system_id == AISystem.id)
    runtime_status = _upper_in(RuntimeRiskFinding.status, filters.finding_status)
    if runtime_status is not None:
        runtime = runtime.filter(runtime_status)
    runtime_severity = _upper_in(RuntimeRiskFinding.severity, filters.risk_severity)
    if runtime_severity is not None:
        runtime = runtime.filter(runtime_severity)
    if filters.ai_system_id:
        runtime = runtime.filter(
            RuntimeRiskFinding.ai_system_id == filters.ai_system_id
        )
    runtime = runtime.group_by(AISystem.id, AISystem.name)

    evidence_rank = _severity_rank(EvidenceFinding.severity)
    evidence = (
        db.query(
            AISystem.id,
            AISystem.name,
            func.count(EvidenceFinding.id).label("finding_count"),
            func.min(evidence_rank).label("highest_severity_order"),
        )
        .join(RuntimeTrajectory, RuntimeTrajectory.ai_system_id == AISystem.id)
        .join(EvidenceFinding, EvidenceFinding.trajectory_id == RuntimeTrajectory.id)
    )
    evidence_status = _upper_in(EvidenceFinding.status, filters.finding_status)
    if evidence_status is not None:
        evidence = evidence.filter(evidence_status)
    evidence_severity = _upper_in(EvidenceFinding.severity, filters.risk_severity)
    if evidence_severity is not None:
        evidence = evidence.filter(evidence_severity)
    if filters.ai_system_id:
        evidence = evidence.filter(
            RuntimeTrajectory.ai_system_id == filters.ai_system_id
        )
    evidence = evidence.group_by(AISystem.id, AISystem.name)

    combined: dict[UUID, dict] = {}
    _merge_system_risk(design.all(), combined)
    _merge_system_risk(runtime.all(), combined)
    _merge_system_risk(evidence.all(), combined)
    ranked = sorted(
        combined.items(),
        key=lambda item: (item[1]["order"], -item[1]["count"]),
    )[:limit]
    severity_map = {1: "CRITICAL", 2: "HIGH", 3: "MEDIUM", 4: "LOW", 5: "INFO"}
    return [
        (
            system_id,
            data["name"],
            data["count"],
            severity_map.get(data["order"], "UNKNOWN"),
        )
        for system_id, data in ranked
    ]


def get_risk_by_agent(
    db: Session,
    filters: ReportFilters,
    limit: int = 5,
) -> list[tuple[UUID, str, int]]:
    """Rank agents in scope by design-time finding count."""
    query = (
        db.query(
            Agent.id,
            Agent.name,
            func.count(RiskFinding.id).label("finding_count"),
        )
        .join(AISystem, Agent.ai_system_id == AISystem.id)
        .join(AgentVersion, AgentVersion.agent_id == Agent.id)
        .join(RiskAssessment, RiskAssessment.agent_version_id == AgentVersion.id)
        .join(RiskFinding, RiskFinding.risk_assessment_id == RiskAssessment.id)
    )
    if filters.ai_system_id:
        query = query.filter(Agent.ai_system_id == filters.ai_system_id)
    status = _upper_in(RiskFinding.status, filters.finding_status)
    if status is not None:
        query = query.filter(status)
    severity = _upper_in(RiskFinding.severity, filters.risk_severity)
    if severity is not None:
        query = query.filter(severity)

    rows = (
        query.group_by(Agent.id, Agent.name)
        .order_by(func.count(RiskFinding.id).desc())
        .limit(limit)
        .all()
    )
    return [(row.id, row.name, row.finding_count) for row in rows]


def get_findings_by_source(db: Session, filters: ReportFilters) -> dict[str, int]:
    """Finding counts per independent source table.

    The three sources have separate identities and no cross-reference, so the
    report total is their plain sum with no deduplication. ``runtime`` and
    ``evidence`` findings can describe the same trajectory event; exposing the
    split lets readers see (and discount) any overlap instead of hiding it.
    """
    return {
        "design_time": int(
            _scoped_risk_findings(db, filters)
            .with_entities(func.count(RiskFinding.id))
            .scalar()
            or 0
        ),
        "runtime": count_runtime_risk_findings(db, filters),
        "evidence": int(
            _scoped_evidence_findings(db, filters)
            .with_entities(func.count(EvidenceFinding.id))
            .scalar()
            or 0
        ),
    }


def get_category_distribution(db: Session, filters: ReportFilters) -> dict[str, int]:
    """Findings by category for sources that record one (design-time, runtime)."""
    distribution: dict[str, int] = {}

    def _add(rows) -> None:
        for category, count in rows:
            key = (category or "UNCATEGORIZED").upper()
            distribution[key] = distribution.get(key, 0) + int(count or 0)

    _add(
        _scoped_risk_findings(db, filters)
        .with_entities(func.upper(RiskFinding.category), func.count(RiskFinding.id))
        .group_by(func.upper(RiskFinding.category))
        .all()
    )
    _add(
        _scoped_runtime_risk_findings(db, filters)
        .with_entities(
            func.upper(RuntimeRiskFinding.category),
            func.count(RuntimeRiskFinding.id),
        )
        .group_by(func.upper(RuntimeRiskFinding.category))
        .all()
    )
    return distribution


# ============================================================================
# Policy Enforcement Queries
# ============================================================================


def count_policy_evaluations(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count policy evaluations (enforcement receipts)."""
    query = db.query(func.count(RuntimeEnforcementReceipt.id)).join(
        AISystem, RuntimeEnforcementReceipt.ai_system_id == AISystem.id
    )

    if filters.period_start:
        query = query.filter(
            RuntimeEnforcementReceipt.started_at >= filters.period_start
        )
    if filters.period_end:
        query = query.filter(RuntimeEnforcementReceipt.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(
            RuntimeEnforcementReceipt.ai_system_id == filters.ai_system_id
        )
    if filters.effect:
        query = query.filter(
            func.upper(RuntimeEnforcementReceipt.policy_effect)
            == filters.effect.upper()
        )

    return query.scalar() or 0


def count_policy_triggers(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count policy triggers (non-ALLOW effects)."""
    query = (
        db.query(func.count(RuntimeEnforcementReceipt.id))
        .join(AISystem, RuntimeEnforcementReceipt.ai_system_id == AISystem.id)
        .filter(
            RuntimeEnforcementReceipt.policy_effect.in_(
                ["MONITOR", "REQUIRE_APPROVAL", "DENY", "SUSPEND"]
            )
        )
    )

    if filters.period_start:
        query = query.filter(
            RuntimeEnforcementReceipt.started_at >= filters.period_start
        )
    if filters.period_end:
        query = query.filter(RuntimeEnforcementReceipt.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(
            RuntimeEnforcementReceipt.ai_system_id == filters.ai_system_id
        )
    if filters.effect:
        query = query.filter(
            func.upper(RuntimeEnforcementReceipt.policy_effect)
            == filters.effect.upper()
        )

    return query.scalar() or 0


def get_effect_distribution(
    db: Session,
    filters: ReportFilters,
) -> dict[str, int]:
    """Get policy effect distribution."""
    query = db.query(
        RuntimeEnforcementReceipt.policy_effect,
        func.count(RuntimeEnforcementReceipt.id),
    ).join(AISystem, RuntimeEnforcementReceipt.ai_system_id == AISystem.id)

    if filters.period_start:
        query = query.filter(
            RuntimeEnforcementReceipt.started_at >= filters.period_start
        )
    if filters.period_end:
        query = query.filter(RuntimeEnforcementReceipt.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(
            RuntimeEnforcementReceipt.ai_system_id == filters.ai_system_id
        )

    if filters.effect:
        query = query.filter(
            func.upper(RuntimeEnforcementReceipt.policy_effect)
            == filters.effect.upper()
        )

    query = query.group_by(RuntimeEnforcementReceipt.policy_effect)

    results = query.all()
    distribution = {
        "allow": 0,
        "monitor": 0,
        "require_approval": 0,
        "deny": 0,
        "suspend": 0,
    }

    for effect, count in results:
        if effect:
            key = effect.lower()
            if key in distribution:
                distribution[key] = count

    return distribution


def count_denied_actions(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count denied actions."""
    query = (
        db.query(func.count(RuntimeEnforcementReceipt.id))
        .join(AISystem, RuntimeEnforcementReceipt.ai_system_id == AISystem.id)
        .filter(RuntimeEnforcementReceipt.enforcement_decision == "DENY")
    )
    if filters.effect:
        query = query.filter(
            func.upper(RuntimeEnforcementReceipt.policy_effect)
            == filters.effect.upper()
        )

    if filters.period_start:
        query = query.filter(
            RuntimeEnforcementReceipt.started_at >= filters.period_start
        )
    if filters.period_end:
        query = query.filter(RuntimeEnforcementReceipt.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(
            RuntimeEnforcementReceipt.ai_system_id == filters.ai_system_id
        )

    return query.scalar() or 0


def _scoped_receipts(db: Session, filters: ReportFilters):
    query = db.query(RuntimeEnforcementReceipt).join(
        AISystem, RuntimeEnforcementReceipt.ai_system_id == AISystem.id
    )
    if filters.period_start:
        query = query.filter(
            RuntimeEnforcementReceipt.started_at >= filters.period_start
        )
    if filters.period_end:
        query = query.filter(RuntimeEnforcementReceipt.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(
            RuntimeEnforcementReceipt.ai_system_id == filters.ai_system_id
        )
    if filters.effect:
        query = query.filter(
            func.upper(RuntimeEnforcementReceipt.policy_effect)
            == filters.effect.upper()
        )
    return query


def get_receipt_distribution(
    db: Session, filters: ReportFilters, column_name: str
) -> dict[str, int]:
    """Receipts grouped by ``enforcement_decision`` or ``execution_status``."""
    if column_name not in {"enforcement_decision", "execution_status"}:
        raise ValueError(f"Unsupported receipt column: {column_name}")
    column = getattr(RuntimeEnforcementReceipt, column_name)
    rows = (
        _scoped_receipts(db, filters)
        .with_entities(func.upper(column), func.count(RuntimeEnforcementReceipt.id))
        .group_by(func.upper(column))
        .all()
    )
    return {(key or "UNKNOWN"): int(count or 0) for key, count in rows}


def count_systems_with_governed_activity(db: Session, filters: ReportFilters) -> int:
    """Distinct AI systems with at least one enforcement receipt in scope.

    This is *observed* governed activity, not a coverage ratio: a registered
    system that has not called the gateway in the period is not counted.
    """
    return int(
        _scoped_receipts(db, filters)
        .with_entities(func.count(distinct(RuntimeEnforcementReceipt.ai_system_id)))
        .scalar()
        or 0
    )


# ============================================================================
# Budget Queries
# ============================================================================


def count_budgets(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count active budgets."""
    query = (
        db.query(func.count(RuntimeBudget.id))
        .join(AISystem, RuntimeBudget.ai_system_id == AISystem.id)
        .filter(
            RuntimeBudget.enabled.is_(True),
        )
    )

    if filters.ai_system_id:
        query = query.filter(RuntimeBudget.ai_system_id == filters.ai_system_id)
    if filters.budget_metric:
        query = query.filter(RuntimeBudget.metric == filters.budget_metric)

    return query.scalar() or 0


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _active_window():
    """Budget windows that contain 'now' (historical windows are excluded)."""
    now = _utc_now()
    return (RuntimeBudgetWindow.window_start <= now) & (
        RuntimeBudgetWindow.window_end > now
    )


def count_budgets_by_status(
    db: Session,
    filters: ReportFilters,
    status_filter: str,
) -> int:
    """Count budgets whose *current* window is in the given status."""
    query = (
        db.query(func.count(distinct(RuntimeBudgetWindow.budget_id)))
        .join(RuntimeBudget, RuntimeBudgetWindow.budget_id == RuntimeBudget.id)
        .join(AISystem, RuntimeBudget.ai_system_id == AISystem.id)
        .filter(RuntimeBudgetWindow.status == status_filter, _active_window())
    )
    if filters.budget_metric:
        query = query.filter(RuntimeBudget.metric == filters.budget_metric)

    if filters.ai_system_id:
        query = query.filter(RuntimeBudget.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0


def get_budget_utilization(
    db: Session,
    filters: ReportFilters,
) -> list[dict]:
    """Get budget utilization details."""
    query = (
        db.query(
            RuntimeBudget.id,
            RuntimeBudget.name,
            RuntimeBudget.metric,
            RuntimeBudget.limit_value,
            RuntimeBudget.warning_threshold,
            RuntimeBudgetWindow.usage_value,
            RuntimeBudgetWindow.status,
            RuntimeBudgetWindow.window_start,
        )
        .join(RuntimeBudgetWindow, RuntimeBudgetWindow.budget_id == RuntimeBudget.id)
        .join(AISystem, RuntimeBudget.ai_system_id == AISystem.id)
        .filter(
            RuntimeBudget.enabled.is_(True),
            _active_window(),
        )
    )

    if filters.ai_system_id:
        query = query.filter(RuntimeBudget.ai_system_id == filters.ai_system_id)
    if filters.budget_metric:
        query = query.filter(RuntimeBudget.metric == filters.budget_metric)

    # A budget can have one active window per scope; report its worst case so
    # each budget appears once. Utilization is only comparable within a metric.
    worst: dict[UUID, dict] = {}
    for row in query.all():
        utilization_pct = 0.0
        if row.limit_value and row.limit_value > 0:
            utilization_pct = (row.usage_value / row.limit_value) * 100
        current = worst.get(row.id)
        if current is not None and current["utilization_pct"] >= utilization_pct:
            continue
        worst[row.id] = {
            "id": row.id,
            "name": row.name,
            "metric": row.metric,
            "current_usage": row.usage_value,
            "limit_value": row.limit_value,
            "warning_threshold": row.warning_threshold,
            "utilization_pct": round(utilization_pct, 2),
            "status": row.status,
            "window": row.window_start.isoformat() if row.window_start else "",
        }

    return sorted(worst.values(), key=lambda item: -item["utilization_pct"])


def _span_occurred_at():
    return func.coalesce(RuntimeMeteringSpan.started_at, RuntimeMeteringSpan.created_at)


def _scoped_metering_spans(db: Session, filters: ReportFilters):
    """Trajectory cost the FinOps and trajectory views already display."""
    occurred = _span_occurred_at()
    query = db.query(RuntimeMeteringSpan)
    if filters.period_start:
        query = query.filter(occurred >= filters.period_start)
    if filters.period_end:
        query = query.filter(occurred <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(RuntimeMeteringSpan.ai_system_id == filters.ai_system_id)
    return query


def _scoped_usage_settlements(db: Session, filters: ReportFilters):
    """Settlements that are not already represented by a metering span."""
    query = (
        db.query(UsageSettlement)
        .outerjoin(
            UsageReservation, UsageSettlement.reservation_id == UsageReservation.id
        )
        .filter(UsageSettlement.metering_span_id.is_(None))
    )
    if filters.period_start:
        query = query.filter(UsageSettlement.created_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(UsageSettlement.created_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(
            or_(
                UsageSettlement.ai_system_id == filters.ai_system_id,
                UsageReservation.ai_system_id == filters.ai_system_id,
            )
        )
    return query


def _sum_known_cost(query, model) -> float:
    result = query.with_entities(
        func.coalesce(func.sum(_known_cost(model)), 0.0)
    ).scalar()
    return float(result or 0.0)


def get_total_cost(
    db: Session,
    filters: ReportFilters,
) -> float:
    """Get trajectory and ledger cost for the organization."""
    span_cost = _sum_known_cost(
        _scoped_metering_spans(db, filters),
        RuntimeMeteringSpan,
    )
    settlement_cost = _sum_known_cost(
        _scoped_usage_settlements(db, filters),
        UsageSettlement,
    )
    return span_cost + settlement_cost


def get_total_tokens(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Get trajectory and ledger token count for the organization."""
    span_tokens = (
        _scoped_metering_spans(db, filters)
        .with_entities(func.coalesce(func.sum(RuntimeMeteringSpan.total_tokens), 0))
        .scalar()
        or 0
    )
    settlement_tokens = (
        _scoped_usage_settlements(db, filters)
        .with_entities(func.coalesce(func.sum(UsageSettlement.total_tokens), 0))
        .scalar()
        or 0
    )
    return int(span_tokens) + int(settlement_tokens)


# ============================================================================
# Runtime Activity Queries
# ============================================================================


def count_governed_actions(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count governed actions (enforcement receipts)."""
    return count_policy_evaluations(db, filters)


def count_conversations(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count conversations."""
    query = db.query(func.count(RuntimeConversation.id)).join(
        AISystem, RuntimeConversation.ai_system_id == AISystem.id
    )

    if filters.period_start:
        query = query.filter(RuntimeConversation.started_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(RuntimeConversation.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(RuntimeConversation.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0


def count_executions(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count executions (trajectories)."""
    query = db.query(func.count(RuntimeTrajectory.id)).join(
        AISystem, RuntimeTrajectory.ai_system_id == AISystem.id
    )

    if filters.period_start:
        query = query.filter(RuntimeTrajectory.started_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(RuntimeTrajectory.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(RuntimeTrajectory.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0


def get_execution_success_rate(
    db: Session,
    filters: ReportFilters,
) -> float:
    """Calculate execution success rate."""
    total_query = db.query(func.count(RuntimeTrajectory.id)).join(
        AISystem, RuntimeTrajectory.ai_system_id == AISystem.id
    )

    if filters.period_start:
        total_query = total_query.filter(
            RuntimeTrajectory.started_at >= filters.period_start
        )
    if filters.period_end:
        total_query = total_query.filter(
            RuntimeTrajectory.started_at <= filters.period_end
        )
    if filters.ai_system_id:
        total_query = total_query.filter(
            RuntimeTrajectory.ai_system_id == filters.ai_system_id
        )

    total = total_query.scalar() or 0
    if total == 0:
        return 0.0

    success_query = total_query.filter(RuntimeTrajectory.status == "COMPLETED")
    success = success_query.scalar() or 0

    return (success / total) * 100


def get_execution_status_distribution(
    db: Session, filters: ReportFilters
) -> dict[str, int]:
    """Trajectories started in scope, grouped by their recorded status."""
    query = db.query(
        func.upper(RuntimeTrajectory.status), func.count(RuntimeTrajectory.id)
    ).join(AISystem, RuntimeTrajectory.ai_system_id == AISystem.id)
    if filters.period_start:
        query = query.filter(RuntimeTrajectory.started_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(RuntimeTrajectory.started_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(RuntimeTrajectory.ai_system_id == filters.ai_system_id)
    rows = query.group_by(func.upper(RuntimeTrajectory.status)).all()
    return {(key or "UNKNOWN"): int(count or 0) for key, count in rows}


# ============================================================================
# Approval Queries
# ============================================================================


def count_approval_requests(
    db: Session,
    filters: ReportFilters,
    status_filter: str | None = None,
) -> int:
    """Count approval requests."""
    query = db.query(func.count(ApprovalRequest.id)).join(
        AISystem, ApprovalRequest.ai_system_id == AISystem.id
    )

    if status_filter:
        query = query.filter(ApprovalRequest.status == status_filter)
    if filters.period_start:
        query = query.filter(ApprovalRequest.created_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(ApprovalRequest.created_at <= filters.period_end)
    if filters.ai_system_id:
        query = query.filter(ApprovalRequest.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0


# ============================================================================
# Assurance Queries
# ============================================================================


def count_assurance_records(
    db: Session,
    filters: ReportFilters,
    status_filter: str | None = None,
) -> int:
    """Count assurance records."""
    query = db.query(func.count(AssuranceRecord.id)).join(
        AISystem, AssuranceRecord.ai_system_id == AISystem.id
    )

    if status_filter:
        query = query.filter(AssuranceRecord.status == status_filter)
    if filters.ai_system_id:
        query = query.filter(AssuranceRecord.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0


def count_ai_systems(
    db: Session,
    filters: ReportFilters,
) -> int:
    """Count registered AI systems in scope (the coverage denominator)."""
    query = db.query(func.count(AISystem.id))
    if filters.ai_system_id:
        query = query.filter(AISystem.id == filters.ai_system_id)

    return query.scalar() or 0


def get_effective_assurance_by_system(
    db: Session, filters: ReportFilters
) -> dict[UUID, str]:
    """Effective status of each system's current assurance record.

    "Current" is the newest record that has not been superseded. A record
    stored as VALID whose ``valid_until`` has passed is reported as EXPIRED so
    coverage never counts lapsed assurance as valid. Systems with no current
    record are absent from the result (they are *unassessed*, not failed).
    """
    now = _utc_now()
    query = (
        db.query(AssuranceRecord)
        .join(AISystem, AssuranceRecord.ai_system_id == AISystem.id)
        .filter(AssuranceRecord.superseded_at.is_(None))
    )
    if filters.ai_system_id:
        query = query.filter(AssuranceRecord.ai_system_id == filters.ai_system_id)

    def _naive(value):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    newest: dict[UUID, AssuranceRecord] = {}
    for record in query.all():
        held = newest.get(record.ai_system_id)
        created = _naive(record.created_at) or now
        if held is None or created > (_naive(held.created_at) or now):
            newest[record.ai_system_id] = record

    effective: dict[UUID, str] = {}
    for system_id, record in newest.items():
        status = (record.status or "").upper()
        valid_until = _naive(record.valid_until)
        if status == "VALID" and valid_until is not None and valid_until < now:
            status = "EXPIRED"
        effective[system_id] = status
    return effective


def count_gate_evaluations(
    db: Session, filters: ReportFilters, decision: str | None = None
) -> int:
    """Deployment gate evaluations in the period (by ``evaluated_at``)."""
    query = db.query(func.count(DeploymentGateEvaluation.id)).join(
        AISystem, DeploymentGateEvaluation.ai_system_id == AISystem.id
    )
    if decision is not None:
        query = query.filter(DeploymentGateEvaluation.decision == decision)
    if filters.period_start:
        query = query.filter(
            DeploymentGateEvaluation.evaluated_at >= filters.period_start
        )
    if filters.period_end:
        query = query.filter(
            DeploymentGateEvaluation.evaluated_at <= filters.period_end
        )
    if filters.ai_system_id:
        query = query.filter(
            DeploymentGateEvaluation.ai_system_id == filters.ai_system_id
        )
    return int(query.scalar() or 0)


def count_incidents(
    db: Session,
    filters: ReportFilters,
    status_filter: str | None = None,
) -> int:
    """Count AI incidents."""
    query = db.query(func.count(AIIncident.id)).join(
        AISystem, AIIncident.ai_system_id == AISystem.id
    )

    if status_filter:
        query = query.filter(AIIncident.status == status_filter)
    if filters.ai_system_id:
        query = query.filter(AIIncident.ai_system_id == filters.ai_system_id)
    if filters.period_start:
        query = query.filter(AIIncident.detected_at >= filters.period_start)
    if filters.period_end:
        query = query.filter(AIIncident.detected_at <= filters.period_end)

    return query.scalar() or 0


def count_exceptions(
    db: Session,
    filters: ReportFilters,
    status_filter: str | None = None,
) -> int:
    """Count governance exceptions."""
    query = db.query(func.count(GovernanceException.id)).join(
        AISystem, GovernanceException.ai_system_id == AISystem.id
    )

    if status_filter:
        query = query.filter(GovernanceException.status == status_filter)
    if filters.ai_system_id:
        query = query.filter(GovernanceException.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0


def count_waivers(
    db: Session,
    filters: ReportFilters,
    active_only: bool = True,
) -> int:
    """Count evaluation waivers."""
    query = db.query(func.count(EvaluationWaiver.id)).join(
        AISystem, EvaluationWaiver.ai_system_id == AISystem.id
    )

    if active_only:
        now = _utc_now()
        query = query.filter(
            EvaluationWaiver.status == "APPROVED",
            EvaluationWaiver.revoked_at.is_(None),
            or_(
                EvaluationWaiver.valid_until.is_(None),
                EvaluationWaiver.valid_until >= now,
            ),
        )
    if filters.ai_system_id:
        query = query.filter(EvaluationWaiver.ai_system_id == filters.ai_system_id)

    return query.scalar() or 0
