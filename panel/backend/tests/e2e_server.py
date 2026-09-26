"""Test-only launcher for the web end-to-end tests (panel/web/e2e).

Runs the real app against PostgreSQL (testcontainers, or PANEL_TEST_DATABASE_URL) with every VPN server
replaced by the in-memory AWG server from tests.fakes, and creates the admin `admin` / E2E_ADMIN_PASSWORD.

    uv run python -m tests.e2e_server        # listens on E2E_API_PORT (default 8765)
"""

import asyncio
import os
import sys

import uvicorn

from app.config import Settings
from app.db.base import make_engine, make_sessionmaker
from app.db.migrate import upgrade_head
from app.db.models import Admin
from app.main import create_app
from app.security.passwords import hash_password
from tests.fakes import awg_server, remote_factory_for

E2E_ADMIN_PASSWORD = "Adm1n-Pa55w0rd!xyz"
MASTER_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


async def serve(database_url: str, port: int) -> None:
    await upgrade_head(database_url)
    engine = make_engine(database_url)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as db:
        db.add(Admin(login="admin", password_hash=hash_password(E2E_ADMIN_PASSWORD)))
        await db.commit()

    settings = Settings(database_url=database_url, master_key=MASTER_KEY, cookie_secure=False,
                        scheduler_enabled=True, tz="Europe/Moscow")
    app = create_app(settings, sessionmaker=sessionmaker)
    remote = awg_server()
    app.state.remote_factory = remote_factory_for(remote)

    async def fetch_host_key(host: str, port_: int) -> str:
        return "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIE2eTestHostKey"

    app.state.fetch_host_key = fetch_host_key
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()
    await engine.dispose()


def main() -> None:
    port = int(os.environ.get("E2E_API_PORT", "8765"))
    url = os.environ.get("PANEL_TEST_DATABASE_URL")
    if url:
        asyncio.run(serve(url, port))
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        print("e2e backend: postgres ready", file=sys.stderr, flush=True)
        asyncio.run(serve(pg.get_connection_url(), port))


if __name__ == "__main__":
    main()
