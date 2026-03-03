from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from sre_agent.safety.policy_models import DangerReason, PolicyDecision, PolicyViolation
from sre_agent.schemas.scans import ScanSummary


class EvidenceLineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idx: int
    line: str
    tag: str
    operation_idx: int | None = None


class ConfidenceFactorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: str
    value: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    note: str


class ExplainSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = None
    root_cause: str | None = None
    adapter: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    confidence_breakdown: list[ConfidenceFactorOut] = Field(default_factory=list)


class ProposedFixOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any] | None = None
    files: list[str] = Field(default_factory=list)
    diff_available: bool = False


class ExplainSafetyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    danger_score: int | None = None
    danger_breakdown: list[DangerReason] = Field(default_factory=list)
    violations: list[PolicyViolation] = Field(default_factory=list)
    patch_policy: PolicyDecision | None = None


class ExplainValidationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox: str
    tests: str
    lint: str
    scans: ScanSummary | None = None


class ExplainRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class TimelineStepOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_ms: int | None = None


class FailureExplainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_id: str
    repo: str
    summary: ExplainSummaryOut
    evidence: list[EvidenceLineOut] = Field(default_factory=list)
    proposed_fix: ProposedFixOut
    safety: ExplainSafetyOut
    validation: ExplainValidationOut
    run: ExplainRunOut
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str


class RunDiffResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    diff_text: str
    stats: dict[str, Any] | None = None
    redacted: bool = True


class RunTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    timeline: list[TimelineStepOut] = Field(default_factory=list)
