"""Load summaries from minute samples (load spec §2, §5-6): windows for levels, history, chart series."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Server
from app.domain.load import WINDOW, Capacity, History, Level, LoadWindow, MetricWindow, load_level, recommended_eligible

BUCKET = timedelta(minutes=15)
WEEK_BUCKETS = 7 * 24 * 4
GAP = timedelta(minutes=2)
_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)
# max(in, out) of a sample, only when both directions are known (greatest() would skip a NULL).
_NET = "CASE WHEN rx_mbps IS NOT NULL AND tx_mbps IS NOT NULL THEN greatest(rx_mbps, tx_mbps) END"
_METRICS = {"cpu": "cpu_pct", "mem": "mem_pct", "net": _NET, "clients": "active_clients"}


def capacity_of(server: Server) -> Capacity:
    return Capacity(server.bandwidth_mbps, server.expected_clients)


def _window_columns() -> str:
    cols = []
    for name, expr in _METRICS.items():
        cols += [f"avg({expr}) AS {name}_avg", f"count({expr}) AS {name}_n",
                 f"max(ts) FILTER (WHERE ({expr}) IS NOT NULL) AS {name}_last"]
    return ", ".join(cols)


async def windows(db: AsyncSession, server_ids: list[int], now: datetime) -> dict[int, LoadWindow]:
    """15-minute averages of every metric, for many servers in one query."""
    rows = (await db.execute(text(
        f"SELECT server_id, {_window_columns()} FROM server_samples "
        "WHERE server_id = ANY(:ids) AND ts > :since AND ts <= :now GROUP BY server_id"),
        {"ids": server_ids, "since": now - WINDOW, "now": now})).mappings().all()
    by_id = {r["server_id"]: r for r in rows}
    out: dict[int, LoadWindow] = {}
    for server_id in server_ids:
        r = by_id.get(server_id)

        def metric(name: str) -> MetricWindow:
            if r is None:
                return MetricWindow(None, 0, None)
            avg = r[f"{name}_avg"]
            return MetricWindow(float(avg) if avg is not None else None, r[f"{name}_n"], r[f"{name}_last"])

        out[server_id] = LoadWindow(cpu=metric("cpu"), mem=metric("mem"), net_mbps=metric("net"),
                                    clients=metric("clients"))
    return out


async def levels(db: AsyncSession, servers: list[Server], now: datetime) -> dict[int, tuple[Level, float | None, bool]]:
    """Level, utilisation and "recommended" eligibility per server."""
    ws = await windows(db, [s.id for s in servers], now)
    out = {}
    for s in servers:
        level, pct = load_level(ws[s.id], capacity_of(s), now)
        out[s.id] = (level, pct, recommended_eligible(ws[s.id], capacity_of(s), now))
    return out


async def history(db: AsyncSession, server: Server, now: datetime) -> History:
    cols = []
    for name, expr in _METRICS.items():
        cols += [f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {expr}) FILTER (WHERE ts > :d1) AS {name}_p95_1",
                 f"count({expr}) FILTER (WHERE ts > :d1) / 60.0 AS {name}_hours",
                 f"percentile_cont(0.95) WITHIN GROUP (ORDER BY {expr}) AS {name}_p95_7",
                 f"count({expr}) / 1440.0 AS {name}_days"]
    params = {"id": server.id, "d1": now - timedelta(days=1), "d7": now - timedelta(days=7), "now": now}
    r = (await db.execute(text(
        f"SELECT {', '.join(cols)}, max({_NET}) AS peak FROM server_samples "
        "WHERE server_id = :id AND ts > :d7 AND ts <= :now"), params)).mappings().one()
    last = (await db.execute(text(
        "SELECT ts, disk_pct, active_clients FROM server_samples WHERE server_id = :id AND ts <= :now "
        "ORDER BY ts DESC LIMIT 1"), params)).mappings().one_or_none()

    def f(value) -> float | None:
        return float(value) if value is not None else None

    return History(
        p95_24h={m: f(r[f"{m}_p95_1"]) for m in _METRICS},
        hours_24h={m: float(r[f"{m}_hours"]) for m in _METRICS},
        p95_7d={m: f(r[f"{m}_p95_7"]) for m in _METRICS},
        days_7d={m: float(r[f"{m}_days"]) for m in _METRICS},
        peak_net_mbps_7d=f(r["peak"]),
        last_at=last["ts"] if last else None,
        last_disk_pct=f(last["disk_pct"]) if last else None,
        last_clients=last["active_clients"] if last else None,
    )


def _point(ts: datetime, cpu=None, mem=None, rx=None, tx=None, clients=None) -> dict:
    def f(value):
        return round(float(value), 2) if value is not None else None

    return {"ts": ts.isoformat(), "cpu": f(cpu), "mem": f(mem), "rx": f(rx), "tx": f(tx), "clients": f(clients)}


async def series(db: AsyncSession, server_id: int, now: datetime, range_: Literal["24h", "7d"]) -> list[dict]:
    """Chart points with explicit gaps: charts draw a gap only where a point has null values."""
    if range_ == "24h":
        rows = (await db.execute(text(
            "SELECT ts, cpu_pct, mem_pct, rx_mbps, tx_mbps, active_clients FROM server_samples "
            "WHERE server_id = :id AND ts > :since AND ts <= :now ORDER BY ts"),
            {"id": server_id, "since": now - timedelta(days=1), "now": now})).all()
        points: list[dict] = []
        prev: datetime | None = None
        for ts, cpu, mem, rx, tx, clients in rows:
            if prev is not None and ts - prev > GAP:
                points.append(_point(prev + timedelta(minutes=1)))
            points.append(_point(ts, cpu, mem, rx, tx, clients))
            prev = ts
        return points

    last = _ORIGIN + ((now - _ORIGIN) // BUCKET) * BUCKET
    first = last - (WEEK_BUCKETS - 1) * BUCKET
    rows = (await db.execute(text(
        "SELECT b.bucket, avg(s.cpu_pct), avg(s.mem_pct), avg(s.rx_mbps), avg(s.tx_mbps), avg(s.active_clients) "
        "FROM generate_series(CAST(:first AS timestamptz), CAST(:last AS timestamptz), interval '15 minutes') "
        "AS b(bucket) "
        "LEFT JOIN server_samples s ON s.server_id = :id AND s.ts >= :first AND s.ts <= :now "
        "AND date_bin(interval '15 minutes', s.ts, CAST(:origin AS timestamptz)) = b.bucket "
        "GROUP BY b.bucket ORDER BY b.bucket"),
        {"id": server_id, "first": first, "last": last, "now": now, "origin": _ORIGIN})).all()
    return [_point(*row) for row in rows]
