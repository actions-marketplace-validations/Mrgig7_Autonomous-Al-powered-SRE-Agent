"""Audit log service with async buffering.

This module provides production-grade audit logging for:
- User actions (login, logout, approvals)
- System events (failures, fixes, PRs)
- API access logging
- Security events

Designed for high-throughput with:
- Async buffering to avoid blocking requests
- Batch inserts for efficiency
- Redis queue for reliability
- Automatic log rotation/archival
"""

import asyncio
import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sre_agent.models.user import AuditAction

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    """A single audit log entry."""

    action: AuditAction

    # Resource being acted upon
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None

    # Actor information
    user_id: Optional[UUID] = None
    user_email: Optional[str] = None

    # Request context
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None

    # Additional details
    details: dict[str, Any] = field(default_factory=dict)

    # Outcome
    success: bool = True
    error_message: Optional[str] = None

    # Metadata
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "action": self.action.value if isinstance(self.action, AuditAction) else self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "user_id": str(self.user_id) if self.user_id else None,
            "user_email": self.user_email,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "details": self.details,
            "success": self.success,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class AuditServiceConfig:
    """Configuration for the audit service."""

    # Buffer settings
    buffer_size: int = 1000
    flush_interval_seconds: float = 5.0
    batch_size: int = 100

    # Redis queue settings (for reliability)
    use_redis_queue: bool = True
    redis_queue_key: str = "sre_agent:audit:queue"

    # Database settings
    table_name: str = "audit_logs"

    # Retention
    retention_days: int = 90
    archive_enabled: bool = True


