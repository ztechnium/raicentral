"""onboarding_runtime_verification

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-11-22 10:00:00.000000

Step 07: runtime context, activation, verification, run stage extensions, plan purpose.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "onboarding_runtime_context",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "application_id",
            UUID,
            sa.ForeignKey("onboarding_applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("selected", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("selection_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("delegation_mode", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "delegation_mode IN ('NONE','EXPLICIT','TENANT')",
            name="ck_onb_runtime_ctx_delegation",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_onb_runtime_ctx_row_version"),
    )
    op.create_index(
        "ix_onboarding_runtime_context_session_id",
        "onboarding_runtime_context",
        ["session_id"],
    )

    op.create_table(
        "onboarding_activation_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "runtime_context_id",
            UUID,
            sa.ForeignKey("onboarding_runtime_context.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PREPARED"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PREPARED','ACTIVATING','ACTIVATED','FAILED','DISCARDED')",
            name="ck_onb_activation_plan_status",
        ),
    )

    op.create_table(
        "onboarding_activation_receipts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "plan_id",
            UUID,
            sa.ForeignKey("onboarding_activation_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "key_digest", name="uq_onb_activation_receipt_scope_key"),
    )

    op.create_table(
        "onboarding_verification_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activation_plan_id",
            UUID,
            sa.ForeignKey("onboarding_activation_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PREPARED"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PREPARED','VERIFYING','VERIFIED','FAILED','DISCARDED')",
            name="ck_onb_verification_plan_status",
        ),
    )

    op.create_table(
        "onboarding_verification_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "plan_id",
            UUID,
            sa.ForeignKey("onboarding_verification_plans.id", ondelete="CASCADE"),
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
            "plan_id", "attempt_number", name="uq_onb_verification_attempt_number"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_onb_verification_attempt_number"),
    )

    op.create_table(
        "onboarding_verification_dispatches",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "attempt_id",
            UUID,
            sa.ForeignKey("onboarding_verification_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("scenario_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "attempt_id", "ordinal", name="uq_onb_verification_dispatch_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_onb_verification_dispatch_ordinal"),
        sa.CheckConstraint(
            "status IN ('PENDING','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_onb_verification_dispatch_status",
        ),
    )

    op.add_column(
        "onboarding_runs",
        sa.Column("activation_plan_id", UUID, nullable=True),
    )
    op.add_column(
        "onboarding_runs",
        sa.Column("verification_plan_id", UUID, nullable=True),
    )
    op.create_foreign_key(
        "fk_onb_run_activation_plan",
        "onboarding_runs",
        "onboarding_activation_plans",
        ["activation_plan_id"],
        ["id"],
        use_alter=True,
    )
    op.create_foreign_key(
        "fk_onb_run_verification_plan",
        "onboarding_runs",
        "onboarding_verification_plans",
        ["verification_plan_id"],
        ["id"],
        use_alter=True,
    )

    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
        "'GENERATE_GOVERNANCE','APPLY_CHANGESET','ACTIVATE_CONTROLS','VERIFY_RUNTIME')",
    )

    op.add_column(
        "evaluation_plans",
        sa.Column("purpose", sa.String(32), nullable=False, server_default="DEPLOYMENT"),
    )
    op.execute("UPDATE evaluation_plans SET purpose = 'DEPLOYMENT' WHERE purpose IS NULL")
    op.drop_constraint("uq_evaluation_plan_digest", "evaluation_plans", type_="unique")
    op.create_unique_constraint(
        "uq_evaluation_plan_digest",
        "evaluation_plans",
        ["agent_version_id", "target_environment", "purpose", "plan_digest"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_evaluation_plan_digest", "evaluation_plans", type_="unique")
    op.create_unique_constraint(
        "uq_evaluation_plan_digest",
        "evaluation_plans",
        ["agent_version_id", "target_environment", "plan_digest"],
    )
    op.drop_column("evaluation_plans", "purpose")

    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
        "'GENERATE_GOVERNANCE','APPLY_CHANGESET')",
    )
    op.drop_constraint("fk_onb_run_verification_plan", "onboarding_runs", type_="foreignkey")
    op.drop_constraint("fk_onb_run_activation_plan", "onboarding_runs", type_="foreignkey")
    op.drop_column("onboarding_runs", "verification_plan_id")
    op.drop_column("onboarding_runs", "activation_plan_id")

    op.drop_table("onboarding_verification_dispatches")
    op.drop_table("onboarding_verification_attempts")
    op.drop_table("onboarding_verification_plans")
    op.drop_table("onboarding_activation_receipts")
    op.drop_table("onboarding_activation_plans")
    op.drop_table("onboarding_runtime_context")