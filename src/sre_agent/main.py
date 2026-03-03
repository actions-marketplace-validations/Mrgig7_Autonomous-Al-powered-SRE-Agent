"""FastAPI application entry point.

This is the main application module that configures and starts
the SRE Agent API server.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sre_agent import __version__
from sre_agent.api.artifacts import router as artifacts_router
from sre_agent.api.auth import router as auth_router
from sre_agent.api.consensus import router as consensus_router
from sre_agent.api.dashboard import router as dashboard_router
from sre_agent.api.explainability import router as explainability_router
from sre_agent.api.health import router as health_router
from sre_agent.api.integration import router as integration_router
from sre_agent.api.metrics import router as metrics_router
from sre_agent.api.notifications import router as notifications_router
from sre_agent.api.user_repos import router as user_repos_router
from sre_agent.api.users import router as users_router
from sre_agent.api.webhooks.azuredevops import router as azuredevops_router
from sre_agent.api.webhooks.circleci import router as circleci_router
from sre_agent.api.webhooks.github import router as github_router
from sre_agent.api.webhooks.gitlab import router as gitlab_router
from sre_agent.api.webhooks.jenkins import router as jenkins_router
from sre_agent.config import get_settings
from sre_agent.core.logging import setup_logging
from sre_agent.core.redis_service import init_redis, shutdown_redis
from sre_agent.database import close_database
from sre_agent.notifications.factory import (
    get_notification_manager,
    shutdown_notification_manager,
)
from sre_agent.services.audit_service import init_audit_service, shutdown_audit_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager for startup/shutdown events."""
    # Startup
    setup_logging()
    from sre_agent.observability.tracing import init_tracing

    init_tracing(service_name="sre-agent-api")
    settings = get_settings()
    logger.info(
        "SRE Agent starting",
        extra={
            "version": __version__,
            "environment": settings.environment,
        },
    )

    # Initialize Redis for caching and rate limiting
    await init_redis()
    logger.info("Redis service initialized")

    # Initialize audit service
    await init_audit_service()
    logger.info("Audit service initialized")

    # Initialize notification manager
    notification_manager = get_notification_manager(settings)
    notifier_count = len(notification_manager.list_notifiers())
    logger.info(
        f"Notification system initialized with {notifier_count} channels",
        extra={"channels": notification_manager.list_notifiers()},
    )

    yield

    # Shutdown
    logger.info("SRE Agent shutting down")
    await shutdown_audit_service()
    await shutdown_notification_manager()
    await shutdown_redis()
    await close_database()
    logger.info("SRE Agent shutdown complete")


def create_app() -> FastAPI:
    """
    Application factory for creating the FastAPI app.

    Returns:
        Configured FastAPI application
    """
    settings = get_settings()

    app = FastAPI(
        title="SRE Agent",
        description="Self-Healing CI/CD Platform - Autonomous AI-powered SRE Agent",
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from sre_agent.observability.middleware import (
        CorrelationIdMiddleware,
        PrometheusMetricsMiddleware,
    )
    from sre_agent.observability.tracing import instrument_fastapi

    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(PrometheusMetricsMiddleware)
    instrument_fastapi(app)

    # Exception handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Handle Pydantic validation errors."""
        logger.warning(
            "Request validation failed",
            extra={"errors": exc.errors(), "path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors()},
        )

    # Include routers
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(github_router)
    app.include_router(gitlab_router)
    app.include_router(circleci_router)
    app.include_router(jenkins_router)
    app.include_router(azuredevops_router)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(user_repos_router, prefix=settings.api_prefix)
    app.include_router(integration_router, prefix=settings.api_prefix)
    app.include_router(notifications_router, prefix=settings.api_prefix)
    app.include_router(users_router, prefix=settings.api_prefix)
    app.include_router(dashboard_router, prefix=settings.api_prefix)
    app.include_router(artifacts_router, prefix=settings.api_prefix)
    app.include_router(explainability_router, prefix=settings.api_prefix)
    app.include_router(consensus_router, prefix=settings.api_prefix)

    # Root endpoint
    @app.get("/")
    async def root() -> dict:
        """Root endpoint with API info."""
        return {
            "name": "SRE Agent",
            "version": __version__,
            "docs": "/docs" if not settings.is_production else None,
        }

    return app


# Create the application instance
app = create_app()
