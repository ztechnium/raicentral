# GCP reference — network enforcement

Target path:

```text
GKE workload → VPC firewall / NAT → RaiCentral (ILB) → Cloud NAT → AI provider
```

## Enforcement goal

```text
application subnet → AI provider     = DENY
application subnet → RaiCentral ILB  = ALLOW
RaiCentral subnet    → AI provider   = ALLOW (tcp/443)
```

## Apply

```bash
cd deploy/network-enforcement/gcp/terraform
terraform init
terraform plan -var project_id=YOUR_PROJECT -var region=us-central1
```

Wire provider credentials via **Secret Manager** and GKE Workload Identity on the gateway service account only — not on application pods.

## Secure Web Proxy / domain controls

Where VPC firewall rules cannot express FQDNs, pair these rules with [Secure Web Proxy](https://cloud.google.com/secure-web-proxy/docs) or DNS-based egress policies using hostnames from `../../catalog/provider-destinations.yaml`.