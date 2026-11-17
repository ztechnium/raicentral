"""Persistence for observed egress and runtime attestation (Step 7)."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from rai_gateway.models.database import Base


class ObservedConnectionEvent(Base):
    __tablename__ = "observed_connection_events"
    __table_args__ = (UniqueConstraint("dedup_key", name="uq_observed_connection_dedup"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    observed_at = Column(DateTime(timezone=True), nullable=False, index=True)
    source_workload = Column(String, nullable=True, index=True)
    source_process = Column(String, nullable=True)
    destination_hostname = Column(String, nullable=False, index=True)
    destination_ip = Column(String, nullable=True)
    destination_port = Column(Integer, nullable=True)
    protocol = Column(String, nullable=True)
    cloud_resource_identity = Column(String, nullable=True)
    runtime_identity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    gateway_used = Column(Boolean, nullable=False, default=False)
    provider_guess = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, default="OBSERVED")
    collector_source = Column(String, nullable=True)
    classifier_version = Column(String, nullable=False)
    dedup_key = Column(String, nullable=False)
    bypass_detected = Column(Boolean, nullable=False, default=False)
    incident_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_incidents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    correlation = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RuntimeAttestationState(Base):
    __tablename__ = "runtime_attestation_states"
    __table_args__ = (
        UniqueConstraint("runtime_identity_id", name="uq_runtime_attestation_identity"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runtime_identity_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    agent_version_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    ai_system_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    status = Column(String, nullable=False, default="UNKNOWN", index=True)
    declared_providers = Column(JSON, nullable=True)
    observed_providers = Column(JSON, nullable=True)
    authorized_providers = Column(JSON, nullable=True)
    last_bypass_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )