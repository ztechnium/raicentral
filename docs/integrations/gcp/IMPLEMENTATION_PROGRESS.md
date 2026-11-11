# GCP Governance Connector — Implementation Progress

## Acceptance checklist

| Phase | Status |
| --- | --- |
| Phase 0 — Baseline verification | PASSED |
| Phase 1 — Schema, RBAC, provider contracts | PASSED |
| Phase 2 — GCP authentication and onboarding | PASSED (mocked); AWAITING LIVE VERIFICATION |
| Phase 3 — Resource discovery and synchronization | PASSED (mocked); AWAITING LIVE VERIFICATION |
| Phase 4 — Read-only governance MVP | IN PROGRESS |
| Phase 5 — Approval-gated Model Armor enforcement | IN PROGRESS |
| Phase 6 — Evidence and audit | NOT STARTED |
| Phase 7 — Complete frontend experience | IN PROGRESS |
| Phase 8 — Enterprise and release validation | NOT STARTED |

## Phase 0 — Baseline verification

- **Branch:** `feature/gcp-governance-connector`
- **Unrelated WIP stashed:** client_hooks + `.cursor` hooks (`git stash`)
- **Alembic head before work:** `n3o4p5q6r7s8`
- **Toolchain:** project `.venv` created; system `uv` not on PATH
- **Findings:** no existing `cloud_governance` package; worker previously outbox-only; approval uses `consume` not `apply`; added `IntegrationType.cloud_governance`

## Phase 1 — Schema, RBAC, provider contracts

- **Files:** `models/cloud_governance.py`, migration `p4q5r6s7t8u9`, `cloud_governance/{domain,protocols,secrets,service,api}.py`, RBAC seed, router registration
- **Tests:** `tests/cloud_governance/test_secrets.py`

## Phase 2–3 — Auth, onboarding, discovery (mocked)

- **Files:** `providers/gcp/*`, `deploy/gcp-connector/main.tf`, `workers/cloud_sync.py`, runtime worker hook
- **Tests:** `tests/cloud_governance/test_api.py` (mocked connector; requires Postgres for integration run)
- **Commit:** `62a754a`

## Tests executed (local)

| Command | Result |
| --- | --- |
| `.venv\Scripts\python -m pytest tests/cloud_governance/test_secrets.py -q` | **PASSED** (1) |
| `.venv\Scripts\python -m pytest tests/cloud_governance/test_api.py -q` | **BLOCKED** — no local Postgres credentials |
| `.venv\Scripts\python -m ruff check --fix src/rai_gateway/cloud_governance` | **PASSED** |
| `alembic heads` | **p4q5r6s7t8u9** |

## Known limitations

- Live GCP verification requires `pip install '.[gcp]'` and an authorized test project
- Model Armor apply path raises until approval-gated live apply is validated
- AWS/Azure catalog entries are `coming_soon`

## Regression matrix (partial)

| Change | Checks |
| --- | --- |
| Alembic | `alembic upgrade head` in test schema |
| RBAC | `_ensure_default_rbac_seed` in API tests |
| Worker | `process_cloud_jobs` in API test |
| Approval enum | existing approval tests recommended on CI |
