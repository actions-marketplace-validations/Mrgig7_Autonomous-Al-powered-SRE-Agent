"""Schemas for failure context bundles.

These schemas represent the aggregated observability data
that will be used by the Failure Intelligence Layer for RCA.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LogLanguage(str, Enum):
    """Programming languages for stack trace detection."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    RUBY = "ruby"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    """Error severity levels."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class StackFrame(BaseModel):
    """A single frame in a stack trace."""

    file: str = Field(..., description="Source file path")
    line: int | None = Field(None, description="Line number")
    column: int | None = Field(None, description="Column number")
    function: str | None = Field(None, description="Function/method name")
    code: str | None = Field(None, description="Source code snippet if available")
    module: str | None = Field(None, description="Module/package name")


class StackTrace(BaseModel):
    """A parsed stack trace from logs."""

    language: LogLanguage = Field(..., description="Detected programming language")
    exception_type: str = Field(..., description="Exception class/type name")
    message: str = Field(..., description="Error message")
    frames: list[StackFrame] = Field(default_factory=list, description="Stack frames")
    raw_text: str = Field(..., description="Original stack trace text")
    is_root_cause: bool = Field(
        False,
        description="True if this appears to be the root cause (first/innermost exception)",
    )


class ErrorInfo(BaseModel):
    """An error extracted from logs."""

    error_type: str = Field(..., description="Type/category of error")
    message: str = Field(..., description="Error message")
    location: str | None = Field(None, description="File:line if available")
    severity: Severity = Field(Severity.ERROR, description="Severity level")
    timestamp: datetime | None = Field(None, description="When the error occurred")
    context_lines: list[str] = Field(
        default_factory=list,
        description="Surrounding log lines for context",
    )


class TestFailure(BaseModel):
    """A test failure extracted from logs."""

    test_name: str = Field(..., description="Full test name/path")
    test_file: str | None = Field(None, description="Test file path")
    test_class: str | None = Field(None, description="Test class if applicable")
    error_message: str = Field(..., description="Failure reason")
    assertion: str | None = Field(None, description="Failed assertion if available")
    expected: str | None = Field(None, description="Expected value")
    actual: str | None = Field(None, description="Actual value")
    stack_trace: StackTrace | None = Field(None, description="Associated stack trace")
    duration_seconds: float | None = Field(None, description="Test duration")


class BuildError(BaseModel):
    """A build/compilation error."""

    file: str = Field(..., description="Source file with error")
    line: int | None = Field(None, description="Line number")
    column: int | None = Field(None, description="Column number")
    error_code: str | None = Field(None, description="Compiler error code")
    message: str = Field(..., description="Error message")
    severity: Severity = Field(Severity.ERROR)


class StepTiming(BaseModel):
    """Timing information for a workflow step."""

    name: str = Field(..., description="Step name")
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    conclusion: str | None = Field(None, description="success/failure/skipped")


class ChangedFile(BaseModel):
    """A file changed in the commit."""

    filename: str
    status: Literal["added", "modified", "deleted", "renamed"]
    additions: int = 0
    deletions: int = 0
    patch: str | None = Field(None, description="Diff patch if available")


class LogContent(BaseModel):
    """Container for log content with metadata."""

    raw_content: str = Field(..., description="Full log content")
    truncated: bool = Field(False, description="True if logs were truncated")
    size_bytes: int = Field(..., description="Original size in bytes")
    job_name: str | None = Field(None, description="Job this log belongs to")


class FailureContextBundle(BaseModel):
    """
    Complete failure context for RCA.

    This is the primary output of the Observability Context Builder,
    aggregating all relevant data for the Failure Intelligence Layer.
    """

    # Event identification
    event_id: UUID = Field(..., description="Associated pipeline event ID")
    repo: str = Field(..., description="Repository (owner/repo)")
    commit_sha: str = Field(..., description="Commit SHA")
    branch: str = Field(..., description="Branch name")
    pipeline_id: str = Field(..., description="Pipeline/run ID")
    job_name: str = Field(..., description="Failed job name")

    # Log data
    log_content: LogContent | None = Field(None, description="Raw log content")
    log_summary: str | None = Field(
        None,
        description="Summary of logs (first/last N lines)",
    )

    # Extracted errors
    errors: list[ErrorInfo] = Field(
        default_factory=list,
        description="All extracted errors",
    )
    stack_traces: list[StackTrace] = Field(
        default_factory=list,
        description="All extracted stack traces",
    )
    test_failures: list[TestFailure] = Field(
        default_factory=list,
        description="All test failures",
    )
    build_errors: list[BuildError] = Field(
        default_factory=list,
        description="Build/compilation errors",
    )

    # Git context
    changed_files: list[ChangedFile] = Field(
        default_factory=list,
        description="Files changed in the commit",
    )
    commit_message: str | None = Field(None, description="Commit message")
    commit_author: str | None = Field(None, description="Commit author")

    # Timing
    execution_time_seconds: float | None = Field(
        None,
        description="Total execution time",
    )
    step_timings: list[StepTiming] = Field(
        default_factory=list,
        description="Per-step timing breakdown",
    )

    # Metadata
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this bundle was created",
    )
    context_version: str = Field("1.0", description="Schema version")

    @property
    def has_stack_traces(self) -> bool:
        """Check if any stack traces were extracted."""
        return len(self.stack_traces) > 0

    @property
    def has_test_failures(self) -> bool:
        """Check if this is a test failure."""
        return len(self.test_failures) > 0

    @property
    def primary_error(self) -> ErrorInfo | None:
        """Get the most likely root cause error."""
        if self.errors:
            return self.errors[0]
        return None

    @property
    def primary_stack_trace(self) -> StackTrace | None:
        """Get the most relevant stack trace."""
        # Prefer root cause traces
        for trace in self.stack_traces:
            if trace.is_root_cause:
                return trace
        # Otherwise return first
        if self.stack_traces:
            return self.stack_traces[0]
        return None
