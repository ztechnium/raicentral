"""Pydantic schemas for reporting API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class FreshnessMode(str, Enum):
    """Report freshness mode."""

    LIVE = "LIVE"
    NEAR_REAL_TIME = "NEAR_REAL_TIME"
    SNAPSHOT = "SNAPSHOT"


class Freshness(BaseModel):
    """Report freshness metadata."""

    mode: FreshnessMode = FreshnessMode.LIVE
    last_refreshed_at: datetime = Field(default_factory=datetime.utcnow)


class ReportScope(BaseModel):
    """Report scope metadata."""

    ai_system_id: UUID | None = None
    agent_id: UUID | None = None
    runtime_identity_id: UUID | None = None


class ReportPeriod(BaseModel):
    """Report time period."""

    start: datetime
    end: datetime


class ReportFilters(BaseModel):
    """Common reporting filter schema."""

    period_start: datetime | None = None
    period_end: datetime | None = None

    business_unit: str | None = None
    ai_system_id: UUID | None = None
    agent_id: UUID | None = None
    agent_version_id: UUID | None = None
    runtime_identity_id: UUID | None = None
    environment: str | None = None

    conversation_id: UUID | None = None
    trajectory_id: UUID | None = None

    policy_type: str | None = None
    policy_id: UUID | None = None
    effect: str | None = None
    decision: str | None = None

    risk_severity: list[str] | None = None
    risk_category: list[str] | None = None
    finding_status: list[str] | None = None

    model_id: UUID | None = None
    tool_id: UUID | None = None
    mcp_server_id: UUID | None = None
    data_source_id: UUID | None = None

    budget_id: UUID | None = None
    budget_metric: str | None = None

    @field_validator("period_start", "period_end", mode="after")
    @classmethod
    def _normalize_utc(cls, value: datetime | None) -> datetime | None:
        # Imported lazily: filters.py imports this module for typing only.
        from rai_gateway.reporting.filters import to_naive_utc

        return to_naive_utc(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_period(self) -> ReportFilters:
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_start > self.period_end
        ):
            raise ValueError("period_start must not be after period_end")
        return self


class ComparisonMetric(BaseModel):
    """Metric with previous period comparison."""

    value: int | float
    previous_value: int | float | None = None
    change_pct: float | None = None
    trend_direction: str | None = None  # "up", "down", "stable"


class KpiMetric(BaseModel):
    """Key performance indicator metric."""

    title: str
    value: int | float
    previous_value: int | float | None = None
    change_pct: float | None = None
    trend_direction: str | None = None
    status_tone: str | None = None  # "neutral", "warn", "danger", "success"
    link: str | None = None


class SeverityDistribution(BaseModel):
    """Risk severity distribution."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0


class EffectDistribution(BaseModel):
    """Policy effect distribution."""

    allow: int = 0
    monitor: int = 0
    require_approval: int = 0
    deny: int = 0
    suspend: int = 0


class AttentionItem(BaseModel):
    """Executive attention queue item."""

    type: str
    severity: str
    title: str
    ai_system_id: UUID | None = None
    source_type: str
    source_id: str
    reason: str
    recommended_action: str | None = None
    due_at: datetime | None = None
    created_at: datetime | None = None
    # Authoritative drill-down route supplied by the source queue; never invented.
    drill_down: str | None = None


class TimeSeriesPoint(BaseModel):
    """Time series data point."""

    timestamp: datetime
    value: int | float
    label: str | None = None


class RankedItem(BaseModel):
    """Ranked item for charts."""

    id: UUID
    name: str
    value: int | float
    secondary_value: int | float | None = None
    severity: str | None = None
    link: str | None = None


