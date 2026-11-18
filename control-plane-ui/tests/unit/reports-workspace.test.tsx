import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReportTabs } from "@/components/reports/report-tabs";
import { QueryPanel } from "@/components/reports/states";
import { ExecutiveView } from "@/components/reports/views/executive-view";
import { RiskView } from "@/components/reports/views/risk-view";
import { RuntimeView } from "@/components/reports/views/runtime-view";
import { AssuranceView } from "@/components/reports/views/assurance-view";
import { ReportsShell } from "@/components/reports/reports-shell";
import type { ViewContext } from "@/components/reports/view-context";
import { DEFAULT_FILTERS, parseFilters, resolvePeriod } from "@/lib/reports/filters";
import { clearReportCache } from "@/lib/reports/use-report-data";
import type { ReportQuery } from "@/lib/reports/use-report-data";

const h = vi.hoisted(() => {
  class ApiErrorStub extends Error {
    constructor(
      message: string,
      public status: number,
      public traceId?: string,
    ) {
      super(message);
    }
  }
  return {
    ApiErrorStub,
    apiGet: vi.fn(),
    apiPost: vi.fn(),
    getAISystems: vi.fn(),
    push: vi.fn(),
    replace: vi.fn(),
    search: { value: "" },
    perms: {
      tenant: { permissions: ["reports:read"] } as { permissions: string[] } | null,
      isLoading: false,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: h.ApiErrorStub,
  apiGet: (...a: unknown[]) => h.apiGet(...a),
  apiPost: (...a: unknown[]) => h.apiPost(...a),
  getAISystems: (...a: unknown[]) => h.getAISystems(...a),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.push, replace: h.replace }),
  usePathname: () => "/reports",
  useSearchParams: () => new URLSearchParams(h.search.value),
}));
vi.mock("@/lib/hooks/use-auth", () => ({
  usePermissions: () => ({ tenant: h.perms.tenant, isLoading: h.perms.isLoading, platform: {} }),
}));

const NOW = new Date("2026-11-18T12:00:00.000Z");
const SYSTEM = "11111111-1111-1111-1111-111111111111";

function ctxFor(search = "", over: Partial<ViewContext> = {}): ViewContext {
  const filters = parseFilters(new URLSearchParams(search));
  return {
    filters,
    period: resolvePeriod(filters, NOW),
    systems: new Map([[SYSTEM, "Support Bot"]]),
    compare: filters.compare,
    canSnapshot: false,
    ...over,
  };
}

const metric = (value: number, extra: object = {}) => ({ value, ...extra });
const envelope = {
  scope: {},
  period: { start: "2026-10-19T12:00:00", end: "2026-11-18T12:00:00" },
  freshness: { mode: "LIVE", last_refreshed_at: "2026-11-18T12:00:00" },
};
const summary = {
  ...envelope,
  ai_systems_governed: metric(3, { change_pct: 50 }),
  ai_systems_registered: metric(10),
  governed_actions: metric(0),
  current_risk_exposure: metric(4),
  policy_intervention_rate: metric(0),
  budget_utilization_pct: null,
  assurance_coverage_pct: null,
  open_critical_findings: metric(1),
  open_high_findings: metric(2),
  new_findings: metric(0),
  risk_by_severity: { critical: 1, high: 2, medium: 0, low: 0, info: 0 },
  policy_effects: { allow: 0, monitor: 0, require_approval: 0, deny: 0, suspend: 0 },
  attention: [
    {
      type: "CRITICAL_RISK_FINDING",
      severity: "CRITICAL",
      title: "Prompt injection",
      ai_system_id: SYSTEM,
      source_type: "RUNTIME_RISK_FINDING",
      source_id: "f1",
      reason: "Open CRITICAL runtime risk finding",
      recommended_action: "Review finding",
      due_at: null,
      drill_down: `/risk/findings?ai_system_id=${SYSTEM}`,
    },
  ],
};
const risk = {
  ...envelope,
  open_findings: 4,
  new_findings: 1,
  resolved_findings: 0,
  critical_findings: 1,
  high_findings: 2,
  mean_time_to_resolution_hours: null,
  severity_distribution: { critical: 1, high: 2, medium: 1, low: 0, info: 0 },
  category_distribution: { PROMPT_INJECTION: 2 },
  status_distribution: { OPEN: 1 },
  risk_by_ai_system: [
    {
      id: SYSTEM,
      name: "Support Bot",
      value: 4,
      severity: "CRITICAL",
      link: `/governance/registry/${SYSTEM}`,
    },
  ],
  risk_by_agent: [],
  risk_by_tool: [],
  risk_by_model: [],
  finding_trends: [{ timestamp: "2026-11-17T00:00:00", value: 1 }],
  findings_by_source: { design_time: 1, runtime: 3, evidence: 0 },
  semantics: { open_findings: "Current state: open right now." },
  unavailable_dimensions: ["tool", "model", "data_source"],
  attention: [],
};
const finops = {
  totals: { total_cost: 12.5, currency: "EUR", cost_state: "METERED", unavailable_cost_count: 2 },
  coverage: { cost_coverage_pct: 80 },
};
const runtime = {
  ...envelope,
  governed_actions: 0,
  executions: 10,
  conversations: 3,
  execution_success_rate: 60,
  policy_evaluations: 0,
  interventions: 0,
  approvals_required: 0,
  denied: 0,
  runtime_suspensions: 0,
  execution_status_distribution: { COMPLETED: 6, ACTIVE: 3, FAILED: 1 },
  approvals_pending: 2,
  activity_trends: [],
};

