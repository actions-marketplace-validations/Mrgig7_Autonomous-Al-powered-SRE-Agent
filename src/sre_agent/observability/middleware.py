from __future__ import annotations

import time
from collections.abc import Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from sre_agent.core.logging import correlation_id_ctx, delivery_id_ctx
from sre_agent.observability.metrics import observe_http_request


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        incoming = (
            request.headers.get("x-correlation-id") or request.headers.get("x-request-id") or None
        )
        value = (incoming or str(uuid4())).strip()
        token_corr = correlation_id_ctx.set(value)
        token_deliv = delivery_id_ctx.set(value)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-Id"] = value
            return response
        finally:
            correlation_id_ctx.reset(token_corr)
            delivery_id_ctx.reset(token_deliv)


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        started = time.perf_counter()
        response: Response
        status_code: int
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration = max(0.0, time.perf_counter() - started)
            route_obj = request.scope.get("route")
            route = getattr(route_obj, "path", None) or request.url.path
            observe_http_request(
                method=request.method,
                route=str(route),
                status=str(status_code),
                duration_seconds=duration,
            )
