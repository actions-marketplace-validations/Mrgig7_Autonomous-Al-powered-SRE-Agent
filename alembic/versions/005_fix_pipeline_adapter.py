"""Add fix pipeline adapter fields

Revision ID: 005_fix_pipeline_adapter
Revises: 004_add_fix_pipeline_artifacts
Create Date: 2026-01-21

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "005_fix_pipeline_adapter"
down_revision = "004_add_fix_pipeline_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fix_pipeline_runs", sa.Column("adapter_name", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("detection_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fix_pipeline_runs", "detection_json")
    op.drop_column("fix_pipeline_runs", "adapter_name")
