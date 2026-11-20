"""Onboarding intake workspace: sessions, original sources, runs, receipts.

Step 02 stores sources and preparation runs only. ``BackgroundJob`` remains the
authority for execution state; ``OnboardingRun`` supplies the immutable input
snapshot and its session association. There is no organization column: this is a
single-organization installation and sessions are shared by authorized users.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from rai_gateway.models.database import Base


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False)
    lifecycle = Column(String(16), nullable=False, default="OPEN")
    row_version = Column(Integer, nullable=False, default=1)
    input_revision = Column(Integer, nullable=False, default=0)
    created_by = Column(String, nullable=False)
    updated_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "lifecycle IN ('OPEN','ARCHIVED')", name="ck_onb_session_lifecycle"
        ),
        CheckConstraint("row_version >= 1", name="ck_onb_session_row_version"),
        CheckConstraint("input_revision >= 0", name="ck_onb_session_input_revision"),
    )


class OnboardingSource(Base):
    __tablename__ = "onboarding_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(8), nullable=False)
    role = Column(String(24), nullable=False)
    included = Column(Boolean, nullable=False, default=True)
    current_revision_id = Column(UUID(as_uuid=True), nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('FILE','TEXT')", name="ck_onb_source_kind"),
        CheckConstraint(
            "role IN ('USER_PROMPT','SUPPORTING_MATERIAL')", name="ck_onb_source_role"
        ),
        CheckConstraint(
            "role <> 'USER_PROMPT' OR kind = 'TEXT'", name="ck_onb_source_prompt_text"
        ),
        UniqueConstraint("id", "session_id", name="uq_onb_source_id_session"),
        # The current revision must belong to this very source.
        ForeignKeyConstraint(
            ["current_revision_id", "id"],
            [
                "onboarding_source_revisions.id",
                "onboarding_source_revisions.source_id",
            ],
            name="fk_onb_source_current_revision",
            use_alter=True,
        ),
        Index(
            "uq_onb_one_included_prompt",
            "session_id",
            unique=True,
            postgresql_where=text("role = 'USER_PROMPT' AND included"),
        ),
    )


class OnboardingSourceRevision(Base):
    __tablename__ = "onboarding_source_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    display_name = Column(String(255), nullable=False)
    declared_media_type = Column(String(255), nullable=True)
    media_family = Column(String(16), nullable=False)
    storage_key = Column(String(128), nullable=False)
    sha256 = Column(String(64), nullable=False)
    byte_length = Column(BigInteger, nullable=False)
    created_by = Column(String, nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "session_id"],
            ["onboarding_sources.id", "onboarding_sources.session_id"],
            name="fk_onb_rev_source_session",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "source_id", "revision_number", name="uq_onb_rev_source_number"
        ),
        UniqueConstraint("storage_key", name="uq_onb_rev_storage_key"),
        UniqueConstraint("id", "source_id", name="uq_onb_rev_id_source"),
        CheckConstraint("revision_number >= 1", name="ck_onb_rev_number"),
        CheckConstraint("byte_length >= 0", name="ck_onb_rev_bytes"),
    )


class OnboardingRun(Base):
    __tablename__ = "onboarding_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_number = Column(Integer, nullable=False)
    stage = Column(String(24), nullable=False, default="PREPARE_SOURCES")
    input_revision = Column(Integer, nullable=False)
    manifest = Column(JSON, nullable=False)
    manifest_digest = Column(String(64), nullable=False)
    job_id = Column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id"), nullable=False
    )
    retry_of_run_id = Column(
        UUID(as_uuid=True), ForeignKey("onboarding_runs.id"), nullable=True
    )
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "run_number", name="uq_onb_run_session_number"),
        UniqueConstraint("job_id", name="uq_onb_run_job"),
        CheckConstraint("run_number >= 1", name="ck_onb_run_number"),
        CheckConstraint("input_revision >= 0", name="ck_onb_run_input_revision"),
        CheckConstraint("stage IN ('PREPARE_SOURCES')", name="ck_onb_run_stage"),
        # At most one active run per session.
        Index(
            "uq_onb_one_active_run",
            "session_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )


class OnboardingMutationReceipt(Base):
    """Durable idempotency record: identifiers and digests only."""

    __tablename__ = "onboarding_mutation_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope = Column(String(255), nullable=False)
    key_digest = Column(String(64), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    resource_kind = Column(String(24), nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=False)
    actor = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scope", "key_digest", name="uq_onb_receipt_scope_key"),
    )
