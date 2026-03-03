from __future__ import annotations

from fnmatch import fnmatch

from sre_agent.safety.diff_parser import ParsedDiff
from sre_agent.safety.policy_models import DangerPolicy, DangerReason, PlanIntent


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    return normalized[2:] if normalized.startswith("./") else normalized


def score_plan_intent(intent: PlanIntent, danger: DangerPolicy) -> tuple[int, list[DangerReason]]:
    score = 0
    reasons: list[DangerReason] = []

    for rule in danger.risky_paths:
        if any(fnmatch(_normalize_path(path), rule.glob) for path in intent.target_files):
            score += rule.weight
            reasons.append(
                DangerReason(code="risky_path", weight=rule.weight, message=rule.message)
            )

    file_weight = danger.weights.get("per_file", 0)
    if file_weight and intent.target_files:
        weight = file_weight * len(intent.target_files)
        score += weight
        reasons.append(
            DangerReason(code="file_count", weight=weight, message="Files proposed for change")
        )

    op_weights = {
        "modify_code": 15,
        "update_config": 8,
        "remove_unused": 5,
        "add_dependency": 5,
        "pin_dependency": 5,
    }
    for op_type in intent.operation_types:
        w = op_weights.get(op_type, 0)
        if w:
            score += w
            reasons.append(
                DangerReason(code="operation_type", weight=w, message=f"Operation: {op_type}")
            )

    return min(100, score), reasons


def score_patch(parsed: ParsedDiff, danger: DangerPolicy) -> tuple[int, list[DangerReason]]:
    score = 0
    reasons: list[DangerReason] = []

    for rule in danger.risky_paths:
        if any(fnmatch(f.path, rule.glob) for f in parsed.files):
            score += rule.weight
            reasons.append(
                DangerReason(code="risky_path", weight=rule.weight, message=rule.message)
            )

    per_file = danger.weights.get("per_file", 0)
    if per_file and parsed.total_files:
        weight = per_file * parsed.total_files
        score += weight
        reasons.append(DangerReason(code="file_count", weight=weight, message="Files changed"))

    lines_changed = parsed.total_lines_added + parsed.total_lines_removed
    per_50_lines = danger.weights.get("per_50_lines_changed", 0)
    if per_50_lines and lines_changed:
        buckets = (lines_changed + 49) // 50
        weight = per_50_lines * buckets
        score += weight
        reasons.append(DangerReason(code="lines_changed", weight=weight, message="Lines changed"))

    per_10kb = danger.weights.get("per_10kb_diff", 0)
    if per_10kb and parsed.diff_bytes:
        buckets = (parsed.diff_bytes + 10_239) // 10_240
        weight = per_10kb * buckets
        score += weight
        reasons.append(DangerReason(code="diff_size", weight=weight, message="Diff size"))

    return min(100, score), reasons
