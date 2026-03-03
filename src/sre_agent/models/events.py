"""SQLAlchemy models for pipeline events."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        UUID: PG_UUID(as_uuid=True),
    }


class EventStatus(str, Enum):
    """Status of a pipeline event in the processing pipeline."""

    PENDING = "pending"
    DISPATCHED = "dispatched"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureType(str, Enum):
    """Type of CI/CD failure."""

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


class PipelineEvent(Base):
    """
    Represents a normalized CI/CD pipeline failure event.

    This is the core entity that flows through the entire system,
    from ingestion to remediation.
    """

    __tablename__ = "pipeline_events"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Idempotency key for deduplication
    # Format: {ci_provider}:{repo}:{run_id}:{job_id}:{attempt}
    idempotency_key: Mapped[str] = mapped_column(
        String(512),
        unique=True,
        index=True,
        nullable=False,
    )

    # Source information
    ci_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Normalized pipeline information
    pipeline_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    repo: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    stage: Mapped[str] = mapped_column(String(255), nullable=False)
    failure_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Error details
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Processing status
    status: Mapped[str] = mapped_column(
        String(50),
        default=EventStatus.PENDING.value,
        nullable=False,
    )

    # Correlation ID for tracing
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Timestamps
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    # Indexes for common query patterns
    __table_args__ = (
        Index("ix_pipeline_events_repo_created", "repo", "created_at"),
        Index("ix_pipeline_events_status_created", "status", "created_at"),
        Index("ix_pipeline_events_failure_type", "failure_type"),
    )

    def __repr__(self) -> str:
        return (
            f"<PipelineEvent(id={self.id}, repo={self.repo}, "
            f"pipeline_id={self.pipeline_id}, status={self.status})>"
        )
