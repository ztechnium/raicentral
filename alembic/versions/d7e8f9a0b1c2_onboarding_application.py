"""onboarding_application

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-11-21 14:00:00.000000

Step 06: change sets, review decisions, governed application runs, bindings,
receipts, run stage APPLY_CHANGESET, and obligation-aware policy_rule_links uniqueness.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "onboarding_change_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PREPARED"),
        sa.Column(
            "inventory_proposal_set_id",
            UUID,
            sa.ForeignKey("onboarding_proposal_sets.id"),
            nullable=True,
        ),
        sa.Column(
            "governance_proposal_set_id",
            UUID,
            sa.ForeignKey("onboarding_governance_proposal_sets.id"),
            nullable=True,
        ),
        sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("review_digest", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PREPARED','DISCARDED','APPLYING','APPLIED')",
            name="ck_onb_change_set_status",
        ),
    )
    op.create_index(
        "ix_onboarding_change_sets_session_id",
        "onboarding_change_sets",
        ["session_id"],
    )

    op.create_table(
        "onboarding_change_operations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "change_set_id",
            UUID,
            sa.ForeignKey("onboarding_change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("verb", sa.String(8), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("source_domain", sa.String(16), nullable=True),
        sa.Column("source_proposal_id", UUID, nullable=True),
        sa.Column("action_payload", sa.JSON(), nullable=False),
        sa.Column("dependency_ordinals", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.UniqueConstraint(
            "change_set_id", "ordinal", name="uq_onb_change_op_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_onb_change_op_ordinal"),
        sa.CheckConstraint(
            "verb IN ('CREATE','REUSE','UPDATE')", name="ck_onb_change_op_verb"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPLIED','SKIPPED','FAILED')",
            name="ck_onb_change_op_status",
        ),
    )
    op.create_index(
        "ix_onboarding_change_operations_change_set_id",
        "onboarding_change_operations",
        ["change_set_id"],
    )

    op.create_table(
        "onboarding_review_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "change_set_id",
            UUID,
            sa.ForeignKey("onboarding_change_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation_id",
            UUID,
            sa.ForeignKey("onboarding_change_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "change_set_id", "operation_id", name="uq_onb_review_decision_op"
        ),
        sa.CheckConstraint(
            "decision IN ('ACCEPT','SKIP')", name="ck_onb_review_decision"
        ),
    )

    op.create_table(
        "onboarding_applications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "change_set_id",
            UUID,
            sa.ForeignKey("onboarding_change_sets.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_onb_application_status",
        ),
    )
    op.create_index(
        "ix_onboarding_applications_session_id",
        "onboarding_applications",
        ["session_id"],
    )

    op.create_table(
        "onboarding_application_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "application_id",
            UUID,
            sa.ForeignKey("onboarding_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            UUID,
            sa.ForeignKey("background_jobs.id"),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "application_id", "attempt_number", name="uq_onb_app_attempt_number"
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_onb_app_attempt_number"
        ),
    )

    op.create_table(
        "onboarding_applied_bindings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "application_id",
            UUID,
            sa.ForeignKey("onboarding_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "operation_id",
            UUID,
            sa.ForeignKey("onboarding_change_operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", UUID, nullable=False),
        sa.Column("binding_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "application_id", "operation_id", name="uq_onb_applied_binding_op"
        ),
    )

    op.create_table(
        "onboarding_application_receipts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "application_id",
            UUID,
            sa.ForeignKey("onboarding_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "key_digest", name="uq_onb_app_receipt_scope_key"),
    )

    op.add_column(
        "onboarding_runs",
        sa.Column(
            "change_set_id",
            UUID,
            sa.ForeignKey("onboarding_change_sets.id"),
            nullable=True,
        ),
    )
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
        "'GENERATE_GOVERNANCE','APPLY_CHANGESET')",
    )

    op.drop_constraint(
        "uq_policy_rule_link_source_rule", "policy_rule_links", type_="unique"
    )
    op.create_index(
        "uq_policy_rule_link_source_rule",
        "policy_rule_links",
        ["policy_source_id", "rule_type", "rule_id"],
        unique=True,
        postgresql_where=sa.text("obligation_id IS NULL"),
    )
    op.create_index(
        "uq_policy_rule_link_source_obligation_rule",
        "policy_rule_links",
        ["policy_source_id", "obligation_id", "rule_type", "rule_id"],
        unique=True,
        postgresql_where=sa.text("obligation_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_policy_rule_link_source_obligation_rule", table_name="policy_rule_links"
    )
    op.drop_index("uq_policy_rule_link_source_rule", table_name="policy_rule_links")
    op.create_unique_constraint(
        "uq_policy_rule_link_source_rule",
        "policy_rule_links",
        ["policy_source_id", "rule_type", "rule_id"],
    )

    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
        "'GENERATE_GOVERNANCE')",
    )
    op.drop_column("onboarding_runs", "change_set_id")

    op.drop_table("onboarding_application_receipts")
    op.drop_table("onboarding_applied_bindings")
    op.drop_table("onboarding_application_attempts")
    op.drop_table("onboarding_applications")
    op.drop_table("onboarding_review_decisions")
    op.drop_table("onboarding_change_operations")
    op.drop_table("onboarding_change_sets")