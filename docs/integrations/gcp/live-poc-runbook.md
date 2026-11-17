# Live GCP POC runbook

**Status:** **PASSED (live)** on `lapeep-account-2` (2026-11-12). See [IMPLEMENTATION_PROGRESS.md](./IMPLEMENTATION_PROGRESS.md).

## Prerequisites

- Non-production GCP project
- Postgres with migration `p4q5r6s7t8u9` applied
- RAICentral org admin user
- **Docker (recommended):** `docker compose up -d postgres redis rai-gateway rai-worker control-plane-ui` — gateway image includes `[gcp]`; worker processes sync/drift jobs
- **Host Python:** `uv sync --extra gcp` or `pip install '.[gcp]'`

## Steps

| # | Action | API / UI | Record |
| --- | --- | --- | --- |
| 1 | Apply Terraform or manual SA setup | `deploy/gcp-connector/` | project id (redacted) |
| 2 | Create connection | UI `/integrations/gcp` or `POST .../connections` | connection id |
| 3 | Verify | `POST .../verify` | capabilities + diagnostics |
| 4 | Sync | `POST .../sync` (worker runs automatically, or `docker compose exec rai-worker rai-worker --once`) | asset count |
| 5 | Inspect | `GET .../assets`, `.../model-armor`, `.../coverage` | screenshots optional |
| 6 | Correlate asset | `POST .../assets/{id}/correlate` | linked `ai_system_id` |

## Pass criteria

- Verify `ok: true` for discovery capability
- At least one asset persisted, or documented empty project
- Coverage matrix returned with honest `limitations`

When completed, update [IMPLEMENTATION_PROGRESS.md](./IMPLEMENTATION_PROGRESS.md) Phase 2–4 to **PASSED (live)** with date and commit SHA.
