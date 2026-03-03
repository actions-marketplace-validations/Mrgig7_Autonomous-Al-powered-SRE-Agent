from __future__ import annotations

import re
import time
from typing import Any

from reporter import ValidatorOutcome

METRIC_ALIASES = {
    "failure_count": "sre_agent_pipeline_runs_total",
    "fix_attempts": "sre_agent_pipeline_retry_total",
    "sandbox_success_rate": "sre_agent_pipeline_runs_total",
    "policy_rejections": "sre_agent_policy_violations_total",
}


def _extract_metric_names(payload: str) -> set[str]:
    names: set[str] = set()
    for line in payload.splitlines():
        if not line or line.startswith("#"):
            continue
        metric = re.split(r"[{ ]", line, maxsplit=1)[0].strip()
        if metric:
            names.add(metric)
    return names


async def validate(context: dict[str, Any], sre_client) -> ValidatorOutcome:
    started = time.perf_counter()
    try:
        metrics_payload = await sre_client.get_metrics()
        metric_names = _extract_metric_names(metrics_payload)

        required_aliases = [
            "failure_count",
            "fix_attempts",
            "sandbox_success_rate",
            "policy_rejections",
        ]
        required_metrics = [METRIC_ALIASES[item] for item in required_aliases]
        missing = [name for name in required_metrics if name not in metric_names]

        passed = len(missing) == 0
        return ValidatorOutcome(
            name="observability",
            passed=passed,
            duration_seconds=time.perf_counter() - started,
            details={
                "required_metrics": required_metrics,
                "missing_metrics": missing,
                "metric_count": len(metric_names),
            },
            error=None if passed else "Required observability metrics missing",
        )
    except Exception as exc:
        return ValidatorOutcome(
            name="observability",
            passed=False,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
