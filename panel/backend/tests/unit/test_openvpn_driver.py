import json
import re

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.drivers.base import ClientMaterial, get_driver
from app.render.vpnkey import decode_vpn_key
from tests.fakes import FakeRemote

OVPN = "amnezia-openvpn"
D = "/opt/amnezia/openvpn"
SERVER_CONF = """port 1194
proto udp
dev tun
ca /opt/amnezia/openvpn/ca.crt
server 10.8.0.0 255.255.255.0
cipher AES-256-GCM
data-ciphers AES-256-GCM
auth SHA512
crl-verify /opt/amnezia/openvpn/crl.pem
status openvpn-status.log
tls-server
tls-auth /opt/amnezia/openvpn/ta.key 0
"""
INDEX = ("V\t360101000000Z\t\t01\tunknown\t/CN=AmneziaReq\n"
         "V\t360101000000Z\t\t02\tunknown\t/C=ORG/O=/CN=oldphone\n"
         "R\t360101000000Z\t260101000000Z\t03\tunknown\t/C=ORG/O=/CN=gone\n")


def ovpn_remote() -> FakeRemote:
    r = FakeRemote([OVPN])
    r.files[(OVPN, f"{D}/server.conf")] = SERVER_CONF
    r.files[(OVPN, f"{D}/pki/index.txt")] = INDEX
    r.files[(OVPN, f"{D}/ca.crt")] = "-----BEGIN CERTIFICATE-----\nCA\n-----END CERTIFICATE-----\n"
    r.files[(OVPN, f"{D}/ta.key")] = "#\n-----BEGIN OpenVPN Static key V1-----\nTA\n-----END OpenVPN Static key V1-----\n"

    def sign(container, script):
        m = re.search(r"sign-req client (\w+)", script)
        if m:  # easyrsa issues the certificate for the uploaded request
            r.files[(OVPN, f"{D}/pki/issued/{m.group(1)}.crt")] = "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE-----\n"
            r.files[(OVPN, f"{D}/pki/index.txt")] += f"V\t360101000000Z\t\t04\tunknown\t/C=ORG/O=/CN={m.group(1)}\n"

    r.hooks.append(sign)
    return r


def ccd(remote) -> dict[str, str]:
    return {path.rsplit("/", 1)[1]: text for (c, path), text in remote.files.items() if "/ccd/" in path}


def execs(remote) -> list[str]:
    return [c for who, c in remote.commands if who == OVPN]


async def test_list_clients_reads_valid_certificates():
    clients = await get_driver(OVPN).list_clients(ovpn_remote())
    assert set(clients) == {"oldphone"} and clients["oldphone"].data == {}


async def test_create_material_signs_a_request():
    remote = ovpn_remote()
    driver = get_driver(OVPN)
    params = await driver.read_params(remote)
    m = await driver.create_material(remote, params, set())
    assert re.fullmatch(r"[0-9a-f]{32}", m.client_id)
    req = remote.files[(OVPN, f"{D}/clients/{m.client_id}.req")]
    csr = x509.load_pem_x509_csr(req.encode())
    assert csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value == m.client_id
    key = serialization.load_pem_private_key(m.data["private_key"].encode(), None)
    assert key.key_size == 2048 and "BEGIN CERTIFICATE" in m.data["cert"]
    assert any(f"easyrsa import-req {D}/clients/{m.client_id}.req {m.client_id}" in c for c in execs(remote))


async def test_first_apply_prepares_ccd_and_management_once():
    remote = ovpn_remote()
    driver = get_driver(OVPN)
    await driver.apply(remote, [ClientMaterial("oldphone", {})], {"oldphone"})
    conf = remote.files[(OVPN, f"{D}/server.conf")]
    assert "client-config-dir /opt/amnezia/openvpn/ccd" in conf and "management 127.0.0.1 7505" in conf
    restarts = [c for w, c in remote.commands if w == "host" and "docker restart amnezia-openvpn" in c]
    assert len(restarts) == 1
    await driver.apply(remote, [ClientMaterial("oldphone", {})], {"oldphone"})
    assert len([c for w, c in remote.commands if w == "host" and "docker restart" in c]) == 1


async def test_block_unblock_and_revoke():
    remote = ovpn_remote()
    driver = get_driver(OVPN)
    result = await driver.apply(remote, [], {"oldphone"})
    assert ccd(remote)["oldphone"].strip() == "disable" and result.removed == {"oldphone"}
    assert any("kill oldphone" in c for c in execs(remote))

    result = await driver.apply(remote, [ClientMaterial("oldphone", {})], {"oldphone"})
    assert "oldphone" not in ccd(remote) and result.added == {"oldphone"}

    await driver.apply(remote, [], {"oldphone"}, revoked={"oldphone"})
    assert any("easyrsa revoke oldphone" in c for c in execs(remote))
    assert any("easyrsa gen-crl" in c and "cp pki/crl.pem" in c for c in execs(remote))


async def test_unknown_clients_are_left_alone():
    remote = ovpn_remote()
    await get_driver(OVPN).apply(remote, [], set())
    assert ccd(remote) == {}


async def test_read_traffic_from_status_log():
    remote = ovpn_remote()
    remote.outputs["openvpn-status.log"] = (
        "OpenVPN CLIENT LIST\nUpdated,2026-09-26 12:00:00\n"
        "Common Name,Real Address,Bytes Received,Bytes Sent,Connected Since\n"
        "oldphone,198.51.100.7:40000,1000,20000,2026-09-26 11:00:00\n"
        "ROUTING TABLE\nVirtual Address,Common Name,Real Address,Last Ref\nGLOBAL STATS\nEND\n")
    traffic = await get_driver(OVPN).read_traffic(remote)
    c = traffic["oldphone"]
    assert (c.rx, c.tx, c.session) == (1000, 20000, "198.51.100.7:40000@2026-09-26 11:00:00")


async def test_render_ovpn_and_vpn_key():
    remote = ovpn_remote()
    driver = get_driver(OVPN)
    params = await driver.read_params(remote)
    m = ClientMaterial("abc", {"private_key": "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----",
                               "cert": "-----BEGIN CERTIFICATE-----\nC\n-----END CERTIFICATE-----"})
    out = driver.render(m, params, "vpn.example.com", ("1.1.1.1", "1.0.0.1"), "NL")
    ovpn = out.native
    assert "remote vpn.example.com 1194" in ovpn and "proto udp" in ovpn and "cipher AES-256-GCM" in ovpn
    for block in ("<ca>", "<cert>", "<key>", "<tls-auth>"):
        assert block in ovpn
    assert "\nCA\n" in ovpn and "\nTA\n" in ovpn and "block-outside-dns" not in ovpn and "$" not in ovpn
    assert out.native_filename.endswith(".ovpn")
    doc = decode_vpn_key(out.vpn_key)
    proto = doc["containers"][0]["openvpn"]
    assert doc["defaultContainer"] == OVPN and proto["port"] == "1194" and proto["transport_proto"] == "udp"
    client = json.loads(proto["last_config"])
    assert client["config"] == ovpn and client["clientId"] == "abc"
