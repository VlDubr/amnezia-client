import copy
import json
import time
import urllib.parse

import pytest
import yaml

from app.drivers.base import ClientMaterial, get_driver
from app.drivers.xray import _derive_public_key, _id_hash
from app.render.vpnkey import decode_vpn_key
from tests.fakes import FakeRemote

XRAY = "amnezia-xray"
CONF = "/opt/amnezia/xray/server.json"
UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"
# A real X25519 pair (private = bytes 1..32), so read_params derives the public key locally.
PRIV_KEY = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA"
PUB_KEY = "B6N8vBQgk8i3VdwbEOhstCY3StFqqFPtC9_AsrhtHHw"
VERSION_LINE = "Xray 26.7.28 (Xray, Penetrates Everything.) Custom (go1.26.0 linux/amd64)"

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
                                "privateKey": PRIV_KEY, "serverNames": ["www.googletagmanager.com"],
                                "shortIds": ["abcd1234"], "minClientVer": "0.0.0", "maxClientVer": "",
                                "maxTimeDiff": 0},
        },
    }],
    "outbounds": [{"protocol": "freedom"}],
}


def xray_remote(conf=None) -> FakeRemote:
    r = FakeRemote([XRAY])
    r.files[(XRAY, CONF)] = json.dumps(conf or SERVER_JSON, indent=2)
    r.files[(XRAY, "/opt/amnezia/xray/xray_public.key")] = PUB_KEY + "\n"
    r.files[(XRAY, "/opt/amnezia/xray/xray_short_id.key")] = "abcd1234\n"
    r.files[(XRAY, "/opt/amnezia/xray/xray_uuid.key")] = UUID_A + "\n"
    r.outputs["xray version"] = VERSION_LINE
    return r


def two_client_conf() -> dict:
    conf = copy.deepcopy(SERVER_JSON)
    conf["inbounds"][0]["settings"]["clients"].append({"id": UUID_B, "flow": "xtls-rprx-vision"})
    return conf


def conf(remote) -> dict:
    return json.loads(remote.files[(XRAY, CONF)])


def restarts(remote) -> int:
    return sum(1 for who, c in remote.commands if who == "host" and "docker restart amnezia-xray" in c)


def rendered_client(out) -> dict:
    return json.loads(decode_vpn_key(out.vpn_key)["containers"][0]["xray"]["last_config"])


def report_of(out) -> str:
    return next(f.content for f in out.extra_files if f.kind == "invariants")


def test_derive_public_key_matches_the_rfc7748_vector():
    priv = "dwdtCnMYpX08FsFyUbJmRd9ML4frwJkqsXf7pR25LCo"  # RFC 7748 §6.1 Alice's private key
    assert _derive_public_key(priv) == "hSDwCYkwp1R0i33ctD73Wg2_Og0mOBr066SpjqqbTmo"


async def test_read_params_and_list_clients():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    assert params["port"] == "443" and params["flow"] == "xtls-rprx-vision"
    assert params["public_key"] == PUB_KEY and params["public_key_source"] == "derived"
    assert params["short_id"] == "abcd1234"
    assert params["core_version"] == "26.7.28"
    # the private key is never sent back to the server (no x25519 command with the key in it)
    assert not any("x25519" in c for _, c in remote.commands)
    assert params["client_flows"] == {_id_hash(UUID_A): "xtls-rprx-vision"}
    assert "privateKey" not in params["stream"]["realitySettings"]  # the server key never enters params_json
    clients = await driver.list_clients(remote)
    assert clients[UUID_A].data == {"secret": UUID_A, "flow": "xtls-rprx-vision"}


async def test_read_params_takes_keys_from_the_live_server_json_not_stale_files():
    remote = xray_remote()
    remote.files[(XRAY, "/opt/amnezia/xray/xray_public.key")] = "STALE\n"
    remote.files[(XRAY, "/opt/amnezia/xray/xray_short_id.key")] = "deadbeef\n"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["shortIds"] = ["6a8522b78b7cbccc"]
    remote.files[(XRAY, CONF)] = json.dumps(c)
    params = await get_driver(XRAY).read_params(remote)
    assert params["public_key"] == PUB_KEY and params["public_key_source"] == "derived"
    assert params["short_id"] == "6a8522b78b7cbccc"


