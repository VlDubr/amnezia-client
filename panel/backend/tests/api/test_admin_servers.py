from sqlalchemy import select

from app.db.models import Config, Job, Server
from app.security.secretbox import SecretBox
from tests.conftest import MASTER_KEY, add_server, bearer, registered_user, run_jobs


async def test_add_server_imports_containers_and_clients(db, client, admin_token, app, fake_remote):
    r = await client.post("/api/admin/servers", json={"name": "nl-1", "host": "203.0.113.10", "ssh_user": "root",
                                                      "ssh_password": "secret"}, headers=bearer(admin_token))
    assert r.status_code == 202
    body = r.json()
    assert "ssh_password" not in body["server"] and "secret" not in r.text
    assert body["server"]["host_key"].startswith("ssh-ed25519")
    job = await client.get(f"/api/jobs/{body['job_id']}", headers=bearer(admin_token))
    assert job.json()["status"] == "queued"
    await run_jobs(app)
    assert (await client.get(f"/api/jobs/{body['job_id']}", headers=bearer(admin_token))).json()["status"] == "done"

    server = (await client.get(f"/api/admin/servers/{body['server']['id']}", headers=bearer(admin_token))).json()
    assert server["containers"] == [{"container": "amnezia-awg2", "title": "AmneziaWG", "port": "55424"}]
    assert server["imported_at"] is not None and server["last_error"] is None
    orphans = (await db.execute(select(Config.client_id).where(Config.user_id.is_(None)))).scalars().all()
    assert sorted(orphans) == ["pubA=", "pubB="]


async def test_ssh_secret_is_encrypted(db, client, admin_token, app, fake_remote):
    s = await add_server(client, admin_token, app, ssh_password="p@ss-123")
    row = await db.get(Server, s["id"])
    assert "p@ss-123" not in row.ssh_secret_enc
    assert b"p@ss-123" in SecretBox(MASTER_KEY).decrypt(row.ssh_secret_enc)


async def test_add_server_requires_a_credential(client, admin_token, fake_remote):
    r = await client.post("/api/admin/servers", json={"name": "x", "host": "h", "ssh_user": "root"},
                          headers=bearer(admin_token))
    assert r.status_code == 422 and r.json()["code"] == "ssh_credentials_required"


async def test_unreachable_server_is_rejected(client, admin_token, fake_remote):
    fake_remote.fail_with = "timed out"
    r = await client.post("/api/admin/servers", json={"name": "x", "host": "h", "ssh_user": "root",
                                                      "ssh_password": "p"}, headers=bearer(admin_token))
    assert r.status_code == 502 and r.json()["code"] == "server_unreachable"


async def test_list_patch_and_delete(db, client, admin_token, app, fake_remote):
    s = await add_server(client, admin_token, app)
    listing = (await client.get("/api/admin/servers", headers=bearer(admin_token))).json()
    assert [x["id"] for x in listing] == [s["id"]] and listing[0]["configs_count"] == 2
    r = await client.patch(f"/api/admin/servers/{s['id']}", json={"name": "renamed", "enabled_for_users": False},
                           headers=bearer(admin_token))
    assert r.json()["name"] == "renamed" and r.json()["enabled_for_users"] is False
    assert (await client.delete(f"/api/admin/servers/{s['id']}", headers=bearer(admin_token))).status_code == 204
    assert (await client.get(f"/api/admin/servers/{s['id']}", headers=bearer(admin_token))).status_code == 404
    assert (await db.execute(select(Config))).first() is None
    assert len(fake_remote.files) > 0  # the server itself is left untouched


async def test_sync_is_deduplicated(db, client, admin_token, app, fake_remote):
    s = await add_server(client, admin_token, app)
    a = await client.post(f"/api/admin/servers/{s['id']}/sync", headers=bearer(admin_token))
    b = await client.post(f"/api/admin/servers/{s['id']}/sync", headers=bearer(admin_token))
    assert a.status_code == 202 and a.json()["job_id"] == b.json()["job_id"]


async def test_host_key_change_fails_job_until_accepted(db, client, admin_token, app, fake_remote):
    from app.ssh.conn import HostKeyMismatch

    s = await add_server(client, admin_token, app)
    good_factory = app.state.remote_factory

    def mismatching(server):
        raise HostKeyMismatch("host key of h changed")

    app.state.remote_factory = mismatching
    job_id = (await client.post(f"/api/admin/servers/{s['id']}/sync", headers=bearer(admin_token))).json()["job_id"]
    await run_jobs(app)
    job = (await client.get(f"/api/jobs/{job_id}", headers=bearer(admin_token))).json()
    assert job["status"] == "failed" and "host_key_mismatch" in job["last_error"]
    server = (await client.get(f"/api/admin/servers/{s['id']}", headers=bearer(admin_token))).json()
    assert "host_key_mismatch" in server["last_error"]

    app.state.remote_factory = good_factory
    r = await client.post(f"/api/admin/servers/{s['id']}/host-key/accept", headers=bearer(admin_token))
    assert r.status_code == 202
    await run_jobs(app)
    server = (await client.get(f"/api/admin/servers/{s['id']}", headers=bearer(admin_token))).json()
    assert server["last_error"] is None


async def test_install_container_validates_type(client, admin_token, app, fake_remote):
    s = await add_server(client, admin_token, app)
    r = await client.post(f"/api/admin/servers/{s['id']}/containers", json={"container": "amnezia-sftp"},
                          headers=bearer(admin_token))
    assert r.status_code == 422 and r.json()["code"] == "unsupported_container"


async def test_install_container_enqueues_job(db, client, admin_token, app, fake_remote):
    s = await add_server(client, admin_token, app)
    r = await client.post(f"/api/admin/servers/{s['id']}/containers",
                          json={"container": "amnezia-wireguard", "port": "51999"}, headers=bearer(admin_token))
    assert r.status_code == 202
    job = await db.get(Job, r.json()["job_id"])
    assert job.kind == "install_container" and job.payload_json == {"container": "amnezia-wireguard",
                                                                    "port": "51999"}


async def test_user_cannot_manage_servers(db, client, fake_remote):
    _, token = await registered_user(db, client)
    assert (await client.get("/api/admin/servers", headers=bearer(token))).status_code == 403


async def test_installable_protocols(client, admin_token):
    r = await client.get("/api/admin/servers/installable", headers=bearer(admin_token))
    names = {c["container"]: c["title"] for c in r.json()}
    assert {"amnezia-awg2", "amnezia-wireguard", "amnezia-xray", "amnezia-openvpn", "amnezia-socks5proxy",
            "amnezia-telemt", "amnezia-mtproxy"} <= set(names)
    assert "amnezia-ipsec" not in names and names["amnezia-xray"] == "XRay"
