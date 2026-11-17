# Step 2 — Build the Provider Proxy Core

## Goal

Create the reusable L7 proxy foundation that can accept provider-native HTTP traffic, invoke RaiCentral governance, forward allowed requests, and return provider-native responses.

Do not implement every provider yet.

---

# Target Architecture

```text
Provider-Compatible Route
        |
        v
ProviderProtocolAdapter
        |
        +--> parse governance metadata
        +--> preserve original request body
        |
        v
Canonical Gateway Action
        |
        v
RuntimeEnforcementService
        |
        +--> DENY
        +--> APPROVAL
        +--> ALLOW
                 |
                 v
          Provider Transport
                 |
                 v
           Upstream Provider
```

---

# New Package

Recommended:

```text
src/rai_gateway/provider_gateway/
    __init__.py
    api.py
    domain.py
    transport.py
    auth.py
    headers.py
    errors.py
    metering.py
    streaming.py
    adapters/
        base.py
```

---

# Tasks

## 1. Add ProviderProtocolAdapter

Define an interface such as:

```python
class ProviderProtocolAdapter(ABC):
    provider_name: str

    def matches_request(...)
    def extract_governance_action(...)
    def upstream_url(...)
    def upstream_headers(...)
    def sanitize_response_metadata(...)
    def extract_usage(...)
```

Important:

The adapter must **not** rewrite the entire provider payload into a generic RAI format.

Keep the raw provider request intact.

---

## 2. Add upstream transport

Use `httpx.AsyncClient`.

Requirements:

- connection pooling
- configurable timeouts
- request cancellation
- streaming support
- safe header forwarding
- bounded body size
- upstream DNS resolution
- retry policy only for safe retryable conditions
- no retry on policy rejection
- no retry after partial streaming response

---

## 3. Header policy

Explicitly define:

### Strip inbound headers

Examples:

```text
Host
Content-Length
Connection
Proxy-Authorization
provider API keys from client
```

### Inject gateway-controlled headers

Examples:

```text
Authorization
api-key
x-goog-user-project
provider-specific credentials
```

### Preserve safe tracing headers

Examples:

```text
traceparent
tracestate
x-request-id
```

---

## 4. Credential source

Provider credentials must come from server-side configuration or secret references.

The client must not be allowed to supply upstream provider credentials.

Add a provider credential resolver abstraction:

```python
ProviderCredentialResolver
```

Initial implementation may use existing settings.

Later steps will move this to proper secret isolation.

---

## 5. Request body handling

Support:

- JSON bodies
- streaming responses
- multipart where required later
- body size limits
- canonical hash
- optional sensitive content redaction before persistence

Do not persist full request bodies by default.

Persist:

```text
body digest
provider
operation
model
usage
policy decision
receipt
```

---

## 6. Error normalization

Internally normalize gateway failures, but expose predictable provider-compatible HTTP behavior.

For policy denial:

```text
HTTP 403
```

For missing runtime identity:

```text
HTTP 401
```

For approval required:

choose one stable contract and document it, for example:

```text
HTTP 409
```

with RAI metadata.

Do not pretend upstream provider returned these errors.

Include a clearly namespaced object:

```json
{
  "rai": {
    "decision": "REQUIRE_APPROVAL",
    "receipt_id": "...",
    "approval_request_id": "..."
  }
}
```

---

# Tests

- provider adapter interface
- stripped credential headers
- upstream credential injection
- body digest correctness
- timeout handling
- upstream 4xx passthrough
- upstream 5xx passthrough
- RAI policy deny before upstream call
- no upstream request on deny
- receipt finalized on success
- receipt finalized on upstream failure

---

# Exit Criteria

- provider-native traffic can be proxied through a reusable core
- governance executes before upstream traffic
- upstream credentials are gateway controlled
- request and response transport logic is separated from policy logic
- code is ready for the first real provider-compatible route
