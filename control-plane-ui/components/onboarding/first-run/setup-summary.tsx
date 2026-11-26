"use client";

import { useState } from "react";
import { Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SystemsResult } from "@/lib/first-run";
import type { CreatedRule } from "@/lib/first-run-policies";

const apiBaseUrl = () => process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

const FAMILY_LABELS = { content: "Content", tool: "Tool", budget: "Budget" };

function CopyRow({ label, value, secret }: { label: string; value: string; secret?: boolean }) {
  const [shown, setShown] = useState(!secret);
  const [note, setNote] = useState<string | null>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setNote("Copied");
    } catch {
      // Clipboard needs HTTPS or localhost: show the value for manual copying.
      setShown(true);
      setNote("Select and copy manually");
    }
  }

  return (
    <div className="grid items-center gap-2 sm:grid-cols-[8rem_1fr_auto]">
      <span className="text-xs text-ink-400">{label}</span>
      <code className="break-all rounded-md border border-base-200 bg-surface px-2 py-1.5 font-mono text-xs text-ink-900">
        {shown ? value : "•".repeat(32)}
      </code>
      <span className="flex items-center gap-1">
        {secret ? (
          <Button
            size="sm"
            variant="ghost"
            aria-label={`${shown ? "Hide" : "Show"} ${label}`}
            onClick={() => setShown((v) => !v)}
          >
            {shown ? "Hide" : "Show"}
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="secondary"
          aria-label={`Copy ${label}`}
          onClick={() => void copy()}
        >
          <Copy className="size-3.5" aria-hidden />
          Copy
        </Button>
        <span role="status" className="text-xs text-ink-500">
          {note}
        </span>
      </span>
    </div>
  );
}

export function SetupSummary({
  result,
  rules,
  onFinish,
}: {
  result: SystemsResult;
  rules: CreatedRule[];
  onFinish: () => void;
}) {
  const rulesOf = (systemId: string) => rules.filter((r) => r.ai_system_id === systemId);

  function download() {
    const payload = {
      base_url: apiBaseUrl(),
      environment: result.environment,
      systems: result.systems.map((s) => ({
        ai_system: s.name,
        agent: s.agent.name,
        version: s.version.label,
        runtime_identity: s.runtime_identity.identity_key,
        integration_id: s.integration_id,
        agent_key: s.credential.secret,
        policies: rulesOf(s.ai_system_id).map((r) => ({
          type: r.family,
          title: r.title,
          active: r.enabled,
        })),
      })),
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "rai-credentials.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  const count = result.systems.length;

  return (
    <div className="space-y-4">
      <div
        role="alert"
        className="rounded-lg border border-warn-600/30 bg-warn-50 p-4 text-sm text-warn-700"
      >
        <strong>Save the credentials now.</strong> They are shown only once. If you lose one, rotate
        it under Runtime identities. Keep them out of source control.
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-500">
          Created {count} AI {count === 1 ? "system" : "systems"} in the {result.environment}{" "}
          environment, with {rules.length} policy {rules.length === 1 ? "rule" : "rules"}.
        </p>
        <Button variant="secondary" onClick={download}>
          <Download className="size-4" aria-hidden />
          Download all
        </Button>
      </div>
      <CopyRow label="API base URL" value={apiBaseUrl()} />
      {result.systems.map((system) => (
        <section
          key={system.ai_system_id}
          aria-label={system.name}
          className="space-y-3 rounded-xl border border-base-200 bg-base-50 p-4"
        >
          <div>
            <h2 className="text-sm font-semibold text-ink-900">{system.name}</h2>
            <p className="text-xs text-ink-500">{system.version.label}</p>
            <p className="font-mono text-xs text-ink-500">
              {system.models.map((m) => m.model_name).join(", ")}
            </p>
          </div>
          <CopyRow label="Integration ID" value={system.integration_id} />
          <CopyRow label="Agent key" value={system.credential.secret} secret />
          <div className="space-y-1">
            <h3 className="text-xs font-semibold text-ink-600">Policies</h3>
            {rulesOf(system.ai_system_id).length ? (
              <ul className="divide-y divide-base-200 rounded-md border border-base-200">
                {rulesOf(system.ai_system_id).map((rule) => (
                  <li
                    key={`${rule.family}-${rule.rule_id}`}
                    className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs"
                  >
                    <span className="w-16 text-ink-400">{FAMILY_LABELS[rule.family]}</span>
                    <span className="text-ink-900">{rule.title}</span>
                    <span
                      className={`ml-auto font-semibold ${rule.enabled ? "text-accent-700" : "text-ink-400"}`}
                    >
                      {rule.enabled ? "Active" : "Off"}
                      {rule.status === "existing" ? " · already existed" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-ink-500">No rules of its own yet.</p>
            )}
          </div>
        </section>
      ))}
      <div className="flex justify-end">
        <Button size="lg" onClick={onFinish}>
          I saved the credentials — go to dashboard
        </Button>
      </div>
    </div>
  );
}
