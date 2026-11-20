"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  getCitation,
  listBlocks,
  type BlocksPage,
  type Citation,
  type ExtractedBlock,
} from "@/lib/onboarding-api";
import { codePointLength, describeLocator, highlightParts } from "@/lib/onboarding-intake";

/**
 * Paginated preview of exact stored extraction output plus citation resolution.
 * This is extracted text, not a rendering of the original page or document layout.
 */
export function SourcePreview({
  sessionId,
  extractionId,
  onClose,
}: {
  sessionId: string;
  extractionId: string;
  onClose: () => void;
}) {
  const [page, setPage] = useState<BlocksPage | null>(null);
  const [blocks, setBlocks] = useState<ExtractedBlock[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [citation, setCitation] = useState<Citation | null>(null);
  const [range, setRange] = useState<Record<string, { start: string; end: string }>>({});

  const load = useCallback(
    async (after: number) => {
      setLoading(true);
      setError(null);
      try {
        const next = await listBlocks(sessionId, extractionId, after);
        setPage(next);
        setBlocks((prev) => (after === -1 ? next.items : [...prev, ...next.items]));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load extracted content");
      } finally {
        setLoading(false);
      }
    },
    [sessionId, extractionId],
  );

  useEffect(() => {
    setBlocks([]);
    setCitation(null);
    void load(-1);
  }, [load]);

  async function cite(block: ExtractedBlock) {
    const r = range[block.id];
    const length = codePointLength(block.text);
    const start = r?.start ? Number(r.start) : 0;
    const end = r?.end ? Number(r.end) : length;
    try {
      setError(null);
      setCitation(await getCitation(sessionId, block.id, { start, end }));
    } catch (e) {
      setCitation(null);
      setError(e instanceof Error ? e.message : "Could not resolve the citation");
    }
  }

  const ext = page?.extraction;
  return (
    <section
      aria-label="Extracted content"
      className="space-y-3 rounded-xl border border-base-200 bg-base-50 p-4"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink-900">Extracted text preview</h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>
      <p className="text-xs text-ink-500">
        This is the exact text the parser stored, not a rendering of the original document. Offsets
        count Unicode characters (code points) in the stored block text.
      </p>
      {ext && (
        <dl
          className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-600"
          data-testid="extraction-details"
        >
          <dt>Source revision</dt>
          <dd>
            {ext.revision_number ?? ""} · digest <bdi>{ext.original_sha256.slice(0, 12)}</bdi>
          </dd>
          <dt>Extractor</dt>
          <dd>{ext.extractor_build}</dd>
          <dt>Normalization</dt>
          <dd>{ext.normalization || "none"}</dd>
          <dt>Input</dt>
          <dd>{ext.is_current_input ? "Current input" : "Historical (not current input)"}</dd>
          {ext.warnings && ext.warnings.length > 0 && (
            <>
              <dt>Warnings</dt>
              <dd>{ext.warnings.join(", ")}</dd>
            </>
          )}
        </dl>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger-600">
          {error}
        </p>
      )}
      <ul className="space-y-2">
        {blocks.map((block) => {
          const length = codePointLength(block.text);
          const formula = block.cell_data?.formula as string | undefined;
          return (
            <li key={block.id} className="rounded-lg border border-base-200 p-3 text-sm">
              <p className="text-xs text-ink-500">
                <bdi>{describeLocator(block.locator)}</bdi>
              </p>
              <div dir="auto" className="whitespace-pre-wrap text-ink-900">
                {block.text}
              </div>
              {formula && (
                <p className="mt-1 text-xs text-ink-500">
                  Formula <bdi>{formula}</bdi> · cached value{" "}
                  <bdi>
                    {block.cell_data?.cache_state === "MISSING"
                      ? "missing"
                      : String(block.cell_data?.cached_value)}
                  </bdi>{" "}
                  ({String(block.cell_data?.cache_state)})
                </p>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <label>
                  from
                  <input
                    aria-label={`Start offset for block ${block.ordinal}`}
                    className="mx-1 w-16 rounded border border-base-200 px-1"
                    inputMode="numeric"
                    placeholder="0"
                    value={range[block.id]?.start ?? ""}
                    onChange={(e) =>
                      setRange((r) => ({
                        ...r,
                        [block.id]: { start: e.target.value, end: r[block.id]?.end ?? "" },
                      }))
                    }
                  />
                </label>
                <label>
                  to
                  <input
                    aria-label={`End offset for block ${block.ordinal}`}
                    className="mx-1 w-16 rounded border border-base-200 px-1"
                    inputMode="numeric"
                    placeholder={String(length)}
                    value={range[block.id]?.end ?? ""}
                    onChange={(e) =>
                      setRange((r) => ({
                        ...r,
                        [block.id]: { start: r[block.id]?.start ?? "", end: e.target.value },
                      }))
                    }
                  />
                </label>
                <Button variant="outline" size="sm" onClick={() => void cite(block)}>
                  Cite excerpt
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
      {loading && <p className="text-sm text-ink-500">Loading…</p>}
      {page?.next_after != null && !loading && (
        <Button variant="outline" onClick={() => void load(page.next_after as number)}>
          Load more
        </Button>
      )}
      {citation && <CitationView citation={citation} />}
    </section>
  );
}

function CitationView({ citation }: { citation: Citation }) {
  const parts = highlightParts(
    citation.block_text,
    citation.text_range.start,
    citation.text_range.end,
  );
  return (
    <div
      className="space-y-2 rounded-lg border border-accent-600 p-3 text-sm"
      data-testid="citation"
    >
      <p className="font-semibold text-ink-900">
        Citation{" "}
        {citation.historical && (
          <span className="ml-2 rounded bg-base-100 px-1 text-xs font-normal text-ink-600">
            Historical input
          </span>
        )}
      </p>
      <p className="text-xs text-ink-600">
        <bdi>{citation.display_name}</bdi> · revision {citation.revision_number} · digest{" "}
        <bdi>{citation.source_sha256.slice(0, 12)}</bdi> ·{" "}
        <bdi>{describeLocator(citation.locator)}</bdi>
      </p>
      <p className="text-xs text-ink-600">
        Characters {citation.text_range.start}–{citation.text_range.end} (Unicode code points) of
        the stored block text. This is an excerpt of extracted text, not of the original file bytes.
      </p>
      <blockquote
        dir="auto"
        className="whitespace-pre-wrap border-l-2 border-base-200 pl-3 text-ink-900"
      >
        {parts.before}
        <mark data-testid="citation-excerpt">{parts.match}</mark>
        {parts.after}
      </blockquote>
      {!citation.integrity_ok && (
        <p role="alert" className="text-xs text-danger-600">
          The stored text no longer matches its recorded digest.
        </p>
      )}
    </div>
  );
}
