"use client";

import { useState } from "react";
import {
  connectionType,
  SEARCH_TYPE,
  TYPE_ORDER,
  TypeIcon,
  typeLabel,
  WEBHOOK_TYPE,
  type Connection,
} from "@/components/integrations/connection-types";
import { FormField } from "@/components/registry/form-field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Textarea } from "@/components/ui/textarea";
import {
  createAlertIntegration,
  createWebhookEndpoint,
  testSearchIntegration,
  updateAlertIntegration,
  updateWebhookEndpoint,
  upsertElasticsearchIntegration,
} from "@/lib/api";
import type {
  AlertEventGroup,
  AlertIntegrationProvider,
  SearchIntegrationProvider,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const FORM_ID = "connection-form";
const DEFAULT_ALERT_EVENTS = ["enforcement.*", "risk.finding.created"];
const DEFAULT_WEBHOOK_EVENTS = [...DEFAULT_ALERT_EVENTS, "approval.requested"];

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf(":");
    if (idx <= 0) continue;
    out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return out;
}

function formatHeaders(headers?: Record<string, string>) {
  return Object.entries(headers ?? {})
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");
}

type FormProps = {
  type: string;
  connection: Connection | null;
  groups: AlertEventGroup[];
  alertProviders: AlertIntegrationProvider[];
  searchProviders: SearchIntegrationProvider[];
  onBusyChange: (busy: boolean) => void;
  /** `secret` is set only when a new webhook returned its signing secret. */
  onSaved: (message: string, secret?: string) => void;
};

export function ConnectionFormModal({
  editing,
  connections,
  onClose,
  ...formProps
}: Omit<FormProps, "type" | "connection" | "onBusyChange"> & {
  /** Row being edited, or null to add a new one. */
  editing: Connection | null;
  connections: Connection[];
  onClose: () => void;
}) {
  const [picked, setPicked] = useState(editing ? connectionType(editing) : TYPE_ORDER[0]);
  const [busy, setBusy] = useState(false);

  // Only offer types the gateway actually reports.
  const available = TYPE_ORDER.filter(
    (id) =>
      id === WEBHOOK_TYPE ||
      formProps.alertProviders.some((p) => p.id === id) ||
      formProps.searchProviders.some((p) => p.id === id),
  );
  const type = editing || available.includes(picked) ? picked : (available[0] ?? WEBHOOK_TYPE);
  // Elasticsearch is a single record, so "adding" it again edits the existing one.
  const connection =
    editing ??
    (type === SEARCH_TYPE ? (connections.find((c) => c.kind === "search") ?? null) : null);

  return (
    <Modal
      open
      onClose={onClose}
      title={editing ? `Edit ${editing.item.name}` : "Add integration"}
      description={
        editing ? typeLabel(type) : "Choose where to send events, then fill in the details."
      }
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form={FORM_ID} disabled={busy}>
            {busy ? "Saving…" : connection ? "Save changes" : "Add integration"}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        {editing ? null : (
          <div
            className="grid grid-cols-3 gap-2 sm:grid-cols-5"
            role="radiogroup"
            aria-label="Integration type"
          >
            {available.map((id) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={type === id}
                onClick={() => setPicked(id)}
                className={cn(
                  "flex flex-col items-center gap-2 rounded-lg border px-2 py-3 text-center transition",
                  type === id
                    ? "border-accent-600 bg-accent-50 ring-1 ring-accent-600"
                    : "border-base-200 bg-base-100 hover:border-ink-300",
                )}
              >
                <TypeIcon type={id} className="size-9" />
                <span className="text-xs font-semibold text-ink-800">{typeLabel(id)}</span>
              </button>
            ))}
          </div>
        )}

        <ConnectionForm
          key={`${type}:${connection?.item.id ?? "new"}`}
          type={type}
          connection={connection}
          onBusyChange={setBusy}
          {...formProps}
        />
      </div>
    </Modal>
  );
}

