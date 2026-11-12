# Google Cloud connector setup

## Customer actions (summary)

1. Create or choose a GCP project for AI workloads.
2. Enable **Cloud Asset API** and **Model Armor API**.
3. Apply Terraform in `deploy/gcp-connector/` (or follow Cloud Shell steps from the connection dashboard).
4. Create service accounts `raicentral-discover` (read) and optionally `raicentral-enforce`.
5. Configure **Workload Identity Federation** for the RAICentral host (no JSON keys).
6. In RAICentral: **Organization → Integrations → Google Cloud**, create a connection, verify, and run discovery.

## Administrator vs operator

| Step | GCP admin | RAICentral org admin |
| --- | --- | --- |
| IAM roles / SAs | Required | |
| WIF pool + provider | Required | |
| Connection wizard | | Required |
| Link assets to AI Systems | | Required |

## Scheduled sync and drift

On the connection dashboard, set **Sync interval** and **Drift interval** (hours). The `rai-worker` service enqueues jobs when intervals elapse for **connected** projects.

## What RAICentral can and cannot do (v1)

- **Can:** inventory Vertex-related resources via Cloud Asset Inventory (when permitted), inspect Model Armor configuration, report coverage honestly.
- **Cannot:** claim full traffic protection from configuration alone; ingest prompts/responses by default; auto-create AI Systems from discovery.
