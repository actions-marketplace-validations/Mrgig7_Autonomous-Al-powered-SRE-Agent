"""Phase 1 onboarding installation persistence

Revision ID: 007_phase1_onboarding_installations
Revises: 006_phase7_reliability
Create Date: 2026-02-17
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "007_phase1_onboarding_installations"
down_revision: Union[str, None] = "006_phase7_reliability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "github_app_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "automation_mode",
            sa.String(length=32),
            nullable=False,
            server_default="suggest",
        ),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ux_github_app_installations_user_repo",
        "github_app_installations",
        ["user_id", "repo_id"],
        unique=True,
    )
    op.create_index(
        "ux_github_app_installations_installation_id",
        "github_app_installations",
        ["installation_id"],
        unique=True,
    )
    op.create_index(
        "ix_github_app_installations_repo_full_name",
        "github_app_installations",
        ["repo_full_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_app_installations_repo_full_name", table_name="github_app_installations"
    )
    op.drop_index(
        "ux_github_app_installations_installation_id", table_name="github_app_installations"
    )
    op.drop_index("ux_github_app_installations_user_repo", table_name="github_app_installations")
    op.drop_table("github_app_installations")
