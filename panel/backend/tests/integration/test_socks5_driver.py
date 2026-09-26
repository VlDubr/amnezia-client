"""SOCKS5 driver against a real amnezia-socks5proxy (3proxy) container."""

import asyncio
import os
import random
import subprocess
import urllib.parse
import uuid

import pytest

from app.drivers.base import get_driver
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration
S5 = "amnezia-socks5proxy"


def ip_of(container: str) -> str:
    return docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container).split()[0]


def fetch(web: str, proxy_url: str, target: str) -> bool:
    r = subprocess.run(["docker", "exec", web, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time",
                        "6", "--proxy", proxy_url.replace("socks5://", "socks5h://"), target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() == "200"


async def eventually(check, expected: bool, attempts: int = 10) -> bool:
    for _ in range(attempts):
        if check() == expected:
            return True
        await asyncio.sleep(1)
    return False


async def test_socks5_lifecycle(sshhost):
    docker("rm", "-f", S5, check=False)
    web = f"panel-nginx-{uuid.uuid4().hex[:6]}"
    docker("run", "-d", "--rm", "--name", web, "nginx:alpine")
    docker("exec", web, "apk", "add", "--no-cache", "curl", check=False)
    port = str(random.randint(40000, 49999))
    try:
        async with open_remote(sshhost) as remote:
            await install_container(remote, S5, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), port=port, host_setup=False)
            driver = get_driver(S5)
            params = await driver.read_params(remote)
            assert params["port"] == port
            material = await driver.create_material(remote, params, set())
            await driver.apply(remote, [material], {material.client_id})

            link = driver.render(material, params, ip_of(S5), ("1.1.1.1", "1.0.0.1"), "it").native
            target = f"http://{ip_of(web)}/"
            assert await eventually(lambda: fetch(web, link, target), True), "user cannot use the proxy"
            wrong = link.replace(urllib.parse.quote(material.data["secret"], safe=""), "wrong-password")
            assert not fetch(web, wrong, target), "wrong password accepted"

            await driver.apply(remote, [], {material.client_id})
            assert await eventually(lambda: fetch(web, link, target), False), "blocked user still works"

            await driver.apply(remote, [material], {material.client_id})
            assert await eventually(lambda: fetch(web, link, target), True), "unblocked user cannot connect"
            traffic = await driver.read_traffic(remote)
            assert traffic[material.client_id].tx > 0, traffic  # bytes sent to the client, from the 3proxy log
    finally:
        if not os.environ.get("PANEL_KEEP_CONTAINERS"):
            for name in (web, S5):
                docker("rm", "-f", name, check=False)
