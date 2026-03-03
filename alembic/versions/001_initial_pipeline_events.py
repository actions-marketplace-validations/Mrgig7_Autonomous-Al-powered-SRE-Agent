"""Initial pipeline_events table

Revision ID: 001
Revises:
Create Date: 2026-01-09

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_pipeline_events"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create pipeline_events table
    op.create_table(
        "pipeline_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "idempotency_key",
            sa.String(512),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("ci_provider", sa.String(50), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("pipeline_id", sa.String(255), nullable=False, index=True),
        sa.Column("repo", sa.String(255), nullable=False, index=True),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False),
        sa.Column("stage", sa.String(255), nullable=False),
        sa.Column("failure_type", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, default="pending"),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Create composite indexes for common query patterns
    op.create_index(
        "ix_pipeline_events_repo_created",
        "pipeline_events",
        ["repo", "created_at"],
    )
    op.create_index(
        "ix_pipeline_events_status_created",
        "pipeline_events",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_pipeline_events_failure_type",
        "pipeline_events",
        ["failure_type"],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_pipeline_events_failure_type", table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_status_created", table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_repo_created", table_name="pipeline_events")

    # Drop table
    op.drop_table("pipeline_events")