async def test_read_params_public_key_falls_back_to_the_key_file():
    c = copy.deepcopy(SERVER_JSON)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] = "not-a-key"
    remote = xray_remote(c)
    params = await get_driver(XRAY).read_params(remote)
    assert params["public_key"] == PUB_KEY and params["public_key_source"] == "file"


async def test_dest_loop_by_name_and_by_ip():
    driver = get_driver(XRAY)
    remote = xray_remote()
    remote.host = "vpn.example.com"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = "vpn.example.com:443"
    remote.files[(XRAY, CONF)] = json.dumps(c)
    assert (await driver.read_params(remote))["dest_loop"] is True

    remote = xray_remote()
    remote.host = "31.76.54.100"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = "172.29.172.1:8443"
    remote.files[(XRAY, CONF)] = json.dumps(c)
    assert (await driver.read_params(remote))["dest_loop"] is False


async def test_dest_loop_resolves_the_site_domain(monkeypatch):
    def fake_getaddrinfo(host, port):
        return [(2, 1, 6, "", ("31.76.54.100", 0))] if host in ("vlblog.online", "31.76.54.100") else []

    monkeypatch.setattr("app.drivers.xray.socket.getaddrinfo", fake_getaddrinfo)
    remote = xray_remote()
    remote.host = "31.76.54.100"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = "vlblog.online:443"
    remote.files[(XRAY, CONF)] = json.dumps(c)
    assert (await get_driver(XRAY).read_params(remote))["dest_loop"] is True


async def test_dest_loop_matches_equivalent_ipv6_literals():
    remote = xray_remote()
    remote.host = "2001:db8::1"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = "[2001:db8:0:0:0:0:0:1]:443"
    remote.files[(XRAY, CONF)] = json.dumps(c)
    assert (await get_driver(XRAY).read_params(remote))["dest_loop"] is True


async def test_dns_hang_yields_an_inconclusive_verdict(monkeypatch):
    monkeypatch.setattr("app.drivers.xray.DEST_LOOP_TIMEOUT_S", 0.2)

    def slow_resolver(*a):
        time.sleep(2)  # outlives the timeout; its thread stays in the bounded DNS executor
        return False

    monkeypatch.setattr("app.drivers.xray._same_ip", slow_resolver)
    remote = xray_remote()
    remote.host = "31.76.54.100"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = "vlblog.online:443"
    remote.files[(XRAY, CONF)] = json.dumps(c)
    params = await get_driver(XRAY).read_params(remote)
    assert params["dest_loop"] is None
    out = get_driver(XRAY).render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "31.76.54.100", ("", ""), "x")
    assert "could not verify" in report_of(out)  # inconclusive is a WARN, not a FAIL


