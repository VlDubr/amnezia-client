"""Traffic accounting: per-config deltas summed per day (spec §5, §8)."""

from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, Server, ServerContainer, TrafficDaily
from app.domain.clock import Clock
from app.domain.rules import traffic_delta
from app.drivers.base import get_driver, supported_containers
from app.services.servers import RemoteFactory


async def collect_traffic(db: AsyncSession, server_id: int, remote_factory: RemoteFactory, clock: Clock,
                          tz: str) -> None:
    """Stores traffic from the user's point of view: rx = downloaded (server tx), tx = uploaded (server rx)."""
    server = await db.get(Server, server_id)
    now = clock.now()
    day = now.astimezone(ZoneInfo(tz)).date()
    containers = (await db.execute(
        select(ServerContainer.container).where(ServerContainer.server_id == server_id,
                                                ServerContainer.container.in_(supported_containers()))
    )).scalars().all()
    async with remote_factory(server) as remote:
        for container in containers:
            counters = await get_driver(container).read_traffic(remote)
            configs = (await db.execute(select(Config).where(
                Config.server_id == server_id, Config.container == container, Config.deleted_at.is_(None),
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
                cfg.last_rx, cfg.last_tx, cfg.counter_session = c.tx, c.rx, c.session
    server.last_ok_at = now
    server.last_error = None
    await db.commit()
