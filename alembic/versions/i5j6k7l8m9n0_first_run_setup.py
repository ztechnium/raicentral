"""first-run setup: skip timestamp and agent version label

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-11-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i5j6k7l8m9n0"
down_revision: Union[str, None] = "h4i5j6k7l8m9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organization_onboarding",
        sa.Column("setup_skipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_versions",
        sa.Column("version_label", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "version_label")
    op.drop_column("organization_onboarding", "setup_skipped_at")
