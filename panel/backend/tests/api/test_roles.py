"""One account table with roles: the role is never taken from the client, and user management never reaches admins."""

from sqlalchemy import select, update

from app.db.models import User
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD, bearer, make_admin, make_user, registered_user


async def _login(client, login_, password):
    r = await client.post("/api/auth/login", json={"login": login_, "password": password})
    client.cookies.clear()
    return r


async def test_one_login_endpoint_serves_both_roles(db, client):
    await make_admin(db)
    await registered_user(db, client)
    admin = await _login(client, "admin", ADMIN_PASSWORD)
    user = await _login(client, "ivan", USER_PASSWORD)
    assert admin.status_code == 200 and admin.json()["role"] == "admin"
    assert user.status_code == 200 and user.json()["role"] == "user"


async def test_role_sent_by_the_client_is_ignored(db, client):
    await registered_user(db, client)
    r = await client.post("/api/auth/login", json={"login": "ivan", "password": USER_PASSWORD, "role": "admin"})
    client.cookies.clear()
    assert r.status_code == 200 and r.json()["role"] == "user"
    token = r.json()["token"]
    assert (await client.get("/api/admin/users", headers=bearer(token))).status_code == 403
    assert (await client.get("/api/auth/session", headers=bearer(token))).json()["role"] == "user"


async def test_role_is_read_from_the_account_on_every_request(db, client):
    admin = await make_admin(db)
    token = (await _login(client, "admin", ADMIN_PASSWORD)).json()["token"]
    assert (await client.get("/api/admin/users", headers=bearer(token))).status_code == 200
    await db.execute(update(User).where(User.id == admin.id).values(role="user"))
    await db.commit()
    assert (await client.get("/api/admin/users", headers=bearer(token))).status_code == 403


async def test_admin_patch_cannot_change_a_role(db, client, admin_token):
    user, _ = await registered_user(db, client)
    r = await client.patch(f"/api/admin/users/{user.id}", json={"role": "admin", "display_name": "Ivan"},
                           headers=bearer(admin_token))
    assert r.status_code == 200
    await db.refresh(user)
    assert user.role == "user"


async def test_user_management_never_reaches_admin_accounts(db, client, admin_token):
    admin_id = (await db.execute(select(User.id).where(User.role == "admin"))).scalar_one()
    users = (await client.get("/api/admin/users", headers=bearer(admin_token))).json()
    assert admin_id not in [u["id"] for u in users]
    h = bearer(admin_token)
    base = f"/api/admin/users/{admin_id}"
    assert (await client.get(base, headers=h)).status_code == 404
    assert (await client.patch(base, json={"max_configs": 5}, headers=h)).status_code == 404
    assert (await client.post(f"{base}/block", headers=h)).status_code == 404
    assert (await client.post(f"{base}/invite", headers=h)).status_code == 404
    assert (await client.delete(base, headers=h)).status_code == 404
    r = await client.post(f"{base}/configs", json={"server_id": 1, "container": "amnezia-awg2"}, headers=h)
    assert r.status_code == 404
    assert (await db.execute(select(User.role).where(User.id == admin_id))).scalar_one() == "admin"


async def test_logins_are_unique_across_roles(db, client):
    await make_admin(db, login="ivan")
    _, key = await make_user(db)
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "ivan", "password": USER_PASSWORD})
    assert r.status_code == 409 and r.json()["code"] == "login_taken"


async def test_admin_has_no_user_cabinet_but_can_change_the_password(db, client, admin_token):
    assert (await client.get("/api/me", headers=bearer(admin_token))).status_code == 403
    assert (await client.get("/api/me/configs", headers=bearer(admin_token))).status_code == 403
    new = "N3w-Adm1n-Pa55w0rd!"
    r = await client.post("/api/me/password", json={"old": ADMIN_PASSWORD, "new": new}, headers=bearer(admin_token))
    assert r.status_code == 204
    assert (await _login(client, "admin", new)).json()["role"] == "admin"


async def test_role_cannot_be_chosen_at_registration_or_creation(db, client, admin_token):
    _, key = await make_user(db)
    r = await client.post("/api/auth/invite/redeem",
                          json={"key": key, "login": "mallory", "password": USER_PASSWORD, "role": "admin"})
    client.cookies.clear()
    assert r.status_code == 200 and r.json()["role"] == "user"
    r = await client.post("/api/admin/users", json={"display_name": "Eve", "max_configs": 1, "role": "admin"},
                          headers=bearer(admin_token))
    assert r.status_code == 201
    roles = (await db.execute(select(User.login, User.role).where(User.role == "admin"))).all()
    assert roles == [("admin", "admin")]


async def test_deleting_an_account_ends_its_sessions(db, client):
    user, token = await registered_user(db, client)
    await db.delete(user)
    await db.commit()
    assert (await client.get("/api/auth/session", headers=bearer(token))).status_code == 401
