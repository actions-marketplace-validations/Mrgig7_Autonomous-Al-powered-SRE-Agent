"""User and audit log database models.

This module defines SQLAlchemy models for:
- User accounts and authentication
- Approval requests for fix workflows
- Audit logging for compliance
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sre_agent.models.events import Base


class ApprovalStatus(str, Enum):
    """Status of a fix approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    AUTO_APPROVED = "auto_approved"


class AuditAction(str, Enum):
    """Types of auditable actions."""

    # Authentication
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    TOKEN_REFRESH = "token_refresh"

    # User management
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DELETED = "user_deleted"
    ROLE_CHANGED = "role_changed"

    # Fix workflow
    FIX_GENERATED = "fix_generated"
    FIX_APPROVED = "fix_approved"
    FIX_REJECTED = "fix_rejected"
    FIX_AUTO_APPROVED = "fix_auto_approved"

    # PR operations
    PR_CREATED = "pr_created"
    PR_MERGED = "pr_merged"

    # Repository
    REPO_ADDED = "repo_added"
    REPO_REMOVED = "repo_removed"
    REPO_CONFIGURED = "repo_configured"

    # Settings
    SETTINGS_UPDATED = "settings_updated"
    NOTIFICATION_SENT = "notification_sent"

    # System
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"
    ERROR_OCCURRED = "error_occurred"


class User(Base):
    """User account model.

    Represents a user who can authenticate and interact with the platform.
    """

    __tablename__ = "users"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Authentication
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,  # Null for OAuth-only users
    )

    # Profile
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # Role and permissions
    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="viewer",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # OAuth connections
    github_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    google_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    approval_requests: Mapped[list["ApprovalRequest"]] = relationship(
        "ApprovalRequest",
        back_populates="requester",
        foreign_keys="ApprovalRequest.requested_by",
    )

    __table_args__ = (
        Index("ix_users_role", "role"),
        Index("ix_users_active_email", "is_active", "email"),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email}, role={self.role})>"


class GitHubAppInstallation(Base):
    """Persisted GitHub App installation metadata for onboarding and webhook mapping."""

    __tablename__ = "github_app_installations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    automation_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="suggest",
    )
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User")

    __table_args__ = (
        Index(
            "ux_github_app_installations_user_repo",
            "user_id",
            "repo_id",
            unique=True,
        ),
        Index(
            "ux_github_app_installations_installation_id",
            "installation_id",
            unique=True,
        ),
        Index("ix_github_app_installations_repo_full_name", "repo_full_name"),
    )

    def __repr__(self) -> str:
        return (
            f"<GitHubAppInstallation(user_id={self.user_id}, repo={self.repo_full_name}, "
            f"installation_id={self.installation_id})>"
        )


class ApprovalRequest(Base):
    """Approval request for fix deployment.

    Tracks the approval workflow for automated fixes before
    they are deployed via PR.
    """

    __tablename__ = "approval_requests"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # References
    failure_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    fix_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Request details
    requested_by: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,  # System-generated requests
    )
    approved_by: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(50),
        default=ApprovalStatus.PENDING.value,
        nullable=False,
    )

    # Fix details (denormalized for audit)
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence_score: Mapped[float] = mapped_column(nullable=False)
    files_changed: Mapped[list[str]] = mapped_column(JSONB, default=list)

    # Resolution
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    requester: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="approval_requests",
        foreign_keys=[requested_by],
    )
    approver: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[approved_by],
    )

    __table_args__ = (
        Index("ix_approval_requests_status_created", "status", "created_at"),
        Index("ix_approval_requests_repository", "repository"),
    )

    def __repr__(self) -> str:
        return f"<ApprovalRequest(id={self.id}, status={self.status})>"


class AuditLog(Base):
    """Audit log for compliance and tracking.

    Records all significant actions in the system for
    compliance, debugging, and security purposes.
    """

    __tablename__ = "audit_logs"

    # Primary key
    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    # Action details
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Actor
    user_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,  # System actions have no user
    )
    user_email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )

    # Request context
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Details
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Outcome
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        Index("ix_audit_logs_user_action", "user_id", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_created_action", "created_at", "action"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action})>"
