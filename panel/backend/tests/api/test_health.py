async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_unknown_route_uses_error_format(client):
    r = await client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


async def test_lifespan_runs_worker_and_scheduler(settings, sessionmaker, clock, db):
    import asyncio

    from sqlalchemy import select

    from app.db.models import Job
    from app.jobs.queue import enqueue
    from app.main import create_app

    job = await enqueue(db, "expire")
    await db.commit()
    app = create_app(settings.model_copy(update={"scheduler_enabled": True}), sessionmaker=sessionmaker, clock=clock)
    async with app.router.lifespan_context(app):
        for _ in range(50):
            await asyncio.sleep(0.1)
            status = (await db.execute(select(Job.status).where(Job.id == job.id))).scalar_one()
            await db.rollback()
            if status == "done":
                break
    assert status == "done"
