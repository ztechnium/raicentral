import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { item } from "./finops-fixtures";

const nav = vi.hoisted(() => ({
  replace: vi.fn(),
  search: new URLSearchParams(),
  pathname: "/finops/usage",
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
  return { ApiError, getFinOpsBreakdown: vi.fn(), getFinOpsTrajectories: vi.fn() };
});

import * as api from "@/lib/api";
import FinOpsUsagePage from "@/app/(app)/finops/usage/page";

const page = (items = [item()], over: Record<string, unknown> = {}) =>
  Promise.resolve({
    data: {
      dimension: "ai_system",
      items,
      total: items.length,
      limit: 25,
      offset: 0,
      sort: "cost",
      order: "desc",
      currency: "USD",
      currencies: ["USD"],
      ...over,
    },
  }) as never;

const lastCall = () => vi.mocked(api.getFinOpsBreakdown).mock.calls.at(-1)?.[0] as Record<string, unknown>;

beforeEach(() => {
  vi.clearAllMocks();
  nav.search = new URLSearchParams();
});

describe("Usage & Costs explorer", () => {
  it("shows unattributed and unpriced rows instead of hiding or zeroing them", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(
      page([
        item(),
        item({
          key: null,
          label: "Unattributed",
          cost: null,
          billed_cost: null,
          estimated_cost: null,
          cost_state: "UNAVAILABLE",
          calls: 4,
          priced_calls: 0,
          unpriced_calls: 4,
          unpriced_share_pct: 100,
          tokens: null,
          input_tokens: null,
          output_tokens: null,
          avg_cost_per_call: null,
        }),
      ]),
    );
    render(<FinOpsUsagePage />);
    const row = (await screen.findByText("Unattributed")).closest("tr") as HTMLElement;
    // Cost is unknown (a value and a state badge both say so), and it is not rendered as $0.
    expect(within(row).getAllByText("Unavailable")).toHaveLength(2);
    expect(row).not.toHaveTextContent("$0");
    expect(within(row).getByText("100%")).toBeInTheDocument();
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(2); // tokens + avg
    const priced = screen.getByText("Support Bot").closest("tr") as HTMLElement;
    expect(within(priced).getByText("+ 1 unpriced call")).toBeInTheDocument();
  });

  it("asks for the selected group-by, sort and a bounded page from the server", async () => {
    nav.search = new URLSearchParams("dimension=model&sort=calls&order=asc&page=3&model=gpt");
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page([item({ label: "gpt-4o" })], { total: 80, offset: 50 }));
    render(<FinOpsUsagePage />);
    await screen.findByText("gpt-4o");
    expect(lastCall()).toMatchObject({ dimension: "model", sort: "calls", order: "asc", limit: 25, offset: 50, model: "gpt" });
    expect(screen.getByText("Showing 51–75 of 80")).toBeInTheDocument();
    expect(screen.getByText("Page 3 of 4")).toBeInTheDocument();
  });

  it("resets to the first page when the group-by changes", async () => {
    nav.search = new URLSearchParams("page=2&dimension=model");
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page());
    render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    fireEvent.click(screen.getByRole("button", { name: "Provider" }));
    expect(nav.replace).toHaveBeenCalledTimes(1);
    const target = nav.replace.mock.calls[0][0] as string;
    expect(target).toContain("dimension=provider");
    expect(target).not.toContain("page=");
  });

  it("pages forward and back through the URL", async () => {
    nav.search = new URLSearchParams("page=2");
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page([item()], { total: 60 }));
    render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(nav.replace.mock.calls.at(-1)?.[0]).toContain("page=3");
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(nav.replace.mock.calls.at(-1)?.[0]).not.toContain("page=");
  });

  it("offers search only for dimensions the server can search", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page());
    const { unmount } = render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    expect(screen.queryByLabelText(/^Search /)).not.toBeInTheDocument();
    unmount();

    nav.search = new URLSearchParams("dimension=model&q=gpt");
    render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    expect(screen.getByLabelText("Search model")).toHaveValue("gpt");
    expect(lastCall()).toMatchObject({ dimension: "model", search: "gpt" });
  });

  it("never sends a stale search to a dimension that cannot search", async () => {
    nav.search = new URLSearchParams("dimension=agent&q=leftover");
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page());
    render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    expect(lastCall().search).toBeUndefined();
  });

  it("hides cost columns for mixed currencies and lets the user choose one", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(
      page([item({ cost: null, billed_cost: null, estimated_cost: null, currency: "MIXED", avg_cost_per_call: null })], {
        currency: "MIXED",
        currencies: ["EUR", "USD"],
      }),
    );
    render(<FinOpsUsagePage />);
    expect(await screen.findByText(/hidden because this data spans several currencies/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show EUR" }));
    expect(nav.replace.mock.calls.at(-1)?.[0]).toContain("currency=EUR");
  });

  it("explains an empty result instead of showing zeros", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page([], { total: 0 }));
    render(<FinOpsUsagePage />);
    expect(await screen.findByTestId("explorer-empty")).toHaveTextContent(/no usage was recorded/i);
    expect(screen.getByText("0 results")).toBeInTheDocument();
  });

  it("tells the user when a 403 blocks the data and when it fails", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(
      Promise.reject(new (api.ApiError as never as new (m: string, s: number) => Error)("no", 403)),
    );
    const { unmount } = render(<FinOpsUsagePage />);
    expect(await screen.findByText(/does not have access to usage and costs/i)).toBeInTheDocument();
    unmount();

    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(Promise.reject(new Error("down")) as never);
    render(<FinOpsUsagePage />);
    expect(await screen.findByText(/unable to load usage and costs/i)).toBeInTheDocument();
  });

  it("wraps very long model names rather than overflowing", async () => {
    const long = "anthropic.claude-3-5-sonnet-20241022-v2:0-" + "x".repeat(80);
    nav.search = new URLSearchParams("dimension=model");
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page([item({ label: long })]));
    render(<FinOpsUsagePage />);
    const cell = await screen.findByText(long);
    expect(cell.className).toContain("break-words");
  });

  it("explains units so calls are not mistaken for trajectories", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page());
    render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    for (const term of ["Provider request", "Model call", "Tool call", "Governed action", "Trajectory", "Conversation"]) {
      expect(screen.getByText(term)).toBeInTheDocument();
    }
  });

  it("cannot drill into an unattributed row and says why", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page([item({ key: null, label: "Unattributed", cost: null })]));
    render(<FinOpsUsagePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Details for Unattributed" }));
    expect(await screen.findByTestId("finops-drilldown")).toHaveTextContent(/nothing to filter on/i);
    expect(api.getFinOpsTrajectories).not.toHaveBeenCalled();
  });

  it("drills a row into its trajectories with the current filters preserved", async () => {
    nav.search = new URLSearchParams("period=7d&provider=openai");
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page([item({ key: "sys-1" })]));
    vi.mocked(api.getFinOpsTrajectories).mockReturnValue(
      Promise.resolve({
        data: {
          items: [{ trajectory_id: "abcdef12-0000-4000-8000-000000000000", cost: 1.5, tokens: 900, started_at: null }],
          total: 4,
          limit: 10,
          offset: 0,
        },
      }) as never,
    );
    render(<FinOpsUsagePage />);
    const button = await screen.findByRole("button", { name: "Details for Support Bot" });
    fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    const link = await screen.findByRole("link", { name: "abcdef12…" });
    expect(link).toHaveAttribute("href", "/runtime/trajectories/abcdef12-0000-4000-8000-000000000000");
    expect(api.getFinOpsTrajectories).toHaveBeenCalledWith(
      expect.objectContaining({ ai_system_id: "sys-1", provider: "openai", limit: 10 }),
    );
    expect(screen.getByText("Showing 1 of 4 trajectories.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in registry" })).toHaveAttribute(
      "href",
      "/governance/registry/sys-1",
    );
    fireEvent.click(screen.getByRole("button", { name: "Filter everything to this AI system" }));
    expect(nav.replace.mock.calls.at(-1)?.[0]).toContain("ai_system_id=sys-1");
  });

  it("is keyboard operable: group-by controls report their pressed state", async () => {
    vi.mocked(api.getFinOpsBreakdown).mockReturnValue(page());
    render(<FinOpsUsagePage />);
    await screen.findByText("Support Bot");
    expect(screen.getByRole("button", { name: "AI system" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Model" })).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => expect(screen.getByRole("group", { name: "Group by" })).toBeInTheDocument());
  });
});
