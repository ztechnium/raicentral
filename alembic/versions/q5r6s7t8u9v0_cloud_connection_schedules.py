"""cloud connection schedule columns

Revision ID: q5r6s7t8u9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-11-12 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q5r6s7t8u9v0"
down_revision: Union[str, None] = "p4q5r6s7t8u9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cloud_connections",
        sa.Column("sync_interval_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "cloud_connections",
        sa.Column("drift_interval_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "cloud_connections",
        sa.Column(
            "last_scheduled_sync_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "cloud_connections",
        sa.Column(
            "last_scheduled_drift_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("cloud_connections", "last_scheduled_drift_at")
    op.drop_column("cloud_connections", "last_scheduled_sync_at")
    op.drop_column("cloud_connections", "drift_interval_hours")
    op.drop_column("cloud_connections", "sync_interval_hours")
