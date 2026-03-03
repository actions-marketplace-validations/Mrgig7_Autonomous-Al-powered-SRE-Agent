"""Phase 4 consensus core persistence

Revision ID: 009_phase4_consensus_core
Revises: 008_phase3_autonomous_pipeline
Create Date: 2026-02-18
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "009_phase4_consensus_core"
down_revision: Union[str, None] = "008_phase3_autonomous_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("issue_graph_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("consensus_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column(
            "consensus_shadow_diff_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("consensus_state", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fix_pipeline_runs", "consensus_state")
    op.drop_column("fix_pipeline_runs", "consensus_shadow_diff_json")
    op.drop_column("fix_pipeline_runs", "consensus_json")
    op.drop_column("fix_pipeline_runs", "issue_graph_json")
