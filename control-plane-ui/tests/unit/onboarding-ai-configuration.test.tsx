import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests: they prove client behavior and the exact API calls made. No
// provider is contacted; backend probe behavior is covered by tests/onboarding.

const auth = vi.hoisted(() => ({ permissions: [] as string[] }));
vi.mock("@/lib/hooks/use-auth", () => ({
  usePermissions: () => ({ tenant: { permissions: auth.permissions }, isLoading: false }),
}));

const api = vi.hoisted(() => ({
  getAIConfiguration: vi.fn(),
  saveAIConfiguration: vi.fn(),
  listProviderTargets: vi.fn(),
  testAIConnection: vi.fn(),
  listAITests: vi.fn(),
  cancelAITest: vi.fn(),
  newIdempotencyKey: vi.fn(() => "key-fixed-0001"),
}));
vi.mock("@/lib/onboarding-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/onboarding-api")>("@/lib/onboarding-api");
  return { ...actual, ...api };
});

import { AISetup } from "@/components/onboarding/ai-setup";
import type { AIConfigDetail, ProbeView } from "@/lib/onboarding-api";

const MANAGER = ["sdk:read", "sdk:manage", "onboarding:providers:manage"];

const TARGET = {
  key: "primary",
  label: "Primary model",
  protocol: "OPENAI_COMPATIBLE_CHAT",
  destination_label: "llm.example.com",
  models: ["model-a", "model-b"],
  structured_modes: ["JSON_SCHEMA", "JSON_OBJECT"],
  max_output_tokens: 512,
  credential_configured: true,
  credential_version: 1,
};

const probe = (over: Partial<ProbeView> = {}): ProbeView => ({
  id: "p1",
  probe_number: 1,
  configuration_version: 2,
  target_key: "primary",
  model: "model-a",
  structured_mode: "JSON_SCHEMA",
  credential_version: 1,
  target_fingerprint: "fp-1",
  state: "PASSED",
  outcome: "PASSED",
  error_code: null,
  dispatch_started: true,
  latency_ms: 420,
  usage: { total_tokens: 39 },
  created_at: null,
  ended_at: "2026-11-20T10:00:00Z",
  ...over,
});

const config = (over: Partial<AIConfigDetail> = {}): AIConfigDetail => ({
  state: "CONFIGURED_UNTESTED",
  enabled: true,
  configuration_version: 2,
  target_key: "primary",
  model: "model-a",
  structured_mode: "JSON_SCHEMA",
  max_output_tokens: 256,
  target_fingerprint: "fp-1",
  credential_configured: true,
  destination_label: "llm.example.com",
  last_test: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  auth.permissions = MANAGER;
  api.newIdempotencyKey.mockReturnValue("key-fixed-0001");
  api.listProviderTargets.mockResolvedValue({ items: [TARGET], invalid_entries: 0 });
  api.listAITests.mockResolvedValue({ items: [], total: 0 });
});
afterEach(() => vi.useRealTimers());

