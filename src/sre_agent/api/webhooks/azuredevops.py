"""Azure DevOps webhook handler.

Receives Azure DevOps Service Hook events for builds and releases.
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


@router.post("/azuredevops", response_model=WebhookResponse)
async def azuredevops_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> WebhookResponse:
    """Azure DevOps webhook handler for build and release events."""
    raw_body = await request.body()
    headers = dict(request.headers)

    try:
        provider = ProviderRegistry.get_provider(ProviderType.AZURE_DEVOPS)
    except Exception as e:
        logger.error(f"Failed to get Azure DevOps provider: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Azure DevOps provider not available",
        )

    verification = provider.verify_webhook(headers, raw_body)

    if not verification.valid:
        logger.warning(f"Azure DevOps webhook verification failed: {verification.error}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=verification.error or "Invalid authentication",
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    delivery_id = compute_fallback_delivery_id(payload, provider="azuredevops")
    correlation_id_ctx.set(delivery_id)
    delivery_id_ctx.set(delivery_id)
    repo_hint = None
    if isinstance(payload, dict):
        repo_hint = payload.get("resource", {}).get("repository", {}).get("name")

    delivery_store = WebhookDeliveryStore(session)
    is_new_delivery = await delivery_store.record_delivery(
        delivery_id=delivery_id,
        event_type=str(verification.event_type or "unknown"),
        repository=str(repo_hint) if repo_hint else None,
    )
    if not is_new_delivery:
        inc(
            "webhook_deduped",
            attributes={"provider": "azuredevops", "repo": str(repo_hint or "unknown")},
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
                "provider": "azuredevops",
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

    event_type = payload.get("eventType", "")
    if not event_type.startswith(("build.complete", "ms.vss-release")):
        return WebhookResponse(
            status="ignored",
            message=f"Event type '{event_type}' not processed",
            correlation_id=delivery_id,
        )

    should_process, reason = provider.should_process(payload)
    if not should_process:
        return WebhookResponse(status="ignored", message=reason, correlation_id=delivery_id)

    try:
        normalized = provider.normalize_event(payload, delivery_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    event_store = EventStore(session)
    with start_span(
        "store_event",
        attributes={"delivery_id": delivery_id, "event_type": "azuredevops"},
    ):
        stored_event, is_new = await event_store.store_event(normalized)

    if not is_new:
        return WebhookResponse(
            status="ignored",
            message="Duplicate event",
            event_id=stored_event.id,
            correlation_id=delivery_id,
        )

    await event_store.update_status(stored_event.id, EventStatus.DISPATCHED)
    await session.commit()
    trace_headers = inject_trace_headers()
    if not allowed and retry_after > 0:
        inc(
            "pipeline_throttled",
            attributes={
                "provider": "azuredevops",
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
                headers=trace_headers,
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
            headers=trace_headers,
        )

    return WebhookResponse(
        status="accepted",
        message="Event queued for processing",
        event_id=stored_event.id,
        correlation_id=delivery_id,
    )
