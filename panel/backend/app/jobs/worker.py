import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Job
from app.domain.clock import Clock
from datetime import timedelta

from app.jobs.locks import ServerBusy, server_lock
from app.jobs.queue import PermanentJobError, claim, fail, finish, postpone

BUSY_RETRY = timedelta(seconds=10)

log = logging.getLogger("panel.worker")

Handler = Callable[[AsyncSession, Job], Awaitable[None]]


class Worker:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], handlers: dict[str, Handler], clock: Clock,
                 concurrency: int = 4):
        self._sessionmaker = sessionmaker
        self._handlers = handlers
        self._clock = clock
        self._slots = asyncio.Semaphore(concurrency)

    async def _claim(self) -> Job | None:
        async with self._sessionmaker() as db:
            return await claim(db, self._clock.now())

    async def _execute(self, job: Job) -> None:
        async with self._sessionmaker() as db:
            job_id, kind = job.id, job.kind
            job = await db.get(Job, job_id)
            handler = self._handlers.get(kind)
            try:
                if handler is None:
                    raise PermanentJobError(f"no handler for job kind '{kind}'")
                if job.server_id is not None:
                    async with server_lock(self._sessionmaker, job.server_id, wait=False):
                        await handler(db, job)
                else:
                    await handler(db, job)
            except ServerBusy:
                # Do not hold a worker slot while another job works on the same server.
                await db.rollback()
                await postpone(db, await db.get(Job, job_id), self._clock.now() + BUSY_RETRY)
                return
            except PermanentJobError as e:
                await db.rollback()
                log.warning("job %s (%s) failed permanently: %s", job_id, kind, e)
                await fail(db, await db.get(Job, job_id), str(e), self._clock.now(), retry=False)
                return
            except Exception as e:  # noqa: BLE001 - any handler failure is recorded and retried
                await db.rollback()
                log.warning("job %s (%s) failed: %s", job_id, kind, e)
                await fail(db, await db.get(Job, job_id), f"{type(e).__name__}: {e}", self._clock.now())
                return
            await finish(db, job, self._clock.now())

    async def run_once(self) -> bool:
        job = await self._claim()
        if job is None:
            return False
        await self._execute(job)
        return True

    async def run_forever(self, stop: asyncio.Event, poll_s: float = 1.0) -> None:
        tasks: set[asyncio.Task] = set()
        while not stop.is_set():
            await self._slots.acquire()
            try:
                job = await self._claim()
            except Exception:
                log.exception("claiming a job failed")
                job = None
            if job is None:
                self._slots.release()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_s)
                except TimeoutError:
                    pass
                continue
            task = asyncio.create_task(self._execute(job))
            tasks.add(task)
            task.add_done_callback(lambda t: (tasks.discard(t), self._slots.release()))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
