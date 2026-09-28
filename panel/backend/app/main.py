import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import admin_servers, admin_users, audit, auth, configs, jobs, me, traffic
from app.config import Settings, get_settings
from app.db.base import make_engine, make_sessionmaker
from app.domain.clock import Clock, SystemClock
from app.errors import install_error_handlers
from app.jobs.handlers import build_handlers
from app.jobs.periodic import build_scheduler
from app.jobs.queue import requeue_stale
from app.jobs.sampler import Sampler
from app.jobs.worker import Worker
from app.security.ratelimit import RateLimiter
from app.security.secretbox import SecretBox
from app.services.servers import make_remote_factory
from app.ssh.conn import fetch_host_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("asyncssh").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None,
               sessionmaker: async_sessionmaker[AsyncSession] | None = None,
               clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    owns_engine = sessionmaker is None
    engine = make_engine(settings.database_url) if owns_engine else None
    sessionmaker = sessionmaker or make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        stop = asyncio.Event()
        worker_task = sampler_task = scheduler = None
        if settings.scheduler_enabled:
            async with sessionmaker() as db:
                await requeue_stale(db)
            worker_task = asyncio.create_task(app_.state.worker.run_forever(stop))
            sampler_task = asyncio.create_task(app_.state.sampler.run_forever(stop))
            scheduler = build_scheduler(sessionmaker)
            scheduler.start()
        yield
        stop.set()
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        if sampler_task is not None:
            try:
                await asyncio.wait_for(sampler_task, 5)  # an SSH call in flight must not hold the shutdown
            except TimeoutError:
                pass
        if worker_task is not None:
            await worker_task
        if owns_engine:
            await engine.dispose()

    app = FastAPI(title="Amnezia Panel", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessionmaker = sessionmaker
    app.state.clock = clock
    app.state.secretbox = SecretBox(settings.master_key)
    app.state.remote_factory = make_remote_factory(app.state.secretbox)
    app.state.fetch_host_key = fetch_host_key
    app.state.worker = Worker(sessionmaker, build_handlers(app.state), clock)
    # The factory is looked up per call: tests and the e2e launcher replace app.state.remote_factory later.
    app.state.sampler = Sampler(engine or sessionmaker.kw["bind"], sessionmaker,
                                lambda server: app.state.remote_factory(server), clock)
    app.state.limiters = {
        "login_name": RateLimiter(5, 60, clock),
        "login_ip": RateLimiter(20, 60, clock),
        "invite_ip": RateLimiter(10, 3600, clock),
    }
    install_error_handlers(app)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(audit.router)
    app.include_router(jobs.router)
    app.include_router(admin_servers.router)
    app.include_router(admin_users.router)
    app.include_router(configs.admin_router)
    app.include_router(configs.me_router)
    app.include_router(traffic.router)
    return app
