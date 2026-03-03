"""GitLab webhook handler.

Receives GitLab CI/CD webhook events, validates signatures,
normalizes events, stores them idempotently, and dispatches
async processing tasks.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.config import get_settings
from sre_agent.core.logging import correlation_id_ctx, delivery_id_ctx
from sre_agent.core.redis_service import get_redis_service
from sre_agent.database import get_db_session
from sre_agent.models.events import EventStatus
from sre_agent.observability.tracing import inject_trace_headers, start_span
from sre_agent.ops.metrics import inc
from sre_agent.providers import ProviderRegistry, ProviderType
from sre_agent.schemas.normalized import WebhookResponse
from sre_agent.services.event_store import EventStore
from sre_agent.services.webhook_delivery_store import (
    WebhookDeliveryStore,
    compute_fallback_delivery_id,
)
from sre_agent.tasks.dispatch import process_pipeline_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/gitlab", response_model=WebhookResponse)
async def gitlab_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> WebhookResponse:
    """
    GitLab webhook handler for pipeline and job events.

    Receives GitLab CI/CD webhook events, specifically:
    - Pipeline events: Fired when a pipeline completes
    - Build events: Fired when a job starts, completes, or fails

    We only process completed jobs/pipelines that have failed.

    Flow:
    1. Verify webhook token (X-Gitlab-Token header)
    2. Parse and validate payload
    3. Filter for relevant events (failures only)
    4. Normalize to canonical format
    5. Store idempotently
    6. Dispatch async processing task
    7. Return response

    Returns:
        - 200: Event ignored (not a failure or unsupported event type)
        - 202: Event accepted and queued for processing
        - 400: Invalid payload
        - 401: Invalid token
    """
    delivery_id = "gitlab"
    # Get raw body
    raw_body = await request.body()
    headers = dict(request.headers)

    # Get provider
    try:
        provider = ProviderRegistry.get_provider(ProviderType.GITLAB)
    except Exception as e:
        logger.error(f"Failed to get GitLab provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitLab provider not available",
        )

    # Verify webhook
    verification = provider.verify_webhook(headers, raw_body)

    if not verification.valid:
        logger.warning(
            "GitLab webhook verification failed",
            extra={"error": verification.error, "delivery_id": delivery_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=verification.error or "Invalid webhook token",
        )

    event_type = verification.event_type

    logger.info(
        "Received GitLab webhook",
        extra={
            "event_type": event_type,
            "delivery_id": delivery_id,
        },
    )

    # Parse JSON payload
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as e:
        logger.warning(
            "Invalid JSON payload",
            extra={"error": str(e), "delivery_id": delivery_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    delivery_id = compute_fallback_delivery_id(payload, provider="gitlab")
    correlation_id_ctx.set(delivery_id)
    delivery_id_ctx.set(delivery_id)

    repo_hint = None
    project = payload.get("project") if isinstance(payload, dict) else None
    if isinstance(project, dict):
        repo_hint = project.get("path_with_namespace") or project.get("name")

    delivery_store = WebhookDeliveryStore(session)
    is_new_delivery = await delivery_store.record_delivery(
        delivery_id=delivery_id,
        event_type=str(event_type or "unknown"),
        repository=str(repo_hint) if repo_hint else None,
    )
    if not is_new_delivery:
        inc(
            "webhook_deduped",
            attributes={"provider": "gitlab", "repo": str(repo_hint or "unknown")},
        )
        return WebhookResponse(
            status="duplicate_ignored",
            message="Duplicate webhook delivery ignored",
            correlation_id=delivery_id,
        )

    redis_service = get_redis_service()
    settings = get_settings()
    repo_rate_limit = int(getattr(settings, "repo_webhook_rate_limit_per_minute", 30))
    allowed, current, retry_after = await redis_service.check_rate_limit(
        key=f"webhook:repo:{repo_hint or 'unknown'}",
        limit=repo_rate_limit,
        window_seconds=60,
    )
    if not allowed:
        inc(
            "pipeline_throttled",
            attributes={
                "provider": "gitlab",
                "repo": str(repo_hint or "unknown"),
                "stage": "webhook",
            },
        )
        logger.warning(
            "Webhook throttled; delaying enqueue",
            extra={
                "repo": repo_hint,
                "delivery_id": delivery_id,
                "current": current,
                "retry_after": retry_after,
            },
        )

    # Filter for supported event types
    object_kind = payload.get("object_kind", "")
    if object_kind not in ("pipeline", "build"):
        logger.debug(
            "Ignoring unsupported GitLab event type",
            extra={"object_kind": object_kind, "delivery_id": delivery_id},
        )
        return WebhookResponse(
            status="ignored",
            message=f"Event type '{object_kind}' is not processed",
            correlation_id=delivery_id,
        )

    # Check if we should process this event
    should_process, reason = provider.should_process(payload)
    if not should_process:
        logger.debug(
            "Ignoring non-failure GitLab event",
            extra={"reason": reason, "delivery_id": delivery_id},
        )
        return WebhookResponse(
            status="ignored",
            message=reason,
            correlation_id=delivery_id,
        )

    # Normalize the event
    try:
        normalized_event = provider.normalize_event(payload, delivery_id)
    except ValueError as e:
        logger.warning(
            "Failed to normalize GitLab event",
            extra={"error": str(e), "delivery_id": delivery_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to normalize event: {e}",
        )

    # Store the event idempotently
    event_store = EventStore(session)
    try:
        with start_span(
            "store_event",
            attributes={"delivery_id": delivery_id, "event_type": event_type},
        ):
            stored_event, is_new = await event_store.store_event(normalized_event)
    except Exception as e:
        logger.error(
            "Failed to store GitLab event",
            extra={"error": str(e), "delivery_id": delivery_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable",
            headers={"Retry-After": "60"},
        )

    # If duplicate, don't dispatch again
    if not is_new:
        logger.info(
            "Duplicate GitLab event - skipping dispatch",
            extra={
                "event_id": str(stored_event.id),
                "idempotency_key": normalized_event.idempotency_key,
            },
        )
        return WebhookResponse(
            status="ignored",
            message="Duplicate event - already processed",
            event_id=stored_event.id,
            correlation_id=delivery_id,
        )

    # Dispatch async processing task
    try:
        await event_store.update_status(stored_event.id, EventStatus.DISPATCHED)
        await session.commit()

        headers = inject_trace_headers()

        if not allowed and retry_after > 0:
            inc(
                "pipeline_throttled",
                attributes={
                    "provider": "gitlab",
                    "repo": str(repo_hint or "unknown"),
                    "stage": "enqueue_delay",
                },
            )
            from sre_agent.observability.metrics import METRICS

            METRICS.pipeline_throttled_total.labels(scope="repo").inc()
            with start_span(
                "enqueue_pipeline",
                attributes={"delivery_id": delivery_id, "failure_id": str(stored_event.id)},
            ):
                process_pipeline_event.apply_async(
                    kwargs={"event_id": str(stored_event.id), "correlation_id": delivery_id},
                    countdown=retry_after,
                    headers=headers,
                )
            return WebhookResponse(
                status="throttled_delayed",
                message=f"Event accepted but throttled; delayed {retry_after}s",
                event_id=stored_event.id,
                correlation_id=delivery_id,
            )
        with start_span(
            "enqueue_pipeline",
            attributes={"delivery_id": delivery_id, "failure_id": str(stored_event.id)},
        ):
            process_pipeline_event.apply_async(
                kwargs={"event_id": str(stored_event.id), "correlation_id": delivery_id},
                headers=headers,
            )

        logger.info(
            "GitLab event dispatched for processing",
            extra={
                "event_id": str(stored_event.id),
                "repo": normalized_event.repo,
                "failure_type": normalized_event.failure_type.value,
            },
        )
    except Exception as e:
        logger.error(
            "Failed to dispatch GitLab event",
            extra={"error": str(e), "event_id": str(stored_event.id)},
            exc_info=True,
        )

    return WebhookResponse(
        status="accepted",
        message="Event accepted and queued for processing",
        event_id=stored_event.id,
        correlation_id=delivery_id,
    )
