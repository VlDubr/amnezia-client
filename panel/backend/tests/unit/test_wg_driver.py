import base64
import json

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.drivers.base import ClientMaterial, get_driver
from app.render.vpnkey import decode_vpn_key
from tests.fakes import FakeRemote

AWG = "amnezia-awg2"
WG = "amnezia-wireguard"
AWG_CONF = "/opt/amnezia/awg/awg0.conf"
WG_CONF = "/opt/amnezia/wireguard/wg0.conf"

AWG_SERVER = """[Interface]
PrivateKey = c2VydmVycHJpdg==
Address = 10.8.1.0/24
ListenPort = 55424
Jc = 5
Jmin = 10
Jmax = 50
S1 = 12
H1 = 1
HeaderProtectionKey = aHBr
RandomTrailers = on
# I1 = <r 2><b 0x85>

[Peer]
PublicKey = pubA=
PresharedKey = srvpsk=
AllowedIPs = 10.8.1.1/32

[Peer]
PublicKey = pubB=
PresharedKey = srvpsk=
AllowedIPs = 10.8.1.2/32
"""


def awg_remote() -> FakeRemote:
    r = FakeRemote([AWG])
    r.files[(AWG, AWG_CONF)] = AWG_SERVER
    r.files[(AWG, "/opt/amnezia/awg/wireguard_server_public_key.key")] = "srvpub=\n"
    r.files[(AWG, "/opt/amnezia/awg/wireguard_psk.key")] = "srvpsk=\n"
    r.files[(AWG, "/opt/amnezia/awg/clientsTable")] = json.dumps(
        [{"clientId": "pubA=", "userData": {"clientName": "Old phone"}}])
    return r


def wg_remote() -> FakeRemote:
    r = FakeRemote([WG])
    r.files[(WG, WG_CONF)] = "[Interface]\nPrivateKey = x\nAddress = 10.8.1.0/24\nListenPort = 51820\n"
    r.files[(WG, "/opt/amnezia/wireguard/wireguard_server_public_key.key")] = "wgpub=\n"
    r.files[(WG, "/opt/amnezia/wireguard/wireguard_psk.key")] = "wgpsk=\n"
    return r


def syncconf_calls(remote, container):
    return [c for who, c in remote.commands if who == container and "syncconf" in c]


async def test_read_params():
    params = await get_driver(AWG).read_params(awg_remote())
    assert params["port"] == "55424"
    assert params["subnet_address"] == "10.8.1.0" and params["subnet_cidr"] == "24"
    assert params["server_public_key"] == "srvpub=" and params["psk"] == "srvpsk="
    assert params["awg"]["Jc"] == "5" and params["awg"]["I1"] == "<r 2><b 0x85>"
    assert params["protocol_version"] == "3.1"


async def test_list_clients_uses_clients_table_names():
    clients = await get_driver(AWG).list_clients(awg_remote())
    assert set(clients) == {"pubA=", "pubB="}
    assert clients["pubA="].name == "Old phone" and clients["pubB="].name is None
    assert clients["pubB="].data == {"ip": "10.8.1.2", "psk": "srvpsk="}


async def test_create_material_generates_keys_and_free_ip():
    remote = awg_remote()
    driver = get_driver(AWG)
    params = await driver.read_params(remote)
    m = await driver.create_material(remote, params, taken={"10.8.1.3"})
    assert m.data["ip"] == "10.8.1.4" and m.data["psk"] == "srvpsk="
    priv = X25519PrivateKey.from_private_bytes(base64.b64decode(m.data["private_key"]))
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    assert base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode() == m.client_id
    assert m.data["public_key"] == m.client_id


async def test_create_material_skips_network_broadcast_and_server_addresses():
    remote = wg_remote()
    driver = get_driver(WG)
    params = await driver.read_params(remote)
    m = await driver.create_material(remote, params, taken={f"10.8.1.{i}" for i in range(1, 254)})
    assert m.data["ip"] == "10.8.1.254"
    import pytest

    with pytest.raises(RuntimeError):
        await driver.create_material(remote, params, taken={f"10.8.1.{i}" for i in range(1, 255)})


async def test_apply_keeps_unknown_removes_known_undesired_and_adds_new():
    remote = awg_remote()
    driver = get_driver(AWG)
    new = ClientMaterial("pubC=", {"ip": "10.8.1.3", "psk": "srvpsk=", "private_key": "p", "public_key": "pubC="})
    result = await driver.apply(remote, [new], known_ids={"pubB=", "pubC="})
    conf = remote.files[(AWG, AWG_CONF)]
    assert "pubA=" in conf and "pubB=" not in conf and "PublicKey = pubC=" in conf
    assert "AllowedIPs = 10.8.1.3/32" in conf
    assert result.added == {"pubC="} and result.removed == {"pubB="}
    assert syncconf_calls(remote, AWG) == [f"awg syncconf awg0 <(awg-quick strip {AWG_CONF})"]


