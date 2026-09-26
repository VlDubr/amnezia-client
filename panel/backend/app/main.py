from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import audit, auth, me
from app.config import Settings, get_settings
from app.db.base import make_engine, make_sessionmaker
from app.domain.clock import Clock, SystemClock
from app.errors import install_error_handlers
from app.security.ratelimit import RateLimiter
from app.security.secretbox import SecretBox


def create_app(settings: Settings | None = None,
               sessionmaker: async_sessionmaker[AsyncSession] | None = None,
               clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    owns_engine = sessionmaker is None
    engine = make_engine(settings.database_url) if owns_engine else None
    sessionmaker = sessionmaker or make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owns_engine:
            await engine.dispose()

    app = FastAPI(title="Amnezia Panel", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.settings = settings
    app.state.sessionmaker = sessionmaker
    app.state.clock = clock
    app.state.secretbox = SecretBox(settings.master_key)
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
    return app
