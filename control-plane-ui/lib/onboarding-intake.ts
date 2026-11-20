import { useMemo } from "react";
import { usePermissions } from "@/lib/hooks/use-auth";
import type { RunSummary, SessionDetail, SessionView } from "@/lib/onboarding-api";

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
  return { canRead, canContent, canManage };
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
};

/** Human status for the current run, from real server state only. */
export function runStatus(run: RunSummary | null): { title: string; detail: string } | null {
  if (!run) return null;
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
        title: "Preparation failed",
        detail: (run.error_code && ERROR_COPY[run.error_code]) || "Preparation did not complete.",
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
