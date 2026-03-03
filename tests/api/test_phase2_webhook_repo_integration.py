from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sre_agent.core.security import get_verified_github_payload
from sre_agent.database import get_db_session
from sre_agent.main import create_app
from sre_agent.schemas.repository_config import RepositoryRuntimeConfig


def _client_with_overrides(payload: dict, delivery_id: str, *, event_type: str = "workflow_job"):
    app = create_app()

    async def _override_verified():
        return json.dumps(payload).encode("utf-8"), event_type, delivery_id

    class _Session:
        async def commit(self):
            return None

        async def rollback(self):
            return None

        async def close(self):
            return None

    async def _override_db():
        yield _Session()

    app.dependency_overrides[get_verified_github_payload] = _override_verified
    app.dependency_overrides[get_db_session] = _override_db
    return TestClient(app)


def _payload(*, installation_id: int | None = None) -> dict:
    payload = {
        "action": "completed",
        "workflow_job": {
            "id": 123,
            "run_id": 456,
            "run_attempt": 1,
            "head_sha": "a" * 40,
            "head_branch": "main",
            "conclusion": "failure",
            "name": "Run tests",
            "created_at": "2026-01-09T00:00:00Z",
            "steps": [],
        },
        "repository": {"full_name": "acme/widgets"},
    }
    if installation_id is not None:
        payload["installation"] = {"id": installation_id}
    return payload


def test_github_webhook_ignores_non_onboarded_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_overrides(_payload(), delivery_id="p2-1")

    async def _missing_installation(self, *, repo_full_name: str):
        return None

    monkeypatch.setattr(
        "sre_agent.services.github_app_installations.GitHubAppInstallationService.get_by_repo_full_name",
        _missing_installation,
    )

    res = client.post("/webhooks/github")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ignored"
    assert "not onboarded" in body["message"]


def test_github_webhook_ignores_installation_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_overrides(_payload(installation_id=222), delivery_id="p2-2")

    async def _installation(self, *, repo_full_name: str):
        return type(
            "Install",
            (),
            {
                "installation_id": 111,
                "repo_full_name": repo_full_name,
                "user_id": uuid4(),
                "automation_mode": "suggest",
            },
        )()

    monkeypatch.setattr(
        "sre_agent.services.github_app_installations.GitHubAppInstallationService.get_by_repo_full_name",
        _installation,
    )

    res = client.post("/webhooks/github")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ignored"
    assert "mismatch" in body["message"]


def test_github_webhook_injects_repo_config_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client_with_overrides(_payload(installation_id=111), delivery_id="p2-3")
    captured = {}

    async def _installation(self, *, repo_full_name: str):
        return type(
            "Install",
            (),
            {
                "installation_id": 111,
                "repo_full_name": repo_full_name,
                "user_id": uuid4(),
                "automation_mode": "suggest",
            },
        )()

    async def _resolve_repo_config(self, **_kwargs):
        return RepositoryRuntimeConfig(
            automation_mode="auto_pr",
            protected_paths=["infra/**"],
            retry_limit=5,
            source="repo_file",
        )

    async def _record_delivery(self, **_kwargs):
        return True

    def _normalize(self, payload, correlation_id, event_type="workflow_job"):
        captured["payload"] = payload
        return type(
            "E",
            (),
            {
                "repo": "acme/widgets",
                "idempotency_key": "k1",
                "failure_type": "test",
            },
        )()

    async def _store_event(self, _evt):
        return type("S", (), {"id": uuid4()})(), True

    async def _update_status(self, *_args, **_kwargs):
        return None

    class _Redis:
        async def check_rate_limit(self, *a, **k):
            return True, 1, 0

    monkeypatch.setattr(
        "sre_agent.services.github_app_installations.GitHubAppInstallationService.get_by_repo_full_name",
        _installation,
    )
    monkeypatch.setattr(
        "sre_agent.services.repository_config.RepositoryConfigService.resolve_for_repository",
        _resolve_repo_config,
    )
    monkeypatch.setattr(
        "sre_agent.services.webhook_delivery_store.WebhookDeliveryStore.record_delivery",
        _record_delivery,
    )
    monkeypatch.setattr(
        "sre_agent.services.event_normalizer.GitHubEventNormalizer.normalize",
        _normalize,
    )
    monkeypatch.setattr("sre_agent.services.event_store.EventStore.store_event", _store_event)
    monkeypatch.setattr("sre_agent.services.event_store.EventStore.update_status", _update_status)
    monkeypatch.setattr("sre_agent.api.webhooks.github.get_redis_service", lambda: _Redis())
    monkeypatch.setattr(
        "sre_agent.api.webhooks.github.process_pipeline_event.apply_async",
        lambda *a, **k: None,
    )

    res = client.post("/webhooks/github")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "accepted"
    assert captured["payload"]["_sre_agent"]["repo_config"]["automation_mode"] == "auto_pr"
    assert captured["payload"]["_sre_agent"]["repo_config"]["retry_limit"] == 5
    assert captured["payload"]["_sre_agent"]["installation"]["installation_id"] == 111
