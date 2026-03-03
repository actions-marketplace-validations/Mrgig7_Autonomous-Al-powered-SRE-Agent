from __future__ import annotations

from evals.metrics import EvalCaseResult, compute_aggregate_metrics


def test_compute_aggregate_metrics_rates() -> None:
    results = [
        EvalCaseResult(
            case_id="0001",
            category="python_missing_dependency",
            expected_category="python_missing_dependency",
            expected_validation_must_pass=True,
            classification_category="dependency",
            plan_valid=True,
            patch_generated=True,
            policy_violations=[],
            danger_score=10,
            pr_label="safe",
            patch_touches_outside_plan=False,
            diff_too_large=False,
            forbidden_path_touched=False,
            validation_passed=True,
            validation_mode="mock",
            time_ms=100,
        ),
        EvalCaseResult(
            case_id="0002",
            category="pytest_test_failure",
            expected_category="pytest_test_failure",
            expected_validation_must_pass=False,
            classification_category="test",
            plan_valid=True,
            patch_generated=False,
            policy_violations=[],
            danger_score=0,
            pr_label="needs-review",
            patch_touches_outside_plan=False,
            diff_too_large=False,
            forbidden_path_touched=False,
            validation_passed=False,
            validation_mode="mock",
            time_ms=200,
        ),
    ]

    agg = compute_aggregate_metrics("mock", results)
    assert agg.cases == 2
    assert agg.fix_success_rate == 0.5
    assert agg.safe_fix_rate == 0.5
    assert agg.regression_rate == 0.0
    assert agg.avg_danger_score == 5.0


def test_regression_rate_counts_expected_pass_failures() -> None:
    results = [
        EvalCaseResult(
            case_id="0001",
            category="python_missing_dependency",
            expected_category="python_missing_dependency",
            expected_validation_must_pass=True,
            classification_category="dependency",
            plan_valid=True,
            patch_generated=True,
            policy_violations=[],
            danger_score=10,
            pr_label="safe",
            patch_touches_outside_plan=False,
            diff_too_large=False,
            forbidden_path_touched=False,
            validation_passed=False,
            validation_mode="mock",
            time_ms=100,
        ),
        EvalCaseResult(
            case_id="0002",
            category="pytest_test_failure",
            expected_category="pytest_test_failure",
            expected_validation_must_pass=False,
            classification_category="test",
            plan_valid=True,
            patch_generated=False,
            policy_violations=[],
            danger_score=0,
            pr_label="needs-review",
            patch_touches_outside_plan=False,
            diff_too_large=False,
            forbidden_path_touched=False,
            validation_passed=False,
            validation_mode="mock",
            time_ms=200,
        ),
    ]

    agg = compute_aggregate_metrics("mock", results)
    assert agg.regression_rate == 0.5
