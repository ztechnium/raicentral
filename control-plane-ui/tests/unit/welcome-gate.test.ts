import { beforeEach, describe, expect, it } from "vitest";
import {
  markWelcomeDoneLocally,
  postLoginPath,
  readWelcomeDoneLocally,
  resolveSetupAccess,
  setupAction,
  welcomeDestination,
} from "@/lib/welcome-gate";

const BOOTSTRAP = ["sdk:read", "sdk:manage", "registry:write", "policies:write"];

describe("setup access (exact permissions)", () => {
  it("lets a bootstrap administrator with sdk:read and sdk:manage open setup", () => {
    expect(resolveSetupAccess({ loading: false, permissions: BOOTSTRAP })).toBe("manage");
  });

  it("gives a reader status only", () => {
    expect(resolveSetupAccess({ loading: false, permissions: ["sdk:read"] })).toBe("read");
  });

  it("does not treat sdk:manage alone, or other manage/write grants, as setup read access", () => {
    expect(
      resolveSetupAccess({ loading: false, permissions: ["registry:write", "risk:manage"] }),
    ).toBe("unavailable");
  });

  it("reaches a finite unavailable state for a platform-only user with no product permissions", () => {
    expect(resolveSetupAccess({ loading: false, permissions: [] })).toBe("unavailable");
  });

  it("never infers access while permissions are loading", () => {
    expect(resolveSetupAccess({ loading: true, permissions: null })).toBe("loading");
    expect(resolveSetupAccess({ loading: true, permissions: BOOTSTRAP })).toBe("loading");
    expect(resolveSetupAccess({ loading: false, permissions: null })).toBe("loading");
  });

  it("reports a failed context fetch as an error, not as 'no permission'", () => {
    // The provider leaves a fallback tenant with an empty permission list on failure.
    expect(resolveSetupAccess({ loading: false, error: "boom", permissions: [] })).toBe("error");
  });
});

describe("dashboard setup action", () => {
  it("offers Set up RaiCentral while welcome is incomplete", () => {
    expect(setupAction({ access: "manage", welcomeComplete: false })).toEqual({
      label: "Set up RaiCentral",
      href: "/welcome",
    });
  });

  it("offers Continue setup once welcome is complete", () => {
    expect(setupAction({ access: "manage", welcomeComplete: true })).toEqual({
      label: "Continue setup",
      href: "/app/onboarding",
    });
  });

  it("offers View setup status to readers, never the welcome flow", () => {
    for (const welcomeComplete of [true, false]) {
      expect(setupAction({ access: "read", welcomeComplete })).toEqual({
        label: "View setup status",
        href: "/app/onboarding",
      });
    }
  });

  it("offers nothing in loading, error or unavailable states", () => {
    for (const access of ["loading", "error", "unavailable"] as const) {
      expect(setupAction({ access, welcomeComplete: false })).toBeNull();
    }
  });
});

describe("routing policy", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("always lands on the dashboard after login", () => {
    expect(postLoginPath()).toBe("/dashboard");
  });

  it("never forces anyone through /welcome", () => {
    for (const welcomeCompleted of [false, null]) {
      for (const pathname of ["/dashboard", "/governance/registry", "/welcome"]) {
        expect(welcomeDestination({ welcomeCompleted, pathname })).toBeNull();
      }
    }
  });

  it("keeps a completed installation on its requested route", () => {
    expect(welcomeDestination({ welcomeCompleted: true, pathname: "/dashboard" })).toBeNull();
    expect(
      welcomeDestination({ welcomeCompleted: true, pathname: "/governance/registry" }),
    ).toBeNull();
  });

  it("sends a completed installation off the full-screen welcome page", () => {
    expect(welcomeDestination({ welcomeCompleted: true, pathname: "/welcome" })).toBe(
      "/app/onboarding",
    );
  });

  it("does not let a stale local flag override server status", () => {
    markWelcomeDoneLocally();
    expect(readWelcomeDoneLocally()).toBe(true);
    // The server says incomplete: the local hint changes nothing.
    expect(welcomeDestination({ welcomeCompleted: false, pathname: "/welcome" })).toBeNull();
  });

  it("keeps the storage key earlier builds wrote", () => {
    markWelcomeDoneLocally();
    expect(window.sessionStorage.getItem("rai_welcome_done_instance")).toBe("1");
  });
});
