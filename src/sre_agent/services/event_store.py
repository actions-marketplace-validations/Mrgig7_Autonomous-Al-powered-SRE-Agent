"""Event storage service with idempotent persistence.

Handles storing normalized pipeline events to PostgreSQL with
idempotency guarantees using UPSERT (INSERT ... ON CONFLICT).
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.models.events import EventStatus, PipelineEvent
from sre_agent.schemas.normalized import NormalizedPipelineEvent

logger = logging.getLogger(__name__)


class EventStore:
    """
    Service for idempotent event storage.

    Uses PostgreSQL UPSERT to handle duplicate events gracefully.
    """

    def __init__(self, session: AsyncSession):
        """Initialize with database session."""
        self.session = session

    async def store_event(
        self,
        event: NormalizedPipelineEvent,
    ) -> tuple[PipelineEvent, bool]:
        """
        Store a normalized event with idempotency handling.

        Uses INSERT ... ON CONFLICT to detect and handle duplicates.
        If the event already exists, updates the updated_at timestamp
        but does not reprocess.

        Args:
            event: Normalized pipeline event to store

        Returns:
            Tuple of (PipelineEvent, is_new) where is_new is True
            if this is a new event, False if it was a duplicate.
        """
        # Prepare event data
        event_data = {
            "idempotency_key": event.idempotency_key,
            "ci_provider": (
                event.ci_provider.value
                if hasattr(event.ci_provider, "value")
                else event.ci_provider
            ),
            "raw_payload": event.raw_payload,
            "pipeline_id": event.pipeline_id,
            "repo": event.repo,
            "commit_sha": event.commit_sha,
            "branch": event.branch,
            "stage": event.stage,
            "failure_type": (
                event.failure_type.value
                if hasattr(event.failure_type, "value")
                else event.failure_type
            ),
            "error_message": event.error_message,
            "status": EventStatus.PENDING.value,
            "correlation_id": event.correlation_id,
            "event_timestamp": event.event_timestamp,
        }

        # PostgreSQL UPSERT
        stmt = insert(PipelineEvent).values(**event_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=["idempotency_key"],
            set_={
                "updated_at": stmt.excluded.updated_at,
            },
        ).returning(PipelineEvent.id, PipelineEvent.created_at, PipelineEvent.updated_at)

        result = await self.session.execute(stmt)
        row = result.fetchone()

        if row is None:
            # This shouldn't happen with RETURNING, but handle it safely
            logger.error(
                "Failed to store event - no row returned",
                extra={"idempotency_key": event.idempotency_key},
            )
            raise RuntimeError("Failed to store event")

        event_id, created_at, updated_at = row

        # Determine if this is a new event or duplicate
        # If updated_at is None or equals created_at, it's new
        is_new = updated_at is None or updated_at == created_at

        # Fetch the full event
        stored_event = await self.get_event_by_id(event_id)
        if stored_event is None:
            raise RuntimeError("Failed to retrieve stored event")

        if is_new:
            logger.info(
                "Stored new pipeline event",
                extra={
                    "event_id": str(event_id),
                    "idempotency_key": event.idempotency_key,
                    "repo": event.repo,
                    "correlation_id": event.correlation_id,
                },
            )
        else:
            logger.info(
                "Duplicate event detected",
                extra={
                    "event_id": str(event_id),
                    "idempotency_key": event.idempotency_key,
                    "correlation_id": event.correlation_id,
                },
            )

        return stored_event, is_new

    async def get_event(self, idempotency_key: str) -> PipelineEvent | None:
        """Retrieve an event by its idempotency key."""
        stmt = select(PipelineEvent).where(PipelineEvent.idempotency_key == idempotency_key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_event_by_id(self, event_id: UUID) -> PipelineEvent | None:
        """Retrieve an event by its database ID."""
        stmt = select(PipelineEvent).where(PipelineEvent.id == event_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        event_id: UUID,
        status: EventStatus,
    ) -> None:
        """
        Update the processing status of an event.

        Args:
            event_id: Database ID of the event
            status: New status to set
        """
        event = await self.get_event_by_id(event_id)
        if event is None:
            logger.warning(
                "Attempted to update status of non-existent event",
                extra={"event_id": str(event_id)},
            )
            return

        event.status = status.value
        await self.session.flush()

        logger.debug(
            "Updated event status",
            extra={
                "event_id": str(event_id),
                "status": status.value,
            },
        )

    async def get_pending_events(self, limit: int = 100) -> list[PipelineEvent]:
        """Get pending events that haven't been dispatched yet."""
        stmt = (
            select(PipelineEvent)
            .where(PipelineEvent.status == EventStatus.PENDING.value)
            .order_by(PipelineEvent.created_at)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
