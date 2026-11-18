"use client";

import Link from "next/link";
import { FilterBar } from "@/components/finops/filter-bar";
import { CostStateBadge } from "@/components/finops/cost-state-badge";
import { MetricCard } from "@/components/finops/metric-card";
import { SpendRankChart, SpendTrendChart } from "@/components/finops/spend-charts";
import { WidgetBody } from "@/components/dashboard/widget-body";
import { MetricStrip } from "@/components/console/page-parts";
import { Panel } from "@/components/registry/panel";
import { SectionHeader } from "@/components/section-header";
import { getFinOpsBreakdown, getFinOpsBudgets, getFinOpsDashboard, getFinOpsTimeseries } from "@/lib/api";
import { useWidget } from "@/lib/dashboard/use-widget";
import { formatAmount, formatChange, formatCount, formatPct } from "@/lib/finops-format";
import {
  BUDGET_STATUS_LABEL,
  buildNeedsAttention,
  comparisonSummary,
  enforcementKind,
  ENFORCEMENT_LABEL,
  formatUnitValue,
  withFilters,
} from "@/lib/finops-insights";
import { periodLabel } from "@/lib/finops-filters";
import { useFinOpsFilters } from "@/lib/use-finops-filters";
import type { FinOpsDashboard } from "@/lib/types";

function Definition({ text }: { text?: string }) {
  if (!text) return null;
  return <p className="mt-2 text-xs text-ink-500">{text}</p>;
}

function CurrencyBanner({ totals }: { totals: FinOpsDashboard["totals"] }) {
  if (totals.currency !== "MIXED" || !totals.by_currency) return null;
  return (
    <div role="status" className="rounded-lg border border-warn-600/30 bg-warn-100 p-4 text-sm text-warn-700">
      <p className="font-semibold">
        Usage in this period is priced in more than one currency, so totals are not combined.
      </p>
      <p className="mt-1">
        No conversion is applied. Pick a currency in the filters to see its totals; per-currency
        known cost is shown below.
      </p>
      <ul className="mt-2 list-disc pl-5">
        {totals.by_currency.map((row) => (
          <li key={row.currency}>
            {row.currency}: {formatAmount(row.total_cost, row.currency)} known across{" "}
            {formatCount(row.spans)} calls
          </li>
        ))}
      </ul>
    </div>
  );
}

