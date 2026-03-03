from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sre_agent.tasks.fix_pipeline_tasks import _run_fix_pipeline_guarded


@dataclass
class _Run:
    id: object
    event_id: object
    run_key: str | None
    blocked_reason: str | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime | None


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *_args, **_kwargs):
        class _R:
            def scalar_one_or_none(self):
                return None

        return _R()


class _AsyncSessionCtx:
    async def __aenter__(self):
        return _Session()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_loop_block_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = uuid4()
    run = _Run(
        id=run_id,
        event_id=uuid4(),
        run_key="rk",
        blocked_reason=None,
        attempt_count=3,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    class _Store:
        async def get_run(self, _rid):
            return run

        async def update_run(self, _rid, **fields):
            run.blocked_reason = fields.get("blocked_reason", run.blocked_reason)

    class _Settings:
        max_pipeline_attempts = 3
        cooldown_seconds = 900
        base_backoff_seconds = 30
        max_backoff_seconds = 600
        repo_pipeline_concurrency_limit = 2
        repo_pipeline_concurrency_ttl_seconds = 1200

    monkeypatch.setattr("sre_agent.fix_pipeline.store.FixPipelineRunStore", lambda: _Store())
    monkeypatch.setattr("sre_agent.config.get_settings", lambda: _Settings())
    monkeypatch.setattr("sre_agent.database.get_async_session", lambda: _AsyncSessionCtx())

    res = await _run_fix_pipeline_guarded(run_id, None)
    assert res["error"] == "blocked"
    assert res["blocked_reason"] == "max_attempts"
