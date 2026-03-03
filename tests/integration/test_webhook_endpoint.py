"""Integration tests for webhook API endpoint."""

import json
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
from sre_agent.schemas.repository_config import RepositoryRuntimeConfig


class TestGitHubWebhookEndpoint:
    """Integration tests for GitHub webhook endpoint."""

    def test_health_check(self, client: TestClient) -> None:
        """Health endpoint should return 200."""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_missing_event_header_returns_400(self, client: TestClient) -> None:
        """Missing X-GitHub-Event header should return 400."""
        response = client.post(
            "/webhooks/github",
            content=b"{}",
            headers={
                "X-GitHub-Delivery": "test-delivery-id",
            },
        )

        assert response.status_code == 400
        assert "X-GitHub-Event" in response.json()["detail"]

    def test_missing_delivery_header_returns_400(self, client: TestClient) -> None:
        """Missing X-GitHub-Delivery header should return 400."""
        response = client.post(
            "/webhooks/github",
            content=b"{}",
            headers={
                "X-GitHub-Event": "workflow_job",
            },
        )

        assert response.status_code == 400
        assert "X-GitHub-Delivery" in response.json()["detail"]

    def test_unsupported_event_type_returns_ignored(self, client: TestClient) -> None:
        """Unsupported event type should return 200 with ignored status."""
        response = client.post(
            "/webhooks/github",
            content=b"{}",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "test-delivery-id",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_non_completed_job_returns_ignored(
        self,
        client: TestClient,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Non-completed job action should be ignored."""
        payload = sample_github_workflow_job_payload.copy()
        payload["action"] = "in_progress"

        response = client.post(
            "/webhooks/github",
            content=json.dumps(payload).encode(),
            headers={
                "X-GitHub-Event": "workflow_job",
                "X-GitHub-Delivery": "test-delivery-id",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    @patch(
        "sre_agent.api.webhooks.github.PostMergeMonitorService",
    )
    def test_successful_job_returns_ignored(
        self,
        mock_monitor_class: Any,
        client: TestClient,
        sample_github_workflow_job_success_payload: dict[str, Any],
    ) -> None:
        """Successful job should be ignored."""
        from unittest.mock import AsyncMock

        mock_monitor_class.return_value.process_outcome = AsyncMock(return_value=None)

        response = client.post(
            "/webhooks/github",
            content=json.dumps(sample_github_workflow_job_success_payload).encode(),
            headers={
                "X-GitHub-Event": "workflow_job",
                "X-GitHub-Delivery": "test-delivery-id",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    @patch("sre_agent.api.webhooks.github.EventStore")
    @patch("sre_agent.api.webhooks.github.process_pipeline_event")
    @patch("sre_agent.services.webhook_delivery_store.WebhookDeliveryStore.record_delivery")
    @patch(
        "sre_agent.services.repository_config.RepositoryConfigService.resolve_for_repository",
    )
    @patch(
        "sre_agent.services.github_app_installations.GitHubAppInstallationService.get_by_repo_full_name",
    )
    def test_failed_job_is_accepted(
        self,
        mock_get_installation: Any,
        mock_resolve_config: Any,
        mock_record_delivery: Any,
        mock_task: Any,
        mock_store_class: Any,
        client: TestClient,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Failed job should be accepted and processed."""
        from unittest.mock import AsyncMock, MagicMock
        from uuid import uuid4

        # Mock the event store
        mock_event = MagicMock()
        mock_event.id = uuid4()
        mock_store = AsyncMock()
        mock_store.store_event.return_value = (mock_event, True)
        mock_store.update_status = AsyncMock()
        mock_store_class.return_value = mock_store
        mock_record_delivery.return_value = True
        mock_get_installation.return_value = MagicMock(
            installation_id=999,
            repo_full_name="test-org/test-repo",
            user_id=uuid4(),
            automation_mode="suggest",
        )
        mock_resolve_config.return_value = RepositoryRuntimeConfig(
            automation_mode="suggest",
            protected_paths=[],
            retry_limit=3,
            source="installation_default",
        )

        # Mock Celery task
        mock_task.delay = MagicMock()

        response = client.post(
            "/webhooks/github",
            content=json.dumps(sample_github_workflow_job_payload).encode(),
            headers={
                "X-GitHub-Event": "workflow_job",
                "X-GitHub-Delivery": "test-delivery-id",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event_id"] is not None

    def test_invalid_json_returns_400(self, client: TestClient) -> None:
        """Invalid JSON payload should return 400."""
        response = client.post(
            "/webhooks/github",
            content=b"not valid json",
            headers={
                "X-GitHub-Event": "workflow_job",
                "X-GitHub-Delivery": "test-delivery-id",
            },
        )

        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["detail"]
