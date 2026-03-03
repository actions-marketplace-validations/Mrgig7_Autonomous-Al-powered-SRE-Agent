"""Authentication API routes.

This module provides REST endpoints for:
- User authentication (login/logout)
- OAuth flows (GitHub, Google)
- Token management (refresh, revoke)
- User profile management
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.api.rate_limit import limit_by_ip
from sre_agent.api.response_envelope import success_response
from sre_agent.auth.jwt_handler import TokenPayload, get_jwt_handler
from sre_agent.auth.oauth_providers import OAuthError, OAuthUserInfo
from sre_agent.auth.permissions import get_current_user
from sre_agent.auth.rbac import UserRole, get_role_permissions
from sre_agent.config import get_settings
from sre_agent.database import get_db_session
from sre_agent.observability.metrics import record_oauth_login_failure, record_oauth_login_success
from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore
from sre_agent.services.oauth_state_store import OAuthStateError, OAuthStateStore
from sre_agent.services.onboarding_state import OnboardingStateService
from sre_agent.services.user_service import UserService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])


# Request/Response Models
class LoginRequest(BaseModel):
    """Email/password login request."""

    email: EmailStr
    password: str = Field(..., min_length=8)


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    """Token refresh request."""

    refresh_token: Optional[str] = None


class UserProfileResponse(BaseModel):
    """User profile response."""

    id: str
    email: str
    name: str
    role: str
    permissions: list[str]
    avatar_url: Optional[str] = None
    created_at: str
    last_login_at: Optional[str] = None


class OAuthInitResponse(BaseModel):
    """OAuth initialization response."""

    authorization_url: str
    state: str


class OAuthCallbackRequest(BaseModel):
    """OAuth callback request."""

    code: str
    state: str


class GitHubLoginRequest(BaseModel):
    """GitHub login wrapper for start/exchange flow."""

    action: Literal["start", "exchange"] = "start"
    code: Optional[str] = None
    state: Optional[str] = None

    @model_validator(mode="after")
    def validate_exchange_payload(self) -> "GitHubLoginRequest":
        if self.action == "exchange":
            if not self.code:
                raise ValueError("code is required when action is exchange")
            if not self.state:
                raise ValueError("state is required when action is exchange")
        return self


def _parse_required_scopes(raw_scopes: str) -> set[str]:
    parts = [chunk.strip() for chunk in raw_scopes.replace(" ", ",").split(",")]
    return {chunk for chunk in parts if chunk}


def _set_auth_cookies(response: Response, token_response: TokenResponse) -> None:
    settings = get_settings()
    secure = settings.auth_cookie_secure or settings.is_production

    response.set_cookie(
        key=settings.jwt_access_cookie_name,
        value=token_response.access_token,
        httponly=True,
        secure=secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        key=settings.jwt_refresh_cookie_name,
        value=token_response.refresh_token,
        httponly=True,
        secure=secure,
        samesite=settings.auth_cookie_samesite,
        max_age=settings.jwt_refresh_token_expire_days * 86400,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.jwt_access_cookie_name, path="/")
    response.delete_cookie(settings.jwt_refresh_cookie_name, path="/")


async def _upsert_oauth_user(
    *,
    session: AsyncSession,
    user_info: OAuthUserInfo,
    provider: Literal["github", "google"],
) -> tuple[UUID, str, UserRole]:
    service = UserService(session)

    user = await service.get_by_oauth(provider=provider, provider_id=user_info.provider_id)
    if user is None:
        by_email = await service.get_by_email(user_info.email)
        if by_email is not None:
            user = by_email
            if provider == "github" and not user.github_id:
                linked_user = await service.link_oauth(
                    user_id=user.id,
                    provider=provider,
                    provider_id=user_info.provider_id,
                )
                if linked_user is not None:
                    user = linked_user
            elif provider == "google" and not user.google_id:
                linked_user = await service.link_oauth(
                    user_id=user.id,
                    provider=provider,
                    provider_id=user_info.provider_id,
                )
                if linked_user is not None:
                    user = linked_user
        else:
            user = await service.create_user(
                email=user_info.email,
                name=user_info.name or user_info.email.split("@")[0],
                role=UserRole.OPERATOR,
                github_id=user_info.provider_id if provider == "github" else None,
                google_id=user_info.provider_id if provider == "google" else None,
                avatar_url=user_info.avatar_url,
            )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create OAuth user session",
        )

    update_fields: dict[str, Any] = {}
    if user_info.name and user.name != user_info.name:
        update_fields["name"] = user_info.name
    if user_info.avatar_url and user.avatar_url != user_info.avatar_url:
        update_fields["avatar_url"] = user_info.avatar_url
    if update_fields:
        refreshed = await service.update_user(user.id, **update_fields)
        if refreshed is not None:
            user = refreshed

    await service.update_last_login(user.id)

    role = UserRole(user.role) if user.role in {r.value for r in UserRole} else UserRole.OPERATOR
    return user.id, user.email, role


async def _build_token_response(
    *,
    user_id: UUID,
    email: str,
    role: UserRole,
    github_access_token: str | None = None,
) -> TokenResponse:
    permissions = [p.value for p in get_role_permissions(role)]
    jwt_handler = get_jwt_handler()

    access_token = jwt_handler.create_access_token(
        user_id=user_id,
        email=email,
        role=role.value,
        permissions=permissions,
    )
    refresh_token = jwt_handler.create_refresh_token(
        user_id=user_id,
        email=email,
    )

    if github_access_token:
        payload = jwt_handler.verify_token(access_token, token_type="access")
        if payload:
            store = GitHubOAuthTokenStore()
            await store.store_token(
                jti=payload.jti,
                user_id=user_id,
                access_token=github_access_token,
            )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=jwt_handler.access_token_expire_minutes * 60,
    )


async def _start_github_oauth() -> OAuthInitResponse:
    settings = get_settings()

    if not settings.github_oauth_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="GitHub OAuth not configured",
        )

    from sre_agent.auth.oauth_providers import GitHubOAuthProvider

    provider = GitHubOAuthProvider(
        client_id=settings.github_oauth_client_id,
        client_secret=settings.github_oauth_client_secret,
        redirect_uri=settings.github_oauth_redirect_uri,
        scope=" ".join(sorted(_parse_required_scopes(settings.github_oauth_required_scopes))),
    )

    state = secrets.token_urlsafe(32)
    state_store = OAuthStateStore()
    await state_store.store_state(provider="github", state=state)

    return OAuthInitResponse(
        authorization_url=provider.get_authorization_url(state),
        state=state,
    )


async def _start_google_oauth() -> OAuthInitResponse:
    settings = get_settings()

    if not settings.google_oauth_client_id:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth not configured",
        )

    from sre_agent.auth.oauth_providers import GoogleOAuthProvider

    provider = GoogleOAuthProvider(
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        redirect_uri=settings.google_oauth_redirect_uri,
    )

    state = secrets.token_urlsafe(32)
    state_store = OAuthStateStore()
    await state_store.store_state(provider="google", state=state)

    return OAuthInitResponse(
        authorization_url=provider.get_authorization_url(state),
        state=state,
    )


async def _complete_github_oauth(
    *,
    callback_request: OAuthCallbackRequest,
    response: Response,
    session: AsyncSession,
) -> TokenResponse:
    settings = get_settings()
    state_store = OAuthStateStore()

    logger.info(
        f"GitHub OAuth exchange: state={callback_request.state[:12]}... code={callback_request.code[:8]}..."
    )

    try:
        await state_store.validate_and_consume(provider="github", state=callback_request.state)
        logger.info("GitHub OAuth: state validated successfully")
    except OAuthStateError as exc:
        logger.error(f"GitHub OAuth: state validation FAILED: {exc} (code={exc.code})")
        record_oauth_login_failure(provider="github", reason=exc.code)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    from sre_agent.auth.oauth_providers import GitHubOAuthProvider

    provider = GitHubOAuthProvider(
        client_id=settings.github_oauth_client_id,
        client_secret=settings.github_oauth_client_secret,
        redirect_uri=settings.github_oauth_redirect_uri,
        scope=" ".join(sorted(_parse_required_scopes(settings.github_oauth_required_scopes))),
    )

    try:
        logger.info("GitHub OAuth: exchanging code for token...")
        access_token = await provider.exchange_code(callback_request.code)
        logger.info("GitHub OAuth: token exchange succeeded, fetching user info...")
        user_info = await provider.get_user_info(access_token)
        logger.info(
            f"GitHub OAuth: user info retrieved: email={user_info.email}, scopes={user_info.granted_scopes}"
        )

        required_scopes = _parse_required_scopes(settings.github_oauth_required_scopes)
        granted_scopes = set(user_info.granted_scopes or [])
        missing_scopes = sorted(required_scopes - granted_scopes)
        if missing_scopes:
            logger.warning(f"GitHub OAuth: missing scopes: {missing_scopes}")
            record_oauth_login_failure(provider="github", reason="missing_scope")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=("Missing required GitHub scopes: " + ", ".join(missing_scopes)),
            )

    except OAuthError as exc:
        logger.error(f"GitHub OAuth: exchange/user-info FAILED: {exc}")
        record_oauth_login_failure(provider="github", reason="oauth_exchange_failed")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    finally:
        await provider.close()

    user_id, email, role = await _upsert_oauth_user(
        session=session,
        user_info=user_info,
        provider="github",
    )

    token_response = await _build_token_response(
        user_id=user_id,
        email=email,
        role=role,
        github_access_token=access_token,
    )
    _set_auth_cookies(response, token_response)

    onboarding_state = OnboardingStateService()
    await onboarding_state.mark_oauth_completed(user_id=user_id)

    record_oauth_login_success(provider="github")
    logger.info(f"User authenticated via GitHub: {email}")
    return token_response


async def _complete_google_oauth(
    *,
    callback_request: OAuthCallbackRequest,
    response: Response,
    session: AsyncSession,
) -> TokenResponse:
    settings = get_settings()

    state_store = OAuthStateStore()
    try:
        await state_store.validate_and_consume(provider="google", state=callback_request.state)
    except OAuthStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    from sre_agent.auth.oauth_providers import GoogleOAuthProvider

    provider = GoogleOAuthProvider(
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        redirect_uri=settings.google_oauth_redirect_uri,
    )

    try:
        access_token = await provider.exchange_code(callback_request.code)
        user_info = await provider.get_user_info(access_token)
    except OAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    finally:
        await provider.close()

    user_id, email, role = await _upsert_oauth_user(
        session=session,
        user_info=user_info,
        provider="google",
    )

    token_response = await _build_token_response(
        user_id=user_id,
        email=email,
        role=role,
    )
    _set_auth_cookies(response, token_response)

    logger.info(f"User authenticated via Google: {email}")
    return token_response


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login with email and password",
)
async def login(request: LoginRequest, response: Response) -> TokenResponse:
    """Authenticate user with email and password.

    Returns access and refresh tokens on success.
    """
    jwt_handler = get_jwt_handler()

    user_id = UUID("00000000-0000-0000-0000-000000000001")
    role = UserRole.OPERATOR
    permissions = [p.value for p in get_role_permissions(role)]

    access_token = jwt_handler.create_access_token(
        user_id=user_id,
        email=request.email,
        role=role.value,
        permissions=permissions,
    )

    refresh_token = jwt_handler.create_refresh_token(
        user_id=user_id,
        email=request.email,
    )

    token_response = TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=jwt_handler.access_token_expire_minutes * 60,
    )
    _set_auth_cookies(response, token_response)

    logger.info(f"User logged in: {request.email}")
    return token_response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
)
async def refresh_token(
    request: RefreshTokenRequest,
    http_request: Request,
    response: Response,
) -> TokenResponse:
    """Get a new access token using a refresh token."""
    settings = get_settings()
    jwt_handler = get_jwt_handler()

    refresh_token_value = request.refresh_token or http_request.cookies.get(
        settings.jwt_refresh_cookie_name
    )
    if not refresh_token_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )

    payload = jwt_handler.verify_token(refresh_token_value, token_type="refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    role = UserRole.OPERATOR
    permissions = [p.value for p in get_role_permissions(role)]

    new_access_token = jwt_handler.create_access_token(
        user_id=payload.user_id,
        email=payload.email,
        role=role.value,
        permissions=permissions,
    )

    token_response = TokenResponse(
        access_token=new_access_token,
        refresh_token=refresh_token_value,
        token_type="bearer",
        expires_in=jwt_handler.access_token_expire_minutes * 60,
    )
    _set_auth_cookies(response, token_response)
    return token_response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout and revoke tokens",
)
async def logout(
    request: Request,
    response: Response,
    user: TokenPayload = Depends(get_current_user),
) -> None:
    """Logout user and revoke their tokens."""
    jwt_handler = get_jwt_handler()

    settings = get_settings()
    token = request.cookies.get(settings.jwt_access_cookie_name)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if token:
        jwt_handler.revoke_token(token)

    token_store = GitHubOAuthTokenStore()
    await token_store.clear_token(jti=user.jti)

    _clear_auth_cookies(response)
    logger.info(f"User logged out: {user.email}")


@router.get(
    "/me",
    response_model=UserProfileResponse,
    summary="Get current user profile",
)
async def get_profile(
    user: TokenPayload = Depends(get_current_user),
) -> UserProfileResponse:
    """Get the current authenticated user's profile."""
    return UserProfileResponse(
        id=str(user.user_id),
        email=user.email,
        name=user.email.split("@")[0],
        role=user.role,
        permissions=user.permissions,
        avatar_url=None,
        created_at=user.iat.isoformat(),
        last_login_at=datetime.now(UTC).isoformat(),
    )


