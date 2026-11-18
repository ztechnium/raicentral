import { expect, test, type Page } from "@playwright/test";
import { seedAdminSession } from "./session";

/**
 * FinOps workspace against mocked API responses (no backend needed): checks the pages render
 * with real layout at desktop and phone widths, that nothing overflows horizontally, and that
 * the filter/tab/keyboard flows work in a browser. Screenshots land in test-results/finops.
 */

const totals = {
  total_cost: 12.5,
  billed_cost: 10,
  metered_cost: 2,
  actual_cost: 12,
  estimated_cost: 0.5,
  unavailable_cost_count: 3,
  span_counts: { total: 23, billed: 10, metered: 8, estimated: 2, unavailable: 3 },
  cost_complete: false,
  currency: "USD",
  currencies: ["USD"],
  by_currency: null,
  cost_state: "MIXED",
  total_tokens: 5000,
  input_tokens: 3000,
  output_tokens: 2000,
  cached_tokens: null,
  reasoning_tokens: null,
  token_reported_spans: 20,
  model_calls: 20,
  tool_calls: 3,
  trajectories: 5,
  conversations: 3,
  known_cost_trajectories: 4,
  known_cost_conversations: 3,
  avg_cost_per_trajectory: 3.125,
  avg_cost_per_conversation: 4.1666,
  avg_cost_denominator: "trajectories_and_conversations_with_known_cost",
  avg_tokens_per_trajectory: 1000,
};

const dashboard = {
  period: { start: "2026-10-19T00:00:00Z", end: "2026-11-18T00:00:00Z" },
  previous_period: { start: "2026-09-19T00:00:00Z", end: "2026-10-19T00:00:00Z" },
  totals,
  previous_totals: { total_cost: 10, cost_state: "MIXED", currency: "USD", total_tokens: 4000 },
  comparison: { total_cost_change_pct: 25, total_tokens_change_pct: 25, like_for_like: true, basis: "" },
  coverage: {
    span_count: 23,
    cost_coverage_pct: 87,
    billed_pct: 43.5,
    metered_pct: 34.8,
    actual_pct: 78.3,
    estimated_pct: 8.7,
    unavailable_pct: 13,
    token_coverage_pct: 100,
    model_attribution_pct: 100,
    tenant_attribution_pct: 20,
    system_attribution_pct: 91,
    unattributed_pct: 9,
  },
  budget_pressure: { healthy: 2, warning: 1, exceeded: 1, unavailable: 0, no_active_window: 0, total: 4 },
  risk_associated_spend: 1.25,
  risk_breakdown: {},
  prevented_estimated_spend: 0.75,
  prevented_estimated_currency: "USD",
  definition: {
    risk_associated_spend: "Known cost of trajectories with a HIGH or CRITICAL finding or an intervention.",
    prevented_estimated_spend: "Estimated reservation cost for requests denied before execution.",
  },
};

const long = "anthropic.claude-3-5-sonnet-20241022-v2:0-regional-inference-profile-with-a-very-long-name";
const rows = [
  { key: "a", label: "Support Bot", cost: 4.2, billed_cost: 4, metered_cost: null, estimated_cost: 0.2, cost_state: "MIXED", calls: 10, model_calls: 8, tool_calls: 2, trajectories: 4, priced_calls: 9, unpriced_calls: 1, unpriced_share_pct: 10, tokens: 2000, input_tokens: 1500, output_tokens: 500, cached_tokens: null, reasoning_tokens: null, token_reported_calls: 10, avg_cost_per_call: 0.4667, avg_cost_per_trajectory: 1.05 },
  { key: null, label: "Unattributed", cost: null, billed_cost: null, metered_cost: null, estimated_cost: null, cost_state: "UNAVAILABLE", calls: 4, model_calls: 4, tool_calls: 0, trajectories: 2, priced_calls: 0, unpriced_calls: 4, unpriced_share_pct: 100, tokens: null, input_tokens: null, output_tokens: null, cached_tokens: null, reasoning_tokens: null, token_reported_calls: 0, avg_cost_per_call: null, avg_cost_per_trajectory: null },
  { key: "c", label: long, cost: 0.0000465, billed_cost: null, metered_cost: 0.0000465, estimated_cost: null, cost_state: "METERED", calls: 1, model_calls: 1, tool_calls: 0, trajectories: 1, priced_calls: 1, unpriced_calls: 0, unpriced_share_pct: 0, tokens: 12, input_tokens: 10, output_tokens: 2, cached_tokens: null, reasoning_tokens: null, token_reported_calls: 1, avg_cost_per_call: 0.0000465, avg_cost_per_trajectory: 0.0000465 },
].map((row) => ({ ...row, currency: "USD" }));

