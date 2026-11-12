# Step 5 — Workload Identity and Provider Credential Isolation

## Goal

Remove the application developer from the provider trust path.

The application should authenticate to RaiCentral using workload/runtime identity.

RaiCentral should authenticate to the provider separately.

---

# Target State

```text
Application
  |
  | workload identity / RAI runtime credential
  v
RaiCentral
  |
  | provider secret / managed identity
  v
Provider
```

The application must not need:

```text
OPENAI_API_KEY
ANTHROPIC_API_KEY
AZURE_OPENAI_API_KEY
Google provider credential
AWS Bedrock provider credential
```

---

# Tasks

## 1. Provider credential model

Add a provider credential reference model.

Suggested fields:

```text
provider
credential_type
secret_reference
scope
environment
status
rotation_metadata
last_validated_at
```

Credential types:

```text
API_KEY
AZURE_MANAGED_IDENTITY
GCP_SERVICE_ACCOUNT
GCP_WORKLOAD_IDENTITY
AWS_IAM_ROLE
STATIC_BEARER
```

Never store raw secrets in normal application tables.

---

## 2. Secret resolver abstraction

Create:

```python
ProviderSecretResolver
```

Backends:

```text
ENVIRONMENT   # development only
GCP_SECRET_MANAGER
AZURE_KEY_VAULT
AWS_SECRETS_MANAGER
HASHICORP_VAULT
```

Keep resolver modular.

---

## 3. Runtime identity derivation

Support:

### Existing RaiCentral runtime credential

```text
Authorization: Bearer rai_rt_...
```

### Future cloud workload identity

Map signed infrastructure identity into:

```text
RuntimeIdentity
AI System
Agent
Environment
```

---

## 4. GCP identity path

Prepare support for:

```text
GKE workload identity
GCE service account identity
OIDC identity token
```

Map:

```text
GCP service account principal
        ->
RaiCentral runtime identity
```

Do not trust caller-provided runtime identity headers if infrastructure identity is available.

---

## 5. Azure identity path

Prepare support for:

```text
Managed Identity
AKS Workload Identity
Microsoft Entra workload token
```

Map:

```text
tenant ID
client ID
object ID
        ->
RaiCentral runtime identity
```

---

## 6. AWS identity path

Prepare support for:

```text
IAM role
IRSA
signed identity metadata
```

---

## 7. Credential ownership checks

Add governance controls:

```text
runtime identity
   may use
provider credential
   for
provider/model/environment
```

Do not let every runtime use every stored provider credential.

---

## 8. Rotation safety

Add:

- active credential version
- next credential version
- validation endpoint
- cutover
- revoke old secret
- audit history

---

# Tests

- app cannot inject provider key
- provider secret resolver works
- runtime identity maps correctly
- suspended runtime cannot use credential
- wrong environment denied
- wrong provider scope denied
- secret never appears in receipt/logs
- rotation does not break active traffic unexpectedly

---

# Exit Criteria

Provider credentials are controlled by infrastructure, not application developers.

RaiCentral can prove:

```text
who the workload is
what provider credential it was authorized to use
which provider/model was accessed
```
