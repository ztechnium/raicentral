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
  apiUpload: vi.fn(),
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
  can_add_policies: true,
  content_rules_enforced: true,
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
        { resource_type: "mcp_server", id: "mcp-1", key: "github", name: "GitHub" },
        { resource_type: "tool", id: "tool-1", key: "github.read", name: "Read" },
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

async function goToPolicies() {
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
    await goToPolicies();
    expect(api.apiPost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /^Create( without rules)?$/ }));

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
    await goToPolicies();
    fireEvent.click(screen.getByRole("button", { name: /^Create( without rules)?$/ }));

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
    await goToPolicies();
    expect(screen.getByText(/Verify your email/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Create( without rules)?$/ })).toBeDisabled();
  });

  it("leaves a completed setup", async () => {
    mockServer(status({ state: "COMPLETED", should_redirect: false }));
    render(<FirstRunWizard />);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/app/onboarding"));
  });
});

const extraction = {
  documents: [{ ref: "doc1", title: "policy.md", text: "Never share bank accounts." }],
  rules: [
    {
      family: "content",
      title: "No bank accounts",
      system_refs: [] as string[],
      enabled: true,
      warnings: [],
      document_ref: "doc1",
      quote: "Never share bank accounts.",
      content: {
        content: "Never share bank accounts.",
        effect: "DENY",
        applies_to: "OUTPUT",
        approver_role: null,
      },
    },
    {
      family: "tool",
      title: "Approve reads",
      system_refs: [] as string[],
      enabled: false,
      warnings: ["Disabled: it would apply to every call of the tool."],
      document_ref: "doc1",
      quote: "Reading needs approval.",
      tool: {
        tool_ref: "seed:github|github.read",
        effect: "REQUIRE_APPROVAL",
        approver_role: null,
      },
    },
  ],
  notes: [{ title: "Review quarterly", quote: "Review every quarter.", document: "policy.md" }],
  warnings: [],
};

const policiesResult = {
  rules: [
    {
      family: "content",
      title: "No bank accounts",
      rule_id: "rule-1",
      ai_system_id: "sys-1",
      ai_system_name: "Support",
      enabled: true,
      status: "created",
    },
  ],
};

function mockAi() {
  mockServer(
    status({ ai: { configured: true, provider: "vertex", model: "gemini", problem: null } }),
  );
  api.apiUpload.mockImplementation(async (_path: string, body: FormData) => {
    const context = JSON.parse(String(body.get("context")));
    const ids = context.systems.map((s: { ref: string }) => s.ref);
    return {
      data: {
        ...extraction,
        rules: extraction.rules.map((rule) => ({ ...rule, system_refs: ids })),
      },
    };
  });
}

function mockPolicyStage(policies: () => unknown) {
  api.apiPost.mockImplementation(async (path: string) => {
    if (path.endsWith("/apply/resources")) return { data: resourcesResult };
    if (path.endsWith("/apply/systems")) return { data: systemsResult };
    return { data: policies() };
  });
}

function change(scope: HTMLElement, label: string, value: string) {
  fireEvent.change(within(scope).getByLabelText(label), { target: { value } });
}

function addBudget(limit: string) {
  fireEvent.click(screen.getByRole("button", { name: "Add budget" }));
  const card = screen.getByRole("group", { name: "budget rule 1" });
  change(card, "Rule title", "Daily requests");
  change(card, "Limit", limit);
}

