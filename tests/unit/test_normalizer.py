"""Unit tests for event normalization."""

from typing import Any

import pytest
from sre_agent.schemas.normalized import CIProvider, FailureType
from sre_agent.services.event_normalizer import GitHubEventNormalizer


class TestGitHubEventNormalizer:
    """Tests for GitHub event normalization."""

    @pytest.fixture
    def normalizer(self) -> GitHubEventNormalizer:
        """Create normalizer instance."""
        return GitHubEventNormalizer()

    def test_normalize_workflow_job_failure(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Normalize a failed workflow job payload."""
        result = normalizer.normalize(
            payload=sample_github_workflow_job_payload,
            correlation_id="test-correlation-id",
        )

        assert result.ci_provider == CIProvider.GITHUB_ACTIONS
        assert result.repo == "test-org/test-repo"
        assert result.commit_sha == "a" * 40
        assert result.branch == "main"
        assert result.stage == "Run unit tests"
        assert result.correlation_id == "test-correlation-id"
        assert result.raw_payload == sample_github_workflow_job_payload

    def test_idempotency_key_format(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Verify idempotency key format."""
        result = normalizer.normalize(payload=sample_github_workflow_job_payload)

        # Expected format: github_actions:{repo}:{run_id}:{job_id}:{attempt}
        expected_key = "github_actions:test-org/test-repo:987654321:123456789:1"
        assert result.idempotency_key == expected_key

    def test_failure_type_inference_test(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Job with 'test' in name should be classified as test failure."""
        # Payload already has "Run unit tests" as job name
        result = normalizer.normalize(payload=sample_github_workflow_job_payload)

        assert result.failure_type == FailureType.TEST

    def test_failure_type_inference_build(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Job with 'build' in name should be classified as build failure."""
        payload = sample_github_workflow_job_payload.copy()
        payload["workflow_job"] = payload["workflow_job"].copy()
        payload["workflow_job"]["name"] = "Build application"

        result = normalizer.normalize(payload=payload)

        assert result.failure_type == FailureType.BUILD

    def test_failure_type_inference_deploy(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Job with 'deploy' in name should be classified as deploy failure."""
        payload = sample_github_workflow_job_payload.copy()
        payload["workflow_job"] = payload["workflow_job"].copy()
        payload["workflow_job"]["name"] = "Deploy to staging"

        result = normalizer.normalize(payload=payload)

        assert result.failure_type == FailureType.DEPLOY

    def test_failure_type_inference_timeout(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Job with timed_out conclusion should be classified as timeout."""
        payload = sample_github_workflow_job_payload.copy()
        payload["workflow_job"] = payload["workflow_job"].copy()
        payload["workflow_job"]["conclusion"] = "timed_out"
        payload["workflow_job"]["name"] = "Run integration tests"

        result = normalizer.normalize(payload=payload)

        assert result.failure_type == FailureType.TIMEOUT

    def test_error_message_extraction(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Failed steps should be extracted as error message."""
        result = normalizer.normalize(payload=sample_github_workflow_job_payload)

        assert result.error_message is not None
        assert "Run tests" in result.error_message

    def test_invalid_payload_raises_error(
        self,
        normalizer: GitHubEventNormalizer,
    ) -> None:
        """Invalid payload should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid GitHub webhook payload"):
            normalizer.normalize(payload={"invalid": "payload"})

    def test_idempotency_key_is_deterministic(
        self,
        normalizer: GitHubEventNormalizer,
        sample_github_workflow_job_payload: dict[str, Any],
    ) -> None:
        """Same payload should produce same idempotency key."""
        result1 = normalizer.normalize(payload=sample_github_workflow_job_payload)
        result2 = normalizer.normalize(payload=sample_github_workflow_job_payload)

        assert result1.idempotency_key == result2.idempotency_key
