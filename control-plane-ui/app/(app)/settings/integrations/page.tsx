"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { SectionHeader } from "@/components/section-header";
import { FormField } from "@/components/registry/form-field";
import { Panel } from "@/components/registry/panel";
import { StatusBadge } from "@/components/registry/status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createAlertIntegration,
  deleteAlertIntegration,
  getAlertEventGroups,
  getAlertIntegrationCatalog,
  listAlertIntegrations,
} from "@/lib/api";
import type {
  AlertEventGroup,
  AlertIntegrationProvider,
  AlertIntegrationSummary,
} from "@/lib/types";

const PROVIDER_ORDER = [
  "slack",
  "microsoft_teams",
  "discord",
  "telegram",
  "whatsapp",
  "jira",
  "trello",
  "clickup",
];

function parseHeaders(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf(":");
    if (idx <= 0) continue;
    out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return out;
}

export default function IntegrationsPage() {
  const [items, setItems] = useState<AlertIntegrationSummary[]>([]);
  const [providers, setProviders] = useState<AlertIntegrationProvider[]>([]);
  const [groups, setGroups] = useState<AlertEventGroup[]>([]);
  const [provider, setProvider] = useState("slack");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [headerText, setHeaderText] = useState("");
  const [selectedEvents, setSelectedEvents] = useState<Set<string>>(
    () => new Set(["enforcement.*", "risk.finding.created"]),
  );
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const providerMeta = useMemo(
    () => providers.find((p) => p.id === provider),
    [providers, provider],
  );

  const orderedProviders = useMemo(() => {
    const byId = new Map(providers.map((p) => [p.id, p]));
    return PROVIDER_ORDER.map((id) => byId.get(id)).filter(Boolean) as AlertIntegrationProvider[];
  }, [providers]);

  const load = useCallback(() => {
    setError(null);
    Promise.all([
      listAlertIntegrations(),
      getAlertIntegrationCatalog(),
      getAlertEventGroups(),
    ])
      .then(([list, cat, ev]) => {
        setItems(list.data.items ?? []);
        setProviders(cat.data.providers ?? []);
        setGroups(ev.data.groups ?? []);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const label = providerMeta?.label ?? provider;
    setName((prev) => (prev ? prev : `${label} alerts`));
    setConfig({});
  }, [provider, providerMeta?.label]);

  const toggleEvent = (type: string) => {
    setSelectedEvents((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const onSave = async () => {
    if (selectedEvents.size === 0) {
      setError("Select at least one event to send.");
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await createAlertIntegration({
        name: name.trim() || `${providerMeta?.label ?? provider} alerts`,
        provider,
        event_types: Array.from(selectedEvents),
        config,
        custom_headers: parseHeaders(headerText),
        enabled: true,
      });
      setMessage("Integration saved.");
      setHeaderText("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (id: string) => {
    setSaving(true);
    setError(null);
    try {
      await deleteAlertIntegration(id);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Integrations"
        subtitle="Send governance findings and enforcement signals to Slack, Teams, chat apps, and work trackers."
        actions={
          <div className="flex flex-wrap gap-3 text-sm">
            <Link href="/settings/webhooks" className="text-primary hover:underline">
              Webhooks
            </Link>
            <Link href="/settings" className="text-primary hover:underline">
              Settings
            </Link>
          </div>
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

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {orderedProviders.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setProvider(p.id)}
            className={`rounded-xl border p-4 text-left transition ${
              provider === p.id
                ? "border-accent-400 bg-accent-50 shadow-sm"
                : "border-base-200 bg-base-50 hover:border-base-300"
            }`}
          >
            <div className="font-medium">{p.label}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {items.filter((i) => i.provider === p.id).length} configured
            </div>
          </button>
        ))}
      </div>

      <Panel title={`Configure ${providerMeta?.label ?? provider}`}>
        <div className="space-y-4">
          <FormField label="Display name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </FormField>

          {(providerMeta?.config_fields ?? []).map((field) => (
            <FormField key={field.key} label={field.label}>
              <Input
                type={field.type === "secret" ? "password" : "text"}
                value={config[field.key] ?? ""}
                onChange={(e) => setConfig((c) => ({ ...c, [field.key]: e.target.value }))}
                autoComplete="off"
              />
            </FormField>
          ))}

          <FormField label="What to send">
            <div className="max-h-48 space-y-3 overflow-y-auto rounded-lg border border-base-200 p-3">
              {groups.map((group) => (
                <div key={group.id}>
                  <p className="text-xs font-semibold uppercase text-ink-400">{group.label}</p>
                  <ul className="mt-1 space-y-1">
                    {group.events.map((ev) => (
                      <li key={ev.type}>
                        <label className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={selectedEvents.has(ev.type)}
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
          </FormField>

          <FormField label="Custom headers (optional)">
            <textarea
              className="min-h-[72px] w-full rounded-md border border-base-200 bg-base-50 p-2 font-mono text-xs"
              value={headerText}
              onChange={(e) => setHeaderText(e.target.value)}
            />
          </FormField>

          <Button disabled={saving} onClick={() => void onSave()}>
            {saving ? "Saving…" : "Save integration"}
          </Button>
        </div>
      </Panel>

      <Panel title="Active integrations">
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No integrations configured yet.</p>
        ) : (
          <ul className="divide-y divide-base-200">
            {items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div>
                  <div className="font-medium">
                    {item.name}{" "}
                    <span className="text-xs font-normal text-muted-foreground">
                      ({item.provider_label})
                    </span>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Events: {(item.event_types || []).join(", ")}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={item.enabled ? "ACTIVE" : "DISABLED"} />
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={saving}
                    onClick={() => void onDelete(item.id)}
                  >
                    Remove
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}