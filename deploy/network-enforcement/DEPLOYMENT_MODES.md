# Deployment modes — network + gateway

Infrastructure rollout should match gateway configuration:

| `GATEWAY_ENFORCEMENT_MODE` | Workload network | Gateway behavior |
|----------------------------|------------------|------------------|
| `OBSERVE` | Allow direct egress | Log enforcement decisions; do not deny at gateway |
| `WARN` | Allow direct egress | Deny only if policy requires; emit bypass signals |
| `ENFORCE` | **Deny** direct egress to catalog domains | Fail closed on missing identity/policy |

## Recommended progression

1. Deploy gateway in a dedicated namespace (`rai-gateway`) with provider credentials in Secret Manager / K8s secrets **only on gateway pods**.
2. Point a pilot workload at the internal gateway Service URL; keep `OBSERVE` and open egress.
3. Enable flow logging; confirm receipts show gateway execution (see `ACCEPTANCE_TESTS.md`).
4. Switch to `WARN`; configure alerts for direct catalog hostname hits (firewall / VPC flow logs).
5. Apply workload network policies (or cloud firewall rules) and set `ENFORCE` on gateway + infrastructure.

## Environment variables (gateway)

```text
GATEWAY_ENFORCEMENT_MODE=ENFORCE
FAIL_CLOSED_ON_RAI_UNAVAILABLE=true
PROVIDER_GATEWAY_ISOLATED_CREDENTIALS=true
```

Workloads should **not** receive `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or cloud provider inference keys — only RaiCentral runtime credentials.