"""Add fix pipeline artifacts

Revision ID: 004_add_fix_pipeline_artifacts
Revises: 003_add_fix_pipeline_runs
Create Date: 2026-01-20

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "004_add_fix_pipeline_artifacts"
down_revision = "003_add_fix_pipeline_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("artifact_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("fix_pipeline_runs", sa.Column("sbom_path", sa.Text(), nullable=True))
    op.add_column(
        "fix_pipeline_runs", sa.Column("sbom_sha256", sa.String(length=128), nullable=True)
    )
    op.add_column("fix_pipeline_runs", sa.Column("sbom_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("fix_pipeline_runs", "sbom_size_bytes")
    op.drop_column("fix_pipeline_runs", "sbom_sha256")
    op.drop_column("fix_pipeline_runs", "sbom_path")
    op.drop_column("fix_pipeline_runs", "artifact_json")
