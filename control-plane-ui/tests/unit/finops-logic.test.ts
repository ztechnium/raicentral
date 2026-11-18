import { describe, expect, it } from "vitest";
import { shorten } from "@/components/finops/spend-charts";
import { spendKpi } from "@/lib/dashboard/overview";
import {
  DIMENSIONS,
  dimensionById,
  parsePage,
  parseSort,
} from "@/lib/finops-dimensions";
import {
  filtersToQuery,
  filtersToSearch,
  parseFilters,
  periodRange,
  validateCustomRange,
} from "@/lib/finops-filters";
import {
  formatAmount,
  formatChange,
  formatCost,
  formatPct,
  formatTokens,
} from "@/lib/finops-format";
import {
  buildNeedsAttention,
  enforcementKind,
  formatBurnRate,
  formatUnitValue,
  formatWindow,
  withFilters,
} from "@/lib/finops-insights";
import { budget, dashboard, emptyDashboard, totals } from "./finops-fixtures";

const parse = (qs: string) => parseFilters(new URLSearchParams(qs));

describe("zero is not unknown (release blocker)", () => {
  it("renders a genuine zero billed amount as money, not Unavailable", () => {
    expect(formatCost(0, "BILLED")).toContain("0.00");
    expect(formatCost(0, "BILLED")).not.toBe("Unavailable");
    expect(formatAmount(0, "USD")).toBe("$0.00");
  });

  it("renders unknown as Unavailable and no data as No data", () => {
    expect(formatCost(null, "BILLED")).toBe("Unavailable");
    expect(formatCost(0, "UNAVAILABLE")).toBe("Unavailable");
    expect(formatCost(null, "NO_DATA")).toBe("No data");
    expect(formatPct(null)).toBe("—");
    expect(formatPct(0)).toBe("0%");
    expect(formatTokens(null)).toBe("—");
  });

  it("does not format amounts across currencies", () => {
    expect(formatCost(5, "METERED", "MIXED")).toBe("Mixed currencies");
    expect(formatAmount(5, null)).toBe("Mixed currencies");
    expect(formatAmount(1.5, "EUR")).toContain("1.50");
    expect(formatAmount(1.5, "EUR")).not.toContain("$");
  });

  it("words period change neutrally and handles a missing baseline", () => {
    expect(formatChange(12.34)).toBe("+12.3% vs previous period");
    expect(formatChange(-5)).toBe("−5.0% vs previous period");
    expect(formatChange(0)).toBe("No change vs previous period");
    expect(formatChange(null)).toBe("No prior-period baseline");
  });
});

describe("dashboard spend KPI uses state, not truthiness", () => {
  it("shows a valid $0 billed spend as available", () => {
    const kpi = spendKpi(
      dashboard({
        totals: totals({
          cost_state: "BILLED",
          total_cost: 0,
          billed_cost: 0,
          metered_cost: null,
          actual_cost: 0,
          estimated_cost: null,
        }),
      }),
    );
    expect(kpi.available).toBe(true);
    expect(kpi.value).toBe("$0.00");
    expect(kpi.detail).toContain("billed");
  });

  it("says no data when nothing matched, and unavailable when nothing is priced", () => {
    expect(spendKpi(emptyDashboard()).detail).toBe("No usage in this period.");
    const unpriced = spendKpi(
      dashboard({
        totals: totals({
          cost_state: "UNAVAILABLE",
          total_cost: null,
          billed_cost: null,
          metered_cost: null,
          actual_cost: null,
          estimated_cost: null,
          unavailable_cost_count: 3,
        }),
      }),
    );
    expect(unpriced.available).toBe(false);
    expect(unpriced.detail).toContain("3 calls have no cost");
  });

  it("refuses to show a single figure for mixed currencies", () => {
    const kpi = spendKpi(
      dashboard({ totals: totals({ currency: "MIXED", currencies: ["EUR", "USD"] }) }),
    );
    expect(kpi.value).toBe("Mixed currencies");
    expect(kpi.available).toBe(false);
  });

  it("uses the dataset currency rather than assuming USD", () => {
    const kpi = spendKpi(
      dashboard({ totals: totals({ currency: "EUR", currencies: ["EUR"], actual_cost: 12 }) }),
    );
    expect(kpi.value).toContain("12");
    expect(kpi.value).not.toContain("$");
  });
});

