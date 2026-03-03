from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PolicySeverity(str, Enum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


class PolicyViolation(BaseModel):
    code: str
    severity: PolicySeverity
    message: str
    file_path: str | None = None


class DangerReason(BaseModel):
    code: str
    weight: int
    message: str


class PatchLimits(BaseModel):
    max_files: int = 5
    max_lines_added: int = 200
    max_lines_removed: int = 200
    max_diff_bytes: int = 200_000


class PathPolicy(BaseModel):
    allowed: list[str] = Field(default_factory=lambda: ["**"])
    forbidden: list[str] = Field(
        default_factory=lambda: [
            ".git/**",
            ".github/workflows/**",
            ".github/actions/**",
            ".env",
            ".env.*",
            "**/*.pem",
            "**/*.key",
        ]
    )


class SecretPolicy(BaseModel):
    forbidden_patterns: list[str] = Field(
        default_factory=lambda: [
            r"(?i)password\s*[=:]\s*['\"][^'\"]+['\"]",
            r"(?i)api[_-]?key\s*[=:]\s*['\"][^'\"]+['\"]",
            r"(?i)secret\s*[=:]\s*['\"][^'\"]+['\"]",
            r"(?i)token\s*[=:]\s*['\"][^'\"]+['\"]",
            r"(?i)aws_access_key_id\s*[=:]",
            r"(?i)aws_secret_access_key\s*[=:]",
            r"ghp_[a-zA-Z0-9]{36}",
            r"sk-[a-zA-Z0-9]{48}",
            r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----",
        ]
    )


class RiskyPathRule(BaseModel):
    glob: str
    weight: int
    message: str


class DangerPolicy(BaseModel):
    safe_max: int = 20
    weights: dict[str, int] = Field(
        default_factory=lambda: {
            "per_file": 5,
            "per_50_lines_changed": 5,
            "per_10kb_diff": 3,
        }
    )
    risky_paths: list[RiskyPathRule] = Field(
        default_factory=lambda: [
            RiskyPathRule(glob="Dockerfile", weight=25, message="Touches Dockerfile"),
            RiskyPathRule(
                glob="docker-compose.yml", weight=25, message="Touches docker-compose.yml"
            ),
            RiskyPathRule(glob=".github/**", weight=30, message="Touches GitHub configuration"),
            RiskyPathRule(glob="**/infra/**", weight=30, message="Touches infra directory"),
        ]
    )


class SafetyPolicy(BaseModel):
    version: int = 1
    paths: PathPolicy = Field(default_factory=PathPolicy)
    secrets: SecretPolicy = Field(default_factory=SecretPolicy)
    patch_limits: PatchLimits = Field(default_factory=PatchLimits)
    danger: DangerPolicy = Field(default_factory=DangerPolicy)


class PlanIntent(BaseModel):
    target_files: list[str] = Field(default_factory=list)
    category: str | None = None
    operation_types: list[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    allowed: bool
    violations: list[PolicyViolation] = Field(default_factory=list)
    danger_score: int = 0
    danger_reasons: list[DangerReason] = Field(default_factory=list)
    pr_label: str = "needs-review"

    @property
    def blocking_violations(self) -> list[PolicyViolation]:
        return [v for v in self.violations if v.severity == PolicySeverity.BLOCK]
