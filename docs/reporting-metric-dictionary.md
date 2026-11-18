# Reporting metric dictionary

Definitions for every figure in **Reports & Analytics** (`/reports`). Source of
truth is `src/rai_gateway/reporting/` unless noted. Update this file in the same
change as any metric.

**Scope of this edition.** One organization per instance (see
`docs/releases/SYSTEM_STATUS.md`); no row carries an `organization_id`. Access
control is RBAC: `reports:read` to read reports and snapshots,
`reports:snapshot` to create a snapshot. Both are enforced on the backend
(`reporting/api.py`), not only in navigation.

## Conventions

| Term | Meaning |
|---|---|
| **Current state** | Evaluated "now". The selected period does not change it; no previous-period value exists. |
| **Period event** | Rows whose timestamp falls in `[period_start, period_end]` (both inclusive). |
| **Period cohort** | Rows created in the period, classified by their *current* status. |
| **Previous period** | Same duration, ending 1 µs before `period_start`, so no row is in both periods. |
| **Time** | All instants are normalized to naive UTC at the API boundary. The UI shows the range and the viewer's timezone. |
| **null / "Not available"** | Unknown. Never rendered as 0. A real zero renders as `0`. |
| **Default period** | 30 days ending now, when none is sent. |

## Executive Overview (`GET /v1/reports/summary`)

| Metric | Definition | Numerator / denominator | Type | Limitations |
|---|---|---|---|---|
| Governed AI systems (`ai_systems_governed`) | Distinct AI systems with ≥1 enforcement receipt in the period | Shown as `governed of registered` | Period event, compared with previous period | Observed activity only. A registered system that never called the gateway is not counted. **Not** a coverage guarantee. Replaces the old field that returned the total system count. |
| Registered systems (`ai_systems_registered`) | Count of `ai_systems` in scope | Denominator for the two ratios | Current state | Includes systems of any lifecycle status. |
| Open critical / high findings | Open findings (`OPEN`, `INVESTIGATING`, `ACKNOWLEDGED`, `MONITORING`) by severity | – | Current state | Sum of three independent sources, no deduplication (see Risk). |
| Governed actions | Enforcement receipts in the period | – | Period event | Receipts, not requests or model calls. |
| Policy intervention rate | Receipts whose matched rule effect is `MONITOR`, `REQUIRE_APPROVAL`, `DENY` or `SUSPEND` ÷ all receipts | triggers / evaluations | Period event | Activity, not effectiveness. 0 when there are no evaluations. |
| Assurance coverage | Systems whose current assurance is effectively `VALID` ÷ registered systems | valid systems / registered | Current state | `null` with no registered systems. Control assurance, **not** regulatory certification. |
| Budget utilization (`budget_utilization_pct`) | Highest utilization among enabled `COST` budgets' current windows | – | Current state | `null` when no cost budget. Non-cost budgets are never mixed in. |
| New findings | Findings created in the period | – | Period event, compared with previous | – |
| Spend (UI) | FinOps `GET /v1/finops/dashboard` `totals.total_cost` | – | Period event | Authoritative cost; see Economics. |
| Attention queue | Portfolio attention queue (`portfolio/attention.py`), ranked severity then due date, top 10 | – | Current state | Items carry only source-recorded fields. No owner field exists upstream, so none is shown. |

## Risk Analysis (`GET /v1/reports/risk`)

| Metric | Definition | Type |
|---|---|---|
| `open_findings` | Findings in an open status now | Current state |
| `new_findings` | Findings created in the period | Period event |
| `resolved_findings` | Findings created in the period that are no longer open | **Period cohort** |
| `critical_findings`, `high_findings` | Open findings by severity | Current state |
| `severity_distribution`, `category_distribution`, `risk_by_*` | Open findings only | Current state |
| `status_distribution` | Current status of findings created in the period | Period cohort |
| `finding_trends` | Findings created per day | Period event |
| `findings_by_source` | Open findings per source table: `design_time` (`risk_findings`), `runtime` (`runtime_risk_findings`), `evidence` (`evidence_findings`) | Current state |
| `mean_time_to_resolution_hours` | Always `null` | – |

Limitations:

* **No resolution timestamp.** Neither finding table has `resolved_at`, so
  "resolved *in* the period" and time-to-resolution cannot be computed. The
  cohort definition is the closest honest figure.
* **No deduplication across sources.** Totals are a plain sum. Runtime and
  evidence findings may describe the same event; `findings_by_source` exposes
  the split. A dedup/identity strategy needs a shared key that does not exist yet.
* **Risk by tool, model, data source is unavailable** (`unavailable_dimensions`).
  Findings are not attributed to those dimensions. Agent attribution covers
  design-time findings only.
* Open-finding counts have no previous-period value.

## Control Effectiveness (`GET /v1/reports/policies`)

Three facts that are reported separately and must not be conflated:

