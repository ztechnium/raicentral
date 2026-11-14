"""network_bypass_detection_step7

Revision ID: a2b3c4d5e6f7
Revises: z1a2b3c4d5e6
Create Date: 2026-11-14 00:00:00.000000

Step 7 — observed connection events and runtime attestation state.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "z1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observed_connection_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_workload", sa.String(), nullable=True),
        sa.Column("source_process", sa.String(), nullable=True),
        sa.Column("destination_hostname", sa.String(), nullable=False),
        sa.Column("destination_ip", sa.String(), nullable=True),
        sa.Column("destination_port", sa.Integer(), nullable=True),
        sa.Column("protocol", sa.String(), nullable=True),
        sa.Column("cloud_resource_identity", sa.String(), nullable=True),
        sa.Column("runtime_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gateway_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("provider_guess", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False, server_default="OBSERVED"),
        sa.Column("collector_source", sa.String(), nullable=True),
        sa.Column("classifier_version", sa.String(), nullable=False),
        sa.Column("dedup_key", sa.String(), nullable=False),
        sa.Column("bypass_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["ai_incidents.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dedup_key", name="uq_observed_connection_dedup"),
    )
    op.create_index(
        "ix_observed_connection_events_observed_at",
        "observed_connection_events",
        ["observed_at"],
    )
    op.create_index(
        "ix_observed_connection_events_destination_hostname",
        "observed_connection_events",
        ["destination_hostname"],
    )
    op.create_index(
        "ix_observed_connection_events_runtime_identity_id",
        "observed_connection_events",
        ["runtime_identity_id"],
    )
    op.create_index(
        "ix_observed_connection_events_provider_guess",
        "observed_connection_events",
        ["provider_guess"],
    )
    op.create_index(
        "ix_observed_connection_events_incident_id",
        "observed_connection_events",
        ["incident_id"],
    )

    op.create_table(
        "runtime_attestation_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runtime_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ai_system_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="UNKNOWN"),
        sa.Column("declared_providers", sa.JSON(), nullable=True),
        sa.Column("observed_providers", sa.JSON(), nullable=True),
        sa.Column("authorized_providers", sa.JSON(), nullable=True),
        sa.Column("last_bypass_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "runtime_identity_id", name="uq_runtime_attestation_identity"
        ),
    )
    op.create_index(
        "ix_runtime_attestation_states_runtime_identity_id",
        "runtime_attestation_states",
        ["runtime_identity_id"],
    )
    op.create_index(
        "ix_runtime_attestation_states_status",
        "runtime_attestation_states",
        ["status"],
    )
    op.create_index(
        "ix_runtime_attestation_states_ai_system_id",
        "runtime_attestation_states",
        ["ai_system_id"],
    )


def downgrade() -> None:
    op.drop_table("runtime_attestation_states")
    op.drop_table("observed_connection_events")