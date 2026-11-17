# GCP network enforcement POC — deployment and verification checklist

**Status:** Pre-live. Do **not** describe the gateway as network-enforced until this checklist is executed against an isolated GCP workload with recorded evidence.

## Prerequisites

- Terraform from `deploy/network-enforcement/gcp/terraform/` applied in a dedicated POC project
- `gateway_network_rollout_mode = enforce` (or org-approved equivalent)
- MIG gateway instances healthy behind internal ILB
- Workload VM in application subnet with only gateway egress on 443
- Runtime identity + credential issued for the workload (no provider API key on the VM)
- Redis and PostgreSQL reachable from gateway replicas (policy cache + receipts)

## Deploy

1. `terraform init && terraform apply` in `gcp/terraform` with POC tfvars
2. Confirm ILB address and forwarding rule target the gateway MIG
3. Confirm firewall: workload → ILB:443 allow; workload → `0.0.0.0/0:443` deny for catalog destinations
4. Startup script on MIG nodes: gateway container passes `/health` and `/ready`
5. Record: ILB IP, MIG instance names, firewall rule IDs, rollout mode

## Verification matrix (capture command output + receipt IDs)

| # | Scenario | Pass criteria |
|---|----------|----------------|
| 1 | Gateway path | Workload `curl` to `http://<ILB>/health` → 200 |
| 2 | Governed completion | OpenAI-compatible call via gateway returns 200 + `receipt_id` in response headers/metadata |
| 3 | Direct egress blocked | From workload: `curl https://api.openai.com` → connection refused or firewall deny |
| 4 | Policy DENY | Apply deny policy for model/provider → gateway 403, **no** upstream call (receipt shows DENY) |
| 5 | Revoked identity | Revoke runtime identity → next gateway call 401/403; policy cache revocation visible in `/_gateway/status` |
| 6 | Receipts complete | Each scenario produces terminal receipt with authority digest + execution outcome |
| 7 | Multi-replica cache | Revoke on one gateway admin path; second replica denies within fast-invalidate TTL |

## Automated parity (local / CI)

```bash
export TEST_DATABASE_URL=postgresql://rai_user:rai_password@localhost:5434/rai_gateway
export REDIS_URL=redis://localhost:6380
uv run pytest tests/provider_gateway/test_policy_cache_redis.py -q
uv run pytest tests/provider_gateway/test_upstream_security.py -q
uv run pytest tests/provider_gateway tests/network_enforcement -q
```

## Evidence package

Attach to the issue / evidence store:

- Terraform outputs (sanitized)
- Firewall rule screenshots or `gcloud compute firewall-rules describe` excerpts
- Failed direct-egress attempt log
- Successful gateway call with receipt ID
- Policy deny receipt export
- Post-revocation denial receipt export

## Sign-off

- [ ] All seven verification rows passed on live infrastructure
- [ ] Evidence linked from ZTEC-20
- [ ] Only then proceed to PR-G/H from Step 9 gap matrix