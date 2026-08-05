"""Async dispatch tasks for pipeline events.

These Celery tasks handle async processing of pipeline events,
dispatching them to downstream components (Observability Context Builder).
"""

import logging
from typing import Any

from celery import Task

from sre_agent.celery_app import celery_app

logger = logging.getLogger(__name__)


class BaseTask(Task):
    """Base task class with common error handling."""

    abstract = True

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: Any,
    ) -> None:
        """Handle task failure."""
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="fail").inc()
        logger.error(
            "Task failed",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "error": str(exc),
                "args": args,
                "kwargs": kwargs,
            },
            exc_info=exc,
        )

    def on_retry(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: Any,
    ) -> None:
        """Handle task retry."""
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="retry").inc()
        logger.warning(
            "Task retrying",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "error": str(exc),
                "retry_count": self.request.retries,
            },
        )

    def on_success(
        self,
        retval: Any,
        task_id: str,
        args: tuple,
        kwargs: dict,
    ) -> None:
        """Handle task success."""
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="success").inc()
        logger.info(
            "Task completed successfully",
            extra={
                "task_id": task_id,
                "task_name": self.name,
            },
        )


@celery_app.task(
    bind=True,
    base=BaseTask,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=900,  # Max 15 minutes between retries
)
def process_pipeline_event(self, event_id: str, correlation_id: str | None = None) -> dict:
    """
    Process a pipeline event after initial ingestion.

    This is the main async handler that:
    1. Updates event status to "processing"
    2. (MVP) Logs the event for observation
    3. (Future) Triggers Observability Context Builder
    4. Updates event status to "completed" or "failed"

    Args:
        event_id: UUID of the stored event
        correlation_id: Optional correlation ID for tracing

    Returns:
        Dict with processing result info
    """
    logger.info(
        "Processing pipeline event",
        extra={
            "event_id": event_id,
            "correlation_id": correlation_id,
            "task_id": self.request.id,
        },
    )
    from sre_agent.observability.metrics import METRICS

    METRICS.celery_tasks_total.labels(task=str(self.name), status="started").inc()

    from sre_agent.core.logging import delivery_id_ctx, failure_id_ctx

    delivery_id_ctx.set(correlation_id)
    failure_id_ctx.set(event_id)

    from sre_agent.observability.tracing import (
        attach_context,
        init_tracing,
        inject_trace_headers,
        start_span,
    )

    init_tracing(service_name="sre-agent-worker")

    import asyncio

    from sre_agent.core.redis_service import get_redis_service

    async def _check_once() -> bool:
        redis_service = get_redis_service()
        is_dup, _ = await redis_service.check_dedup(
            operation="dispatch_event",
            payload_hash=event_id,
            ttl_seconds=3600,
        )
        if is_dup:
            return False
        await redis_service.mark_processed(
            operation="dispatch_event",
            payload_hash=event_id,
            result_id=event_id,
            ttl_seconds=3600,
        )
        return True

    should_dispatch = asyncio.get_event_loop().run_until_complete(_check_once())
    if not should_dispatch:
        from sre_agent.ops.metrics import inc

        inc("pipeline_skipped", attributes={"stage": "dispatch", "event_id": event_id})
        logger.info(
            "Dispatch deduped; already dispatched recently",
            extra={"event_id": event_id, "correlation_id": correlation_id},
        )
        return {
            "event_id": event_id,
            "status": "skipped",
            "message": "Duplicate dispatch ignored",
        }

    from sre_agent.tasks.context_tasks import build_failure_context

    with attach_context(getattr(self.request, "headers", None)):
        with start_span(
            "enqueue_pipeline",
            attributes={
                "delivery_id": correlation_id,
                "failure_id": event_id,
            },
        ):
            headers = inject_trace_headers()
            build_failure_context.apply_async(
                kwargs={"event_id": event_id, "correlation_id": correlation_id},
                headers=headers,
            )

    logger.info(
        "Dispatched to context builder",
        extra={
            "event_id": event_id,
            "correlation_id": correlation_id,
        },
    )

    return {
        "event_id": event_id,
        "status": "dispatched",
        "message": "Event dispatched to context builder",
        "next_step": "build_failure_context",
    }


@celery_app.task(
    bind=True,
    base=BaseTask,
)
def update_event_status(self, event_id: str, status: str) -> dict:
    """
    Update the processing status of a pipeline event.

    Persists the new status to the ``pipeline_events`` table and returns
    a confirmation dict. Designed to be chained from other Celery tasks.

    Args:
        event_id: UUID string of the event
        status:   New status string (validated against ``EventStatus``)

    Returns:
        Dict confirming the update.
    """
    import asyncio
    from uuid import UUID

    from sqlalchemy import select

    from sre_agent.database import get_async_session
    from sre_agent.models.events import EventStatus, PipelineEvent

    logger.info(
        "Updating event status",
        extra={
            "event_id": event_id,
            "status": status,
            "task_id": self.request.id,
        },
    )

    # Normalise / validate status
    try:
        normalized_status = EventStatus(status).value
    except ValueError:
        # Accept the raw string for forward-compatibility but log a warning
        logger.warning(
            "Unknown event status value, persisting verbatim",
            extra={"event_id": event_id, "status": status},
        )
        normalized_status = status

    async def _apply() -> bool:
        try:
            uuid_value = UUID(event_id)
        except (ValueError, TypeError):
            logger.error("Invalid event_id for status update", extra={"event_id": event_id})
            return False
        async with get_async_session() as session:
            row = (
                await session.execute(select(PipelineEvent).where(PipelineEvent.id == uuid_value))
            ).scalar_one_or_none()
            if row is None:
                logger.warning(
                    "Pipeline event not found for status update",
                    extra={"event_id": event_id},
                )
                return False
            row.status = normalized_status
            await session.commit()
        return True

    updated = asyncio.run(_apply())

    return {
        "event_id": event_id,
        "status": normalized_status,
        "updated": updated,
    }
