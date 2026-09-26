import base64
import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_invite_key() -> str:
    raw = base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def normalize_invite_key(key: str) -> str:
    return "".join(ch for ch in key.upper() if ch not in "- \t")
