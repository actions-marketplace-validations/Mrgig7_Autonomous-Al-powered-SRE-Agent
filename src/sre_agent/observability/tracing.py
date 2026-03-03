from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.context import attach, detach
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span

_INITIALIZED = False


def init_tracing(*, service_name: str) -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    traces_endpoint = ""
    if endpoint:
        endpoint = endpoint.rstrip("/")
        traces_endpoint = f"{endpoint}/v1/traces"

    resource = Resource.create(
        {
            "service.name": service_name,
        }
    )

    provider = TracerProvider(resource=resource)
    if traces_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=traces_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception:
            pass

    trace.set_tracer_provider(provider)
    _INITIALIZED = True


def instrument_fastapi(app: Any) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        return


def get_trace_ids() -> tuple[str | None, str | None]:
    span = trace.get_current_span()
    if span is None:
        return None, None
    ctx = span.get_span_context()
    if not ctx or not ctx.is_valid:
        return None, None
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    carrier: dict[str, str] = {}
    if headers:
        carrier.update({k: str(v) for k, v in headers.items()})
    propagate.inject(carrier)
    return carrier


def extract_trace_context(headers: dict[str, Any] | None) -> Any:
    if not headers:
        return None
    carrier = {str(k): str(v) for k, v in headers.items()}
    return propagate.extract(carrier)


@contextmanager
def start_span(
    name: str,
    *,
    context: Any | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    tracer = trace.get_tracer("sre_agent")
    with tracer.start_as_current_span(name, context=context) as span:
        if attributes:
            for k, v in attributes.items():
                if v is None:
                    continue
                span.set_attribute(k, v)
        yield span


@contextmanager
def attach_context(headers: dict[str, Any] | None) -> Iterator[None]:
    ctx = extract_trace_context(headers)
    token = attach(ctx) if ctx is not None else None
    try:
        yield
    finally:
        if token is not None:
            detach(token)
