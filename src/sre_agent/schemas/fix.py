"""Schemas for AI-generated fix suggestions."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class GuardrailSeverity(str, Enum):
    """Severity level for guardrail violations."""

    BLOCK = "block"  # Fix cannot be applied
    WARN = "warn"  # Proceed with caution
    INFO = "info"  # Informational


class GuardrailViolation(BaseModel):
    """A single guardrail violation."""

    rule: str = Field(..., description="Name of the violated rule")
    severity: GuardrailSeverity = Field(..., description="Severity level")
    message: str = Field(..., description="Description of the violation")
    location: str | None = Field(None, description="Where in the diff")


class GuardrailStatus(BaseModel):
    """Result of guardrail validation."""

    passed: bool = Field(..., description="True if fix passed all blocking rules")
    violations: list[GuardrailViolation] = Field(
        default_factory=list,
        description="All violations found",
    )

    @property
    def blocking_violations(self) -> list[GuardrailViolation]:
        """Get only blocking violations."""
        return [v for v in self.violations if v.severity == GuardrailSeverity.BLOCK]

    @property
    def warnings(self) -> list[str]:
        """Get warning messages."""
        return [v.message for v in self.violations if v.severity == GuardrailSeverity.WARN]


class SafetyViolation(BaseModel):
    code: str
    severity: str
    message: str
    file_path: str | None = None


class SafetyStatus(BaseModel):
    allowed: bool
    pr_label: str
    danger_score: int
    violations: list[SafetyViolation] = Field(default_factory=list)
    danger_reasons: list[str] = Field(default_factory=list)


class FileDiff(BaseModel):
    """A diff for a single file."""

    filename: str = Field(..., description="Target file path")
    original_content: str | None = Field(None, description="Original file content")
    diff: str = Field(..., description="Unified diff for this file")
    lines_added: int = Field(0, description="Number of lines added")
    lines_removed: int = Field(0, description="Number of lines removed")


class FixSuggestion(BaseModel):
    """AI-generated fix suggestion."""

    # Identification
    event_id: UUID = Field(..., description="Associated pipeline event")
    fix_id: str = Field(..., description="Unique fix identifier")

    # Diff content
    diffs: list[FileDiff] = Field(
        default_factory=list,
        description="Diffs for affected files",
    )

    # Explanation
    explanation: str = Field(..., description="Why this fix should work")
    summary: str = Field(..., description="One-line summary of the fix")

    # Metadata
    target_files: list[str] = Field(
        default_factory=list,
        description="Files affected by this fix",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in this fix",
    )

    # Stats
    total_lines_added: int = Field(0)
    total_lines_removed: int = Field(0)

    # Safety
    guardrail_status: GuardrailStatus = Field(
        ...,
        description="Result of guardrail validation",
    )
    safety_status: SafetyStatus | None = Field(
        default=None,
        description="Result of safety policy validation",
    )

    # Model info
    model_used: str = Field(..., description="LLM model used")
    prompt_tokens: int | None = Field(None, description="Tokens in prompt")
    completion_tokens: int | None = Field(None, description="Tokens in response")

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the fix was generated",
    )

    @property
    def is_safe_to_apply(self) -> bool:
        """Check if fix passed guardrails and is safe to apply."""
        return self.guardrail_status.passed and (
            self.safety_status.allowed if self.safety_status else True
        )

    @property
    def full_diff(self) -> str:
        """Get combined diff for all files."""
        return "\n".join(d.diff for d in self.diffs)


class FixGenerationRequest(BaseModel):
    """Request to generate a fix."""

    event_id: UUID
    rca_result: dict  # Serialized RCAResult
    file_contents: dict[str, str] = Field(
        default_factory=dict,
        description="Map of filename to content for context",
    )
    max_files: int = Field(3, description="Maximum files to modify")
    max_tokens: int = Field(2000, description="Maximum LLM tokens")


class FixGenerationResponse(BaseModel):
    """Response from fix generation."""

    success: bool
    fix: FixSuggestion | None = None
    error: str | None = None
    generation_time_seconds: float | None = None
