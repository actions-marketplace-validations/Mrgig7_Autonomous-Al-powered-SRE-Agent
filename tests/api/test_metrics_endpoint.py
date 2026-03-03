from __future__ import annotations

from fastapi.testclient import TestClient
from sre_agent.main import create_app


def test_metrics_endpoint_returns_prometheus_text() -> None:
    app = create_app()
    client = TestClient(app)

    res_health = client.get("/health")
    assert res_health.status_code == 200

    res = client.get("/metrics")
    assert res.status_code == 200
    assert "text/plain" in (res.headers.get("content-type") or "")
    body = res.text
    assert "sre_agent_http_requests_total" in body
    assert 'route="/health"' in body
