from __future__ import annotations

import time
from typing import Any

from reporter import ValidatorOutcome


async def validate(context: dict[str, Any], sre_client) -> ValidatorOutcome:
    started = time.perf_counter()
    try:
        run_id = context.get("run_id")
        if not run_id:
            return ValidatorOutcome(
                name="fix_pipeline",
                passed=False,
                duration_seconds=time.perf_counter() - started,
                error="Missing run_id in validator context",
            )

        analysis = await sre_client.get_analysis(str(context["failure_id"]))
        artifact = await sre_client.get_run_artifact(str(run_id))
        diff = await sre_client.get_run_diff(str(run_id))
        timeline = await sre_client.get_run_timeline(str(run_id))

        plan = analysis.get("proposed_fix", {}).get("plan")
        diff_text = diff.get("diff_text", "")
        safety = analysis.get("safety", {})

        has_plan = isinstance(plan, dict) and len(plan) > 0
        has_diff = isinstance(diff_text, str) and diff_text.startswith("--- a/")
        has_policy = (
            safety.get("patch_policy") is not None or safety.get("danger_score") is not None
        )
        has_timeline = isinstance(timeline.get("timeline"), list) and len(timeline["timeline"]) > 0

        passed = all([has_plan, has_diff, has_policy, has_timeline, bool(artifact.get("run_id"))])
        return ValidatorOutcome(
            name="fix_pipeline",
            passed=passed,
            duration_seconds=time.perf_counter() - started,
            details={
                "has_plan": has_plan,
                "has_diff": has_diff,
                "has_policy": has_policy,
                "timeline_steps": len(timeline.get("timeline", [])),
            },
            error=None if passed else "Plan/diff/policy/timeline validation failed",
        )
    except Exception as exc:
        return ValidatorOutcome(
            name="fix_pipeline",
            passed=False,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
