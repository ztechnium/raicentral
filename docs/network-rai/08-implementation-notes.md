# Step 8 implementation notes

## Delivered

- `provider_gateway/production/` — policy cache, fail-closed matrix, limits, SLO metrics, lifecycle draining, outbound allowlist, rollout phases
- Extended `/ready` with policy cache, redis, drain state
- `/v1/providers/_gateway/status` operational surface (SLO + fail-closed matrix)
- `rai-gateway doctor` CLI (`src/rai_gateway/cli/gateway_doctor.py`)
- TLS verification + optional CA bundle on upstream httpx client
- Request header limits, runtime rate limit, stream duration cap
- Graceful shutdown triggers drain on application shutdown
- Deploy guides: `deploy/network-enforcement/MIGRATION.md`, `PRODUCTION_READINESS_TESTS.md`

## Configuration

| Variable | Purpose |
|---|---|
| `GATEWAY_ROLLOUT_PHASE` | MIRROR → PRIVATE_PROVIDER_ACCESS |
| `PROVIDER_GATEWAY_POLICY_CACHE_TTL_SECONDS` | Default enforcement state TTL |
| `PROVIDER_GATEWAY_POLICY_CACHE_FAST_INVALIDATE_TTL_SECONDS` | Revoke/suspend TTL |
| `PROVIDER_GATEWAY_OUTBOUND_ALLOWLIST` | Extra comma-separated upstream hosts |
| `PROVIDER_GATEWAY_MAX_HEADER_BYTES` | Header size limit |
| `PROVIDER_GATEWAY_MAX_STREAM_DURATION_SECONDS` | Stream wall clock cap |
| `PROVIDER_GATEWAY_VERIFY_TLS` | Upstream TLS verification |
| `PROVIDER_GATEWAY_CA_BUNDLE_PATH` | Custom CA bundle |

## FinOps

Pre/post execution estimate/reserve/release remains in `RuntimeEnforcementService._budget_authorization_check` (Step 2+). Gateway receipts continue to store provider-reported usage via `build_execution_metadata`.

## Exit criteria mapping

| Criterion | Evidence |
|---|---|
| Direct bypass blockable | Step 6/7 network + `NETWORK_ENFORCED` rollout phase |
| Credential isolation | Step 5 + `PROVIDER_GATEWAY_ISOLATED_CREDENTIALS` |
| Fail-closed ENFORCE | `FailClosedMatrix` + existing gateway posture |
| Streaming reliability | stream lifecycle + duration cap tests |
| FinOps accuracy | usage in receipt metadata |
| Rollback to WARN | `GATEWAY_ENFORCEMENT_MODE=WARN` + matrix WARN column |