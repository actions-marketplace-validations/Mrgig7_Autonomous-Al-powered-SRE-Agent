"""Jenkins provider integration.

Handles Jenkins webhooks (via Generic Webhook Trigger plugin)
and API interactions for:
- Build completed events
- Console log retrieval
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


@ProviderRegistry.register(ProviderType.JENKINS)
class JenkinsProvider(BaseCIProvider):
    """Jenkins provider implementation.

    Works with:
    - Generic Webhook Trigger Plugin
    - Notification Plugin
    - HTTP Request Plugin

    Note: Jenkins webhook payloads vary by plugin, so we support
    multiple formats.
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.JENKINS

    @property
    def ci_provider_enum(self) -> CIProvider:
        return CIProvider.JENKINS

    def _get_auth_headers(self) -> dict[str, str]:
        """Get Jenkins API authentication headers."""
        headers = {"Content-Type": "application/json"}

        if self.config.username and self.config.api_token:
            # Jenkins uses Basic Auth
            credentials = f"{self.config.username}:{self.config.api_token}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookVerificationResult:
        """Verify Jenkins webhook.

        Jenkins Generic Webhook Trigger can use:
        - Token in URL query parameter
        - Token in header
        - No auth (IP whitelist)

        We check for X-Jenkins-Token header or accept all if no secret configured.
        """
        token = headers.get("X-Jenkins-Token") or headers.get("x-jenkins-token")

        if not self.config.webhook_secret:
            logger.warning("Jenkins webhook secret not configured - accepting all webhooks")
            return WebhookVerificationResult(
                valid=True,
                provider=ProviderType.JENKINS,
                event_type="build",
            )

        if not token:
            # Check if token might be in Authorization header
            auth = headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]

        if token != self.config.webhook_secret:
            return WebhookVerificationResult(
                valid=False,
                provider=ProviderType.JENKINS,
                event_type="build",
                error="Invalid webhook token",
            )

        return WebhookVerificationResult(
            valid=True,
            provider=ProviderType.JENKINS,
            event_type="build",
        )

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse Jenkins webhook payload.

        Supports multiple payload formats:
        - Generic Webhook Trigger format
        - Notification Plugin format
        - Custom format
        """
        # Try to detect format
        if "build" in payload:
            return self._parse_notification_format(payload)
        elif "name" in payload and "number" in payload:
            return self._parse_generic_format(payload)
        else:
            return self._parse_custom_format(payload)

    def _parse_notification_format(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse Notification Plugin format."""
        build = payload.get("build", {})

        return {
            "event_type": "build",
            "job_name": payload.get("name"),
            "job_url": payload.get("url"),
            "build_number": build.get("number"),
            "build_url": build.get("full_url") or build.get("url"),
            "status": build.get("status"),
            "phase": build.get("phase"),
            "duration": build.get("duration"),
            "scm": build.get("scm", {}),
        }

    def _parse_generic_format(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse Generic Webhook Trigger format."""
        return {
            "event_type": "build",
            "job_name": payload.get("name"),
            "job_url": payload.get("url"),
            "build_number": payload.get("number"),
            "build_url": payload.get("buildUrl"),
            "status": payload.get("result") or payload.get("status"),
            "phase": payload.get("phase", "COMPLETED"),
            "duration": payload.get("duration"),
            "scm": {
                "commit": payload.get("commit") or payload.get("sha"),
                "branch": payload.get("branch") or payload.get("ref"),
                "url": payload.get("repoUrl"),
            },
        }

    def _parse_custom_format(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse custom/unknown format with best effort."""
        return {
            "event_type": "build",
            "job_name": payload.get("job_name") or payload.get("project") or payload.get("name"),
            "job_url": payload.get("job_url") or payload.get("url"),
            "build_number": payload.get("build_number")
            or payload.get("number")
            or payload.get("id"),
            "build_url": payload.get("build_url"),
            "status": payload.get("status") or payload.get("result"),
            "phase": payload.get("phase", "COMPLETED"),
            "scm": {
                "commit": payload.get("commit") or payload.get("sha") or payload.get("revision"),
                "branch": payload.get("branch") or payload.get("ref"),
            },
        }

    def should_process(self, payload: dict[str, Any]) -> tuple[bool, str]:
        """Check if event should be processed."""
        parsed = self.parse_event(payload)

        phase = parsed.get("phase", "").upper()
        status = (parsed.get("status") or "").upper()

        # Only process completed builds
        if phase not in ("COMPLETED", "FINALIZED", "FINISHED"):
            return False, f"Build phase '{phase}' is not completed"

        # Check for failure
        if status not in ("FAILURE", "FAILED", "UNSTABLE", "ABORTED"):
            return False, f"Build status '{status}' is not a failure"

        return True, ""

    def normalize_event(
        self,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> NormalizedPipelineEvent:
        """Normalize Jenkins event to canonical format."""
        parsed = self.parse_event(payload)

        job_name = parsed.get("job_name", "unknown")
        build_number = str(parsed.get("build_number", 0))

        # Extract repo from job name or SCM info
        scm = parsed.get("scm", {})
        repo = scm.get("url", "") or job_name
        # Clean up repo URL to get name
        if repo.endswith(".git"):
            repo = repo[:-4]
        if repo.startswith("https://") or repo.startswith("git@"):
            parts = repo.replace(":", "/").split("/")
            repo = "/".join(parts[-2:]) if len(parts) >= 2 else job_name

        idempotency_key = self.generate_idempotency_key(
            repo=repo,
            pipeline_id=job_name,
            job_id=build_number,
        )

        failure_type = self.infer_failure_type(
            job_name=job_name,
            status=parsed.get("status", ""),
        )

        return NormalizedPipelineEvent(
            idempotency_key=idempotency_key,
            ci_provider=self.ci_provider_enum,
            pipeline_id=job_name,
            repo=repo,
            commit_sha=scm.get("commit"),
            branch=scm.get("branch"),
            stage=job_name,
            failure_type=failure_type,
            error_message=f"Jenkins build #{build_number} failed with status: {parsed.get('status')}",
            event_timestamp=datetime.now(UTC),
            raw_payload=payload,
            correlation_id=correlation_id,
        )

    async def fetch_logs(
        self,
        job_id: str,
        job_name: Optional[str] = None,
        build_number: Optional[str] = None,
        **kwargs,
    ) -> FetchedLogs:
        """Fetch build console log from Jenkins API.

        Args:
            job_id: Either job URL or job name
            job_name: Jenkins job name
            build_number: Build number
        """
        client = await self.get_client()

        jenkins_url = self.config.api_url
        if not jenkins_url:
            raise ValueError("Jenkins URL not configured")

        # Build URL for console text
        if job_name and build_number:
            url = f"{jenkins_url}/job/{job_name}/{build_number}/consoleText"
        else:
            # Assume job_id is the full build URL
            url = f"{job_id}/consoleText"

        try:
            response = await client.get(url)
            response.raise_for_status()

            content = response.text

            return FetchedLogs(
                job_id=job_id,
                content=content,
                truncated=False,
            )
        except Exception as e:
            logger.error(f"Failed to fetch Jenkins logs: {e}")
            raise