const budgets = {
  as_of: "2026-11-18T10:00:00Z",
  summary: { total: 2, exceeded: 1, warning: 0, healthy: 0, unavailable: 0, no_active_window: 1 },
  items: [
    {
      budget_id: "b1", name: "Daily orders", metric: "ACTION_COUNT", unit: "actions", scope_type: "RUNTIME_IDENTITY",
      scope_id: null, ai_system_id: "a", ai_system_name: "Support Bot", tool_name: "create_order", limit_value: 100,
      window_seconds: 86400, warning_threshold: 0.8, effect_on_exceed: "DENY", enforcement_mode: "MANDATORY", blocking: true,
      enforcement_summary: "Configured to DENY; 12 checks recorded in the last 30 days.",
      evidence: { lookback_days: 30, checks: 12, last_check_at: "2026-11-18T09:00:00Z", blocking_interventions: 1 },
      window: { start: "2026-11-18T00:00:00Z", end: "2026-11-19T00:00:00Z", usage: 112, reserved: 0, headroom: -12, utilization_pct: 112, scope_id: "r1" },
      projection: { method: "linear_run_rate_within_window", status: "projected", observation_seconds: 36000, burn_rate_per_hour: 11.2, projected_window_usage: 268.8, projected_utilization_pct: 268.8, projected_exhaustion_at: "2026-11-18T08:56:00Z", will_exceed_before_reset: true, confidence: "high", reason: "Assumes the average rate so far continues until this window resets." },
      status: "EXCEEDED",
    },
    {
      budget_id: "b2", name: "Token cap", metric: "TOKEN_USAGE", unit: "tokens", scope_type: "AI_SYSTEM", scope_id: null,
      ai_system_id: null, ai_system_name: null, tool_name: null, limit_value: 1000000, window_seconds: 3600,
      warning_threshold: 0.8, effect_on_exceed: "MONITOR", enforcement_mode: "BEST_EFFORT", blocking: false,
      enforcement_summary: "Monitor only: exceeding the limit is recorded, not blocked.",
      evidence: { lookback_days: 30, checks: 0, last_check_at: null, blocking_interventions: 0 },
      window: null, projection: null, status: "NO_ACTIVE_WINDOW",
    },
  ],
};

async function mockFinOps(page: Page) {
  const json = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route(/\/v1\/finops\/dashboard/, (r) => r.fulfill(json(dashboard)));
  await page.route(/\/v1\/finops\/timeseries/, (r) =>
    r.fulfill(
      json({
        period: dashboard.period, bucket: "day", currency: "USD", currencies: ["USD"], total_cost: 12.5,
        points: Array.from({ length: 14 }, (_, i) => ({
          timestamp: new Date(Date.UTC(2026, 10, 1 + i)).toISOString(),
          cost: i === 5 ? null : 0.5 + (i % 4), billed_cost: i === 5 ? null : 0.4 + (i % 3), metered_cost: i === 5 ? null : 0.1,
          estimated_cost: i % 5 === 0 ? 0.2 : null, cost_state: i === 5 ? "UNAVAILABLE" : "MIXED",
          priced_spans: i === 5 ? 0 : 3, unavailable_spans: i === 5 ? 2 : 0, tokens: 100, trajectories: 2, model_calls: 3,
        })),
      }),
    ),
  );
  await page.route(/\/v1\/finops\/breakdown/, (r) =>
    r.fulfill(json({ dimension: "ai_system", items: rows, total: 3, limit: 25, offset: 0, sort: "cost", order: "desc", currency: "USD", currencies: ["USD"] })),
  );
  await page.route(/\/v1\/finops\/budgets/, (r) => r.fulfill(json(budgets)));
  await page.route(/\/v1\/finops\/trajectories/, (r) =>
    r.fulfill(json({ items: [{ trajectory_id: "abcdef12-0000-4000-8000-000000000000", cost: 1.5, tokens: 900, started_at: null }], total: 1, limit: 10, offset: 0 })),
  );
  await page.route(/\/v1\/finops\/pricing(\?|$)/, (r) => r.fulfill(json({ items: [] })));
  await page.route(/\/v1\/finops\/pricing\/cursor/, (r) => r.fulfill({ status: 404, contentType: "application/json", body: "{}" }));
}

const SIZES = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

for (const size of SIZES) {
  test.describe(`FinOps (${size.name})`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    test.beforeEach(async ({ page }) => {
      await seedAdminSession(page);
      await mockFinOps(page);
    });

    for (const [path, heading] of [
      ["/finops", "AI economics"],
      ["/finops/usage", "Usage & costs"],
      ["/finops/budgets", "Budgets & forecasting"],
    ] as const) {
      test(`${path} renders without horizontal page overflow`, async ({ page }) => {
        await page.goto(path);
        await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
        await page.waitForLoadState("networkidle");
        const overflow = await page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        );
        expect(overflow).toBeLessThanOrEqual(1);
        await page.screenshot({
          path: `test-results/finops/${size.name}-${path.replace(/\W+/g, "_")}.png`,
          fullPage: true,
        });
      });
    }
  });
}

test("overview shows the cost hierarchy and the unpriced floor warning", async ({ page }) => {
  await seedAdminSession(page);
  await mockFinOps(page);
  await page.goto("/finops");
  await expect(page.getByTestId("card-known-spend")).toContainText("$12.50");
  await expect(page.getByTestId("card-known-spend")).toContainText("3 calls have no price");
  await expect(page.getByTestId("card-estimated")).toContainText(/not confirmed incurred spend/i);
  await expect(page.getByTestId("attention-list")).toContainText("Daily orders exceeded its limit");
});

test("usage explorer: keyboard group-by, unattributed row, drill-down", async ({ page }) => {
  await seedAdminSession(page);
  await mockFinOps(page);
  await page.goto("/finops/usage");
  await expect(page.getByText("Unattributed").first()).toBeVisible();
  await page.getByRole("button", { name: "Model", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/dimension=model/);
  await page.getByRole("button", { name: "Details for Support Bot" }).click();
  await expect(page.getByRole("link", { name: "abcdef12…" })).toHaveAttribute(
    "href",
    "/runtime/trajectories/abcdef12-0000-4000-8000-000000000000",
  );
});

test("local tabs carry the period but keep a single sidebar entry", async ({ page }) => {
  await seedAdminSession(page);
  await mockFinOps(page);
  await page.goto("/finops?period=7d");
  await page.getByRole("link", { name: "Usage & Costs" }).click();
  await expect(page).toHaveURL(/\/finops\/usage\?period=7d/);
  await expect(page.getByRole("link", { name: "Usage & Costs" })).toHaveAttribute("aria-current", "page");
});
