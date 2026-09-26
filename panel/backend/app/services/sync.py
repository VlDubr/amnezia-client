"""Scheduling reconcile runs after a change of desired state."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, Job
from app.jobs.queue import enqueue


async def enqueue_server_sync(db: AsyncSession, server_id: int, kind: str = "reconcile") -> Job:
    return await enqueue(db, kind, server_id=server_id, dedupe_key=f"sync:{server_id}")


async def enqueue_user_sync(db: AsyncSession, user_id: int) -> set[int]:
    server_ids = set((await db.execute(
        select(Config.server_id).where(Config.user_id == user_id).distinct())).scalars())
    for server_id in server_ids:
        await enqueue_server_sync(db, server_id)
    return server_ids
