"use client";

import { useEffect, useState } from "react";
import { SourcePreview } from "@/components/onboarding/intake/source-preview";
import { Button } from "@/components/ui/button";
import {
  IntakeApiError,
  extractSources,
  getCapabilities,
  newIdempotencyKey,
  setParsingOptions,
  type ExtractionSourceState,
  type ParsingOptions,
  type SessionDetail,
  type SourceSummary,
} from "@/lib/onboarding-api";
import { outcomeGuidance, outcomeLabel } from "@/lib/onboarding-intake";

const OPTION_REASONS = new Set(["ENCODING_ERROR", "AMBIGUOUS_DELIMITER"]);

function coverageText(state: ExtractionSourceState): string | null {
  const cov = state.coverage;
  if (!cov || cov.total == null) return null;
  const unit = cov.unit ?? "item";
  return `${cov.extracted ?? 0} of ${cov.total} ${unit}${cov.total === 1 ? "" : "s"} extracted`;
}

/**
 * Screens A and C: real per-source extraction state and concrete next steps. Worker
 * activity is reported separately from source quality ("Needs attention").
 */
export function ExtractionPanel({
  session,
  canManage,
  onSession,
  onReload,
}: {
  session: SessionDetail;
  canManage: boolean;
  onSession: (next: SessionDetail) => void;
  onReload: () => Promise<void>;
}) {
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, ParsingOptions>>({});

  const extraction = session.extraction;
  const preparation = session.preparation;
  const canExtract = session.allowed_actions.includes("extract");

  useEffect(() => {
    if (!canManage || !canExtract) return;
    let cancelled = false;
    getCapabilities()
      .then((c) => !cancelled && setFingerprint(c.parser_profile.fingerprint))
      .catch(() => !cancelled && setFingerprint(null));
    return () => {
      cancelled = true;
    };
  }, [canManage, canExtract]);

  if (!extraction || !preparation) return null;
  const byId = new Map<string, SourceSummary>(session.sources.map((s) => [s.id, s]));

  async function run() {
    if (!fingerprint || !preparation?.run_id) return;
    setBusy(true);
    setError(null);
    try {
      const result = await extractSources(session.id, {
        expectedRowVersion: session.row_version,
        inputRevision: session.input_revision,
        preparationRunId: preparation.run_id,
        parserFingerprint: fingerprint,
        key: newIdempotencyKey(),
      });
      onSession(result.session);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start extraction");
      if (e instanceof IntakeApiError && e.status === 409) await onReload();
    } finally {
      setBusy(false);
    }
  }

  async function saveOptions(source: SourceSummary) {
    const options = draft[source.id];
    if (!options || Object.keys(options).length === 0) return;
    setBusy(true);
    setError(null);
    try {
      onSession(
        await setParsingOptions(session.id, source.id, {
          options: { ...(source.parsing_options ?? {}), ...options },
          expectedRowVersion: session.row_version,
          key: newIdempotencyKey(),
        }),
      );
      setDraft((d) => ({ ...d, [source.id]: {} }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save options");
      if (e instanceof IntakeApiError && e.isConflict) await onReload();
    } finally {
      setBusy(false);
    }
  }

  const finished = extraction.state === "EXTRACTED";
  return (
    <section aria-label="Extracted sources" className="space-y-3" data-testid="extraction-panel">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink-900">Extracted sources</h2>
        {canManage && canExtract && (
          <Button disabled={busy || !fingerprint} onClick={() => void run()}>
            Extract text and tables
          </Button>
        )}
      </div>
      <p className="text-xs text-ink-500">
        Extraction reads text and tables deterministically. It works without any AI connection and
        does not interpret what your documents mean.
      </p>
      {preparation.requires_new_preparation && (
        <p className="text-sm text-ink-600">
          This preparation predates parsing options. Prepare the sources again to extract them.
        </p>
      )}
      {preparation.state !== "PREPARED" && extraction.state === "NOT_STARTED" && (
        <p className="text-sm text-ink-500">Prepare the sources first; extraction comes next.</p>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger-600">
          {error}
        </p>
      )}
      {!extraction.current && extraction.state !== "NOT_STARTED" && (
        <p className="text-sm text-ink-600" data-testid="extraction-stale">
          Inputs or options changed since this extraction. It is kept as history; prepare and
          extract the current inputs.
        </p>
      )}
      {finished && extraction.current && (
        <p className="text-sm font-medium text-ink-800" data-testid="extraction-attention">
          {extraction.attention_count > 0
            ? `Needs attention: ${extraction.attention_count} of ${extraction.sources.length} sources`
            : "All sources extracted"}
        </p>
      )}
      <ul className="divide-y divide-base-200 rounded-xl border border-base-200">
        {extraction.sources.map((state, index) => {
          const source = state.source_id ? byId.get(state.source_id) : undefined;
          const name = source?.current_revision?.display_name ?? state.label;
          const guidance = outcomeGuidance(state.outcome, state.reason);
          const coverage = coverageText(state);
          const showOptions =
            canManage &&
            source?.kind === "FILE" &&
            state.reason &&
            OPTION_REASONS.has(state.reason);
          return (
            <li key={state.source_revision_id ?? index} className="space-y-1 p-4 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-ink-900">
                  <bdi>{name}</bdi>
                </span>
                <span
                  className={
                    state.outcome === "COMPLETE" ? "text-ink-700" : "font-medium text-warning-600"
                  }
                >
                  {outcomeLabel(state.state === "FINAL" ? state.outcome : null)}
                </span>
              </div>
              {coverage && <p className="text-xs text-ink-500">{coverage}</p>}
              {state.coverage?.gaps && state.coverage.gaps.length > 0 && (
                <p className="text-xs text-ink-500">
                  Known gaps: {state.coverage.gaps.map((g) => g.code).join(", ")}
                </p>
              )}
              {guidance && <p className="text-xs text-ink-600">{guidance}</p>}
              {state.extraction_id && state.block_count > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPreview(state.extraction_id as string)}
                >
                  Preview text
                </Button>
              )}
              {showOptions && source && (
                <div className="flex flex-wrap items-end gap-2 pt-1" data-testid="options-form">
                  {state.reason === "ENCODING_ERROR" && (
                    <label className="text-xs">
                      Encoding
                      <select
                        aria-label="Encoding"
                        className="ml-1 rounded border border-base-200"
                        value={draft[source.id]?.encoding ?? ""}
                        onChange={(e) =>
                          setDraft((d) => ({
                            ...d,
                            [source.id]: {
                              ...d[source.id],
                              encoding: e.target.value as ParsingOptions["encoding"],
                            },
                          }))
                        }
                      >
                        <option value="">Choose…</option>
                        <option value="utf-8">UTF-8</option>
                        <option value="windows-1256">Windows-1256 (Arabic)</option>
                      </select>
                    </label>
                  )}
                  {state.reason === "AMBIGUOUS_DELIMITER" && (
                    <label className="text-xs">
                      Delimiter
                      <select
                        aria-label="Delimiter"
                        className="ml-1 rounded border border-base-200"
                        value={draft[source.id]?.delimiter ?? ""}
                        onChange={(e) =>
                          setDraft((d) => ({
                            ...d,
                            [source.id]: {
                              ...d[source.id],
                              delimiter: e.target.value as ParsingOptions["delimiter"],
                            },
                          }))
                        }
                      >
                        <option value="">Choose…</option>
                        <option value=",">Comma</option>
                        <option value=";">Semicolon</option>
                        <option value={"\t"}>Tab</option>
                        <option value="|">Pipe</option>
                      </select>
                    </label>
                  )}
                  <Button
                    size="sm"
                    disabled={
                      busy || !draft[source.id] || Object.keys(draft[source.id]).length === 0
                    }
                    onClick={() => void saveOptions(source)}
                  >
                    Save options
                  </Button>
                  <p className="w-full text-xs text-ink-500">
                    Your original file is kept unchanged. After saving, prepare and extract again.
                  </p>
                </div>
              )}
            </li>
          );
        })}
        {extraction.sources.length === 0 && (
          <li className="p-4 text-sm text-ink-500">No extraction has been run yet.</li>
        )}
      </ul>
      {preview && (
        <SourcePreview
          sessionId={session.id}
          extractionId={preview}
          onClose={() => setPreview(null)}
        />
      )}
    </section>
  );
}
