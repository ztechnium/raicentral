"""Runtime identity → provider credential authorization checks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.provider_credentials import (
    ProviderCredentialGrant,
    ProviderCredentialRef,
)
from rai_gateway.provider_gateway.credentials.domain import (
    GrantStatus,
    ProviderCredentialStatus,
    normalize_provider_name,
    provider_matches,
)


class ProviderCredentialScopeDenied(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def select_grant(
    db: Session,
    *,
    runtime_identity_id: UUID,
    provider: str,
    environment: str | None,
    model: str | None,
) -> tuple[ProviderCredentialGrant, ProviderCredentialRef]:
    grants = (
        db.query(ProviderCredentialGrant)
        .join(
            ProviderCredentialRef,
            ProviderCredentialGrant.provider_credential_id == ProviderCredentialRef.id,
        )
        .filter(ProviderCredentialGrant.runtime_identity_id == runtime_identity_id)
        .filter(ProviderCredentialGrant.status == GrantStatus.ACTIVE.value)
        .filter(
            ProviderCredentialRef.status.in_(
                [
                    ProviderCredentialStatus.ACTIVE.value,
                    ProviderCredentialStatus.ROTATING.value,
                ]
            )
        )
        .all()
    )
    if not grants:
        raise ProviderCredentialScopeDenied(
            "No provider credential grant for this runtime identity"
        )

    wanted_provider = normalize_provider_name(provider)
    for grant in grants:
        ref = grant.credential_ref
        if not provider_matches(list(grant.allowed_providers or []), wanted_provider):
            continue
        if not provider_matches([ref.provider], wanted_provider):
            continue
        if grant.environment is not None and grant.environment != environment:
            continue
        if ref.environment is not None and ref.environment != environment:
            continue
        allowed_models = grant.allowed_models
        if allowed_models and model and model not in allowed_models:
            continue
        return grant, ref

    raise ProviderCredentialScopeDenied(
        "Runtime identity is not authorized for this provider credential scope"
    )
