import json
import urllib.parse

from app.drivers.base import ClientMaterial, get_driver
from tests.fakes import FakeRemote

S5 = "amnezia-socks5proxy"
CFG = "/usr/local/3proxy/conf/3proxy.cfg"
LOG = "/usr/local/3proxy/logs/3proxy.log"
BASE = """#!/bin/3proxy
config /usr/local/3proxy/conf/3proxy.cfg
timeouts 1 5 30 60 180 1800 15 60
users admin:CL:adminpass
log /usr/local/3proxy/logs/3proxy.log
auth strong
socks -p38080
"""


def remote(cfg=BASE) -> FakeRemote:
    r = FakeRemote([S5])
    r.files[(S5, CFG)] = cfg
    return r


def restarts(r) -> int:
    return sum(1 for w, c in r.commands if w == "host" and "docker restart amnezia-socks5proxy" in c)


async def test_params_and_clients():
    r = remote()
    d = get_driver(S5)
    assert (await d.read_params(r))["port"] == "38080"
    clients = await d.list_clients(r)
    assert clients["admin"].data == {"login": "admin", "secret": "adminpass"}


async def test_apply_rewrites_users_and_restarts_only_on_change():
    r = remote()
    d = get_driver(S5)
    m = await d.create_material(r, await d.read_params(r), set())
    assert m.client_id == m.data["login"] and len(m.data["secret"]) >= 20
    result = await d.apply(r, [m], {m.client_id})
    cfg = r.files[(S5, CFG)]
    assert f"users {m.client_id}:CL:{m.data['secret']}" in cfg and "users admin:CL:adminpass" in cfg
    assert cfg.index("users") < cfg.index("auth strong") < cfg.index("socks -p38080")
    assert result.added == {m.client_id} and restarts(r) == 1
    await d.apply(r, [m], {m.client_id})
    assert restarts(r) == 1
    result = await d.apply(r, [], {m.client_id, "admin"})
    assert "users" not in r.files[(S5, CFG)] and result.removed == {m.client_id, "admin"}
    assert "auth strong" in r.files[(S5, CFG)]  # never falls back to an open proxy


async def test_open_proxy_becomes_strong_auth():
    r = remote(BASE.replace("users admin:CL:adminpass\n", "").replace("auth strong", "auth none"))
    d = get_driver(S5)
    m = ClientMaterial("u1", {"login": "u1", "secret": "p1"})
    await d.apply(r, [m], {"u1"})
    assert "auth strong" in r.files[(S5, CFG)] and "auth none" not in r.files[(S5, CFG)]


async def test_traffic_from_json_log():
    r = remote()
    lines = [
        '{"time_unix":1, "auth":{"user":"u1"}, "bytes":{"sent":1000, "received":50}}',
        'garbage line',
        '{"time_unix":2, "auth":{"user":"u1"}, "bytes":{"sent":500, "received":25}}',
        '{"time_unix":3, "auth":{"user":"-"}, "bytes":{"sent":9, "received":9}}',
    ]
    text = "\n".join(lines) + "\n"
    r.outputs["wc -c"] = str(len(text.encode()))
    r.outputs["tail -c"] = text
    t = await get_driver(S5).read_traffic(r)
    assert (t["u1"].rx, t["u1"].tx) == (1500, 75) and "-" not in t


async def test_render_socks_link():
    r = remote()
    d = get_driver(S5)
    m = ClientMaterial("u1", {"login": "u1", "secret": "p@ss:/"})
    out = d.render(m, await d.read_params(r), "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "NL")
    link = urllib.parse.urlparse(out.native)
    assert out.vpn_key == "" and link.scheme == "socks5" and link.hostname == "vpn.example.com"
    assert link.port == 38080 and link.username == "u1" and urllib.parse.unquote(link.password) == "p@ss:/"
    assert json.dumps(out.native)
    assert d.render(m, await d.read_params(r), "h", ("", ""), "Ноутбук").native_filename == "Noutbuk.txt"
