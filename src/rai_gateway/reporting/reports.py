"""Report payload composition."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from rai_gateway.models.approval import ApprovalRequest
from rai_gateway.models.evaluations import (
    AssuranceRecord,
    EvaluationWaiver,
)
from rai_gateway.models.evidence import RuntimeConversation, RuntimeTrajectory
from rai_gateway.models.finops import UsageReservation, UsageSettlement
from rai_gateway.models.governance import GovernanceException
from rai_gateway.models.registry import AISystem, RiskAssessment
from rai_gateway.models.risk_intelligence import RuntimeBudgetCheck, RuntimeRiskFinding
from rai_gateway.models.runtime_enforcement import RuntimeEnforcementReceipt
from rai_gateway.reporting import metrics, queries, trends
from rai_gateway.reporting.filters import (
    calculate_change_pct,
    get_default_period,
    get_previous_period,
    get_trend_direction,
)
from rai_gateway.reporting.schemas import (
    AISystemReport,
    AssuranceReport,
    AttentionItem,
    BudgetReport,
    BudgetStatus,
    ConversationReport,
    EffectDistribution,
    Freshness,
    FreshnessMode,
    PolicyReport,
    RankedItem,
    ReportPeriod,
    ReportScope,
    ReportSummary,
    RiskReport,
    RuntimeReport,
    SeverityDistribution,
)

if TYPE_CHECKING:
    from rai_gateway.reporting.schemas import ReportFilters

# Runtime findings use ACKNOWLEDGED and MONITORING; design-time findings use INVESTIGATING.
OPEN_FINDING_STATUSES = ["OPEN", "INVESTIGATING", "ACKNOWLEDGED", "MONITORING"]


def _severity_distribution(data: dict[str, int]) -> SeverityDistribution:
    return SeverityDistribution(
        critical=data.get("CRITICAL", 0),
        high=data.get("HIGH", 0),
        medium=data.get("MEDIUM", 0),
        low=data.get("LOW", 0),
        info=data.get("INFO", 0),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _current_state(filters: ReportFilters, **updates) -> ReportFilters:
    """Filters for *current-state* counts: the period never narrows them."""
    scoped = filters.model_copy(update={"period_start": None, "period_end": None})
    return scoped.model_copy(update=updates) if updates else scoped


def _period_filters(filters: ReportFilters, start: datetime, end: datetime, **updates):
    scoped = filters.model_copy(update={"period_start": start, "period_end": end})
    return scoped.model_copy(update=updates) if updates else scoped


def build_report_summary(
    db: Session,
    filters: ReportFilters,
) -> ReportSummary:
    """Build executive summary report.

    Period-derived KPIs (governed activity, actions, new findings, intervention
    rate, policy effects) use [period_start, period_end] and are compared with
    the previous equivalent period. Current-state KPIs (open findings,
    assurance coverage, budget pressure) ignore the period and carry no
    previous value.
    """
    scoped, start, end = _with_default_period(filters)
    prev_start, prev_end = get_previous_period(start, end)
    current = _period_filters(scoped, start, end)
    previous = _period_filters(scoped, prev_start, prev_end)

    # AI systems: registered (denominator) vs observed governed activity.
    registered = queries.count_ai_systems(db, scoped)
    governed_now = queries.count_systems_with_governed_activity(db, current)
    governed_prev = queries.count_systems_with_governed_activity(db, previous)

    current_actions = queries.count_governed_actions(db, current)
    prev_actions = queries.count_governed_actions(db, previous)

    # Open findings: present state, never narrowed by the period.
    open_state = _current_state(scoped, finding_status=list(OPEN_FINDING_STATUSES))
    open_findings = queries.count_risk_findings(db, open_state)
    severity_dist = queries.get_severity_distribution(db, open_state)

    # New findings: period event, compared with the previous period.
    created_now = queries.count_risk_findings(
        db, current.model_copy(update={"finding_status": None})
    )
    created_prev = queries.count_risk_findings(
        db, previous.model_copy(update={"finding_status": None})
    )

    policy_triggers = queries.count_policy_triggers(db, current)
    policy_evaluations = queries.count_policy_evaluations(db, current)
    intervention_rate = metrics.calculate_rate(policy_triggers, policy_evaluations)
    prev_intervention_rate = metrics.calculate_rate(
        queries.count_policy_triggers(db, previous),
        queries.count_policy_evaluations(db, previous),
    )

    # Budget pressure: only COST-metric budgets share a unit, so only they are
    # comparable. Never average action/token/cost/amount budgets together.
    cost_budgets = [
        b
        for b in queries.get_budget_utilization(db, scoped)
        if (b["metric"] or "").upper() == "COST"
    ]
    max_cost_utilization = (
        max(b["utilization_pct"] for b in cost_budgets) if cost_budgets else None
    )

    # Assurance coverage: systems whose current assurance is effectively VALID.
    assurance = queries.get_effective_assurance_by_system(db, scoped)
    valid_systems = sum(1 for status in assurance.values() if status == "VALID")
    assurance_coverage = (
        metrics.calculate_assurance_coverage(valid_systems, registered)
        if registered
        else None
    )

    effects_data = queries.get_effect_distribution(db, current)

    return ReportSummary(
        scope=ReportScope(
            ai_system_id=scoped.ai_system_id,
            agent_id=scoped.agent_id,
            runtime_identity_id=scoped.runtime_identity_id,
        ),
        period=ReportPeriod(start=start, end=end),
        freshness=Freshness(mode=FreshnessMode.LIVE, last_refreshed_at=_utc_now()),
        ai_systems_governed=metrics.build_comparison_metric(
            governed_now, governed_prev
        ),
        ai_systems_registered=metrics.build_comparison_metric(registered),
        governed_actions=metrics.build_comparison_metric(current_actions, prev_actions),
        current_risk_exposure=metrics.build_comparison_metric(open_findings),
        policy_intervention_rate=metrics.build_comparison_metric(
            intervention_rate, prev_intervention_rate
        ),
        budget_utilization_pct=(
            metrics.build_comparison_metric(max_cost_utilization)
            if max_cost_utilization is not None
            else None
        ),
        assurance_coverage_pct=(
            metrics.build_comparison_metric(assurance_coverage)
            if assurance_coverage is not None
            else None
        ),
        open_critical_findings=metrics.build_comparison_metric(
            severity_dist.get("CRITICAL", 0)
        ),
        open_high_findings=metrics.build_comparison_metric(
            severity_dist.get("HIGH", 0)
        ),
        new_findings=metrics.build_comparison_metric(created_now, created_prev),
        risk_by_severity=_severity_distribution(severity_dist),
        policy_effects=EffectDistribution(
            allow=effects_data.get("allow", 0),
            monitor=effects_data.get("monitor", 0),
            require_approval=effects_data.get("require_approval", 0),
            deny=effects_data.get("deny", 0),
            suspend=effects_data.get("suspend", 0),
        ),
        attention=build_attention_items(db, scoped),
    )


RISK_SEMANTICS = {
    "open_findings": "Current state: findings in an open status right now. The period does not narrow it.",
    "new_findings": "Period event: findings created inside the period.",
    "resolved_findings": (
        "Period cohort: findings created inside the period that are no longer open. "
        "Findings have no resolution timestamp, so findings closed in the period "
        "but created earlier cannot be counted."
    ),
    "severity_distribution": "Current state, open findings only.",
    "category_distribution": "Current state, open findings only (design-time and runtime sources).",
    "status_distribution": "Findings created inside the period, grouped by current status.",
    "risk_by_entity": "Current state, open findings only, ranked by count then highest severity.",
}


def build_risk_report(
    db: Session,
    filters: ReportFilters,
) -> RiskReport:
    """Build risk analysis report (current state vs period events kept apart)."""
    scoped, start, end = _with_default_period(filters)
    in_period = _period_filters(scoped, start, end, finding_status=None)
    open_state = _current_state(scoped, finding_status=list(OPEN_FINDING_STATUSES))

    open_findings = queries.count_risk_findings(db, open_state)
    new_findings = queries.count_risk_findings(db, in_period)
    still_open_from_period = queries.count_risk_findings(
        db, in_period.model_copy(update={"finding_status": list(OPEN_FINDING_STATUSES)})
    )
    resolved_findings = max(0, new_findings - still_open_from_period)

    severity_dist = queries.get_severity_distribution(db, open_state)

    risk_by_system_data = queries.get_risk_by_ai_system(db, open_state, limit=10)
    risk_by_ai_system = [
        RankedItem(
            id=system_id,
            name=name,
            value=finding_count,
            severity=highest_severity,
            link=f"/governance/registry/{system_id}",
        )
        for system_id, name, finding_count, highest_severity in risk_by_system_data
    ]
    risk_by_agent = [
        RankedItem(id=agent_id, name=name, value=count)
        for agent_id, name, count in queries.get_risk_by_agent(db, open_state)
    ]

    finding_trends_data = trends.get_finding_trends(db, in_period, start, end)

    return RiskReport(
        scope=ReportScope(
            ai_system_id=scoped.ai_system_id,
            agent_id=scoped.agent_id,
            runtime_identity_id=scoped.runtime_identity_id,
        ),
        period=ReportPeriod(start=start, end=end),
        freshness=Freshness(mode=FreshnessMode.LIVE, last_refreshed_at=_utc_now()),
        open_findings=open_findings,
        new_findings=new_findings,
        resolved_findings=resolved_findings,
        critical_findings=severity_dist.get("CRITICAL", 0),
        high_findings=severity_dist.get("HIGH", 0),
        mean_time_to_resolution_hours=None,
        severity_distribution=_severity_distribution(severity_dist),
        category_distribution=queries.get_category_distribution(db, open_state),
        status_distribution=queries.get_finding_status_distribution(db, in_period),
        risk_by_ai_system=risk_by_ai_system,
        risk_by_agent=risk_by_agent,
        finding_trends=finding_trends_data.get("new_findings", []),
        findings_by_source=queries.get_findings_by_source(db, open_state),
        semantics=RISK_SEMANTICS,
        unavailable_dimensions=["tool", "model", "data_source"],
        attention=build_attention_items(db, scoped),
    )


def build_policy_report(
    db: Session,
    filters: ReportFilters,
) -> PolicyReport:
    """Build policy effectiveness report.

    ``effects`` is the effect of the matched rule; ``decision_distribution`` is
    the finalized enforcement decision; ``execution_outcome_distribution`` is
    what happened afterwards. They are different facts and are reported apart.
    """
    scoped, start, end = _with_default_period(filters)

    evaluations = queries.count_policy_evaluations(db, scoped)
    triggers = queries.count_policy_triggers(db, scoped)
    trigger_rate = metrics.calculate_rate(triggers, evaluations)

    denied = queries.count_denied_actions(db, scoped)
    deny_rate = metrics.calculate_rate(denied, evaluations)

    effects_data = queries.get_effect_distribution(db, scoped)

    intervention_rate = metrics.calculate_intervention_rate(
        effects_data.get("monitor", 0),
        effects_data.get("require_approval", 0),
        effects_data.get("deny", 0),
        effects_data.get("suspend", 0),
        evaluations,
    )

    eval_trends = trends.get_policy_evaluation_trends(db, scoped, start, end)
    trigger_trends = trends.get_policy_trigger_trends(db, scoped, start, end)

    return PolicyReport(
        scope=ReportScope(
            ai_system_id=scoped.ai_system_id,
            agent_id=scoped.agent_id,
            runtime_identity_id=scoped.runtime_identity_id,
        ),
        period=ReportPeriod(start=start, end=end),
        freshness=Freshness(mode=FreshnessMode.LIVE, last_refreshed_at=_utc_now()),
        evaluations=evaluations,
        triggers=triggers,
        trigger_rate=round(trigger_rate, 2),
        intervention_rate=round(intervention_rate, 2),
        deny_rate=round(deny_rate, 2),
        effects=EffectDistribution(
            allow=effects_data.get("allow", 0),
            monitor=effects_data.get("monitor", 0),
            require_approval=effects_data.get("require_approval", 0),
            deny=effects_data.get("deny", 0),
            suspend=effects_data.get("suspend", 0),
        ),
        decision_distribution=queries.get_receipt_distribution(
            db, scoped, "enforcement_decision"
        ),
        execution_outcome_distribution=queries.get_receipt_distribution(
            db, scoped, "execution_status"
        ),
        evaluation_trends=eval_trends,
        trigger_trends=trigger_trends,
    )


def _round_cost(value: float | None) -> float | None:
    """Keep sub-cent LLM costs; None (unknown) must stay None, never become 0."""
    return None if value is None else round(value, 8)


def build_budget_report(
    db: Session,
    filters: ReportFilters,
) -> BudgetReport:
    """Build budget and cost report."""
    scoped, start, end = _with_default_period(filters)

    # Budget counts
    total_budgets = queries.count_budgets(db, scoped)
    budgets_warning = queries.count_budgets_by_status(db, scoped, "WARNING")
    budgets_exceeded = queries.count_budgets_by_status(db, scoped, "EXCEEDED")

    # Cost and tokens from the same metering spans trajectories display.
    cost_summary = queries.get_cost_summary(db, scoped)
    total_tokens = queries.get_total_tokens(db, scoped)
    cost_trends, token_trends = trends.get_spend_trends(db, scoped, start, end)

    # Budget details
    budgets_data = queries.get_budget_utilization(db, filters)
    budget_statuses = [
        BudgetStatus(
            id=b["id"],
            name=b["name"],
            metric=b["metric"],
            current_usage=b["current_usage"],
            limit_value=b["limit_value"],
            warning_threshold=b["warning_threshold"],
            utilization_pct=b["utilization_pct"],
            status=b["status"],
            window=b["window"],
        )
        for b in budgets_data
    ]

    return BudgetReport(
        scope=ReportScope(
            ai_system_id=filters.ai_system_id,
            agent_id=filters.agent_id,
            runtime_identity_id=filters.runtime_identity_id,
        ),
        period=ReportPeriod(start=start, end=end),
        freshness=Freshness(
            mode=FreshnessMode.LIVE, last_refreshed_at=datetime.utcnow()
        ),
        total_budgets=total_budgets,
        budgets_warning=budgets_warning,
        budgets_exceeded=budgets_exceeded,
        total_cost=_round_cost(cost_summary["total_cost"]),
        span_cost=_round_cost(cost_summary["span_cost"]),
        ledger_only_cost=_round_cost(cost_summary["ledger_only_cost"]),
        currency=cost_summary["currency"],
        currencies=cost_summary["currencies"],
        total_tokens=total_tokens,
        budgets=budget_statuses,
        cost_trends=cost_trends,
        token_trends=token_trends,
    )


def build_ai_system_report(
    db: Session,
    ai_system_id: UUID,
    filters: ReportFilters,
) -> AISystemReport:
    """Build a live report for one AI system in the caller's organization."""
    system = (
        db.query(AISystem)
        .filter(
            AISystem.id == ai_system_id,
        )
        .first()
    )
    if system is None:
        raise LookupError("AI system not found")

    scoped = filters.model_copy()
    scoped.ai_system_id = ai_system_id
    if scoped.period_start and scoped.period_end:
        start, end = scoped.period_start, scoped.period_end
    else:
        start, end = get_default_period(days=30)
        scoped.period_start = start
        scoped.period_end = end

    open_filters = scoped.model_copy()
    open_filters.period_start = None
    open_filters.period_end = None
    open_filters.finding_status = list(OPEN_FINDING_STATUSES)

    critical_filters = open_filters.model_copy()
    critical_filters.risk_severity = ["CRITICAL"]
    critical_filters.finding_status = ["OPEN"]

    evaluations = queries.count_policy_evaluations(db, scoped)
    triggers = queries.count_policy_triggers(db, scoped)
    denied = queries.count_denied_actions(db, scoped)
    effects = queries.get_effect_distribution(db, scoped)
    budgets = queries.get_budget_utilization(db, scoped)

    prev_start, prev_end = get_previous_period(start, end)
    prev_filters = scoped.model_copy()
    prev_filters.period_start = prev_start
    prev_filters.period_end = prev_end
    prev_filters.finding_status = list(OPEN_FINDING_STATUSES)
    current_open = queries.count_risk_findings(db, open_filters)
    previous_open = queries.count_risk_findings(db, prev_filters)

    latest_score = (
        db.query(RiskAssessment.overall_score)
        .filter(RiskAssessment.ai_system_id == ai_system_id)
        .order_by(RiskAssessment.created_at.desc())
        .limit(1)
        .scalar()
    )
    latest_assurance = (
        db.query(AssuranceRecord.status)
        .filter(AssuranceRecord.ai_system_id == ai_system_id)
        .order_by(AssuranceRecord.created_at.desc())
        .limit(1)
        .scalar()
    )
    evidence_completeness = (
        db.query(func.avg(RuntimeTrajectory.completeness_pct))
        .filter(
            RuntimeTrajectory.ai_system_id == ai_system_id,
            RuntimeTrajectory.started_at >= start,
            RuntimeTrajectory.started_at <= end,
        )
        .scalar()
    )

    return AISystemReport(
        scope=ReportScope(
            ai_system_id=ai_system_id,
        ),
        period=ReportPeriod(start=start, end=end),
        freshness=Freshness(
            mode=FreshnessMode.LIVE, last_refreshed_at=datetime.utcnow()
        ),
        executions=queries.count_executions(db, scoped),
        conversations=queries.count_conversations(db, scoped),
        governed_actions=queries.count_governed_actions(db, scoped),
        execution_success_rate=round(queries.get_execution_success_rate(db, scoped), 2),
        open_findings=current_open,
        critical_findings=queries.count_risk_findings(db, critical_filters),
        risk_score=float(latest_score) if latest_score is not None else None,
        risk_trend=get_trend_direction(
            calculate_change_pct(current_open, previous_open)
        ),
        policy_trigger_rate=round(metrics.calculate_rate(triggers, evaluations), 2),
        deny_rate=round(metrics.calculate_rate(denied, evaluations), 2),
        approval_rate=round(
            metrics.calculate_rate(effects.get("require_approval", 0), evaluations), 2
        ),
        budget_usage=round(
            sum(item["current_usage"] or 0 for item in budgets) / len(budgets), 2
        )
        if budgets
        else 0.0,
        token_usage=queries.get_total_tokens(db, scoped),
        cost=_round_cost(queries.get_cost_summary(db, scoped)["total_cost"]),
        assurance_status=latest_assurance,
        evidence_completeness_pct=round(float(evidence_completeness), 2)
        if evidence_completeness is not None
        else None,
        incidents=queries.count_incidents(db, open_filters, status_filter="OPEN"),
        exceptions=queries.count_exceptions(db, open_filters, status_filter="APPROVED"),
        evaluation_waivers=queries.count_waivers(db, open_filters),
        risk_by_agent=[
            RankedItem(id=agent_id, name=name, value=count)
            for agent_id, name, count in queries.get_risk_by_agent(db, open_filters)
        ],
    )


