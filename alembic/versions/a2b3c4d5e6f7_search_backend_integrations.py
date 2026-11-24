"""search backend integrations (Elasticsearch)

Revision ID: a2b3c4d5e6f7
Revises: z1a2b3c4d5e6
Create Date: 2026-11-24 00:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "z1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_backend_integrations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sync_logs", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "use_for_search", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
        sa.Column("config", sa.JSON(), nullable=False),
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
        "ix_search_backend_integrations_provider",
        "search_backend_integrations",
        ["provider"],
    )
    op.create_index(
        "ix_search_backend_integrations_status",
        "search_backend_integrations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_search_backend_integrations_status", table_name="search_backend_integrations"
    )
    op.drop_index(
        "ix_search_backend_integrations_provider",
        table_name="search_backend_integrations",
    )
    op.drop_table("search_backend_integrations")