async def test_dns_thread_startup_failure_does_not_leak_a_slot(monkeypatch):
    class RefusingThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr("app.drivers.xray.threading.Thread", RefusingThread)
    remote = xray_remote()
    remote.host = "31.76.54.100"
    c = conf(remote)
    c["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = "vlblog.online:443"
    remote.files[(XRAY, CONF)] = json.dumps(c)
    driver = get_driver(XRAY)
    assert (await driver.read_params(remote))["dest_loop"] is None
    # both slots must still be free: a working resolver gives a real verdict afterwards
    monkeypatch.undo()
    monkeypatch.setattr("app.drivers.xray._same_ip", lambda *a: True)
    assert (await driver.read_params(remote))["dest_loop"] is True


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


async def test_render_mobile_profile_and_vless_link():
    remote = xray_remote(two_client_conf())
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    material = ClientMaterial(UUID_B, {"secret": UUID_B, "flow": "xtls-rprx-vision"})
    out = driver.render(material, params, "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "NL")
    doc = decode_vpn_key(out.vpn_key)
    assert doc["defaultContainer"] == XRAY and doc["hostName"] == "vpn.example.com"
    client = rendered_client(out)
    assert set(client) == {"log", "inbounds", "outbounds"}  # nothing else: no dns/routing/mux/sniffing
    assert client["log"] == {"loglevel": "error"}
    assert client["inbounds"] == [{"listen": "127.0.0.1", "port": 10808, "protocol": "socks",
                                   "settings": {"udp": True}}]
    vless, frag = client["outbounds"]
    vnext = vless["settings"]["vnext"][0]
    assert vnext["address"] == "vpn.example.com" and vnext["port"] == 443
    assert vnext["users"][0] == {"id": UUID_B, "encryption": "none", "flow": "xtls-rprx-vision"}
    stream = vless["streamSettings"]
    assert stream["network"] == "tcp" and stream["security"] == "reality"
    assert stream["tcpSettings"] == {"header": {"type": "none"}}
    assert stream["realitySettings"] == {"fingerprint": "firefox", "serverName": "www.googletagmanager.com",
                                         "publicKey": PUB_KEY, "shortId": "abcd1234", "spiderX": ""}
    assert stream["sockopt"] == {"dialerProxy": "frag"}
    assert frag == {"tag": "frag", "protocol": "freedom",
                    "settings": {"fragment": {"packets": "tlshello", "length": "100-200", "interval": "10-20"}}}

    link = urllib.parse.urlparse(out.native)
    q = dict(urllib.parse.parse_qsl(link.query))
    assert link.scheme == "vless" and link.username == UUID_B and link.hostname == "vpn.example.com"
    assert link.port == 443 and q["security"] == "reality" and q["pbk"] == PUB_KEY and q["sid"] == "abcd1234"
    assert q["sni"] == "www.googletagmanager.com" and q["flow"] == "xtls-rprx-vision" and q["type"] == "tcp"
    assert q["fp"] == "chrome"  # the link is for clients without fragmentation, not for the mobile profile
    assert urllib.parse.unquote(link.fragment) == "NL"


async def test_render_uses_the_live_flow_not_the_stored_one():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    material = ClientMaterial(UUID_A, {"secret": UUID_A, "flow": ""})  # stored before the server got vision
    client = rendered_client(driver.render(material, params, "h", ("", ""), "x"))
    assert client["outbounds"][0]["settings"]["vnext"][0]["users"][0]["flow"] == "xtls-rprx-vision"


async def test_downloads_include_json_yaml_and_the_invariant_report():
    remote = xray_remote(two_client_conf())
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    material = ClientMaterial(UUID_B, {"secret": UUID_B, "flow": "xtls-rprx-vision"})
    out = driver.render(material, params, "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "Мой ноутбук")
    assert out.native_filename == "Moy_noutbuk.txt"
    by_kind = {f.kind: f for f in out.extra_files}
    assert set(by_kind) == {"xray_json", "mihomo_yaml", "invariants"}
    assert by_kind["xray_json"].filename == "Moy_noutbuk.json"
    assert by_kind["mihomo_yaml"].filename == "Moy_noutbuk.yaml"
    assert by_kind["invariants"].filename == "Moy_noutbuk_invariants.txt"
    # The JSON file carries the same client config the AmneziaVPN key does.
    assert json.loads(by_kind["xray_json"].content) == rendered_client(out)
    assert by_kind["xray_json"].content.startswith("{\n    \"log\"")
    report = by_kind["invariants"].content
    assert "[OK  ]" in report and "vless:// link" in report and "does NOT carry fragment" in report


async def test_mihomo_yaml_profile():
    remote = xray_remote(two_client_conf())
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    material = ClientMaterial(UUID_B, {"secret": UUID_B, "flow": "xtls-rprx-vision"})
    out = driver.render(material, params, "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "NL")
    doc = yaml.safe_load(next(f.content for f in out.extra_files if f.kind == "mihomo_yaml"))
    [proxy] = doc["proxies"]
    assert proxy == {"name": "NL", "type": "vless", "server": "vpn.example.com", "port": 443, "uuid": UUID_B,
                     "network": "tcp", "udp": True, "tls": True, "flow": "xtls-rprx-vision",
                     "servername": "www.googletagmanager.com", "client-fingerprint": "chrome",
                     "reality-opts": {"public-key": PUB_KEY, "short-id": "abcd1234",
                                      "support-x25519mlkem768": False}}
    assert "alpn" not in proxy and "xhttp-opts" not in proxy


@pytest.mark.parametrize("name", ["PROXY", "DIRECT", "reject"])
async def test_mihomo_proxy_name_avoids_reserved_names(name):
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "h", ("", ""), name)
    doc = yaml.safe_load(next(f.content for f in out.extra_files if f.kind == "mihomo_yaml"))
    proxy_name = doc["proxies"][0]["name"]
    assert proxy_name.upper() not in {"PROXY", "DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}
    assert doc["proxy-groups"][0]["proxies"] == [proxy_name, "DIRECT"]


async def test_invariant_warnings_ship_with_the_config():
    c = two_client_conf()
    rs = c["inbounds"][0]["streamSettings"]["realitySettings"]
    rs.update({"minClientVer": "1.0.0", "maxClientVer": "2.0.0", "maxTimeDiff": 60})
    remote = xray_remote(c)
    remote.outputs["xray version"] = "Xray 26.8.1 (Xray, Penetrates Everything.)"
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "h", ("", ""), "x")
    report = report_of(out)
    assert "[WARN] core version 26.8.1 != 26.7.28" in report
    assert "[WARN] minClientVer '1.0.0'" in report and "REALITY authentication failed" in report
    assert "[WARN] maxClientVer '2.0.0' / maxTimeDiff 60" in report
    assert "[FAIL]" not in report


@pytest.mark.parametrize("mutate, message", [
    (lambda rs: rs.update(dest="vpn.example.com:443"), "dest loop"),
    (lambda rs: rs.update(serverNames=[]), "serverNames is empty"),
    (lambda rs: rs.update(shortIds=[]), "shortIds is empty"),
    (lambda rs: rs.pop("dest"), "dest is not set"),
])
async def test_render_blocks_on_failed_invariants(mutate, message):
    c = copy.deepcopy(SERVER_JSON)
    mutate(c["inbounds"][0]["streamSettings"]["realitySettings"])
    remote = xray_remote(c)
    remote.host = "vpn.example.com"
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    with pytest.raises(ValueError, match=message):
        driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "vpn.example.com", ("", ""), "x")


async def test_render_blocks_on_a_uuid_missing_from_the_server():
    remote = xray_remote()  # only UUID_A lives on the server
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    with pytest.raises(ValueError, match="not present in the live server.json"):
        driver.render(ClientMaterial(UUID_B, {"secret": UUID_B}), params, "h", ("", ""), "x")


async def test_render_blocks_on_a_malformed_uuid():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    for bad in (UUID_A + "tail", "{" + UUID_A + "}", UUID_A.replace("-", "")):
        with pytest.raises(ValueError, match="not a valid UUID"):
            driver.render(ClientMaterial(bad, {"secret": bad}), params, "h", ("", ""), "x")


async def test_render_accepts_an_imported_uppercase_uuid():
    c = copy.deepcopy(SERVER_JSON)
    c["inbounds"][0]["settings"]["clients"] = [{"id": UUID_A.upper(), "flow": "xtls-rprx-vision"}]
    remote = xray_remote(c)
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    out = driver.render(ClientMaterial(UUID_A.upper(), {"secret": UUID_A.upper()}), params, "h", ("", ""), "x")
    client = rendered_client(out)
    assert client["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"] == UUID_A.upper()
    assert urllib.parse.urlparse(out.native).username == UUID_A.upper()


@pytest.mark.parametrize("key, value", [("public_key", "short"), ("public_key", PUB_KEY + "="),
                                        ("short_id", "abc"), ("short_id", "zzzz"), ("short_id", "a" * 18)])
async def test_render_blocks_on_bad_reality_keys(key, value):
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    params[key] = value
    with pytest.raises(ValueError, match="publicKey|shortId"):
        driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "h", ("", ""), "x")


async def test_tls_server_with_stale_reality_settings_leaks_no_private_key():
    c = copy.deepcopy(SERVER_JSON)
    c["inbounds"][0]["streamSettings"] = {
        "network": "tcp", "security": "tls",
        "tlsSettings": {"serverName": "vpn.example.com",
                        "certificates": [{"keyFile": "/etc/x.key", "key": ["TLS-SECRET-KEY"]}]},
        "realitySettings": {"privateKey": PRIV_KEY, "serverNames": ["old.site"], "shortIds": ["abcd1234"]},
    }
    remote = xray_remote(c)
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    assert PRIV_KEY not in json.dumps(params) and "TLS-SECRET-KEY" not in json.dumps(params)
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "h", ("", ""), "x")
    stream = rendered_client(out)["outbounds"][0]["streamSettings"]
    assert "realitySettings" not in stream and "certificates" not in stream["tlsSettings"]
    assert all(PRIV_KEY not in f.content and "TLS-SECRET-KEY" not in f.content for f in out.extra_files)


