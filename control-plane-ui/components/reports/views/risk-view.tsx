"use client";

import { useState } from "react";
import { Kpi, stateOf } from "@/components/reports/kpi";
import { BarList, type BarItem } from "@/components/reports/bar-list";
import { NotConfigured, QueryPanel } from "@/components/reports/states";
import { TrendFigure } from "@/components/reports/trend-figure";
import { findingsHref, useViewQuery, type ViewContext } from "@/components/reports/view-context";
import { reportsClient } from "@/lib/reports/client";
import { changePercent, formatCount, humanize } from "@/lib/reports/format";
import type { RankedItem, RiskReport } from "@/lib/reports/types";
import { CHART } from "@/components/reporting/chart-colors";
import { cn } from "@/lib/utils";

type EntityDimension = "ai_system" | "agent" | "tool" | "model";

const DIMENSIONS: { id: EntityDimension; label: string }[] = [
  { id: "ai_system", label: "AI system" },
  { id: "agent", label: "Agent" },
  { id: "tool", label: "Tool" },
  { id: "model", label: "Model" },
];

const SEVERITY_TONE = {
  critical: "danger",
  high: "warn",
  medium: "accent",
  low: "muted",
  info: "muted",
} as const;

function entityItems(items: RankedItem[], hrefFor: (item: RankedItem) => string | null): BarItem[] {
  return items.map((item) => ({
    key: item.id,
    label: item.name,
    value: item.value,
    href: hrefFor(item),
    tone: item.severity === "CRITICAL" ? "danger" : item.severity === "HIGH" ? "warn" : "accent",
    note: item.severity ? `Highest severity: ${item.severity}` : undefined,
  }));
}

function EntityPanel({
  data,
  ctx,
  query,
}: {
  data: RiskReport;
  ctx: ViewContext;
  query: ReturnType<typeof useViewQuery<RiskReport>>;
}) {
  const [dimension, setDimension] = useState<EntityDimension>("ai_system");
  void query;
  const unavailable = data.unavailable_dimensions.includes(dimension);
  const items: Record<EntityDimension, RankedItem[]> = {
    ai_system: data.risk_by_ai_system,
    agent: data.risk_by_agent,
    tool: data.risk_by_tool,
    model: data.risk_by_model,
  };
  const hrefFor = (item: RankedItem) =>
    dimension === "ai_system"
      ? findingsHref(ctx, { systemId: item.id, status: "OPEN" })
      : dimension === "agent"
        ? `/governance/registry/agents/${item.id}`
        : null;

  return (
    <div className="space-y-3">
      <div role="group" aria-label="Group risk by" className="flex flex-wrap gap-1">
        {DIMENSIONS.map((d) => (
          <button
            key={d.id}
            type="button"
            aria-pressed={dimension === d.id}
            onClick={() => setDimension(d.id)}
            className={cn(
              "rounded-md border px-3 py-1 text-xs font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600",
              dimension === d.id
                ? "border-accent-600 bg-accent-100 text-accent-700"
                : "border-base-200 bg-base-50 text-ink-600 hover:bg-base-100",
            )}
          >
            {d.label}
          </button>
        ))}
      </div>
      {unavailable ? (
        <NotConfigured
          title={`Risk by ${DIMENSIONS.find((d) => d.id === dimension)?.label.toLowerCase()} is not available`}
        >
          Findings in the current data model are not attributed to this dimension, so an empty chart
          would wrongly read as &ldquo;no risk&rdquo;. This will appear once findings carry that
          attribution.
        </NotConfigured>
      ) : (
        <BarList
          label={`Open findings by ${dimension.replace("_", " ")}`}
          unit="findings"
          limit={10}
          items={entityItems(items[dimension], hrefFor)}
          emptyLabel={`No open findings attributed to any ${dimension.replace("_", " ")}.`}
        />
      )}
      {dimension === "agent" && (
        <p className="text-xs text-ink-500">
          Agent attribution covers design-time (assessment) findings only.
        </p>
      )}
    </div>
  );
}

