"""Domain types for isolated provider credentials."""

from __future__ import annotations

from enum import StrEnum


class ProviderCredentialType(StrEnum):
    API_KEY = "API_KEY"
    AZURE_MANAGED_IDENTITY = "AZURE_MANAGED_IDENTITY"
    GCP_SERVICE_ACCOUNT = "GCP_SERVICE_ACCOUNT"
    GCP_WORKLOAD_IDENTITY = "GCP_WORKLOAD_IDENTITY"
    AWS_IAM_ROLE = "AWS_IAM_ROLE"
    STATIC_BEARER = "STATIC_BEARER"


class SecretBackend(StrEnum):
    ENVIRONMENT = "ENVIRONMENT"
    GCP_SECRET_MANAGER = "GCP_SECRET_MANAGER"
    AZURE_KEY_VAULT = "AZURE_KEY_VAULT"
    AWS_SECRETS_MANAGER = "AWS_SECRETS_MANAGER"
    HASHICORP_VAULT = "HASHICORP_VAULT"


class ProviderCredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ROTATING = "ROTATING"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class GrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class CloudProvider(StrEnum):
    GCP = "GCP"
    AZURE = "AZURE"
    AWS = "AWS"


def normalize_provider_name(provider: str) -> str:
    name = provider.lower().strip()
    if name in {"azure", "azure_openai"}:
        return "azure_openai"
    if name in {"google", "gemini", "google_ai"}:
        return "google_ai"
    return name


def provider_matches(grant_providers: list[str], requested: str) -> bool:
    wanted = normalize_provider_name(requested)
    normalized = {normalize_provider_name(p) for p in grant_providers}
    return wanted in normalized
