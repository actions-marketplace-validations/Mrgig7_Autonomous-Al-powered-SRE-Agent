"""Schemas for the plan critic stage."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CriticIssue(BaseModel):
    """A single critic finding."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str = Field(default="warn")
    message: str
    evidence_refs: list[str] = Field(default_factory=list)


class CriticDecision(BaseModel):
    """Structured critic decision for FixPlan review."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    hallucination_risk: float = Field(ge=0.0, le=1.0, default=0.0)
    reasoning_consistency: float = Field(ge=0.0, le=1.0, default=1.0)
    issues: list[CriticIssue] = Field(default_factory=list)
    requires_manual_review: bool = False
    recommended_label: str = "needs-review"


class CriticParseError(Exception):
    """Raised when critic output cannot be parsed/validated."""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output
