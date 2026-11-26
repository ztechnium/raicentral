"""last event tracking for alert channel integrations

Revision ID: h4i5j6k7l8m9
Revises: g2h3i4j5k6l7
Create Date: 2026-11-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h4i5j6k7l8m9"
down_revision: Union[str, None] = "g2h3i4j5k6l7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "alert_channel_integrations",
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "alert_channel_integrations",
        sa.Column("last_event_status", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alert_channel_integrations", "last_event_status")
    op.drop_column("alert_channel_integrations", "last_event_at")
