"""Traffic accounting: per-config deltas summed per day (spec §5, §8), and recently active configs (load spec §4)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, Server, ServerContainer, TrafficDaily
from app.domain.clock import Clock
from app.domain.rules import traffic_delta
from app.drivers.base import get_driver, supported_containers
from app.services.servers import RemoteFactory
from app.ssh.conn import RemoteError

ACTIVE_WINDOW = timedelta(minutes=15)


async def collect_traffic(db: AsyncSession, server_id: int, remote_factory: RemoteFactory, clock: Clock,
                          tz: str) -> None:
    """Stores traffic from the user's point of view: rx = downloaded (server tx), tx = uploaded (server rx)."""
    server = await db.get(Server, server_id)
    now = clock.now()
    day = now.astimezone(ZoneInfo(tz)).date()
    containers = (await db.execute(
        select(ServerContainer).where(ServerContainer.server_id == server_id,
                                      ServerContainer.container.in_(supported_containers()))
    )).scalars().all()
    async with remote_factory(server) as remote:
        for sc in containers:
            try:
                counters = await get_driver(sc.container).read_traffic(remote)
            except (RemoteError, ValueError, KeyError):
                continue  # one container's statistics failing must not stop the others; its freshness stays old
            sc.traffic_read_at = now
            configs = (await db.execute(select(Config).where(
                Config.server_id == server_id, Config.container == sc.container, Config.deleted_at.is_(None),
                Config.client_id.in_(list(counters))))).scalars().all()
            for cfg in configs:
                c = counters[cfg.client_id]
                d_rx = traffic_delta(cfg.last_rx, cfg.counter_session, c.tx, c.session)
                d_tx = traffic_delta(cfg.last_tx, cfg.counter_session, c.rx, c.session)
                if d_rx or d_tx:
                    stmt = insert(TrafficDaily).values(config_id=cfg.id, user_id=cfg.user_id, server_id=server_id,
                                                       day=day, rx=d_rx, tx=d_tx)
                    await db.execute(stmt.on_conflict_do_update(
                        index_elements=["config_id", "day"],
                        set_={"rx": TrafficDaily.rx + stmt.excluded.rx, "tx": TrafficDaily.tx + stmt.excluded.tx}))
                    # Activity only between two recent readings: a first reading carries the whole history, and a
                    # delta across an outage may be old.
                    if cfg.counter_read_at is not None and now - cfg.counter_read_at <= ACTIVE_WINDOW:
                        cfg.last_active_at = now
                cfg.last_rx, cfg.last_tx, cfg.counter_session = c.tx, c.rx, c.session
                cfg.counter_read_at = now
    server.last_ok_at = now
    server.last_error = None
    await db.commit()


async def active_clients(db: AsyncSession, server_id: int, now: datetime) -> int | None:
    """Configs with traffic in the last 15 minutes, or None when that cannot be known: no container with traffic
    counters, or one of them not read recently."""
    rows = (await db.execute(select(ServerContainer).where(
        ServerContainer.server_id == server_id, ServerContainer.container.in_(supported_containers())))).scalars()
    tracked = [sc for sc in rows if get_driver(sc.container).traffic_counters]
    if not tracked or any(sc.traffic_read_at is None or now - sc.traffic_read_at > ACTIVE_WINDOW for sc in tracked):
        return None
    return (await db.execute(select(func.count()).select_from(Config).where(
        Config.server_id == server_id, Config.container.in_([sc.container for sc in tracked]),
        Config.deleted_at.is_(None), Config.last_active_at >= now - ACTIVE_WINDOW))).scalar_one()
