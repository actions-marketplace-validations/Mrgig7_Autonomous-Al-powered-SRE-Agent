"""Schemas for Pull Request operations."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class PRStatus(str, Enum):
    """Status of a Pull Request."""

    PENDING = "pending"
    CREATED = "created"
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"
    FAILED = "failed"


class PRResult(BaseModel):
    """Result of PR creation."""

    # PR identification
    pr_number: int | None = Field(None, description="GitHub PR number")
    pr_url: str | None = Field(None, description="PR URL")
    status: PRStatus = Field(..., description="PR status")

    # Branch info
    branch_name: str = Field(..., description="Fix branch name")
    base_branch: str = Field(..., description="Target branch")

    # Fix info
    fix_id: str = Field(..., description="Associated fix ID")
    event_id: UUID = Field(..., description="Original event ID")

    # Metadata
    title: str | None = Field(None, description="PR title")
    error_message: str | None = Field(None, description="Error if failed")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When PR was created",
    )


class PRRequest(BaseModel):
    """Request to create a PR."""

    fix_id: str = Field(..., description="Fix to create PR for")
    event_id: UUID = Field(..., description="Original event")
    repo: str = Field(..., description="Repository (owner/repo)")
    base_branch: str = Field("main", description="Target branch")
    diff: str = Field(..., description="Unified diff to apply")
    title: str | None = Field(None, description="Custom PR title")
    description: str | None = Field(None, description="Custom PR body")
    labels: list[str] | None = Field(None, description="Labels to apply to the PR")

    # RCA context for PR body
    error_type: str | None = None
    hypothesis: str | None = None
    confidence: float | None = None
    affected_files: list[str] = Field(default_factory=list)

    # Validation context
    tests_passed: int = 0
    tests_failed: int = 0
    validation_status: str | None = None
    risk_score: int | None = None
    evidence_lines: list[str] = Field(default_factory=list)
    policy_summary: str | None = None
    sandbox_summary: str | None = None
    provenance_artifact_url: str | None = None


class RollbackRequest(BaseModel):
    """Request to rollback a merged PR."""

    repo: str = Field(..., description="Repository")
    pr_number: int = Field(..., description="PR number to rollback")
    reason: str = Field(..., description="Reason for rollback")


class RollbackResult(BaseModel):
    """Result of a rollback operation."""

    success: bool
    revert_pr_number: int | None = None
    revert_pr_url: str | None = None
    error_message: str | None = None
