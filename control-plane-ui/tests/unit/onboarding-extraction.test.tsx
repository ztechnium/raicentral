import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests: they prove client behavior and the exact API calls made, not
// backend enforcement (that is covered by tests/onboarding).

const api = vi.hoisted(() => ({
  getCapabilities: vi.fn(),
  extractSources: vi.fn(),
  setParsingOptions: vi.fn(),
  listBlocks: vi.fn(),
  getCitation: vi.fn(),
  newIdempotencyKey: vi.fn(() => "key-fixed-0001"),
}));
vi.mock("@/lib/onboarding-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/onboarding-api")>("@/lib/onboarding-api");
  return { ...actual, ...api };
});
vi.mock("@/lib/hooks/use-auth", () => ({
  usePermissions: () => ({ tenant: { permissions: [] }, isLoading: false }),
}));

import { ExtractionPanel } from "@/components/onboarding/intake/extraction-panel";
import { SourcePreview } from "@/components/onboarding/intake/source-preview";
import type {
  ExtractionSourceState,
  RunSummary,
  SessionDetail,
  SourceSummary,
} from "@/lib/onboarding-api";
import {
  codePointLength,
  codePointSlice,
  describeLocator,
  highlightParts,
  outcomeGuidance,
  outcomeLabel,
  resolveIntakeAccess,
  runStatus,
} from "@/lib/onboarding-intake";

function source(id: string, name: string, kind: "FILE" | "TEXT" = "FILE"): SourceSummary {
  return {
    id,
    label: name,
    kind,
    role: "SUPPORTING_MATERIAL",
    included: true,
    created_at: null,
    parsing_options: {},
    parsing_options_version: 1,
    current_revision: {
      id: `rev-${id}`,
      revision_number: 1,
      media_family: "CSV",
      byte_length: 10,
      received_at: null,
      display_name: name,
    },
  };
}

function state(over: Partial<ExtractionSourceState>): ExtractionSourceState {
  return {
    label: "Source 1",
    state: "FINAL",
    outcome: "COMPLETE",
    block_count: 2,
    source_id: "s1",
    source_revision_id: "r1",
    extraction_id: "e1",
    reason: null,
    coverage: { unit: "page", total: 4, extracted: 4, gaps: [] },
    ...over,
  };
}

function session(over: Partial<SessionDetail> = {}): SessionDetail {
  return {
    id: "sess",
    label: "x",
    title: "T",
    lifecycle: "OPEN",
    view: "READY_FOR_ANALYSIS",
    row_version: 5,
    input_revision: 3,
    source_count: 3,
    total_source_count: 3,
    sources: [source("s1", "policy.pdf"), source("s2", "amb.csv"), source("s3", "scan.pdf")],
    current_run: null,
    preparation: {
      state: "PREPARED",
      run_id: "prep-1",
      input_revision: 3,
      snapshot_version: 2,
      current: true,
      requires_new_preparation: false,
    },
    extraction: {
      state: "NOT_STARTED",
      run_id: null,
      current: false,
      sources: [],
      counts: {},
      attention_count: 0,
      all_sources_accounted: false,
      coverage_acceptable: false,
    },
    allowed_actions: ["edit_inputs", "extract", "read_content"],
    created_at: null,
    updated_at: null,
    ...over,
  };
}

const finished = (sources: ExtractionSourceState[], attention: number) =>
  session({
    allowed_actions: ["edit_inputs", "read_content"],
    extraction: {
      state: "EXTRACTED",
      run_id: "ext-1",
      current: true,
      sources,
      counts: {},
      attention_count: attention,
      all_sources_accounted: true,
      coverage_acceptable: attention === 0,
    },
  });

beforeEach(() => {
  vi.clearAllMocks();
  api.newIdempotencyKey.mockReturnValue("key-fixed-0001");
  api.getCapabilities.mockResolvedValue({ parser_profile: { fingerprint: "fp-abc12345" } });
});