class AuditService:
    """High-performance audit logging service.

    Uses async buffering to avoid blocking request processing:
    1. Audit entries are added to an in-memory buffer
    2. A background task periodically flushes the buffer
    3. Optionally, a Redis queue provides durability
    4. Batch inserts to database for efficiency
    """

    def __init__(self, config: Optional[AuditServiceConfig] = None):
        """Initialize audit service.

        Args:
            config: Service configuration
        """
        self.config = config or AuditServiceConfig()

        # In-memory buffer with thread-safe deque
        self._buffer: deque[AuditEntry] = deque(maxlen=self.config.buffer_size)

        # Background task handle
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

        # Lock for buffer operations
        self._buffer_lock = asyncio.Lock()

        # Statistics
        self._stats = {
            "entries_logged": 0,
            "entries_flushed": 0,
            "flush_errors": 0,
            "buffer_overflows": 0,
        }

    async def start(self) -> None:
        """Start the audit service background task."""
        if self._running:
            return

        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("Audit service started")

    async def stop(self) -> None:
        """Stop the audit service and flush remaining entries."""
        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        # Final flush
        await self._flush_buffer()
        logger.info(f"Audit service stopped. Stats: {self._stats}")

    def log(
        self,
        action: AuditAction,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_id: Optional[UUID] = None,
        user_email: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        success: bool = True,
        error_message: Optional[str] = None,
    ) -> UUID:
        """Log an audit entry (non-blocking).

        This method returns immediately. The entry will be
        persisted asynchronously by the background task.

        Args:
            action: The action being logged
            resource_type: Type of resource (e.g., "user", "fix")
            resource_id: ID of the resource
            user_id: User performing the action
            user_email: User's email
            ip_address: Client IP
            user_agent: Client user agent
            request_id: Request correlation ID
            details: Additional details
            success: Whether action succeeded
            error_message: Error if failed

        Returns:
            UUID of the audit entry
        """
        entry = AuditEntry(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            user_email=user_email,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            details=details or {},
            success=success,
            error_message=error_message,
        )

        # Add to buffer (thread-safe deque handles overflow)
        if len(self._buffer) >= self.config.buffer_size:
            self._stats["buffer_overflows"] += 1

        self._buffer.append(entry)
        self._stats["entries_logged"] += 1

        return entry.id

    async def log_async(
        self,
        action: AuditAction,
        **kwargs,
    ) -> UUID:
        """Async version of log for when you need to await.

        Also pushes to Redis queue for durability if enabled.
        """
        entry_id = self.log(action, **kwargs)

        if self.config.use_redis_queue:
            await self._push_to_redis(self._buffer[-1])

        return entry_id

    @asynccontextmanager
    async def audit_context(
        self,
        action: AuditAction,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_id: Optional[UUID] = None,
        user_email: Optional[str] = None,
        **kwargs,
    ):
        """Context manager for auditing operations.

        Automatically logs success/failure based on exception.

        Usage:
            async with audit_service.audit_context(
                AuditAction.FIX_APPROVED,
                resource_type="fix",
                resource_id=str(fix_id),
            ) as audit:
                # Do something
                audit.details["extra_info"] = "value"
        """
        entry = AuditEntry(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            user_email=user_email,
            **kwargs,
        )

        try:
            yield entry
            entry.success = True
        except Exception as e:
            entry.success = False
            entry.error_message = str(e)
            raise
        finally:
            self._buffer.append(entry)
            self._stats["entries_logged"] += 1

    async def _flush_loop(self) -> None:
        """Background task to periodically flush the buffer."""
        while self._running:
            try:
                await asyncio.sleep(self.config.flush_interval_seconds)
                await self._flush_buffer()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Audit flush error: {e}")
                self._stats["flush_errors"] += 1

    async def _flush_buffer(self) -> None:
        """Flush buffer to database in batches."""
        if not self._buffer:
            return

        async with self._buffer_lock:
            # Collect entries to flush
            entries_to_flush = []
            while self._buffer and len(entries_to_flush) < self.config.batch_size:
                entries_to_flush.append(self._buffer.popleft())

        if not entries_to_flush:
            return

        try:
            # Insert batch to database
            await self._insert_batch(entries_to_flush)
            self._stats["entries_flushed"] += len(entries_to_flush)
            logger.debug(f"Flushed {len(entries_to_flush)} audit entries")
        except Exception as e:
            logger.error(f"Failed to flush audit entries: {e}")
            # Re-add to buffer on failure
            async with self._buffer_lock:
                for entry in reversed(entries_to_flush):
                    self._buffer.appendleft(entry)
            raise

    async def _insert_batch(self, entries: list[AuditEntry]) -> None:
        """Insert a batch of entries to the database."""
        from sre_agent.database import get_async_session
        from sre_agent.models.user import AuditLog

        async with get_async_session() as session:
            for entry in entries:
                audit_log = AuditLog(
                    id=entry.id,
                    action=(
                        entry.action.value
                        if isinstance(entry.action, AuditAction)
                        else entry.action
                    ),
                    resource_type=entry.resource_type,
                    resource_id=entry.resource_id,
                    user_id=entry.user_id,
                    user_email=entry.user_email,
                    ip_address=entry.ip_address,
                    user_agent=entry.user_agent,
                    request_id=entry.request_id,
                    details=entry.details,
                    success=entry.success,
                    error_message=entry.error_message,
                    created_at=entry.created_at,
                )
                session.add(audit_log)

            await session.commit()

    async def _push_to_redis(self, entry: AuditEntry) -> None:
        """Push entry to Redis queue for durability."""
        try:
            from sre_agent.core.redis_service import get_redis_service

            redis_service = get_redis_service()
            async with redis_service.get_client() as client:
                await client.rpush(
                    self.config.redis_queue_key,
                    json.dumps(entry.to_dict()),
                )
        except Exception as e:
            logger.warning(f"Failed to push to Redis queue: {e}")

    def get_stats(self) -> dict[str, Any]:
        """Get service statistics."""
        return {
            **self._stats,
            "buffer_size": len(self._buffer),
            "buffer_capacity": self.config.buffer_size,
        }

    # =========================================
    # QUERY METHODS
    # =========================================

    async def query(
        self,
        action: Optional[AuditAction] = None,
        user_id: Optional[UUID] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        success_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit logs with filters.

        Args:
            action: Filter by action type
            user_id: Filter by user
            resource_type: Filter by resource type
            resource_id: Filter by resource ID
            start_date: Filter by start date
            end_date: Filter by end date
            success_only: Only successful actions
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of audit log entries
        """
        from sqlalchemy import and_, select

        from sre_agent.database import get_async_session
        from sre_agent.models.user import AuditLog

        async with get_async_session() as session:
            query = select(AuditLog)

            conditions = []

            if action:
                conditions.append(AuditLog.action == action.value)
            if user_id:
                conditions.append(AuditLog.user_id == user_id)
            if resource_type:
                conditions.append(AuditLog.resource_type == resource_type)
            if resource_id:
                conditions.append(AuditLog.resource_id == resource_id)
            if start_date:
                conditions.append(AuditLog.created_at >= start_date)
            if end_date:
                conditions.append(AuditLog.created_at <= end_date)
            if success_only:
                conditions.append(AuditLog.success.is_(True))

            if conditions:
                query = query.where(and_(*conditions))

            query = query.order_by(AuditLog.created_at.desc())
            query = query.limit(limit).offset(offset)

            result = await session.execute(query)
            logs = result.scalars().all()

            return [
                {
                    "id": str(log.id),
                    "action": log.action,
                    "resource_type": log.resource_type,
                    "resource_id": log.resource_id,
                    "user_id": str(log.user_id) if log.user_id else None,
                    "user_email": log.user_email,
                    "ip_address": log.ip_address,
                    "details": log.details,
                    "success": log.success,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ]


# Global audit service instance
_audit_service: Optional[AuditService] = None


def get_audit_service() -> AuditService:
    """Get the global audit service instance."""
    global _audit_service
    if _audit_service is None:
        _audit_service = AuditService()
    return _audit_service


async def init_audit_service() -> AuditService:
    """Initialize and start the audit service."""
    service = get_audit_service()
    await service.start()
    return service


async def shutdown_audit_service() -> None:
    """Shutdown the audit service."""
    global _audit_service
    if _audit_service:
        await _audit_service.stop()
        _audit_service = None


# Convenience function for quick logging
def audit_log(
    action: AuditAction,
    **kwargs,
) -> UUID:
    """Quick audit log function."""
    return get_audit_service().log(action, **kwargs)
