import base64
import os
import re
from datetime import UTC, datetime

import pytest

from app.domain.clock import FixedClock
from app.security.passwords import hash_password, validate_password, verify_password
from app.security.ratelimit import RateLimiter
from app.security.secretbox import SecretBox
from app.security.tokens import new_invite_key, new_token, normalize_invite_key, sha256_hex


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple 9")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct horse battery staple 9")
    assert not verify_password(h, "wrong")


def test_verify_password_rejects_garbage_hash():
    assert not verify_password("not-a-hash", "x")


def test_password_policy():
    assert validate_password("short") == ["too_short"]
    assert validate_password("aaaaaaaaaaaa") == ["too_weak"]
    assert validate_password("Vq7#mZ2!rT9p@Lx") == []


def test_password_policy_uses_user_inputs():
    assert validate_password("ivanpetrov1990", user_inputs=["ivanpetrov1990"]) == ["too_weak"]


def test_secretbox_roundtrip():
    box = SecretBox(base64.b64encode(os.urandom(32)).decode())
    assert box.decrypt(box.encrypt("secret")) == b"secret"
    assert box.encrypt("secret") != box.encrypt("secret")


def test_secretbox_rejects_tampered_data():
    box = SecretBox(base64.b64encode(os.urandom(32)).decode())
    with pytest.raises(ValueError):
        box.decrypt(base64.b64encode(b"x" * 40).decode())


def test_secretbox_rejects_wrong_key_length():
    with pytest.raises(ValueError):
        SecretBox(base64.b64encode(b"short").decode())


def test_tokens():
    t = new_token()
    assert len(t) >= 43 and t != new_token()
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_invite_key_format_and_normalize():
    k = new_invite_key()
    assert re.fullmatch(r"([A-Z2-7]{4}-){6}[A-Z2-7]{2}", k)
    assert normalize_invite_key(k.lower().replace("-", " ")) == k.replace("-", "")


def test_ratelimiter_sliding_window():
    clk = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    rl = RateLimiter(2, 60, clk)
    assert rl.hit("a")
    assert rl.hit("a")
    assert not rl.hit("a")
    assert rl.hit("b")
    clk.advance(61)
    assert rl.hit("a")
