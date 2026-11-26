import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests: they prove client behaviour and the exact API calls made, not
// backend creation (that is covered by tests/public_product/test_first_run.py).

const nav = vi.hoisted(() => ({
  router: { replace: vi.fn(), push: vi.fn() },
  pathname: "/welcome",
}));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => nav.pathname,
}));
vi.mock("@/lib/hooks/use-auth", () => ({
  useAuth: () => ({ user: { email: "owner@example.com" } }),
}));

const api = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  resendVerificationEmail: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ ...api }));

const pub = vi.hoisted(() => ({ completeWelcome: vi.fn() }));
vi.mock("@/lib/public-api", () => ({ ...pub }));

import { FirstRunGate } from "@/components/onboarding/first-run/first-run-gate";
import { FirstRunWizard } from "@/components/onboarding/first-run/first-run-wizard";

const status = (over: Record<string, unknown> = {}) => ({
  state: "REQUIRED",
  is_new_organization: true,
  can_setup: true,
  missing_permissions: [],
  should_redirect: true,
  email_verified: true,
  ai: { configured: false, provider: null, model: null, problem: null },
  ...over,
});

const catalog = {
  providers: [
    { value: "google_ai", label: "Google" },
    { value: "anthropic", label: "Anthropic" },
    { value: "openai", label: "OpenAI" },
  ],
  models: [
    { provider: "anthropic", model_name: "claude-sonnet-5-5", label: "Claude Sonnet 5.5" },
    { provider: "anthropic", model_name: "claude-opus-5-5", label: "Claude Opus 5.5" },
    { provider: "openai", model_name: "gpt-5.5", label: "GPT-5.5" },
  ],
  resources: [
    {
      key: "github",
      name: "GitHub",
      category: "code",
      tools: [
        { key: "github.read", name: "Read", risk: "LOW" },
        { key: "github.write", name: "Write", risk: "HIGH" },
      ],
    },
  ],
};

const resourcesResult = {
  models: [{ provider: "anthropic", model_name: "claude-sonnet-5-5", id: "model-1" }],
  resources: [
    {
      ref: "seed:github",
      items: [
        { resource_type: "mcp_server", id: "mcp-1", name: "GitHub" },
        { resource_type: "tool", id: "tool-1", name: "Read" },
      ],
    },
  ],
};

const systemsResult = {
  environment: "development",
  systems: [
    {
      ai_system_id: "sys-1",
      name: "Support",
      system_key: "support",
      agent: { id: "agent-1", name: "Support - agent 01" },
      version: { id: "v-1", label: "Support - agent 01 - V1.0.0" },
      runtime_identity: { id: "rt-1", identity_key: "rti_1", name: "runtime" },
      integration_id: "integration-1",
      credential: { id: "c-1", credential_key: "rai_rt_1", secret: "rai_rt_1.secret" },
      models: [{ provider: "anthropic", model_name: "claude-sonnet-5-5" }],
    },
  ],
};

function mockServer(current = status()) {
  api.apiGet.mockImplementation(async (path: string) => ({
    data: path.endsWith("/catalog") ? catalog : current,
  }));
  api.apiPost.mockImplementation(async (path: string) => {
    if (path.endsWith("/apply/resources")) return { data: resourcesResult };
    if (path.endsWith("/apply/systems")) return { data: systemsResult };
    return { data: status({ state: "SKIPPED", should_redirect: false }) };
  });
}

const posted = (suffix: string) =>
  api.apiPost.mock.calls.filter(([path]) => String(path).endsWith(suffix));

async function defineSystem(name = "Support") {
  fireEvent.change(await screen.findByLabelText("AI system name"), {
    target: { value: name },
  });
  fireEvent.click(screen.getByRole("button", { name: "Anthropic" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Claude Sonnet 5\.5/ }));
}

async function goToReview() {
  await defineSystem();
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /GitHub/ }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  nav.pathname = "/welcome";
  pub.completeWelcome.mockResolvedValue({});
  mockServer();
});

