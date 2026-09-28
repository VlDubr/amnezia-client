from datetime import timedelta

import pytest

from app.db.models import Server, ServerSample
from app.services.load_summary import history, levels, series, windows


async def _server(db, **kw) -> Server:
    s = Server(name=kw.pop("name", "nl"), host="h", ssh_port=22, ssh_user="root", ssh_secret_enc="x", **kw)
    db.add(s)
    await db.commit()
    return s


def _sample(server_id, ts, cpu=None, mem=None, rx=None, tx=None, clients=None, disk=None):
    return ServerSample(server_id=server_id, ts=ts, boot_id="b", uptime_s=1, cpu_busy=1, cpu_total=2, cpu_pct=cpu,
                        mem_pct=mem, rx_mbps=rx, tx_mbps=tx, active_clients=clients, disk_pct=disk)


async def test_window_averages_valid_points_of_the_last_15_minutes(db, clock):
    s = await _server(db)
    now = clock.now()
    db.add_all([_sample(s.id, now - timedelta(minutes=m), cpu=10.0 * m, mem=40.0, rx=100.0, tx=300.0, clients=4)
                for m in range(1, 6)])
    db.add(_sample(s.id, now - timedelta(minutes=6), cpu=None, mem=40.0))           # CPU rate unknown
    db.add(_sample(s.id, now - timedelta(minutes=30), cpu=99.0, mem=99.0))          # outside the window
    await db.commit()
    w = (await windows(db, [s.id], now))[s.id]
    assert w.cpu.avg == pytest.approx(30.0) and w.cpu.points == 5
    assert w.cpu.last_at == now - timedelta(minutes=1)
    assert w.mem.points == 6
    assert w.net_mbps.avg == pytest.approx(300.0)   # max(in, out) per sample
    assert w.clients.avg == pytest.approx(4.0)


async def test_a_server_without_samples_has_an_empty_window(db, clock):
    s = await _server(db)
    w = (await windows(db, [s.id], clock.now()))[s.id]
    assert w.cpu.points == 0 and w.cpu.avg is None and w.cpu.last_at is None


async def test_history_percentiles_peaks_and_coverage(db, clock):
    s = await _server(db)
    now = clock.now()
    db.add_all([_sample(s.id, now - timedelta(minutes=m), cpu=float(m % 100), mem=50.0, rx=10.0, tx=20.0,
                        clients=3, disk=70.0) for m in range(24 * 60)])
    db.add(_sample(s.id, now - timedelta(days=3), cpu=5.0, mem=5.0, rx=900.0, tx=100.0))
    await db.commit()
    h = await history(db, s, now)
    assert h.p95_24h["cpu"] == pytest.approx(94.0, abs=1.0)
    assert h.hours_24h["cpu"] == pytest.approx(24.0, abs=0.1)
    assert h.days_7d["mem"] == pytest.approx(1.0, abs=0.01)
    assert h.peak_net_mbps_7d == pytest.approx(900.0)
    assert h.last_at == now and h.last_disk_pct == pytest.approx(70.0) and h.last_clients == 3


async def test_series_make_gaps_explicit(db, clock):
    s = await _server(db)
    now = clock.now()
    for m in (60, 59, 58, 30, 29):  # a gap between 58 and 30 minutes ago
        db.add(_sample(s.id, now - timedelta(minutes=m), cpu=10.0, mem=20.0))
    db.add(_sample(s.id, now - timedelta(days=2), cpu=50.0, mem=60.0))
    await db.commit()

    day = await series(db, s.id, now, "24h")
    assert [p["cpu"] for p in day] == [10.0, 10.0, 10.0, None, 10.0, 10.0]

    week = await series(db, s.id, now, "7d")
    assert len(week) == 7 * 24 * 4                      # every 15-minute bucket, empty ones as nulls
    filled = [p for p in week if p["cpu"] is not None]
    assert len(filled) == 3 and {p["cpu"] for p in filled} == {10.0, 50.0}


async def test_levels_for_several_servers_in_one_call(db, clock):
    now = clock.now()
    busy = await _server(db, name="busy")
    calm = await _server(db, name="calm", bandwidth_mbps=1000)
    empty = await _server(db, name="empty")
    for m in range(1, 8):
        db.add(_sample(busy.id, now - timedelta(minutes=m), cpu=90.0, mem=30.0))
        db.add(_sample(calm.id, now - timedelta(minutes=m), cpu=10.0, mem=30.0, rx=100.0, tx=50.0))
    await db.commit()
    result = await levels(db, [busy, calm, empty], now)
    assert result[busy.id][:2] == ("high", pytest.approx(90.0))
    assert result[calm.id] == ("low", pytest.approx(30.0), True)
    assert result[empty.id] == ("unknown", None, False)
