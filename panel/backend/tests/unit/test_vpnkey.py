import base64
import json
import struct
import zlib

import pytest

from app.render.qr import qr_svg
from app.render.vpnkey import decode_vpn_key, encode_vpn_key


def test_roundtrip():
    obj = {"hostName": "203.0.113.10", "containers": [{"container": "amnezia-awg2"}], "dns1": "1.1.1.1"}
    key = encode_vpn_key(obj)
    assert key.startswith("vpn://") and "=" not in key and "+" not in key and "/" not in key[6:]
    assert decode_vpn_key(key) == obj


def test_payload_is_qcompress_format():
    obj = {"a": "б" * 50}
    raw = base64.urlsafe_b64decode(encode_vpn_key(obj)[6:] + "==")
    (length,) = struct.unpack(">I", raw[:4])
    payload = zlib.decompress(raw[4:])
    assert length == len(payload)
    assert json.loads(payload) == obj


def test_decode_accepts_qt_style_key():
    # qCompress(json): 4-byte big-endian length + zlib stream, base64url without padding, as ExportController emits
    data = json.dumps({"hostName": "h"}, indent=4).encode()
    qt = struct.pack(">I", len(data)) + zlib.compress(data, 8)
    key = "vpn://" + base64.urlsafe_b64encode(qt).decode().rstrip("=")
    assert decode_vpn_key(key) == {"hostName": "h"}


def test_decode_rejects_garbage():
    with pytest.raises(ValueError):
        decode_vpn_key("vpn://@@@")


def test_qr_svg():
    svg = qr_svg("vpn://abc")
    assert svg.lstrip().startswith("<?xml") or svg.lstrip().startswith("<svg")
