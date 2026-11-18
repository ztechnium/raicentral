# FinOps UX & AI economics: implementation summary

Branch `feature/finops-ai-economics`, from `main` @ `12a2222`. Definitions are in
[`finops-metric-dictionary.md`](./finops-metric-dictionary.md); screenshots in
[`finops-visual-qa/`](./finops-visual-qa/).

## What shipped

| Area | Status |
|---|---|
| P0 Overview, Usage & Costs, Budgets health, correctness, Reports reconciliation, a11y/QA | Done |
| P1 in-window burn rate and projection | Done (simple, transparent, gated; see below) |
| P1 intervention histories / alerting, first-class tenant allocation view, bucketed spend | **Deferred** |
| P2 Optimization, advanced comparisons | **Deferred** (not rendered: nothing is shown that lacks reliable data) |
| Pricing catalog | Extended |

Sidebar is unchanged (one **FinOps** entry); local tabs are Overview, Usage & Costs,
Budgets & Forecasting, Pricing Catalog. Period and filters follow the user between
Overview and Usage via the URL.

## Correctness defects found and fixed

These were release-blocker invariants in the plan; each has a test.

1. **Cost state from numeric truthiness** (`reporting.py`, `finops/page.tsx`,
   `dashboard/overview.ts`). `bool(billed)` and `totals.billed_cost ? "BILLED" : …` turned a
   valid `$0.00` into UNAVAILABLE, and an empty period into UNAVAILABLE too. State now comes
   from per-state span counts; `NO_DATA`, `UNAVAILABLE` and a known zero are distinct. The main
   dashboard's `spendKpi` had the same bug and is fixed.
2. **Hardcoded `currency: "USD"`** and silent cross-currency sums. Currency is read from the
   spans; mixed currencies yield `null` totals + per-currency breakdown + a currency filter.
3. **Budget pressure was wrong.** `_budget_pressure()` took no filters and counted every
   *historical* window ever recorded as pressure. It now reads current windows only, honours
   `ai_system_id`, and a budget with no open window is "no active window", never "healthy".
4. **Averages on the wrong denominator.** Per-trajectory/conversation/call averages now divide
   by entities with a known cost, and the denominator is named in the response and UI.
5. **Percentages of nothing.** Coverage was `0.0` with no spans; it is `null` now.
6. **Risk-spend query** inner-joined `AISystem`, silently dropping unattributed
   trajectories; the pointless joins are removed.
7. **Prevented-estimate ignored filters** (system/agent/provider/model) and summed across
   currencies; it now honours them and returns `null`/`MIXED` for mixed currency.
8. **Export hardening.** Unknown `view` raised a 500 and was interpolated into
   `Content-Disposition`; it is now an allowlist (400 otherwise). CSV cells are protected
   against formula injection from user-supplied labels.
9. **Routing bug no unit test could catch.** `next.config.mjs` redirected `/finops/usage` to
   the old `/finops#usage` anchor, shadowing the new route; found by the browser test. Fixed,
   and the legacy `/finops/models` redirect now targets `/finops/usage?dimension=model`.
10. **Shared chart defects** noticed in visual QA: long labels clipped at the head;
    billed and metered rendered as two near-identical blues (now blue vs grey).

## Financial reconciliation (Reports ⇄ FinOps)

- Both modules now aggregate the **same** expression, `finops/cost_expr.py`. (The Reports
  module previously counted `UNAVAILABLE` spans that held stray amounts and fell back across
  columns; another contributor aligned `_known_cost` to it on this branch.)
- `GET /v1/reports/budgets` exposes `span_cost`, `ledger_only_cost`, `total_cost`,
  `currency(ies)`. `span_cost == FinOps totals.total_cost` for matching filters/period; this is
  asserted in `tests/finops/test_reports_reconciliation.py`.
- Legitimate remaining differences, stated in `cost_basis`: settlements with no metering span
  (`ledger_only_cost`; FinOps does not count them), inclusive vs half-open period bounds, and
  Reports' `ai_system_id`-only filtering.
- Double counting: FinOps counts spans only. Reservations, settlements and receipts are never
  summed. Settlements are adopted into their span at record time, and Reports excludes
  settlements that already carry a `metering_span_id` (tested).
- Unknown cost is `null`, never `0`, in Reports too. Sub-cent costs are no longer rounded to
  2 dp.

## Authorization and isolation: what was and was not verified

- **This edition is single-organization.** There is no `organization_id` on any FinOps table
  (migration `n3o4p5q6r7s8`), so "two org fixtures" does not apply and there is no org
  predicate to add. I did not invent one.
- Verified by tests: `finops:read` is required on dashboard, timeseries, breakdown, budgets and
  trajectories; `finops:export` is required for every export view except `summary`; invalid
  periods (inverted, > 400 days), unknown `cost_state`, unknown dimension/sort/view, bad UUIDs
  are 400/422; every supported filter is accepted on every read view.
- Verified by tests: the **delegation filter isolates** dashboard, timeseries, breakdown and
  trajectories to one delegation.
