"""Role-based access control (RBAC) system.

This module defines the role hierarchy and permission model
for the SRE Agent platform.
"""

from enum import Enum


class UserRole(str, Enum):
    """User roles with hierarchical permissions.

    Roles are ordered from least to most privileged.
    Higher roles inherit all permissions from lower roles.
    """

    VIEWER = "viewer"  # Read-only access
    OPERATOR = "operator"  # Can trigger actions
    ADMIN = "admin"  # Can manage users and settings
    SUPER_ADMIN = "super_admin"  # Full system access


class Permission(str, Enum):
    """Granular permissions for the SRE Agent platform.

    Permissions are grouped by domain for clarity.
    """

    # Dashboard & Viewing
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_FAILURES = "view_failures"
    VIEW_FIXES = "view_fixes"
    VIEW_ANALYTICS = "view_analytics"
    VIEW_AUDIT_LOGS = "view_audit_logs"

    # Failure Management
    TRIGGER_ANALYSIS = "trigger_analysis"
    RETRY_PIPELINE = "retry_pipeline"

    # Fix Management
    GENERATE_FIX = "generate_fix"
    APPROVE_FIX = "approve_fix"
    REJECT_FIX = "reject_fix"
    AUTO_APPROVE_HIGH_CONFIDENCE = "auto_approve_high_confidence"

    # PR Management
    CREATE_PR = "create_pr"
    MERGE_PR = "merge_pr"

    # Notification Management
    SEND_NOTIFICATION = "send_notification"
    MANAGE_CHANNELS = "manage_channels"

    # User Management
    VIEW_USERS = "view_users"
    CREATE_USER = "create_user"
    UPDATE_USER = "update_user"
    DELETE_USER = "delete_user"
    ASSIGN_ROLES = "assign_roles"

    # Repository Management
    VIEW_REPOS = "view_repos"
    ADD_REPO = "add_repo"
    REMOVE_REPO = "remove_repo"
    CONFIGURE_REPO = "configure_repo"

    # System Configuration
    VIEW_SETTINGS = "view_settings"
    UPDATE_SETTINGS = "update_settings"
    MANAGE_INTEGRATIONS = "manage_integrations"

    # API Access
    API_READ = "api_read"
    API_WRITE = "api_write"
    API_ADMIN = "api_admin"


# Role to permissions mapping
ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.VIEWER: {
        Permission.VIEW_DASHBOARD,
        Permission.VIEW_FAILURES,
        Permission.VIEW_FIXES,
        Permission.VIEW_ANALYTICS,
        Permission.VIEW_REPOS,
        Permission.VIEW_SETTINGS,
        Permission.API_READ,
    },
    UserRole.OPERATOR: {
        # Inherits VIEWER permissions implicitly
        Permission.TRIGGER_ANALYSIS,
        Permission.RETRY_PIPELINE,
        Permission.GENERATE_FIX,
        Permission.APPROVE_FIX,
        Permission.REJECT_FIX,
        Permission.CREATE_PR,
        Permission.SEND_NOTIFICATION,
        Permission.API_WRITE,
    },
    UserRole.ADMIN: {
        # Inherits OPERATOR permissions implicitly
        Permission.VIEW_AUDIT_LOGS,
        Permission.VIEW_USERS,
        Permission.CREATE_USER,
        Permission.UPDATE_USER,
        Permission.ASSIGN_ROLES,
        Permission.ADD_REPO,
        Permission.REMOVE_REPO,
        Permission.CONFIGURE_REPO,
        Permission.UPDATE_SETTINGS,
        Permission.MANAGE_CHANNELS,
        Permission.MANAGE_INTEGRATIONS,
        Permission.AUTO_APPROVE_HIGH_CONFIDENCE,
        Permission.MERGE_PR,
    },
    UserRole.SUPER_ADMIN: {
        # Full access
        Permission.DELETE_USER,
        Permission.API_ADMIN,
    },
}


def get_role_permissions(role: UserRole) -> set[Permission]:
    """Get all permissions for a role, including inherited ones.

    Args:
        role: The user role

    Returns:
        Set of all permissions the role has
    """
    role_hierarchy = [
        UserRole.VIEWER,
        UserRole.OPERATOR,
        UserRole.ADMIN,
        UserRole.SUPER_ADMIN,
    ]

    role_index = role_hierarchy.index(role)

    permissions: set[Permission] = set()
    for i in range(role_index + 1):
        current_role = role_hierarchy[i]
        permissions.update(ROLE_PERMISSIONS.get(current_role, set()))

    return permissions


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Check if a role has a specific permission.

    Args:
        role: The user role
        permission: The permission to check

    Returns:
        True if the role has the permission
    """
    return permission in get_role_permissions(role)


def get_role_display_name(role: UserRole) -> str:
    """Get human-readable display name for a role.

    Args:
        role: The user role

    Returns:
        Display name string
    """
    display_names = {
        UserRole.VIEWER: "Viewer",
        UserRole.OPERATOR: "Operator",
        UserRole.ADMIN: "Administrator",
        UserRole.SUPER_ADMIN: "Super Administrator",
    }
    return display_names.get(role, role.value)


def get_role_description(role: UserRole) -> str:
    """Get description of what a role can do.

    Args:
        role: The user role

    Returns:
        Description string
    """
    descriptions = {
        UserRole.VIEWER: "Read-only access to dashboards, failures, and analytics",
        UserRole.OPERATOR: "Can trigger analysis, approve/reject fixes, and create PRs",
        UserRole.ADMIN: "Can manage users, repositories, and system settings",
        UserRole.SUPER_ADMIN: "Full system access including user deletion and API admin",
    }
    return descriptions.get(role, "")
