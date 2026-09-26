import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.db.models import Job, Server
from app.jobs.queue import PermanentJobError, claim, enqueue, fail, finish, requeue_stale
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