- **Not provided by the product:** RBAC is not scoped per delegation. Any user with
  `finops:read` can read every delegation by changing the filter. This is how the edition
  works today, not something changed here; if delegates must not see each other, that needs a
  new authorization rule.
- **Not verified:** data retention for spans; where pricing rows are sourced from.
- Observed, **not changed**: `GET /v1/finops/pricing`, `/pricing/resolve` and `POST /estimate`
  require only an authenticated user, not `finops:read`.
- Spans have no per-row owner, so "an AI system id from another org" cannot occur in this
  edition; `_assert_owned_refs` on the write path is unchanged.

## Budgets & forecasting

Read-only (`GET /v1/finops/budgets`); editing stays in `/risk/budgets`. Shows current usage,
limit, headroom (reservations subtracted), window and reset time, warning threshold, effect,
enforcement mode, and **enforced vs configured**: a blocking effect is "enforced" only when
`runtime_budget_checks` shows checks in the last 30 days. Types are never mixed; the page
groups by metric and states units. The projection is linear run-rate **within the current
window only**, requires ≥ 10 minutes and ≥ 10% of the window, never crosses a reset, and
otherwise says "insufficient data" with a reason. Confidence is a coarse statement about how
much of the window it rests on, not a statistical interval.

## Files

Backend (new): `finops/cost_expr.py`, `finops/budget_health.py`.
Backend (changed): `finops/reporting.py` (rewritten), `finops/api.py`,
`reporting/queries.py`, `reporting/reports.py`, `reporting/schemas.py`.
Frontend (new): `app/(app)/finops/{layout,usage/page,budgets/page}.tsx`,
`components/finops/*` (filter bar, tabs, metric card, state badge, charts, explorer),
`lib/{finops-filters,finops-insights,finops-dimensions,finops-pricing,use-finops-filters}.ts`.
Frontend (changed): `finops/page.tsx` (Overview), `finops/pricing/page.tsx`, `lib/api.ts`,
`lib/types.ts`, `lib/finops-format.ts`, `lib/dashboard/overview.ts`,
`app/(app)/dashboard/page.tsx`, `components/reporting/{trend-line-chart,ai-system-report-tab}.tsx`,
`next.config.mjs`.

## Tests

| Suite | Result |
|---|---|
| `tests/finops` + reporting + risk-intelligence (backend) | 158 passed |
| New backend tests | 35 (28 reporting/budgets/API, 7 Reports reconciliation) |
| UI unit (vitest) | 222 passed, **3 failed (pre-existing, unrelated)** |
| `tsc --noEmit`, ESLint on touched files, `ruff check` | clean |
| `next build` | succeeds (all 4 FinOps routes) |
| Playwright `finops.spec.ts` (9 tests, mocked API, desktop 1440 + mobile 390) | 9 passed |

**Pre-existing failures** (3): `runtime-identities.test.tsx` (2) and
`create-agent-drawer.test.tsx` (1). Their `vi.mock("@/lib/api")` lacks the
`listOrganizationDepartments` export added by an earlier commit (`6e67343`). Not touched.

Coverage of the plan's UI cases: partial endpoint failure, 403, no data, missing prices,
invalid date range, long model names, mobile layout, keyboard interaction, pagination,
mixed currency, empty results, unattributed rows. Tests that encoded the old truthiness
behaviour were corrected (`dashboard-overview.test.ts` estimated-only fixture).

## Deferred and known gaps

- **Optimization (P2)**: not built. Recommendations need quality/safety/latency comparison
  data this codebase does not collect; shipping savings claims without it would be fabricated.
- **Intervention history & alerting, tenant allocation view, server-side bucketed spend**
  (P1): the existing daily timeseries and the delegation dimension cover the underlying data;
  dedicated views were not built.
- **Export UI**: the endpoint is hardened and permission-gated, but no button was added
  (the UI has no reliable way to know the caller's `finops:export` permission yet).
- **Drill-down** opens an in-page trajectory list (→ `/runtime/trajectories/{id}`) rather than
  deep-linking `/runtime/trajectories?…`, because that page ignores URL filters.
- `COST`/`AMOUNT` budgets record no currency; the UI says "currency units".
- The AI-system report card (`ai-system-report-tab`) still renders its cost with a `$`: its
  source (settlement `actual_cost`) has no currency in the payload. Null is now handled.
- Reports' `…/summary → cost` is now nullable; no other UI consumer exists.
- Chart text alternatives and data tables are provided; a full assistive-technology pass
  (screen reader on real hardware) was not done.
- Visual QA used **mocked** API data, not a live gateway (the available gateway container is
  an older image without the new endpoints).

## Environment notes (things done outside the diff)

- `control-plane-ui/node_modules` had MSYS-style symlinks Node could not follow; I ran
  `pnpm install --frozen-lockfile` to repair it (git-ignored, no lockfile change).
- Installed Playwright Chromium into the user cache for the e2e run.
- Component tests must run from a shell whose cwd uses an uppercase drive letter (`E:\`);
  with `e:\` Vite loads two React copies ("Cannot read properties of null (reading
  'useState')").
- Another contributor committed Reports work to this branch while this was in progress
  (`fe6c4e1`, `8495e6c`); those commits are theirs and were not modified.
