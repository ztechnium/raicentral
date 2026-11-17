# Azure reference — network enforcement

```text
Spoke workload VNet → UDR → Hub → RaiCentral gateway → Azure OpenAI private endpoint
```

## Enforcement goal

```text
workload → Azure OpenAI (public) = DENY
workload → RaiCentral            = ALLOW
RaiCentral → Private Endpoint    = ALLOW
```

## Apply

```bash
cd deploy/network-enforcement/azure/terraform
terraform init
terraform plan -var resource_group_name=rai-enforcement -var location=eastus
```

Disable public network access on Azure OpenAI where supported; store keys in **Key Vault** referenced by the gateway managed identity only.