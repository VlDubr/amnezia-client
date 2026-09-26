import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Config, Job, Server, User


async def _user_and_server(db):
    user = User(display_name="Ivan", max_configs=3)
    server = Server(name="nl-1", host="203.0.113.10", ssh_port=22, ssh_user="root", ssh_secret_enc="x")
    db.add_all([user, server])
    await db.flush()
    return user, server


async def test_config_roundtrip(db):
    user, server = await _user_and_server(db)
    db.add(Config(user_id=user.id, server_id=server.id, container="amnezia-awg2", name="phone", client_id="pub1"))
    await db.commit()
    cfg = (await db.execute(select(Config))).scalar_one()
    assert cfg.user_id == user.id and cfg.blocked_by is None and cfg.created_at is not None
    assert user.blocked_by is None and user.created_at is not None


async def test_duplicate_client_id_on_same_container_is_rejected(db):
    user, server = await _user_and_server(db)
    db.add(Config(user_id=user.id, server_id=server.id, container="amnezia-awg2", name="a", client_id="pub1"))
    db.add(Config(user_id=None, server_id=server.id, container="amnezia-awg2", name="b", client_id="pub1"))
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_invalid_blocked_by_is_rejected(db):
    db.add(User(display_name="x", max_configs=1, blocked_by="nope"))
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_active_jobs_dedupe_key_is_unique(db):
    db.add(Job(kind="reconcile", payload_json={}, dedupe_key="reconcile:1"))
    db.add(Job(kind="reconcile", payload_json={}, dedupe_key="reconcile:1"))
    with pytest.raises(IntegrityError):
        await db.commit()


async def test_finished_jobs_do_not_block_dedupe_key(db):
    db.add(Job(kind="reconcile", payload_json={}, dedupe_key="reconcile:1", status="done"))
    db.add(Job(kind="reconcile", payload_json={}, dedupe_key="reconcile:1"))
    await db.commit()
