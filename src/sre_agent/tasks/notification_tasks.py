"""Celery tasks for asynchronous notification dispatch.

This module provides background tasks for sending notifications
without blocking the main request processing.
"""

import logging
from typing import Any, Optional
from uuid import UUID

from celery import shared_task

from sre_agent.notifications.base import (
    NotificationLevel,
    NotificationPayload,
    NotificationType,
)
from sre_agent.notifications.factory import get_notification_manager

logger = logging.getLogger(__name__)


@shared_task(
    name="sre_agent.tasks.notifications.send_notification",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def send_notification_task(
    self,
    notification_type: str,
    level: str,
    title: str,
    message: str,
    repository: Optional[str] = None,
    branch: Optional[str] = None,
    commit_sha: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    failure_id: Optional[str] = None,
    fix_id: Optional[str] = None,
    pr_url: Optional[str] = None,
    error_snippet: Optional[str] = None,
    confidence_score: Optional[float] = None,
    suggested_actions: Optional[list[str]] = None,
    tags: Optional[dict[str, str]] = None,
    channels: Optional[list[str]] = None,
    priority: int = 1,
) -> dict[str, Any]:
    """Send a notification asynchronously.

    This task is designed to be called from anywhere in the application
    to dispatch notifications without blocking.

    Args:
        notification_type: Type of notification (from NotificationType)
        level: Severity level (from NotificationLevel)
        title: Notification title
        message: Main message body
        repository: Repository identifier
        branch: Branch name
        commit_sha: Commit SHA
        pipeline_id: CI/CD pipeline ID
        failure_id: UUID of the failure event
        fix_id: UUID of the generated fix
        pr_url: URL of the created PR
        error_snippet: Error log snippet
        confidence_score: Fix confidence score (0.0-1.0)
        suggested_actions: List of suggested actions
        tags: Additional metadata tags
        channels: Specific channels to notify
        priority: Notification priority (1-5)

    Returns:
        Dictionary with send results
    """
    import asyncio

    try:
        # Map string enums
        ntype = NotificationType(notification_type)
        nlevel = NotificationLevel(level)

        # Build payload
        payload = NotificationPayload(
            type=ntype,
            level=nlevel,
            title=title,
            message=message,
            repository=repository,
            branch=branch,
            commit_sha=commit_sha,
            pipeline_id=pipeline_id,
            failure_id=UUID(failure_id) if failure_id else None,
            fix_id=UUID(fix_id) if fix_id else None,
            pr_url=pr_url,
            error_snippet=error_snippet,
            confidence_score=confidence_score,
            suggested_actions=suggested_actions or [],
            tags=tags or {},
            channels=channels or [],
            priority=priority,
        )

        # Get manager and send
        manager = get_notification_manager()

        # Run async send in event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(manager.send(payload))
        finally:
            loop.close()

        # Convert results to serializable format
        serializable_results = {channel: result.to_dict() for channel, result in results.items()}

        success_count = sum(1 for r in results.values() if r.success)
        total_count = len(results)

        logger.info(
            f"Notification {payload.notification_id} sent: "
            f"{success_count}/{total_count} channels succeeded"
        )

        return {
            "notification_id": str(payload.notification_id),
            "success_count": success_count,
            "total_count": total_count,
            "results": serializable_results,
        }

    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        raise


@shared_task(
    name="sre_agent.tasks.notifications.send_failure_detected",
    bind=True,
)
def send_failure_detected_task(
    self,
    repository: str,
    branch: str,
    commit_sha: str,
    pipeline_id: str,
    failure_id: str,
    error_message: str,
    error_snippet: Optional[str] = None,
    failure_type: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience task for failure detection notifications."""
    title = f"CI/CD Failure Detected in {repository}"

    suggested_actions = [
        "Review the error logs for root cause",
        "Check recent commits for potential issues",
        "Wait for automated fix generation",
    ]

    return send_notification_task.delay(
        notification_type=NotificationType.FAILURE_DETECTED.value,
        level=NotificationLevel.ERROR.value,
        title=title,
        message=error_message,
        repository=repository,
        branch=branch,
        commit_sha=commit_sha,
        pipeline_id=pipeline_id,
        failure_id=failure_id,
        error_snippet=error_snippet,
        suggested_actions=suggested_actions,
        tags={"failure_type": failure_type} if failure_type else None,
    ).get()


@shared_task(
    name="sre_agent.tasks.notifications.send_fix_generated",
    bind=True,
)
def send_fix_generated_task(
    self,
    repository: str,
    fix_id: str,
    failure_id: str,
    confidence_score: float,
    fix_summary: str,
    files_changed: list[str],
) -> dict[str, Any]:
    """Convenience task for fix generation notifications."""
    title = f"Fix Generated for {repository}"

    message = f"{fix_summary}\n\nFiles changed: {', '.join(files_changed)}"

    level = NotificationLevel.INFO if confidence_score >= 0.8 else NotificationLevel.WARNING

    suggested_actions = []
    if confidence_score >= 0.9:
        suggested_actions.append("High confidence - consider auto-approval")
    elif confidence_score >= 0.7:
        suggested_actions.append("Review the proposed changes")
    else:
        suggested_actions.append("Low confidence - manual review required")
        suggested_actions.append("Consider alternative approaches")

    return send_notification_task.delay(
        notification_type=NotificationType.FIX_GENERATED.value,
        level=level.value,
        title=title,
        message=message,
        repository=repository,
        failure_id=failure_id,
        fix_id=fix_id,
        confidence_score=confidence_score,
        suggested_actions=suggested_actions,
    ).get()


@shared_task(
    name="sre_agent.tasks.notifications.send_pr_created",
    bind=True,
)
def send_pr_created_task(
    self,
    repository: str,
    pr_url: str,
    fix_id: str,
    pr_number: int,
    pr_title: str,
) -> dict[str, Any]:
    """Convenience task for PR creation notifications."""
    title = f"Pull Request #{pr_number} Created"
    message = f"PR Title: {pr_title}\n\nA pull request has been created with the automated fix."

    return send_notification_task.delay(
        notification_type=NotificationType.PR_CREATED.value,
        level=NotificationLevel.INFO.value,
        title=title,
        message=message,
        repository=repository,
        pr_url=pr_url,
        fix_id=fix_id,
        suggested_actions=["Review the pull request", "Run additional tests if needed"],
    ).get()


@shared_task(
    name="sre_agent.tasks.notifications.send_sandbox_result",
    bind=True,
)
def send_sandbox_result_task(
    self,
    repository: str,
    fix_id: str,
    passed: bool,
    test_output: Optional[str] = None,
) -> dict[str, Any]:
    """Convenience task for sandbox validation results."""
    if passed:
        title = f"Sandbox Validation Passed for {repository}"
        ntype = NotificationType.SANDBOX_PASSED
        level = NotificationLevel.INFO
        message = "The automated fix has passed sandbox validation and is ready for PR creation."
        suggested_actions = ["Proceed with PR creation", "Review validation logs"]
    else:
        title = f"Sandbox Validation Failed for {repository}"
        ntype = NotificationType.SANDBOX_FAILED
        level = NotificationLevel.WARNING
        message = (
            "The automated fix failed sandbox validation. Manual intervention may be required."
        )
        suggested_actions = [
            "Review validation output",
            "Consider manual fix",
            "Retry with different approach",
        ]

    return send_notification_task.delay(
        notification_type=ntype.value,
        level=level.value,
        title=title,
        message=message,
        repository=repository,
        fix_id=fix_id,
        error_snippet=test_output if not passed else None,
        suggested_actions=suggested_actions,
    ).get()
