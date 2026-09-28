"""Load sampling (load spec §4): one sample per server and minute, a single sampler owner, bounded work."""

import asyncio
import contextlib
import itertools
import time
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import Server, ServerSample
from app.jobs import sampler as sampler_module
from app.jobs.sampler import Sampler
from app.services import metrics
from app.services.metrics import cleanup_samples, sample_server
from tests.fakes import FakeRemote, remote_factory_for
from tests.unit.test_metrics_parse import sample_text

SPECS = ("@@cpu\nmodel name\t: Test CPU\n@@nproc\n2\n@@mem\nMemTotal: 2000000 kB\n@@df\nFs 1-blocks Used Avail C M\n"
         "/dev/vda1 40000000000 1 2 1% /\n@@os\nPRETTY_NAME=\"Debian 12\"\n@@kernel\n6.1\n@@uptime\n1000.0 1\n")


def host(text: str) -> FakeRemote:
    r = FakeRemote([])
    r.host_outputs = {"@@boot": text, "@@cpu": SPECS, "/speed": "1000\n"}
    return r


async def _server(db, **kw) -> int:
    s = Server(name="nl", host="h", ssh_port=22, ssh_user="root", ssh_secret_enc="x", **kw)
    db.add(s)
    await db.commit()
    return s.id


async def _samples(db, server_id):
    db.expire_all()
    return (await db.execute(select(ServerSample).where(ServerSample.server_id == server_id)
                             .order_by(ServerSample.ts))).scalars().all()


def specs_runs(remote) -> int:
    return sum(1 for who, c in remote.commands if who == "host" and "@@cpu" in c)


async def test_samples_are_stored_with_rates_and_specs_once_a_day(db, sessionmaker, clock):
    sid = await _server(db)
    remote = host(sample_text(uptime="1000.00", cpu="cpu  4705 356 584 3699 23 23 0 0 0 0"))
    await sample_server(sessionmaker, sid, remote_factory_for(remote), clock)
    clock.advance(60)
    remote.host_outputs["@@boot"] = sample_text(uptime="1060.00", cpu="cpu  4805 356 584 3799 23 23 0 0 0 0")
    await sample_server(sessionmaker, sid, remote_factory_for(remote), clock)

    first, second = await _samples(db, sid)
    assert first.cpu_pct is None and first.mem_pct == pytest.approx(25.0) and first.iface == "eth0"
    assert second.cpu_pct == pytest.approx(50.0)
    assert second.active_clients is None  # no container with traffic counters
    server = await db.get(Server, sid)
    assert server.specs_json["cores"] == 2 and server.specs_json["link_mbps"] == 1000
    assert server.specs_json["iface"] == "eth0" and specs_runs(remote) == 1

    clock.advance(25 * 3600)
    await sample_server(sessionmaker, sid, remote_factory_for(remote), clock)
    assert specs_runs(remote) == 2


async def test_a_failure_is_recorded_apart_from_server_errors(db, sessionmaker, clock):
    sid = await _server(db, last_error="reconcile: old")
    remote = host(sample_text())
    remote.fail_with = "ssh: connection refused\x1b[31m"
    await sample_server(sessionmaker, sid, remote_factory_for(remote), clock)
    db.expire_all()
    server = await db.get(Server, sid)
    assert "connection refused" in server.metrics_error and "\x1b" not in server.metrics_error
    assert server.metrics_error_at == clock.now() and server.last_error == "reconcile: old"
    assert await _samples(db, sid) == []

    remote.fail_with = None
    await sample_server(sessionmaker, sid, remote_factory_for(remote), clock)
    db.expire_all()
    assert (await db.get(Server, sid)).metrics_error is None


async def test_a_server_deleted_during_sampling_is_skipped(db, sessionmaker, clock):
    sid = await _server(db)
    remote = host(sample_text())

    @asynccontextmanager
    async def deleting_factory(server):
        async with sessionmaker() as other:
            await other.delete(await other.get(Server, sid))
            await other.commit()
        yield remote

    await sample_server(sessionmaker, sid, deleting_factory, clock)
    assert (await db.execute(select(func.count()).select_from(ServerSample))).scalar_one() == 0


async def test_only_one_sampler_samples(db, engine, sessionmaker, clock, monkeypatch):
    await _server(db)
    calls: list[str] = []

    async def fake_sample(sm, sid, factory, clk):
        calls.append(factory)

    monkeypatch.setattr(sampler_module, "sample_server", fake_sample)
    first = Sampler(engine, sessionmaker, "first", clock)
    second = Sampler(engine, sessionmaker, "second", clock)
    try:
        await first.tick()
        await second.tick()
        assert calls == ["first"]
        await first.release()  # the owner goes away: the other one takes over
        await second.tick()
        assert calls == ["first", "second"]
    finally:
        await first.release()
        await second.release()