describe("first-run wizard", () => {
  it("previews the generated agent and version names and searches models", async () => {
    render(<FirstRunWizard />);
    await defineSystem("  Customer   Support ");
    expect(screen.getByText("Customer Support - agent 01")).toBeInTheDocument();
    expect(screen.getByText("Customer Support - agent 01 - V1.0.0")).toBeInTheDocument();

    expect(screen.queryByRole("checkbox", { name: /GPT-5\.5/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("Search models"), { target: { value: "opus" } });
    const list = screen.getByRole("list", { name: "Models" });
    expect(within(list).getAllByRole("checkbox")).toHaveLength(1);
  });

  it("adds a custom model and drops it when its provider is removed", async () => {
    render(<FirstRunWizard />);
    await defineSystem();
    fireEvent.change(screen.getByLabelText("Search models"), {
      target: { value: "claude-private-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Add .* as a Anthropic model/ }));
    const chosen = screen.getByRole("list", { name: "Selected models" });
    expect(within(chosen).getByText("claude-private-1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Anthropic" }));
    expect(screen.queryByRole("list", { name: "Selected models" })).toBeNull();
  });

  it("will not continue without a name and a model", async () => {
    render(<FirstRunWizard />);
    await screen.findByLabelText("AI system name");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent("needs a name");

    fireEvent.change(screen.getByLabelText("AI system name"), { target: { value: "Bot" } });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent('at least one model for "Bot"');
    expect(api.apiPost).not.toHaveBeenCalled();
  });

  it("creates resources then systems and shows the credential", async () => {
    render(<FirstRunWizard />);
    await goToReview();
    expect(api.apiPost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByRole("heading", { name: "Setup complete" });
    expect(posted("/apply/resources")[0][1]).toEqual({
      models: [{ provider: "anthropic", model_name: "claude-sonnet-5-5" }],
      seed_servers: [{ key: "github", tool_keys: null }],
      custom: [],
    });
    expect(posted("/apply/systems")[0][1]).toMatchObject({
      systems: [
        {
          name: "Support",
          model_ids: ["model-1"],
          dependencies: [
            { resource_type: "mcp_server", resource_id: "mcp-1" },
            { resource_type: "tool", resource_id: "tool-1" },
          ],
        },
      ],
    });

    // The secret is masked until asked for, and never stored in the browser.
    expect(screen.queryByText("rai_rt_1.secret")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show Agent key" }));
    expect(screen.getByText("rai_rt_1.secret")).toBeInTheDocument();
    expect(JSON.stringify({ ...window.localStorage })).not.toContain("rai_rt_1.secret");
    expect(screen.queryByRole("button", { name: "Skip for now" })).toBeNull();
    await waitFor(() => expect(pub.completeWelcome).toHaveBeenCalledTimes(1));
  });

  it("keeps the same request id when a failed stage is retried", async () => {
    let attempts = 0;
    api.apiPost.mockImplementation(async (path: string) => {
      if (path.endsWith("/apply/resources")) return { data: resourcesResult };
      attempts += 1;
      if (attempts === 1) throw new Error("An AI system named 'Support' already exists.");
      return { data: systemsResult };
    });
    render(<FirstRunWizard />);
    await goToReview();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("already exists");
    expect(screen.getByTestId("stage-resources")).toHaveAttribute("data-status", "done");
    expect(screen.getByTestId("stage-systems")).toHaveAttribute("data-status", "failed");
    expect(pub.completeWelcome).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("heading", { name: "Setup complete" });
    const [first, second] = posted("/apply/systems");
    expect((second[1] as { request_id: string }).request_id).toBe(
      (first[1] as { request_id: string }).request_id,
    );
  });

  it("restores the draft after a reload", async () => {
    const first = render(<FirstRunWizard />);
    await defineSystem("Remembered");
    first.unmount();
    render(<FirstRunWizard />);
    expect(await screen.findByLabelText("AI system name")).toHaveValue("Remembered");
  });

  it("skips to the dashboard", async () => {
    render(<FirstRunWizard />);
    await screen.findByLabelText("AI system name");
    fireEvent.click(screen.getByRole("button", { name: "Skip for now" }));
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(posted("/skip")).toHaveLength(1);
  });

  it("still offers Skip when setup cannot load", async () => {
    api.apiGet.mockRejectedValue(new Error("offline"));
    render(<FirstRunWizard />);
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    expect(screen.getByRole("button", { name: "Skip for now" })).toBeEnabled();
  });

  it("blocks creation until the email is verified", async () => {
    mockServer(status({ email_verified: false }));
    render(<FirstRunWizard />);
    await goToReview();
    expect(screen.getByText(/Verify your email/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("leaves a completed setup", async () => {
    mockServer(status({ state: "COMPLETED", should_redirect: false }));
    render(<FirstRunWizard />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/app/onboarding"));
  });
});

describe("first-run gate", () => {
  const gate = () => (
    <FirstRunGate fallback={<p>checking</p>}>
      <p>console</p>
    </FirstRunGate>
  );

  it("sends a new organization to setup without showing the console", async () => {
    nav.pathname = "/dashboard";
    render(gate());
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/welcome"));
    expect(screen.queryByText("console")).toBeNull();
  });

  it("shows the console otherwise and does not ask again", async () => {
    nav.pathname = "/dashboard";
    mockServer(status({ state: "SKIPPED", should_redirect: false }));
    const view = render(gate());
    expect(await screen.findByText("console")).toBeInTheDocument();
    nav.pathname = "/portfolio";
    view.rerender(gate());
    expect(screen.getByText("console")).toBeInTheDocument();
    expect(api.apiGet).toHaveBeenCalledTimes(1);
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("never locks anyone out when the check fails", async () => {
    nav.pathname = "/dashboard";
    api.apiGet.mockRejectedValue(new Error("offline"));
    render(gate());
    expect(await screen.findByText("console")).toBeInTheDocument();
    expect(nav.router.replace).not.toHaveBeenCalled();
  });

  it("renders the setup page without a check", () => {
    render(gate());
    expect(screen.getByText("console")).toBeInTheDocument();
    expect(api.apiGet).not.toHaveBeenCalled();
  });
});
