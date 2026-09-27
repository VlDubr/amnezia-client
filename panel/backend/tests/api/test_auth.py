from datetime import timedelta

from sqlalchemy import select

from app.db.models import InviteKey, Session, User
from tests.conftest import ADMIN_PASSWORD, USER_PASSWORD, bearer, login, make_admin, make_user, registered_user


async def test_admin_login_returns_token_and_cookies(db, client):
    await make_admin(db)
    r = await client.post("/api/auth/login", json={"login": "admin", "password": ADMIN_PASSWORD, "role": "admin"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin" and r.json()["token"]
    assert "panel_session" in r.cookies and "panel_csrf" in r.cookies


async def test_login_wrong_password(db, client):
    await make_admin(db)
    r = await client.post("/api/auth/login", json={"login": "admin", "password": "nope", "role": "admin"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"


async def test_login_is_rate_limited_per_login(db, client):
    await make_admin(db)
    for _ in range(5):
        await client.post("/api/auth/login", json={"login": "admin", "password": "nope", "role": "admin"})
    r = await client.post("/api/auth/login", json={"login": "admin", "password": ADMIN_PASSWORD, "role": "admin"})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"


async def test_session_endpoint_reports_role(db, client, admin_token):
    r = await client.get("/api/auth/session", headers=bearer(admin_token))
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["login"] == "admin"


async def test_no_token_is_401(client):
    r = await client.get("/api/auth/session")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


async def test_session_expires_and_slides(db, client, admin_token, clock):
    clock.advance(11 * 3600)
    assert (await client.get("/api/auth/session", headers=bearer(admin_token))).status_code == 200
    clock.advance(11 * 3600)  # 22h after login but only 11h after last use
    assert (await client.get("/api/auth/session", headers=bearer(admin_token))).status_code == 200
    clock.advance(13 * 3600)
    assert (await client.get("/api/auth/session", headers=bearer(admin_token))).status_code == 401


async def test_logout_revokes_token(client, admin_token):
    assert (await client.post("/api/auth/logout", headers=bearer(admin_token))).status_code == 204
    assert (await client.get("/api/auth/session", headers=bearer(admin_token))).status_code == 401


async def test_cookie_auth_requires_csrf_on_unsafe_methods(db, client):
    await make_admin(db)
    await client.post("/api/auth/login", json={"login": "admin", "password": ADMIN_PASSWORD, "role": "admin"})
    assert (await client.get("/api/auth/session")).status_code == 200
    r = await client.post("/api/auth/logout")
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    r = await client.post("/api/auth/logout", headers={"X-CSRF-Token": client.cookies["panel_csrf"]})
    assert r.status_code == 204


async def test_invite_check_and_redeem(db, client):
    user, key = await make_user(db, display_name="Ivan Petrov")
    assert (await client.post("/api/auth/invite/check", json={"key": key.lower()})).json() == {"ok": True}
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "ivan", "password": USER_PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "user"
    invite = (await db.execute(select(InviteKey))).scalar_one()
    await db.refresh(invite)
    assert invite.used_at is not None
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "ivan2", "password": USER_PASSWORD})
    assert r.status_code == 404 and r.json()["code"] == "invite_invalid"
    assert (await client.post("/api/auth/invite/check", json={"key": key})).status_code == 404


async def test_redeem_rejects_weak_password_and_keeps_key(db, client):
    _, key = await make_user(db)
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "ivan", "password": "aaaaaaaaaaaa"})
    assert r.status_code == 422 and r.json()["code"] == "password_too_weak"
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "ivan", "password": "short"})
    assert r.json()["code"] == "password_too_short"
    assert (await client.post("/api/auth/invite/check", json={"key": key})).status_code == 200


async def test_redeem_rejects_taken_login(db, client):
    await registered_user(db, client, login_="ivan")
    _, key = await make_user(db, display_name="Other")
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "ivan", "password": USER_PASSWORD})
    assert r.status_code == 409 and r.json()["code"] == "login_taken"


async def test_redeem_rejects_bad_login_format(db, client):
    _, key = await make_user(db)
    r = await client.post("/api/auth/invite/redeem", json={"key": key, "login": "a b", "password": USER_PASSWORD})
    assert r.status_code == 422 and r.json()["code"] == "login_invalid"


