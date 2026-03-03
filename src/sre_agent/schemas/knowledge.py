"""Schemas for knowledge and learning store."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    """Status of a stored incident."""

    PENDING = "pending"  # Fix generated but not validated
    VALIDATED = "validated"  # Fix passed validation
    PR_CREATED = "pr_created"  # PR created
    MERGED = "merged"  # PR merged successfully
    FAILED = "failed"  # Fix or PR failed
    ROLLED_BACK = "rolled_back"  # Fix was rolled back


class FixPattern(BaseModel):
    """A learned fix pattern."""

    pattern_id: str = Field(..., description="Pattern identifier")
    category: str = Field(..., description="Failure category")
    description: str = Field(..., description="Pattern description")
    example_diff: str | None = Field(None, description="Example fix diff")
    success_count: int = Field(0, description="Times this pattern succeeded")
    total_count: int = Field(0, description="Total times applied")

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_count == 0:
            return 0.0
        return self.success_count / self.total_count


class IncidentRecord(BaseModel):
    """A stored incident record."""

    # Identification
    id: UUID = Field(..., description="Incident ID")
    event_id: UUID = Field(..., description="Original pipeline event")
    status: IncidentStatus = Field(..., description="Current status")

    # Classification
    category: str = Field(..., description="Failure category")
    confidence: float = Field(..., description="Classification confidence")
    hypothesis: str = Field(..., description="Root cause hypothesis")

    # Error info
    error_type: str | None = Field(None, description="Error type/exception")
    error_message: str | None = Field(None, description="Error message")
    affected_files: list[str] = Field(default_factory=list)

    # Fix info
    fix_id: str | None = Field(None, description="Fix ID")
    fix_summary: str | None = Field(None, description="Fix summary")
    fix_diff: str | None = Field(None, description="Applied diff")

    # Validation
    validation_passed: bool | None = None
    tests_passed: int = 0
    tests_failed: int = 0

    # PR info
    pr_number: int | None = None
    pr_url: str | None = None
    pr_merged: bool = False

    # Resolution
    resolution: str | None = Field(None, description="How it was resolved")
    resolved_by: str | None = Field(None, description="Auto or manual")

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None

    # Embedding (for storage reference)
    embedding_id: str | None = None

    @property
    def is_resolved(self) -> bool:
        """Check if incident is resolved."""
        return self.status in (
            IncidentStatus.MERGED,
            IncidentStatus.FAILED,
            IncidentStatus.ROLLED_BACK,
        )

    @property
    def was_successful(self) -> bool:
        """Check if fix was successful."""
        return self.status == IncidentStatus.MERGED and self.pr_merged


class IncidentQuery(BaseModel):
    """Query for searching incidents."""

    category: str | None = None
    status: IncidentStatus | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    limit: int = 100
    offset: int = 0


class CategoryStats(BaseModel):
    """Statistics for a failure category."""

    category: str
    total_incidents: int
    successful_fixes: int
    failed_fixes: int
    avg_confidence: float
    avg_resolution_time_hours: float | None

    @property
    def success_rate(self) -> float:
        if self.total_incidents == 0:
            return 0.0
        return self.successful_fixes / self.total_incidents
