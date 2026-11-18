"use client";

import Link from "next/link";
import { WidgetBody } from "@/components/dashboard/widget-body";
import { Panel } from "@/components/registry/panel";
import { SectionHeader } from "@/components/section-header";
import { Badge } from "@/components/ui/badge";
import { getFinOpsBudgets } from "@/lib/api";
import { useWidget } from "@/lib/dashboard/use-widget";
import { formatCount } from "@/lib/finops-format";
import {
  BUDGET_STATUS_LABEL,
  EFFECT_LABEL,
  ENFORCEMENT_LABEL,
  enforcementKind,
  formatBurnRate,
  formatUnitValue,
  formatWindow,
} from "@/lib/finops-insights";
import { useFinOpsFilters } from "@/lib/use-finops-filters";
import type { FinOpsBudgetItem } from "@/lib/types";

const METRIC_TITLE: Record<string, string> = {
  ACTION_COUNT: "Action count budgets",
  TOOL_COUNT: "Tool call budgets",
  AMOUNT: "Amount budgets",
  TOKEN_USAGE: "Token usage budgets",
  COST: "Cost budgets",
};

const STATUS_RANK: Record<string, number> = {
  EXCEEDED: 0,
  WARNING: 1,
  UNAVAILABLE: 2,
  OK: 3,
  NO_ACTIVE_WINDOW: 4,
};