describe("code-point helpers", () => {
  it("slices by Unicode code points, not UTF-16 units", () => {
    const text = "\u{1F600} abc é end";
    expect(text.length).toBeGreaterThan(codePointLength(text)); // emoji = 2 UTF-16 units
    expect(codePointSlice(text, 2, 5)).toBe("abc");
    expect(text.slice(2, 5)).not.toBe("abc"); // JS UTF-16 indexing would be wrong here
    expect(codePointSlice(text, 6, 8)).toBe("é");
    const parts = highlightParts(text, 2, 5);
    expect(parts).toEqual({ before: "\u{1F600} ", match: "abc", after: " é end" });
  });

  it("keeps Arabic text in logical order", () => {
    const text = "سياسة الذكاء";
    expect(codePointSlice(text, 0, 5)).toBe("سياسة");
    expect(highlightParts(text, 0, 5).match + highlightParts(text, 0, 5).after).toBe(text);
  });

  it("describes native locators without inventing pages or columns", () => {
    expect(describeLocator({ type: "PDF_PAGE", page: 3 })).toBe("Page 3");
    expect(
      describeLocator({ type: "XLSX_CELL", sheet_index: 1, sheet_name: "AI", cell: "B7" }),
    ).toContain("cell B7");
    expect(describeLocator({ type: "DOCX_PATH", part: "document", path: "body/p[2]" })).not.toMatch(
      /page/i,
    );
    expect(describeLocator({ type: "CSV_RECORD", record: 2, start_line: 2, end_line: 3 })).toBe(
      "Record 2 · lines 2–3",
    );
  });
});

describe("copy and permissions", () => {
  it("labels outcomes truthfully and offers no OCR, password or AI-repair action", () => {
    expect(outcomeLabel("COMPLETE")).toBe("Text extracted");
    expect(outcomeLabel("NEEDS_OCR")).toBe("Needs OCR");
    expect(outcomeLabel("NEEDS_INPUT")).toBe("Needs parsing options");
    const all = [
      outcomeGuidance("NEEDS_OCR", "SCANNED_IMAGE_ONLY"),
      outcomeGuidance("UNSUPPORTED", "PASSWORD_REQUIRED"),
      outcomeGuidance("NEEDS_INPUT", "AMBIGUOUS_DELIMITER"),
    ].join(" ");
    expect(all).toMatch(/OCR is not available/);
    expect(all).toMatch(/unprotected copy/);
    expect(all).not.toMatch(/run ocr|enter.*password|fix with ai/i);
    expect(outcomeGuidance("COMPLETE")).toBeNull();
  });

  it("reports extraction worker activity separately from source quality", () => {
    const run = (over: Partial<RunSummary>): RunSummary => ({
      id: "r",
      run_number: 2,
      stage: "EXTRACT_SOURCES",
      state: "SUCCEEDED",
      active: false,
      input_revision: 3,
      retry_of_run_id: null,
      attempt_count: 1,
      max_attempts: 5,
      source_count: 3,
      sources_checked: 3,
      sources_total: 3,
      created_at: null,
      ended_at: null,
      error_code: null,
      ...over,
    });
    const done = runStatus(run({}));
    expect(done?.title).toBe("Extraction finished");
    expect(done?.title).not.toMatch(/sources saved/i);
    expect(runStatus(run({ state: "PREPARING", sources_checked: 1 }))?.detail).toBe(
      "1 of 3 sources processed.",
    );
    expect(
      runStatus(run({ state: "FAILED", error_code: "PARSER_PROFILE_MISMATCH" }))?.detail,
    ).toMatch(/different parser configuration/);
  });

  it("requires the dedicated permission for provider management", () => {
    expect(resolveIntakeAccess(["sdk:manage", "onboarding:sources:read"]).canProviders).toBe(false);
    expect(resolveIntakeAccess(["sdk:manage", "onboarding:providers:manage"]).canProviders).toBe(
      true,
    );
    expect(resolveIntakeAccess(["onboarding:providers:manage"]).canProviders).toBe(false);
  });
});