async def test_legacy_params_with_a_private_key_do_not_leak_it_into_a_tls_config():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    # params_json as an older driver stored it: the whole stream, privateKey included, now security tls
    params["stream"] = {"network": "tcp", "security": "tls", "tlsSettings": {"serverName": "vpn.example.com"},
                        "realitySettings": {"privateKey": PRIV_KEY}}
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A, "flow": "xtls-rprx-vision"}),
                        params, "h", ("", ""), "x")
    assert PRIV_KEY not in json.dumps(rendered_client(out))
    assert all(PRIV_KEY not in f.content for f in out.extra_files)


async def test_xhttp_profile_has_no_flow_no_fragment_and_no_yaml():
    c = copy.deepcopy(SERVER_JSON)
    c["inbounds"][0]["settings"]["clients"] = [{"id": UUID_A}]
    c["inbounds"][0]["streamSettings"] = {
        "network": "xhttp", "security": "reality",
        "xhttpSettings": {"host": "site.example", "path": "/x", "mode": "auto"},
        "realitySettings": c["inbounds"][0]["streamSettings"]["realitySettings"],
    }
    remote = xray_remote(c)
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A, "flow": "xtls-rprx-vision"}),
                        params, "h", ("", ""), "x")
    client = rendered_client(out)
    [outbound] = client["outbounds"]
    assert "flow" not in outbound["settings"]["vnext"][0]["users"][0]
    assert outbound["streamSettings"]["network"] == "xhttp"
    assert "sockopt" not in outbound["streamSettings"]
    assert {f.kind for f in out.extra_files} == {"xray_json", "invariants"}
    assert "xhttp clients have no flow" in report_of(out)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(out.native).query))
    assert q["type"] == "xhttp" and "flow" not in q and q["path"] == "/x"


