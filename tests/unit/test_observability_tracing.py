from __future__ import annotations

from sre_agent.observability.tracing import (
    init_tracing,
    inject_trace_headers,
    start_span,
)


def test_tracing_init_and_span_creation_does_not_crash(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    init_tracing(service_name="test-service")
    with start_span("test_span"):
        headers = inject_trace_headers()
    assert isinstance(headers, dict)
