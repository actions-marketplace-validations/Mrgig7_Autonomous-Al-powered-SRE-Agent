"""Base notification classes and interfaces.

This module defines the abstract base classes and data structures
for the notification system, ensuring consistent behavior across
all notification channels.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class NotificationLevel(str, Enum):
    """Severity level for notifications.

    Determines how the notification is displayed and whether
    it should trigger escalation.
    """

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationType(str, Enum):
    """Type of notification event."""

    FAILURE_DETECTED = "failure_detected"
    FIX_GENERATED = "fix_generated"
    FIX_APPROVED = "fix_approved"
    FIX_REJECTED = "fix_rejected"
    PR_CREATED = "pr_created"
    PR_MERGED = "pr_merged"
    SANDBOX_PASSED = "sandbox_passed"
    SANDBOX_FAILED = "sandbox_failed"
    ESCALATION = "escalation"
    SYSTEM_ALERT = "system_alert"


@dataclass
class NotificationPayload:
    """Payload for a notification to be sent.

    This is the unified data structure that all notifiers consume,
    ensuring consistent information across all channels.
    """

    # Required fields
    type: NotificationType
    level: NotificationLevel
    title: str
    message: str

    # Context information
    repository: Optional[str] = None
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    pipeline_id: Optional[str] = None
    failure_id: Optional[UUID] = None
    fix_id: Optional[UUID] = None
    pr_url: Optional[str] = None

    # Additional metadata
    error_snippet: Optional[str] = None
    confidence_score: Optional[float] = None
    suggested_actions: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    # Internal tracking
    notification_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Channel routing hints
    channels: list[str] = field(default_factory=list)  # Empty = all enabled
    priority: int = 1  # 1 (highest) to 5 (lowest)

    def to_dict(self) -> dict[str, Any]:
        """Convert payload to dictionary for serialization."""
        return {
            "notification_id": str(self.notification_id),
            "type": self.type.value,
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "repository": self.repository,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "pipeline_id": self.pipeline_id,
            "failure_id": str(self.failure_id) if self.failure_id else None,
            "fix_id": str(self.fix_id) if self.fix_id else None,
            "pr_url": self.pr_url,
            "error_snippet": self.error_snippet,
            "confidence_score": self.confidence_score,
            "suggested_actions": self.suggested_actions,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "priority": self.priority,
        }


@dataclass
class NotificationResult:
    """Result of a notification send attempt."""

    success: bool
    channel: str
    notification_id: UUID
    message_id: Optional[str] = None  # Provider-specific message ID
    error: Optional[str] = None
    retry_after: Optional[int] = None  # Seconds to wait before retry
    response_data: Optional[dict[str, Any]] = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary for logging/storage."""
        return {
            "success": self.success,
            "channel": self.channel,
            "notification_id": str(self.notification_id),
            "message_id": self.message_id,
            "error": self.error,
            "retry_after": self.retry_after,
            "sent_at": self.sent_at.isoformat(),
        }


class BaseNotifier(ABC):
    """Abstract base class for all notification providers.

    All notifiers must implement this interface to ensure
    consistent behavior and proper integration with the
    notification manager.
    """

    def __init__(self, name: str, enabled: bool = True):
        """Initialize the notifier.

        Args:
            name: Unique identifier for this notifier
            enabled: Whether this notifier is active
        """
        self.name = name
        self.enabled = enabled
        self._logger = logging.getLogger(f"{__name__}.{name}")

    @abstractmethod
    async def send(self, payload: NotificationPayload) -> NotificationResult:
        """Send a notification.

        Args:
            payload: The notification data to send

        Returns:
            NotificationResult indicating success or failure
        """
        pass

    @abstractmethod
    async def validate_config(self) -> bool:
        """Validate that the notifier is properly configured.

        Returns:
            True if configuration is valid and channel is reachable
        """
        pass

    def should_send(self, payload: NotificationPayload) -> bool:
        """Determine if this notifier should handle the payload.

        Args:
            payload: The notification to evaluate

        Returns:
            True if this notifier should send the notification
        """
        if not self.enabled:
            return False

        # If channels are specified, only send if this channel is included
        if payload.channels and self.name not in payload.channels:
            return False

        return True

    def get_color_for_level(self, level: NotificationLevel) -> str:
        """Get a color code for the notification level.

        Args:
            level: The severity level

        Returns:
            Hex color code
        """
        colors = {
            NotificationLevel.DEBUG: "#808080",  # Gray
            NotificationLevel.INFO: "#2196F3",  # Blue
            NotificationLevel.WARNING: "#FF9800",  # Orange
            NotificationLevel.ERROR: "#F44336",  # Red
            NotificationLevel.CRITICAL: "#9C27B0",  # Purple
        }
        return colors.get(level, "#808080")

    def get_emoji_for_level(self, level: NotificationLevel) -> str:
        """Get an emoji for the notification level.

        Args:
            level: The severity level

        Returns:
            Emoji string
        """
        emojis = {
            NotificationLevel.DEBUG: "🔍",
            NotificationLevel.INFO: "ℹ️",
            NotificationLevel.WARNING: "⚠️",
            NotificationLevel.ERROR: "❌",
            NotificationLevel.CRITICAL: "🚨",
        }
        return emojis.get(level, "📢")

    def get_emoji_for_type(self, ntype: NotificationType) -> str:
        """Get an emoji for the notification type.

        Args:
            ntype: The notification type

        Returns:
            Emoji string
        """
        emojis = {
            NotificationType.FAILURE_DETECTED: "🔴",
            NotificationType.FIX_GENERATED: "🔧",
            NotificationType.FIX_APPROVED: "✅",
            NotificationType.FIX_REJECTED: "❎",
            NotificationType.PR_CREATED: "📝",
            NotificationType.PR_MERGED: "🎉",
            NotificationType.SANDBOX_PASSED: "✅",
            NotificationType.SANDBOX_FAILED: "💥",
            NotificationType.ESCALATION: "📢",
            NotificationType.SYSTEM_ALERT: "🔔",
        }
        return emojis.get(ntype, "📣")

    def truncate_text(self, text: str, max_length: int = 1000) -> str:
        """Truncate text to a maximum length.

        Args:
            text: The text to truncate
            max_length: Maximum allowed length

        Returns:
            Truncated text with ellipsis if needed
        """
        if len(text) <= max_length:
            return text
        return text[: max_length - 3] + "..."
