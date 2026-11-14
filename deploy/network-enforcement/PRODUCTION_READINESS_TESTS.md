# Production readiness tests (Step 8)

Run after Steps 1–7 acceptance tests pass.

## Automated

```bash
uv run pytest tests/provider_gateway/test_production_hardening.py -q
uv run pytest tests/provider_gateway tests/network_enforcement -q
```

## Scenario matrix

| Scenario | Command / signal |
|---|---|
| High concurrency | `hey` or k6 against `/providers/openai/v1/models` with runtime key |
| Long streaming | chat completion `stream=true` > 5 minutes (within `PROVIDER_GATEWAY_MAX_STREAM_DURATION_SECONDS`) |
| Provider timeout | block upstream; expect 504 + receipt FAILED |
| Gateway restart during stream | SIGTERM pod; client should see abort + receipt terminal state |
| Control-plane DB outage | `/ready` → `not_ready`; ENFORCE denies new provider calls |
| Credential rotation | revoke credential; policy cache fast invalidation + 401/403 |
| Runtime suspension | suspend identity; deny at gateway |
| Policy emergency deny | emergency control; deny regardless of WARN |
| Direct bypass attempt | catalog host from workload → blocked (Step 6/7) |
| Budget exhaustion | finops deny before upstream |
| Approval-required | 409 + `approval_request_id` |
| Multi-provider routing | OpenAI + Anthropic adapters both receipt |
| Network partition | gateway pod ↔ DB split; fail-closed matrix applies |

## SLO baselines

Inspect live snapshot:

```bash
curl -s "$GATEWAY/v1/providers/_gateway/status" -H "X-RAI-Agent-Key: $SECRET"
```

Fields: `slo.gateway_overhead_ms`, `policy_cache`, `fail_closed_matrix`.