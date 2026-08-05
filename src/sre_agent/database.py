"""Database connection and session management.

Production-grade database configuration with:
- Connection pooling optimized for high concurrency
- Health checks and pool monitoring
- Context managers for proper session handling

The engine and session factory are created lazily so that environment-driven
``Settings`` changes (e.g. tests pointing at an in-memory SQLite URL) are
respected. Use ``reset_engine()`` in tests after monkey-patching the URL.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sre_agent.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    """Construct the async engine from the current Settings."""
    settings = get_settings()
    url = settings.database_url

    # SQLite (used by tests) doesn't accept pool_size/max_overflow/recycle.
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=settings.debug, future=True)

    return create_async_engine(
        url,
        echo=settings.debug,
        pool_pre_ping=True,
        pool_size=20,
        max_overflow=30,
        pool_recycle=1800,
        pool_timeout=30,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async sessionmaker, creating it on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


async def reset_engine() -> None:
    """Dispose of the current engine and forget cached factories.

    Useful for tests that swap ``DATABASE_URL`` at runtime.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_async_session() -> AsyncIterator[AsyncSession]:
    """Context manager for database sessions.

    Usage::

        async with get_async_session() as session:
            result = await session.execute(query)
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_pool_status() -> dict:
    """Get connection pool status for monitoring."""
    pool = get_engine().pool
    return {
        "pool_size": getattr(pool, "size", lambda: 0)(),
        "checked_in": getattr(pool, "checkedin", lambda: 0)(),
        "checked_out": getattr(pool, "checkedout", lambda: 0)(),
        "overflow": getattr(pool, "overflow", lambda: 0)(),
    }


async def close_database() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def __getattr__(name: str) -> Any:
    """Module-level proxy so ``from sre_agent.database import engine``
    (or ``async_session_factory``) keeps working and triggers lazy init.
    """
    if name == "engine":
        return get_engine()
    if name == "async_session_factory":
        return get_sessionmaker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
