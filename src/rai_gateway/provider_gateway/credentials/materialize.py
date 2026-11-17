"""Turn credential refs + resolved secrets into transport :class:`ProviderCredential`."""

from __future__ import annotations

from rai_gateway.core.config import settings
from rai_gateway.models.provider_credentials import ProviderCredentialRef
from rai_gateway.provider_gateway.credentials.domain import (
    ProviderCredentialType,
    normalize_provider_name,
)
from rai_gateway.provider_gateway.credentials.secret_resolver import (
    ResolvedProviderSecret,
)
from rai_gateway.provider_gateway.domain import ProviderCredential


def materialize_upstream_credential(
    ref: ProviderCredentialRef,
    secret: ResolvedProviderSecret,
) -> ProviderCredential:
    provider = normalize_provider_name(ref.provider)
    cred_type = ref.credential_type
    transport = dict(ref.transport_config or {})
    base_url = ref.base_url_override or transport.get("base_url")
    headers: dict[str, str] = {}
    query_params: dict[str, str] = {}

    if cred_type in {
        ProviderCredentialType.API_KEY.value,
        ProviderCredentialType.STATIC_BEARER.value,
    }:
        token = secret.payload.get("value") or secret.payload.get("bearer")
        if not token:
            raise ValueError("Secret payload missing token material")
        if provider == "openai":
            base_url = base_url or settings.llm_provider_url
            headers["Authorization"] = f"Bearer {token}"
        elif provider == "anthropic":
            base_url = base_url or "https://api.anthropic.com"
            headers["x-api-key"] = token
            headers.setdefault(
                "anthropic-version", transport.get("anthropic_version", "2023-06-01")
            )
        elif provider == "azure_openai":
            base_url = base_url or settings.azure_openai_endpoint or ""
            headers["api-key"] = token
            ver = transport.get("api_version") or settings.azure_openai_api_version
            if ver:
                query_params["api-version"] = ver
        elif provider == "google_ai":
            base_url = base_url or settings.google_ai_base_url
            headers["x-goog-api-key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
            base_url = base_url or settings.llm_provider_url or ""
    elif cred_type == ProviderCredentialType.GCP_SERVICE_ACCOUNT.value:
        # Workload / SA paths use ADC at transport layer in later steps.
        base_url = base_url or (
            f"https://{settings.gcp_location}-aiplatform.googleapis.com/v1"
            if provider == "vertex"
            else settings.google_ai_base_url
        )
    elif cred_type == ProviderCredentialType.AWS_IAM_ROLE.value:
        region = transport.get("region") or settings.aws_region
        base_url = base_url or (
            settings.bedrock_runtime_url
            or f"https://bedrock-runtime.{region}.amazonaws.com"
        )
    else:
        token = secret.payload.get("value")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    if not base_url:
        raise ValueError(f"No base_url for provider credential {ref.credential_key}")

    return ProviderCredential(
        provider=provider,
        base_url=str(base_url).rstrip("/"),
        headers=headers,
        query_params=query_params,
        credential_ref_id=ref.id,
    )
