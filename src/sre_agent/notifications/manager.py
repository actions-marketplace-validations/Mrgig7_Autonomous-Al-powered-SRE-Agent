"""Notification manager for orchestrating multiple notification channels.

This module provides the central coordination point for all notifications,
handling:
- Multi-channel dispatch
- Fallback logic for failed channels
- Notification history tracking
- Rate limiting across channels
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional
from uuid import UUID

from sre_agent.notifications.base import (
    BaseNotifier,
    NotificationLevel,
    NotificationPayload,
    NotificationResult,
    NotificationType,
)

logger = logging.getLogger(__name__)


@dataclass
class NotificationManagerConfig:
    """Configuration for the notification manager."""

    # Dispatch behavior
    parallel_dispatch: bool = True  # Send to all channels in parallel
    fail_silently: bool = True  # Don't raise on individual channel failures

    # Fallback settings
    enable_fallback: bool = True  # Use fallback channels on failure
    fallback_order: list[str] = field(default_factory=lambda: ["email", "slack", "webhook"])

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_per_window: int = 30

    # Filtering
    min_level: NotificationLevel = NotificationLevel.INFO
    excluded_types: list[NotificationType] = field(default_factory=list)

    # History
    keep_history: bool = True
    history_max_size: int = 1000


@dataclass
class NotificationHistory:
    """Record of a sent notification."""

    notification_id: UUID
    payload: NotificationPayload
    results: dict[str, NotificationResult]
    sent_at: datetime
    overall_success: bool


class NotificationManager:
    """Central manager for all notification channels.

    Provides unified interface for sending notifications across
    multiple channels with proper error handling, fallbacks,
    and rate limiting.
    """

    def __init__(self, config: Optional[NotificationManagerConfig] = None):
        """Initialize the notification manager.

        Args:
            config: Manager configuration
        """
        self.config = config or NotificationManagerConfig()
        self._notifiers: dict[str, BaseNotifier] = {}
        self._history: list[NotificationHistory] = []
        self._rate_limit_counts: dict[str, list[datetime]] = {}
        self._logger = logging.getLogger(__name__)

    def register_notifier(self, notifier: BaseNotifier) -> None:
        """Register a notification channel.

        Args:
            notifier: The notifier instance to register
        """
        if notifier.name in self._notifiers:
            self._logger.warning(f"Overwriting existing notifier: {notifier.name}")

        self._notifiers[notifier.name] = notifier
        self._logger.info(f"Registered notifier: {notifier.name} (enabled={notifier.enabled})")

    def unregister_notifier(self, name: str) -> bool:
        """Unregister a notification channel.

        Args:
            name: Name of the notifier to remove

        Returns:
            True if notifier was removed
        """
        if name in self._notifiers:
            del self._notifiers[name]
            self._logger.info(f"Unregistered notifier: {name}")
            return True
        return False

    def get_notifier(self, name: str) -> Optional[BaseNotifier]:
        """Get a registered notifier by name.

        Args:
            name: Name of the notifier

        Returns:
            The notifier or None if not found
        """
        return self._notifiers.get(name)

    def list_notifiers(self) -> list[str]:
        """List all registered notifier names.

        Returns:
            List of notifier names
        """
        return list(self._notifiers.keys())

    async def send(
        self,
        payload: NotificationPayload,
        channels: Optional[list[str]] = None,
    ) -> dict[str, NotificationResult]:
        """Send a notification to all or specified channels.

        Args:
            payload: The notification to send
            channels: Optional list of specific channels to use

        Returns:
            Dictionary of channel name to result
        """
        # Apply filters
        if not self._should_send(payload):
            self._logger.debug(f"Notification filtered out: {payload.notification_id}")
            return {}

        # Check rate limit
        if not self._check_rate_limit():
            self._logger.warning("Rate limit exceeded, notification dropped")
            return {}

        # Determine target notifiers
        if channels:
            payload.channels = channels
            targets = [
                (name, n) for name, n in self._notifiers.items() if name in channels and n.enabled
            ]
        else:
            targets = [(name, n) for name, n in self._notifiers.items() if n.should_send(payload)]

        if not targets:
            self._logger.warning("No enabled notifiers for payload")
            return {}

        # Dispatch to channels
        if self.config.parallel_dispatch:
            results = await self._parallel_dispatch(payload, targets)
        else:
            results = await self._sequential_dispatch(payload, targets)

        # Handle fallbacks if needed
        if self.config.enable_fallback:
            failed_channels = [name for name, r in results.items() if not r.success]
            if failed_channels:
                fallback_results = await self._handle_fallbacks(payload, failed_channels, results)
                results.update(fallback_results)

        # Record history
        if self.config.keep_history:
            self._record_history(payload, results)

        # Log summary
        success_count = sum(1 for r in results.values() if r.success)
        self._logger.info(
            f"Notification {payload.notification_id}: "
            f"{success_count}/{len(results)} channels succeeded"
        )

        return results

    async def _parallel_dispatch(
        self,
        payload: NotificationPayload,
        targets: list[tuple[str, BaseNotifier]],
    ) -> dict[str, NotificationResult]:
        """Dispatch to all channels in parallel."""
        tasks = [self._safe_send(name, notifier, payload) for name, notifier in targets]

        results = await asyncio.gather(*tasks)
        return {name: result for (name, _), result in zip(targets, results)}

    async def _sequential_dispatch(
        self,
        payload: NotificationPayload,
        targets: list[tuple[str, BaseNotifier]],
    ) -> dict[str, NotificationResult]:
        """Dispatch to channels sequentially."""
        results = {}
        for name, notifier in targets:
            results[name] = await self._safe_send(name, notifier, payload)
        return results

    async def _safe_send(
        self,
        name: str,
        notifier: BaseNotifier,
        payload: NotificationPayload,
    ) -> NotificationResult:
        """Send notification with error handling."""
        try:
            return await notifier.send(payload)
        except Exception as e:
            self._logger.error(f"Notifier {name} failed: {e}")
            if not self.config.fail_silently:
                raise
            return NotificationResult(
                success=False,
                channel=name,
                notification_id=payload.notification_id,
                error=str(e),
            )

    async def _handle_fallbacks(
        self,
        payload: NotificationPayload,
        failed_channels: list[str],
        existing_results: dict[str, NotificationResult],
    ) -> dict[str, NotificationResult]:
        """Try fallback channels for failed notifications."""
        fallback_results = {}

        for fallback_name in self.config.fallback_order:
            # Skip if already tried or already succeeded elsewhere
            if fallback_name in existing_results:
                continue

            notifier = self._notifiers.get(fallback_name)
            if not notifier or not notifier.enabled:
                continue

            self._logger.info(
                f"Trying fallback channel {fallback_name} "
                f"for notification {payload.notification_id}"
            )

            result = await self._safe_send(fallback_name, notifier, payload)
            fallback_results[f"{fallback_name}_fallback"] = result

            if result.success:
                break  # Stop on first successful fallback

        return fallback_results

    def _should_send(self, payload: NotificationPayload) -> bool:
        """Check if notification should be sent based on filters."""
        # Check minimum level
        level_order = [
            NotificationLevel.DEBUG,
            NotificationLevel.INFO,
            NotificationLevel.WARNING,
            NotificationLevel.ERROR,
            NotificationLevel.CRITICAL,
        ]

        payload_level_idx = level_order.index(payload.level)
        min_level_idx = level_order.index(self.config.min_level)

        if payload_level_idx < min_level_idx:
            return False

        # Check excluded types
        if payload.type in self.config.excluded_types:
            return False

        return True

    def _check_rate_limit(self) -> bool:
        """Check if rate limit allows sending."""
        if not self.config.rate_limit_enabled:
            return True

        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=self.config.rate_limit_window_seconds)

        # Clean old entries
        key = "global"
        if key not in self._rate_limit_counts:
            self._rate_limit_counts[key] = []

        self._rate_limit_counts[key] = [
            ts for ts in self._rate_limit_counts[key] if ts > window_start
        ]

        # Check limit
        if len(self._rate_limit_counts[key]) >= self.config.rate_limit_max_per_window:
            return False

        # Record this request
        self._rate_limit_counts[key].append(now)
        return True

    def _record_history(
        self,
        payload: NotificationPayload,
        results: dict[str, NotificationResult],
    ) -> None:
        """Record notification in history."""
        overall_success = any(r.success for r in results.values())

        history_entry = NotificationHistory(
            notification_id=payload.notification_id,
            payload=payload,
            results=results,
            sent_at=datetime.now(UTC),
            overall_success=overall_success,
        )

        self._history.append(history_entry)

        # Trim if needed
        if len(self._history) > self.config.history_max_size:
            self._history = self._history[-self.config.history_max_size :]

    def get_history(
        self,
        limit: int = 100,
        success_only: bool = False,
    ) -> list[NotificationHistory]:
        """Get recent notification history.

        Args:
            limit: Maximum number of entries
            success_only: Only return successful notifications

        Returns:
            List of history entries
        """
        history = self._history[-limit:]

        if success_only:
            history = [h for h in history if h.overall_success]

        return history

    async def validate_all(self) -> dict[str, bool]:
        """Validate all registered notifiers.

        Returns:
            Dictionary of notifier name to validation status
        """
        results = {}

        for name, notifier in self._notifiers.items():
            try:
                results[name] = await notifier.validate_config()
            except Exception as e:
                self._logger.error(f"Validation failed for {name}: {e}")
                results[name] = False

        return results

    async def close(self) -> None:
        """Close all notifiers and cleanup resources."""
        for name, notifier in self._notifiers.items():
            try:
                await notifier.close()
            except Exception as e:
                self._logger.error(f"Error closing {name}: {e}")

        self._notifiers.clear()
        self._history.clear()


# Convenience functions for quick notifications
async def send_failure_notification(
    manager: NotificationManager,
    repository: str,
    branch: str,
    commit_sha: str,
    pipeline_id: str,
    error_message: str,
    error_snippet: Optional[str] = None,
) -> dict[str, NotificationResult]:
    """Send a standardized failure notification.

    Helper function for common failure notification pattern.
    """
    payload = NotificationPayload(
        type=NotificationType.FAILURE_DETECTED,
        level=NotificationLevel.ERROR,
        title=f"CI/CD Failure in {repository}",
        message=error_message,
        repository=repository,
        branch=branch,
        commit_sha=commit_sha,
        pipeline_id=pipeline_id,
        error_snippet=error_snippet,
    )

    return await manager.send(payload)


async def send_fix_notification(
    manager: NotificationManager,
    repository: str,
    fix_id: UUID,
    confidence_score: float,
    suggested_actions: list[str],
) -> dict[str, NotificationResult]:
    """Send a standardized fix generated notification."""
    level = NotificationLevel.INFO if confidence_score >= 0.8 else NotificationLevel.WARNING

    payload = NotificationPayload(
        type=NotificationType.FIX_GENERATED,
        level=level,
        title=f"Fix Generated for {repository}",
        message=f"An automated fix has been generated with {int(confidence_score * 100)}% confidence.",
        repository=repository,
        fix_id=fix_id,
        confidence_score=confidence_score,
        suggested_actions=suggested_actions,
    )

    return await manager.send(payload)


async def send_pr_notification(
    manager: NotificationManager,
    repository: str,
    pr_url: str,
    fix_id: UUID,
) -> dict[str, NotificationResult]:
    """Send a standardized PR created notification."""
    payload = NotificationPayload(
        type=NotificationType.PR_CREATED,
        level=NotificationLevel.INFO,
        title=f"Pull Request Created for {repository}",
        message="A pull request has been created with the automated fix.",
        repository=repository,
        pr_url=pr_url,
        fix_id=fix_id,
    )

    return await manager.send(payload)
