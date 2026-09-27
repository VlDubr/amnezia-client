import urllib.parse

from app.drivers.base import ClientMaterial, get_driver
from tests.fakes import FakeRemote

TM = "amnezia-telemt"
CFG = "/data/config.toml"
SECRET = "0123456789abcdef0123456789abcdef"
TOML = f"""[general]
use_middle_proxy = false

[general.modes]
classic = false
secure = false
tls = true

[server]
port = 443

[censorship]
tls_domain = "googletagmanager.com"

[access.users]
amnezia = "{SECRET}"
"""


def remote(text=TOML) -> FakeRemote:
    r = FakeRemote([TM])
    r.files[(TM, CFG)] = text
    return r


def restarts(r) -> int:
    return sum(1 for w, c in r.commands if w == "host" and "docker restart amnezia-telemt" in c)


async def test_params_and_clients():
    r = remote()
    d = get_driver(TM)
    params = await d.read_params(r)
    assert params == {"port": "443", "tls": True, "tls_domain": "googletagmanager.com", "public_host": ""}
    assert (await d.list_clients(r))["amnezia"].data == {"secret": SECRET}


async def test_apply_changes_users_section_and_restarts_only_on_change():
    r = remote()
    d = get_driver(TM)
    m = await d.create_material(r, {}, set())
    result = await d.apply(r, [m], {m.client_id})
    text = r.files[(TM, CFG)]
    assert f'{m.client_id} = "{m.data["secret"]}"' in text and f'amnezia = "{SECRET}"' in text
    assert "[censorship]" in text and result.added == {m.client_id} and restarts(r) == 1
    await d.apply(r, [m], {m.client_id})
    assert restarts(r) == 1
    await d.apply(r, [], {m.client_id})
    assert m.client_id not in r.files[(TM, CFG)] and restarts(r) == 2


async def test_render_links():
    d = get_driver(TM)
    m = ClientMaterial("p1", {"secret": SECRET})
    tls = d.render(m, {"port": "443", "tls": True, "tls_domain": "ab.c", "public_host": ""}, "1.2.3.4", ("", ""), "x")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(tls.native).query))
    assert tls.native.startswith("tg://proxy?") and q == {"server": "1.2.3.4", "port": "443",
                                                           "secret": "ee" + SECRET + b"ab.c".hex()}
    plain = d.render(m, {"port": "8443", "tls": False, "tls_domain": "", "public_host": "tg.example"}, "h", ("", ""),
                     "x")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(plain.native).query))
    assert q == {"server": "tg.example", "port": "8443", "secret": "dd" + SECRET} and plain.vpn_key == ""


async def test_link_uses_the_public_port_behind_nat():
    toml = TOML.replace("[server]\n", '[general.links]\nshow = "*"\npublic_port = 8443\n\n[server]\n', 1)
    assert "public_port" in toml
    params = await get_driver("amnezia-telemt").read_params(remote(toml))
    assert params["port"] == "8443"
