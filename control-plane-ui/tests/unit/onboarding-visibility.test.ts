import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getAISystems: vi.fn(),
  getAgentVersions: vi.fn(),
  getAgents: vi.fn(),
  getRaiRulesUnified: vi.fn(),
  getRuntimeIdentities: vi.fn(),
}));
vi.mock("@/lib/api", () => api);

import { hasCompleteOnboardingInventory } from "@/lib/onboarding-visibility";

beforeEach(() => {
  vi.clearAllMocks();
  api.getAISystems.mockResolvedValue({ data: [{ id: "system-1" }] });
  api.getAgents.mockResolvedValue({ data: [{ id: "agent-1" }] });
  api.getAgentVersions.mockResolvedValue({ data: [{ id: "version-1" }] });
  api.getRuntimeIdentities.mockResolvedValue({ data: [{ id: "runtime-1" }] });
  api.getRaiRulesUnified.mockResolvedValue({ data: { total: 1 } });
});

describe("onboarding inventory visibility", () => {
  it("is complete only when systems, agents, versions, runtimes and rules exist", async () => {
    await expect(hasCompleteOnboardingInventory()).resolves.toBe(true);
    expect(api.getRaiRulesUnified).toHaveBeenCalledWith({ limit: 1 });
  });

  it.each([
    ["AI system", () => api.getAISystems.mockResolvedValue({ data: [] })],
    ["runtime", () => api.getRuntimeIdentities.mockResolvedValue({ data: [] })],
    ["rule", () => api.getRaiRulesUnified.mockResolvedValue({ data: { total: 0 } })],
    ["agent", () => api.getAgents.mockResolvedValue({ data: [] })],
    ["version", () => api.getAgentVersions.mockResolvedValue({ data: [] })],
  ])("remains visible while a %s is missing", async (_label, arrange) => {
    arrange();
    await expect(hasCompleteOnboardingInventory()).resolves.toBe(false);
  });

  it("surfaces API errors instead of treating unknown inventory as complete", async () => {
    api.getAISystems.mockRejectedValue(new Error("registry unavailable"));
    await expect(hasCompleteOnboardingInventory()).rejects.toThrow("registry unavailable");
  });
});
