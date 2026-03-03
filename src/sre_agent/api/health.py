"""Health check endpoints for monitoring and load balancer probes."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sre_agent.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthStatus(BaseModel):
    """Health check response."""

    status: Literal["healthy", "unhealthy", "degraded"]
    version: str
    database: Literal["connected", "disconnected"]
    details: dict | None = None


@router.get("/health", response_model=HealthStatus)
async def health_check() -> HealthStatus:
    """
    Basic health check endpoint.

    Used by load balancers and container orchestrators.
    Returns 200 if the service is running.
    """
    from sre_agent import __version__

    return HealthStatus(
        status="healthy",
        version=__version__,
        database="connected",  # Optimistic for basic health
    )


@router.get("/health/ready", response_model=HealthStatus)
async def readiness_check(
    session: AsyncSession = Depends(get_db_session),
) -> HealthStatus:
    """
    Readiness check with dependency verification.

    Checks database connectivity. Used by Kubernetes readiness probes.
    """
    from sre_agent import __version__

    db_status: Literal["connected", "disconnected"] = "disconnected"
    details = {}

    # Check database
    try:
        result = await session.execute(text("SELECT 1"))
        result.scalar()
        db_status = "connected"
    except Exception as e:
        logger.warning("Database health check failed", extra={"error": str(e)})
        details["database_error"] = str(e)

    # Determine overall status
    if db_status == "connected":
        status: Literal["healthy", "unhealthy", "degraded"] = "healthy"
    else:
        status = "unhealthy"

    return HealthStatus(
        status=status,
        version=__version__,
        database=db_status,
        details=details if details else None,
    )


@router.get("/health/live")
async def liveness_check() -> dict:
    """
    Liveness check - minimal endpoint.

    Used by Kubernetes liveness probes. Returns 200 if process is alive.
    """
    return {"alive": True}
