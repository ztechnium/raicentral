"""content_evaluator_vertex_fields

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-16 14:00:00.000000

Vertex AI settings on the content evaluator config: location, project, and an optional
service-account JSON (server-side only, never returned to clients).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("content_evaluator_configs", sa.Column("location", sa.String(), nullable=True))
    op.add_column(
        "content_evaluator_configs", sa.Column("gcp_project_id", sa.String(), nullable=True)
    )
    op.add_column(
        "content_evaluator_configs",
        sa.Column("service_account_json_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("content_evaluator_configs", "service_account_json_encrypted")
    op.drop_column("content_evaluator_configs", "gcp_project_id")
    op.drop_column("content_evaluator_configs", "location")
