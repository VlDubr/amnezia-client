"""Periodic work (spec §8): the scheduler only enqueues jobs; the worker runs them."""

from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job, Server, Session
from app.domain.clock import Clock
from app.jobs.queue import enqueue
from app.services.metrics import cleanup_samples
from app.services.sync import enqueue_server_sync

INTERVALS_S = {"expire": 60, "traffic": 300, "reconcile": 900, "cleanup": 86400}
JOB_RETENTION = timedelta(days=30)


async def enqueue_periodic(db: AsyncSession, what: str) -> None:
    if what in ("expire", "cleanup"):
        await enqueue(db, what, dedupe_key=what)
        return
    server_ids = (await db.execute(select(Server.id).order_by(Server.id))).scalars().all()
    for server_id in server_ids:
        if what == "traffic":
            await enqueue(db, "traffic", server_id=server_id, dedupe_key=f"traffic:{server_id}")
        else:
            await enqueue_server_sync(db, server_id)


async def cleanup(db: AsyncSession, clock: Clock) -> None:
    now = clock.now()
    await db.execute(delete(Session).where(or_(Session.expires_at < now, Session.revoked_at < now)))
    await db.execute(delete(Job).where(Job.status.in_(("done", "failed")), Job.finished_at < now - JOB_RETENTION))
    await db.commit()
    await cleanup_samples(db, now)


def build_scheduler(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    def add(what: str) -> None:
        async def tick() -> None:
            async with sessionmaker() as db:
                await enqueue_periodic(db, what)
                await db.commit()

        scheduler.add_job(tick, "interval", seconds=INTERVALS_S[what], id=what, coalesce=True, max_instances=1)

    for what in INTERVALS_S:
        add(what)
    return scheduler
