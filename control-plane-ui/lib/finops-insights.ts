import { filtersToSearch, type FinOpsFilterState } from "@/lib/finops-filters";
import type { FinOpsBudgetHealth, FinOpsBudgetItem, FinOpsDashboard } from "@/lib/types";

/**
 * Pure derivations for the FinOps views. Everything here reads figures the backend already
 * computed (coverage, budget windows, comparison); nothing is re-estimated client-side.
 */

export type NeedsAttentionItem = {
  key: string;
  severity: "high" | "medium" | "info";
  title: string;
  detail: string;
  href: string;
  /** What the link opens, so the user knows it leads to a source record. */
  linkLabel: string;
};

const SEVERITY_ORDER = { high: 0, medium: 1, info: 2 } as const;

/** Query string carrying the active period/filters (plus overrides) into another FinOps tab. */
export function withFilters(
  path: string,
  filters: FinOpsFilterState,
  extra: Record<string, string> = {},
): string {
  const params = filtersToSearch(filters);
  for (const [key, value] of Object.entries(extra)) params.set(key, value);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function buildNeedsAttention(
  dashboard: FinOpsDashboard | null,
  budgets: FinOpsBudgetHealth | null,
  filters: FinOpsFilterState,
): NeedsAttentionItem[] {
  const items: NeedsAttentionItem[] = [];

  if (budgets) {
    for (const budget of budgets.items) {
      if (budget.status !== "EXCEEDED" && budget.status !== "WARNING") continue;
      const exceeded = budget.status === "EXCEEDED";
      const window = budget.window;
      items.push({
        key: `budget:${budget.budget_id}:${window?.scope_id ?? ""}`,
        severity: exceeded ? "high" : "medium",
        title: `${budget.name} ${exceeded ? "exceeded its limit" : "is near its limit"}`,
        detail: window
          ? `${formatUnitValue(window.usage, budget.unit)} of ${formatUnitValue(budget.limit_value, budget.unit)} used this window. ${budget.enforcement_summary}`
          : budget.enforcement_summary,
        href: `/risk/budgets/${budget.budget_id}`,
        linkLabel: "Open budget policy",
      });
    }
  }

  const coverage = dashboard?.coverage;
  if (coverage && coverage.span_count > 0) {
    const unavailable = coverage.unavailable_pct ?? 0;
    if (unavailable > 0) {
      items.push({
        key: "gap:unpriced",
        severity: unavailable >= 10 ? "medium" : "info",
        title: `${unavailable}% of metered calls have no cost`,
        detail:
          "These calls are counted as unknown, not as zero, so known spend is a floor. Check the pricing catalog for the models involved.",
        href: withFilters("/finops/usage", filters, { dimension: "model", cost_state: "UNAVAILABLE" }),
        linkLabel: "See unpriced usage",
      });
    }
    const unattributed = coverage.unattributed_pct ?? 0;
    if (unattributed > 0) {
      items.push({
        key: "gap:unattributed",
        severity: unattributed >= 10 ? "medium" : "info",
        title: `${unattributed}% of usage is not tied to an AI system`,
        detail: "Spend from this usage cannot be allocated to a system owner.",
        href: withFilters("/finops/usage", filters, { dimension: "ai_system" }),
        linkLabel: "See attribution by AI system",
      });
    }
  }

  if (dashboard && dashboard.totals.currency === "MIXED") {
    items.push({
      key: "gap:currency",
      severity: "medium",
      title: "Usage is recorded in more than one currency",
      detail: `Amounts are not converted or summed (${dashboard.totals.currencies.join(", ")}). Pick a currency to see totals.`,
      href: withFilters("/finops/usage", filters),
      linkLabel: "Choose a currency",
    });
  }

  return items.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

export function formatUnitValue(value: number, unit: string): string {
  const rounded = Number.isInteger(value)
    ? value.toLocaleString("en-US")
    : value.toLocaleString("en-US", { maximumFractionDigits: 4 });
  // Cost/amount budgets do not record a currency, so the unit is stated, never assumed.
  return unit === "currency" ? `${rounded} currency units` : `${rounded} ${unit}`;
}

export function formatWindow(seconds: number): string {
  if (seconds % 86_400 === 0) {
    const days = seconds / 86_400;
    return days === 1 ? "24 hours" : `${days} days`;
  }
  if (seconds % 3600 === 0) {
    const hours = seconds / 3600;
    return hours === 1 ? "1 hour" : `${hours} hours`;
  }
  if (seconds % 60 === 0) return `${seconds / 60} minutes`;
  return `${seconds} seconds`;
}

export function formatBurnRate(perHour: number | null, unit: string): string {
  if (perHour === null) return "Insufficient data";
  return `${formatUnitValue(Number(perHour.toFixed(4)), unit)} / hour`;
}

export type EnforcementKind = "enforced" | "unproven" | "monitor" | "inactive";

/**
 * Configured is not the same as enforced. A blocking effect with recorded checks is
 * "enforced"; a blocking effect with no checks on record is only "configured".
 */
export function enforcementKind(budget: FinOpsBudgetItem): EnforcementKind {
  if (!budget.blocking) return "monitor";
  return budget.evidence.checks > 0 ? "enforced" : "unproven";
}

export const ENFORCEMENT_LABEL: Record<EnforcementKind, string> = {
  enforced: "Enforced",
  unproven: "Configured, no checks recorded",
  monitor: "Monitor only",
  inactive: "Inactive",
};

export const EFFECT_LABEL: Record<string, string> = {
  MONITOR: "Monitor",
  REQUIRE_APPROVAL: "Require approval",
  DENY: "Deny",
  SUSPEND_RUNTIME_IDENTITY: "Suspend identity",
};

export const BUDGET_STATUS_LABEL: Record<string, string> = {
  OK: "Healthy",
  WARNING: "Warning",
  EXCEEDED: "Exceeded",
  UNAVAILABLE: "Measurement unavailable",
  NO_ACTIVE_WINDOW: "No active window",
};

/** Spend share movement, worded neutrally (growth is not inherently bad). */
export function comparisonSummary(dashboard: FinOpsDashboard): {
  text: string;
  caveat: string | null;
} {
  const change = dashboard.comparison.total_cost_change_pct;
  const caveat = dashboard.comparison.like_for_like
    ? null
    : "Pricing coverage differs between the two periods, so this change may reflect measurement rather than usage.";
  if (change === null) {
    return { text: "No comparable previous period", caveat };
  }
  return { text: `${change > 0 ? "+" : change < 0 ? "−" : ""}${Math.abs(change)}%`, caveat };
}
