"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  fetchRevisionContent,
  fetchRevisionText,
  listRevisions,
  type RevisionHistory,
} from "@/lib/onboarding-api";
import { formatBytes } from "@/lib/onboarding-intake";

/**
 * Immutable revisions of one source. Text is rendered as text (never HTML);
 * files download as exact original bytes. No rollback in this step.
 */
export function SourceHistory({
  sessionId,
  sourceId,
  onClose,
}: {
  sessionId: string;
  sourceId: string;
  onClose: () => void;
}) {
  const [history, setHistory] = useState<RevisionHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState<{ id: string; body: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listRevisions(sessionId, sourceId)
      .then((h) => !cancelled && setHistory(h))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : "Failed"));
    return () => {
      cancelled = true;
    };
  }, [sessionId, sourceId]);

  async function download(revisionId: string, name: string) {
    try {
      const blob = await fetchRevisionContent(sessionId, sourceId, revisionId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    }
  }

  async function view(revisionId: string) {
    try {
      setText({ id: revisionId, body: await fetchRevisionText(sessionId, sourceId, revisionId) });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load the saved text");
    }
  }

  return (
    <section
      className="space-y-3 rounded-xl border border-base-200 bg-base-50 p-4"
      aria-label="Revision history"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink-900">Revision history</h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>
      <p className="text-xs text-ink-500">
        Earlier revisions are kept exactly as saved. Excluding a source or archiving a session does
        not erase them.
      </p>
      {error && (
        <p role="alert" className="text-sm text-danger-600">
          {error}
        </p>
      )}
      {!history && !error && <p className="text-sm text-ink-500">Loading history…</p>}
      {history && (
        <ul className="space-y-2">
          {history.items.map((rev) => (
            <li key={rev.id} className="rounded-lg border border-base-200 p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-ink-900">
                  Revision {rev.revision_number}
                  {rev.current && <span className="ml-2 text-xs text-accent-600">Current</span>}
                  {!history.included && rev.current && (
                    <span className="ml-2 text-xs text-ink-500">Excluded</span>
                  )}
                </span>
                {history.kind === "TEXT" ? (
                  <Button variant="outline" size="sm" onClick={() => void view(rev.id)}>
                    View text
                  </Button>
                ) : (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void download(rev.id, rev.display_name ?? "source")}
                  >
                    Download original
                  </Button>
                )}
              </div>
              <p className="text-xs text-ink-500">
                {rev.display_name} · {formatBytes(rev.byte_length)} · {rev.media_family} ·{" "}
                {rev.received_at ? new Date(rev.received_at).toLocaleString() : ""} · by{" "}
                {rev.created_by}
              </p>
              {text?.id === rev.id && (
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-base-100 p-2 text-xs text-ink-800">
                  {text.body}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
