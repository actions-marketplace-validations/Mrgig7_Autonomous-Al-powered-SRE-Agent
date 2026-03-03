"""Onboarding status tracking service."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sre_agent.config import get_settings
from sre_agent.core.redis_service import get_redis_service

logger = logging.getLogger(__name__)

_fallback_onboarding_states: dict[str, dict[str, Any]] = {}


class OnboardingStateService:
    """Stores onboarding progress in Redis with in-memory fallback."""

    KEY_PREFIX = "onboarding:state:"

    def __init__(self) -> None:
        self._settings = get_settings()

    def _key(self, user_id: UUID) -> str:
        return f"{self.KEY_PREFIX}{user_id}"

    def _default_state(self) -> dict[str, Any]:
        return {
            "oauth_completed": False,
            "repo_selected": False,
            "app_installed": False,
            "dashboard_ready": False,
            "selected_repository": None,
            "installation_id": None,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    async def get_state(self, *, user_id: UUID) -> dict[str, Any]:
        key = self._key(user_id)
        fallback = _fallback_onboarding_states.get(key)
        if isinstance(fallback, dict):
            return {**self._default_state(), **fallback}

        try:
            redis_service = get_redis_service()
            cached = await redis_service.cache_get(key)
            if isinstance(cached, dict):
                return {**self._default_state(), **cached}
        except Exception as exc:
            logger.warning("Failed to load onboarding state", extra={"error": str(exc)})

        return self._default_state()

    async def update_state(self, *, user_id: UUID, **updates: Any) -> dict[str, Any]:
        current = await self.get_state(user_id=user_id)
        current.update(updates)
        current["updated_at"] = datetime.now(UTC).isoformat()

        key = self._key(user_id)
        _fallback_onboarding_states[key] = current

        try:
            redis_service = get_redis_service()
            await redis_service.cache_set(
                key,
                current,
                ttl_seconds=max(3600, int(self._settings.phase1_onboarding_state_ttl_seconds)),
            )
        except Exception as exc:
            logger.warning("Failed to persist onboarding state", extra={"error": str(exc)})

        return current

    async def mark_oauth_completed(self, *, user_id: UUID) -> dict[str, Any]:
        return await self.update_state(
            user_id=user_id,
            oauth_completed=True,
        )

    async def mark_repo_selected(self, *, user_id: UUID, repo_full_name: str) -> dict[str, Any]:
        return await self.update_state(
            user_id=user_id,
            oauth_completed=True,
            repo_selected=True,
            selected_repository=repo_full_name,
        )

    async def mark_app_installed(
        self,
        *,
        user_id: UUID,
        repo_full_name: str,
        installation_id: int,
    ) -> dict[str, Any]:
        return await self.update_state(
            user_id=user_id,
            oauth_completed=True,
            repo_selected=True,
            app_installed=True,
            dashboard_ready=True,
            selected_repository=repo_full_name,
            installation_id=installation_id,
        )
