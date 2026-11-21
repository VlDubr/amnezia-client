"""Sealing and resolving the optional SSH jump host of a server."""

import json

from app.db.models import Server
from app.security.secretbox import SecretBox
from app.services.servers import jump_target, seal_jump_secret, seal_ssh_secret, ssh_target
from tests.conftest import MASTER_KEY

BOX = SecretBox(MASTER_KEY)


def server_with_jump(jump: dict | None) -> Server:
    return Server(name="de-1", host="31.76.54.100", ssh_port=22, ssh_user="root",
                  ssh_secret_enc=seal_ssh_secret(BOX, "pw", None),
                  ssh_jump_enc=seal_jump_secret(BOX, jump) if jump else None)


def test_jump_secret_round_trip():
    jump = {"host": "194.87.216.254", "port": 22, "user": "root", "password": "juMp-p@ss-9F2", "private_key": None,
            "host_key": "ssh-ed25519 AAAA"}
    enc = seal_jump_secret(BOX, jump)
    assert "juMp-p@ss-9F2" not in enc
    assert json.loads(BOX.decrypt(enc)) == jump


def test_jump_target_none_without_a_sealed_jump():
    assert jump_target(server_with_jump(None), BOX) is None


def test_ssh_target_carries_the_jump_with_its_own_credentials():
    jump = {"host": "194.87.216.254", "port": 22022, "user": "jumpuser", "password": None,
            "private_key": "KEY", "host_key": "ssh-ed25519 AAAA"}
    target = ssh_target(server_with_jump(jump), BOX)
    assert target.host == "31.76.54.100" and target.jump is not None
    assert target.jump.host == "194.87.216.254" and target.jump.port == 22022
    assert target.jump.user == "jumpuser" and target.jump.private_key == "KEY"
    assert target.jump.password is None and target.jump.host_key == "ssh-ed25519 AAAA"


def test_jump_target_defaults_missing_port_to_22():
    target = jump_target(server_with_jump({"host": "j", "user": "root", "password": "p"}), BOX)
    assert target.port == 22 and target.jump is None


def test_ssh_target_without_jump_stays_positional_compatible():
    target = ssh_target(server_with_jump(None), BOX)
    assert target.jump is None
