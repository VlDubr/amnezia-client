"""Xray driver against a real amnezia-xray container; a real Xray client fetches a page through it."""

import asyncio
import json
import os
import random
import subprocess
import uuid

import pytest

from app.drivers.base import get_driver
from app.render.vpnkey import decode_vpn_key
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration
XRAY = "amnezia-xray"


def ip_of(container: str) -> str:
    return docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container).split()[0]


def fetch_through(client: str, url: str) -> bool:
    r = subprocess.run(["docker", "exec", client, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                        "--max-time", "8", "--socks5-hostname", "127.0.0.1:10808", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() in ("200", "204")


def start_client(client_json: str) -> str:
    name = f"panel-xray-client-{uuid.uuid4().hex[:6]}"
    docker("run", "-d", "--rm", "--name", name, "--entrypoint", "sh", XRAY, "-c", "sleep 600")
    subprocess.run(["docker", "exec", "-i", name, "sh", "-c", "cat > /tmp/client.json"], input=client_json,
                   text=True, encoding="utf-8", check=True)
    docker("exec", "-d", name, "sh", "-c", "xray -config /tmp/client.json > /tmp/xray.log 2>&1")
    return name


TLS_SITE = "panel.test"
# nginx serving plain HTTP (the page fetched through the tunnel) and TLS 1.3 (the REALITY camouflage target).
# A local target keeps the test independent of how this machine reaches the internet.
NGINX_TLS = (
    "apk add --no-cache openssl >/dev/null && "
    "openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 2 "
    f"-subj /CN={TLS_SITE} -keyout /etc/nginx/k.pem -out /etc/nginx/c.pem 2>/dev/null && "
    "printf 'server { listen 443 ssl; ssl_protocols TLSv1.3; ssl_certificate /etc/nginx/c.pem; "
    "ssl_certificate_key /etc/nginx/k.pem; location / { return 200 ok; } }' > /etc/nginx/conf.d/tls.conf && "
    "nginx -g 'daemon off;'"
)


async def point_reality_at(remote, target_ip: str) -> None:
    conf = json.loads(await remote.read_container_file(XRAY, "/opt/amnezia/xray/server.json"))
    rs = conf["inbounds"][0]["streamSettings"]["realitySettings"]
    rs["dest"] = f"{target_ip}:443"
    rs["serverNames"] = [TLS_SITE]
    await remote.write_container_file(XRAY, "/opt/amnezia/xray/server.json", json.dumps(conf, indent=2))
    await remote.run(f"sudo docker restart {XRAY}")


async def test_xray_lifecycle(sshhost):
    docker("rm", "-f", XRAY, check=False)
    port = str(random.randint(40000, 49999))
    web = f"panel-nginx-{uuid.uuid4().hex[:6]}"
    docker("run", "-d", "--rm", "--name", web, "--entrypoint", "sh", "nginx:alpine", "-c", NGINX_TLS)
    client = None
    try:
        async with open_remote(sshhost) as remote:
            await install_container(remote, XRAY, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), port=port, host_setup=False)
            driver = get_driver(XRAY)
            await point_reality_at(remote, ip_of(web))
            params = await driver.read_params(remote)
            assert params["port"] == port
            material = await driver.create_material(remote, params, set())
            existing = set(await driver.list_clients(remote))
            await driver.apply(remote, [material], {material.client_id})
            assert material.client_id in await driver.list_clients(remote)
            assert existing <= set(await driver.list_clients(remote))  # the install's own client is kept

            rendered = driver.render(material, params, ip_of(XRAY), ("1.1.1.1", "1.0.0.1"), "it")
            client_json = json.loads(decode_vpn_key(rendered.vpn_key)["containers"][0]["xray"]["last_config"])
            client = start_client(json.dumps(client_json))

            # Xray v26 refuses private destinations and this machine resolves public names to 198.18.x, so the
            # proof of access is the server accepting the client: its per-user counters grow.
            async def accepted() -> bool:
                fetch_through(client, "http://www.gstatic.com/generate_204")
                c = (await driver.read_traffic(remote)).get(material.client_id)
                return bool(c and c.rx > 0)

            async def wait_accepted(expected: bool, attempts: int = 10) -> bool:
                for _ in range(attempts):
                    if await accepted() == expected:
                        return True
                    await asyncio.sleep(1)
                return False

            assert await wait_accepted(True), "server did not accept the new client"

            await driver.apply(remote, [], {material.client_id})
            await asyncio.sleep(2)
            assert await wait_accepted(False, 3) and not await accepted(), "blocked client still accepted"

            await driver.apply(remote, [material], {material.client_id})
            assert await wait_accepted(True), "unblocked client not accepted"
    finally:
        if not os.environ.get("PANEL_KEEP_CONTAINERS"):  # set to debug a failure by hand
            for name in (client, web, XRAY):
                if name:
                    docker("rm", "-f", name, check=False)
