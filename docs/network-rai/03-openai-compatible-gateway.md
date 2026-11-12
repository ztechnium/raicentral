# Step 3 — Add OpenAI-Compatible Gateway

## Goal

Make RaiCentral usable as an OpenAI-compatible endpoint so existing OpenAI SDK users can change the base URL rather than rewrite their application around `/v1/execute`.

---

# Target Developer Experience

Example:

```ts
const client = new OpenAI({
  apiKey: process.env.RAI_RUNTIME_CREDENTIAL,
  baseURL: "https://rai.company/providers/openai/v1"
})
```

The developer should continue using normal OpenAI SDK methods.

RaiCentral should:

```text
authenticate runtime
resolve authority
evaluate policy
forward request
stream response
meter usage
finalize receipt
```

---

# Initial Endpoint Scope

Implement in this order:

1. `/providers/openai/v1/chat/completions`
2. `/providers/openai/v1/responses`
3. `/providers/openai/v1/embeddings`
4. `/providers/openai/v1/models`

Do not implement every OpenAI endpoint in the first PR.

---

# Tasks

## 1. Add OpenAI protocol adapter

Create:

```text
provider_gateway/adapters/openai.py
```

Extract:

```text
operation
model
stream flag
tool declarations
max output tokens
response format
metadata
```

Build canonical governance action.

---

## 2. Runtime authentication

Use the RaiCentral runtime credential instead of an OpenAI API key.

Accepted inbound forms:

```text
Authorization: Bearer <RAI_RUNTIME_CREDENTIAL>
```

Optionally preserve:

```text
X-RAI-Agent-Key
```

for backward compatibility.

Do not forward that credential upstream.

---

## 3. Upstream credential injection

Gateway resolves actual OpenAI credential server-side.

Forward:

```text
Authorization: Bearer <OPENAI_PROVIDER_SECRET>
```

---

## 4. Model policy enforcement

Before forwarding:

```text
requested model
    |
    v
authority model scope
    |
    v
policy rules
    |
    v
budget precheck
```

Reject:

- undeclared model
- unauthorized model family
- provider not allowed
- environment not allowed
- suspended runtime identity

---

## 5. Tool declaration awareness

Extract declared tools from request payload.

Do not yet execute tool calls.

Use declarations for:

- policy checks
- risk classification
- resource catalog mapping
- evidence

---

## 6. Streaming

Support:

```text
stream=false
stream=true
```

For streaming:

- start upstream only after governance allow
- stream chunks without buffering entire response
- capture final usage if provider emits it
- if final usage is missing, mark metering confidence accordingly
- finalize receipt when stream closes
- record client disconnects

---

## 7. Usage and cost

Extract:

```text
prompt/input tokens
completion/output tokens
cached tokens if available
reasoning tokens if available
model actually used
```

Map to FinOps.

Store:

```text
estimated cost before call
actual cost after call
variance
```

---

## 8. Preserve `/v1/execute`

Do not remove or rewrite the current high-level RAI execution API.

The product now has two paths:

```text
RAI-native orchestration
/provider-compatible proxy
```

---

# Tests

Use the official OpenAI SDK against a local mocked upstream.

Test:

- non-stream response
- streaming response
- embeddings
- model deny
- budget deny
- approval required
- runtime credential invalid
- provider secret never exposed
- upstream status passthrough
- tool declarations captured
- token usage persisted
- receipt links to trajectory/execution

---

# Exit Criteria

A standard OpenAI SDK can use RaiCentral by changing only:

```text
baseURL
credential
```

and all requests are governed before reaching OpenAI.
