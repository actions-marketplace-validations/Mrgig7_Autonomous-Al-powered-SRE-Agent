"""Integration onboarding API endpoints."""

from __future__ import annotations

import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.api.rate_limit import limit_by_ip
from sre_agent.api.response_envelope import success_response
from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import get_current_user, require_permission
from sre_agent.auth.rbac import Permission
from sre_agent.config import get_settings
from sre_agent.database import get_db_session
from sre_agent.observability.metrics import record_integration_install_success
from sre_agent.services.github_app_installations import GitHubAppInstallationService
from sre_agent.services.github_client import GitHubAPIError, GitHubClient
from sre_agent.services.github_install_state import GitHubInstallStateStore
from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore
from sre_agent.services.onboarding_state import OnboardingStateService

router = APIRouter(prefix="/integration", tags=["integration"])


class InstallRequest(BaseModel):
    repository: str = Field(..., min_length=3, description="Repository in owner/repo format")
    automation_mode: str = Field(default="suggest", description="suggest | auto_pr | auto_merge")


class InstallResponse(BaseModel):
    repository: str
    install_url: str
    configured: bool
    provider: str = "github"
    install_state: str
    status: str = "installing"


class ConfirmInstallRequest(BaseModel):
    state: str = Field(..., min_length=16)
    installation_id: int
    setup_action: str | None = None


class InstallStatusResponse(BaseModel):
    repository: str | None = None
    status: str
    installation_id: int | None = None


class OnboardingStatusResponse(BaseModel):
    onboarding_status: dict[str, bool]
    selected_repository: str | None = None
    installation_id: int | None = None


def _append_query_params(base_url: str, params: dict[str, str]) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query))
    query.update(params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _ensure_install_flow_enabled() -> None:
    settings = get_settings()
    if settings.phase1_enable_install_flow:
        return
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Phase 1 install flow is disabled",
    )


def _require_repo_admin_or_maintain(repo_data: dict[str, object]) -> None:
    permissions = repo_data.get("permissions")
    if not isinstance(permissions, dict):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing repository permissions for installation",
        )

    has_permission = bool(permissions.get("admin") or permissions.get("maintain"))
    if has_permission:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Missing repository permissions for installation",
    )


@router.post(
    "/install",
    summary="Build GitHub App installation URL for a repository",
    dependencies=[
        Depends(require_permission(Permission.VIEW_REPOS)),
        Depends(limit_by_ip(key_prefix="phase1:integration_install", limit=10, window_seconds=60)),
    ],
)
async def build_install_link(
    payload: InstallRequest,
    current_user: TokenPayload = Depends(get_current_user),
) -> dict[str, object]:
    _ensure_install_flow_enabled()

    settings = get_settings()
    if not settings.github_app_install_url:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="GitHub App install URL is not configured",
        )

    token_store = GitHubOAuthTokenStore()
    github_token = await token_store.get_token(jti=current_user.jti)
    if not github_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="GitHub session expired. Please sign in with GitHub again.",
        )

    try:
        async with GitHubClient(token=github_token) as client:
            repo_data = await client.get_repository(payload.repository)
    except GitHubAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch repository details from GitHub",
        ) from exc

    _require_repo_admin_or_maintain(repo_data)

    repo_id_raw = repo_data.get("id")
    if not isinstance(repo_id_raw, int):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository metadata missing id",
        )

    repo_full_name = str(repo_data.get("full_name") or payload.repository)
    install_state = secrets.token_urlsafe(32)

    install_state_store = GitHubInstallStateStore()
    await install_state_store.save_state(
        state=install_state,
        user_id=current_user.user_id,
        repo_id=repo_id_raw,
        repo_full_name=repo_full_name,
        automation_mode=payload.automation_mode,
    )

    onboarding_service = OnboardingStateService()
    await onboarding_service.mark_repo_selected(
        user_id=current_user.user_id,
        repo_full_name=repo_full_name,
    )

    install_url = _append_query_params(
        settings.github_app_install_url,
        {
            "state": install_state,
            "repository": repo_full_name,
        },
    )

    result = InstallResponse(
        repository=repo_full_name,
        install_url=install_url,
        configured=True,
        install_state=install_state,
    )

    return success_response(result.model_dump())


@router.post(
    "/install/confirm",
    summary="Confirm GitHub App installation from callback payload",
    dependencies=[Depends(require_permission(Permission.VIEW_REPOS))],
)
async def confirm_installation(
    payload: ConfirmInstallRequest,
    current_user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_install_flow_enabled()

    install_state_store = GitHubInstallStateStore()
    install_state = await install_state_store.consume_state(state=payload.state)
    if install_state is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired installation state",
        )

    if install_state.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Installation state does not belong to current user",
        )

    installation_service = GitHubAppInstallationService(session)
    await installation_service.upsert_installation(
        user_id=current_user.user_id,
        repo_id=install_state.repo_id,
        repo_full_name=install_state.repo_full_name,
        installation_id=payload.installation_id,
        automation_mode=install_state.automation_mode,
    )

    onboarding_service = OnboardingStateService()
    await onboarding_service.mark_app_installed(
        user_id=current_user.user_id,
        repo_full_name=install_state.repo_full_name,
        installation_id=payload.installation_id,
    )

    record_integration_install_success()

    result = InstallStatusResponse(
        repository=install_state.repo_full_name,
        status="installed",
        installation_id=payload.installation_id,
    )

    return success_response(result.model_dump())


@router.get(
    "/install/status",
    summary="Get install status for selected repository",
    dependencies=[Depends(require_permission(Permission.VIEW_REPOS))],
)
async def install_status(
    repository: str | None = None,
    current_user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    _ensure_install_flow_enabled()

    onboarding_service = OnboardingStateService()
    onboarding = await onboarding_service.get_state(user_id=current_user.user_id)

    selected_repo = repository or onboarding.get("selected_repository")
    if isinstance(selected_repo, str):
        installation_service = GitHubAppInstallationService(session)
        installation = await installation_service.get_by_user_repo_full_name(
            user_id=current_user.user_id,
            repo_full_name=selected_repo,
        )
        if installation is not None:
            result = InstallStatusResponse(
                repository=selected_repo,
                status="installed",
                installation_id=installation.installation_id,
            )
        elif bool(onboarding.get("repo_selected")):
            result = InstallStatusResponse(repository=selected_repo, status="installing")
        else:
            result = InstallStatusResponse(repository=selected_repo, status="not_started")
    else:
        result = InstallStatusResponse(status="not_started")

    return success_response(result.model_dump())


@router.get(
    "/onboarding/status",
    summary="Get current onboarding progress flags",
    dependencies=[Depends(require_permission(Permission.VIEW_REPOS))],
)
async def onboarding_status(
    current_user: TokenPayload = Depends(get_current_user),
) -> dict[str, object]:
    onboarding_service = OnboardingStateService()
    state_data = await onboarding_service.get_state(user_id=current_user.user_id)

    result = OnboardingStatusResponse(
        onboarding_status={
            "oauth_completed": bool(state_data.get("oauth_completed")),
            "repo_selected": bool(state_data.get("repo_selected")),
            "app_installed": bool(state_data.get("app_installed")),
            "dashboard_ready": bool(state_data.get("dashboard_ready")),
        },
        selected_repository=(
            str(state_data.get("selected_repository"))
            if isinstance(state_data.get("selected_repository"), str)
            else None
        ),
        installation_id=(
            int(state_data.get("installation_id"))
            if isinstance(state_data.get("installation_id"), int)
            else None
        ),
    )

    return success_response(result.model_dump())