async def test_legacy_cached_params_still_render():
    # params_json stored before this driver version: no client_flows/core_version/dest_loop facts.
    remote = xray_remote()
    driver = get_driver(XRAY)
    fresh = await driver.read_params(remote)
    legacy = {k: fresh[k] for k in ("port", "stream", "flow", "public_key", "short_id")}
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A, "flow": "xtls-rprx-vision"}),
                        legacy, "h", ("", ""), "x")
    assert rendered_client(out)["outbounds"][0]["protocol"] == "vless"
    report = report_of(out)
    assert "core version unknown" in report and "[FAIL]" not in report


async def test_render_options_override_the_profiles_and_ship_in_the_report():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    options = {"mobile_fingerprint": "ios", "fragment": {"length": "50-100", "interval": "5-10"},
               "mihomo_fingerprint": "firefox", "spider_x": "/blog"}
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "h", ("", ""), "x", options)
    vless, frag = rendered_client(out)["outbounds"]
    assert vless["streamSettings"]["realitySettings"]["fingerprint"] == "ios"
    assert vless["streamSettings"]["realitySettings"]["spiderX"] == "/blog"
    assert frag["settings"]["fragment"] == {"packets": "tlshello", "length": "50-100", "interval": "5-10"}
    doc = yaml.safe_load(next(f.content for f in out.extra_files if f.kind == "mihomo_yaml"))
    assert doc["proxies"][0]["client-fingerprint"] == "firefox"
    report = report_of(out)
    assert "Render options (admin overrides)" in report
    # the profile descriptions reflect the effective values, not the defaults
    assert "REALITY fingerprint ios" in report and "tlshello fragment 50-100/5-10" in report
    assert "mihomo: firefox fingerprint" in report
    assert "fingerprint firefox," not in report and "100-200/10-20" not in report


