# Step 1 — Define the Runtime Gateway Boundary

## Goal

Refactor the existing runtime enforcement code so it can become the common enforcement engine for:

- provider-compatible LLM proxy traffic
- MCP traffic
- tool traffic
- existing SDK execution
- future infrastructure gateway traffic

This step does **not** add network enforcement yet.

---

# Current Relevant Code

Primary areas:

```text
src/rai_gateway/runtime_enforcement/
src/rai_gateway/services/llm_controller.py
src/rai_gateway/services/llm_providers.py
src/rai_gateway/services/rai_guard.py
src/rai_gateway/identity/
src/rai_gateway/approval/
src/rai_gateway/risk_intelligence/
src/rai_gateway/finops/
```

Existing endpoints include:

```text
POST /v1/execute
POST /v1/runtime/actions/authorize
POST /v1/runtime/actions/execute
```

The repository already has:

```python
RuntimeActionAdapter
RuntimeAdapterRegistry
RuntimeEnforcementService
```

Use these rather than creating a second enforcement engine.

---

# Required Design

Introduce an explicit internal boundary:

```text
Inbound Transport
    |
    v
Gateway Request Adapter
    |
    v
CanonicalRuntimeAction
    |
    v
RuntimeEnforcementService
    |
    +--> DENY
    +--> REQUIRE_APPROVAL
    +--> ALLOW
             |
             v
       Downstream Executor
```

---

# Tasks

## 1. Add a canonical gateway action model

Create a transport-neutral model, for example:

```text
runtime_enforcement/gateway_action.py
```

Suggested fields:

```python
class GatewayAction:
    action_type
    provider
    operation
    model
    target
    parameters
    declared_tools
    input_metadata
    estimated_input_tokens
    estimated_output_tokens
    estimated_cost
    environment
    delegation_id
    trace_id
    correlation_id
    client_context
```

Do not store full prompts in the canonical object by default.

Separate:

```text
governance metadata
```

from:

```text
sensitive payload body
```

---

## 2. Define enforcement result contract

Add a stable internal result:

```python
class GatewayDecision:
    allowed
    decision
    reason_codes
    approval_request_id
    authority_snapshot_id
    receipt_id
    obligations
    policy_version
```

This should be usable by:

- MCP execution
- OpenAI proxy
- Anthropic proxy
- Gemini proxy
- SDK hooks

---

## 3. Separate authorize from execute cleanly

The current service already exposes both ideas.

Make the boundary explicit:

```text
authorize_gateway_action(...)
execute_authorized_gateway_action(...)
```

Do not combine transport parsing with policy logic.

---

## 4. Add execution-source classification

Every runtime receipt should identify how the action was executed.

Suggested enum:

```text
SDK_HOOK
RAI_EXECUTED
PROVIDER_PROXY
MCP_PROXY
TOOL_PROXY
NETWORK_OBSERVED
CLIENT_REPORTED
```

---

## 5. Add enforcement mode

Add a canonical enforcement mode:

```text
OBSERVE
WARN
ENFORCE
```

Optional later extension:

```text
NETWORK_ENFORCED
```

Behavior:

```text
OBSERVE
  evaluate, log, do not block

WARN
  evaluate, mark violations, allow

ENFORCE
  evaluate and fail closed
```

---

## 6. Preserve existing APIs

Do not break:

```text
/v1/execute
/v1/runtime/actions/authorize
/v1/runtime/actions/execute
```

They should call the new shared boundary.

---

# Tests

Add unit tests for:

- canonical action creation
- deterministic decision serialization
- fail-closed enforcement
- observe mode
- warn mode
- enforce mode
- approval-required flow
- receipt creation
- execution-source persistence

Add regression tests proving existing MCP enforcement still works.

---

# Exit Criteria

This step is complete when:

- there is one shared runtime enforcement boundary
- transport code no longer owns governance logic
- existing endpoints still work
- receipts identify execution source
- enforcement mode is explicit
- future provider proxies can plug into the same service without duplicating policy logic
