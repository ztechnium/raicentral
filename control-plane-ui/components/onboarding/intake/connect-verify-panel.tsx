"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  getRuntimeContext,
  prepareActivationPlan,
  saveRuntimeContext,
  type RuntimeContextView,
  type SessionDetail,
} from "@/lib/onboarding-api";

/** Step 07: connect and verify tracks are independent from configuration apply. */
export function ConnectVerifyPanel({
  session,
  canManage,
  onReload: _onReload,
}: {
  session: SessionDetail;
  canManage: boolean;
  onReload: () => Promise<void>;
}) {
  void _onReload;
  const [ctx, setCtx] = useState<RuntimeContextView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setCtx(await getRuntimeContext(session.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load runtime context");
    }
  }, [session.id]);

  useEffect(() => {
    void refresh();
  }, [refresh, session.row_version]);

  const saveSelection = async () => {
    if (!ctx || ctx.eligibility !== "READY" || !ctx.application) return;
    const ai = ctx.selectable.ai_systems[0]?.id;
  const rules = ctx.selectable.content_rules.map((r) => r.id);
    if (!ai) {
      setError("No AI system binding available from the last application.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const next = await saveRuntimeContext(session.id, {
        expected_row_version: session.row_version,
        delegation_mode: "NONE",
        selected: {
          application_id: ctx.application.id,
          ai_system_id: ai,
          controls: { content_rule_ids: rules },
        },
      });
      setCtx(next);
      setHint("Runtime selection saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save selection");
    } finally {
      setBusy(false);
    }
  };

  const prepareActivate = async () => {
    if (!ctx?.context) return;
    setBusy(true);
    setError(null);
    try {
      const plan = await prepareActivationPlan(session.id, {
        expected_row_version: ctx.context.row_version,
        expected_selection_digest: ctx.context.selection_digest,
      });
      setHint(`Activation plan ${plan.id.slice(0, 8)} prepared (controls remain staged until worker runs).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not prepare activation");
    } finally {
      setBusy(false);
    }
  };

  if (!ctx) {
    return <p className="text-sm text-ink-500">Loading connect &amp; verify…</p>;
  }

  return (
    <section className="space-y-3 rounded-xl border border-base-200 p-4">
      <h3 className="text-sm font-semibold text-ink-900">Connect &amp; verify</h3>
      <p className="text-xs text-ink-500">
        Eligibility: <strong>{ctx.eligibility}</strong>
        {ctx.eligibility !== "READY" && ctx.reasons.length > 0 && (
          <span> — {ctx.reasons.join("; ")}</span>
        )}
      </p>
      {error && (
        <p className="text-sm text-danger-600" role="alert">
          {error}
        </p>
      )}
      {hint && <p className="text-sm text-ink-600">{hint}</p>}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!canManage || busy || ctx.eligibility !== "READY"}
          onClick={() => void saveSelection()}
        >
          Save runtime selection
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={!canManage || busy || !ctx.context}
          onClick={() => void prepareActivate()}
        >
          Prepare activation plan
        </Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => void refresh()}>
          Refresh
        </Button>
      </div>
      {ctx.context && (
        <p className="text-xs text-ink-500">
          Context v{ctx.context.row_version} · digest {ctx.context.selection_digest.slice(0, 12)}…
        </p>
      )}
    </section>
  );
}