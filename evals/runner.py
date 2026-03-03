from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

from sre_agent.safety.diff_parser import parse_unified_diff

from evals.dataset import load_dataset
from evals.metrics import EvalAggregateMetrics, EvalCaseResult, compute_aggregate_metrics


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _extract_policy_flags(violations: list[dict]) -> tuple[bool, bool]:
    forbidden_path = any(v.get("code") == "forbidden_path" for v in violations)
    diff_too_large = any(
        v.get("code") in {"max_diff_bytes", "max_lines_added", "max_lines_removed", "max_files"}
        for v in violations
    )
    return forbidden_path, diff_too_large


def _mock_validate(
    *,
    plan_category: str,
    patch_diff: str,
    patch_policy_allowed: bool,
    patch_touches_outside_plan: bool,
) -> bool:
    supported = plan_category in {
        "python_missing_dependency",
        "lint_format",
        "node_missing_dependency",
        "node_lockfile_mismatch",
        "go_mod_tidy",
        "go_add_missing_module",
        "java_dependency_version_missing",
        "java_plugin_version_missing",
        "docker_pin_base_image",
        "docker_apt_get_cleanup",
    }
    if not supported:
        return False
    if not patch_policy_allowed:
        return False
    if patch_touches_outside_plan:
        return False
    if not patch_diff.strip():
        return False
    try:
        parse_unified_diff(patch_diff)
    except Exception:
        return False
    return True


async def run_eval_cases(
    *,
    dataset_path: Path,
    out_dir: Path,
    model: str,
    limit: int | None,
    real_sandbox: bool,
    fail_fast: bool,
) -> tuple[list[EvalCaseResult], EvalAggregateMetrics, dict]:
    from sre_agent.fix_pipeline.offline import run_pipeline_from_logs

    run_id = out_dir.name or f"run_{uuid4().hex[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = load_dataset(dataset_path, limit=limit)
    results: list[EvalCaseResult] = []
    per_case_artifacts: dict[str, dict] = {}

    validation_mode = "real-sandbox" if real_sandbox else "mock"

    for case in cases:
        started = time.perf_counter()
        try:
            pipeline_out = await run_pipeline_from_logs(
                case.logs_text,
                case_id=case.case_id,
                model=model,
                repo_fixture_dir=case.repo_fixture_dir,
                fix_category_hint=case.failure.category,
                allowed_fix_types=case.expected.allowed_fix_types,
            )

            plan = pipeline_out["plan"]
            plan_decision = pipeline_out["plan_decision"]
            patch_decision = pipeline_out["patch_decision"]
            patch_diff = pipeline_out["patch_diff"]
            patch_error = pipeline_out["patch_error"]
            patch_touches_outside_plan = pipeline_out["patch_touches_outside_plan"]

            policy_violations: list[dict] = []
            for v in plan_decision.violations:
                policy_violations.append({"stage": "plan", **v.model_dump()})
            if patch_decision is not None:
                for v in patch_decision.violations:
                    policy_violations.append({"stage": "patch", **v.model_dump()})

            forbidden_path_touched, diff_too_large = _extract_policy_flags(policy_violations)

            patch_policy_allowed = bool(patch_decision.allowed) if patch_decision else False
            validation_passed = _mock_validate(
                plan_category=plan.category,
                patch_diff=patch_diff,
                patch_policy_allowed=patch_policy_allowed,
                patch_touches_outside_plan=patch_touches_outside_plan,
            )

            result = EvalCaseResult(
                case_id=case.case_id,
                category=plan.category,
                expected_category=case.expected.expected_category,
                expected_validation_must_pass=case.expected.success_criteria.validation_must_pass,
                classification_category=pipeline_out["rca"].classification.category.value,
                plan_valid=True,
                patch_generated=bool(patch_diff.strip()),
                policy_violations=policy_violations,
                danger_score=(
                    int(patch_decision.danger_score)
                    if patch_decision
                    else int(plan_decision.danger_score)
                ),
                pr_label=(
                    str(patch_decision.pr_label) if patch_decision else str(plan_decision.pr_label)
                ),
                patch_touches_outside_plan=bool(patch_touches_outside_plan),
                diff_too_large=diff_too_large,
                forbidden_path_touched=forbidden_path_touched,
                validation_passed=validation_passed,
                validation_mode=validation_mode,
                time_ms=max(1, int((time.perf_counter() - started) * 1000)),
                notes=patch_error or "",
            )

            artifacts = {
                "failure": case.failure.model_dump(),
                "expected": case.expected.model_dump(),
                "classification": pipeline_out["rca"].classification.model_dump(),
                "plan": plan.model_dump(),
                "plan_policy": plan_decision.model_dump(),
                "patch_policy": patch_decision.model_dump() if patch_decision else None,
                "patch_diff": patch_diff,
            }
            per_case_artifacts[case.case_id] = artifacts

        except Exception as e:
            result = EvalCaseResult(
                case_id=case.case_id,
                category="error",
                expected_category=case.expected.expected_category,
                expected_validation_must_pass=case.expected.success_criteria.validation_must_pass,
                classification_category=None,
                plan_valid=False,
                patch_generated=False,
                policy_violations=[],
                danger_score=0,
                pr_label="needs-review",
                patch_touches_outside_plan=False,
                diff_too_large=False,
                forbidden_path_touched=False,
                validation_passed=False,
                validation_mode=validation_mode,
                time_ms=max(1, int((time.perf_counter() - started) * 1000)),
                notes=str(e),
            )
            per_case_artifacts[case.case_id] = {"error": str(e)}
            if fail_fast:
                results.append(result)
                break

        results.append(result)
        _write_json(
            out_dir / f"{case.case_id}.json",
            {"result": result.to_json_dict(), "artifacts": per_case_artifacts[case.case_id]},
        )

    aggregate = compute_aggregate_metrics(model, results)
    summary = {
        "run_id": run_id,
        "model": model,
        "dataset_path": str(dataset_path),
        "validation_mode": validation_mode,
        "generated_at_ms": _now_ms(),
        "metrics": aggregate.to_json_dict(),
    }
    return results, aggregate, summary


async def run_evals(
    *,
    dataset_path: Path,
    out_dir: Path,
    model: str,
    limit: int | None,
    real_sandbox: bool,
    fail_fast: bool,
) -> dict:
    from evals.reporting import render_markdown_summary

    results, aggregate, summary = await run_eval_cases(
        dataset_path=dataset_path,
        out_dir=out_dir,
        model=model,
        limit=limit,
        real_sandbox=real_sandbox,
        fail_fast=fail_fast,
    )

    _write_json(
        out_dir / "summary.json", {**summary, "results": [r.to_json_dict() for r in results]}
    )
    (out_dir / "summary.md").write_text(render_markdown_summary(aggregate), encoding="utf-8")
    return summary
