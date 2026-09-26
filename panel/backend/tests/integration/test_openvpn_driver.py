"""OpenVPN driver against a real amnezia-openvpn container, with a real OpenVPN client."""

import asyncio
import os
import random
import subprocess
import uuid

import pytest

from app.drivers.base import get_driver
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration
OVPN = "amnezia-openvpn"


def ip_of(container: str) -> str:
    return docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container).split()[0]


def start_client(ovpn: str, name: str) -> None:
    docker("run", "-d", "--rm", "--name", name, "--cap-add", "NET_ADMIN", "--device", "/dev/net/tun",
           "--entrypoint", "sh", OVPN, "-c", "sleep 900")
    subprocess.run(["docker", "exec", "-i", name, "sh", "-c", "cat > /tmp/c.ovpn"], input=ovpn, text=True,
                   encoding="utf-8", check=True)


def connect(name: str) -> None:
    docker("exec", name, "sh", "-c", "pkill openvpn; rm -f /tmp/c.log; "
                                     "openvpn --config /tmp/c.ovpn --daemon --log /tmp/c.log", check=False)


def log(name: str) -> str:
    return docker("exec", name, "cat", "/tmp/c.log", check=False)


async def wait_for(name: str, text: str, seconds: int = 30) -> bool:
    for _ in range(seconds):
        if text in log(name):
            return True
        await asyncio.sleep(1)
    return False


async def test_openvpn_lifecycle(sshhost):
    docker("rm", "-f", OVPN, check=False)
    client = f"panel-ovpn-client-{uuid.uuid4().hex[:6]}"
    port = str(random.randint(40000, 49999))
    try:
        async with open_remote(sshhost) as remote:
            await install_container(remote, OVPN, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), port=port, host_setup=False)
            driver = get_driver(OVPN)
            params = await driver.read_params(remote)
            assert params["port"] == port

            material = await driver.create_material(remote, params, set())
            await driver.apply(remote, [material], {material.client_id})  # prepares ccd + management
            assert material.client_id in await driver.list_clients(remote)

            rendered = driver.render(material, params, ip_of(OVPN), ("1.1.1.1", "1.0.0.1"), "it")
            start_client(rendered.native, client)
            connect(client)
            assert await wait_for(client, "Initialization Sequence Completed"), log(client)[-1500:]

            await driver.apply(remote, [], {material.client_id})  # block: ccd disable + kill
            connect(client)
            assert not await wait_for(client, "Initialization Sequence Completed", 15), "blocked client connected"

            await driver.apply(remote, [material], {material.client_id})  # unblock
            connect(client)
            assert await wait_for(client, "Initialization Sequence Completed"), log(client)[-1500:]

            await driver.apply(remote, [], {material.client_id}, revoked={material.client_id})
            assert material.client_id not in await driver.list_clients(remote)
            connect(client)
            assert not await wait_for(client, "Initialization Sequence Completed", 15), "revoked client connected"
    finally:
        if not os.environ.get("PANEL_KEEP_CONTAINERS"):
            for name in (client, OVPN):
                docker("rm", "-f", name, check=False)
