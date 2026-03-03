from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session
