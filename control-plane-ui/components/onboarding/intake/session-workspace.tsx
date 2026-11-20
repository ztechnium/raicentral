"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { ExtractionPanel } from "@/components/onboarding/intake/extraction-panel";
import { SourceHistory } from "@/components/onboarding/intake/source-history";
import {
  IntakeApiError,
  cancelRun,
  fetchRevisionText,
  getSession,
  newIdempotencyKey,
  prepareSources,
  retryRun,
  saveTextSource,
  setArchived,
  setSourceIncluded,
  uploadFileSource,
  type SessionDetail,
  type SourceSummary,
} from "@/lib/onboarding-api";
import {
  SUPPORTED_ACCEPT,
  canEditInputs,
  formatBytes,
  isActive,
  pollDelay,
  runStatus,
  useIntakeAccess,
  viewLabel,
} from "@/lib/onboarding-intake";

interface UploadItem {
  id: string;
  name: string;
  progress: number;
  status: "uploading" | "saved" | "failed";
  error?: string;
}

type Load =
  | { status: "loading" }
  | { status: "error"; message: string; forbidden: boolean }
  | { status: "ready" };

export function SessionWorkspace({ sessionId }: { sessionId: string }) {
  const access = useIntakeAccess();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [promptDraft, setPromptDraft] = useState("");
  const [savedPrompt, setSavedPrompt] = useState("");
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [conflict, setConflict] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyFor, setHistoryFor] = useState<string | null>(null);

  const sessionRef = useRef<SessionDetail | null>(null);
  const keys = useRef<Record<string, string>>({});
  const loadedPromptRevision = useRef<string | null>(null);
  const replaceTarget = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const apply = useCallback((next: SessionDetail) => {
    sessionRef.current = next;
    setSession(next);
  }, []);

  // Server state is the only authority; local state holds unsaved edits and progress.
  const reload = useCallback(async () => {
    try {
      apply(await getSession(sessionId));
      setLoad({ status: "ready" });
    } catch (error) {
      const api = error instanceof IntakeApiError ? error : null;
      setLoad({
        status: "error",
        message: api?.message ?? "Could not load this session.",
        forbidden: api?.status === 403,
      });
    }
  }, [apply, sessionId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Idempotency keys are per user action; they reset once the inputs change.
  const inputRevision = session?.input_revision;
  useEffect(() => {
    keys.current = {};
  }, [inputRevision]);

  const promptSource: SourceSummary | undefined = session?.sources.find(
    (s) => s.role === "USER_PROMPT" && s.included,
  );
  const promptRevisionId = promptSource?.current_revision?.id ?? null;

  // Load the saved prompt text whenever its saved revision changes.
  useEffect(() => {
    if (!access.canContent || !session) return;
    if (!promptSource || !promptRevisionId) {
      loadedPromptRevision.current = null;
      return;
    }
    if (loadedPromptRevision.current === promptRevisionId) return;
    let cancelled = false;
    loadedPromptRevision.current = promptRevisionId;
    fetchRevisionText(sessionId, promptSource.id, promptRevisionId)
      .then((body) => {
        if (cancelled) return;
        setSavedPrompt((prevSaved) => {
          // Never clobber unsaved local edits.
          setPromptDraft((draft) => (draft === prevSaved ? body : draft));
          return body;
        });
      })
      .catch(() => {
        loadedPromptRevision.current = null;
      });
    return () => {
      cancelled = true;
    };
  }, [access.canContent, promptRevisionId, promptSource, session, sessionId]);

  // Poll the server while a run is active; bounded backoff, cleaned up on unmount.
  const active = isActive(session);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await getSession(sessionId);
        if (!cancelled) apply(next);
      } catch {
        // transient: keep polling with backoff
      }
      attempt += 1;
      if (!cancelled) timer = setTimeout(() => void tick(), pollDelay(attempt));
    };
    timer = setTimeout(() => void tick(), pollDelay(0));
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [active, apply, sessionId]);

  const dirty = promptDraft !== savedPrompt;
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setActionError(null);
    try {
      const result = await fn();
      setConflict(false);
      return result;
    } catch (error) {
      if (error instanceof IntakeApiError && error.isConflict) {
        // Keep the user's unsaved text; show the fresh server state; ask to reconcile.
        setConflict(true);
        await reload();
      } else {
        setActionError(error instanceof Error ? error.message : "Something went wrong.");
        if (error instanceof IntakeApiError && error.status === 409) await reload();
      }
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  function keyFor(name: string): string {
    keys.current[name] ??= newIdempotencyKey();
    return keys.current[name];
  }

  async function savePrompt() {
    const current = sessionRef.current;
    if (!current) return;
    const result = await guarded(() =>
      saveTextSource(sessionId, {
        role: "USER_PROMPT",
        text: promptDraft,
        sourceId: promptSource?.id,
        expectedRowVersion: current.row_version,
        key: keyFor(`prompt:${promptDraft}`),
      }),
    );
    if (result) {
      loadedPromptRevision.current =
        result.session.sources.find((s) => s.id === result.source_id)?.current_revision?.id ?? null;
      setSavedPrompt(promptDraft);
      apply(result.session);
    }
  }

  async function uploadFiles(files: File[], replaceSourceId?: string) {
    for (const file of files) {
      const id = `${file.name}-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setUploads((u) => [...u, { id, name: file.name, progress: 0, status: "uploading" }]);
      const current = sessionRef.current;
      if (!current) return;
      try {
        const result = await uploadFileSource(sessionId, {
          file,
          sourceId: replaceSourceId,
          expectedRowVersion: current.row_version,
          key: newIdempotencyKey(),
          onProgress: (p) =>
            setUploads((u) => u.map((x) => (x.id === id ? { ...x, progress: p } : x))),
        });
        apply(result.session);
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, progress: 1, status: "saved" } : x)));
      } catch (error) {
        const message = error instanceof Error ? error.message : "Upload failed";
        setUploads((u) =>
          u.map((x) => (x.id === id ? { ...x, status: "failed", error: message } : x)),
        );
        if (error instanceof IntakeApiError && error.isConflict) {
          setConflict(true);
          await reload();
        }
      }
    }
  }

  async function toggleIncluded(source: SourceSummary) {
    const current = sessionRef.current;
    if (!current) return;
    const result = await guarded(() =>
      setSourceIncluded(sessionId, source.id, !source.included, current.row_version),
    );
    if (result) apply(result);
  }

  async function prepare() {
    const current = sessionRef.current;
    if (!current) return;
    const result = await guarded(() =>
      prepareSources(sessionId, {
        expectedRowVersion: current.row_version,
        inputRevision: current.input_revision,
        key: keyFor("prepare"),
      }),
    );
    if (result) apply(result.session);
  }

  async function retry() {
    const current = sessionRef.current;
    if (!current?.current_run) return;
    const run = current.current_run;
    const result = await guarded(() =>
      retryRun(sessionId, run.id, {
        expectedRowVersion: current.row_version,
        inputRevision: current.input_revision,
        key: keyFor(`retry:${run.id}`),
      }),
    );
    if (result) apply(result.session);
  }

  async function cancel() {
    const run = sessionRef.current?.current_run;
    if (!run) return;
    const result = await guarded(() => cancelRun(sessionId, run.id));
    if (result) apply(result.session);
  }

  async function toggleArchive(archive: boolean) {
    const current = sessionRef.current;
    if (!current) return;
    const result = await guarded(() => setArchived(sessionId, archive, current.row_version));
    if (result) apply(result);
  }

  if (load.status === "loading") {
    return <p className="text-sm text-ink-500">Loading session…</p>;
  }
  if (load.status === "error") {
    return (
      <div role="alert" className="space-y-2 rounded-xl border border-base-200 p-4 text-sm">
        <p className="text-ink-700">
          {load.forbidden ? "You do not have access to this session." : load.message}
        </p>
        {!load.forbidden && <Button onClick={() => void reload()}>Retry</Button>}
        <Link href="/app/onboarding/sessions">
          <Button variant="ghost">Back to sessions</Button>
        </Link>
      </div>
    );
  }
  if (!session) return null;

  const canEdit = access.canManage && canEditInputs(session);
  const status = runStatus(session.current_run);
  const showStatus = status && session.current_run?.input_revision === session.input_revision;
  const actions = session.allowed_actions;

  return (
    <div className="space-y-6" data-testid="intake-workspace">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <Link href="/app/onboarding/sessions" className="text-xs text-accent-600">
            ← All sessions
          </Link>
          <h1 className="text-lg font-semibold text-ink-900">{session.title ?? session.label}</h1>
          <p className="text-xs text-ink-500" data-testid="saved-indicator">
            Saved on the server · {viewLabel(session.view)} · input revision{" "}
            {session.input_revision}
          </p>
        </div>
        <div className="flex gap-2">
          {actions.includes("archive") && (
            <Button variant="outline" disabled={busy} onClick={() => void toggleArchive(true)}>
              Archive session
            </Button>
          )}
          {actions.includes("restore") && (
            <Button variant="outline" disabled={busy} onClick={() => void toggleArchive(false)}>
              Restore session
            </Button>
          )}
        </div>
      </header>

      {conflict && (
        <div role="alert" className="rounded-lg border border-warning-600 p-3 text-sm text-ink-800">
          This session changed since you opened it (a colleague may have saved). We refreshed the
          saved version below and kept your unsaved text. Review it, then save again if you still
          want your change.
        </div>
      )}
      {actionError && (
        <p role="alert" className="text-sm text-danger-600">
          {actionError}
        </p>
      )}

      {showStatus && status && (
        <section
          aria-label="Preparation status"
          className="space-y-2 rounded-xl border border-base-200 bg-base-50 p-4"
          data-testid="preparation-status"
        >
          <h2 className="text-sm font-semibold text-ink-900">{status.title}</h2>
          <p className="text-sm text-ink-600">{status.detail}</p>
          {session.view === "READY_FOR_ANALYSIS" && session.current_run && (
            <p className="text-xs text-ink-500">
              Prepared input revision {session.current_run.input_revision}. Analysis is not
              available yet.
            </p>
          )}
          <div className="flex gap-2">
            {actions.includes("cancel") && (
              <Button variant="outline" disabled={busy} onClick={() => void cancel()}>
                Cancel preparation
              </Button>
            )}
            {actions.includes("retry") && (
              <Button disabled={busy} onClick={() => void retry()}>
                Retry preparation
              </Button>
            )}
          </div>
        </section>
      )}

      {!access.canContent ? (
        <section className="rounded-xl border border-base-200 p-4 text-sm text-ink-600">
          <p>
            {session.source_count} source{session.source_count === 1 ? "" : "s"} saved. Source
            names, text and revisions need source access.
          </p>
          <ul className="mt-2 space-y-1 text-xs text-ink-500">
            {session.sources.map((s) => (
              <li key={s.id}>
                {s.label} · {s.current_revision ? formatBytes(s.current_revision.byte_length) : ""}
                {s.included ? "" : " · excluded"}
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <>
          {isActive(session) && (
            <p className="text-sm text-ink-500">
              Inputs are locked while preparation is active. Cancel preparation to edit.
            </p>
          )}

          <section className="space-y-2" aria-label="Prompt">
            <h2 className="text-sm font-semibold text-ink-900">Prompt</h2>
            <textarea
              className="min-h-32 w-full rounded-md border border-base-200 bg-base-50 p-3 text-sm text-ink-900"
              aria-label="Prompt text"
              value={promptDraft}
              disabled={!canEdit}
              onChange={(e) => setPromptDraft(e.target.value)}
              placeholder="Describe what to onboard. This is saved only when you press Save prompt."
            />
            <div className="flex items-center gap-3">
              <Button
                disabled={!canEdit || busy || !dirty || !promptDraft.trim()}
                onClick={() => void savePrompt()}
              >
                Save prompt
              </Button>
              <span className="text-xs text-ink-500" data-testid="prompt-state">
                {dirty ? "Unsaved changes" : promptSource ? "Saved" : "Nothing saved yet"}
              </span>
            </div>
          </section>

          <section className="space-y-3" aria-label="Files">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-ink-900">Files</h2>
              <div className="text-sm">
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  hidden
                  accept={SUPPORTED_ACCEPT}
                  data-testid="file-input"
                  onChange={(e) => {
                    const files = Array.from(e.target.files ?? []);
                    const target = replaceTarget.current ?? undefined;
                    replaceTarget.current = null;
                    e.target.value = "";
                    if (files.length) void uploadFiles(files, target);
                  }}
                />
                <Button
                  variant="outline"
                  disabled={!canEdit}
                  onClick={() => {
                    replaceTarget.current = null;
                    fileInput.current?.click();
                  }}
                >
                  Add files
                </Button>
              </div>
            </div>
            <p className="text-xs text-ink-500">
              PDF, DOCX, XLSX, CSV, TXT or Markdown. Files are saved as-is; nothing is analyzed, and
              no catalog or AI-system records are created. For structured workbook imports use the
              separate Catalog import.
            </p>

            {uploads.length > 0 && (
              <ul className="space-y-1 text-sm" aria-label="Uploads">
                {uploads.map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-3">
                    <span className="truncate">{u.name}</span>
                    <span
                      className={u.status === "failed" ? "text-danger-600" : "text-ink-500"}
                      role={u.status === "failed" ? "alert" : undefined}
                    >
                      {u.status === "uploading" && `Uploading ${Math.round(u.progress * 100)}%`}
                      {u.status === "saved" && "Saved"}
                      {u.status === "failed" && `Failed: ${u.error}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            <ul className="divide-y divide-base-200 rounded-xl border border-base-200">
              {session.sources.length === 0 && (
                <li className="p-4 text-sm text-ink-500">No sources saved yet.</li>
              )}
              {session.sources.map((source) => {
                const rev = source.current_revision;
                return (
                  <li key={source.id} className="space-y-1 p-4 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium text-ink-900">
                        {rev?.display_name ?? source.label}
                        {source.role === "USER_PROMPT" && (
                          <span className="ml-2 text-xs text-ink-500">Prompt</span>
                        )}
                        {!source.included && (
                          <span className="ml-2 text-xs text-ink-500">Excluded</span>
                        )}
                      </span>
                      <span className="flex gap-2">
                        <Button variant="ghost" size="sm" onClick={() => setHistoryFor(source.id)}>
                          Revision history
                        </Button>
                        {source.kind === "FILE" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={!canEdit}
                            onClick={() => {
                              replaceTarget.current = source.id;
                              fileInput.current?.click();
                            }}
                          >
                            Replace
                          </Button>
                        )}
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={!canEdit || busy}
                          onClick={() => void toggleIncluded(source)}
                        >
                          {source.included ? "Exclude" : "Include"}
                        </Button>
                      </span>
                    </div>
                    {rev && (
                      <p className="text-xs text-ink-500">
                        {rev.media_family} · {formatBytes(rev.byte_length)} · revision{" "}
                        {rev.revision_number} · saved{" "}
                        {rev.received_at ? new Date(rev.received_at).toLocaleString() : ""}
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
            {historyFor && (
              <SourceHistory
                sessionId={sessionId}
                sourceId={historyFor}
                onClose={() => setHistoryFor(null)}
              />
            )}
          </section>

          <ExtractionPanel
            session={session}
            canManage={access.canManage}
            onSession={apply}
            onReload={reload}
          />

          {actions.includes("prepare") && (
            <div className="space-y-1">
              <Button disabled={busy || dirty} onClick={() => void prepare()}>
                Prepare sources
              </Button>
              {dirty && (
                <p className="text-xs text-ink-500">Save your prompt before preparing sources.</p>
              )}
              <p className="text-xs text-ink-500">
                Checks that your saved files and prompt are stored, readable and unchanged. It does
                not interpret their content.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
