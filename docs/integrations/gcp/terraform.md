# GCP connector Terraform

Customer-facing module: [`deploy/gcp-connector/`](../../../deploy/gcp-connector/).

## Quick start

```bash
cd deploy/gcp-connector
terraform init
terraform apply -var="project_id=YOUR_PROJECT_ID"
```

Outputs include `discover_service_account_email` and optional WIF pool resources when `wif_issuer_uri` is set.

## Variables

| Variable | Purpose |
| --- | --- |
| `project_id` | Target GCP project |
| `enable_enforcement_sa` | Create `raicentral-enforce` (default true) |
| `wif_issuer_uri` | RAICentral OIDC issuer for Workload Identity Federation |
| `wif_allowed_audiences` | Optional audience allowlist |

## RAICentral connection

After apply:

1. Create connection with `adc_impersonation` and `service_account_email` = discover SA, **or**
2. `wif_external_account` with external account JSON from your IdP + impersonation target.

Never upload service account JSON keys. See [permissions.md](./permissions.md).

## APIs

Enable before verify:

- `cloudresourcemanager.googleapis.com`
- `cloudasset.googleapis.com`
- `modelarmor.googleapis.com`