def _with_default_period(
    filters: ReportFilters,
) -> tuple[ReportFilters, datetime, datetime]:
    scoped = filters.model_copy()
    if scoped.period_start and scoped.period_end:
        return scoped, scoped.period_start, scoped.period_end
    start, end = get_default_period(days=30)
    scoped.period_start = start
    scoped.period_end = end
    return scoped, start, end


def _scope_header(filters: ReportFilters, start: datetime, end: datetime):
    return (
        ReportScope(
            ai_system_id=filters.ai_system_id,
            agent_id=filters.agent_id,
            runtime_identity_id=filters.runtime_identity_id,
        ),
        ReportPeriod(start=start, end=end),
        Freshness(mode=FreshnessMode.LIVE, last_refreshed_at=datetime.utcnow()),
    )


def build_runtime_report(
    db: Session,
    filters: ReportFilters,
) -> RuntimeReport:
    """Build runtime governance activity.

    Count units differ and are not interchangeable: governed actions and policy
    evaluations are enforcement receipts, executions are trajectories, and
    conversations are conversation projections.
    """
    scoped, start, end = _with_default_period(filters)
    scope, period, freshness = _scope_header(scoped, start, end)
    effects = queries.get_effect_distribution(db, scoped)
    return RuntimeReport(
        scope=scope,
        period=period,
        freshness=freshness,
        governed_actions=queries.count_governed_actions(db, scoped),
        executions=queries.count_executions(db, scoped),
        conversations=queries.count_conversations(db, scoped),
        execution_success_rate=round(queries.get_execution_success_rate(db, scoped), 2),
        policy_evaluations=queries.count_policy_evaluations(db, scoped),
        interventions=int(
            effects.get("monitor", 0)
            + effects.get("require_approval", 0)
            + effects.get("deny", 0)
            + effects.get("suspend", 0)
        ),
        approvals_required=int(effects.get("require_approval", 0)),
        denied=queries.count_denied_actions(db, scoped),
        runtime_suspensions=int(effects.get("suspend", 0)),
        execution_status_distribution=queries.get_execution_status_distribution(
            db, scoped
        ),
        approvals_pending=queries.count_approval_requests(
            db, _current_state(scoped), status_filter="PENDING"
        ),
        activity_trends=trends.get_policy_evaluation_trends(db, scoped, start, end),
    )


