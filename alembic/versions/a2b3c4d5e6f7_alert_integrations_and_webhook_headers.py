"""alert integrations and webhook custom headers

Revision ID: a2b3c4d5e6f7
Revises: z1a2b3c4d5e6
Create Date: 2026-11-23 10:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f9a0b1c2d3e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "webhook_endpoints",
        sa.Column("custom_headers", sa.JSON(), nullable=True),
    )
    op.create_table(
        "alert_channel_integrations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("custom_headers", sa.JSON(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_alert_channel_integrations_provider",
        "alert_channel_integrations",
        ["provider"],
    )
    op.create_index(
        "ix_alert_channel_integrations_status",
        "alert_channel_integrations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_channel_integrations_status", table_name="alert_channel_integrations")
    op.drop_index(
        "ix_alert_channel_integrations_provider", table_name="alert_channel_integrations"
    )
    op.drop_table("alert_channel_integrations")
    op.drop_column("webhook_endpoints", "custom_headers")