function SpendCards({ dashboard }: { dashboard: FinOpsDashboard }) {
  const { totals, coverage, budget_pressure: pressure } = dashboard;
  const currency = totals.currency;
  const comparison = comparisonSummary(dashboard);
  const noData = totals.cost_state === "NO_DATA";
  const unpriced = totals.span_counts.unavailable;

  const knownDetail = noData
    ? "No usage matched these filters."
    : [
        totals.billed_cost !== null ? `Billed ${formatAmount(totals.billed_cost, currency)}` : null,
        totals.metered_cost !== null ? `Metered ${formatAmount(totals.metered_cost, currency)}` : null,
        totals.estimated_cost !== null ? `Estimated ${formatAmount(totals.estimated_cost, currency)}` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "No priced usage.";

  return (
    <MetricStrip>
      <MetricCard
        testId="card-known-spend"
        title="Known spend"
        value={noData ? "No data" : formatAmount(totals.total_cost, currency)}
        badge={<CostStateBadge state={totals.cost_state} />}
        detail={knownDetail}
        caveat={
          unpriced > 0
            ? `${formatCount(unpriced)} call${unpriced === 1 ? "" : "s"} have no price, so this is a floor, not the full total.`
            : null
        }
      />
      <MetricCard
        testId="card-billed"
        title="Billed cost"
        value={
          noData ? "No data" : totals.billed_cost === null ? "None recorded" : formatAmount(totals.billed_cost, currency)
        }
        detail="Invoice- or billing-API-backed amounts only."
      />
      <MetricCard
        testId="card-estimated"
        title="Estimated spend"
        value={
          noData
            ? "No data"
            : totals.estimated_cost === null
              ? "None"
              : `≈ ${formatAmount(totals.estimated_cost, currency)}`
        }
        detail="Pre-execution estimates. Not confirmed incurred spend; excluded from billed and metered."
      />
      <MetricCard
        testId="card-coverage"
        title="Measurement coverage"
        value={formatPct(coverage.cost_coverage_pct)}
        detail={
          <>
            Calls with a price. Unattributed usage {formatPct(coverage.unattributed_pct)} · No price{" "}
            {formatPct(coverage.unavailable_pct)}
          </>
        }
        caveat="Cost measurement only, not governance control coverage."
      />
      <MetricCard
        testId="card-budget-pressure"
        title="Budget pressure"
        value={`${pressure.warning} warning · ${pressure.exceeded} exceeded`}
        tone={pressure.exceeded > 0 ? "danger" : pressure.warning > 0 ? "warn" : "neutral"}
        detail={`${pressure.total} enabled budget${pressure.total === 1 ? "" : "s"} · current windows only`}
        href="/finops/budgets"
        hrefLabel="open budgets and forecasting"
      />
      <MetricCard
        testId="card-comparison"
        title="Vs previous period"
        value={comparison.text}
        detail={
          dashboard.comparison.total_cost_change_pct === null
            ? formatChange(null)
            : "Change in known spend. Growth is neutral, not a defect."
        }
        caveat={comparison.caveat}
      />
    </MetricStrip>
  );
}

export default function FinOpsOverviewPage() {
  const { filters, errors, query, dataKey, setFilters, refresh } = useFinOpsFilters();

  const [dash, retryDash] = useWidget(`dash:${dataKey}`, () => getFinOpsDashboard(query));
  const [series, retrySeries] = useWidget(`series:${dataKey}`, () => getFinOpsTimeseries(query));
  const [systems, retrySystems] = useWidget(`systems:${dataKey}`, () =>
    getFinOpsBreakdown({ ...query, dimension: "ai_system", limit: 8 }),
  );
  const [budgets, retryBudgets] = useWidget(`budgets:${filters.ai_system_id ?? ""}:${dataKey}`, () =>
    getFinOpsBudgets({ ai_system_id: filters.ai_system_id }),
  );

  const currencies = dash.status === "ready" ? dash.data.totals.currencies : [];
  const attention = buildNeedsAttention(
    dash.status === "ready" ? dash.data : null,
    budgets.status === "ready" ? budgets.data : null,
    filters,
  );

  return (
    <div className="space-y-6" data-testid="finops-overview">
      <SectionHeader
        eyebrow="FinOps"
        title="AI economics"
        subtitle="What AI usage cost, how confident each figure is, and where spend is under pressure. Billed, metered and estimated amounts are never merged into one number."
      />
      <FilterBar
        filters={filters}
        errors={errors}
        currencies={currencies}
        onChange={setFilters}
        onRefresh={() => {
          refresh();
          retryDash();
        }}
      />

      <WidgetBody state={dash} label="spend summary" onRetry={retryDash}>
        {(data) => (
          <div className="space-y-4">
            <CurrencyBanner totals={data.totals} />
            <SpendCards dashboard={data} />
            <p className="text-xs text-ink-500">
              {periodLabel(filters)} · {formatCount(data.totals.span_counts.total)} metered calls ·
              averages divide by trajectories with a known cost (
              {formatCount(data.totals.known_cost_trajectories)} of{" "}
              {formatCount(data.totals.trajectories)}).
            </p>
          </div>
        )}
      </WidgetBody>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Spend trend" description="Daily known spend, split by cost state.">
          <WidgetBody state={series} label="spend trend" onRetry={retrySeries}>
            {(data) => <SpendTrendChart points={data.points} currency={data.currency} />}
          </WidgetBody>
        </Panel>
        <Panel
          title="Top AI systems"
          description="Ranked by known cost. Unpriced and unattributed usage is listed, not hidden."
          actions={
            <Link
              href={withFilters("/finops/usage", filters, { dimension: "ai_system" })}
              className="text-sm font-semibold text-accent-700"
            >
              Explore all
            </Link>
          }
        >
          <WidgetBody state={systems} label="top AI systems" onRetry={retrySystems}>
            {(data) => {
              const priced = data.items.filter((item) => item.cost !== null);
              const unknown = data.items.filter((item) => item.cost === null);
              return (
                <div>
                  <SpendRankChart
                    currency={data.currency}
                    data={priced.map((item) => ({ name: item.label || "Unattributed", value: item.cost as number }))}
                  />
                  {unknown.length > 0 ? (
                    <p className="mt-2 text-xs text-ink-500">
                      No price available for: {unknown.map((item) => item.label || "Unattributed").join(", ")}.
                    </p>
                  ) : null}
                </div>
              );
            }}
          </WidgetBody>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Budget health"
          description="Read-only view of current budget windows. Policies are edited under Risk."
          actions={
            <Link href="/finops/budgets" className="text-sm font-semibold text-accent-700">
              Budgets & forecasting
            </Link>
          }
        >
          <WidgetBody state={budgets} label="budget health" onRetry={retryBudgets}>
            {(data) =>
              data.items.length === 0 ? (
                <p className="text-sm text-ink-500">No enabled budgets are configured.</p>
              ) : (
                <ul className="divide-y divide-base-200">
                  {data.items.slice(0, 6).map((item) => (
                    <li key={`${item.budget_id}:${item.window?.scope_id ?? ""}`} className="py-2 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <Link href={`/risk/budgets/${item.budget_id}`} className="font-medium text-accent-700">
                          {item.name}
                        </Link>
                        <span className="text-xs font-semibold text-ink-700">
                          {BUDGET_STATUS_LABEL[item.status] ?? item.status}
                        </span>
                      </div>
                      <p className="text-xs text-ink-500">
                        {item.window
                          ? `${formatUnitValue(item.window.usage, item.unit)} of ${formatUnitValue(item.limit_value, item.unit)}`
                          : "No usage window open"}{" "}
                        · {ENFORCEMENT_LABEL[enforcementKind(item)]}
                      </p>
                    </li>
                  ))}
                </ul>
              )
            }
          </WidgetBody>
        </Panel>

        <Panel
          title="Needs attention"
          description="Known budget problems and metering gaps, each linking to its source."
        >
          {dash.status === "loading" || budgets.status === "loading" ? (
            <p className="text-sm text-ink-500">Checking…</p>
          ) : attention.length === 0 ? (
            <p className="text-sm text-ink-500" data-testid="attention-empty">
              Nothing needs attention for these filters.
              {dash.status !== "ready" || budgets.status !== "ready"
                ? " Some sources failed to load, so this list may be incomplete."
                : ""}
            </p>
          ) : (
            <ul className="space-y-3" data-testid="attention-list">
              {attention.map((item) => (
                <li key={item.key} className="text-sm">
                  <p className="font-medium text-ink-900">
                    <span className="mr-2 text-[10px] font-semibold uppercase text-ink-500">
                      {item.severity === "high" ? "High" : item.severity === "medium" ? "Medium" : "Info"}
                    </span>
                    {item.title}
                  </p>
                  <p className="text-xs text-ink-500">{item.detail}</p>
                  <Link href={item.href} className="text-xs font-semibold text-accent-700">
                    {item.linkLabel}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <WidgetBody state={dash} label="risk and prevented spend" onRetry={retryDash}>
        {(data) => (
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Risk-associated spend">
              <p className="font-mono text-lg font-semibold text-ink-900" data-testid="risk-spend">
                {data.risk_associated_spend === null
                  ? "Unavailable"
                  : formatAmount(data.risk_associated_spend, data.totals.currency)}
              </p>
              <Definition text={data.definition.risk_associated_spend} />
              <p className="mt-2 text-xs font-semibold text-ink-700">
                Correlated cost, not a measure of loss.
              </p>
            </Panel>
            <Panel title="Prevented estimated spend">
              <p className="font-mono text-lg font-semibold text-ink-900" data-testid="prevented-spend">
                {data.prevented_estimated_spend === null
                  ? "Unavailable"
                  : `≈ ${formatAmount(data.prevented_estimated_spend, data.prevented_estimated_currency)}`}
              </p>
              <Definition text={data.definition.prevented_estimated_spend} />
              <p className="mt-2 text-xs font-semibold text-ink-700">
                A counterfactual estimate. It is not realized savings and no invoice was reduced.
              </p>
            </Panel>
          </div>
        )}
      </WidgetBody>
    </div>
  );
}
