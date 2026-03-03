from __future__ import annotations

import time
from typing import Any

from reporter import ValidatorOutcome


def _scan_present(scans: dict[str, Any] | None, key: str) -> bool:
    if not isinstance(scans, dict):
        return False
    return scans.get(key) is not None


async def validate(context: dict[str, Any], sre_client) -> ValidatorOutcome:
    started = time.perf_counter()
    try:
        analysis = await sre_client.get_analysis(str(context["failure_id"]))
        validation = analysis.get("validation", {})
        safety = analysis.get("safety", {})
        scans = validation.get("scans")

        has_gitleaks = _scan_present(scans, "gitleaks")
        has_trivy = _scan_present(scans, "trivy")
        has_sbom = _scan_present(scans, "sbom")
        has_danger = safety.get("danger_score") is not None
        has_label = bool(safety.get("label"))

        passed = all([has_gitleaks, has_trivy, has_sbom, has_danger, has_label])
        return ValidatorOutcome(
            name="security_safety",
            passed=passed,
            duration_seconds=time.perf_counter() - started,
            details={
                "gitleaks": has_gitleaks,
                "trivy": has_trivy,
                "sbom": has_sbom,
                "danger_score": safety.get("danger_score"),
                "label": safety.get("label"),
            },
            error=None if passed else "Safety scan and policy expectations not met",
        )
    except Exception as exc:
        return ValidatorOutcome(
            name="security_safety",
            passed=False,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
