"""MTProxy driver against a real amnezia-mtproxy container: a panel secret reaches mtproto-proxy's arguments."""

import os
import random

import pytest

from app.drivers.base import get_driver
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration
MT = "amnezia-mtproxy"


async def proxy_args(remote) -> str:
    """The mtproto-proxy command line the deployed start.sh builds (the binary itself is not run: in this test
    environment it cannot load Telegram's proxy-secret, with or without the panel)."""
    script = (await remote.read_container_file(MT, "/opt/amnezia/start.sh")).replace(
        "exec mtproto-proxy", "echo mtproto-proxy")
    return docker("run", "--rm", "--volumes-from", MT, "--entrypoint", "sh", MT, "-c", script, check=False)


async def test_mtproxy_secrets(sshhost):
    docker("rm", "-f", MT, check=False)
    docker("volume", "rm", "-f", "amnezia-mtproxy-data", check=False)
    port = str(random.randint(40000, 49999))
    try:
        async with open_remote(sshhost) as remote:
            await install_container(remote, MT, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), port=port, host_setup=False)
            driver = get_driver(MT)
            params = await driver.read_params(remote)
            assert params["port"] == port
            assert await driver.list_clients(remote) == {}

            m = await driver.create_material(remote, params, set())
            await driver.apply(remote, [m], {m.client_id})
            assert set(await driver.list_clients(remote)) == {m.client_id}
            assert f"-S {m.client_id}" in await proxy_args(remote), "secret not passed to mtproto-proxy"

            await driver.apply(remote, [], {m.client_id})
            assert await driver.list_clients(remote) == {}
            args = await proxy_args(remote)
            assert "mtproto-proxy" in args and m.client_id not in args
    finally:
        if not os.environ.get("PANEL_KEEP_CONTAINERS"):
            docker("rm", "-f", MT, check=False)
            docker("volume", "rm", "-f", "amnezia-mtproxy-data", check=False)
