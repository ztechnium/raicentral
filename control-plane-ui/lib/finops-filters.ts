import type { FinOpsQuery } from "@/lib/api";

/**
 * FinOps filter state, serialised to the URL so views are shareable and the same context
 * survives moving between Overview, Usage & Costs and drill-downs.
 *
 * Only filters the backend actually supports are represented. Anything else in the URL is
 * reported through `errors` rather than silently dropped or silently applied.
 */
export const PERIOD_PRESETS = {
  "7d": { days: 7, label: "Last 7 days" },
  "30d": { days: 30, label: "Last 30 days" },
  "90d": { days: 90, label: "Last 90 days" },
} as const;

export type PeriodPreset = keyof typeof PERIOD_PRESETS;
export type PeriodKey = PeriodPreset | "custom";
export const DEFAULT_PERIOD: PeriodPreset = "30d";
export const MAX_CUSTOM_DAYS = 365;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const COST_STATES = ["BILLED", "METERED", "ESTIMATED", "UNAVAILABLE"] as const;

export interface FinOpsFilterState {
  period: PeriodKey;
  /** Custom range, inclusive calendar dates (UTC), YYYY-MM-DD. */
  from?: string;
  to?: string;
  ai_system_id?: string;
  agent_id?: string;
  delegation_id?: string;
  provider?: string;
  model?: string;
  environment?: string;
  cost_state?: string;
  metering_source?: string;
  currency?: string;
}

export const ID_FILTERS = ["ai_system_id", "agent_id", "delegation_id"] as const;
export const TEXT_FILTERS = [
  "provider",
  "model",
  "environment",
  "metering_source",
] as const;
export const FILTER_KEYS = [
  ...ID_FILTERS,
  ...TEXT_FILTERS,
  "cost_state",
  "currency",
] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

export const FILTER_LABELS: Record<FilterKey, string> = {
  ai_system_id: "AI system",
  agent_id: "Agent",
  delegation_id: "Delegation / tenant",
  provider: "Provider",
  model: "Model",
  environment: "Environment",
  metering_source: "Metering source",
  cost_state: "Cost state",
  currency: "Currency",
};

export function validateCustomRange(from?: string, to?: string): string | null {
  if (!from || !to) return "Choose both a start and an end date.";
  if (!DATE.test(from) || !DATE.test(to)) return "Dates must be valid calendar dates.";
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return "Dates must be valid calendar dates.";
  if (end < start) return "The end date must not be before the start date.";
  if ((end - start) / 86_400_000 + 1 > MAX_CUSTOM_DAYS) {
    return `The range may not exceed ${MAX_CUSTOM_DAYS} days.`;
  }
  return null;
}

export function parseFilters(params: URLSearchParams): {
  filters: FinOpsFilterState;
  errors: string[];
} {
  const errors: string[] = [];
  const filters: FinOpsFilterState = { period: DEFAULT_PERIOD };

  const period = params.get("period");
  if (period === "custom") {
    const from = params.get("from") ?? undefined;
    const to = params.get("to") ?? undefined;
    const problem = validateCustomRange(from, to);
    if (problem) {
      errors.push(`Custom date range ignored: ${problem}`);
    } else {
      filters.period = "custom";
      filters.from = from;
      filters.to = to;
    }
  } else if (period && period in PERIOD_PRESETS) {
    filters.period = period as PeriodPreset;
  } else if (period) {
    errors.push(`Unknown period "${period}"; showing ${PERIOD_PRESETS[DEFAULT_PERIOD].label}.`);
  }

  for (const key of ID_FILTERS) {
    const value = params.get(key);
    if (!value) continue;
    if (UUID.test(value)) filters[key] = value.toLowerCase();
    else errors.push(`${FILTER_LABELS[key]} filter ignored: not a valid id.`);
  }
  for (const key of TEXT_FILTERS) {
    const value = params.get(key)?.trim();
    if (value) filters[key] = value.slice(0, 300);
  }
  const state = params.get("cost_state")?.toUpperCase();
  if (state) {
    if ((COST_STATES as readonly string[]).includes(state)) filters.cost_state = state;
    else errors.push(`Cost state "${state}" is not supported and was ignored.`);
  }
  const currency = params.get("currency")?.toUpperCase();
  if (currency) {
    if (/^[A-Z]{3}$/.test(currency)) filters.currency = currency;
    else errors.push("Currency filter ignored: expected a 3-letter code.");
  }
  return { filters, errors };
}

/** Serialise only non-default values so URLs stay short and stable. */
export function filtersToSearch(filters: FinOpsFilterState): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.period !== DEFAULT_PERIOD) params.set("period", filters.period);
  if (filters.period === "custom") {
    if (filters.from) params.set("from", filters.from);
    if (filters.to) params.set("to", filters.to);
  }
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value) params.set(key, value);
  }
  return params;
}

/** Stable window: end is floored to the minute so every widget on a page shares one range. */
export function periodRange(filters: FinOpsFilterState, now: Date = new Date()) {
  if (filters.period === "custom" && filters.from && filters.to) {
    const start = new Date(`${filters.from}T00:00:00Z`);
    // Inclusive end date: the range runs to the start of the following day.
    const end = new Date(`${filters.to}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 1);
    return { period_start: start.toISOString(), period_end: end.toISOString() };
  }
  const days = PERIOD_PRESETS[filters.period as PeriodPreset]?.days ?? 30;
  const end = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const start = new Date(end.getTime() - days * 86_400_000);
  return { period_start: start.toISOString(), period_end: end.toISOString() };
}

export function filtersToQuery(filters: FinOpsFilterState, now?: Date): FinOpsQuery {
  const query: FinOpsQuery = { ...periodRange(filters, now) };
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value) (query as Record<string, string>)[key] = value;
  }
  return query;
}

export function activeFilters(filters: FinOpsFilterState): Array<[FilterKey, string]> {
  return FILTER_KEYS.flatMap((key) => (filters[key] ? [[key, filters[key] as string]] : [])) as Array<
    [FilterKey, string]
  >;
}

export function periodLabel(filters: FinOpsFilterState): string {
  if (filters.period === "custom" && filters.from && filters.to) {
    return `${filters.from} to ${filters.to}`;
  }
  return PERIOD_PRESETS[filters.period as PeriodPreset]?.label ?? "Last 30 days";
}
