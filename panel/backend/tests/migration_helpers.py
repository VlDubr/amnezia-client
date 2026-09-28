"""Helpers for migration tests: a fresh database next to the test one, and one-off SQL."""

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db.base import make_engine


async def fresh_database(pg_url: str, name: str) -> str:
    admin_engine = make_engine(pg_url)
    async with admin_engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        await conn.execute(text(f"CREATE DATABASE {name}"))
    await admin_engine.dispose()
    return make_url(pg_url).set(database=name).render_as_string(hide_password=False)


async def run_sql(url: str, sql: str, **params):
    engine = make_engine(url)
    async with engine.begin() as conn:
        result = (await conn.execute(text(sql), params)).all() if sql.lstrip().upper().startswith("SELECT") \
            else await conn.execute(text(sql), params)
    await engine.dispose()
    return result
