/**
 * Typed client for the Reports workspace. Each function is one independent
 * request so a failing endpoint can never hide data from the others
 * (the old page used a single Promise.all).
 */

import { ApiError, apiGet, apiPost } from "@/lib/api";
import type {
  AssuranceReport,
  BudgetReport,
  PolicyReport,
  ReportSnapshot,
  RiskReport,
  RuntimeReport,
  SnapshotReportType,
  SummaryReport,
} from "@/lib/reports/types";

export type ReportParams = Record<string, string | string[]>;

export function toQuery(params: ReportParams): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) value.forEach((item) => query.append(key, item));
    else if (value !== "") query.append(key, value);
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

const get = async <T>(path: string, params: ReportParams): Promise<T> =>
  (await apiGet<T>(`${path}${toQuery(params)}`)).data;

export const reportsClient = {
  summary: (params: ReportParams) => get<SummaryReport>("/v1/reports/summary", params),
  risk: (params: ReportParams) => get<RiskReport>("/v1/reports/risk", params),
  policies: (params: ReportParams) => get<PolicyReport>("/v1/reports/policies", params),
  budgets: (params: ReportParams) => get<BudgetReport>("/v1/reports/budgets", params),
  runtime: (params: ReportParams) => get<RuntimeReport>("/v1/reports/runtime", params),
  assurance: (params: ReportParams) => get<AssuranceReport>("/v1/reports/assurance", params),
  finops: (params: ReportParams) => get<FinOpsSummary>("/v1/finops/dashboard", params),
  listSnapshots: (reportType?: SnapshotReportType) =>
    get<ReportSnapshot[]>("/v1/reports/snapshots", reportType ? { report_type: reportType } : {}),
  getSnapshot: (id: string) => get<ReportSnapshot>(`/v1/reports/snapshots/${id}`, {}),
  createSnapshot: async (
    reportType: SnapshotReportType,
    filters: Record<string, unknown>,
  ): Promise<ReportSnapshot> =>
    (await apiPost<ReportSnapshot>("/v1/reports/snapshots", { report_type: reportType, filters }))
      .data,
};

/**
 * The slice of /v1/finops/dashboard the Economics view consumes. Every field is
 * optional: FinOps owns this contract and may add or move fields.
 */
export interface FinOpsSummary {
  period?: { start: string; end: string };
  totals?: {
    total_cost?: number | null;
    billed_cost?: number | null;
    metered_cost?: number | null;
    actual_cost?: number | null;
    estimated_cost?: number | null;
    unavailable_cost_count?: number;
    currency?: string | null;
    cost_state?: string | null;
    total_tokens?: number;
    model_calls?: number;
    tool_calls?: number;
    trajectories?: number;
  };
  comparison?: { total_cost_change_pct?: number | null };
  coverage?: {
    span_count?: number;
    cost_coverage_pct?: number | null;
    billed_pct?: number | null;
    metered_pct?: number | null;
    actual_pct?: number | null;
    estimated_pct?: number | null;
    unavailable_pct?: number | null;
  };
  budget_pressure?: { healthy?: number; warning?: number; exceeded?: number };
  risk_associated_spend?: number | null;
}

export type FailureKind = "not_authorized" | "invalid" | "failed";

export interface LoadFailure {
  kind: FailureKind;
  message: string;
  status?: number;
  traceId?: string;
}

/**
 * Distinguish "you may not see this" (403/401) from "your filter was rejected"
 * (422) from a genuine load failure. "No records" is never an error: it is a
 * successful response whose counts are zero/empty.
 */
export function classifyFailure(error: unknown): LoadFailure {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) {
      return {
        kind: "not_authorized",
        message: "Your role does not include access to this report.",
        status: error.status,
        traceId: error.traceId,
      };
    }
    if (error.status === 422 || error.status === 400) {
      return {
        kind: "invalid",
        message: error.message || "These filters are not valid for this report.",
        status: error.status,
        traceId: error.traceId,
      };
    }
    return {
      kind: "failed",
      message: error.message || "The report could not be loaded.",
      status: error.status,
      traceId: error.traceId,
    };
  }
  return {
    kind: "failed",
    message: error instanceof Error ? error.message : "The report could not be loaded.",
  };
}
