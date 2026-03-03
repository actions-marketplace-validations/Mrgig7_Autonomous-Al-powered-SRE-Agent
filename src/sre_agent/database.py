"""Database connection and session management.

Production-grade database configuration with:
- Connection pooling optimized for high concurrency
- Health checks and pool monitoring
- Context managers for proper session handling
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sre_agent.config import get_settings

settings = get_settings()

# Create async engine with production pool settings
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,  # Verify connection before use
    pool_size=20,  # Base connections for high concurrency
    max_overflow=30,  # Additional connections under load
    pool_recycle=1800,  # Recycle after 30 min
    pool_timeout=30,  # Wait timeout for connection
)

# Create session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database session."""
    async with async_session_factory() as session:
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

    Usage:
        async with get_async_session() as session:
            result = await session.execute(query)
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_pool_status() -> dict:
    """Get connection pool status for monitoring."""
    pool = engine.pool

    return {
        "pool_size": pool.size(),
        "checked_in": pool.checkedin(),
        "checked_out": pool.checkedout(),
        "overflow": pool.overflow(),
    }


async def close_database() -> None:
    """Close database connections."""
    await engine.dispose()