function route(handlers: Record<string, () => unknown>) {
  h.apiGet.mockImplementation(async (path: string) => {
    const key = Object.keys(handlers).find((prefix) => path.startsWith(prefix));
    if (!key) throw new h.ApiErrorStub(`unmocked ${path}`, 500);
    const value = handlers[key]();
    if (value instanceof Error) throw value;
    return { data: value };
  });
}
const paramsOf = (prefix: string) => {
  const call = h.apiGet.mock.calls.map((c) => c[0] as string).find((p) => p.startsWith(prefix));
  return new URLSearchParams(call?.split("?")[1] ?? "");
};

beforeEach(() => {
  clearReportCache();
  h.apiGet.mockReset();
  h.apiPost.mockReset();
  h.push.mockReset();
  h.replace.mockReset();
  h.getAISystems.mockReset();
  h.getAISystems.mockResolvedValue({ data: [{ id: SYSTEM, name: "Support Bot" }] });
  h.search.value = "";
  h.perms.tenant = { permissions: ["reports:read"] };
  h.perms.isLoading = false;
});

describe("report tabs", () => {
  it("is a labelled tablist with roving tabindex and linked panels", () => {
    render(<ReportTabs active="risk" onChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(6);
    expect(screen.getByRole("tablist", { name: "Report views" })).toBeInTheDocument();
    const selected = screen.getByRole("tab", { name: "Risk Analysis" });
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(selected).toHaveAttribute("tabindex", "0");
    expect(selected).toHaveAttribute("aria-controls", "report-panel-risk");
    for (const tab of tabs.filter((t) => t !== selected)) {
      expect(tab).toHaveAttribute("tabindex", "-1");
      expect(tab).toHaveAttribute("aria-selected", "false");
    }
  });

  it("moves with arrow keys, wraps, and supports Home/End", () => {
    const onChange = vi.fn();
    render(<ReportTabs active="executive" onChange={onChange} />);
    const first = screen.getByRole("tab", { name: "Executive Overview" });
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(onChange).toHaveBeenLastCalledWith("risk");
    fireEvent.keyDown(first, { key: "ArrowLeft" });
    expect(onChange).toHaveBeenLastCalledWith("economics"); // wraps
    fireEvent.keyDown(first, { key: "End" });
    expect(onChange).toHaveBeenLastCalledWith("economics");
    fireEvent.keyDown(first, { key: "Home" });
    expect(onChange).toHaveBeenLastCalledWith("executive");
  });
});

describe("panel states are distinct facts", () => {
  const base = { refetch: vi.fn() };
  const render1 = (query: ReportQuery<{ n: number }>) =>
    render(
      <QueryPanel
        title="Panel"
        query={query}
        isEmpty={(d) => d.n === 0}
        emptyMessage="Nothing recorded."
      >
        {(d) => <span>count {d.n}</span>}
      </QueryPanel>,
    );

  it("shows empty (successful, zero records) separately from errors", () => {
    const { container } = render1({ ...base, status: "success", data: { n: 0 }, error: null });
    expect(container.querySelector('[data-state="empty"]')).toHaveTextContent("Nothing recorded.");
  });

  it("shows a real value, not the empty state, when data exists", () => {
    render1({ ...base, status: "success", data: { n: 5 }, error: null });
    expect(screen.getByText("count 5")).toBeInTheDocument();
  });

  it.each([
    ["not_authorized", "Not authorized", false],
    ["invalid", "These filters can't be applied here", true],
    ["failed", "This report failed to load", true],
  ] as const)("renders %s with its own heading and retry rule", (kind, heading, retry) => {
    const { container } = render1({
      ...base,
      status: "error",
      data: null,
      error: { kind, message: "details here" },
    });
    expect(screen.getByText(heading)).toBeInTheDocument();
    expect(container.querySelector(`[data-state="${kind}"]`)).not.toBeNull();
    expect(Boolean(screen.queryByRole("button", { name: "Try again" }))).toBe(retry);
  });

  it("keeps earlier data visible when only a refresh failed", () => {
    render1({
      ...base,
      status: "error",
      data: { n: 7 },
      error: { kind: "failed", message: "boom" },
    });
    expect(screen.getByText("count 7")).toBeInTheDocument();
    expect(screen.getByText(/showing the last data that loaded/i)).toBeInTheDocument();
  });
});

describe("independent loading", () => {
  it("one failing endpoint does not hide the others", async () => {
    route({
      "/v1/reports/summary": () => new h.ApiErrorStub("summary exploded", 500, "trace-9"),
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
    });
    render(<ExecutiveView ctx={ctxFor()} />);

    // The failed panel says so, with its trace id...
    const attention = await screen.findByTestId("attention-panel");
    await waitFor(() => expect(within(attention).getByRole("alert")).toBeInTheDocument());
    expect(within(attention).getByText(/Trace ID: trace-9/)).toBeInTheDocument();

    // ...while panels fed by other endpoints still render real data.
    expect(await screen.findByText("Support Bot")).toBeInTheDocument(); // top systems (risk)
    expect(await screen.findByText(/12\.50 EUR/)).toBeInTheDocument(); // spend (finops)
  });

  it("maps 403 to not-authorized, not to a generic failure", async () => {
    route({
      "/v1/reports/summary": () => new h.ApiErrorStub("forbidden", 403),
      "/v1/reports/risk": () => new h.ApiErrorStub("forbidden", 403),
      "/v1/finops/dashboard": () => new h.ApiErrorStub("forbidden", 403),
    });
    const { container } = render(<ExecutiveView ctx={ctxFor()} />);
    await waitFor(() =>
      expect(container.querySelector('[data-state="not_authorized"]')).not.toBeNull(),
    );
    expect(screen.queryByText("This report failed to load")).not.toBeInTheDocument();
  });
});

describe("zero is not unknown", () => {
  it("renders real zeros as 0 and a missing denominator as Not assessed", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
    });
    render(<ExecutiveView ctx={ctxFor()} />);
    // Governed activity is a real zero and must not read "Unavailable".
    const actions = (await screen.findByText("Policy enforcement activity")).closest(
      "div",
    )!.parentElement!;
    expect(within(actions).getByText("0")).toBeInTheDocument();
    // Coverage has no defensible value (null) so it says so.
    expect(await screen.findByText("Not assessed")).toBeInTheDocument();
    // Governed systems are shown against their denominator.
    expect(screen.getByText("3 of 10 (30.0%)")).toBeInTheDocument();
  });

  it("never shows unavailable spend as a zero amount", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => ({
        totals: { total_cost: null, currency: "USD", cost_state: "UNAVAILABLE" },
        coverage: { cost_coverage_pct: 0 },
      }),
    });
    render(<ExecutiveView ctx={ctxFor()} />);
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
  });
});

describe("filters reach the right endpoints and only supported ones", () => {
  it("executive requests share one period and AI system; FinOps gets only what it supports", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
    });
    render(
      <ExecutiveView ctx={ctxFor(`ai_system_id=${SYSTEM}&severity=HIGH&effect=DENY&period=7d`)} />,
    );
    await screen.findByText("Requires attention");
    await waitFor(() => expect(h.apiGet).toHaveBeenCalledTimes(3));

    const s = paramsOf("/v1/reports/summary");
    const r = paramsOf("/v1/reports/risk");
    const f = paramsOf("/v1/finops/dashboard");
    for (const p of [s, r, f]) {
      expect(p.get("ai_system_id")).toBe(SYSTEM);
      expect(p.get("period_start")).toBe(s.get("period_start"));
      expect(p.get("period_end")).toBe(s.get("period_end"));
    }
    // Severity/effect are not executive filters, so they are never sent.
    for (const p of [s, r, f]) {
      expect(p.has("risk_severity")).toBe(false);
      expect(p.has("effect")).toBe(false);
    }
    expect(new Set(f.keys())).toEqual(new Set(["period_start", "period_end", "ai_system_id"]));
  });

  it("risk view sends severity/status but runtime view never does", async () => {
    route({ "/v1/reports/risk": () => risk, "/v1/reports/runtime": () => runtime });
    const search = `severity=CRITICAL&status=OPEN&compare=0`;
    const { unmount } = render(<RiskView ctx={ctxFor(`view=risk&${search}`)} />);
    await screen.findByText("Open findings");
    await waitFor(() => expect(h.apiGet).toHaveBeenCalled());
    const riskParams = paramsOf("/v1/reports/risk");
    expect(riskParams.getAll("risk_severity")).toEqual(["CRITICAL"]);
    expect(riskParams.getAll("finding_status")).toEqual(["OPEN"]);
    unmount();

    h.apiGet.mockClear();
    clearReportCache();
    render(<RuntimeView ctx={ctxFor(`view=runtime&${search}`)} />);
    await screen.findByText("Governed actions");
    await waitFor(() => expect(h.apiGet).toHaveBeenCalled());
    const rt = paramsOf("/v1/reports/runtime");
    expect(rt.has("risk_severity")).toBe(false);
    expect(rt.has("finding_status")).toBe(false);
  });

  it("requests the previous period only when comparison is on", async () => {
    route({ "/v1/reports/risk": () => risk });
    const { unmount } = render(<RiskView ctx={ctxFor("view=risk&compare=0")} />);
    await screen.findByText("Open findings");
    await waitFor(() => expect(h.apiGet).toHaveBeenCalledTimes(1));
    unmount();

    h.apiGet.mockClear();
    clearReportCache();
    render(<RiskView ctx={ctxFor("view=risk")} />);
    await waitFor(() => expect(h.apiGet).toHaveBeenCalledTimes(2));
    const ends = h.apiGet.mock.calls.map(
      (c) => new URLSearchParams((c[0] as string).split("?")[1]),
    );
    const [cur, prev] = ends.sort((a, b) =>
      (b.get("period_end") as string).localeCompare(a.get("period_end") as string),
    );
    expect(new Date(prev.get("period_end") as string).getTime()).toBeLessThan(
      new Date(cur.get("period_start") as string).getTime(),
    );
  });
});

