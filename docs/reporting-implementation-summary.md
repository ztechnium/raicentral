# Reports & Analytics: implementation summary

Branch: `feature/finops-ai-economics` (stacked there deliberately, see
"Process notes"). Commits: `fe6c4e1` (backend), `8495e6c` (frontend), plus the
docs/test-hygiene commit that adds this file. Metric definitions:
[reporting-metric-dictionary.md](reporting-metric-dictionary.md).

## 1. Repo-state audit (before changes)

**Tenancy.** This edition is single-organization. `TenantContext` says so, no
model has `organization_id`, and `docs/releases/SYSTEM_STATUS.md` lists
multi-organization as out of scope. The plan's "org A cannot read org B" tests
do not apply and no tenant predicate was invented. The boundary is
authentication + RBAC, so the audit focused on that.

| Finding | Severity | Resolution |
|---|---|---|
| Snapshot **create** was gated only by `reports:read` (a write behind a read permission) | High | New `reports:snapshot` permission (organization_admin; `*` roles). Auditors stay read-only. Email-verification rule for mutating permissions applies. |
| `/summary` accepted `agent_id` and `risk_severity`; no query applied `agent_id`. `effect` was applied to evaluations but not triggers/distribution/denied | High | Per-endpoint allow-lists; unsupported params return **422** (`strict_params`). `effect` applied everywhere on `/policies`. |
| "Open" findings were silently narrowed by the period whenever a period was sent | High | Open/severity/category/entity figures ignore the period (current state). |
| `resolved_findings = total − open` mixed lifetime and period | High | Redefined as a period cohort; documented why true resolution events are impossible (no `resolved_at`). |
| Critical findings counted `OPEN` only while "open findings" counted four statuses | Med | Both use the same open-status set. |
| Assurance coverage counted *records* (history, duplicates) ÷ systems; expired-but-stored-VALID counted as valid; gate counts lifetime; 0% when no data | High | Current record per system, effective status, `null` without data, gates period-scoped, `has_assurance_data`. |
| Budget "exceeded/warning" counted any historical window; one row per window; mixed metrics averaged into one utilization | High | Current window only, one row per budget, utilization summarized for `COST` budgets only. |
| `build_attention_items` returned an empty list (stub) | High | Reuses `portfolio/attention.py` (single authoritative queue). |
| Previous period shared its boundary instant with the current period; naive/aware datetimes unvalidated; inverted periods accepted; invalid strings → 500 | Med | 1 µs-gap previous period, UTC normalization, typed params, 422 on inversion/garbage. |
| `ai_systems_governed` returned the total system count (period ignored) | High | Now distinct systems with governed activity, shown against registered systems. |
| Reports cost summed `ESTIMATED` etc. via its own expression | Med | Delegates to `finops.cost_expr.known_cost`; UI uses FinOps for spend. |
| Snapshot recorded `now/now` as period when none given; digest never verified | Med | Stores effective period/filters; digest verified on read (`integrity_verified`). |
| Nav shows Reports to all roles but `reports:read` is held by `organization_admin`, `auditor` and `*` roles only | Info | UI shows an explicit not-authorized state. **Decision needed**, see §5. |

**Assurance readiness.** The endpoint's values were not trustworthy before
(see above). After the fixes the data is defensible and the view is **active
when records exist** and a sober "Not configured" state otherwise. The blurred
"coming soon" overlay is removed. The edition has no console pages for gates,
waivers or exceptions, so the empty state says records arrive via the API.

## 2. What changed

Backend (`src/rai_gateway/reporting/`, `services/admin_console.py`):
`filters.py`, `schemas.py`, `queries.py`, `reports.py`, `api.py`,
`snapshots.py`; permission added to `organization_admin`. Additive response
fields (`findings_by_source`, `semantics`, `unavailable_dimensions`,
`decision_distribution`, `execution_outcome_distribution`,
`execution_status_distribution`, `approvals_pending`, `activity_trends`,
`total_systems`, `unassessed_systems`, `has_assurance_data`, ...). Behavior
changes to existing fields are listed in the dictionary. Policy enforcement
decision logic was **not** touched.

Frontend (new modules, shared `lib/api.ts`/`lib/types.ts` untouched):
`app/(app)/reports/page.tsx` (now a thin wrapper),
`components/reports/*` (shell, toolbar, tabs, six views, panel states, KPI,
ranked bar list, trend figure, snapshots panel), `lib/reports/*` (filters/URL,
formatting, client, data hook, types). Single sidebar item retained.

## 3. Acceptance criteria

