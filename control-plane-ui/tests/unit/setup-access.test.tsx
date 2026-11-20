import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocked UI tests (permission context and welcome fetch are stubbed).

const perms = vi.hoisted(() => ({
  value: {
    tenant: null as { permissions: string[]; canWrite?: boolean } | null,
    platform: { roles: [] as string[], isPlatformAdmin: false },
    isLoading: false,
    error: null as string | null,
    refreshPermissions: vi.fn(),
  },
}));
vi.mock("@/lib/hooks/use-auth", () => ({ usePermissions: () => perms.value }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return { ApiError };
});

const pub = vi.hoisted(() => ({ fetchWelcome: vi.fn() }));
vi.mock("@/lib/public-api", () => ({ ...pub }));

import { SetupEntry } from "@/components/dashboard/setup-entry";
import { SetupGate } from "@/components/onboarding/setup-gate";

function setPerms(over: Partial<typeof perms.value>) {
  perms.value = { ...perms.value, ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  perms.value = {
    tenant: { permissions: ["sdk:read", "sdk:manage"] },
    platform: { roles: [], isPlatformAdmin: false },
    isLoading: false,
    error: null,
    refreshPermissions: vi.fn(),
  };
  pub.fetchWelcome.mockResolvedValue({ welcome_completed: false });
});

describe("dashboard setup entry", () => {
  it("shows Set up RaiCentral to a bootstrap administrator, even when platform admin", async () => {
    // isPlatformAdmin must not hide setup: only exact permissions matter.
    setPerms({ platform: { roles: ["platform_admin"], isPlatformAdmin: true } });
    render(<SetupEntry />);
    const link = await screen.findByRole("link", { name: "Set up RaiCentral" });
    expect(link).toHaveAttribute("href", "/welcome");
  });

  it("shows Continue setup once welcome is complete", async () => {
    pub.fetchWelcome.mockResolvedValue({ welcome_completed: true });
    render(<SetupEntry />);
    expect(await screen.findByRole("link", { name: "Continue setup" })).toHaveAttribute(
      "href",
      "/app/onboarding",
    );
  });

  it("shows View setup status to a reader and never the welcome flow", async () => {
    setPerms({ tenant: { permissions: ["sdk:read"], canWrite: true } });
    render(<SetupEntry />);
    expect(await screen.findByRole("link", { name: "View setup status" })).toHaveAttribute(
      "href",
      "/app/onboarding",
    );
    expect(screen.queryByRole("link", { name: "Set up RaiCentral" })).toBeNull();
  });

  it("shows nothing for a platform-only user and does not fetch welcome", () => {
    setPerms({
      tenant: { permissions: [] },
      platform: { roles: ["platform_admin"], isPlatformAdmin: true },
    });
    render(<SetupEntry />);
    expect(screen.queryByTestId("setup-entry")).toBeNull();
    expect(pub.fetchWelcome).not.toHaveBeenCalled();
  });

  it("does not guess while permissions load", () => {
    setPerms({ isLoading: true, tenant: null });
    render(<SetupEntry />);
    expect(screen.queryByTestId("setup-entry")).toBeNull();
    expect(pub.fetchWelcome).not.toHaveBeenCalled();
  });

  it("offers Retry when the permission context failed", () => {
    setPerms({ error: "boom", tenant: { permissions: [] } });
    render(<SetupEntry />);
    expect(screen.getByText("Setup status unavailable.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(perms.value.refreshPermissions).toHaveBeenCalled();
  });

  it("treats a failed welcome fetch as unknown, not as incomplete", async () => {
    pub.fetchWelcome.mockRejectedValueOnce(new Error("down"));
    render(<SetupEntry />);
    expect(await screen.findByText("Setup status unavailable.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Set up RaiCentral" })).toBeNull();
    pub.fetchWelcome.mockResolvedValue({ welcome_completed: false });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: "Set up RaiCentral" })).toBeInTheDocument();
  });
});

describe("setup gate", () => {
  const child = () => <div data-testid="child">setup</div>;

  it("opens for a bootstrap administrator", () => {
    render(<SetupGate requireManage>{() => child()}</SetupGate>);
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("shows a loading state without rendering children", () => {
    setPerms({ isLoading: true, tenant: null });
    render(<SetupGate>{() => child()}</SetupGate>);
    expect(screen.getByText("Checking your access…")).toBeInTheDocument();
    expect(screen.queryByTestId("child")).toBeNull();
  });

  it("gives a platform-only user a finite state with a dashboard link", () => {
    setPerms({
      tenant: { permissions: [] },
      platform: { roles: ["platform_admin"], isPlatformAdmin: true },
    });
    render(<SetupGate>{() => child()}</SetupGate>);
    expect(screen.getByTestId("setup-gate-notice")).toHaveTextContent(/not available/i);
    expect(screen.getByRole("link", { name: "Go to dashboard" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
  });

  it("reports a failed permission fetch as unavailable, with Retry", () => {
    setPerms({ error: "boom", tenant: { permissions: [] } });
    render(<SetupGate>{() => child()}</SetupGate>);
    expect(screen.getByTestId("setup-gate-notice")).toHaveTextContent(
      /does not mean setup is incomplete/i,
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(perms.value.refreshPermissions).toHaveBeenCalled();
  });

  it("lets a reader view status but not enter the welcome flow", () => {
    setPerms({ tenant: { permissions: ["sdk:read"] } });
    const { unmount } = render(<SetupGate requireManage>{() => child()}</SetupGate>);
    expect(screen.queryByTestId("child")).toBeNull();
    expect(screen.getByRole("link", { name: "View setup status" })).toBeInTheDocument();
    unmount();

    render(
      <SetupGate>{({ canManage }) => <div data-testid="mode">{String(canManage)}</div>}</SetupGate>,
    );
    expect(screen.getByTestId("mode")).toHaveTextContent("false");
  });
});
