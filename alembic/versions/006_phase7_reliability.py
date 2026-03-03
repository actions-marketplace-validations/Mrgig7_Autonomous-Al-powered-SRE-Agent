"""Phase 7 reliability hardening

Revision ID: 006_phase7_reliability
Revises: 005_fix_pipeline_adapter
Create Date: 2026-01-21

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "006_phase7_reliability"
down_revision: Union[str, None] = "005_fix_pipeline_adapter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("details", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="uq_webhook_deliveries_delivery_id"),
    )
    op.create_index(
        "idx_webhook_deliveries_repo_received",
        "webhook_deliveries",
        ["repository", "received_at"],
    )

    op.add_column("fix_pipeline_runs", sa.Column("run_key", sa.String(length=512), nullable=True))
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("fix_pipeline_runs", sa.Column("blocked_reason", sa.Text(), nullable=True))
    op.add_column("fix_pipeline_runs", sa.Column("last_pr_url", sa.Text(), nullable=True))
    op.add_column(
        "fix_pipeline_runs",
        sa.Column("last_pr_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_fix_pipeline_runs_event_id",
        "fix_pipeline_runs",
        ["event_id"],
        unique=True,
    )
    op.create_index(
        "uq_fix_pipeline_runs_run_key",
        "fix_pipeline_runs",
        ["run_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_fix_pipeline_runs_run_key", table_name="fix_pipeline_runs")
    op.drop_index("uq_fix_pipeline_runs_event_id", table_name="fix_pipeline_runs")
    op.drop_column("fix_pipeline_runs", "last_pr_created_at")
    op.drop_column("fix_pipeline_runs", "last_pr_url")
    op.drop_column("fix_pipeline_runs", "blocked_reason")
    op.drop_column("fix_pipeline_runs", "attempt_count")
    op.drop_column("fix_pipeline_runs", "run_key")

    op.drop_index("idx_webhook_deliveries_repo_received", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
