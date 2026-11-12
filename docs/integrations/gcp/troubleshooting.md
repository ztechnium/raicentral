# Troubleshooting

## Verify fails with `GCP_LIBS_MISSING`

Install optional dependencies: `pip install '.[gcp]'`.

## Verify shows missing IAM permissions

Grant roles listed in [permissions.md](./permissions.md). Re-run **Verify** from the GCP wizard.

## Sync job stuck or `DEAD_LETTER`

- Check `background_jobs.last_error` for the connection sync job.
- After a code fix, click **Sync** again — each run uses a new job idempotency key.
- Ensure `rai-worker` is running (`docker compose up -d rai-worker`).
- Manually: `docker compose exec rai-worker rai-worker --once`.

## Sync returns no assets

- Enable **Cloud Asset API**.
- Confirm Vertex AI resources exist in the project.
- Discovery uses Cloud Asset Inventory; some deployment associations may be absent.

## Apply rejected

- Connection must have **enforcement enabled**.
- Bind an **APPROVED** approval whose `action_digest` matches the policy change (see `GET .../policy-changes/{id}/approval-payload`).
- Approvals in `CONSUMED` or `EXPIRED` state cannot be reused.

## Integration tests fail locally

Set `TEST_DATABASE_URL` to a running Postgres (see `.env.example`, port 5434 with docker compose).
