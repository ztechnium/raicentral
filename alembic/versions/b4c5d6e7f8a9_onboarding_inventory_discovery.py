"""onboarding_inventory_discovery

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-21 12:00:00.000000

Step 04: inventory discovery plans, calls, proposal sets, reconciliation, edits,
clarification answers, and session/run extensions.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "onboarding_sessions",
        sa.Column("clarification_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_onb_session_clarification_revision",
        "onboarding_sessions",
        "clarification_revision >= 0",
    )

    op.create_table(
        "onboarding_discovery_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_onboarding_discovery_plans_session_id",
        "onboarding_discovery_plans",
        ["session_id"],
    )

    op.add_column(
        "onboarding_runs",
        sa.Column(
            "extraction_run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "onboarding_runs",
        sa.Column("discovery_plan_id", UUID, nullable=True),
    )
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY')",
    )
    op.create_foreign_key(
        "fk_onb_run_discovery_plan",
        "onboarding_runs",
        "onboarding_discovery_plans",
        ["discovery_plan_id"],
        ["id"],
    )

    op.create_table(
        "onboarding_discovery_calls",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_onb_discovery_call_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_onb_discovery_call_ordinal"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('NOT_DISPATCHED','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')",
            name="ck_onb_discovery_call_outcome",
        ),
    )
    op.create_index(
        "ix_onboarding_discovery_calls_run_id",
        "onboarding_discovery_calls",
        ["run_id"],
    )

    op.create_table(
        "onboarding_proposal_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "discovery_run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_reconciliation_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("edit_version >= 1", name="ck_onb_proposal_set_edit_version"),
    )
    op.create_index(
        "ix_onboarding_proposal_sets_session_id",
        "onboarding_proposal_sets",
        ["session_id"],
    )

    op.create_table(
        "onboarding_inventory_proposals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("generated", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "entity_type IN ('ai_system','agent','agent_version','model','tool',"
            "'mcp_server','external_service','data_source','dependency')",
            name="ck_onb_proposal_entity_type",
        ),
    )
    op.create_index(
        "ix_onboarding_inventory_proposals_set_id",
        "onboarding_inventory_proposals",
        ["set_id"],
    )

    op.create_table(
        "onboarding_reconciliation_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("registry_projection", sa.JSON(), nullable=False),
        sa.Column("registry_digest", sa.String(64), nullable=False),
        sa.Column("matching_rule_version", sa.String(16), nullable=False),
        sa.Column("matches", sa.JSON(), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "set_id", "edit_version", name="uq_onb_reconciliation_set_version"
        ),
        sa.CheckConstraint("edit_version >= 1", name="ck_onb_reconciliation_edit_version"),
    )
    op.create_index(
        "ix_onboarding_reconciliation_snapshots_set_id",
        "onboarding_reconciliation_snapshots",
        ["set_id"],
    )

    op.create_foreign_key(
        "fk_onb_proposal_set_reconciliation",
        "onboarding_proposal_sets",
        "onboarding_reconciliation_snapshots",
        ["current_reconciliation_id"],
        ["id"],
    )

    op.create_table(
        "onboarding_proposal_edits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("onboarding_inventory_proposals.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("replacement", sa.JSON(), nullable=True),
        sa.Column("resolution", sa.String(32), nullable=True),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("edit_version >= 1", name="ck_onb_proposal_edit_version"),
    )
    op.create_index(
        "ix_onboarding_proposal_edits_set_id",
        "onboarding_proposal_edits",
        ["set_id"],
    )

    op.create_table(
        "onboarding_clarification_answers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer_revision", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(64), nullable=False),
        sa.Column("question_snapshot", sa.JSON(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("unknown", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("set_id", UUID, nullable=True),
        sa.Column("proposal_id", UUID, nullable=True),
        sa.Column(
            "supersedes_id",
            UUID,
            sa.ForeignKey("onboarding_clarification_answers.id"),
            nullable=True,
        ),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("answer_revision >= 1", name="ck_onb_answer_revision"),
    )
    op.create_index(
        "ix_onboarding_clarification_answers_session_id",
        "onboarding_clarification_answers",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_table("onboarding_clarification_answers")
    op.drop_table("onboarding_proposal_edits")
    op.drop_constraint(
        "fk_onb_proposal_set_reconciliation", "onboarding_proposal_sets", type_="foreignkey"
    )
    op.drop_table("onboarding_reconciliation_snapshots")
    op.drop_table("onboarding_inventory_proposals")
    op.drop_table("onboarding_proposal_sets")
    op.drop_table("onboarding_discovery_calls")
    op.drop_constraint("fk_onb_run_discovery_plan", "onboarding_runs", type_="foreignkey")
    op.drop_column("onboarding_runs", "discovery_plan_id")
    op.drop_column("onboarding_runs", "extraction_run_id")
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES')",
    )
    op.drop_table("onboarding_discovery_plans")
    op.drop_constraint(
        "ck_onb_session_clarification_revision", "onboarding_sessions", type_="check"
    )
    op.drop_column("onboarding_sessions", "clarification_revision")