async def test_invite_attempts_are_rate_limited(client):
    for _ in range(10):
        await client.post("/api/auth/invite/check", json={"key": "AAAA"})
    r = await client.post("/api/auth/invite/check", json={"key": "AAAA"})
    assert r.status_code == 429


async def test_user_login_and_me(db, client, clock):
    user, _ = await registered_user(db, client)
    token = await login(client, "ivan", USER_PASSWORD, "user")
    r = await client.get("/api/me", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["login"] == "ivan" and body["status"] == "active" and body["max_configs"] == 3
    assert body["configs_count"] == 0 and body["expires_on"] is None


async def test_blocked_user_can_login_and_sees_status(db, client):
    user, _ = await registered_user(db, client)
    user.blocked_by = "admin"
    db.add(user)
    await db.commit()
    token = await login(client, "ivan", USER_PASSWORD, "user")
    assert (await client.get("/api/me", headers=bearer(token))).json()["status"] == "blocked"


async def test_deleting_user_cannot_login(db, client, clock):
    user, token = await registered_user(db, client)
    user.deleting_at = clock.now()
    db.add(user)
    await db.commit()
    r = await client.post("/api/auth/login", json={"login": "ivan", "password": USER_PASSWORD, "role": "user"})
    assert r.status_code == 401
    assert (await client.get("/api/me", headers=bearer(token))).status_code == 401


async def test_expires_on_is_last_day_in_panel_tz(db, client, clock):
    from datetime import date

    from app.domain.rules import expiry_instant

    user, token = await registered_user(db, client)
    user.expires_at = expiry_instant(date(2026, 10, 31), "Europe/Moscow")
    db.add(user)
    await db.commit()
    assert (await client.get("/api/me", headers=bearer(token))).json()["expires_on"] == "2026-10-31"


async def test_change_password_revokes_other_sessions(db, client):
    _, first = await registered_user(db, client)
    second = await login(client, "ivan", USER_PASSWORD, "user")
    new = "N3w-Pa55w0rd!asd"
    r = await client.post("/api/me/password", json={"old": USER_PASSWORD, "new": new}, headers=bearer(second))
    assert r.status_code == 204
    assert (await client.get("/api/me", headers=bearer(first))).status_code == 401
    assert (await client.get("/api/me", headers=bearer(second))).status_code == 200
    await login(client, "ivan", new, "user")


async def test_change_password_checks_old_and_policy(db, client):
    _, token = await registered_user(db, client)
    r = await client.post("/api/me/password", json={"old": "wrong", "new": "N3w-Pa55w0rd!asd"}, headers=bearer(token))
    assert r.status_code == 403 and r.json()["code"] == "wrong_password"
    r = await client.post("/api/me/password", json={"old": USER_PASSWORD, "new": "short"}, headers=bearer(token))
    assert r.status_code == 422 and r.json()["code"] == "password_too_short"


async def test_user_token_cannot_reach_admin_api(db, client):
    _, token = await registered_user(db, client)
    r = await client.get("/api/auth/session", headers=bearer(token))
    assert r.json()["role"] == "user"
    r = await client.get("/api/admin/audit", headers=bearer(token))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


async def test_admin_token_cannot_reach_user_api(client, admin_token):
    r = await client.get("/api/me", headers=bearer(admin_token))
    assert r.status_code == 403


async def test_sessions_store_only_hashes(db, client, admin_token):
    s = (await db.execute(select(Session))).scalar_one()
    assert s.token_hash != admin_token and len(s.token_hash) == 64
    assert s.expires_at - s.last_used_at == timedelta(hours=12)


async def test_cli_create_admin(pg_url, db, monkeypatch):
    from app import cli

    monkeypatch.setenv("PANEL_DATABASE_URL", pg_url)
    monkeypatch.setenv("PANEL_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    await cli.create_admin("root", ADMIN_PASSWORD, pg_url)
    from app.db.models import Admin

    assert (await db.execute(select(Admin.login))).scalar_one() == "root"
    assert (await db.execute(select(User))).first() is None
