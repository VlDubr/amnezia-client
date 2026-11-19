"""Migration 0006 scrubs Xray server private keys (REALITY and inline TLS) persisted in params_json."""

import asyncio
import json

from alembic import command

from app.db.migrate import alembic_config
from tests.migration_helpers import fresh_database as _fresh_database
from tests.migration_helpers import run_sql as _run


def _params(value) -> dict:
    return json.loads(value) if isinstance(value, str) else value


async def test_xray_private_keys_are_scrubbed_and_other_params_survive(pg_url):
    url = await _fresh_database(pg_url, "mig0006_ok")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0005")
    await _run(url, "INSERT INTO servers (name, host, ssh_port, ssh_user, ssh_secret_enc) "
                    "VALUES ('s', 'h', 22, 'r', 'x')")  # gets id 1
    params = {"port": "443", "flow": "xtls-rprx-vision",
              "stream": {"network": "tcp", "security": "reality",
                         "realitySettings": {"privateKey": "SERVER-SECRET", "serverNames": ["site"]}}}
    await _run(url, "INSERT INTO server_containers (server_id, container, params_json) VALUES "
                    "(1, 'amnezia-xray', :xray), (1, 'amnezia-awg2', :awg)",
               xray=json.dumps(params), awg=json.dumps({"port": "55424"}))

    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")

    rows = await _run(url, "SELECT container, params_json FROM server_containers ORDER BY container")
    by_container = {r.container: _params(r.params_json) for r in rows}
    xray = by_container["amnezia-xray"]
    assert "privateKey" not in xray["stream"]["realitySettings"]
    assert xray["stream"]["realitySettings"]["serverNames"] == ["site"]
    assert xray["port"] == "443" and xray["flow"] == "xtls-rprx-vision"
    assert by_container["amnezia-awg2"] == {"port": "55424"}


async def test_tls_certificate_keys_are_scrubbed(pg_url):
    url = await _fresh_database(pg_url, "mig0006_tls")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0005")
    await _run(url, "INSERT INTO servers (name, host, ssh_port, ssh_user, ssh_secret_enc) "
                    "VALUES ('s', 'h', 22, 'r', 'x')")
    tls_params = {"port": "443", "stream": {"network": "tcp", "security": "tls",
                                            "tlsSettings": {"serverName": "site",
                                                            "certificates": [{"key": ["TLS-SECRET"]}]}}}
    await _run(url, "INSERT INTO server_containers (server_id, container, params_json) "
                    "VALUES (1, 'amnezia-xray', :tls)", tls=json.dumps(tls_params))

    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")

    rows = await _run(url, "SELECT params_json FROM server_containers")
    params = _params(rows[0].params_json)
    assert "certificates" not in params["stream"]["tlsSettings"]
    assert params["stream"]["tlsSettings"]["serverName"] == "site" and params["port"] == "443"


async def test_scrub_is_a_noop_without_the_key(pg_url):
    url = await _fresh_database(pg_url, "mig0006_noop")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    await _run(url, "INSERT INTO servers (name, host, ssh_port, ssh_user, ssh_secret_enc) "
                    "VALUES ('s', 'h', 22, 'r', 'x')")
    await _run(url, "INSERT INTO server_containers (server_id, container, params_json) "
                    "VALUES (1, 'amnezia-xray', '{\"port\": \"443\"}')")
    await asyncio.to_thread(command.downgrade, alembic_config(url), "0005")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
    rows = await _run(url, "SELECT params_json FROM server_containers")
    assert _params(rows[0].params_json) == {"port": "443"}
