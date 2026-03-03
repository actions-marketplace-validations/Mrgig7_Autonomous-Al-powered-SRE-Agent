from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import get_current_user
from sre_agent.config import get_settings
from sre_agent.main import create_app


def _authed_client() -> TestClient:
    app = create_app()

    async def _override_user() -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            user_id=uuid4(),
            email="operator@example.com",
            role="operator",
            permissions=[
                "view_dashboard",
                "view_failures",
                "view_repos",
                "api_read",
                "api_write",
            ],
            exp=now + timedelta(hours=1),
            iat=now,
            jti="phase1-jti",
            token_type="access",
        )

    app.dependency_overrides[get_current_user] = _override_user
    return TestClient(app)


def _mock_github_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from sre_agent.services.github_client import GitHubClient

    async def _fake_aenter(self):
        return self

    async def _fake_aexit(self, *args):
        return None

    monkeypatch.setattr(GitHubClient, "__aenter__", _fake_aenter)
    monkeypatch.setattr(GitHubClient, "__aexit__", _fake_aexit)


def test_github_login_start_returns_authorization_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:3000/oauth/github/callback")
    get_settings.cache_clear()

    client = _authed_client()

    response = client.post("/api/v1/auth/github/login", json={"action": "start"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["error"] is None
    data = payload["data"]
    assert "authorization_url" in data
    assert data["authorization_url"].startswith("https://github.com/login/oauth/authorize")
    assert "state" in data


def test_github_login_exchange_rejects_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GITHUB_OAUTH_REDIRECT_URI", "http://localhost:3000/oauth/github/callback")
    get_settings.cache_clear()

    client = _authed_client()

    response = client.post(
        "/api/v1/auth/github/login",
        json={"action": "exchange", "code": "abc", "state": "missing-state"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid OAuth state"


def test_user_repos_requires_auth() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/user/repos")
    assert response.status_code == 401


def test_user_repos_returns_normalized_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from sre_agent.services.github_client import GitHubClient
    from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore

    async def _fake_get_token(self, *, jti: str) -> str | None:
        assert jti == "phase1-jti"
        return "github-token"

    async def _fake_get_user_repositories(self, *, per_page: int = 100, sort: str = "updated"):
        assert per_page == 100
        assert sort == "updated"
        return [
            {
                "id": 1,
                "name": "repo-one",
                "full_name": "acme/repo-one",
                "private": False,
                "default_branch": "main",
                "html_url": "https://github.com/acme/repo-one",
                "permissions": {"admin": True, "push": True, "pull": True},
            }
        ]

    _mock_github_context(monkeypatch)
    monkeypatch.setattr(GitHubOAuthTokenStore, "get_token", _fake_get_token)
    monkeypatch.setattr(GitHubClient, "get_user_repositories", _fake_get_user_repositories)

    client = _authed_client()
    response = client.get("/api/v1/user/repos")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"][0]["full_name"] == "acme/repo-one"
    assert payload["data"][0]["permissions"]["admin"] is True


def test_user_repos_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from sre_agent.services.github_client import GitHubClient
    from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore

    async def _fake_get_token(self, *, jti: str) -> str | None:
        return "github-token"

    async def _fake_get_user_repositories(self, *, per_page: int = 100, sort: str = "updated"):
        return []

    _mock_github_context(monkeypatch)
    monkeypatch.setattr(GitHubOAuthTokenStore, "get_token", _fake_get_token)
    monkeypatch.setattr(GitHubClient, "get_user_repositories", _fake_get_user_repositories)

    client = _authed_client()
    response = client.get("/api/v1/user/repos")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"] == []


def test_user_repos_returns_expired_session_when_github_token_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore

    async def _fake_get_token(self, *, jti: str) -> str | None:
        return None

    monkeypatch.setattr(GitHubOAuthTokenStore, "get_token", _fake_get_token)

    client = _authed_client()
    response = client.get("/api/v1/user/repos")
    assert response.status_code == 401
    assert response.json()["detail"] == "GitHub session expired. Please sign in with GitHub again."


def test_integration_install_returns_configured_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from sre_agent.services.github_client import GitHubClient
    from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore

    monkeypatch.setenv(
        "GITHUB_APP_INSTALL_URL",
        "https://github.com/apps/sre-agent/installations/new",
    )
    get_settings.cache_clear()

    async def _fake_get_token(self, *, jti: str) -> str | None:
        return "github-token"

    async def _fake_get_repository(self, repo: str):
        return {
            "id": 99,
            "full_name": repo,
            "permissions": {"admin": True, "maintain": False},
        }

    _mock_github_context(monkeypatch)
    monkeypatch.setattr(GitHubOAuthTokenStore, "get_token", _fake_get_token)
    monkeypatch.setattr(GitHubClient, "get_repository", _fake_get_repository)

    client = _authed_client()

    response = client.post(
        "/api/v1/integration/install",
        json={"repository": "acme/repo-one", "automation_mode": "suggest"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["error"] is None
    data = payload["data"]
    assert data["configured"] is True
    assert data["repository"] == "acme/repo-one"
    assert data["install_state"]
    assert f"state={data['install_state']}" in data["install_url"]


def test_integration_install_rejects_missing_repo_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sre_agent.services.github_client import GitHubClient
    from sre_agent.services.github_oauth_tokens import GitHubOAuthTokenStore

    monkeypatch.setenv(
        "GITHUB_APP_INSTALL_URL",
        "https://github.com/apps/sre-agent/installations/new",
    )
    get_settings.cache_clear()

    async def _fake_get_token(self, *, jti: str) -> str | None:
        return "github-token"

    async def _fake_get_repository(self, repo: str):
        return {
            "id": 42,
            "full_name": repo,
            "permissions": {"admin": False, "maintain": False, "push": True},
        }

    _mock_github_context(monkeypatch)
    monkeypatch.setattr(GitHubOAuthTokenStore, "get_token", _fake_get_token)
    monkeypatch.setattr(GitHubClient, "get_repository", _fake_get_repository)

    client = _authed_client()
    response = client.post(
        "/api/v1/integration/install",
        json={"repository": "acme/repo-one"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing repository permissions for installation"


def test_integration_install_fails_when_install_url_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_APP_INSTALL_URL", raising=False)
    get_settings.cache_clear()

    client = _authed_client()
    response = client.post(
        "/api/v1/integration/install",
        json={"repository": "acme/repo-one"},
    )
    assert response.status_code == 501
    assert response.json()["detail"] == "GitHub App install URL is not configured"
