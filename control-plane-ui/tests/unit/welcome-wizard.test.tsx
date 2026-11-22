import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests: they prove client behavior and the exact API calls made, not
// backend enforcement (that is covered by tests/public_product).

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/welcome",
  useSearchParams: () => new URLSearchParams(),
}));

const api = vi.hoisted(() => ({
  createAISystem: vi.fn(),
  createAgent: vi.fn(),
  createAgentVersion: vi.fn(),
  transitionAISystem: vi.fn(),
  transitionAgentVersion: vi.fn(),
  downloadCatalogTemplate: vi.fn(),
  importCatalogWorkbook: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ ...api }));

const pub = vi.hoisted(() => ({
  fetchWelcome: vi.fn(),
  saveWelcomeProfile: vi.fn(),
  seedWelcomeTemplate: vi.fn(),
  completeWelcome: vi.fn(),
  patchOnboarding: vi.fn(),
}));
vi.mock("@/lib/public-api", () => ({ ...pub }));

import { WelcomeWizard } from "@/components/onboarding/welcome-wizard";

function welcomeState(completed = false) {
  return {
    welcome_completed: completed,
    welcome_completed_at: null,
    organization: { name: "Acme", industry: "Technology", logo: null },
    setup: null,
    templates: [
      {
        key: "support-ai",
        name: "Support AI",
        description: "Sample support assistant",
        tools: ["create_support_ticket"],
        model_name: "gpt",
      },
    ],
  };
}

async function openManualForm() {
  render(<WelcomeWizard />);
  await screen.findByTestId("welcome-wizard");
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(await screen.findByRole("button", { name: "Manual input" }));
  fireEvent.change(await screen.findByLabelText("AI system"), { target: { value: "Support Bot" } });
  fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "Triage" } });
}

beforeEach(() => {
  vi.clearAllMocks();
  pub.fetchWelcome.mockResolvedValue(welcomeState());
  pub.saveWelcomeProfile.mockResolvedValue(welcomeState());
  pub.completeWelcome.mockResolvedValue({ welcome_completed: true });
  pub.patchOnboarding.mockResolvedValue({});
  api.createAISystem.mockResolvedValue({ data: { id: "sys-1", name: "Support Bot" } });
  api.createAgent.mockResolvedValue({ data: { id: "ag-1" } });
  api.createAgentVersion.mockResolvedValue({ data: { id: "ver-1" } });
});

describe("manual creation keeps drafts", () => {
  it("sends no lifecycle transition and shows that review is required", async () => {
    await openManualForm();
    fireEvent.click(screen.getByRole("button", { name: "Create drafts" }));

    const banner = await screen.findByTestId("draft-created");
    expect(banner).toHaveTextContent("Draft created — review required before runtime setup");
    expect(screen.getByRole("link", { name: "Review the version" })).toHaveAttribute(
      "href",
      "/governance/registry/versions/ver-1",
    );
    expect(api.transitionAISystem).not.toHaveBeenCalled();
    expect(api.transitionAgentVersion).not.toHaveBeenCalled();
    expect(pub.patchOnboarding).toHaveBeenCalledWith({
      complete_step: "agent_version",
      first_ai_system_id: "sys-1",
    });
    // No status is ever sent from here.
    expect(JSON.stringify(pub.patchOnboarding.mock.calls)).not.toMatch(/READY|status/);
  });

  it("retains created records after a partial failure and resumes without duplicates", async () => {
    api.createAgent.mockRejectedValueOnce(new Error("agent key taken"));
    await openManualForm();
    fireEvent.click(screen.getByRole("button", { name: "Create drafts" }));

    expect(await screen.findByText(/agent key taken/)).toHaveTextContent(
      /AI system was created; the agent and version still need to be created/,
    );
    expect(screen.queryByTestId("draft-created")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Retry — resume with existing records/ }));
    expect(await screen.findByTestId("draft-created")).toBeInTheDocument();
    expect(api.createAISystem).toHaveBeenCalledTimes(1);
    expect(api.createAgent).toHaveBeenCalledTimes(2);
    expect(api.createAgentVersion).toHaveBeenCalledTimes(1);
  });

  it("reuses the agent when only the version failed", async () => {
    api.createAgentVersion.mockRejectedValueOnce(new Error("version rejected"));
    await openManualForm();
    fireEvent.click(screen.getByRole("button", { name: "Create drafts" }));
    expect(await screen.findByText(/version still needs to be created/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Retry — resume with existing records/ }));
    await screen.findByTestId("draft-created");
    expect(api.createAISystem).toHaveBeenCalledTimes(1);
    expect(api.createAgent).toHaveBeenCalledTimes(1);
    expect(api.createAgentVersion).toHaveBeenCalledTimes(2);
  });

  it("describes the real review → runtime → credential sequence", async () => {
    await openManualForm();
    fireEvent.click(screen.getByRole("button", { name: "Create drafts" }));
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));

    const text = document.body.textContent ?? "";
    expect(text).toMatch(/Runtime creation is blocked until that approval has happened/);
    expect(text).toMatch(/Explicitly issue a credential/);
    expect(text).not.toMatch(/issued on the next page/);
  });
});

describe("finishing welcome", () => {
  it("uses the welcome-complete contract and writes no READY or evidence step", async () => {
    await openManualForm();
    fireEvent.click(screen.getByRole("button", { name: "Create drafts" }));
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Finish setup" }));

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/app/onboarding"));
    expect(pub.completeWelcome).toHaveBeenCalledTimes(1);
    const bodies = JSON.stringify(pub.patchOnboarding.mock.calls);
    expect(bodies).not.toMatch(
      /READY|production_readiness|test_connection|connect_application|first_governed_action/,
    );
  });
});

describe("loading", () => {
  it("shows a finite error with Retry, not an empty wizard, when welcome cannot load", async () => {
    pub.fetchWelcome.mockRejectedValueOnce(new Error("unreachable"));
    render(<WelcomeWizard />);
    expect(await screen.findByTestId("welcome-load-error")).toHaveTextContent("unreachable");
    expect(screen.queryByTestId("welcome-wizard")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("welcome-wizard")).toBeInTheDocument();
  });

  it("moves a completed installation on to onboarding instead of repeating welcome", async () => {
    pub.fetchWelcome.mockResolvedValue(welcomeState(true));
    render(<WelcomeWizard />);
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/app/onboarding"));
  });
});

describe("sample data", () => {
  it("labels seeding as sample data and keeps its explicit endpoint", async () => {
    pub.seedWelcomeTemplate.mockResolvedValue({
      ...welcomeState(),
      seeded: {
        template_key: "support-ai",
        tool_name: "create_support_ticket",
        integration_id: "i",
      },
    });
    render(<WelcomeWizard />);
    await screen.findByTestId("welcome-wizard");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText(/sample\/demo data/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Support AI/ }));
    await waitFor(() => expect(pub.seedWelcomeTemplate).toHaveBeenCalledWith("support-ai"));
    expect(api.createAISystem).not.toHaveBeenCalled();
  });
});
