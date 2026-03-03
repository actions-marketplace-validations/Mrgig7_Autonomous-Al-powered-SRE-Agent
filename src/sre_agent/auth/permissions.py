"""Permission decorators and dependency injection.

This module provides FastAPI dependencies and decorators for
enforcing authentication and authorization on endpoints.
"""

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sre_agent.auth.jwt_handler import TokenPayload, get_jwt_handler
from sre_agent.auth.rbac import Permission, UserRole, get_role_permissions, has_permission
from sre_agent.config import get_settings

logger = logging.getLogger(__name__)

# HTTP Bearer security scheme
security = HTTPBearer(auto_error=False)


class AuthenticationError(HTTPException):
    """Raised when authentication fails."""

    def __init__(self, detail: str = "Authentication required"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthorizationError(HTTPException):
    """Raised when authorization fails."""

    def __init__(self, detail: str = "Insufficient permissions"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenPayload:
    """FastAPI dependency to get the current authenticated user.

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        TokenPayload with user information

    Raises:
        AuthenticationError: If authentication fails
    """
    settings = get_settings()
    token = request.cookies.get(settings.jwt_access_cookie_name)
    if not token and credentials:
        token = credentials.credentials
    if not token:
        raise AuthenticationError("No authorization token provided")

    jwt_handler = get_jwt_handler()
    payload = jwt_handler.verify_token(token, token_type="access")

    if not payload:
        raise AuthenticationError("Invalid or expired token")

    request.state.user = payload
    return payload


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[TokenPayload]:
    """FastAPI dependency to get the current user if authenticated.

    Returns None instead of raising an error if not authenticated.
    Useful for endpoints that work for both authenticated and anonymous users.

    Args:
        credentials: HTTP Bearer credentials

    Returns:
        TokenPayload or None
    """
    settings = get_settings()
    token = request.cookies.get(settings.jwt_access_cookie_name)
    if not token and credentials:
        token = credentials.credentials
    if not token:
        return None

    try:
        jwt_handler = get_jwt_handler()
        payload = jwt_handler.verify_token(token, token_type="access")
        request.state.user = payload
        return payload
    except Exception:
        return None


def require_permission(*required_permissions: Permission):
    """Dependency factory requiring specific permissions.

    Args:
        required_permissions: One or more permissions required

    Returns:
        FastAPI dependency function
    """

    async def dependency(
        user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        user_role = UserRole(user.role)

        for permission in required_permissions:
            if not has_permission(user_role, permission):
                logger.warning(
                    f"Permission denied: user {user.email} lacks {permission.value}",
                    extra={
                        "user_id": str(user.user_id),
                        "role": user.role,
                        "required_permission": permission.value,
                    },
                )
                raise AuthorizationError(f"Required permission: {permission.value}")

        return user

    return dependency


def require_role(*required_roles: UserRole):
    """Dependency factory requiring specific roles.

    Args:
        required_roles: One or more roles (user must have at least one)

    Returns:
        FastAPI dependency function
    """

    async def dependency(
        user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        user_role = UserRole(user.role)

        if user_role not in required_roles:
            logger.warning(
                f"Role denied: user {user.email} has role {user_role.value}",
                extra={
                    "user_id": str(user.user_id),
                    "user_role": user.role,
                    "required_roles": [r.value for r in required_roles],
                },
            )
            raise AuthorizationError(f"Required role: {', '.join(r.value for r in required_roles)}")

        return user

    return dependency


def require_admin():
    """Dependency requiring admin or super_admin role."""
    return require_role(UserRole.ADMIN, UserRole.SUPER_ADMIN)


def require_super_admin():
    """Dependency requiring super_admin role."""
    return require_role(UserRole.SUPER_ADMIN)


class PermissionChecker:
    """FastAPI dependency class for permission checking.

    Can be used as a dependency that stores the required permissions
    and checks them against the current user.

    Usage:
        @router.get("/endpoint", dependencies=[Depends(PermissionChecker(Permission.VIEW_DASHBOARD))])
        async def endpoint():
            ...
    """

    def __init__(self, *permissions: Permission):
        self.permissions = permissions

    async def __call__(
        self,
        user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        user_role = UserRole(user.role)

        for permission in self.permissions:
            if not has_permission(user_role, permission):
                raise AuthorizationError(f"Required permission: {permission.value}")

        return user


class RoleChecker:
    """FastAPI dependency class for role checking.

    Usage:
        @router.get("/admin", dependencies=[Depends(RoleChecker(UserRole.ADMIN))])
        async def admin_endpoint():
            ...
    """

    def __init__(self, *roles: UserRole):
        self.roles = roles

    async def __call__(
        self,
        user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        user_role = UserRole(user.role)

        if user_role not in self.roles:
            raise AuthorizationError(f"Required role: {', '.join(r.value for r in self.roles)}")

        return user


def get_user_permissions(user: TokenPayload) -> set[Permission]:
    """Get all permissions for the current user.

    Args:
        user: Current user's token payload

    Returns:
        Set of Permission enums
    """
    return get_role_permissions(UserRole(user.role))


def check_permission(user: TokenPayload, permission: Permission) -> bool:
    """Check if a user has a specific permission.

    Args:
        user: User's token payload
        permission: Permission to check

    Returns:
        True if user has the permission
    """
    return has_permission(UserRole(user.role), permission)
