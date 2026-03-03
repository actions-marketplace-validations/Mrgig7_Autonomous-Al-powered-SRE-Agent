"""GitHub OAuth token storage for authenticated sessions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sre_agent.config import get_settings
from sre_agent.core.redis_service import get_redis_service

logger = logging.getLogger(__name__)

_fallback_tokens: dict[str, dict[str, str | datetime]] = {}


class GitHubOAuthTokenStore:
    """Stores GitHub OAuth access tokens in Redis keyed by JWT session ID."""

    CACHE_PREFIX = "oauth:github:jti:"

    def __init__(self) -> None:
        self._settings = get_settings()

    def _cache_key(self, jti: str) -> str:
        return f"{self.CACHE_PREFIX}{jti}"

    async def store_token(self, *, jti: str, user_id: UUID, access_token: str) -> None:
        ttl = max(60, int(self._settings.github_oauth_token_ttl_seconds))
        _fallback_tokens[self._cache_key(jti)] = {
            "access_token": access_token,
            "expires_at": datetime.now(UTC) + timedelta(seconds=ttl),
            "user_id": str(user_id),
        }

        try:
            redis_service = get_redis_service()
            await redis_service.cache_set(
                self._cache_key(jti),
                {
                    "user_id": str(user_id),
                    "access_token": access_token,
                },
                ttl_seconds=ttl,
            )
        except Exception as exc:
            logger.warning(
                "Failed to store GitHub OAuth token",
                extra={"error": str(exc), "user_id": str(user_id)},
            )

    async def get_token(self, *, jti: str) -> str | None:
        cached = _fallback_tokens.get(self._cache_key(jti))
        if isinstance(cached, dict):
            expires_at = cached.get("expires_at")
            if isinstance(expires_at, datetime) and expires_at > datetime.now(UTC):
                token = cached.get("access_token")
                if isinstance(token, str):
                    return token
            _fallback_tokens.pop(self._cache_key(jti), None)

        try:
            redis_service = get_redis_service()
            data = await redis_service.cache_get(self._cache_key(jti))
            if not isinstance(data, dict):
                return None
            token = data.get("access_token")
            if not isinstance(token, str) or not token:
                return None
            return token
        except Exception as exc:
            logger.warning("Failed to read GitHub OAuth token", extra={"error": str(exc)})
            return None

    async def clear_token(self, *, jti: str) -> None:
        _fallback_tokens.pop(self._cache_key(jti), None)
        try:
            redis_service = get_redis_service()
            await redis_service.cache_delete(self._cache_key(jti))
        except Exception as exc:
            logger.warning("Failed to clear GitHub OAuth token", extra={"error": str(exc)})