function formatWhen(iso: string | null) {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

function statusVariant(status: string): "neutral" | "warn" | "danger" | "accent" {
  if (status === "EXCEEDED") return "danger";
  if (status === "WARNING" || status === "UNAVAILABLE") return "warn";
  if (status === "OK") return "accent";
  return "neutral";
}

function Utilization({ budget }: { budget: FinOpsBudgetItem }) {
  const window = budget.window;
  if (!window || window.utilization_pct === null) return null;
  const pct = window.utilization_pct;
  const filled = Math.min(100, Math.max(0, pct));
  return (
    <div className="mt-2">
      <div
        role="progressbar"
        aria-label={`${budget.name} utilization`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(filled)}
        aria-valuetext={`${pct}% of limit used`}
        className="relative h-2 w-full overflow-hidden rounded bg-base-200"
      >
        <div
          className={`h-full ${budget.status === "EXCEEDED" ? "bg-danger-600" : budget.status === "WARNING" ? "bg-warn-600" : "bg-accent-600"}`}
          style={{ width: `${filled}%` }}
        />
        {/* Warning threshold marker */}
        <div
          aria-hidden
          className="absolute top-0 h-full w-px bg-ink-700"
          style={{ left: `${Math.min(100, budget.warning_threshold * 100)}%` }}
        />
      </div>
      <p className="mt-1 text-[11px] text-ink-500">
        {pct}% of limit used · warning at {Math.round(budget.warning_threshold * 100)}%
      </p>
    </div>
  );
}

function Projection({ budget }: { budget: FinOpsBudgetItem }) {
  const projection = budget.projection;
  if (!budget.window || !projection) {
    return <p className="text-xs text-ink-500">No active window, so there is nothing to project.</p>;
  }
  if (projection.status !== "projected") {
    return (
      <div className="text-xs text-ink-600">
        <p className="font-semibold text-ink-700">Insufficient data for a projection</p>
        <p>{projection.reason}</p>
      </div>
    );
  }
  const exceeds = projection.will_exceed_before_reset;
  return (
    <div className="space-y-1 text-xs text-ink-600" data-testid="projection">
      <p className="font-semibold text-ink-800">
        Projected to use {formatUnitValue(Number(projection.projected_window_usage?.toFixed(4)), budget.unit)} (
        {projection.projected_utilization_pct}% of limit) by {formatWhen(budget.window.end)}
      </p>
      <p>
        {budget.window.usage >= budget.limit_value
          ? "The limit has already been reached in this window."
          : exceeds
          ? projection.projected_exhaustion_at
            ? `At this pace the limit is reached around ${formatWhen(projection.projected_exhaustion_at)}, before the window resets.`
            : "At this pace the limit would be passed before the window resets."
          : "At this pace the limit is not reached before the window resets."}
      </p>
      <p>Burn rate {formatBurnRate(projection.burn_rate_per_hour, budget.unit)}</p>
      <p className="text-ink-500">
        Method: linear run-rate over {Math.round((projection.observation_seconds ?? 0) / 60)} minutes
        observed in this window · confidence {projection.confidence}. {projection.reason}
      </p>
    </div>
  );
}

function BudgetCard({ budget }: { budget: FinOpsBudgetItem }) {
  const kind = enforcementKind(budget);
  const window = budget.window;
  return (
    <li
      className="rounded-lg border border-base-200 bg-base-50 p-4"
      data-testid="budget-card"
      aria-label={`${budget.name}: ${BUDGET_STATUS_LABEL[budget.status] ?? budget.status}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <Link href={`/risk/budgets/${budget.budget_id}`} className="font-semibold text-accent-700">
            {budget.name}
          </Link>
          <p className="text-xs text-ink-500">
            {budget.ai_system_name ?? "Organization-wide"}
            {budget.tool_name ? ` · tool ${budget.tool_name}` : ""} · {budget.scope_type.toLowerCase().replace(/_/g, " ")} scope
          </p>
        </div>
        <Badge variant={statusVariant(budget.status)}>{BUDGET_STATUS_LABEL[budget.status] ?? budget.status}</Badge>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-ink-500">Limit</dt>
          <dd className="font-mono text-ink-900">{formatUnitValue(budget.limit_value, budget.unit)}</dd>
        </div>
        <div>
          <dt className="text-ink-500">Used this window</dt>
          <dd className="font-mono text-ink-900">
            {window ? formatUnitValue(window.usage, budget.unit) : "No active window"}
          </dd>
        </div>
        <div>
          <dt className="text-ink-500">Headroom</dt>
          <dd className="font-mono text-ink-900">
            {window ? formatUnitValue(window.headroom, budget.unit) : "—"}
          </dd>
        </div>
        <div>
          <dt className="text-ink-500">Window</dt>
          <dd className="text-ink-900">
            {formatWindow(budget.window_seconds)}
            {window ? <span className="block text-ink-500">resets {formatWhen(window.end)}</span> : null}
          </dd>
        </div>
      </dl>
      {window && window.reserved > 0 ? (
        <p className="mt-1 text-[11px] text-ink-500">
          Headroom already accounts for {formatUnitValue(window.reserved, budget.unit)} reserved by in-flight requests.
        </p>
      ) : null}

      <Utilization budget={budget} />

      <div className="mt-3 grid gap-3 border-t border-base-200 pt-3 sm:grid-cols-2">
        <div className="text-xs">
          <p className="font-semibold text-ink-800">
            {ENFORCEMENT_LABEL[kind]} · on exceed: {EFFECT_LABEL[budget.effect_on_exceed] ?? budget.effect_on_exceed}
          </p>
          <p className="text-ink-600">{budget.enforcement_summary}</p>
          <p className="mt-1 text-ink-500">
            Mode {budget.enforcement_mode === "MANDATORY" ? "mandatory (fails closed)" : "best effort (may skip when unmeasurable)"} ·{" "}
            {formatCount(budget.evidence.checks)} checks in {budget.evidence.lookback_days} days
            {budget.evidence.last_check_at ? `, last ${formatWhen(budget.evidence.last_check_at)}` : ""} ·{" "}
            {formatCount(budget.evidence.blocking_interventions)} blocking interventions
          </p>
        </div>
        <Projection budget={budget} />
      </div>
    </li>
  );
}

export default function FinOpsBudgetsPage() {
  const { filters, dataKey, refresh } = useFinOpsFilters();
  const [state, retry] = useWidget(`budgets:${filters.ai_system_id ?? ""}:${dataKey}`, () =>
    getFinOpsBudgets({ ai_system_id: filters.ai_system_id }),
  );

  return (
    <div className="space-y-6" data-testid="finops-budgets">
      <SectionHeader
        eyebrow="FinOps"
        title="Budgets & forecasting"
        subtitle="Where each budget stands in its current window, whether it is enforced, and, when the data supports it, where it is heading."
        actions={
          <>
            <button
              type="button"
              onClick={() => {
                refresh();
                retry();
              }}
              className="rounded-md border border-base-200 px-3 py-1.5 text-sm hover:bg-base-100"
            >
              Refresh
            </button>
            <Link
              href="/risk/budgets"
              className="rounded-md border border-accent-600 px-3 py-1.5 text-sm font-semibold text-accent-700 hover:bg-accent-50"
            >
              Manage budget policies
            </Link>
          </>
        }
      />

      <Panel title="How to read this">
        <ul className="list-disc space-y-1 pl-5 text-sm text-ink-600">
          <li>
            This page is read-only. Limits, windows and effects are edited under{" "}
            <Link href="/risk/budgets" className="font-semibold text-accent-700">
              Risk › Budgets
            </Link>
            .
          </li>
          <li>
            Budgets are grouped by what they measure. Actions, tool calls, tokens and money are
            different units and are never added together.
          </li>
          <li>
            Usage resets when a window ends. A projection covers only the current window and is
            never carried across a reset.
          </li>
          <li>
            “Configured” is not “enforced”: a blocking effect is called enforced only when checks
            are on record. Monitor-only budgets record overruns but do not stop anything.
          </li>
        </ul>
      </Panel>

      <WidgetBody state={state} label="budgets" onRetry={retry}>
        {(data) => {
          if (data.items.length === 0) {
            return (
              <Panel title="No budgets">
                <p className="text-sm text-ink-500">
                  No enabled budgets{filters.ai_system_id ? " apply to this AI system" : " are configured"}.{" "}
                  <Link href="/risk/budgets" className="font-semibold text-accent-700">
                    Create one under Risk › Budgets
                  </Link>
                  .
                </p>
              </Panel>
            );
          }
          const groups = new Map<string, FinOpsBudgetItem[]>();
          for (const item of data.items) {
            groups.set(item.metric, [...(groups.get(item.metric) ?? []), item]);
          }
          const s = data.summary;
          return (
            <div className="space-y-6">
              <p className="text-sm text-ink-600" data-testid="budget-summary">
                {s.total} enabled budget{s.total === 1 ? "" : "s"}: {s.exceeded} exceeded · {s.warning} warning ·{" "}
                {s.healthy} healthy · {s.unavailable} unmeasurable · {s.no_active_window} with no active window. As of{" "}
                {formatWhen(data.as_of)}.
              </p>
              {Array.from(groups.entries()).map(([metric, items]) => (
                <section key={metric} aria-labelledby={`metric-${metric}`} className="space-y-3">
                  <h2 id={`metric-${metric}`} className="text-sm font-semibold text-ink-900">
                    {METRIC_TITLE[metric] ?? metric}{" "}
                    <span className="font-normal text-ink-500">· measured in {items[0].unit}</span>
                  </h2>
                  <ul className="space-y-3">
                    {[...items]
                      .sort((a, b) => (STATUS_RANK[a.status] ?? 9) - (STATUS_RANK[b.status] ?? 9))
                      .map((item) => (
                        <BudgetCard key={`${item.budget_id}:${item.window?.scope_id ?? ""}`} budget={item} />
                      ))}
                  </ul>
                </section>
              ))}
            </div>
          );
        }}
      </WidgetBody>
    </div>
  );
}
