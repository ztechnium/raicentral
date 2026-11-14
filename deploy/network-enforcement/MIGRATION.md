# Migration tooling — infrastructure gateway (Step 8)

## SDK base URL

Point the OpenAI-compatible client at RaiCentral instead of the vendor:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://rai-gateway.internal/providers/openai/v1",
    api_key="unused",  # do not use vendor keys in workloads
    default_headers={"X-RAI-Agent-Key": "<runtime-credential>"},
)
```

TypeScript:

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "https://rai-gateway.internal/providers/openai/v1",
  apiKey: "unused",
  defaultHeaders: { "X-RAI-Agent-Key": process.env.RAI_RUNTIME_SECRET! },
});
```

## Provider-specific routes

| Provider | Gateway prefix |
|---|---|
| OpenAI | `/providers/openai/v1/...` |
| Anthropic | `/providers/anthropic/v1/...` |
| Azure OpenAI | `/providers/azure_openai/{api-version}/...` |

## Terraform (private ILB sketch)

```hcl
resource "google_compute_forwarding_rule" "rai_gateway" {
  name                  = "rai-gateway-ilb"
  load_balancing_scheme = "INTERNAL"
  backend_service       = google_compute_region_backend_service.rai.id
  ports                 = ["443"]
}
```

## Kubernetes

```yaml
env:
  - name: OPENAI_BASE_URL
    value: http://rai-gateway.rai-gateway.svc.cluster.local:8000/providers/openai/v1
  - name: RAI_RUNTIME_SECRET
    valueFrom:
      secretKeyRef:
        name: workload-runtime-credential
        key: secret
```

## Validation CLI

```bash
uv run rai-gateway doctor \
  --base-url https://rai-gateway.internal \
  --runtime-secret "$RAI_RUNTIME_SECRET"
```

Checks: reachability, readiness, policy cache health, fail-closed mode, rollout phase, optional runtime status.