async def test_losing_the_leader_connection_cancels_the_tick(db, engine, sessionmaker, clock, monkeypatch):
    await _server(db)
    finished: list[int] = []

    async def slow_sample(sm, sid, factory, clk):
        await asyncio.sleep(5)
        finished.append(sid)

    monkeypatch.setattr(sampler_module, "sample_server", slow_sample)
    s = Sampler(engine, sessionmaker, None, clock, watchdog_s=0.05)
    try:
        assert await s.acquire()
        alive_calls = 0
        real_alive = s.alive

        async def flaky_alive():
            nonlocal alive_calls
            alive_calls += 1
            if alive_calls > 1:
                await s.drop()
                return False
            return await real_alive()

        s.alive = flaky_alive
        started = time.monotonic()
        await s.tick()
        assert time.monotonic() - started < 2 and finished == [] and not s.is_leader
    finally:
        await s.release()


async def test_ticks_never_overlap_and_shutdown_is_bounded(db, engine, sessionmaker, clock, monkeypatch):
    await _server(db)
    spans: list[tuple[float, float]] = []

    async def slow_sample(sm, sid, factory, clk):
        start = time.monotonic()
        await asyncio.sleep(0.15)
        spans.append((start, time.monotonic()))

    monkeypatch.setattr(sampler_module, "sample_server", slow_sample)
    s = Sampler(engine, sessionmaker, None, clock, interval_s=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(s.run_forever(stop))
    await asyncio.sleep(0.6)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert len(spans) >= 2
    assert all(later[0] >= earlier[1] for earlier, later in itertools.pairwise(spans))
    assert not s.is_leader


async def test_cleanup_removes_old_samples_in_batches(db, clock, monkeypatch):
    sid = await _server(db)
    now = clock.now()
    for age_days in (40, 35, 31, 29, 1):
        db.add(ServerSample(server_id=sid, ts=now - timedelta(days=age_days), boot_id="b", uptime_s=1,
                            cpu_busy=1, cpu_total=2))
    await db.commit()
    monkeypatch.setattr(metrics, "CLEANUP_BATCH", 2)
    assert await cleanup_samples(db, now) == 3
    assert len(await _samples(db, sid)) == 2


async def test_a_cancelled_tick_stops_its_sampling(db, engine, sessionmaker, clock, monkeypatch):
    """Final review I1: cancelling the tick (e.g. the shutdown timeout) must not leave samples running."""
    await _server(db)
    finished: list[int] = []

    async def slow_sample(sm, sid, factory, clk):
        await asyncio.sleep(0.5)
        finished.append(sid)

    monkeypatch.setattr(sampler_module, "sample_server", slow_sample)
    s = Sampler(engine, sessionmaker, None, clock)
    try:
        tick = asyncio.create_task(s.tick())
        await asyncio.sleep(0.1)
        tick.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tick
        await asyncio.sleep(0.7)
        assert finished == []
    finally:
        await s.release()


async def test_a_kick_never_samples_a_server_twice_at_once(db, engine, sessionmaker, clock, monkeypatch):
    """Final review I2: a kick during a tick must not start a second concurrent sample of the same server."""
    sid = await _server(db)
    running: set[int] = set()
    overlaps: list[int] = []

    async def slow_sample(sm, server_id, factory, clk):
        if server_id in running:
            overlaps.append(server_id)
        running.add(server_id)
        await asyncio.sleep(0.2)
        running.discard(server_id)

    monkeypatch.setattr(sampler_module, "sample_server", slow_sample)
    s = Sampler(engine, sessionmaker, None, clock)
    try:
        tick = asyncio.create_task(s.tick())
        await asyncio.sleep(0.05)
        s.kick(sid)
        await tick
        await asyncio.sleep(0.3)
        assert overlaps == []
    finally:
        await s.release()


async def test_a_stalled_leader_connection_counts_as_lost(engine, sessionmaker, clock):
    """Final review I3: the leadership probe has a deadline."""
    s = Sampler(engine, sessionmaker, None, clock, watchdog_s=0.1)
    try:
        assert await s.acquire()

        class Stalled:
            async def execute(self, *a, **k):
                await asyncio.sleep(30)

            async def invalidate(self):
                pass

            async def close(self):
                pass

        real = s._leader
        s._leader = Stalled()
        started = time.monotonic()
        assert await s.alive() is False
        assert time.monotonic() - started < 2 and not s.is_leader
        await Sampler._discard(real)
    finally:
        await s.release()
