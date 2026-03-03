from __future__ import annotations

import pytest
from sre_agent.services.dashboard_events import publish_dashboard_event


@pytest.mark.asyncio
async def test_publish_dashboard_event_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    published = {}

    class _Redis:
        async def publish(self, channel: str, message: dict):
            published["channel"] = channel
            published["message"] = message
            return 1

    monkeypatch.setattr("sre_agent.services.dashboard_events.get_redis_service", lambda: _Redis())
    await publish_dashboard_event(
        event_type="pipeline_stage",
        stage="plan",
        status="completed",
        failure_id="f1",
        run_id="r1",
        correlation_id="c1",
        metadata={"allowed": True},
    )
    assert published["channel"] == "dashboard_events"
    assert published["message"]["stage"] == "plan"
    assert published["message"]["status"] == "completed"
    assert published["message"]["failure_id"] == "f1"


@pytest.mark.asyncio
async def test_publish_dashboard_event_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Redis:
        async def publish(self, channel: str, message: dict):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr("sre_agent.services.dashboard_events.get_redis_service", lambda: _Redis())
    await publish_dashboard_event(event_type="pipeline_stage", stage="plan", status="failed")
