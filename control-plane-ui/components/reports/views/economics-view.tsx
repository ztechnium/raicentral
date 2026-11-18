"use client";

import Link from "next/link";
import { Kpi, stateOf } from "@/components/reports/kpi";
import { BarList, type BarItem } from "@/components/reports/bar-list";
import { NotConfigured, QueryPanel } from "@/components/reports/states";
import { useViewQuery, type ViewContext } from "@/components/reports/view-context";
import { reportsClient, type FinOpsSummary } from "@/lib/reports/client";
import { formatCount, formatMoney, formatPercent, humanize } from "@/lib/reports/format";
import type { BudgetStatus } from "@/lib/reports/types";

const METRIC_UNIT: Record<string, string> = {
  COST: "currency",
  AMOUNT: "currency",
  TOKEN_USAGE: "tokens",
  ACTION_COUNT: "actions",
  TOOL_COUNT: "tool calls",
};

/** Measured (billed + metered + actual) spend; null when no state reported any. */
function measuredSpend(totals?: FinOpsSummary["totals"]): number | null {
  if (!totals) return null;
  const parts = [totals.billed_cost, totals.metered_cost, totals.actual_cost].filter(
    (v): v is number => typeof v === "number",
  );
  return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
}

function groupByMetric(budgets: BudgetStatus[]): Map<string, BudgetStatus[]> {
  const groups = new Map<string, BudgetStatus[]>();
  for (const budget of budgets) {
    const key = (budget.metric || "UNKNOWN").toUpperCase();
    groups.set(key, [...(groups.get(key) ?? []), budget]);
  }
  return groups;
}

