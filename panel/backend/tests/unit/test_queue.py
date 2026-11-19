import asyncio
from datetime import timedelta

from sqlalchemy import select

import app.jobs.queue as queue
from app.db.models import Job, Server
from app.jobs.queue import PermanentJobError, claim, enqueue, fail, finish, postpone, requeue_stale
from app.jobs.worker import Worker
from tests.conftest import bearer


async def _server(db) -> Server:
    s = Server(name="s", host="h", ssh_port=22, ssh_user="root", ssh_secret_enc="x")
    db.add(s)
    await db.commit()
    return s


async def test_enqueue_dedupes_active_jobs(db):
    a = await enqueue(db, "reconcile", dedupe_key="reconcile:1")
    b = await enqueue(db, "reconcile", dedupe_key="reconcile:1")
    await db.commit()
    assert a.id == b.id
    a.status = "done"
    await db.commit()
    c = await enqueue(db, "reconcile", dedupe_key="reconcile:1")
    await db.commit()
    assert c.id != a.id


async def test_claim_respects_run_after(db, clock):
    await enqueue(db, "x", run_after=clock.now() + timedelta(minutes=5))
    await db.commit()
    assert await claim(db, clock.now()) is None
    clock.advance(301)
    job = await claim(db, clock.now())
    assert job is not None and job.status == "running" and job.locked_at == clock.now()


async def test_fail_backs_off_then_gives_up(db, clock):
    server = await _server(db)
    job = await enqueue(db, "x", server_id=server.id)
    await db.commit()
    delays = []
    for _ in range(8):
        await fail(db, job, "boom", clock.now())
        if job.status == "queued":
            delays.append((job.run_after - clock.now()).total_seconds() / 60)
    assert delays == [1, 2, 4, 8, 16, 30, 30]
    assert job.status == "failed" and job.attempts == 8 and job.finished_at is not None
    await db.refresh(server)
    assert server.last_error == "boom"


async def test_permanent_failure_is_not_retried(db, clock):
    job = await enqueue(db, "x")
    await db.commit()
    await fail(db, job, "host key changed", clock.now(), retry=False)
    assert job.status == "failed"


async def test_finish_marks_done(db, clock):
    job = await enqueue(db, "x")
    await db.commit()
    await finish(db, job, clock.now())
    assert job.status == "done" and job.finished_at == clock.now()


async def test_requeue_stale_running_jobs(db, clock):
    await enqueue(db, "x")
    await db.commit()
    job = await claim(db, clock.now())
    await requeue_stale(db)
    await db.refresh(job)
    assert job.status == "queued"


async def test_requeue_stale_retires_running_job_with_queued_twin(db, clock):
    """Prod startup crash: requeueing a running job whose key is already queued violates the dedupe index."""
    await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    running = await claim(db, clock.now())
    queued = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await requeue_stale(db)
    await db.refresh(running)
    await db.refresh(queued)
    assert running.status == "failed" and running.finished_at is not None
    assert str(queued.id) in running.last_error
    assert queued.status == "queued"


async def test_requeue_stale_keeps_newest_of_running_twins(db, clock):
    first = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    second = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    await requeue_stale(db)
    await db.refresh(first)
    await db.refresh(second)
    assert second.status == "queued" and second.locked_at is None
    assert first.status == "failed" and str(second.id) in first.last_error


async def test_requeue_stale_requeues_jobs_without_dedupe_key(db, clock):
    for _ in range(2):
        await enqueue(db, "x")
    await db.commit()
    await claim(db, clock.now())
    await claim(db, clock.now())
    await requeue_stale(db)
    assert (await db.execute(select(Job.status))).scalars().all() == ["queued", "queued"]


async def test_fail_retires_job_with_queued_twin(db, clock):
    server = await _server(db)
    await enqueue(db, "reconcile", server_id=server.id, dedupe_key="sync:1")
    await db.commit()
    running = await claim(db, clock.now())
    queued = await enqueue(db, "reconcile", server_id=server.id, dedupe_key="sync:1")
    await db.commit()
    await fail(db, running, "ssh down", clock.now())
    await db.refresh(queued)
    assert running.status == "failed" and running.attempts == 1 and "ssh down" in running.last_error
    assert queued.status == "queued"
    await db.refresh(server)
    assert server.last_error == "ssh down"


async def test_postpone_retires_job_with_queued_twin(db, clock):
    await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    running = await claim(db, clock.now())
    queued = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await postpone(db, running, clock.now())
    await db.refresh(queued)
    assert running.status == "failed" and running.attempts == 0 and str(queued.id) in running.last_error
    assert queued.status == "queued"


async def test_worker_failure_with_queued_twin_keeps_queue_consistent(sessionmaker, db, clock):
    """The prod failure chain: a running job fails while a newer queued twin exists."""
    server = await _server(db)

    async def handler(session, job):
        async with sessionmaker() as other:
            await enqueue(other, "x", server_id=server.id, dedupe_key="sync:1")
            await other.commit()
        raise RuntimeError("ssh down")

    job = await enqueue(db, "x", server_id=server.id, dedupe_key="sync:1")
    await db.commit()
    await Worker(sessionmaker, {"x": handler}, clock).run_once()
    await db.refresh(job)
    assert job.status == "failed" and "ssh down" in job.last_error
    assert (await db.execute(select(Job.status).where(Job.id != job.id))).scalars().all() == ["queued"]


def _blind_twin_check_once(monkeypatch, blind_calls: int):
    """Simulates the race: the twin check ran before a concurrent enqueue committed its queued job."""
    real_twin = queue._queued_twin
    calls = []

    async def stale_check(db_, key):
        calls.append(key)
        if len(calls) <= blind_calls:
            return None
        return await real_twin(db_, key)

    monkeypatch.setattr(queue, "_queued_twin", stale_check)


