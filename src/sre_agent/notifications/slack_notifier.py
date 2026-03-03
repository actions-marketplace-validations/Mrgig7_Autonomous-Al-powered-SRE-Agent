"""Slack notification integration.

This module provides enterprise-grade Slack integration with support for:
- Rich Block Kit messages
- Interactive buttons for approval/rejection
- Thread replies for updates
- Channel routing based on severity
- Rate limiting and retry logic
"""

import asyncio
import hashlib
import hmac
import logging
import time
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
class SlackConfig:
    """Configuration for Slack integration."""

    webhook_url: Optional[str] = None
    bot_token: Optional[str] = None
    signing_secret: Optional[str] = None
    default_channel: str = "#sre-alerts"
    critical_channel: str = "#sre-critical"
    approval_channel: str = "#sre-approvals"
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0


class SlackNotifier(BaseNotifier):
    """Slack notification provider with Block Kit support.

    Supports both webhook-based notifications and Bot API
    for interactive features like buttons and thread replies.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        bot_token: Optional[str] = None,
        signing_secret: Optional[str] = None,
        config: Optional[SlackConfig] = None,
        enabled: bool = True,
    ):
        """Initialize Slack notifier.

        Args:
            webhook_url: Slack incoming webhook URL
            bot_token: Slack bot token for API access
            signing_secret: Secret for verifying Slack requests
            config: Full configuration object
            enabled: Whether this notifier is active
        """
        super().__init__(name="slack", enabled=enabled)

        if config:
            self.config = config
        else:
            self.config = SlackConfig(
                webhook_url=webhook_url,
                bot_token=bot_token,
                signing_secret=signing_secret,
            )

        self._client: Optional[httpx.AsyncClient] = None
        self._rate_limit_reset: float = 0

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                headers=self._get_headers(),
            )
        return self._client

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for Slack API."""
        headers = {"Content-Type": "application/json"}
        if self.config.bot_token:
            headers["Authorization"] = f"Bearer {self.config.bot_token}"
        return headers

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        """Send notification to Slack.

        Uses Block Kit for rich formatting, with fallback to
        simple text if block construction fails.
        """
        if not self.should_send(payload):
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                message_id=None,
                error="Notification skipped (not enabled or filtered)",
            )

        # Respect rate limits
        if time.time() < self._rate_limit_reset:
            wait_time = self._rate_limit_reset - time.time()
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Rate limited",
                retry_after=int(wait_time) + 1,
            )

        try:
            # Build Block Kit message
            blocks = self._build_blocks(payload)
            slack_payload = {
                "blocks": blocks,
                "text": f"{self.get_emoji_for_type(payload.type)} {payload.title}",  # Fallback
            }

            # Determine target channel
            channel = self._get_channel_for_payload(payload)
            if self.config.bot_token:
                slack_payload["channel"] = channel

            # Send with retry logic
            response = await self._send_with_retry(slack_payload)

            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                message_id=response.get("ts"),
                response_data=response,
            )

        except Exception as e:
            self._logger.error(f"Failed to send Slack notification: {e}")
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error=str(e),
            )

    async def _send_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send message with retry logic for transient failures."""
        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                if self.config.bot_token:
                    # Use Bot API
                    response = await client.post(
                        "https://slack.com/api/chat.postMessage",
                        json=payload,
                    )
                else:
                    # Use webhook
                    response = await client.post(
                        self.config.webhook_url,
                        json=payload,
                    )

                # Check for rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    self._rate_limit_reset = time.time() + retry_after
                    raise Exception(f"Rate limited, retry after {retry_after}s")

                response.raise_for_status()

                # Parse response
                if self.config.bot_token:
                    data = response.json()
                    if not data.get("ok"):
                        raise Exception(f"Slack API error: {data.get('error')}")
                    return data
                else:
                    return {"ok": True}

            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))

        raise last_error or Exception("Failed after max retries")

    def _get_channel_for_payload(self, payload: NotificationPayload) -> str:
        """Determine the appropriate channel based on payload characteristics."""
        if payload.level == NotificationLevel.CRITICAL:
            return self.config.critical_channel

        if payload.type in (
            NotificationType.FIX_GENERATED,
            NotificationType.FIX_APPROVED,
            NotificationType.FIX_REJECTED,
        ):
            return self.config.approval_channel

        return self.config.default_channel

    def _build_blocks(self, payload: NotificationPayload) -> list[dict[str, Any]]:
        """Build Slack Block Kit blocks for the notification."""
        blocks = []

        # Header block
        emoji = self.get_emoji_for_type(payload.type)
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {payload.title}",
                    "emoji": True,
                },
            }
        )

        # Context block with metadata
        context_elements = []
        if payload.repository:
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Repo:* `{payload.repository}`",
                }
            )
        if payload.branch:
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Branch:* `{payload.branch}`",
                }
            )
        if payload.commit_sha:
            context_elements.append(
                {
                    "type": "mrkdwn",
                    "text": f"*Commit:* `{payload.commit_sha[:8]}`",
                }
            )

        if context_elements:
            blocks.append(
                {
                    "type": "context",
                    "elements": context_elements[:10],  # Slack limit
                }
            )

        # Divider
        blocks.append({"type": "divider"})

        # Main message section
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": self.truncate_text(payload.message, 2900),
                },
            }
        )

        # Error snippet if present
        if payload.error_snippet:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```{self.truncate_text(payload.error_snippet, 2900)}```",
                    },
                }
            )

        # Confidence score if present
        if payload.confidence_score is not None:
            confidence_pct = int(payload.confidence_score * 100)
            confidence_bar = self._build_confidence_bar(payload.confidence_score)
            blocks.append(
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Confidence Score:* {confidence_pct}%",
                        },
                        {
                            "type": "mrkdwn",
                            "text": confidence_bar,
                        },
                    ],
                }
            )

        # Suggested actions
        if payload.suggested_actions:
            actions_text = "\n".join([f"• {action}" for action in payload.suggested_actions[:5]])
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Suggested Actions:*\n{actions_text}",
                    },
                }
            )

        # Action buttons for fix-related notifications
        if payload.type == NotificationType.FIX_GENERATED and payload.fix_id:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Approve Fix"},
                            "style": "primary",
                            "action_id": "approve_fix",
                            "value": str(payload.fix_id),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ Reject Fix"},
                            "style": "danger",
                            "action_id": "reject_fix",
                            "value": str(payload.fix_id),
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "🔍 View Details"},
                            "action_id": "view_details",
                            "value": str(payload.failure_id) if payload.failure_id else "",
                        },
                    ],
                }
            )

        # PR link if available
        if payload.pr_url:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Pull Request:* <{payload.pr_url}|View PR>",
                    },
                }
            )

        # Footer with timestamp
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"🕐 {payload.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')} | 🆔 `{str(payload.notification_id)[:8]}`",
                    }
                ],
            }
        )

        return blocks

    def _build_confidence_bar(self, score: float) -> str:
        """Build a visual confidence bar."""
        filled = int(score * 10)
        empty = 10 - filled
        return f"{'🟢' * filled}{'⚪' * empty}"

    async def validate_config(self) -> bool:
        """Validate Slack configuration by testing connectivity."""
        if not self.config.webhook_url and not self.config.bot_token:
            self._logger.error("No webhook URL or bot token configured")
            return False

        try:
            if self.config.bot_token:
                client = await self._get_client()
                response = await client.post(
                    "https://slack.com/api/auth.test",
                    json={},
                )
                data = response.json()
                if data.get("ok"):
                    self._logger.info(f"Slack connected as: {data.get('user')}")
                    return True
                else:
                    self._logger.error(f"Slack auth failed: {data.get('error')}")
                    return False
            else:
                # Webhook doesn't have a test endpoint, assume valid if URL is set
                return True

        except Exception as e:
            self._logger.error(f"Slack validation failed: {e}")
            return False

    async def send_thread_reply(
        self,
        channel: str,
        thread_ts: str,
        message: str,
    ) -> NotificationResult:
        """Send a reply to an existing thread.

        Args:
            channel: The channel containing the thread
            thread_ts: Timestamp of the parent message
            message: Reply content

        Returns:
            NotificationResult
        """
        if not self.config.bot_token:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=None,
                error="Thread replies require bot token",
            )

        try:
            client = await self._get_client()
            response = await client.post(
                "https://slack.com/api/chat.postMessage",
                json={
                    "channel": channel,
                    "thread_ts": thread_ts,
                    "text": message,
                },
            )
            data = response.json()

            return NotificationResult(
                success=data.get("ok", False),
                channel=self.name,
                notification_id=None,
                message_id=data.get("ts"),
                error=data.get("error"),
            )

        except Exception as e:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=None,
                error=str(e),
            )

    def verify_request_signature(
        self,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> bool:
        """Verify a request from Slack using the signing secret.

        Args:
            body: Raw request body
            timestamp: X-Slack-Request-Timestamp header
            signature: X-Slack-Signature header

        Returns:
            True if signature is valid
        """
        if not self.config.signing_secret:
            return False

        # Check timestamp to prevent replay attacks
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:  # 5 minute window
                return False
        except ValueError:
            return False

        # Compute expected signature
        sig_basestring = f"v0:{timestamp}:{body.decode()}"
        expected = (
            "v0="
            + hmac.new(
                self.config.signing_secret.encode(),
                sig_basestring.encode(),
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(expected, signature)

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
