# Gemini on Vertex AI through the RAI gateway (TypeScript)

Calls `gemini-3.1-flash-lite` on Vertex AI via the provider-compatible gateway route
`/providers/vertex-ai`. No Google SDK or Google credential on the client: the app sends
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
GCP_LOCATION=us-central1          # or "global"
# One of:
GCP_SERVICE_ACCOUNT_JSON={...single-line service account key JSON...}
GOOGLE_APPLICATION_CREDENTIALS=/path/inside/container/key.json
# (on GCE/GKE with no key set, the gateway uses the metadata server)
```

The service account needs `roles/aiplatform.user`. The model must be registered with
provider `vertex` (the seed catalog includes `gemini-3.1-flash-lite`) and allowed for
the runtime identity's agent version.

The gateway forces the configured project and location, so a client cannot redirect
spend to another project.
