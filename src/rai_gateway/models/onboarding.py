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
    Text,
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
    session_kind = Column(String(24), nullable=False, default="INITIAL")
    baseline_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    confirmed_baseline_id = Column(UUID(as_uuid=True), nullable=True)
    update_status = Column(String(32), nullable=True)
    row_version = Column(Integer, nullable=False, default=1)
    input_revision = Column(Integer, nullable=False, default=0)
    clarification_revision = Column(Integer, nullable=False, default=0)
    governance_clarification_revision = Column(Integer, nullable=False, default=0)
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
        CheckConstraint(
            "session_kind IN ('INITIAL','UPDATE','INVENTORY_CORRECTION','POLICY_REVISION')",
            name="ck_onb_session_kind",
        ),
        CheckConstraint("row_version >= 1", name="ck_onb_session_row_version"),
        CheckConstraint("input_revision >= 0", name="ck_onb_session_input_revision"),
        CheckConstraint(
            "clarification_revision >= 0", name="ck_onb_session_clarification_revision"
        ),
        CheckConstraint(
            "governance_clarification_revision >= 0",
            name="ck_onb_session_governance_clarification_revision",
        ),
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
    # Versioned parsing inputs (Step 03): validated overrides only, never parser code.
    parsing_options = Column(JSON, nullable=False, default=dict)
    parsing_options_version = Column(Integer, nullable=False, default=1)
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
    # Step 03: extraction runs name their exact preparation parent and frozen plan.
    preparation_run_id = Column(
        UUID(as_uuid=True), ForeignKey("onboarding_runs.id"), nullable=True
    )
    extraction_run_id = Column(
        UUID(as_uuid=True), ForeignKey("onboarding_runs.id"), nullable=True
    )
    discovery_plan_id = Column(UUID(as_uuid=True), nullable=True)
    inventory_proposal_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_proposal_sets.id"),
        nullable=True,
    )
    governance_plan_id = Column(UUID(as_uuid=True), nullable=True)
    change_set_id = Column(UUID(as_uuid=True), nullable=True)
    activation_plan_id = Column(UUID(as_uuid=True), nullable=True)
    verification_plan_id = Column(UUID(as_uuid=True), nullable=True)
    delta_plan_id = Column(UUID(as_uuid=True), nullable=True)
    plan = Column(JSON, nullable=True)
    plan_digest = Column(String(64), nullable=True)
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
        CheckConstraint(
            "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
            "'GENERATE_GOVERNANCE','APPLY_CHANGESET','ACTIVATE_CONTROLS','VERIFY_RUNTIME',"
            "'ANALYZE_DELTA')",
            name="ck_onb_run_stage",
        ),
        ForeignKeyConstraint(
            ["discovery_plan_id"],
            ["onboarding_discovery_plans.id"],
            name="fk_onb_run_discovery_plan",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["governance_plan_id"],
            ["onboarding_governance_plans.id"],
            name="fk_onb_run_governance_plan",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["change_set_id"],
            ["onboarding_change_sets.id"],
            name="fk_onb_run_change_set",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["activation_plan_id"],
            ["onboarding_activation_plans.id"],
            name="fk_onb_run_activation_plan",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["verification_plan_id"],
            ["onboarding_verification_plans.id"],
            name="fk_onb_run_verification_plan",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["delta_plan_id"],
            ["onboarding_delta_plans.id"],
            name="fk_onb_run_delta_plan",
            use_alter=True,
        ),
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


class OnboardingSourceExtraction(Base):
    """Immutable, reusable result of parsing one source revision under one identity."""

    __tablename__ = "onboarding_source_extractions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_source_revisions.id"),
        nullable=False,
        index=True,
    )
    original_sha256 = Column(String(64), nullable=False)
    extractor_build = Column(String(64), nullable=False)
    dependency_versions = Column(JSON, nullable=False)
    schema_version = Column(Integer, nullable=False)
    normalization_version = Column(Integer, nullable=False)
    options_snapshot = Column(JSON, nullable=False)
    options_digest = Column(String(64), nullable=False)
    policy_digest = Column(String(64), nullable=False)
    identity_digest = Column(String(64), nullable=False)
    outcome = Column(String(16), nullable=False)
    reason = Column(String(64), nullable=True)
    coverage = Column(JSON, nullable=False)
    warnings = Column(JSON, nullable=False, default=list)
    normalization = Column(String(128), nullable=False, default="")
    block_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("identity_digest", name="uq_onb_extraction_identity"),
        CheckConstraint(
            "outcome IN ('COMPLETE','PARTIAL','NEEDS_OCR','NEEDS_INPUT','UNSUPPORTED','FAILED')",
            name="ck_onb_extraction_outcome",
        ),
        CheckConstraint("block_count >= 0", name="ck_onb_extraction_blocks"),
    )


