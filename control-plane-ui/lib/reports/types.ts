/**
 * Response contracts for /v1/reports/*. Kept separate from lib/types.ts because
 * the backend contract changed (current-state vs period semantics, nullable
 * "unknown" values). Fields documented here mirror
 * src/rai_gateway/reporting/schemas.py.
 */

export interface ReportPeriod {
  start: string;
  end: string;
}

export interface ReportFreshness {
  mode: "LIVE" | "NEAR_REAL_TIME" | "SNAPSHOT";
  last_refreshed_at: string;
}

export interface ReportEnvelope {
  scope: { ai_system_id?: string | null; agent_id?: string | null };
  period: ReportPeriod;
  freshness: ReportFreshness;
}

export interface ComparisonMetric {
  value: number;
  previous_value?: number | null;
  change_pct?: number | null;
  trend_direction?: "up" | "down" | "stable" | null;
}

export interface SeverityDistribution {
  critical: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface EffectDistribution {
  allow: number;
  monitor: number;
  require_approval: number;
  deny: number;
  suspend: number;
}

export interface TimeSeriesPoint {
  timestamp: string;
  value: number;
  label?: string | null;
}

export interface RankedItem {
  id: string;
  name: string;
  value: number;
  secondary_value?: number | null;
  severity?: string | null;
  link?: string | null;
}

export interface AttentionItem {
  type: string;
  severity: string;
  title: string;
  ai_system_id?: string | null;
  source_type: string;
  source_id: string;
  reason: string;
  recommended_action?: string | null;
  due_at?: string | null;
  created_at?: string | null;
  drill_down?: string | null;
}

export interface SummaryReport extends ReportEnvelope {
  ai_systems_governed: ComparisonMetric;
  ai_systems_registered?: ComparisonMetric | null;
  governed_actions: ComparisonMetric;
  current_risk_exposure: ComparisonMetric;
  policy_intervention_rate: ComparisonMetric;
  budget_utilization_pct?: ComparisonMetric | null;
  assurance_coverage_pct?: ComparisonMetric | null;
  open_critical_findings: ComparisonMetric;
  open_high_findings?: ComparisonMetric | null;
  new_findings?: ComparisonMetric | null;
  risk_by_severity: SeverityDistribution;
  policy_effects: EffectDistribution;
  attention: AttentionItem[];
}

export interface RiskReport extends ReportEnvelope {
  open_findings: number;
  new_findings: number;
  resolved_findings: number;
  critical_findings: number;
  high_findings: number;
  mean_time_to_resolution_hours?: number | null;
  severity_distribution: SeverityDistribution;
  category_distribution: Record<string, number>;
  status_distribution: Record<string, number>;
  risk_by_ai_system: RankedItem[];
  risk_by_agent: RankedItem[];
  risk_by_tool: RankedItem[];
  risk_by_model: RankedItem[];
  finding_trends: TimeSeriesPoint[];
  findings_by_source: Record<string, number>;
  semantics: Record<string, string>;
  unavailable_dimensions: string[];
  attention: AttentionItem[];
}

export interface PolicyReport extends ReportEnvelope {
  evaluations: number;
  triggers: number;
  trigger_rate: number;
  intervention_rate: number;
  deny_rate: number;
  effects: EffectDistribution;
  decision_distribution: Record<string, number>;
  execution_outcome_distribution: Record<string, number>;
  top_policies: RankedItem[];
  evaluation_trends: TimeSeriesPoint[];
  trigger_trends: TimeSeriesPoint[];
}

export interface BudgetStatus {
  id: string;
  name: string;
  metric: string;
  current_usage: number;
  limit_value: number;
  warning_threshold: number;
  utilization_pct: number;
  status: string;
  window: string;
}

export interface BudgetReport extends ReportEnvelope {
  total_budgets: number;
  budgets_warning: number;
  budgets_exceeded: number;
  /** Known-cost sum incl. estimates; null = no priced usage or mixed currencies. Spend is read from FinOps. */
  total_cost: number | null;
  span_cost?: number | null;
  ledger_only_cost?: number | null;
  currency?: string | null;
  currencies?: string[];
  total_tokens: number;
  cost_basis: string;
  budgets: BudgetStatus[];
}

export interface RuntimeReport extends ReportEnvelope {
  governed_actions: number;
  executions: number;
  conversations: number;
  execution_success_rate: number;
  policy_evaluations: number;
  interventions: number;
  approvals_required: number;
  denied: number;
  runtime_suspensions: number;
  execution_status_distribution: Record<string, number>;
  approvals_pending: number;
  activity_trends: TimeSeriesPoint[];
}

export interface AssuranceReport extends ReportEnvelope {
  total_systems: number;
  unassessed_systems: number;
  has_assurance_data: boolean;
  /** null = no registered systems, i.e. no defensible denominator. */
  assurance_coverage_pct: number | null;
  valid_assurance: number;
  conditional_assurance: number;
  expired_assurance: number;
  failed_assurance: number;
  gate_evaluations: number;
  /** null = no gate evaluations in the period (unknown, not 0%). */
  evaluation_pass_rate: number | null;
  failed_deployment_gates: number;
  active_waivers: number;
  expiring_waivers: number;
  open_exceptions: number;
  expiring_exceptions: number;
  active_exceptions: number;
}

export interface ReportSnapshot {
  id: string;
  report_type: string;
  scope_type: string;
  scope_id?: string | null;
  period_start: string;
  period_end: string;
  filters_json: Record<string, unknown>;
  generated_at: string;
  generated_by: string;
  schema_version: string;
  data_snapshot: Record<string, unknown>;
  source_manifest?: Record<string, string[]> | null;
  content_digest: string;
  integrity_verified?: boolean | null;
}

export type SnapshotReportType =
  | "EXECUTIVE_SUMMARY"
  | "RISK_REPORT"
  | "POLICY_REPORT"
  | "BUDGET_REPORT"
  | "RUNTIME_REPORT"
  | "ASSURANCE_REPORT";