describe("honest presentation", () => {
  it("shows tool/model risk as not available instead of an empty chart", async () => {
    route({ "/v1/reports/risk": () => risk });
    render(<RiskView ctx={ctxFor("view=risk&compare=0")} />);
    fireEvent.click(await screen.findByRole("button", { name: "Tool" }));
    expect(await screen.findByText(/Risk by tool is not available/)).toBeInTheDocument();
  });

  it("assurance shows 'not configured' instead of blurred or fake metrics", async () => {
    route({
      "/v1/reports/assurance": () => ({
        ...envelope,
        total_systems: 4,
        unassessed_systems: 4,
        has_assurance_data: false,
        assurance_coverage_pct: 0,
        valid_assurance: 0,
        conditional_assurance: 0,
        expired_assurance: 0,
        failed_assurance: 0,
        gate_evaluations: 0,
        evaluation_pass_rate: null,
        failed_deployment_gates: 0,
        active_waivers: 0,
        expiring_waivers: 0,
        open_exceptions: 0,
        expiring_exceptions: 0,
        active_exceptions: 0,
      }),
    });
    const { container } = render(<AssuranceView ctx={ctxFor("view=assurance")} />);
    expect(
      await screen.findByText("Assurance is not configured for these systems"),
    ).toBeInTheDocument();
    expect(container.querySelector('[data-state="not-configured"]')).not.toBeNull();
    expect(screen.queryByText(/Assurance will be active soon/)).not.toBeInTheDocument();
    expect(screen.queryByText("Assurance coverage")).not.toBeInTheDocument();
    expect(container.querySelector(".blur-sm")).toBeNull();
    expect(screen.getByText(/not a regulatory certification/i)).toBeInTheDocument();
  });

  it("assurance shows real metrics, with null pass rate as 'No evaluations'", async () => {
    route({
      "/v1/reports/assurance": () => ({
        ...envelope,
        total_systems: 4,
        unassessed_systems: 1,
        has_assurance_data: true,
        assurance_coverage_pct: 25,
        valid_assurance: 1,
        conditional_assurance: 1,
        expired_assurance: 1,
        failed_assurance: 0,
        gate_evaluations: 0,
        evaluation_pass_rate: null,
        failed_deployment_gates: 0,
        active_waivers: 2,
        expiring_waivers: 1,
        open_exceptions: 0,
        expiring_exceptions: 0,
        active_exceptions: 0,
      }),
    });
    render(<AssuranceView ctx={ctxFor("view=assurance")} />);
    expect(await screen.findByText("25.0%")).toBeInTheDocument();
    expect(screen.getByText("No evaluations")).toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
  });

  it("runtime separates in-flight work from failures", async () => {
    route({ "/v1/reports/runtime": () => runtime });
    render(<RuntimeView ctx={ctxFor("view=runtime&compare=0")} />);
    expect(
      await screen.findByText(/3 still active are neither successes nor failures/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/6 of 10 \(60\.0%\) completed/).length).toBeGreaterThanOrEqual(1);
  });

  it("every KPI carries a definition", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
    });
    render(<ExecutiveView ctx={ctxFor()} />);
    await screen.findByText("Requires attention");
    const tiles = document.querySelectorAll("[aria-describedby^='kpi-']");
    expect(tiles.length).toBeGreaterThanOrEqual(6);
    tiles.forEach((tile) => {
      const def = document.getElementById(tile.getAttribute("aria-describedby")!);
      expect(def?.textContent?.length ?? 0).toBeGreaterThan(20);
    });
  });

  it("drill-down links on data points are real anchors", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
    });
    render(<ExecutiveView ctx={ctxFor()} />);
    const link = await screen.findByRole("link", { name: /Open Prompt injection/ });
    expect(link).toHaveAttribute("href", `/risk/findings?ai_system_id=${SYSTEM}`);
    const bar = await screen.findByRole("link", { name: /Support Bot: 4 findings/ });
    expect(bar).toHaveAttribute("href", `/governance/registry/${SYSTEM}`);
  });
});

