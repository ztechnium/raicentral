"use client";

import Link from "next/link";
import { Kpi, stateOf } from "@/components/reports/kpi";
import { BarList } from "@/components/reports/bar-list";
import { NotConfigured, QueryPanel } from "@/components/reports/states";
import { useViewQuery, type ViewContext } from "@/components/reports/view-context";
import { reportsClient } from "@/lib/reports/client";
import { formatCount, formatPercent, formatRatio } from "@/lib/reports/format";

const DISCLAIMER =
  "Control assurance is internal evidence that specific controls were evaluated and passed. It is not a regulatory certification, audit opinion or legal attestation.";

export function AssuranceView({ ctx }: { ctx: ViewContext }) {
  const assurance = useViewQuery(
    ctx,
    "assurance",
    "/v1/reports/assurance",
    reportsClient.assurance,
  );
  const st = stateOf(assurance);
  const d = assurance.data;

  return (
    <div className="space-y-4">
      <p
        role="note"
        className="rounded-md border border-base-200 bg-base-100 p-3 text-sm text-ink-600"
      >
        <strong className="text-ink-900">Control assurance ≠ regulatory certification.</strong>{" "}
        {DISCLAIMER}
      </p>

      <QueryPanel title="Assurance readiness" query={assurance}>
        {(data) => {
          if (data.total_systems === 0) {
            return (
              <NotConfigured
                title="No AI systems are registered"
                action={
                  <Link
                    href="/governance/registry"
                    className="text-sm font-semibold text-accent-700"
                  >
                    Register an AI system →
                  </Link>
                }
              >
                Assurance coverage is measured against registered AI systems, so there is nothing to
                report yet.
              </NotConfigured>
            );
          }
          if (!data.has_assurance_data) {
            return (
              <NotConfigured
                title="Assurance is not configured for these systems"
                action={
                  <div className="flex flex-wrap gap-4 text-sm font-semibold text-accent-700">
                    <Link href="/governance/registry">Open AI systems →</Link>
                    <Link href="/portfolio">Open portfolio →</Link>
                  </div>
                }
              >
                {formatCount(data.total_systems)} registered system
                {data.total_systems === 1 ? "" : "s"} have no assurance record and no deployment
                gate evaluation, so no assurance figure is shown. Assurance records are created when
                a deployment gate is evaluated through the gateway API; this edition has no console
                page for managing gates, waivers or exceptions (see{" "}
                <code className="text-xs">docs/releases/SYSTEM_STATUS.md</code>).
              </NotConfigured>
            );
          }
          return (
            <p className="text-sm text-ink-600">
              Assurance data exists for{" "}
              {formatRatio(data.total_systems - data.unassessed_systems, data.total_systems)}{" "}
              registered systems. Status, waivers and exceptions below are current state; deployment
              gate counts cover the selected period.
            </p>
          );
        }}
      </QueryPanel>

      {d?.has_assurance_data && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi
              label="Assurance coverage"
              loading={st.loading}
              error={st.error}
              value={
                d.assurance_coverage_pct === null
                  ? "Not assessed"
                  : formatPercent(d.assurance_coverage_pct)
              }
              detail={`${formatCount(d.valid_assurance)} of ${formatCount(d.total_systems)} systems`}
              definition="Systems whose current assurance record is VALID and not past its valid-until date ÷ registered AI systems. A stored VALID record that has expired counts as expired. Current state."
              href="/portfolio"
              hrefLabel="Open portfolio"
            />
            <Kpi
              label="Need reassessment"
              loading={st.loading}
              error={st.error}
              value={formatCount(d.expired_assurance + d.failed_assurance + d.unassessed_systems)}
              detail={`${formatCount(d.expired_assurance)} expired · ${formatCount(d.failed_assurance)} failed · ${formatCount(d.unassessed_systems)} unassessed`}
              tone={d.failed_assurance + d.expired_assurance > 0 ? "warn" : "default"}
              definition="Systems with expired or failed current assurance, plus registered systems that have no assurance record at all."
              href="/governance/registry"
              hrefLabel="Open AI systems"
            />
            <Kpi
              label="Deployment gates (period)"
              loading={st.loading}
              error={st.error}
              value={
                d.evaluation_pass_rate === null
                  ? "No evaluations"
                  : formatPercent(d.evaluation_pass_rate)
              }
              detail={`${formatCount(d.gate_evaluations)} evaluated · ${formatCount(d.failed_deployment_gates)} failed`}
              definition="PASS ÷ (PASS + FAIL) for gate evaluations made in the period. Shown as 'No evaluations' when none were run; it is never 0%."
            />
            <Kpi
              label="Waivers & exceptions"
              loading={st.loading}
              error={st.error}
              value={formatCount(d.active_waivers + d.active_exceptions)}
              detail={`${formatCount(d.expiring_waivers + d.expiring_exceptions)} expiring in 14 days · ${formatCount(d.open_exceptions)} awaiting decision`}
              definition="Approved, unrevoked, unexpired waivers and exceptions (current state). Expiring = valid-until within the next 14 days. Awaiting decision = requested or under review."
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <QueryPanel
              title="Assurance status by system"
              description="Effective status of each system's current record."
              query={assurance}
            >
              {(data) => (
                <BarList
                  label="Systems by assurance status"
                  unit="systems"
                  sortable={false}
                  items={[
                    { key: "valid", label: "Valid", value: data.valid_assurance, tone: "accent" },
                    {
                      key: "conditional",
                      label: "Conditional",
                      value: data.conditional_assurance,
                      tone: "warn",
                    },
                    {
                      key: "expired",
                      label: "Expired",
                      value: data.expired_assurance,
                      tone: "muted",
                    },
                    {
                      key: "failed",
                      label: "Failed",
                      value: data.failed_assurance,
                      tone: "danger",
                    },
                    {
                      key: "unassessed",
                      label: "No assurance record",
                      value: data.unassessed_systems,
                      tone: "muted",
                    },
                  ]}
                />
              )}
            </QueryPanel>
            <QueryPanel
              title="Review and expiry work"
              description="Time-bound approvals that need attention."
              query={assurance}
            >
              {(data) => (
                <BarList
                  label="Waivers and exceptions"
                  sortable={false}
                  items={[
                    {
                      key: "w-active",
                      label: "Active waivers",
                      value: data.active_waivers,
                      tone: "accent",
                    },
                    {
                      key: "w-exp",
                      label: "Waivers expiring ≤14 days",
                      value: data.expiring_waivers,
                      tone: "warn",
                    },
                    {
                      key: "e-active",
                      label: "Active exceptions",
                      value: data.active_exceptions,
                      tone: "accent",
                    },
                    {
                      key: "e-exp",
                      label: "Exceptions expiring ≤14 days",
                      value: data.expiring_exceptions,
                      tone: "warn",
                    },
                    {
                      key: "e-open",
                      label: "Exceptions awaiting decision",
                      value: data.open_exceptions,
                      tone: "muted",
                    },
                  ]}
                />
              )}
            </QueryPanel>
          </div>
        </>
      )}
    </div>
  );
}
