"""onboarding_extraction_ai_configuration

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-20 10:00:00.000000

Step 03: versioned parsing options on sources, extraction plan/parent on runs, immutable
extraction results with blocks, per-run source accounting, and explicit AI configuration with
provider probes. Existing rows are preserved; historical runs keep their manifests and digests.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
OUTCOMES = "'COMPLETE','PARTIAL','NEEDS_OCR','NEEDS_INPUT','UNSUPPORTED','FAILED'"


def _ts(name: str, **kw) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def upgrade() -> None:
    op.add_column(
        "onboarding_sources",
        sa.Column("parsing_options", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "onboarding_sources",
        sa.Column(
            "parsing_options_version", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "onboarding_runs",
        sa.Column(
            "preparation_run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id"),
            nullable=True,
        ),
    )
    op.add_column("onboarding_runs", sa.Column("plan", sa.JSON(), nullable=True))
    op.add_column(
        "onboarding_runs", sa.Column("plan_digest", sa.String(64), nullable=True)
    )
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage",
        "onboarding_runs",
        "stage IN ('PREPARE_SOURCES','EXTRACT_SOURCES')",
    )

    op.create_table(
        "onboarding_source_extractions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "source_revision_id",
            UUID,
            sa.ForeignKey("onboarding_source_revisions.id"),
            nullable=False,
        ),
        sa.Column("original_sha256", sa.String(64), nullable=False),
        sa.Column("extractor_build", sa.String(64), nullable=False),
        sa.Column("dependency_versions", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.Column("options_snapshot", sa.JSON(), nullable=False),
        sa.Column("options_digest", sa.String(64), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("identity_digest", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("normalization", sa.String(128), nullable=False, server_default=""),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="0"),
        _ts("created_at", server_default=sa.func.now()),
        sa.UniqueConstraint("identity_digest", name="uq_onb_extraction_identity"),
        sa.CheckConstraint(
            f"outcome IN ({OUTCOMES})", name="ck_onb_extraction_outcome"
        ),
        sa.CheckConstraint("block_count >= 0", name="ck_onb_extraction_blocks"),
    )
    op.create_index(
        "ix_onboarding_source_extractions_source_revision_id",
        "onboarding_source_extractions",
        ["source_revision_id"],
    )

    op.create_table(
        "onboarding_extracted_blocks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "extraction_id",
            UUID,
            sa.ForeignKey("onboarding_source_extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("cell_data", sa.JSON(), nullable=True),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.UniqueConstraint("extraction_id", "ordinal", name="uq_onb_block_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ck_onb_block_ordinal"),
    )

    op.create_table(
        "onboarding_run_sources",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "run_id",
            UUID,
            sa.ForeignKey("onboarding_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column(
            "source_revision_id",
            UUID,
            sa.ForeignKey("onboarding_source_revisions.id"),
            nullable=False,
        ),
        sa.Column(
            "extraction_id",
            UUID,
            sa.ForeignKey("onboarding_source_extractions.id"),
            nullable=True,
        ),
        sa.Column("state", sa.String(8), nullable=False, server_default="PENDING"),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.Column("block_count", sa.Integer(), nullable=False, server_default="0"),
        _ts("started_at", nullable=True),
        _ts("finished_at", nullable=True),
        sa.UniqueConstraint(
            "run_id", "source_revision_id", name="uq_onb_run_source_rev"
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','FINAL')", name="ck_onb_run_source_state"
        ),
        sa.CheckConstraint(
            f"outcome IS NULL OR outcome IN ({OUTCOMES},'NOT_PROCESSED')",
            name="ck_onb_run_source_outcome",
        ),
        sa.CheckConstraint(
            "(state = 'PENDING' AND outcome IS NULL) OR (state = 'FINAL' AND outcome IS NOT NULL)",
            name="ck_onb_run_source_final",
        ),
    )
    op.create_index(
        "ix_onboarding_run_sources_run_id", "onboarding_run_sources", ["run_id"]
    )

    op.create_table(
        "onboarding_ai_configuration",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("singleton_key", sa.String(), nullable=False, unique=True),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("target_key", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("structured_mode", sa.String(16), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=False),
        _ts("created_at", server_default=sa.func.now()),
        _ts("updated_at", server_default=sa.func.now()),
        sa.CheckConstraint("config_version >= 1", name="ck_onb_ai_config_version"),
        sa.CheckConstraint(
            "structured_mode IS NULL OR structured_mode IN ('JSON_SCHEMA','JSON_OBJECT')",
            name="ck_onb_ai_config_mode",
        ),
    )

    op.create_table(
        "onboarding_provider_probes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("probe_number", sa.Integer(), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("target_fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("credential_alias", sa.String(64), nullable=False),
        sa.Column("credential_version", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("job_id", UUID, sa.ForeignKey("background_jobs.id"), nullable=False),
        _ts("dispatch_started_at", nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        _ts("created_at", server_default=sa.func.now()),
        _ts("ended_at", nullable=True),
        sa.UniqueConstraint("probe_number", name="uq_onb_probe_number"),
        sa.UniqueConstraint("job_id", name="uq_onb_probe_job"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('PASSED','FAILED','OUTCOME_UNKNOWN','CANCELLED')",
            name="ck_onb_probe_outcome",
        ),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (outcome IS NULL)", name="ck_onb_probe_closed"
        ),
    )
    op.create_index(
        "uq_onb_one_active_probe",
        "onboarding_provider_probes",
        ["configuration_version", "target_fingerprint"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("onboarding_provider_probes")
    op.drop_table("onboarding_ai_configuration")
    op.drop_table("onboarding_run_sources")
    op.drop_table("onboarding_extracted_blocks")
    op.drop_table("onboarding_source_extractions")
    op.execute("DELETE FROM onboarding_runs WHERE stage = 'EXTRACT_SOURCES'")
    op.drop_constraint("ck_onb_run_stage", "onboarding_runs", type_="check")
    op.create_check_constraint(
        "ck_onb_run_stage", "onboarding_runs", "stage IN ('PREPARE_SOURCES')"
    )
    op.drop_column("onboarding_runs", "plan_digest")
    op.drop_column("onboarding_runs", "plan")
    op.drop_column("onboarding_runs", "preparation_run_id")
    op.drop_column("onboarding_sources", "parsing_options_version")
    op.drop_column("onboarding_sources", "parsing_options")
