import { afterEach, describe, expect, it, vi } from "vitest";

describe("API authentication headers", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
    window.localStorage.clear();
  });

  it("sends the user session and never a shared admin token", async () => {
    // Even a build that still defines the old variable must not leak or send it.
    vi.stubEnv("NEXT_PUBLIC_ADMIN_TOKEN", "backend-secret");
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const api = await import("@/lib/api");
    api.persistSessionTokens("user-jwt", "refresh-token");
    await api.getAuthMe();

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer user-jwt");
    expect(Object.keys(headers).map((name) => name.toLowerCase())).not.toContain("x-admin-token");
    expect(JSON.stringify(init)).not.toContain("backend-secret");
  });
});
