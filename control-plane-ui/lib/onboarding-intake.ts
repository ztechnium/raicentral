import { useMemo } from "react";
import { usePermissions } from "@/lib/hooks/use-auth";
import type {
  AIState,
  RunSummary,
  SessionDetail,
  SessionView,
  SourceOutcome,
} from "@/lib/onboarding-api";

/**
 * Exact-permission intake capability. `sdk:read` shows sanitized progress;
 * source content additionally needs `onboarding:sources:read`; every mutation
 * additionally needs `sdk:manage`.
 */
export function resolveIntakeAccess(permissions: readonly string[] | null) {
  const list = permissions ?? [];
  const canRead = list.includes("sdk:read");
  const canContent = canRead && list.includes("onboarding:sources:read");
  const canManage = canContent && list.includes("sdk:manage");
  // Provider setup needs sdk:manage plus its own dedicated permission.
  const canProviders = list.includes("sdk:manage") && list.includes("onboarding:providers:manage");
  return { canRead, canContent, canManage, canProviders };
}

export function useIntakeAccess() {
  const { tenant } = usePermissions();
  const permissions = tenant?.permissions ?? null;
  return useMemo(() => resolveIntakeAccess(permissions), [permissions]);
}

const VIEW_LABELS: Record<SessionView, string> = {
  DRAFT: "Draft",
  QUEUED: "Queued",
  PREPARING: "Checking saved sources",
  READY_FOR_ANALYSIS: "Sources saved",
  PREPARATION_FAILED: "Preparation failed",
  ARCHIVED: "Archived",
};

export function viewLabel(view: SessionView): string {
  return VIEW_LABELS[view];
}

const ERROR_COPY: Record<string, string> = {
  SOURCE_INTEGRITY_FAILED:
    "A saved original no longer matches its recorded checksum. Restore the original, then retry.",
  SOURCE_MISSING: "A saved original could not be found in storage. Restore it, then retry.",
  STORAGE_UNAVAILABLE: "Source storage was unavailable. Retry when it is back.",
  UNSUPPORTED_MEDIA: "A saved source is not a supported file type.",
  SOURCE_TOO_LARGE: "A saved source exceeds the size limit.",
  INVALID_RUN_BINDING: "The preparation job was not recognized.",
  LEASE_EXPIRED: "The worker stopped responding repeatedly. Retry to start a new attempt.",
  AI_CONFIGURATION_UNAVAILABLE:
    "The AI connection is not available. Check AI setup, then run the analysis again.",
  AI_CONFIGURATION_CHANGED:
    "The AI connection changed after this analysis was planned. Review the plan and run it again.",
  AI_CREDENTIAL_UNAVAILABLE:
    "The server could not use the AI credential. Check RAI_AI_* in the environment and run an AI connection test.",
  AI_AUTH_REJECTED: "The AI provider rejected the credential. Check the API key or service account.",
  AI_MODEL_OR_ENDPOINT_NOT_FOUND:
    "The AI provider does not know this model or endpoint. Check RAI_AI_MODEL.",
  AI_RATE_LIMITED: "The AI provider rate-limited the request. Wait a moment and run the analysis again.",
  AI_PROVIDER_ERROR: "The AI provider returned an error. Run the analysis again later.",
  AI_TRUNCATED_OUTPUT:
    "The AI reply was cut off. Raise RAI_AI_MAX_OUTPUT_TOKENS (up to 8192) or use fewer sources, then run again.",
  AI_INVALID_OUTPUT: "The AI reply was not in the required format. Run the analysis again.",
  AI_INVALID_RESPONSE: "The AI provider reply could not be read. Run the analysis again.",
  AI_REFUSAL: "The AI provider declined the request.",
  AI_TIMEOUT_AFTER_DISPATCH:
    "The AI provider did not answer in time. The request may have been processed; run the analysis again.",
  AI_CONNECT_FAILED: "The server could not reach the AI provider.",
  TOO_MANY_PROPOSALS: "The sources produced more proposals than the review limit. Use fewer sources.",
  PARSER_PROFILE_MISMATCH:
    "The worker uses a different parser configuration than the server. Ask an operator to align them, then extract again.",
};

