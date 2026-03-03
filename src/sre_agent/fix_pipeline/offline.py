from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

from sre_agent.adapters.registry import select_adapter
from sre_agent.ai.llm_provider import OllamaProvider
from sre_agent.ai.plan_generator import PlanGenerator
from sre_agent.fix_pipeline.patch_generator import PatchGenerator
from sre_agent.intelligence.classifier import FailureClassifier
from sre_agent.safety.diff_parser import parse_unified_diff
from sre_agent.safety.policy_engine import PolicyEngine
from sre_agent.safety.policy_loader import load_policy_from_file
from sre_agent.safety.policy_models import PlanIntent, SafetyPolicy
from sre_agent.safety.runtime import get_policy_engine
from sre_agent.schemas.context import FailureContextBundle, LogContent
from sre_agent.schemas.fix_plan import FixOperation, FixPlan
from sre_agent.schemas.intelligence import RCAHypothesis, RCAResult
from sre_agent.services.log_parser import LogParser


def _build_context_from_logs(case_id: str, log_text: str) -> FailureContextBundle:
    parser = LogParser()
    parsed = parser.parse(log_text)
    log_content = LogContent(
        raw_content=log_text,
        truncated=False,
        size_bytes=len(log_text.encode("utf-8")),
        job_name=case_id,
    )
    return FailureContextBundle(
        event_id=uuid4(),
        repo="evals/offline",
        commit_sha="0" * 40,
        branch="main",
        pipeline_id=case_id,
        job_name=case_id,
        log_content=log_content,
        log_summary=parsed.summary,
        errors=parsed.errors,
        stack_traces=parsed.stack_traces,
        test_failures=parsed.test_failures,
        build_errors=parsed.build_errors,
        changed_files=[],
    )


def _get_policy_engine(policy_path: Path | None) -> PolicyEngine:
    if policy_path is None:
        return get_policy_engine()
    try:
        return PolicyEngine(load_policy_from_file(policy_path))
    except Exception:
        return PolicyEngine(SafetyPolicy())


def _mock_plan(
    *,
    fix_category_hint: str | None,
    allowed_fix_types: list[str] | None,
    log_text: str,
    repo_fixture_dir: Path | None,
) -> FixPlan:
    category = fix_category_hint or "unknown"
    evidence = [line.strip() for line in log_text.splitlines() if line.strip()][-3:]
    operations: list[FixOperation] = []
    files: list[str] = []

    if category == "python_missing_dependency":
        name = "requests"
        m = None
        for pat in (
            r"No module named ['\\\"]([^'\\\"]+)['\\\"]",
            r"ModuleNotFoundError: No module named ['\\\"]([^'\\\"]+)['\\\"]",
        ):
            import re

            m = re.search(pat, log_text)
            if m:
                break
        if m:
            name = m.group(1).split(".")[0]

        target = "pyproject.toml"
        if repo_fixture_dir and (repo_fixture_dir / "requirements.txt").exists():
            target = "requirements.txt"

        files = [target]
        op_type = "add_dependency"
        if allowed_fix_types and "pin_dependency" in allowed_fix_types:
            op_type = "pin_dependency"
        details = {"name": name, "spec": "^1.0.0" if target.endswith("toml") else "==1.0.0"}
        operations = [
            FixOperation(
                type=op_type,
                file=target,
                details=details,
                rationale="Add the missing dependency referenced by the failure logs",
                evidence=evidence,
            )
        ]

    elif category == "lint_format":
        name = "os"
        import re

        m = re.search(r"F401: '([^']+)' imported but unused", log_text)
        if m:
            name = m.group(1).split(".")[-1]
        files = ["src/app.py"]
        operations = [
            FixOperation(
                type="remove_unused",
                file="src/app.py",
                details={"name": name},
                rationale="Remove unused import to satisfy linting",
                evidence=evidence,
            )
        ]
    elif category == "node_missing_dependency":
        import re

        name = "lodash"
        m = re.search(r"Cannot find module ['\\\"]([^'\\\"]+)['\\\"]", log_text)
        if m:
            name = m.group(1)
        files = ["package.json"]
        operations = [
            FixOperation(
                type="add_dependency",
                file="package.json",
                details={"name": name, "spec": "^1.0.0"},
                rationale="Add the missing Node dependency referenced by the failure logs",
                evidence=evidence,
            )
        ]
    elif category == "node_lockfile_mismatch":
        files = ["package-lock.json"]
        operations = [
            FixOperation(
                type="update_config",
                file="package-lock.json",
                details={"lockfile_version": 2},
                rationale="Bring package-lock.json into a supported lockfileVersion",
                evidence=evidence,
            )
        ]
    elif category == "go_mod_tidy":
        files = ["go.sum"]
        operations = [
            FixOperation(
                type="update_config",
                file="go.sum",
                details={},
                rationale="Normalize go.sum presence for deterministic builds",
                evidence=evidence,
            )
        ]
    elif category == "go_add_missing_module":
        import re

        module = "github.com/acme/foo"
        m = re.search(r"no required module provides package\s+([^\s;]+)", log_text)
        if m:
            module = m.group(1).split("/")[0:3]
            module = "/".join(module)
        files = ["go.mod"]
        operations = [
            FixOperation(
                type="pin_dependency",
                file="go.mod",
                details={"name": module, "spec": "v1.0.0"},
                rationale="Add the missing Go module requirement",
                evidence=evidence,
            )
        ]
    elif category == "java_dependency_version_missing":
        import re

        group_id = "org.junit.jupiter"
        artifact_id = "junit-jupiter"
        m = re.search(
            r"dependencies\.dependency\.version.*?for\s+([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)\s+is missing",
            log_text,
        )
        if m:
            group_id, artifact_id = m.group(1), m.group(2)
        files = ["pom.xml"]
        operations = [
            FixOperation(
                type="pin_dependency",
                file="pom.xml",
                details={"group_id": group_id, "artifact_id": artifact_id, "spec": "1.0.0"},
                rationale="Pin a missing Maven dependency version",
                evidence=evidence,
            )
        ]
    elif category == "java_plugin_version_missing":
        import re

        group_id = "org.apache.maven.plugins"
        artifact_id = "maven-surefire-plugin"
        m = re.search(
            r"Plugin\s+([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)\s+or one of its dependencies could not be resolved",
            log_text,
        )
        if m:
            group_id, artifact_id = m.group(1), m.group(2)
        files = ["pom.xml"]
        operations = [
            FixOperation(
                type="pin_dependency",
                file="pom.xml",
                details={
                    "plugin": True,
                    "group_id": group_id,
                    "artifact_id": artifact_id,
                    "spec": "3.1.2",
                },
                rationale="Pin a missing Maven plugin version",
                evidence=evidence,
            )
        ]
    elif category == "docker_pin_base_image":
        files = ["Dockerfile"]
        operations = [
            FixOperation(
                type="update_config",
                file="Dockerfile",
                details={"pin_base_image": {"image": "ubuntu", "tag": "22.04"}},
                rationale="Pin a stable base image tag instead of an invalid/unstable reference",
                evidence=evidence,
            )
        ]
    elif category == "docker_apt_get_cleanup":
        files = ["Dockerfile"]
        operations = [
            FixOperation(
                type="update_config",
                file="Dockerfile",
                details={"apt_get_cleanup": True},
                rationale="Ensure apt cache cleanup to reduce transient apt failures",
                evidence=evidence,
            )
        ]
    else:
        files = ["src/app.py"]

    if allowed_fix_types:
        operations = [op for op in operations if op.type in set(allowed_fix_types)]

    return FixPlan(
        root_cause=f"offline mock plan for {category}",
        category=category,
        confidence=0.5,
        files=files,
        operations=operations,
    )