async def test_apply_without_changes_does_not_touch_server():
    remote = awg_remote()
    keep = ClientMaterial("pubB=", {"ip": "10.8.1.2", "psk": "srvpsk="})
    result = await get_driver(AWG).apply(remote, [keep], known_ids={"pubB="})
    assert result.added == set() and result.removed == set()
    assert not syncconf_calls(remote, AWG)
    assert not [c for _, c in remote.commands if c.startswith("write")]


async def test_apply_uses_wg_binary_for_wireguard_and_legacy_awg():
    remote = wg_remote()
    await get_driver(WG).apply(remote, [ClientMaterial("k=", {"ip": "10.8.1.9", "psk": "wgpsk="})], known_ids={"k="})
    assert syncconf_calls(remote, WG) == [f"wg syncconf wg0 <(wg-quick strip {WG_CONF})"]
    legacy = get_driver("amnezia-awg")
    assert (legacy.bin, legacy.iface, legacy.conf_path) == ("wg", "wg0", "/opt/amnezia/awg/wg0.conf")


async def test_read_traffic_parses_dump():
    remote = awg_remote()
    remote.dumps[AWG] = (
        "srvpriv=\tsrvpub=\t55424\toff\n"
        "pubA=\tsrvpsk=\t198.51.100.7:40000\t10.8.1.1/32\t1727350000\t1000\t2000\t25\n"
        "pubB=\tsrvpsk=\t(none)\t10.8.1.2/32\t0\t0\t0\toff\n"
    )
    traffic = await get_driver(AWG).read_traffic(remote)
    assert traffic["pubA="].rx == 1000 and traffic["pubA="].tx == 2000 and traffic["pubA="].session is None
    assert traffic["pubB="].rx == 0


async def test_render_awg_native_and_vpn_key():
    remote = awg_remote()
    driver = get_driver(AWG)
    params = await driver.read_params(remote)
    m = ClientMaterial("pubC=", {"ip": "10.8.1.3", "psk": "srvpsk=", "private_key": "cHJpdg==", "public_key": "pubC="})
    out = driver.render(m, params, "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "NL server")
    assert "Address = 10.8.1.3/32" in out.native and "PrivateKey = cHJpdg==" in out.native
    assert "Endpoint = vpn.example.com:55424" in out.native and "Jc = 5" in out.native
    assert "I1 = <r 2><b 0x85>" in out.native and "DNS = 1.1.1.1, 1.0.0.1" in out.native
    assert "S2 =" not in out.native and "$" not in out.native
    assert out.native_filename.endswith(".conf")
    doc = decode_vpn_key(out.vpn_key)
    assert doc["hostName"] == "vpn.example.com" and doc["defaultContainer"] == AWG
    assert doc["description"] == "NL server" and doc["dns1"] == "1.1.1.1"
    proto = doc["containers"][0]["awg"]
    assert doc["containers"][0]["container"] == AWG and proto["port"] == "55424" and proto["Jc"] == "5"
    assert proto["transport_proto"] == "udp" and proto["protocol_version"] == "3.1"
    client = json.loads(proto["last_config"])
    assert client["client_priv_key"] == "cHJpdg==" and client["client_ip"] == "10.8.1.3"
    assert client["server_pub_key"] == "srvpub=" and client["psk_key"] == "srvpsk=" and client["port"] == 55424
    assert client["clientId"] == "pubC=" and client["config"] == out.native
    assert client["allowed_ips"] == ["0.0.0.0/0", "::/0"] and client["persistent_keep_alive"] == "25-35"


async def test_render_wireguard_uses_wireguard_section():
    remote = wg_remote()
    driver = get_driver(WG)
    params = await driver.read_params(remote)
    m = ClientMaterial("k=", {"ip": "10.8.1.5", "psk": "wgpsk=", "private_key": "cHJpdg==", "public_key": "k="})
    out = driver.render(m, params, "1.2.3.4", ("1.1.1.1", "1.0.0.1"), "WG")
    assert "Jc" not in out.native and "Endpoint = 1.2.3.4:51820" in out.native
    doc = decode_vpn_key(out.vpn_key)
    client = json.loads(doc["containers"][0]["wireguard"]["last_config"])
    assert client["persistent_keep_alive"] == "25" and client["port"] == 51820
