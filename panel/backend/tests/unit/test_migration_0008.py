"""Migration 0008: client_host and ssh_jump_enc columns added, nullable, and removable."""

import asyncio

from alembic import command

from app.db.migrate import alembic_config
from tests.migration_helpers import fresh_database as _fresh_database
from tests.migration_helpers import run_sql as _run


async def test_0008_adds_nullable_relay_and_jump_columns(pg_url):
    url = await _fresh_database(pg_url, "mig0008_add")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0007")
    await _run(url, "INSERT INTO servers (name, host, ssh_port, ssh_user, ssh_secret_enc) "
                    "VALUES ('s', 'h', 22, 'r', 'x')")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    rows = await _run(url, "SELECT client_host, ssh_jump_enc FROM servers")
    assert rows[0].client_host is None and rows[0].ssh_jump_enc is None
    await _run(url, "UPDATE servers SET client_host = '89.37.193.163', ssh_jump_enc = 'sealed'")
    rows = await _run(url, "SELECT client_host, ssh_jump_enc FROM servers")
    assert rows[0].client_host == "89.37.193.163" and rows[0].ssh_jump_enc == "sealed"


async def test_0008_downgrade_removes_the_columns(pg_url):
    url = await _fresh_database(pg_url, "mig0008_down")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    await asyncio.to_thread(command.downgrade, alembic_config(url), "0007")
    cols = await _run(url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'servers'")
    names = {r.column_name for r in cols}
    assert "client_host" not in names and "ssh_jump_enc" not in names
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
