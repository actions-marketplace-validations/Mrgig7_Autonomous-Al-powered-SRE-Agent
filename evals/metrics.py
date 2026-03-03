from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean


@dataclass(frozen=True)
class EvalCaseResult:
    case_id: str
    category: str
    expected_category: str
    expected_validation_must_pass: bool
    classification_category: str | None
    plan_valid: bool
    patch_generated: bool
    policy_violations: list[dict]
    danger_score: int
    pr_label: str
    patch_touches_outside_plan: bool
    diff_too_large: bool
    forbidden_path_touched: bool
    validation_passed: bool
    validation_mode: str
    time_ms: int
    notes: str = ""

    def to_json_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvalAggregateMetrics:
    model: str
    cases: int
    fix_success_rate: float
    safe_fix_rate: float
    regression_rate: float
    hallucination_rate_proxy: float
    classification_accuracy: float
    avg_danger_score: float
    avg_mttr_seconds: float

    def to_json_dict(self) -> dict:
        return asdict(self)


def _bool_rate(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v) / len(values)


def expected_failure_category_from_fix_category(fix_category: str) -> str:
    mapping = {
        "python_missing_dependency": "dependency",
        "npm_install_error": "dependency",
        "go_mod_issue": "dependency",
        "lint_format": "code",
        "pytest_test_failure": "test",
        "jest_test_failure": "test",
        "java_test_failure": "test",
        "docker_build_error": "infrastructure",
        "config_missing_env": "configuration",
    }
    return mapping.get(fix_category, "unknown")


def compute_aggregate_metrics(model: str, results: list[EvalCaseResult]) -> EvalAggregateMetrics:
    fix_success = _bool_rate([r.validation_passed for r in results])

    safe_fix = _bool_rate(
        [
            (
                r.validation_passed
                and len(r.policy_violations) == 0
                and not r.patch_touches_outside_plan
            )
            for r in results
        ]
    )

    regression = _bool_rate(
        [(r.expected_validation_must_pass and not r.validation_passed) for r in results]
    )

    hallucination_proxy = _bool_rate(
        [
            (
                len(r.policy_violations) > 0
                or r.patch_touches_outside_plan
                or r.diff_too_large
                or r.forbidden_path_touched
            )
            for r in results
        ]
    )

    classification_ok = _bool_rate(
        [
            (
                (r.classification_category or "unknown")
                == expected_failure_category_from_fix_category(r.expected_category)
            )
            for r in results
        ]
    )

    avg_danger = mean([r.danger_score for r in results]) if results else 0.0
    avg_mttr = (mean([r.time_ms for r in results]) / 1000.0) if results else 0.0

    return EvalAggregateMetrics(
        model=model,
        cases=len(results),
        fix_success_rate=fix_success,
        safe_fix_rate=safe_fix,
        regression_rate=regression,
        hallucination_rate_proxy=hallucination_proxy,
        classification_accuracy=classification_ok,
        avg_danger_score=avg_danger,
        avg_mttr_seconds=avg_mttr,
    )
