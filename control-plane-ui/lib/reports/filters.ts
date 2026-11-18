/**
 * Report filter state: URL <-> state serialization, period resolution and the
 * per-view filter support matrix.
 *
 * The matrix mirrors the backend's strict_params allow-lists
 * (src/rai_gateway/reporting/api.py). A filter that a view does not support is
 * never sent and never shown, so no control can silently fail to filter.
 */

export const REPORT_VIEWS = [
  "executive",
  "risk",
  "controls",
  "assurance",
  "runtime",
  "economics",
] as const;
export type ReportView = (typeof REPORT_VIEWS)[number];

export const VIEW_LABELS: Record<ReportView, string> = {
  executive: "Executive Overview",
  risk: "Risk Analysis",
  controls: "Control Effectiveness",
  assurance: "Assurance & Compliance",
  runtime: "Runtime Governance",
  economics: "AI Economics",
};

export const PERIOD_PRESETS = ["24h", "7d", "30d", "90d", "custom"] as const;
export type PeriodPreset = (typeof PERIOD_PRESETS)[number];

const PRESET_MS: Record<Exclude<PeriodPreset, "custom">, number> = {
  "24h": 24 * 3600 * 1000,
  "7d": 7 * 24 * 3600 * 1000,
  "30d": 30 * 24 * 3600 * 1000,
  "90d": 90 * 24 * 3600 * 1000,
};

export const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"] as const;
export const FINDING_STATUSES = [
  "OPEN",
  "INVESTIGATING",
  "ACKNOWLEDGED",
  "MONITORING",
  "RESOLVED",
] as const;
export const POLICY_EFFECTS = ["ALLOW", "MONITOR", "REQUIRE_APPROVAL", "DENY", "SUSPEND"] as const;
export const BUDGET_METRICS = [
  "COST",
  "TOKEN_USAGE",
  "ACTION_COUNT",
  "TOOL_COUNT",
  "AMOUNT",
] as const;

export interface ReportFilterState {
  view: ReportView;
  period: PeriodPreset;
  /** ISO instants; only meaningful when period === "custom". */
  from?: string;
  to?: string;
  compare: boolean;
  aiSystemId?: string;
  severity: string[];
  category: string[];
  status: string[];
  effect?: string;
  budgetMetric?: string;
}

export const DEFAULT_FILTERS: ReportFilterState = {
  view: "executive",
  period: "30d",
  compare: true,
  severity: [],
  category: [],
  status: [],
};

/** Which filters each view genuinely applies. Keep in sync with the backend. */
export interface FilterSupport {
  period: boolean;
  aiSystem: boolean;
  severity: boolean;
  category: boolean;
  status: boolean;
  effect: boolean;
  budgetMetric: boolean;
}

const NONE: FilterSupport = {
  period: false,
  aiSystem: false,
  severity: false,
  category: false,
  status: false,
  effect: false,
  budgetMetric: false,
};

export const FILTER_SUPPORT: Record<ReportView, FilterSupport> = {
  executive: { ...NONE, period: true, aiSystem: true },
  risk: { ...NONE, period: true, aiSystem: true, severity: true, category: true, status: true },
  controls: { ...NONE, period: true, aiSystem: true, effect: true },
  assurance: { ...NONE, period: true, aiSystem: true },
  runtime: { ...NONE, period: true, aiSystem: true },
  // Economics reads the FinOps dashboard (period + AI system) and the budget report.
  economics: { ...NONE, period: true, aiSystem: true, budgetMetric: true },
};

function isView(value: string | null): value is ReportView {
  return value !== null && (REPORT_VIEWS as readonly string[]).includes(value);
}
function isPreset(value: string | null): value is PeriodPreset {
  return value !== null && (PERIOD_PRESETS as readonly string[]).includes(value);
}
function isIso(value: string | null | undefined): value is string {
  return Boolean(value) && !Number.isNaN(Date.parse(value as string));
}
function list(params: URLSearchParams, key: string): string[] {
  return params
    .getAll(key)
    .flatMap((value) => value.split(","))
    .map((value) => value.trim())
    .filter(Boolean);
}

/** Parse a URL query string. Unknown or malformed values fall back to defaults. */
export function parseFilters(params: URLSearchParams): ReportFilterState {
  const viewParam = params.get("view");
  const periodParam = params.get("period");
  const from = params.get("from");
  const to = params.get("to");
  let period: PeriodPreset = isPreset(periodParam) ? periodParam : DEFAULT_FILTERS.period;
  const customOk = isIso(from) && isIso(to) && Date.parse(from) <= Date.parse(to);
  if (period === "custom" && !customOk) period = DEFAULT_FILTERS.period;
  return {
    view: isView(viewParam) ? viewParam : DEFAULT_FILTERS.view,
    period,
    from: period === "custom" ? (from as string) : undefined,
    to: period === "custom" ? (to as string) : undefined,
    compare: params.get("compare") !== "0",
    aiSystemId: params.get("ai_system_id") || undefined,
    severity: list(params, "severity").map((v) => v.toUpperCase()),
    category: list(params, "category").map((v) => v.toUpperCase()),
    status: list(params, "status").map((v) => v.toUpperCase()),
    effect: params.get("effect")?.toUpperCase() || undefined,
    budgetMetric: params.get("budget_metric")?.toUpperCase() || undefined,
  };
}

