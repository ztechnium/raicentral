# Step 4 — Expand Provider Adapters and Streaming Semantics

## Goal

Generalize the provider gateway beyond OpenAI while preserving provider-native protocols.

---

# Provider Order

Implement in this order:

1. Azure OpenAI
2. Anthropic
3. Gemini / Google AI
4. Vertex AI
5. AWS Bedrock
6. Ollama / OpenAI-compatible local providers

---

# Principle

Do not force all providers through:

```python
generate(prompt, variables, model)
```

Instead:

```text
Provider-native request
    |
    v
ProviderProtocolAdapter
    |
    +--> governance extraction
    +--> upstream routing
    +--> usage extraction
```

---

# Tasks

## Azure OpenAI

Support:

```text
/openai/deployments/{deployment}/chat/completions
/openai/deployments/{deployment}/embeddings
```

Enforcement must map:

```text
deployment -> governed model resource
```

Support provider credential injection through:

```text
api-key
```

and prepare for managed identity in a later step.

---

## Anthropic

Support Messages API.

Extract:

```text
model
max_tokens
system
messages
tools
stream
```

Capture usage:

```text
input_tokens
output_tokens
cache_creation_input_tokens
cache_read_input_tokens
```

---

## Gemini / Google AI

Support native Gemini generate content patterns.

Extract:

```text
model
contents
system instruction
tools
generation config
```

---

## Vertex AI

Support project/location/model routing.

Do not trust project or location supplied by the client without validating policy.

Map:

```text
GCP project
location
publisher/model
endpoint
```

into resource governance.

---

## Bedrock

Support model invocation and Converse where possible.

Map:

```text
region
model ID
inference profile
```

into governed resources.

---

## Ollama / local providers

Treat as a provider class but require explicit allowlisting.

Do not assume local means trusted.

---

# Common Streaming Contract

Create a shared internal stream lifecycle:

```text
REQUEST_ACCEPTED
UPSTREAM_CONNECTED
FIRST_TOKEN
STREAM_CHUNK
STREAM_COMPLETED
STREAM_ABORTED
```

Capture:

```text
time_to_first_token
total_duration
client_disconnect
upstream_disconnect
usage_available
```

---

# Common Metering Contract

Normalize into:

```python
ProviderUsage:
    input_tokens
    output_tokens
    cached_input_tokens
    reasoning_tokens
    total_tokens
    provider_reported
    confidence
```

Do not fabricate zero token usage when provider usage is unavailable.

Use:

```text
unknown
estimated
provider_reported
```

confidence states.

---

# Resource Mapping

Add registry mappings for:

```text
provider
provider account/project/subscription
model/deployment
region
endpoint
```

Runtime policy should be able to target these.

---

# Tests

For each provider:

- authentication injection
- model extraction
- policy deny
- provider routing
- streaming
- usage extraction
- provider error passthrough
- receipt finalization
- sensitive header stripping

---

# Exit Criteria

The provider gateway supports multiple major providers with:

- native protocol compatibility
- shared governance
- shared metering
- shared receipt lifecycle
- no duplicate policy engine