def _count_expiring(
    db: Session,
    model,
    filters: ReportFilters,
    *,
    approved_status: str,
) -> int:
    now = _utc_now()
    horizon = now + timedelta(days=14)
    query = (
        db.query(func.count(model.id))
        .join(AISystem, model.ai_system_id == AISystem.id)
        .filter(
            model.status == approved_status,
            model.revoked_at.is_(None),
            model.valid_until.isnot(None),
            model.valid_until >= now,
            model.valid_until <= horizon,
        )
    )
    if filters.ai_system_id:
        query = query.filter(model.ai_system_id == filters.ai_system_id)
    return query.scalar() or 0


def build_assurance_report(
    db: Session,
    filters: ReportFilters,
) -> AssuranceReport:
    """Build assurance coverage from assurance records, gates, waivers, and exceptions.

    Assurance status, waivers and exceptions are *current state*. Deployment
    gate counts are period events. Coverage is
    ``systems with effectively VALID assurance / registered systems`` and is
    null when there are no registered systems. This is control assurance
    evidence, not a regulatory certification.
    """
    scoped, start, end = _with_default_period(filters)
    scope, period, freshness = _scope_header(scoped, start, end)
    total_systems = queries.count_ai_systems(db, scoped)
    effective = queries.get_effective_assurance_by_system(db, scoped)
    by_status: dict[str, int] = {}
    for status in effective.values():
        by_status[status] = by_status.get(status, 0) + 1
    valid = by_status.get("VALID", 0)

    passed = queries.count_gate_evaluations(db, scoped, "PASS")
    failed = queries.count_gate_evaluations(db, scoped, "FAIL")
    gate_total = queries.count_gate_evaluations(db, scoped)
    scored = passed + failed

    now = _utc_now()
    open_exceptions = (
        db.query(func.count(GovernanceException.id))
        .join(AISystem, GovernanceException.ai_system_id == AISystem.id)
        .filter(GovernanceException.status.in_(["REQUESTED", "UNDER_REVIEW"]))
    )
    active_exceptions = (
        db.query(func.count(GovernanceException.id))
        .join(AISystem, GovernanceException.ai_system_id == AISystem.id)
        .filter(
            GovernanceException.status == "APPROVED",
            GovernanceException.revoked_at.is_(None),
            or_(
                GovernanceException.valid_until.is_(None),
                GovernanceException.valid_until >= now,
            ),
        )
    )
    if scoped.ai_system_id:
        open_exceptions = open_exceptions.filter(
            GovernanceException.ai_system_id == scoped.ai_system_id
        )
        active_exceptions = active_exceptions.filter(
            GovernanceException.ai_system_id == scoped.ai_system_id
        )

    has_data = bool(effective) or gate_total > 0
    return AssuranceReport(
        scope=scope,
        period=period,
        freshness=freshness,
        total_systems=total_systems,
        unassessed_systems=max(0, total_systems - len(effective)),
        has_assurance_data=has_data,
        assurance_coverage_pct=(
            round(metrics.calculate_assurance_coverage(valid, total_systems), 2)
            if total_systems
            else None
        ),
        valid_assurance=valid,
        conditional_assurance=by_status.get("CONDITIONAL", 0),
        expired_assurance=by_status.get("EXPIRED", 0),
        failed_assurance=by_status.get("FAILED", 0),
        gate_evaluations=gate_total,
        evaluation_pass_rate=round((passed / scored) * 100, 2) if scored else None,
        failed_deployment_gates=failed,
        active_waivers=queries.count_waivers(db, scoped),
        expiring_waivers=_count_expiring(
            db, EvaluationWaiver, scoped, approved_status="APPROVED"
        ),
        open_exceptions=open_exceptions.scalar() or 0,
        expiring_exceptions=_count_expiring(
            db, GovernanceException, scoped, approved_status="APPROVED"
        ),
        active_exceptions=active_exceptions.scalar() or 0,
    )