class OnboardingExtractedBlock(Base):
    __tablename__ = "onboarding_extracted_blocks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    extraction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal = Column(Integer, nullable=False)
    kind = Column(String(24), nullable=False)
    text = Column(Text, nullable=False)
    cell_data = Column(JSON, nullable=True)
    locator = Column(JSON, nullable=False)
    content_digest = Column(String(64), nullable=False)
    warnings = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("extraction_id", "ordinal", name="uq_onb_block_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_block_ordinal"),
    )


class OnboardingRunSource(Base):
    """Explicit per-source accounting for an extraction run (no silent coverage)."""

    __tablename__ = "onboarding_run_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id = Column(UUID(as_uuid=True), nullable=False)
    source_revision_id = Column(
        UUID(as_uuid=True), ForeignKey("onboarding_source_revisions.id"), nullable=False
    )
    extraction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_source_extractions.id"),
        nullable=True,
    )
    state = Column(String(8), nullable=False, default="PENDING")
    outcome = Column(String(16), nullable=True)
    reason = Column(String(64), nullable=True)
    coverage = Column(JSON, nullable=True)
    block_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "source_revision_id", name="uq_onb_run_source_rev"),
        CheckConstraint("state IN ('PENDING','FINAL')", name="ck_onb_run_source_state"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('COMPLETE','PARTIAL','NEEDS_OCR','NEEDS_INPUT',"
            "'UNSUPPORTED','FAILED','NOT_PROCESSED')",
            name="ck_onb_run_source_outcome",
        ),
        CheckConstraint(
            "(state = 'PENDING' AND outcome IS NULL) OR (state = 'FINAL' AND outcome IS NOT NULL)",
            name="ck_onb_run_source_final",
        ),
    )


class OnboardingAIConfiguration(Base):
    """Singleton explicit AI setup. Holds no secrets: only approved-target choices."""

    __tablename__ = "onboarding_ai_configuration"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton_key = Column(String, nullable=False, unique=True, default="default")
    config_version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    target_key = Column(String(64), nullable=True)
    model = Column(String(128), nullable=True)
    structured_mode = Column(String(16), nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    updated_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("config_version >= 1", name="ck_onb_ai_config_version"),
        CheckConstraint(
            "structured_mode IS NULL OR structured_mode IN ('JSON_SCHEMA','JSON_OBJECT')",
            name="ck_onb_ai_config_mode",
        ),
    )


class OnboardingProviderProbe(Base):
    __tablename__ = "onboarding_provider_probes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    probe_number = Column(Integer, nullable=False)
    configuration_version = Column(Integer, nullable=False)
    target_fingerprint = Column(String(64), nullable=False)
    snapshot = Column(JSON, nullable=False)  # nonsecret effective configuration
    credential_alias = Column(String(64), nullable=False)
    credential_version = Column(Integer, nullable=False)
    nonce = Column(String(64), nullable=False)
    job_id = Column(
        UUID(as_uuid=True), ForeignKey("background_jobs.id"), nullable=False
    )
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(20), nullable=True)
    error_code = Column(String(64), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    usage = Column(JSON, nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("probe_number", name="uq_onb_probe_number"),
        UniqueConstraint("job_id", name="uq_onb_probe_job"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('PASSED','FAILED','OUTCOME_UNKNOWN','CANCELLED')",
            name="ck_onb_probe_outcome",
        ),
        CheckConstraint(
            "(ended_at IS NULL) = (outcome IS NULL)", name="ck_onb_probe_closed"
        ),
        Index(
            "uq_onb_one_active_probe",
            "configuration_version",
            "target_fingerprint",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )


class OnboardingDiscoveryPlan(Base):
    """Immutable inventory analysis plan (Step 04)."""

    __tablename__ = "onboarding_discovery_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version = Column(Integer, nullable=False, default=1)
    body = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OnboardingDiscoveryCall(Base):
    __tablename__ = "onboarding_discovery_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    request_digest = Column(String(64), nullable=False)
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(20), nullable=True)
    result = Column(JSON, nullable=True)
    usage = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_onb_discovery_call_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_discovery_call_ordinal"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('NOT_DISPATCHED','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')",
            name="ck_onb_discovery_call_outcome",
        ),
    )


