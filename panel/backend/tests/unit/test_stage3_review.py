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


async def test_openvpn_sums_devices_sharing_a_config():
    from app.drivers.base import get_driver
    from tests.unit.test_openvpn_driver import OVPN, ovpn_remote

    r = ovpn_remote()
    header = ("OpenVPN CLIENT LIST\nUpdated,x\nCommon Name,Real Address,Bytes Received,Bytes Sent,Connected Since\n")
    r.outputs["openvpn-status.log"] = header + (
        "oldphone,1.1.1.1:1,100,1000,2026-09-26 10:00:00\noldphone,2.2.2.2:2,10,20,2026-09-26 11:00:00\nROUTING TABLE\n")
    first = (await get_driver(OVPN).read_traffic(r))["oldphone"]
    assert (first.rx, first.tx) == (110, 1020)
    r.outputs["openvpn-status.log"] = header + (
        "oldphone,2.2.2.2:2,10,20,2026-09-26 11:00:00\noldphone,1.1.1.1:1,100,1000,2026-09-26 10:00:00\nROUTING TABLE\n")
    again = (await get_driver(OVPN).read_traffic(r))["oldphone"]
    assert again.session == first.session  # row order does not change the session


async def test_openvpn_retries_crl_after_a_partial_revoke():
    from app.drivers.base import get_driver
    from tests.unit.test_openvpn_driver import D, OVPN, ovpn_remote

    r = ovpn_remote()
    r.files[(OVPN, f"{D}/pki/index.txt")] += "R\t360101000000Z\t260101000000Z\t09\tunknown\t/CN=halfdone\n"
    await get_driver(OVPN).apply(r, [], {"halfdone"}, revoked={"halfdone"})
    assert any("easyrsa gen-crl" in c for w, c in r.commands if w == OVPN)


async def test_xray_tls_client_config_does_not_carry_server_certificates():
    import copy

    from app.drivers.base import ClientMaterial, get_driver
    from app.render.vpnkey import decode_vpn_key

    conf = copy.deepcopy(SERVER_JSON)
    conf["inbounds"][0]["streamSettings"] = {"network": "tcp", "security": "tls", "tlsSettings": {
        "serverName": "vpn.example.com", "alpn": ["h2"], "fingerprint": "chrome",
        "certificates": [{"certificateFile": "/etc/x.crt", "keyFile": "/etc/x.key", "key": ["SECRET-KEY"]}]}}
    r = xray_remote(conf)
    d = get_driver(XRAY)
    out = d.render(ClientMaterial(UUID_A, {"secret": UUID_A}), await d.read_params(r), "h", ("", ""), "x")
    text = decode_vpn_key(out.vpn_key)["containers"][0]["xray"]["last_config"]
    assert "SECRET-KEY" not in text and "certificates" not in text and "vpn.example.com" in text


async def test_socks5_traffic_reads_only_new_log_lines():
    import json as _json

    from app.drivers.base import get_driver
    from tests.unit.test_socks5_driver import LOG, S5, remote as s5_remote

    line = '{"auth":{"user":"u1"}, "bytes":{"sent":10, "received":100}}\n'
    r = s5_remote()
    r.outputs["wc -c"] = str(len(line))
    r.outputs["tail -c"] = line
    first = (await get_driver(S5).read_traffic(r))["u1"]
    assert (first.rx, first.tx) == (10, 100)
    state = _json.loads(r.files[(S5, LOG + ".panel-state")])
    assert state["offset"] == len(line)
    tails = [c for w, c in r.commands if "tail -c" in c]
    assert tails and f"tail -c +1 {LOG}" in tails[-1]

    r.outputs["wc -c"] = str(2 * len(line))  # one more line appended
    second = (await get_driver(S5).read_traffic(r))["u1"]
    assert (second.rx, second.tx) == (20, 200)  # cumulative, only the new line was added
    assert f"tail -c +{len(line) + 1} {LOG}" in [c for w, c in r.commands if "tail -c" in c][-1]
