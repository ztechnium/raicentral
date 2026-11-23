"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  IntakeApiError,
  getSession,
  newIdempotencyKey,
  toError,
  type SessionDetail,
} from "@/lib/onboarding-api";

async function fetchDeltaSummary(sessionId: string) {
  const res = await fetch(
    `/api/productization/onboarding/sessions/${sessionId}/delta/summary`,
    { credentials: "include", cache: "no-store" },
  );
  if (!res.ok) throw await toError(res);
  return res.json();
}

export function UpdateChangesPanel({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const next = await getSession(sessionId);
      setSession(next);
      if (next.session_kind === "UPDATE") {
        setSummary(await fetchDeltaSummary(sessionId));
      }
    } catch (e) {
      const api = e instanceof IntakeApiError ? e : null;
      setError(api?.message ?? "Could not load update analysis.");
    }
  }, [sessionId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (!session || session.session_kind !== "UPDATE") {
    return null;
  }

  const counts = (summary?.source_delta_counts ?? {}) as Record<string, number>;

  return (
    <section
      className="rounded-lg border bg-card p-4 space-y-3"
      aria-labelledby="update-changes-heading"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="update-changes-heading" className="text-lg font-semibold">
          Review updates
        </h2>
        <span className="text-sm text-muted-foreground">
          Status: {session.update_status ?? "SOURCE_UPDATED"}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        Compare revised sources against the accepted baseline. Withdrawn rows in a workbook never
        delete or disable live controls automatically.
      </p>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
        {Object.entries(counts).map(([key, value]) => (
          <div key={key}>
            <dt className="text-muted-foreground">{key.replace(/_/g, " ")}</dt>
            <dd className="font-medium tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" disabled={busy} onClick={() => void reload()}>
          Check for updates
        </Button>
        <Button
          type="button"
          disabled={busy || !summary?.delta_plan_id}
          onClick={async () => {
            if (!session.confirmed_baseline_id) {
              setError("Confirm a baseline before preparing review.");
              return;
            }
            setBusy(true);
            setError(null);
            try {
              await fetch(
                `/api/productization/onboarding/sessions/${sessionId}/delta/preview`,
                {
                  method: "POST",
                  credentials: "include",
                  headers: {
                    "Content-Type": "application/json",
                    "Idempotency-Key": newIdempotencyKey(),
                  },
                  body: JSON.stringify({
                    baseline_id: session.confirmed_baseline_id,
                    expected_row_version: session.row_version,
                    requested_input_revision: session.input_revision,
                  }),
                },
              );
              await reload();
            } catch (e) {
              const api = e instanceof IntakeApiError ? e : null;
              setError(api?.message ?? "Could not start change analysis.");
            } finally {
              setBusy(false);
            }
          }}
        >
          Prepare review
        </Button>
      </div>
    </section>
  );
}