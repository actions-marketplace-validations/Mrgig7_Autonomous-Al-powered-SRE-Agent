"""Service layer for persisted GitHub App installation metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.models.user import GitHubAppInstallation


class GitHubAppInstallationService:
    """CRUD operations for GitHub App installation records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_installation(
        self,
        *,
        user_id: UUID,
        repo_id: int,
        repo_full_name: str,
        installation_id: int,
        automation_mode: str = "suggest",
    ) -> GitHubAppInstallation:
        installation = await self.get_by_user_repo(user_id=user_id, repo_id=repo_id)
        if installation is None:
            installation = GitHubAppInstallation(
                user_id=user_id,
                repo_id=repo_id,
                repo_full_name=repo_full_name,
                installation_id=installation_id,
                automation_mode=automation_mode,
                connected_at=datetime.now(UTC),
            )
            self._session.add(installation)
            await self._session.flush()
            return installation

        installation.repo_full_name = repo_full_name
        installation.installation_id = installation_id
        installation.automation_mode = automation_mode
        installation.connected_at = datetime.now(UTC)
        await self._session.flush()
        return installation

    async def get_by_user_repo(
        self,
        *,
        user_id: UUID,
        repo_id: int,
    ) -> GitHubAppInstallation | None:
        result = await self._session.execute(
            select(GitHubAppInstallation).where(
                GitHubAppInstallation.user_id == user_id,
                GitHubAppInstallation.repo_id == repo_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_repo_full_name(
        self,
        *,
        user_id: UUID,
        repo_full_name: str,
    ) -> GitHubAppInstallation | None:
        result = await self._session.execute(
            select(GitHubAppInstallation).where(
                GitHubAppInstallation.user_id == user_id,
                GitHubAppInstallation.repo_full_name == repo_full_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_repo_full_name(self, *, repo_full_name: str) -> GitHubAppInstallation | None:
        result = await self._session.execute(
            select(GitHubAppInstallation).where(
                GitHubAppInstallation.repo_full_name == repo_full_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_installation_id(self, *, installation_id: int) -> GitHubAppInstallation | None:
        result = await self._session.execute(
            select(GitHubAppInstallation).where(
                GitHubAppInstallation.installation_id == installation_id,
            )
        )
        return result.scalar_one_or_none()