/** Serialize to a URL query string; defaults are omitted to keep links short. */
export function serializeFilters(state: ReportFilterState): string {
  const params = new URLSearchParams();
  if (state.view !== DEFAULT_FILTERS.view) params.set("view", state.view);
  if (state.period !== DEFAULT_FILTERS.period) params.set("period", state.period);
  if (state.period === "custom" && state.from && state.to) {
    params.set("from", state.from);
    params.set("to", state.to);
  }
  if (!state.compare) params.set("compare", "0");
  if (state.aiSystemId) params.set("ai_system_id", state.aiSystemId);
  if (state.severity.length) params.set("severity", state.severity.join(","));
  if (state.category.length) params.set("category", state.category.join(","));
  if (state.status.length) params.set("status", state.status.join(","));
  if (state.effect) params.set("effect", state.effect);
  if (state.budgetMetric) params.set("budget_metric", state.budgetMetric);
  return params.toString();
}

export interface ResolvedPeriod {
  start: Date;
  end: Date;
  /** Same-length period ending 1ms before `start` (inclusive bounds, no overlap). */
  previous: { start: Date; end: Date };
}

/**
 * Resolve a period to concrete instants. `now` is injected so tests and a
 * "refresh" action control the clock; presets are rolling windows ending at `now`.
 */
export function resolvePeriod(state: ReportFilterState, now: Date): ResolvedPeriod {
  let start: Date;
  let end: Date;
  if (state.period === "custom" && isIso(state.from) && isIso(state.to)) {
    start = new Date(state.from);
    end = new Date(state.to);
  } else {
    const preset = state.period === "custom" ? "30d" : state.period;
    end = now;
    start = new Date(now.getTime() - PRESET_MS[preset]);
  }
  const duration = end.getTime() - start.getTime();
  const previousEnd = new Date(start.getTime() - 1);
  const previousStart = new Date(previousEnd.getTime() - duration);
  return { start, end, previous: { start: previousStart, end: previousEnd } };
}

/** Exact range + timezone label, e.g. "18 Oct 2026 14:00 – 17 Nov 2026 14:00 (UTC+02:00)". */
export function describePeriod(period: { start: Date; end: Date }, timeZone?: string): string {
  const tz = timeZone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  const fmt = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: tz,
  });
  const offset = new Intl.DateTimeFormat("en-GB", {
    timeZone: tz,
    timeZoneName: "longOffset",
  })
    .formatToParts(period.end)
    .find((part) => part.type === "timeZoneName")?.value;
  return `${fmt.format(period.start)} – ${fmt.format(period.end)} (${tz}${offset ? `, ${offset}` : ""})`;
}

/**
 * Query params sent to a view's endpoint. Only filters the view supports are
 * included, so the request is always accepted by the backend's strict check.
 */
export function buildReportParams(
  state: ReportFilterState,
  period: ResolvedPeriod,
  view: ReportView = state.view,
): Record<string, string | string[]> {
  const support = FILTER_SUPPORT[view];
  const params: Record<string, string | string[]> = {};
  if (support.period) {
    params.period_start = period.start.toISOString();
    params.period_end = period.end.toISOString();
  }
  if (support.aiSystem && state.aiSystemId) params.ai_system_id = state.aiSystemId;
  if (support.severity && state.severity.length) params.risk_severity = state.severity;
  if (support.category && state.category.length) params.risk_category = state.category;
  if (support.status && state.status.length) params.finding_status = state.status;
  if (support.effect && state.effect) params.effect = state.effect;
  if (support.budgetMetric && state.budgetMetric) params.budget_metric = state.budgetMetric;
  return params;
}

/**
 * Stable cache/request key: identical for identical effective scope, different
 * for any change that alters the response. The instants are part of the key,
 * so a Refresh (new `now`) intentionally misses the cache.
 */
export function reportCacheKey(
  endpoint: string,
  params: Record<string, string | string[]>,
): string {
  const entries = Object.entries(params)
    .map(([key, value]) => [key, Array.isArray(value) ? [...value].sort().join(",") : value])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `${endpoint}?${entries.map(([k, v]) => `${k}=${v}`).join("&")}`;
}

/** Filters that are active but not supported by `view` (for an honest hint). */
export function ignoredFilters(state: ReportFilterState, view: ReportView): string[] {
  const support = FILTER_SUPPORT[view];
  const ignored: string[] = [];
  if (state.severity.length && !support.severity) ignored.push("severity");
  if (state.category.length && !support.category) ignored.push("category");
  if (state.status.length && !support.status) ignored.push("status");
  if (state.effect && !support.effect) ignored.push("policy effect");
  if (state.budgetMetric && !support.budgetMetric) ignored.push("budget metric");
  return ignored;
}
