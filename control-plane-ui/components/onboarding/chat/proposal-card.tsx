"use client";

import type { ExecutedCall, ProposedAction } from "@/lib/assistant-api";

/** What the assistant wants to change (before confirmation) or what happened (after). */
export function ProposalCard({
  proposal,
  executed,
}: {
  proposal?: ProposedAction;
  executed?: ExecutedCall[];
}) {
  return (
    <div className="space-y-2 pt-1" data-testid="assistant-proposal">
      {proposal && (
        <>
          <ol className="space-y-2">
            {proposal.calls.map((call, i) => (
              <li key={i} className="rounded-lg border border-base-200 p-2 text-xs">
                <p className="font-medium text-ink-900">
                  {i + 1}. {call.tool.replace(/_/g, " ")}{" "}
                  <span className="font-mono font-normal text-ink-500">
                    {call.method} {call.path}
                  </span>
                </p>
                {call.body && (
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-ink-600">
                    {JSON.stringify(call.body, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ol>
          {!executed && (
            <p className="text-xs text-ink-500">
              Nothing has changed yet. Say “apply” to run this, or tell me what to adjust.
            </p>
          )}
        </>
      )}
      {executed && (
        <ul className="space-y-1 text-xs">
          {executed.map((r, i) => (
            <li key={i} className={r.ok ? "text-ink-800" : "text-danger-600"}>
              {r.ok ? "✓" : "✕"} {r.tool.replace(/_/g, " ")} · HTTP {r.status}
              {!r.ok && <span className="block text-ink-600">{errorDetail(r.data)}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function errorDetail(data: unknown): string {
  if (data && typeof data === "object") {
    const d = data as Record<string, unknown>;
    const err = (d.error ?? d.detail ?? d.message) as unknown;
    if (typeof err === "string") return err;
    if (err && typeof err === "object" && typeof (err as { message?: unknown }).message === "string") {
      return (err as { message: string }).message;
    }
  }
  return "The request was rejected.";
}
