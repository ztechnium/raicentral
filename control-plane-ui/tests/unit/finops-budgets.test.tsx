import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { budget } from "./finops-fixtures";

const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: new URLSearchParams(),
  pathname: "/finops/budgets",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => nav.pathname,
  useSearchParams: () => nav.search,
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
    ) {
      super(message);
    }
  }
  return { ApiError, getFinOpsBudgets: vi.fn() };
});

import * as api from "@/lib/api";
import FinOpsBudgetsPage from "@/app/(app)/finops/budgets/page";

const health = (items: ReturnType<typeof budget>[]) =>
  Promise.resolve({
    data: {
      as_of: "2026-11-18T10:00:00Z",
      items,
      summary: { total: items.length, exceeded: 0, warning: 1, healthy: 0, unavailable: 0, no_active_window: 1 },
    },
  }) as never;

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = new URLSearchParams();
});

describe("Budgets & Forecasting", () => {
  it("keeps budget types apart, each with its own unit", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(
      health([
        budget(),
        budget({ budget_id: "b2", name: "Token cap", metric: "TOKEN_USAGE", unit: "tokens", limit_value: 1_000_000 }),
      ]),
    );
    render(<FinOpsBudgetsPage />);
    const actions = await screen.findByRole("heading", { name: /Action count budgets/ });
    expect(actions).toHaveTextContent("measured in actions");
    expect(screen.getByRole("heading", { name: /Token usage budgets/ })).toHaveTextContent("measured in tokens");
    // No cross-metric total is ever rendered.
    expect(screen.getByTestId("budget-summary")).not.toHaveTextContent(/total (usage|spend)/i);
  });

  it("shows usage, limit, headroom, window, threshold and links to the policy record", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(health([budget()]));
    render(<FinOpsBudgetsPage />);
    const card = await screen.findByTestId("budget-card");
    expect(card).toHaveTextContent("100 actions"); // limit
    expect(card).toHaveTextContent("90 actions"); // used
    expect(card).toHaveTextContent("10 actions"); // headroom
    expect(card).toHaveTextContent("24 hours");
    expect(card).toHaveTextContent(/warning at 80%/);
    expect(within(card).getByRole("link", { name: "Daily orders" })).toHaveAttribute("href", "/risk/budgets/b1");
    const bar = within(card).getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "90");
  });

  it("links to the existing policy workflow instead of duplicating an editor", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(health([budget()]));
    render(<FinOpsBudgetsPage />);
    await screen.findByTestId("budget-card");
    expect(screen.getByRole("link", { name: "Manage budget policies" })).toHaveAttribute("href", "/risk/budgets");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save|create|delete/i })).not.toBeInTheDocument();
  });

  it("separates enforced from configured and from monitor-only", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(
      health([
        budget({ budget_id: "e", name: "Enforced one" }),
        budget({
          budget_id: "u",
          name: "Unproven one",
          evidence: { lookback_days: 30, checks: 0, last_check_at: null, blocking_interventions: 0 },
          enforcement_summary: "Configured to DENY, but no enforcement check was recorded in the last 30 days.",
        }),
        budget({
          budget_id: "m",
          name: "Monitor one",
          blocking: false,
          effect_on_exceed: "MONITOR",
          enforcement_summary: "Monitor only: exceeding the limit is recorded, not blocked.",
        }),
      ]),
    );
    render(<FinOpsBudgetsPage />);
    const cards = await screen.findAllByTestId("budget-card");
    const byName = (name: string) => cards.find((c) => c.textContent?.includes(name)) as HTMLElement;
    expect(byName("Enforced one")).toHaveTextContent(/Enforced · on exceed: Deny/);
    expect(byName("Unproven one")).toHaveTextContent(/Configured, no checks recorded/);
    expect(byName("Monitor one")).toHaveTextContent(/Monitor only/);
  });

  it("states the projection method and when the limit would be hit", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(health([budget()]));
    render(<FinOpsBudgetsPage />);
    const projection = await screen.findByTestId("projection");
    expect(projection).toHaveTextContent(/projected to use 180 actions \(180% of limit\)/i);
    expect(projection).toHaveTextContent(/before the window resets/);
    expect(projection).toHaveTextContent(/linear run-rate/i);
    expect(projection).toHaveTextContent(/confidence high/);
    expect(projection).toHaveTextContent("7.5 actions / hour");
  });

  it("says insufficient data rather than guessing", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(
      health([
        budget({
          projection: {
            method: "linear_run_rate_within_window",
            status: "insufficient_data",
            observation_seconds: 120,
            burn_rate_per_hour: null,
            projected_window_usage: null,
            projected_utilization_pct: null,
            projected_exhaustion_at: null,
            will_exceed_before_reset: null,
            confidence: null,
            reason: "Less than 10% of the window (and under 10 minutes) has elapsed.",
          },
        }),
      ]),
    );
    render(<FinOpsBudgetsPage />);
    expect(await screen.findByText("Insufficient data for a projection")).toBeInTheDocument();
    expect(screen.getByText(/Less than 10% of the window/)).toBeInTheDocument();
    expect(screen.queryByTestId("projection")).not.toBeInTheDocument();
  });

  it("does not assume a budget with no active window is healthy", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(
      health([budget({ status: "NO_ACTIVE_WINDOW", window: null, projection: null })]),
    );
    render(<FinOpsBudgetsPage />);
    const card = await screen.findByTestId("budget-card");
    expect(card).toHaveTextContent("No active window");
    expect(card).not.toHaveTextContent(/Healthy/);
    expect(card).toHaveTextContent(/nothing to project/);
  });

  it("states currency units honestly for cost budgets", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(
      health([budget({ metric: "COST", unit: "currency", limit_value: 50 })]),
    );
    render(<FinOpsBudgetsPage />);
    expect((await screen.findByTestId("budget-card")).textContent).toContain("50 currency units");
    expect(screen.getByTestId("budget-card").textContent).not.toContain("$");
  });

  it("explains an empty state and 403 differently", async () => {
    vi.mocked(api.getFinOpsBudgets).mockReturnValue(health([]));
    const { unmount } = render(<FinOpsBudgetsPage />);
    expect(await screen.findByText(/No enabled budgets are configured/)).toBeInTheDocument();
    unmount();

    vi.mocked(api.getFinOpsBudgets).mockReturnValue(
      Promise.reject(new (api.ApiError as never as new (m: string, s: number) => Error)("no", 403)),
    );
    render(<FinOpsBudgetsPage />);
    expect(await screen.findByText(/does not have access to budgets/i)).toBeInTheDocument();
  });
});