describe("workspace shell", () => {
  it("makes no requests and says why when the role lacks reports:read", async () => {
    h.perms.tenant = { permissions: ["finops:read"] };
    render(<ReportsShell />);
    expect(await screen.findByText("You don't have access to reports")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(h.apiGet).not.toHaveBeenCalled();
  });

  it("switches live when the permission arrives", async () => {
    h.perms.tenant = { permissions: ["finops:read"] };
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
      "/v1/reports/snapshots": () => [],
    });
    const { rerender } = render(<ReportsShell />);
    expect(await screen.findByText("You don't have access to reports")).toBeInTheDocument();

    h.perms.tenant = { permissions: ["reports:read"] };
    rerender(<ReportsShell />);
    expect(await screen.findByRole("tablist", { name: "Report views" })).toBeInTheDocument();
    expect(screen.queryByText("You don't have access to reports")).not.toBeInTheDocument();
  });

  it("selecting a tab pushes a deep-linkable URL; filters replace it", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
      "/v1/reports/snapshots": () => [],
    });
    render(<ReportsShell />);
    fireEvent.click(await screen.findByRole("tab", { name: "Risk Analysis" }));
    expect(h.push).toHaveBeenCalledWith("/reports?view=risk", { scroll: false });

    fireEvent.change(screen.getByLabelText("Period"), { target: { value: "7d" } });
    expect(h.replace).toHaveBeenCalledWith("/reports?period=7d", { scroll: false });
  });

  it("restores view and filters from the URL and labels the tabpanel", async () => {
    h.search.value = `view=runtime&period=90d&ai_system_id=${SYSTEM}`;
    route({ "/v1/reports/runtime": () => runtime, "/v1/reports/snapshots": () => [] });
    render(<ReportsShell />);
    const panel = await screen.findByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", "report-tab-runtime");
    expect(screen.getByRole("tab", { name: "Runtime Governance" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect((screen.getByLabelText("Period") as HTMLSelectElement).value).toBe("90d");
    const windowLine = screen.getByText(/Reporting window:/).closest("p");
    expect(windowLine).toHaveTextContent(/Reporting window:.*–.*\(.+\)/);
    expect(windowLine).toHaveTextContent(/Previous:/);
  });

  it("only offers filters the active view applies", async () => {
    h.search.value = "view=runtime";
    route({ "/v1/reports/runtime": () => runtime, "/v1/reports/snapshots": () => [] });
    render(<ReportsShell />);
    await screen.findByRole("tabpanel");
    expect(screen.getByLabelText("Period")).toBeInTheDocument();
    expect(screen.getByLabelText("AI system")).toBeInTheDocument();
    expect(screen.queryByLabelText("Severity")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Matched rule effect")).not.toBeInTheDocument();
  });

  it("warns when an active filter is not applied on the current view", async () => {
    h.search.value = "view=runtime&severity=HIGH";
    route({ "/v1/reports/runtime": () => runtime, "/v1/reports/snapshots": () => [] });
    render(<ReportsShell />);
    expect(await screen.findByText(/Not applied on this view: severity/)).toBeInTheDocument();
  });

  it("only shows the snapshot save action to callers who may create snapshots", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
      "/v1/reports/snapshots": () => [],
    });
    const { rerender } = render(<ReportsShell />);
    expect(await screen.findByText(/needs the reports:snapshot permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save snapshot/ })).not.toBeInTheDocument();

    h.perms.tenant = { permissions: ["reports:read", "reports:snapshot"] };
    rerender(<ReportsShell />);
    expect(
      await screen.findByRole("button", { name: /Save snapshot of this view/ }),
    ).toBeInTheDocument();
  });

  it("saves a snapshot with the same effective scope the view shows", async () => {
    h.perms.tenant = { permissions: ["reports:read", "reports:snapshot"] };
    h.search.value = `view=risk&ai_system_id=${SYSTEM}&severity=HIGH&compare=0`;
    route({ "/v1/reports/risk": () => risk, "/v1/reports/snapshots": () => [] });
    h.apiPost.mockResolvedValue({ data: { id: "s1", content_digest: "a".repeat(64) } });
    render(<ReportsShell />);
    fireEvent.click(await screen.findByRole("button", { name: /Save snapshot of this view/ }));
    await waitFor(() => expect(h.apiPost).toHaveBeenCalled());
    const [path, body] = h.apiPost.mock.calls[0] as [
      string,
      { report_type: string; filters: Record<string, unknown> },
    ];
    expect(path).toBe("/v1/reports/snapshots");
    expect(body.report_type).toBe("RISK_REPORT");
    expect(body.filters.ai_system_id).toBe(SYSTEM);
    expect(body.filters.risk_severity).toEqual(["HIGH"]);
    expect(body.filters.period_start).toBeTruthy();
    expect(await screen.findByText(/Snapshot saved/)).toBeInTheDocument();
  });

  it("has no inert export button", async () => {
    route({
      "/v1/reports/summary": () => summary,
      "/v1/reports/risk": () => risk,
      "/v1/finops/dashboard": () => finops,
      "/v1/reports/snapshots": () => [],
    });
    render(<ReportsShell />);
    await screen.findByRole("tablist");
    expect(screen.queryByRole("button", { name: /export|download|csv/i })).not.toBeInTheDocument();
    // Every visible button does something real.
    for (const label of ["Refresh", "Copy link"]) {
      expect(screen.getByRole("button", { name: label })).toBeEnabled();
    }
  });
});

describe("defaults", () => {
  it("default state is the executive view over 30 days with comparison on", () => {
    expect(DEFAULT_FILTERS).toMatchObject({ view: "executive", period: "30d", compare: true });
  });
});
