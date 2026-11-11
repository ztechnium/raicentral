"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SectionHeader } from "@/components/section-header";
import { Panel } from "@/components/registry/panel";
import {
  createCloudConnection,
  listCloudConnections,
  syncCloudConnection,
  verifyCloudConnection,
} from "@/lib/api";
import type { CloudConnectionSummary } from "@/lib/types";

export default function GcpIntegrationsPage() {
  const [connections, setConnections] = useState<CloudConnectionSummary[]>([]);
  const [displayName, setDisplayName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [serviceAccount, setServiceAccount] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () =>
    listCloudConnections()
      .then((res) => setConnections(res.data.connections))
      .catch((err: Error) => setError(err.message));

  useEffect(() => {
    refresh();
  }, []);

  async function onCreate() {
    setError(null);
    setMessage(null);
    try {
      const res = await createCloudConnection({
        provider: "gcp",
        display_name: displayName || projectId,
        scope_identifier: projectId,
        auth_mode: "adc_impersonation",
        auth_config: { service_account_email: serviceAccount },
        enforcement_enabled: false,
      });
      setMessage(`Connection created: ${res.data.connection.id}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Google Cloud"
        description="Keyless onboarding: verify permissions, discover Vertex AI resources, inspect Model Armor (read-only)."
      />
      <Panel title="Connect project">
        <div className="grid gap-3 max-w-lg">
          <label className="text-sm">
            Display name
            <input
              className="mt-1 w-full rounded border px-2 py-1"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </label>
          <label className="text-sm">
            GCP project ID
            <input
              className="mt-1 w-full rounded border px-2 py-1"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
            />
          </label>
          <label className="text-sm">
            Discovery service account email
            <input
              className="mt-1 w-full rounded border px-2 py-1"
              value={serviceAccount}
              onChange={(e) => setServiceAccount(e.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded bg-brand-600 px-3 py-2 text-sm text-white"
            onClick={onCreate}
            disabled={!projectId || !serviceAccount}
          >
            Create connection
          </button>
          {message && <p className="text-sm text-ink-600">{message}</p>}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
      </Panel>
      <Panel title="Connections">
        {connections.length === 0 ? (
          <p className="text-sm text-ink-500">No connections yet.</p>
        ) : (
          <ul className="space-y-3">
            {connections.map((conn) => (
              <li key={conn.id} className="rounded border p-3 text-sm">
                <ConnectionRow conn={conn} onRefresh={refresh} />
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Link href="/integrations" className="text-sm text-brand-600 hover:underline">
        ← All integrations
      </Link>
    </div>
  );
}

function ConnectionRow({
  conn,
  onRefresh,
}: {
  conn: CloudConnectionSummary;
  onRefresh: () => Promise<void>;
}) {
  const [status, setStatus] = useState<string | null>(null);

  async function verify() {
    setStatus("Verifying…");
    await verifyCloudConnection(conn.id);
    setStatus("Verified");
    await onRefresh();
  }

  async function sync() {
    setStatus("Sync queued…");
    await syncCloudConnection(conn.id);
    setStatus("Sync queued");
    await onRefresh();
  }

  return (
    <>
      <div className="font-medium">{conn.display_name}</div>
      <div className="text-ink-600">
        Project {conn.scope_identifier} · {conn.status}
      </div>
      <div className="mt-2 flex gap-2">
        <button type="button" className="rounded border px-2 py-1" onClick={verify}>
          Verify
        </button>
        <button type="button" className="rounded border px-2 py-1" onClick={sync}>
          Discover
        </button>
        <Link className="rounded border px-2 py-1" href={`/integrations/gcp/${conn.id}`}>
          Dashboard
        </Link>
      </div>
      {status && <p className="mt-1 text-ink-500">{status}</p>}
    </>
  );
}