async def test_fail_recovers_when_queued_twin_appears_mid_transition(db, clock, monkeypatch):
    server = await _server(db)
    await enqueue(db, "reconcile", server_id=server.id, dedupe_key="sync:1")
    await db.commit()
    running = await claim(db, clock.now())
    await enqueue(db, "reconcile", server_id=server.id, dedupe_key="sync:1")
    await db.commit()
    _blind_twin_check_once(monkeypatch, blind_calls=1)
    await fail(db, running, "ssh down", clock.now())
    assert running.status == "failed" and running.attempts == 1
    assert "ssh down" in running.last_error and "superseded by queued job" in running.last_error
    await db.refresh(server)
    assert server.last_error == running.last_error


async def test_postpone_recovers_when_queued_twin_appears_mid_transition(db, clock, monkeypatch):
    await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    running = await claim(db, clock.now())
    queued = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    _blind_twin_check_once(monkeypatch, blind_calls=1)
    await postpone(db, running, clock.now())
    await db.refresh(queued)
    assert running.status == "failed" and str(queued.id) in running.last_error
    assert queued.status == "queued"


async def test_requeue_stale_recovers_from_conflicting_requeue(db, clock, monkeypatch):
    first = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    second = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    _blind_twin_check_once(monkeypatch, blind_calls=2)  # the whole first pass misses the twin it creates itself
    await requeue_stale(db)
    await db.refresh(first)
    await db.refresh(second)
    assert second.status == "queued"
    assert first.status == "failed" and str(second.id) in first.last_error


async def test_requeue_stale_recovers_from_autoflush_conflict(db, clock, monkeypatch):
    """The pending flip of one job is flushed by the next job's twin SELECT, before the commit."""
    other = await enqueue(db, "traffic", dedupe_key="traffic:2")
    await db.commit()
    await claim(db, clock.now())
    stale = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    twin = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    _blind_twin_check_once(monkeypatch, blind_calls=1)  # stale (newest) is requeued blind, then other's check flushes it
    await requeue_stale(db)
    await db.refresh(stale)
    await db.refresh(other)
    await db.refresh(twin)
    assert stale.status == "failed" and str(twin.id) in stale.last_error
    assert other.status == "queued" and twin.status == "queued"


async def test_worker_runs_handler_and_marks_done(sessionmaker, db, clock):
    seen = []

    async def handler(session, job):
        seen.append(job.payload_json["n"])

    await enqueue(db, "x", {"n": 7})
    await db.commit()
    worker = Worker(sessionmaker, {"x": handler}, clock)
    assert await worker.run_once() is True
    assert await worker.run_once() is False
    assert seen == [7]
    assert (await db.execute(select(Job.status))).scalar_one() == "done"


async def test_worker_records_handler_error(sessionmaker, db, clock):
    async def handler(session, job):
        raise RuntimeError("ssh down")

    await enqueue(db, "x")
    await db.commit()
    await Worker(sessionmaker, {"x": handler}, clock).run_once()
    job = (await db.execute(select(Job))).scalar_one()
    assert job.status == "queued" and job.attempts == 1 and "ssh down" in job.last_error


async def test_worker_permanent_error(sessionmaker, db, clock):
    async def handler(session, job):
        raise PermanentJobError("host_key_mismatch")

    await enqueue(db, "x")
    await db.commit()
    await Worker(sessionmaker, {"x": handler}, clock).run_once()
    assert (await db.execute(select(Job.status))).scalar_one() == "failed"


async def test_worker_serializes_jobs_of_one_server(sessionmaker, db, clock):
    server = await _server(db)
    running = 0
    peak = 0

    async def handler(session, job):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1

    for _ in range(3):
        await enqueue(db, "x", server_id=server.id)
    await db.commit()
    worker = Worker(sessionmaker, {"x": handler}, clock, concurrency=3)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop, poll_s=0.01))
    for _ in range(100):
        await asyncio.sleep(0.02)
        clock.advance(11)  # jobs of a busy server are postponed for 10 s instead of holding a worker slot
        statuses = (await db.execute(select(Job.status).execution_options(populate_existing=True))).scalars().all()
        await db.rollback()
        if all(s == "done" for s in statuses):
            break
    stop.set()
    await task
    assert peak == 1
    assert all(s == "done" for s in statuses)


async def test_jobs_api(db, client, admin_token):
    job = await enqueue(db, "reconcile")
    await db.commit()
    r = await client.get(f"/api/jobs/{job.id}", headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json() == {"id": job.id, "kind": "reconcile", "status": "queued", "attempts": 0, "last_error": None}
    assert (await client.get("/api/jobs/999", headers=bearer(admin_token))).status_code == 404


async def test_worker_survives_its_server_being_deleted_mid_job(sessionmaker, db, clock):
    """Deleting a server cascades to its jobs; the running job's outcome then has no row to land in."""
    async def drop_server(then_raise):
        async def handler(session, job):
            async with sessionmaker() as other:
                await other.delete(await other.get(Server, server_id))
                await other.commit()
            if then_raise:
                raise RuntimeError("ssh down")
        return handler

    for then_raise in (True, False):
        server = await _server(db)
        server_id = server.id
        await enqueue(db, "x", server_id=server_id)
        await db.commit()
        assert await Worker(sessionmaker, {"x": await drop_server(then_raise)}, clock).run_once() is True
    assert (await db.execute(select(Job))).first() is None
