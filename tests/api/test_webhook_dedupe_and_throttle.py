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


def test_github_webhook_duplicate_delivery_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "action": "completed",
        "workflow_job": {"conclusion": "failure"},
        "repository": {"full_name": "acme/widgets"},
    }
    client = _client_with_overrides(payload, delivery_id="dup-1")

    async def _record_delivery(self, **_kwargs):
        return False

    async def _get_installation(self, *, repo_full_name: str):
        return type(
            "Install",
            (),
            {
                "installation_id": 999,
                "repo_full_name": repo_full_name,
                "user_id": uuid4(),
                "automation_mode": "suggest",
            },
        )()

    async def _resolve_repo_config(self, **_kwargs):
        return RepositoryRuntimeConfig(
            automation_mode="suggest",
            protected_paths=[],
            retry_limit=3,
            source="installation_default",
        )

    monkeypatch.setattr(
        "sre_agent.services.webhook_delivery_store.WebhookDeliveryStore.record_delivery",
        _record_delivery,
    )
    monkeypatch.setattr(
        "sre_agent.services.github_app_installations.GitHubAppInstallationService.get_by_repo_full_name",
        _get_installation,
    )
    monkeypatch.setattr(
        "sre_agent.services.repository_config.RepositoryConfigService.resolve_for_repository",
        _resolve_repo_config,
    )

    res = client.post("/webhooks/github")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "duplicate_ignored"


def test_github_webhook_throttle_delays_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "action": "completed",
        "workflow_job": {"conclusion": "failure"},
        "repository": {"full_name": "acme/widgets"},
    }
    client = _client_with_overrides(payload, delivery_id="t-1")

    async def _record_delivery(self, **_kwargs):
        return True

    def _normalize(self, payload, correlation_id, event_type="workflow_job"):
        return type(
            "E",
            (),
            {
                "repo": "acme/widgets",
                "idempotency_key": "k1",
                "failure_type": type("X", (), {"value": "ci"})(),
            },
        )()

    async def _store_event(self, _evt):
        return type("S", (), {"id": uuid4()})(), True

    async def _update_status(self, *_args, **_kwargs):
        return None

    async def _get_installation(self, *, repo_full_name: str):
        return type(
            "Install",
            (),
            {
                "installation_id": 999,
                "repo_full_name": repo_full_name,
                "user_id": uuid4(),
                "automation_mode": "suggest",
            },
        )()

    async def _resolve_repo_config(self, **_kwargs):
        return RepositoryRuntimeConfig(
            automation_mode="suggest",
            protected_paths=[],
            retry_limit=3,
            source="installation_default",
        )

    scheduled = {}

    def _apply_async(*, args, countdown):
        scheduled["args"] = args
        scheduled["countdown"] = countdown

    class _Redis:
        async def check_rate_limit(self, *a, **k):
            return False, 999, 10

    monkeypatch.setattr(
        "sre_agent.services.webhook_delivery_store.WebhookDeliveryStore.record_delivery",
        _record_delivery,
    )
    monkeypatch.setattr(
        "sre_agent.services.event_normalizer.GitHubEventNormalizer.normalize",
        _normalize,
    )
    monkeypatch.setattr(
        "sre_agent.services.github_app_installations.GitHubAppInstallationService.get_by_repo_full_name",
        _get_installation,
    )
    monkeypatch.setattr(
        "sre_agent.services.repository_config.RepositoryConfigService.resolve_for_repository",
        _resolve_repo_config,
    )
    monkeypatch.setattr("sre_agent.services.event_store.EventStore.store_event", _store_event)
    monkeypatch.setattr("sre_agent.services.event_store.EventStore.update_status", _update_status)
    monkeypatch.setattr("sre_agent.api.webhooks.github.get_redis_service", lambda: _Redis())
    monkeypatch.setattr(
        "sre_agent.api.webhooks.github.process_pipeline_event.apply_async",
        lambda *a, **k: _apply_async(args=k.get("args"), countdown=k.get("countdown")),
    )
    monkeypatch.setattr(
        "sre_agent.api.webhooks.github.process_pipeline_event.delay",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("delay should not be called")),
    )

    res = client.post("/webhooks/github")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "throttled_delayed"
    assert scheduled["countdown"] == 10
