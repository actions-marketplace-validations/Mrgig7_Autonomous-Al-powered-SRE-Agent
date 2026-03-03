"""Celery tasks for building failure context.

These tasks are triggered after event ingestion to aggregate
observability data for RCA.
"""

import logging

from celery import Task

from sre_agent.celery_app import celery_app
from sre_agent.services.dashboard_events import publish_dashboard_event

logger = logging.getLogger(__name__)


class BaseContextTask(Task):
    """Base task class for context building."""

    abstract = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="fail").inc()
        logger.error(
            "Context building task failed",
            extra={
                "task_id": task_id,
                "task_name": self.name,
                "error": str(exc),
            },
            exc_info=exc,
        )

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="retry").inc()

    def on_success(self, retval, task_id, args, kwargs):
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="success").inc()


@celery_app.task(
    bind=True,
    base=BaseContextTask,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def build_failure_context(
    self,
    event_id: str,
    correlation_id: str | None = None,
) -> dict:
    """
    Build failure context bundle for a pipeline event.

    This task:
    1. Loads the event from database
    2. Fetches logs from GitHub
    3. Parses logs for errors/stack traces
    4. Fetches git context (changed files, commit message)
    5. Stores the context bundle
    6. Triggers the next stage (Failure Intelligence)

    Args:
        event_id: UUID of the pipeline event
        correlation_id: Optional correlation ID for tracing

    Returns:
        Dict with context building result
    """
    import asyncio

    logger.info(
        "Building failure context",
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

    from sre_agent.observability.tracing import attach_context, init_tracing, start_span

    init_tracing(service_name="sre-agent-worker")

    # Run async context building
    with attach_context(getattr(self.request, "headers", None)):
        with start_span(
            "build_failure_context",
            attributes={"delivery_id": correlation_id, "failure_id": event_id},
        ):
            result = asyncio.get_event_loop().run_until_complete(
                _build_context_async(event_id, correlation_id)
            )

    return result


async def _build_context_async(
    event_id: str,
    correlation_id: str | None,
) -> dict:
    """Async implementation of context building."""
    from uuid import UUID

    from sqlalchemy import select

    from sre_agent.core.redis_service import get_redis_service
    from sre_agent.database import async_session_factory
    from sre_agent.models.events import EventStatus, PipelineEvent
    from sre_agent.services.context_builder import ContextBuilder

    redis_service = get_redis_service()
    async with redis_service.distributed_lock(
        f"context:{event_id}",
        timeout=600.0,
        blocking=False,
    ) as acquired:
        if not acquired:
            from sre_agent.ops.metrics import inc

            inc("pipeline_skipped", attributes={"stage": "context_lock", "event_id": event_id})
            logger.info(
                "Context build skipped; already running",
                extra={"event_id": event_id, "correlation_id": correlation_id},
            )
            return {
                "event_id": event_id,
                "status": "skipped",
                "message": "Already running",
            }

    async with async_session_factory() as session:
        # Load event from database
        stmt = select(PipelineEvent).where(PipelineEvent.id == UUID(event_id))
        result = await session.execute(stmt)
        event = result.scalar_one_or_none()

        if event is None:
            logger.error(
                "Event not found for context building",
                extra={"event_id": event_id},
            )
            return {
                "event_id": event_id,
                "status": "error",
                "message": "Event not found",
            }

        # Update status to processing
        event.status = EventStatus.PROCESSING.value
        await session.commit()
        await publish_dashboard_event(
            event_type="pipeline_stage",
            stage="context",
            status="running",
            failure_id=event_id,
            run_id=None,
            correlation_id=correlation_id,
            metadata={"repo": event.repo},
        )

        # Build context
        builder = ContextBuilder()
        context = await builder.build_context(event)

        # Store context bundle
        # TODO: Store in MinIO or PostgreSQL JSONB column
        # For MVP, we log the summary

        logger.info(
            "Context building completed",
            extra={
                "event_id": event_id,
                "has_stack_traces": context.has_stack_traces,
                "has_test_failures": context.has_test_failures,
                "errors_count": len(context.errors),
                "changed_files": len(context.changed_files),
            },
        )
        await publish_dashboard_event(
            event_type="pipeline_stage",
            stage="context",
            status="completed",
            failure_id=event_id,
            correlation_id=correlation_id,
            metadata={
                "errors": len(context.errors),
                "test_failures": len(context.test_failures),
            },
        )

        # Run RCA analysis
        from sre_agent.intelligence.rca_engine import RCAEngine

        rca_engine = RCAEngine()
        rca_result = rca_engine.analyze(context)

        logger.info(
            "RCA analysis completed",
            extra={
                "event_id": event_id,
                "category": rca_result.classification.category.value,
                "confidence": rca_result.classification.confidence,
                "hypothesis": rca_result.primary_hypothesis.description[:100],
            },
        )
        await publish_dashboard_event(
            event_type="pipeline_stage",
            stage="rca",
            status="completed",
            failure_id=event_id,
            correlation_id=correlation_id,
            metadata={
                "category": rca_result.classification.category.value,
                "confidence": rca_result.classification.confidence,
            },
        )

        from sre_agent.fix_pipeline.store import FixPipelineRunStore
        from sre_agent.tasks.fix_pipeline_tasks import run_fix_pipeline

        run_store = FixPipelineRunStore()
        run_id = await run_store.create_run(
            event_id=event.id,
            run_key=event.idempotency_key,
            context_json=context.model_dump(),
            rca_json=rca_result.model_dump(),
        )
        from sre_agent.observability.tracing import inject_trace_headers

        run_fix_pipeline.apply_async(
            kwargs={"run_id": str(run_id), "correlation_id": correlation_id},
            headers=inject_trace_headers(),
        )
        await publish_dashboard_event(
            event_type="pipeline_stage",
            stage="fix_pipeline",
            status="queued",
            failure_id=event_id,
            run_id=str(run_id),
            correlation_id=correlation_id,
        )

        # Mark as completed
        event.status = EventStatus.COMPLETED.value
        await session.commit()

        return {
            "event_id": event_id,
            "status": "completed",
            "context_summary": {
                "errors": len(context.errors),
                "stack_traces": len(context.stack_traces),
                "test_failures": len(context.test_failures),
                "changed_files": len(context.changed_files),
                "has_logs": context.log_content is not None,
            },
            "rca_summary": {
                "category": rca_result.classification.category.value,
                "confidence": rca_result.classification.confidence,
                "hypothesis": rca_result.primary_hypothesis.description,
                "affected_files": len(rca_result.affected_files),
                "similar_incidents": len(rca_result.similar_incidents),
            },
            "next_step": "fix_pipeline",
            "fix_pipeline_run_id": str(run_id),
        }


@celery_app.task(bind=True, base=BaseContextTask)
def store_context_bundle(
    self,
    event_id: str,
    context_data: dict,
) -> dict:
    """
    Store a context bundle for later retrieval.

    Args:
        event_id: Event ID
        context_data: Serialized context bundle

    Returns:
        Storage confirmation
    """
    logger.info(
        "Storing context bundle",
        extra={"event_id": event_id, "task_id": self.request.id},
    )

    # TODO: Implement storage in MinIO or PostgreSQL
    # For MVP, this is a placeholder

    return {
        "event_id": event_id,
        "stored": True,
        "storage_location": "database",  # Future: MinIO path
    }
