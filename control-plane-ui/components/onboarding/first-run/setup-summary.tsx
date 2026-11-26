"use client";

import { useEffect, useState } from "react";
import { Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SystemsResult } from "@/lib/first-run";

const apiBaseUrl = () => process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8001";

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
  onFinish,
}: {
  result: SystemsResult;
  onFinish: () => void;
}) {
  // Leaving the page loses the secrets for good.
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

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
          environment.
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
