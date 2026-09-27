from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.db.models import Config, Job, Server, ServerContainer, Session, TrafficDaily, User
from app.jobs.periodic import cleanup, enqueue_periodic
from app.jobs.queue import enqueue, finish
from app.services.expiry import expire_users
from app.services.traffic import collect_traffic
from tests.fakes import AWG, awg_server, remote_factory_for

TZ = "Europe/Moscow"


def dump(rows: dict[str, tuple[int, int]]) -> str:
    """`awg show dump`: rx is what the server received from the client, tx what it sent."""
    lines = ["priv\tpub\t55424\toff"]
    lines += [f"{k}\tpsk\t(none)\t10.8.1.9/32\t0\t{rx}\t{tx}\toff" for k, (rx, tx) in rows.items()]
    return "\n".join(lines) + "\n"


async def _server_with_config(db, clock):
    server = Server(name="s", host="h", ssh_port=22, ssh_user="root", ssh_secret_enc="x", imported_at=clock.now())
    db.add(server)
    await db.flush()
    db.add(ServerContainer(server_id=server.id, container=AWG, params_json={}))
    user = User(display_name="u", max_configs=3)
    db.add(user)
    await db.flush()
    cfg = Config(user_id=user.id, server_id=server.id, container=AWG, name="c", client_id="pubA=")
    db.add(cfg)
    await db.commit()
    return server.id, cfg.id, user.id


async def _collect(db, server_id, remote, clock):
    await collect_traffic(db, server_id, remote_factory_for(remote), clock, TZ)
    db.expire_all()


async def _rows(db):
    return [(r.day, r.rx, r.tx) for r in (await db.execute(select(TrafficDaily).order_by(TrafficDaily.day))).scalars()]


async def test_traffic_accumulates_from_user_point_of_view(db, clock):
    server_id, cfg_id, _ = await _server_with_config(db, clock)
    remote = awg_server()
    remote.dumps[AWG] = dump({"pubA=": (100, 1000)})
    await _collect(db, server_id, remote, clock)
    remote.dumps[AWG] = dump({"pubA=": (150, 1600)})
    await _collect(db, server_id, remote, clock)
    # user downloaded what the server sent (tx) and uploaded what it received (rx)
    assert await _rows(db) == [(date(2026, 9, 26), 1600, 150)]
    cfg = await db.get(Config, cfg_id)
    assert (cfg.last_rx, cfg.last_tx) == (1600, 150)


async def test_counter_reset_counts_new_value(db, clock):
    server_id, _, _ = await _server_with_config(db, clock)
    remote = awg_server()
    remote.dumps[AWG] = dump({"pubA=": (100, 1000)})
    await _collect(db, server_id, remote, clock)
    remote.dumps[AWG] = dump({"pubA=": (10, 20)})
    await _collect(db, server_id, remote, clock)
    assert await _rows(db) == [(date(2026, 9, 26), 1020, 110)]


async def test_day_boundary_uses_panel_timezone(db, clock):
    server_id, _, _ = await _server_with_config(db, clock)
    remote = awg_server()
    clock.set(datetime(2026, 9, 26, 20, 50, tzinfo=UTC))  # 23:50 in Moscow
    remote.dumps[AWG] = dump({"pubA=": (0, 100)})
    await _collect(db, server_id, remote, clock)
    clock.set(datetime(2026, 9, 26, 21, 10, tzinfo=UTC))  # 00:10 next day in Moscow
    remote.dumps[AWG] = dump({"pubA=": (0, 130)})
    await _collect(db, server_id, remote, clock)
    assert await _rows(db) == [(date(2026, 9, 26), 100, 0), (date(2026, 9, 27), 30, 0)]


async def test_unknown_and_absent_clients_are_ignored(db, clock):
    server_id, cfg_id, _ = await _server_with_config(db, clock)
    remote = awg_server()
    remote.dumps[AWG] = dump({"stranger=": (5, 5)})
    await _collect(db, server_id, remote, clock)
    assert await _rows(db) == []
    assert (await db.get(Server, server_id)).last_ok_at == clock.now()


async def test_expire_users_blocks_and_enqueues_reconcile(db, clock):
    server_id, _, user_id = await _server_with_config(db, clock)
    user = await db.get(User, user_id)
    user.expires_at = clock.now() - timedelta(minutes=1)
    fresh = User(display_name="fresh", max_configs=1, expires_at=clock.now() + timedelta(days=1))
    db.add(fresh)
    await db.commit()
    fresh_id = fresh.id
    assert await expire_users(db, clock) == [user_id]
    db.expire_all()
    assert (await db.get(User, user_id)).blocked_by == "expiry"
    assert (await db.get(User, fresh_id)).blocked_by is None
    jobs = (await db.execute(select(Job.kind, Job.server_id))).all()
    assert jobs == [("reconcile", server_id)]
    assert await expire_users(db, clock) == []


async def test_enqueue_periodic_jobs(db, clock):
    server_id, _, _ = await _server_with_config(db, clock)
    await enqueue_periodic(db, "expire")
    await enqueue_periodic(db, "traffic")
    await enqueue_periodic(db, "reconcile")
    await enqueue_periodic(db, "cleanup")
    await enqueue_periodic(db, "traffic")  # deduplicated
    await db.commit()
    kinds = sorted((await db.execute(select(Job.kind, Job.server_id))).all(), key=lambda x: x[0])
    assert kinds == [("cleanup", None), ("expire", None), ("reconcile", server_id), ("traffic", server_id)]


async def test_cleanup_removes_old_sessions_and_jobs(db, clock):
    now = clock.now()
    user = User(display_name="Ivan", max_configs=1)
    db.add(user)
    await db.flush()
    db.add_all([
        Session(user_id=user.id, token_hash="a" * 64, expires_at=now - timedelta(days=2)),
        Session(user_id=user.id, token_hash="b" * 64, expires_at=now + timedelta(days=2)),
    ])
    old = await enqueue(db, "x")
    new = await enqueue(db, "y")
    await db.commit()
    await finish(db, old, now - timedelta(days=31))
    await finish(db, new, now - timedelta(days=1))
    await cleanup(db, clock)
    assert [s.token_hash[0] for s in (await db.execute(select(Session))).scalars()] == ["b"]
    assert [j.kind for j in (await db.execute(select(Job))).scalars()] == ["y"]
