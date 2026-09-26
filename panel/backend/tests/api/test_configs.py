import asyncio
import json

from sqlalchemy import func, select

from app.db.models import Config, User
from app.render.vpnkey import decode_vpn_key
from tests.conftest import add_server, bearer, registered_user, run_jobs
from tests.fakes import peers_on

AWG = "amnezia-awg2"


async def _setup(db, client, admin_token, app, max_configs=3):
    server = await add_server(client, admin_token, app)
    user, token = await registered_user(db, client, max_configs=max_configs)
    return server, user.id, token


async def _create(client, token, server_id, name=None):
    body = {"server_id": server_id, "container": AWG}
    if name:
        body["name"] = name
    return await client.post("/api/me/configs", json=body, headers=bearer(token))


async def _count(db, user_id) -> int:
    return (await db.execute(select(func.count()).select_from(Config).where(Config.user_id == user_id))).scalar_one()


async def test_user_sees_enabled_servers(db, client, admin_token, app, fake_remote):
    server, _, token = await _setup(db, client, admin_token, app)
    r = await client.get("/api/me/servers", headers=bearer(token))
    assert r.json() == [{"id": server["id"], "name": "nl-1",
                         "containers": [{"container": AWG, "title": "AmneziaWG"}]}]
    await client.patch(f"/api/admin/servers/{server['id']}", json={"enabled_for_users": False},
                       headers=bearer(admin_token))
    assert (await client.get("/api/me/servers", headers=bearer(token))).json() == []
    assert (await _create(client, token, server["id"])).status_code == 404


async def test_create_and_export_config(db, client, admin_token, app, fake_remote):
    server, uid, token = await _setup(db, client, admin_token, app)
    r = await _create(client, token, server["id"], name="Phone")
    assert r.status_code == 201, r.text
    cfg = r.json()
    assert cfg["name"] == "Phone" and cfg["status"] == "active" and cfg["protocol"] == "AmneziaWG"
    assert cfg["client_id"] in peers_on(fake_remote)

    detail = (await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(token))).json()
    export = detail["export"]
    assert export["native"].startswith("[Interface]") and "Address = 10.8.1.3/32" in export["native"]
    assert export["qr_svg"].startswith("<svg") and export["native_filename"] == "nl-1.conf"
    doc = decode_vpn_key(export["vpn_key"])
    assert doc["hostName"] == "203.0.113.10" and doc["description"] == "nl-1"
    assert json.loads(doc["containers"][0]["awg"]["last_config"])["clientId"] == cfg["client_id"]

    me = (await client.get("/api/me", headers=bearer(token))).json()
    assert me["configs_count"] == 1
    listing = (await client.get("/api/me/configs", headers=bearer(token))).json()
    assert [c["id"] for c in listing] == [cfg["id"]]


async def test_export_is_repeatable(db, client, admin_token, app, fake_remote):
    server, _, token = await _setup(db, client, admin_token, app)
    cfg = (await _create(client, token, server["id"])).json()
    first = (await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(token))).json()["export"]
    second = (await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(token))).json()["export"]
    assert first["native"] == second["native"]


async def test_limit_is_enforced_under_concurrency(db, client, admin_token, app, fake_remote):
    server, uid, token = await _setup(db, client, admin_token, app, max_configs=2)
    assert (await _create(client, token, server["id"])).status_code == 201
    results = await asyncio.gather(_create(client, token, server["id"]), _create(client, token, server["id"]))
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409]
    assert [r.json()["code"] for r in results if r.status_code == 409] == ["config_limit"]
    assert await _count(db, uid) == 2


async def test_unreachable_server_creates_nothing(db, client, admin_token, app, fake_remote):
    server, uid, token = await _setup(db, client, admin_token, app)
    fake_remote.fail_with = "connection timed out"
    r = await _create(client, token, server["id"])
    assert r.status_code == 503 and r.json()["code"] == "server_unavailable"
    assert await _count(db, uid) == 0


