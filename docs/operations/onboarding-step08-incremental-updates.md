# Onboarding Step 08 — incremental updates (Community)

## Enablement

Set `ONBOARDING_UPDATE_WORKFLOW_ENABLED=true` after running migration `f9a0b1c2d3e4` and deploying a worker that processes `ONBOARDING_ANALYZE_DELTA` jobs.

## Operator checklist

1. Apply Alembic head (`f9a0b1c2d3e4`).
2. Ensure onboarding worker image includes `delta_job.process_delta_job`.
3. Turn on the feature flag in the API/worker environment.
4. Verify `background_jobs` can enqueue reserved type `ONBOARDING_ANALYZE_DELTA`.
5. Confirm source storage and existing Step 02–07 paths are healthy before piloting updates.

## Safety defaults

- Missing source rows produce `WITHDRAWN_IN_SOURCE` findings only; no automatic delete, disable, or retire.
- Domain refresh (`POST .../delta/refresh`) performs read-only registry/policy snapshots (no LLM).
- Step 06 remains the only application path (`POST .../delta/prepare-review` delegates to change-set preparation).
- Step 07 verification history is not rewritten; applicability is reported as `CURRENT` / `STALE` / `REVIEW_REQUIRED`.

## Rollback

Downgrade migration `f9a0b1c2d3e4` after draining active delta runs. Disable the feature flag to hide update-session creation without affecting initial onboarding.