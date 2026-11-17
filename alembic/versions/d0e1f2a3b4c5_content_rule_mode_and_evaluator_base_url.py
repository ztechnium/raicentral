"""content_rule_mode_and_evaluator_base_url

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-16 13:00:00.000000

Per-agent-version Content Rule mode (OFF/OBSERVE/ENFORCE) and an optional endpoint
override on the content evaluator config.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_versions", sa.Column("content_rule_mode", sa.String(), nullable=True)
    )
    op.add_column(
        "content_evaluator_configs", sa.Column("base_url", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("content_evaluator_configs", "base_url")
    op.drop_column("agent_versions", "content_rule_mode")
