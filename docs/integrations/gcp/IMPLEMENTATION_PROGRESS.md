# GCP Governance Connector — Implementation Progress

## Acceptance checklist

| Phase / Gate | Status |
| --- | --- |
| Gate 1 — Migrate and integration tests | PASSED (unit); integration **BLOCKED** — no local Postgres/Docker |
| Gate 2 — Live GCP read-only POC | **AWAITING LIVE VERIFICATION** — [live-poc-runbook.md](./live-poc-runbook.md) |
| Gate 3 — Approval-gated enforcement | PASSED (mocked unit + API code); live apply **AWAITING** |
| Gate 4 — Evidence and drift | PASSED (code + worker paths) |
| Gate 5 — UI, docs, CI, PR | PASSED (docs + UI); frontend build requires `pnpm install` locally |

## Gate 1 — Tests executed

| Command | Result |
| --- | --- |
| `pytest tests/cloud_governance/test_secrets.py tests/cloud_governance/test_policy_approval.py -q` | **PASSED** (5) |
| `pytest tests/cloud_governance/test_api.py -q` | **BLOCKED** — `TEST_DATABASE_URL` / Postgres unavailable |
| `ruff check src/rai_gateway/cloud_governance` | **PASSED** (after --fix) |
| `alembic heads` | `p4q5r6s7t8u9` |

Set `TEST_DATABASE_URL=postgresql://rai_user:rai_password@localhost:5434/rai_gateway` and start Postgres (`docker compose up -d postgres`) to unblock integration tests.

## Gate 3 — Enforcement (implemented)

- `policy_action_digest`, `validate_approval_for_policy_apply`, `mark_approval_consumed`
- `POST /policy-changes/{id}/apply`, `/verify`, `GET .../approval-payload`
- GCP `apply_model_armor_changes` (floor settings v1)
- Security unit tests: `tests/cloud_governance/test_policy_approval.py`

## Gate 4 — Evidence and drift

- Evidence links on verify, sync, apply, verify
- `POST /connections/{id}/drift-check` + worker `CLOUD_DRIFT_CHECK`
- `cloud_governance/drift.py`

## Gate 5 — Docs

- [setup.md](./setup.md), [permissions.md](./permissions.md), [troubleshooting.md](./troubleshooting.md), [api.md](./api.md), [live-poc-runbook.md](./live-poc-runbook.md)

## Commits

- `62a754a`, `8426f62` — initial connector
- `e0e22d9` — enforcement, evidence, drift, docs, UI

## Production readiness

- **Staging-ready after:** migrate + integration tests green + live Gate 2 POC
- **Enforcement-ready after:** Gate 3 live apply on non-prod GCP project