def build_conversation_report(
    db: Session,
    conversation_id: UUID,
    filters: ReportFilters,
) -> ConversationReport:
    del filters
    """Build a report for one conversation in the caller's organization."""
    conversation = (
        db.query(RuntimeConversation)
        .filter(
            RuntimeConversation.id == conversation_id,
        )
        .first()
    )
    if conversation is None:
        raise LookupError("Conversation not found")

    start = conversation.started_at or conversation.created_at
    end = conversation.last_activity_at or datetime.utcnow()
    trajectories = (
        db.query(RuntimeTrajectory)
        .filter(RuntimeTrajectory.runtime_conversation_id == conversation.id)
        .order_by(RuntimeTrajectory.started_at.asc())
        .all()
    )
    trajectory_ids = [row.id for row in trajectories]
    receipts = []
    if trajectory_ids:
        correlation_ids = [
            row.correlation_id for row in trajectories if row.correlation_id
        ]
        receipt_query = db.query(RuntimeEnforcementReceipt).filter(
            RuntimeEnforcementReceipt.ai_system_id == conversation.ai_system_id
        )
        if correlation_ids:
            receipts = receipt_query.filter(
                RuntimeEnforcementReceipt.correlation_id.in_(correlation_ids)
            ).all()
        else:
            receipts = []

    findings = 0
    if trajectory_ids:
        findings = (
            db.query(func.count(RuntimeRiskFinding.id))
            .filter(RuntimeRiskFinding.trajectory_id.in_(trajectory_ids))
            .scalar()
            or 0
        )
    budget_events = 0
    if trajectory_ids:
        budget_events = (
            db.query(func.count(RuntimeBudgetCheck.id))
            .filter(RuntimeBudgetCheck.trajectory_id.in_(trajectory_ids))
            .scalar()
            or 0
        )

    receipt_ids = [row.id for row in receipts]
    token_usage = None
    cost = None
    if receipt_ids:
        usage = (
            db.query(
                func.sum(UsageSettlement.total_tokens),
                func.sum(UsageSettlement.actual_cost),
            )
            .join(
                UsageReservation, UsageSettlement.reservation_id == UsageReservation.id
            )
            .filter(UsageReservation.enforcement_receipt_id.in_(receipt_ids))
            .one()
        )
        if usage[0] is not None:
            token_usage = int(usage[0])
        if usage[1] is not None:
            cost = round(float(usage[1]), 2)

    completeness_values = [
        row.completeness_pct for row in trajectories if row.completeness_pct is not None
    ]
    completeness = (
        round(sum(completeness_values) / len(completeness_values), 2)
        if completeness_values
        else None
    )
    triggers = [
        row
        for row in receipts
        if (row.policy_effect or "").upper()
        in {"MONITOR", "REQUIRE_APPROVAL", "DENY", "SUSPEND"}
    ]
    approval_ids = [
        row.approval_request_id for row in receipts if row.approval_request_id
    ]
    approvals = []
    if approval_ids:
        approvals = (
            db.query(ApprovalRequest).filter(ApprovalRequest.id.in_(approval_ids)).all()
        )

    events = [
        {
            "type": "trajectory",
            "id": str(row.id),
            "occurred_at": row.started_at.isoformat() if row.started_at else None,
            "status": row.status,
            "finding_count": row.finding_count,
        }
        for row in trajectories[:50]
    ]

    return ConversationReport(
        scope=ReportScope(
            ai_system_id=conversation.ai_system_id,
        ),
        period=ReportPeriod(start=start, end=end),
        freshness=Freshness(
            mode=FreshnessMode.LIVE, last_refreshed_at=datetime.utcnow()
        ),
        turns=len(trajectories) or int(conversation.trajectory_count or 0),
        actions=len(receipts) or int(conversation.action_count or 0),
        tool_calls=sum(1 for row in receipts if row.tool_id is not None),
        policy_evaluations=len(receipts),
        policy_triggers=len(triggers),
        findings=int(findings or conversation.finding_count or 0),
        highest_risk=conversation.highest_risk_level,
        approvals_requested=len(approvals),
        approvals_approved=sum(1 for row in approvals if row.status == "APPROVED"),
        approvals_rejected=sum(1 for row in approvals if row.status == "REJECTED"),
        denied_actions=sum(1 for row in receipts if row.enforcement_decision == "DENY"),
        budget_events=int(budget_events),
        token_usage=token_usage,
        cost=cost,
        evidence_completeness_pct=completeness,
        events=events,
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def build_attention_items(
    db: Session,
    filters: ReportFilters,
    limit: int = 10,
) -> list[AttentionItem]:
    """Rank the portfolio attention queue; reports never invent attention items.

    The portfolio queue is the single authoritative source (critical findings,
    expired assurance, exceeded budgets, expiring waivers/exceptions, ...). It
    is re-used as-is: owner/due fields appear only when the source supplies
    them, and ``drill_down`` is the route the source provided.
    """
    from rai_gateway.portfolio.attention import build_attention_queue

    queue = build_attention_queue(db, limit=200)
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    items: list[AttentionItem] = []
    for raw in queue.get("items", []):
        system_id = raw.get("ai_system_id")
        if filters.ai_system_id and str(filters.ai_system_id) != str(system_id):
            continue
        try:
            parsed_system = UUID(str(system_id)) if system_id else None
        except ValueError:
            parsed_system = None
        items.append(
            AttentionItem(
                type=str(raw.get("type", "")),
                severity=str(raw.get("severity", "")).upper(),
                title=str(raw.get("title", "")),
                ai_system_id=parsed_system,
                source_type=str(raw.get("source_type", "")),
                source_id=str(raw.get("source_id", "")),
                reason=str(raw.get("reason", "")),
                recommended_action=raw.get("recommended_action"),
                due_at=_parse_iso(raw.get("due_at")),
                created_at=_parse_iso(raw.get("created_at")),
                drill_down=raw.get("drill_down"),
            )
        )
    items.sort(
        key=lambda item: (
            rank.get(item.severity, 9),
            item.due_at or datetime.max,
        )
    )
    return items[:limit]
