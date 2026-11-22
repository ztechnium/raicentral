"use client";

import { Suspense, useMemo } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { KpiCard } from "@/components/kpi-card";
import { SectionHeader } from "@/components/section-header";
import { Badge } from "@/components/ui/badge";
import { Table } from "@/components/ui/table";
import { Panel } from "@/components/registry/panel";
import { TrendLineChart } from "@/components/reporting/trend-line-chart";
import { SetupEntry } from "@/components/dashboard/setup-entry";
import { WidgetBody } from "@/components/dashboard/widget-body";
import {
  getFinOpsBreakdown,
  getFinOpsDashboard,
  getFinOpsTimeseries,
  getGatewayTrafficShare,
  getPortfolioDashboard,
  getReportSummary,
  getRiskReport,
} from "@/lib/api";
import {
  PERIODS,
  buildAttentionQueue,
  budgetAttention,
  buildSystemReviewRows,
  parsePeriod,
  periodRange,
  severityRank,
  spendKpi,
  type PeriodKey,
} from "@/lib/dashboard/overview";
import { useWidget } from "@/lib/dashboard/use-widget";
import { formatMoney } from "@/lib/finops-format";
import { useOrganization } from "@/lib/hooks/use-auth";
import { cn } from "@/lib/utils";

function severityVariant(severity: string | null): "danger" | "warn" | "neutral" {
  if (severity === "CRITICAL") return "danger";
  if (severity === "HIGH" || severity === "MEDIUM") return "warn";
  return "neutral";
}

function formatWhen(value?: string | null) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString();
}

function KpiLink({ href, label, children }: { href: string; label: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      aria-label={label}
      className="block rounded-lg border border-base-200 bg-base-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600 hover:border-accent-500"
    >
      {children}
    </Link>
  );
}

function KpiSkeleton() {
  return <div role="status" aria-label="Loading metric" className="h-20 animate-pulse rounded-lg bg-base-100" />;
}

function DashboardContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { activeOrganization } = useOrganization();
  const period = parsePeriod(searchParams.get("period"));
  const orgId = activeOrganization?.id ?? "";

  // One window shared by every period-based widget (re-evaluated only when the period changes).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const range = useMemo(() => periodRange(period), [period]);
  const key = `${orgId}|${period}`;

  const [summary, retrySummary] = useWidget(`summary|${key}`, () => getReportSummary(range));
  const [risk, retryRisk] = useWidget(`risk|${key}`, () => getRiskReport(range));
  const [finops, retryFinops] = useWidget(`finops|${key}`, () => getFinOpsDashboard(range));
  const [series, retrySeries] = useWidget(`series|${key}`, () => getFinOpsTimeseries(range));
  const [spendBySystem, retrySpend] = useWidget(`spend|${key}`, () =>
    getFinOpsBreakdown({ ...range, dimension: "ai_system", limit: 50 }),
  );
  const [traffic] = useWidget(`traffic|${key}`, () => getGatewayTrafficShare(range));
  const [portfolio, retryPortfolio] = useWidget(`portfolio|${key}`, () =>
    orgId ? getPortfolioDashboard(orgId) : Promise.reject(new Error("No organization")),
  );

  const setPeriod = (next: PeriodKey) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("period", next);
    router.replace(`${pathname}?${params.toString()}`);
  };

  const attention = useMemo(
    () =>
      buildAttentionQueue(
        summary.status === "ready" ? summary.data.attention : null,
        risk.status === "ready" ? risk.data.attention : null,
        portfolio.status === "ready" ? portfolio.data.attention.items : null,
        // Budget entries share the same shape; they have no source_type/id so key directly.
      ).concat(finops.status === "ready" ? budgetAttention(finops.data) : []),
    [summary, risk, portfolio, finops],
  );
  const sortedAttention = useMemo(
    () => [...attention].sort((a, b) => severityRank(a.severity) - severityRank(b.severity)),
    [attention],
  );
  const attentionLoading = [summary, risk, portfolio, finops].some((s) => s.status === "loading");
  const attentionFailed = [summary, risk, portfolio, finops].filter(
    (s) => s.status === "error" || s.status === "forbidden",
  ).length;

  const spendMap = useMemo(() => {
    if (spendBySystem.status !== "ready") return null;
    const map = new Map<string, number>();
    // Unpriced systems stay absent (unknown), never recorded as 0.
    for (const item of spendBySystem.data.items) if (item.key && item.cost !== null) map.set(item.key, item.cost);
    return map;
  }, [spendBySystem]);
  const reviewRows = useMemo(
    () =>
      buildSystemReviewRows(risk.status === "ready" ? risk.data.risk_by_ai_system : null, spendMap),
    [risk, spendMap],
  );

  const spend = spendKpi(finops.status === "ready" ? finops.data : null);
  const currency = finops.status === "ready" ? finops.data.totals.currency || "USD" : "USD";
  const freshness = summary.status === "ready" ? summary.data.freshness : null;
  const governedActions = summary.status === "ready" ? summary.data.governed_actions.value : null;

  // Setup checklist: only from real portfolio state, hidden once milestones are met.
  const setup = useMemo(() => {
    if (portfolio.status !== "ready") return null;
    const systems = portfolio.data.summary.metrics.total_ai_systems;
    const actions = portfolio.data.runtime_activity.governed_actions;
    const items = [
      { label: "Register an AI system", done: systems > 0, href: "/governance/registry" },
      { label: "Run your first governed action", done: (actions ?? 0) > 0, href: "/app/onboarding" },
    ];
    return items.every((item) => item.done) ? null : items;
  }, [portfolio]);

  return (
    <div className="space-y-6">
      <SectionHeader
        eyebrow="Overview"
        title="AI Governance Overview"
        subtitle="Understand AI risk, controls, spending, and required decisions across your organization."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={activeOrganization ? "accent" : "neutral"}>
              {activeOrganization?.name ?? "No organization selected"}
            </Badge>
            <div role="group" aria-label="Reporting period" className="flex overflow-hidden rounded-md border border-base-200">
              {(Object.keys(PERIODS) as PeriodKey[]).map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={period === option}
                  onClick={() => setPeriod(option)}
                  className={cn(
                    "px-3 py-1.5 text-xs font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600",
                    period === option ? "bg-accent-600 text-white" : "bg-base-50 text-ink-600 hover:bg-base-100",
                  )}
                >
                  {PERIODS[option].label}
                </button>
              ))}
            </div>
          </div>
        }
      />
      <p className="text-xs text-ink-500">
        Period: {new Date(range.period_start).toLocaleString()} →{" "}
        {new Date(range.period_end).toLocaleString()}
        {freshness
          ? ` · ${freshness.mode === "SNAPSHOT" ? "Snapshot" : "Data"} refreshed ${formatWhen(freshness.last_refreshed_at) ?? "at an unknown time"}`
          : ""}
      </p>

      <SetupEntry />

      {setup ? (
        <section aria-label="Setup checklist" className="rounded-lg border border-accent-200 bg-accent-50 p-4">
          <h2 className="text-sm font-semibold text-ink-900">Finish setting up your organization</h2>
          <ul className="mt-2 space-y-1 text-sm">
            {setup.map((item) => (
              <li key={item.label} className="flex items-center gap-2">
                <span aria-hidden>{item.done ? "✓" : "○"}</span>
                {item.done ? (
                  <span className="text-ink-500 line-through">{item.label}</span>
                ) : (
                  <Link href={item.href} className="font-semibold text-accent-700 hover:text-accent-500">
                    {item.label} →
                  </Link>
                )}
                <span className="sr-only">{item.done ? "(done)" : "(to do)"}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-label="Key metrics" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {portfolio.status === "loading" && summary.status === "loading" ? (
          <KpiSkeleton />
        ) : (
          <KpiLink href="/governance/registry" label="AI Systems">
            {portfolio.status === "ready" ? (
              <KpiCard
                title="AI Systems"
                value={String(portfolio.data.summary.metrics.total_ai_systems)}
                trend={`${portfolio.data.summary.metrics.active_ai_systems} active`}
              />
            ) : summary.status === "ready" ? (
              <KpiCard
                title="AI Systems"
                value={String(summary.data.ai_systems_governed.value)}
                trend="Governed systems (registry total unavailable)"
              />
            ) : (
              <KpiCard title="AI Systems" value="Unavailable" trend="Could not be loaded" />
            )}
          </KpiLink>
        )}
        {risk.status === "loading" ? (
          <KpiSkeleton />
        ) : (
          <KpiLink href="/risk/findings?status=OPEN" label="Critical and high risks">
            {risk.status === "ready" ? (
              <KpiCard
                title="Critical & High Risks"
                value={String(risk.data.critical_findings + risk.data.high_findings)}
                tone={risk.data.critical_findings > 0 ? "danger" : risk.data.high_findings > 0 ? "warn" : "neutral"}
                trend={`${risk.data.critical_findings} critical · ${risk.data.high_findings} high · open findings`}
              />
            ) : (
              <KpiCard title="Critical & High Risks" value="Unavailable" trend="Could not be loaded" />
            )}
          </KpiLink>
        )}
        {finops.status === "loading" ? (
          <KpiSkeleton />
        ) : (
          <KpiLink href="/finops" label="AI spend">
            <KpiCard
              title={`AI Spend · ${PERIODS[period].label}`}
              value={spend.value}
              tone={
                finops.status === "ready" && finops.data.budget_pressure.exceeded > 0 ? "danger" : "neutral"
              }
              trend={
                finops.status === "ready"
                  ? `${spend.detail}${
                      finops.data.comparison.total_cost_change_pct != null
                        ? ` · ${finops.data.comparison.total_cost_change_pct >= 0 ? "▲" : "▼"} ${Math.abs(finops.data.comparison.total_cost_change_pct)}% vs previous`
                        : ""
                    }`
                  : spend.detail
              }
            />
          </KpiLink>
        )}
        {traffic.status === "loading" || summary.status === "loading" ? (
          <KpiSkeleton />
        ) : (
          <KpiLink href="/reports" label="Gateway traffic share">
            {traffic.status === "ready" && traffic.data.share_pct !== null ? (
              <KpiCard
                title="Gateway Traffic Share"
                value={`${traffic.data.share_pct}%`}
                tone={traffic.data.direct > 0 ? "warn" : "neutral"}
                trend={`${traffic.data.via_gateway.toLocaleString()} of ${traffic.data.observed_ai_connections.toLocaleString()} observed AI connections via gateway${
                  traffic.data.unclassified_ai_destinations > 0
                    ? ` · ${traffic.data.unclassified_ai_destinations} unclassified excluded`
                    : ""
                }`}
              />
            ) : (
              <KpiCard
                title="Gateway Traffic Share"
                value="Unavailable"
                trend={
                  traffic.status === "ready"
                    ? "No network telemetry observed in this period. Connect a collector to measure coverage."
                    : traffic.status === "forbidden"
                      ? "Your role cannot view network telemetry."
                      : governedActions !== null
                        ? `Telemetry failed to load · Governed actions: ${governedActions.toLocaleString()} (activity, not coverage)`
                        : "Telemetry failed to load"
                }
              />
            )}
          </KpiLink>
        )}
      </section>

      <Panel
        title="Requires your attention"
        description="Prioritized by severity, then due date. Pending approvals assigned to you are not included yet."
        actions={
          <Link href="/portfolio" className="text-sm font-semibold text-accent-700 hover:text-accent-500">
            View all
          </Link>
        }
      >
        {attentionLoading && sortedAttention.length === 0 ? (
          <div role="status" aria-label="Loading attention queue" className="h-24 animate-pulse rounded-md bg-base-100" />
        ) : (
          <div className="space-y-3">
            {attentionFailed > 0 ? (
              <p role="alert" className="text-xs text-warn-700">
                {attentionFailed} source{attentionFailed === 1 ? "" : "s"} could not be loaded or are not
                permitted for your role, so this list may be incomplete.{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => {
                    retrySummary();
                    retryRisk();
                    retryFinops();
                    retryPortfolio();
                  }}
                >
                  Retry
                </button>
              </p>
            ) : null}
            {sortedAttention.length === 0 ? (
              <p className="text-sm text-ink-500">
                {attentionFailed > 0
                  ? "No attention items from the sources that loaded."
                  : "Nothing needs your attention right now."}
              </p>
            ) : (
              <ul className="divide-y divide-base-200">
                {sortedAttention.slice(0, 8).map((item) => (
                  <li key={item.key} className="flex flex-wrap items-center gap-3 py-2.5">
                    <Badge variant={severityVariant(item.severity)}>{item.severity || "INFO"}</Badge>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-ink-900">{item.title}</p>
                      <p className="truncate text-xs text-ink-500">
                        {item.reason}
                        {item.dueAt && formatWhen(item.dueAt) ? ` · Due ${formatWhen(item.dueAt)}` : ""}
                        {!item.dueAt && formatWhen(item.updatedAt) ? ` · Updated ${formatWhen(item.updatedAt)}` : ""}
                      </p>
                    </div>
                    <Link href={item.href} className="text-sm font-semibold text-accent-700 hover:text-accent-500">
                      {item.action} →
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="AI spend over time"
          description={`${PERIODS[period].label} · ${currency}. Cost per bucket as reported by FinOps; estimated and metered amounts are not split in this series.`}
          actions={<Link href="/finops" className="text-sm font-semibold text-accent-700">View details</Link>}
        >
          <WidgetBody state={series} label="spend trend" onRetry={retrySeries}>
            {(data) =>
              data.points.length === 0 ? (
                <p className="text-sm text-ink-500">No priced usage in this period.</p>
              ) : (
                <TrendLineChart
                  title={`Cost (${currency})`}
                  data={data.points.flatMap((point) =>
                    point.timestamp ? [{ timestamp: point.timestamp, value: point.cost }] : [],
                  )}
                />
              )
            }
          </WidgetBody>
        </Panel>
        <Panel
          title="New risk findings over time"
          description={`${PERIODS[period].label} · findings raised per bucket.`}
          actions={<Link href="/risk/findings" className="text-sm font-semibold text-accent-700">View details</Link>}
        >
          <WidgetBody state={risk} label="risk trend" onRetry={retryRisk}>
            {(data) =>
              data.finding_trends.length === 0 ? (
                <p className="text-sm text-ink-500">No findings were raised in this period.</p>
              ) : (
                <TrendLineChart title="New findings" data={data.finding_trends} />
              )
            }
          </WidgetBody>
        </Panel>
      </div>

      <Panel
        title="AI systems requiring review"
        description="Systems with open findings, ordered by highest severity then open findings. Spend is for the selected period."
        actions={
          <Link href="/governance/registry" className="text-sm font-semibold text-accent-700">
            View all systems
          </Link>
        }
      >
        <WidgetBody state={risk} label="AI systems" onRetry={retryRisk}>
          {() =>
            reviewRows.length === 0 ? (
              <p className="text-sm text-ink-500">No AI systems have open findings.</p>
            ) : (
              <Table
                columns={["AI system", "Highest severity", "Open findings", "Spend", ""]}
                rows={reviewRows.map((row) => [
                  row.name,
                  <Badge key={`${row.id}-sev`} variant={severityVariant(row.severity)}>
                    {row.severity ?? "Unknown"}
                  </Badge>,
                  String(row.openFindings),
                  row.spend === null
                    ? spendBySystem.status === "error"
                      ? "Unavailable"
                      : "—"
                    : (formatMoney(row.spend, currency) ?? "Unavailable"),
                  <Link key={`${row.id}-go`} href={row.href} className="font-semibold text-accent-700">
                    Review system
                  </Link>,
                ])}
              />
            )
          }
        </WidgetBody>
        {spendBySystem.status === "error" ? (
          <button type="button" onClick={retrySpend} className="mt-2 text-xs text-accent-700 underline">
            Retry loading spend
          </button>
        ) : null}
      </Panel>

      <p className="text-xs text-ink-400">
        Gateway health is intentionally not shown: no trusted gateway telemetry API is available yet, and
        gateway availability would not prove policy enforcement.{" "}
        <Link href="/runtime/trajectories" className="font-semibold text-accent-700">
          View runtime activity
        </Link>
      </p>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<p className="text-sm text-ink-500">Loading overview…</p>}>
      <DashboardContent />
    </Suspense>
  );
}
