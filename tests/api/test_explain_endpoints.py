from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import get_current_user
from sre_agent.main import create_app


@dataclass
class DummyEvent:
    id: UUID
    repo: str


@dataclass
class DummyRun:
    id: UUID
    event_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime | None
    context_json: dict[str, Any] | None = None
    rca_json: dict[str, Any] | None = None
    plan_json: dict[str, Any] | None = None
    plan_policy_json: dict[str, Any] | None = None
    patch_policy_json: dict[str, Any] | None = None
    patch_diff: str | None = None
    patch_stats_json: dict[str, Any] | None = None
    validation_json: dict[str, Any] | None = None
    adapter_name: str | None = None
    detection_json: dict[str, Any] | None = None
    artifact_json: dict[str, Any] | None = None
    issue_graph_json: dict[str, Any] | None = None
    consensus_json: dict[str, Any] | None = None
    consensus_shadow_diff_json: dict[str, Any] | None = None
    consensus_state: str | None = None


def _authed_client() -> TestClient:
    app = create_app()

    async def _override_user() -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            user_id=uuid4(),
            email="viewer@example.com",
            role="viewer",
            permissions=[
                "view_dashboard",
                "view_failures",
                "api_read",
            ],
            exp=now + timedelta(hours=1),
            iat=now,
            jti="test",
            token_type="access",
        )

    app.dependency_overrides[get_current_user] = _override_user
    return TestClient(app)


def test_failure_explain_endpoint_returns_contract_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _authed_client()
    failure_id = uuid4()
    run_id = uuid4()

    event = DummyEvent(id=failure_id, repo="acme/widgets")
    run = DummyRun(
        id=run_id,
        event_id=failure_id,
        status="validation_passed",
        created_at=datetime.now(UTC),
        updated_at=None,
        context_json={
            "log_content": {
                "raw_content": 'npm ERR! token=abcd1234\npassword="supersecret"\nFAILED tests/test_api.py::test_health\n',
                "truncated": False,
                "size_bytes": 120,
            },
            "log_summary": None,
        },
        plan_json={
            "root_cause": "Missing dependency (token=abcd1234)",
            "category": "node_missing_dependency",
            "confidence": 0.7,
            "files": ["package.json"],
            "operations": [
                {
                    "type": "add_dependency",
                    "file": "package.json",
                    "details": {},
                    "rationale": "Add missing dep",
                    "evidence": ["FAILED tests/test_api.py::test_health"],
                }
            ],
        },
        plan_policy_json={
            "allowed": True,
            "violations": [],
            "danger_score": 10,
            "danger_reasons": [{"code": "file_count", "weight": 5, "message": "Files touched: 1"}],
            "pr_label": "safe",
        },
        patch_policy_json=None,
        patch_diff='diff --git a/package.json b/package.json\n+"token": "abcd1234"\n',
        patch_stats_json={"total_files": 1, "lines_added": 1, "lines_removed": 0},
        validation_json={"status": "passed", "tests_failed": 0, "tests_total": 10, "scans": None},
        adapter_name="node",
        detection_json={
            "repo_language": "node",
            "category": "node_missing_dependency",
            "confidence": 0.9,
        },
    )

    async def _fake_load_failure_and_latest_run(*, failure_id: UUID):
        return event, run

    monkeypatch.setattr(
        "sre_agent.explainability.explain_service.load_failure_and_latest_run",
        _fake_load_failure_and_latest_run,
    )

    res = client.get(f"/api/v1/failures/{failure_id}/explain")
    assert res.status_code == 200
    body = res.json()
    alias_res = client.get(f"/api/v1/failures/{failure_id}/analysis")
    assert alias_res.status_code == 200
    alias_body = alias_res.json()

    assert body["failure_id"] == str(failure_id)
    assert body["repo"] == "acme/widgets"
    assert alias_body["failure_id"] == body["failure_id"]
    assert alias_body["summary"] == body["summary"]
    assert "summary" in body and "confidence" in body["summary"]
    assert isinstance(body.get("evidence"), list)
    assert body["proposed_fix"]["diff_available"] is True

    serialized = res.text
    assert "abcd1234" not in serialized
    assert "supersecret" not in serialized
    assert "[REDACTED]" in serialized