class OnboardingProposalSet(Base):
    __tablename__ = "onboarding_proposal_sets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    discovery_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    content_digest = Column(String(64), nullable=False)
    provenance = Column(JSON, nullable=False)
    edit_version = Column(Integer, nullable=False, default=1)
    current_reconciliation_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("edit_version >= 1", name="ck_onb_proposal_set_edit_version"),
        ForeignKeyConstraint(
            ["current_reconciliation_id"],
            ["onboarding_reconciliation_snapshots.id"],
            name="fk_onb_proposal_set_reconciliation",
            use_alter=True,
        ),
    )


class OnboardingInventoryProposal(Base):
    __tablename__ = "onboarding_inventory_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type = Column(String(32), nullable=False)
    generated = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('ai_system','agent','agent_version','model','tool',"
            "'mcp_server','external_service','data_source','dependency')",
            name="ck_onb_proposal_entity_type",
        ),
    )


class OnboardingReconciliationSnapshot(Base):
    __tablename__ = "onboarding_reconciliation_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edit_version = Column(Integer, nullable=False)
    registry_projection = Column(JSON, nullable=False)
    registry_digest = Column(String(64), nullable=False)
    matching_rule_version = Column(String(16), nullable=False)
    matches = Column(JSON, nullable=False)
    questions = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "set_id", "edit_version", name="uq_onb_reconciliation_set_version"
        ),
        CheckConstraint("edit_version >= 1", name="ck_onb_reconciliation_edit_version"),
    )


class OnboardingProposalEdit(Base):
    __tablename__ = "onboarding_proposal_edits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_inventory_proposals.id", ondelete="CASCADE"),
        nullable=True,
    )
    path = Column(String(255), nullable=False)
    replacement = Column(JSON, nullable=True)
    resolution = Column(String(32), nullable=True)
    edit_version = Column(Integer, nullable=False)
    evidence = Column(JSON, nullable=False)
    actor = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("edit_version >= 1", name="ck_onb_proposal_edit_version"),
    )


class OnboardingClarificationAnswer(Base):
    __tablename__ = "onboarding_clarification_answers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    answer_revision = Column(Integer, nullable=False)
    question_id = Column(String(64), nullable=False)
    question_snapshot = Column(JSON, nullable=False)
    answer_text = Column(Text, nullable=False)
    unknown = Column(Boolean, nullable=False, default=False)
    set_id = Column(UUID(as_uuid=True), nullable=True)
    proposal_id = Column(UUID(as_uuid=True), nullable=True)
    supersedes_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_clarification_answers.id"),
        nullable=True,
    )
    digest = Column(String(64), nullable=False)
    author = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("answer_revision >= 1", name="ck_onb_answer_revision"),
    )


class OnboardingGovernancePlan(Base):
    """Immutable governance generation plan (Step 05)."""

    __tablename__ = "onboarding_governance_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_version = Column(Integer, nullable=False, default=1)
    body = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OnboardingGovernanceCall(Base):
    __tablename__ = "onboarding_governance_calls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    request_digest = Column(String(64), nullable=False)
    dispatch_started_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(20), nullable=True)
    result = Column(JSON, nullable=True)
    usage = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("run_id", "ordinal", name="uq_onb_governance_call_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_governance_call_ordinal"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('NOT_DISPATCHED','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')",
            name="ck_onb_governance_call_outcome",
        ),
    )


class OnboardingGovernanceProposalSet(Base):
    __tablename__ = "onboarding_governance_proposal_sets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    governance_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    inventory_proposal_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_proposal_sets.id"),
        nullable=False,
    )
    content_digest = Column(String(64), nullable=False)
    clause_digest = Column(String(64), nullable=False)
    provenance = Column(JSON, nullable=False)
    edit_version = Column(Integer, nullable=False, default=1)
    current_reconciliation_id = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "edit_version >= 1", name="ck_onb_governance_set_edit_version"
        ),
        ForeignKeyConstraint(
            ["current_reconciliation_id"],
            ["onboarding_governance_reconciliation_snapshots.id"],
            name="fk_onb_governance_set_reconciliation",
            use_alter=True,
        ),
    )


class OnboardingGovernanceClause(Base):
    __tablename__ = "onboarding_governance_clauses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    requirement = Column(JSON, nullable=False)
    outcome = Column(String(32), nullable=False)
    evidence = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("set_id", "ordinal", name="uq_onb_governance_clause_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_governance_clause_ordinal"),
    )


