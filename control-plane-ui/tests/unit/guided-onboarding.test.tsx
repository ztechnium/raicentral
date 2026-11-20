import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests: they prove client behavior and the exact API calls made, not
// backend enforcement (that is covered by tests/public_product).

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/app/onboarding",
  useSearchParams: () => new URLSearchParams(),
}));

const api = vi.hoisted(() => ({
  createAgent: vi.fn(),
  createAgentVersion: vi.fn(),
  createAISystem: vi.fn(),
  createRuntimeIdentity: vi.fn(),
  getAISystems: vi.fn(),
  getAgentVersions: vi.fn(),
  getAgents: vi.fn(),
  getRuntimeCredentials: vi.fn(),
  getRuntimeIdentities: vi.fn(),
  getTrajectories: vi.fn(),
  issueRuntimeCredential: vi.fn(),
  transitionAgentVersion: vi.fn(),
  transitionAISystem: vi.fn(),
  downloadCatalogTemplate: vi.fn(),
  importCatalogWorkbook: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ ...api }));

const pub = vi.hoisted(() => ({
  fetchOnboarding: vi.fn(),
  patchOnboarding: vi.fn(),
  completeWelcome: vi.fn(),
  listSdkIntegrations: vi.fn(),
}));
vi.mock("@/lib/public-api", () => ({ ...pub }));

import { GuidedOnboarding } from "@/components/onboarding/guided-onboarding";
import type { OnboardingResponse, OnboardingSummary } from "@/lib/public-api";

const SYSTEM = {
  id: "sys-1",
  system_key: "support",
  name: "Support AI",
  status: "DRAFT",
  agent_count: 1,
};
const AGENT = { id: "ag-1", agent_key: "support-agent", name: "Support Agent" };
const RT_A = { id: "rt-a", name: "runtime-a", agent_id: "ag-1", agent_version_id: "v-1" };
const RT_B = { id: "rt-b", name: "runtime-b", agent_id: "ag-1", agent_version_id: "v-1" };
const INT_A = { id: "int-a", name: "Integration A", runtime_identity_id: "rt-a" };
const INT_B = { id: "int-b", name: "Integration B", runtime_identity_id: "rt-b" };
const TRAJ_A = {
  id: "t-a",
  runtime_identity_id: "rt-a",
  status: "COMPLETED",
  event_count: 3,
  started_at: "2026-11-01T12:00:00Z",
};

const STEP_IDS = [
  "organization_profile",
  "team_roles",
  "first_ai_system",
  "agent_version",
  "resources",
  "first_policies",
  "choose_integration",
  "runtime_identity",
  "connect_application",
  "test_connection",
  "first_governed_action",
  "inspect_trajectory",
  "production_readiness",
];

function summary(
  state: "UNSCOPED" | "NOT_OBSERVED" | "OBSERVED",
  who: { integration: string; runtime: string } = { integration: "int-a", runtime: "rt-a" },
  welcome = false,
): OnboardingSummary {
  return {
    welcome_complete: welcome,
    connection: {
      state,
      integration_id: state === "UNSCOPED" ? null : who.integration,
      runtime_identity_id: state === "UNSCOPED" ? null : who.runtime,
      last_observed_at: state === "OBSERVED" ? "2026-11-01T12:00:00+00:00" : null,
    },
    verification: { state: "NOT_RUN" },
  };
}

function onboarding(
  over: {
    summary?: OnboardingSummary | null;
    integration?: string | null;
    status?: string;
    allDone?: boolean;
  } = {},
): OnboardingResponse {
  const sum = over.summary === undefined ? summary("UNSCOPED") : over.summary;
  return {
    onboarding: {
      current_step: "organization_profile",
      completed_steps: [],
      first_ai_system_id: "sys-1",
      first_integration_id: over.integration ?? null,
      first_trajectory_id: null,
      status: over.status ?? "IN_PROGRESS",
      welcome_completed: Boolean(sum?.welcome_complete),
      welcome_completed_at: null,
    },
    ...(sum ? { summary: sum } : {}),
    steps: STEP_IDS.map((id) => ({
      id,
      label: id.replace(/_/g, " "),
      done: Boolean(over.allDone),
      actionable: id !== "test_connection" && id !== "production_readiness",
      completion_source: over.allDone ? ("MANUAL" as const) : ("NONE" as const),
    })),
    complete: Boolean(over.allDone),
    progress: { completed: over.allDone ? 13 : 0, total: 13 },
    next_recommended_action: null,
  };
}

