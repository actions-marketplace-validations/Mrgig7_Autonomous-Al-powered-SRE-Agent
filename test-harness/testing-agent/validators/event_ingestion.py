from __future__ import annotations

import time
from typing import Any

from reporter import ValidatorOutcome


async def validate(context: dict[str, Any], sre_client) -> ValidatorOutcome:
    started = time.perf_counter()
    try:
        failure_id = str(context["failure_id"])
        events = await sre_client.get_dashboard_events(
            repository=context["repository"],
            branch=context["branch"],
            limit=100,
        )
        items = events.get("events", [])
        persisted = any(str(item.get("id")) == failure_id for item in items)
        if not persisted:
            return ValidatorOutcome(
                name="event_ingestion",
                passed=False,
                duration_seconds=time.perf_counter() - started,
                error=f"Failure {failure_id} not found in dashboard events",
            )

        try:
            sse_payload = await sre_client.wait_for_dashboard_event(
                failure_id=failure_id,
                timeout_seconds=int(context["sse_wait_timeout_seconds"]),
            )
        except Exception:
            sse_payload = {"stage": "missed (event already persisted)"}

        metrics_text = await sre_client.get_metrics()
        has_celery_metric = "sre_agent_celery_tasks_total" in metrics_text

        return ValidatorOutcome(
            name="event_ingestion",
            passed=has_celery_metric,
            duration_seconds=time.perf_counter() - started,
            details={
                "persisted_event": persisted,
                "sse_stage": sse_payload.get("stage"),
                "celery_metric_present": has_celery_metric,
            },
            error=None if has_celery_metric else "Expected celery task metric not found",
        )
    except Exception as exc:
        return ValidatorOutcome(
            name="event_ingestion",
            passed=False,
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
