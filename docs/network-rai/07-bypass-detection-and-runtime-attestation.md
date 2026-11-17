# Step 7 — Bypass Detection and Runtime Attestation

## Goal

Detect workloads attempting to reach AI providers outside the governed gateway.

This is complementary to network enforcement.

---

# Target Capability

RaiCentral should distinguish:

```text
DECLARED
OBSERVED
AUTHORIZED
```

Example:

```text
Declared:
SupportAgent -> OpenAI

Observed:
SupportAgent -> OpenAI
SupportAgent -> Anthropic

Authorized:
OpenAI only

Result:
shadow AI / bypass incident
```

---

# Tasks

## 1. Add observed connection event model

Suggested fields:

```text
timestamp
source workload
source process/container
destination hostname
destination IP
destination port
protocol
cloud resource identity
runtime identity if resolved
gateway_used
provider_guess
action
```

---

## 2. Add provider destination classifier

Map outbound destinations to:

```text
OPENAI
ANTHROPIC
AZURE_OPENAI
GOOGLE_AI
VERTEX
BEDROCK
OLLAMA
UNKNOWN_AI
```

Keep classifier versioned.

---

## 3. Node/host observation interface

Define an ingestion contract first.

Possible future collectors:

```text
eBPF agent
Cilium/Hubble
cloud flow logs
Azure NSG flow logs
GCP VPC Flow Logs
proxy logs
firewall logs
```

Do not tie the backend to one telemetry source.

---

## 4. Bypass event rules

Generate incident when:

```text
AI provider destination detected
AND
gateway_used = false
AND
runtime is expected to be governed
```

Severity may depend on:

```text
environment
provider
data sensitivity
runtime risk tier
enforcement mode
```

---

## 5. Runtime attestation status

Add a computed state per runtime:

```text
UNKNOWN
PARTIALLY_OBSERVED
GATEWAY_ONLY
NETWORK_ENFORCED
BYPASS_DETECTED
```

Display this separately from policy compliance.

---

## 6. Correlate with existing objects

Link observed traffic to:

```text
AI System
Agent
Agent Version
Runtime Identity
Environment
Trajectory
Governance Incident
```

when possible.

---

## 7. Enforcement assurance dashboard data

Expose API metrics:

```text
gateway requests
direct AI egress attempts
blocked bypass attempts
unclassified AI destinations
runtimes without network enforcement
```

---

# Tests

- classify known provider domains
- unknown AI endpoint handling
- bypass event generated
- allowed gateway traffic not flagged
- duplicated network events deduplicated
- runtime attestation updated
- incident links to runtime identity

---

# Exit Criteria

RaiCentral can report not only:

```text
what passed through the gateway
```

but also:

```text
what attempted to avoid the gateway
```