describe("extraction panel", () => {
  it("shows the Extract action only when the server allows it and sends the exact plan inputs", async () => {
    const next = finished([state({})], 0);
    api.extractSources.mockResolvedValue({ session: next, run: {}, replayed: false });
    const onSession = vi.fn();
    render(
      <ExtractionPanel session={session()} canManage onSession={onSession} onReload={vi.fn()} />,
    );
    const button = screen.getByText("Extract text and tables") as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false)); // waits for the parser fingerprint
    fireEvent.click(button);
    await waitFor(() =>
      expect(api.extractSources).toHaveBeenCalledWith("sess", {
        expectedRowVersion: 5,
        inputRevision: 3,
        preparationRunId: "prep-1",
        parserFingerprint: "fp-abc12345",
        key: "key-fixed-0001",
      }),
    );
    expect(onSession).toHaveBeenCalledWith(next);
  });

  it("hides Extract for readers and when the server does not offer it", () => {
    const { rerender } = render(
      <ExtractionPanel
        session={session()}
        canManage={false}
        onSession={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    expect(screen.queryByText("Extract text and tables")).toBeNull();
    rerender(
      <ExtractionPanel
        session={session({ allowed_actions: ["edit_inputs"] })}
        canManage
        onSession={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    expect(screen.queryByText("Extract text and tables")).toBeNull();
    expect(api.getCapabilities).not.toHaveBeenCalled();
  });

  it("separates worker completion from needs-attention and shows real coverage", () => {
    const s = finished(
      [
        state({ source_id: "s1", extraction_id: "e1" }),
        state({
          label: "Source 2",
          source_id: "s2",
          source_revision_id: "r2",
          extraction_id: "e2",
          outcome: "NEEDS_INPUT",
          reason: "AMBIGUOUS_DELIMITER",
          block_count: 0,
          coverage: { unit: "record", total: 0, extracted: 0, gaps: [] },
        }),
        state({
          label: "Source 3",
          source_id: "s3",
          source_revision_id: "r3",
          extraction_id: "e3",
          outcome: "PARTIAL",
          reason: "OCR_REQUIRED",
          coverage: { unit: "page", total: 4, extracted: 3, gaps: [{ code: "OCR_REQUIRED" }] },
        }),
      ],
      2,
    );
    render(<ExtractionPanel session={s} canManage onSession={vi.fn()} onReload={vi.fn()} />);
    expect(screen.getByTestId("extraction-attention").textContent).toBe(
      "Needs attention: 2 of 3 sources",
    );
    expect(screen.getByText("Text extracted")).toBeTruthy();
    expect(screen.getByText("Needs parsing options")).toBeTruthy();
    expect(screen.getByText("Partially extracted")).toBeTruthy();
    expect(screen.getByText("3 of 4 pages extracted")).toBeTruthy();
    expect(screen.getByText(/Known gaps: OCR_REQUIRED/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/run ocr|enter password|ai repair/i);
  });

  it("offers a delimiter choice for an ambiguous CSV and saves versioned options", async () => {
    const s = finished(
      [
        state({
          source_id: "s2",
          source_revision_id: "r2",
          extraction_id: "e2",
          outcome: "NEEDS_INPUT",
          reason: "AMBIGUOUS_DELIMITER",
          block_count: 0,
          coverage: null,
        }),
      ],
      1,
    );
    api.setParsingOptions.mockResolvedValue(session({ row_version: 6, input_revision: 4 }));
    const onSession = vi.fn();
    render(<ExtractionPanel session={s} canManage onSession={onSession} onReload={vi.fn()} />);
    expect(screen.getByText(/original file is kept unchanged/i)).toBeTruthy();
    const save = screen.getByText("Save options") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Delimiter"), { target: { value: ";" } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(api.setParsingOptions).toHaveBeenCalledWith("sess", "s2", {
        options: { delimiter: ";" },
        expectedRowVersion: 5,
        key: "key-fixed-0001",
      }),
    );
    expect(onSession).toHaveBeenCalled();
  });

  it("marks stale extraction as history and never silently reruns", () => {
    const s = finished([state({})], 0);
    s.extraction!.current = false;
    render(<ExtractionPanel session={s} canManage onSession={vi.fn()} onReload={vi.fn()} />);
    expect(screen.getByTestId("extraction-stale").textContent).toMatch(/kept as history/);
    expect(screen.queryByTestId("extraction-attention")).toBeNull();
    expect(api.extractSources).not.toHaveBeenCalled();
  });

  it("gives a metadata-only reader no content, options or preview controls", () => {
    const s = finished(
      [{ label: "Source 1", state: "FINAL", outcome: "NEEDS_INPUT", block_count: 0 }],
      1,
    );
    render(
      <ExtractionPanel session={s} canManage={false} onSession={vi.fn()} onReload={vi.fn()} />,
    );
    expect(screen.getByText("Source 1")).toBeTruthy();
    expect(screen.queryByText("Save options")).toBeNull();
    expect(screen.queryByText("Preview text")).toBeNull();
  });

  it("asks for a new preparation when only a legacy preparation exists", () => {
    const s = session({
      allowed_actions: ["edit_inputs"],
      preparation: {
        state: "PREPARED",
        run_id: "old",
        input_revision: 3,
        snapshot_version: 1,
        current: true,
        requires_new_preparation: true,
      },
    });
    render(<ExtractionPanel session={s} canManage onSession={vi.fn()} onReload={vi.fn()} />);
    expect(screen.getByText(/predates parsing options/)).toBeTruthy();
    expect(screen.queryByText("Extract text and tables")).toBeNull();
  });
});

