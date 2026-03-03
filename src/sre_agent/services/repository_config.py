"""Repository-level runtime config loading from .sre-agent.yaml."""

from __future__ import annotations

import logging
from typing import Any

import yaml

from sre_agent.config import get_settings
from sre_agent.observability.metrics import (
    record_repo_config_load_failure,
    record_repo_config_load_success,
    record_repo_config_missing,
)
from sre_agent.schemas.repository_config import RepositoryRuntimeConfig
from sre_agent.services.github_client import GitHubAPIError, GitHubClient

logger = logging.getLogger(__name__)

_ALLOWED_AUTOMATION_MODES = {"suggest", "auto_pr", "auto_merge"}


class RepositoryConfigService:
    """Loads and validates repository runtime config from GitHub."""

    CONFIG_PATH = ".sre-agent.yaml"

    def __init__(self) -> None:
        self._settings = get_settings()

    async def resolve_for_repository(
        self,
        *,
        repo_full_name: str,
        installation_automation_mode: str = "suggest",
        ref: str | None = None,
    ) -> RepositoryRuntimeConfig:
        """Resolve final runtime config with repo-file override precedence."""
        default_mode = self._normalize_automation_mode(installation_automation_mode)
        default_config = RepositoryRuntimeConfig(
            automation_mode=default_mode,
            protected_paths=[],
            retry_limit=3,
            source="installation_default",
        )

        try:
            content = await self._fetch_config_file(
                repo_full_name=repo_full_name,
                ref=ref,
            )
        except GitHubAPIError as exc:
            record_repo_config_load_failure()
            logger.warning(
                "Failed to fetch repository config",
                extra={"repo": repo_full_name, "error": str(exc)},
            )
            return default_config.model_copy(update={"source": "repo_file_unavailable"})

        if content is None:
            record_repo_config_missing()
            return default_config.model_copy(update={"source": "repo_file_missing"})

        try:
            parsed = yaml.safe_load(content)
            if parsed is None:
                parsed = {}
            if not isinstance(parsed, dict):
                raise ValueError("Repository config must be a YAML object")

            config = self._parse_config(parsed, default_mode=default_mode)
            record_repo_config_load_success()
            return config.model_copy(update={"source": "repo_file"})
        except Exception as exc:
            record_repo_config_load_failure()
            logger.warning(
                "Invalid repository config; falling back to defaults",
                extra={"repo": repo_full_name, "error": str(exc)},
            )
            return default_config.model_copy(update={"source": "repo_file_invalid"})

    async def _fetch_config_file(self, *, repo_full_name: str, ref: str | None) -> str | None:
        async with GitHubClient(token=self._settings.github_token) as client:
            return await client.get_file_content(
                repo=repo_full_name,
                path=self.CONFIG_PATH,
                ref=ref,
            )

    def _parse_config(self, raw: dict[str, Any], *, default_mode: str) -> RepositoryRuntimeConfig:
        automation_mode = self._normalize_automation_mode(
            value=raw.get("automation_mode"),
            fallback=default_mode,
        )

        protected_paths_raw = raw.get("protected_paths", [])
        if protected_paths_raw is None:
            protected_paths_raw = []
        if not isinstance(protected_paths_raw, list):
            raise ValueError("protected_paths must be a list of glob strings")
        protected_paths: list[str] = []
        for path in protected_paths_raw:
            if not isinstance(path, str):
                raise ValueError("protected_paths entries must be strings")
            normalized = path.strip()
            if normalized:
                protected_paths.append(normalized)

        retry_limit_raw = raw.get("retry_limit", 3)
        if isinstance(retry_limit_raw, bool):
            raise ValueError("retry_limit must be an integer")
        retry_limit = int(retry_limit_raw)
        retry_limit = max(1, min(10, retry_limit))

        return RepositoryRuntimeConfig(
            automation_mode=automation_mode,
            protected_paths=protected_paths,
            retry_limit=retry_limit,
        )

    def _normalize_automation_mode(self, value: Any, fallback: str = "suggest") -> str:
        candidate = value if isinstance(value, str) else fallback
        normalized = candidate.strip().lower()
        if normalized in _ALLOWED_AUTOMATION_MODES:
            return normalized
        return fallback if fallback in _ALLOWED_AUTOMATION_MODES else "suggest"
