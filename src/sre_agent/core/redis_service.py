"""Redis service for distributed caching and coordination.

This module provides production-grade Redis integration for:
- JWT token blocklist (distributed revocation)
- Distributed rate limiting
- Request deduplication
- Session caching
- Pub/Sub for real-time updates

Designed for high-throughput scenarios with 700-800 concurrent test runs
and multiple simultaneous code pushes.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional
from uuid import UUID

import redis.asyncio as redis
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.lock import Lock

logger = logging.getLogger(__name__)


@dataclass
class RedisConfig:
    """Redis configuration for production deployment."""

    url: str = "redis://localhost:6379/0"

    # Connection pooling for high concurrency
    max_connections: int = 100
    min_idle_connections: int = 10

    # Timeouts
    socket_timeout: float = 5.0
    socket_connect_timeout: float = 5.0

    # Retry settings
    retry_on_timeout: bool = True
    health_check_interval: int = 30

    # Key prefixes for namespace isolation
    prefix: str = "sre_agent:"

    # TTL defaults
    default_ttl_seconds: int = 3600
    token_blocklist_ttl_seconds: int = 86400 * 7  # 7 days
    rate_limit_window_seconds: int = 60
    dedup_window_seconds: int = 300  # 5 minutes


class RedisService:
    """Production-grade Redis service with connection pooling.

    Handles high-concurrency scenarios with:
    - Connection pooling to prevent connection exhaustion
    - Automatic reconnection on failures
    - Distributed locking for critical sections
    - Pub/Sub for real-time coordination
    """

    def __init__(self, config: Optional[RedisConfig] = None):
        """Initialize Redis service.

        Args:
            config: Redis configuration
        """
        self.config = config or RedisConfig()
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None
        self._subscriber_tasks: list[asyncio.Task] = []

    async def connect(self) -> None:
        """Establish connection pool to Redis."""
        if self._pool is not None:
            return

        self._pool = ConnectionPool.from_url(
            self.config.url,
            max_connections=self.config.max_connections,
            socket_timeout=self.config.socket_timeout,
            socket_connect_timeout=self.config.socket_connect_timeout,
            retry_on_timeout=self.config.retry_on_timeout,
            health_check_interval=self.config.health_check_interval,
            decode_responses=True,
        )

        self._client = Redis(connection_pool=self._pool)

        # Verify connection
        try:
            await self._client.ping()
            logger.info(f"Redis connected: {self.config.url}")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Close Redis connections and cleanup."""
        # Cancel subscriber tasks
        for task in self._subscriber_tasks:
            task.cancel()
        self._subscriber_tasks.clear()

        if self._pubsub:
            await self._pubsub.aclose()
            self._pubsub = None

        if self._client:
            await self._client.aclose()
            self._client = None

        if self._pool:
            await self._pool.disconnect()
            self._pool = None

        logger.info("Redis disconnected")

    @asynccontextmanager
    async def get_client(self) -> AsyncIterator[Redis]:
        """Get a Redis client from the pool.

        Usage:
            async with redis_service.get_client() as client:
                await client.set("key", "value")
        """
        if self._client is None:
            await self.connect()
        yield self._client

    def _key(self, *parts: str) -> str:
        """Build a namespaced key."""
        return f"{self.config.prefix}{':'.join(parts)}"

    # =========================================
    # JWT TOKEN BLOCKLIST
    # =========================================

    async def add_to_blocklist(
        self,
        jti: str,
        user_id: Optional[UUID] = None,
        reason: str = "logout",
        ttl_seconds: Optional[int] = None,
    ) -> bool:
        """Add a JWT token ID to the blocklist.

        Args:
            jti: JWT ID (jti claim)
            user_id: User who owned the token
            reason: Why token was revoked
            ttl_seconds: How long to keep in blocklist

        Returns:
            True if added successfully
        """
        async with self.get_client() as client:
            key = self._key("blocklist", jti)
            ttl = ttl_seconds or self.config.token_blocklist_ttl_seconds

            data = {
                "jti": jti,
                "user_id": str(user_id) if user_id else None,
                "reason": reason,
                "blocked_at": datetime.now(UTC).isoformat(),
            }

            await client.setex(key, ttl, json.dumps(data))

            # Also add to a set for bulk operations
            await client.sadd(self._key("blocklist", "all"), jti)

            logger.info(f"Token blocklisted: {jti[:8]}... reason={reason}")
            return True

    async def is_blocklisted(self, jti: str) -> bool:
        """Check if a token is blocklisted.

        Args:
            jti: JWT ID to check

        Returns:
            True if token is blocklisted
        """
        async with self.get_client() as client:
            key = self._key("blocklist", jti)
            return await client.exists(key) > 0

    async def revoke_all_user_tokens(
        self,
        user_id: UUID,
        reason: str = "security",
    ) -> int:
        """Revoke all tokens for a user by marking in a user-level blocklist.

        This is a nuclear option that invalidates all existing tokens
        for a user, forcing re-authentication.

        Args:
            user_id: User whose tokens to revoke
            reason: Reason for revocation

        Returns:
            Number of invalidation markers set
        """
        async with self.get_client() as client:
            key = self._key("user_revoked", str(user_id))

            data = {
                "user_id": str(user_id),
                "revoked_at": datetime.now(UTC).isoformat(),
                "reason": reason,
            }

            await client.setex(
                key,
                self.config.token_blocklist_ttl_seconds,
                json.dumps(data),
            )

            logger.warning(f"All tokens revoked for user: {user_id}")
            return 1

    async def is_user_tokens_revoked(
        self,
        user_id: UUID,
        token_iat: datetime,
    ) -> bool:
        """Check if user's tokens issued before a certain time are revoked.

        Args:
            user_id: User ID
            token_iat: Token issued-at time

        Returns:
            True if token was issued before revocation
        """
        async with self.get_client() as client:
            key = self._key("user_revoked", str(user_id))
            data = await client.get(key)

            if not data:
                return False

            revocation = json.loads(data)
            revoked_at = datetime.fromisoformat(revocation["revoked_at"])

            return token_iat < revoked_at

    # =========================================
    # DISTRIBUTED RATE LIMITING
    # =========================================

    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: Optional[int] = None,
    ) -> tuple[bool, int, int]:
        """Check and update rate limit using sliding window.

        Uses Redis sorted sets for accurate sliding window rate limiting
        that works across multiple instances.

        Args:
            key: Rate limit key (e.g., "api:user:123")
            limit: Maximum requests allowed
            window_seconds: Time window in seconds

        Returns:
            Tuple of (allowed, current_count, retry_after_seconds)
        """
        window = window_seconds or self.config.rate_limit_window_seconds
        now = time.time()
        window_start = now - window

        redis_key = self._key("ratelimit", key)

        try:
            async with self.get_client() as client:
                async with client.pipeline(transaction=True) as pipe:
                    pipe.zremrangebyscore(redis_key, 0, window_start)
                    pipe.zcard(redis_key)
                    pipe.zadd(redis_key, {f"{now}:{id(now)}": now})
                    pipe.expire(redis_key, window)

                    results = await pipe.execute()
                    current_count = results[1]

            if current_count >= limit:
                retry_after = window
                try:
                    async with self.get_client() as client:
                        oldest = await client.zrange(redis_key, 0, 0, withscores=True)
                        if oldest:
                            retry_after = int(oldest[0][1] + window - now) + 1
                except Exception:
                    retry_after = window

                return False, current_count, retry_after

            return True, current_count + 1, 0
        except Exception as e:
            logger.warning(
                "Redis rate limit unavailable; allowing request",
                extra={"key": key, "error": str(e)},
            )
            return True, 0, 0

    async def get_rate_limit_status(
        self,
        key: str,
        limit: int,
        window_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        """Get current rate limit status without incrementing.

        Args:
            key: Rate limit key
            limit: Maximum allowed
            window_seconds: Time window

        Returns:
            Status dictionary with remaining, reset time, etc.
        """
        window = window_seconds or self.config.rate_limit_window_seconds
        now = time.time()
        window_start = now - window

        redis_key = self._key("ratelimit", key)

        try:
            async with self.get_client() as client:
                await client.zremrangebyscore(redis_key, 0, window_start)
                current_count = await client.zcard(redis_key)

                oldest = await client.zrange(redis_key, 0, 0, withscores=True)
                reset_at = oldest[0][1] + window if oldest else now + window

            return {
                "limit": limit,
                "remaining": max(0, limit - current_count),
                "used": current_count,
                "reset_at": datetime.fromtimestamp(reset_at).isoformat(),
                "retry_after": max(0, int(reset_at - now)) if current_count >= limit else 0,
            }
        except Exception as e:
            logger.warning(
                "Redis rate limit status unavailable",
                extra={"key": key, "error": str(e)},
            )
            return {
                "limit": limit,
                "remaining": limit,
                "used": 0,
                "reset_at": datetime.fromtimestamp(now + window).isoformat(),
                "retry_after": 0,
            }

    # =========================================
    # REQUEST DEDUPLICATION
    # =========================================

    async def check_dedup(
        self,
        operation: str,
        payload_hash: str,
        ttl_seconds: Optional[int] = None,
    ) -> tuple[bool, Optional[str]]:
        """Check if a request is a duplicate.

        Prevents processing the same webhook/event multiple times.
        Critical for handling retries from CI/CD systems.

        Args:
            operation: Operation type (e.g., "webhook", "fix")
            payload_hash: Hash of the request payload
            ttl_seconds: Dedup window

        Returns:
            Tuple of (is_duplicate, previous_result_id)
        """
        redis_key = self._key("dedup", operation, payload_hash)

        try:
            async with self.get_client() as client:
                existing = await client.get(redis_key)

                if existing:
                    return True, existing

                return False, None
        except Exception as e:
            logger.warning(
                "Redis dedup unavailable; treating as not duplicate",
                extra={"operation": operation, "error": str(e)},
            )
            return False, None

    async def mark_processed(
        self,
        operation: str,
        payload_hash: str,
        result_id: str,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Mark a request as processed for deduplication.

        Args:
            operation: Operation type
            payload_hash: Hash of the request
            result_id: ID of the result for idempotent responses
            ttl_seconds: How long to remember
        """
        ttl = ttl_seconds or self.config.dedup_window_seconds
        redis_key = self._key("dedup", operation, payload_hash)

        try:
            async with self.get_client() as client:
                await client.setex(redis_key, ttl, result_id)
        except Exception as e:
            logger.warning(
                "Redis dedup mark unavailable; skipping",
                extra={"operation": operation, "error": str(e)},
            )

    async def increment_counter(self, key: str, ttl_seconds: int) -> int:
        """Atomically increment a counter and apply/refresh TTL."""
        redis_key = self._key("counter", key)
        try:
            async with self.get_client() as client:
                async with client.pipeline(transaction=True) as pipe:
                    pipe.incr(redis_key)
                    pipe.expire(redis_key, int(ttl_seconds))
                    results = await pipe.execute()
                return int(results[0] or 0)
        except Exception as e:
            logger.warning(
                "Redis counter increment unavailable; returning conservative fallback",
                extra={"key": key, "error": str(e)},
            )
            return 1

    @staticmethod
    def hash_payload(payload: dict[str, Any]) -> str:
        """Create a hash of a payload for deduplication."""
        serialized = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    # =========================================
    # DISTRIBUTED LOCKING
    # =========================================

    @asynccontextmanager
    async def distributed_lock(
        self,
        name: str,
        timeout: float = 30.0,
        blocking: bool = True,
        blocking_timeout: float = 10.0,
    ) -> AsyncIterator[bool]:
        """Acquire a distributed lock for critical sections.

        Use for operations that must not run concurrently across
        multiple instances, like:
        - Processing a specific failure event
        - Generating a fix for a specific repo
        - Updating shared state

        Args:
            name: Lock name
            timeout: Lock auto-release timeout
            blocking: Whether to wait for lock
            blocking_timeout: How long to wait

        Yields:
            True if lock acquired
        """
        try:
            async with self.get_client() as client:
                lock = Lock(
                    client,
                    self._key("lock", name),
                    timeout=timeout,
                    blocking=blocking,
                    blocking_timeout=blocking_timeout,
                )

                acquired = await lock.acquire()
                try:
                    yield acquired
                finally:
                    if acquired:
                        try:
                            await lock.release()
                        except Exception:
                            logger.warning(
                                "Redis lock release failed",
                                extra={"lock": name},
                            )
        except Exception as e:
            logger.warning(
                "Redis lock unavailable; proceeding without lock",
                extra={"lock": name, "error": str(e)},
            )
            yield True

    async def try_acquire_repo_concurrency(
        self, *, repo: str, limit: int, ttl_seconds: int
    ) -> bool:
        try:
            async with self.get_client() as client:
                key = self._key("concurrency", f"repo:{repo}")
                script = """
                local key = KEYS[1]
                local limit = tonumber(ARGV[1])
                local ttl = tonumber(ARGV[2])
                local current = tonumber(redis.call('GET', key) or '0')
                if current >= limit then
                  return 0
                end
                redis.call('INCR', key)
                redis.call('EXPIRE', key, ttl)
                return 1
                """
                res = await client.eval(script, 1, key, str(limit), str(ttl_seconds))
                return bool(res)
        except Exception as e:
            logger.warning(
                "Redis repo concurrency unavailable; allowing",
                extra={"repo": repo, "error": str(e)},
            )
            return True

    async def release_repo_concurrency(self, *, repo: str) -> None:
        try:
            async with self.get_client() as client:
                key = self._key("concurrency", f"repo:{repo}")
                script = """
                local key = KEYS[1]
                local current = tonumber(redis.call('GET', key) or '0')
                if current <= 1 then
                  redis.call('DEL', key)
                  return 0
                end
                return redis.call('DECR', key)
                """
                await client.eval(script, 1, key)
        except Exception as e:
            logger.warning(
                "Redis repo concurrency release unavailable; skipping",
                extra={"repo": repo, "error": str(e)},
            )

    # =========================================
    # CACHING
    # =========================================

    async def cache_get(self, key: str) -> Optional[Any]:
        """Get a cached value."""
        async with self.get_client() as client:
            data = await client.get(self._key("cache", key))
            if data:
                return json.loads(data)
            return None

    async def cache_set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Set a cached value."""
        ttl = ttl_seconds or self.config.default_ttl_seconds
        async with self.get_client() as client:
            await client.setex(
                self._key("cache", key),
                ttl,
                json.dumps(value, default=str),
            )

    async def cache_delete(self, key: str) -> None:
        """Delete a cached value."""
        async with self.get_client() as client:
            await client.delete(self._key("cache", key))

    async def cache_get_or_set(
        self,
        key: str,
        factory: Callable[[], Any],
        ttl_seconds: Optional[int] = None,
    ) -> Any:
        """Get cached value or compute and cache it."""
        value = await self.cache_get(key)
        if value is not None:
            return value

        value = factory()
        if asyncio.iscoroutine(value):
            value = await value

        await self.cache_set(key, value, ttl_seconds)
        return value

    # =========================================
    # PUB/SUB FOR REAL-TIME UPDATES
    # =========================================

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """Publish a message to a channel.

        Args:
            channel: Channel name
            message: Message to publish

        Returns:
            Number of subscribers that received the message
        """
        async with self.get_client() as client:
            return await client.publish(
                self._key("channel", channel),
                json.dumps(message, default=str),
            )

    async def subscribe(
        self,
        channel: str,
        handler: Callable[[dict[str, Any]], None],
    ) -> None:
        """Subscribe to a channel with a message handler.

        Args:
            channel: Channel name
            handler: Async function to handle messages
        """
        if self._pubsub is None:
            async with self.get_client() as client:
                self._pubsub = client.pubsub()

        await self._pubsub.subscribe(self._key("channel", channel))

        async def listener():
            async for message in self._pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        if asyncio.iscoroutinefunction(handler):
                            await handler(data)
                        else:
                            handler(data)
                    except Exception as e:
                        logger.error(f"Error handling message: {e}")

        task = asyncio.create_task(listener())
        self._subscriber_tasks.append(task)

    # =========================================
    # METRICS & HEALTH
    # =========================================

    async def health_check(self) -> dict[str, Any]:
        """Check Redis health and get stats."""
        try:
            async with self.get_client() as client:
                info = await client.info("server", "clients", "memory", "stats")

                return {
                    "status": "healthy",
                    "version": info.get("redis_version"),
                    "connected_clients": info.get("connected_clients"),
                    "used_memory_human": info.get("used_memory_human"),
                    "total_commands_processed": info.get("total_commands_processed"),
                    "uptime_in_seconds": info.get("uptime_in_seconds"),
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }


# Global Redis service instance
_redis_service: Optional[RedisService] = None


def get_redis_service() -> RedisService:
    """Get the global Redis service instance."""
    global _redis_service
    if _redis_service is None:
        from sre_agent.config import get_settings

        settings = get_settings()
        config = RedisConfig(url=settings.redis_url)
        _redis_service = RedisService(config)
    return _redis_service


async def init_redis() -> RedisService:
    """Initialize and connect the Redis service."""
    service = get_redis_service()
    try:
        await service.connect()
    except Exception as e:
        logger.warning(f"Redis init skipped (unavailable): {e}")
    return service


async def shutdown_redis() -> None:
    """Shutdown the Redis service."""
    global _redis_service
    if _redis_service:
        await _redis_service.disconnect()
        _redis_service = None
