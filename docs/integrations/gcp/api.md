# Cloud governance API

Base path: `/v1/cloud-governance`

Permissions:

- `cloud_governance:read` — list/get, coverage, assets, evidence, drift
- `cloud_governance:manage` — create connection, verify, sync, disconnect, drift-check enqueue
- `cloud_governance:enforce` — policy preview, bind approval, apply, verify

## Enforcement flow

1. `POST /connections/{id}/policy-changes/preview` with `desired` payload
2. `GET /policy-changes/{changeId}/approval-payload` — use digest when creating an `ApprovalRequest`
3. Approve via existing `/v1/approval-requests` APIs
4. `POST /policy-changes/{changeId}/bind-approval`
5. `POST /policy-changes/{changeId}/apply`
6. `POST /policy-changes/{changeId}/verify`

See [setup.md](./setup.md) for customer onboarding.
