import type { CompletionSource, OnboardingResponse, OnboardingStep } from "@/lib/public-api";

/**
 * Presentation rules for onboarding evidence. Every label here states what the
 * server actually established. Nothing in this module can produce a
 * "verified", "production ready" or "compliant" claim in this step.
 */

export type ConnectionDisplay =
  | { kind: "unavailable"; label: string }
  | { kind: "unscoped"; label: string }
  | { kind: "not_observed"; label: string }
  | { kind: "observed"; label: string; observedAt: string | null };

export function connectionDisplay(summary: OnboardingResponse["summary"]): ConnectionDisplay {
  if (!summary) {
    // Rolling upgrade or failed projection: unknown, not "not connected".
    return { kind: "unavailable", label: "Status unavailable" };
  }
  switch (summary.connection.state) {
    case "OBSERVED":
      return {
        kind: "observed",
        label: "Runtime activity observed",
        observedAt: summary.connection.last_observed_at,
      };
    case "NOT_OBSERVED":
      return { kind: "not_observed", label: "No runtime activity observed" };
    default:
      return { kind: "unscoped", label: "Select an integration to check activity" };
  }
}

export function verificationLabel(summary: OnboardingResponse["summary"]): string {
  if (!summary) return "Verification status unavailable";
  return summary.verification.state === "NOT_RUN"
    ? "Verification not run"
    : "Verification status unavailable";
}

export function welcomeLabel(summary: OnboardingResponse["summary"]): string {
  if (!summary) return "Welcome status unavailable";
  return summary.welcome_complete ? "Welcome complete" : "Welcome not completed";
}

/** What a checklist item's completion is based on, in plain terms. */
export function completionLabel(
  step: Pick<OnboardingStep, "id" | "done" | "completion_source">,
): string | null {
  if (!step.done) return null;
  const source: CompletionSource | undefined = step.completion_source;
  if (step.id === "first_governed_action" && source === "OBSERVATION") {
    return "Governed decision observed";
  }
  switch (source) {
    case "OBSERVATION":
      return "Runtime activity observed";
    case "RECORD":
      return "Record exists";
    case "MANUAL":
      // Says nothing about who marked it or whether anything was reviewed.
      return "Marked complete";
    default:
      return "Recorded";
  }
}

/** Steps the product cannot establish yet: informational, never a to-do. */
export function isInformationalStep(step: Pick<OnboardingStep, "actionable">): boolean {
  return step.actionable === false;
}

/** Evidence-only steps never accept a manual completion from the UI. */
export const EVIDENCE_ONLY_STEP_IDS = [
  "connect_application",
  "test_connection",
  "first_governed_action",
  "production_readiness",
] as const;

export function canMarkManually(stepId: string): boolean {
  return !(EVIDENCE_ONLY_STEP_IDS as readonly string[]).includes(stepId);
}

/** Label for the manual button: an acknowledgment, never a technical claim. */
export function manualActionLabel(stepId: string): string {
  return stepId === "inspect_trajectory" ? "Mark reviewed" : "Mark complete";
}

export type StepperState = "done" | "deferred" | "current" | "pending";

/**
 * A wizard stepper cell is done only on explicit completion, deferred only on
 * an explicit deferral. Being earlier than the current screen proves nothing.
 */
export function stepperState(input: {
  completed: boolean;
  deferred: boolean;
  current: boolean;
}): StepperState {
  if (input.completed) return "done";
  if (input.deferred) return "deferred";
  return input.current ? "current" : "pending";
}

export interface DisplayedSelection {
  integrationId?: string | null;
  runtimeId?: string | null;
}

/**
 * True only when the server summary resolved exactly the integration and
 * runtime the user is looking at. A late or stale response (previous selection,
 * pending save) fails this check and must not be displayed as evidence.
 */
export function matchesSelection(
  summary: OnboardingResponse["summary"],
  selection: DisplayedSelection,
): boolean {
  if (!summary || !selection.integrationId || !selection.runtimeId) return false;
  const connection = summary.connection;
  return (
    connection.state !== "UNSCOPED" &&
    connection.integration_id === selection.integrationId &&
    connection.runtime_identity_id === selection.runtimeId
  );
}

/** Connection status for the selection currently displayed. */
export function scopedConnectionDisplay(
  summary: OnboardingResponse["summary"],
  selection: DisplayedSelection,
): ConnectionDisplay {
  if (!summary) return connectionDisplay(summary);
  if (!matchesSelection(summary, selection)) {
    return { kind: "unscoped", label: "Select an integration to check activity" };
  }
  return connectionDisplay(summary);
}

/**
 * Exact-match trajectory lookup. There is deliberately no "first item"
 * fallback: a trajectory from another runtime proves nothing about this one.
 */
export function findMatchingTrajectory<T extends { id: string; runtime_identity_id: string }>(
  items: readonly T[],
  input: { runtimeId?: string | null; trajectoryId?: string | null },
): T | null {
  if (!input.runtimeId) return null;
  const forRuntime = items.filter((item) => item.runtime_identity_id === input.runtimeId);
  if (input.trajectoryId) {
    return forRuntime.find((item) => item.id === input.trajectoryId) ?? null;
  }
  return forRuntime[0] ?? null;
}
