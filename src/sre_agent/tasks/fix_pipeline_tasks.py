import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from celery import Task

from sre_agent.celery_app import celery_app
from sre_agent.fix_pipeline.orchestrator import FixPipelineOrchestrator
from sre_agent.ops.retry_policy import RetryablePipelineError

logger = logging.getLogger(__name__)


class BaseTask(Task):
    abstract = True

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple,
        kwargs: dict,
        einfo: Any,
    ) -> None:
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

    def on_retry(self, exc: Exception, task_id: str, args: tuple, kwargs: dict, einfo: Any) -> None:
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="retry").inc()

    def on_success(self, retval: Any, task_id: str, args: tuple, kwargs: dict) -> None:
        from sre_agent.observability.metrics import METRICS

        METRICS.celery_tasks_total.labels(task=str(self.name), status="success").inc()


@celery_app.task(
    bind=True,
    base=BaseTask,
    max_retries=10,
    default_retry_delay=30,
)
def run_fix_pipeline(self, run_id: str, correlation_id: str | None = None) -> dict:
    import asyncio

    from sre_agent.observability.tracing import attach_context, init_tracing, start_span

    init_tracing(service_name="sre-agent-worker")
    from sre_agent.core.logging import delivery_id_ctx, run_id_ctx

    delivery_id_ctx.set(correlation_id)
    run_id_ctx.set(run_id)

    from sre_agent.observability.metrics import METRICS

    METRICS.celery_tasks_total.labels(task=str(self.name), status="started").inc()
    logger.info(
        "Running fix pipeline",
        extra={
            "run_id": run_id,
            "correlation_id": correlation_id,
            "task_id": self.request.id,
        },
    )
    try:
        with attach_context(getattr(self.request, "headers", None)):
            with start_span(
                "run_fix_pipeline",
                attributes={
                    "delivery_id": correlation_id,
                    "run_id": run_id,
                },
            ):
                result = asyncio.run(_run_fix_pipeline_guarded(UUID(run_id), correlation_id))
    except RetryablePipelineError as e:
        from sre_agent.observability.metrics import METRICS
        from sre_agent.ops.metrics import inc

        inc(
            "pipeline_retry",
            attributes={"run_id": run_id, "reason": e.reason},
        )
        METRICS.pipeline_retry_total.labels(reason=str(e.reason)).inc()
        logger.warning(
            "Fix pipeline retry scheduled",
            extra={
                "run_id": run_id,
                "correlation_id": correlation_id,
                "countdown_seconds": e.countdown_seconds,
                "reason": e.reason,
                "retry_count": self.request.retries,
            },
        )
        raise self.retry(countdown=e.countdown_seconds, exc=e)
    logger.info(
        "Fix pipeline complete",
        extra={
            "run_id": run_id,
            "correlation_id": correlation_id,
            "success": result.get("success"),
        },
    )
    return result


@celery_app.task(
    bind=True,
    base=BaseTask,
    max_retries=3,
    default_retry_delay=30,
)
def approve_run_and_create_pr(
    self,
    run_id: str,
    approved_by: str,
    correlation_id: str | None = None,
) -> dict:
    """Approve an awaiting run and create PR (plus conditional auto-merge)."""
    import asyncio

    from sre_agent.fix_pipeline.orchestrator import FixPipelineOrchestrator

    orchestrator = FixPipelineOrchestrator()
    return asyncio.run(
        orchestrator.approve_and_create_pr(
            UUID(run_id),
            approved_by=approved_by,
        )
    )