async def test_blocked_user_cannot_create_or_export(db, client, admin_token, app, fake_remote):
    server, uid, token = await _setup(db, client, admin_token, app)
    cfg = (await _create(client, token, server["id"])).json()
    await client.post(f"/api/admin/users/{uid}/block", headers=bearer(admin_token))
    r = await _create(client, token, server["id"])
    assert r.status_code == 403 and r.json()["code"] == "user_blocked"
    r = await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(token))
    assert r.status_code == 403 and r.json()["code"] == "user_blocked"
    listing = (await client.get("/api/me/configs", headers=bearer(token))).json()
    assert listing[0]["status"] == "inactive"


async def test_expired_user_gets_user_expired(db, client, admin_token, app, fake_remote):
    server, uid, token = await _setup(db, client, admin_token, app)
    await client.patch(f"/api/admin/users/{uid}", json={"expires_on": "2026-01-01"}, headers=bearer(admin_token))
    r = await _create(client, token, server["id"])
    assert r.status_code == 403 and r.json()["code"] == "user_expired"


async def test_user_blocks_and_unblocks_own_config(db, client, admin_token, app, fake_remote):
    server, _, token = await _setup(db, client, admin_token, app)
    cfg = (await _create(client, token, server["id"])).json()
    r = await client.post(f"/api/me/configs/{cfg['id']}/block", headers=bearer(token))
    assert r.json()["status"] == "blocked" and r.json()["blocked_by"] == "user"
    await run_jobs(app)
    assert cfg["client_id"] not in peers_on(fake_remote)
    r = await client.post(f"/api/me/configs/{cfg['id']}/unblock", headers=bearer(token))
    assert r.json()["status"] == "active"
    await run_jobs(app)
    assert cfg["client_id"] in peers_on(fake_remote)


async def test_user_cannot_unblock_admin_block(db, client, admin_token, app, fake_remote):
    server, _, token = await _setup(db, client, admin_token, app)
    cfg = (await _create(client, token, server["id"])).json()
    await client.post(f"/api/admin/configs/{cfg['id']}/block", headers=bearer(admin_token))
    r = await client.post(f"/api/me/configs/{cfg['id']}/unblock", headers=bearer(token))
    assert r.status_code == 403 and r.json()["code"] == "blocked_by_admin"
    r = await client.post(f"/api/me/configs/{cfg['id']}/block", headers=bearer(token))
    assert r.json()["blocked_by"] == "admin"
    r = await client.post(f"/api/admin/configs/{cfg['id']}/unblock", headers=bearer(admin_token))
    assert r.json()["status"] == "active"


async def test_user_deletes_config_and_frees_limit(db, client, admin_token, app, fake_remote):
    server, uid, token = await _setup(db, client, admin_token, app, max_configs=1)
    cfg = (await _create(client, token, server["id"])).json()
    assert (await client.delete(f"/api/me/configs/{cfg['id']}", headers=bearer(token))).status_code == 202
    assert (await client.get("/api/me/configs", headers=bearer(token))).json() == []
    await run_jobs(app)
    assert cfg["client_id"] not in peers_on(fake_remote)
    assert (await _create(client, token, server["id"])).status_code == 201


async def test_users_cannot_touch_each_others_configs(db, client, admin_token, app, fake_remote):
    server, _, token = await _setup(db, client, admin_token, app)
    cfg = (await _create(client, token, server["id"])).json()
    _, other = await registered_user(db, client, login_="petr", display_name="Petr")
    assert (await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(other))).status_code == 404
    assert (await client.delete(f"/api/me/configs/{cfg['id']}", headers=bearer(other))).status_code == 404
    assert (await client.post(f"/api/me/configs/{cfg['id']}/block", headers=bearer(other))).status_code == 404


