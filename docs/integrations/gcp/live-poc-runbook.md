# Live GCP POC runbook

**Status:** AWAITING LIVE VERIFICATION (no authorized test project was available in the implementation environment).

## Prerequisites

- Non-production GCP project
- `pip install '.[gcp]'`
- Postgres with migration `p4q5r6s7t8u9` applied
- RAICentral org admin user

## Steps

| # | Action | API / UI | Record |
| --- | --- | --- | --- |
| 1 | Apply Terraform or manual SA setup | `deploy/gcp-connector/` | project id (redacted) |
| 2 | Create connection | UI `/integrations/gcp` or `POST .../connections` | connection id |
| 3 | Verify | `POST .../verify` | capabilities + diagnostics |
| 4 | Sync | `POST .../sync` + `rai-worker --once` | asset count |
| 5 | Inspect | `GET .../assets`, `.../model-armor`, `.../coverage` | screenshots optional |
| 6 | Correlate asset | `POST .../assets/{id}/correlate` | linked `ai_system_id` |

## Pass criteria

- Verify `ok: true` for discovery capability
- At least one asset persisted, or documented empty project
- Coverage matrix returned with honest `limitations`

When completed, update [IMPLEMENTATION_PROGRESS.md](./IMPLEMENTATION_PROGRESS.md) Phase 2–4 to **PASSED (live)** with date and commit SHA.
