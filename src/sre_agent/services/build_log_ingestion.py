"""Deterministic build log ingestion helpers for pipeline events."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sre_agent.models.events import PipelineEvent
from sre_agent.observability.metrics import (
    record_build_log_ingestion_failure,
    record_build_log_ingestion_success,
)
from sre_agent.services.github_client import GitHubAPIError, GitHubClient, GitHubNotFoundError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestedBuildLog:
    """Normalized result of a build-log ingestion operation."""

    content: str
    truncated: bool
    size_bytes: int
    source: str


class BuildLogIngestionService:
    """Fetches build logs for a pipeline event from GitHub APIs."""

    def __init__(self, *, max_log_size_mb: int = 10) -> None:
        self.max_log_size_bytes = max(1, int(max_log_size_mb)) * 1024 * 1024

    async def ingest(
        self, *, client: GitHubClient, event: PipelineEvent
    ) -> IngestedBuildLog | None:
        """Fetch logs for an event by preferring job logs, then run logs."""
        try:
            raw_content, source = await self._fetch_content(client=client, event=event)
        except GitHubNotFoundError:
            record_build_log_ingestion_failure()
            logger.warning(
                "Build log resource not found",
                extra={"event_id": str(event.id), "repo": event.repo},
            )
            return None
        except GitHubAPIError as exc:
            record_build_log_ingestion_failure()
            logger.warning(
                "Build log ingestion failed due to GitHub API error",
                extra={"event_id": str(event.id), "repo": event.repo, "error": str(exc)},
            )
            return None
        except Exception as exc:
            record_build_log_ingestion_failure()
            logger.error(
                "Build log ingestion failed",
                extra={"event_id": str(event.id), "repo": event.repo, "error": str(exc)},
            )
            return None

        size_bytes = len(raw_content.encode("utf-8"))
        truncated = False
        if size_bytes > self.max_log_size_bytes:
            raw_content = raw_content[-self.max_log_size_bytes :]
            truncated = True

        record_build_log_ingestion_success()
        return IngestedBuildLog(
            content=raw_content,
            truncated=truncated,
            size_bytes=size_bytes,
            source=source,
        )

    async def _fetch_content(
        self, *, client: GitHubClient, event: PipelineEvent
    ) -> tuple[str, str]:
        raw_payload = event.raw_payload if isinstance(event.raw_payload, dict) else {}
        job_id = self._extract_job_id(raw_payload)
        if job_id is not None:
            content = await client.download_job_logs(repo=event.repo, job_id=job_id)
            return content, "job"

        run_id = self._extract_run_id(raw_payload)
        if run_id is not None:
            run_logs = await client.download_run_logs(repo=event.repo, run_id=run_id)
            return self._select_run_log_content(run_logs, event.stage), "run"

        raise ValueError("No workflow job_id or run_id found in event payload")

    def _extract_job_id(self, raw_payload: dict[str, Any]) -> int | None:
        workflow_job = raw_payload.get("workflow_job")
        if isinstance(workflow_job, dict) and isinstance(workflow_job.get("id"), int):
            return int(workflow_job["id"])
        return None

    def _extract_run_id(self, raw_payload: dict[str, Any]) -> int | None:
        workflow_run = raw_payload.get("workflow_run")
        if isinstance(workflow_run, dict) and isinstance(workflow_run.get("id"), int):
            return int(workflow_run["id"])
        return None

    def _select_run_log_content(self, run_logs: dict[str, str], stage: str) -> str:
        if not run_logs:
            return ""

        stage_normalized = stage.strip().lower()
        if stage_normalized:
            for job_name, content in run_logs.items():
                if job_name.strip().lower() == stage_normalized:
                    return content

        combined: list[str] = []
        for job_name, content in run_logs.items():
            combined.append(f"=== {job_name} ===\n{content}")
        return "\n".join(combined)
