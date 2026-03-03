from __future__ import annotations

import time
from typing import Any

from reporter import ValidatorOutcome


async def validate(context: dict[str, Any], sre_client) -> ValidatorOutcome:
    started = time.perf_counter()
    try:
        analysis = await sre_client.get_analysis(str(context["failure_id"]))
        validation = analysis.get("validation", {})

        sandbox_status = str(validation.get("sandbox", "")).lower()
        tests_status = str(validation.get("tests", "")).lower()
        scans = validation.get("scans")

        sandbox_ok = sandbox_status == "passed"
        tests_ok = tests_status == "pass"
        scans_recorded = isinstance(scans, dict)
        passed = sandbox_ok and tests_ok and scans_recorded

        return ValidatorOutcome(
            name="sandbox",
            passed=passed,
            duration_seconds=time.perf_counter() - started,
            details={
                "sandbox": sandbox_status,
                "tests": tests_status,
                "scans_recorded": scans_recorded,
            },
            error=None if passed else "Sandbox validation did not pass cleanly",
        )
    except Exception as exc:
        return ValidatorOutcome(
            name="sandbox",
            passed=False,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
