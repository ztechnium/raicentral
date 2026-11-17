"""SQLAlchemy models for cloud governance connectors."""

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


class CloudConnection(Base):
    """Customer cloud provider connection (instance-scoped, CE)."""

    __tablename__ = "cloud_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String, nullable=False, index=True)
    display_name = Column(String, nullable=False)
    scope_type = Column(String, nullable=False, default="project")
    scope_identifier = Column(String, nullable=False, index=True)
    auth_mode = Column(String, nullable=False)
    encrypted_config = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="draft", index=True)
    capabilities = Column(JSON, nullable=False, default=dict)
    health = Column(JSON, nullable=True)
    enforcement_enabled = Column(Boolean, nullable=False, default=False)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    sync_interval_hours = Column(Integer, nullable=True)
    drift_interval_hours = Column(Integer, nullable=True)
    last_scheduled_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_scheduled_drift_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CloudDiscoveredAsset(Base):
    """Discovered cloud resource candidate or linked registry asset."""

    __tablename__ = "cloud_discovered_assets"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "canonical_resource_name",
            name="uq_cloud_asset_canonical",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_resource_name = Column(String, nullable=False)
    asset_type = Column(String, nullable=False, index=True)
    display_name = Column(String, nullable=True)
    location = Column(String, nullable=True)
    config_summary = Column(JSON, nullable=True)
    coverage = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="candidate", index=True)
    ai_system_id = Column(
        UUID(as_uuid=True), ForeignKey("ai_systems.id"), nullable=True, index=True
    )
    observed_at = Column(DateTime(timezone=True), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())


class CloudSyncRun(Base):
    """One discovery or evidence sync execution for a connection."""

    __tablename__ = "cloud_sync_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    background_job_id = Column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id"), nullable=True, index=True
    )
    run_type = Column(String, nullable=False, default="asset_discovery")
    status = Column(String, nullable=False, default="running", index=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    assets_discovered = Column(Integer, nullable=False, default=0)
    assets_updated = Column(Integer, nullable=False, default=0)
    partial_errors = Column(JSON, nullable=True)
    summary = Column(JSON, nullable=True)


class CloudCoverageSnapshot(Base):
    """Point-in-time coverage matrix for a connection."""

    __tablename__ = "cloud_coverage_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    methodology = Column(String, nullable=False)
    control_matrix = Column(JSON, nullable=False)
    limitations = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CloudPolicyChangeRequest(Base):
    """Preview/apply lifecycle for native control changes (Model Armor)."""

    __tablename__ = "cloud_policy_change_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_resource = Column(String, nullable=False)
    change_kind = Column(String, nullable=False, default="model_armor")
    preview_payload = Column(JSON, nullable=True)
    desired_payload = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default="preview", index=True)
    approval_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id"),
        nullable=True,
        index=True,
    )
    apply_result = Column(JSON, nullable=True)
    verify_result = Column(JSON, nullable=True)
    optimistic_version = Column(String, nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CloudDriftFinding(Base):
    """Observed configuration drift vs expected state."""

    __tablename__ = "cloud_drift_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resource_name = Column(String, nullable=False, index=True)
    finding_type = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="medium")
    expected = Column(JSON, nullable=True)
    observed = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="open", index=True)
    detected_at = Column(DateTime(timezone=True), server_default=func.now())


class CloudEvidenceLink(Base):
    """External evidence pointer with completeness metadata."""

    __tablename__ = "cloud_evidence_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id = Column(
        UUID(as_uuid=True),
        ForeignKey("cloud_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ref_type = Column(String, nullable=False)
    ref_uri = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    complete = Column(Boolean, nullable=False, default=False)
    evidence_metadata = Column(JSON, nullable=True)
    linked_entity_type = Column(String, nullable=True)
    linked_entity_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
