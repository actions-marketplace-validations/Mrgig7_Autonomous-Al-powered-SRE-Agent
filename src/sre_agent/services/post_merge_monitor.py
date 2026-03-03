"""Post-merge event-driven monitor for recently merged automated fixes."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sre_agent.config import get_settings
from sre_agent.core.redis_service import get_redis_service
from sre_agent.fix_pipeline.store import FixPipelineRunStore
from sre_agent.models.fix_pipeline import FixPipelineRunStatus
from sre_agent.observability.metrics import METRICS
from sre_agent.services.dashboard_events import publish_dashboard_event

logger = logging.getLogger(__name__)


class PostMergeMonitorService:
    """Tracks merged runs and correlates subsequent CI outcomes."""

    def __init__(self, *, ttl_seconds: int | None = None) -> None:
        settings = get_settings()
        default_ttl = int(getattr(settings, "phase3_post_merge_monitor_ttl_seconds", 7200))
        self.ttl_seconds = int(ttl_seconds if ttl_seconds is not None else default_ttl)

    @staticmethod
    def _cache_key(repo: str, branch: str) -> str:
        return f"post_merge:{repo}:{branch}"

    async def register(
        self,
        *,
        run_id: UUID,
        repo: str,
        branch: str,
        pr_number: int | None,
    ) -> None:
        payload = {
            "run_id": str(run_id),
            "repo": repo,
            "branch": branch,
            "pr_number": pr_number,
            "status": "monitoring",
        }
        redis_service = get_redis_service()
        await redis_service.cache_set(self._cache_key(repo, branch), payload, self.ttl_seconds)
        store = FixPipelineRunStore()
        await store.update_run(
            run_id,
            status=FixPipelineRunStatus.MONITORING.value,
            post_merge_monitor_json=payload,
        )
        await publish_dashboard_event(
            event_type="post_merge_monitor",
            stage="post_merge",
            status="monitoring",
            run_id=str(run_id),
            metadata={"repo": repo, "branch": branch},
        )

    async def process_outcome(
        self,
        *,
        repo: str,
        branch: str,
        conclusion: str | None,
    ) -> dict[str, Any] | None:
        redis_service = get_redis_service()
        monitor = await redis_service.cache_get(self._cache_key(repo, branch))
        if not isinstance(monitor, dict):
            return None

        run_id_raw = monitor.get("run_id")
        if not isinstance(run_id_raw, str):
            return None

        store = FixPipelineRunStore()
        run_id = UUID(run_id_raw)
        normalized = (conclusion or "").strip().lower()
        if normalized in {"success", "neutral"}:
            monitor["status"] = "stabilized"
            await store.update_run(
                run_id,
                status=FixPipelineRunStatus.MERGED.value,
                post_merge_monitor_json=monitor,
            )
            await redis_service.cache_delete(self._cache_key(repo, branch))
            await publish_dashboard_event(
                event_type="post_merge_monitor",
                stage="post_merge",
                status="stabilized",
                run_id=str(run_id),
                metadata={"repo": repo, "branch": branch},
            )
            return monitor

        if normalized in {"failure", "timed_out", "cancelled"}:
            monitor["status"] = "regressed"
            monitor["conclusion"] = normalized
            await store.update_run(
                run_id,
                status=FixPipelineRunStatus.ESCALATED.value,
                blocked_reason="post_merge_regression",
                post_merge_monitor_json=monitor,
            )
            METRICS.pipeline_loop_blocked_total.labels(reason="post_merge_regression").inc()
            await redis_service.cache_delete(self._cache_key(repo, branch))
            await publish_dashboard_event(
                event_type="post_merge_monitor",
                stage="post_merge",
                status="regressed",
                run_id=str(run_id),
                metadata={"repo": repo, "branch": branch, "conclusion": normalized},
            )
            return monitor

        logger.debug(
            "Ignoring post-merge outcome",
            extra={"repo": repo, "branch": branch, "conclusion": normalized},
        )
        return monitor
