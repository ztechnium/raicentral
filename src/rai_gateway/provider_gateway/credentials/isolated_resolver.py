"""Resolve upstream credentials from isolated references + ownership grants."""

from __future__ import annotations

from sqlalchemy.orm import Session

from rai_gateway.provider_gateway.auth import (
    CredentialResolveContext,
    ProviderCredentialResolver,
    ProviderCredentialUnavailable,
    SettingsCredentialResolver,
)
from rai_gateway.provider_gateway.credentials.materialize import (
    materialize_upstream_credential,
)
from rai_gateway.provider_gateway.credentials.ownership import (
    ProviderCredentialScopeDenied,
    select_grant,
)
from rai_gateway.provider_gateway.credentials.secret_resolver import (
    ProviderSecretResolver,
    SecretResolutionError,
    default_secret_resolver,
)
from rai_gateway.provider_gateway.domain import ProviderCredential


class IsolatedProviderCredentialResolver(ProviderCredentialResolver):
    def __init__(
        self,
        db: Session,
        *,
        secrets: ProviderSecretResolver | None = None,
        fallback: ProviderCredentialResolver | None = None,
    ) -> None:
        self._db = db
        self._secrets = secrets or default_secret_resolver()
        self._fallback = fallback or SettingsCredentialResolver()

    def resolve(
        self,
        provider: str,
        *,
        context: CredentialResolveContext | None = None,
    ) -> ProviderCredential:
        if context is None or context.runtime_identity_id is None:
            return self._fallback.resolve(provider, context=context)

        try:
            _grant, ref = select_grant(
                self._db,
                runtime_identity_id=context.runtime_identity_id,
                provider=provider,
                environment=context.environment,
                model=context.model,
            )
        except ProviderCredentialScopeDenied as exc:
            raise ProviderCredentialScopeDenied(exc.reason) from exc

        secret_ref = ref.secret_reference
        if ref.pending_secret_reference and ref.pending_version:
            # During rotation, active traffic keeps the active secret until cutover.
            secret_ref = ref.secret_reference

        try:
            resolved = self._secrets.resolve(ref.secret_backend, secret_ref)
        except SecretResolutionError as exc:
            raise ProviderCredentialUnavailable(provider) from exc

        try:
            return materialize_upstream_credential(ref, resolved)
        except ValueError as exc:
            raise ProviderCredentialUnavailable(provider) from exc
