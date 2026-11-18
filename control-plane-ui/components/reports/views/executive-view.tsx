"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Kpi, stateOf } from "@/components/reports/kpi";
import { BarList } from "@/components/reports/bar-list";
import { QueryPanel } from "@/components/reports/states";
import { TrendFigure } from "@/components/reports/trend-figure";
import {
  findingsHref,
  systemLabel,
  useViewQuery,
  type ViewContext,
} from "@/components/reports/view-context";
import { reportsClient } from "@/lib/reports/client";
import { formatCount, formatMoney, formatPercent, formatRatio } from "@/lib/reports/format";
import type { AttentionItem } from "@/lib/reports/types";
import { CHART } from "@/components/reporting/chart-colors";

const SEVERITY_VARIANT = {
  CRITICAL: "danger",
  HIGH: "warn",
  MEDIUM: "accent",
  LOW: "neutral",
  INFO: "neutral",
} as const;

function severityVariant(severity: string): "danger" | "warn" | "accent" | "neutral" {
  return SEVERITY_VARIANT[severity.toUpperCase() as keyof typeof SEVERITY_VARIANT] ?? "neutral";
}

function formatDue(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toISOString().slice(0, 10);
}

function AttentionTable({ items, ctx }: { items: AttentionItem[]; ctx: ViewContext }) {
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <caption className="sr-only">
          Items requiring attention, ranked by severity then due date
        </caption>
        <thead>
          <tr className="border-b border-base-200 text-xs uppercase tracking-wide text-ink-500">
            <th scope="col" className="py-2 pr-3 font-semibold">
              Severity
            </th>
            <th scope="col" className="py-2 pr-3 font-semibold">
              Item
            </th>
            <th scope="col" className="py-2 pr-3 font-semibold">
              AI system
            </th>
            <th scope="col" className="py-2 pr-3 font-semibold">
              Recommended action
            </th>
            <th scope="col" className="py-2 pr-3 font-semibold">
              Due
            </th>
            <th scope="col" className="py-2 font-semibold">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={`${item.source_type}-${item.source_id}`}
              className="border-b border-base-100 align-top"
            >
              <td className="py-2 pr-3">
                <Badge variant={severityVariant(item.severity)}>{item.severity || "UNRATED"}</Badge>
              </td>
              <td className="py-2 pr-3">
                <p className="font-medium text-ink-900">{item.title}</p>
                <p className="text-xs text-ink-500">{item.reason}</p>
              </td>
              <td className="py-2 pr-3 text-ink-700">
                {item.ai_system_id ? (
                  <Link
                    href={`/governance/registry/${item.ai_system_id}`}
                    className="text-accent-700 hover:text-accent-500"
                  >
                    {systemLabel(ctx, item.ai_system_id)}
                  </Link>
                ) : (
                  "—"
                )}
              </td>
              <td className="py-2 pr-3 text-ink-700">{item.recommended_action ?? "—"}</td>
              <td className="py-2 pr-3 tabular-nums text-ink-700">{formatDue(item.due_at)}</td>
              <td className="py-2 text-right">
                {item.drill_down ? (
                  <Link
                    href={item.drill_down}
                    className="font-semibold text-accent-700 hover:text-accent-500"
                    aria-label={`Open ${item.title}`}
                  >
                    Open →
                  </Link>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ExecutiveView({ ctx }: { ctx: ViewContext }) {
  const summary = useViewQuery(ctx, "executive", "/v1/reports/summary", reportsClient.summary);
  const risk = useViewQuery(ctx, "executive", "/v1/reports/risk", reportsClient.risk);
  // FinOps only understands period + AI system; send exactly those.
  const finops = useViewQuery(ctx, "executive", "/v1/finops/dashboard", reportsClient.finops, {
    only: ["period_start", "period_end", "ai_system_id"],
  });

  const s = summary.data;
  const sState = stateOf(summary);
  const fState = stateOf(finops);
  const totals = finops.data?.totals;
  const critical = s?.open_critical_findings.value ?? 0;
  const high = s?.open_high_findings?.value ?? 0;
  const registered = s?.ai_systems_registered?.value;
  const coverage = s?.assurance_coverage_pct;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <Kpi
          label="Governed AI systems"
          loading={sState.loading}
          error={sState.error}
          value={s ? formatRatio(s.ai_systems_governed.value, registered ?? null) : null}
          detail="systems with enforced activity in the period"
          definition="Registered AI systems with at least one governed (policy-enforced) action in the period. Observed activity only: a registered system that has not called the gateway is not counted, and this is not a coverage guarantee."
          changePct={s?.ai_systems_governed.change_pct ?? null}
          showChange={ctx.compare}
          href="/governance/registry"
          hrefLabel="Open AI systems"
        />
        <Kpi
          label="Open critical + high findings"
          loading={sState.loading}
          error={sState.error}
          value={s ? formatCount(critical + high) : null}
          detail={s ? `${formatCount(critical)} critical · ${formatCount(high)} high` : undefined}
          tone={critical > 0 ? "danger" : "default"}
          definition="Findings in an open status right now across design-time, runtime and evidence sources. Current state: the period does not change it, so there is no previous-period comparison."
          href={findingsHref(ctx, { status: "OPEN" })}
          hrefLabel="Open findings"
        />
        <Kpi
          label="Policy enforcement activity"
          loading={sState.loading}
          error={sState.error}
          value={s ? formatCount(s.governed_actions.value) : null}
          detail={
            s ? `Intervention rate ${formatPercent(s.policy_intervention_rate.value)}` : undefined
          }
          definition="Governed actions = enforcement receipts in the period. Intervention rate = receipts whose matched rule was MONITOR, REQUIRE_APPROVAL, DENY or SUSPEND ÷ all receipts. Volume and rate show activity, not effectiveness."
          changePct={s?.governed_actions.change_pct ?? null}
          showChange={ctx.compare}
          href="/runtime/enforcement"
          hrefLabel="Open enforcement"
        />
        <Kpi
          label="Assurance coverage"
          loading={sState.loading}
          error={sState.error}
          value={s ? (coverage ? formatPercent(coverage.value) : "Not assessed") : null}
          detail={
            s
              ? coverage
                ? `of ${formatCount(registered)} registered systems have valid assurance`
                : "no registered AI systems to measure against"
              : undefined
          }
          definition="Systems whose current assurance record is VALID and unexpired ÷ registered AI systems. Control assurance evidence, not a regulatory certification. Current state."
          href="/portfolio"
          hrefLabel="Open portfolio"
        />
        <Kpi
          label="Spend (known)"
          loading={fState.loading}
          error={fState.error}
          value={
            totals
              ? formatMoney({
                  amount: totals.total_cost ?? null,
                  currency: totals.currency,
                  costState: totals.cost_state,
                })
              : null
          }
          detail={
            finops.data
              ? `Cost coverage ${formatPercent(finops.data.coverage?.cost_coverage_pct ?? null)} · ${formatCount(
                  totals?.unavailable_cost_count ?? null,
                )} calls without cost`
              : undefined
          }
          definition="FinOps span-based total for the period: billed, metered and estimated cost. Estimated amounts are marked ≈; calls with no cost data are excluded, never counted as zero."
          changePct={finops.data?.comparison?.total_cost_change_pct ?? null}
          showChange={ctx.compare}
          href="/finops"
          hrefLabel="Open FinOps"
        />
        <Kpi
          label="Pending attention"
          loading={sState.loading}
          error={sState.error}
          value={s ? formatCount(s.attention.length) : null}
          detail={s && s.attention.length >= 10 ? "showing the top 10" : undefined}
          definition="Items from the portfolio attention queue (critical findings, lapsed assurance, exceeded budgets, expiring waivers and exceptions), ranked by severity then due date."
          href="#attention"
          hrefLabel="Jump to queue"
        />
      </div>

      <QueryPanel
        title="Requires attention"
        description="Ranked from the portfolio attention queue. Owner and due date are shown only where the source records them."
        query={summary}
        isEmpty={(d) => d.attention.length === 0}
        emptyMessage="Nothing in the attention queue for this scope."
        testId="attention-panel"
      >
        {(d) => (
          <div id="attention">
            <AttentionTable items={d.attention} ctx={ctx} />
          </div>
        )}
      </QueryPanel>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel
          title="Top AI systems requiring review"
          description="Open findings per system (current state)."
          query={risk}
          isEmpty={(d) => d.risk_by_ai_system.length === 0}
          emptyMessage="No open findings on any AI system."
        >
          {(d) => (
            <BarList
              label="AI systems by open findings"
              unit="findings"
              limit={8}
              items={d.risk_by_ai_system.map((item) => ({
                key: item.id,
                label: item.name,
                value: item.value,
                href: item.link,
                tone:
                  item.severity === "CRITICAL"
                    ? "danger"
                    : item.severity === "HIGH"
                      ? "warn"
                      : "accent",
                note: item.severity ? `Highest severity: ${item.severity}` : undefined,
              }))}
            />
          )}
        </QueryPanel>
        <QueryPanel
          title="New findings per day"
          description="Findings created in the period."
          query={risk}
        >
          {(d) => (
            <TrendFigure points={d.finding_trends} noun="New findings" color={CHART.danger} />
          )}
        </QueryPanel>
      </div>
    </div>
  );
}
