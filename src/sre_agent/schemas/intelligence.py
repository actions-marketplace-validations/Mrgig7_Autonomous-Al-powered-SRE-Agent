"""Schemas for failure intelligence and RCA results."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class FailureCategory(str, Enum):
    """Categories of CI/CD failures."""

    INFRASTRUCTURE = "infrastructure"  # CI system issues, resource exhaustion
    DEPENDENCY = "dependency"  # Package/version conflicts
    CODE = "code"  # Logic errors, type errors, bugs
    CONFIGURATION = "configuration"  # Missing env vars, bad config
    TEST = "test"  # Test-specific failures, assertions
    FLAKY = "flaky"  # Non-deterministic failures
    SECURITY = "security"  # Security scan failures
    UNKNOWN = "unknown"  # Unable to classify


class Classification(BaseModel):
    """Result of failure classification."""

    category: FailureCategory = Field(..., description="Failure category")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0 - 1.0)",
    )
    reasoning: str = Field(..., description="Explanation for classification")
    indicators: list[str] = Field(
        default_factory=list,
        description="Patterns/indicators that led to this classification",
    )
    secondary_category: FailureCategory | None = Field(
        None,
        description="Secondary category if applicable",
    )


class AffectedFile(BaseModel):
    """A file likely affected by or causing the failure."""

    filename: str = Field(..., description="File path")
    relevance_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="How likely this file is related to the failure",
    )
    reason: str = Field(..., description="Why this file is relevant")
    is_in_stack_trace: bool = Field(False, description="Appears in stack trace")
    is_recently_changed: bool = Field(False, description="Changed in this commit")
    suggested_action: str | None = Field(None, description="Suggested fix action")


class SimilarIncident(BaseModel):
    """A similar historical incident."""

    incident_id: str = Field(..., description="Historical incident ID")
    similarity_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="How similar (0.0 - 1.0)",
    )
    summary: str = Field(..., description="Brief summary of the incident")
    root_cause: str | None = Field(None, description="Known root cause")
    resolution: str | None = Field(None, description="How it was resolved")
    fix_diff: str | None = Field(None, description="Fix diff if available")
    occurred_at: datetime | None = Field(None, description="When it occurred")


class RCAHypothesis(BaseModel):
    """A root cause hypothesis."""

    description: str = Field(..., description="Description of the hypothesis")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in this hypothesis",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Evidence supporting this hypothesis",
    )
    suggested_fix: str | None = Field(None, description="Suggested fix approach")


class RCAResult(BaseModel):
    """Complete root cause analysis result."""

    # Event identification
    event_id: UUID = Field(..., description="Pipeline event ID")

    # Classification
    classification: Classification = Field(..., description="Failure classification")

    # Primary hypothesis
    primary_hypothesis: RCAHypothesis = Field(
        ...,
        description="Most likely root cause",
    )
    alternative_hypotheses: list[RCAHypothesis] = Field(
        default_factory=list,
        description="Alternative possible causes",
    )

    # File analysis
    affected_files: list[AffectedFile] = Field(
        default_factory=list,
        description="Files related to the failure",
    )

    # Historical analysis
    similar_incidents: list[SimilarIncident] = Field(
        default_factory=list,
        description="Similar historical incidents",
    )

    # Fix patterns
    suggested_patterns: list[str] = Field(
        default_factory=list,
        description="Suggested fix patterns based on history",
    )

    # Metadata
    analysis_time_seconds: float | None = Field(
        None,
        description="Time taken for analysis",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this analysis was created",
    )

    @property
    def is_high_confidence(self) -> bool:
        """Check if we have high confidence in the analysis."""
        return self.classification.confidence >= 0.8 and self.primary_hypothesis.confidence >= 0.7

    @property
    def has_historical_match(self) -> bool:
        """Check if we found similar historical incidents."""
        return len(self.similar_incidents) > 0 and any(
            i.similarity_score >= 0.8 for i in self.similar_incidents
        )
