"""User repository API endpoints."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from sre_agent.api.rate_limit import limit_by_ip
from sre_agent.api.response_envelope import success_response
from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import get_current_user, require_permission
from sre_agent.auth.rbac import Permission
from sre_agent.observability.metrics import observe_repo_fetch_latency_ms
from sre_agent.services.github_client import GitHubAPIError, GitHubClient
from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user"])


class RepositoryPermissions(BaseModel):
    admin: bool = False
    maintain: bool = False
    push: bool = False
    triage: bool = False
    pull: bool = True


class RepositorySummary(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    default_branch: str
    html_url: str
    permissions: RepositoryPermissions


@router.get(
    "/repos",
    summary="List repositories for authenticated GitHub user",
    dependencies=[
        Depends(require_permission(Permission.VIEW_REPOS)),
        Depends(limit_by_ip(key_prefix="phase1:user_repos", limit=10, window_seconds=60)),
    ],
)
async def list_user_repos(
    current_user: TokenPayload = Depends(get_current_user),
) -> dict[str, object]:
    """Return repositories available to the current authenticated user."""
    started = time.perf_counter()
    try:
        token_store = GitHubOAuthTokenStore()
        github_token = await token_store.get_token(jti=current_user.jti)

        if not github_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="GitHub session expired. Please sign in with GitHub again.",
            )

        try:
            async with GitHubClient(token=github_token) as client:
                repos = await client.get_user_repositories()
        except GitHubAPIError as exc:
            logger.warning("Failed to fetch user repositories", extra={"error": str(exc)})
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to fetch repositories from GitHub",
            ) from exc

        normalized: list[RepositorySummary] = []
        for repo in repos:
            if not isinstance(repo, dict):
                continue
            permissions = repo.get("permissions")
            normalized.append(
                RepositorySummary(
                    id=int(repo.get("id", 0)),
                    name=str(repo.get("name", "")),
                    full_name=str(repo.get("full_name", "")),
                    private=bool(repo.get("private", False)),
                    default_branch=str(repo.get("default_branch", "main")),
                    html_url=str(repo.get("html_url", "")),
                    permissions=RepositoryPermissions(
                        **(permissions if isinstance(permissions, dict) else {}),
                    ),
                )
            )

        return success_response([repo.model_dump() for repo in normalized])
    finally:
        observe_repo_fetch_latency_ms(latency_ms=(time.perf_counter() - started) * 1000.0)