| Field | Meaning |
|---|---|
| `effects` | `policy_effect` of the **matched rule** (`ALLOW`/`MONITOR`/`REQUIRE_APPROVAL`/`DENY`/`SUSPEND`) |
| `decision_distribution` | Finalized `enforcement_decision` |
| `execution_outcome_distribution` | `execution_status` after the decision |

`trigger_rate` = non-`ALLOW` matches ÷ evaluations. `deny_rate` = final `DENY`
decisions ÷ evaluations. A `DENY` is an enforcement outcome, not a runtime
failure. **Effectiveness is unknown**: there is no true/false-positive labelling,
so rates are shown without good/bad colouring. `top_policies` is not populated
(rule ids are stored as a list per receipt and are not aggregated). The `effect`
filter applies to every figure on the endpoint.

## Assurance & Compliance (`GET /v1/reports/assurance`)

| Metric | Definition | Type |
|---|---|---|
| Status counts | One status per system: the newest non-superseded `assurance_records` row. A stored `VALID` whose `valid_until` has passed is `EXPIRED`. | Current state |
| `unassessed_systems` | Registered systems with no current record | Current state |
| `assurance_coverage_pct` | valid ÷ `total_systems`; `null` if none registered | Current state |
| `gate_evaluations`, `failed_deployment_gates` | `deployment_gate_evaluations` by `evaluated_at` in the period | Period event |
| `evaluation_pass_rate` | PASS ÷ (PASS + FAIL); `null` if none | Period event |
| `active_waivers` / `active_exceptions` | `APPROVED`, not revoked, not past `valid_until` | Current state |
| `expiring_*` | Active and `valid_until` within 14 days | Current state |
| `open_exceptions` | `REQUESTED` or `UNDER_REVIEW` | Current state |

`has_assurance_data` is false when no system has any record or gate evaluation;
the UI then shows **Not configured** rather than metrics. This edition has no
console pages for gates, waivers or exceptions, so records arrive through the
API. **Control assurance is not regulatory certification.**

## Runtime Governance (`GET /v1/reports/runtime`)

| Field | Unit | Type |
|---|---|---|
| `governed_actions`, `policy_evaluations` | Enforcement receipts | Period event |
| `executions` | Trajectories started | Period event |
| `conversations` | Conversations started | Period event |
| `interventions`, `approvals_required`, `runtime_suspensions` | Receipts by matched effect | Period event |
| `denied` | Receipts with final decision `DENY` | Period event |
| `execution_status_distribution` | Trajectories by recorded status | Period event |
| `execution_success_rate` | `COMPLETED` ÷ all started | Period event |
| `approvals_pending` | Approval requests `PENDING` now | Current state |

The success rate's denominator includes runs still `ACTIVE`; the UI shows the
distribution and states that active runs are neither successes nor failures.
Pending approvals, denials and suspensions are intended control outcomes, not
failures. Gateway traffic visibility (availability vs instrumentation vs
enforcement coverage) is **not** included.

## AI Economics Summary

| Figure | Source | Notes |
|---|---|---|
| Measured / estimated spend, coverage, unavailable count, budget pressure, risk-linked spend | `GET /v1/finops/dashboard` (FinOps, span-based) | Authoritative. Cost states: billed, metered, estimated, unavailable. Unavailable is never shown as 0; currency comes from the data. |
| Budget utilization | `GET /v1/reports/budgets` `budgets[]` | Current window per budget (worst scope), grouped by metric with its unit. Never summed across metrics. |

`BudgetReport` cost fields (reconciled with FinOps in `5bb6553`): `span_cost`
is known cost (billed + metered + estimated) over metering spans using
`finops.cost_expr.known_cost`, and **equals the FinOps `total_cost` for the same
filters and period**. `ledger_only_cost` is known cost of settlements no span
represents (FinOps does not count these). `total_cost = span_cost +
ledger_only_cost`. All three are `null` when there is no priced usage, and
`total_cost` is `null` when several currencies are present (never summed; see
`currencies`). Estimates are included, so billed/metered/estimated separation
still comes from FinOps. The Economics view reads spend from FinOps directly and
does not display `BudgetReport.total_cost`. Budget counts
(`budgets_warning`, `budgets_exceeded`) use only the window containing "now".

## Snapshots

`POST /v1/reports/snapshots` (needs `reports:snapshot`) stores: report type,
effective period and filters (the defaulted period, not "now"), `schema_version`
`1.0`, `generated_at`, `generated_by`, the payload and a SHA-256 digest. Reading
a snapshot recomputes the digest (`integrity_verified`). Limitations: the
database does not itself forbid updating a snapshot row (tampering is
*detected*, not *prevented*); `source_manifest` currently lists AI system ids
only for reports that carry `risk_by_ai_system`.

## Owners

Reporting engine: platform/governance team. FinOps figures: FinOps owners.
Attention queue: portfolio module owners.
