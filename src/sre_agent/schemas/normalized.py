"""Normalized event schemas - provider-agnostic representations.

These schemas represent the canonical format for pipeline events
after normalization from various CI/CD providers.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FailureType(str, Enum):
    """Classification of CI/CD failure types."""

    BUILD = "build"
    TEST = "test"
    DEPLOY = "deploy"
    TIMEOUT = "timeout"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class CIProvider(str, Enum):
    """Supported CI/CD providers."""

    GITHUB_ACTIONS = "github_actions"
    GITLAB_CI = "gitlab_ci"
    JENKINS = "jenkins"


class NormalizedPipelineEvent(BaseModel):
    """
    Provider-agnostic pipeline event representation.

    This is the canonical format that flows through the entire system
    after normalization from the original CI provider webhook.
    """

    # Idempotency key for deduplication
    # Format: {ci_provider}:{repo}:{run_id}:{job_id}:{attempt}
    idempotency_key: str = Field(
        ...,
        description="Unique key for event deduplication",
        examples=["github_actions:org/repo:12345:67890:1"],
    )

    # Source identification
    ci_provider: CIProvider = Field(
        ...,
        description="The CI/CD provider that generated this event",
    )

    # Pipeline information
    pipeline_id: str = Field(
        ...,
        description="Unique identifier for the pipeline run",
        examples=["12345"],
    )

    repo: str = Field(
        ...,
        description="Repository in org/repo format",
        examples=["myorg/myservice"],
    )

    commit_sha: str = Field(
        ...,
        description="Full commit SHA",
        min_length=40,
        max_length=40,
    )

    branch: str = Field(
        ...,
        description="Branch name",
        examples=["main", "feature/new-api"],
    )

    stage: str = Field(
        ...,
        description="Job or stage name that failed",
        examples=["build", "test-unit", "deploy-staging"],
    )

    # Failure classification
    failure_type: FailureType = Field(
        ...,
        description="Classification of the failure",
    )

    # Optional error details
    error_message: str | None = Field(
        default=None,
        description="Error message or summary if available",
    )

    # Timing
    event_timestamp: datetime = Field(
        ...,
        description="When the event occurred (from CI provider)",
    )

    # Original payload for debugging and future processing
    raw_payload: dict[str, Any] = Field(
        ...,
        description="Original webhook payload",
    )

    # Correlation ID for distributed tracing
    correlation_id: str | None = Field(
        default=None,
        description="Correlation ID from the webhook delivery",
    )

    class Config:
        """Pydantic config."""

        use_enum_values = True


class StoredPipelineEvent(NormalizedPipelineEvent):
    """Pipeline event as stored in the database (with additional fields)."""

    id: UUID = Field(..., description="Database ID")
    status: Literal["pending", "dispatched", "processing", "completed", "failed"] = Field(
        "pending",
        description="Processing status",
    )
    created_at: datetime = Field(..., description="When the event was stored")
    updated_at: datetime | None = Field(None, description="Last update timestamp")


class EventResponse(BaseModel):
    """API response for event operations."""

    event_id: UUID
    idempotency_key: str
    status: str
    is_new: bool = Field(
        ...,
        description="True if this is a new event, False if duplicate",
    )
    message: str


class WebhookResponse(BaseModel):
    """Standard webhook response."""

    status: Literal["accepted", "ignored", "error", "duplicate_ignored", "throttled_delayed"]
    message: str
    event_id: UUID | None = None
    correlation_id: str | None = None
