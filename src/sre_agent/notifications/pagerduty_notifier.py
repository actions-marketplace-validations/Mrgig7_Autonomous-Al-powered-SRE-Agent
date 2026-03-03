"""PagerDuty notification integration.

This module provides enterprise-grade PagerDuty integration with:
- Event creation via Events API v2
- Incident management
- Escalation policies
- Proper severity mapping
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from sre_agent.notifications.base import (
    BaseNotifier,
    NotificationLevel,
    NotificationPayload,
    NotificationResult,
    NotificationType,
)

logger = logging.getLogger(__name__)


@dataclass
class PagerDutyConfig:
    """Configuration for PagerDuty integration."""

    routing_key: str  # Integration key from PagerDuty service
    api_key: Optional[str] = None  # REST API key for advanced operations
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0

    # Severity mapping thresholds
    critical_threshold: float = 0.9  # Confidence below this for critical issues
    auto_resolve: bool = True  # Auto-resolve on fix success


class PagerDutyNotifier(BaseNotifier):
    """PagerDuty notification provider with Events API v2.

    Supports creating incidents, acknowledgments, and resolutions
    through PagerDuty's Events API.
    """

    EVENTS_API_URL = "https://events.pagerduty.com/v2/enqueue"

    def __init__(
        self,
        routing_key: Optional[str] = None,
        config: Optional[PagerDutyConfig] = None,
        enabled: bool = True,
    ):
        """Initialize PagerDuty notifier.

        Args:
            routing_key: PagerDuty integration routing key
            config: Full configuration object
            enabled: Whether this notifier is active
        """
        super().__init__(name="pagerduty", enabled=enabled)

        if config:
            self.config = config
        elif routing_key:
            self.config = PagerDutyConfig(routing_key=routing_key)
        else:
            self.config = None

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds if self.config else 30,
            )
        return self._client

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        """Send notification to PagerDuty.

        Creates an incident or sends event based on notification type.
        """
        if not self.should_send(payload):
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Notification skipped",
            )

        if not self.config or not self.config.routing_key:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error="PagerDuty routing key not configured",
            )

        # Determine event action
        event_action = self._get_event_action(payload)

        # Skip if this notification type shouldn't create PagerDuty events
        if event_action is None:
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Notification type doesn't require PagerDuty event",
            )

        try:
            # Build PagerDuty event
            event = self._build_event(payload, event_action)

            # Send with retry logic
            response = await self._send_with_retry(event)

            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                message_id=response.get("dedup_key"),
                response_data=response,
            )

        except Exception as e:
            self._logger.error(f"Failed to send PagerDuty notification: {e}")
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error=str(e),
            )

    def _get_event_action(self, payload: NotificationPayload) -> Optional[str]:
        """Determine PagerDuty event action based on notification type."""
        triggers = {
            NotificationType.FAILURE_DETECTED: "trigger",
            NotificationType.SANDBOX_FAILED: "trigger",
            NotificationType.ESCALATION: "trigger",
            NotificationType.SYSTEM_ALERT: "trigger",
        }

        resolves = {
            NotificationType.FIX_APPROVED: "resolve",
            NotificationType.PR_MERGED: "resolve",
            NotificationType.SANDBOX_PASSED: "acknowledge",
        }

        if payload.type in triggers:
            return triggers[payload.type]

        if self.config.auto_resolve and payload.type in resolves:
            return resolves[payload.type]

        return None

    def _build_event(self, payload: NotificationPayload, action: str) -> dict[str, Any]:
        """Build PagerDuty Events API v2 payload."""
        # Determine severity
        severity = self._map_severity(payload.level)

        # Build dedup key for correlation
        dedup_key = self._build_dedup_key(payload)

        # Build custom details
        custom_details = {
            "notification_id": str(payload.notification_id),
            "type": payload.type.value,
            "level": payload.level.value,
        }

        if payload.repository:
            custom_details["repository"] = payload.repository
        if payload.branch:
            custom_details["branch"] = payload.branch
        if payload.commit_sha:
            custom_details["commit_sha"] = payload.commit_sha
        if payload.pipeline_id:
            custom_details["pipeline_id"] = payload.pipeline_id
        if payload.confidence_score is not None:
            custom_details["confidence_score"] = payload.confidence_score
        if payload.error_snippet:
            custom_details["error_snippet"] = self.truncate_text(payload.error_snippet, 1000)
        if payload.suggested_actions:
            custom_details["suggested_actions"] = payload.suggested_actions[:5]
        if payload.pr_url:
            custom_details["pr_url"] = payload.pr_url

        # Build links
        links = []
        if payload.pr_url:
            links.append(
                {
                    "href": payload.pr_url,
                    "text": "View Pull Request",
                }
            )

        event = {
            "routing_key": self.config.routing_key,
            "event_action": action,
            "dedup_key": dedup_key,
            "payload": {
                "summary": self.truncate_text(
                    f"{self.get_emoji_for_type(payload.type)} {payload.title}: {payload.message}",
                    1024,
                ),
                "severity": severity,
                "source": payload.repository or "sre-agent",
                "component": payload.branch or "unknown",
                "group": (
                    payload.repository.split("/")[0]
                    if payload.repository and "/" in payload.repository
                    else "default"
                ),
                "class": payload.type.value,
                "custom_details": custom_details,
                "timestamp": payload.created_at.isoformat() + "Z",
            },
            "links": links if links else None,
        }

        # Remove None values
        event = {k: v for k, v in event.items() if v is not None}

        return event

    def _map_severity(self, level: NotificationLevel) -> str:
        """Map notification level to PagerDuty severity."""
        mapping = {
            NotificationLevel.DEBUG: "info",
            NotificationLevel.INFO: "info",
            NotificationLevel.WARNING: "warning",
            NotificationLevel.ERROR: "error",
            NotificationLevel.CRITICAL: "critical",
        }
        return mapping.get(level, "info")

    def _build_dedup_key(self, payload: NotificationPayload) -> str:
        """Build deduplication key for incident correlation."""
        parts = [
            payload.repository or "unknown",
            payload.pipeline_id or str(payload.failure_id) or str(payload.notification_id),
        ]
        return "/".join(parts)[:255]

    async def _send_with_retry(self, event: dict[str, Any]) -> dict[str, Any]:
        """Send event with retry logic."""
        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = await client.post(
                    self.EVENTS_API_URL,
                    json=event,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()

            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))

        raise last_error or Exception("Failed after max retries")

    async def acknowledge_incident(self, dedup_key: str) -> NotificationResult:
        """Acknowledge an existing incident.

        Args:
            dedup_key: The deduplication key of the incident

        Returns:
            NotificationResult
        """
        event = {
            "routing_key": self.config.routing_key,
            "event_action": "acknowledge",
            "dedup_key": dedup_key,
        }

        try:
            response = await self._send_with_retry(event)
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=None,
                message_id=response.get("dedup_key"),
                response_data=response,
            )
        except Exception as e:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=None,
                error=str(e),
            )

    async def resolve_incident(self, dedup_key: str) -> NotificationResult:
        """Resolve an existing incident.

        Args:
            dedup_key: The deduplication key of the incident

        Returns:
            NotificationResult
        """
        event = {
            "routing_key": self.config.routing_key,
            "event_action": "resolve",
            "dedup_key": dedup_key,
        }

        try:
            response = await self._send_with_retry(event)
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=None,
                message_id=response.get("dedup_key"),
                response_data=response,
            )
        except Exception as e:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=None,
                error=str(e),
            )

    async def validate_config(self) -> bool:
        """Validate PagerDuty configuration."""
        if not self.config or not self.config.routing_key:
            self._logger.error("PagerDuty routing key not configured")
            return False

        # Validate routing key format (32 character hex string)
        if len(self.config.routing_key) != 32:
            self._logger.warning("PagerDuty routing key may be invalid")

        return True

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
