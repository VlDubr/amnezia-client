"""The load sampler (load spec §4): samples every server once a minute, outside the job queue.

Only one process samples: it holds a session-level PostgreSQL advisory lock on a dedicated connection for as long
as it runs. The lock is checked before each tick and every few seconds during it; losing the connection cancels
the tick and gives the lock up, so another process can take over.
"""

import asyncio
import contextlib
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Server
from app.domain.clock import Clock
from app.services.metrics import sample_server
from app.services.servers import RemoteFactory

SAMPLER_LOCK_KEY = 0x70616E656C6C6F61  # "panelloa"
log = logging.getLogger("panel.sampler")


class Sampler:
    def __init__(self, engine: AsyncEngine, sessionmaker: async_sessionmaker[AsyncSession],
                 remote_factory: RemoteFactory, clock: Clock, interval_s: float = 60.0, concurrency: int = 8,
                 watchdog_s: float = 10.0):
        self._engine = engine
        self._sessionmaker = sessionmaker
        self._remote_factory = remote_factory
        self._clock = clock
        self._interval_s = interval_s
        self._concurrency = concurrency
        self._watchdog_s = watchdog_s
        self._leader: AsyncConnection | None = None
        self._pending: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(concurrency)  # shared by ticks and kicks
        self._in_flight: set[int] = set()  # a server is never sampled twice at once

    @property
    def is_leader(self) -> bool:
        return self._leader is not None

    async def acquire(self) -> bool:
        if self._leader is not None:
            return True
        conn = await self._engine.connect()
        try:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": SAMPLER_LOCK_KEY})).scalar()
        except Exception:  # noqa: BLE001 - no database now: try again next tick
            await self._discard(conn)
            return False
        if not got:
            await conn.close()
            return False
        self._leader = conn
        return True

    async def alive(self) -> bool:
        if self._leader is None:
            return False
        try:
            # A stalled connection must not hold the watchdog: no answer in time counts as a lost lock.
            await asyncio.wait_for(self._leader.execute(text("SELECT 1")), self._watchdog_s)
            return True
        except Exception:  # noqa: BLE001 - the lock went with the connection, or it cannot be trusted
            await self.drop()
            return False

    async def drop(self) -> None:
        """Gives leadership up without unlocking: the connection is discarded, never returned to the pool."""
        conn, self._leader = self._leader, None
        if conn is not None:
            await self._discard(conn)

    @staticmethod
    async def _discard(conn: AsyncConnection) -> None:
        with contextlib.suppress(Exception):
            await conn.invalidate()
        with contextlib.suppress(Exception):
            await conn.close()

    async def release(self) -> None:
        conn, self._leader = self._leader, None
        if conn is None:
            return
        try:
            await asyncio.wait_for(conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": SAMPLER_LOCK_KEY}), 5)
            await conn.close()
        except Exception:  # noqa: BLE001
            await self._discard(conn)

    async def _sample_one(self, server_id: int) -> None:
        if server_id in self._in_flight:
            return
        self._in_flight.add(server_id)
        try:
            async with self._slots:
                await sample_server(self._sessionmaker, server_id, self._remote_factory, self._clock)
        except Exception:
            log.exception("sampling server %s failed", server_id)
        finally:
            self._in_flight.discard(server_id)

    async def _sample_all(self, server_ids: list[int]) -> None:
        await asyncio.gather(*(self._sample_one(i) for i in server_ids))

    async def _cancel_pending(self) -> None:
        tasks = list(self._pending)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def tick(self) -> None:
        if not await self.acquire() or not await self.alive():
            return
        async with self._sessionmaker() as db:
            server_ids = list((await db.execute(select(Server.id).order_by(Server.id))).scalars())
        work = asyncio.create_task(self._sample_all(server_ids))
        try:
            while not work.done():
                await asyncio.wait({work}, timeout=self._watchdog_s)
                if not work.done() and not await self.alive():
                    log.warning("sampler lost its database lock; this tick was cancelled")
                    await self._cancel_pending()
                    return
            await work
        finally:
            # Also when the tick itself is cancelled (shutdown): no sample may outlive the lock.
            if not work.done():
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await work

    def kick(self, server_id: int) -> None:
        """Samples a newly added server now instead of at the next tick (only in the owning process)."""
        if self._leader is None:
            return
        task = asyncio.create_task(self._sample_one(server_id))  # same slots and per-server exclusion as ticks
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def run_forever(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        try:
            while not stop.is_set():
                started = loop.time()
                try:
                    await self.tick()  # an overrun delays the next tick; ticks never overlap
                except Exception:
                    log.exception("sampler tick failed")
                    await self.drop()
                remaining = self._interval_s - (loop.time() - started)
                if remaining > 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), remaining)
        finally:
            await self._cancel_pending()
            await self.release()
