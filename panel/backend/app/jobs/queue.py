"""Job queue stored in PostgreSQL (spec §8)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.db.models import Job, Server

MAX_ATTEMPTS = 8
MAX_BACKOFF_MIN = 30
BUSY_RETRY = timedelta(seconds=10)
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


async def _queued_twin(db: AsyncSession, dedupe_key: str | None) -> Job | None:
    """The queued job that already covers this work; uq_jobs_queued_dedupe allows at most one per key."""
    if dedupe_key is None:
        return None
    return (await db.execute(
        select(Job).where(Job.status == "queued", Job.dedupe_key == dedupe_key)
    )).scalar_one_or_none()


def _is_dedupe_conflict(exc: IntegrityError) -> bool:
    return "uq_jobs_queued_dedupe" in str(exc.orig)


async def fail(db: AsyncSession, job: Job | None, error: str, now: datetime, retry: bool = True) -> None:
    if job is None:  # deleted with its server while it ran
        return
    job_id = job.id
    for attempt in range(2):
        try:
            await _record_failure(db, job, error, now, retry)
            return
        except IntegrityError as e:
            # A concurrent transition or enqueue won the dedupe index after our twin check.
            if not _is_dedupe_conflict(e):
                raise
            await db.rollback()
            job = await db.get(Job, job_id)
            if job is None:  # deleted with its server while it ran
                return
            twin = await _queued_twin(db, job.dedupe_key)
            if twin is not None:
                await _record_failure(db, job, f"{error[:1900]} (superseded by queued job {twin.id})", now,
                                      retry=False)
                return
            if attempt == 1:
                raise


async def _record_failure(db: AsyncSession, job: Job, error: str, now: datetime, retry: bool) -> None:
    job.attempts += 1
    job.last_error = error[:2000]
    # A queued twin already carries the work forward; requeueing this job too would violate the dedupe index.
    if retry and job.attempts < MAX_ATTEMPTS and await _queued_twin(db, job.dedupe_key) is None:
        job.status = "queued"
        job.run_after = now + timedelta(minutes=min(2 ** (job.attempts - 1), MAX_BACKOFF_MIN))
    else:
        job.status = "failed"
        job.finished_at = now
    if job.server_id is not None:
        await db.execute(update(Server).where(Server.id == job.server_id).values(last_error=job.last_error))
    await db.commit()


async def postpone(db: AsyncSession, job: Job | None, now: datetime) -> None:
    """Puts a claimed job back without counting an attempt (its server is busy)."""
    if job is None:
        return
    job_id = job.id
    for attempt in range(2):
        twin = await _queued_twin(db, job.dedupe_key)
        job.locked_at = None
        if twin is None:
            job.status = "queued"
            job.run_after = now + BUSY_RETRY
        else:
            job.status = "failed"
            job.finished_at = now
            job.last_error = f"superseded by queued job {twin.id}"
        try:
            await db.commit()
            return
        except IntegrityError as e:
            # A concurrent transition or enqueue won the dedupe index after our twin check.
            if not _is_dedupe_conflict(e):
                raise
            await db.rollback()
            job = await db.get(Job, job_id)
            if job is None:
                return
            if attempt == 1:
                raise


async def requeue_stale(db: AsyncSession) -> None:
    """Jobs left 'running' by a crashed process go back to the queue; the panel runs one backend process.

    A queued job with the same dedupe_key already covers the work, so stale twins are retired instead of
    requeued: putting them all back at once would violate uq_jobs_queued_dedupe and block startup.
    """
    for attempt in range(2):
        try:
            stale = (await db.execute(
                select(Job).where(Job.status == "running").order_by(Job.id.desc())
            )).scalars().all()
            for job in stale:
                twin = await _queued_twin(db, job.dedupe_key)
                job.locked_at = None
                if twin is None:
                    job.status = "queued"
                else:
                    job.status = "failed"
                    job.finished_at = func.now()
                    job.last_error = f"superseded by queued job {twin.id} after restart"
            await db.commit()
            return
        except IntegrityError as e:
            # The conflict can surface at an autoflush inside the loop, not only at the commit.
            if not _is_dedupe_conflict(e):
                raise
            await db.rollback()
            if attempt == 1:
                raise