class ReportSummary(BaseModel):
    """Executive summary report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Top-level KPIs. See docs/reporting-metric-dictionary.md for definitions.
    #
    # ai_systems_governed: distinct AI systems with at least one enforcement
    #   receipt in the period (observed governed activity), compared with the
    #   previous equivalent period. It is NOT a coverage ratio on its own; the
    #   denominator is ai_systems_registered.
    ai_systems_governed: ComparisonMetric
    ai_systems_registered: ComparisonMetric | None = None
    governed_actions: ComparisonMetric
    # Open findings right now (current state; no previous value is reported
    # because findings carry no resolution timestamp to reconstruct history).
    current_risk_exposure: ComparisonMetric
    policy_intervention_rate: ComparisonMetric
    # Highest utilization among enabled COST-metric budgets; null when none.
    budget_utilization_pct: ComparisonMetric | None = None
    # Systems whose current assurance is effectively VALID / registered
    # systems; null when there are no registered systems.
    assurance_coverage_pct: ComparisonMetric | None = None
    open_critical_findings: ComparisonMetric
    open_high_findings: ComparisonMetric | None = None
    # Findings created in the period vs the previous equivalent period.
    new_findings: ComparisonMetric | None = None

    # Risk breakdown
    risk_by_severity: SeverityDistribution

    # Policy effectiveness
    policy_effects: EffectDistribution

    # Attention items
    attention: list[AttentionItem] = []


class RiskReport(BaseModel):
    """Risk analysis report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Summary metrics
    open_findings: int
    new_findings: int
    resolved_findings: int
    critical_findings: int
    high_findings: int
    mean_time_to_resolution_hours: float | None = None

    # Distributions
    severity_distribution: SeverityDistribution
    category_distribution: dict[str, int] = {}
    status_distribution: dict[str, int] = {}

    # Risk by entity
    risk_by_ai_system: list[RankedItem] = []
    risk_by_agent: list[RankedItem] = []
    risk_by_tool: list[RankedItem] = []
    risk_by_model: list[RankedItem] = []

    # Trends
    finding_trends: list[TimeSeriesPoint] = []

    # Open findings per independent source (design_time, runtime, evidence).
    # The headline counts are the plain sum of these with no deduplication.
    findings_by_source: dict[str, int] = {}
    # Plain-language definitions of the state/period semantics of each count.
    semantics: dict[str, str] = {}

    # Dimensions the current data model cannot attribute findings to (for
    # example "tool", "model"). Lets clients show "not supported" rather than
    # an empty chart that reads as "no risk".
    unavailable_dimensions: list[str] = []

    # Attention
    attention: list[AttentionItem] = []


class PolicyReport(BaseModel):
    """Policy effectiveness report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Summary metrics
    evaluations: int
    triggers: int
    trigger_rate: float
    intervention_rate: float
    deny_rate: float

    # Effect distribution: the effect of the matched rule (not a final decision)
    effects: EffectDistribution

    # Finalized enforcement decision per evaluation, keyed by decision value.
    decision_distribution: dict[str, int] = {}
    # What happened after the decision (execution outcome), keyed by status.
    execution_outcome_distribution: dict[str, int] = {}

    # Top policies
    top_policies: list[RankedItem] = []

    # Trends
    evaluation_trends: list[TimeSeriesPoint] = []
    trigger_trends: list[TimeSeriesPoint] = []


class BudgetStatus(BaseModel):
    """Budget status details."""

    id: UUID
    name: str
    metric: str
    current_usage: float
    limit_value: float
    warning_threshold: float
    utilization_pct: float
    status: str  # "OK", "WARNING", "EXCEEDED"
    window: str
    warning_events: int = 0
    exceeded_events: int = 0


class BudgetReport(BaseModel):
    """Budget and cost report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Summary metrics
    total_budgets: int
    budgets_warning: int
    budgets_exceeded: int
    # null = no priced usage, or several currencies (never summed, never 0).
    total_cost: float | None = None
    # Known cost over metering spans: equals the FinOps total_cost for the same
    # filters and period. ledger_only_cost covers settlements no span represents.
    span_cost: float | None = None
    ledger_only_cost: float | None = None
    currency: str | None = None
    currencies: list[str] = []
    total_tokens: int = 0
    cost_basis: str = (
        "total_cost = span_cost + ledger_only_cost. span_cost is known cost "
        "(billed + metered + estimated) over metering spans, using the same "
        "definition and therefore the same value as /v1/finops/dashboard "
        "total_cost for matching filters and period. ledger_only_cost is known "
        "cost of settlements not represented by any span, which FinOps does not "
        "count. Estimated amounts are included; unpriced usage contributes "
        "nothing and is not shown as 0. Amounts in different currencies are not "
        "summed. Use /v1/finops/dashboard for billed/metered/estimated separation."
    )

    # Budget statuses
    budgets: list[BudgetStatus] = []

    # Trends
    cost_trends: list[TimeSeriesPoint] = []
    token_trends: list[TimeSeriesPoint] = []


