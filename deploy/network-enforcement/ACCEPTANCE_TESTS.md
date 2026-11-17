# Acceptance tests — reference deployments

Prove each environment satisfies Step 6 exit criteria.

## Common checks

1. Workload can call RaiCentral (`GET /health` or `POST /v1/providers/openai/v1/chat/completions` with runtime credential).
2. RaiCentral can call the configured provider (controlled test model / deployment).
3. Workload **direct** call to a catalog hostname (e.g. `api.openai.com`) **fails** when mode is `ENFORCE`.
4. Policy **DENY** on gateway prevents provider forward even when network allows.
5. Runtime identity in receipts matches the workload credential (not a shared API key).
6. Provider secret is **absent** from workload env / mounts (`kubectl exec` / VM inspect).
7. Evidence receipts reference gateway execution, not a bypass path.

## Kubernetes

```bash
# From a labeled workload pod (namespace rai-workloads):
curl -sf "http://rai-gateway.rai-gateway.svc.cluster.local:8000/health"
curl -sf "https://api.openai.com/v1/models" && echo "FAIL: direct egress allowed" || echo "OK: blocked"
```

## GCP

Use a VM or GKE pod in the application subnet. Confirm firewall rule priority: deny `api.openai.com` / catalog ranges to workload SA; allow tcp/443 to gateway ILB only.

## Azure

From spoke VNet VM: direct `*.openai.azure.com` fails; gateway private endpoint succeeds.

## Linux / VM

With `rai-egress-enforce.nft` loaded in `ENFORCE`, `curl https://api.openai.com` from application UID fails; `curl http://127.0.0.1:8000/health` to local gateway succeeds.

## Automated catalog parity

```bash
uv run pytest tests/network_enforcement -q
```