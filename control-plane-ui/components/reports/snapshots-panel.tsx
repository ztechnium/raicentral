"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/components/registry/panel";
import { FailureMessage } from "@/components/reports/states";
import { classifyFailure, reportsClient, type LoadFailure } from "@/lib/reports/client";
import {
  buildReportParams,
  VIEW_LABELS,
  type ReportFilterState,
  type ReportView,
  type ResolvedPeriod,
} from "@/lib/reports/filters";
import type { ReportSnapshot, SnapshotReportType } from "@/lib/reports/types";

/** Which stored report type backs each view. */
const SNAPSHOT_TYPE: Record<ReportView, SnapshotReportType> = {
  executive: "EXECUTIVE_SUMMARY",
  risk: "RISK_REPORT",
  controls: "POLICY_REPORT",
  assurance: "ASSURANCE_REPORT",
  runtime: "RUNTIME_REPORT",
  economics: "BUDGET_REPORT",
};

export function SnapshotsPanel({
  filters,
  period,
  canCreate,
}: {
  filters: ReportFilterState;
  period: ResolvedPeriod;
  canCreate: boolean;
}) {
  const type = SNAPSHOT_TYPE[filters.view];
  const [items, setItems] = useState<ReportSnapshot[] | null>(null);
  const [failure, setFailure] = useState<LoadFailure | null>(null);
  const [selected, setSelected] = useState<ReportSnapshot | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await reportsClient.listSnapshots(type));
      setFailure(null);
    } catch (error) {
      setFailure(classifyFailure(error));
    }
  }, [type]);

  useEffect(() => {
    setItems(null);
    setSelected(null);
    void load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      // Same effective scope/period the visible view uses.
      const params = buildReportParams(filters, period, filters.view);
      const created = await reportsClient.createSnapshot(type, params as Record<string, unknown>);
      setNotice(`Snapshot saved (${created.content_digest.slice(0, 12)}…).`);
      await load();
    } catch (error) {
      setFailure(classifyFailure(error));
    } finally {
      setSaving(false);
    }
  };

  const open = async (id: string) => {
    try {
      setSelected(await reportsClient.getSnapshot(id));
    } catch (error) {
      setFailure(classifyFailure(error));
    }
  };

  return (
    <Panel
      title="Saved snapshots"
      description={`Immutable, hash-verified copies of the ${VIEW_LABELS[filters.view]} data.`}
      actions={
        canCreate ? (
          <Button size="sm" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save snapshot of this view"}
          </Button>
        ) : (
          <span className="text-xs text-ink-500">
            Creating snapshots needs the reports:snapshot permission.
          </span>
        )
      }
    >
      <div className="space-y-3" aria-live="polite">
        {notice && <p className="text-sm text-accent-700">{notice}</p>}
        {failure && <FailureMessage failure={failure} onRetry={load} />}
        {!failure && items === null && <p className="text-sm text-ink-500">Loading snapshots…</p>}
        {items && items.length === 0 && (
          <p className="text-sm text-ink-500">No snapshots saved for this report yet.</p>
        )}
        {items && items.length > 0 && (
          <ul className="divide-y divide-base-100 text-sm">
            {items.slice(0, 10).map((snapshot) => (
              <li
                key={snapshot.id}
                className="flex flex-wrap items-center justify-between gap-2 py-2"
              >
                <span className="text-ink-800">
                  {new Date(snapshot.generated_at).toLocaleString()} ·{" "}
                  <span className="text-ink-500">
                    {new Date(snapshot.period_start).toISOString().slice(0, 10)} →{" "}
                    {new Date(snapshot.period_end).toISOString().slice(0, 10)}
                  </span>
                </span>
                <Button variant="ghost" size="sm" onClick={() => open(snapshot.id)}>
                  Inspect
                </Button>
              </li>
            ))}
          </ul>
        )}

        {selected && (
          <div
            className="rounded-md border border-base-200 bg-base-100 p-3 text-xs text-ink-700"
            role="region"
            aria-label="Snapshot details"
          >
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <strong className="text-sm text-ink-900">Snapshot {selected.id.slice(0, 8)}</strong>
              {selected.integrity_verified === true && (
                <Badge variant="accent">Integrity verified</Badge>
              )}
              {selected.integrity_verified === false && (
                <Badge variant="danger">Content does not match digest</Badge>
              )}
            </div>
            <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
              <dt className="font-semibold">Generated</dt>
              <dd>
                {new Date(selected.generated_at).toISOString()} by {selected.generated_by}
              </dd>
              <dt className="font-semibold">Period</dt>
              <dd>
                {new Date(selected.period_start).toISOString()} →{" "}
                {new Date(selected.period_end).toISOString()}
              </dd>
              <dt className="font-semibold">Schema version</dt>
              <dd>{selected.schema_version}</dd>
              <dt className="font-semibold">SHA-256</dt>
              <dd className="break-all font-mono">{selected.content_digest}</dd>
              <dt className="font-semibold">Applied filters</dt>
              <dd className="break-all font-mono">{JSON.stringify(selected.filters_json)}</dd>
            </dl>
            <details className="mt-2">
              <summary className="cursor-pointer font-semibold text-accent-700">
                Stored data
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto rounded bg-base-50 p-2">
                {JSON.stringify(selected.data_snapshot, null, 2)}
              </pre>
            </details>
          </div>
        )}
      </div>
    </Panel>
  );
}