export function RiskView({ ctx }: { ctx: ViewContext }) {
  const risk = useViewQuery(ctx, "risk", "/v1/reports/risk", reportsClient.risk);
  const prior = useViewQuery(ctx, "risk", "/v1/reports/risk", reportsClient.risk, {
    previous: true,
    enabled: ctx.compare,
  });
  const st = stateOf(risk);
  const d = risk.data;
  const newChange = d && prior.data ? changePercent(d.new_findings, prior.data.new_findings) : null;
  const compareReady = ctx.compare && Boolean(prior.data);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Open findings"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.open_findings) : null}
          definition={
            d?.semantics?.open_findings ?? "Current state: findings in an open status right now."
          }
          href={findingsHref(ctx, { status: "OPEN" })}
          tone={d && d.critical_findings > 0 ? "danger" : "default"}
          detail={
            d
              ? `${formatCount(d.critical_findings)} critical · ${formatCount(d.high_findings)} high`
              : undefined
          }
        />
        <Kpi
          label="New in period"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.new_findings) : null}
          definition={d?.semantics?.new_findings ?? "Period event: findings created in the period."}
          changePct={compareReady ? newChange : undefined}
          polarity="higher-is-worse"
          showChange={ctx.compare}
          href={findingsHref(ctx)}
        />
        <Kpi
          label="Resolved (period cohort)"
          loading={st.loading}
          error={st.error}
          value={d ? formatCount(d.resolved_findings) : null}
          definition={
            d?.semantics?.resolved_findings ??
            "Findings created in the period that are no longer open."
          }
        />
        <Kpi
          label="Mean time to resolution"
          loading={st.loading}
          error={st.error}
          value={
            d?.mean_time_to_resolution_hours != null
              ? `${d.mean_time_to_resolution_hours.toFixed(1)} h`
              : "Not available"
          }
          definition="Needs a resolution timestamp, which findings do not record yet, so no figure is shown rather than a guessed one."
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel title="Severity" description="Open findings (current state)." query={risk}>
          {(data) => (
            <BarList
              label="Open findings by severity"
              unit="findings"
              sortable={false}
              items={(["critical", "high", "medium", "low", "info"] as const).map((sev) => ({
                key: sev,
                label: humanize(sev),
                value: data.severity_distribution[sev],
                tone: SEVERITY_TONE[sev],
                href:
                  data.severity_distribution[sev] > 0
                    ? findingsHref(ctx, { severity: sev, status: "OPEN" })
                    : null,
              }))}
            />
          )}
        </QueryPanel>
        <QueryPanel
          title="Category"
          description="Open findings with a recorded category (design-time and runtime)."
          query={risk}
          isEmpty={(data) => Object.keys(data.category_distribution).length === 0}
          emptyMessage="No open categorized findings."
        >
          {(data) => (
            <BarList
              label="Open findings by category"
              unit="findings"
              items={Object.entries(data.category_distribution).map(([key, value]) => ({
                key,
                label: humanize(key),
                value,
              }))}
            />
          )}
        </QueryPanel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryPanel title="Findings created per day" description="Period event." query={risk}>
          {(data) => (
            <TrendFigure points={data.finding_trends} noun="New findings" color={CHART.danger} />
          )}
        </QueryPanel>
        <QueryPanel
          title="Status of findings created in the period"
          description="Current status of the period cohort."
          query={risk}
          isEmpty={(data) => Object.keys(data.status_distribution).length === 0}
          emptyMessage="No findings were created in this period."
        >
          {(data) => (
            <BarList
              label="Period cohort by current status"
              unit="findings"
              items={Object.entries(data.status_distribution).map(([key, value]) => ({
                key,
                label: humanize(key),
                value,
                tone: ["RESOLVED", "CLOSED", "DISMISSED"].includes(key) ? "muted" : "accent",
              }))}
            />
          )}
        </QueryPanel>
      </div>

      <QueryPanel
        title="Where risk is concentrated"
        description="Open findings ranked by entity. Select a bar to open the filtered records."
        query={risk}
      >
        {(data) => <EntityPanel data={data} ctx={ctx} query={risk} />}
      </QueryPanel>

      <QueryPanel title="How these counts are built" query={risk}>
        {(data) => (
          <div className="space-y-3 text-sm text-ink-600">
            <p>
              Totals add three independent sources with no deduplication. Runtime and evidence
              findings can describe the same event, so read the split below before treating the
              total as unique issues.
            </p>
            <BarList
              label="Open findings by source"
              unit="findings"
              sortable={false}
              items={Object.entries(data.findings_by_source).map(([key, value]) => ({
                key,
                label: humanize(key),
                value,
                tone: "muted",
              }))}
            />
            <details>
              <summary className="cursor-pointer text-xs font-semibold text-accent-700">
                Definitions of every figure on this page
              </summary>
              <dl className="mt-2 space-y-1 text-xs">
                {Object.entries(data.semantics).map(([key, text]) => (
                  <div key={key}>
                    <dt className="font-semibold text-ink-800">{humanize(key)}</dt>
                    <dd>{text}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </div>
        )}
      </QueryPanel>
    </div>
  );
}