async def test_admin_creates_config_for_user_and_lists_orphans(db, client, admin_token, app, fake_remote):
    server, uid, _ = await _setup(db, client, admin_token, app)
    r = await client.post(f"/api/admin/users/{uid}/configs", json={"server_id": server["id"], "container": AWG},
                          headers=bearer(admin_token))
    assert r.status_code == 201 and r.json()["user_id"] == uid
    detail = (await client.get(f"/api/admin/configs/{r.json()['id']}", headers=bearer(admin_token))).json()
    assert detail["export"]["vpn_key"].startswith("vpn://")
    orphans = (await client.get("/api/admin/configs?orphan=true", headers=bearer(admin_token))).json()
    assert sorted(o["client_id"] for o in orphans) == ["pubA=", "pubB="]
    orphan = (await client.get(f"/api/admin/configs/{orphans[0]['id']}", headers=bearer(admin_token))).json()
    assert orphan["export"] is None and orphan["can_render"] is False


async def test_assign_orphan_respects_limit(db, client, admin_token, app, fake_remote):
    server, uid, _ = await _setup(db, client, admin_token, app, max_configs=1)
    orphans = (await client.get("/api/admin/configs?orphan=true", headers=bearer(admin_token))).json()
    r = await client.post(f"/api/admin/configs/{orphans[0]['id']}/assign", json={"user_id": uid},
                          headers=bearer(admin_token))
    assert r.status_code == 200 and r.json()["user_id"] == uid
    r = await client.post(f"/api/admin/configs/{orphans[1]['id']}/assign", json={"user_id": uid},
                          headers=bearer(admin_token))
    assert r.status_code == 409 and r.json()["code"] == "config_limit"


async def test_admin_deletes_config(db, client, admin_token, app, fake_remote):
    await _setup(db, client, admin_token, app)
    orphans = (await client.get("/api/admin/configs?orphan=true", headers=bearer(admin_token))).json()
    victim = next(o for o in orphans if o["client_id"] == "pubA=")
    assert (await client.delete(f"/api/admin/configs/{victim['id']}", headers=bearer(admin_token))).status_code == 202
    await run_jobs(app)
    assert peers_on(fake_remote) == {"pubB="}
    assert (await client.get(f"/api/admin/configs/{victim['id']}", headers=bearer(admin_token))).status_code == 404


async def test_new_config_avoids_ips_of_blocked_configs(db, client, admin_token, app, fake_remote):
    server, _, token = await _setup(db, client, admin_token, app)
    first = (await _create(client, token, server["id"])).json()
    await client.post(f"/api/me/configs/{first['id']}/block", headers=bearer(token))
    await run_jobs(app)
    second = (await _create(client, token, server["id"])).json()
    ip_first = "10.8.1.3"
    detail = (await client.get(f"/api/me/configs/{second['id']}", headers=bearer(token))).json()
    assert f"Address = {ip_first}/32" not in detail["export"]["native"]
    user = (await db.execute(select(User))).scalar_one()
    assert user.max_configs == 3


async def test_service_export_has_qr_of_the_link(db, client, admin_token, app, fake_remote, monkeypatch):
    from app.drivers.base import Rendered, get_driver

    server, _, token = await _setup(db, client, admin_token, app)
    cfg = (await _create(client, token, server["id"])).json()
    driver = get_driver(AWG)
    monkeypatch.setattr(driver, "render", lambda *a, **k: Rendered("", "tg://proxy?server=h&port=1&secret=ee00",
                                                                   "t.txt"))
    export = (await client.get(f"/api/me/configs/{cfg['id']}", headers=bearer(token))).json()["export"]
    assert export["vpn_key"] == "" and export["native"].startswith("tg://")
    from app.render.qr import qr_svg

    assert export["qr_svg"] == qr_svg("tg://proxy?server=h&port=1&secret=ee00")  # QR of the link, not of ""


async def test_full_server_gets_a_clear_error(db, client, admin_token, app, fake_remote, monkeypatch):
    from app.drivers.base import get_driver

    server, uid, token = await _setup(db, client, admin_token, app)

    async def no_addresses(*a, **k):
        raise RuntimeError("no free addresses left in 10.8.1.0/24")

    monkeypatch.setattr(get_driver(AWG), "create_material", no_addresses)
    r = await _create(client, token, server["id"])
    assert r.status_code == 409 and r.json()["code"] == "server_full"
    assert await _count(db, uid) == 0
