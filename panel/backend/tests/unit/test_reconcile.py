import pytest
from sqlalchemy import select

from app.db.models import Config, Server, ServerContainer, User
from app.security.secretbox import SecretBox
from app.services.materials import seal, unseal
from app.services.reconcile import reconcile_server
from app.services.servers import discover_containers
from app.ssh.conn import RemoteError
from tests.conftest import MASTER_KEY
from tests.fakes import AWG, awg_server, peers_on, remote_factory_for

BOX = SecretBox(MASTER_KEY)


async def _server(db, imported=False, clock=None) -> Server:
    server = Server(name="nl", host="203.0.113.10", ssh_port=22, ssh_user="root", ssh_secret_enc="x",
                    imported_at=clock.now() if imported else None)
    db.add(server)
    await db.commit()
    return server


async def _reconcile(db, server, remote, clock):
    server_id = server.id if isinstance(server, Server) else server
    await reconcile_server(db, server_id, remote_factory_for(remote), clock, BOX)
    db.expire_all()


async def _discovered(db, clock, imported=True):
    remote = awg_server()
    server = await _server(db, imported, clock)
    await discover_containers(db, server, remote, clock)
    await db.commit()
    return server.id, remote


def _material(client_id, ip):
    return seal(BOX, {"private_key": "cHJpdg==", "public_key": client_id, "psk": "srvpsk=", "ip": ip})


async def test_discover_containers_stores_supported_containers(db, clock):
    server, _ = await _discovered(db, clock)
    rows = (await db.execute(select(ServerContainer))).scalars().all()
    assert [r.container for r in rows] == [AWG]
    assert rows[0].params_json["port"] == "55424"


async def test_first_import_records_orphans_and_touches_nothing(db, clock):
    server, remote = await _discovered(db, clock, imported=False)
    await _reconcile(db, server, remote, clock)
    configs = (await db.execute(select(Config).order_by(Config.client_id))).scalars().all()
    assert [(c.client_id, c.user_id, c.name) for c in configs] == [("pubA=", None, "Old phone"),
                                                                   ("pubB=", None, "Imported pubB=")]
    assert unseal(BOX, configs[0].material_enc) == {"ip": "10.8.1.1", "psk": "srvpsk=", "imported": True}
    assert peers_on(remote) == {"pubA=", "pubB="}
    assert not [c for _, c in remote.commands if "syncconf" in c]
    server = await db.get(Server, server)
    assert server.imported_at is not None and server.last_ok_at == clock.now() and server.last_error is None


async def test_unknown_peer_added_later_is_imported_and_kept(db, clock):
    server, remote = await _discovered(db, clock)
    await _reconcile(db, server, remote, clock)
    remote.files[(AWG, "/opt/amnezia/awg/awg0.conf")] += "\n[Peer]\nPublicKey = pubZ=\nAllowedIPs = 10.8.1.9/32\n"
    await _reconcile(db, server, remote, clock)
    assert "pubZ=" in peers_on(remote)
    assert (await db.execute(select(Config.user_id).where(Config.client_id == "pubZ="))).scalar_one() is None


async def test_blocked_user_config_is_removed_and_restored(db, clock):
    server, remote = await _discovered(db, clock)
    user = User(display_name="u", max_configs=3)
    db.add(user)
    await db.flush()
    cfg = Config(user_id=user.id, server_id=server, container=AWG, name="c", client_id="pubC=",
                 material_enc=_material("pubC=", "10.8.1.3"), last_rx=500, last_tx=700)
    db.add(cfg)
    await db.commit()
    user_id, cfg_id = user.id, cfg.id

    await _reconcile(db, server, remote, clock)
    assert "pubC=" in peers_on(remote)

    user = await db.get(User, user_id)
    user.blocked_by = "admin"
    await db.commit()
    await _reconcile(db, server, remote, clock)
    assert "pubC=" not in peers_on(remote) and {"pubA=", "pubB="} <= peers_on(remote)
    assert await db.get(Config, cfg_id) is not None

    user = await db.get(User, user_id)
    user.blocked_by = None
    await db.commit()
    await _reconcile(db, server, remote, clock)
    assert "pubC=" in peers_on(remote)
    cfg = await db.get(Config, cfg_id)
    assert cfg.last_rx is None and cfg.last_tx is None  # counters restart with the re-added peer


async def test_blocked_orphan_is_removed_and_can_return(db, clock):
    server, remote = await _discovered(db, clock)
    await _reconcile(db, server, remote, clock)
    orphan = (await db.execute(select(Config).where(Config.client_id == "pubB="))).scalar_one()
    orphan.blocked_by = "admin"
    await db.commit()
    orphan_id = orphan.id
    await _reconcile(db, server, remote, clock)
    assert peers_on(remote) == {"pubA="}
    orphan = await db.get(Config, orphan_id)
    orphan.blocked_by = None
    await db.commit()
    await _reconcile(db, server, remote, clock)
    assert peers_on(remote) == {"pubA=", "pubB="}


async def test_deleted_config_is_removed_then_row_dropped(db, clock):
    server, remote = await _discovered(db, clock)
    await _reconcile(db, server, remote, clock)
    orphan = (await db.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
    orphan.deleted_at = clock.now()
    await db.commit()
    orphan_id = orphan.id
    await _reconcile(db, server, remote, clock)
    assert peers_on(remote) == {"pubB="}
    assert await db.get(Config, orphan_id) is None


async def test_deleting_user_is_dropped_after_configs_are_gone(db, clock):
    server, remote = await _discovered(db, clock)
    user = User(display_name="u", max_configs=3, deleting_at=clock.now())
    db.add(user)
    await db.flush()
    db.add(Config(user_id=user.id, server_id=server, container=AWG, name="c", client_id="pubB=",
                  deleted_at=clock.now()))
    await db.commit()
    user_id = user.id
    await _reconcile(db, server, remote, clock)
    assert peers_on(remote) == {"pubA="}
    assert await db.get(User, user_id) is None


async def test_remote_failure_changes_nothing(db, clock):
    server, remote = await _discovered(db, clock)
    await _reconcile(db, server, remote, clock)
    orphan = (await db.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
    orphan.deleted_at = clock.now()
    await db.commit()
    orphan_id = orphan.id
    remote.fail_with = "connection refused"
    with pytest.raises(RemoteError):
        await _reconcile(db, server, remote, clock)
    await db.rollback()
    assert await db.get(Config, orphan_id) is not None