/** Human status for the current run, from real server state only. */
export function runStatus(run: RunSummary | null): { title: string; detail: string } | null {
  if (!run) return null;
  if (run.stage === "EXTRACT_SOURCES") return extractionRunStatus(run);
  switch (run.state) {
    case "QUEUED":
      return { title: "Queued", detail: "Waiting for a worker." };
    case "RETRY_SCHEDULED":
      return {
        title: "Retry scheduled",
        detail: `Attempt ${run.attempt_count} of ${run.max_attempts} did not finish. Another attempt is scheduled.`,
      };
    case "PREPARING": {
      const checked = run.sources_checked ?? 0;
      const total = run.sources_total ?? run.source_count;
      return {
        title: "Checking saved sources",
        detail: `${checked} of ${total} sources checked.`,
      };
    }
    case "SUCCEEDED":
      return {
        title: "Sources saved",
        detail: "Your selected files and prompt are saved and ready for analysis.",
      };
    case "FAILED":
      return {
        title: run.stage === "PREPARE_SOURCES" ? "Preparation failed" : "Analysis failed",
        detail: (run.error_code && ERROR_COPY[run.error_code]) || run.stage === "PREPARE_SOURCES" ? "Preparation did not complete." : "The analysis did not complete.",
      };
    case "CANCELLED":
      return { title: "Preparation cancelled", detail: "You can edit inputs or retry." };
  }
}

/** Inputs are editable only while the server says so. */
export function canEditInputs(session: SessionDetail | null): boolean {
  return Boolean(session?.allowed_actions.includes("edit_inputs"));
}

export function isActive(session: SessionDetail | null): boolean {
  return Boolean(session?.current_run?.active);
}