| Criterion | Status |
|---|---|
| Six business views, no long page | Done. `?view=` deep links, history, keyboard tabs. |
| Real cross-tab filters, none silently ignored | Done. Toolbar mirrors the backend allow-list; unsupported-but-active filters are called out; backend rejects strays with 422. |
| KPIs have drill-down and provenance | Done where a destination exists. Every KPI carries a definition; assurance/waiver/exception have no console page, so they link to `/portfolio` and the registry. |
| Errors isolated per view; no fabricated figures | Done (independent requests; tests prove one failing endpoint leaves others). |
| Reports vs FinOps semantics reconciled | Partly. Spend comes from FinOps; Reports' own total shares the known-cost definition but is not equal and is not displayed. |
| Tenant isolation and role access tested | Role/permission tested. Org isolation N/A (single org). |
| No inert export/refresh/snapshot buttons | Done. No export button exists. Refresh, Copy link and snapshot actions work. |
| Existing routes keep working | `/reports` is the only route changed; `/risk/*`, `/finops`, `/runtime/*` untouched. |

## 4. Tests and commands

| Command | Outcome |
|---|---|
| `pytest tests/test_reporting.py tests/test_reporting_live_sources.py tests/test_reporting_semantics.py tests/finops tests/authorization tests/auth tests/governance` | **172 passed** |
| `ruff check` / `ruff format --check` on reporting + new tests | clean |
| `vitest run` on `reports-filters`, `reports-format`, `reports-workspace` | **60 passed** |
| `tsc --noEmit` | 0 errors |
| `next lint` | no errors (pre-existing warnings elsewhere) |
| `prettier --check` on new frontend files | clean |
| Full `vitest run` | 3 failures, all pre-existing and unrelated (`runtime-identities` ×2, `create-agent-drawer` ×1: the page changed in `6e67343` but its test mock lacks `listOrganizationDepartments`). Not touched. |
| Playwright visual QA | 14 captures, see [reporting-visual-qa/](reporting-visual-qa/README.md) |

New backend tests (`tests/test_reporting_semantics.py`, 27): current-state vs
period-event fixture, boundary row in exactly one period, UTC normalization,
distinct-system assurance with superseded/expired records, null gate pass rate,
period-scoped gates, historical budget windows, non-cost budgets not averaged,
unavailable cost contributes nothing, decision vs effect vs execution outcome,
`effect` filter consistency, strict-filter 422s, inverted/malformed periods,
`reports:read` required on every endpoint, snapshot create needs
`reports:snapshot`, unverified email refused, digest tamper detection,
attention from the portfolio queue.

Frontend tests cover filter serialization, cache keys, zero vs null, ratios,
trend direction, money/cost state, CSV escaping, tab keyboard behaviour, panel
state distinctions, per-panel failure isolation, request scoping per view,
role switching and snapshot permission gating.

**Environment gotcha.** Running Vitest from a shell whose working directory has
a lower-case drive letter (`e:\`) loads React twice and every hook test fails
with "Invalid hook call". Run from `E:\...` (upper-case).

## 5. Remaining work / decisions

**Decision for you.** `reports:read` is held only by `organization_admin`,
`auditor` and `*` roles. The plan names RAI officers, risk officers and business
approvers as audiences; they currently get the "not authorized" state. Granting
`reports:read` to those roles is an access-policy change I did not make.

P1/P2:
* Add `resolved_at` to findings (migration + lifecycle writes) for true
  resolved-in-period and time-to-resolution.
* Findings dedup/identity strategy across design-time, runtime and evidence sources.
* Attribute findings to tool/model/data source; aggregate matched policy ids for "top policies".
* Decision-quality labelling to make control effectiveness measurable.
* Gateway traffic visibility (availability vs instrumentation vs enforcement coverage); a traffic-share endpoint exists but is not wired in.
* Console pages for gates, waivers, exceptions so assurance counts can drill down.
* Database-level immutability for `report_snapshots` (trigger/revoke UPDATE); today tampering is detected, not prevented.
* Environment / business-unit filters (schema fields exist; no query support).
* CSV export: deliberately omitted until a server-side, PII-reviewed export exists. `escapeCsvCell` is ready and tested.
* Reconcile or retire `BudgetReport.total_cost` once FinOps stabilizes.

## 6. Process notes

* Mid-task, another session switched this shared working tree to
  `feature/finops-ai-economics` and began editing FinOps files. On your
  instruction I stacked on that branch and staged only my own paths; their
  files are unmodified by me. The FinOps session's new "no data" tests exposed
  that `test_reporting_live_sources.py` committed rows without cleanup; I made
  that test purge its rows.
* Not verified here: a run against a real backend with seeded data (UI
  screenshots use stubbed API responses), and screen-reader testing with a real
  assistive technology (only ARIA structure and keyboard behaviour are tested).
