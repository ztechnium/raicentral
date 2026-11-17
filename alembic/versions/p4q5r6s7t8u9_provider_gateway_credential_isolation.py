"""provider gateway credential isolation (network-rai step 5)

Revision ID: p4q5r6s7t8u9
Revises: n3o4p5q6r7s8
Create Date: 2026-11-14 08:40:00.000000
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
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = set(inspector.get_table_names())

    if "provider_credential_refs" not in existing:
        op.create_table(
            "provider_credential_refs",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("credential_key", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("credential_type", sa.String(), nullable=False),
            sa.Column("secret_backend", sa.String(), nullable=False),
            sa.Column("secret_reference", sa.String(), nullable=False),
            sa.Column("scope", sa.JSON(), nullable=False),
            sa.Column("environment", sa.String()),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("active_version", sa.Integer(), nullable=False),
            sa.Column("pending_secret_reference", sa.String()),
            sa.Column("pending_version", sa.Integer()),
            sa.Column("rotation_metadata", sa.JSON(), nullable=False),
            sa.Column("last_validated_at", sa.DateTime(timezone=True)),
            sa.Column("base_url_override", sa.String()),
            sa.Column("transport_config", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_by", sa.String()),
        )
        op.create_index(
            "ix_provider_credential_refs_credential_key",
            "provider_credential_refs",
            ["credential_key"],
            unique=True,
        )
        op.create_index(
            "ix_provider_credential_refs_provider",
            "provider_credential_refs",
            ["provider"],
        )
        op.create_index(
            "ix_provider_credential_refs_status",
            "provider_credential_refs",
            ["status"],
        )

    if "provider_credential_grants" not in existing:
        op.create_table(
            "provider_credential_grants",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "runtime_identity_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agent_runtime_identities.id"),
                nullable=False,
            ),
            sa.Column(
                "provider_credential_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("provider_credential_refs.id"),
                nullable=False,
            ),
            sa.Column("allowed_providers", sa.JSON(), nullable=False),
            sa.Column("allowed_models", sa.JSON()),
            sa.Column("environment", sa.String()),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by", sa.String(), nullable=False),
        )
        op.create_index(
            "ix_provider_credential_grants_runtime_identity_id",
            "provider_credential_grants",
            ["runtime_identity_id"],
        )
        op.create_index(
            "ix_provider_credential_grants_provider_credential_id",
            "provider_credential_grants",
            ["provider_credential_id"],
        )

    if "provider_credential_rotation_events" not in existing:
        op.create_table(
            "provider_credential_rotation_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "provider_credential_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("provider_credential_refs.id"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("from_version", sa.Integer()),
            sa.Column("to_version", sa.Integer()),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by", sa.String(), nullable=False),
        )
        op.create_index(
            "ix_provider_credential_rotation_events_provider_credential_id",
            "provider_credential_rotation_events",
            ["provider_credential_id"],
        )

    if "workload_identity_bindings" not in existing:
        op.create_table(
            "workload_identity_bindings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("cloud_provider", sa.String(), nullable=False),
            sa.Column("principal_identifier", sa.String(), nullable=False),
            sa.Column(
                "runtime_identity_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agent_runtime_identities.id"),
                nullable=False,
            ),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("binding_metadata", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by", sa.String(), nullable=False),
        )
        op.create_index(
            "ix_workload_identity_bindings_cloud_provider",
            "workload_identity_bindings",
            ["cloud_provider"],
        )
        op.create_index(
            "ix_workload_identity_bindings_principal_identifier",
            "workload_identity_bindings",
            ["principal_identifier"],
        )
        op.create_index(
            "uq_workload_binding_principal",
            "workload_identity_bindings",
            ["cloud_provider", "principal_identifier"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_table("workload_identity_bindings")
    op.drop_table("provider_credential_rotation_events")
    op.drop_table("provider_credential_grants")
    op.drop_table("provider_credential_refs")