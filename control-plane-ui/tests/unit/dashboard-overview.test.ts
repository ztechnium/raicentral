import { describe, expect, it } from "vitest";
import {
  buildAttentionQueue,
  buildSystemReviewRows,
  budgetAttention,
  parsePeriod,
  periodRange,
  spendKpi,
} from "@/lib/dashboard/overview";
import type { AttentionItem, FinOpsDashboard } from "@/lib/types";

function item(over: Partial<AttentionItem>): AttentionItem {
  return {
    type: "risk",
    severity: "HIGH",
    title: "t",
    source_type: "finding",
    source_id: "1",
    reason: "r",
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function finops(totals: Record<string, unknown>, pressure = { healthy: 0, warning: 0, exceeded: 0 }) {
  return {
    totals: { currency: "USD", cost_state: "METERED", unavailable_cost_count: 0, ...totals },
    budget_pressure: pressure,
  } as unknown as FinOpsDashboard;
}

describe("dashboard overview logic", () => {
  it("defaults unknown periods and builds a stable window", () => {
    expect(parsePeriod("bogus")).toBe("30d");
    expect(parsePeriod("7d")).toBe("7d");
    const range = periodRange("24h", new Date("2026-05-01T10:30:45Z"));
    expect(range.period_end).toBe("2026-05-01T10:30:00.000Z");
    expect(range.period_start).toBe("2026-04-30T10:30:00.000Z");
  });

  it("sorts critical first, dedupes by source and orders by due date", () => {
    const queue = buildAttentionQueue(
      [
        item({ source_id: "a", severity: "HIGH", due_at: "2026-02-01T00:00:00Z" }),
        item({ source_id: "b", severity: "CRITICAL" }),
        item({ source_id: "c", severity: "HIGH", due_at: "2026-01-15T00:00:00Z" }),
      ],
      [item({ source_id: "b", severity: "CRITICAL" })],
    );
    expect(queue.map((entry) => entry.key)).toEqual(["finding:b", "finding:c", "finding:a"]);
  });

  it("handles null sources without inventing items", () => {
    expect(buildAttentionQueue(null, undefined)).toEqual([]);
  });

  it("never shows missing spend as zero", () => {
    expect(spendKpi(null).value).toBe("Unavailable");
    const none = spendKpi(
      finops({ cost_state: "UNAVAILABLE", metered_cost: 0, actual_cost: 0, estimated_cost: 0, unavailable_cost_count: 3 }),
    );
    expect(none.value).toBe("Unavailable");
    expect(none.available).toBe(false);
  });

  it("keeps estimated spend separate from metered spend", () => {
    const kpi = spendKpi(finops({ metered_cost: 12.5, actual_cost: 12.5, estimated_cost: 3 }));
    expect(kpi.value).toBe("$12.50");
    expect(kpi.detail).toContain("estimated");
    expect(kpi.detail).toContain("$3.00");
    const estimatedOnly = spendKpi(finops({ metered_cost: 0, actual_cost: 0, estimated_cost: 3 }));
    expect(estimatedOnly.detail).toContain("estimated only");
  });

  it("derives budget attention only from real pressure counts", () => {
    expect(budgetAttention(finops({}))).toEqual([]);
    const entries = budgetAttention(finops({}, { healthy: 1, warning: 2, exceeded: 1 }));
    expect(entries.map((entry) => entry.severity)).toEqual(["HIGH", "MEDIUM"]);
  });

  it("joins spend by system id and orders by severity then findings", () => {
    const rows = buildSystemReviewRows(
      [
        { id: "s1", name: "Same", value: 9, severity: "HIGH" },
        { id: "s2", name: "Same", value: 1, severity: "CRITICAL" },
        { id: "s3", name: "Other", value: 4, severity: "HIGH" },
      ],
      new Map([["s2", 7]]),
    );
    expect(rows.map((row) => row.id)).toEqual(["s2", "s1", "s3"]);
    expect(rows[0].spend).toBe(7);
    expect(rows[1].spend).toBeNull();
  });
});