function ConnectionForm({
  type,
  connection,
  groups,
  alertProviders,
  searchProviders,
  onBusyChange,
  onSaved,
}: FormProps) {
  const isWebhook = type === WEBHOOK_TYPE;
  const isSearch = type === SEARCH_TYPE;
  const alert = connection?.kind === "alert" ? connection.item : null;
  const webhook = connection?.kind === "webhook" ? connection.item : null;
  const search = connection?.kind === "search" ? connection.item : null;

  const configFields =
    (isSearch ? searchProviders : alertProviders).find((p) => p.id === type)?.config_fields ?? [];

  const [name, setName] = useState(
    connection?.item.name ?? (isWebhook ? "Governance alerts" : `${typeLabel(type)} alerts`),
  );
  const [url, setUrl] = useState(webhook?.url ?? "https://");
  const [config, setConfig] = useState<Record<string, string>>(() => {
    if (alert) return { ...alert.config };
    if (search) {
      // The stored API key comes back masked; leave it blank so saving keeps it.
      return {
        url: String(search.config.url ?? ""),
        index_prefix: String(search.config.index_prefix ?? "rai"),
        verify_tls: String(search.config.verify_tls ?? "true"),
      };
    }
    return {};
  });
  const [events, setEvents] = useState<Set<string>>(
    () =>
      new Set(
        alert?.event_types ??
          webhook?.event_types ??
          (isWebhook ? DEFAULT_WEBHOOK_EVENTS : DEFAULT_ALERT_EVENTS),
      ),
  );
  const [headerText, setHeaderText] = useState(() =>
    formatHeaders(alert?.custom_headers ?? webhook?.custom_headers),
  );
  const [enabled, setEnabled] = useState(connection?.item.enabled ?? true);
  const [syncLogs, setSyncLogs] = useState(search?.sync_logs ?? true);
  const [useForSearch, setUseForSearch] = useState(search?.use_for_search ?? true);
  const [error, setError] = useState<string | null>(null);
  const [testMessage, setTestMessage] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);

  // Keep subscriptions that are not in the catalog visible, so they can be removed.
  const known = new Set(groups.flatMap((g) => g.events.map((e) => e.type)));
  const extra = (alert?.event_types ?? webhook?.event_types ?? []).filter((e) => !known.has(e));
  const eventGroups: AlertEventGroup[] = extra.length
    ? [
        ...groups,
        { id: "other", label: "Other", events: extra.map((e) => ({ type: e, label: e })) },
      ]
    : groups;

  function toggleEvent(eventType: string) {
    setEvents((prev) => {
      const next = new Set(prev);
      if (next.has(eventType)) next.delete(eventType);
      else next.add(eventType);
      return next;
    });
  }

  function validate() {
    if (isSearch) {
      return config.url?.trim() ? null : "Cluster URL is required.";
    }
    if (!name.trim()) return "Name is required.";
    if (isWebhook) {
      if (!/^https?:\/\/[^/\s]+/i.test(url.trim())) return "Enter a full webhook URL.";
    } else {
      const missing = configFields.find((field) => !config[field.key]?.trim());
      if (missing) return `${missing.label} is required.`;
    }
    return events.size ? null : "Select at least one event to send.";
  }

  async function save() {
    const eventTypes = Array.from(events);
    const headers = parseHeaders(headerText);

    if (isSearch) {
      await upsertElasticsearchIntegration({
        name: search?.name ?? "Elasticsearch",
        config,
        enabled,
        sync_logs: syncLogs,
        use_for_search: useForSearch,
      });
      onSaved("Elasticsearch integration saved.");
    } else if (isWebhook) {
      const payload = {
        name: name.trim(),
        url: url.trim(),
        event_types: eventTypes,
        custom_headers: headers,
      };
      if (webhook) {
        await updateWebhookEndpoint(webhook.id, { ...payload, enabled });
        onSaved("Webhook updated.");
      } else {
        const res = await createWebhookEndpoint(payload);
        onSaved("Webhook created.", res.data.secret);
      }
    } else {
      const payload = {
        name: name.trim(),
        event_types: eventTypes,
        config,
        custom_headers: headers,
        enabled,
      };
      if (alert) {
        await updateAlertIntegration(alert.id, payload);
        onSaved("Integration updated.");
      } else {
        await createAlertIntegration({ ...payload, provider: type });
        onSaved("Integration added.");
      }
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const problem = validate();
    setError(problem);
    if (problem) return;
    onBusyChange(true);
    try {
      await save();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      onBusyChange(false);
    }
  }

  async function onTestConnection() {
    if (!search) return;
    setTesting(true);
    setError(null);
    setTestMessage(null);
    try {
      const res = await testSearchIntegration(search.id);
      setTestMessage(
        `Connected to cluster “${res.data.cluster_name ?? "unknown"}” (indexes: ${res.data.audit_index}, ${res.data.trajectory_index}).`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection test failed");
    } finally {
      setTesting(false);
    }
  }

  return (
    <form id={FORM_ID} noValidate onSubmit={(e) => void onSubmit(e)} className="space-y-4">
      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-danger-100 bg-danger-50 px-4 py-3 text-sm text-danger-600"
        >
          {error}
        </div>
      ) : null}

      {isSearch ? (
        <p className="text-sm text-ink-500">
          Indexes runtime audit records and trajectories for search.
          {search ? " Already configured — saving updates it." : ""}
        </p>
      ) : (
        <FormField label="Name" required>
          <Input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        </FormField>
      )}

      {isWebhook ? (
        <FormField label="HTTPS URL" required hint="Each delivery is signed with X-RAI-Signature.">
          <Input aria-label="HTTPS URL" value={url} onChange={(e) => setUrl(e.target.value)} />
        </FormField>
      ) : (
        configFields.map((field) => (
          <FormField
            key={field.key}
            label={field.label}
            required={!isSearch}
            hint={search && field.type === "secret" ? "Leave blank to keep the key." : undefined}
          >
            <Input
              aria-label={field.label}
              type={field.type === "secret" ? "password" : "text"}
              placeholder={"placeholder" in field ? String(field.placeholder ?? "") : undefined}
              value={config[field.key] ?? ""}
              onChange={(e) => setConfig((c) => ({ ...c, [field.key]: e.target.value }))}
              autoComplete="off"
            />
          </FormField>
        ))
      )}

      {isSearch ? (
        <div className="space-y-2 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={syncLogs}
              onChange={(e) => setSyncLogs(e.target.checked)}
            />
            Sync logs to Elasticsearch
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={useForSearch}
              onChange={(e) => setUseForSearch(e.target.checked)}
            />
            Query Audit Explorer and trajectories from Elasticsearch
          </label>
        </div>
      ) : (
        <>
          <fieldset className="space-y-1.5">
            <legend className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">
              Events to send
            </legend>
            <div className="max-h-48 space-y-3 overflow-y-auto rounded-lg border border-base-200 p-3">
              {eventGroups.map((group) => (
                <div key={group.id}>
                  <p className="text-xs font-semibold uppercase text-ink-400">{group.label}</p>
                  <ul className="mt-1 space-y-1">
                    {group.events.map((ev) => (
                      <li key={ev.type}>
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={events.has(ev.type)}
                            onChange={() => toggleEvent(ev.type)}
                          />
                          {ev.label}
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </fieldset>

          <FormField
            label="Custom headers"
            hint="Optional. One header per line, as Name: value. X-RAI-* headers cannot be overridden."
          >
            <Textarea
              aria-label="Custom headers"
              rows={3}
              className="font-mono text-xs"
              value={headerText}
              onChange={(e) => setHeaderText(e.target.value)}
            />
          </FormField>
        </>
      )}

      {/* A new webhook is always created active, so the toggle appears once it exists. */}
      {isWebhook && !webhook ? null : (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Active
        </label>
      )}

      {search ? (
        <div className="space-y-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={testing}
            onClick={() => void onTestConnection()}
          >
            {testing ? "Testing…" : "Test saved connection"}
          </Button>
          {testMessage ? <p className="text-sm text-ink-700">{testMessage}</p> : null}
        </div>
      ) : null}
    </form>
  );
}
