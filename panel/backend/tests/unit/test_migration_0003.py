"""Migration 0003 moves administrators into the users table and keeps what points at them."""

import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db.base import make_engine
from app.db.migrate import alembic_config


async def _fresh_database(pg_url: str, name: str) -> str:
    admin_engine = make_engine(pg_url)
    async with admin_engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        await conn.execute(text(f"CREATE DATABASE {name}"))
    await admin_engine.dispose()
    return make_url(pg_url).set(database=name).render_as_string(hide_password=False)


async def _run(url: str, sql: str, **params):
    engine = make_engine(url)
    async with engine.begin() as conn:
        result = (await conn.execute(text(sql), params)).all() if sql.lstrip().upper().startswith("SELECT") \
            else await conn.execute(text(sql), params)
    await engine.dispose()
    return result


async def test_admins_move_into_users_with_the_admin_role(pg_url):
    url = await _fresh_database(pg_url, "mig0003_ok")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0002")
    await _run(url, "INSERT INTO users (display_name, max_configs, login, password_hash) "
                    "VALUES ('Ivan', 3, 'ivan', 'h-ivan')")  # gets id 1
    await _run(url, "INSERT INTO admins (id, login, password_hash) VALUES (1, 'root', 'h-root')")
    await _run(url, "INSERT INTO sessions (subject, subject_id, token_hash, expires_at) VALUES "
                    "('user', 1, 'tok-user', now() + interval '1 day'), "
                    "('admin', 1, 'tok-admin', now() + interval '1 day')")
    await _run(url, "INSERT INTO audit_log (actor, action, target, details_json) "
                    "VALUES ('admin:1', 'user_block', 'user:1', '{}')")

    await asyncio.to_thread(command.upgrade, alembic_config(url), "head")

    rows = await _run(url, "SELECT id, login, role, password_hash FROM users ORDER BY id")
    assert [(r.login, r.role, r.password_hash) for r in rows] == [("ivan", "user", "h-ivan"),
                                                                   ("root", "admin", "h-root")]
    root_id = rows[1].id
    # The user's session survives; the admin signs in again (its session pointed at the old table).
    sessions = await _run(url, "SELECT user_id, token_hash FROM sessions")
    assert [(s.user_id, s.token_hash) for s in sessions] == [(1, "tok-user")]
    audit = await _run(url, "SELECT actor, target FROM audit_log")
    assert [(a.actor, a.target) for a in audit] == [(f"admin:{root_id}", "user:1")]
    tables = await _run(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'admins'")
    assert tables == []


async def test_a_login_used_by_both_an_admin_and_a_user_stops_the_migration(pg_url):
    url = await _fresh_database(pg_url, "mig0003_clash")
    await asyncio.to_thread(command.upgrade, alembic_config(url), "0002")
    await _run(url, "INSERT INTO users (display_name, max_configs, login) VALUES ('Ivan', 3, 'ivan')")
    await _run(url, "INSERT INTO admins (login, password_hash) VALUES ('ivan', 'h')")
    with pytest.raises(Exception, match="ivan"):
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
