"""cloud_governance connector initial tables

Revision ID: p4q5r6s7t8u9
Revises: n3o4p5q6r7s8
Create Date: 2026-11-11 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p4q5r6s7t8u9"
down_revision: Union[str, None] = "n3o4p5q6r7s8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cloud_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False, server_default="project"),
        sa.Column("scope_identifier", sa.String(), nullable=False),
        sa.Column("auth_mode", sa.String(), nullable=False),
        sa.Column("encrypted_config", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("health", sa.JSON(), nullable=True),
        sa.Column(
            "enforcement_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index("ix_cloud_connections_provider", "cloud_connections", ["provider"])
    op.create_index("ix_cloud_connections_status", "cloud_connections", ["status"])
    op.create_index(
        "ix_cloud_connections_scope_identifier",
        "cloud_connections",
        ["scope_identifier"],
    )

    op.create_table(
        "cloud_discovered_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("canonical_resource_name", sa.String(), nullable=False),
        sa.Column("asset_type", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("config_summary", sa.JSON(), nullable=True),
        sa.Column("coverage", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="candidate"),
        sa.Column(
            "ai_system_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_systems.id"),
            nullable=True,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "connection_id",
            "canonical_resource_name",
            name="uq_cloud_asset_canonical",
        ),
    )
    op.create_index(
        "ix_cloud_discovered_assets_connection_id",
        "cloud_discovered_assets",
        ["connection_id"],
    )

    op.create_table(
        "cloud_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "background_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("background_jobs.id"),
            nullable=True,
        ),
        sa.Column("run_type", sa.String(), nullable=False, server_default="asset_discovery"),
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assets_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assets_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partial_errors", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
    )

    op.create_table(
        "cloud_coverage_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("methodology", sa.String(), nullable=False),
        sa.Column("control_matrix", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    op.create_table(
        "cloud_policy_change_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_resource", sa.String(), nullable=False),
        sa.Column("change_kind", sa.String(), nullable=False, server_default="model_armor"),
        sa.Column("preview_payload", sa.JSON(), nullable=True),
        sa.Column("desired_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="preview"),
        sa.Column(
            "approval_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("approval_requests.id"),
            nullable=True,
        ),
        sa.Column("apply_result", sa.JSON(), nullable=True),
        sa.Column("verify_result", sa.JSON(), nullable=True),
        sa.Column("optimistic_version", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    op.create_table(
        "cloud_drift_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("resource_name", sa.String(), nullable=False),
        sa.Column("finding_type", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False, server_default="medium"),
        sa.Column("expected", sa.JSON(), nullable=True),
        sa.Column("observed", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )

    op.create_table(
        "cloud_evidence_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cloud_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ref_type", sa.String(), nullable=False),
        sa.Column("ref_uri", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("evidence_metadata", sa.JSON(), nullable=True),
        sa.Column("linked_entity_type", sa.String(), nullable=True),
        sa.Column("linked_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("cloud_evidence_links")
    op.drop_table("cloud_drift_findings")
    op.drop_table("cloud_policy_change_requests")
    op.drop_table("cloud_coverage_snapshots")
    op.drop_table("cloud_sync_runs")
    op.drop_table("cloud_discovered_assets")
    op.drop_table("cloud_connections")