class OnboardingGovernanceProposal(Base):
    __tablename__ = "onboarding_governance_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposal_family = Column(String(32), nullable=False)
    generated = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class OnboardingGovernanceReconciliationSnapshot(Base):
    __tablename__ = "onboarding_governance_reconciliation_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edit_version = Column(Integer, nullable=False)
    baseline_projection = Column(JSON, nullable=False)
    baseline_digest = Column(String(64), nullable=False)
    matching_rule_version = Column(String(16), nullable=False)
    matches = Column(JSON, nullable=False)
    capabilities = Column(JSON, nullable=False)
    questions = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "set_id", "edit_version", name="uq_onb_governance_reconciliation_set_version"
        ),
        CheckConstraint(
            "edit_version >= 1", name="ck_onb_governance_reconciliation_edit_version"
        ),
    )


class OnboardingGovernanceProposalEdit(Base):
    __tablename__ = "onboarding_governance_proposal_edits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposal_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_proposals.id", ondelete="CASCADE"),
        nullable=True,
    )
    path = Column(String(255), nullable=False)
    replacement = Column(JSON, nullable=True)
    resolution = Column(String(32), nullable=True)
    edit_version = Column(Integer, nullable=False)
    evidence = Column(JSON, nullable=False)
    actor = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "edit_version >= 1", name="ck_onb_governance_proposal_edit_version"
        ),
    )


class OnboardingGovernanceClarificationAnswer(Base):
    __tablename__ = "onboarding_governance_clarification_answers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    answer_revision = Column(Integer, nullable=False)
    question_id = Column(String(64), nullable=False)
    question_snapshot = Column(JSON, nullable=False)
    answer_text = Column(Text, nullable=False)
    unknown = Column(Boolean, nullable=False, default=False)
    set_id = Column(UUID(as_uuid=True), nullable=True)
    proposal_id = Column(UUID(as_uuid=True), nullable=True)
    supersedes_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_clarification_answers.id"),
        nullable=True,
    )
    digest = Column(String(64), nullable=False)
    author = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "answer_revision >= 1", name="ck_onb_governance_answer_revision"
        ),
    )


class OnboardingChangeSet(Base):
    __tablename__ = "onboarding_change_sets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_version = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="PREPARED")
    inventory_proposal_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_proposal_sets.id"),
        nullable=True,
    )
    governance_proposal_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_governance_proposal_sets.id"),
        nullable=True,
    )
    selection = Column(JSON, nullable=False)
    payload = Column(JSON, nullable=False)
    payload_digest = Column(String(64), nullable=False)
    review_digest = Column(String(64), nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    discarded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED','DISCARDED','APPLYING','APPLIED')",
            name="ck_onb_change_set_status",
        ),
    )


class OnboardingChangeOperation(Base):
    __tablename__ = "onboarding_change_operations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_change_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    verb = Column(String(8), nullable=False)
    target_kind = Column(String(32), nullable=False)
    source_domain = Column(String(16), nullable=True)
    source_proposal_id = Column(UUID(as_uuid=True), nullable=True)
    action_payload = Column(JSON, nullable=False)
    dependency_ordinals = Column(JSON, nullable=False, default=list)
    status = Column(String(16), nullable=False, default="PENDING")

    __table_args__ = (
        UniqueConstraint("change_set_id", "ordinal", name="uq_onb_change_op_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_change_op_ordinal"),
        CheckConstraint(
            "verb IN ('CREATE','REUSE','UPDATE')", name="ck_onb_change_op_verb"
        ),
        CheckConstraint(
            "status IN ('PENDING','APPLIED','SKIPPED','FAILED')",
            name="ck_onb_change_op_status",
        ),
    )


class OnboardingReviewDecision(Base):
    __tablename__ = "onboarding_review_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_change_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_change_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision = Column(String(8), nullable=False)
    actor = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "change_set_id", "operation_id", name="uq_onb_review_decision_op"
        ),
        CheckConstraint(
            "decision IN ('ACCEPT','SKIP')", name="ck_onb_review_decision"
        ),
    )


class OnboardingApplication(Base):
    __tablename__ = "onboarding_applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    change_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_change_sets.id"),
        nullable=False,
        unique=True,
    )
    status = Column(String(16), nullable=False, default="QUEUED")
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_onb_application_status",
        ),
    )


