from datetime import date, timedelta

from sqlalchemy import select

from app.db.models import Config, Job, Server, TrafficDaily, User
from tests.conftest import USER_PASSWORD, bearer, login


async def _create(client, token, **kw) -> dict:
    body = {"display_name": "Ivan Petrov", "max_configs": 3, **kw}
    r = await client.post("/api/admin/users", json=body, headers=bearer(token))
    assert r.status_code == 201, r.text
    return r.json()


async def _servers_with_configs(db, user_id) -> list[int]:
    ids = []
    for n in (1, 2):
        s = Server(name=f"s{n}", host=f"h{n}", ssh_port=22, ssh_user="root", ssh_secret_enc="x")
        db.add(s)
        await db.flush()
        db.add(Config(user_id=user_id, server_id=s.id, container="amnezia-awg2", name="c", client_id=f"k{n}"))
        ids.append(s.id)
    await db.commit()
    return ids


async def _sync_jobs(db) -> set[int]:
    return set((await db.execute(select(Job.server_id).where(Job.status == "queued"))).scalars())


async def test_create_user_returns_one_time_invite_key(client, admin_token):
    body = await _create(client, admin_token, note="VIP", expires_on="2026-12-31")
    user = body["user"]
    assert user["display_name"] == "Ivan Petrov" and user["registered"] is False and user["login"] is None
    assert user["status"] == "active" and user["expires_on"] == "2026-12-31" and user["max_configs"] == 3
    r = await client.post("/api/auth/invite/redeem",
                          json={"key": body["invite_key"], "login": "ivan", "password": USER_PASSWORD})
    assert r.status_code == 200


async def test_create_validates_input(client, admin_token):
    r = await client.post("/api/admin/users", json={"display_name": "", "max_configs": 3}, headers=bearer(admin_token))
    assert r.status_code == 422
    r = await client.post("/api/admin/users", json={"display_name": "x", "max_configs": -1},
                          headers=bearer(admin_token))
    assert r.status_code == 422


async def test_list_filters_and_search(db, client, admin_token):
    a = (await _create(client, admin_token, display_name="Anna"))["user"]
    await _create(client, admin_token, display_name="Boris")
    await client.post(f"/api/admin/users/{a['id']}/block", headers=bearer(admin_token))
    names = [u["display_name"] for u in (await client.get("/api/admin/users", headers=bearer(admin_token))).json()]
    assert names == ["Anna", "Boris"]
    blocked = (await client.get("/api/admin/users?status=blocked", headers=bearer(admin_token))).json()
    assert [u["display_name"] for u in blocked] == ["Anna"]
    found = (await client.get("/api/admin/users?q=bor", headers=bearer(admin_token))).json()
    assert [u["display_name"] for u in found] == ["Boris"]


async def test_block_and_unblock_reconcile_every_server_of_the_user(db, client, admin_token):
    user = (await _create(client, admin_token))["user"]
    servers = await _servers_with_configs(db, user["id"])
    r = await client.post(f"/api/admin/users/{user['id']}/block", headers=bearer(admin_token))
    assert r.json()["status"] == "blocked"
    assert await _sync_jobs(db) == set(servers)
    r = await client.post(f"/api/admin/users/{user['id']}/unblock", headers=bearer(admin_token))
    assert r.json()["status"] == "active"


async def test_unblocking_expired_user_keeps_expiry_block(client, admin_token, clock):
    user = (await _create(client, admin_token, expires_on=str(date(2026, 9, 1))))["user"]
    assert user["status"] == "expired"
    await client.post(f"/api/admin/users/{user['id']}/block", headers=bearer(admin_token))
    r = await client.post(f"/api/admin/users/{user['id']}/unblock", headers=bearer(admin_token))
    assert r.json()["status"] == "expired" and r.json()["blocked_by"] == "expiry"


async def test_extending_expiry_lifts_expiry_block_but_not_admin_block(db, client, admin_token):
    user = (await _create(client, admin_token, expires_on="2026-09-01"))["user"]
    assert user["blocked_by"] == "expiry"
    r = await client.patch(f"/api/admin/users/{user['id']}", json={"expires_on": "2026-12-31"},
                           headers=bearer(admin_token))
    assert r.json()["status"] == "active" and r.json()["blocked_by"] is None

    await client.post(f"/api/admin/users/{user['id']}/block", headers=bearer(admin_token))
    r = await client.patch(f"/api/admin/users/{user['id']}", json={"expires_on": "2027-06-30"},
                           headers=bearer(admin_token))
    assert r.json()["status"] == "blocked"
    r = await client.patch(f"/api/admin/users/{user['id']}", json={"expires_on": None}, headers=bearer(admin_token))
    assert r.json()["expires_on"] is None and r.json()["status"] == "blocked"