describe("URL filter state", () => {
  it("defaults to the last 30 days with no filters", () => {
    const { filters, errors } = parse("");
    expect(filters.period).toBe("30d");
    expect(errors).toEqual([]);
    expect(filtersToSearch(filters).toString()).toBe("");
  });

  it("round-trips supported filters", () => {
    const id = "3f2a1b4c-0000-4000-8000-000000000001";
    const { filters } = parse(`period=7d&ai_system_id=${id}&model=gpt-4o&cost_state=billed&currency=eur`);
    expect(filters).toMatchObject({ period: "7d", ai_system_id: id, model: "gpt-4o", cost_state: "BILLED", currency: "EUR" });
    const again = parseFilters(filtersToSearch(filters)).filters;
    expect(again).toEqual(filters);
  });

  it("reports invalid values instead of silently applying or dropping them", () => {
    const { filters, errors } = parse("ai_system_id=not-a-uuid&cost_state=BOGUS&period=forever&currency=us");
    expect(filters.ai_system_id).toBeUndefined();
    expect(filters.cost_state).toBeUndefined();
    expect(filters.currency).toBeUndefined();
    expect(errors).toHaveLength(4);
  });

  it("validates custom date ranges", () => {
    expect(validateCustomRange("2026-11-10", "2026-11-01")).toMatch(/before/);
    expect(validateCustomRange("", "2026-11-01")).toMatch(/both/);
    expect(validateCustomRange("2026-02-30x", "2026-03-01")).toMatch(/valid/);
    expect(validateCustomRange("2024-01-01", "2026-01-01")).toMatch(/exceed/);
    expect(validateCustomRange("2026-11-01", "2026-11-18")).toBeNull();
  });

  it("ignores an invalid custom range and falls back to the default", () => {
    const { filters, errors } = parse("period=custom&from=2026-11-10&to=2026-11-01");
    expect(filters.period).toBe("30d");
    expect(errors[0]).toMatch(/Custom date range ignored/);
  });

  it("makes a custom range inclusive of the end date", () => {
    const { filters } = parse("period=custom&from=2026-11-01&to=2026-11-03");
    expect(periodRange(filters)).toEqual({
      period_start: "2026-11-01T00:00:00.000Z",
      period_end: "2026-11-04T00:00:00.000Z",
    });
  });

  it("pins the end of a preset window to the minute so widgets share one range", () => {
    const now = new Date("2026-11-18T10:15:42Z");
    const range = periodRange(parse("period=7d").filters, now);
    expect(range.period_end).toBe("2026-11-18T10:15:00.000Z");
    expect(range.period_start).toBe("2026-11-11T10:15:00.000Z");
  });

  it("sends only supported filters to the API", () => {
    const query = filtersToQuery(parse("model=gpt-4o&provider=openai").filters, new Date("2026-11-18T00:00:00Z"));
    expect(Object.keys(query).sort()).toEqual(["model", "period_end", "period_start", "provider"]);
  });
});

describe("dimensions contract", () => {
  it("covers exactly the dimensions the backend accepts", () => {
    expect(DIMENSIONS.map((d) => d.id)).toEqual([
      "ai_system",
      "agent",
      "agent_version",
      "runtime_identity",
      "delegation",
      "model",
      "provider",
      "environment",
      "tool",
      "integration",
    ]);
  });

  it("only offers search where the server supports it", () => {
    expect(DIMENSIONS.filter((d) => d.searchable).map((d) => d.id).sort()).toEqual(
      ["environment", "model", "provider", "tool"],
    );
  });

  it("falls back safely on bad URL values", () => {
    expect(dimensionById("nope").id).toBe("ai_system");
    expect(parseSort("nope")).toBe("cost");
    expect(parsePage("-3")).toBe(1);
    expect(parsePage("abc")).toBe(1);
    expect(parsePage("4")).toBe(4);
  });
});

