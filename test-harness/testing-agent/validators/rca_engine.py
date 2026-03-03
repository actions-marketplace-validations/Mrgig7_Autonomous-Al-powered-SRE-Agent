from __future__ import annotations

import time
from typing import Any

from reporter import ValidatorOutcome


async def validate(context: dict[str, Any], sre_client) -> ValidatorOutcome:
    started = time.perf_counter()
    try:
        analysis = await sre_client.get_analysis(str(context["failure_id"]))
        summary = analysis.get("summary", {})
        evidence = analysis.get("evidence", [])
        run = analysis.get("run", {})

        has_core_fields = bool(summary.get("category")) and bool(summary.get("root_cause"))
        confidence = float(summary.get("confidence", 0.0))
        confidence_ok = 0.0 <= confidence <= 1.0
        evidence_ok = isinstance(evidence, list) and len(evidence) > 0
        run_id = run.get("run_id")
        context["run_id"] = run_id

        passed = all([has_core_fields, confidence_ok, evidence_ok, bool(run_id)])
        return ValidatorOutcome(
            name="rca_engine",
            passed=passed,
            duration_seconds=time.perf_counter() - started,
            details={
                "category": summary.get("category"),
                "confidence": confidence,
                "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
                "run_id": run_id,
            },
            error=None if passed else "Missing RCA summary/evidence/run linkage",
        )
    except Exception as exc:
        return ValidatorOutcome(
            name="rca_engine",
            passed=False,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
