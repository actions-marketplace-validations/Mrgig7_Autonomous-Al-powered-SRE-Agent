"""Failure context builder service.

Aggregates observability data (logs, git context, timing) to build
a FailureContextBundle for downstream RCA.
"""

import logging
from datetime import datetime

from sre_agent.models.events import PipelineEvent
from sre_agent.schemas.context import (
    ChangedFile,
    FailureContextBundle,
    LogContent,
    StepTiming,
)
from sre_agent.services.build_log_ingestion import BuildLogIngestionService
from sre_agent.services.github_client import (
    GitHubAPIError,
    GitHubClient,
    GitHubNotFoundError,
)
from sre_agent.services.log_parser import LogParser

logger = logging.getLogger(__name__)


class ContextBuildError(Exception):
    """Error during context building."""

    pass


class ContextBuilder:
    """
    Builds failure context bundles for RCA.

    Aggregates:
    - Log content from GitHub Actions
    - Parsed errors and stack traces
    - Git commit context (changed files, message)
    - Execution timing
    """

    def __init__(
        self,
        github_client: GitHubClient | None = None,
        log_parser: LogParser | None = None,
        build_log_ingestion_service: BuildLogIngestionService | None = None,
        max_log_size_mb: int = 10,
    ):
        """
        Initialize context builder.

        Args:
            github_client: GitHub API client (creates one if not provided)
            log_parser: Log parser instance
            max_log_size_mb: Maximum log size to download
        """
        self._github_client = github_client
        self.log_parser = log_parser or LogParser()
        self.log_ingestion = build_log_ingestion_service or BuildLogIngestionService(
            max_log_size_mb=max_log_size_mb
        )

    async def build_context(
        self,
        event: PipelineEvent,
    ) -> FailureContextBundle:
        """
        Build a complete failure context bundle for an event.

        Args:
            event: Pipeline event to build context for

        Returns:
            FailureContextBundle with all aggregated data
        """
        logger.info(
            "Building failure context",
            extra={
                "event_id": str(event.id),
                "repo": event.repo,
                "pipeline_id": event.pipeline_id,
            },
        )

        # Initialize bundle with event data
        bundle = FailureContextBundle(
            event_id=event.id,
            repo=event.repo,
            commit_sha=event.commit_sha,
            branch=event.branch,
            pipeline_id=event.pipeline_id,
            job_name=event.stage,
        )

        # Use provided client or create new one
        if self._github_client:
            client = self._github_client
            should_close = False
        else:
            client = GitHubClient()
            should_close = True

        try:
            if should_close:
                await client.__aenter__()

            # Fetch and parse logs
            await self._add_log_context(client, event, bundle)

            # Fetch git context
            await self._add_git_context(client, event, bundle)

            # Add timing information
            await self._add_timing_context(client, event, bundle)

        except GitHubNotFoundError as e:
            logger.warning(
                "GitHub resource not found during context building",
                extra={"error": str(e), "event_id": str(event.id)},
            )
        except GitHubAPIError as e:
            logger.error(
                "GitHub API error during context building",
                extra={"error": str(e), "event_id": str(event.id)},
            )
        except Exception as e:
            logger.error(
                "Unexpected error during context building",
                extra={"error": str(e), "event_id": str(event.id)},
                exc_info=True,
            )
        finally:
            if should_close:
                await client.__aexit__(None, None, None)

        logger.info(
            "Built failure context",
            extra={
                "event_id": str(event.id),
                "has_logs": bundle.log_content is not None,
                "stack_traces": len(bundle.stack_traces),
                "errors": len(bundle.errors),
                "test_failures": len(bundle.test_failures),
            },
        )

        return bundle

    async def _add_log_context(
        self,
        client: GitHubClient,
        event: PipelineEvent,
        bundle: FailureContextBundle,
    ) -> None:
        """Fetch and parse logs for the failed job."""
        try:
            ingested = await self.log_ingestion.ingest(client=client, event=event)
            if ingested is None:
                return

            bundle.log_content = LogContent(
                raw_content=ingested.content,
                truncated=ingested.truncated,
                size_bytes=ingested.size_bytes,
                job_name=event.stage,
            )

            # Parse logs
            parsed = self.log_parser.parse(ingested.content)
            bundle.errors = parsed.errors
            bundle.stack_traces = parsed.stack_traces
            bundle.test_failures = parsed.test_failures
            bundle.build_errors = parsed.build_errors
            bundle.log_summary = parsed.summary

        except Exception as e:
            logger.error(
                "Failed to fetch logs",
                extra={"error": str(e), "event_id": str(event.id)},
            )

    async def _add_git_context(
        self,
        client: GitHubClient,
        event: PipelineEvent,
        bundle: FailureContextBundle,
    ) -> None:
        """Add git commit context to the bundle."""
        try:
            commit = await client.get_commit(
                repo=event.repo,
                sha=event.commit_sha,
            )

            bundle.commit_message = commit.get("commit", {}).get("message")
            bundle.commit_author = commit.get("commit", {}).get("author", {}).get("name")

            # Extract changed files
            files = commit.get("files", [])
            bundle.changed_files = [
                ChangedFile(
                    filename=f.get("filename", ""),
                    status=f.get("status", "modified"),
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch"),
                )
                for f in files
            ]

        except GitHubNotFoundError:
            logger.warning(
                "Commit not found",
                extra={"sha": event.commit_sha, "repo": event.repo},
            )
        except Exception as e:
            logger.error(
                "Failed to fetch commit",
                extra={"error": str(e), "sha": event.commit_sha},
            )

    async def _add_timing_context(
        self,
        client: GitHubClient,
        event: PipelineEvent,
        bundle: FailureContextBundle,
    ) -> None:
        """Add timing information to the bundle."""
        try:
            # Get job details for step timing
            job_id = self._extract_job_id(event)
            if not job_id:
                return

            job = await client.get_workflow_job(
                repo=event.repo,
                job_id=job_id,
            )

            # Calculate total execution time
            started_at = job.get("started_at")
            completed_at = job.get("completed_at")
            if started_at and completed_at:
                start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                bundle.execution_time_seconds = (end - start).total_seconds()

            # Extract step timings
            steps = job.get("steps", [])
            bundle.step_timings = [
                StepTiming(
                    name=step.get("name", "unknown"),
                    started_at=(
                        datetime.fromisoformat(step["started_at"].replace("Z", "+00:00"))
                        if step.get("started_at")
                        else None
                    ),
                    completed_at=(
                        datetime.fromisoformat(step["completed_at"].replace("Z", "+00:00"))
                        if step.get("completed_at")
                        else None
                    ),
                    conclusion=step.get("conclusion"),
                )
                for step in steps
            ]

            # Calculate duration for each step
            for timing in bundle.step_timings:
                if timing.started_at and timing.completed_at:
                    timing.duration_seconds = (
                        timing.completed_at - timing.started_at
                    ).total_seconds()

        except Exception as e:
            logger.error(
                "Failed to fetch timing context",
                extra={"error": str(e), "event_id": str(event.id)},
            )

    def _extract_job_id(self, event: PipelineEvent) -> int | None:
        """Extract job ID from event raw payload."""
        try:
            raw = event.raw_payload
            if isinstance(raw, dict):
                job = raw.get("workflow_job", {})
                return job.get("id")
            return None
        except Exception:
            return None