describe("source preview and citations", () => {
  const block = (id: string, ordinal: number, over = {}) => ({
    id,
    ordinal,
    kind: "TABLE_CELL",
    text: "\u{1F600} abc",
    cell_data: null,
    locator: { type: "XLSX_CELL", sheet_index: 1, sheet_name: "AI Inventory", cell: "B7" },
    content_digest: "d",
    warnings: [],
    ...over,
  });
  const page = (items: ReturnType<typeof block>[], next: number | null = null) => ({
    extraction: {
      id: "e1",
      outcome: "COMPLETE",
      block_count: items.length,
      coverage: {},
      created_at: null,
      is_current_input: true,
      original_sha256: "abcdef0123456789",
      source_id: "s1",
      source_revision_id: "r1",
      revision_number: 2,
      extractor_build: "onb-parsers-1",
      normalization: "XLSX_STORED_VALUES",
      warnings: ["HIDDEN_SHEET_CONTENT"],
    },
    items,
    next_after: next,
  });

  it("shows exact stored text with native locators, formula and cached value distinctly", async () => {
    api.listBlocks.mockResolvedValue(
      page([
        block("b1", 0),
        block("b2", 1, {
          text: "12.5",
          cell_data: {
            formula: "=SUM(C2:C2)",
            cached_value: 12.5,
            cache_state: "PRESENT_UNVERIFIED",
          },
        }),
        block("b3", 2, {
          text: "=A1+1",
          cell_data: { formula: "=A1+1", cached_value: null, cache_state: "MISSING" },
        }),
      ]),
    );
    render(<SourcePreview sessionId="sess" extractionId="e1" onClose={vi.fn()} />);
    expect(await screen.findByText(/not a rendering of the original document/)).toBeTruthy();
    expect(screen.getAllByText(/Sheet 1/).length).toBe(3);
    expect(screen.getByText(/=SUM\(C2:C2\)/)).toBeTruthy();
    expect(screen.getAllByText(/cached value/).length).toBe(2);
    expect(screen.getByText(/missing/)).toBeTruthy(); // never substituted by zero
    expect(screen.getByTestId("extraction-details").textContent).toContain("HIDDEN_SHEET_CONTENT");
    const text = screen.getByText("\u{1F600} abc");
    expect(text.getAttribute("dir")).toBe("auto");
    expect(api.getCitation).not.toHaveBeenCalled();
  });

  it("resolves a citation by code points and highlights the server excerpt", async () => {
    api.listBlocks.mockResolvedValue(page([block("b1", 0)]));
    api.getCitation.mockResolvedValue({
      source_id: "s1",
      source_revision_id: "r1",
      revision_number: 2,
      source_sha256: "abcdef0123456789",
      display_name: "inventory.xlsx",
      extraction_id: "e1",
      extractor_build: "onb-parsers-1",
      block_id: "b1",
      locator: { type: "XLSX_CELL", sheet_index: 1, sheet_name: "AI Inventory", cell: "B7" },
      cell_data: null,
      text_range: { start: 2, end: 5, unit: "UNICODE_CODE_POINT" },
      excerpt: "abc",
      block_text: "\u{1F600} abc",
      integrity_ok: true,
      is_current_input: false,
      historical: true,
    });
    render(<SourcePreview sessionId="sess" extractionId="e1" onClose={vi.fn()} />);
    await screen.findByText("\u{1F600} abc");
    fireEvent.change(screen.getByLabelText("Start offset for block 0"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("End offset for block 0"), { target: { value: "5" } });
    fireEvent.click(screen.getByText("Cite excerpt"));
    await waitFor(() =>
      expect(api.getCitation).toHaveBeenCalledWith("sess", "b1", { start: 2, end: 5 }),
    );
    const mark = await screen.findByTestId("citation-excerpt");
    expect(mark.textContent).toBe("abc");
    expect(screen.getByText("Historical input")).toBeTruthy();
    expect(screen.getByTestId("citation").textContent).toMatch(/not of the original file bytes/);
    expect(screen.getByTestId("citation").textContent).toMatch(/code points/);
  });

  it("surfaces an invalid range error and loads further pages on request", async () => {
    api.listBlocks
      .mockResolvedValueOnce(page([block("b1", 0)], 0))
      .mockResolvedValueOnce(page([block("b2", 1, { text: "second" })], null));
    api.getCitation.mockRejectedValue(new Error("Range is outside the stored block text"));
    render(<SourcePreview sessionId="sess" extractionId="e1" onClose={vi.fn()} />);
    await screen.findByText("\u{1F600} abc");
    fireEvent.click(screen.getByText("Cite excerpt"));
    expect(await screen.findByText(/outside the stored block text/)).toBeTruthy();
    fireEvent.click(screen.getByText("Load more"));
    expect(await screen.findByText("second")).toBeTruthy();
    expect(api.listBlocks).toHaveBeenLastCalledWith("sess", "e1", 0);
  });
});
