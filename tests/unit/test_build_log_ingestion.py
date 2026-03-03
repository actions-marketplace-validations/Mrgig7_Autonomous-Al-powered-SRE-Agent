from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sre_agent.services.build_log_ingestion import BuildLogIngestionService
from sre_agent.services.github_client import GitHubAPIError


def _event(*, raw_payload: dict, stage: str = "Run tests"):
    return SimpleNamespace(
        id=uuid4(),
        repo="acme/widgets",
        stage=stage,
        raw_payload=raw_payload,
    )


@pytest.mark.asyncio
async def test_ingest_prefers_workflow_job_logs() -> None:
    service = BuildLogIngestionService(max_log_size_mb=1)

    class _Client:
        async def download_job_logs(self, *, repo: str, job_id: int) -> str:
            assert repo == "acme/widgets"
            assert job_id == 123
            return "job logs"

    result = await service.ingest(
        client=_Client(),
        event=_event(raw_payload={"workflow_job": {"id": 123}}),
    )

    assert result is not None
    assert result.content == "job logs"
    assert result.source == "job"
    assert result.truncated is False


@pytest.mark.asyncio
async def test_ingest_falls_back_to_workflow_run_logs() -> None:
    service = BuildLogIngestionService(max_log_size_mb=1)

    class _Client:
        async def download_run_logs(self, *, repo: str, run_id: int) -> dict[str, str]:
            assert repo == "acme/widgets"
            assert run_id == 456
            return {
                "Build": "build logs",
                "Run tests": "test logs",
            }

    result = await service.ingest(
        client=_Client(),
        event=_event(raw_payload={"workflow_run": {"id": 456}}, stage="Run tests"),
    )

    assert result is not None
    assert result.content == "test logs"
    assert result.source == "run"


@pytest.mark.asyncio
async def test_ingest_returns_none_when_no_identifiers_present() -> None:
    service = BuildLogIngestionService(max_log_size_mb=1)

    class _Client:
        async def download_job_logs(self, *, repo: str, job_id: int) -> str:
            raise AssertionError("should not be called")

    result = await service.ingest(
        client=_Client(),
        event=_event(raw_payload={}),
    )

    assert result is None


@pytest.mark.asyncio
async def test_ingest_truncates_large_logs() -> None:
    service = BuildLogIngestionService(max_log_size_mb=1)

    class _Client:
        async def download_job_logs(self, *, repo: str, job_id: int) -> str:
            return "a" * (2 * 1024 * 1024)

    result = await service.ingest(
        client=_Client(),
        event=_event(raw_payload={"workflow_job": {"id": 123}}),
    )

    assert result is not None
    assert result.truncated is True
    assert len(result.content.encode("utf-8")) <= 1024 * 1024


@pytest.mark.asyncio
async def test_ingest_returns_none_on_github_api_error() -> None:
    service = BuildLogIngestionService(max_log_size_mb=1)

    class _Client:
        async def download_job_logs(self, *, repo: str, job_id: int) -> str:
            raise GitHubAPIError("rate limited", status_code=403)

    result = await service.ingest(
        client=_Client(),
        event=_event(raw_payload={"workflow_job": {"id": 123}}),
    )

    assert result is None