class OnboardingApplicationAttempt(Base):
    __tablename__ = "onboarding_application_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number = Column(Integer, nullable=False)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("background_jobs.id"),
        nullable=False,
    )
    outcome = Column(String(16), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "application_id", "attempt_number", name="uq_onb_app_attempt_number"
        ),
        CheckConstraint("attempt_number >= 1", name="ck_onb_app_attempt_number"),
    )


class OnboardingAppliedBinding(Base):
    __tablename__ = "onboarding_applied_bindings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_change_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_kind = Column(String(32), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=False)
    binding_key = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "application_id", "operation_id", name="uq_onb_applied_binding_op"
        ),
    )


class OnboardingApplicationReceipt(Base):
    __tablename__ = "onboarding_application_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope = Column(String(255), nullable=False)
    key_digest = Column(String(64), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scope", "key_digest", name="uq_onb_app_receipt_scope_key"),
    )


class OnboardingRuntimeContext(Base):
    __tablename__ = "onboarding_runtime_context"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    selected = Column(JSON, nullable=False, default=dict)
    selection_digest = Column(String(64), nullable=False, default="")
    delegation_mode = Column(String(16), nullable=False, default="NONE")
    row_version = Column(Integer, nullable=False, default=1)
    updated_by = Column(String, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "delegation_mode IN ('NONE','EXPLICIT','TENANT')",
            name="ck_onb_runtime_ctx_delegation",
        ),
        CheckConstraint("row_version >= 1", name="ck_onb_runtime_ctx_row_version"),
    )


class OnboardingActivationPlan(Base):
    __tablename__ = "onboarding_activation_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    runtime_context_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runtime_context.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_version = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="PREPARED")
    payload = Column(JSON, nullable=False)
    payload_digest = Column(String(64), nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    discarded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED','ACTIVATING','ACTIVATED','FAILED','DISCARDED')",
            name="ck_onb_activation_plan_status",
        ),
    )


class OnboardingActivationReceipt(Base):
    __tablename__ = "onboarding_activation_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_activation_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope = Column(String(255), nullable=False)
    key_digest = Column(String(64), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("scope", "key_digest", name="uq_onb_activation_receipt_scope_key"),
    )


class OnboardingVerificationPlan(Base):
    __tablename__ = "onboarding_verification_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activation_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_activation_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_version = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="PREPARED")
    payload = Column(JSON, nullable=False)
    payload_digest = Column(String(64), nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    discarded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED','VERIFYING','VERIFIED','FAILED','DISCARDED')",
            name="ck_onb_verification_plan_status",
        ),
    )


class OnboardingVerificationAttempt(Base):
    __tablename__ = "onboarding_verification_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_verification_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number = Column(Integer, nullable=False)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("background_jobs.id"),
        nullable=False,
    )
    outcome = Column(String(16), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "plan_id", "attempt_number", name="uq_onb_verification_attempt_number"
        ),
        CheckConstraint(
            "attempt_number >= 1", name="ck_onb_verification_attempt_number"
        ),
    )


class OnboardingVerificationDispatch(Base):
    __tablename__ = "onboarding_verification_dispatches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_verification_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    scenario_key = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="PENDING")
    evidence = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "ordinal", name="uq_onb_verification_dispatch_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ck_onb_verification_dispatch_ordinal"),
        CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_onb_verification_dispatch_status",
        ),
    )


# --- Step 08: incremental updates, change impact, verification applicability ---


class OnboardingBaseline(Base):
    __tablename__ = "onboarding_baselines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    baseline_kind = Column(String(32), nullable=False)
    application_receipt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_application_receipts.id", ondelete="SET NULL"),
        nullable=True,
    )
    application_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    domain_projection = Column(JSON, nullable=False)
    domain_digest = Column(String(64), nullable=False)
    scope_notes = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="CONFIRMED")
    confirmed_by = Column(String, nullable=False)
    confirmed_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "baseline_kind IN ('APPLIED_RECEIPT','REVIEWED_CURRENT_STATE')",
            name="ck_onb_baseline_kind",
        ),
        CheckConstraint(
            "status IN ('PREVIEW','CONFIRMED')", name="ck_onb_baseline_status"
        ),
    )


class OnboardingSourceLineage(Base):
    __tablename__ = "onboarding_source_lineage"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    previous_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_source_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    new_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_source_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    relationship = Column(String(16), nullable=False)
    effective_notes = Column(Text, nullable=True)
    confirmed_by = Column(String, nullable=False)
    confirmed_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "relationship IN ('SUPERSEDES','SUPPLEMENTS','INDEPENDENT')",
            name="ck_onb_source_lineage_rel",
        ),
        UniqueConstraint(
            "previous_revision_id", "new_revision_id", name="uq_onb_source_lineage_pair"
        ),
    )