/** Bounded exponential backoff for polling an active run (ms). */
export function pollDelay(attempt: number): number {
  return Math.min(10_000, 1_500 * 2 ** Math.min(attempt, 3));
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export const SUPPORTED_ACCEPT = ".pdf,.docx,.xlsx,.csv,.txt,.md,.markdown";

function extractionRunStatus(run: RunSummary): { title: string; detail: string } {
  switch (run.state) {
    case "QUEUED":
      return { title: "Extraction queued", detail: "Waiting for a worker." };
    case "RETRY_SCHEDULED":
      return {
        title: "Extraction retry scheduled",
        detail: `Attempt ${run.attempt_count} of ${run.max_attempts} did not finish. Another attempt is scheduled.`,
      };
    case "PREPARING":
      return {
        title: "Extracting text and tables",
        detail: `${run.sources_checked ?? 0} of ${run.sources_total ?? run.source_count} sources processed.`,
      };
    case "SUCCEEDED":
      // Worker activity, not source quality: see each source's own outcome.
      return {
        title: "Extraction finished",
        detail: "The worker recorded an outcome for every source. Check each source below.",
      };
    case "FAILED":
      return {
        title: "Extraction failed",
        detail: (run.error_code && ERROR_COPY[run.error_code]) || "Extraction did not complete.",
      };
    case "CANCELLED":
      return {
        title: "Extraction cancelled",
        detail: "Unprocessed sources were recorded as not processed.",
      };
  }
}

const OUTCOME_LABELS: Record<SourceOutcome, string> = {
  COMPLETE: "Text extracted",
  PARTIAL: "Partially extracted",
  NEEDS_OCR: "Needs OCR",
  NEEDS_INPUT: "Needs parsing options",
  UNSUPPORTED: "Unsupported",
  FAILED: "Failed",
  NOT_PROCESSED: "Not processed",
};

export function outcomeLabel(outcome: SourceOutcome | null | undefined): string {
  return outcome ? OUTCOME_LABELS[outcome] : "Pending";
}

/** Concrete guidance matched to the reason. No OCR, password or AI-repair action exists. */
export function outcomeGuidance(
  outcome: SourceOutcome | null,
  reason?: string | null,
): string | null {
  if (!outcome || outcome === "COMPLETE") return null;
  const byReason: Record<string, string> = {
    ENCODING_ERROR:
      "Choose the file encoding (for Arabic legacy exports try Windows-1256), then prepare and extract again.",
    AMBIGUOUS_DELIMITER: "Choose the CSV delimiter explicitly, then prepare and extract again.",
    NO_USABLE_TEXT:
      "No readable text was found. Replace the file, or exclude it if it is not needed.",
    SCANNED_IMAGE_ONLY:
      "This looks scanned. OCR is not available yet: provide a searchable PDF, or exclude it.",
    OCR_REQUIRED:
      "Some pages are scanned images. Provide a searchable replacement, or exclude the source.",
    PASSWORD_REQUIRED:
      "Password-protected files are not supported. Provide a permitted, unprotected copy.",
    MALFORMED_DOCUMENT: "The file could not be read. Replace a damaged file, or exclude it.",
    PAGE_LIMIT: "The page limit was reached. Split the document or exclude it.",
    ROW_LIMIT: "The row limit was reached. Split the file or exclude it.",
    OUTPUT_LIMIT: "The output limit was reached. Split the file or exclude it.",
    SOURCE_MISSING:
      "The saved original is missing from storage. Ask an operator to restore it, or re-upload.",
    SOURCE_INTEGRITY_FAILED:
      "The saved original no longer matches its checksum. Re-upload the file.",
    RUN_OUTPUT_LIMIT: "The run output limit was reached before this source. Extract again.",
    RUN_CANCELLED: "The run was cancelled before this source. Extract again.",
    RUN_ABORTED: "The run stopped before this source. Extract again.",
  };
  if (reason && byReason[reason]) return byReason[reason];
  return "Replace the file, change its parsing options, or exclude it.";
}

const AI_LABELS: Record<AIState, string> = {
  UNCONFIGURED: "Not configured",
  DISABLED: "Disabled",
  TARGET_UNAVAILABLE: "Approved target unavailable",
  CONFIGURED_UNTESTED: "Configured, not tested",
  TEST_PENDING: "Test in progress",
  TEST_PASSED: "Last test passed",
  TEST_FAILED: "Last test failed",
  TEST_UNKNOWN: "Last test outcome unknown",
  TEST_STALE: "Test is out of date",
};

export function aiStateLabel(state: AIState): string {
  return AI_LABELS[state];
}

/**
 * Text offsets are Unicode code points (not UTF-16 units). Array.from iterates by code
 * point, so these slices match the server's start-inclusive/end-exclusive ranges.
 */
export function codePointSlice(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join("");
}

export function highlightParts(
  text: string,
  start: number,
  end: number,
): { before: string; match: string; after: string } {
  const points = Array.from(text);
  return {
    before: points.slice(0, start).join(""),
    match: points.slice(start, end).join(""),
    after: points.slice(end).join(""),
  };
}

export function codePointLength(text: string): number {
  return Array.from(text).length;
}

/** Human locator in one-based native coordinates. Never invents pages or columns. */
export function describeLocator(locator: Record<string, unknown> & { type: string }): string {
  switch (locator.type) {
    case "PDF_PAGE":
      return `Page ${locator.page}`;
    case "DOCX_PATH":
      return `${locator.part} · ${locator.path}`;
    case "XLSX_CELL":
      return `Sheet ${locator.sheet_index} “${locator.sheet_name}” · cell ${locator.cell}`;
    case "CSV_RECORD":
      return `Record ${locator.record} · lines ${locator.start_line}–${locator.end_line}`;
    case "TEXT_LINES":
      return `Lines ${locator.start_line}–${locator.end_line}`;
    default:
      return locator.type;
  }
}
