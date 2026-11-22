"""onboarding_sessions_sources_jobs

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-19 10:00:00.000000

Step 02 intake workspace: sessions, immutable source revisions, preparation runs and
mutation receipts, plus nullable lease columns on ``background_jobs``.
Existing rows are untouched. Downgrade drops only what this revision created.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade() -> None:
    op.add_column("background_jobs", sa.Column("worker_id", sa.String(), nullable=True))
    op.add_column(
        "background_jobs", sa.Column("lease_token", sa.String(), nullable=True)
    )
    op.add_column("background_jobs", _ts("lease_expires_at", nullable=True))
    op.add_column("background_jobs", _ts("heartbeat_at", nullable=True))
    op.create_index(
        "ix_background_jobs_lease",
        "background_jobs",
        ["job_type", "status", "lease_expires_at"],
    )

    op.create_table(
        "onboarding_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("lifecycle", sa.String(16), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=False),
        _ts("created_at", server_default=sa.func.now()),
        _ts("updated_at", server_default=sa.func.now()),
        sa.CheckConstraint(
            "lifecycle IN ('OPEN','ARCHIVED')", name="ck_onb_session_lifecycle"
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_onb_session_row_version"),
        sa.CheckConstraint("input_revision >= 0", name="ck_onb_session_input_revision"),
    )

    op.create_table(
        "onboarding_sources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("current_revision_id", UUID, nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        _ts("created_at", server_default=sa.func.now()),
        _ts("updated_at", server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('FILE','TEXT')", name="ck_onb_source_kind"),
        sa.CheckConstraint(
            "role IN ('USER_PROMPT','SUPPORTING_MATERIAL')", name="ck_onb_source_role"
        ),
        sa.CheckConstraint(
            "role <> 'USER_PROMPT' OR kind = 'TEXT'", name="ck_onb_source_prompt_text"
        ),
        sa.UniqueConstraint("id", "session_id", name="uq_onb_source_id_session"),
    )
    op.create_index(
        "ix_onboarding_sources_session_id", "onboarding_sources", ["session_id"]
    )
    op.create_index(
        "uq_onb_one_included_prompt",
        "onboarding_sources",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("role = 'USER_PROMPT' AND included"),
    )

    op.create_table(
        "onboarding_source_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("session_id", UUID, nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("declared_media_type", sa.String(255), nullable=True),
        sa.Column("media_family", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        _ts("received_at", server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["source_id", "session_id"],
            ["onboarding_sources.id", "onboarding_sources.session_id"],
            name="fk_onb_rev_source_session",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "source_id", "revision_number", name="uq_onb_rev_source_number"
        ),
        sa.UniqueConstraint("storage_key", name="uq_onb_rev_storage_key"),
        sa.UniqueConstraint("id", "source_id", name="uq_onb_rev_id_source"),
        sa.CheckConstraint("revision_number >= 1", name="ck_onb_rev_number"),
        sa.CheckConstraint("byte_length >= 0", name="ck_onb_rev_bytes"),
    )
    op.create_index(
        "ix_onboarding_source_revisions_source_id",
        "onboarding_source_revisions",
        ["source_id"],
    )
    op.create_index(
        "ix_onboarding_source_revisions_session_id",
        "onboarding_source_revisions",
        ["session_id"],
    )
    op.create_foreign_key(
        "fk_onb_source_current_revision",
        "onboarding_sources",
        "onboarding_source_revisions",
        ["current_revision_id", "id"],
        ["id", "source_id"],
    )

    op.create_table(
        "onboarding_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("onboarding_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(24), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=False),
        sa.Column("job_id", UUID, sa.ForeignKey("background_jobs.id"), nullable=False),
        sa.Column(
            "retry_of_run_id", UUID, sa.ForeignKey("onboarding_runs.id"), nullable=True
        ),
        sa.Column("created_by", sa.String(), nullable=False),
        _ts("created_at", server_default=sa.func.now()),
        _ts("ended_at", nullable=True),
        sa.UniqueConstraint(
            "session_id", "run_number", name="uq_onb_run_session_number"
        ),
        sa.UniqueConstraint("job_id", name="uq_onb_run_job"),
        sa.CheckConstraint("run_number >= 1", name="ck_onb_run_number"),
        sa.CheckConstraint("input_revision >= 0", name="ck_onb_run_input_revision"),
        sa.CheckConstraint("stage IN ('PREPARE_SOURCES')", name="ck_onb_run_stage"),
    )
    op.create_index("ix_onboarding_runs_session_id", "onboarding_runs", ["session_id"])
    op.create_index(
        "uq_onb_one_active_run",
        "onboarding_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "onboarding_mutation_receipts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(24), nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        _ts("created_at", server_default=sa.func.now()),
        sa.UniqueConstraint("scope", "key_digest", name="uq_onb_receipt_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("onboarding_mutation_receipts")
    op.drop_table("onboarding_runs")
    op.drop_constraint(
        "fk_onb_source_current_revision", "onboarding_sources", type_="foreignkey"
    )
    op.drop_table("onboarding_source_revisions")
    op.drop_table("onboarding_sources")
    op.drop_table("onboarding_sessions")
    op.drop_index("ix_background_jobs_lease", table_name="background_jobs")
    op.drop_column("background_jobs", "heartbeat_at")
    op.drop_column("background_jobs", "lease_expires_at")
    op.drop_column("background_jobs", "lease_token")
    op.drop_column("background_jobs", "worker_id")
