"""Migration 0007 adds per-container render options, empty for existing rows."""

import asyncio
import json

from alembic import command

from app.db.migrate import alembic_config
from tests.migration_helpers import fresh_database as _fresh_database
from tests.migration_helpers import run_sql as _run


def _params(value) -> dict:
    return json.loads(value) if isinstance(value, str) else value


async def test_render_options_column_defaults_to_empty_and_round_trips(pg_url):
    url = await _fresh_database(pg_url, "mig0007_ok")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0006")
    await _run(url, "INSERT INTO servers (name, host, ssh_port, ssh_user, ssh_secret_enc) "
                    "VALUES ('s', 'h', 22, 'r', 'x')")
    await _run(url, "INSERT INTO server_containers (server_id, container, params_json) "
                    "VALUES (1, 'amnezia-xray', '{\"port\": \"443\"}')")

    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")

    rows = await _run(url, "SELECT render_options_json FROM server_containers")
    assert _params(rows[0].render_options_json) == {}
    await _run(url, "UPDATE server_containers SET render_options_json = :opts",
               opts=json.dumps({"mobile_fingerprint": "ios"}))
    rows = await _run(url, "SELECT render_options_json FROM server_containers")
    assert _params(rows[0].render_options_json) == {"mobile_fingerprint": "ios"}

    await asyncio.to_thread(command.downgrade, alembic_config(url), "0006")
    columns = await _run(url, "SELECT column_name FROM information_schema.columns "
                              "WHERE table_name = 'server_containers' AND column_name = 'render_options_json'")
    assert columns == []
