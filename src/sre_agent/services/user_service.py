"""User management service.

This module provides CRUD operations for users with:
- Secure password hashing
- OAuth account linking
- Role management
- Batch operations for MNC scale
"""

import logging
import secrets
from datetime import UTC, datetime
from typing import Any, Optional
from uuid import UUID

import bcrypt
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.auth.rbac import UserRole
from sre_agent.models.user import AuditAction, User
from sre_agent.services.audit_service import get_audit_service

logger = logging.getLogger(__name__)


class UserService:
    """Service for user management operations.

    Handles all user-related operations with proper
    security practices and audit logging.
    """

    def __init__(self, session: AsyncSession):
        """Initialize with database session.

        Args:
            session: SQLAlchemy async session
        """
        self.session = session
        self._audit = get_audit_service()

    # =========================================
    # PASSWORD HANDLING
    # =========================================

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password securely using bcrypt.

        Args:
            password: Plain text password

        Returns:
            Hashed password
        """
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(password.encode(), salt).decode()

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """Verify a password against its hash.

        Args:
            password: Plain text password
            hashed: Stored hash

        Returns:
            True if password matches
        """
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except Exception:
            return False

    @staticmethod
    def generate_temp_password() -> str:
        """Generate a secure temporary password."""
        return secrets.token_urlsafe(16)

    # =========================================
    # CRUD OPERATIONS
    # =========================================

    async def create_user(
        self,
        email: str,
        name: str,
        password: Optional[str] = None,
        role: UserRole = UserRole.VIEWER,
        github_id: Optional[str] = None,
        google_id: Optional[str] = None,
        avatar_url: Optional[str] = None,
        created_by: Optional[UUID] = None,
    ) -> User:
        """Create a new user.

        Args:
            email: User's email (unique)
            name: Display name
            password: Optional password (for email/password auth)
            role: User's role
            github_id: GitHub OAuth ID
            google_id: Google OAuth ID
            avatar_url: Profile picture URL
            created_by: Admin who created this user

        Returns:
            Created User object

        Raises:
            ValueError: If email already exists
        """
        # Check for existing user
        existing = await self.get_by_email(email)
        if existing:
            raise ValueError(f"User with email {email} already exists")

        # Hash password if provided
        password_hash = self.hash_password(password) if password else None

        user = User(
            email=email,
            name=name,
            password_hash=password_hash,
            role=role.value,
            github_id=github_id,
            google_id=google_id,
            avatar_url=avatar_url,
            is_active=True,
        )

        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        # Audit log
        self._audit.log(
            AuditAction.USER_CREATED,
            resource_type="user",
            resource_id=str(user.id),
            user_id=created_by,
            details={"email": email, "role": role.value},
        )

        logger.info(f"User created: {email} (role={role.value})")
        return user

    async def get_by_id(self, user_id: UUID) -> Optional[User]:
        """Get user by ID.

        Args:
            user_id: User's UUID

        Returns:
            User or None
        """
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email.

        Args:
            email: User's email

        Returns:
            User or None
        """
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_oauth(
        self,
        provider: str,
        provider_id: str,
    ) -> Optional[User]:
        """Get user by OAuth provider ID.

        Args:
            provider: "github" or "google"
            provider_id: Provider's user ID

        Returns:
            User or None
        """
        if provider == "github":
            condition = User.github_id == provider_id
        elif provider == "google":
            condition = User.google_id == provider_id
        else:
            raise ValueError(f"Unknown provider: {provider}")

        result = await self.session.execute(select(User).where(condition))
        return result.scalar_one_or_none()

    async def update_user(
        self,
        user_id: UUID,
        updated_by: Optional[UUID] = None,
        **fields,
    ) -> Optional[User]:
        """Update user fields.

        Args:
            user_id: User to update
            updated_by: Admin performing update
            **fields: Fields to update

        Returns:
            Updated User or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        # Handle password specially
        if "password" in fields:
            fields["password_hash"] = self.hash_password(fields.pop("password"))

        # Handle role specially
        if "role" in fields:
            if isinstance(fields["role"], UserRole):
                fields["role"] = fields["role"].value

        # Update fields
        for field, value in fields.items():
            if hasattr(user, field):
                setattr(user, field, value)

        await self.session.commit()
        await self.session.refresh(user)

        # Audit log
        self._audit.log(
            AuditAction.USER_UPDATED,
            resource_type="user",
            resource_id=str(user_id),
            user_id=updated_by,
            details={"fields_updated": list(fields.keys())},
        )

        return user

    async def change_role(
        self,
        user_id: UUID,
        new_role: UserRole,
        changed_by: UUID,
    ) -> Optional[User]:
        """Change a user's role.

        Args:
            user_id: User to change
            new_role: New role
            changed_by: Admin performing change

        Returns:
            Updated User or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        old_role = user.role
        user.role = new_role.value

        await self.session.commit()
        await self.session.refresh(user)

        # Audit log
        self._audit.log(
            AuditAction.ROLE_CHANGED,
            resource_type="user",
            resource_id=str(user_id),
            user_id=changed_by,
            details={"old_role": old_role, "new_role": new_role.value},
        )

        logger.info(f"Role changed for {user.email}: {old_role} -> {new_role.value}")
        return user

    async def deactivate_user(
        self,
        user_id: UUID,
        deactivated_by: UUID,
        reason: str = "",
    ) -> Optional[User]:
        """Deactivate a user account.

        Args:
            user_id: User to deactivate
            deactivated_by: Admin performing deactivation
            reason: Reason for deactivation

        Returns:
            Updated User or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        user.is_active = False

        await self.session.commit()
        await self.session.refresh(user)

        # Revoke all tokens
        from sre_agent.core.redis_service import get_redis_service

        redis_service = get_redis_service()
        await redis_service.revoke_all_user_tokens(user_id, reason="account_deactivated")

        # Audit log
        self._audit.log(
            AuditAction.USER_DELETED,  # Using deleted for deactivation
            resource_type="user",
            resource_id=str(user_id),
            user_id=deactivated_by,
            details={"reason": reason, "action": "deactivated"},
        )

        logger.warning(f"User deactivated: {user.email} by {deactivated_by}")
        return user

    async def reactivate_user(
        self,
        user_id: UUID,
        reactivated_by: UUID,
    ) -> Optional[User]:
        """Reactivate a deactivated user account.

        Args:
            user_id: User to reactivate
            reactivated_by: Admin performing reactivation

        Returns:
            Updated User or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        user.is_active = True

        await self.session.commit()
        await self.session.refresh(user)

        # Audit log
        self._audit.log(
            AuditAction.USER_UPDATED,
            resource_type="user",
            resource_id=str(user_id),
            user_id=reactivated_by,
            details={"action": "reactivated"},
        )

        logger.info(f"User reactivated: {user.email}")
        return user

    async def delete_user(
        self,
        user_id: UUID,
        deleted_by: UUID,
        hard_delete: bool = False,
    ) -> bool:
        """Delete a user.

        Args:
            user_id: User to delete
            deleted_by: Super admin performing deletion
            hard_delete: If True, permanently delete. Otherwise soft delete.

        Returns:
            True if deleted
        """
        user = await self.get_by_id(user_id)
        if not user:
            return False

        email = user.email

        if hard_delete:
            await self.session.delete(user)
        else:
            user.is_active = False
            user.email = f"deleted_{user_id}@deleted.local"

        await self.session.commit()

        # Revoke all tokens
        from sre_agent.core.redis_service import get_redis_service

        redis_service = get_redis_service()
        await redis_service.revoke_all_user_tokens(user_id, reason="account_deleted")

        # Audit log
        self._audit.log(
            AuditAction.USER_DELETED,
            resource_type="user",
            resource_id=str(user_id),
            user_id=deleted_by,
            details={
                "email": email,
                "hard_delete": hard_delete,
            },
        )

        logger.warning(f"User deleted: {email} (hard={hard_delete})")
        return True

    # =========================================
    # QUERY OPERATIONS
    # =========================================

    async def list_users(
        self,
        role: Optional[UserRole] = None,
        active_only: bool = True,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[User], int]:
        """List users with filtering and pagination.

        Args:
            role: Filter by role
            active_only: Only active users
            search: Search in email/name
            limit: Maximum results
            offset: Pagination offset

        Returns:
            Tuple of (users, total_count)
        """
        query = select(User)
        count_query = select(func.count(User.id))

        conditions = []

        if role:
            conditions.append(User.role == role.value)

        if active_only:
            conditions.append(User.is_active.is_(True))

        if search:
            search_pattern = f"%{search}%"
            conditions.append(
                or_(
                    User.email.ilike(search_pattern),
                    User.name.ilike(search_pattern),
                )
            )

        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))

        # Get total count
        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        # Get users
        query = query.order_by(User.created_at.desc())
        query = query.limit(limit).offset(offset)

        result = await self.session.execute(query)
        users = result.scalars().all()

        return list(users), total

    async def get_user_stats(self) -> dict[str, Any]:
        """Get user statistics.

        Returns:
            Dictionary with user counts by role, etc.
        """
        # Total users
        total_result = await self.session.execute(select(func.count(User.id)))
        total = total_result.scalar_one()

        # Active users
        active_result = await self.session.execute(
            select(func.count(User.id)).where(User.is_active.is_(True))
        )
        active = active_result.scalar_one()

        # By role
        role_counts = {}
        for role in UserRole:
            result = await self.session.execute(
                select(func.count(User.id)).where(User.role == role.value)
            )
            role_counts[role.value] = result.scalar_one()

        # Recent logins (last 24h)
        yesterday = datetime.now(UTC).replace(hour=0, minute=0, second=0)
        recent_result = await self.session.execute(
            select(func.count(User.id)).where(User.last_login_at >= yesterday)
        )
        recent_logins = recent_result.scalar_one()

        return {
            "total_users": total,
            "active_users": active,
            "inactive_users": total - active,
            "by_role": role_counts,
            "recent_logins_24h": recent_logins,
        }

    # =========================================
    # OAUTH LINKING
    # =========================================

    async def link_oauth(
        self,
        user_id: UUID,
        provider: str,
        provider_id: str,
    ) -> Optional[User]:
        """Link an OAuth account to a user.

        Args:
            user_id: User to link
            provider: OAuth provider
            provider_id: Provider's user ID

        Returns:
            Updated User or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        if provider == "github":
            user.github_id = provider_id
        elif provider == "google":
            user.google_id = provider_id
        else:
            raise ValueError(f"Unknown provider: {provider}")

        await self.session.commit()
        await self.session.refresh(user)

        self._audit.log(
            AuditAction.USER_UPDATED,
            resource_type="user",
            resource_id=str(user_id),
            user_id=user_id,
            details={"action": "oauth_linked", "provider": provider},
        )

        return user

    async def unlink_oauth(
        self,
        user_id: UUID,
        provider: str,
    ) -> Optional[User]:
        """Unlink an OAuth account from a user.

        Args:
            user_id: User to unlink
            provider: OAuth provider

        Returns:
            Updated User or None
        """
        user = await self.get_by_id(user_id)
        if not user:
            return None

        if provider == "github":
            user.github_id = None
        elif provider == "google":
            user.google_id = None
        else:
            raise ValueError(f"Unknown provider: {provider}")

        await self.session.commit()
        await self.session.refresh(user)

        self._audit.log(
            AuditAction.USER_UPDATED,
            resource_type="user",
            resource_id=str(user_id),
            user_id=user_id,
            details={"action": "oauth_unlinked", "provider": provider},
        )

        return user

    # =========================================
    # AUTHENTICATION
    # =========================================

    async def authenticate(
        self,
        email: str,
        password: str,
        ip_address: Optional[str] = None,
    ) -> Optional[User]:
        """Authenticate a user with email/password.

        Args:
            email: User's email
            password: Plain text password
            ip_address: Client IP for logging

        Returns:
            User if authenticated, None otherwise
        """
        user = await self.get_by_email(email)

        if not user:
            self._audit.log(
                AuditAction.USER_LOGIN,
                details={"email": email, "success": False, "reason": "user_not_found"},
                ip_address=ip_address,
                success=False,
            )
            return None

        if not user.is_active:
            self._audit.log(
                AuditAction.USER_LOGIN,
                resource_type="user",
                resource_id=str(user.id),
                user_email=email,
                details={"success": False, "reason": "account_inactive"},
                ip_address=ip_address,
                success=False,
            )
            return None

        if not user.password_hash:
            self._audit.log(
                AuditAction.USER_LOGIN,
                resource_type="user",
                resource_id=str(user.id),
                user_email=email,
                details={"success": False, "reason": "no_password_set"},
                ip_address=ip_address,
                success=False,
            )
            return None

        if not self.verify_password(password, user.password_hash):
            self._audit.log(
                AuditAction.USER_LOGIN,
                resource_type="user",
                resource_id=str(user.id),
                user_email=email,
                details={"success": False, "reason": "invalid_password"},
                ip_address=ip_address,
                success=False,
            )
            return None

        # Update last login
        user.last_login_at = datetime.now(UTC)
        await self.session.commit()

        self._audit.log(
            AuditAction.USER_LOGIN,
            resource_type="user",
            resource_id=str(user.id),
            user_id=user.id,
            user_email=email,
            ip_address=ip_address,
            details={"success": True},
        )

        return user

    async def update_last_login(self, user_id: UUID) -> None:
        """Update user's last login timestamp.

        Args:
            user_id: User who logged in
        """
        await self.session.execute(
            update(User).where(User.id == user_id).values(last_login_at=datetime.now(UTC))
        )
        await self.session.commit()