async def test_render_options_partial_override_keeps_the_other_defaults():
    remote = xray_remote()
    driver = get_driver(XRAY)
    params = await driver.read_params(remote)
    out = driver.render(ClientMaterial(UUID_A, {"secret": UUID_A}), params, "h", ("", ""), "x",
                        {"fragment": {"length": "50-100"}})
    vless, frag = rendered_client(out)["outbounds"]
    assert vless["streamSettings"]["realitySettings"]["fingerprint"] == "firefox"
    assert frag["settings"]["fragment"] == {"packets": "tlshello", "length": "50-100", "interval": "10-20"}
    doc = yaml.safe_load(next(f.content for f in out.extra_files if f.kind == "mihomo_yaml"))
    assert doc["proxies"][0]["client-fingerprint"] == "chrome"
    assert "Render options (admin overrides)" in report_of(out)


def test_effective_render_options_merges_sparse_overrides():
    from app.drivers.xray import DEFAULT_RENDER_OPTIONS

    driver = get_driver(XRAY)
    assert driver.effective_render_options(None) == DEFAULT_RENDER_OPTIONS
    assert driver.effective_render_options({}) == DEFAULT_RENDER_OPTIONS
    eff = driver.effective_render_options({"mobile_fingerprint": "ios", "fragment": {"length": "50-100"}})
    assert eff["mobile_fingerprint"] == "ios"
    assert eff["fragment"] == {"length": "50-100", "interval": "10-20"}
    assert eff["mihomo_fingerprint"] == "chrome" and eff["spider_x"] == ""
    assert DEFAULT_RENDER_OPTIONS["fragment"] == {"length": "100-200", "interval": "10-20"}  # not mutated


def test_render_options_model_validation():
    from pydantic import ValidationError

    from app.drivers.xray import XrayRenderOptions

    XrayRenderOptions.model_validate({"mobile_fingerprint": "ios", "spider_x": "/",
                                      "fragment": {"length": "50-100", "interval": "0-0"}})
    for bad in ({"mobile_fingerprint": "chrome"}, {"mihomo_fingerprint": "netscape"},
                {"fragment": {"length": "200-100"}}, {"fragment": {"length": "0-10"}},
                {"fragment": {"interval": "10-70000"}}, {"fragment": {"length": "50"}},
                {"fragment": {"length": "１００-２００"}},  # fullwidth Unicode digits: Xray cannot parse them
                {"fragment": {"lenght": "50-100"}}, {"fragmnt": {"length": "50-100"}},  # typos must fail
                {"spider_x": "blog"}, {"spider_x": "/a\nb"}, {"spider_x": "/a\u0085b"}):  # C0 and C1 controls
        with pytest.raises(ValidationError):
            XrayRenderOptions.model_validate(bad)


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
