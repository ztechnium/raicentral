import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { budget, dashboard, emptyDashboard, item, timeseries, totals } from "./finops-fixtures";

const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: new URLSearchParams(),
  pathname: "/finops",
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
  return {
    ApiError,
    getFinOpsDashboard: vi.fn(),
    getFinOpsTimeseries: vi.fn(),
    getFinOpsBreakdown: vi.fn(),
    getFinOpsBudgets: vi.fn(),
  };
});

import * as api from "@/lib/api";
import FinOpsOverviewPage from "@/app/(app)/finops/page";

const ok = <T,>(data: T) => Promise.resolve({ data }) as never;
const budgetsOf = (items = [budget()]) => ({
  as_of: "2026-11-18T10:00:00Z",
  items,
  summary: { total: items.length, exceeded: 0, warning: 1, healthy: 0, unavailable: 0, no_active_window: 0 },
});

function mockAll(overrides: { dash?: unknown; budgets?: unknown } = {}) {
  vi.mocked(api.getFinOpsDashboard).mockReturnValue((overrides.dash ?? ok(dashboard())) as never);
  vi.mocked(api.getFinOpsTimeseries).mockReturnValue(ok(timeseries()));
  vi.mocked(api.getFinOpsBreakdown).mockReturnValue(
    ok({ dimension: "ai_system", items: [item()], total: 1, limit: 8, offset: 0, sort: "cost", order: "desc", currency: "USD", currencies: ["USD"] }),
  );
  vi.mocked(api.getFinOpsBudgets).mockReturnValue((overrides.budgets ?? ok(budgetsOf())) as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = new URLSearchParams();
});

