"""Job queue stored in PostgreSQL (spec §8)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.db.models import Job, Server

MAX_ATTEMPTS = 8
MAX_BACKOFF_MIN = 30
# "Run as soon as possible": independent of any clock, so jobs are due for both real and test clocks.
RUN_NOW = datetime(1970, 1, 1, tzinfo=UTC)


class PermanentJobError(Exception):
    """A failure that retrying cannot fix (for example a changed host key)."""


async def enqueue(db: AsyncSession, kind: str, payload: dict[str, Any] | None = None, server_id: int | None = None,
                  dedupe_key: str | None = None, run_after: datetime | None = None) -> Job:
    """Adds a job to the current transaction; an active job with the same dedupe_key is reused."""
    values: dict[str, Any] = {"kind": kind, "payload_json": payload or {}, "server_id": server_id,
                              "dedupe_key": dedupe_key, "run_after": run_after or RUN_NOW}
    stmt = insert(Job).values(**values)
    if dedupe_key is not None:
        # Only a queued job absorbs new work: a running one may already have read the state it acts on.
        # A job waiting out a retry backoff is pulled forward so fresh work is not delayed by old failures.
        stmt = stmt.on_conflict_do_update(index_elements=["dedupe_key"], index_where=text("status = 'queued'"),
                                          set_={"run_after": func.least(Job.run_after, stmt.excluded.run_after)})
    job_id = (await db.execute(stmt.returning(Job.id))).scalar_one()
    job = await db.get(Job, job_id)
    await db.refresh(job)
    return job


async def claim(db: AsyncSession, now: datetime) -> Job | None:
    job = (await db.execute(
        select(Job).where(Job.status == "queued", Job.run_after <= now)
        .order_by(Job.run_after, Job.id).limit(1).with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if job is None:
        await db.rollback()
        return None
    job.status = "running"
    job.locked_at = now
    await db.commit()
    return job


async def finish(db: AsyncSession, job: Job, now: datetime) -> None:
    job.status = "done"
    job.finished_at = now
    job.last_error = None
    try:
        await db.commit()
    except StaleDataError:  # the job was deleted with its server while it ran
        await db.rollback()


async def fail(db: AsyncSession, job: Job | None, error: str, now: datetime, retry: bool = True) -> None:
    if job is None:  # deleted with its server while it ran
        return
    job.attempts += 1
    job.last_error = error[:2000]
    if retry and job.attempts < MAX_ATTEMPTS:
        job.status = "queued"
        job.run_after = now + timedelta(minutes=min(2 ** (job.attempts - 1), MAX_BACKOFF_MIN))
    else:
        job.status = "failed"
        job.finished_at = now
    if job.server_id is not None:
        await db.execute(update(Server).where(Server.id == job.server_id).values(last_error=job.last_error))
    await db.commit()


async def postpone(db: AsyncSession, job: Job | None, until: datetime) -> None:
    """Puts a claimed job back without counting an attempt (its server is busy)."""
    if job is None:
        return
    job.status = "queued"
    job.locked_at = None
    job.run_after = until
    await db.commit()


async def requeue_stale(db: AsyncSession) -> None:
    """Jobs left 'running' by a crashed process go back to the queue; the panel runs one backend process."""
    await db.execute(update(Job).where(Job.status == "running").values(status="queued", locked_at=None))
    await db.commit()
