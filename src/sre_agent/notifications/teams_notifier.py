"""Microsoft Teams notification integration.

This module provides enterprise-grade Microsoft Teams integration with:
- Adaptive Card support for rich messages
- ActionSet for interactive buttons
- Proper error handling and retries
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
class TeamsConfig:
    """Configuration for Microsoft Teams integration."""

    webhook_url: str
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0


class TeamsNotifier(BaseNotifier):
    """Microsoft Teams notification provider with Adaptive Cards.

    Uses the incoming webhook connector to send rich notifications
    with Adaptive Card formatting.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        config: Optional[TeamsConfig] = None,
        enabled: bool = True,
    ):
        """Initialize Teams notifier.

        Args:
            webhook_url: Teams incoming webhook URL
            config: Full configuration object
            enabled: Whether this notifier is active
        """
        super().__init__(name="teams", enabled=enabled)

        if config:
            self.config = config
        elif webhook_url:
            self.config = TeamsConfig(webhook_url=webhook_url)
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
        """Send notification to Microsoft Teams.

        Uses Adaptive Cards for rich formatting.
        """
        if not self.should_send(payload):
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Notification skipped",
            )

        if not self.config or not self.config.webhook_url:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Teams webhook URL not configured",
            )

        try:
            # Build Adaptive Card
            card = self._build_adaptive_card(payload)

            # Send with retry logic
            response = await self._send_with_retry(card)

            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                response_data=response,
            )

        except Exception as e:
            self._logger.error(f"Failed to send Teams notification: {e}")
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error=str(e),
            )

    async def _send_with_retry(self, card: dict[str, Any]) -> dict[str, Any]:
        """Send message with retry logic."""
        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = await client.post(
                    self.config.webhook_url,
                    json=card,
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()
                return {"status": "sent"}

            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))

        raise last_error or Exception("Failed after max retries")

    def _build_adaptive_card(self, payload: NotificationPayload) -> dict[str, Any]:
        """Build Microsoft Teams Adaptive Card."""
        emoji = self.get_emoji_for_type(payload.type)

        # Build card body
        body = []

        # Header with colored container
        body.append(
            {
                "type": "TextBlock",
                "size": "Large",
                "weight": "Bolder",
                "text": f"{emoji} {payload.title}",
                "wrap": True,
                "color": self._get_teams_color(payload.level),
            }
        )

        # Fact set for metadata
        facts = []
        if payload.repository:
            facts.append({"title": "Repository", "value": payload.repository})
        if payload.branch:
            facts.append({"title": "Branch", "value": payload.branch})
        if payload.commit_sha:
            facts.append({"title": "Commit", "value": payload.commit_sha[:8]})
        if payload.pipeline_id:
            facts.append({"title": "Pipeline", "value": payload.pipeline_id})
        if payload.confidence_score is not None:
            facts.append(
                {
                    "title": "Confidence",
                    "value": f"{int(payload.confidence_score * 100)}%",
                }
            )

        if facts:
            body.append(
                {
                    "type": "FactSet",
                    "facts": facts,
                }
            )

        # Main message
        body.append(
            {
                "type": "TextBlock",
                "text": self.truncate_text(payload.message, 2000),
                "wrap": True,
            }
        )

        # Error snippet
        if payload.error_snippet:
            body.append(
                {
                    "type": "TextBlock",
                    "text": self.truncate_text(payload.error_snippet, 1000),
                    "wrap": True,
                    "fontType": "Monospace",
                }
            )

        # Suggested actions
        if payload.suggested_actions:
            body.append(
                {
                    "type": "TextBlock",
                    "text": "**Suggested Actions:**",
                    "wrap": True,
                }
            )
            for action in payload.suggested_actions[:5]:
                body.append(
                    {
                        "type": "TextBlock",
                        "text": f"• {action}",
                        "wrap": True,
                    }
                )

        # Build actions
        actions = []

        if payload.pr_url:
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "View Pull Request",
                    "url": payload.pr_url,
                }
            )

        if payload.type == NotificationType.FIX_GENERATED and payload.fix_id:
            actions.append(
                {
                    "type": "Action.OpenUrl",
                    "title": "View Fix Details",
                    "url": f"{{{{dashboard_url}}}}/fixes/{payload.fix_id}",
                }
            )

        # Build final card
        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.4",
                        "body": body,
                        "actions": actions if actions else None,
                        "msteams": {
                            "width": "Full",
                        },
                    },
                }
            ],
        }

        return card

    def _get_teams_color(self, level: NotificationLevel) -> str:
        """Get Teams color name for notification level."""
        mapping = {
            NotificationLevel.DEBUG: "Default",
            NotificationLevel.INFO: "Accent",
            NotificationLevel.WARNING: "Warning",
            NotificationLevel.ERROR: "Attention",
            NotificationLevel.CRITICAL: "Attention",
        }
        return mapping.get(level, "Default")

    async def validate_config(self) -> bool:
        """Validate Teams configuration."""
        if not self.config or not self.config.webhook_url:
            self._logger.error("Teams webhook URL not configured")
            return False

        # Teams doesn't have a test endpoint, verify URL format
        if not self.config.webhook_url.startswith("https://"):
            self._logger.error("Invalid Teams webhook URL format")
            return False

        return True

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
