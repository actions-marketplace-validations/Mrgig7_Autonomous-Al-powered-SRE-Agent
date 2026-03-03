"""Safety guardrails for AI-generated fixes.

Validates generated fixes before they can be applied.
"""

import logging
import re
from dataclasses import dataclass

from sre_agent.schemas.fix import (
    FileDiff,
    FixSuggestion,
    GuardrailSeverity,
    GuardrailStatus,
    GuardrailViolation,
)

logger = logging.getLogger(__name__)


@dataclass
class GuardrailConfig:
    """Configuration for guardrail rules."""

    max_files: int = 3
    max_lines_changed: int = 50
    warn_lines_threshold: int = 20
    block_patterns: list[str] | None = None
    allow_delete_files: bool = False


class FixGuardrails:
    """
    Safety guardrails for AI-generated fixes.

    Validates fixes against a set of rules to ensure they are safe to apply:
    - File scope limits
    - Change size limits
    - No secret patterns
    - No destructive commands
    - Valid diff syntax
    """

    # Secret patterns to block
    SECRET_PATTERNS = [
        r"(?i)password\s*[=:]\s*['\"][^'\"]+['\"]",
        r"(?i)api_key\s*[=:]\s*['\"][^'\"]+['\"]",
        r"(?i)secret\s*[=:]\s*['\"][^'\"]+['\"]",
        r"(?i)token\s*[=:]\s*['\"][^'\"]+['\"]",
        r"(?i)aws_access_key_id\s*[=:]",
        r"(?i)aws_secret_access_key\s*[=:]",
        r"ghp_[a-zA-Z0-9]{36}",  # GitHub token
        r"sk-[a-zA-Z0-9]{48}",  # OpenAI key
        r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----",
    ]

    # Destructive patterns to block
    DESTRUCTIVE_PATTERNS = [
        r"\brm\s+-rf?\s+[/~]",
        r"\brmdir\s+[/~]",
        r"DROP\s+DATABASE",
        r"DROP\s+TABLE",
        r"DELETE\s+FROM\s+\w+\s*;?\s*$",  # DELETE without WHERE
        r"TRUNCATE\s+TABLE",
        r"\bformat\s+[a-z]:",
        r"(?i)os\.remove\s*\(",
        r"(?i)shutil\.rmtree\s*\(",
    ]

    def __init__(self, config: GuardrailConfig | None = None):
        """
        Initialize guardrails.

        Args:
            config: Optional configuration overrides
        """
        self.config = config or GuardrailConfig()

        # Compile patterns
        self._secret_patterns = [re.compile(p) for p in self.SECRET_PATTERNS]
        self._destructive_patterns = [re.compile(p) for p in self.DESTRUCTIVE_PATTERNS]

        # Add any custom block patterns
        if self.config.block_patterns:
            self._custom_patterns = [re.compile(p) for p in self.config.block_patterns]
        else:
            self._custom_patterns = []

    def validate(self, fix: FixSuggestion) -> GuardrailStatus:
        """
        Validate a fix against all guardrail rules.

        Args:
            fix: Fix suggestion to validate

        Returns:
            GuardrailStatus with pass/fail and any violations
        """
        violations: list[GuardrailViolation] = []

        # Run all checks
        violations.extend(self._check_file_scope(fix))
        violations.extend(self._check_change_size(fix))
        violations.extend(self._check_no_secrets(fix))
        violations.extend(self._check_no_destructive(fix))
        violations.extend(self._check_diff_syntax(fix))

        # Determine pass/fail
        has_blocking = any(v.severity == GuardrailSeverity.BLOCK for v in violations)

        logger.info(
            "Guardrail validation complete",
            extra={
                "passed": not has_blocking,
                "violations": len(violations),
                "blocking": sum(1 for v in violations if v.severity == GuardrailSeverity.BLOCK),
            },
        )

        return GuardrailStatus(
            passed=not has_blocking,
            violations=violations,
        )

    def _check_file_scope(self, fix: FixSuggestion) -> list[GuardrailViolation]:
        """Check that fix doesn't affect too many files."""
        violations = []

        if len(fix.target_files) > self.config.max_files:
            violations.append(
                GuardrailViolation(
                    rule="file_scope",
                    severity=GuardrailSeverity.BLOCK,
                    message=(
                        f"Fix affects {len(fix.target_files)} files, "
                        f"max allowed is {self.config.max_files}"
                    ),
                )
            )

        # Check for file deletions
        if not self.config.allow_delete_files:
            for diff in fix.diffs:
                if self._is_file_deletion(diff):
                    violations.append(
                        GuardrailViolation(
                            rule="file_deletion",
                            severity=GuardrailSeverity.BLOCK,
                            message=f"Fix deletes file {diff.filename}",
                            location=diff.filename,
                        )
                    )

        return violations

    def _check_change_size(self, fix: FixSuggestion) -> list[GuardrailViolation]:
        """Check that changes aren't too large."""
        violations = []

        total_changes = fix.total_lines_added + fix.total_lines_removed

        if total_changes > self.config.max_lines_changed:
            violations.append(
                GuardrailViolation(
                    rule="change_size",
                    severity=GuardrailSeverity.BLOCK,
                    message=(
                        f"Fix changes {total_changes} lines, "
                        f"max allowed is {self.config.max_lines_changed}"
                    ),
                )
            )
        elif total_changes > self.config.warn_lines_threshold:
            violations.append(
                GuardrailViolation(
                    rule="change_size",
                    severity=GuardrailSeverity.WARN,
                    message=f"Fix changes {total_changes} lines (threshold: {self.config.warn_lines_threshold})",
                )
            )

        return violations

    def _check_no_secrets(self, fix: FixSuggestion) -> list[GuardrailViolation]:
        """Check that fix doesn't introduce secrets."""
        violations = []

        full_diff = fix.full_diff

        for pattern in self._secret_patterns:
            matches = pattern.findall(full_diff)
            if matches:
                violations.append(
                    GuardrailViolation(
                        rule="no_secrets",
                        severity=GuardrailSeverity.BLOCK,
                        message="Fix contains potential secret or credential",
                    )
                )
                break  # One violation is enough

        return violations

    def _check_no_destructive(self, fix: FixSuggestion) -> list[GuardrailViolation]:
        """Check that fix doesn't contain destructive commands."""
        violations = []

        full_diff = fix.full_diff

        for pattern in self._destructive_patterns:
            if pattern.search(full_diff):
                violations.append(
                    GuardrailViolation(
                        rule="no_destructive",
                        severity=GuardrailSeverity.BLOCK,
                        message="Fix contains potentially destructive command",
                    )
                )
                break

        # Check custom patterns
        for pattern in self._custom_patterns:
            if pattern.search(full_diff):
                violations.append(
                    GuardrailViolation(
                        rule="custom_block",
                        severity=GuardrailSeverity.BLOCK,
                        message="Fix matches blocked pattern",
                    )
                )
                break

        return violations

    def _check_diff_syntax(self, fix: FixSuggestion) -> list[GuardrailViolation]:
        """Check that diff syntax is valid."""
        violations = []

        for diff in fix.diffs:
            if not self._is_valid_diff(diff.diff):
                violations.append(
                    GuardrailViolation(
                        rule="diff_syntax",
                        severity=GuardrailSeverity.BLOCK,
                        message=f"Invalid diff syntax for {diff.filename}",
                        location=diff.filename,
                    )
                )

        return violations

    def _is_valid_diff(self, diff_text: str) -> bool:
        """Check if diff text has valid unified diff format."""
        if not diff_text.strip():
            return False

        lines = diff_text.strip().split("\n")

        # Should have --- and +++ headers
        has_old_header = any(line.startswith("---") for line in lines[:5])
        has_new_header = any(line.startswith("+++") for line in lines[:5])

        if not (has_old_header and has_new_header):
            return False

        # Should have at least one hunk header
        has_hunk = any(line.startswith("@@") for line in lines)

        return has_hunk

    def _is_file_deletion(self, diff: FileDiff) -> bool:
        """Check if diff represents a file deletion."""
        # Check for /dev/null as the target
        if "+++ /dev/null" in diff.diff or "+++ b/dev/null" in diff.diff:
            return True

        # Check if all non-header lines are removals
        lines = diff.diff.strip().split("\n")
        content_lines = [line for line in lines if not line.startswith(("---", "+++", "@@"))]

        if content_lines and all(line.startswith("-") for line in content_lines if line.strip()):
            return True

        return False
