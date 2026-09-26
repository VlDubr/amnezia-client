"""WireGuard-family driver against real Amnezia containers built from client/server_scripts."""

import asyncio
import random

import pytest

from app.drivers import wgconf
from app.drivers.base import get_driver
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration


async def _peer_keys(remote, driver) -> set[str]:
    out = await remote.container_exec(driver.container, f"{driver.bin} show {driver.iface} peers")
    return set(out.split())


@pytest.mark.parametrize("container", ["amnezia-awg2", "amnezia-wireguard"])
async def test_driver_lifecycle(sshhost, container):
    docker("rm", "-f", container, check=False)
    port = str(random.randint(40000, 49999))
    try:
        async with open_remote(sshhost) as remote:
            await install_container(remote, container, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), port=port,
                                    host_setup=False)
            driver = get_driver(container)
            for _ in range(30):  # start.sh brings the interface up in the background
                if (await remote.container_exec(container, f"{driver.bin} show {driver.iface} 2>&1 || true")).strip():
                    break
                await asyncio.sleep(1)
            params = await driver.read_params(remote)
            assert params["port"] == port and params["server_public_key"]

            m = await driver.create_material(remote, params, taken=set())
            await driver.apply(remote, [m], known_ids={m.client_id})
            assert m.client_id in await _peer_keys(remote, driver)

            await driver.apply(remote, [], known_ids={m.client_id})
            assert m.client_id not in await _peer_keys(remote, driver)

            await driver.apply(remote, [m], known_ids={m.client_id})
            assert m.client_id in await _peer_keys(remote, driver)
            assert m.client_id in await driver.list_clients(remote)

            traffic = await driver.read_traffic(remote)
            assert traffic[m.client_id].rx == 0

            rendered = driver.render(m, params, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), "test")
            client_conf = wgconf.parse(rendered.native)
            assert client_conf.interface_values()["PrivateKey"] == m.data["private_key"]
            assert client_conf.peers[0].public_key == params["server_public_key"]
    finally:
        docker("rm", "-f", container, check=False)
