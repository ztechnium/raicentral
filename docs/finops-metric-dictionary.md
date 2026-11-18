# FinOps metric dictionary

Authoritative definitions for every figure shown in the FinOps workspace (`/finops`,
`/finops/usage`, `/finops/budgets`, `/finops/pricing`) and the Reports cost figures that
reconcile with it. Source of truth in code: `src/rai_gateway/finops/cost_expr.py`,
`reporting.py`, `budget_health.py`.

## Scope and isolation

- **This edition is single-organization.** No FinOps table carries an `organization_id`
  (see migration `n3o4p5q6r7s8_drop_organization_tenancy`). Access is gated by the
  `finops:read` permission (and `finops:export` for non-summary exports).
- The meaningful isolation boundaries *inside* the instance are the **delegation** (SaaS
  tenant) and **AI system** filters. Every read endpoint accepts them through one shared,
  validated dependency (`finops_filters`), so a filtered view cannot leak a sibling's rows.
- A filter with an empty value is "no filter". Because of that, **rows with no value for a
  dimension (e.g. no `delegation_id`) cannot be selected by a filter**; they are shown as
  an "Unattributed" row and cannot be drilled into.

## Data source

Display source is the **metering span** (`finops_metering_spans`): one row per metered
operation. Reservations, settlements and enforcement receipts are **not summed** by FinOps,
so one request is never counted once per record. When a span is recorded, an unlinked
settlement for the same call is adopted into it (`RuntimeMeteringService`, `metering_span_id`).

Settlements with **no** span (written directly through `POST /v1/finops/settlements`) are
real ledger entries FinOps does not count. Reports shows them separately as
`ledger_only_cost` (see *Reconciliation with Reports*).

## Cost states

| State | Meaning | Contributes to `total_cost` |
|---|---|---|
| `BILLED` | Invoice- or provider-billing-backed amount (`billed_cost`; legacy fallback `actual_cost`) | yes |
| `METERED` | Measured usage priced from the catalog (`metered_cost`; legacy `ACTUAL` is treated as metered) | yes |
| `ESTIMATED` | Pre-execution estimate (`estimated_cost`). Not confirmed incurred spend | yes, but always shown separately |
| `UNAVAILABLE` | Usage recorded, no price | **no** (counted separately, never 0) |
| `MIXED` | An aggregate containing more than one known state | n/a (aggregate label) |
| `NO_DATA` | No spans matched the filters | n/a |

**State is derived from per-state span counts, never from whether an amount is truthy.**
A genuine `$0.00` billed span is `BILLED`; "no spans" is `NO_DATA`; "spans but no price" is
`UNAVAILABLE`. `null` means unknown, `0` means a known zero.

## Totals (`GET /v1/finops/dashboard` → `totals`)

| Field | Definition | Null when |
|---|---|---|
| `total_cost` | billed + metered + estimated over spans with a known state | no span has a known cost, or currencies are mixed |
| `billed_cost` | sum over `BILLED` spans | no billed span |
| `metered_cost` | sum over `METERED`/`ACTUAL` spans | no metered span |
| `actual_cost` | `billed_cost + metered_cost` (legacy name). **Never includes estimates** | neither exists |
| `estimated_cost` | sum over `ESTIMATED` spans | no estimated span |
| `unavailable_cost_count` | spans with no known cost | – |
| `span_counts` | `{total, billed, metered, estimated, unavailable}` | – |
| `cost_complete` | `unavailable == 0 && total > 0`. When false, `total_cost` is a **floor** | – |
| `cost_state` | derived from `span_counts` | – |
| `total_tokens`, `*_tokens` | sum over spans that reported them | no span reported tokens |
| `token_reported_spans` | spans that reported total tokens | – |
| `model_calls` / `tool_calls` | spans with `span_type` `MODEL_CALL` / `TOOL_CALL` | – |
| `trajectories` / `conversations` | distinct ids across all spans | – |
| `known_cost_trajectories` / `…_conversations` | distinct ids having ≥1 known-cost span | – |
| `avg_cost_per_trajectory` | `total_cost / known_cost_trajectories` | no known-cost trajectory |
| `avg_cost_per_conversation` | `total_cost / known_cost_conversations` | no known-cost conversation |
| `avg_tokens_per_trajectory` | `total_tokens / trajectories` (all trajectories) | no tokens or trajectories |

**Denominators are explicit.** Averages divide by entities that *have* a known cost
(`avg_cost_denominator`), so unpriced activity cannot silently dilute them. Token averages
divide by all trajectories because tokens are counted over the same population.

## Currency

Each span carries its own `currency`. FinOps **never adds amounts of different currencies
and never assumes USD**.

- One currency present → `currency` is that code.
- Several, no `currency` filter → `currency: "MIXED"`, `currencies: [...]`, every cost field
  `null`, and `totals.by_currency` lists per-currency totals. Breakdown/timeseries do the same.
- `?currency=EUR` filters to one currency and makes totals summable again.
- No conversion is performed anywhere.
- `risk_associated_spend` and `prevented_estimated_spend` follow the same rule
  (`null` / `"MIXED"`).

Known gap: budgets of metric `COST`/`AMOUNT` carry no currency field, so the UI states the
unit as "currency units" rather than inventing a symbol.

## Coverage (`coverage`)

Measurement coverage over spans. **Not governance control coverage**, and **tenant
attribution is not metering coverage**. All percentages are `null` (not 0) when there are
no spans.

| Field | Definition |
|---|---|
| `cost_coverage_pct` | spans with a known cost state ÷ all spans |
| `billed_pct` / `metered_pct` / `estimated_pct` / `unavailable_pct` | share of spans in that state |
| `token_coverage_pct` | spans reporting total tokens ÷ all spans |
| `model_attribution_pct` | spans with a model name |
| `tenant_attribution_pct` | spans with a `delegation_id` |
| `system_attribution_pct` / `unattributed_pct` | spans with / without an `ai_system_id` |

