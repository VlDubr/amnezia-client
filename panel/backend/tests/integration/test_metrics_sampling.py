"""Sampling a real Linux host over SSH (load spec §8): the commands, the parser and the rates together."""

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import Server, ServerSample
from app.domain import metrics as metrics_rules
from app.services.metrics import sample_server
from app.ssh.conn import open_remote

pytestmark = pytest.mark.integration


async def test_sampling_the_ssh_test_host(db, sessionmaker, clock, sshhost, monkeypatch):
    server = Server(name="real", host=sshhost.host, ssh_port=sshhost.port, ssh_user=sshhost.user, ssh_secret_enc="x")
    db.add(server)
    await db.commit()
    server_id = server.id
    monkeypatch.setattr(metrics_rules, "MIN_GAP_S", 1.0)  # the host's uptime, not a minute of test time

    def factory(_server):
        return open_remote(sshhost)

    await sample_server(sessionmaker, server_id, factory, clock)
    await asyncio.sleep(2)
    clock.advance(60)
    await sample_server(sessionmaker, server_id, factory, clock)

    db.expire_all()
    fresh = await db.get(Server, server_id)
    assert fresh.metrics_error is None, fresh.metrics_error
    first, second = (await db.execute(select(ServerSample).where(ServerSample.server_id == server_id)
                                      .order_by(ServerSample.ts))).scalars().all()
    assert first.boot_id == second.boot_id and second.uptime_s > first.uptime_s
    assert second.cpu_pct is not None and 0 <= second.cpu_pct <= 100
    assert 0 < second.mem_pct < 100 and 0 < second.disk_pct <= 100
    assert second.iface is not None and second.rx_mbps is not None and second.rx_mbps >= 0
    assert fresh.specs_json["cores"] >= 1 and fresh.specs_json["mem_bytes"] > 0
    assert fresh.specs_json["iface"] == second.iface
