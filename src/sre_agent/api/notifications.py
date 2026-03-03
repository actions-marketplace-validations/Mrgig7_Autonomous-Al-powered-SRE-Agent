"""API routes for notification management.

This module provides REST endpoints for:
- Sending manual notifications
- Viewing notification history
- Managing notification channels
- Testing notification configuration
"""

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from sre_agent.notifications.base import (
    NotificationLevel,
    NotificationPayload,
    NotificationType,
)
from sre_agent.notifications.factory import get_notification_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])


# Request/Response Models
class SendNotificationRequest(BaseModel):
    """Request body for sending a notification."""

    type: NotificationType = Field(..., description="Notification type")
    level: NotificationLevel = Field(NotificationLevel.INFO, description="Severity level")
    title: str = Field(..., min_length=1, max_length=200, description="Notification title")
    message: str = Field(..., min_length=1, max_length=4000, description="Message body")
    repository: Optional[str] = Field(None, description="Repository identifier")
    branch: Optional[str] = Field(None, description="Branch name")
    commit_sha: Optional[str] = Field(None, max_length=40, description="Commit SHA")
    pipeline_id: Optional[str] = Field(None, description="Pipeline ID")
    failure_id: Optional[UUID] = Field(None, description="Failure event UUID")
    fix_id: Optional[UUID] = Field(None, description="Fix UUID")
    pr_url: Optional[str] = Field(None, description="Pull request URL")
    error_snippet: Optional[str] = Field(None, max_length=2000, description="Error snippet")
    confidence_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence score")
    suggested_actions: list[str] = Field(default_factory=list, description="Suggested actions")
    channels: list[str] = Field(default_factory=list, description="Target channels")
    priority: int = Field(1, ge=1, le=5, description="Priority (1=highest)")


class NotificationResultResponse(BaseModel):
    """Response for a single channel result."""

    success: bool
    channel: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    sent_at: str


class SendNotificationResponse(BaseModel):
    """Response for notification send request."""

    notification_id: str
    success_count: int
    total_count: int
    results: dict[str, NotificationResultResponse]


class ChannelStatusResponse(BaseModel):
    """Response for channel status."""

    name: str
    enabled: bool
    valid: bool
    type: str


class HistoryEntryResponse(BaseModel):
    """Response for a history entry."""

    notification_id: str
    type: str
    level: str
    title: str
    repository: Optional[str]
    sent_at: str
    overall_success: bool
    channels_notified: list[str]


# Endpoints
@router.post(
    "/send",
    response_model=SendNotificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a notification",
    description="Send a notification to all or specified channels",
)
async def send_notification(request: SendNotificationRequest) -> SendNotificationResponse:
    """Send a notification to configured channels."""
    try:
        manager = get_notification_manager()

        payload = NotificationPayload(
            type=request.type,
            level=request.level,
            title=request.title,
            message=request.message,
            repository=request.repository,
            branch=request.branch,
            commit_sha=request.commit_sha,
            pipeline_id=request.pipeline_id,
            failure_id=request.failure_id,
            fix_id=request.fix_id,
            pr_url=request.pr_url,
            error_snippet=request.error_snippet,
            confidence_score=request.confidence_score,
            suggested_actions=request.suggested_actions,
            channels=request.channels,
            priority=request.priority,
        )

        results = await manager.send(payload, channels=request.channels or None)

        result_responses = {
            channel: NotificationResultResponse(
                success=result.success,
                channel=result.channel,
                message_id=result.message_id,
                error=result.error,
                sent_at=result.sent_at.isoformat(),
            )
            for channel, result in results.items()
        }

        return SendNotificationResponse(
            notification_id=str(payload.notification_id),
            success_count=sum(1 for r in results.values() if r.success),
            total_count=len(results),
            results=result_responses,
        )

    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get(
    "/channels",
    response_model=list[ChannelStatusResponse],
    summary="List notification channels",
    description="Get status of all registered notification channels",
)
async def list_channels() -> list[ChannelStatusResponse]:
    """List all registered notification channels and their status."""
    manager = get_notification_manager()

    channels = []
    validation_results = await manager.validate_all()

    for name in manager.list_notifiers():
        notifier = manager.get_notifier(name)
        channels.append(
            ChannelStatusResponse(
                name=name,
                enabled=notifier.enabled if notifier else False,
                valid=validation_results.get(name, False),
                type=type(notifier).__name__ if notifier else "unknown",
            )
        )

    return channels


@router.post(
    "/channels/{channel_name}/test",
    response_model=NotificationResultResponse,
    summary="Test a notification channel",
    description="Send a test notification to a specific channel",
)
async def test_channel(channel_name: str) -> NotificationResultResponse:
    """Send a test notification to a specific channel."""
    manager = get_notification_manager()
    notifier = manager.get_notifier(channel_name)

    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_name}' not found",
        )

    payload = NotificationPayload(
        type=NotificationType.SYSTEM_ALERT,
        level=NotificationLevel.INFO,
        title="Test Notification from SRE Agent",
        message="This is a test notification to verify the channel configuration.",
        suggested_actions=["No action required - this is a test"],
    )

    try:
        result = await notifier.send(payload)

        return NotificationResultResponse(
            success=result.success,
            channel=result.channel,
            message_id=result.message_id,
            error=result.error,
            sent_at=result.sent_at.isoformat(),
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get(
    "/history",
    response_model=list[HistoryEntryResponse],
    summary="Get notification history",
    description="Retrieve recent notification history",
)
async def get_history(
    limit: int = 50,
    success_only: bool = False,
) -> list[HistoryEntryResponse]:
    """Get recent notification history."""
    manager = get_notification_manager()
    history = manager.get_history(limit=limit, success_only=success_only)

    return [
        HistoryEntryResponse(
            notification_id=str(entry.notification_id),
            type=entry.payload.type.value,
            level=entry.payload.level.value,
            title=entry.payload.title,
            repository=entry.payload.repository,
            sent_at=entry.sent_at.isoformat(),
            overall_success=entry.overall_success,
            channels_notified=list(entry.results.keys()),
        )
        for entry in history
    ]


@router.post(
    "/channels/{channel_name}/enable",
    status_code=status.HTTP_200_OK,
    summary="Enable a notification channel",
)
async def enable_channel(channel_name: str) -> dict[str, Any]:
    """Enable a notification channel."""
    manager = get_notification_manager()
    notifier = manager.get_notifier(channel_name)

    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_name}' not found",
        )

    notifier.enabled = True
    return {"channel": channel_name, "enabled": True}


@router.post(
    "/channels/{channel_name}/disable",
    status_code=status.HTTP_200_OK,
    summary="Disable a notification channel",
)
async def disable_channel(channel_name: str) -> dict[str, Any]:
    """Disable a notification channel."""
    manager = get_notification_manager()
    notifier = manager.get_notifier(channel_name)

    if not notifier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{channel_name}' not found",
        )

    notifier.enabled = False
    return {"channel": channel_name, "enabled": False}
