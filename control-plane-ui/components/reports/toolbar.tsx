"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  BUDGET_METRICS,
  FILTER_SUPPORT,
  FINDING_STATUSES,
  PERIOD_PRESETS,
  POLICY_EFFECTS,
  SEVERITIES,
  describePeriod,
  ignoredFilters,
  type ReportFilterState,
  type ResolvedPeriod,
} from "@/lib/reports/filters";
import { humanize } from "@/lib/reports/format";

const PRESET_LABEL: Record<(typeof PERIOD_PRESETS)[number], string> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  custom: "Custom range",
};

const FIELD =
  "h-9 rounded-md border border-base-200 bg-base-50 px-2 text-sm text-ink-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-600";

function toLocalInput(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-[9rem] flex-col gap-1">
      <label htmlFor={id} className="text-xs font-semibold text-ink-500">
        {label}
      </label>
      {children}
    </div>
  );
}

export function ReportsToolbar({
  filters,
  period,
  systems,
  asOf,
  onChange,
  onRefresh,
  refreshing,
}: {
  filters: ReportFilterState;
  period: ResolvedPeriod;
  systems: { id: string; name: string }[];
  asOf: Date;
  onChange: (patch: Partial<ReportFilterState>) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const support = FILTER_SUPPORT[filters.view];
  const [copied, setCopied] = useState<"idle" | "ok" | "failed">("idle");
  const ignored = ignoredFilters(filters, filters.view);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied("ok");
    } catch {
      setCopied("failed");
    }
    window.setTimeout(() => setCopied("idle"), 2500);
  };

  return (
    <section
      aria-label="Report filters"
      className="space-y-3 rounded-lg border border-base-200 bg-base-50 p-4"
    >
      <div className="flex flex-wrap items-end gap-3">
        {support.period && (
          <Field id="rp-period" label="Period">
            <select
              id="rp-period"
              className={FIELD}
              value={filters.period}
              onChange={(event) => {
                const next = event.target.value as ReportFilterState["period"];
                if (next === "custom") {
                  onChange({
                    period: "custom",
                    from: period.start.toISOString(),
                    to: period.end.toISOString(),
                  });
                } else {
                  onChange({ period: next, from: undefined, to: undefined });
                }
              }}
            >
              {PERIOD_PRESETS.map((preset) => (
                <option key={preset} value={preset}>
                  {PRESET_LABEL[preset]}
                </option>
              ))}
            </select>
          </Field>
        )}

        {support.period && filters.period === "custom" && (
          <>
            <Field id="rp-from" label="From (local time)">
              <input
                id="rp-from"
                type="datetime-local"
                className={FIELD}
                value={toLocalInput(filters.from)}
                max={toLocalInput(filters.to)}
                onChange={(event) =>
                  event.target.value &&
                  onChange({ from: new Date(event.target.value).toISOString() })
                }
              />
            </Field>
            <Field id="rp-to" label="To (local time)">
              <input
                id="rp-to"
                type="datetime-local"
                className={FIELD}
                value={toLocalInput(filters.to)}
                min={toLocalInput(filters.from)}
                onChange={(event) =>
                  event.target.value && onChange({ to: new Date(event.target.value).toISOString() })
                }
              />
            </Field>
          </>
        )}

        {support.aiSystem && (
          <Field id="rp-system" label="AI system">
            <select
              id="rp-system"
              className={FIELD}
              value={filters.aiSystemId ?? ""}
              onChange={(event) => onChange({ aiSystemId: event.target.value || undefined })}
            >
              <option value="">All AI systems</option>
              {systems.map((system) => (
                <option key={system.id} value={system.id}>
                  {system.name}
                </option>
              ))}
            </select>
          </Field>
        )}

        {support.severity && (
          <Field id="rp-severity" label="Severity">
            <select
              id="rp-severity"
              className={FIELD}
              value={filters.severity[0] ?? ""}
              onChange={(event) =>
                onChange({ severity: event.target.value ? [event.target.value] : [] })
              }
            >
              <option value="">Any severity</option>
              {SEVERITIES.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
          </Field>
        )}

        {support.status && (
          <Field id="rp-status" label="Finding status">
            <select
              id="rp-status"
              className={FIELD}
              value={filters.status[0] ?? ""}
              onChange={(event) =>
                onChange({ status: event.target.value ? [event.target.value] : [] })
              }
            >
              <option value="">Any status</option>
              {FINDING_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
          </Field>
        )}

        {support.effect && (
          <Field id="rp-effect" label="Matched rule effect">
            <select
              id="rp-effect"
              className={FIELD}
              value={filters.effect ?? ""}
              onChange={(event) => onChange({ effect: event.target.value || undefined })}
            >
              <option value="">Any effect</option>
              {POLICY_EFFECTS.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
          </Field>
        )}

        {support.budgetMetric && (
          <Field id="rp-metric" label="Budget metric">
            <select
              id="rp-metric"
              className={FIELD}
              value={filters.budgetMetric ?? ""}
              onChange={(event) => onChange({ budgetMetric: event.target.value || undefined })}
            >
              <option value="">All metrics</option>
              {BUDGET_METRICS.map((value) => (
                <option key={value} value={value}>
                  {humanize(value)}
                </option>
              ))}
            </select>
          </Field>
        )}

        <label className="flex h-9 items-center gap-2 text-sm text-ink-700">
          <input
            type="checkbox"
            checked={filters.compare}
            onChange={(event) => onChange({ compare: event.target.checked })}
          />
          Compare to previous period
        </label>

        <div className="ml-auto flex flex-wrap gap-2">
          <Button variant="outline" onClick={onRefresh} disabled={refreshing}>
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
          <Button variant="outline" onClick={copyLink}>
            {copied === "ok" ? "Link copied" : copied === "failed" ? "Copy failed" : "Copy link"}
          </Button>
        </div>
      </div>

      <div className="space-y-1 text-xs text-ink-500" aria-live="polite">
        {support.period && (
          <p>
            <span className="font-semibold text-ink-700">Reporting window:</span>{" "}
            {describePeriod(period)}
            {filters.compare && (
              <>
                {" "}
                · <span className="font-semibold text-ink-700">Previous:</span>{" "}
                {describePeriod(period.previous)}
              </>
            )}
          </p>
        )}
        <p>
          Scope: this organization, {filters.aiSystemId ? "one AI system" : "all AI systems"}. Data
          as of {asOf.toLocaleTimeString()}; the window rolls forward when you press Refresh.
        </p>
        {ignored.length > 0 && (
          <p className="text-warn-700">
            Not applied on this view: {ignored.join(", ")}. These filters are kept for the views
            that use them.
          </p>
        )}
      </div>
    </section>
  );
}
