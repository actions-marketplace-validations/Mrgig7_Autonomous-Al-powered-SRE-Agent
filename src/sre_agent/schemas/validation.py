"""Schemas for sandbox validation results."""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from sre_agent.adapters.base import ValidationStep
from sre_agent.schemas.scans import ScanSummary


class ValidationStatus(str, Enum):
    """Status of a validation run."""

    PENDING = "pending"
    CLONING = "cloning"
    PATCHING = "patching"
    INSTALLING = "installing"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class TestFramework(str, Enum):
    """Supported test frameworks."""

    PYTEST = "pytest"
    JEST = "jest"
    MOCHA = "mocha"
    GO_TEST = "go_test"
    MAVEN = "maven"
    GRADLE = "gradle"
    CARGO = "cargo"
    RSPEC = "rspec"
    UNKNOWN = "unknown"


class TestResult(BaseModel):
    """Result of a single test."""

    name: str = Field(..., description="Test name/path")
    status: str = Field(..., description="passed/failed/skipped/error")
    duration_seconds: float | None = None
    error_message: str | None = None
    stack_trace: str | None = None


class CommandResult(BaseModel):
    """Result of a command execution."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


class PatchResult(BaseModel):
    """Result of applying a patch."""

    success: bool
    files_modified: list[str] = Field(default_factory=list)
    hunks_applied: int = 0
    hunks_failed: int = 0
    error_message: str | None = None


class SandboxConfig(BaseModel):
    """Configuration for sandbox execution."""

    docker_image: str = "sre-agent-sandbox:scanners-2026-01-20"
    timeout_seconds: int = 300
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = False
    env_vars: dict[str, str] = Field(default_factory=dict)
    working_dir: str = "/workspace"


class ValidationRequest(BaseModel):
    """Request to validate a fix."""

    fix_id: str = Field(..., description="Fix to validate")
    event_id: UUID = Field(..., description="Original event")
    repo_url: str = Field(..., description="Repository URL")
    branch: str = Field(..., description="Branch name")
    commit_sha: str = Field(..., description="Commit to base on")
    diff: str = Field(..., description="Unified diff to apply")
    test_filter: str | None = Field(
        None,
        description="Test filter (e.g., specific test file)",
    )
    config: SandboxConfig = Field(
        default_factory=SandboxConfig,
        description="Sandbox configuration",
    )
    adapter_name: str | None = Field(
        None,
        description="Selected adapter name (optional)",
    )
    validation_steps: list[ValidationStep] | None = Field(
        None,
        description="Explicit validation steps to run in the sandbox (optional)",
    )


class ValidationResult(BaseModel):
    """Result of fix validation."""

    # Identification
    fix_id: str = Field(..., description="Validated fix ID")
    event_id: UUID = Field(..., description="Original event")
    validation_id: str = Field(..., description="Unique validation run ID")

    # Status
    status: ValidationStatus = Field(..., description="Validation status")

    # Test results
    tests_passed: int = Field(0, description="Number of tests passed")
    tests_failed: int = Field(0, description="Number of tests failed")
    tests_skipped: int = Field(0, description="Number of tests skipped")
    tests_total: int = Field(0, description="Total tests run")
    test_results: list[TestResult] = Field(
        default_factory=list,
        description="Individual test results",
    )

    # Execution info
    execution_time_seconds: float = Field(0.0, description="Total execution time")
    steps_completed: list[str] = Field(
        default_factory=list,
        description="Completed validation steps",
    )

    # Output
    logs: str = Field("", description="Execution logs")
    error_message: str | None = Field(None, description="Error if failed")

    # Metadata
    framework_detected: TestFramework = Field(
        TestFramework.UNKNOWN,
        description="Detected test framework",
    )
    docker_image: str | None = Field(None, description="Docker image used")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When validation started",
    )
    completed_at: datetime | None = Field(
        None,
        description="When validation completed",
    )
    scans: ScanSummary | None = Field(
        None,
        description="Supply-chain scan summaries (redacted)",
    )

    @property
    def is_successful(self) -> bool:
        """Check if validation passed."""
        return self.status == ValidationStatus.PASSED

    @property
    def pass_rate(self) -> float:
        """Calculate test pass rate."""
        if self.tests_total == 0:
            return 0.0
        return self.tests_passed / self.tests_total

    @property
    def all_tests_passed(self) -> bool:
        """Check if all tests passed."""
        return self.tests_failed == 0 and self.tests_passed > 0
