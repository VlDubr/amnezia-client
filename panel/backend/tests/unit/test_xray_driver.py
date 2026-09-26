import json
import urllib.parse

from app.drivers.base import ClientMaterial, get_driver
from app.render.vpnkey import decode_vpn_key
from tests.fakes import FakeRemote

XRAY = "amnezia-xray"
CONF = "/opt/amnezia/xray/server.json"
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"

SERVER_JSON = {
    "log": {"loglevel": "error"},
    "inbounds": [{
        "port": 443,
        "protocol": "vless",
        "settings": {"clients": [{"id": UUID_A, "flow": "xtls-rprx-vision"}], "decryption": "none"},
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {"dest": "www.googletagmanager.com:443", "fingerprint": "chrome",
                                "privateKey": "PRIV", "serverNames": ["www.googletagmanager.com"],
                                "shortIds": ["abcd1234"]},
        },
    }],
    "outbounds": [{"protocol": "freedom"}],
}


def xray_remote(conf=None) -> FakeRemote:
    r = FakeRemote([XRAY])
    r.files[(XRAY, CONF)] = json.dumps(conf or SERVER_JSON, indent=2)
    r.files[(XRAY, "/opt/amnezia/xray/xray_public.key")] = "PUBKEY\n"
    r.files[(XRAY, "/opt/amnezia/xray/xray_short_id.key")] = "abcd1234\n"
    r.files[(XRAY, "/opt/amnezia/xray/xray_uuid.key")] = UUID_A + "\n"
    return r


def conf(remote) -> dict:
    return json.loads(remote.files[(XRAY, CONF)])


def restarts(remote) -> int:
    return sum(1 for who, c in remote.commands if who == "host" and "docker restart amnezia-xray" in c)


async def test_read_params_and_list_clients():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    assert params["port"] == "443" and params["flow"] == "xtls-rprx-vision"
    assert params["public_key"] == "PUBKEY" and params["short_id"] == "abcd1234"
    clients = await driver.list_clients(remote)
    assert clients[UUID_A].data == {"secret": UUID_A, "flow": "xtls-rprx-vision"}


async def test_first_apply_prepares_stats_and_restarts_once():
    remote = xray_remote()
    driver = get_driver(XRAY)
    keep = ClientMaterial(UUID_A, {"secret": UUID_A, "flow": "xtls-rprx-vision"})
    await driver.apply(remote, [keep], {UUID_A})
    c = conf(remote)
    assert c["api"]["listen"] == "127.0.0.1:10085" and "StatsService" in c["api"]["services"]
    assert c["stats"] == {} and c["policy"]["levels"]["0"]["statsUserUplink"] is True
    client = c["inbounds"][0]["settings"]["clients"][0]
    assert client["email"] == UUID_A and client["level"] == 0
    assert restarts(remote) == 1
    await driver.apply(remote, [keep], {UUID_A})
    assert restarts(remote) == 1


async def test_apply_adds_removes_and_keeps_unknown():
    unknown = "33333333-3333-4333-8333-333333333333"
    base = json.loads(json.dumps(SERVER_JSON))
    base["inbounds"][0]["settings"]["clients"] += [{"id": UUID_B}, {"id": unknown}]
    remote = xray_remote(base)
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    new = await driver.create_material(remote, params, set())
    result = await driver.apply(remote, [ClientMaterial(UUID_A, {"secret": UUID_A}), new],
                                {UUID_A, UUID_B, new.client_id})
    ids = [c["id"] for c in conf(remote)["inbounds"][0]["settings"]["clients"]]
    assert set(ids) == {UUID_A, unknown, new.client_id}
    assert result.added == {new.client_id} and result.removed == {UUID_B}
    new_entry = next(c for c in conf(remote)["inbounds"][0]["settings"]["clients"] if c["id"] == new.client_id)
    assert new_entry["flow"] == "xtls-rprx-vision" and new.data["secret"] == new.client_id


async def test_read_traffic_parses_statsquery():
    remote = xray_remote()
    remote.outputs["statsquery"] = json.dumps({"stat": [
        {"name": f"user>>>{UUID_A}>>>traffic>>>uplink", "value": 100},
        {"name": f"user>>>{UUID_A}>>>traffic>>>downlink", "value": "2000"},
        {"name": f"user>>>{UUID_B}>>>traffic>>>downlink"},
    ]})
    traffic = await get_driver(XRAY).read_traffic(remote)
    assert (traffic[UUID_A].rx, traffic[UUID_A].tx) == (100, 2000)
    assert (traffic[UUID_B].rx, traffic[UUID_B].tx) == (0, 0)


async def test_read_traffic_tolerates_missing_api():
    remote = xray_remote()
    remote.fail_on = "statsquery"
    assert await get_driver(XRAY).read_traffic(remote) == {}


async def test_render_client_json_and_vless_link():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    material = ClientMaterial(UUID_B, {"secret": UUID_B, "flow": "xtls-rprx-vision"})
    out = driver.render(material, params, "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "NL")
    doc = decode_vpn_key(out.vpn_key)
    assert doc["defaultContainer"] == XRAY and doc["hostName"] == "vpn.example.com"
    client = json.loads(doc["containers"][0]["xray"]["last_config"])
    vnext = client["outbounds"][0]["settings"]["vnext"][0]
    assert vnext["address"] == "vpn.example.com" and vnext["port"] == 443
    assert vnext["users"][0] == {"id": UUID_B, "encryption": "none", "flow": "xtls-rprx-vision"}
    reality = client["outbounds"][0]["streamSettings"]["realitySettings"]
    assert reality == {"fingerprint": "chrome", "serverName": "www.googletagmanager.com", "publicKey": "PUBKEY",
                       "shortId": "abcd1234", "spiderX": ""}
    assert client["inbounds"][0]["protocol"] == "socks" and client["inbounds"][0]["port"] == 10808

    link = urllib.parse.urlparse(out.native)
    q = dict(urllib.parse.parse_qsl(link.query))
    assert link.scheme == "vless" and link.username == UUID_B and link.hostname == "vpn.example.com"
    assert link.port == 443 and q["security"] == "reality" and q["pbk"] == "PUBKEY" and q["sid"] == "abcd1234"
    assert q["sni"] == "www.googletagmanager.com" and q["flow"] == "xtls-rprx-vision" and q["type"] == "tcp"
    assert urllib.parse.unquote(link.fragment) == "NL"


async def test_before_start_writes_initial_reality_config():
    remote = FakeRemote([XRAY])
    remote.files[(XRAY, "/opt/amnezia/xray/xray_uuid.key")] = UUID_A + "\n"
    remote.files[(XRAY, "/opt/amnezia/xray/xray_private.key")] = "PRIV\n"
    remote.files[(XRAY, "/opt/amnezia/xray/xray_short_id.key")] = "abcd1234\n"
    await get_driver(XRAY).before_start(remote, {"XRAY_SERVER_PORT": "8443"})
    c = conf(remote)
    inbound = c["inbounds"][0]
    assert inbound["port"] == 8443 and inbound["settings"]["clients"][0]["id"] == UUID_A
    assert inbound["streamSettings"]["realitySettings"]["privateKey"] == "PRIV"
