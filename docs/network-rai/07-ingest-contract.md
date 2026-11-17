# Network observation ingest contract

Collectors (eBPF, Cilium/Hubble, VPC flow logs, NSG flow logs, proxy logs) POST batches to:

```text
POST /v1/network-observation/events
Header: X-RAI-Network-Observation-Key: <NETWORK_OBSERVATION_INGEST_TOKEN>
```

Each event is telemetry-neutral; RaiCentral classifies destinations and applies bypass rules.

Required fields per event:

- `observed_at` (RFC3339 timestamp)
- `destination_hostname`
- `gateway_used` (boolean)

Optional correlation fields:

- `source_workload`, `source_process`
- `destination_ip`, `destination_port`, `protocol`
- `cloud_resource_identity`
- `runtime_identity_id` (when workload binding is known)
- `action` — `ALLOWED` | `OBSERVED` | `BLOCKED` | `UNKNOWN`
- `collector_source` — e.g. `gcp_vpc_flow`, `cilium_hubble`
- `correlation` — opaque JSON for operator forensics

Assurance APIs (authenticated users with `reports:read`):

- `GET /v1/enforcement-assurance/metrics`
- `GET /v1/enforcement-assurance/runtime-attestation/{runtime_identity_id}`