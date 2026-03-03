from __future__ import annotations

import re
from fnmatch import fnmatch

from sre_agent.safety.danger_score import score_patch, score_plan_intent
from sre_agent.safety.diff_parser import parse_unified_diff
from sre_agent.safety.policy_models import (
    PlanIntent,
    PolicyDecision,
    PolicySeverity,
    PolicyViolation,
    SafetyPolicy,
)


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized = _normalize_path(path)
    return any(fnmatch(normalized, p) for p in patterns)


class PolicyEngine:
    def __init__(self, policy: SafetyPolicy):
        self.policy = policy
        self._secret_patterns = [re.compile(p) for p in policy.secrets.forbidden_patterns]

    def evaluate_plan(self, intent: PlanIntent) -> PolicyDecision:
        violations: list[PolicyViolation] = []

        for path in intent.target_files:
            if _matches_any(path, self.policy.paths.forbidden):
                violations.append(
                    PolicyViolation(
                        code="forbidden_path",
                        severity=PolicySeverity.BLOCK,
                        message="Target file is forbidden by policy",
                        file_path=_normalize_path(path),
                    )
                )
                continue

            if self.policy.paths.allowed and not _matches_any(path, self.policy.paths.allowed):
                violations.append(
                    PolicyViolation(
                        code="path_not_allowed",
                        severity=PolicySeverity.BLOCK,
                        message="Target file is not in the allowed path set",
                        file_path=_normalize_path(path),
                    )
                )

        danger_score, reasons = score_plan_intent(intent, self.policy.danger)
        allowed = not any(v.severity == PolicySeverity.BLOCK for v in violations)
        pr_label = (
            "safe" if allowed and danger_score <= self.policy.danger.safe_max else "needs-review"
        )

        return PolicyDecision(
            allowed=allowed,
            violations=violations,
            danger_score=danger_score,
            danger_reasons=reasons,
            pr_label=pr_label,
        )

    def evaluate_patch(self, diff_text: str) -> PolicyDecision:
        violations: list[PolicyViolation] = []

        parsed = parse_unified_diff(diff_text)

        for f in parsed.files:
            if _matches_any(f.path, self.policy.paths.forbidden):
                violations.append(
                    PolicyViolation(
                        code="forbidden_path",
                        severity=PolicySeverity.BLOCK,
                        message="Patch touches a forbidden path",
                        file_path=f.path,
                    )
                )
            elif self.policy.paths.allowed and not _matches_any(f.path, self.policy.paths.allowed):
                violations.append(
                    PolicyViolation(
                        code="path_not_allowed",
                        severity=PolicySeverity.BLOCK,
                        message="Patch touches a path not in the allowlist",
                        file_path=f.path,
                    )
                )

        limits = self.policy.patch_limits
        if parsed.total_files > limits.max_files:
            violations.append(
                PolicyViolation(
                    code="max_files",
                    severity=PolicySeverity.BLOCK,
                    message=f"Patch modifies {parsed.total_files} files (max {limits.max_files})",
                )
            )

        if parsed.total_lines_added > limits.max_lines_added:
            violations.append(
                PolicyViolation(
                    code="max_lines_added",
                    severity=PolicySeverity.BLOCK,
                    message=f"Patch adds {parsed.total_lines_added} lines (max {limits.max_lines_added})",
                )
            )

        if parsed.total_lines_removed > limits.max_lines_removed:
            violations.append(
                PolicyViolation(
                    code="max_lines_removed",
                    severity=PolicySeverity.BLOCK,
                    message=f"Patch removes {parsed.total_lines_removed} lines (max {limits.max_lines_removed})",
                )
            )

        if parsed.diff_bytes > limits.max_diff_bytes:
            violations.append(
                PolicyViolation(
                    code="max_diff_bytes",
                    severity=PolicySeverity.BLOCK,
                    message=f"Patch size is {parsed.diff_bytes} bytes (max {limits.max_diff_bytes})",
                )
            )

        for pattern in self._secret_patterns:
            if pattern.search(diff_text):
                violations.append(
                    PolicyViolation(
                        code="secret_pattern",
                        severity=PolicySeverity.BLOCK,
                        message="Patch contains a forbidden secret/credential pattern",
                    )
                )
                break

        danger_score, reasons = score_patch(parsed, self.policy.danger)
        allowed = not any(v.severity == PolicySeverity.BLOCK for v in violations)
        pr_label = (
            "safe" if allowed and danger_score <= self.policy.danger.safe_max else "needs-review"
        )

        return PolicyDecision(
            allowed=allowed,
            violations=violations,
            danger_score=danger_score,
            danger_reasons=reasons,
            pr_label=pr_label,
        )
