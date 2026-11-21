"""Cascaded servers: client configs are rendered with the relay address, while the REALITY invariants
are checked against the real server host the panel read the live params from."""

import copy
import json
from types import SimpleNamespace

import pytest

from app.db.models import Config, Server, ServerContainer
from app.drivers.base import get_driver
from app.errors import ApiError
from app.render.vpnkey import decode_vpn_key
from app.security.secretbox import SecretBox
from app.services.configs import export_config
from app.services.materials import seal
from tests.conftest import MASTER_KEY
from tests.unit.test_xray_driver import UUID_B, two_client_conf, xray_remote

XRAY = "amnezia-xray"
RELAY = "89.37.193.163"
SERVER_HOST = "31.76.54.100"


class FakeDB:
    def __init__(self, objects: dict) -> None:
        self._objects = objects

    async def get(self, model, pk):
        return self._objects[model]


async def params_with_dest(dest: str) -> dict:
    """Live params of the Germany server, read over SSH with remote.host = the real server address."""
    conf = copy.deepcopy(two_client_conf())
    # An IP dest (not a domain): dest_loop is decided locally, without DNS.
    conf["inbounds"][0]["streamSettings"]["realitySettings"]["dest"] = dest
    remote = xray_remote(conf)
    remote.host = SERVER_HOST
    return await get_driver(XRAY).read_params(remote)


def state_for(params: dict, client_host: str | None):
    box = SecretBox(MASTER_KEY)
    cfg = Config(server_id=1, container=XRAY, client_id=UUID_B, name="Phone",
                 material_enc=seal(box, {"secret": UUID_B, "flow": "xtls-rprx-vision"}))
    server = Server(id=1, name="de-1", host=SERVER_HOST, ssh_port=22, ssh_user="root", ssh_secret_enc="x",
                    client_host=client_host)
    sc = ServerContainer(server_id=1, container=XRAY, params_json=params)
    state = SimpleNamespace(secretbox=box, settings=SimpleNamespace(dns1="1.1.1.1", dns2="1.0.0.1"))
    return state, cfg, FakeDB({Server: server, ServerContainer: sc})


async def test_export_config_renders_with_the_relay_address():
    params = await params_with_dest("93.184.216.34:443")
    assert params["dest_loop"] is False  # checked against the real host, not the relay
    state, cfg, db = state_for(params, RELAY)
    out = await export_config(db, state, cfg)
    doc = decode_vpn_key(out["vpn_key"])
    assert doc["hostName"] == RELAY
    client = json.loads(doc["containers"][0]["xray"]["last_config"])
    vnext = client["outbounds"][0]["settings"]["vnext"][0]
    assert vnext["address"] == RELAY
    assert out["native"].startswith(f"vless://{UUID_B}@{RELAY}:443")
    assert SERVER_HOST not in out["native"]


async def test_export_config_without_client_host_still_uses_the_server_host():
    params = await params_with_dest("93.184.216.34:443")
    state, cfg, db = state_for(params, None)
    out = await export_config(db, state, cfg)
    assert decode_vpn_key(out["vpn_key"])["hostName"] == SERVER_HOST


async def test_dest_loop_invariant_is_checked_against_the_real_host_not_the_relay():
    # The dest points back at the real server: even though clients connect to the relay, the config must not ship.
    params = await params_with_dest(f"{SERVER_HOST}:443")
    assert params["dest_loop"] is True
    state, cfg, db = state_for(params, RELAY)
    with pytest.raises(ApiError) as e:
        await export_config(db, state, cfg)
    assert e.value.code == "render_failed" and "dest loop" in e.value.message


async def test_legacy_params_without_dest_loop_do_not_false_fail_on_the_relay():
    # Legacy params (no dest_loop key): the string fallback in _invariants compares dest with the render host.
    # With a relay that differs from the real server, a dest equal to the relay must NOT be reported as a loop
    # of the real server; and a dest equal to the real server is caught by the fresh read_params path above.
    params = await params_with_dest("93.184.216.34:443")
    params.pop("dest_loop", None)  # simulate legacy params cached before the fact existed
    state, cfg, db = state_for(params, RELAY)
    out = await export_config(db, state, cfg)  # must not raise: dest is a third-party site
    assert decode_vpn_key(out["vpn_key"])["hostName"] == RELAY
