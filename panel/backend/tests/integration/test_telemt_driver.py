"""Telemt driver against a real amnezia-telemt container: users are added and removed, and Telemt keeps running."""

import asyncio
import os
import random

import pytest

from app.drivers.base import get_driver
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration
TM = "amnezia-telemt"


async def telemt_running() -> bool:
    for _ in range(15):
        if docker("exec", TM, "sh", "-c", "pgrep -x telemt || pidof telemt || true", check=False).strip():
            return True
        await asyncio.sleep(1)
    return False


async def test_telemt_users(sshhost):
    docker("rm", "-f", TM, check=False)
    docker("volume", "rm", "-f", "amnezia-telemt-data", check=False)
    port = str(random.randint(40000, 49999))
    try:
        async with open_remote(sshhost) as remote:
            await install_container(remote, TM, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), port=port, host_setup=False)
            driver = get_driver(TM)
            assert await telemt_running(), "telemt did not start after install"
            params = await driver.read_params(remote)
            assert params["port"] == port and params["tls"] is True
            installed = set(await driver.list_clients(remote))
            assert installed == {"amnezia"}

            m = await driver.create_material(remote, params, set())
            await driver.apply(remote, [m], {m.client_id})
            assert set(await driver.list_clients(remote)) == installed | {m.client_id}
            assert await telemt_running(), "telemt did not come back after adding a user"
            assert driver.render(m, params, "203.0.113.10", ("", ""), "it").native.startswith("tg://proxy?")

            await driver.apply(remote, [], {m.client_id})
            assert set(await driver.list_clients(remote)) == installed
            assert await telemt_running()
    finally:
        if not os.environ.get("PANEL_KEEP_CONTAINERS"):
            docker("rm", "-f", TM, check=False)
            docker("volume", "rm", "-f", "amnezia-telemt-data", check=False)
