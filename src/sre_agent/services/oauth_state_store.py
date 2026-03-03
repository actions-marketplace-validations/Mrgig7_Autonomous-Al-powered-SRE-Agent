"""OAuth state storage for CSRF protection with Redis-backed persistence."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sre_agent.config import get_settings
from sre_agent.core.redis_service import get_redis_service

logger = logging.getLogger(__name__)

_fallback_state_cache: dict[str, dict[str, str | datetime]] = {}


class OAuthStateError(Exception):
    """Raised when OAuth state is invalid or expired."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OAuthStateEntry:
    provider: str
    created_at: datetime


class OAuthStateStore:
    """Stores and validates OAuth state values with TTL enforcement."""

    KEY_PREFIX = "oauth:state:"

    def __init__(self) -> None:
        self._settings = get_settings()

    def _key(self, state: str) -> str:
        return f"{self.KEY_PREFIX}{state}"

    def _ttl_seconds(self) -> int:
        return max(60, int(self._settings.github_oauth_state_ttl_seconds))

    async def store_state(self, *, provider: str, state: str) -> None:
        now = datetime.now(UTC)
        cache_key = self._key(state)
        _fallback_state_cache[cache_key] = {
            "provider": provider,
            "created_at": now,
        }

        try:
            redis_service = get_redis_service()
            await redis_service.cache_set(
                cache_key,
                {
                    "provider": provider,
                    "created_at": now.isoformat(),
                },
                ttl_seconds=self._ttl_seconds(),
            )
        except Exception as exc:
            logger.warning("Failed to persist oauth state in redis", extra={"error": str(exc)})

    async def validate_and_consume(self, *, provider: str, state: str) -> None:
        entry = await self._consume_entry(state)
        if entry is None:
            raise OAuthStateError("Invalid OAuth state", code="invalid_state")

        if entry.provider != provider:
            raise OAuthStateError("Invalid OAuth provider state", code="provider_mismatch")

        if datetime.now(UTC) - entry.created_at > timedelta(seconds=self._ttl_seconds()):
            raise OAuthStateError("OAuth state expired", code="state_expired")

    async def _consume_entry(self, state: str) -> OAuthStateEntry | None:
        cache_key = self._key(state)

        fallback = _fallback_state_cache.pop(cache_key, None)
        if isinstance(fallback, dict):
            provider = fallback.get("provider")
            created_at = fallback.get("created_at")
            if isinstance(provider, str) and isinstance(created_at, datetime):
                return OAuthStateEntry(provider=provider, created_at=created_at)

        try:
            redis_service = get_redis_service()
            cached = await redis_service.cache_get(cache_key)
            await redis_service.cache_delete(cache_key)
            if not isinstance(cached, dict):
                return None
            provider = cached.get("provider")
            created_at_raw = cached.get("created_at")
            if not isinstance(provider, str) or not isinstance(created_at_raw, str):
                return None
            created_at = datetime.fromisoformat(created_at_raw)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return OAuthStateEntry(provider=provider, created_at=created_at)
        except Exception as exc:
            logger.warning("Failed to read oauth state from redis", extra={"error": str(exc)})
            return None
