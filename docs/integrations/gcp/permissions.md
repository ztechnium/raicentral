# GCP IAM permissions (v1)

## Discovery service account (`raicentral-discover`)

| Role / permission | Purpose |
| --- | --- |
| `roles/cloudasset.viewer` | Cloud Asset Inventory for Vertex-related types |
| `roles/modelarmor.viewer` | Read Model Armor templates and floor settings |
| `roles/resourcemanager.projectViewer` or `resourcemanager.projects.get` | Project metadata |

## Enforcement service account (`raicentral-enforce`) — optional

| Role / permission | Purpose |
| --- | --- |
| All discover roles | Read-back after apply |
| `roles/modelarmor.admin` or `modelarmor.floorSettings.update` | Apply floor setting changes |

RAICentral rejects uploaded service account JSON private keys; use Workload Identity Federation or ADC impersonation.
