"""Request throttling and rate limiting middleware.

Production-grade middleware for handling high-throughput scenarios:
- Distributed rate limiting via Redis
- Request deduplication
- Adaptive throttling based on system load
- Request queuing for burst handling
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, Request, Response, status
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


@dataclass
class ThrottleConfig:
    """Configuration for request throttling."""

    # Rate limiting
    enabled: bool = True
    requests_per_minute: int = 100
    requests_per_minute_authenticated: int = 300
    burst_limit: int = 50  # Max burst over limit

    # Per-endpoint limits (more restrictive)
    endpoint_limits: dict[str, int] = None

    # Deduplication
    dedup_enabled: bool = True
    dedup_window_seconds: int = 5

    # Adaptive throttling
    adaptive_enabled: bool = True
    high_load_threshold: float = 0.8  # 80% capacity
    throttle_ratio_at_high_load: float = 0.5  # Reduce to 50%

    # Queue settings
    queue_enabled: bool = True
    max_queue_size: int = 1000
    queue_timeout_seconds: float = 30.0

    def __post_init__(self):
        if self.endpoint_limits is None:
            self.endpoint_limits = {
                "POST:/api/v1/fixes/generate": 10,  # Heavy operation
                "POST:/api/v1/notifications/send": 30,
                "POST:/webhooks/github": 200,  # Need to handle burst
            }


class RequestQueue:
    """Async request queue for burst handling.

    When rate limit is hit, requests can be queued instead of
    immediately rejected. This helps handle CI/CD webhook bursts.
    """

    def __init__(self, max_size: int = 1000, timeout: float = 30.0):
        self.max_size = max_size
        self.timeout = timeout
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_size)
        self._processing = False
        self._processor_task: Optional[asyncio.Task] = None
        self._stats = {
            "enqueued": 0,
            "processed": 0,
            "dropped": 0,
            "timeout": 0,
        }

    async def start(self):
        """Start the queue processor."""
        self._processing = True
        self._processor_task = asyncio.create_task(self._process_loop())

    async def stop(self):
        """Stop the queue processor."""
        self._processing = False
        if self._processor_task:
            self._processor_task.cancel()

    async def enqueue(
        self,
        request_id: str,
        process_func: Callable,
    ) -> Optional[Response]:
        """Enqueue a request for later processing.

        Args:
            request_id: Unique request ID
            process_func: Async function to call when processing

        Returns:
            Response from the processed request or None if dropped
        """
        if self._queue.full():
            self._stats["dropped"] += 1
            return None

        future: asyncio.Future = asyncio.Future()

        try:
            await asyncio.wait_for(
                self._queue.put((request_id, process_func, future)),
                timeout=1.0,
            )
            self._stats["enqueued"] += 1
        except asyncio.TimeoutError:
            self._stats["dropped"] += 1
            return None

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._stats["timeout"] += 1
            return None

    async def _process_loop(self):
        """Background process loop."""
        while self._processing:
            try:
                request_id, process_func, future = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=1.0,
                )

                try:
                    result = await process_func()
                    future.set_result(result)
                    self._stats["processed"] += 1
                except Exception as e:
                    future.set_exception(e)

                self._queue.task_done()

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_stats(self) -> dict:
        return {
            **self._stats,
            "queue_size": self._queue.qsize(),
            "max_size": self.max_size,
        }


class ThrottleMiddleware(BaseHTTPMiddleware):
    """Production-grade request throttling middleware.

    Features:
    - Distributed rate limiting via Redis
    - Per-user and per-IP limiting
    - Endpoint-specific limits
    - Request deduplication
    - Adaptive throttling based on load
    - Request queuing for bursts
    """

    def __init__(self, app: FastAPI, config: Optional[ThrottleConfig] = None):
        super().__init__(app)
        self.config = config or ThrottleConfig()
        self._queue = (
            RequestQueue(
                max_size=self.config.max_queue_size,
                timeout=self.config.queue_timeout_seconds,
            )
            if self.config.queue_enabled
            else None
        )
        self._request_times: list[float] = []  # For adaptive throttling
        self._stats = {
            "requests_total": 0,
            "requests_throttled": 0,
            "requests_deduplicated": 0,
        }

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Handle each request with throttling."""
        if not self.config.enabled:
            return await call_next(request)

        self._stats["requests_total"] += 1

        # Generate request ID for tracing
        request_id = str(uuid4())
        request.state.request_id = request_id

        # Get client identifier
        client_id = self._get_client_id(request)
        endpoint_key = f"{request.method}:{request.url.path}"

        # Check deduplication for POST/PUT/PATCH
        if self.config.dedup_enabled and request.method in ("POST", "PUT", "PATCH"):
            is_dup = await self._check_dedup(request, client_id)
            if is_dup:
                self._stats["requests_deduplicated"] += 1
                return Response(
                    content='{"detail": "Duplicate request detected"}',
                    status_code=status.HTTP_409_CONFLICT,
                    media_type="application/json",
                    headers={"X-Request-ID": request_id},
                )

        # Get rate limit for this request
        limit = self._get_limit(request, endpoint_key)

        # Apply adaptive throttling
        if self.config.adaptive_enabled:
            limit = self._apply_adaptive_throttling(limit)

        # Check rate limit
        allowed, current, retry_after = await self._check_rate_limit(client_id, limit)

        if not allowed:
            self._stats["requests_throttled"] += 1

            # Try queuing if enabled
            if self._queue and self.config.queue_enabled:
                response = await self._queue.enqueue(
                    request_id,
                    lambda: call_next(request),
                )
                if response:
                    return response

            # Return rate limit error
            return Response(
                content='{"detail": "Rate limit exceeded"}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
                headers={
                    "X-Request-ID": request_id,
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(retry_after),
                    "Retry-After": str(retry_after),
                },
            )

        # Process request
        start_time = time.time()
        response = await call_next(request)
        duration = time.time() - start_time

        # Track for adaptive throttling
        self._request_times.append(duration)
        if len(self._request_times) > 1000:
            self._request_times = self._request_times[-500:]

        # Add rate limit headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current))

        return response

    def _get_client_id(self, request: Request) -> str:
        """Get client identifier for rate limiting."""
        # Try to get user ID from auth token
        if hasattr(request.state, "user") and request.state.user:
            return f"user:{request.state.user.user_id}"

        # Fall back to IP address
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"

        return f"ip:{ip}"

    def _get_limit(self, request: Request, endpoint_key: str) -> int:
        """Get rate limit for this request."""
        # Check endpoint-specific limit
        if endpoint_key in self.config.endpoint_limits:
            return self.config.endpoint_limits[endpoint_key]

        # Use authenticated limit if user is authenticated
        if hasattr(request.state, "user") and request.state.user:
            return self.config.requests_per_minute_authenticated

        return self.config.requests_per_minute

    def _apply_adaptive_throttling(self, base_limit: int) -> int:
        """Apply adaptive throttling based on system load."""
        if len(self._request_times) < 100:
            return base_limit

        # Calculate average response time
        avg_time = sum(self._request_times[-100:]) / 100

        # If response times are high, reduce limits
        if avg_time > 1.0:  # > 1 second average
            return int(base_limit * self.config.throttle_ratio_at_high_load)
        elif avg_time > 0.5:  # > 500ms average
            return int(base_limit * 0.75)

        return base_limit

    async def _check_rate_limit(
        self,
        client_id: str,
        limit: int,
    ) -> tuple[bool, int, int]:
        """Check rate limit using Redis."""
        try:
            from sre_agent.core.redis_service import get_redis_service

            redis_service = get_redis_service()
            return await redis_service.check_rate_limit(
                key=client_id,
                limit=limit,
                window_seconds=60,
            )
        except Exception as e:
            logger.warning(f"Rate limit check failed: {e}")
            # Fail open - allow request if Redis is down
            return True, 0, 0

    async def _check_dedup(
        self,
        request: Request,
        client_id: str,
    ) -> bool:
        """Check if request is a duplicate."""
        try:
            from sre_agent.core.redis_service import get_redis_service

            # Create hash of request
            body = await request.body()
            payload_hash = f"{client_id}:{request.url.path}:{hash(body)}"

            redis_service = get_redis_service()
            is_dup, _ = await redis_service.check_dedup(
                operation="request",
                payload_hash=payload_hash,
                ttl_seconds=self.config.dedup_window_seconds,
            )

            if not is_dup:
                await redis_service.mark_processed(
                    operation="request",
                    payload_hash=payload_hash,
                    result_id=request.state.request_id,
                    ttl_seconds=self.config.dedup_window_seconds,
                )

            return is_dup

        except Exception as e:
            logger.warning(f"Dedup check failed: {e}")
            return False

    def get_stats(self) -> dict:
        """Get middleware statistics."""
        stats = {**self._stats}
        if self._queue:
            stats["queue"] = self._queue.get_stats()
        return stats


def setup_throttling(app: FastAPI, config: Optional[ThrottleConfig] = None):
    """Setup throttling middleware on FastAPI app."""
    middleware = ThrottleMiddleware(app, config)
    app.add_middleware(BaseHTTPMiddleware, dispatch=middleware.dispatch)
    return middleware
