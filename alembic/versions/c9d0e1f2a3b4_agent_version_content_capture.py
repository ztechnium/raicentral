"""agent_version_content_capture

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-16 12:00:00.000000

Per-agent-version evidence content policy (prompt / model response capture).
Operational setting, like evaluation_endpoint_url: it can change on any lifecycle
status without creating a new governed version.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_versions",
        sa.Column("evidence_content_policy", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_versions", "evidence_content_policy")
