"""GitLab CI provider integration.

Handles GitLab CI/CD webhooks and API interactions for:
- Pipeline events
- Job events
- Build logs retrieval
"""

import logging
from datetime import UTC, datetime
from typing import Any, Optional

from sre_agent.providers.base_provider import (
    BaseCIProvider,
    FetchedLogs,
    ProviderRegistry,
    ProviderType,
    WebhookVerificationResult,
)
from sre_agent.schemas.normalized import CIProvider, FailureType, NormalizedPipelineEvent

logger = logging.getLogger(__name__)


@ProviderRegistry.register(ProviderType.GITLAB)
class GitLabProvider(BaseCIProvider):
    """GitLab CI provider implementation.

    Handles:
    - Pipeline webhook events
    - Job webhook events
    - Log retrieval via GitLab API
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GITLAB

    @property
    def ci_provider_enum(self) -> CIProvider:
        return CIProvider.GITLAB_CI

    def _get_auth_headers(self) -> dict[str, str]:
        """Get GitLab API authentication headers."""
        headers = {"Content-Type": "application/json"}
        if self.config.api_token:
            headers["PRIVATE-TOKEN"] = self.config.api_token
        return headers

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookVerificationResult:
        """Verify GitLab webhook using X-Gitlab-Token header.

        GitLab uses a simple token-based verification (not HMAC).
        """
        # GitLab uses X-Gitlab-Token for webhook verification
        token = headers.get("X-Gitlab-Token") or headers.get("x-gitlab-token")
        event_type = headers.get("X-Gitlab-Event") or headers.get("x-gitlab-event", "")

        # If no secret configured, accept all (development mode)
        if not self.config.webhook_secret:
            logger.warning("GitLab webhook secret not configured - accepting all webhooks")
            return WebhookVerificationResult(
                valid=True,
                provider=ProviderType.GITLAB,
                event_type=event_type,
            )

        if not token:
            return WebhookVerificationResult(
                valid=False,
                provider=ProviderType.GITLAB,
                event_type=event_type,
                error="Missing X-Gitlab-Token header",
            )

        if token != self.config.webhook_secret:
            return WebhookVerificationResult(
                valid=False,
                provider=ProviderType.GITLAB,
                event_type=event_type,
                error="Invalid webhook token",
            )

        return WebhookVerificationResult(
            valid=True,
            provider=ProviderType.GITLAB,
            event_type=event_type,
        )

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse GitLab webhook payload.

        Extracts common fields from pipeline/job events.
        """
        object_kind = payload.get("object_kind", "")

        if object_kind == "pipeline":
            return self._parse_pipeline_event(payload)
        elif object_kind == "build":
            return self._parse_build_event(payload)
        else:
            raise ValueError(f"Unsupported GitLab event type: {object_kind}")

    def _parse_pipeline_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse pipeline webhook event."""
        pipeline = payload.get("object_attributes", {})
        project = payload.get("project", {})

        return {
            "event_type": "pipeline",
            "pipeline_id": str(pipeline.get("id")),
            "status": pipeline.get("status"),
            "ref": pipeline.get("ref"),
            "sha": pipeline.get("sha"),
            "project_id": project.get("id"),
            "project_name": project.get("path_with_namespace"),
            "web_url": project.get("web_url"),
            "created_at": pipeline.get("created_at"),
            "finished_at": pipeline.get("finished_at"),
        }

    def _parse_build_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse build (job) webhook event."""
        build = payload.get("object_attributes", {}) or payload
        project = payload.get("project", {})
        repo = payload.get("repository", {})

        return {
            "event_type": "build",
            "build_id": str(build.get("build_id") or build.get("id")),
            "pipeline_id": str(build.get("pipeline_id")),
            "job_name": build.get("build_name") or build.get("name"),
            "stage": build.get("build_stage") or build.get("stage"),
            "status": build.get("build_status") or build.get("status"),
            "ref": build.get("ref"),
            "sha": build.get("sha"),
            "project_id": project.get("id") or build.get("project_id"),
            "project_name": project.get("path_with_namespace") or repo.get("name"),
            "web_url": project.get("web_url") or repo.get("homepage"),
            "created_at": build.get("build_created_at") or build.get("created_at"),
            "finished_at": build.get("build_finished_at") or build.get("finished_at"),
            "failure_reason": build.get("build_failure_reason"),
        }

    def should_process(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Check if event should be processed (is a failure)."""
        object_kind = payload.get("object_kind", "")

        if object_kind == "pipeline":
            status = payload.get("object_attributes", {}).get("status")
            if status != "failed":
                return False, f"Pipeline status '{status}' is not a failure"
            return True, ""

        elif object_kind == "build":
            attrs = payload.get("object_attributes", {}) or payload
            status = attrs.get("build_status") or attrs.get("status")
            if status != "failed":
                return False, f"Build status '{status}' is not a failure"
            return True, ""

        return False, f"Unsupported event type: {object_kind}"

    def normalize_event(
        self,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> NormalizedPipelineEvent:
        """Normalize GitLab event to canonical format."""
        parsed = self.parse_event(payload)

        # Determine if this is a pipeline or build event
        if parsed["event_type"] == "pipeline":
            return self._normalize_pipeline(parsed, payload, correlation_id)
        else:
            return self._normalize_build(parsed, payload, correlation_id)

    def _normalize_pipeline(
        self,
        parsed: dict[str, Any],
        raw_payload: dict[str, Any],
        correlation_id: Optional[str],
    ) -> NormalizedPipelineEvent:
        """Normalize pipeline event."""
        idempotency_key = self.generate_idempotency_key(
            repo=parsed["project_name"],
            pipeline_id=parsed["pipeline_id"],
            job_id=parsed["pipeline_id"],
            attempt=1,
        )

        # Parse timestamp
        finished_at = parsed.get("finished_at")
        if finished_at:
            if isinstance(finished_at, str):
                event_timestamp = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            else:
                event_timestamp = finished_at
        else:
            event_timestamp = datetime.now(UTC)

        return NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=self.ci_provider_enum,
            pipeline_id=parsed["pipeline_id"],
            repo=parsed["project_name"],
            commit_sha=parsed["sha"],
            branch=parsed["ref"],
            stage="pipeline",
            failure_type=FailureType.BUILD,  # Pipeline failures default to build
            error_message=f"Pipeline {parsed['pipeline_id']} failed",
            event_timestamp=event_timestamp,
            raw_payload=raw_payload,
            correlation_id=correlation_id,
        )

    def _normalize_build(
        self,
        parsed: dict[str, Any],
        raw_payload: dict[str, Any],
        correlation_id: Optional[str],
    ) -> NormalizedPipelineEvent:
        """Normalize build (job) event."""
        idempotency_key = self.generate_idempotency_key(
            repo=parsed["project_name"],
            pipeline_id=parsed["pipeline_id"],
            job_id=parsed["build_id"],
            attempt=1,
        )

        # Infer failure type
        failure_type = self.infer_failure_type(
            job_name=parsed.get("job_name", ""),
            status=parsed.get("status", ""),
        )

        # Build error message
        error_message = parsed.get("failure_reason") or f"Job '{parsed.get('job_name')}' failed"

        # Parse timestamp
        finished_at = parsed.get("finished_at")
        if finished_at:
            if isinstance(finished_at, str):
                event_timestamp = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            else:
                event_timestamp = finished_at
        else:
            event_timestamp = datetime.now(UTC)

        return NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=self.ci_provider_enum,
            pipeline_id=parsed["pipeline_id"],
            repo=parsed["project_name"],
            commit_sha=parsed["sha"],
            branch=parsed["ref"],
            stage=parsed.get("stage") or parsed.get("job_name", "build"),
            failure_type=failure_type,
            error_message=error_message,
            event_timestamp=event_timestamp,
            raw_payload=raw_payload,
            correlation_id=correlation_id,
        )

    async def fetch_logs(
        self,
        job_id: str,
        project_id: Optional[str] = None,
        **kwargs,
    ) -> FetchedLogs:
        """Fetch job logs from GitLab API.

        Args:
            job_id: GitLab job ID
            project_id: GitLab project ID (URL-encoded)
        """
        if not project_id:
            raise ValueError("project_id is required for GitLab log fetching")

        client = await self.get_client()

        # GitLab API endpoint for job trace (logs)
        api_url = self.config.api_url or "https://gitlab.com"
        url = f"{api_url}/api/v4/projects/{project_id}/jobs/{job_id}/trace"

        try:
            response = await client.get(url)
            response.raise_for_status()

            content = response.text

            # GitLab may truncate logs, check for indicator
            truncated = len(content) >= 4 * 1024 * 1024  # 4MB limit

            return FetchedLogs(
                job_id=job_id,
                content=content,
                truncated=truncated,
            )
        except Exception as e:
            logger.error(f"Failed to fetch GitLab logs: {e}")
            raise
