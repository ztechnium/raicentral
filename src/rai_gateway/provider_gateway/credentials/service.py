"""Lifecycle for provider credential references, grants, and rotation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from rai_gateway.models.provider_credentials import (
    ProviderCredentialGrant,
    ProviderCredentialRef,
    ProviderCredentialRotationEvent,
    WorkloadIdentityBinding,
)
from rai_gateway.provider_gateway.credentials.domain import (
    GrantStatus,
    ProviderCredentialStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderCredentialService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_credential_ref(
        self,
        *,
        credential_key: str,
        provider: str,
        credential_type: str,
        secret_backend: str,
        secret_reference: str,
        created_by: str,
        environment: str | None = None,
        scope: dict[str, Any] | None = None,
        base_url_override: str | None = None,
        transport_config: dict[str, Any] | None = None,
    ) -> ProviderCredentialRef:
        ref = ProviderCredentialRef(
            credential_key=credential_key,
            provider=provider,
            credential_type=credential_type,
            secret_backend=secret_backend,
            secret_reference=secret_reference,
            environment=environment,
            scope=scope or {},
            base_url_override=base_url_override,
            transport_config=transport_config or {},
            created_by=created_by,
        )
        self._db.add(ref)
        self._db.commit()
        self._db.refresh(ref)
        return ref

    def grant_to_runtime(
        self,
        *,
        runtime_identity_id: UUID,
        provider_credential_id: UUID,
        allowed_providers: list[str],
        created_by: str,
        allowed_models: list[str] | None = None,
        environment: str | None = None,
    ) -> ProviderCredentialGrant:
        grant = ProviderCredentialGrant(
            runtime_identity_id=runtime_identity_id,
            provider_credential_id=provider_credential_id,
            allowed_providers=allowed_providers,
            allowed_models=allowed_models,
            environment=environment,
            status=GrantStatus.ACTIVE.value,
            created_by=created_by,
        )
        self._db.add(grant)
        self._db.commit()
        self._db.refresh(grant)
        return grant

    def begin_rotation(
        self,
        credential_id: UUID,
        *,
        pending_secret_reference: str,
        actor: str,
    ) -> ProviderCredentialRef:
        ref = self._db.get(ProviderCredentialRef, credential_id)
        if not ref:
            raise ValueError("credential ref not found")
        next_version = (ref.active_version or 1) + 1
        ref.status = ProviderCredentialStatus.ROTATING.value
        ref.pending_secret_reference = pending_secret_reference
        ref.pending_version = next_version
        ref.rotation_metadata = {
            **(ref.rotation_metadata or {}),
            "pending_secret_reference": pending_secret_reference,
        }
        self._db.add(
            ProviderCredentialRotationEvent(
                provider_credential_id=ref.id,
                event_type="ROTATION_STARTED",
                from_version=ref.active_version,
                to_version=next_version,
                detail={"secret_reference": pending_secret_reference},
                created_by=actor,
            )
        )
        self._db.commit()
        self._db.refresh(ref)
        return ref

    def validate_pending(
        self, credential_id: UUID, *, actor: str
    ) -> ProviderCredentialRef:
        ref = self._db.get(ProviderCredentialRef, credential_id)
        if not ref or not ref.pending_secret_reference:
            raise ValueError("no pending credential to validate")
        ref.last_validated_at = _utcnow()
        self._db.add(
            ProviderCredentialRotationEvent(
                provider_credential_id=ref.id,
                event_type="PENDING_VALIDATED",
                from_version=ref.active_version,
                to_version=ref.pending_version,
                detail={},
                created_by=actor,
            )
        )
        self._db.commit()
        self._db.refresh(ref)
        return ref

    def cutover(self, credential_id: UUID, *, actor: str) -> ProviderCredentialRef:
        ref = self._db.get(ProviderCredentialRef, credential_id)
        if not ref or not ref.pending_secret_reference or not ref.pending_version:
            raise ValueError("no pending credential to cut over")
        previous = ref.secret_reference
        ref.secret_reference = ref.pending_secret_reference
        ref.active_version = ref.pending_version
        ref.pending_secret_reference = None
        ref.pending_version = None
        ref.status = ProviderCredentialStatus.ACTIVE.value
        ref.rotation_metadata = {
            **(ref.rotation_metadata or {}),
            "previous_secret_reference": previous,
            "cutover_at": _utcnow().isoformat(),
        }
        self._db.add(
            ProviderCredentialRotationEvent(
                provider_credential_id=ref.id,
                event_type="CUT_OVER",
                from_version=ref.active_version - 1,
                to_version=ref.active_version,
                detail={"revoked_reference": previous},
                created_by=actor,
            )
        )
        self._db.commit()
        self._db.refresh(ref)
        return ref

    def bind_workload_principal(
        self,
        *,
        cloud_provider: str,
        principal_identifier: str,
        runtime_identity_id: UUID,
        created_by: str,
        binding_metadata: dict[str, Any] | None = None,
    ) -> WorkloadIdentityBinding:
        row = WorkloadIdentityBinding(
            cloud_provider=cloud_provider,
            principal_identifier=principal_identifier,
            runtime_identity_id=runtime_identity_id,
            status="ACTIVE",
            binding_metadata=binding_metadata or {},
            created_by=created_by,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row
