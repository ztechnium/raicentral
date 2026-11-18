"use client";

import { Kpi, stateOf } from "@/components/reports/kpi";
import { BarList } from "@/components/reports/bar-list";
import { QueryPanel } from "@/components/reports/states";
import { TrendFigure } from "@/components/reports/trend-figure";
import { useViewQuery, type ViewContext } from "@/components/reports/view-context";
import { reportsClient } from "@/lib/reports/client";
import { changePercent, formatCount, formatRatio, humanize } from "@/lib/reports/format";

const UNITS: { term: string; meaning: string }[] = [
  {
    term: "Governed actions / policy evaluations",
    meaning: "Enforcement receipts: one per action checked against policy.",
  },
  {
    term: "Executions",
    meaning:
      "Trajectories: one recorded run of an agent, which can contain many actions and model calls.",
  },
  { term: "Conversations", meaning: "Conversation projections that group related trajectories." },
  {
    term: "Interventions",
    meaning:
      "Evaluations whose matched rule monitored, required approval for, denied or suspended an action.",
  },
];

export function RuntimeView({ ctx }: { ctx: ViewContext }) {
  const runtime = useViewQuery(ctx, "runtime", "/v1/reports/runtime", reportsClient.runtime);
  const prior = useViewQuery(ctx, "runtime", "/v1/reports/runtime", reportsClient.runtime, {
    previous: true,
    enabled: ctx.compare,
  });
  const st = stateOf(runtime);
  const d = runtime.data;
  const p = prior.data;
  const compareReady = ctx.compare && Boolean(p);

  const completed = d?.execution_status_distribution["COMPLETED"] ?? 0;
  const active = d?.execution_status_distribution["ACTIVE"] ?? 0;
  const total = d?.executions ?? 0;

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Governed actions"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.governed_actions) : null}
          definition="Unit: enforcement receipts in the period."
          changePct={
            compareReady && d && p
              ? changePercent(d.governed_actions, p.governed_actions)
              : undefined
          }
          showChange={ctx.compare}
          href="/runtime/enforcement"
          hrefLabel="Open enforcement decisions"
        />
        <Kpi
          label="Executions"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.executions) : null}
          detail={d ? `${formatRatio(completed, total)} completed` : undefined}
          definition="Unit: trajectories started in the period. Completed ÷ started; runs still active are not failures."
          changePct={compareReady && d && p ? changePercent(d.executions, p.executions) : undefined}
          showChange={ctx.compare}
          href="/runtime/trajectories"
          hrefLabel="Open trajectories"
        />
        <Kpi
          label="Awaiting approval"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.approvals_pending) : null}
          definition="Approval requests pending a decision right now (current state, not limited to the period)."
          href="/governance/approvals"
          hrefLabel="Open approvals"
        />
        <Kpi
          label="Conversations"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.conversations) : null}
          definition="Unit: conversations started in the period."
          changePct={
            compareReady && d && p ? changePercent(d.conversations, p.conversations) : undefined
          }
          showChange={ctx.compare}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel
          title="Execution status"
          description={`Of ${formatCount(total)} executions started in the period.`}
          query={runtime}
          isEmpty={(data) => data.executions === 0}
          emptyMessage="No executions started in this period."
        >
          {(data) => (
            <div className="space-y-3">
              <BarList
                label="Executions by recorded status"
                unit="runs"
                items={Object.entries(data.execution_status_distribution).map(([key, value]) => ({
                  key,
                  label: humanize(key),
                  value,
                  tone: key === "COMPLETED" ? "accent" : key === "ACTIVE" ? "muted" : "warn",
                  href: "/runtime/trajectories",
                }))}
              />
              <p className="text-xs text-ink-500">
                {formatRatio(completed, total)} completed. {formatCount(active)} still active are
                neither successes nor failures yet.
              </p>
            </div>
          )}
        </QueryPanel>
        <QueryPanel
          title="Governance outcomes"
          description="Intended control outcomes. None of these are execution failures."
          query={runtime}
          isEmpty={(data) => data.policy_evaluations === 0}
          emptyMessage="No policy evaluations in this period."
        >
          {(data) => (
            <BarList
              label="Governance outcomes"
              sortable={false}
              items={[
                {
                  key: "ev",
                  label: "Policy evaluations",
                  value: data.policy_evaluations,
                  tone: "muted",
                  href: "/runtime/enforcement",
                },
                {
                  key: "int",
                  label: "Interventions",
                  value: data.interventions,
                  tone: "accent",
                  href: "/runtime/enforcement",
                },
                {
                  key: "apr",
                  label: "Approval required",
                  value: data.approvals_required,
                  tone: "warn",
                  href: "/governance/approvals",
                },
                {
                  key: "den",
                  label: "Denied (final decision)",
                  value: data.denied,
                  tone: "accent",
                  href: "/runtime/enforcement",
                },
                {
                  key: "sus",
                  label: "Suspensions",
                  value: data.runtime_suspensions,
                  tone: "accent",
                  href: "/runtime/enforcement",
                },
              ]}
            />
          )}
        </QueryPanel>
      </div>

      <QueryPanel
        title="Governed activity per day"
        description="Policy evaluations (enforcement receipts) per day."
        query={runtime}
      >
        {(data) => <TrendFigure points={data.activity_trends} noun="Policy evaluations" />}
      </QueryPanel>

      <QueryPanel title="What each count measures" query={runtime}>
        {() => (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {UNITS.map((unit) => (
              <div key={unit.term}>
                <dt className="font-semibold text-ink-900">{unit.term}</dt>
                <dd className="text-ink-600">{unit.meaning}</dd>
              </div>
            ))}
          </dl>
        )}
      </QueryPanel>
    </div>
  );
}
