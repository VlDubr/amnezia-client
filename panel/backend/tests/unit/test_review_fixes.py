"""Regression tests for the stage 1 final review (C1, I1-I9)."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date

from sqlalchemy import select

from app.db.models import Config, Server, TrafficDaily
from app.jobs.queue import claim, enqueue, fail
from app.jobs.worker import Worker
from app.security.secretbox import SecretBox
from app.services.reconcile import reconcile_server
from app.services.servers import discover_containers
from tests.conftest import MASTER_KEY, add_server, bearer, registered_user, run_jobs
from tests.fakes import AWG, AWG_CONF, awg_server, peers_on, remote_factory_for

BOX = SecretBox(MASTER_KEY)
PEER_A = "[Peer]\nPublicKey = pubA=\nPresharedKey = srvpsk=\nAllowedIPs = 10.8.1.1/32\n"


# --- I1: dedupe must not attach new work to a job that already read its state ------------------


async def test_enqueue_does_not_attach_to_running_job(db, clock):
    first = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    second = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    assert second.id != first.id and second.status == "queued"


async def test_enqueue_pulls_backed_off_job_forward(db, clock):
    job = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await claim(db, clock.now())
    await fail(db, job, "boom", clock.now())
    assert job.run_after > clock.now()
    again = await enqueue(db, "reconcile", dedupe_key="sync:1")
    await db.commit()
    await db.refresh(again)
    assert again.id == job.id and again.run_after <= clock.now()


# --- I6: a busy server must not hold worker slots ------------------------------------------------


async def test_worker_postpones_job_of_busy_server(sessionmaker, db, clock):
    from app.jobs.locks import server_lock

    server = Server(name="s", host="h", ssh_port=22, ssh_user="r", ssh_secret_enc="x")
    db.add(server)
    await db.commit()
    ran = []

    async def handler(session, job):
        ran.append(job.id)

    job = await enqueue(db, "x", server_id=server.id)
    await db.commit()
    async with server_lock(sessionmaker, server.id):
        assert await asyncio.wait_for(Worker(sessionmaker, {"x": handler}, clock).run_once(), 5) is True
    await db.refresh(job)
    assert ran == [] and job.status == "queued" and job.attempts == 0 and job.run_after > clock.now()


# --- I4 / C1: revoked clients must never come back -----------------------------------------------


async def _discovered(db, clock):
    remote = awg_server()
    server = Server(name="nl", host="h", ssh_port=22, ssh_user="r", ssh_secret_enc="x", imported_at=clock.now())
    db.add(server)
    await db.commit()
    await discover_containers(db, server, remote, clock)
    await db.commit()
    return server.id, remote


async def _reconcile(db, server_id, remote, clock):
    await reconcile_server(db, server_id, remote_factory_for(remote), clock, BOX)
    db.expire_all()


async def _rediscover(db, server_id, remote, clock):
    await discover_containers(db, await db.get(Server, server_id), remote, clock)
    await db.commit()


async def test_deleted_config_of_stopped_container_stays_revoked(db, clock):
    server_id, remote = await _discovered(db, clock)
    await _reconcile(db, server_id, remote, clock)
    victim = (await db.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
    victim.deleted_at = clock.now()
    await db.commit()
    remote.containers = []  # container stopped: not in `docker ps`
    await _rediscover(db, server_id, remote, clock)
    await _reconcile(db, server_id, remote, clock)
    assert (await db.execute(select(Config).where(Config.client_id == "pubA="))).first() is None
    remote.containers = [AWG]  # container is back with its old conf
    await _rediscover(db, server_id, remote, clock)
    await _reconcile(db, server_id, remote, clock)
    assert "pubA=" not in peers_on(remote)
    assert (await db.execute(select(Config).where(Config.client_id == "pubA="))).first() is None


async def test_peer_of_deleted_config_reappearing_is_removed(db, clock):
    server_id, remote = await _discovered(db, clock)
    await _reconcile(db, server_id, remote, clock)
    victim = (await db.execute(select(Config).where(Config.client_id == "pubB="))).scalar_one()
    victim.deleted_at = clock.now()
    await db.commit()
    await _reconcile(db, server_id, remote, clock)
    remote.files[(AWG, AWG_CONF)] += "\n[Peer]\nPublicKey = pubB=\nPresharedKey = srvpsk=\nAllowedIPs = 10.8.1.2/32\n"
    await _reconcile(db, server_id, remote, clock)
    assert "pubB=" not in peers_on(remote)


# --- I5: a keyless imported client removed outside the panel stays removed ------------------------


async def test_keyless_config_removed_outside_panel_is_not_restored(db, clock):
    server_id, remote = await _discovered(db, clock)
    await _reconcile(db, server_id, remote, clock)
    remote.files[(AWG, AWG_CONF)] = remote.files[(AWG, AWG_CONF)].replace(PEER_A, "")
    await _reconcile(db, server_id, remote, clock)
    await _reconcile(db, server_id, remote, clock)
    assert peers_on(remote) == {"pubB="}
    assert (await db.execute(select(Config).where(Config.client_id == "pubA="))).first() is None


async def test_keyless_config_blocked_then_unblocked_by_panel_returns(db, clock):
    server_id, remote = await _discovered(db, clock)
    await _reconcile(db, server_id, remote, clock)
    cfg = (await db.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
    cfg.blocked_by = "admin"
    await db.commit()
    cfg_id = cfg.id
    await _reconcile(db, server_id, remote, clock)
    assert peers_on(remote) == {"pubB="}
    cfg = await db.get(Config, cfg_id)
    cfg.blocked_by = None
    await db.commit()
    await _reconcile(db, server_id, remote, clock)
    assert peers_on(remote) == {"pubA=", "pubB="}


# --- C1 / I2 / I3 / I9: config creation -----------------------------------------------------------


async def test_create_reads_desired_state_after_taking_the_server_lock(db, client, admin_token, app, fake_remote,
                                                                     monkeypatch, sessionmaker):
    import app.services.configs as configs_service

    server = await add_server(client, admin_token, app)
    _, token = await registered_user(db, client)
    real_lock = configs_service.server_lock

    @asynccontextmanager
    async def lock_after_concurrent_delete(sm, server_id, **kw):
        # While this request waited for the lock, a sync job deleted pubA= and removed its peer.
        async with sessionmaker() as other:
            cfg = (await other.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
            await other.delete(cfg)
            await other.commit()
        fake_remote.files[(AWG, AWG_CONF)] = fake_remote.files[(AWG, AWG_CONF)].replace(PEER_A, "")
        async with real_lock(sm, server_id, **kw):
            yield

    monkeypatch.setattr(configs_service, "server_lock", lock_after_concurrent_delete)
    r = await client.post("/api/me/configs", json={"server_id": server["id"], "container": AWG},
                          headers=bearer(token))
    assert r.status_code == 201
    assert "pubA=" not in peers_on(fake_remote)


async def test_config_row_is_committed_before_the_lock_is_released(db, client, admin_token, app, fake_remote,
                                                                  monkeypatch, sessionmaker):
    import app.services.configs as configs_service

    server = await add_server(client, admin_token, app)
    user, token = await registered_user(db, client)
    user_id = user.id
    real_lock = configs_service.server_lock
    seen_at_release = []

    @asynccontextmanager
    async def observing_lock(sm, server_id, **kw):
        async with real_lock(sm, server_id, **kw):
            yield
            async with sessionmaker() as other:
                seen_at_release.append((await other.execute(
                    select(Config.id).where(Config.user_id == user_id))).scalars().all())

    monkeypatch.setattr(configs_service, "server_lock", observing_lock)
    r = await client.post("/api/me/configs", json={"server_id": server["id"], "container": AWG},
                          headers=bearer(token))
    assert r.status_code == 201 and seen_at_release == [[r.json()["id"]]]


async def test_failed_create_leaves_no_working_peer(db, client, admin_token, app, fake_remote):
    server = await add_server(client, admin_token, app)
    _, token = await registered_user(db, client)
    fake_remote.fail_on = "syncconf"  # the conf is written, applying it fails, and so does the rollback attempt
    r = await client.post("/api/me/configs", json={"server_id": server["id"], "container": AWG},
                          headers=bearer(token))
    assert r.status_code == 503
    fake_remote.fail_on = None
    await client.post(f"/api/admin/servers/{server['id']}/sync", headers=bearer(admin_token))
    await run_jobs(app)
    assert peers_on(fake_remote) == {"pubA=", "pubB="}
    assert (await db.execute(select(Config).where(Config.user_id.is_not(None)))).first() is None


async def test_database_pool_timeout_is_reported_as_unavailable(db, client, admin_token, app, fake_remote,
                                                                monkeypatch):
    import sqlalchemy.exc

    import app.services.configs as configs_service

    server = await add_server(client, admin_token, app)
    _, token = await registered_user(db, client)

    @asynccontextmanager
    async def exhausted(sm, server_id, **kw):
        raise sqlalchemy.exc.TimeoutError("QueuePool limit reached")
        yield

    monkeypatch.setattr(configs_service, "server_lock", exhausted)
    r = await client.post("/api/me/configs", json={"server_id": server["id"], "container": AWG},
                          headers=bearer(token))
    assert r.status_code == 503 and r.json()["code"] == "server_unavailable"


def test_engine_pool_is_sized_for_worker_and_requests():
    from app.db.base import make_engine

    engine = make_engine("postgresql+asyncpg://u:p@localhost/db")
    assert engine.pool.size() >= 20


# --- I7: install must not wipe an existing container --------------------------------------------


async def test_install_refuses_existing_container_without_force(db, client, admin_token, app, fake_remote):
    server = await add_server(client, admin_token, app)
    r = await client.post(f"/api/admin/servers/{server['id']}/containers", json={"container": AWG},
                          headers=bearer(admin_token))
    assert r.status_code == 409 and r.json()["code"] == "already_installed"
    r = await client.post(f"/api/admin/servers/{server['id']}/containers", json={"container": AWG, "force": True},
                          headers=bearer(admin_token))
    assert r.status_code == 202


# --- I8: deleting a config keeps its traffic history ---------------------------------------------


async def test_traffic_history_survives_config_deletion(db, client, admin_token, app, fake_remote):
    server = await add_server(client, admin_token, app)
    user, token = await registered_user(db, client)
    user_id = user.id
    cfg = (await client.post("/api/me/configs", json={"server_id": server["id"], "container": AWG},
                             headers=bearer(token))).json()
    db.add(TrafficDaily(config_id=cfg["id"], user_id=user_id, server_id=server["id"], day=date(2026, 9, 26),
                        rx=100, tx=10))
    await db.commit()
    await client.delete(f"/api/me/configs/{cfg['id']}", headers=bearer(token))
    await run_jobs(app)
    assert (await db.execute(select(Config).where(Config.id == cfg["id"]))).first() is None
    detail = (await client.get(f"/api/admin/users/{user_id}", headers=bearer(admin_token))).json()
    assert detail["traffic_total"] == {"rx": 100, "tx": 10}
    assert detail["traffic_by_server"][0]["rx"] == 100
    report = (await client.get(f"/api/admin/traffic?user_id={user_id}", headers=bearer(admin_token))).json()
    assert report["total"] == {"rx": 100, "tx": 10}


async def test_assigning_an_orphan_moves_its_traffic_to_the_user(db, client, admin_token, app, fake_remote):
    await add_server(client, admin_token, app)
    user, _ = await registered_user(db, client)
    user_id = user.id
    orphan = (await db.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
    db.add(TrafficDaily(config_id=orphan.id, user_id=None, server_id=orphan.server_id, day=date(2026, 9, 26),
                        rx=7, tx=3))
    await db.commit()
    await client.post(f"/api/admin/configs/{orphan.id}/assign", json={"user_id": user_id},
                      headers=bearer(admin_token))
    detail = (await client.get(f"/api/admin/users/{user_id}", headers=bearer(admin_token))).json()
    assert detail["traffic_total"] == {"rx": 7, "tx": 3}
