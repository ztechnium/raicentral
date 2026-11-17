# Step 8 — Production Hardening and Rollout

## Goal

Make the infrastructure enforcement gateway safe for production traffic.

---

# 1. Availability

Gateway must be horizontally scalable.

Requirements:

```text
stateless request path where possible
shared policy cache
shared receipt persistence
health checks
readiness checks
graceful shutdown
connection draining
```

---

# 2. Policy Cache

The data plane should not depend on slow control-plane queries for every request.

Add signed/cacheable enforcement state for:

```text
runtime identity
authority snapshot
delegation
model permissions
provider permissions
budget state
policy bundle
```

Define TTL and invalidation semantics.

High-risk changes must invalidate quickly:

```text
credential revoke
runtime suspend
delegation revoke
emergency control
```

---

# 3. Fail-Closed Matrix

Define behavior explicitly.

| Failure | Observe | Warn | Enforce |
|---|---|---|---|
| policy service unavailable | allow+log | allow+warn | deny |
| authority unavailable | allow+log | allow+warn | deny |
| provider credential unavailable | fail | fail | fail |
| receipt persistence temporarily unavailable | configurable | configurable | usually deny for high-risk |
| cost estimation unavailable | allow | warn | policy-dependent |

Do not leave failure behavior implicit.

---

# 4. Performance SLOs

Track at minimum:

```text
gateway overhead p50
gateway overhead p95
gateway overhead p99
time to first token overhead
policy decision latency
provider latency
error rate
stream interruption rate
```

Target gateway overhead separately from upstream provider latency.

---

# 5. Request Limits

Add:

```text
max body size
max header size
max streaming duration
max concurrent streams
per-runtime concurrency
per-provider concurrency
rate limits
```

---

# 6. FinOps

Before execution:

```text
estimate tokens
estimate cost
reserve budget
```

After execution:

```text
read provider usage
calculate actual cost
release/adjust reservation
finalize receipt
```

Track:

```text
estimated
actual
variance
pricing version
```

---

# 7. Security Hardening

Add:

- secret redaction
- structured audit logging
- outbound destination allowlist
- SSRF protection
- upstream URL validation
- no arbitrary client-supplied upstream URLs
- TLS verification
- configurable CA bundle
- mTLS for internal gateway access where required
- strict runtime credential validation
- admin/runtime API separation

---

# 8. Rollout Strategy

Use staged rollout.

## Phase A — Mirror

Application still calls provider directly.

RaiCentral receives mirrored metadata where possible.

No blocking.

## Phase B — Proxy Optional

Selected workloads use gateway.

Direct provider path still exists.

## Phase C — Proxy Preferred

Gateway is default.

Bypass produces warning.

## Phase D — Network Enforced

Direct provider egress denied.

## Phase E — Private Provider Access

Use private endpoints/private service connectivity where supported.

---

# 9. Migration Tooling

Provide:

- SDK base URL examples
- OpenAI migration examples
- Anthropic migration examples
- Azure OpenAI migration examples
- GCP examples
- Terraform snippets
- Kubernetes snippets
- validation CLI

Suggested CLI:

```text
rai gateway doctor
```

Checks:

```text
gateway reachable
runtime identity valid
provider credential valid
direct provider path blocked
DNS configuration
policy cache healthy
streaming works
```

---

# 10. Production Readiness Tests

Must include:

- high concurrency
- long streaming responses
- provider timeout
- gateway restart during stream
- control-plane outage
- credential rotation
- runtime suspension
- policy emergency deny
- direct bypass attempt
- budget exhaustion
- approval-required request
- multi-provider routing
- network partition

---

# Exit Criteria

The infrastructure gateway is production-ready when:

- direct provider bypass can be blocked
- provider credentials are isolated
- runtime identity is infrastructure-rooted
- policy decisions are fail-closed in mandatory mode
- streaming is reliable
- cost/usage is accurate enough for FinOps
- receipts represent actual gateway executions
- GCP/Azure/Kubernetes deployment patterns are validated
- bypass attempts are observable
- rollback to warning mode is operationally safe
