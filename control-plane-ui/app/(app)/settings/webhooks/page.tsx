"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { SectionHeader } from "@/components/section-header";
import { FormField } from "@/components/registry/form-field";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createWebhookEndpoint,
  getAlertEventGroups,
  listWebhookDeliveries,
  listWebhookEndpoints,
  rotateWebhookEndpoint,
} from "@/lib/api";
import type { AlertEventGroup, WebhookDeliverySummary, WebhookEndpointSummary } from "@/lib/types";

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf(":");
    if (idx <= 0) continue;
    const key = trimmed.slice(0, idx).trim();
    const value = trimmed.slice(idx + 1).trim();
    if (key) out[key] = value;
  }
  return out;
}

export default function WebhookSettingsPage() {
  const [endpoints, setEndpoints] = useState<WebhookEndpointSummary[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDeliverySummary[]>([]);
  const [groups, setGroups] = useState<AlertEventGroup[]>([]);
  const [selectedEvents, setSelectedEvents] = useState<Set<string>>(
    () => new Set(["enforcement.*", "risk.finding.created", "approval.requested"]),
  );
  const [error, setError] = useState<string | null>(null);
  const [secretOnce, setSecretOnce] = useState<string | null>(null);
  const [name, setName] = useState("Governance alerts");
  const [url, setUrl] = useState("https://");
  const [headerText, setHeaderText] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      listWebhookEndpoints(),
      listWebhookDeliveries(40),
      getAlertEventGroups(),
    ])
      .then(([ep, del, ev]) => {
        setEndpoints(ep.data.items ?? []);
        setDeliveries(del.data.items ?? []);
        setGroups(ev.data.groups ?? []);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggleEvent = (type: string) => {
    setSelectedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const handleCreate = async () => {
    if (selectedEvents.size === 0) {
      setError("Select at least one event type.");
      return;
    }
    setSaving(true);
    setError(null);
    setSecretOnce(null);
    try {
      const res = await createWebhookEndpoint({
        name,
        url,
        event_types: Array.from(selectedEvents),
        custom_headers: parseHeaders(headerText),
      });
      setSecretOnce(res.data.secret);
      setUrl("https://");
      setHeaderText("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create webhook");
    } finally {
      setSaving(false);
    }
  };

  const handleRotate = async (id: string) => {
    setSaving(true);
    setError(null);
    try {
      const res = await rotateWebhookEndpoint(id);
      setSecretOnce(res.data.secret);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to rotate secret");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Webhooks"
        subtitle="Signed HTTPS callbacks for enforcement blocks, monitor signals, approval holds, and risk findings."
        actions={
          <div className="flex flex-wrap gap-3 text-sm">
            <Link href="/settings/integrations" className="text-primary hover:underline">
              Integrations
            </Link>
            <Link href="/settings" className="text-primary hover:underline">
              Settings
            </Link>
          </div>
        }
      />

      {error ? (
        <Panel title="Error">
          <p className="text-sm text-destructive">{error}</p>
        </Panel>
      ) : null}

      {secretOnce ? (
        <Panel title="Signing secret (shown once)">
          <p className="mb-2 text-sm text-muted-foreground">
            Store this secret now. It verifies <code>X-RAI-Signature</code> on each delivery.
          </p>
          <code className="block break-all rounded bg-base-100 p-3 text-xs">{secretOnce}</code>
        </Panel>
      ) : null}

      <Panel title="Create webhook">
        <div className="grid gap-4 md:grid-cols-2">
          <FormField label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </FormField>
          <FormField label="HTTPS URL">
            <Input value={url} onChange={(e) => setUrl(e.target.value)} />
          </FormField>
        </div>

        <div className="mt-4">
        <FormField
          label="Event types"
          hint="Choose which governance signals to send — enforcement (block / monitor / require approval), findings, and approvals."
        >
          <div className="max-h-64 space-y-4 overflow-y-auto rounded-lg border border-base-200 p-3">
            {groups.length === 0 ? (
              <p className="text-sm text-muted-foreground">Loading event catalog…</p>
            ) : (
              groups.map((group) => (
                <div key={group.id}>
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-400">
                    {group.label}
                  </p>
                  <ul className="space-y-1">
                    {group.events.map((ev) => (
                      <li key={ev.type}>
                        <label className="flex cursor-pointer items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={selectedEvents.has(ev.type)}
                            onChange={() => toggleEvent(ev.type)}
                          />
                          <span>{ev.label}</span>
                          <span className="text-xs text-muted-foreground">({ev.type})</span>
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        </FormField>
        </div>

        <div className="mt-4">
        <FormField
          label="Custom headers"
          hint="Optional. One header per line: Name: value (cannot override X-RAI-* signing headers)."
        >
          <textarea
            className="min-h-[88px] w-full rounded-md border border-base-200 bg-base-50 p-2 font-mono text-xs"
            value={headerText}
            onChange={(e) => setHeaderText(e.target.value)}
            placeholder={"Authorization: Bearer …\nX-Custom-Tenant: acme"}
          />
        </FormField>
        </div>

        <div className="mt-4">
          <Button onClick={() => void handleCreate()} disabled={saving}>
            {saving ? "Saving…" : "Create webhook"}
          </Button>
        </div>
      </Panel>

      <Panel title="Endpoints">
        {endpoints.length === 0 ? (
          <p className="text-sm text-muted-foreground">No webhook endpoints yet.</p>
        ) : (
          <ul className="divide-y divide-base-200">
            {endpoints.map((ep) => (
              <li key={ep.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div>
                  <div className="font-medium">{ep.name}</div>
                  <div className="text-xs text-muted-foreground">{ep.url}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    secret {ep.secret_prefix}… · {(ep.event_types || []).join(", ")}
                  </div>
                  {ep.custom_headers && Object.keys(ep.custom_headers).length > 0 ? (
                    <div className="mt-1 text-xs text-muted-foreground">
                      headers: {Object.keys(ep.custom_headers).join(", ")}
                    </div>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={ep.status} />
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={saving}
                    onClick={() => void handleRotate(ep.id)}
                  >
                    Rotate secret
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Recent deliveries">
        {deliveries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No deliveries yet.</p>
        ) : (
          <ul className="divide-y divide-base-200 text-sm">
            {deliveries.map((d) => (
              <li key={d.id} className="flex flex-wrap justify-between gap-2 py-2">
                <span>
                  {d.event_type} · {d.event_id.slice(0, 8)}…
                </span>
                <span className="flex items-center gap-2">
                  <StatusBadge status={d.status} />
                  <span className="text-xs text-muted-foreground">
                    attempts {d.attempt_count}
                    {d.last_error ? ` · ${d.last_error}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

    </div>
  );
}