# Step 6 — Network Enforcement Reference Deployments

## Goal

Make RaiCentral mandatory at infrastructure level.

This step is where the system moves from:

```text
application chooses gateway
```

to:

```text
network requires gateway
```

---

# Important Boundary

RaiCentral should not become a general firewall product.

Use native cloud/network controls to force AI traffic through RaiCentral.

RaiCentral provides:

```text
AI-aware L7 enforcement
```

Infrastructure provides:

```text
routing + egress restriction
```

---

# A. Kubernetes Reference Deployment

Provide manifests/Helm values for:

```text
rai-gateway namespace
rai-gateway deployment
service
network policies
pod disruption budget
horizontal scaling
```

Example policy goal:

```text
workload namespace
    ALLOW -> rai-gateway
    DENY  -> direct internet AI endpoints
```

If cluster-wide internet denial is not practical, document provider-specific egress controls.

---

# B. GCP Reference Deployment

Target architecture:

```text
GKE/GCE workload
      |
      v
VPC egress control
      |
      v
RaiCentral Gateway
      |
      v
Cloud NAT / private connectivity
      |
      v
AI provider
```

Provide Terraform examples for:

- gateway subnet
- internal load balancer where appropriate
- Cloud NAT
- firewall rules
- workload identity
- Secret Manager
- logging
- optional Secure Web Proxy integration
- private service connectivity where applicable

Enforcement goal:

```text
application subnet -> AI provider = denied
application subnet -> RaiCentral = allowed
RaiCentral -> AI provider = allowed
```

---

# C. Azure Reference Deployment

Target architecture:

```text
Spoke workload VNet
       |
       v
Hub / route enforcement
       |
       v
RaiCentral Gateway
       |
       v
Azure OpenAI private endpoint
or approved external provider
```

Provide Terraform/Bicep examples for:

- hub/spoke routing
- UDR
- NSG
- Azure Firewall integration
- Managed Identity
- Key Vault
- Private Endpoint for Azure OpenAI
- public access disabled where supported

Goal:

```text
workload -> Azure OpenAI directly = denied
workload -> RaiCentral = allowed
RaiCentral -> Private Endpoint = allowed
```

---

# D. Linux / VM Reference Deployment

Provide:

```text
systemd service
Docker deployment
nftables examples
explicit proxy mode
```

Initial production recommendation:

```text
explicit internal gateway endpoint
+
firewall deny direct provider access
```

Do not make transparent TLS MITM a required first release.

---

# E. DNS and Domain Controls

Maintain provider destination catalog:

```text
api.openai.com
api.anthropic.com
generativelanguage.googleapis.com
aiplatform.googleapis.com
Azure OpenAI endpoints
Bedrock regional endpoints
```

Do not rely only on static IP lists.

Provider domains can change.

Use domain-aware firewall/proxy controls where supported.

---

# F. Deployment Modes

Document:

```text
OBSERVE
WARN
ENFORCE
```

Network rollout:

### Observe
do not block direct egress

### Warn
detect direct egress and alert

### Enforce
deny direct provider egress

---

# Acceptance Tests

For each reference deployment prove:

1. workload can call RaiCentral
2. RaiCentral can call provider
3. workload direct provider call fails
4. policy deny prevents provider call
5. runtime identity is preserved
6. provider secret is not present in workload
7. receipts show actual gateway execution

---

# Exit Criteria

There is at least one tested reference deployment for:

- Kubernetes
- GCP
- Azure

with direct provider bypass blocked by infrastructure configuration.

---

## Implementation (reference artifacts)

Shipped in this repository under `deploy/network-enforcement/`:

| Area | Location |
|------|----------|
| Provider destination catalog | `catalog/provider-destinations.yaml` |
| Kubernetes namespaces, policies, PDB, HPA | `kubernetes/` |
| Helm overlay | `kubernetes/values-network-enforcement.yaml` + `deploy/helm/rai-control-plane` |
| GCP Terraform reference | `gcp/terraform/` |
| Azure Terraform reference | `azure/terraform/` |
| Linux / VM (systemd, Docker, nftables) | `linux/` |
| Rollout modes & acceptance tests | `DEPLOYMENT_MODES.md`, `ACCEPTANCE_TESTS.md` |

Runtime helpers: `rai_gateway.network_enforcement` (catalog classification and rollout mode). Automated parity tests: `tests/network_enforcement/`.

---

# Implementation (branch `feature/netwrok-geteway`)

Reference artifacts ship under `deploy/network-enforcement/`:

- **Kubernetes** — namespace, workload/gateway `NetworkPolicy`, PDB, HPA, Helm overlay (`kubernetes/values-network-enforcement.yaml`); optional chart templates for PDB/HPA/gateway egress in `deploy/helm/rai-control-plane/`.
- **GCP / Azure** — Terraform examples under `gcp/terraform/` and `azure/terraform/`.
- **Linux/VM** — systemd unit, Docker Compose, nftables skeleton under `linux/`.
- **DNS catalog** — `catalog/provider-destinations.yaml` plus `rai_gateway.network_enforcement.classify_hostname` (validated in `tests/network_enforcement/`).
- **Rollout modes** — documented in `deploy/network-enforcement/DEPLOYMENT_MODES.md`; gateway env `GATEWAY_ENFORCEMENT_MODE`.
- **Acceptance tests** — checklist in `deploy/network-enforcement/ACCEPTANCE_TESTS.md`.
