"""Regression tests for the stage 3 final review."""

import json

from sqlalchemy import select

from app.db.models import Config, Server
from app.security.secretbox import SecretBox
from app.services.reconcile import reconcile_server
from app.services.servers import discover_containers
from tests.conftest import MASTER_KEY
from tests.fakes import remote_factory_for
from tests.unit.test_xray_driver import CONF, SERVER_JSON, UUID_A, XRAY, xray_remote

BOX = SecretBox(MASTER_KEY)


async def test_imported_xray_client_revoked_in_qt_stays_removed(db, clock):
    remote = xray_remote()
    server = Server(name="s", host="h", ssh_port=22, ssh_user="r", ssh_secret_enc="x", imported_at=clock.now())
    db.add(server)
    await db.commit()
    await discover_containers(db, server, remote, clock)
    await db.commit()
    sid = server.id
    await reconcile_server(db, sid, remote_factory_for(remote), clock, BOX)
    # the admin revokes the imported client in the Qt app
    conf = json.loads(remote.files[(XRAY, CONF)])
    conf["inbounds"][0]["settings"]["clients"] = []
    remote.files[(XRAY, CONF)] = json.dumps(conf)
    await reconcile_server(db, sid, remote_factory_for(remote), clock, BOX)
    await reconcile_server(db, sid, remote_factory_for(remote), clock, BOX)
    clients = json.loads(remote.files[(XRAY, CONF)])["inbounds"][0]["settings"]["clients"]
    assert UUID_A not in [c["id"] for c in clients]
    db.expire_all()
    assert (await db.execute(select(Config).where(Config.client_id == UUID_A))).first() is None


__all__ = ["SERVER_JSON"]


async def test_broken_container_does_not_stop_the_others(db, clock):
    from app.db.models import User
    from app.services.materials import seal
    from tests.fakes import AWG, awg_server, peers_on

    remote = awg_server()
    remote.containers.append(XRAY)
    remote.files[(XRAY, CONF)] = "{not json"  # a broken or third-party Xray config
    server = Server(name="s", host="h", ssh_port=22, ssh_user="r", ssh_secret_enc="x", imported_at=clock.now())
    db.add(server)
    await db.commit()
    sid = server.id
    await discover_containers(db, server, remote, clock)  # must not fail on Xray, and still records it
    user = User(display_name="u", max_configs=3)
    db.add(user)
    await db.flush()
    db.add(Config(user_id=user.id, server_id=sid, container=AWG, name="c", client_id="pubC=",
                  material_enc=seal(BOX, {"private_key": "k", "public_key": "pubC=", "psk": "p", "ip": "10.8.1.3"})))
    await db.commit()
    await reconcile_server(db, sid, remote_factory_for(remote), clock, BOX)
    assert "pubC=" in peers_on(remote)
    db.expire_all()
    s = await db.get(Server, sid)
    assert s.last_error and XRAY in s.last_error
