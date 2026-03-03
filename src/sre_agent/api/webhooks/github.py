"""GitHub webhook handler.

Receives GitHub Actions webhook events, validates signatures,
normalizes events, stores them idempotently, and dispatches
async processing tasks.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.config import get_settings
from sre_agent.core.logging import correlation_id_ctx, delivery_id_ctx
from sre_agent.core.redis_service import get_redis_service
from sre_agent.core.security import get_verified_github_payload
from sre_agent.database import get_db_session
from sre_agent.models.events import EventStatus
from sre_agent.observability.tracing import inject_trace_headers, start_span
from sre_agent.ops.metrics import inc
from sre_agent.schemas.normalized import WebhookResponse
from sre_agent.services.dashboard_events import publish_dashboard_event
from sre_agent.services.event_normalizer import GitHubEventNormalizer
from sre_agent.services.event_store import EventStore
from sre_agent.services.github_app_installations import GitHubAppInstallationService
from sre_agent.services.post_merge_monitor import PostMergeMonitorService
from sre_agent.services.repository_config import RepositoryConfigService
from sre_agent.services.webhook_delivery_store import WebhookDeliveryStore
from sre_agent.tasks.dispatch import process_pipeline_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _extract_installation_id(payload: dict[str, Any]) -> int | None:
    installation = payload.get("installation")
    if not isinstance(installation, dict):
        return None
    installation_id = installation.get("id")
    return installation_id if isinstance(installation_id, int) else None


def _extract_ref(payload: dict[str, Any], *, event_type: str) -> str | None:
    if event_type == "workflow_job":
        workflow_job = payload.get("workflow_job")
        if isinstance(workflow_job, dict):
            head_sha = workflow_job.get("head_sha")
            if isinstance(head_sha, str) and head_sha.strip():
                return head_sha.strip()

    if event_type == "workflow_run":
        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            head_sha = workflow_run.get("head_sha")
            if isinstance(head_sha, str) and head_sha.strip():
                return head_sha.strip()
    return None


def _extract_branch(payload: dict[str, Any], *, event_type: str) -> str | None:
    if event_type == "workflow_job":
        workflow_job = payload.get("workflow_job")
        if isinstance(workflow_job, dict):
            head_branch = workflow_job.get("head_branch")
            if isinstance(head_branch, str) and head_branch.strip():
                return head_branch.strip()
    if event_type == "workflow_run":
        workflow_run = payload.get("workflow_run")
        if isinstance(workflow_run, dict):
            head_branch = workflow_run.get("head_branch")
            if isinstance(head_branch, str) and head_branch.strip():
                return head_branch.strip()
    return None


@router.post("/github", response_model=WebhookResponse)
async def github_webhook(
    verified_payload: tuple[bytes, str, str] = Depends(get_verified_github_payload),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookResponse:
    """
    GitHub webhook handler for workflow events.

    Receives GitHub Actions webhook events, specifically:
    - workflow_job: Fired when a job starts, completes, or fails

    We only process completed jobs that have failed.

    Flow:
    1. Verify webhook signature (done in dependency)
    2. Parse and validate payload
    3. Filter for relevant events (completed failures only)
    4. Normalize to canonical format
    5. Store idempotently
    6. Dispatch async processing task
    7. Return response

    Returns:
        - 200: Event ignored (not a failure or unsupported event type)
        - 202: Event accepted and queued for processing
        - 400: Invalid payload
        - 401: Invalid signature (handled by dependency)
    """
    raw_body, event_type, delivery_id = verified_payload

    # Set correlation ID for tracing
    correlation_id_ctx.set(delivery_id)
    delivery_id_ctx.set(delivery_id)

    logger.info(
        "Received GitHub webhook",
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

    allowed = True
    retry_after = 0

    repo_name_hint = None
    repo_info = payload.get("repository") if isinstance(payload, dict) else None
    if isinstance(repo_info, dict):
        repo_name_hint = repo_info.get("full_name") or repo_info.get("name")
        if isinstance(repo_name_hint, str):
            repo_name_hint = repo_name_hint.strip() or None
    if not repo_name_hint:
        return WebhookResponse(
            status="ignored",
            message="Repository metadata is required to process GitHub webhooks",
            correlation_id=delivery_id,
        )

    monitor_service = PostMergeMonitorService()

    # Filter for supported event types
    if event_type not in ("workflow_job", "workflow_run"):
        logger.debug(
            "Ignoring unsupported event type",
            extra={"event_type": event_type, "delivery_id": delivery_id},
        )
        return WebhookResponse(
            status="ignored",
            message=f"Event type '{event_type}' is not processed",
            correlation_id=delivery_id,
        )

    # For workflow_job events, check if it's a completed failure
    if event_type == "workflow_job":
        action = payload.get("action")
        conclusion = payload.get("workflow_job", {}).get("conclusion")

        # Only process completed jobs that failed
        if action != "completed":
            logger.debug(
                "Ignoring non-completed job event",
                extra={"action": action, "delivery_id": delivery_id},
            )
            return WebhookResponse(
                status="ignored",
                message=f"Job action '{action}' is not processed (only 'completed')",
                correlation_id=delivery_id,
            )

        if conclusion not in ("failure", "timed_out"):
            branch = _extract_branch(payload, event_type=event_type)
            if branch:
                await monitor_service.process_outcome(
                    repo=repo_name_hint,
                    branch=branch,
                    conclusion=conclusion,
                )
            logger.debug(
                "Ignoring non-failure job event",
                extra={"conclusion": conclusion, "delivery_id": delivery_id},
            )
            return WebhookResponse(
                status="ignored",
                message=f"Job conclusion '{conclusion}' is not a failure",
                correlation_id=delivery_id,
            )

    # For workflow_run events, check if it's a completed failure
    elif event_type == "workflow_run":
        action = payload.get("action")
        conclusion = payload.get("workflow_run", {}).get("conclusion")

        if action != "completed" or conclusion != "failure":
            if action == "completed":
                branch = _extract_branch(payload, event_type=event_type)
                if branch:
                    await monitor_service.process_outcome(
                        repo=repo_name_hint,
                        branch=branch,
                        conclusion=conclusion,
                    )
            logger.debug(
                "Ignoring non-failure run event",
                extra={"action": action, "conclusion": conclusion},
            )
            return WebhookResponse(
                status="ignored",
                message="Workflow run is not a completed failure",
                correlation_id=delivery_id,
            )

        # workflow_run completed failure — proceed to normalization & dispatch
        logger.info(
            "Processing workflow_run failure event",
            extra={"conclusion": conclusion, "delivery_id": delivery_id},
        )

    installation_service = GitHubAppInstallationService(session)
    installation = await installation_service.get_by_repo_full_name(repo_full_name=repo_name_hint)
    if installation is None:
        return WebhookResponse(
            status="ignored",
            message="Repository is not onboarded for GitHub App integration",
            correlation_id=delivery_id,
        )

    payload_installation_id = _extract_installation_id(payload)
    if (
        payload_installation_id is not None
        and installation.installation_id != payload_installation_id
    ):
        return WebhookResponse(
            status="ignored",
            message="Repository installation_id mismatch; webhook ignored",
            correlation_id=delivery_id,
        )

    repo_config_service = RepositoryConfigService()
    runtime_config = await repo_config_service.resolve_for_repository(
        repo_full_name=repo_name_hint,
        installation_automation_mode=installation.automation_mode,
        ref=_extract_ref(payload, event_type=event_type),
    )
    payload["_sre_agent"] = {
        "installation": {
            "installation_id": installation.installation_id,
            "repo_full_name": installation.repo_full_name,
            "user_id": str(installation.user_id),
        },
        "repo_config": runtime_config.model_dump(),
    }

    delivery_store = WebhookDeliveryStore(session)
    is_new_delivery = await delivery_store.record_delivery(
        delivery_id=delivery_id,
        event_type=event_type,
        repository=repo_name_hint,
        status="received",
    )
    if not is_new_delivery:
        inc(
            "webhook_deduped",
            attributes={"provider": "github", "repo": repo_name_hint or "unknown"},
        )
        await publish_dashboard_event(
            event_type="ingestion",
            stage="ingest",
            status="duplicate",
            correlation_id=delivery_id,
            metadata={"provider": "github", "repo": repo_name_hint},
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
        key=f"webhook:repo:{repo_name_hint or 'unknown'}",
        limit=repo_rate_limit,
        window_seconds=60,
    )
    if not allowed:
        inc(
            "pipeline_throttled",
            attributes={
                "provider": "github",
                "repo": repo_name_hint or "unknown",
                "stage": "webhook",
            },
        )
        logger.warning(
            "Webhook throttled; delaying enqueue",
            extra={
                "repo": repo_name_hint,
                "delivery_id": delivery_id,
                "current": current,
                "retry_after": retry_after,
            },
        )

    # Normalize the event
    try:
        normalizer = GitHubEventNormalizer()
        normalized_event = normalizer.normalize(
            payload=payload,
            correlation_id=delivery_id,
            event_type=event_type,
        )
    except ValueError as e:
        logger.warning(
            "Failed to normalize event",
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
            "Failed to store event",
            extra={"error": str(e), "delivery_id": delivery_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database temporarily unavailable",
            headers={"Retry-After": "60"},
        )

    # If this is a duplicate, don't dispatch again
    if not is_new:
        logger.info(
            "Duplicate event - skipping dispatch",
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
        # Update status to dispatched
        await event_store.update_status(stored_event.id, EventStatus.DISPATCHED)
        await session.commit()

        headers = inject_trace_headers()

        if not allowed and retry_after > 0:
            inc(
                "pipeline_throttled",
                attributes={
                    "provider": "github",
                    "repo": repo_name_hint or "unknown",
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
            await publish_dashboard_event(
                event_type="ingestion",
                stage="ingest",
                status="queued_delayed",
                failure_id=str(stored_event.id),
                correlation_id=delivery_id,
                metadata={"retry_after": retry_after, "provider": "github"},
            )
            return WebhookResponse(
                status="throttled_delayed",
                message=f"Event accepted but throttled; delayed {retry_after}s",
                event_id=stored_event.id,
                correlation_id=delivery_id,
            )
        else:
            with start_span(
                "enqueue_pipeline",
                attributes={"delivery_id": delivery_id, "failure_id": str(stored_event.id)},
            ):
                process_pipeline_event.apply_async(
                    kwargs={"event_id": str(stored_event.id), "correlation_id": delivery_id},
                    headers=headers,
                )
        await publish_dashboard_event(
            event_type="ingestion",
            stage="ingest",
            status="queued",
            failure_id=str(stored_event.id),
            correlation_id=delivery_id,
            metadata={"provider": "github", "repo": normalized_event.repo},
        )

        logger.info(
            "Event dispatched for processing",
            extra={
                "event_id": str(stored_event.id),
                "repo": normalized_event.repo,
                "failure_type": normalized_event.failure_type,
            },
        )
    except Exception as e:
        logger.error(
            "Failed to dispatch event",
            extra={"error": str(e), "event_id": str(stored_event.id)},
            exc_info=True,
        )
        # Event is stored, so we return success but log the dispatch failure
        # The event can be reprocessed later

    return WebhookResponse(
        status="accepted",
        message="Event accepted and queued for processing",
        event_id=stored_event.id,
        correlation_id=delivery_id,
    )
