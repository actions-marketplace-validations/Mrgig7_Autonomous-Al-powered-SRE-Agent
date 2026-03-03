"""Event normalization service.

Transforms provider-specific webhook payloads into a canonical
NormalizedPipelineEvent format for downstream processing.
"""

import logging
import re
from datetime import UTC
from typing import Protocol

from sre_agent.schemas.github import GitHubWorkflowJobPayload, GitHubWorkflowRunPayload
from sre_agent.schemas.normalized import CIProvider, FailureType, NormalizedPipelineEvent

logger = logging.getLogger(__name__)


class EventNormalizer(Protocol):
    """Protocol for event normalizers."""

    def normalize(
        self,
        payload: dict,
        correlation_id: str | None = None,
        event_type: str = "workflow_job",
    ) -> NormalizedPipelineEvent:
        """Normalize a provider-specific payload to canonical format."""
        ...


class GitHubEventNormalizer:
    """
    Normalizer for GitHub Actions workflow_job events.

    Transforms GitHub webhook payloads into NormalizedPipelineEvent format.
    """

    # Patterns for failure type inference from job names
    TEST_PATTERNS = [
        r"\btest\b",
        r"\btests\b",
        r"\bunit\b",
        r"\bintegration\b",
        r"\be2e\b",
        r"\bspec\b",
        r"\bcheck\b",
    ]
    BUILD_PATTERNS = [
        r"\bbuild\b",
        r"\bcompile\b",
        r"\bpackage\b",
        r"\bbundle\b",
        r"\bassemble\b",
    ]
    DEPLOY_PATTERNS = [
        r"\bdeploy\b",
        r"\brelease\b",
        r"\bpublish\b",
        r"\bpush\b",
        r"\brollout\b",
    ]
    INFRA_PATTERNS = [
        r"\binfra\b",
        r"\bterraform\b",
        r"\bprovision\b",
        r"\bsetup\b",
        r"\bensure\b",
    ]

    def normalize(
        self,
        payload: dict,
        correlation_id: str | None = None,
        event_type: str = "workflow_job",
    ) -> NormalizedPipelineEvent:
        """
        Normalize GitHub workflow_job or workflow_run payload to canonical format.

        Args:
            payload: Raw GitHub webhook payload dict
            correlation_id: Optional correlation ID for tracing
            event_type: GitHub event type ("workflow_job" or "workflow_run")

        Returns:
            NormalizedPipelineEvent with all fields populated

        Raises:
            ValueError: If payload structure is invalid
        """
        if event_type == "workflow_run":
            return self._normalize_workflow_run(payload, correlation_id)

        # Parse and validate workflow_job payload
        try:
            parsed = GitHubWorkflowJobPayload.model_validate(payload)
        except Exception as e:
            logger.error(
                "Failed to parse GitHub webhook payload",
                extra={"error": str(e), "correlation_id": correlation_id},
            )
            raise ValueError(f"Invalid GitHub webhook payload: {e}") from e

        job = parsed.workflow_job
        repo = parsed.repository

        # Generate idempotency key
        idempotency_key = self._generate_idempotency_key(
            repo=repo.full_name,
            run_id=job.run_id,
            job_id=job.id,
            attempt=job.run_attempt,
        )

        # Infer failure type from job name and conclusion
        failure_type = self._infer_failure_type(
            job_name=job.name,
            conclusion=job.conclusion,
        )

        # Extract error message from failed steps
        error_message = self._extract_error_message(job.steps)

        # Determine event timestamp
        event_timestamp = job.completed_at or job.started_at or job.created_at
        if event_timestamp.tzinfo is None:
            event_timestamp = event_timestamp.replace(tzinfo=UTC)

        normalized = NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=CIProvider.GITHUB_ACTIONS,
            pipeline_id=str(job.run_id),
            repo=repo.full_name,
            commit_sha=job.head_sha,
            branch=job.head_branch,
            stage=job.name,
            failure_type=failure_type,
            error_message=error_message,
            event_timestamp=event_timestamp,
            raw_payload=payload,
            correlation_id=correlation_id,
        )

        logger.info(
            "Normalized GitHub event",
            extra={
                "idempotency_key": idempotency_key,
                "repo": repo.full_name,
                "failure_type": failure_type.value,
                "correlation_id": correlation_id,
            },
        )

        return normalized

    def _generate_idempotency_key(
        self,
        repo: str,
        run_id: int,
        job_id: int,
        attempt: int,
    ) -> str:
        """Generate a unique idempotency key for deduplication."""
        return f"github_actions:{repo}:{run_id}:{job_id}:{attempt}"

    def _infer_failure_type(
        self,
        job_name: str,
        conclusion: str | None,
    ) -> FailureType:
        """
        Infer the type of failure from job name and conclusion.

        Uses rule-based heuristics on job name patterns.
        ML-based classification is deferred to Failure Intelligence Layer.
        """
        # Check for timeout first
        if conclusion == "timed_out":
            return FailureType.TIMEOUT

        # Normalize job name for pattern matching
        job_name_lower = job_name.lower()

        # Check patterns in order of specificity
        for pattern in self.TEST_PATTERNS:
            if re.search(pattern, job_name_lower):
                return FailureType.TEST

        for pattern in self.DEPLOY_PATTERNS:
            if re.search(pattern, job_name_lower):
                return FailureType.DEPLOY

        for pattern in self.BUILD_PATTERNS:
            if re.search(pattern, job_name_lower):
                return FailureType.BUILD

        for pattern in self.INFRA_PATTERNS:
            if re.search(pattern, job_name_lower):
                return FailureType.INFRASTRUCTURE

        # Default to BUILD for unknown job types
        return FailureType.BUILD

    def _extract_error_message(self, steps: list) -> str | None:
        """Extract error message from failed steps."""
        failed_steps = [s for s in steps if s.conclusion == "failure"]
        if not failed_steps:
            return None

        # Return names of failed steps as error summary
        failed_names = [s.name for s in failed_steps]
        return f"Failed steps: {', '.join(failed_names)}"

    def _normalize_workflow_run(
        self,
        payload: dict,
        correlation_id: str | None = None,
    ) -> NormalizedPipelineEvent:
        """
        Normalize GitHub workflow_run payload to canonical format.

        Handles run-level failure events which indicate that the overall
        workflow (not just a single job) has failed.
        """
        try:
            parsed = GitHubWorkflowRunPayload.model_validate(payload)
        except Exception as e:
            logger.error(
                "Failed to parse GitHub workflow_run payload",
                extra={"error": str(e), "correlation_id": correlation_id},
            )
            raise ValueError(f"Invalid GitHub workflow_run payload: {e}") from e

        run = parsed.workflow_run
        repo = parsed.repository

        # Generate idempotency key for workflow_run
        idempotency_key = f"github_actions_run:{repo.full_name}:{run.id}:{run.run_attempt}"

        # Infer failure type from workflow name
        workflow_name = run.name or "unknown"
        failure_type = self._infer_failure_type(
            job_name=workflow_name,
            conclusion=run.conclusion,
        )

        # Determine event timestamp
        event_timestamp = run.updated_at or run.created_at
        if event_timestamp.tzinfo is None:
            event_timestamp = event_timestamp.replace(tzinfo=UTC)

        normalized = NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=CIProvider.GITHUB_ACTIONS,
            pipeline_id=str(run.id),
            repo=repo.full_name,
            commit_sha=run.head_sha,
            branch=run.head_branch,
            stage=workflow_name,
            failure_type=failure_type,
            error_message=f"Workflow '{workflow_name}' failed (run #{run.run_number})",
            event_timestamp=event_timestamp,
            raw_payload=payload,
            correlation_id=correlation_id,
        )

        logger.info(
            "Normalized GitHub workflow_run event",
            extra={
                "idempotency_key": idempotency_key,
                "repo": repo.full_name,
                "failure_type": failure_type.value,
                "workflow_name": workflow_name,
                "correlation_id": correlation_id,
            },
        )

        return normalized


def get_normalizer(ci_provider: str) -> EventNormalizer:
    """Factory function to get the appropriate normalizer for a CI provider."""
    normalizers: dict[str, EventNormalizer] = {
        "github": GitHubEventNormalizer(),
        "github_actions": GitHubEventNormalizer(),
    }

    normalizer = normalizers.get(ci_provider.lower())
    if not normalizer:
        raise ValueError(f"Unsupported CI provider: {ci_provider}")

    return normalizer
