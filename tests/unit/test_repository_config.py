from __future__ import annotations

import asyncio

import pytest
from sre_agent.schemas.repository_config import RepositoryRuntimeConfig
from sre_agent.services.github_client import GitHubAPIError
from sre_agent.services.repository_config import RepositoryConfigService


def test_repository_config_uses_installation_defaults_when_file_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RepositoryConfigService()

    async def _missing_file(**_kwargs) -> str | None:
        return None

    monkeypatch.setattr(service, "_fetch_config_file", _missing_file)

    config = asyncio.run(
        service.resolve_for_repository(
            repo_full_name="acme/widgets",
            installation_automation_mode="auto_pr",
        )
    )

    assert config == RepositoryRuntimeConfig(
        automation_mode="auto_pr",
        protected_paths=[],
        retry_limit=3,
        source="repo_file_missing",
    )


def test_repository_config_repo_file_overrides_installation_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RepositoryConfigService()

    async def _file(**_kwargs) -> str | None:
        return (
            "automation_mode: auto_merge\n"
            "protected_paths:\n"
            "  - infra/**\n"
            "  - payments/**\n"
            "retry_limit: 7\n"
        )

    monkeypatch.setattr(service, "_fetch_config_file", _file)

    config = asyncio.run(
        service.resolve_for_repository(
            repo_full_name="acme/widgets",
            installation_automation_mode="suggest",
        )
    )

    assert config == RepositoryRuntimeConfig(
        automation_mode="auto_merge",
        protected_paths=["infra/**", "payments/**"],
        retry_limit=7,
        source="repo_file",
    )


def test_repository_config_invalid_yaml_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RepositoryConfigService()

    async def _invalid(**_kwargs) -> str | None:
        return "[]"

    monkeypatch.setattr(service, "_fetch_config_file", _invalid)

    config = asyncio.run(
        service.resolve_for_repository(
            repo_full_name="acme/widgets",
            installation_automation_mode="suggest",
        )
    )

    assert config == RepositoryRuntimeConfig(
        automation_mode="suggest",
        protected_paths=[],
        retry_limit=3,
        source="repo_file_invalid",
    )


def test_repository_config_unavailable_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RepositoryConfigService()

    async def _error(**_kwargs) -> str | None:
        raise GitHubAPIError("boom", status_code=500)

    monkeypatch.setattr(service, "_fetch_config_file", _error)

    config = asyncio.run(
        service.resolve_for_repository(
            repo_full_name="acme/widgets",
            installation_automation_mode="suggest",
        )
    )

    assert config == RepositoryRuntimeConfig(
        automation_mode="suggest",
        protected_paths=[],
        retry_limit=3,
        source="repo_file_unavailable",
    )


def test_repository_config_invalid_values_are_safely_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RepositoryConfigService()

    async def _file(**_kwargs) -> str | None:
        return "automation_mode: nonsense\n" "protected_paths:\n" "  - api/**\n" "retry_limit: 99\n"

    monkeypatch.setattr(service, "_fetch_config_file", _file)

    config = asyncio.run(
        service.resolve_for_repository(
            repo_full_name="acme/widgets",
            installation_automation_mode="auto_pr",
        )
    )

    assert config == RepositoryRuntimeConfig(
        automation_mode="auto_pr",
        protected_paths=["api/**"],
        retry_limit=10,
        source="repo_file",
    )
