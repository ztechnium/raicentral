"""Provider upstream credential references (no raw secrets in DB)."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from rai_gateway.models.database import Base


class ProviderCredentialRef(Base):
    __tablename__ = "provider_credential_refs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    credential_key = Column(String, nullable=False, unique=True, index=True)
    provider = Column(String, nullable=False, index=True)
    credential_type = Column(String, nullable=False, default="API_KEY")
    secret_backend = Column(String, nullable=False, default="ENVIRONMENT")
    secret_reference = Column(String, nullable=False)
    scope = Column(JSON, nullable=False, default=dict)
    environment = Column(String)
    status = Column(String, nullable=False, default="ACTIVE", index=True)
    active_version = Column(Integer, nullable=False, default=1)
    pending_secret_reference = Column(String)
    pending_version = Column(Integer)
    rotation_metadata = Column(JSON, nullable=False, default=dict)
    last_validated_at = Column(DateTime(timezone=True))
    base_url_override = Column(String)
    transport_config = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by = Column(String)

    grants = relationship(
        "ProviderCredentialGrant",
        back_populates="credential_ref",
        foreign_keys="ProviderCredentialGrant.provider_credential_id",
    )
    rotation_events = relationship(
        "ProviderCredentialRotationEvent",
        back_populates="credential_ref",
        foreign_keys="ProviderCredentialRotationEvent.provider_credential_id",
    )


class ProviderCredentialGrant(Base):
    """Maps a runtime identity to an authorized upstream credential."""

    __tablename__ = "provider_credential_grants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runtime_identity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_runtime_identities.id"),
        nullable=False,
        index=True,
    )
    provider_credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("provider_credential_refs.id"),
        nullable=False,
        index=True,
    )
    allowed_providers = Column(JSON, nullable=False, default=list)
    allowed_models = Column(JSON)
    environment = Column(String)
    status = Column(String, nullable=False, default="ACTIVE", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String, nullable=False)

    credential_ref = relationship(
        "ProviderCredentialRef",
        back_populates="grants",
        foreign_keys=[provider_credential_id],
    )
    runtime_identity = relationship(
        "AgentRuntimeIdentity", foreign_keys=[runtime_identity_id]
    )


class ProviderCredentialRotationEvent(Base):
    __tablename__ = "provider_credential_rotation_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("provider_credential_refs.id"),
        nullable=False,
        index=True,
    )
    event_type = Column(String, nullable=False)
    from_version = Column(Integer)
    to_version = Column(Integer)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String, nullable=False)

    credential_ref = relationship(
        "ProviderCredentialRef",
        back_populates="rotation_events",
        foreign_keys=[provider_credential_id],
    )


class WorkloadIdentityBinding(Base):
    """Maps cloud workload principals onto RaiCentral runtime identities."""

    __tablename__ = "workload_identity_bindings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cloud_provider = Column(String, nullable=False, index=True)
    principal_identifier = Column(String, nullable=False, index=True)
    runtime_identity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_runtime_identities.id"),
        nullable=False,
        index=True,
    )
    status = Column(String, nullable=False, default="ACTIVE", index=True)
    binding_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String, nullable=False)

    runtime_identity = relationship(
        "AgentRuntimeIdentity", foreign_keys=[runtime_identity_id]
    )