describe("FinOps overview", () => {
  it("leads with known spend and keeps billed and estimated separate", async () => {
    mockAll();
    render(<FinOpsOverviewPage />);
    const known = await screen.findByTestId("card-known-spend");
    expect(within(known).getByText("$12.50")).toBeInTheDocument();
    expect(within(known).getByText("Mixed")).toBeInTheDocument(); // cost-state badge, as text
    expect(within(screen.getByTestId("card-billed")).getByText("$10.00")).toBeInTheDocument();
    const estimated = screen.getByTestId("card-estimated");
    expect(within(estimated).getByText(/^≈ \$0\.50/)).toBeInTheDocument(); // sub-$1 amounts keep 4 decimals
    expect(estimated).toHaveTextContent(/not confirmed incurred spend/i);
  });

  it("shows a genuine $0.00 billed amount instead of unavailable", async () => {
    mockAll({
      dash: ok(
        dashboard({
          totals: totals({ cost_state: "BILLED", total_cost: 0, billed_cost: 0, metered_cost: null, actual_cost: 0, estimated_cost: null }),
        }),
      ),
    });
    render(<FinOpsOverviewPage />);
    const billed = await screen.findByTestId("card-billed");
    expect(within(billed).getByText("$0.00")).toBeInTheDocument();
    expect(billed).not.toHaveTextContent(/unavailable|none recorded/i);
    expect(within(screen.getByTestId("card-known-spend")).getByText("$0.00")).toBeInTheDocument();
  });

  it("distinguishes no data from zero", async () => {
    mockAll({ dash: ok(emptyDashboard()), budgets: ok(budgetsOf([])) });
    render(<FinOpsOverviewPage />);
    const known = await screen.findByTestId("card-known-spend");
    expect(within(known).getAllByText("No data").length).toBeGreaterThan(0);
    expect(known).not.toHaveTextContent("$0");
    expect(within(screen.getByTestId("card-coverage")).getByText("—")).toBeInTheDocument();
    expect(screen.getByTestId("attention-empty")).toBeInTheDocument();
  });

  it("marks known spend as a floor when calls are unpriced", async () => {
    mockAll({
      dash: ok(
        dashboard({
          totals: totals({ cost_complete: false, unavailable_cost_count: 3, span_counts: { total: 20, billed: 10, metered: 5, estimated: 2, unavailable: 3 } }),
          coverage: { ...dashboard().coverage, unavailable_pct: 15, cost_coverage_pct: 85 },
        }),
      ),
    });
    render(<FinOpsOverviewPage />);
    expect(await screen.findByText(/3 calls have no price, so this is a floor/)).toBeInTheDocument();
    // Needs-attention links the gap to the filtered, unpriced usage.
    const link = await screen.findByRole("link", { name: "See unpriced usage" });
    expect(link).toHaveAttribute("href", expect.stringContaining("cost_state=UNAVAILABLE"));
  });

  it("makes budget pressure a link to the budgets view", async () => {
    mockAll();
    render(<FinOpsOverviewPage />);
    const card = await screen.findByTestId("card-budget-pressure");
    expect(card).toHaveTextContent("1 warning · 1 exceeded");
    expect(card).toHaveAttribute("href", "/finops/budgets");
  });

  it("labels risk spend as correlation and prevented spend as a counterfactual", async () => {
    mockAll();
    render(<FinOpsOverviewPage />);
    await screen.findByTestId("risk-spend");
    expect(screen.getByText(/correlated cost, not a measure of loss/i)).toBeInTheDocument();
    expect(screen.getByTestId("prevented-spend")).toHaveTextContent("≈ $0.75");
    expect(screen.getByText(/not realized savings and no invoice was reduced/i)).toBeInTheDocument();
  });

  it("does not combine currencies and lists each separately", async () => {
    mockAll({
      dash: ok(
        dashboard({
          totals: totals({
            currency: "MIXED",
            currencies: ["EUR", "USD"],
            total_cost: null,
            billed_cost: null,
            by_currency: [
              { currency: "EUR", total_cost: 3, billed_cost: null, metered_cost: 3, estimated_cost: null, cost_state: "METERED", spans: 4 },
              { currency: "USD", total_cost: 1, billed_cost: null, metered_cost: 1, estimated_cost: null, cost_state: "METERED", spans: 2 },
            ],
          }),
        }),
      ),
    });
    render(<FinOpsOverviewPage />);
    expect(await screen.findByText(/priced in more than one currency/i)).toBeInTheDocument();
    expect(screen.getByText(/EUR: .*3\.00 known across 4 calls/)).toBeInTheDocument();
    expect(within(screen.getByTestId("card-known-spend")).getByText("Mixed currencies")).toBeInTheDocument();
  });

  it("survives a partial failure: a 403 on budgets does not blank the page", async () => {
    mockAll({ budgets: Promise.reject(new (api.ApiError as never as new (m: string, s: number) => Error)("denied", 403)) });
    render(<FinOpsOverviewPage />);
    expect(await screen.findByTestId("card-known-spend")).toBeInTheDocument();
    expect(await screen.findByText(/does not have access to budget health/i)).toBeInTheDocument();
    // The attention list says it may be incomplete rather than claiming all is well.
    await waitFor(() => expect(screen.getByTestId("attention-empty")).toHaveTextContent(/may be incomplete/i));
  });

  it("shows an error with retry when the summary fails, while other widgets still load", async () => {
    mockAll({ dash: Promise.reject(new Error("boom")) });
    render(<FinOpsOverviewPage />);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => /unable to load spend summary/i.test(a.textContent ?? ""))).toBe(true);
    expect(screen.getAllByRole("button", { name: "Retry" }).length).toBeGreaterThan(0);
    expect(await screen.findByRole("img", { name: /known spend/i })).toBeInTheDocument();
  });

  it("passes the URL filters to every query and rejects unsupported ones visibly", async () => {
    nav.search = new URLSearchParams("period=7d&model=gpt-4o&ai_system_id=nope");
    mockAll();
    render(<FinOpsOverviewPage />);
    await screen.findByTestId("card-known-spend");
    const query = vi.mocked(api.getFinOpsDashboard).mock.calls[0][0] as Record<string, string>;
    expect(query.model).toBe("gpt-4o");
    expect(query.ai_system_id).toBeUndefined();
    expect(screen.getByText(/AI system filter ignored: not a valid id/)).toBeInTheDocument();
    const days = (Date.parse(query.period_end) - Date.parse(query.period_start)) / 86_400_000;
    expect(days).toBe(7);
  });

  it("provides a text alternative for the trend chart", async () => {
    mockAll();
    render(<FinOpsOverviewPage />);
    expect(await screen.findByText("View data table")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /known spend over 2 days/i })).toBeInTheDocument();
  });
});
