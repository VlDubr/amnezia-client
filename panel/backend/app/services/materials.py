"""Client material (keys, addresses) is stored encrypted in configs.material_enc."""

import json
from typing import Any

from app.security.secretbox import SecretBox


def seal(box: SecretBox, data: dict[str, Any]) -> str:
    return box.encrypt(json.dumps(data, separators=(",", ":")))


def unseal(box: SecretBox, token: str | None) -> dict[str, Any]:
    return json.loads(box.decrypt(token)) if token else {}


def has_private_part(data: dict[str, Any]) -> bool:
    """Whether the config can be issued again: WireGuard/OpenVPN need the client's private key, which imported
    configs lack; Xray, SOCKS5 and Telegram proxies only need a secret the server itself holds."""
    return bool(data.get("private_key") or data.get("secret"))