async def test_patch_fields_and_past_expiry_blocks_now(db, client, admin_token, clock):
    user = (await _create(client, admin_token))["user"]
    servers = await _servers_with_configs(db, user["id"])
    yesterday = str((clock.now() - timedelta(days=2)).date())
    r = await client.patch(f"/api/admin/users/{user['id']}",
                           json={"display_name": "New", "note": "n", "max_configs": 1, "expires_on": yesterday},
                           headers=bearer(admin_token))
    body = r.json()
    assert body["display_name"] == "New" and body["note"] == "n" and body["max_configs"] == 1
    assert body["status"] == "expired" and body["configs_count"] == 2
    assert await _sync_jobs(db) == set(servers)


async def test_reissue_invite(client, admin_token):
    created = await _create(client, admin_token)
    uid = created["user"]["id"]
    r = await client.post(f"/api/admin/users/{uid}/invite", headers=bearer(admin_token))
    new_key = r.json()["invite_key"]
    assert new_key != created["invite_key"]
    assert (await client.post("/api/auth/invite/check", json={"key": created["invite_key"]})).status_code == 404
    r = await client.post("/api/auth/invite/redeem", json={"key": new_key, "login": "ivan", "password": USER_PASSWORD})
    assert r.status_code == 200
    r = await client.post(f"/api/admin/users/{uid}/invite", headers=bearer(admin_token))
    assert r.status_code == 409 and r.json()["code"] == "already_registered"


async def test_delete_user_marks_configs_and_blocks_login(db, client, admin_token):
    created = await _create(client, admin_token)
    uid = created["user"]["id"]
    await client.post("/api/auth/invite/redeem",
                      json={"key": created["invite_key"], "login": "ivan", "password": USER_PASSWORD})
    client.cookies.clear()
    servers = await _servers_with_configs(db, uid)
    r = await client.delete(f"/api/admin/users/{uid}", headers=bearer(admin_token))
    assert r.status_code == 202
    user = await db.get(User, uid)
    await db.refresh(user)
    assert user.deleting_at is not None
    deleted = (await db.execute(select(Config.deleted_at).where(Config.user_id == uid))).scalars().all()
    assert all(d is not None for d in deleted)
    assert await _sync_jobs(db) == set(servers)
    r = await client.post("/api/auth/login", json={"login": "ivan", "password": USER_PASSWORD, "role": "user"})
    assert r.status_code == 401
    listed = (await client.get(f"/api/admin/users/{uid}", headers=bearer(admin_token))).json()
    assert listed["status"] == "deleting"


async def test_delete_user_without_configs_is_immediate(db, client, admin_token):
    uid = (await _create(client, admin_token))["user"]["id"]
    assert (await client.delete(f"/api/admin/users/{uid}", headers=bearer(admin_token))).status_code == 202
    assert (await client.get(f"/api/admin/users/{uid}", headers=bearer(admin_token))).status_code == 404


async def test_user_details_include_configs_and_traffic(db, client, admin_token):
    uid = (await _create(client, admin_token))["user"]["id"]
    servers = await _servers_with_configs(db, uid)
    configs = (await db.execute(select(Config).order_by(Config.id))).scalars().all()
    def row(c, day, rx, tx):
        return TrafficDaily(config_id=c.id, user_id=c.user_id, server_id=c.server_id, day=day, rx=rx, tx=tx)

    db.add_all([row(configs[0], date(2026, 9, 25), 100, 1000), row(configs[0], date(2026, 9, 26), 50, 500),
                row(configs[1], date(2026, 9, 26), 7, 70)])
    await db.commit()
    body = (await client.get(f"/api/admin/users/{uid}", headers=bearer(admin_token))).json()
    assert body["traffic_total"] == {"rx": 157, "tx": 1570}
    assert body["traffic_by_server"] == [
        {"server_id": servers[0], "server_name": "s1", "rx": 150, "tx": 1500},
        {"server_id": servers[1], "server_name": "s2", "rx": 7, "tx": 70},
    ]
    assert [c["client_id"] for c in body["configs"]] == ["k1", "k2"]
    assert body["configs"][0]["traffic"] == {"rx": 150, "tx": 1500}
    listing = (await client.get("/api/admin/users", headers=bearer(admin_token))).json()
    assert listing[0]["traffic_total"] == {"rx": 157, "tx": 1570}


async def test_changes_are_audited(client, admin_token):
    uid = (await _create(client, admin_token))["user"]["id"]
    await client.post(f"/api/admin/users/{uid}/block", headers=bearer(admin_token))
    actions = [e["action"] for e in (await client.get("/api/admin/audit", headers=bearer(admin_token))).json()]
    assert actions[:2] == ["user_block", "user_create"]


async def test_login_still_works_for_active_user(db, client, admin_token):
    created = await _create(client, admin_token)
    await client.post("/api/auth/invite/redeem",
                      json={"key": created["invite_key"], "login": "ivan", "password": USER_PASSWORD})
    await login(client, "ivan", USER_PASSWORD, "user")
