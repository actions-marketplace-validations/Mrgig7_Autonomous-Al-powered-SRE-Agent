"""Azure DevOps provider integration.

Handles Azure DevOps Service Hooks (webhooks) and API interactions for:
- Build completed events
- Release deployment events
- Pipeline logs retrieval
"""

import base64
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


@ProviderRegistry.register(ProviderType.AZURE_DEVOPS)
class AzureDevOpsProvider(BaseCIProvider):
    """Azure DevOps provider implementation.

    Handles:
    - Build completed webhooks (Service Hooks)
    - Release deployment webhooks
    - Pipeline log retrieval via Azure DevOps REST API
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.AZURE_DEVOPS

    @property
    def ci_provider_enum(self) -> CIProvider:
        return CIProvider.AZURE_DEVOPS

    def _get_auth_headers(self) -> dict[str, str]:
        """Get Azure DevOps API authentication headers."""
        headers = {"Content-Type": "application/json"}

        if self.config.api_token:
            # Azure DevOps uses PAT with Basic Auth (empty username)
            credentials = f":{self.config.api_token}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookVerificationResult:
        """Verify Azure DevOps Service Hook.

        Azure DevOps Service Hooks support:
        - Basic Auth
        - No auth (rely on URL obscurity)

        We check for Authorization header or accept if no secret configured.
        """
        auth_header = headers.get("Authorization", "")

        if not self.config.webhook_secret:
            logger.warning("Azure DevOps webhook secret not configured - accepting all webhooks")
            return WebhookVerificationResult(
                valid=True,
                provider=ProviderType.AZURE_DEVOPS,
                event_type="build.complete",
            )

        # Check Basic Auth
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                _, password = decoded.split(":", 1)
                if password == self.config.webhook_secret:
                    return WebhookVerificationResult(
                        valid=True,
                        provider=ProviderType.AZURE_DEVOPS,
                        event_type="build.complete",
                    )
            except Exception:
                pass

        return WebhookVerificationResult(
            valid=False,
            provider=ProviderType.AZURE_DEVOPS,
            event_type="build.complete",
            error="Invalid webhook authentication",
        )

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse Azure DevOps Service Hook payload."""
        event_type = payload.get("eventType", "")

        if event_type.startswith("build.complete"):
            return self._parse_build_event(payload)
        elif event_type.startswith("ms.vss-release"):
            return self._parse_release_event(payload)
        else:
            raise ValueError(f"Unsupported Azure DevOps event type: {event_type}")

    def _parse_build_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse build.complete event."""
        resource = payload.get("resource", {})

        repo = resource.get("repository", {})
        definition = resource.get("definition", {})

        return {
            "event_type": "build",
            "build_id": str(resource.get("id")),
            "build_number": resource.get("buildNumber"),
            "build_url": resource.get("url"),
            "status": resource.get("status"),
            "result": resource.get("result"),
            "reason": resource.get("reason"),
            "project": resource.get("project", {}).get("name"),
            "definition_name": definition.get("name"),
            "definition_id": str(definition.get("id")),
            "repo_name": repo.get("name"),
            "repo_type": repo.get("type"),
            "branch": resource.get("sourceBranch", "").replace("refs/heads/", ""),
            "sha": resource.get("sourceVersion"),
            "start_time": resource.get("startTime"),
            "finish_time": resource.get("finishTime"),
            "organization": payload.get("resourceContainers", {}).get("account", {}).get("id"),
        }

    def _parse_release_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse release deployment event."""
        resource = payload.get("resource", {})

        environment = resource.get("environment", {})
        release = resource.get("release", {})
        project = resource.get("project", {})

        return {
            "event_type": "release",
            "deployment_id": str(resource.get("id")),
            "release_id": str(release.get("id")),
            "release_name": release.get("name"),
            "environment_name": environment.get("name"),
            "status": environment.get("status"),
            "project": project.get("name"),
            "definition_name": release.get("releaseDefinition", {}).get("name"),
            "branch": "",  # Release may not have branch info
            "sha": "",
            "start_time": resource.get("deploymentStartedOn"),
            "finish_time": resource.get("deploymentCompletedOn"),
            "organization": payload.get("resourceContainers", {}).get("account", {}).get("id"),
        }

    def should_process(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Check if event should be processed."""
        parsed = self.parse_event(payload)

        if parsed["event_type"] == "build":
            result = (parsed.get("result") or "").lower()
            if result not in ("failed", "canceled", "partiallysucceeded"):
                return False, f"Build result '{result}' is not a failure"
            return True, ""

        elif parsed["event_type"] == "release":
            status = (parsed.get("status") or "").lower()
            if status not in ("failed", "canceled", "rejected"):
                return False, f"Release status '{status}' is not a failure"
            return True, ""

        return False, f"Unsupported event type: {parsed['event_type']}"

    def normalize_event(
        self,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> NormalizedPipelineEvent:
        """Normalize Azure DevOps event to canonical format."""
        parsed = self.parse_event(payload)

        # Build repo identifier
        project = parsed.get("project", "")
        repo_name = parsed.get("repo_name") or parsed.get("definition_name", "")
        repo = f"{project}/{repo_name}" if project else repo_name

        # Get identifiers
        if parsed["event_type"] == "build":
            pipeline_id = parsed["definition_id"]
            job_id = parsed["build_id"]
            stage = parsed["definition_name"]
        else:
            pipeline_id = str(parsed.get("release_id", ""))
            job_id = str(parsed.get("deployment_id", ""))
            stage = parsed.get("environment_name", "release")

        idempotency_key = self.generate_idempotency_key(
            repo=repo,
            pipeline_id=pipeline_id,
            job_id=job_id,
        )

        failure_type = self.infer_failure_type(
            job_name=stage or "",
            status=parsed.get("result") or parsed.get("status", ""),
        )

        # Parse timestamp
        finish_time = parsed.get("finish_time")
        if finish_time:
            if isinstance(finish_time, str):
                event_timestamp = datetime.fromisoformat(finish_time.replace("Z", "+00:00"))
            else:
                event_timestamp = finish_time
        else:
            event_timestamp = datetime.now(UTC)

        error_message = f"Azure DevOps {parsed['event_type']} '{stage}' failed"
        if parsed.get("reason"):
            error_message += f" (reason: {parsed['reason']})"

        return NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=self.ci_provider_enum,
            pipeline_id=pipeline_id,
            repo=repo,
            commit_sha=parsed.get("sha"),
            branch=parsed.get("branch"),
            stage=stage or "build",
            failure_type=failure_type,
            error_message=error_message,
            event_timestamp=event_timestamp,
            raw_payload=payload,
            correlation_id=correlation_id,
        )

    async def fetch_logs(
        self,
        job_id: str,
        organization: Optional[str] = None,
        project: Optional[str] = None,
        build_id: Optional[str] = None,
        **kwargs,
    ) -> FetchedLogs:
        """Fetch build logs from Azure DevOps API.

        Args:
            job_id: Build or timeline record ID
            organization: Azure DevOps organization
            project: Project name
            build_id: Build ID
        """
        client = await self.get_client()

        org = organization or self.config.extra.get("organization")
        if not org:
            raise ValueError("Azure DevOps organization not configured")

        if not project or not build_id:
            raise ValueError("project and build_id are required for Azure DevOps log fetching")

        # Azure DevOps REST API endpoint for build logs
        api_url = f"https://dev.azure.com/{org}/{project}/_apis/build/builds/{build_id}/logs"

        try:
            # First get list of logs
            response = await client.get(api_url, params={"api-version": "7.0"})
            response.raise_for_status()
            logs_list = response.json()

            # Fetch all log contents
            all_logs = []
            for log_entry in logs_list.get("value", []):
                log_url = log_entry.get("url")
                if log_url:
                    log_response = await client.get(log_url, params={"api-version": "7.0"})
                    if log_response.status_code == 200:
                        all_logs.append(log_response.text)

            content = "\n".join(all_logs)

            return FetchedLogs(
                job_id=job_id,
                content=content,
                truncated=False,
            )
        except Exception as e:
            logger.error(f"Failed to fetch Azure DevOps logs: {e}")
            raise
