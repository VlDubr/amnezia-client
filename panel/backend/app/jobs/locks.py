from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

SERVER_LOCK_CLASS = 1


@asynccontextmanager
async def server_lock(sessionmaker: async_sessionmaker[AsyncSession], server_id: int) -> AsyncIterator[None]:
    """Serializes all SSH work on one server across the worker and API requests.

    A session-level advisory lock is held on a dedicated connection so that the caller's own
    transactions can commit freely while the lock stays taken.
    """
    engine = sessionmaker.kw["bind"]
    async with engine.connect() as conn:
        await conn.execute(select(func.pg_advisory_lock(SERVER_LOCK_CLASS, server_id)))
        await conn.commit()
        try:
            yield
        finally:
            await conn.execute(select(func.pg_advisory_unlock(SERVER_LOCK_CLASS, server_id)))
            await conn.commit()
