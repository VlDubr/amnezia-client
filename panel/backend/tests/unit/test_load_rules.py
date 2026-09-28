from datetime import UTC, datetime, timedelta

import pytest

from app.domain.load import (
    Capacity,
    History,
    LoadWindow,
    MetricWindow,
    load_level,
    recommendations,
    recommended_eligible,
    utilisations,
)

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
FRESH = NOW - timedelta(minutes=1)


def mw(avg, points=15, last_at=FRESH) -> MetricWindow:
    return MetricWindow(avg=avg, points=points, last_at=last_at if avg is not None else None)


def window(cpu=20.0, mem=30.0, net=100.0, clients=10.0, **kw) -> LoadWindow:
    values = {"cpu": mw(cpu), "mem": mw(mem), "net_mbps": mw(net), "clients": mw(clients)}
    values.update(kw)
    return LoadWindow(**values)


FULL = Capacity(bandwidth_mbps=1000, expected_clients=100)
NONE = Capacity(bandwidth_mbps=None, expected_clients=None)


def history(**kw) -> History:
    values = {"p95_24h": {"cpu": 30.0, "mem": 30.0, "net": 200.0, "clients": 20.0},
              "hours_24h": {"cpu": 24.0, "mem": 24.0, "net": 24.0, "clients": 24.0},
              "p95_7d": {"cpu": 30.0, "mem": 30.0, "net": 200.0, "clients": 20.0},
              "days_7d": {"cpu": 7.0, "mem": 7.0, "net": 7.0, "clients": 7.0},
              "peak_net_mbps_7d": 400.0, "last_at": FRESH, "last_disk_pct": 40.0, "last_clients": 10}
    values.update(kw)
    return History(**values)


def codes(recs) -> list[str]:
    return [r.code for r in recs]


@pytest.mark.parametrize("cpu,level", [(49.9, "low"), (50.0, "medium"), (80.0, "medium"), (80.1, "high")])
def test_level_boundaries(cpu, level):
    assert load_level(window(cpu=cpu, mem=10.0), NONE, NOW) == (level, cpu)


def test_level_is_the_worst_qualifying_metric():
    # channel 900/1000 = 90 % beats CPU 20 %
    assert load_level(window(net=900.0), FULL, NOW) == ("high", 90.0)
    # without a width the channel does not count
    assert load_level(window(net=900.0), NONE, NOW) == ("low", 30.0)
    # clients 60/100
    assert load_level(window(clients=60.0), FULL, NOW) == ("medium", 60.0)


def test_metrics_need_enough_fresh_points():
    few = window(net=900.0, net_mbps=mw(900.0, points=4))
    assert "net" not in utilisations(few, FULL, NOW)
    stale = window(net_mbps=mw(900.0, last_at=NOW - timedelta(minutes=11)))
    assert "net" not in utilisations(stale, FULL, NOW)


def test_unknown_without_cpu_or_memory():
    assert load_level(window(cpu=None), FULL, NOW) == ("unknown", None)
    assert load_level(window(mem=None), FULL, NOW) == ("unknown", None)
    too_few = LoadWindow(cpu=mw(10.0, points=3), mem=mw(30.0), net_mbps=mw(1.0), clients=mw(1.0))
    assert load_level(too_few, FULL, NOW) == ("unknown", None)


def test_recommended_needs_a_good_level_and_complete_telemetry():
    assert recommended_eligible(window(), FULL, NOW)
    assert not recommended_eligible(window(cpu=85.0), FULL, NOW)            # high
    assert not recommended_eligible(window(cpu=None), FULL, NOW)            # unknown
    # width set but no channel data: a low level would hide missing telemetry
    assert not recommended_eligible(window(net_mbps=mw(None, points=0)), FULL, NOW)
    # width not set: the channel is not required
    assert recommended_eligible(window(net_mbps=mw(None, points=0)), NONE, NOW)


def test_quiet_well_configured_server_has_room_to_grow():
    recs = recommendations(window(), history(), FULL, [], None, NOW)
    assert codes(recs) == ["room_to_grow"]


def test_room_to_grow_needs_five_days_and_every_metric():
    assert "room_to_grow" not in codes(recommendations(window(), history(days_7d={"cpu": 4.0, "mem": 7.0, "net": 7.0,
                                                                                    "clients": 7.0}),
                                                       FULL, [], None, NOW))
    assert "room_to_grow" not in codes(recommendations(window(), history(), NONE, [], None, NOW))


def test_no_data():
    recs = recommendations(window(cpu=None, mem=None), history(last_at=NOW - timedelta(minutes=30)), FULL, [],
                           "ssh: timeout", NOW)
    assert codes(recs)[0] == "no_data" and recs[0].severity == "warning"
    assert recs[0].params["error"] == "ssh: timeout"


def test_sustained_high_load_warnings():
    h = history(p95_24h={"cpu": 85.0, "mem": 90.0, "net": 850.0, "clients": 20.0})
    assert {"cpu_high", "memory_high", "channel_high"} <= set(codes(recommendations(window(), h, FULL, [], None,
                                                                                    NOW)))
    few_hours = history(p95_24h={"cpu": 85.0, "mem": 30.0, "net": 1.0, "clients": 1.0},
                        hours_24h={"cpu": 11.0, "mem": 24.0, "net": 24.0, "clients": 24.0})
    assert "cpu_high" not in codes(recommendations(window(), few_hours, FULL, [], None, NOW))


def test_disk_clients_bandwidth_and_untracked():
    recs = codes(recommendations(window(), history(last_disk_pct=90.0, last_clients=150, peak_net_mbps_7d=1200.0),
                                 FULL, ["IKEv2"], None, NOW))
    assert {"disk_full", "clients_over", "bandwidth_exceeded", "untracked_protocols"} <= set(recs)
    unset = codes(recommendations(window(), history(), NONE, [], None, NOW))
    assert {"bandwidth_unset", "clients_unset"} <= set(unset)


def test_warnings_come_before_info():
    recs = recommendations(window(), history(last_disk_pct=90.0), NONE, [], None, NOW)
    severities = [r.severity for r in recs]
    assert severities == sorted(severities, key=lambda s: s != "warning")
