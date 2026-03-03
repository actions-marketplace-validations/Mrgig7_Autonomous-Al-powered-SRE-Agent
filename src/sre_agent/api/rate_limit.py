"""Route-level rate limiting dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from sre_agent.core.redis_service import get_redis_service


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limit_by_ip(*, key_prefix: str, limit: int, window_seconds: int = 60):
    """Create an endpoint dependency that enforces per-IP limits."""

    async def dependency(request: Request) -> None:
        client_ip = _client_ip(request)
        redis_service = get_redis_service()
        allowed, _, retry_after = await redis_service.check_rate_limit(
            key=f"{key_prefix}:{client_ip}",
            limit=limit,
            window_seconds=window_seconds,
        )
        if allowed:
            return

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, retry_after))},
        )

    return dependency