class RuntimeReport(BaseModel):
    """Runtime governance activity report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Summary metrics
    governed_actions: int
    executions: int
    conversations: int
    execution_success_rate: float

    # Governance funnel
    policy_evaluations: int
    interventions: int
    approvals_required: int
    denied: int
    runtime_suspensions: int

    # Execution status of trajectories started in the period, keyed by status.
    # Lets clients separate in-flight/pending work from failures instead of
    # treating "not COMPLETED" as "failed".
    execution_status_distribution: dict[str, int] = {}
    # Approval requests currently awaiting a decision (current state).
    approvals_pending: int = 0
    # Daily policy-evaluation volume for the period.
    activity_trends: list[TimeSeriesPoint] = []


class AssuranceReport(BaseModel):
    """Assurance and control coverage report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Denominator for coverage: AI systems in scope (registered systems).
    total_systems: int = 0
    # Systems with no current assurance record at all.
    unassessed_systems: int = 0
    # True when any assurance record or deployment gate evaluation exists, so
    # clients can tell "not configured" from "configured, nothing to report".
    has_assurance_data: bool = False

    # Systems by effective status of their current assurance record
    # (current state, not period-derived). null coverage = no denominator.
    assurance_coverage_pct: float | None = None
    valid_assurance: int
    conditional_assurance: int
    expired_assurance: int
    failed_assurance: int

    # Deployment gate evaluations in the period. null rate = no evaluations.
    gate_evaluations: int = 0
    evaluation_pass_rate: float | None = None
    failed_deployment_gates: int

    # Waivers and exceptions (current state)
    active_waivers: int
    expiring_waivers: int
    open_exceptions: int
    expiring_exceptions: int
    active_exceptions: int = 0


class AISystemReport(BaseModel):
    """AI System detailed report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Execution metrics
    executions: int
    conversations: int
    governed_actions: int
    execution_success_rate: float

    # Risk metrics
    open_findings: int
    critical_findings: int
    risk_score: float | None = None
    risk_trend: str | None = None

    # Policy metrics
    policy_trigger_rate: float
    deny_rate: float
    approval_rate: float

    # Budget metrics
    budget_usage: float = 0.0
    token_usage: int = 0
    cost: float | None = None

    # Assurance
    assurance_status: str | None = None
    evidence_completeness_pct: float | None = None

    # Incidents
    incidents: int = 0
    exceptions: int = 0
    evaluation_waivers: int = 0

    # Component breakdown
    risk_by_agent: list[RankedItem] = []
    risk_by_tool: list[RankedItem] = []
    risk_by_model: list[RankedItem] = []


class ConversationReport(BaseModel):
    """Conversation detailed report."""

    scope: ReportScope
    period: ReportPeriod
    freshness: Freshness

    # Conversation metrics
    turns: int
    actions: int
    tool_calls: int

    # Policy metrics
    policy_evaluations: int
    policy_triggers: int
    findings: int
    highest_risk: str | None = None

    # Approvals
    approvals_requested: int
    approvals_approved: int
    approvals_rejected: int
    denied_actions: int

    # Budget
    budget_events: int
    token_usage: int | None = None
    cost: float | None = None

    # Evidence
    evidence_completeness_pct: float | None = None

    # Event timeline
    events: list[dict[str, Any]] = []


class SnapshotFilters(BaseModel):
    """Report snapshot filters."""

    report_type: str | None = None
    scope_type: str | None = None
    generated_after: datetime | None = None
    generated_before: datetime | None = None
    limit: int = 50
    offset: int = 0


class ReportSnapshot(BaseModel):
    """Immutable historical report snapshot."""

    id: UUID
    report_type: str
    scope_type: str
    scope_id: UUID | None
    period_start: datetime
    period_end: datetime
    filters_json: dict[str, Any]
    generated_at: datetime
    generated_by: str
    schema_version: str
    data_snapshot: dict[str, Any]
    source_manifest: dict[str, list[UUID]] | None
    content_digest: str
    # Set on single-snapshot reads: whether stored data still matches the digest.
    integrity_verified: bool | None = None


class ReportSnapshotCreate(BaseModel):
    """Request to create a report snapshot."""

    report_type: str
    scope_type: str = "ORGANIZATION"
    scope_id: UUID | None = None
    filters: ReportFilters
