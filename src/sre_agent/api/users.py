"""User management API routes.

This module provides REST endpoints for:
- User CRUD operations
- Role management
- User search and listing
- OAuth account linking

Protected by RBAC - only admins can manage users.
"""

import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import (
    get_current_user,
    require_permission,
)
from sre_agent.auth.rbac import Permission, UserRole
from sre_agent.database import get_db_session
from sre_agent.services.user_service import UserService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])


# =========================================
# REQUEST/RESPONSE MODELS
# =========================================


class CreateUserRequest(BaseModel):
    """Request to create a new user."""

    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    password: Optional[str] = Field(None, min_length=8)
    role: UserRole = UserRole.VIEWER


class UpdateUserRequest(BaseModel):
    """Request to update a user."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    avatar_url: Optional[str] = None
    password: Optional[str] = Field(None, min_length=8)


class ChangeRoleRequest(BaseModel):
    """Request to change a user's role."""

    role: UserRole


class UserResponse(BaseModel):
    """User response model."""

    id: str
    email: str
    name: str
    role: str
    is_active: bool
    avatar_url: Optional[str] = None
    has_github: bool = False
    has_google: bool = False
    created_at: str
    last_login_at: Optional[str] = None


class UserListResponse(BaseModel):
    """Paginated user list response."""

    users: list[UserResponse]
    total: int
    limit: int
    offset: int


class UserStatsResponse(BaseModel):
    """User statistics response."""

    total_users: int
    active_users: int
    inactive_users: int
    by_role: dict[str, int]
    recent_logins_24h: int


def user_to_response(user) -> UserResponse:
    """Convert User model to response."""
    return UserResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        avatar_url=user.avatar_url,
        has_github=bool(user.github_id),
        has_google=bool(user.google_id),
        created_at=user.created_at.isoformat(),
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


# =========================================
# ENDPOINTS
# =========================================


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
    dependencies=[Depends(require_permission(Permission.CREATE_USER))],
)
async def create_user(
    request: CreateUserRequest,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Create a new user (admin only)."""
    service = UserService(session)

    try:
        user = await service.create_user(
            email=request.email,
            name=request.name,
            password=request.password,
            role=request.role,
            created_by=current_user.user_id,
        )
        return user_to_response(user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@router.get(
    "",
    response_model=UserListResponse,
    summary="List users",
    dependencies=[Depends(require_permission(Permission.VIEW_USERS))],
)
async def list_users(
    role: Optional[UserRole] = None,
    search: Optional[str] = Query(None, min_length=2, max_length=100),
    active_only: bool = True,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session=Depends(get_db_session),
) -> UserListResponse:
    """List users with filtering and pagination."""
    service = UserService(session)

    users, total = await service.list_users(
        role=role,
        active_only=active_only,
        search=search,
        limit=limit,
        offset=offset,
    )

    return UserListResponse(
        users=[user_to_response(u) for u in users],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/stats",
    response_model=UserStatsResponse,
    summary="Get user statistics",
    dependencies=[Depends(require_permission(Permission.VIEW_USERS))],
)
async def get_user_stats(
    session=Depends(get_db_session),
) -> UserStatsResponse:
    """Get user statistics (counts by role, etc.)."""
    service = UserService(session)
    stats = await service.get_user_stats()
    return UserStatsResponse(**stats)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get a user by ID",
    dependencies=[Depends(require_permission(Permission.VIEW_USERS))],
)
async def get_user(
    user_id: UUID,
    session=Depends(get_db_session),
) -> UserResponse:
    """Get a user by their ID."""
    service = UserService(session)
    user = await service.get_by_id(user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update a user",
    dependencies=[Depends(require_permission(Permission.UPDATE_USER))],
)
async def update_user(
    user_id: UUID,
    request: UpdateUserRequest,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Update a user's profile."""
    service = UserService(session)

    # Only include non-None fields
    update_fields = {k: v for k, v in request.dict().items() if v is not None}

    if not update_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    user = await service.update_user(
        user_id=user_id,
        updated_by=current_user.user_id,
        **update_fields,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)


@router.post(
    "/{user_id}/role",
    response_model=UserResponse,
    summary="Change user role",
    dependencies=[Depends(require_permission(Permission.ASSIGN_ROLES))],
)
async def change_user_role(
    user_id: UUID,
    request: ChangeRoleRequest,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Change a user's role (admin only)."""
    # Prevent self-demotion for super_admins
    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role",
        )

    # Only super_admin can create super_admins
    if request.role == UserRole.SUPER_ADMIN:
        if current_user.role != UserRole.SUPER_ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super admins can create other super admins",
            )

    service = UserService(session)
    user = await service.change_role(
        user_id=user_id,
        new_role=request.role,
        changed_by=current_user.user_id,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)


@router.post(
    "/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate a user",
    dependencies=[Depends(require_permission(Permission.UPDATE_USER))],
)
async def deactivate_user(
    user_id: UUID,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Deactivate a user account."""
    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    service = UserService(session)
    user = await service.deactivate_user(
        user_id=user_id,
        deactivated_by=current_user.user_id,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)


@router.post(
    "/{user_id}/reactivate",
    response_model=UserResponse,
    summary="Reactivate a user",
    dependencies=[Depends(require_permission(Permission.UPDATE_USER))],
)
async def reactivate_user(
    user_id: UUID,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Reactivate a deactivated user account."""
    service = UserService(session)
    user = await service.reactivate_user(
        user_id=user_id,
        reactivated_by=current_user.user_id,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user",
    dependencies=[Depends(require_permission(Permission.DELETE_USER))],
)
async def delete_user(
    user_id: UUID,
    hard_delete: bool = False,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> None:
    """Delete a user (super admin only)."""
    if user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    service = UserService(session)
    deleted = await service.delete_user(
        user_id=user_id,
        deleted_by=current_user.user_id,
        hard_delete=hard_delete,
    )

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


# =========================================
# SELF-SERVICE ENDPOINTS
# =========================================


@router.get(
    "/me/profile",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def get_my_profile(
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Get the current user's profile."""
    service = UserService(session)
    user = await service.get_by_id(current_user.user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)


@router.patch(
    "/me/profile",
    response_model=UserResponse,
    summary="Update current user profile",
)
async def update_my_profile(
    request: UpdateUserRequest,
    current_user: TokenPayload = Depends(get_current_user),
    session=Depends(get_db_session),
) -> UserResponse:
    """Update the current user's profile."""
    service = UserService(session)

    # Only allow updating name, avatar, password
    allowed_fields = {"name", "avatar_url", "password"}
    update_fields = {
        k: v for k, v in request.dict().items() if v is not None and k in allowed_fields
    }

    if not update_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )

    user = await service.update_user(
        user_id=current_user.user_id,
        updated_by=current_user.user_id,
        **update_fields,
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user_to_response(user)
