"""onboarding_incremental_updates

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-11-22 12:00:00.000000

Step 08: baselines, source lineage, delta analysis, reconciliation, verification impact.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "onboarding_sessions",
        sa.Column("session_kind", sa.String(24), nullable=False, server_default="INITIAL"),
    )
    op.add_column(
        "onboarding_sessions",
        sa.Column("baseline_session_id", UUID, nullable=True),
    )
    op.add_column(
        "onboarding_sessions",
        sa.Column("confirmed_baseline_id", UUID, nullable=True),
    )
    op.add_column(
        "onboarding_sessions",
        sa.Column("update_status", sa.String(32), nullable=True),
    )
    op.create_foreign_key(
        "fk_onb_session_baseline_session",
        "onboarding_sessions",
        "onboarding_sessions",
        ["baseline_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_onb_session_kind",
        "onboarding_sessions",
        "session_kind IN ('INITIAL','UPDATE','INVENTORY_CORRECTION','POLICY_REVISION')",
    )
    op.create_index(
        "ix_onboarding_sessions_baseline_session_id",
        "onboarding_sessions",
        ["baseline_session_id"],
    )

    op.create_table(
        "onboarding_baselines",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("baseline_kind", sa.String(32), nullable=False),
        sa.Column(
            "application_receipt_id",
            UUID,
            sa.ForeignKey("onboarding_application_receipts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "application_id",
            UUID,
            sa.ForeignKey("onboarding_applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("domain_projection", sa.JSON(), nullable=False),
        sa.Column("domain_digest", sa.String(64), nullable=False),
        sa.Column("scope_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="CONFIRMED"),
        sa.Column("confirmed_by", sa.String(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "baseline_kind IN ('APPLIED_RECEIPT','REVIEWED_CURRENT_STATE')",
            name="ck_onb_baseline_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PREVIEW','CONFIRMED')", name="ck_onb_baseline_status"
        ),
    )
    op.create_index("ix_onboarding_baselines_session_id", "onboarding_baselines", ["session_id"])

    op.create_table(
        "onboarding_source_lineage",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "previous_revision_id",
            UUID,
            sa.ForeignKey("onboarding_source_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "new_revision_id",
            UUID,
            sa.ForeignKey("onboarding_source_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship", sa.String(16), nullable=False),
        sa.Column("effective_notes", sa.Text(), nullable=True),
        sa.Column("confirmed_by", sa.String(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "relationship IN ('SUPERSEDES','SUPPLEMENTS','INDEPENDENT')",
            name="ck_onb_source_lineage_rel",
        ),
        sa.UniqueConstraint(
            "previous_revision_id", "new_revision_id", name="uq_onb_source_lineage_pair"
        ),
    )

    op.create_table(
        "onboarding_delta_plans",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "baseline_id",
            UUID,
            sa.ForeignKey("onboarding_baselines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PREPARED"),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('PREPARED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name="ck_onb_delta_plan_status",
        ),
        sa.CheckConstraint("plan_version >= 1", name="ck_onb_delta_plan_version"),
    )

    for suffix, cols in (
        (
            "assertion_matches",
            [
                sa.Column("ordinal", sa.Integer(), nullable=False),
                sa.Column("old_identity", sa.String(512), nullable=False),
                sa.Column("new_identity", sa.String(512), nullable=True),
                sa.Column("match_class", sa.String(32), nullable=False),
                sa.Column("method_version", sa.String(16), nullable=False),
                sa.Column("match_reason", sa.Text(), nullable=False),
                sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            ],
        ),
        (
            "change_findings",
            [
                sa.Column("ordinal", sa.Integer(), nullable=False),
                sa.Column("change_type", sa.String(32), nullable=False),
                sa.Column("identity", sa.String(512), nullable=False),
                sa.Column("before_digest", sa.String(64), nullable=True),
                sa.Column("after_digest", sa.String(64), nullable=True),
                sa.Column("summary", sa.Text(), nullable=False),
                sa.Column("impact_level", sa.String(16), nullable=False, server_default="MEDIUM"),
                sa.Column("resolution_status", sa.String(24), nullable=False, server_default="OPEN"),
                sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            ],
        ),
        (
            "impact_edges",
            [
                sa.Column("finding_ordinal", sa.Integer(), nullable=False),
                sa.Column("ordinal", sa.Integer(), nullable=False),
                sa.Column("edge_type", sa.String(32), nullable=False),
                sa.Column("reference_kind", sa.String(32), nullable=False),
                sa.Column("reference_id", sa.String(128), nullable=False),
                sa.Column("direct", sa.Boolean(), nullable=False, server_default=sa.text("true")),
                sa.Column("confidence", sa.String(24), nullable=False, server_default="INFERRED_NEEDS_REVIEW"),
                sa.Column("reason", sa.Text(), nullable=False),
            ],
        ),
        (
            "update_reconciliations",
            [
                sa.Column("edit_version", sa.Integer(), nullable=False),
                sa.Column("baseline_digest", sa.String(64), nullable=False),
                sa.Column("current_domain_digest", sa.String(64), nullable=False),
                sa.Column("source_digest", sa.String(64), nullable=False),
                sa.Column("difference", sa.JSON(), nullable=False),
                sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            ],
        ),
        (
            "verification_impacts",
            [
                sa.Column("ordinal", sa.Integer(), nullable=False),
                sa.Column(
                    "verification_attempt_id",
                    UUID,
                    sa.ForeignKey("onboarding_verification_attempts.id", ondelete="SET NULL"),
                    nullable=True,
                ),
                sa.Column("scenario_key", sa.String(64), nullable=False),
                sa.Column("target_digest", sa.String(64), nullable=False),
                sa.Column("applicability", sa.String(24), nullable=False),
                sa.Column("reason", sa.Text(), nullable=False),
            ],
        ),
    ):
        table = f"onboarding_{suffix}"
        op.create_table(
            table,
            sa.Column("id", UUID, primary_key=True),
            sa.Column(
                "delta_plan_id",
                UUID,
                sa.ForeignKey("onboarding_delta_plans.id", ondelete="CASCADE"),
                nullable=False,
            ),
            *cols,
        )

    op.create_table(
        "onboarding_update_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            UUID,
            sa.ForeignKey("onboarding_change_findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("edit_revision", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_id",
            UUID,
            sa.ForeignKey("onboarding_update_decisions.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("edit_revision >= 1", name="ck_onb_update_decision_revision"),
        sa.CheckConstraint(
            "decision IN ('KEEP','UPDATE','EXCLUDE','MATCH','CLARIFY','DEFER','SOURCE_ONLY')",
            name="ck_onb_update_decision",
        ),
    )

    op.add_column("onboarding_runs", sa.Column("delta_plan_id", UUID, nullable=True))
    op.create_foreign_key(
        "fk_onb_run_delta_plan",
        "onboarding_runs",
        "onboarding_delta_plans",
        ["delta_plan_id"],
        ["id"],
    )

    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
        "'GENERATE_GOVERNANCE','APPLY_CHANGESET','ACTIVATE_CONTROLS','VERIFY_RUNTIME',"
        "'ANALYZE_DELTA')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES','DISCOVER_INVENTORY',"
        "'GENERATE_GOVERNANCE','APPLY_CHANGESET','ACTIVATE_CONTROLS','VERIFY_RUNTIME')",
    )
    op.drop_constraint("fk_onb_run_delta_plan", "onboarding_runs", type_="foreignkey")
    op.drop_column("onboarding_runs", "delta_plan_id")

    op.drop_table("onboarding_update_decisions")
    op.drop_table("onboarding_verification_impacts")
    op.drop_table("onboarding_update_reconciliations")
    op.drop_table("onboarding_impact_edges")
    op.drop_table("onboarding_change_findings")
    op.drop_table("onboarding_assertion_matches")
    op.drop_table("onboarding_delta_plans")
    op.drop_table("onboarding_source_lineage")
    op.drop_table("onboarding_baselines")

    op.drop_constraint("ck_onb_session_kind", "onboarding_sessions", type_="check")
    op.drop_constraint("fk_onb_session_baseline_session", "onboarding_sessions", type_="foreignkey")
    op.drop_index("ix_onboarding_sessions_baseline_session_id", table_name="onboarding_sessions")
    op.drop_column("onboarding_sessions", "update_status")
    op.drop_column("onboarding_sessions", "confirmed_baseline_id")
    op.drop_column("onboarding_sessions", "baseline_session_id")
    op.drop_column("onboarding_sessions", "session_kind")