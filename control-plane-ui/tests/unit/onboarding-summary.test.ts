import { describe, expect, it } from "vitest";
import {
  canMarkManually,
  completionLabel,
  connectionDisplay,
  findMatchingTrajectory,
  isInformationalStep,
  manualActionLabel,
  matchesSelection,
  scopedConnectionDisplay,
  stepperState,
  verificationLabel,
  welcomeLabel,
} from "@/lib/onboarding-summary";
import type { OnboardingSummary } from "@/lib/public-api";

function summary(
  state: OnboardingSummary["connection"]["state"],
  ids: { integration?: string; runtime?: string; at?: string | null } = {},
): OnboardingSummary {
  return {
    welcome_complete: false,
    connection: {
      state,
      integration_id: state === "UNSCOPED" ? null : (ids.integration ?? "int-a"),
      runtime_identity_id: state === "UNSCOPED" ? null : (ids.runtime ?? "rt-a"),
      last_observed_at: state === "OBSERVED" ? (ids.at ?? "2026-11-01T12:00:00+00:00") : null,
    },
    verification: { state: "NOT_RUN" },
  };
}

describe("connection and verification labels", () => {
  it("maps each server state to its own wording", () => {
    expect(connectionDisplay(summary("OBSERVED"))).toMatchObject({
      kind: "observed",
      label: "Runtime activity observed",
      observedAt: "2026-11-01T12:00:00+00:00",
    });
    expect(connectionDisplay(summary("NOT_OBSERVED")).label).toBe("No runtime activity observed");
    expect(connectionDisplay(summary("UNSCOPED")).label).toBe(
      "Select an integration to check activity",
    );
  });

  it("treats a missing summary as unavailable, never as 'not connected' or verified", () => {
    expect(connectionDisplay(undefined)).toEqual({
      kind: "unavailable",
      label: "Status unavailable",
    });
    expect(verificationLabel(undefined)).toBe("Verification status unavailable");
    expect(welcomeLabel(undefined)).toBe("Welcome status unavailable");
  });

  it("renders verification as not run", () => {
    expect(verificationLabel(summary("OBSERVED"))).toBe("Verification not run");
  });

  it("never produces verified / production-ready / compliant wording", () => {
    const labels = [
      connectionDisplay(summary("OBSERVED")).label,
      connectionDisplay(undefined).label,
      verificationLabel(summary("OBSERVED")),
      verificationLabel(undefined),
      welcomeLabel(summary("OBSERVED")),
      completionLabel({ id: "connect_application", done: true, completion_source: "OBSERVATION" }),
      completionLabel({ id: "inspect_trajectory", done: true, completion_source: "MANUAL" }),
    ].join(" | ");
    expect(labels).not.toMatch(/verified|production ready|compliant|controls verified/i);
  });
});

describe("completion provenance", () => {
  it("labels governed decisions as observed, not as an executed action", () => {
    expect(
      completionLabel({
        id: "first_governed_action",
        done: true,
        completion_source: "OBSERVATION",
      }),
    ).toBe("Governed decision observed");
  });

  it("labels manual marks as Marked complete, never as reviewed by a person", () => {
    expect(
      completionLabel({ id: "inspect_trajectory", done: true, completion_source: "MANUAL" }),
    ).toBe("Marked complete");
  });

  it("shows nothing for steps that are not done", () => {
    expect(
      completionLabel({ id: "team_roles", done: false, completion_source: "NONE" }),
    ).toBeNull();
  });

  it("only allows manual completion for non-evidence steps", () => {
    for (const id of [
      "connect_application",
      "test_connection",
      "first_governed_action",
      "production_readiness",
    ]) {
      expect(canMarkManually(id)).toBe(false);
    }
    expect(canMarkManually("inspect_trajectory")).toBe(true);
    expect(canMarkManually("team_roles")).toBe(true);
    expect(manualActionLabel("inspect_trajectory")).toBe("Mark reviewed");
  });

  it("treats steps the server marks unactionable as informational", () => {
    expect(isInformationalStep({ actionable: false })).toBe(true);
    expect(isInformationalStep({ actionable: true })).toBe(false);
    expect(isInformationalStep({})).toBe(false);
  });
});

describe("selection matching", () => {
  const selection = { integrationId: "int-b", runtimeId: "rt-b" };

  it("accepts only an exact integration and runtime match", () => {
    expect(
      matchesSelection(summary("OBSERVED", { integration: "int-b", runtime: "rt-b" }), selection),
    ).toBe(true);
    expect(matchesSelection(summary("OBSERVED"), selection)).toBe(false);
    expect(
      matchesSelection(summary("OBSERVED", { integration: "int-b", runtime: "rt-a" }), selection),
    ).toBe(false);
    expect(matchesSelection(summary("UNSCOPED"), selection)).toBe(false);
    expect(matchesSelection(undefined, selection)).toBe(false);
    expect(matchesSelection(summary("OBSERVED"), { runtimeId: "rt-a" })).toBe(false);
  });

  it("does not show runtime A's heartbeat as activity for runtime B", () => {
    const display = scopedConnectionDisplay(summary("OBSERVED"), selection);
    expect(display.kind).toBe("unscoped");
    expect(display.label).toBe("Select an integration to check activity");
  });

  it("shows the observation for the selection actually on screen", () => {
    expect(
      scopedConnectionDisplay(summary("OBSERVED"), { integrationId: "int-a", runtimeId: "rt-a" })
        .kind,
    ).toBe("observed");
  });
});

describe("trajectory matching", () => {
  const items = [
    { id: "t-other", runtime_identity_id: "rt-other" },
    { id: "t-a", runtime_identity_id: "rt-a" },
  ];

  it("has no first-item fallback", () => {
    expect(findMatchingTrajectory(items, { runtimeId: "rt-none" })).toBeNull();
    expect(findMatchingTrajectory(items, { runtimeId: null })).toBeNull();
    expect(findMatchingTrajectory([], { runtimeId: "rt-a" })).toBeNull();
  });

  it("keeps a missing saved trajectory unavailable", () => {
    expect(findMatchingTrajectory(items, { runtimeId: "rt-a", trajectoryId: "gone" })).toBeNull();
  });

  it("rejects a saved trajectory that belongs to another runtime", () => {
    expect(
      findMatchingTrajectory(items, { runtimeId: "rt-a", trajectoryId: "t-other" }),
    ).toBeNull();
  });

  it("returns an exact runtime match", () => {
    expect(findMatchingTrajectory(items, { runtimeId: "rt-a" })?.id).toBe("t-a");
    expect(findMatchingTrajectory(items, { runtimeId: "rt-a", trajectoryId: "t-a" })?.id).toBe(
      "t-a",
    );
  });
});

describe("stepper state", () => {
  it("is done only on explicit completion", () => {
    expect(stepperState({ completed: true, deferred: false, current: false })).toBe("done");
    // Being before the current screen proves nothing.
    expect(stepperState({ completed: false, deferred: false, current: false })).toBe("pending");
  });

  it("renders deferral distinctly from both done and pending", () => {
    expect(stepperState({ completed: false, deferred: true, current: false })).toBe("deferred");
    expect(stepperState({ completed: false, deferred: true, current: true })).toBe("deferred");
  });
});
