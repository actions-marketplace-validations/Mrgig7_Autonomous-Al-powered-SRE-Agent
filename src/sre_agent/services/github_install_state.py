"""Temporary GitHub App installation state storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sre_agent.config import get_settings
from sre_agent.core.redis_service import get_redis_service

logger = logging.getLogger(__name__)

_fallback_install_states: dict[str, dict[str, str | int | datetime]] = {}


@dataclass(frozen=True)
class InstallStatePayload:
    user_id: UUID
    repo_id: int
    repo_full_name: str
    automation_mode: str
    created_at: datetime


class GitHubInstallStateStore:
    """Stores temporary installation intent state used during app install callback."""

    KEY_PREFIX = "github:install:state:"

    def __init__(self) -> None:
        self._settings = get_settings()

    def _key(self, state: str) -> str:
        return f"{self.KEY_PREFIX}{state}"

    def _ttl_seconds(self) -> int:
        return max(120, int(self._settings.phase1_install_state_ttl_seconds))

    async def save_state(
        self,
        *,
        state: str,
        user_id: UUID,
        repo_id: int,
        repo_full_name: str,
        automation_mode: str,
    ) -> None:
        now = datetime.now(UTC)
        cache_key = self._key(state)
        _fallback_install_states[cache_key] = {
            "user_id": str(user_id),
            "repo_id": repo_id,
            "repo_full_name": repo_full_name,
            "automation_mode": automation_mode,
            "created_at": now,
        }

        try:
            redis_service = get_redis_service()
            await redis_service.cache_set(
                cache_key,
                {
                    "user_id": str(user_id),
                    "repo_id": repo_id,
                    "repo_full_name": repo_full_name,
                    "automation_mode": automation_mode,
                    "created_at": now.isoformat(),
                },
                ttl_seconds=self._ttl_seconds(),
            )
        except Exception as exc:
            logger.warning("Failed to persist install state", extra={"error": str(exc)})

    async def consume_state(self, *, state: str) -> InstallStatePayload | None:
        cache_key = self._key(state)
        fallback = _fallback_install_states.pop(cache_key, None)
        if isinstance(fallback, dict):
            parsed = self._parse_payload(fallback)
            if parsed:
                if datetime.now(UTC) - parsed.created_at > timedelta(seconds=self._ttl_seconds()):
                    return None
                return parsed

        try:
            redis_service = get_redis_service()
            cached = await redis_service.cache_get(cache_key)
            await redis_service.cache_delete(cache_key)
            if not isinstance(cached, dict):
                return None
            parsed = self._parse_payload(cached)
            if parsed is None:
                return None
            if datetime.now(UTC) - parsed.created_at > timedelta(seconds=self._ttl_seconds()):
                return None
            return parsed
        except Exception as exc:
            logger.warning("Failed to read install state", extra={"error": str(exc)})
            return None

    def _parse_payload(self, value: dict[str, str | int | datetime]) -> InstallStatePayload | None:
        user_id_raw = value.get("user_id")
        repo_id_raw = value.get("repo_id")
        repo_full_name = value.get("repo_full_name")
        automation_mode = value.get("automation_mode")
        created_at_raw = value.get("created_at")

        if not isinstance(user_id_raw, str):
            return None
        if not isinstance(repo_id_raw, int):
            return None
        if not isinstance(repo_full_name, str):
            return None
        if not isinstance(automation_mode, str):
            return None

        if isinstance(created_at_raw, str):
            created_at = datetime.fromisoformat(created_at_raw)
        elif isinstance(created_at_raw, datetime):
            created_at = created_at_raw
        else:
            return None

        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return InstallStatePayload(
            user_id=UUID(user_id_raw),
            repo_id=repo_id_raw,
            repo_full_name=repo_full_name,
            automation_mode=automation_mode,
            created_at=created_at,
        )