function seedRegistry(opts: { lifecycle?: string; runtimes?: unknown[] } = {}) {
  api.getAISystems.mockResolvedValue({ data: [SYSTEM] });
  api.getAgents.mockResolvedValue({ data: [AGENT] });
  api.getRuntimeIdentities.mockResolvedValue({ data: opts.runtimes ?? [RT_A, RT_B] });
  api.getAgentVersions.mockResolvedValue({
    data: [{ id: "v-1", version_number: 1, lifecycle_status: opts.lifecycle ?? "APPROVED" }],
  });
  api.getRuntimeCredentials.mockResolvedValue({
    data: [{ id: "cred-1", credential_key: "rai_abc", status: "ACTIVE" }],
  });
  api.getTrajectories.mockResolvedValue({ data: { items: [] } });
  pub.listSdkIntegrations.mockResolvedValue({ items: [] });
}

function stepperCell(label: string) {
  const cells = Array.from(document.querySelectorAll<HTMLButtonElement>("button[data-state]"));
  const cell = cells.find((item) => item.textContent?.includes(label));
  if (!cell) throw new Error(`no stepper cell ${label}`);
  return cell;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  seedRegistry();
  pub.fetchOnboarding.mockResolvedValue(onboarding());
  pub.patchOnboarding.mockResolvedValue(onboarding());
  pub.completeWelcome.mockResolvedValue({ welcome_completed: true });
});

async function renderReady(canManage = true) {
  render(<GuidedOnboarding canManage={canManage} />);
  await screen.findByTestId("guided-onboarding");
}

