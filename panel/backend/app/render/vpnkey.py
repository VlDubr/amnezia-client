"""`vpn://` keys as produced by the Qt client's ExportController: base64url(qCompress(json))."""

import base64
import binascii
import json
import struct
import zlib
from typing import Any

PREFIX = "vpn://"


def encode_vpn_key(obj: dict[str, Any]) -> str:
    data = json.dumps(obj, ensure_ascii=False, indent=4).encode()
    compressed = struct.pack(">I", len(data)) + zlib.compress(data, 8)  # Qt qCompress(data, 8)
    return PREFIX + base64.urlsafe_b64encode(compressed).decode().rstrip("=")


def decode_vpn_key(key: str) -> dict[str, Any]:
    body = key.strip().removeprefix(PREFIX)
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return json.loads(zlib.decompress(raw[4:]))
    except (binascii.Error, zlib.error, ValueError) as e:
        raise ValueError("not a valid vpn:// key") from e
