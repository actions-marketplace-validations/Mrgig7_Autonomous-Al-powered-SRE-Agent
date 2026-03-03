"""OAuth providers for third-party authentication.

This module provides integration with OAuth providers:
- GitHub OAuth
- Google OAuth

Supports both authorization code flow and token validation.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


@dataclass
class OAuthUserInfo:
    """User information retrieved from OAuth provider."""

    provider: str
    provider_id: str
    email: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    access_token: Optional[str] = None
    granted_scopes: Optional[list[str]] = None
    raw_data: Optional[dict[str, Any]] = None


class OAuthError(Exception):
    """Raised when OAuth flow fails."""

    pass


class GitHubOAuthProvider:
    """GitHub OAuth 2.0 provider.

    Implements the authorization code flow for GitHub authentication.
    """

    AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USER_URL = "https://api.github.com/user"
    EMAIL_URL = "https://api.github.com/user/emails"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: str = "repo read:user workflow user:email",
    ):
        """Initialize GitHub OAuth provider.

        Args:
            client_id: GitHub OAuth app client ID
            client_secret: GitHub OAuth app client secret
            redirect_uri: Callback URL for OAuth flow
            scope: OAuth scopes to request
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    def get_authorization_url(self, state: str) -> str:
        """Get the URL to redirect users to for authorization.

        Args:
            state: Random state parameter for CSRF protection

        Returns:
            Authorization URL
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "state": state,
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> str:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from callback

        Returns:
            Access token

        Raises:
            OAuthError: If exchange fails
        """
        client = await self._get_client()

        response = await client.post(
            self.TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            headers={"Accept": "application/json"},
        )

        if response.status_code != 200:
            raise OAuthError(f"Token exchange failed: {response.text}")

        data = response.json()

        if "error" in data:
            raise OAuthError(f"OAuth error: {data.get('error_description', data['error'])}")

        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("No access token in response")

        return access_token

    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        """Get user information from GitHub.

        Args:
            access_token: GitHub access token

        Returns:
            OAuthUserInfo with user details

        Raises:
            OAuthError: If user info retrieval fails
        """
        client = await self._get_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        # Get user profile
        response = await client.get(self.USER_URL, headers=headers)
        if response.status_code != 200:
            raise OAuthError(f"Failed to get user info: {response.text}")

        user_data = response.json()
        granted_scopes = self._parse_scopes(response.headers.get("X-OAuth-Scopes"))

        # Get primary email if not in profile
        email = user_data.get("email")
        if not email:
            email_response = await client.get(self.EMAIL_URL, headers=headers)
            if email_response.status_code == 200:
                emails = email_response.json()
                primary_emails = [e for e in emails if e.get("primary")]
                if primary_emails:
                    email = primary_emails[0].get("email")
                elif emails:
                    email = emails[0].get("email")

        if not email:
            raise OAuthError("Could not retrieve user email")

        return OAuthUserInfo(
            provider="github",
            provider_id=str(user_data.get("id")),
            email=email,
            name=user_data.get("name") or user_data.get("login"),
            avatar_url=user_data.get("avatar_url"),
            access_token=access_token,
            granted_scopes=sorted(granted_scopes),
            raw_data=user_data,
        )

    @staticmethod
    def _parse_scopes(scope_header: str | None) -> set[str]:
        if not scope_header:
            return set()
        chunks = [part.strip() for part in scope_header.replace(" ", ",").split(",")]
        return {chunk for chunk in chunks if chunk}

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class GoogleOAuthProvider:
    """Google OAuth 2.0 provider.

    Implements the authorization code flow for Google authentication.
    """

    AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USER_INFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: str = "openid email profile",
    ):
        """Initialize Google OAuth provider.

        Args:
            client_id: Google OAuth client ID
            client_secret: Google OAuth client secret
            redirect_uri: Callback URL for OAuth flow
            scope: OAuth scopes to request
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scope = scope
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    def get_authorization_url(self, state: str) -> str:
        """Get the URL to redirect users to for authorization.

        Args:
            state: Random state parameter for CSRF protection

        Returns:
            Authorization URL
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "state": state,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self.AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> str:
        """Exchange authorization code for access token.

        Args:
            code: Authorization code from callback

        Returns:
            Access token

        Raises:
            OAuthError: If exchange fails
        """
        client = await self._get_client()

        response = await client.post(
            self.TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
        )

        if response.status_code != 200:
            raise OAuthError(f"Token exchange failed: {response.text}")

        data = response.json()

        if "error" in data:
            raise OAuthError(f"OAuth error: {data.get('error_description', data['error'])}")

        access_token = data.get("access_token")
        if not access_token:
            raise OAuthError("No access token in response")

        return access_token

    async def get_user_info(self, access_token: str) -> OAuthUserInfo:
        """Get user information from Google.

        Args:
            access_token: Google access token

        Returns:
            OAuthUserInfo with user details

        Raises:
            OAuthError: If user info retrieval fails
        """
        client = await self._get_client()

        response = await client.get(
            self.USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if response.status_code != 200:
            raise OAuthError(f"Failed to get user info: {response.text}")

        user_data = response.json()

        email = user_data.get("email")
        if not email:
            raise OAuthError("Could not retrieve user email")

        return OAuthUserInfo(
            provider="google",
            provider_id=str(user_data.get("id")),
            email=email,
            name=user_data.get("name"),
            avatar_url=user_data.get("picture"),
            access_token=access_token,
            raw_data=user_data,
        )

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class OAuthProviderFactory:
    """Factory for creating OAuth providers based on configuration."""

    @staticmethod
    def create_github(
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> GitHubOAuthProvider:
        return GitHubOAuthProvider(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )

    @staticmethod
    def create_google(
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> GoogleOAuthProvider:
        return GoogleOAuthProvider(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