async def _run_fix_pipeline_guarded(run_id: UUID, correlation_id: str | None) -> dict:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from sre_agent.config import get_settings
    from sre_agent.core.redis_service import get_redis_service
    from sre_agent.database import get_async_session
    from sre_agent.fix_pipeline.store import FixPipelineRunStore
    from sre_agent.models.events import PipelineEvent
    from sre_agent.models.fix_pipeline import FixPipelineRunStatus
    from sre_agent.observability.metrics import METRICS, record_retry_signature_blocked
    from sre_agent.ops.retry_policy import compute_backoff_seconds, is_retryable_exception

    store = FixPipelineRunStore()
    run = await store.get_run(run_id)
    if run is None:
        METRICS.pipeline_runs_total.labels(outcome="fail").inc()
        return {"success": False, "error": "run_not_found"}
    if run.blocked_reason:
        from sre_agent.ops.metrics import inc

        inc(
            "pipeline_loop_blocked",
            attributes={"run_id": str(run_id), "reason": run.blocked_reason},
        )
        METRICS.pipeline_loop_blocked_total.labels(reason=str(run.blocked_reason)).inc()
        METRICS.pipeline_runs_total.labels(outcome="blocked").inc()
        return {"success": False, "error": "blocked", "blocked_reason": run.blocked_reason}

    async with get_async_session() as session:
        event = (
            await session.execute(select(PipelineEvent).where(PipelineEvent.id == run.event_id))
        ).scalar_one_or_none()
    repo = event.repo if event else "unknown"
    run_key = run.run_key or (event.idempotency_key if event else None) or str(run.event_id)
    from sre_agent.core.logging import failure_id_ctx, run_key_ctx

    failure_id_ctx.set(str(run.event_id))
    run_key_ctx.set(str(run_key))

    settings = get_settings()
    redis_service = get_redis_service()
    max_attempts = int(getattr(settings, "max_pipeline_attempts", 3))
    cooldown_seconds = int(getattr(settings, "cooldown_seconds", 900))
    base_backoff = int(getattr(settings, "base_backoff_seconds", 30))
    max_backoff = int(getattr(settings, "max_backoff_seconds", 600))
    retry_signature_ttl = int(getattr(settings, "retry_signature_ttl_seconds", 86400))
    retry_limit = int(getattr(run, "retry_limit_snapshot", 3) or 3)

    if run.attempt_count >= max_attempts:
        await store.update_run(run_id, blocked_reason="max_attempts")
        from sre_agent.ops.metrics import inc

        inc("pipeline_loop_blocked", attributes={"run_id": str(run_id), "reason": "max_attempts"})
        METRICS.pipeline_loop_blocked_total.labels(reason="max_attempts").inc()
        METRICS.pipeline_runs_total.labels(outcome="blocked").inc()
        return {"success": False, "error": "blocked", "blocked_reason": "max_attempts"}

    rca_json = getattr(run, "rca_json", None) or {}

    signature_payload = {
        "repo": repo,
        "category": ((rca_json.get("classification") or {}).get("category")),
        "hypothesis": ((rca_json.get("primary_hypothesis") or {}).get("description")),
        "error": (event.error_message if event else None),
        "adapter": getattr(run, "adapter_name", None),
    }
    signature_hash = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]
    signature_key = f"retry_signature:{repo}:{signature_hash}"
    signature_count = await redis_service.increment_counter(
        key=signature_key,
        ttl_seconds=retry_signature_ttl,
    )
    if signature_count > retry_limit:
        await store.update_run(
            run_id,
            blocked_reason="retry_limit_exceeded",
            status=FixPipelineRunStatus.ESCALATED.value,
        )
        record_retry_signature_blocked()
        METRICS.pipeline_loop_blocked_total.labels(reason="retry_limit_exceeded").inc()
        METRICS.pipeline_runs_total.labels(outcome="blocked").inc()
        return {
            "success": False,
            "error": "blocked",
            "blocked_reason": "retry_limit_exceeded",
        }

    if run.attempt_count > 0:
        last = run.updated_at or run.created_at
        now = datetime.now(UTC)
        elapsed = (now - last).total_seconds()
        if elapsed < cooldown_seconds:
            remaining = int(cooldown_seconds - elapsed)
            raise RetryablePipelineError(countdown_seconds=remaining, reason="cooldown")

    repo_limit = int(getattr(settings, "repo_pipeline_concurrency_limit", 2))
    repo_ttl = int(getattr(settings, "repo_pipeline_concurrency_ttl_seconds", 1200))

    async with redis_service.distributed_lock(
        f"pipeline:{run_key}",
        timeout=float(repo_ttl),
        blocking=False,
    ) as acquired:
        if not acquired:
            from sre_agent.ops.metrics import inc

            inc("pipeline_skipped", attributes={"run_id": str(run_id), "reason": "already_running"})
            METRICS.pipeline_runs_total.labels(outcome="skipped").inc()
            logger.info(
                "Fix pipeline skipped; already running",
                extra={"run_id": str(run_id), "run_key": run_key, "repo": repo},
            )
            return {"success": False, "error": "already_running"}

        slot_acquired = await redis_service.try_acquire_repo_concurrency(
            repo=repo,
            limit=repo_limit,
            ttl_seconds=repo_ttl,
        )
        if not slot_acquired:
            from sre_agent.ops.metrics import inc

            inc(
                "pipeline_throttled",
                attributes={"run_id": str(run_id), "repo": repo, "stage": "repo_concurrency"},
            )
            METRICS.pipeline_throttled_total.labels(scope="repo").inc()
            logger.info(
                "Fix pipeline skipped; repo concurrency limit reached",
                extra={"run_id": str(run_id), "run_key": run_key, "repo": repo},
            )
            raise RetryablePipelineError(countdown_seconds=base_backoff, reason="repo_throttled")

        try:
            attempt = int(run.attempt_count) + 1
            await store.update_run(run_id, attempt_count=attempt, run_key=run_key)
            orchestrator = FixPipelineOrchestrator()
            try:
                result = await orchestrator.run(run_id)
                if result.get("success"):
                    outcome = "success"
                else:
                    err = str(result.get("error") or "")
                    outcome = "blocked" if err.endswith("blocked") or "blocked" in err else "fail"
                METRICS.pipeline_runs_total.labels(outcome=outcome).inc()
                return result
            except Exception as e:
                if not is_retryable_exception(e):
                    raise
                if attempt >= max_attempts:
                    await store.update_run(run_id, blocked_reason="max_attempts")
                    raise
                countdown = compute_backoff_seconds(
                    attempt=attempt, base=base_backoff, maximum=max_backoff
                )
                raise RetryablePipelineError(
                    countdown_seconds=countdown, reason="transient_error"
                ) from e
        finally:
            await redis_service.release_repo_concurrency(repo=repo)
