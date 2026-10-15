from sqlalchemy import select

from app.db.models import AuditLog, InviteKey
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD, bearer, login, make_admin, make_user, registered_user

NEW_PASSWORD = "N3w-Pa55w0rd!asd"


async def _reset_key(client, admin_token, account_id) -> str:
    r = await client.post(f"/api/admin/accounts/{account_id}/reset-key", headers=bearer(admin_token))
    assert r.status_code == 200, r.text
    return r.json()["reset_key"]


async def test_issuing_a_key_keeps_the_old_password(db, client, admin_token, clock):
    user, _ = await registered_user(db, client)
    r = await client.post(f"/api/admin/accounts/{user.id}/reset-key", headers=bearer(admin_token))
    assert r.status_code == 200
    assert r.json()["expires_at"].startswith("2026-09-27T12:00:00")
    await login(client, "ivan", USER_PASSWORD, "user")


async def test_reset_sets_the_password_and_signs_out_everywhere(db, client, admin_token):
    user, token = await registered_user(db, client)
    key = await _reset_key(client, admin_token, user.id)
    assert (await client.post("/api/auth/reset/check", json={"key": key.lower()})).json() == {"login": "ivan"}
    r = await client.post("/api/auth/reset", json={"key": key, "password": NEW_PASSWORD})
    assert r.status_code == 204
    assert (await client.get("/api/me", headers=bearer(token))).status_code == 401
    r = await client.post("/api/auth/login", json={"login": "ivan", "password": USER_PASSWORD})
    assert r.status_code == 401
    await login(client, "ivan", NEW_PASSWORD, "user")
    r = await client.post("/api/auth/reset", json={"key": key, "password": "An0ther-Pa55w0rd!z"})
    assert r.status_code == 404 and r.json()["code"] == "reset_invalid"
    actions = (await db.execute(select(AuditLog.action))).scalars().all()
    assert "password_reset_key" in actions and "password_reset" in actions


async def test_key_expires_after_24_hours(db, client, admin_token, clock):
    user, _ = await registered_user(db, client)
    key = await _reset_key(client, admin_token, user.id)
    clock.advance(24 * 3600)
    r = await client.post("/api/auth/reset/check", json={"key": key})
    assert r.status_code == 404 and r.json()["code"] == "reset_invalid"


async def test_new_key_revokes_the_previous_one(db, client, admin_token):
    user, _ = await registered_user(db, client)
    first = await _reset_key(client, admin_token, user.id)
    second = await _reset_key(client, admin_token, user.id)
    assert (await client.post("/api/auth/reset/check", json={"key": first})).status_code == 404
    assert (await client.post("/api/auth/reset/check", json={"key": second})).status_code == 200


async def test_weak_password_keeps_the_key(db, client, admin_token):
    user, _ = await registered_user(db, client)
    key = await _reset_key(client, admin_token, user.id)
    r = await client.post("/api/auth/reset", json={"key": key, "password": "ivan"})
    assert r.status_code == 422 and r.json()["code"] == "password_too_short"
    assert (await client.post("/api/auth/reset/check", json={"key": key})).status_code == 200


async def test_keys_do_not_cross_purposes(db, client, admin_token):
    user, _ = await registered_user(db, client)
    _, invite = await make_user(db, display_name="Other")
    reset = await _reset_key(client, admin_token, user.id)
    assert (await client.post("/api/auth/reset/check", json={"key": invite})).status_code == 404
    assert (await client.post("/api/auth/invite/check", json={"key": reset})).status_code == 404


async def test_unregistered_and_unknown_accounts(db, client, admin_token):
    user, _ = await make_user(db)
    r = await client.post(f"/api/admin/accounts/{user.id}/reset-key", headers=bearer(admin_token))
    assert r.status_code == 409 and r.json()["code"] == "not_registered"
    r = await client.post("/api/admin/accounts/999/reset-key", headers=bearer(admin_token))
    assert r.status_code == 404


async def test_admin_resets_another_admins_password(db, client, admin_token):
    other = await make_admin(db, login="second")
    admins = (await client.get("/api/admin/admins", headers=bearer(admin_token))).json()
    assert [a["login"] for a in admins] == ["admin", "second"]
    key = await _reset_key(client, admin_token, other.id)
    assert (await client.post("/api/auth/reset", json={"key": key, "password": NEW_PASSWORD})).status_code == 204
    await login(client, "second", NEW_PASSWORD, "admin")
    r = await client.post("/api/auth/login", json={"login": "second", "password": ADMIN_PASSWORD})
    assert r.status_code == 401


async def test_users_cannot_issue_keys(db, client):
    user, token = await registered_user(db, client)
    r = await client.post(f"/api/admin/accounts/{user.id}/reset-key", headers=bearer(token))
    assert r.status_code == 403
    assert (await db.execute(select(InviteKey).where(InviteKey.purpose == "reset"))).first() is None


async def test_reset_attempts_are_rate_limited(client):
    for _ in range(10):
        await client.post("/api/auth/reset/check", json={"key": "AAAA"})
    assert (await client.post("/api/auth/reset", json={"key": "AAAA", "password": NEW_PASSWORD})).status_code == 429
