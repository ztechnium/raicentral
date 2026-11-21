"""onboarding_governance_proposals

Revision ID: c6d7e8f9a0b1
Revises: b4c5d6e7f8a9
Create Date: 2026-11-21 13:30:00.000000

Step 05: governance plans, calls, proposal sets, clauses, reconciliation, edits,
governance clarification answers, and session/run extensions.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "onboarding_sessions",
        sa.Column(
            "governance_clarification_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_onb_session_governance_clarification_revision",
        "onboarding_sessions",
        "governance_clarification_revision >= 0",
    )

    op.create_table(
        "onboarding_governance_plans",
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
        "ix_onboarding_governance_plans_session_id",
        "onboarding_governance_plans",
        ["session_id"],
    )

    op.add_column(
        "onboarding_runs",
        sa.Column(
            "inventory_proposal_set_id",
            UUID,
            sa.ForeignKey("onboarding_proposal_sets.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "onboarding_runs",
        sa.Column("governance_plan_id", UUID, nullable=True),
    )
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY','GENERATE_GOVERNANCE')",
    )
    op.create_foreign_key(
        "fk_onb_run_governance_plan",
        "onboarding_runs",
        "onboarding_governance_plans",
        ["governance_plan_id"],
        ["id"],
    )

    op.create_table(
        "onboarding_governance_calls",
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
        sa.UniqueConstraint("run_id", "ordinal", name="uq_onb_governance_call_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_onb_governance_call_ordinal"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('NOT_DISPATCHED','SUCCEEDED','FAILED','OUTCOME_UNKNOWN')",
            name="ck_onb_governance_call_outcome",
        ),
    )
    op.create_index(
        "ix_onboarding_governance_calls_run_id",
        "onboarding_governance_calls",
        ["run_id"],
    )

    op.create_table(
        "onboarding_governance_proposal_sets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "governance_run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "inventory_proposal_set_id",
            UUID,
            sa.ForeignKey("onboarding_proposal_sets.id"),
            nullable=False,
        ),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("clause_digest", sa.String(64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("edit_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_reconciliation_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "edit_version >= 1", name="ck_onb_governance_set_edit_version"
        ),
    )
    op.create_index(
        "ix_onboarding_governance_proposal_sets_session_id",
        "onboarding_governance_proposal_sets",
        ["session_id"],
    )

    op.create_table(
        "onboarding_governance_clauses",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("requirement", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("set_id", "ordinal", name="uq_onb_governance_clause_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_onb_governance_clause_ordinal"),
    )
    op.create_index(
        "ix_onboarding_governance_clauses_set_id",
        "onboarding_governance_clauses",
        ["set_id"],
    )

    op.create_table(
        "onboarding_governance_proposals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposal_family", sa.String(32), nullable=False),
        sa.Column("generated", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_onboarding_governance_proposals_set_id",
        "onboarding_governance_proposals",
        ["set_id"],
    )

    op.create_table(
        "onboarding_governance_reconciliation_snapshots",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("baseline_projection", sa.JSON(), nullable=False),
        sa.Column("baseline_digest", sa.String(64), nullable=False),
        sa.Column("matching_rule_version", sa.String(16), nullable=False),
        sa.Column("matches", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "set_id", "edit_version", name="uq_onb_governance_reconciliation_set_version"
        ),
        sa.CheckConstraint(
            "edit_version >= 1", name="ck_onb_governance_reconciliation_edit_version"
        ),
    )
    op.create_foreign_key(
        "fk_onb_governance_set_reconciliation",
        "onboarding_governance_proposal_sets",
        "onboarding_governance_reconciliation_snapshots",
        ["current_reconciliation_id"],
        ["id"],
        use_alter=True,
    )

    op.create_table(
        "onboarding_governance_proposal_edits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "set_id",
            UUID,
            sa.ForeignKey("onboarding_governance_proposal_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "proposal_id",
            UUID,
            sa.ForeignKey("onboarding_governance_proposals.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("replacement", sa.JSON(), nullable=True),
        sa.Column("resolution", sa.String(32), nullable=True),
        sa.Column("edit_version", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "edit_version >= 1", name="ck_onb_governance_proposal_edit_version"
        ),
    )

    op.create_table(
        "onboarding_governance_clarification_answers",
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
        sa.Column("supersedes_id", UUID, nullable=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("author", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "answer_revision >= 1", name="ck_onb_governance_answer_revision"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["onboarding_governance_clarification_answers.id"],
            name="fk_onb_governance_answer_supersedes",
        ),
    )
    op.create_index(
        "ix_onboarding_governance_clarification_answers_session_id",
        "onboarding_governance_clarification_answers",
        ["session_id"],
    )


def downgrade() -> None:
    op.drop_table("onboarding_governance_clarification_answers")
    op.drop_table("onboarding_governance_proposal_edits")
    op.drop_constraint(
        "fk_onb_governance_set_reconciliation",
        "onboarding_governance_proposal_sets",
        type_="foreignkey",
    )
    op.drop_table("onboarding_governance_reconciliation_snapshots")
    op.drop_table("onboarding_governance_proposals")
    op.drop_table("onboarding_governance_clauses")
    op.drop_table("onboarding_governance_proposal_sets")
    op.drop_table("onboarding_governance_calls")
    op.drop_constraint("fk_onb_run_governance_plan", "onboarding_runs", type_="foreignkey")
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY')",
    )
    op.drop_column("onboarding_runs", "governance_plan_id")
    op.drop_column("onboarding_runs", "inventory_proposal_set_id")
    op.drop_table("onboarding_governance_plans")
    op.drop_constraint(
        "ck_onb_session_governance_clarification_revision",
        "onboarding_sessions",
        type_="check",
    )
    op.drop_column("onboarding_sessions", "governance_clarification_revision")