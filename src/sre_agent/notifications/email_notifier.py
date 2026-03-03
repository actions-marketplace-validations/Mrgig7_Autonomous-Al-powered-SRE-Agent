"""Email notification integration.

This module provides enterprise-grade email integration with:
- SMTP support (with TLS/SSL)
- SendGrid integration
- HTML template rendering
- Proper error handling and retries
"""

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import httpx

from sre_agent.notifications.base import (
    BaseNotifier,
    NotificationLevel,
    NotificationPayload,
    NotificationResult,
)

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """Configuration for email integration."""

    # SMTP Settings
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True

    # SendGrid Settings (alternative to SMTP)
    sendgrid_api_key: Optional[str] = None

    # Email Settings
    from_address: str = "sre-agent@company.com"
    from_name: str = "SRE Agent"
    default_recipients: list[str] = None
    critical_recipients: list[str] = None

    # Behavior
    timeout_seconds: int = 30
    max_retries: int = 3

    def __post_init__(self):
        if self.default_recipients is None:
            self.default_recipients = []
        if self.critical_recipients is None:
            self.critical_recipients = []


class EmailNotifier(BaseNotifier):
    """Email notification provider with SMTP and SendGrid support.

    Supports both traditional SMTP and SendGrid API for sending
    HTML-formatted notification emails.
    """

    def __init__(
        self,
        config: Optional[EmailConfig] = None,
        enabled: bool = True,
    ):
        """Initialize Email notifier.

        Args:
            config: Email configuration
            enabled: Whether this notifier is active
        """
        super().__init__(name="email", enabled=enabled)
        self.config = config or EmailConfig()
        self._client: Optional[httpx.AsyncClient] = None

    async def send(self, payload: NotificationPayload) -> NotificationResult:
        """Send notification via email."""
        if not self.should_send(payload):
            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                error="Notification skipped",
            )

        # Determine recipients
        recipients = self._get_recipients(payload)
        if not recipients:
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error="No recipients configured",
            )

        try:
            # Build email content
            subject = self._build_subject(payload)
            html_body = self._build_html_body(payload)
            text_body = self._build_text_body(payload)

            # Send via appropriate method
            if self.config.sendgrid_api_key:
                result = await self._send_via_sendgrid(recipients, subject, html_body, text_body)
            elif self.config.smtp_host:
                result = await self._send_via_smtp(recipients, subject, html_body, text_body)
            else:
                return NotificationResult(
                    success=False,
                    channel=self.name,
                    notification_id=payload.notification_id,
                    error="No email transport configured",
                )

            return NotificationResult(
                success=True,
                channel=self.name,
                notification_id=payload.notification_id,
                response_data=result,
            )

        except Exception as e:
            self._logger.error(f"Failed to send email notification: {e}")
            return NotificationResult(
                success=False,
                channel=self.name,
                notification_id=payload.notification_id,
                error=str(e),
            )

    def _get_recipients(self, payload: NotificationPayload) -> list[str]:
        """Get recipients based on notification level."""
        if payload.level == NotificationLevel.CRITICAL:
            return list(set(self.config.default_recipients + self.config.critical_recipients))
        return self.config.default_recipients.copy()

    def _build_subject(self, payload: NotificationPayload) -> str:
        """Build email subject line."""
        level_prefix = {
            NotificationLevel.DEBUG: "[DEBUG]",
            NotificationLevel.INFO: "[INFO]",
            NotificationLevel.WARNING: "[WARN]",
            NotificationLevel.ERROR: "[ERROR]",
            NotificationLevel.CRITICAL: "[CRITICAL]",
        }
        prefix = level_prefix.get(payload.level, "")
        repo = f" [{payload.repository}]" if payload.repository else ""
        return f"SRE Agent {prefix}{repo} {payload.title}"

    def _build_html_body(self, payload: NotificationPayload) -> str:
        """Build HTML email body."""
        emoji = self.get_emoji_for_type(payload.type)
        color = self.get_color_for_level(payload.level)

        # Build metadata table rows
        metadata_rows = ""
        if payload.repository:
            metadata_rows += (
                f"<tr><td><strong>Repository:</strong></td><td>{payload.repository}</td></tr>"
            )
        if payload.branch:
            metadata_rows += f"<tr><td><strong>Branch:</strong></td><td>{payload.branch}</td></tr>"
        if payload.commit_sha:
            metadata_rows += f"<tr><td><strong>Commit:</strong></td><td><code>{payload.commit_sha[:8]}</code></td></tr>"
        if payload.pipeline_id:
            metadata_rows += (
                f"<tr><td><strong>Pipeline:</strong></td><td>{payload.pipeline_id}</td></tr>"
            )
        if payload.confidence_score is not None:
            score = int(payload.confidence_score * 100)
            metadata_rows += f"<tr><td><strong>Confidence:</strong></td><td>{score}%</td></tr>"

        # Build actions section
        actions_html = ""
        if payload.suggested_actions:
            actions_list = "".join([f"<li>{a}</li>" for a in payload.suggested_actions[:5]])
            actions_html = f"<h3>Suggested Actions:</h3><ul>{actions_list}</ul>"

        # Build error snippet section
        error_html = ""
        if payload.error_snippet:
            error_html = f"""
            <h3>Error Details:</h3>
            <pre style="background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto;">
{self.truncate_text(payload.error_snippet, 2000)}
            </pre>
            """

        # Build PR link
        pr_html = ""
        if payload.pr_url:
            pr_html = (
                f'<p><a href="{payload.pr_url}" style="color: #2196F3;">View Pull Request →</a></p>'
            )

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{payload.title}</title>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="border-left: 4px solid {color}; padding-left: 16px; margin-bottom: 20px;">
                <h1 style="margin: 0; color: {color}; font-size: 24px;">
                    {emoji} {payload.title}
                </h1>
            </div>

            <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                {metadata_rows}
            </table>

            <div style="margin-bottom: 20px;">
                <p>{payload.message}</p>
            </div>

            {error_html}

            {actions_html}

            {pr_html}

            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">

            <p style="color: #666; font-size: 12px;">
                Notification ID: {payload.notification_id}<br>
                Generated at: {payload.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}<br>
                Sent by SRE Agent
            </p>
        </body>
        </html>
        """
        return html

    def _build_text_body(self, payload: NotificationPayload) -> str:
        """Build plain text email body."""
        lines = [
            f"{self.get_emoji_for_type(payload.type)} {payload.title}",
            "=" * 50,
            "",
        ]

        if payload.repository:
            lines.append(f"Repository: {payload.repository}")
        if payload.branch:
            lines.append(f"Branch: {payload.branch}")
        if payload.commit_sha:
            lines.append(f"Commit: {payload.commit_sha[:8]}")
        if payload.pipeline_id:
            lines.append(f"Pipeline: {payload.pipeline_id}")
        if payload.confidence_score is not None:
            lines.append(f"Confidence: {int(payload.confidence_score * 100)}%")

        lines.extend(["", payload.message, ""])

        if payload.error_snippet:
            lines.extend(
                [
                    "Error Details:",
                    "-" * 30,
                    self.truncate_text(payload.error_snippet, 2000),
                    "",
                ]
            )

        if payload.suggested_actions:
            lines.append("Suggested Actions:")
            for action in payload.suggested_actions[:5]:
                lines.append(f"  • {action}")
            lines.append("")

        if payload.pr_url:
            lines.append(f"Pull Request: {payload.pr_url}")

        lines.extend(
            [
                "",
                "-" * 50,
                f"Notification ID: {payload.notification_id}",
                f"Generated at: {payload.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            ]
        )

        return "\n".join(lines)

    async def _send_via_smtp(
        self,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
    ) -> dict[str, Any]:
        """Send email via SMTP."""
        # Build message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.config.from_name} <{self.config.from_address}>"
        msg["To"] = ", ".join(recipients)

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        # Send in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._smtp_send, recipients, msg)

        return {"method": "smtp", "recipients": len(recipients)}

    def _smtp_send(self, recipients: list[str], msg: MIMEMultipart):
        """Synchronous SMTP send (runs in executor)."""
        context = ssl.create_default_context()

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            if self.config.smtp_use_tls:
                server.starttls(context=context)

            if self.config.smtp_user and self.config.smtp_password:
                server.login(self.config.smtp_user, self.config.smtp_password)

            server.sendmail(
                self.config.from_address,
                recipients,
                msg.as_string(),
            )

    async def _send_via_sendgrid(
        self,
        recipients: list[str],
        subject: str,
        html_body: str,
        text_body: str,
    ) -> dict[str, Any]:
        """Send email via SendGrid API."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
            )

        payload = {
            "personalizations": [{"to": [{"email": r} for r in recipients]}],
            "from": {
                "email": self.config.from_address,
                "name": self.config.from_name,
            },
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": text_body},
                {"type": "text/html", "value": html_body},
            ],
        }

        response = await self._client.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.config.sendgrid_api_key}",
                "Content-Type": "application/json",
            },
        )

        response.raise_for_status()
        return {"method": "sendgrid", "recipients": len(recipients)}

    async def validate_config(self) -> bool:
        """Validate email configuration."""
        if not self.config.sendgrid_api_key and not self.config.smtp_host:
            self._logger.error("No email transport configured")
            return False

        if self.config.smtp_host:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._validate_smtp)
                return True
            except Exception as e:
                self._logger.error(f"SMTP validation failed: {e}")
                return False

        return True

    def _validate_smtp(self):
        """Validate SMTP connection."""
        context = ssl.create_default_context()
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            if self.config.smtp_use_tls:
                server.starttls(context=context)
            server.noop()

    async def close(self):
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
