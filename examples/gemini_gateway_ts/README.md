# Gemini on Vertex AI through the RAI gateway (TypeScript)

Calls `gemini-3.1-flash-lite` on Vertex AI via the provider-compatible gateway route
`/providers/vertex-ai` with the official `@google/genai` SDK (`vertexai: true`, `httpOptions.baseUrl`). No Google credential on the client: the app sends
the Vertex REST request plus the RaiCentral runtime credential, and the gateway applies
policy, attaches its own service-account token, meters usage, and issues a receipt.

## Run

```bash
cd examples/gemini_gateway_ts
npm install
export RAI_BASE_URL=http://localhost:8001
export RAI_RUNTIME_CREDENTIAL=<runtime credential>
npm start
```

PowerShell: `$env:RAI_RUNTIME_CREDENTIAL = "..."; npm start`

## Gateway configuration (server-side `.env`)

```
GCP_PROJECT_ID=<project with Vertex AI enabled and billing linked>
GCP_LOCATION=global                 # default when the client sends none
# One of:
GCP_SERVICE_ACCOUNT_JSON={...single-line service account key JSON...}
GOOGLE_APPLICATION_CREDENTIALS=/path/inside/container/key.json
# (on GCE/GKE with no key set, the gateway uses the metadata server)
```

The service account needs `roles/aiplatform.user`. The model must be registered with
provider `vertex` (the seed catalog includes `gemini-3.1-flash-lite`) and allowed for
the runtime identity's agent version.

The client chooses the location (Gemini 3.x is global-only). The gateway always pins the project, so a client cannot redirect
spend to another project. Set GCP_PIN_LOCATION=true to pin location too.

## One base URL, any SDK shape

`setDefaultBaseUrls({ vertexUrl, geminiUrl })` with `<gateway>/providers/genai` is all the
app configures. The gateway detects the target API from the request path
(`/projects/.../locations/...` = Vertex AI, `/models/...` = Gemini Developer API), and it
reads project, location and model from the request, so none of it is repeated.

The runtime credential is read from `x-rai-agent-key`, then `Authorization: Bearer`, then
`x-goog-api-key` (so `new GoogleGenAI({ apiKey: "<runtime credential>" })` works for the
Gemini API). If your SDK also holds a Google service account (`googleAuthOptions.keyFile`
or `.credentials`), the SDK sends a Google token in `Authorization`. The gateway discards
it and uses its own service account, but also send `x-rai-agent-key` via
`httpOptions.headers` so it is not mistaken for the runtime credential.

## Tracing SDK calls

Each call logs two `sdk-trace` lines in the gateway output (`docker compose logs -f rai-gateway`):

```
sdk-trace req=ab12 POST api=vertex project=lapeep-account-2 location=global model=gemini-3.1-flash-lite op=generateContent stream=False sdk='google-genai-sdk/3.3.0 gl-node/22' cred=rai_rt_17a… path=/providers/genai/v1beta1/... routed=/providers/vertex-ai/v1beta1/...
sdk-trace req=ab12 done status=200 duration_ms=840 receipt=rcpt_...
```
