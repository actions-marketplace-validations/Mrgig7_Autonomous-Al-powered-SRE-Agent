"""Test configuration and fixtures."""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sre_agent.config import get_settings
from sre_agent.main import create_app

# Use in-memory SQLite for tests (requires aiosqlite)
# For full PostgreSQL tests, use testcontainers
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Create test client for API tests."""
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_github_workflow_job_payload() -> dict[str, Any]:
    """Sample GitHub workflow_job webhook payload for a failed job."""
    return {
        "action": "completed",
        "workflow_job": {
            "id": 123456789,
            "run_id": 987654321,
            "run_attempt": 1,
            "workflow_name": "CI",
            "head_branch": "main",
            "head_sha": "a" * 40,  # 40 character SHA
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-01-09T00:00:00Z",
            "started_at": "2026-01-09T00:01:00Z",
            "completed_at": "2026-01-09T00:05:00Z",
            "name": "Run unit tests",
            "steps": [
                {
                    "name": "Checkout",
                    "status": "completed",
                    "conclusion": "success",
                    "number": 1,
                    "started_at": "2026-01-09T00:01:00Z",
                    "completed_at": "2026-01-09T00:01:30Z",
                },
                {
                    "name": "Run tests",
                    "status": "completed",
                    "conclusion": "failure",
                    "number": 2,
                    "started_at": "2026-01-09T00:01:30Z",
                    "completed_at": "2026-01-09T00:05:00Z",
                },
            ],
            "runner_name": "GitHub Actions 1",
            "html_url": "https://github.com/test-org/test-repo/actions/runs/987654321/job/123456789",
        },
        "repository": {
            "id": 12345,
            "node_id": "R_12345",
            "name": "test-repo",
            "full_name": "test-org/test-repo",
            "private": False,
            "owner": {
                "login": "test-org",
                "id": 11111,
                "type": "Organization",
            },
            "html_url": "https://github.com/test-org/test-repo",
            "default_branch": "main",
        },
        "sender": {
            "login": "test-user",
            "id": 22222,
            "type": "User",
        },
    }


@pytest.fixture
def sample_github_workflow_job_success_payload(
    sample_github_workflow_job_payload: dict[str, Any],
) -> dict[str, Any]:
    """Sample GitHub workflow_job webhook payload for a successful job."""
    payload = sample_github_workflow_job_payload.copy()
    payload["workflow_job"] = payload["workflow_job"].copy()
    payload["workflow_job"]["conclusion"] = "success"
    payload["workflow_job"]["steps"] = [
        {
            "name": "Checkout",
            "status": "completed",
            "conclusion": "success",
            "number": 1,
            "started_at": "2026-01-09T00:01:00Z",
            "completed_at": "2026-01-09T00:01:30Z",
        },
        {
            "name": "Run tests",
            "status": "completed",
            "conclusion": "success",
            "number": 2,
            "started_at": "2026-01-09T00:01:30Z",
            "completed_at": "2026-01-09T00:05:00Z",
        },
    ]
    return payload


@pytest.fixture
def mock_celery_task() -> MagicMock:
    """Mock Celery task for testing async dispatch."""
    return MagicMock()
