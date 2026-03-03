"""Phase 3 autonomous pipeline controls

Revision ID: 008_phase3_autonomous_pipeline
Revises: 007_phase1_onboarding_installations
Create Date: 2026-02-18
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "008_phase3_autonomous_pipeline"
down_revision: Union[str, None] = "007_phase1_onboarding_installations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("critic_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("merge_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column(
            "post_merge_monitor_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("retry_limit_snapshot", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column(
            "automation_mode", sa.String(length=32), nullable=False, server_default="auto_pr"
        ),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column(
            "manual_review_required", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_column("fix_pipeline_runs", "manual_review_required")
    op.drop_column("fix_pipeline_runs", "automation_mode")
    op.drop_column("fix_pipeline_runs", "retry_limit_snapshot")
    op.drop_column("fix_pipeline_runs", "post_merge_monitor_json")
    op.drop_column("fix_pipeline_runs", "merge_result_json")
    op.drop_column("fix_pipeline_runs", "critic_json")