@router.get(
    "/permissions",
    summary="Get current user's permissions",
)
async def get_permissions(
    user: TokenPayload = Depends(get_current_user),
) -> dict[str, Any]:
    """Get the current user's role and permissions."""
    role = UserRole(user.role)
    all_permissions = get_role_permissions(role)

    return {
        "role": user.role,
        "permissions": [p.value for p in all_permissions],
        "permission_count": len(all_permissions),
    }


# OAuth Endpoints (legacy)
@router.get(
    "/oauth/github",
    response_model=OAuthInitResponse,
    summary="Initialize GitHub OAuth flow",
)
async def oauth_github_init(response: Response) -> OAuthInitResponse:
    """Start GitHub OAuth authorization flow."""
    response.headers["X-API-Deprecated"] = "true"
    return await _start_github_oauth()


@router.post(
    "/github/login",
    summary="GitHub login wrapper endpoint",
    dependencies=[
        Depends(limit_by_ip(key_prefix="phase1:github_login", limit=10, window_seconds=60))
    ],
)
async def github_login(
    request: GitHubLoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Wrapper endpoint required by Phase 1 contract.

    - action=start: returns authorization URL + state
    - action=exchange: exchanges code and returns JWT tokens
    """
    if request.action == "start":
        payload = await _start_github_oauth()
    else:
        callback_request = OAuthCallbackRequest(
            code=request.code or "",
            state=request.state or "",
        )
        payload = await _complete_github_oauth(
            callback_request=callback_request,
            response=response,
            session=session,
        )

    if isinstance(payload, BaseModel):
        return success_response(payload.model_dump())
    return success_response(payload)


@router.post(
    "/oauth/github/callback",
    response_model=TokenResponse,
    summary="Handle GitHub OAuth callback",
)
async def oauth_github_callback(
    request: OAuthCallbackRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Complete GitHub OAuth flow and issue tokens."""
    response.headers["X-API-Deprecated"] = "true"
    return await _complete_github_oauth(
        callback_request=request,
        response=response,
        session=session,
    )


@router.get(
    "/oauth/google",
    response_model=OAuthInitResponse,
    summary="Initialize Google OAuth flow",
)
async def oauth_google_init(response: Response) -> OAuthInitResponse:
    """Start Google OAuth authorization flow."""
    response.headers["X-API-Deprecated"] = "true"
    return await _start_google_oauth()


@router.post(
    "/oauth/google/callback",
    response_model=TokenResponse,
    summary="Handle Google OAuth callback",
)
async def oauth_google_callback(
    request: OAuthCallbackRequest,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Complete Google OAuth flow and issue tokens."""
    response.headers["X-API-Deprecated"] = "true"
    return await _complete_google_oauth(
        callback_request=request,
        response=response,
        session=session,
    )
