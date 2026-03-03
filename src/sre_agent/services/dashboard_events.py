from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sre_agent.core.redis_service import get_redis_service

logger = logging.getLogger(__name__)


async def publish_dashboard_event(
    *,
    event_type: str,
    stage: str,
    status: str,
    failure_id: str | None = None,
    run_id: str | None = None,
    correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Publish a structured dashboard event for SSE consumers.

    Publishing is best-effort and never raises, so pipeline stages cannot fail
    because of observability backends.
    """
    payload: dict[str, Any] = {
        "type": event_type,
        "stage": stage,
        "status": status,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if failure_id:
        payload["failure_id"] = failure_id
    if run_id:
        payload["run_id"] = run_id
    if correlation_id:
        payload["correlation_id"] = correlation_id
    if metadata:
        payload["metadata"] = metadata

    try:
        redis_service = get_redis_service()
        await redis_service.publish("dashboard_events", payload)
    except Exception as exc:
        logger.debug(
            "Failed to publish dashboard event",
            extra={
                "stage": stage,
                "status": status,
                "event_type": event_type,
                "error": str(exc),
            },
        )