def test_run_diff_and_timeline_and_artifact_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed_client()
    run_id = uuid4()
    failure_id = uuid4()

    dummy_artifact = {
        "run_id": str(run_id),
        "failure_id": str(failure_id),
        "repo": "acme/widgets",
        "timestamps": {
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        },
        "status": "validation_passed",
        "error_message": None,
        "adapter": {"name": "node", "evidence_lines": []},
        "plan": None,
        "policy": {"allowed": True, "danger_score": 5, "label": "safe", "violations": []},
        "diff_stats": {"files_changed": 1, "lines_added": 1, "lines_deleted": 0},
        "scans": None,
        "validation": None,
        "timeline": [
            {
                "step": "plan",
                "status": "ok",
                "started_at": datetime.now(UTC).isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "duration_ms": 12,
            }
        ],
    }

    run = DummyRun(
        id=run_id,
        event_id=failure_id,
        status="validation_passed",
        created_at=datetime.now(UTC),
        updated_at=None,
        patch_diff="token=abcd1234",
        patch_stats_json={"total_files": 1},
        artifact_json=dummy_artifact,
    )

    async def _fake_get_run(self, rid: UUID):
        assert rid == run_id
        return run

    monkeypatch.setattr("sre_agent.fix_pipeline.store.FixPipelineRunStore.get_run", _fake_get_run)

    diff_res = client.get(f"/api/v1/runs/{run_id}/diff")
    assert diff_res.status_code == 200
    assert "abcd1234" not in diff_res.text
    assert "[REDACTED]" in diff_res.text

    timeline_res = client.get(f"/api/v1/runs/{run_id}/timeline")
    assert timeline_res.status_code == 200
    assert timeline_res.json()["run_id"] == str(run_id)
    assert len(timeline_res.json()["timeline"]) == 1

    artifact_res = client.get(f"/api/v1/runs/{run_id}/artifact")
    assert artifact_res.status_code == 200
    assert artifact_res.json()["run_id"] == str(run_id)


def test_consensus_endpoints_return_persisted_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _authed_client()
    run_id = uuid4()
    failure_id = uuid4()
    run = DummyRun(
        id=run_id,
        event_id=failure_id,
        status="plan_ready",
        created_at=datetime.now(UTC),
        updated_at=None,
        issue_graph_json={
            "issues": [
                {
                    "issue_id": "error_0",
                    "message": "Import error",
                    "severity": "error",
                    "file_paths": ["src/app.py"],
                    "evidence_refs": ["ImportError"],
                }
            ],
            "affected_files": ["src/app.py"],
            "severity_levels": {"error": 1},
            "dependency_links": [],
        },
        consensus_json={
            "state": "accepted",
            "agreement_rate": 1.0,
            "selected_agent": "planner",
            "selected_plan": None,
            "candidates": [],
            "rejections": [],
            "metadata": {"candidate_count": 3},
        },
        consensus_shadow_diff_json={"mode": "dual_run", "same_as_executed": True},
        consensus_state="accepted",
    )

    async def _fake_get_run(self, rid: UUID):
        assert rid == run_id
        return run

    async def _fake_get_run_by_event_id(self, event_id: UUID):
        assert event_id == failure_id
        return run

    monkeypatch.setattr("sre_agent.fix_pipeline.store.FixPipelineRunStore.get_run", _fake_get_run)
    monkeypatch.setattr(
        "sre_agent.fix_pipeline.store.FixPipelineRunStore.get_run_by_event_id",
        _fake_get_run_by_event_id,
    )

    by_run_res = client.get(f"/api/v1/runs/{run_id}/consensus")
    assert by_run_res.status_code == 200
    assert by_run_res.json()["consensus_state"] == "accepted"
    assert by_run_res.json()["run_id"] == str(run_id)

    by_failure_res = client.get(f"/api/v1/failures/{failure_id}/consensus")
    assert by_failure_res.status_code == 200
    assert by_failure_res.json()["failure_id"] == str(failure_id)
