"""Traffic freshness per container and config, and recently active configs (load spec §4)."""

from datetime import timedelta

import pytest

from app.db.models import Config, ServerContainer
from app.drivers.base import get_driver
from app.services.traffic import active_clients
from app.ssh.conn import RemoteError
from tests.fakes import AWG, awg_server
from tests.unit.test_openvpn_driver import OVPN, ovpn_remote
from tests.unit.test_traffic_expiry import _collect, _server_with_config, dump
from tests.unit.test_xray_driver import XRAY, xray_remote

IKEV2 = "amnezia-ipsec"


def test_drivers_declare_whether_they_have_traffic_counters():
    with_counters = {c for c in (AWG, "amnezia-wireguard", XRAY, OVPN, "amnezia-socks5proxy")
                     if get_driver(c).traffic_counters}
    without = {c for c in (IKEV2, "amnezia-mtproxy", "amnezia-telemt") if not get_driver(c).traffic_counters}
    assert len(with_counters) == 5 and len(without) == 3


async def test_xray_stats_failure_is_an_error_not_an_empty_result():
    remote = xray_remote()
    remote.fail_on = "statsquery"
    with pytest.raises(RemoteError):
        await get_driver(XRAY).read_traffic(remote)


async def test_openvpn_missing_status_log_is_an_error():
    remote = ovpn_remote()
    remote.outputs["openvpn-status.log"] = "Common Name,Real Address,Bytes Received,Bytes Sent,Connected Since\n"
    await get_driver(OVPN).read_traffic(remote)
    script = next(s for who, s in remote.commands if who == OVPN and "openvpn-status.log" in s)
    assert "|| true" not in script  # a missing log must fail the command instead of reading as "no traffic"


async def _container(db, server_id) -> ServerContainer:
    db.expire_all()
    return await db.get(ServerContainer, (server_id, AWG))


async def test_a_failed_read_does_not_refresh_freshness(db, clock):
    server_id, _, _ = await _server_with_config(db, clock)
    remote = awg_server()
    remote.fail_on = "dump"
    await _collect(db, server_id, remote, clock)
    assert (await _container(db, server_id)).traffic_read_at is None


async def test_activity_needs_a_delta_between_two_recent_readings(db, clock):
    server_id, cfg_id, _ = await _server_with_config(db, clock)
    remote = awg_server()
    remote.dumps[AWG] = dump({"pubA=": (100, 1000)})
    await _collect(db, server_id, remote, clock)  # first reading: the whole history, not recent activity
    cfg = await db.get(Config, cfg_id)
    assert cfg.counter_read_at == clock.now() and cfg.last_active_at is None
    assert (await _container(db, server_id)).traffic_read_at == clock.now()

    clock.set(clock.now() + timedelta(minutes=5))
    remote.dumps[AWG] = dump({"pubA=": (200, 2000)})
    await _collect(db, server_id, remote, clock)
    active_at = clock.now()
    assert (await db.get(Config, cfg_id)).last_active_at == active_at

    clock.set(clock.now() + timedelta(minutes=5))  # no traffic
    await _collect(db, server_id, remote, clock)
    assert (await db.get(Config, cfg_id)).last_active_at == active_at

    clock.set(clock.now() + timedelta(minutes=20))  # a delta spanning an outage is not recent activity
    remote.dumps[AWG] = dump({"pubA=": (900, 9000)})
    await _collect(db, server_id, remote, clock)
    assert (await db.get(Config, cfg_id)).last_active_at == active_at


async def test_active_clients_counts_only_with_fresh_counters(db, clock):
    server_id, cfg_id, user_id = await _server_with_config(db, clock)
    now = clock.now()
    other = Config(user_id=user_id, server_id=server_id, container=AWG, name="d", client_id="pubB=",
                   last_active_at=now - timedelta(minutes=3))
    idle = Config(user_id=user_id, server_id=server_id, container=AWG, name="e", client_id="pubC=",
                  last_active_at=now - timedelta(hours=2))
    db.add_all([other, idle])
    (await db.get(Config, cfg_id)).last_active_at = now - timedelta(minutes=1)
    sc = await db.get(ServerContainer, (server_id, AWG))
    sc.traffic_read_at = now - timedelta(minutes=4)
    db.add(ServerContainer(server_id=server_id, container=IKEV2, params_json={}))  # no counters: not counted
    await db.commit()
    assert await active_clients(db, server_id, now) == 2

    sc.traffic_read_at = now - timedelta(minutes=20)  # stale counters: the count is unknown, not zero
    await db.commit()
    assert await active_clients(db, server_id, now) is None


async def test_active_clients_unknown_without_any_counters(db, clock):
    server_id, _, _ = await _server_with_config(db, clock)
    sc = await db.get(ServerContainer, (server_id, AWG))
    await db.delete(sc)
    db.add(ServerContainer(server_id=server_id, container=IKEV2, params_json={}))
    await db.commit()
    assert await active_clients(db, server_id, clock.now()) is None
