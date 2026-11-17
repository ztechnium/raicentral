# Network enforcement reference deployments (Step 6)

RaiCentral is an **AI-aware L7 enforcement gateway**. These artifacts show how to combine **network egress controls** with the gateway so workloads cannot reach AI providers directly.

```text
workload ──X──> AI provider          (denied in ENFORCE mode)
workload ─────> RaiCentral gateway   (allowed)
RaiCentral ───> AI provider          (allowed; provider secrets live on gateway only)
```

## Layout

| Path | Purpose |
|------|---------|
| `catalog/provider-destinations.yaml` | Domain catalog for firewalls and proxies |
| `kubernetes/` | Namespace, network policies, PDB, HPA, Helm overlay |
| `gcp/terraform/` | VPC egress, firewall, NAT, ILB reference |
| `azure/terraform/` | Hub/spoke UDR, NSG, private Azure OpenAI |
| `linux/` | systemd, Docker, nftables explicit-proxy pattern |
| `ACCEPTANCE_TESTS.md` | Per-environment validation checklist |

## Rollout modes

Align infrastructure posture with `GATEWAY_ENFORCEMENT_MODE` on the gateway (`OBSERVE` | `WARN` | `ENFORCE`):

| Mode | Network behavior |
|------|------------------|
| **OBSERVE** | Do not block direct egress; collect flow/proxy logs |
| **WARN** | Detect direct provider egress; alert (Step 7 observability) |
| **ENFORCE** | Deny workload → catalog provider destinations |

See [DEPLOYMENT_MODES.md](./DEPLOYMENT_MODES.md) for rollout guidance.

## Quick start (Kubernetes)

```bash
kubectl apply -f deploy/network-enforcement/kubernetes/namespace.yaml
helm upgrade --install rai-gateway deploy/helm/rai-control-plane \
  -n rai-gateway \
  -f deploy/network-enforcement/kubernetes/values-network-enforcement.yaml
kubectl apply -f deploy/network-enforcement/kubernetes/network-policy-gateway-egress.yaml
kubectl apply -f deploy/network-enforcement/kubernetes/network-policy-workload.yaml
```

Label AI workload namespaces: `rai.ztechnium.com/network-enforcement: enforce`

## Provider catalog in application code

```python
from rai_gateway.network_enforcement import classify_hostname, load_provider_destination_catalog
```

The built-in classifier matches `deploy/network-enforcement/catalog/provider-destinations.yaml` (verified in CI).