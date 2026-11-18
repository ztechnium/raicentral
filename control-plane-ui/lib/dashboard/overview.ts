import { formatMoney } from "@/lib/finops-format";
import type {
  AttentionItem,
  FinOpsDashboard,
  PortfolioAttentionItem,
  RankedItem,
} from "@/lib/types";

export const PERIODS = {
  "24h": { hours: 24, label: "24 hours" },
  "7d": { hours: 24 * 7, label: "7 days" },
  "30d": { hours: 24 * 30, label: "30 days" },
} as const;

export type PeriodKey = keyof typeof PERIODS;
export const DEFAULT_PERIOD: PeriodKey = "30d";

export function parsePeriod(value: string | null | undefined): PeriodKey {
  return value && value in PERIODS ? (value as PeriodKey) : DEFAULT_PERIOD;
}

/** Stable boundaries: `end` is floored to the minute so every widget shares one window. */
export function periodRange(key: PeriodKey, now: Date = new Date()) {
  const end = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const start = new Date(end.getTime() - PERIODS[key].hours * 3_600_000);
  return { period_start: start.toISOString(), period_end: end.toISOString() };
}

const SEVERITY_RANK: Record<string, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
  INFO: 4,
};

export function severityRank(severity?: string | null) {
  return SEVERITY_RANK[(severity ?? "").toUpperCase()] ?? 5;
}

export type AttentionEntry = {
  key: string;
  severity: string;
  title: string;
  aiSystemId: string | null;
  reason: string;
  action: string;
  dueAt: string | null;
  updatedAt: string | null;
  href: string;
};

type RawAttention = AttentionItem | PortfolioAttentionItem;

function toEntry(item: RawAttention): AttentionEntry {
  const drill = "drill_down" in item ? item.drill_down : null;
  return {
    key: `${item.source_type}:${item.source_id}`,
    severity: (item.severity ?? "").toUpperCase(),
    title: item.title,
    aiSystemId: item.ai_system_id ?? null,
    reason: item.reason,
    action: item.recommended_action || "Review",
    dueAt: item.due_at ?? null,
    updatedAt: item.created_at ?? null,
    href: drill || (item.ai_system_id ? `/governance/registry/${item.ai_system_id}` : "/portfolio"),
  };
}