describe("needs attention", () => {
  const filters = parse("").filters;

  it("surfaces exceeded budgets first, linked to the policy record", () => {
    const items = buildNeedsAttention(
      dashboard(),
      {
        as_of: "2026-11-18T00:00:00Z",
        summary: { total: 2, exceeded: 1, warning: 1, healthy: 0, unavailable: 0, no_active_window: 0 },
        items: [
          budget({ budget_id: "warn", name: "Warn", status: "WARNING" }),
          budget({ budget_id: "bad", name: "Bad", status: "EXCEEDED" }),
          budget({ budget_id: "ok", name: "Fine", status: "OK" }),
        ],
      },
      filters,
    );
    expect(items.map((i) => i.href)).toEqual(["/risk/budgets/bad", "/risk/budgets/warn"]);
    expect(items[0].severity).toBe("high");
  });

  it("reports metering gaps from real coverage and nothing when there are none", () => {
    expect(buildNeedsAttention(dashboard(), null, filters)).toEqual([]);
    const gaps = buildNeedsAttention(
      dashboard({
        coverage: { ...dashboard().coverage, unavailable_pct: 20, unattributed_pct: 5 },
      }),
      null,
      filters,
    );
    expect(gaps.map((g) => g.key)).toEqual(["gap:unpriced", "gap:unattributed"]);
    expect(gaps[0].severity).toBe("medium");
    expect(gaps[1].severity).toBe("info");
    expect(gaps[0].href).toContain("cost_state=UNAVAILABLE");
  });

  it("invents nothing for an empty dataset", () => {
    expect(buildNeedsAttention(emptyDashboard(), null, filters)).toEqual([]);
  });

  it("flags mixed currencies", () => {
    const items = buildNeedsAttention(
      dashboard({ totals: totals({ currency: "MIXED", currencies: ["EUR", "USD"] }) }),
      null,
      filters,
    );
    expect(items.map((i) => i.key)).toContain("gap:currency");
  });

  it("carries the active filters into the destination", () => {
    const f = parse("period=7d&model=gpt").filters;
    expect(withFilters("/finops/usage", f, { dimension: "model" })).toBe(
      "/finops/usage?period=7d&model=gpt&dimension=model",
    );
  });
});

describe("chart labels", () => {
  it("keeps the start of a long name rather than clipping its head", () => {
    expect(shorten("short", 20)).toBe("short");
    const long = "anthropic.claude-3-5-sonnet-20241022-v2:0-regional";
    const out = shorten(long, 20);
    expect(out).toHaveLength(20);
    expect(out.startsWith("anthropic.claude-3")).toBe(true);
    expect(out.endsWith("…")).toBe(true);
  });
});

describe("budget presentation", () => {
  it("separates configured from enforced", () => {
    expect(enforcementKind(budget())).toBe("enforced");
    expect(
      enforcementKind(budget({ evidence: { lookback_days: 30, checks: 0, last_check_at: null, blocking_interventions: 0 } })),
    ).toBe("unproven");
    expect(enforcementKind(budget({ blocking: false, effect_on_exceed: "MONITOR" }))).toBe("monitor");
  });

  it("states units instead of assuming a currency", () => {
    expect(formatUnitValue(1200, "tokens")).toBe("1,200 tokens");
    expect(formatUnitValue(12.5, "currency")).toBe("12.5 currency units");
    expect(formatWindow(86400)).toBe("24 hours");
    expect(formatWindow(3600)).toBe("1 hour");
    expect(formatWindow(300)).toBe("5 minutes");
    expect(formatBurnRate(null, "actions")).toBe("Insufficient data");
    expect(formatBurnRate(7.5, "actions")).toBe("7.5 actions / hour");
  });
});
