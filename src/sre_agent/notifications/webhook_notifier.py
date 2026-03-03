"""Generic webhook notification integration.

This module provides a flexible webhook notifier for:
- Custom integrations
- Third-party services
- Internal notification systems
- Generic HTTP endpoints
"""

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from sre_agent.notifications.base import (
    BaseNotifier,
    NotificationPayload,
    NotificationResult,
)

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    """Configuration for generic webhook integration."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)

    # Authentication
    auth_type: Optional[str] = None  # "bearer", "basic", "hmac", None
    auth_token: Optional[str] = None
    auth_username: Optional[str] = None
    auth_password: Optional[str] = None
    hmac_secret: Optional[str] = None
    hmac_header: str = "X-Signature-256"

    # Payload customization
    payload_template: Optional[dict[str, Any]] = None  # Custom payload structure
    wrap_in_key: Optional[str] = None  # Wrap payload in this key

    # Behavior
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    verify_ssl: bool = True


class WebhookNotifier(BaseNotifier):
    """Generic webhook notification provider.

    Supports various authentication methods and payload customization
    for integration with any HTTP endpoint.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        config: Optional[WebhookConfig] = None,
        name: str = "webhook",
        enabled: bool = True,
    ):
        """Initialize Webhook notifier.

        Args:
            url: Webhook URL
            config: Full configuration object
            name: Unique identifier for this webhook instance
            enabled: Whether this notifier is active
        """
        super().__init__(name=name, enabled=enabled)

        if config:
            self.config = config
        elif url:
            self.config = WebhookConfig(url=url)
        else:
            self.config = None

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds if self.config else 30,
                verify=self.config.verify_ssl if self.config else True,
            )
        return self._client

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        """Send notification to webhook endpoint."""
        if not self.should_send(payload):
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Notification skipped",
            )

        if not self.config or not self.config.url:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Webhook URL not configured",
            )

        try:
            # Build payload
            webhook_payload = self._build_payload(payload)

            # Build headers
            headers = self._build_headers(webhook_payload)

            # Send with retry logic
            response = await self._send_with_retry(webhook_payload, headers)

            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                response_data=response,
            )

        except Exception as e:
            self._logger.error(f"Failed to send webhook notification: {e}")
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error=str(e),
            )

    def _build_payload(self, payload: NotificationPayload) -> dict[str, Any]:
        """Build webhook payload."""
        # Start with basic payload
        base_payload = payload.to_dict()

        # Apply custom template if configured
        if self.config.payload_template:
            webhook_payload = self._apply_template(
                self.config.payload_template,
                base_payload,
            )
        else:
            webhook_payload = base_payload

        # Wrap in key if configured
        if self.config.wrap_in_key:
            webhook_payload = {self.config.wrap_in_key: webhook_payload}

        return webhook_payload

    def _apply_template(
        self,
        template: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply template to payload data.

        Supports simple variable substitution with ${key} syntax.
        """
        result = {}

        for key, value in template.items():
            if isinstance(value, str):
                # Replace ${key} with data values
                for data_key, data_value in data.items():
                    placeholder = f"${{{data_key}}}"
                    if placeholder in value:
                        if value == placeholder:
                            value = data_value
                        else:
                            value = value.replace(
                                placeholder, str(data_value) if data_value else ""
                            )
                result[key] = value
            elif isinstance(value, dict):
                result[key] = self._apply_template(value, data)
            elif isinstance(value, list):
                result[key] = [
                    self._apply_template(item, data) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    def _build_headers(self, payload: dict[str, Any]) -> dict[str, str]:
        """Build HTTP headers with authentication."""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SRE-Agent/1.0",
        }

        # Add custom headers
        headers.update(self.config.headers)

        # Add authentication
        if self.config.auth_type == "bearer" and self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"

        elif self.config.auth_type == "basic" and self.config.auth_username:
            import base64

            credentials = f"{self.config.auth_username}:{self.config.auth_password or ''}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        elif self.config.auth_type == "hmac" and self.config.hmac_secret:
            payload_bytes = json.dumps(payload, sort_keys=True).encode()
            signature = hmac.new(
                self.config.hmac_secret.encode(),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers[self.config.hmac_header] = f"sha256={signature}"

        return headers

    async def _send_with_retry(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Send request with retry logic."""
        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                if self.config.method.upper() == "POST":
                    response = await client.post(
                        self.config.url,
                        json=payload,
                        headers=headers,
                    )
                elif self.config.method.upper() == "PUT":
                    response = await client.put(
                        self.config.url,
                        json=payload,
                        headers=headers,
                    )
                else:
                    raise ValueError(f"Unsupported method: {self.config.method}")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()

                # Try to parse JSON response, fallback to status
                try:
                    return response.json()
                except Exception:
                    return {"status": response.status_code, "ok": True}

            except Exception as e:
                last_error = e
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay * (attempt + 1))

        raise last_error or Exception("Failed after max retries")

    async def validate_config(self) -> bool:
        """Validate webhook configuration."""
        if not self.config or not self.config.url:
            self._logger.error("Webhook URL not configured")
            return False

        # Validate URL format
        if not self.config.url.startswith(("http://", "https://")):
            self._logger.error("Invalid webhook URL format")
            return False

        return True

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
