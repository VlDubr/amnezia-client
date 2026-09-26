import json
import re

from app.drivers.base import ClientMaterial, get_driver
from app.render.vpnkey import decode_vpn_key
from tests.fakes import FakeRemote

IK = "amnezia-ipsec"
CLIENTS = "/opt/amnezia/ikev2/clients"
CRL_LIST = "/opt/amnezia/ikev2/panel-revoked"


def revoked_ids(r) -> dict[str, str]:
    return dict(line.split() for line in r.files[(IK, CRL_LIST)].splitlines() if line.strip())


def remote() -> FakeRemote:
    r = FakeRemote([IK])
    r.outputs[f"ls {CLIENTS}"] = "oldphone.p12\nlaptop.p12\n"
    r.outputs["ca_cert_base64.p12"] = "Q0FCQVNF"
    r.outputs["Serial Number"] = "    Serial Number: 1234567 (0x12d687)\n"
    r.outputs["base64 -w0"] = "UDEyQkFTRTY0"
    r.files[(IK, CRL_LIST)] = ""
    return r


async def test_list_clients_from_p12_files():
    clients = await get_driver(IK).list_clients(remote())
    assert set(clients) == {"oldphone", "laptop"} and clients["laptop"].data == {}


async def test_create_material_issues_and_exports_certificate():
    r = remote()
    d = get_driver(IK)
    m = await d.create_material(r, await d.read_params(r), set())
    assert re.fullmatch(r"[a-z0-9]{16}", m.client_id)
    cmds = [c for w, c in r.commands if w == IK]
    assert any(f'certutil -z <(head -c 1024 /dev/urandom) -S -c "IKEv2 VPN CA" -n "{m.client_id}"' in c for c in cmds)
    assert any(f'pk12util -W "" -d sql:/etc/ipsec.d -n "{m.client_id}" -o "{CLIENTS}/{m.client_id}.p12"' in c
               for c in cmds)
    assert m.data == {"private_key": "UDEyQkFTRTY0", "serial": "1234567"}


async def test_block_unblock_rebuild_crl():
    r = remote()
    d = get_driver(IK)
    laptop = ClientMaterial("laptop", {"private_key": "x", "serial": "77"})
    oldphone = ClientMaterial("oldphone", {"serial": "88"})
    result = await d.apply(r, [oldphone], {"laptop", "oldphone"})
    assert result.removed == {"laptop"}
    assert revoked_ids(r) == {"laptop": "1234567"}  # serial looked up from the NSS database
    crl_cmds = [c for w, c in r.commands if w == IK and "crlutil -G" in c]
    assert crl_cmds and "addcert 1234567" in crl_cmds[-1]

    result = await d.apply(r, [oldphone, laptop], {"laptop", "oldphone"})
    assert result.added == {"laptop"} and revoked_ids(r) == {}


async def test_revoked_client_is_deleted_and_stays_in_crl():
    r = remote()
    d = get_driver(IK)
    await d.apply(r, [], {"laptop"}, revoked={"laptop"})
    cmds = [c for w, c in r.commands if w == IK]
    assert any(f"rm -f {CLIENTS}/laptop.p12" in c for c in cmds)
    assert "laptop" in revoked_ids(r)


async def test_nothing_changes_without_differences():
    r = remote()
    d = get_driver(IK)
    await d.apply(r, [ClientMaterial("laptop", {}), ClientMaterial("oldphone", {})], {"laptop", "oldphone"})
    assert not [c for w, c in r.commands if "crlutil" in c]


async def test_render_vpn_key_and_mobileconfig():
    r = remote()
    d = get_driver(IK)
    params = await d.read_params(r)
    out = d.render(ClientMaterial("laptop", {"private_key": "UDEy", "serial": "77"}), params, "vpn.example.com",
                   ("1.1.1.1", "1.0.0.1"), "NL")
    doc = decode_vpn_key(out.vpn_key)
    assert doc["defaultContainer"] == IK
    client = json.loads(doc["containers"][0]["ikev2"]["last_config"])
    assert client == {"hostName": "vpn.example.com", "userName": "laptop", "cert": "UDEy", "password": ""}
    assert "UDEy" in out.native and "vpn.example.com" in out.native and "Q0FCQVNF" in out.native
    assert "$" not in out.native and out.native_filename.endswith(".mobileconfig")
