from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sre_agent.models.events import Base


class FixPipelineRunStatus(str, Enum):
    CREATED = "created"
    PLAN_BLOCKED = "plan_blocked"
    PLAN_READY = "plan_ready"
    PATCH_BLOCKED = "patch_blocked"
    PATCH_READY = "patch_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_PASSED = "validation_passed"
    PR_FAILED = "pr_failed"
    PR_CREATED = "pr_created"
    MERGED = "merged"
    MERGE_FAILED = "merge_failed"
    MONITORING = "monitoring"
    ESCALATED = "escalated"


class FixPipelineRun(Base):
    __tablename__ = "fix_pipeline_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("pipeline_events.id", ondelete="CASCADE"))

    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=FixPipelineRunStatus.CREATED.value
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    context_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    rca_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    plan_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    plan_policy_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    patch_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    patch_stats_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    patch_policy_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    validation_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    pr_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    adapter_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detection_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    critic_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    issue_graph_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    consensus_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    consensus_shadow_diff_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    consensus_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merge_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    post_merge_monitor_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    artifact_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sbom_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sbom_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sbom_size_bytes: Mapped[int | None] = mapped_column(nullable=True)

    run_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    retry_limit_snapshot: Mapped[int] = mapped_column(nullable=False, default=3)
    automation_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="auto_pr")
    manual_review_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_pr_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("idx_fix_pipeline_runs_event_id", "event_id"),
        Index("idx_fix_pipeline_runs_status", "status"),
        Index("uq_fix_pipeline_runs_event_id", "event_id", unique=True),
        Index("uq_fix_pipeline_runs_run_key", "run_key", unique=True),
    )