describe("read-only access", () => {
  it("shows status with no mutation controls and never writes", async () => {
    pub.fetchOnboarding.mockResolvedValue(
      onboarding({ summary: summary("OBSERVED"), integration: "int-a" }),
    );
    await renderReady(false);

    expect(screen.getByTestId("read-only-notice")).toBeInTheDocument();
    expect(
      screen.getAllByText("Runtime activity observed", { exact: false }).length,
    ).toBeGreaterThan(0);
    for (const name of [
      /create ai system/i,
      /use this integration/i,
      /mark (complete|reviewed)/i,
      /switch/i,
      /issue runtime credential/i,
    ]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(pub.patchOnboarding).not.toHaveBeenCalled();
    expect(pub.completeWelcome).not.toHaveBeenCalled();
  });

  it("lets a reader leave with navigation only", async () => {
    pub.fetchOnboarding.mockResolvedValue(
      onboarding({ summary: summary("OBSERVED", undefined, false), integration: "int-a" }),
    );
    await renderReady(false);
    fireEvent.click(screen.getByRole("button", { name: "Return to dashboard" }));
    expect(push).toHaveBeenCalledWith("/dashboard");
    expect(pub.completeWelcome).not.toHaveBeenCalled();
  });
});

describe("opening the page", () => {
  it("does not PATCH on mount for a manager either", async () => {
    pub.fetchOnboarding.mockResolvedValue(onboarding({ allDone: true }));
    await renderReady(true);
    await waitFor(() => expect(api.getAgents).toHaveBeenCalled());
    expect(pub.patchOnboarding).not.toHaveBeenCalled();
  });

  it("shows a finite error with Retry when the status cannot be loaded", async () => {
    pub.fetchOnboarding.mockRejectedValueOnce(new Error("backend down"));
    render(<GuidedOnboarding canManage />);
    expect(await screen.findByTestId("onboarding-load-error")).toHaveTextContent("backend down");
    expect(screen.getByText(/does not mean setup is incomplete/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("guided-onboarding")).toBeInTheDocument();
  });
});

describe("deferring the connection", () => {
  it("changes navigation only and keeps connection and verification incomplete", async () => {
    await renderReady();
    // Credential exists, nothing connected: lands on Connect.
    expect(await screen.findByRole("button", { name: "Connect later" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Connect later" }));

    expect(pub.patchOnboarding).not.toHaveBeenCalled();
    expect(stepperCell("Connect").dataset.state).toBe("deferred");
    expect(stepperCell("Connect")).toHaveTextContent("Deferred");
    // Reaching Verify does not turn any earlier step green.
    expect(stepperCell("Verify").dataset.state).not.toBe("done");
    expect(screen.getByTestId("verify-summary")).toHaveTextContent("Verification not run");
    expect(screen.getByTestId("evidence-summary")).toHaveTextContent(
      "Select an integration to check activity",
    );
    expect(screen.getByRole("button", { name: /connect application \(Deferred\)/i })).toBeVisible();
  });

  it("reads a legacy connectSkipped flag as a deferral only", async () => {
    window.sessionStorage.setItem(
      "rai_onboarding_wizard_instance",
      JSON.stringify({ systemId: "sys-1", connectSkipped: true }),
    );
    await renderReady();
    expect(stepperCell("Connect").dataset.state).toBe("deferred");
    expect(stepperCell("Connect").dataset.state).not.toBe("done");
  });
});

describe("integration selection", () => {
  it("explains an unlinked runtime, stays unscoped, and still allows deferral", async () => {
    await renderReady();
    expect(await screen.findByTestId("no-integration")).toHaveTextContent(
      /integration must be linked/i,
    );
    expect(screen.getByTestId("connection-status")).toHaveTextContent(
      "Select an integration to check activity",
    );
    expect(screen.getByRole("button", { name: "Connect later" })).toBeEnabled();
  });

  it("lists only integrations for the selected runtime and saves the system with the choice", async () => {
    pub.listSdkIntegrations.mockResolvedValue({ items: [INT_A, INT_B] });
    pub.patchOnboarding.mockResolvedValue(
      onboarding({ summary: summary("OBSERVED"), integration: "int-a" }),
    );
    await renderReady();

    const select = await screen.findByLabelText("Integration");
    const options = within(select)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(options).toContain("Integration A");
    expect(options).not.toContain("Integration B");

    fireEvent.change(select, { target: { value: "int-a" } });
    fireEvent.click(screen.getByRole("button", { name: "Use this integration" }));

    await waitFor(() =>
      expect(pub.patchOnboarding).toHaveBeenCalledWith({
        first_ai_system_id: "sys-1",
        first_integration_id: "int-a",
      }),
    );
    expect(await screen.findByTestId("connection-status")).toHaveTextContent(
      "Runtime activity observed",
    );
  });

  it("keeps the previous selection and shows the error when saving fails", async () => {
    pub.listSdkIntegrations.mockResolvedValue({ items: [INT_A] });
    pub.patchOnboarding.mockRejectedValue(new Error("not allowed"));
    await renderReady();
    fireEvent.change(await screen.findByLabelText("Integration"), { target: { value: "int-a" } });
    fireEvent.click(screen.getByRole("button", { name: "Use this integration" }));

    expect(await screen.findByText("not allowed")).toBeInTheDocument();
    expect(screen.getByTestId("connection-status")).toHaveTextContent(
      "Select an integration to check activity",
    );
  });
});

describe("runtime switching and late responses", () => {
  const observedForA = () => onboarding({ summary: summary("OBSERVED"), integration: "int-a" });

  it("clears A's saved selections when switching to B and never shows A's activity for B", async () => {
    pub.fetchOnboarding.mockResolvedValue(observedForA());
    // Even a stale response that still describes A must not reach B's screen.
    pub.patchOnboarding.mockResolvedValue(observedForA());
    api.getTrajectories.mockResolvedValue({ data: { items: [TRAJ_A] } });
    await renderReady();

    // Starts on Verify with A's activity and trajectory.
    expect(await screen.findByTestId("verify-summary")).toHaveTextContent(
      "Runtime activity observed",
    );
    expect(await screen.findByRole("link", { name: "Inspect Trajectory" })).toBeInTheDocument();

    fireEvent.click(stepperCell("Runtime"));
    fireEvent.change(await screen.findByRole("combobox"), { target: { value: "rt-b" } });
    fireEvent.click(screen.getByRole("button", { name: "Switch" }));

    await waitFor(() =>
      expect(pub.patchOnboarding).toHaveBeenCalledWith({
        complete_step: "runtime_identity",
        first_integration_id: null,
        first_trajectory_id: null,
      }),
    );

    fireEvent.click(stepperCell("Connect"));
    expect(await screen.findByTestId("connection-status")).toHaveTextContent(
      "Select an integration to check activity",
    );
    expect(screen.queryByText("Runtime activity observed")).toBeNull();

    // After a refresh (poll) the server still describes A: still not B's activity.
    await waitFor(() => expect(pub.fetchOnboarding.mock.calls.length).toBeGreaterThan(1));
    expect(screen.queryByText("Runtime activity observed")).toBeNull();

    fireEvent.click(stepperCell("Verify"));
    expect(await screen.findByText(/No trajectory from the selected runtime/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Inspect Trajectory" })).toBeNull();
  });

  it("does not use another runtime's trajectory when none is saved", async () => {
    pub.fetchOnboarding.mockResolvedValue(onboarding({ allDone: true }));
    api.getTrajectories.mockResolvedValue({
      data: { items: [{ ...TRAJ_A, id: "t-x", runtime_identity_id: "rt-other" }] },
    });
    await renderReady();
    fireEvent.click(stepperCell("Verify"));
    expect(await screen.findByText(/No trajectory from the selected runtime/i)).toBeInTheDocument();
  });
});

describe("evidence presentation", () => {
  it("never renders verified or production-ready for legacy READY with a missing summary", async () => {
    pub.fetchOnboarding.mockResolvedValue(
      onboarding({ summary: null, status: "READY", allDone: true }),
    );
    await renderReady();

    expect(await screen.findByTestId("status-unavailable")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/Verification status unavailable/);
    expect(text).not.toMatch(/\bverified\b|production[- ]ready\b|compliant/i);
    // Legacy READY must not even be shown as a status badge.
    expect(screen.queryByText("READY")).toBeNull();
  });

  it("marks governed-decision and manual items with their real basis", async () => {
    const response = onboarding({ summary: summary("OBSERVED"), integration: "int-a" });
    response.steps = response.steps.map((step) =>
      step.id === "first_governed_action"
        ? { ...step, done: true, completion_source: "OBSERVATION" as const }
        : step.id === "inspect_trajectory"
          ? { ...step, done: true, completion_source: "MANUAL" as const }
          : step,
    );
    pub.fetchOnboarding.mockResolvedValue(response);
    await renderReady();
    fireEvent.click(screen.getByRole("button", { name: /first governed action/i }));
    expect(screen.getByText("Governed decision observed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /inspect trajectory/i }));
    expect(screen.getAllByText("Marked complete").length).toBeGreaterThan(0);
  });

  it("offers no manual completion for evidence-only steps", async () => {
    await renderReady();
    for (const label of [
      /connect application/i,
      /test connection/i,
      /first governed action/i,
      /production readiness/i,
    ]) {
      fireEvent.click(screen.getByRole("button", { name: label }));
      expect(screen.queryByRole("button", { name: /mark (complete|reviewed)/i })).toBeNull();
    }
    fireEvent.click(screen.getByRole("button", { name: /inspect trajectory/i }));
    expect(screen.getByRole("button", { name: "Mark reviewed" })).toBeInTheDocument();
  });
});

describe("finishing setup", () => {
  it("completes welcome through its own contract and never writes READY or evidence IDs", async () => {
    pub.fetchOnboarding.mockResolvedValue(
      onboarding({ summary: summary("OBSERVED", undefined, false), integration: "int-a" }),
    );
    await renderReady();
    fireEvent.click(await screen.findByRole("button", { name: "Finish setup" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"));
    expect(pub.completeWelcome).toHaveBeenCalledTimes(1);
    for (const [body] of pub.patchOnboarding.mock.calls) {
      expect(body.status).toBeUndefined();
      expect(JSON.stringify(body)).not.toMatch(
        /production_readiness|test_connection|connect_application|first_governed_action/,
      );
    }
  });

  it("only navigates when welcome is already complete, with verification still not run", async () => {
    pub.fetchOnboarding.mockResolvedValue(
      onboarding({ summary: summary("OBSERVED", undefined, true), integration: "int-a" }),
    );
    await renderReady();
    expect(screen.getByTestId("verify-summary")).toHaveTextContent("Verification not run");
    fireEvent.click(await screen.findByRole("button", { name: "Return to dashboard" }));
    expect(push).toHaveBeenCalledWith("/dashboard");
    expect(pub.completeWelcome).not.toHaveBeenCalled();
    expect(pub.patchOnboarding).not.toHaveBeenCalled();
  });

  it("can finish while the verification checklist remains incomplete", async () => {
    pub.fetchOnboarding.mockResolvedValue(
      onboarding({ summary: summary("NOT_OBSERVED"), integration: "int-a" }),
    );
    await renderReady();
    expect(await screen.findByRole("button", { name: "Finish setup" })).toBeEnabled();
  });
});

describe("explicit lifecycle", () => {
  it("shows the server error and keeps the previous state when a transition fails", async () => {
    seedRegistry({ lifecycle: "DRAFT" });
    api.transitionAgentVersion.mockRejectedValue(new Error("Review is not permitted"));
    await renderReady();

    fireEvent.click(await screen.findByRole("button", { name: "Submit for Review" }));
    expect(await screen.findByText("Review is not permitted")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit for Review" })).toBeInTheDocument();
    expect(api.transitionAgentVersion).toHaveBeenCalledWith("v-1", "UNDER_REVIEW");
  });

  it("renders the state the server returns after a transition", async () => {
    seedRegistry({ lifecycle: "DRAFT" });
    api.transitionAgentVersion.mockResolvedValue({ data: { lifecycle_status: "UNDER_REVIEW" } });
    await renderReady();
    fireEvent.click(await screen.findByRole("button", { name: "Submit for Review" }));
    expect(await screen.findByRole("button", { name: "Approve Version" })).toBeInTheDocument();
  });

  it("blocks runtime creation until the version is approved and never approves for the user", async () => {
    seedRegistry({ lifecycle: "DRAFT", runtimes: [] });
    await renderReady();
    fireEvent.click(stepperCell("Runtime"));
    expect(await screen.findByTestId("runtime-blocked")).toHaveTextContent(
      /blocked until v1 is approved/i,
    );
    expect(screen.getByRole("button", { name: "Create Runtime Identity" })).toBeDisabled();
    expect(api.createRuntimeIdentity).not.toHaveBeenCalled();
    expect(api.transitionAgentVersion).not.toHaveBeenCalled();
    expect(api.transitionAISystem).not.toHaveBeenCalled();
  });

  it("creates a system as a draft without any follow-up transition", async () => {
    api.getAISystems.mockResolvedValue({ data: [] });
    pub.fetchOnboarding.mockResolvedValue({
      ...onboarding(),
      onboarding: { ...onboarding().onboarding, first_ai_system_id: null },
    });
    api.createAISystem.mockResolvedValue({
      data: { id: "sys-new", system_key: "new", name: "New", status: "DRAFT", agent_count: 0 },
    });
    pub.patchOnboarding.mockResolvedValue(onboarding());
    window.sessionStorage.clear();
    await renderReady();

    fireEvent.change(await screen.findByLabelText(/Display name/), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText(/Business unit/), { target: { value: "Ops" } });
    fireEvent.change(screen.getByLabelText(/Owner/), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: "Create AI System" }));

    await waitFor(() => expect(api.createAISystem).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(pub.patchOnboarding).toHaveBeenCalled());
    expect(api.transitionAISystem).not.toHaveBeenCalled();
    // The same PATCH saves the system and clears dependents explicitly.
    expect(pub.patchOnboarding).toHaveBeenCalledWith({
      complete_step: "first_ai_system",
      first_ai_system_id: "sys-new",
      first_integration_id: null,
      first_trajectory_id: null,
    });
  });
});
