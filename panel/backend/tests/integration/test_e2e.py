"""End to end: the real app drives a real AmneziaWG container over SSH, and a real client connects with the config."""

import asyncio
import random
import subprocess
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.clock import SystemClock
from app.drivers import wgconf
from app.main import create_app
from app.services.install import install_container
from app.ssh.conn import open_remote
from tests.conftest import USER_PASSWORD, bearer, login, make_admin, ADMIN_PASSWORD
from tests.integration.conftest import SSH_PASSWORD, docker

pytestmark = pytest.mark.integration
AWG = "amnezia-awg2"


async def run_jobs(app) -> None:
    while await app.state.worker.run_once():
        pass


def peers() -> set[str]:
    return set(docker("exec", AWG, "awg", "show", "awg0", "peers").split())


@pytest.fixture
async def awg_installed(sshhost):
    docker("rm", "-f", AWG, check=False)
    async with open_remote(sshhost) as remote:
        await install_container(remote, AWG, "127.0.0.1", ("1.1.1.1", "1.0.0.1"),
                                port=str(random.randint(40000, 49999)), host_setup=False)
    for _ in range(30):
        if subprocess.run(["docker", "exec", AWG, "awg", "show", "awg0"], capture_output=True).returncode == 0:
            break
        await asyncio.sleep(1)
    yield
    docker("rm", "-f", AWG, check=False)


@pytest.fixture
async def e2e(settings, sessionmaker, db):
    app = create_app(settings, sessionmaker=sessionmaker, clock=SystemClock())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await make_admin(db)
        token = await login(client, "admin", ADMIN_PASSWORD, "admin")
        yield app, client, token


def connect_client(native: str, server_ip: str, port: str) -> str:
    """Brings up an AmneziaWG client in its own container; returns the container name."""
    conf = wgconf.parse(native)
    conf.interface_lines = [line for line in conf.interface_lines if not line.startswith("DNS")]
    peer = conf.peers[0]
    peer.lines = [line for line in peer.lines if not line.startswith(("Endpoint", "AllowedIPs"))]
    peer.lines += [f"Endpoint = {server_ip}:{port}", "AllowedIPs = 10.8.1.0/24"]
    name = f"panel-e2e-client-{uuid.uuid4().hex[:6]}"
    docker("run", "-d", "--rm", "--privileged", "--name", name, AWG)
    subprocess.run(["docker", "exec", "-i", name, "sh", "-c", "cat > /tmp/awgc.conf"], input=conf.dump(),
                   text=True, encoding="utf-8", check=True)
    docker("exec", name, "awg-quick", "up", "/tmp/awgc.conf")
    return name


async def test_full_flow(sshhost, awg_installed, e2e, db):
    app, client, admin = e2e

    r = await client.post("/api/admin/servers", headers=bearer(admin), json={
        "name": "e2e", "host": sshhost.host, "ssh_port": sshhost.port, "ssh_user": "panel",
        "ssh_password": SSH_PASSWORD})
    assert r.status_code == 202, r.text
    server_id = r.json()["server"]["id"]
    await run_jobs(app)
    server = (await client.get(f"/api/admin/servers/{server_id}", headers=bearer(admin))).json()
    assert server["last_error"] is None and [c["container"] for c in server["containers"]] == [AWG]

    created = (await client.post("/api/admin/users", headers=bearer(admin),
                                 json={"display_name": "E2E", "max_configs": 2})).json()
    r = await client.post("/api/auth/invite/redeem",
                          json={"key": created["invite_key"], "login": "e2e", "password": USER_PASSWORD})
    user_token = r.json()["token"]
    client.cookies.clear()
    uid = created["user"]["id"]

    r = await client.post("/api/me/configs", headers=bearer(user_token), json={"server_id": server_id,
                                                                              "container": AWG})
    assert r.status_code == 201, r.text
    cfg = r.json()
    assert cfg["client_id"] in peers()

    export = (await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(user_token))).json()["export"]
    server_ip = docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", AWG).split()[0]
    port = server["containers"][0]["port"]
    vpn_client = connect_client(export["native"], server_ip, port)
    try:
        subprocess.run(["docker", "exec", vpn_client, "ping", "-c", "3", "-W", "2", "10.8.1.0"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        dump = docker("exec", AWG, "awg", "show", "awg0", "latest-handshakes")
        handshakes = dict(line.split("\t") for line in dump.splitlines())
        assert int(handshakes[cfg["client_id"]]) > 0, "client could not complete a handshake"

        await client.post(f"/api/admin/servers/{server_id}/sync", headers=bearer(admin))
        from app.jobs.periodic import enqueue_periodic

        async with app.state.sessionmaker() as s:
            await enqueue_periodic(s, "traffic")
            await s.commit()
        await run_jobs(app)
        mine = (await client.get("/api/me/configs", headers=bearer(user_token))).json()
        assert mine[0]["traffic"]["rx"] > 0 and mine[0]["traffic"]["tx"] > 0
    finally:
        docker("rm", "-f", vpn_client, check=False)

    await client.post(f"/api/admin/users/{uid}/block", headers=bearer(admin))
    await run_jobs(app)
    assert cfg["client_id"] not in peers()

    await client.post(f"/api/admin/users/{uid}/unblock", headers=bearer(admin))
    await run_jobs(app)
    assert cfg["client_id"] in peers()

    await client.delete(f"/api/admin/users/{uid}", headers=bearer(admin))
    await run_jobs(app)
    assert cfg["client_id"] not in peers()
    assert (await client.get(f"/api/admin/users/{uid}", headers=bearer(admin))).status_code == 404
