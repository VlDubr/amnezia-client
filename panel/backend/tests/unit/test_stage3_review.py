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


async def test_socks5_leaves_open_proxy_alone_without_panel_users():
    from tests.unit.test_socks5_driver import BASE, CFG, S5, remote as s5_remote
    from app.drivers.base import get_driver

    r = s5_remote(BASE.replace("users admin:CL:adminpass\n", "").replace("auth strong", "auth none"))
    await get_driver(S5).apply(r, [], set())
    assert "auth none" in r.files[(S5, CFG)]
    assert not [c for w, c in r.commands if "docker restart" in c]


async def test_socks5_keeps_users_it_cannot_parse():
    from tests.unit.test_socks5_driver import BASE, CFG, S5, remote as s5_remote
    from app.drivers.base import ClientMaterial, get_driver

    r = s5_remote(BASE.replace("users admin:CL:adminpass", "users admin:CR:$1$abc$hash"))
    await get_driver(S5).apply(r, [ClientMaterial("u1", {"login": "u1", "secret": "p1"})], {"u1"})
    assert "users admin:CR:$1$abc$hash" in r.files[(S5, CFG)] and "users u1:CL:p1" in r.files[(S5, CFG)]


async def test_telemt_keeps_lines_it_does_not_manage():
    from tests.unit.test_telemt_driver import CFG, SECRET, TM, TOML, remote as tm_remote
    from app.drivers.base import ClientMaterial, get_driver

    text = TOML.replace(f'amnezia = "{SECRET}"', f'# admin note\n"my-proxy" = "{SECRET}"\nmy-proxy2 = "{SECRET}"')
    r = tm_remote(text)
    await get_driver(TM).apply(r, [ClientMaterial("p1", {"secret": "a" * 32})], {"p1"})
    out = r.files[(TM, CFG)]
    assert "# admin note" in out and f'"my-proxy" = "{SECRET}"' in out and f'my-proxy2 = "{SECRET}"' in out
    assert f'p1 = "{"a" * 32}"' in out
