"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Panel } from "@/components/registry/panel";
import { SectionHeader } from "@/components/section-header";
import {
  correlateCloudAsset,
  driftCheckCloudConnection,
  getCloudConnection,
  getCloudConnectionAssets,
  getCloudCoverage,
  getCloudDrift,
  getCloudEvidence,
  getCloudModelArmor,
  getCloudSyncRuns,
  getAISystems,
  previewCloudPolicyChange,
  syncCloudConnection,
  updateCloudConnectionSchedule,
  verifyCloudConnection,
} from "@/lib/api";
import type {
  AISystemSummary,
  CloudConnectionSummary,
  CloudDiscoveredAssetSummary,
} from "@/lib/types";

export default function GcpConnectionDashboardPage() {
  const params = useParams();
  const connectionId = String(params.connectionId);
  const [connection, setConnection] = useState<CloudConnectionSummary | null>(null);
  const [assets, setAssets] = useState<CloudDiscoveredAssetSummary[]>([]);
  const [systems, setSystems] = useState<AISystemSummary[]>([]);
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);
  const [syncRuns, setSyncRuns] = useState<Array<Record<string, unknown>>>([]);
  const [evidenceCount, setEvidenceCount] = useState(0);
  const [driftCount, setDriftCount] = useState(0);
  const [modelArmor, setModelArmor] = useState<Record<string, unknown> | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncHours, setSyncHours] = useState("");
  const [driftHours, setDriftHours] = useState("");

  const reload = useCallback(() => {
    return Promise.all([
      getCloudConnection(connectionId),
      getCloudConnectionAssets(connectionId),
      getCloudCoverage(connectionId),
      getCloudSyncRuns(connectionId),
      getCloudEvidence(connectionId),
      getCloudDrift(connectionId),
      getCloudModelArmor(connectionId),
      getAISystems(),
    ])
      .then(([conn, assetRes, cov, runs, ev, drift, ma, sysRes]) => {
        setConnection(conn.data.connection);
        setAssets(assetRes.data.assets);
        setCoverage(cov.data.coverage);
        setSyncRuns(runs.data.sync_runs);
        setEvidenceCount(ev.data.evidence_links.length);
        setDriftCount(drift.data.findings.length);
        setModelArmor(ma.data.model_armor);
        setSystems(sysRes.data ?? []);
        setSyncHours(String(conn.data.connection.sync_interval_hours ?? ""));
        setDriftHours(String(conn.data.connection.drift_interval_hours ?? ""));
      })
      .catch((err: Error) => setError(err.message));
  }, [connectionId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function onVerify() {
    setActionMsg("Verifying…");
    await verifyCloudConnection(connectionId);
    setActionMsg("Verified");
    await reload();
  }

  async function onSync() {
    setActionMsg("Sync queued…");
    await syncCloudConnection(connectionId);
    setActionMsg("Sync queued — worker will process shortly");
    await reload();
  }

  async function onDrift() {
    setActionMsg("Drift check queued…");
    await driftCheckCloudConnection(connectionId);
    setActionMsg("Drift check queued");
    await reload();
  }

  async function onSaveSchedule() {
    await updateCloudConnectionSchedule(connectionId, {
      sync_interval_hours: syncHours ? Number(syncHours) : null,
      drift_interval_hours: driftHours ? Number(driftHours) : null,
    });
    setActionMsg("Schedule saved");
    await reload();
  }

  async function onCorrelate(assetId: string, aiSystemId: string) {
    await correlateCloudAsset(connectionId, assetId, aiSystemId);
    setActionMsg("Asset linked to AI system");
    await reload();
  }

  async function onPolicyPreview() {
    await previewCloudPolicyChange(connectionId, { floor_settings: { mode: "AUDIT" } });
    setActionMsg("Policy preview created — complete via Governance → Approvals");
  }

  const caps = connection?.capabilities ?? {};
  const stages = (coverage?.stages as Record<string, number> | undefined) ?? {};

  return (
    <div className="space-y-6">
      <SectionHeader
        title={connection?.display_name ?? "GCP connection"}
        subtitle={`Project ${connection?.scope_identifier ?? connectionId}`}
      />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {actionMsg && <p className="text-sm text-ink-600">{actionMsg}</p>}
      {connection && (
        <Panel title="Status & actions">
          <dl className="grid gap-2 text-sm md:grid-cols-2 mb-4">
            <div>
              <dt className="text-ink-500">Connection status</dt>
              <dd className="font-medium">{connection.status}</dd>
            </div>
            <div>
              <dt className="text-ink-500">Enforcement</dt>
              <dd className="font-medium">
                {connection.enforcement_enabled ? "enabled" : "read-only"}
              </dd>
            </div>
            <div>
              <dt className="text-ink-500">Discovery</dt>
              <dd>{caps.discovery ? "yes" : "no"}</dd>
            </div>
            <div>
              <dt className="text-ink-500">Model Armor read</dt>
              <dd>{caps.model_armor_read ? "yes" : "no"}</dd>
            </div>
          </dl>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="rounded border px-2 py-1 text-sm" onClick={onVerify}>
              Verify
            </button>
            <button type="button" className="rounded border px-2 py-1 text-sm" onClick={onSync}>
              Sync
            </button>
            <button type="button" className="rounded border px-2 py-1 text-sm" onClick={onDrift}>
              Drift check
            </button>
            {connection.enforcement_enabled && (
              <button
                type="button"
                className="rounded border px-2 py-1 text-sm"
                onClick={onPolicyPreview}
              >
                Preview policy change
              </button>
            )}
          </div>
        </Panel>
      )}
      <Panel title="Schedule (hours, optional)">
        <div className="grid gap-2 max-w-md text-sm md:grid-cols-2">
          <label>
            Sync interval
            <input
              className="mt-1 w-full rounded border px-2 py-1"
              value={syncHours}
              onChange={(e) => setSyncHours(e.target.value)}
              placeholder="e.g. 24"
            />
          </label>
          <label>
            Drift interval
            <input
              className="mt-1 w-full rounded border px-2 py-1"
              value={driftHours}
              onChange={(e) => setDriftHours(e.target.value)}
              placeholder="e.g. 168"
            />
          </label>
        </div>
        <button
          type="button"
          className="mt-2 rounded bg-brand-600 px-3 py-1 text-sm text-white"
          onClick={onSaveSchedule}
        >
          Save schedule
        </button>
      </Panel>
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Sync runs">
          {syncRuns.length === 0 ? (
            <p className="text-sm text-ink-500">No sync runs yet.</p>
          ) : (
            <ul className="text-sm space-y-1">
              {syncRuns.slice(0, 5).map((run) => (
                <li key={String(run.id)}>
                  {String(run.status)} — discovered {String(run.assets_discovered)}, updated{" "}
                  {String(run.assets_updated)}
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Coverage snapshot">
          {coverage ? (
            <ul className="text-sm space-y-1">
              <li>Discovered: {stages.discovered ?? 0}</li>
              <li>Linked to registry: {stages.linked_to_registry ?? 0}</li>
              <li>Pending link: {stages.candidates_pending_link ?? 0}</li>
              {Array.isArray(coverage.limitations as string[]) &&
                (coverage.limitations as string[]).slice(0, 2).map((line) => (
                  <li key={line} className="text-ink-500 text-xs">
                    {line}
                  </li>
                ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-500">Loading…</p>
          )}
        </Panel>
      </div>
      <Panel title="Model Armor">
        {modelArmor ? (
          <p className="text-sm">Supported: {String(modelArmor.supported ?? false)}</p>
        ) : (
          <p className="text-sm text-ink-500">Loading…</p>
        )}
      </Panel>
      <Panel title={`Evidence (${evidenceCount}) · Drift findings (${driftCount})`}>
        <p className="text-sm text-ink-600">
          Evidence links are recorded on verify, sync, apply, and verify steps (metadata only).
        </p>
      </Panel>
      <Panel title="Discovered resources">
        {assets.length === 0 ? (
          <p className="text-sm text-ink-500">No assets stored yet. Run Sync.</p>
        ) : (
          <ul className="divide-y text-sm">
            {assets.map((asset) => (
              <li key={asset.id} className="py-2 space-y-1">
                <div className="font-medium">
                  {asset.display_name || asset.canonical_resource_name}
                </div>
                <div className="text-ink-600">
                  {asset.asset_type} · {asset.status}
                </div>
                {asset.status !== "linked" && systems.length > 0 && (
                  <select
                    className="rounded border text-xs"
                    defaultValue=""
                    onChange={(e) => {
                      if (e.target.value) onCorrelate(asset.id, e.target.value);
                    }}
                  >
                    <option value="">Link to AI system…</option>
                    {systems.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Link href="/integrations/gcp" className="text-sm text-brand-600 hover:underline">
        ← GCP wizard
      </Link>
    </div>
  );
}
