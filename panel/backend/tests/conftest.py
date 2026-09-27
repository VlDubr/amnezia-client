import os
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import Settings
from app.db.base import make_engine, make_sessionmaker
from app.db.migrate import upgrade_head
from app.db.models import ROLE_ADMIN, InviteKey, User
from app.domain.clock import FixedClock
from app.main import create_app
from app.security.passwords import hash_password
from app.security.tokens import new_invite_key, normalize_invite_key, sha256_hex

MASTER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
ADMIN_PASSWORD = "Adm1n-Pa55w0rd!xyz"
USER_PASSWORD = "Us3r-Pa55w0rd!qwe"

# --- database -------------------------------------------------------------


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


# --- app ------------------------------------------------------------------


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 9, 26, 12, tzinfo=UTC))


@pytest.fixture
def settings(pg_url) -> Settings:
    return Settings(database_url=pg_url, master_key=MASTER_KEY, scheduler_enabled=False, cookie_secure=False,
                    tz="Europe/Moscow")


@pytest.fixture
def app(settings, sessionmaker, clock):
    return create_app(settings, sessionmaker=sessionmaker, clock=clock)


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# --- factories ------------------------------------------------------------


async def make_admin(db, login="admin", password=ADMIN_PASSWORD) -> User:
    admin = User(role=ROLE_ADMIN, display_name=login, login=login, password_hash=hash_password(password),
                 max_configs=0)
    db.add(admin)
    await db.commit()
    return admin


async def make_user(db, display_name="Ivan", max_configs=3, **kw) -> tuple[User, str]:
    user = User(display_name=display_name, max_configs=max_configs, **kw)
    db.add(user)
    await db.flush()
    key = new_invite_key()
    db.add(InviteKey(user_id=user.id, key_hash=sha256_hex(normalize_invite_key(key))))
    await db.commit()
    return user, key


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def login(client, login_, password, role) -> str:
    r = await client.post("/api/auth/login", json={"login": login_, "password": password})
    assert r.status_code == 200 and r.json()["role"] == role, r.text
    client.cookies.clear()
    return r.json()["token"]


@pytest.fixture
async def admin_token(db, client):
    await make_admin(db)
    return await login(client, "admin", ADMIN_PASSWORD, "admin")


async def registered_user(db, client, login_="ivan", **kw) -> tuple[User, str]:
    user, key = await make_user(db, **kw)
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": login_, "password": USER_PASSWORD})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return user, r.json()["token"]


# --- fake servers ---------------------------------------------------------


@pytest.fixture
def fake_remote(app):
    """Every server the app talks to is this in-memory AWG server."""
    from tests.fakes import awg_server, remote_factory_for

    remote = awg_server()
    app.state.remote_factory = remote_factory_for(remote)

    async def fetch_host_key(host, port):
        remote._check()
        return "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestHostKey"

    app.state.fetch_host_key = fetch_host_key
    return remote


async def run_jobs(app) -> int:
    n = 0
    while await app.state.worker.run_once():
        n += 1
    return n


async def add_server(client, admin_token, app, **overrides) -> dict:
    body = {"name": "nl-1", "host": "203.0.113.10", "ssh_user": "root", "ssh_password": "secret", **overrides}
    r = await client.post("/api/admin/servers", json=body, headers=bearer(admin_token))
    assert r.status_code == 202, r.text
    await run_jobs(app)
    return r.json()["server"]