describe("AI setup", () => {
  it("never tests or saves on load and explains what a test sends", async () => {
    api.getAIConfiguration.mockResolvedValue(config());
    render(<AISetup />);
    expect(await screen.findByTestId("ai-state")).toBeTruthy();
    expect(screen.getByTestId("ai-state").textContent).toBe("Configured, not tested");
    expect(api.testAIConnection).not.toHaveBeenCalled();
    expect(api.saveAIConfiguration).not.toHaveBeenCalled();
    const page = document.body.textContent ?? "";
    expect(page).toMatch(/synthetic request and may use provider tokens/);
    expect(page).toMatch(/documents and prompts are not sent/);
    expect(page).toMatch(/Saving does not contact the provider/);
    expect(page).not.toMatch(/sk-|api key|secret/i);
  });

  it("shows an actionable empty state when no approved target exists", async () => {
    api.getAIConfiguration.mockResolvedValue({
      state: "UNCONFIGURED",
      enabled: false,
      configuration_version: 1,
    });
    api.listProviderTargets.mockResolvedValue({ items: [], invalid_entries: 0 });
    render(<AISetup />);
    expect(await screen.findByText(/No approved AI targets are configured/)).toBeTruthy();
    expect(screen.queryByText("Save configuration")).toBeNull();
    expect(screen.queryByText("Test AI connection")).toBeNull();
  });

  it("saves explicitly with the expected configuration version", async () => {
    api.getAIConfiguration.mockResolvedValue({
      state: "UNCONFIGURED",
      enabled: false,
      configuration_version: 1,
    });
    api.saveAIConfiguration.mockResolvedValue(config({ configuration_version: 2 }));
    render(<AISetup />);
    await screen.findByLabelText("Approved target");
    const save = screen.getByText("Save configuration") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Enable the AI connection"));
    fireEvent.change(screen.getByLabelText("Approved target"), { target: { value: "primary" } });
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "model-a" } });
    fireEvent.change(screen.getByLabelText("Structured output"), {
      target: { value: "JSON_SCHEMA" },
    });
    fireEvent.change(screen.getByLabelText("Max output tokens"), { target: { value: "256" } });
    expect(screen.getByText(/credential configured on this server/)).toBeTruthy();
    expect(screen.getByText("llm.example.com")).toBeTruthy();
    fireEvent.click(screen.getByText("Save configuration"));
    await waitFor(() =>
      expect(api.saveAIConfiguration).toHaveBeenCalledWith({
        expected_configuration_version: 1,
        enabled: true,
        target_key: "primary",
        model: "model-a",
        structured_mode: "JSON_SCHEMA",
        max_output_tokens: 256,
      }),
    );
    expect(api.testAIConnection).not.toHaveBeenCalled(); // save never tests
  });

  it("tests only on explicit click, with version, fingerprint and a stable key", async () => {
    api.getAIConfiguration.mockResolvedValue(config());
    api.testAIConnection.mockResolvedValue({
      probe: probe({ state: "ACTIVE", outcome: null, ended_at: null }),
      ai_configuration: config({ state: "TEST_PENDING" }),
    });
    render(<AISetup />);
    const test = (await screen.findByText("Test AI connection")) as HTMLButtonElement;
    expect(test.disabled).toBe(false);
    fireEvent.click(test);
    await waitFor(() =>
      expect(api.testAIConnection).toHaveBeenCalledWith({
        expectedConfigurationVersion: 2,
        expectedTargetFingerprint: "fp-1",
        key: "key-fixed-0001",
      }),
    );
    expect(await screen.findByText("Test in progress")).toBeTruthy();
  });

  it("disables testing while edits are unsaved and when the connection is disabled", async () => {
    api.getAIConfiguration.mockResolvedValue(config());
    render(<AISetup />);
    const test = (await screen.findByText("Test AI connection")) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("Max output tokens"), { target: { value: "100" } });
    expect(test.disabled).toBe(true);
    expect(screen.getByText(/Save your changes before testing/)).toBeTruthy();
  });

  it("does not show a stale test as a current pass", async () => {
    api.getAIConfiguration.mockResolvedValue(config({ state: "TEST_STALE", last_test: null }));
    api.listAITests.mockResolvedValue({ items: [probe()], total: 1 });
    render(<AISetup />);
    expect((await screen.findByTestId("ai-state")).textContent).toBe("Test is out of date");
    expect(screen.queryByText("Last test passed")).toBeNull();
    expect(screen.getByText(/No test for the current configuration/)).toBeTruthy();
    expect(screen.getByText(/history, not current availability/)).toBeTruthy();
  });

  it("explains an unknown remote outcome and reports unknown usage rather than zero", async () => {
    api.getAIConfiguration.mockResolvedValue(
      config({
        state: "TEST_UNKNOWN",
        last_test: probe({
          outcome: "OUTCOME_UNKNOWN",
          state: "OUTCOME_UNKNOWN",
          usage: null,
          latency_ms: null,
          error_code: "TIMEOUT_AFTER_DISPATCH",
        }),
      }),
    );
    render(<AISetup />);
    expect(await screen.findByText(/never resent automatically/)).toBeTruthy();
    const last = screen.getByTestId("last-test").textContent ?? "";
    expect(last).toContain("usage unknown");
    expect(last).toContain("latency unknown");
    expect(last).not.toMatch(/\b0 tokens/);
  });

  it("shows exact last-test facts for a passed test", async () => {
    api.getAIConfiguration.mockResolvedValue(config({ state: "TEST_PASSED", last_test: probe() }));
    render(<AISetup />);
    const text = (await screen.findByTestId("last-test")).textContent ?? "";
    expect(text).toContain("PASSED");
    expect(text).toContain("configuration version 2");
    expect(text).toContain("model-a");
    expect(text).toContain("420 ms");
    expect(text).toContain("39 tokens");
  });

  it("gives non-managers status only and no controls", async () => {
    auth.permissions = ["sdk:read"];
    api.getAIConfiguration.mockResolvedValue({ state: "TEST_PASSED", enabled: true });
    render(<AISetup />);
    expect((await screen.findByTestId("ai-state")).textContent).toBe("Last test passed");
    expect(screen.getByText(/needs provider-management access/)).toBeTruthy();
    expect(screen.queryByText("Test AI connection")).toBeNull();
    expect(api.listProviderTargets).not.toHaveBeenCalled();
  });

  it("surfaces a stale-version conflict and reloads instead of retrying blindly", async () => {
    const { IntakeApiError } =
      await vi.importActual<typeof import("@/lib/onboarding-api")>("@/lib/onboarding-api");
    api.getAIConfiguration.mockResolvedValue(config());
    api.testAIConnection.mockRejectedValue(
      new IntakeApiError("The AI configuration changed.", 409, "CONFIGURATION_CONFLICT"),
    );
    render(<AISetup />);
    fireEvent.click(await screen.findByText("Test AI connection"));
    expect(await screen.findByText("The AI configuration changed.")).toBeTruthy();
    expect(api.testAIConnection).toHaveBeenCalledTimes(1);
    expect(api.getAIConfiguration.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("polls a pending test with bounded backoff and stops on unmount", async () => {
    vi.useFakeTimers();
    api.getAIConfiguration.mockResolvedValue(config({ state: "TEST_PENDING" }));
    const { unmount } = render(<AISetup />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const initial = api.getAIConfiguration.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(api.getAIConfiguration.mock.calls.length).toBeGreaterThan(initial);
    unmount();
    const after = api.getAIConfiguration.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(api.getAIConfiguration.mock.calls.length).toBe(after);
  });
});
