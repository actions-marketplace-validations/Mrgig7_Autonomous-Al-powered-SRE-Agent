"""Factory for creating notification manager from configuration.

This module provides utilities to initialize the notification system
based on application settings.
"""

import logging
from typing import Optional

from sre_agent.config import Settings
from sre_agent.notifications.base import NotificationLevel
from sre_agent.notifications.email_notifier import EmailConfig, EmailNotifier
from sre_agent.notifications.manager import (
    NotificationManager,
    NotificationManagerConfig,
)
from sre_agent.notifications.pagerduty_notifier import PagerDutyConfig, PagerDutyNotifier
from sre_agent.notifications.slack_notifier import SlackConfig, SlackNotifier
from sre_agent.notifications.teams_notifier import TeamsConfig, TeamsNotifier
from sre_agent.notifications.webhook_notifier import WebhookConfig, WebhookNotifier

logger = logging.getLogger(__name__)


def create_notification_manager(settings: Settings) -> NotificationManager:
    """Create and configure notification manager from settings.

    Args:
        settings: Application settings

    Returns:
        Configured NotificationManager instance
    """
    # Map string level to enum
    level_map = {
        "debug": NotificationLevel.DEBUG,
        "info": NotificationLevel.INFO,
        "warning": NotificationLevel.WARNING,
        "error": NotificationLevel.ERROR,
        "critical": NotificationLevel.CRITICAL,
    }

    min_level = level_map.get(settings.notification_min_level.lower(), NotificationLevel.INFO)

    # Create manager config
    manager_config = NotificationManagerConfig(
        parallel_dispatch=settings.notification_parallel_dispatch,
        rate_limit_enabled=settings.notification_rate_limit_enabled,
        rate_limit_max_per_window=settings.notification_rate_limit_per_minute,
        rate_limit_window_seconds=60,
        min_level=min_level,
    )

    manager = NotificationManager(config=manager_config)

    # Register Slack if enabled
    if settings.slack_enabled:
        slack_config = SlackConfig(
            webhook_url=settings.slack_webhook_url or None,
            bot_token=settings.slack_bot_token or None,
            signing_secret=settings.slack_signing_secret or None,
            default_channel=settings.slack_default_channel,
            critical_channel=settings.slack_critical_channel,
            approval_channel=settings.slack_approval_channel,
        )
        slack_notifier = SlackNotifier(config=slack_config, enabled=True)
        manager.register_notifier(slack_notifier)
        logger.info("Slack notifier registered")

    # Register Teams if enabled
    if settings.teams_enabled and settings.teams_webhook_url:
        teams_config = TeamsConfig(webhook_url=settings.teams_webhook_url)
        teams_notifier = TeamsNotifier(config=teams_config, enabled=True)
        manager.register_notifier(teams_notifier)
        logger.info("Teams notifier registered")

    # Register Email if enabled
    if settings.email_enabled:
        email_config = EmailConfig(
            smtp_host=settings.smtp_host or None,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user or None,
            smtp_password=settings.smtp_password or None,
            smtp_use_tls=settings.smtp_use_tls,
            sendgrid_api_key=settings.sendgrid_api_key or None,
            from_address=settings.smtp_from_address,
            from_name=settings.smtp_from_name,
            default_recipients=_parse_recipients(settings.email_default_recipients),
            critical_recipients=_parse_recipients(settings.email_critical_recipients),
        )
        email_notifier = EmailNotifier(config=email_config, enabled=True)
        manager.register_notifier(email_notifier)
        logger.info("Email notifier registered")

    # Register PagerDuty if enabled
    if settings.pagerduty_enabled and settings.pagerduty_routing_key:
        pd_config = PagerDutyConfig(
            routing_key=settings.pagerduty_routing_key,
            api_key=settings.pagerduty_api_key or None,
            auto_resolve=settings.pagerduty_auto_resolve,
        )
        pd_notifier = PagerDutyNotifier(config=pd_config, enabled=True)
        manager.register_notifier(pd_notifier)
        logger.info("PagerDuty notifier registered")

    # Register Webhook if enabled
    if settings.webhook_enabled and settings.webhook_url:
        webhook_config = WebhookConfig(
            url=settings.webhook_url,
            auth_type=settings.webhook_auth_type or None,
            auth_token=settings.webhook_auth_token or None,
            hmac_secret=settings.webhook_hmac_secret or None,
        )
        webhook_notifier = WebhookNotifier(config=webhook_config, enabled=True)
        manager.register_notifier(webhook_notifier)
        logger.info("Webhook notifier registered")

    return manager


def _parse_recipients(recipients_str: str) -> list[str]:
    """Parse comma-separated recipients string."""
    if not recipients_str:
        return []
    return [r.strip() for r in recipients_str.split(",") if r.strip()]


# Global notification manager instance
_notification_manager: Optional[NotificationManager] = None


def get_notification_manager(settings: Optional[Settings] = None) -> NotificationManager:
    """Get or create the global notification manager.

    Args:
        settings: Optional settings (uses default if not provided)

    Returns:
        NotificationManager instance
    """
    global _notification_manager

    if _notification_manager is None:
        if settings is None:
            from sre_agent.config import get_settings

            settings = get_settings()
        _notification_manager = create_notification_manager(settings)

    return _notification_manager


async def shutdown_notification_manager() -> None:
    """Shutdown the global notification manager."""
    global _notification_manager

    if _notification_manager is not None:
        await _notification_manager.close()
        _notification_manager = None
        logger.info("Notification manager shutdown complete")