class OnboardingDeltaPlan(Base):
    __tablename__ = "onboarding_delta_plans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    baseline_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_baselines.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_version = Column(Integer, nullable=False, default=1)
    body = Column(JSON, nullable=False)
    digest = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="PREPARED")
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_onb_delta_plan_status",
        ),
        CheckConstraint("plan_version >= 1", name="ck_onb_delta_plan_version"),
    )


class OnboardingAssertionMatch(Base):
    __tablename__ = "onboarding_assertion_matches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delta_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_delta_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    old_identity = Column(String(512), nullable=False)
    new_identity = Column(String(512), nullable=True)
    match_class = Column(String(32), nullable=False)
    method_version = Column(String(16), nullable=False)
    match_reason = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("delta_plan_id", "ordinal", name="uq_onb_assertion_match_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_assertion_match_ordinal"),
    )


class OnboardingChangeFinding(Base):
    __tablename__ = "onboarding_change_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delta_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_delta_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    change_type = Column(String(32), nullable=False)
    identity = Column(String(512), nullable=False)
    before_digest = Column(String(64), nullable=True)
    after_digest = Column(String(64), nullable=True)
    summary = Column(Text, nullable=False)
    impact_level = Column(String(16), nullable=False, default="MEDIUM")
    resolution_status = Column(String(24), nullable=False, default="OPEN")
    provenance = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("delta_plan_id", "ordinal", name="uq_onb_change_finding_ordinal"),
        CheckConstraint("ordinal >= 0", name="ck_onb_change_finding_ordinal"),
    )


class OnboardingImpactEdge(Base):
    __tablename__ = "onboarding_impact_edges"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delta_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_delta_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    finding_ordinal = Column(Integer, nullable=False)
    ordinal = Column(Integer, nullable=False)
    edge_type = Column(String(32), nullable=False)
    reference_kind = Column(String(32), nullable=False)
    reference_id = Column(String(128), nullable=False)
    direct = Column(Boolean, nullable=False, default=True)
    confidence = Column(String(24), nullable=False, default="INFERRED_NEEDS_REVIEW")
    reason = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "delta_plan_id", "ordinal", name="uq_onb_impact_edge_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ck_onb_impact_edge_ordinal"),
    )


class OnboardingUpdateDecision(Base):
    __tablename__ = "onboarding_update_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    finding_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_change_findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision = Column(String(24), nullable=False)
    notes = Column(Text, nullable=True)
    actor = Column(String, nullable=False)
    edit_revision = Column(Integer, nullable=False)
    supersedes_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_update_decisions.id"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("edit_revision >= 1", name="ck_onb_update_decision_revision"),
        CheckConstraint(
            "decision IN ('KEEP','UPDATE','EXCLUDE','MATCH','CLARIFY','DEFER','SOURCE_ONLY')",
            name="ck_onb_update_decision",
        ),
    )


class OnboardingUpdateReconciliation(Base):
    __tablename__ = "onboarding_update_reconciliations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delta_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_delta_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edit_version = Column(Integer, nullable=False)
    baseline_digest = Column(String(64), nullable=False)
    current_domain_digest = Column(String(64), nullable=False)
    source_digest = Column(String(64), nullable=False)
    difference = Column(JSON, nullable=False)
    captured_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "delta_plan_id", "edit_version", name="uq_onb_update_reconciliation_version"
        ),
        CheckConstraint("edit_version >= 1", name="ck_onb_update_reconciliation_version"),
    )


class OnboardingVerificationImpact(Base):
    __tablename__ = "onboarding_verification_impacts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delta_plan_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_delta_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    verification_attempt_id = Column(
        UUID(as_uuid=True),
        ForeignKey("onboarding_verification_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    scenario_key = Column(String(64), nullable=False)
    target_digest = Column(String(64), nullable=False)
    applicability = Column(String(24), nullable=False)
    reason = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "delta_plan_id", "ordinal", name="uq_onb_verification_impact_ordinal"
        ),
        CheckConstraint("ordinal >= 0", name="ck_onb_verification_impact_ordinal"),
        CheckConstraint(
            "applicability IN ('CURRENT','STALE','REVIEW_REQUIRED','NOT_APPLICABLE')",
            name="ck_onb_verification_applicability",
        ),
    )
