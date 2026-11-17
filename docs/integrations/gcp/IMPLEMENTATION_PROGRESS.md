# GCP Governance Connector — Implementation Progress

## Acceptance checklist

| Phase / Gate | Status |
| --- | --- |
| Gate 1 — Migrate and integration tests | **PASSED** — `pytest tests/cloud_governance` (13), Docker Postgres :5434 |
| Gate 2 — Live GCP read-only POC | **PASSED (live)** — project `lapeep-account-2`, connection `1e87d1e7-4b7a-40ac-bc2a-44d56b1adfed`, 2026-11-12 |
| Gate 3 — Approval-gated enforcement | **SKIPPED (live)** — no Model Armor templates in POC project; code + unit tests PASSED |
| Gate 4 — Evidence and drift | **PASSED (live)** — drift-check succeeded; 4 evidence links after verify/sync |
| Gate 5 — UI, docs, CI, PR | PASSED (docs + UI); API validated live; browser login automation flaky on :3000 |

## Live POC record (Gate 2)

| Item | Value |
| --- | --- |
| Date (UTC) | 2026-11-12 |
| GCP project | `lapeep-account-2` |
| Connection | LaPeep Account 2 — `1e87d1e7-4b7a-40ac-bc2a-44d56b1adfed` |
| Auth (dev) | `customer_adc` + host ADC mount (`k.ahmed@ztechnium.com`) |
| Verify | `ok: true` — discovery, model_armor_read, enforce capabilities true |
| Sync | Completed — **0** Vertex CAI assets (empty project; matches `gcloud asset search-all-resources`) |
| Model Armor | `supported: false` (no templates observed) |
| Coverage | `evidence_based_v1` with honest `limitations` |
| Correlate | **N/A** — no discovered assets to link |

## Gate 1 — Tests executed

| Command | Result |
| --- | --- |
| `pytest tests/cloud_governance -q` | **PASSED** (13) |
| `ruff check src/rai_gateway/cloud_governance` | **PASSED** |
| `alembic heads` | `q5r6s7t8u9v0` |

Set `TEST_DATABASE_URL=postgresql://rai_user:rai_password@localhost:5434/rai_gateway` for local integration tests.

## Fixes validated during live POC

- `iam_diagnostics.test_project_permissions` — CRM `testIamPermissions` uses project id only (not `projects/{id}` path).
- `discovery.search_vertex_assets` — CAI `SearchAllResourcesRequest.asset_types` (not deprecated `assetType=` query).

## Gate 3 — Enforcement (implemented)

- `policy_action_digest`, `validate_approval_for_policy_apply`, `mark_approval_consumed`
- `POST /policy-changes/{id}/apply`, `/verify`, `GET .../approval-payload`
- GCP `apply_model_armor_changes` (floor settings v1)
- Security unit tests: `tests/cloud_governance/test_policy_approval.py`

## Gate 4 — Evidence and drift (live)

- Evidence links on verify, sync, apply, verify — **4 links** on LaPeep connection
- `POST /connections/{id}/drift-check` + worker `CLOUD_DRIFT_CHECK` — job **SUCCEEDED**, 0 findings
- `cloud_governance/drift.py`

## Gate 5 — Docs

- [setup.md](./setup.md), [permissions.md](./permissions.md), [troubleshooting.md](./troubleshooting.md), [api.md](./api.md), [live-poc-runbook.md](./live-poc-runbook.md)

## Staging implementation (2026-11-12)

- Per-run sync/drift job idempotency; dead-letter re-enqueue
- Connection schedule columns + worker scheduler
- Terraform module split (`iam.tf`, `wif.tf`, `outputs.tf`); [terraform.md](./terraform.md)
- Operator UI: verify/sync/drift, correlate, schedule, policy preview hook
- `customer_adc` blocked when `ENVIRONMENT=production`
- `ENABLE_CLOUD_GOVERNANCE` feature flag

## Production readiness

- **Staging-ready:** Gate 1–2 live on non-prod project; use impersonation/WIF (not user ADC).
- **Enforcement-ready after:** Gate 3 live apply on project with Model Armor resources.

## Commits / PR

- Branch `feature/gcp-governance-connector` — PR https://github.com/ztechnium/raicentral/pull/1
- Post-POC validation commit: IAM + discovery fixes, worker/ADC compose, tests (see git log).