## Period comparison (`comparison`)

Known cost this period vs the immediately preceding period of equal length with the same
filters. `total_cost_change_pct` is `null` when either side is unknown or the previous total is
0 (no baseline). `like_for_like` is `false` when the priced share of spans differs by more
than 10 percentage points between periods; the UI then warns the change may reflect
measurement, not usage. Growth is presented neutrally.

## Breakdown (`GET /v1/finops/breakdown`)

Dimensions (verified against `_dimension_column`): `ai_system`, `agent`, `agent_version`,
`runtime_identity`, `delegation`, `model`, `provider`, `environment`, `tool`
(span `operation`), `integration`. Also: `limit` (1–200), `offset`, `sort`
(`cost|calls|tokens|trajectories`), `order`, `search` (**only** `provider|model|tool|environment`;
other dimensions return 400). `total` is the number of groups, for pagination. A `NULL`
key is its own row: `Unattributed` (IDs) or `Not reported` (strings).

Per-row units:

- **calls** = metering spans (model + tool + other), not provider requests, governed
  actions, trajectories or conversations.
- `priced_calls` = calls with a known cost; `unpriced_calls` = the rest;
  `unpriced_share_pct = unpriced_calls / calls`.
- `avg_cost_per_call = cost / priced_calls`; `avg_cost_per_trajectory = cost / trajectories
  with a known cost`.
- Token fields are `null` when no call in the row reported them; `token_reported_calls`
  says how many did.

Totals reconcile by construction: for identical filters, `dashboard.totals.total_cost`,
`timeseries.total_cost` and the sum of all breakdown rows' `cost` are equal (tested).

## Timeseries

Daily buckets (UTC `date_trunc('day', started_at)`). A day with no priced span has
`cost: null`, rendered as a gap, never a point at 0. Each point carries billed/metered/
estimated components so the chart can show confidence classes separately.

## Risk and prevention

| Field | Definition | Not |
|---|---|---|
| `risk_associated_spend` | Known cost of trajectories with a HIGH/CRITICAL finding **or** a DENY / REQUIRE_APPROVAL / SUSPEND enforcement decision | monetary loss caused by risk |
| `prevented_estimated_spend` | Estimated reservation cost of requests denied before execution, **plus** reservations released with `release_reason = budget_denied`; honours the same system/agent/provider/model/currency filters | realized savings, or a reduction of any invoice |

Both carry a `definition` string from the API that the UI displays next to the number.

## Budgets (`GET /v1/finops/budgets`)

Read-only. Configuration and enforcement live in `risk_intelligence.budgets` and
`/risk/budgets`; nothing here writes to them.

- Only the **current window** counts (`window_start <= now < window_end`). Historical
  windows are not pressure. (Previously, every historical window counted.) A budget with no
  open window is `NO_ACTIVE_WINDOW`, never assumed healthy.
- Windows are epoch-aligned fixed buckets (`_floor_window_start`); usage resets at `window_end`.
- Metrics stay distinct: `ACTION_COUNT`, `TOOL_COUNT`, `AMOUNT`, `TOKEN_USAGE`, `COST`. They
  are never summed or averaged together.
- `headroom = limit − usage − reserved` (in-flight reservations are subtracted).
- `blocking` is true for `DENY`, `REQUIRE_APPROVAL`, `SUSPEND_RUNTIME_IDENTITY`; `MONITOR`
  only records. **"Enforced" requires evidence**: a blocking effect with ≥1 recorded
  `runtime_budget_checks` row in the last 30 days. A blocking effect with none is
  "configured, no checks recorded".
- `enforcement_mode`: `MANDATORY` fails closed; `BEST_EFFORT` may skip unmeasurable metrics.
- With an `ai_system_id` filter, organization-wide budgets (no AI system) are included,
  because they also constrain that system.

### Projection (`projection`)

`method: linear_run_rate_within_window`: `rate = usage ÷ elapsed`;
`projected_window_usage = rate × window length`; exhaustion time is `window_start + limit ÷ rate`
when that falls before `window_end`.

- Reported only when usage > 0 **and** elapsed ≥ max(10 minutes, 10% of the window);
  otherwise `status: "insufficient_data"` with a reason.
- **Never carried across a reset.** It describes the current window only.
- `confidence`: `high` ≥ 50% of window elapsed, `medium` ≥ 25%, else `low`. This is a coarse
  statement about how much data the rate rests on, not a statistical interval.
- Not modelled: spikes, idle periods, schedule effects, price changes. There is no ML.

## Reconciliation with Reports

`GET /v1/reports/budgets → total_cost` (and `GET /v1/reports/summary → cost`) is
`span_cost + ledger_only_cost`.

| Field | Definition | Equals FinOps? |
|---|---|---|
| `span_cost` | known cost over spans, via the same `cost_expr.known_cost` | **yes**, `totals.total_cost` for matching filters/period |
| `ledger_only_cost` | known cost of `UsageSettlement` rows with `metering_span_id IS NULL` | no, FinOps does not count these (the only intended difference) |
| `total_cost` | sum of the two | `null` when currencies are mixed or nothing is priced |
| `currency` / `currencies` | as above | – |

Remaining legitimate differences: Reports' period bounds are **inclusive** (`<= end`, a
convention of the reports module) whereas FinOps uses `[start, end)`; Reports filters
`ai_system_id` only (it has no model/provider filters on this view).

## Known gaps

See `docs/finops-implementation-summary.md` → *Deferred and known gaps*.
