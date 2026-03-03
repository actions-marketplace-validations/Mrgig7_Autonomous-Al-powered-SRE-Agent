from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.models.webhook_deliveries import WebhookDelivery

logger = logging.getLogger(__name__)


class WebhookDeliveryStore:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_delivery(
        self,
        *,
        delivery_id: str,
        event_type: str,
        repository: str | None,
        status: str = "received",
        details: str | None = None,
    ) -> bool:
        delivery = WebhookDelivery(
            delivery_id=delivery_id,
            event_type=event_type,
            repository=repository,
            status=status,
            details=details,
        )
        self.session.add(delivery)
        try:
            await self.session.flush()
            return True
        except IntegrityError:
            await self.session.rollback()
            logger.info(
                "Webhook delivery duplicate ignored",
                extra={
                    "delivery_id": delivery_id,
                    "event_type": event_type,
                    "repository": repository,
                },
            )
            return False
        except Exception as e:
            try:
                await self.session.rollback()
            except Exception:
                pass
            logger.warning(
                "Webhook delivery record skipped (db unavailable)",
                extra={
                    "delivery_id": delivery_id,
                    "event_type": event_type,
                    "repository": repository,
                    "error": str(e),
                },
            )
            return True


def compute_fallback_delivery_id(payload: dict[str, Any], *, provider: str) -> str:
    from sre_agent.core.redis_service import RedisService

    stable = {"provider": provider, "payload": payload}
    return RedisService.hash_payload(stable)
