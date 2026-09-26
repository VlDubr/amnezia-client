import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        master_key="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        scheduler_enabled=False,
    )


@pytest.fixture
async def client(settings):
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- database -------------------------------------------------------------

import os  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.db.base import make_engine, make_sessionmaker  # noqa: E402
from app.db.migrate import upgrade_head  # noqa: E402


@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get("PANEL_TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
async def engine(pg_url):
    await upgrade_head(pg_url)
    eng = make_engine(pg_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def sessionmaker(engine):
    async with engine.begin() as conn:
        tables = (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'"
        ))).scalars().all()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    return make_sessionmaker(engine)


@pytest.fixture
async def db(sessionmaker):
    async with sessionmaker() as s:
        yield s