function time(value: string | null) {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

/** Merge attention sources, dedupe by source identity, sort critical → high → due → recency. */
export function buildAttentionQueue(
  ...sources: Array<ReadonlyArray<RawAttention> | null | undefined>
): AttentionEntry[] {
  const byKey = new Map<string, AttentionEntry>();
  for (const source of sources) {
    for (const item of source ?? []) {
      const entry = toEntry(item);
      const existing = byKey.get(entry.key);
      if (!existing || severityRank(entry.severity) < severityRank(existing.severity)) {
        byKey.set(entry.key, entry);
      }
    }
  }
  return Array.from(byKey.values()).sort((a, b) => {
    const bySeverity = severityRank(a.severity) - severityRank(b.severity);
    if (bySeverity !== 0) return bySeverity;
    const dueA = time(a.dueAt);
    const dueB = time(b.dueAt);
    if (dueA !== dueB) {
      if (dueA === null) return 1;
      if (dueB === null) return -1;
      return dueA - dueB;
    }
    return (time(b.updatedAt) ?? 0) - (time(a.updatedAt) ?? 0);
  });
}

/** Budget pressure comes from FinOps; only real counts become entries. */
export function budgetAttention(dashboard: FinOpsDashboard | null | undefined): AttentionEntry[] {
  const pressure = dashboard?.budget_pressure;
  if (!pressure) return [];
  const entries: AttentionEntry[] = [];
  if (pressure.exceeded > 0) {
    entries.push({
      key: "budget:exceeded",
      severity: "HIGH",
      title: `${pressure.exceeded} budget${pressure.exceeded === 1 ? "" : "s"} exceeded`,
      aiSystemId: null,
      reason: "Spend passed the configured limit.",
      action: "Review budgets",
      dueAt: null,
      updatedAt: null,
      href: "/risk/budgets",
    });
  }
  if (pressure.warning > 0) {
    entries.push({
      key: "budget:warning",
      severity: "MEDIUM",
      title: `${pressure.warning} budget${pressure.warning === 1 ? "" : "s"} near the limit`,
      aiSystemId: null,
      reason: "Spend crossed the warning threshold.",
      action: "Review budgets",
      dueAt: null,
      updatedAt: null,
      href: "/risk/budgets",
    });
  }
  return entries;
}

export type SpendKpi = {
  value: string;
  detail: string;
  available: boolean;
};

/**
 * Metered/billed spend is the headline. Estimated spend is shown separately, never summed in.
 * Missing cost is "Unavailable", never zero.
 */
export function spendKpi(dashboard: FinOpsDashboard | null | undefined): SpendKpi {
  const totals = dashboard?.totals;
  if (!totals) return { value: "Unavailable", detail: "Spend data could not be loaded.", available: false };
  const currency = totals.currency;
  if (currency === "MIXED") {
    return {
      value: "Mixed currencies",
      detail: `Spend is recorded in ${totals.currencies?.join(", ")}; amounts are not combined. See FinOps.`,
      available: false,
    };
  }
  const unavailableCount = totals.unavailable_cost_count || 0;
  // State comes from the backend's per-state counts. Whether an amount is zero says nothing
  // about whether it is known: a genuine $0.00 billed amount is still a known amount.
  const state = totals.cost_state;
  const settled = totals.actual_cost ?? null; // billed + metered, null when neither exists
  const estimated = totals.estimated_cost ?? null;
  if (state === "NO_DATA" || state === "UNAVAILABLE" || (settled === null && estimated === null)) {
    return {
      value: "Unavailable",
      detail:
        state === "NO_DATA"
          ? "No usage in this period."
          : unavailableCount > 0
            ? `${unavailableCount} calls have no cost. Check pricing in FinOps.`
            : "No priced usage in this period.",
      available: false,
    };
  }
  const parts: string[] = [];
  if (settled !== null) {
    const billed = totals.billed_cost ?? null;
    const metered = totals.metered_cost ?? null;
    parts.push(billed !== null && metered !== null ? "billed + metered" : billed !== null ? "billed" : "metered");
  }
  if (settled !== null && estimated !== null) {
    parts.push(`+ ≈ ${formatMoney(estimated, currency ?? undefined)} estimated`);
  }
  if (unavailableCount > 0) parts.push(`${unavailableCount} calls unpriced`);
  const headline = settled !== null ? settled : (estimated as number);
  return {
    value: formatMoney(headline, currency ?? undefined) ?? "Unavailable",
    detail: settled !== null ? parts.join(" · ") : "estimated only · no metered cost yet",
    available: true,
  };
}

export type SystemReviewRow = {
  id: string;
  name: string;
  severity: string | null;
  openFindings: number;
  spend: number | null;
  href: string;
};

/** Join by AI system id (never name). Ordered by highest severity, then open findings. */
export function buildSystemReviewRows(
  risk: ReadonlyArray<RankedItem> | null | undefined,
  spendByKey: ReadonlyMap<string, number> | null,
  limit = 8,
): SystemReviewRow[] {
  return (risk ?? [])
    .map((item) => ({
      id: item.id,
      name: item.name,
      severity: item.severity ? item.severity.toUpperCase() : null,
      openFindings: item.value,
      spend: spendByKey ? (spendByKey.get(item.id) ?? null) : null,
      href: item.link || `/governance/registry/${item.id}`,
    }))
    .sort(
      (a, b) =>
        severityRank(a.severity) - severityRank(b.severity) || b.openFindings - a.openFindings,
    )
    .slice(0, limit);
}
