"""Migration 0004 adds load samples and server capacities."""

import asyncio

from alembic import command

from app.db.migrate import alembic_config
from tests.migration_helpers import fresh_database, run_sql


async def test_samples_follow_their_server_and_the_downgrade_removes_them(pg_url):
    url = await fresh_database(pg_url, "mig0004")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    await run_sql(url, "INSERT INTO servers (name, host, ssh_port, ssh_user, ssh_secret_enc, bandwidth_mbps, "
                       "expected_clients) VALUES ('nl', 'h', 22, 'root', 'x', 1000, 50)")
    await run_sql(url, "INSERT INTO server_samples (server_id, ts, boot_id, uptime_s, cpu_busy, cpu_total) "
                       "VALUES (1, now(), 'b', 10, 1, 2)")
    await run_sql(url, "DELETE FROM servers")
    assert await run_sql(url, "SELECT id FROM server_samples") == []

    await asyncio.to_thread(command.downgrade, alembic_config(url), "0003")
    tables = await run_sql(url, "SELECT tablename FROM pg_tables WHERE tablename = 'server_samples'")
    columns = await run_sql(url, "SELECT column_name FROM information_schema.columns "
                                 "WHERE table_name = 'servers' AND column_name = 'bandwidth_mbps'")
    assert tables == [] and columns == []
