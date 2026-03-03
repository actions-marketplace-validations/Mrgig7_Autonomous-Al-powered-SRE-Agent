"""CircleCI provider integration.

Handles CircleCI webhooks and API interactions for:
- Workflow completed events
- Job completed events
- Build logs retrieval
"""

import hashlib
import hmac
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
from sre_agent.schemas.normalized import CIProvider, NormalizedPipelineEvent

logger = logging.getLogger(__name__)


@ProviderRegistry.register(ProviderType.CIRCLECI)
class CircleCIProvider(BaseCIProvider):
    """CircleCI provider implementation.

    Handles:
    - Workflow webhook events
    - Job webhook events
    - Log retrieval via CircleCI API v2
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.CIRCLECI

    @property
    def ci_provider_enum(self) -> CIProvider:
        return CIProvider.CIRCLECI

    def _get_auth_headers(self) -> dict[str, str]:
        """Get CircleCI API authentication headers."""
        headers = {"Content-Type": "application/json"}
        if self.config.api_token:
            headers["Circle-Token"] = self.config.api_token
        return headers

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookVerificationResult:
        """Verify CircleCI webhook signature.

        CircleCI uses HMAC-SHA256 with circleci-signature header.
        Format: v1=<signature>
        """
        signature_header = headers.get("circleci-signature") or headers.get(
            "Circleci-Signature", ""
        )
        event_type = headers.get("circleci-event-type") or headers.get("Circleci-Event-Type", "")

        if not self.config.webhook_secret:
            logger.warning("CircleCI webhook secret not configured - accepting all webhooks")
            return WebhookVerificationResult(
                valid=True,
                provider=ProviderType.CIRCLECI,
                event_type=event_type,
            )

        if not signature_header:
            return WebhookVerificationResult(
                valid=False,
                provider=ProviderType.CIRCLECI,
                event_type=event_type,
                error="Missing circleci-signature header",
            )

        # Parse signature (format: v1=<hex>)
        try:
            parts = signature_header.split("=", 1)
            if len(parts) != 2 or parts[0] != "v1":
                raise ValueError("Invalid signature format")
            signature = parts[1]
        except Exception:
            return WebhookVerificationResult(
                valid=False,
                provider=ProviderType.CIRCLECI,
                event_type=event_type,
                error="Invalid signature format",
            )

        # Verify HMAC
        expected = hmac.new(
            self.config.webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            return WebhookVerificationResult(
                valid=False,
                provider=ProviderType.CIRCLECI,
                event_type=event_type,
                error="Invalid webhook signature",
            )

        return WebhookVerificationResult(
            valid=True,
            provider=ProviderType.CIRCLECI,
            event_type=event_type,
        )

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse CircleCI webhook payload."""
        event_type = payload.get("type", "")

        if event_type == "workflow-completed":
            return self._parse_workflow_event(payload)
        elif event_type == "job-completed":
            return self._parse_job_event(payload)
        else:
            raise ValueError(f"Unsupported CircleCI event type: {event_type}")

    def _parse_workflow_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse workflow-completed event."""
        workflow = payload.get("workflow", {})
        project = payload.get("project", {})
        pipeline = payload.get("pipeline", {})

        return {
            "event_type": "workflow",
            "workflow_id": workflow.get("id"),
            "workflow_name": workflow.get("name"),
            "status": workflow.get("status"),
            "project_slug": project.get("slug"),
            "branch": pipeline.get("vcs", {}).get("branch"),
            "sha": pipeline.get("vcs", {}).get("revision"),
            "pipeline_id": pipeline.get("id"),
            "created_at": workflow.get("created_at"),
            "stopped_at": workflow.get("stopped_at"),
        }

    def _parse_job_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse job-completed event."""
        job = payload.get("job", {})
        project = payload.get("project", {})
        pipeline = payload.get("pipeline", {})
        workflow = payload.get("workflow", {})

        return {
            "event_type": "job",
            "job_id": job.get("id"),
            "job_name": job.get("name"),
            "job_number": job.get("number"),
            "status": job.get("status"),
            "project_slug": project.get("slug"),
            "branch": pipeline.get("vcs", {}).get("branch"),
            "sha": pipeline.get("vcs", {}).get("revision"),
            "pipeline_id": pipeline.get("id"),
            "workflow_id": workflow.get("id"),
            "started_at": job.get("started_at"),
            "stopped_at": job.get("stopped_at"),
        }

    def should_process(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Check if event should be processed."""
        event_type = payload.get("type", "")

        if event_type == "workflow-completed":
            status = payload.get("workflow", {}).get("status")
            if status != "failed":
                return False, f"Workflow status '{status}' is not a failure"
            return True, ""

        elif event_type == "job-completed":
            status = payload.get("job", {}).get("status")
            if status != "failed":
                return False, f"Job status '{status}' is not a failure"
            return True, ""

        return False, f"Unsupported event type: {event_type}"

    def normalize_event(
        self,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> NormalizedPipelineEvent:
        """Normalize CircleCI event to canonical format."""
        parsed = self.parse_event(payload)

        # Extract project from slug (e.g., "gh/owner/repo")
        project_slug = parsed.get("project_slug", "")
        # Convert "gh/owner/repo" to "owner/repo"
        parts = project_slug.split("/")
        repo = "/".join(parts[1:]) if len(parts) > 1 else project_slug

        # Get job/workflow identifiers
        if parsed["event_type"] == "workflow":
            job_id = parsed["workflow_id"]
            job_name = parsed["workflow_name"]
        else:
            job_id = str(parsed["job_number"])
            job_name = parsed["job_name"]

        idempotency_key = self.generate_idempotency_key(
            repo=repo,
            pipeline_id=str(parsed.get("pipeline_id", "")),
            job_id=str(job_id),
        )

        # Infer failure type
        failure_type = self.infer_failure_type(
            job_name=job_name or "",
            status=parsed.get("status", ""),
        )

        # Parse timestamp
        stopped_at = parsed.get("stopped_at")
        if stopped_at:
            if isinstance(stopped_at, str):
                event_timestamp = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))
            else:
                event_timestamp = stopped_at
        else:
            event_timestamp = datetime.now(UTC)

        return NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=self.ci_provider_enum,
            pipeline_id=str(parsed.get("pipeline_id", "")),
            repo=repo,
            commit_sha=parsed.get("sha"),
            branch=parsed.get("branch"),
            stage=job_name or "build",
            failure_type=failure_type,
            error_message=f"CircleCI {parsed['event_type']} '{job_name}' failed",
            event_timestamp=event_timestamp,
            raw_payload=payload,
            correlation_id=correlation_id,
        )

    async def fetch_logs(
        self,
        job_id: str,
        project_slug: Optional[str] = None,
        job_number: Optional[str] = None,
        **kwargs,
    ) -> FetchedLogs:
        """Fetch job logs from CircleCI API v2.

        Args:
            job_id: CircleCI job ID (UUID)
            project_slug: Project slug (e.g., "gh/owner/repo")
            job_number: Job number for alternative endpoint
        """
        client = await self.get_client()

        # CircleCI API v2 endpoint
        api_url = self.config.api_url or "https://circleci.com/api/v2"

        # Try to get job details first to find steps
        url = f"{api_url}/project/{project_slug}/job/{job_number}"

        try:
            response = await client.get(url)
            response.raise_for_status()
            job_data = response.json()

            # Get logs from steps
            logs = []
            for step in job_data.get("steps", []):
                for action in step.get("actions", []):
                    if action.get("output_url"):
                        # Fetch step output
                        output_resp = await client.get(action["output_url"])
                        if output_resp.status_code == 200:
                            for item in output_resp.json():
                                logs.append(item.get("message", ""))

            content = "\n".join(logs)

            return FetchedLogs(
                job_id=job_id,
                content=content,
                truncated=False,
            )
        except Exception as e:
            logger.error(f"Failed to fetch CircleCI logs: {e}")
            raise
