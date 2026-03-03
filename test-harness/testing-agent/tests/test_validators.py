from __future__ import annotations

import pytest
from validators import event_ingestion, observability, rca_engine


class DummyClient:
    async def get_dashboard_events(self, **_: str):
        return {"events": [{"id": "failure-uuid"}]}

    async def wait_for_dashboard_event(self, **_: str):
        return {"stage": "ingest", "failure_id": "failure-uuid"}

    async def get_metrics(self):
        return """
# HELP sre_agent_celery_tasks_total Total Celery task executions
sre_agent_celery_tasks_total{task="dispatch",status="started"} 1
sre_agent_pipeline_runs_total{outcome="success"} 1
sre_agent_pipeline_retry_total{reason="transient_error"} 0
sre_agent_policy_violations_total{type="path"} 0
"""

    async def get_analysis(self, _: str):
        return {
            "summary": {"category": "test", "root_cause": "assertion failed", "confidence": 0.92},
            "evidence": [{"idx": 1, "line": "FAILED test", "tag": "test-failure"}],
            "run": {"run_id": "run-123"},
        }


@pytest.mark.asyncio
async def test_event_ingestion_validator_passes() -> None:
    ctx = {
        "failure_id": "failure-uuid",
        "repository": "org/repo",
        "branch": "failure-1",
        "sse_wait_timeout_seconds": 1,
    }
    result = await event_ingestion.validate(ctx, DummyClient())
    assert result.passed is True


@pytest.mark.asyncio
async def test_rca_validator_sets_run_id() -> None:
    ctx = {"failure_id": "failure-uuid"}
    result = await rca_engine.validate(ctx, DummyClient())
    assert result.passed is True
    assert ctx["run_id"] == "run-123"


@pytest.mark.asyncio
async def test_observability_validator_uses_alias_mapping() -> None:
    ctx = {}
    result = await observability.validate(ctx, DummyClient())
    assert result.passed is True
