"""Runtime-identity extraction and server-side upstream credential resolution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from rai_gateway.core.config import settings
from rai_gateway.provider_gateway.domain import ProviderCredential


def extract_runtime_secret(headers: Mapping[str, str]) -> str | None:
    """RaiCentral runtime credential from the client (never a provider key)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    key = lowered.get("x-rai-agent-key")
    if key:
        return key.strip() or None
    auth = lowered.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


class ProviderCredentialUnavailable(Exception):
    def __init__(self, provider: str) -> None:
        super().__init__(f"No upstream credential configured for {provider}")
        self.provider = provider


class ProviderCredentialResolver(ABC):
    """Resolves the credential the *gateway* uses to call a provider.

    Step 5 replaces the settings-backed implementation with isolated secret
    references; callers depend only on this interface.
    """

    @abstractmethod
    def resolve(self, provider: str) -> ProviderCredential:
        """Return credential material or raise ProviderCredentialUnavailable."""


class SettingsCredentialResolver(ProviderCredentialResolver):
    """Initial implementation backed by existing server settings."""

    def resolve(self, provider: str) -> ProviderCredential:
        name = provider.lower()
        if name == "openai" and settings.llm_provider_api_key:
            return ProviderCredential(
                provider=name,
                base_url=settings.llm_provider_url,
                headers={"Authorization": f"Bearer {settings.llm_provider_api_key}"},
            )
        if (
            name in {"azure", "azure_openai"}
            and settings.azure_openai_api_key
            and settings.azure_openai_endpoint
        ):
            return ProviderCredential(
                provider="azure_openai",
                base_url=settings.azure_openai_endpoint,
                headers={"api-key": settings.azure_openai_api_key},
                query_params={"api-version": settings.azure_openai_api_version},
            )
        if name == "anthropic" and settings.anthropic_api_key:
            return ProviderCredential(
                provider=name,
                base_url="https://api.anthropic.com",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
        raise ProviderCredentialUnavailable(provider)