async def run_pipeline_from_logs(
    log_text: str,
    *,
    case_id: str,
    model: str,
    repo_fixture_dir: Path | None,
    policy_path: Path | None = None,
    fix_category_hint: str | None = None,
    allowed_fix_types: list[str] | None = None,
) -> dict:
    started = time.perf_counter()

    context = _build_context_from_logs(case_id, log_text)
    repo_files: list[str] = []
    if repo_fixture_dir and repo_fixture_dir.exists():
        repo_files = sorted(
            [
                p.relative_to(repo_fixture_dir).as_posix()
                for p in repo_fixture_dir.rglob("*")
                if p.is_file()
            ]
        )
    selected = select_adapter(log_text, repo_files)
    classification = FailureClassifier().classify(context)
    hypothesis_text = (
        (context.primary_error.message if context.primary_error else None)
        or (context.primary_stack_trace.message if context.primary_stack_trace else None)
        or "Offline classification only (similarity search disabled)"
    )
    rca = RCAResult(
        event_id=context.event_id,
        classification=classification,
        primary_hypothesis=RCAHypothesis(
            description=hypothesis_text, confidence=classification.confidence
        ),
        alternative_hypotheses=[],
        affected_files=[],
        similar_incidents=[],
        suggested_patterns=[],
        analysis_time_seconds=None,
    )

    if model == "mock":
        if fix_category_hint is None and selected is not None:
            fix_category_hint = selected.detection.category
        plan = _mock_plan(
            fix_category_hint=fix_category_hint,
            allowed_fix_types=allowed_fix_types,
            log_text=log_text,
            repo_fixture_dir=repo_fixture_dir,
        )
        model_used = "mock"
    else:
        provider = OllamaProvider(model=model)
        gen = PlanGenerator(llm_provider=provider)
        plan = await gen.generate_plan(rca_result=rca, context=context)
        model_used = gen.last_model_name or model

    policy_engine = _get_policy_engine(policy_path)
    plan_decision = policy_engine.evaluate_plan(
        PlanIntent(
            target_files=plan.files,
            category=plan.category,
            operation_types=[op.type for op in plan.operations],
        )
    )

    patch_diff = ""
    patch_error: str | None = None
    patch_decision = None
    patch_touches_outside_plan = False
    if plan_decision.allowed and repo_fixture_dir:
        try:
            patch = PatchGenerator().generate(repo_fixture_dir, plan)
            patch_diff = patch.diff_text
            parsed = parse_unified_diff(patch_diff) if patch_diff.strip() else None
            touched = {f.path for f in parsed.files} if parsed else set()
            patch_touches_outside_plan = bool(touched - set(plan.files))
            patch_decision = policy_engine.evaluate_patch(patch_diff)
        except Exception as e:
            patch_error = str(e)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "case_id": case_id,
        "model_used": model_used,
        "adapter": selected.adapter.name if selected else None,
        "detection": selected.detection.model_dump(mode="json") if selected else None,
        "context": context,
        "rca": rca,
        "plan": plan,
        "plan_decision": plan_decision,
        "patch_diff": patch_diff,
        "patch_decision": patch_decision,
        "patch_error": patch_error,
        "patch_touches_outside_plan": patch_touches_outside_plan,
        "time_ms": elapsed_ms,
    }
