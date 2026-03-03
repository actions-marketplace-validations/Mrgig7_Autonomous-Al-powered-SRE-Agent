from __future__ import annotations

from fastapi import APIRouter, Response

from sre_agent.observability.metrics import render_prometheus

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    try:
        from sre_agent.core.redis_service import get_redis_service
        from sre_agent.observability.metrics import METRICS

        redis_service = get_redis_service()
        async with redis_service.get_client() as client:
            for q in ("default", "celery"):
                try:
                    depth = await client.llen(q)
                    METRICS.queue_depth.labels(queue=q).set(int(depth))
                except Exception:
                    continue
    except Exception:
        pass
    payload, content_type = render_prometheus()
    return Response(content=payload, media_type=content_type)
