"use client";

import { useCallback, useEffect, useState } from "react";
import { ConnectionFormModal } from "@/components/integrations/connection-form-modal";
import {
  connectionType,
  TypeIcon,
  typeLabel,
  type Connection,
} from "@/components/integrations/connection-types";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Table } from "@/components/ui/table";
import {
  deleteAlertIntegration,
  deleteSearchIntegration,
  getAlertEventGroups,
  getAlertIntegrationCatalog,
  getSearchIntegrationCatalog,
  listAlertIntegrations,
  listSearchIntegrations,
  listWebhookDeliveries,
  listWebhookEndpoints,
  rotateWebhookEndpoint,
} from "@/lib/api";
import type {
  AlertEventGroup,
  AlertIntegrationProvider,
  SearchIntegrationProvider,
  WebhookDeliverySummary,
} from "@/lib/types";

function formatWhen(value?: string | null) {
  if (!value) return "Never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Host only: alert webhook URLs carry secrets in their path. */
function destinationOf(connection: Connection) {
  const raw =
    connection.kind === "webhook"
      ? connection.item.url
      : (connection.item.config.webhook_url ?? connection.item.config.url ?? "");
  if (connection.kind === "webhook") return raw;
  try {
    return new URL(raw).host;
  } catch {
    return "";
  }
}

function LastEvent({ connection }: { connection: Connection }) {
  // Elasticsearch is a log sync target; it does not receive events.
  if (connection.kind === "search") return <span className="text-ink-400">—</span>;
  const { last_event_at: at, last_event_status: status } = connection.item;
  if (!at) return <span className="text-ink-400">Never</span>;
  return (
    <div className="space-y-1">
      <div className="whitespace-nowrap">{formatWhen(at)}</div>
      {status ? <StatusBadge status={status} /> : null}
    </div>
  );
}

export default function IntegrationsPage() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [alertProviders, setAlertProviders] = useState<AlertIntegrationProvider[]>([]);
  const [searchProviders, setSearchProviders] = useState<SearchIntegrationProvider[]>([]);
  const [groups, setGroups] = useState<AlertEventGroup[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDeliverySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [secretOnce, setSecretOnce] = useState<string | null>(null);
  /** null = modal closed; `editing: null` = adding a new connection. */
  const [modal, setModal] = useState<{ editing: Connection | null } | null>(null);

  const load = useCallback(async () => {
    try {
      const [alerts, alertCatalog, events, search, searchCatalog, webhooks, sent] =
        await Promise.all([
          listAlertIntegrations(),
          getAlertIntegrationCatalog(),
          getAlertEventGroups(),
          listSearchIntegrations(),
          getSearchIntegrationCatalog(),
          listWebhookEndpoints(),
          listWebhookDeliveries(20),
        ]);
      const rows: Connection[] = [
        ...(alerts.data.items ?? []).map((item) => ({ kind: "alert" as const, item })),
        ...(webhooks.data.items ?? []).map((item) => ({ kind: "webhook" as const, item })),
        ...(search.data.items ?? []).map((item) => ({ kind: "search" as const, item })),
      ];
      rows.sort((a, b) => (b.item.created_at ?? "").localeCompare(a.item.created_at ?? ""));
      setConnections(rows);
      setAlertProviders(alertCatalog.data.providers ?? []);
      setSearchProviders(searchCatalog.data.providers ?? []);
      setGroups(events.data.groups ?? []);
      setDeliveries(sent.data.items ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load integrations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const closeModal = useCallback(() => setModal(null), []);

  function openModal(editing: Connection | null) {
    setError(null);
    setMessage(null);
    setModal({ editing });
  }

  function onSaved(text: string, secret?: string) {
    setModal(null);
    setError(null);
    setMessage(text);
    if (secret) setSecretOnce(secret);
    void load();
  }

  async function runAction(action: () => Promise<void>, failure: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : failure);
    } finally {
      setBusy(false);
    }
  }

  function onRemove(connection: Connection) {
    if (!window.confirm(`Remove ${connection.item.name}?`)) return;
    void runAction(async () => {
      if (connection.kind === "search") await deleteSearchIntegration(connection.item.id);
      else await deleteAlertIntegration(connection.item.id);
      setMessage("Integration removed.");
    }, "Remove failed");
  }

  function onRotate(id: string, name: string) {
    if (!window.confirm(`Rotate the signing secret for ${name}? The current secret stops working.`))
      return;
    void runAction(async () => {
      const res = await rotateWebhookEndpoint(id);
      setSecretOnce(res.data.secret);
    }, "Failed to rotate secret");
  }

  const rows = connections.map((connection) => {
    const { item } = connection;
    const type = connectionType(connection);
    const events = connection.kind === "search" ? [] : (connection.item.event_types ?? []);
    const destination = destinationOf(connection);
    return [
      <div key="name" className="min-w-0">
        <div className="font-semibold text-ink-900">{item.name}</div>
        {destination ? (
          <div className="max-w-[260px] truncate text-[11px] text-ink-400" title={destination}>
            {destination}
          </div>
        ) : null}
      </div>,
      <div key="type" className="flex items-center gap-2 whitespace-nowrap">
        <TypeIcon type={type} />
        {typeLabel(type)}
      </div>,
      <span key="events" className="block max-w-[220px] break-words">
        {connection.kind === "search" ? "Runtime logs" : events.join(", ")}
      </span>,
      <StatusBadge key="status" status={item.enabled ? item.status : "DISABLED"} />,
      <LastEvent key="last" connection={connection} />,
      <div key="actions" className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="secondary"
          aria-label={`Edit ${item.name}`}
          disabled={busy}
          onClick={() => openModal(connection)}
        >
          Edit
        </Button>
        {connection.kind === "webhook" ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => onRotate(item.id, item.name)}
          >
            Rotate secret
          </Button>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Remove ${item.name}`}
            disabled={busy}
            onClick={() => onRemove(connection)}
          >
            Remove
          </Button>
        )}
      </div>,
    ];
  });

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Integrations"
        subtitle="Alert channels, signed webhooks, and Elasticsearch log sync."
        actions={
          <Button disabled={loading} onClick={() => openModal(null)}>
            Add integration
          </Button>
        }
      />

      {error ? (
        <div className="rounded-xl border border-danger-100 bg-danger-50 p-4 text-sm text-danger-600">
          {error}
        </div>
      ) : null}
      {message ? (
        <div className="rounded-xl border border-base-200 bg-base-50 p-4 text-sm">{message}</div>
      ) : null}

      {secretOnce ? (
        <Panel
          title="Signing secret (shown once)"
          actions={
            <Button size="sm" variant="ghost" onClick={() => setSecretOnce(null)}>
              Dismiss
            </Button>
          }
        >
          <p className="mb-2 text-sm text-ink-500">
            Store this secret now. It verifies <code>X-RAI-Signature</code> on each delivery.
          </p>
          <code className="block break-all rounded bg-base-100 p-3 text-xs">{secretOnce}</code>
        </Panel>
      ) : null}

      <Panel title="Configured integrations">
        {loading ? (
          <p className="text-sm text-ink-500">Loading integrations…</p>
        ) : rows.length === 0 ? (
          <p className="text-sm text-ink-500">
            No integrations yet. Use Add integration to create one.
          </p>
        ) : (
          <Table
            columns={["Name", "Type", "Events", "Status", "Last event", "Actions"]}
            rows={rows}
          />
        )}
      </Panel>

      {deliveries.length > 0 ? (
        <Panel title="Recent webhook deliveries">
          <ul className="divide-y divide-base-200 text-sm">
            {deliveries.map((d) => (
              <li key={d.id} className="flex flex-wrap justify-between gap-2 py-2">
                <span>
                  {connections.find((c) => c.item.id === d.endpoint_id)?.item.name ?? "Webhook"} ·{" "}
                  {d.event_type} · {formatWhen(d.created_at)}
                </span>
                <span className="flex items-center gap-2">
                  <StatusBadge status={d.status} />
                  <span className="text-xs text-ink-500">
                    attempts {d.attempt_count}
                    {d.last_error ? ` · ${d.last_error}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}

      {modal ? (
        <ConnectionFormModal
          editing={modal.editing}
          connections={connections}
          groups={groups}
          alertProviders={alertProviders}
          searchProviders={searchProviders}
          onClose={closeModal}
          onSaved={onSaved}
        />
      ) : null}
    </div>
  );
}
