"use client";

import Link from "next/link";
import { Kpi, stateOf } from "@/components/reports/kpi";
import { BarList } from "@/components/reports/bar-list";
import { NotConfigured, QueryPanel } from "@/components/reports/states";
import { TrendFigure } from "@/components/reports/trend-figure";
import { useViewQuery, type ViewContext } from "@/components/reports/view-context";
import { reportsClient } from "@/lib/reports/client";
import { changePercent, formatCount, formatPercent, humanize } from "@/lib/reports/format";
import { CHART } from "@/components/reporting/chart-colors";

const EFFECT_ORDER = ["allow", "monitor", "require_approval", "deny", "suspend"] as const;

export function ControlsView({ ctx }: { ctx: ViewContext }) {
  const policy = useViewQuery(ctx, "controls", "/v1/reports/policies", reportsClient.policies);
  const prior = useViewQuery(ctx, "controls", "/v1/reports/policies", reportsClient.policies, {
    previous: true,
    enabled: ctx.compare,
  });
  const st = stateOf(policy);
  const d = policy.data;
  const p = prior.data;
  const compareReady = ctx.compare && Boolean(p);

  return (
    <div className="space-y-4">
      <div
        role="note"
        className="rounded-md border border-base-200 bg-base-100 p-3 text-sm text-ink-600"
      >
        <strong className="text-ink-900">Read rates as activity, not quality.</strong> A high deny
        rate can mean strong controls or an over-eager rule; a low intervention rate can mean safe
        usage or controls that miss things. Rates are shown without good/bad colouring because this
        workspace does not yet measure whether each decision was correct.
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Policy evaluations"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.evaluations) : null}
          definition="Enforcement receipts in the period: one per governed action that was evaluated against policy."
          changePct={
            compareReady && d && p ? changePercent(d.evaluations, p.evaluations) : undefined
          }
          showChange={ctx.compare}
          href="/runtime/enforcement"
          hrefLabel="Open enforcement decisions"
        />
        <Kpi
          label="Rules matched (trigger rate)"
          loading={st.loading}
          error={st.error}
          value={d ? formatPercent(d.trigger_rate) : null}
          detail={
            d
              ? `${formatCount(d.triggers)} of ${formatCount(d.evaluations)} evaluations`
              : undefined
          }
          definition="Evaluations whose matched rule had a non-ALLOW effect (MONITOR, REQUIRE_APPROVAL, DENY or SUSPEND) ÷ all evaluations. A match is not a final decision."
          changePct={
            compareReady && d && p ? changePercent(d.trigger_rate, p.trigger_rate) : undefined
          }
          showChange={ctx.compare}
        />
        <Kpi
          label="Denied by final decision"
          loading={st.loading}
          error={st.error}
          value={d ? formatPercent(d.deny_rate) : null}
          detail={
            d ? `${formatCount(d.decision_distribution["DENY"] ?? 0)} DENY decisions` : undefined
          }
          definition="Receipts whose finalized enforcement decision was DENY ÷ all evaluations. A denial is an enforcement outcome, not a runtime failure."
          changePct={compareReady && d && p ? changePercent(d.deny_rate, p.deny_rate) : undefined}
          showChange={ctx.compare}
          href="/runtime/enforcement"
        />
        <Kpi
          label="Intervention rate"
          loading={st.loading}
          error={st.error}
          value={d ? formatPercent(d.intervention_rate) : null}
          definition="Evaluations that monitored, required approval for, denied or suspended an action ÷ all evaluations."
          changePct={
            compareReady && d && p
              ? changePercent(d.intervention_rate, p.intervention_rate)
              : undefined
          }
          showChange={ctx.compare}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <QueryPanel
          title="Matched rule effect"
          description="What the matched rule said to do."
          query={policy}
          isEmpty={(data) => data.evaluations === 0}
          emptyMessage="No policy evaluations in this period."
        >
          {(data) => (
            <BarList
              label="Evaluations by matched rule effect"
              unit="evals"
              sortable={false}
              items={EFFECT_ORDER.map((effect) => ({
                key: effect,
                label: humanize(effect),
                value: data.effects[effect],
                tone: "accent",
                href: "/runtime/enforcement",
              }))}
            />
          )}
        </QueryPanel>
        <QueryPanel
          title="Final enforcement decision"
          description="What the gateway actually decided."
          query={policy}
          isEmpty={(data) => Object.keys(data.decision_distribution).length === 0}
          emptyMessage="No enforcement decisions in this period."
        >
          {(data) => (
            <BarList
              label="Evaluations by final decision"
              unit="evals"
              items={Object.entries(data.decision_distribution).map(([key, value]) => ({
                key,
                label: humanize(key),
                value,
                tone: "accent",
              }))}
            />
          )}
        </QueryPanel>
        <QueryPanel
          title="Execution outcome"
          description="What happened after the decision."
          query={policy}
          isEmpty={(data) => Object.keys(data.execution_outcome_distribution).length === 0}
          emptyMessage="No execution outcomes recorded in this period."
        >
          {(data) => (
            <BarList
              label="Evaluations by execution outcome"
              unit="evals"
              items={Object.entries(data.execution_outcome_distribution).map(([key, value]) => ({
                key,
                label: humanize(key),
                value,
                tone: "muted",
              }))}
            />
          )}
        </QueryPanel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel title="Evaluations per day" query={policy}>
          {(data) => <TrendFigure points={data.evaluation_trends} noun="Evaluations" />}
        </QueryPanel>
        <QueryPanel title="Rule matches per day" query={policy}>
          {(data) => (
            <TrendFigure points={data.trigger_trends} noun="Rule matches" color={CHART.warn} />
          )}
        </QueryPanel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel title="Top policies" query={policy}>
          {(data) =>
            data.top_policies.length > 0 ? (
              <BarList
                label="Policies by matches"
                unit="matches"
                items={data.top_policies.map((item) => ({
                  key: item.id,
                  label: item.name,
                  value: item.value,
                  href: item.link,
                }))}
              />
            ) : (
              <NotConfigured title="Per-policy ranking is not available yet">
                Receipts store matched rule identifiers as a list, and this release does not
                aggregate them per policy. Open{" "}
                <Link href="/governance/policies" className="font-semibold text-accent-700">
                  Policies
                </Link>{" "}
                or the{" "}
                <Link href="/runtime/enforcement" className="font-semibold text-accent-700">
                  enforcement decisions
                </Link>{" "}
                to inspect individual rules.
              </NotConfigured>
            )
          }
        </QueryPanel>
        <QueryPanel title="Effectiveness" query={policy}>
          {() => (
            <NotConfigured title="Effectiveness is unknown">
              Whether a denial prevented harm, or blocked a legitimate request, is not recorded, so
              no false-positive or prevention claim is made. Use the decision records linked above,
              and the evaluation evidence on the Assurance &amp; Compliance view, to judge a
              control.
            </NotConfigured>
          )}
        </QueryPanel>
      </div>
    </div>
  );
}