describe("first-run policies", () => {
  it("explains how to turn AI on and still allows manual rules", async () => {
    render(<FirstRunWizard />);
    await goToPolicies();
    expect(screen.getByText("AI extraction is off.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Extract rules" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add content rule" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Create without rules" })).toBeEnabled();
  });

  it("creates manual rules as the third stage with the real ids", async () => {
    mockPolicyStage(() => policiesResult);
    render(<FirstRunWizard />);
    await goToPolicies();
    addBudget("100");
    fireEvent.click(screen.getByRole("button", { name: "Add tool rule" }));
    const tool = screen.getByRole("group", { name: "tool rule 1" });
    change(tool, "Rule title", "Block reads");
    change(tool, "Tool", "seed:github|github.read");
    fireEvent.click(within(tool).getByRole("button", { name: "Add condition" }));
    change(tool, "Condition 1 parameter", "amount");
    change(tool, "Condition 1 type", "number");
    change(tool, "Condition 1 operator", "gt");
    change(tool, "Condition 1 value", "500");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await screen.findByRole("heading", { name: "Setup complete" });
    const body = posted("/apply/policies")[0][1] as { rules: Record<string, unknown>[] };
    expect(body.rules).toMatchObject([
      {
        family: "budget",
        ai_system_ids: ["sys-1"],
        budget: { metric: "ACTION_COUNT", limit_value: 100, window_seconds: 86400 },
      },
      {
        family: "tool",
        ai_system_ids: ["sys-1"],
        tool: {
          tool_id: "tool-1",
          approver_role: "business_approver",
          conditions: [
            { parameter_path: "amount", operator: "gt", value_type: "number", value: 500 },
          ],
        },
      },
    ]);
    expect(screen.getByText("No bank accounts")).toBeInTheDocument();
  });

  it("accepts different tool names and rejects a repeated one", async () => {
    render(<FirstRunWizard />);
    await defineSystem();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "Add your own resource" }));
    const resource = screen.getByRole("group", { name: "Resource 1" });
    change(resource, "Resource name", "Orders CRM");
    change(resource, "Tool names", "Read order, read ORDER");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alert")).toHaveTextContent("lists the same tool twice");

    change(resource, "Tool names", "Create order, Read order");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("button", { name: "Add tool rule" })).toBeEnabled();
  });

  it("blocks creation while a rule is incomplete", async () => {
    render(<FirstRunWizard />);
    await goToPolicies();
    addBudget("0");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(screen.getByRole("alert")).toHaveTextContent("enter a limit above zero");
    expect(api.apiPost).not.toHaveBeenCalled();
  });

  it("drafts rules from policy text without duplicating them", async () => {
    mockAi();
    render(<FirstRunWizard />);
    await goToPolicies();
    const extract = async () => {
      fireEvent.change(screen.getByLabelText("Policy text"), {
        target: { value: "Never share bank accounts." },
      });
      fireEvent.click(screen.getByRole("button", { name: "Extract rules" }));
      await waitFor(() => expect(screen.getByLabelText("Policy text")).toHaveValue(""));
    };
    await extract();
    expect(screen.getByRole("status")).toHaveTextContent("Drafted 2 rules");
    const tool = screen.getByRole("group", { name: "tool rule 1" });
    expect(within(tool).getByRole("checkbox", { name: "Active" })).not.toBeChecked();
    expect(within(tool).getByLabelText("Tool")).toHaveValue("seed:github|github.read");
    expect(screen.getByText(/cannot be enforced as rules/)).toBeInTheDocument();

    await extract();
    expect(screen.getByRole("status")).toHaveTextContent("No new rules");
    expect(screen.getAllByRole("group", { name: /rule \d/ })).toHaveLength(2);
    expect(api.apiPost).not.toHaveBeenCalled();
  });

  it("keeps the credentials reachable when only the rules fail", async () => {
    mockPolicyStage(() => {
      throw new Error("Rule rejected");
    });
    render(<FirstRunWizard />);
    await goToPolicies();
    addBudget("5");
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Rule rejected");
    expect(screen.queryByRole("button", { name: "Skip for now" })).toBeNull();

    // Editing the rules must not create the AI systems a second time.
    fireEvent.click(screen.getByRole("button", { name: "Edit rules" }));
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await screen.findByRole("alert");
    expect(posted("/apply/systems")).toHaveLength(1);
    expect(posted("/apply/policies")).toHaveLength(2);

    fireEvent.click(
      screen.getByRole("button", { name: "Finish without rules and show credentials" }),
    );
    await screen.findByRole("heading", { name: "Setup complete" });
    expect(screen.getByText("No rules of its own yet.")).toBeInTheDocument();
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
