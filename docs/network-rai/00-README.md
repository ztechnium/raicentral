# RaiCentral — Infrastructure Enforcement Gateway Implementation Plan

## Objective

Move RaiCentral from an **application-layer governance gateway** into an **infrastructure-enforced AI egress gateway** without discarding the current runtime-enforcement architecture.

The target state is:

```text
Workload
  |
  | cannot reach AI providers directly
  v
RaiCentral Enforcement Gateway
  |
  | authenticate workload
  | resolve runtime identity
  | evaluate authority/policy/budget
  | execute or deny
  | meter + receipt
  v
Approved AI Provider / MCP / Tool
```

The current repository already contains important building blocks:

- `src/rai_gateway/runtime_enforcement/`
- `RuntimeEnforcementService`
- runtime credentials and authority resolution
- approval handling
- control decisions and receipts
- `services/llm_providers.py`
- `/v1/execute`
- `/v1/runtime/actions/execute`
- MCP runtime adapter
- Python and TypeScript SDKs

Do not replace these foundations. Extend them.

---

# Delivery Sequence

1. `01-runtime-gateway-boundary.md`
2. `02-provider-proxy-core.md`
3. `03-openai-compatible-gateway.md`
4. `04-provider-adapters-and-streaming.md`
5. `05-workload-identity-and-credential-isolation.md`
6. `06-network-enforcement-reference-deployments.md`
7. `07-bypass-detection-and-runtime-attestation.md`
8. `08-production-hardening-and-rollout.md`

Each step must be independently mergeable and must leave the system in a deployable state.

---

# Architectural Principles

## 1. SDK is not the trust boundary

The SDK may improve developer experience, but enforcement must happen at the gateway and infrastructure layers.

## 2. Preserve provider compatibility

Do not force every provider request into the current generic:

```text
generate(prompt, variables, model)
```

That abstraction remains useful for `/v1/execute`, but the infrastructure gateway must preserve provider-native request/response behavior where possible.

## 3. Canonicalize for policy, not for transport

Transport payloads remain provider-native.

Internally derive a canonical governance action:

```text
provider
model
operation
input classification
tool declarations
estimated tokens
estimated cost
runtime identity
delegation
environment
```

Use this canonical action for enforcement.

## 4. Fail closed for mandatory enforcement

If the runtime identity, policy snapshot, authority resolution, or gateway execution path is unavailable in mandatory mode, deny the request.

## 5. Separate control plane and data plane responsibilities

Control plane:
- policy
- registry
- runtime identity
- delegations
- approvals
- budgets
- risk
- provider configuration

Data plane:
- request authentication
- request normalization
- enforcement
- forwarding
- streaming
- metering
- receipts

## 6. Infrastructure must make bypass difficult

The target production state is not merely:

```text
developer chooses RaiCentral
```

It is:

```text
network and credential controls require RaiCentral
```

---

# Definition of Done for the Full Program

RaiCentral can be deployed so that:

- applications do not hold direct provider credentials
- applications cannot directly egress to approved AI provider endpoints
- workload identity is mapped to a RaiCentral runtime identity
- OpenAI-compatible calls can pass through RaiCentral without application-specific request translation
- provider calls are governed before execution
- streaming is supported
- tokens and cost are metered from provider responses
- enforcement receipts are finalized from actual gateway executions
- direct-provider bypass attempts are observable and reportable
- GCP and Azure reference deployments exist
- Kubernetes reference enforcement exists
- fail-closed behavior is testable
- rollout supports observe → warn → enforce modes
