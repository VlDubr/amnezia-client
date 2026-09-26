import urllib.parse

from app.drivers.base import ClientMaterial, get_driver
from tests.fakes import FakeRemote

MT = "amnezia-mtproxy"
MAIN = "a" * 32
EXTRA = "b" * 32
START = f"""#!/bin/sh
TAG_ARG=""
if [ -n "" ]; then
    TAG_ARG="-P "
fi
LISTEN_PORT=8443
ADDITIONAL_SECRETS_ARG=""
if [ -n "{EXTRA}" ]; then
    for S in $(echo "{EXTRA}" | tr ',' ' '); do
        ADDITIONAL_SECRETS_ARG="$ADDITIONAL_SECRETS_ARG -S $S"
    done
fi
exec mtproto-proxy -H ${{LISTEN_PORT}} -S ${{SECRET}} ${{ADDITIONAL_SECRETS_ARG}}
"""
META = f"mode=faketls\ndomain=ab.c\ntag=\nadditional={EXTRA}\npublic_host=\n"


def remote() -> FakeRemote:
    r = FakeRemote([MT])
    r.files[(MT, "/opt/amnezia/start.sh")] = START
    r.files[(MT, "/data/mtproxy-meta")] = META
    r.files[(MT, "/data/secret")] = MAIN + "\n"
    return r


def restarts(r) -> int:
    return sum(1 for w, c in r.commands if w == "host" and "docker restart amnezia-mtproxy" in c)


async def test_params_and_clients_leave_the_main_secret_alone():
    r = remote()
    d = get_driver(MT)
    assert await d.read_params(r) == {"port": "8443", "tls": True, "tls_domain": "ab.c", "public_host": ""}
    clients = await d.list_clients(r)
    assert set(clients) == {EXTRA} and clients[EXTRA].data == {"secret": EXTRA}


async def test_apply_rewrites_both_places_and_restarts_on_change_only():
    r = remote()
    d = get_driver(MT)
    m = await d.create_material(r, {}, set())
    assert m.client_id == m.data["secret"] and len(m.client_id) == 32
    result = await d.apply(r, [m], {m.client_id})
    start = r.files[(MT, "/opt/amnezia/start.sh")]
    assert start.count(f"{EXTRA},{m.client_id}") == 2
    assert 'if [ -n "" ]; then\n    TAG_ARG' in start  # the tag block is untouched
    assert f"additional={EXTRA},{m.client_id}" in r.files[(MT, "/data/mtproxy-meta")]
    assert result.added == {m.client_id} and restarts(r) == 1
    assert any("proxy-multi.conf ] || curl" in c for w, c in r.commands if w == MT)
    await d.apply(r, [m], {m.client_id})
    assert restarts(r) == 1
    result = await d.apply(r, [], {m.client_id, EXTRA})
    assert f'if [ -n "" ]' in r.files[(MT, "/opt/amnezia/start.sh")] and result.removed == {m.client_id, EXTRA}
    assert r.files[(MT, "/data/secret")] == MAIN + "\n"


async def test_render_link():
    d = get_driver(MT)
    out = d.render(ClientMaterial(EXTRA, {"secret": EXTRA}),
                   {"port": "8443", "tls": True, "tls_domain": "ab.c", "public_host": ""}, "1.2.3.4", ("", ""), "x")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(out.native).query))
    assert q == {"server": "1.2.3.4", "port": "8443", "secret": "ee" + EXTRA + b"ab.c".hex()} and out.vpn_key == ""
