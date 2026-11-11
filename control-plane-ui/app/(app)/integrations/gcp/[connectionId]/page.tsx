"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Panel } from "@/components/registry/panel";
import { SectionHeader } from "@/components/section-header";
import {
  getCloudConnection,
  getCloudConnectionAssets,
  getCloudCoverage,
  getCloudDrift,
  getCloudEvidence,
  getCloudSyncRuns,
} from "@/lib/api";
import type { CloudConnectionSummary, CloudDiscoveredAssetSummary } from "@/lib/types";

export default function GcpConnectionDashboardPage() {
  const params = useParams();
  const connectionId = String(params.connectionId);
  const [connection, setConnection] = useState<CloudConnectionSummary | null>(null);
  const [assets, setAssets] = useState<CloudDiscoveredAssetSummary[]>([]);
  const [coverage, setCoverage] = useState<Record<string, unknown> | null>(null);
  const [syncRuns, setSyncRuns] = useState<Array<Record<string, unknown>>>([]);
  const [evidenceCount, setEvidenceCount] = useState(0);
  const [driftCount, setDriftCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      getCloudConnection(connectionId),
      getCloudConnectionAssets(connectionId),
      getCloudCoverage(connectionId),
      getCloudSyncRuns(connectionId),
      getCloudEvidence(connectionId),
      getCloudDrift(connectionId),
    ])
      .then(([conn, assetRes, cov, runs, ev, drift]) => {
        if (!active) return;
        setConnection(conn.data.connection);
        setAssets(assetRes.data.assets);
        setCoverage(cov.data.coverage);
        setSyncRuns(runs.data.sync_runs);
        setEvidenceCount(ev.data.evidence_links.length);
        setDriftCount(drift.data.findings.length);
      })
      .catch((err: Error) => {
        if (active) setError(err.message);
      });
    return () => {
      active = false;
    };
  }, [connectionId]);

  const caps = connection?.capabilities ?? {};

  return (
    <div className="space-y-6">
      <SectionHeader
        title={connection?.display_name ?? "GCP connection"}
        subtitle={`Project ${connection?.scope_identifier ?? connectionId}`}
      />
      {error && <p className="text-sm text-destructive">{error}</p>}
      {connection && (
        <Panel title="Status">
          <dl className="grid gap-2 text-sm md:grid-cols-2">
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
        </Panel>
      )}
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
        <Panel title="Coverage">
          {coverage ? (
            <pre className="text-xs overflow-auto max-h-48">{JSON.stringify(coverage, null, 2)}</pre>
          ) : (
            <p className="text-sm text-ink-500">Loading…</p>
          )}
        </Panel>
      </div>
      <Panel title={`Evidence (${evidenceCount}) · Drift findings (${driftCount})`}>
        <p className="text-sm text-ink-600">
          Evidence links are recorded on verify, sync, apply, and verify steps (metadata only).
        </p>
      </Panel>
      <Panel title="Discovered resources">
        {assets.length === 0 ? (
          <p className="text-sm text-ink-500">No assets stored yet. Run Discover from the wizard.</p>
        ) : (
          <ul className="divide-y text-sm">
            {assets.map((asset) => (
              <li key={asset.id} className="py-2">
                <div className="font-medium">{asset.display_name || asset.canonical_resource_name}</div>
                <div className="text-ink-600">
                  {asset.asset_type} · {asset.status}
                </div>
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
