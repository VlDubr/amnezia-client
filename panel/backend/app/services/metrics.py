"""Sampling one server's load over SSH (load spec §4). No database transaction is open while SSH runs."""

import asyncio
import logging
import re
import shlex
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Server, ServerSample
from app.domain.clock import Clock
from app.domain.metrics import (
    IFACE_RE,
    SAMPLE_SCRIPT,
    SPECS_SCRIPT,
    Baseline,
    RawSample,
    parse_link_speed,
    parse_sample,
    parse_specs,
    rates,
)
from app.services.servers import RemoteFactory
from app.services.traffic import active_clients
from app.ssh.conn import RemoteError

SAMPLE_TIMEOUT_S = 20
SPECS_MAX_AGE = timedelta(hours=24)
SAMPLE_RETENTION = timedelta(days=30)
CLEANUP_BATCH = 10_000
_UNPRINTABLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]|\x1b\[[0-9;]*[A-Za-z]")

log = logging.getLogger("panel.metrics")


def _sanitise(error: Exception) -> str:
    return _UNPRINTABLE.sub("", str(error) or type(error).__name__)[:500]


async def _collect(server: Server, override: str | None, need_specs: bool,
                   remote_factory: RemoteFactory) -> tuple[RawSample, dict | None]:
    async with asyncio.timeout(SAMPLE_TIMEOUT_S), remote_factory(server) as remote:
        raw = parse_sample((await remote.run(SAMPLE_SCRIPT, timeout=SAMPLE_TIMEOUT_S)).stdout, override)
        if not need_specs:
            return raw, None
        specs = parse_specs((await remote.run(SPECS_SCRIPT, timeout=SAMPLE_TIMEOUT_S)).stdout)
        link = None
        if raw.iface and IFACE_RE.fullmatch(raw.iface):
            speed = await remote.run(f"cat /sys/class/net/{shlex.quote(raw.iface)}/speed", check=False,
                                     timeout=SAMPLE_TIMEOUT_S)
            link = parse_link_speed(speed.stdout)
        return raw, {**specs, "iface": raw.iface, "link_mbps": link}


async def sample_server(sessionmaker: async_sessionmaker[AsyncSession], server_id: int,
                        remote_factory: RemoteFactory, clock: Clock) -> None:
    now = clock.now()
    async with sessionmaker() as db:
        server = await db.get(Server, server_id)
        if server is None:
            return
        prev = (await db.execute(select(ServerSample).where(ServerSample.server_id == server_id)
                                 .order_by(ServerSample.ts.desc()).limit(1))).scalar_one_or_none()
        baseline = None if prev is None else Baseline(prev.boot_id, prev.uptime_s, prev.iface, prev.cpu_busy,
                                                       prev.cpu_total, prev.net_rx, prev.net_tx)
        need_specs = server.specs_at is None or now - server.specs_at > SPECS_MAX_AGE
        override = server.metrics_iface
    # The session is closed: `server` keeps its loaded attributes for the SSH connection.
    try:
        raw, specs = await _collect(server, override, need_specs, remote_factory)
    except (RemoteError, ValueError, OSError, TimeoutError) as e:
        async with sessionmaker() as db:
            await db.execute(update(Server).where(Server.id == server_id)
                             .values(metrics_error=_sanitise(e), metrics_error_at=now))
            await db.commit()
        return

    r = rates(baseline, raw)
    async with sessionmaker() as db:
        try:
            clients = await active_clients(db, server_id, now)
            db.add(ServerSample(server_id=server_id, ts=now, boot_id=raw.boot_id, uptime_s=raw.uptime_s,
                                iface=raw.iface, cpu_busy=raw.cpu_busy, cpu_total=raw.cpu_total, net_rx=raw.net_rx,
                                net_tx=raw.net_tx, cpu_pct=r.cpu_pct, rx_mbps=r.rx_mbps, tx_mbps=r.tx_mbps,
                                mem_pct=raw.mem_pct, disk_pct=raw.disk_pct, load1=raw.load1, active_clients=clients))
            values: dict = {"metrics_error": None, "metrics_error_at": None}
            if specs is not None:
                values.update(specs_json=specs, specs_at=now)
            await db.execute(update(Server).where(Server.id == server_id).values(**values))
            await db.commit()
        except IntegrityError:  # the server was deleted while it was being sampled
            await db.rollback()


async def cleanup_samples(db: AsyncSession, now: datetime) -> int:
    """Deletes samples older than 30 days in batches, so a large backlog never locks the table for long."""
    cutoff = now - SAMPLE_RETENTION
    total = 0
    while True:
        batch = select(ServerSample.id).where(ServerSample.ts < cutoff).limit(CLEANUP_BATCH).scalar_subquery()
        deleted = (await db.execute(delete(ServerSample).where(ServerSample.id.in_(batch)))).rowcount
        await db.commit()
        total += deleted
        if deleted < CLEANUP_BATCH:
            return total
