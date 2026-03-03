from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from sre_agent.models.events import Base


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    delivery_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("idx_webhook_deliveries_repo_received", "repository", "received_at"),)
