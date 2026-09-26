import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_SIZE = 12


class SecretBox:
    """AES-256-GCM encryption for secrets stored in the database."""

    def __init__(self, key_b64: str):
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("master key must be 32 bytes (base64)")
        self._aead = AESGCM(key)

    def encrypt(self, data: bytes | str) -> str:
        if isinstance(data, str):
            data = data.encode()
        nonce = os.urandom(_NONCE_SIZE)
        return base64.b64encode(nonce + self._aead.encrypt(nonce, data, None)).decode()

    def decrypt(self, token: str) -> bytes:
        raw = base64.b64decode(token)
        try:
            return self._aead.decrypt(raw[:_NONCE_SIZE], raw[_NONCE_SIZE:], None)
        except InvalidTag as e:
            raise ValueError("cannot decrypt secret") from e
