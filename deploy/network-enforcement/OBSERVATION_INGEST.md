# Network observation ingest (Step 7)

See `docs/network-rai/07-ingest-contract.md` for the canonical contract.

```text
POST /v1/network-observation/events
Header: X-RAI-Network-Observation-Key: <NETWORK_OBSERVATION_INGEST_TOKEN>

GET /v1/enforcement-assurance/metrics
GET /v1/enforcement-assurance/runtime-attestation/{runtime_identity_id}
```

Set `NETWORK_OBSERVATION_INGEST_TOKEN` in the gateway environment before enabling collectors.