export function EconomicsView({ ctx }: { ctx: ViewContext }) {
  const finops = useViewQuery(ctx, "economics", "/v1/finops/dashboard", reportsClient.finops, {
    only: ["period_start", "period_end", "ai_system_id"],
  });
  const budgets = useViewQuery(ctx, "economics", "/v1/reports/budgets", reportsClient.budgets);
  const fState = stateOf(finops);
  const totals = finops.data?.totals;
  const currency = totals?.currency ?? null;
  const measured = measuredSpend(totals);
  const estimated = totals?.estimated_cost ?? null;
  const pressure = finops.data?.budget_pressure;

  return (
    <div className="space-y-4">
      <div
        role="note"
        className="rounded-md border border-base-200 bg-base-100 p-3 text-sm text-ink-600"
      >
        <strong className="text-ink-900">
          Spend comes from FinOps, budgets from the budget ledger.
        </strong>{" "}
        Spend totals use the FinOps span-based cost states (billed, metered, estimated,
        unavailable). Budget utilization is a separate measurement of each budget&apos;s current
        window, so the two are shown side by side and never added together. This page summarizes;
        the detail lives in{" "}
        <Link href="/finops" className="font-semibold text-accent-700">
          FinOps
        </Link>{" "}
        and{" "}
        <Link href="/risk/budgets" className="font-semibold text-accent-700">
          Budgets
        </Link>
        .
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Measured spend"
          loading={fState.loading}
          error={fState.error}
          value={formatMoney({
            amount: measured,
            currency,
            costState: measured === null ? "UNAVAILABLE" : "METERED",
          })}
          definition="Billed + metered cost reported for the period. A period with no measured cost shows Unavailable, not zero."
          href="/finops"
          hrefLabel="Open FinOps"
        />
        <Kpi
          label="Estimated spend"
          loading={fState.loading}
          error={fState.error}
          value={formatMoney({ amount: estimated, currency, costState: "ESTIMATED" })}
          definition="Cost derived from catalog pricing rather than a provider charge. Marked ≈ and kept apart from measured spend."
          href="/finops"
        />
        <Kpi
          label="Cost measurement coverage"
          loading={fState.loading}
          error={fState.error}
          value={formatPercent(finops.data?.coverage?.cost_coverage_pct ?? null)}
          detail={
            finops.data
              ? `${formatCount(totals?.unavailable_cost_count ?? null)} calls without any cost data`
              : undefined
          }
          definition="Share of metered calls that carry a cost figure. The remainder is unknown, not free."
        />
        <Kpi
          label="Spend linked to risk"
          loading={fState.loading}
          error={fState.error}
          value={formatMoney({
            amount: finops.data?.risk_associated_spend ?? null,
            currency,
            costState: finops.data?.risk_associated_spend == null ? "UNAVAILABLE" : "METERED",
          })}
          definition="Spend on trajectories that produced findings, as reported by FinOps."
          href="/finops"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel
          title="Cost by measurement state"
          description="How much of the spend is measured vs estimated."
          query={finops}
        >
          {(data) =>
            data.coverage ? (
              <BarList
                label="Calls by cost state"
                unit="%"
                sortable={false}
                items={(
                  [
                    {
                      key: "billed",
                      label: "Billed",
                      value: data.coverage.billed_pct ?? 0,
                      tone: "accent",
                    },
                    {
                      key: "metered",
                      label: "Metered",
                      value: (data.coverage.metered_pct ?? 0) + (data.coverage.actual_pct ?? 0),
                      tone: "accent",
                    },
                    {
                      key: "estimated",
                      label: "Estimated",
                      value: data.coverage.estimated_pct ?? 0,
                      tone: "warn",
                    },
                    {
                      key: "unavailable",
                      label: "Unavailable",
                      value: data.coverage.unavailable_pct ?? 0,
                      tone: "muted",
                    },
                  ] satisfies BarItem[]
                ).map((item) => ({ ...item, value: Math.round(item.value * 10) / 10 }))}
              />
            ) : (
              <NotConfigured title="No cost coverage data">
                FinOps returned no coverage breakdown for this period.
              </NotConfigured>
            )
          }
        </QueryPanel>

        <QueryPanel
          title="Budget pressure"
          description="Budgets by current-window status (FinOps)."
          query={finops}
        >
          {() =>
            pressure ? (
              <BarList
                label="Budgets by status"
                unit="budgets"
                sortable={false}
                items={[
                  {
                    key: "healthy",
                    label: "Healthy",
                    value: pressure.healthy ?? 0,
                    tone: "accent",
                  },
                  {
                    key: "warning",
                    label: "Warning",
                    value: pressure.warning ?? 0,
                    tone: "warn",
                    href: "/risk/budgets",
                  },
                  {
                    key: "exceeded",
                    label: "Exceeded",
                    value: pressure.exceeded ?? 0,
                    tone: "danger",
                    href: "/risk/budgets",
                  },
                ]}
              />
            ) : (
              <NotConfigured title="No budgets reported">
                No budget pressure was returned for this scope.
              </NotConfigured>
            )
          }
        </QueryPanel>
      </div>

      <QueryPanel
        title="Budget utilization"
        description="Each budget's current window. Utilization is only comparable within the same metric, so budgets are grouped by metric and never summed."
        query={budgets}
        isEmpty={(data) => data.budgets.length === 0}
        emptyMessage="No enabled budgets have an active window."
      >
        {(data) => (
          <div className="space-y-5">
            {[...groupByMetric(data.budgets)].map(([metric, group]) => (
              <section key={metric} aria-label={`${humanize(metric)} budgets`}>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">
                  {humanize(metric)} budgets · unit: {METRIC_UNIT[metric] ?? "units"}
                </h3>
                <BarList
                  label={`${humanize(metric)} budget utilization`}
                  unit="%"
                  items={group.map((budget) => ({
                    key: budget.id,
                    label: budget.name,
                    value: Math.round(budget.utilization_pct * 10) / 10,
                    tone:
                      budget.status === "EXCEEDED"
                        ? "danger"
                        : budget.status === "WARNING"
                          ? "warn"
                          : "accent",
                    href: `/risk/budgets/${budget.id}`,
                    note: `${formatCount(budget.current_usage)} of ${formatCount(budget.limit_value)} · ${humanize(budget.status)}`,
                  }))}
                />
              </section>
            ))}
          </div>
        )}
      </QueryPanel>
    </div>
  );
}
