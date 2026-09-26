from app.drivers.wgconf import Peer, parse

SERVER_CONF = """[Interface]
PrivateKey = srvpriv=
Address = 10.8.1.0/24
ListenPort = 55424
Jc = 5
H1 = 1
# I1 = <r 2><b 0x85>
# I2 =

[Peer]
PublicKey = pubA=
PresharedKey = psk=
AllowedIPs = 10.8.1.1/32

[Peer]
PublicKey = pubB=
PresharedKey = psk=
AllowedIPs = 10.8.1.2/32
"""


def test_parse_interface_and_peers():
    conf = parse(SERVER_CONF)
    values = conf.interface_values()
    assert values["Address"] == "10.8.1.0/24" and values["ListenPort"] == "55424" and values["Jc"] == "5"
    assert values["I1"] == "<r 2><b 0x85>"
    assert "I2" not in values
    assert [p.public_key for p in conf.peers] == ["pubA=", "pubB="]
    assert conf.peers[1].ip == "10.8.1.2" and conf.peers[1].psk == "psk="


def test_dump_roundtrip_is_stable():
    conf = parse(SERVER_CONF)
    assert parse(conf.dump()).dump() == conf.dump()
    assert conf.dump().startswith("[Interface]\nPrivateKey = srvpriv=\n")


def test_add_and_remove_peers():
    conf = parse(SERVER_CONF)
    conf.peers = [p for p in conf.peers if p.public_key != "pubA="]
    conf.peers.append(Peer.new("pubC=", "psk=", "10.8.1.3"))
    text = conf.dump()
    assert "pubA=" not in text
    assert "[Peer]\nPublicKey = pubC=\nPresharedKey = psk=\nAllowedIPs = 10.8.1.3/32\n" in text


def test_parse_legacy_conf_without_trailing_newline():
    conf = parse("[Interface]\nAddress = 10.8.1.0/24\n[Peer]\nPublicKey = x\nAllowedIPs = 10.8.1.5/32")
    assert conf.peers[0].ip == "10.8.1.5"
