"""Add fix pipeline run persistence

Revision ID: 003_add_fix_pipeline_runs
Revises: 002_add_users_auth
Create Date: 2026-01-20

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "003_add_fix_pipeline_runs"
down_revision: Union[str, None] = "002_add_users_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fix_pipeline_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="created"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("context_json", postgresql.JSONB(), nullable=True),
        sa.Column("rca_json", postgresql.JSONB(), nullable=True),
        sa.Column("plan_json", postgresql.JSONB(), nullable=True),
        sa.Column("plan_policy_json", postgresql.JSONB(), nullable=True),
        sa.Column("patch_diff", sa.Text(), nullable=True),
        sa.Column("patch_stats_json", postgresql.JSONB(), nullable=True),
        sa.Column("patch_policy_json", postgresql.JSONB(), nullable=True),
        sa.Column("validation_json", postgresql.JSONB(), nullable=True),
        sa.Column("pr_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_id"], ["pipeline_events.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_fix_pipeline_runs_event_id", "fix_pipeline_runs", ["event_id"])
    op.create_index("idx_fix_pipeline_runs_status", "fix_pipeline_runs", ["status"])


def downgrade() -> None:
    op.drop_index("idx_fix_pipeline_runs_status", table_name="fix_pipeline_runs")
    op.drop_index("idx_fix_pipeline_runs_event_id", table_name="fix_pipeline_runs")
    op.drop_table("fix_pipeline_runs")
