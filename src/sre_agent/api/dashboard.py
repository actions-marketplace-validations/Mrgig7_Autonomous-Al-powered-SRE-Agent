"""Dashboard API with caching and optimized queries.

Production-grade dashboard endpoints for:
- Event overview and statistics
- Failure trends and analytics
- Fix success rates
- System health metrics

Optimized for high-traffic with:
- Redis caching for expensive queries
- Pagination for large datasets
- Query optimization
- Real-time updates via SSE
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.api.response_envelope import success_response
from sre_agent.auth.jwt_handler import TokenPayload
from sre_agent.auth.permissions import get_current_user, require_permission
from sre_agent.auth.rbac import Permission
from sre_agent.config import get_settings
from sre_agent.database import get_db_session
from sre_agent.models.events import EventStatus, PipelineEvent
from sre_agent.services.onboarding_state import OnboardingStateService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# =========================================
# RESPONSE MODELS
# =========================================


class EventSummary(BaseModel):
    """Summary of a pipeline event."""

    id: str
    repository: str
    branch: str
    status: str
    ci_provider: str
    created_at: str
    error_snippet: Optional[str] = None


class OverviewStats(BaseModel):
    """Dashboard overview statistics."""

    total_events: int
    failures_24h: int
    fixes_generated_24h: int
    fixes_approved_24h: int
    success_rate_7d: float
    avg_fix_time_minutes: float


class TrendPoint(BaseModel):
    """A single point in a trend line."""

    date: str
    count: int
    success_count: int = 0
    failure_count: int = 0


class RepoStats(BaseModel):
    """Statistics for a repository."""

    repository: str
    total_events: int
    failures: int
    fixes_generated: int
    fixes_approved: int
    success_rate: float
    last_event_at: Optional[str] = None


class DashboardOverview(BaseModel):
    """Complete dashboard overview response."""

    stats: OverviewStats
    recent_failures: list[EventSummary]
    pending_approvals: int
    active_fixes: int


class PaginatedEventsResponse(BaseModel):
    """Paginated events response."""

    events: list[EventSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


# =========================================
# CACHING HELPERS
# =========================================

CACHE_TTL = 60  # 1 minute cache for dashboard data


async def get_cached_or_compute(
    cache_key: str,
    compute_fn,
    ttl: int = CACHE_TTL,
) -> Any:
    """Get from cache or compute and cache."""
    try:
        from sre_agent.core.redis_service import get_redis_service

        redis = get_redis_service()
        cached = await redis.cache_get(cache_key)
        if cached is not None:
            return cached

        result = await compute_fn()
        await redis.cache_set(cache_key, result, ttl)
        return result

    except Exception as e:
        logger.warning(f"Cache error: {e}, computing directly")
        return await compute_fn()


# =========================================
# ENDPOINTS
# =========================================


@router.get(
    "/overview",
    response_model=dict[str, Any],
    summary="Get dashboard overview",
    dependencies=[Depends(require_permission(Permission.VIEW_DASHBOARD))],
)
async def get_overview(
    current_user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Get complete dashboard overview with stats and recent activity."""
    settings = get_settings()
    if not settings.phase1_enable_dashboard:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Phase 1 dashboard is disabled",
        )

    onboarding_state = OnboardingStateService()
    await onboarding_state.update_state(
        user_id=current_user.user_id,
        dashboard_ready=True,
    )

    async def compute():
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)

        # Get overview stats
        stats_result = await session.execute(
            select(
                func.count(PipelineEvent.id).label("total"),
                func.sum(
                    case(
                        (
                            and_(
                                PipelineEvent.created_at >= yesterday,
                                PipelineEvent.status == EventStatus.FAILED.value,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ).label("failures_24h"),
            )
        )
        stats_row = stats_result.one()

        # Recent failures
        failures_result = await session.execute(
            select(PipelineEvent)
            .where(PipelineEvent.status == EventStatus.FAILED.value)
            .order_by(PipelineEvent.created_at.desc())
            .limit(10)
        )
        recent_failures = failures_result.scalars().all()

        return {
            "stats": {
                "total_events": stats_row.total or 0,
                "failures_24h": stats_row.failures_24h or 0,
                "fixes_generated_24h": 0,  # Would come from fixes table
                "fixes_approved_24h": 0,
                "success_rate_7d": 85.0,  # Placeholder
                "avg_fix_time_minutes": 12.5,  # Placeholder
            },
            "recent_failures": [
                {
                    "id": str(e.id),
                    "repository": e.repo,
                    "branch": e.branch,
                    "status": e.status,
                    "ci_provider": e.ci_provider,
                    "created_at": e.created_at.isoformat(),
                    "error_snippet": e.error_message[:200] if e.error_message else None,
                }
                for e in recent_failures
            ],
            "pending_approvals": 0,
            "active_fixes": 0,
        }

    data = await get_cached_or_compute(
        f"dashboard:overview:{current_user.user_id}",
        compute,
        ttl=30,  # 30 second cache
    )

    return success_response(DashboardOverview(**data).model_dump())


@router.get(
    "/events",
    response_model=PaginatedEventsResponse,
    summary="Get paginated events",
    dependencies=[Depends(require_permission(Permission.VIEW_FAILURES))],
)
async def get_events(
    status: Optional[str] = None,
    repository: Optional[str] = None,
    branch: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedEventsResponse:
    """Get paginated pipeline events with filtering."""
    query = select(PipelineEvent)
    count_query = select(func.count(PipelineEvent.id))

    conditions = []

    if status:
        conditions.append(PipelineEvent.status == status)
    if repository:
        conditions.append(PipelineEvent.repo.ilike(f"%{repository}%"))
    if branch:
        conditions.append(PipelineEvent.branch == branch)
    if start_date:
        conditions.append(PipelineEvent.created_at >= start_date)
    if end_date:
        conditions.append(PipelineEvent.created_at <= end_date)

    if conditions:
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    # Get total count
    total_result = await session.execute(count_query)
    total = total_result.scalar_one()

    # Get events
    query = query.order_by(PipelineEvent.created_at.desc())
    query = query.limit(limit).offset(offset)

    result = await session.execute(query)
    events = result.scalars().all()

    return PaginatedEventsResponse(
        events=[
            EventSummary(
                id=str(e.id),
                repository=e.repo,
                branch=e.branch,
                status=e.status,
                ci_provider=e.ci_provider,
                created_at=e.created_at.isoformat(),
                error_snippet=e.error_message[:200] if e.error_message else None,
            )
            for e in events
        ],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
    )


@router.get(
    "/trends",
    response_model=list[TrendPoint],
    summary="Get event trends",
    dependencies=[Depends(require_permission(Permission.VIEW_ANALYTICS))],
)
async def get_trends(
    days: int = Query(7, ge=1, le=90),
    repository: Optional[str] = None,
    session: AsyncSession = Depends(get_db_session),
) -> list[TrendPoint]:
    """Get daily event trends for charts."""
    cache_key = f"dashboard:trends:{days}:{repository or 'all'}"

    async def compute():
        now = datetime.now(UTC)
        start_date = now - timedelta(days=days)

        # Group by date
        date_col = func.date(PipelineEvent.created_at)

        query = (
            select(
                date_col.label("date"),
                func.count(PipelineEvent.id).label("count"),
                func.sum(
                    case((PipelineEvent.status == EventStatus.RESOLVED.value, 1), else_=0)
                ).label("success_count"),
                func.sum(
                    case((PipelineEvent.status == EventStatus.FAILED.value, 1), else_=0)
                ).label("failure_count"),
            )
            .where(PipelineEvent.created_at >= start_date)
            .group_by(date_col)
            .order_by(date_col)
        )

        if repository:
            query = query.where(PipelineEvent.repo.ilike(f"%{repository}%"))

        result = await session.execute(query)
        rows = result.all()

        return [
            {
                "date": str(row.date),
                "count": row.count,
                "success_count": row.success_count or 0,
                "failure_count": row.failure_count or 0,
            }
            for row in rows
        ]

    data = await get_cached_or_compute(cache_key, compute, ttl=300)  # 5 min cache
    return [TrendPoint(**d) for d in data]


@router.get(
    "/repos",
    response_model=list[RepoStats],
    summary="Get repository statistics",
    dependencies=[Depends(require_permission(Permission.VIEW_ANALYTICS))],
)
async def get_repo_stats(
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
) -> list[RepoStats]:
    """Get statistics grouped by repository."""
    cache_key = f"dashboard:repos:{limit}"

    async def compute():
        query = (
            select(
                PipelineEvent.repo,
                func.count(PipelineEvent.id).label("total_events"),
                func.sum(
                    case((PipelineEvent.status == EventStatus.FAILED.value, 1), else_=0)
                ).label("failures"),
                func.max(PipelineEvent.created_at).label("last_event_at"),
            )
            .group_by(PipelineEvent.repo)
            .order_by(func.count(PipelineEvent.id).desc())
            .limit(limit)
        )

        result = await session.execute(query)
        rows = result.all()

        return [
            {
                "repository": row.repo,
                "total_events": row.total_events,
                "failures": row.failures or 0,
                "fixes_generated": 0,  # Placeholder
                "fixes_approved": 0,  # Placeholder
                "success_rate": round(
                    (
                        ((row.total_events - (row.failures or 0)) / row.total_events * 100)
                        if row.total_events > 0
                        else 0
                    ),
                    1,
                ),
                "last_event_at": row.last_event_at.isoformat() if row.last_event_at else None,
            }
            for row in rows
        ]

    data = await get_cached_or_compute(cache_key, compute, ttl=120)  # 2 min cache
    return [RepoStats(**d) for d in data]


@router.get(
    "/health",
    summary="Get system health metrics",
    dependencies=[Depends(require_permission(Permission.VIEW_DASHBOARD))],
)
async def get_system_health() -> dict[str, Any]:
    """Get system health metrics for monitoring."""
    from sre_agent.core.redis_service import get_redis_service
    from sre_agent.services.audit_service import get_audit_service

    health = {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "components": {},
    }

    # Redis health
    try:
        redis = get_redis_service()
        health["components"]["redis"] = await redis.health_check()
    except Exception as e:
        health["components"]["redis"] = {"status": "unhealthy", "error": str(e)}
        health["status"] = "degraded"

    # Audit service
    try:
        audit = get_audit_service()
        health["components"]["audit"] = audit.get_stats()
    except Exception as e:
        health["components"]["audit"] = {"status": "error", "error": str(e)}

    # Database (simple check)
    try:
        from sre_agent.database import get_async_session

        async with get_async_session() as session:
            await session.execute(select(1))
        health["components"]["database"] = {"status": "healthy"}
    except Exception as e:
        health["components"]["database"] = {"status": "unhealthy", "error": str(e)}
        health["status"] = "unhealthy"

    return health


# =========================================
# SERVER-SENT EVENTS FOR REAL-TIME
# =========================================


@router.get(
    "/stream",
    summary="Real-time event stream",
    dependencies=[Depends(require_permission(Permission.VIEW_DASHBOARD))],
)
async def stream_events(
    request: Request,
    current_user: TokenPayload = Depends(get_current_user),
):
    """Server-Sent Events stream for real-time dashboard updates.

    Clients can subscribe to receive:
    - New failure notifications
    - Fix generation updates
    - Approval status changes
    """
    import asyncio
    import json

    async def event_generator():
        """Generate SSE events."""
        from sre_agent.core.redis_service import get_redis_service

        try:
            redis = get_redis_service()

            # Subscribe to dashboard events channel
            events_received = asyncio.Queue()

            async def handler(data):
                await events_received.put(data)

            await redis.subscribe("dashboard_events", handler)

            # Send initial connection event
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.now(UTC).isoformat()})}\n\n"

            # Stream events
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(
                        events_received.get(),
                        timeout=30.0,
                    )
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now(UTC).isoformat()})}\n\n"

        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
