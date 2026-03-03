"""JWT token handling for authentication.

This module provides production-grade JWT token management with:
- Secure token generation and validation
- Refresh token support
- Token revocation (blocklist)
- Claims management
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

logger = logging.getLogger(__name__)

# Token configuration defaults
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS = 7
DEFAULT_ALGORITHM = "HS256"


@dataclass
class TokenPayload:
    """Payload data extracted from a JWT token."""

    user_id: UUID
    email: str
    role: str
    permissions: list[str]
    exp: datetime
    iat: datetime
    jti: str  # JWT ID for revocation
    token_type: str  # "access" or "refresh"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenPayload":
        """Create TokenPayload from decoded JWT claims."""
        return cls(
            user_id=UUID(data["sub"]),
            email=data.get("email", ""),
            role=data.get("role", "viewer"),
            permissions=data.get("permissions", []),
            exp=datetime.fromtimestamp(data["exp"]),
            iat=datetime.fromtimestamp(data["iat"]),
            jti=data.get("jti", ""),
            token_type=data.get("token_type", "access"),
        )


class JWTHandler:
    """Handler for JWT token operations.

    Provides secure token generation, validation, and management
    with support for both access and refresh tokens.
    """

    def __init__(
        self,
        secret_key: str,
        algorithm: str = DEFAULT_ALGORITHM,
        access_token_expire_minutes: int = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_token_expire_days: int = DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS,
    ):
        """Initialize JWT handler.

        Args:
            secret_key: Secret key for signing tokens
            algorithm: JWT algorithm (default HS256)
            access_token_expire_minutes: Access token lifetime
            refresh_token_expire_days: Refresh token lifetime
        """
        if not secret_key or len(secret_key) < 32:
            raise ValueError("Secret key must be at least 32 characters")

        self.secret_key = secret_key
        self.algorithm = algorithm
        self.access_token_expire_minutes = access_token_expire_minutes
        self.refresh_token_expire_days = refresh_token_expire_days

        # In-memory token blocklist (use Redis in production)
        self._blocklist: set[str] = set()

    def create_access_token(
        self,
        user_id: UUID,
        email: str,
        role: str,
        permissions: list[str],
        additional_claims: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create a new access token.

        Args:
            user_id: User's unique identifier
            email: User's email address
            role: User's role
            permissions: List of permission strings
            additional_claims: Optional extra claims

        Returns:
            Encoded JWT access token
        """
        now = datetime.now(UTC)
        expire = now + timedelta(minutes=self.access_token_expire_minutes)

        payload = {
            "sub": str(user_id),
            "email": email,
            "role": role,
            "permissions": permissions,
            "iat": now,
            "exp": expire,
            "jti": str(uuid4()),
            "token_type": "access",
        }

        if additional_claims:
            payload.update(additional_claims)

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(
        self,
        user_id: UUID,
        email: str,
    ) -> str:
        """Create a new refresh token.

        Args:
            user_id: User's unique identifier
            email: User's email address

        Returns:
            Encoded JWT refresh token
        """
        now = datetime.now(UTC)
        expire = now + timedelta(days=self.refresh_token_expire_days)

        payload = {
            "sub": str(user_id),
            "email": email,
            "iat": now,
            "exp": expire,
            "jti": str(uuid4()),
            "token_type": "refresh",
        }

        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def decode_token(self, token: str) -> Optional[TokenPayload]:
        """Decode and validate a JWT token.

        Args:
            token: Encoded JWT token

        Returns:
            TokenPayload if valid, None if invalid

        Raises:
            ExpiredSignatureError: If token is expired
            InvalidTokenError: If token is invalid
        """
        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
            )

            # Check blocklist
            jti = payload.get("jti", "")
            if jti in self._blocklist:
                logger.warning(f"Attempted use of revoked token: {jti[:8]}...")
                return None

            return TokenPayload.from_dict(payload)

        except ExpiredSignatureError:
            logger.debug("Token expired")
            raise
        except InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            raise

    def verify_token(self, token: str, token_type: str = "access") -> Optional[TokenPayload]:
        """Verify a token is valid and of the expected type.

        Args:
            token: Encoded JWT token
            token_type: Expected token type ("access" or "refresh")

        Returns:
            TokenPayload if valid, None otherwise
        """
        try:
            payload = self.decode_token(token)
            if payload and payload.token_type == token_type:
                return payload
            return None
        except (ExpiredSignatureError, InvalidTokenError):
            return None

    def revoke_token(self, token: str) -> bool:
        """Revoke a token by adding its JTI to the blocklist.

        Args:
            token: Token to revoke

        Returns:
            True if revoked successfully
        """
        try:
            # Decode without verification to get JTI
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            if jti:
                self._blocklist.add(jti)
                logger.info(f"Token revoked: {jti[:8]}...")
                return True
            return False
        except Exception:
            return False

    def refresh_access_token(
        self,
        refresh_token: str,
        role: str,
        permissions: list[str],
    ) -> Optional[str]:
        """Create a new access token using a refresh token.

        Args:
            refresh_token: Valid refresh token
            role: Current user role
            permissions: Current user permissions

        Returns:
            New access token or None if refresh token is invalid
        """
        payload = self.verify_token(refresh_token, token_type="refresh")
        if not payload:
            return None

        return self.create_access_token(
            user_id=payload.user_id,
            email=payload.email,
            role=role,
            permissions=permissions,
        )


# Global JWT handler instance (initialized via factory)
_jwt_handler: Optional[JWTHandler] = None


def get_jwt_handler() -> JWTHandler:
    """Get the global JWT handler instance."""
    global _jwt_handler
    if _jwt_handler is None:
        from sre_agent.config import get_settings

        settings = get_settings()
        _jwt_handler = JWTHandler(
            secret_key=settings.jwt_secret_key,
            access_token_expire_minutes=settings.jwt_access_token_expire_minutes,
            refresh_token_expire_days=settings.jwt_refresh_token_expire_days,
        )
    return _jwt_handler


def create_access_token(
    user_id: UUID,
    email: str,
    role: str,
    permissions: list[str],
) -> str:
    """Convenience function to create an access token."""
    return get_jwt_handler().create_access_token(
        user_id=user_id,
        email=email,
        role=role,
        permissions=permissions,
    )


def decode_access_token(token: str) -> Optional[TokenPayload]:
    """Convenience function to decode an access token."""
    return get_jwt_handler().verify_token(token, token_type="access")
