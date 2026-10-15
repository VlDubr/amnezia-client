"""Migration 0005 gives one-time keys a purpose and an expiry."""

import asyncio

from alembic import command

from app.db.migrate import alembic_config
from tests.migration_helpers import fresh_database, run_sql


async def test_existing_keys_become_invites_and_the_downgrade_drops_reset_keys(pg_url):
    url = await fresh_database(pg_url, "mig0005")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0004")
    await run_sql(url, "INSERT INTO users (display_name, max_configs) VALUES ('Ivan', 3)")
    await run_sql(url, "INSERT INTO invite_keys (user_id, key_hash) VALUES (1, 'a')")

    await asyncio.to_thread(command.upgrade, alembic_config(url), "0005")
    assert await run_sql(url, "SELECT purpose, expires_at FROM invite_keys") == [("invite", None)]
    await run_sql(url, "INSERT INTO invite_keys (user_id, key_hash, purpose, expires_at) "
                       "VALUES (1, 'b', 'reset', now())")

    await asyncio.to_thread(command.downgrade, alembic_config(url), "0004")
    assert await run_sql(url, "SELECT key_hash FROM invite_keys") == [("a",)]
