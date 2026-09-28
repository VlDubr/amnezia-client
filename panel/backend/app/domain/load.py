"""Load level and recommendations from sampled metrics (spec §2, §5). Pure functions."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

Level = Literal["low", "medium", "high", "unknown"]
LEVEL_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "unknown": 3}

WINDOW = timedelta(minutes=15)
MIN_POINTS = 5
MAX_AGE = timedelta(minutes=10)
MEDIUM_FROM, HIGH_ABOVE = 50.0, 80.0
DISK_FULL_ABOVE = 85.0
SUSTAINED_HOURS = 12.0
GROWTH_DAYS = 5.0


@dataclass(frozen=True)
class MetricWindow:
    """One metric over the last 15 minutes: average of valid points, their number, the newest one's time."""

    avg: float | None
    points: int
    last_at: datetime | None


@dataclass(frozen=True)
class LoadWindow:
    cpu: MetricWindow
    mem: MetricWindow
    net_mbps: MetricWindow  # max(in, out) per sample
    clients: MetricWindow


@dataclass(frozen=True)
class Capacity:
    bandwidth_mbps: int | None
    expected_clients: int | None


@dataclass(frozen=True)
class History:
    """From minute samples. Keys cpu, mem (%), net (Mbit/s, max of in and out), clients (count)."""

    p95_24h: dict[str, float | None]
    hours_24h: dict[str, float]
    p95_7d: dict[str, float | None]
    days_7d: dict[str, float]
    peak_net_mbps_7d: float | None
    last_at: datetime | None
    last_disk_pct: float | None
    last_clients: int | None


@dataclass(frozen=True)
class Recommendation:
    code: str
    severity: Literal["warning", "info"]
    params: dict = field(default_factory=dict)


def _qualifies(m: MetricWindow, now: datetime) -> bool:
    return m.avg is not None and m.points >= MIN_POINTS and m.last_at is not None and now - m.last_at <= MAX_AGE


def _as_pct(metric: str, value: float | None, c: Capacity) -> float | None:
    """A metric in % of its capacity; None when the capacity is not set."""
    if value is None:
        return None
    if metric == "net":
        return value / c.bandwidth_mbps * 100 if c.bandwidth_mbps else None
    if metric == "clients":
        return value / c.expected_clients * 100 if c.expected_clients else None
    return value


def utilisations(w: LoadWindow, c: Capacity, now: datetime) -> dict[str, float]:
    """Qualifying metrics in % of capacity: enough fresh points and, for the channel and clients, a capacity."""
    out: dict[str, float] = {}
    for name, m in (("cpu", w.cpu), ("mem", w.mem), ("net", w.net_mbps), ("clients", w.clients)):
        pct = _as_pct(name, m.avg, c) if _qualifies(m, now) else None
        if pct is not None:
            out[name] = pct
    return out


def _level_of(pct: float) -> Level:
    if pct < MEDIUM_FROM:
        return "low"
    return "medium" if pct <= HIGH_ABOVE else "high"


def load_level(w: LoadWindow, c: Capacity, now: datetime) -> tuple[Level, float | None]:
    u = utilisations(w, c, now)
    # CPU and memory are always measurable: without them the data is stale or broken.
    if "cpu" not in u or "mem" not in u:
        return "unknown", None
    pct = max(u.values())
    return _level_of(pct), pct


def recommended_eligible(w: LoadWindow, c: Capacity, now: datetime) -> bool:
    """A good level that is not hiding missing telemetry: every metric with a capacity must qualify."""
    level, _ = load_level(w, c, now)
    if level not in ("low", "medium"):
        return False
    u = utilisations(w, c, now)
    return (not c.bandwidth_mbps or "net" in u) and (not c.expected_clients or "clients" in u)


def recommendations(w: LoadWindow, h: History, c: Capacity, untracked: list[str], metrics_error: str | None,
                    now: datetime) -> list[Recommendation]:
    recs: list[Recommendation] = []
    if h.last_at is None or now - h.last_at > MAX_AGE:
        recs.append(Recommendation("no_data", "warning", {
            "error": metrics_error, "last_at": h.last_at.isoformat() if h.last_at else None}))
    for metric, code in (("cpu", "cpu_high"), ("mem", "memory_high"), ("net", "channel_high")):
        p95 = _as_pct(metric, h.p95_24h.get(metric), c)
        if p95 is not None and p95 > HIGH_ABOVE and h.hours_24h.get(metric, 0) >= SUSTAINED_HOURS:
            recs.append(Recommendation(code, "warning", {"p95": round(p95, 1)}))
    if h.last_disk_pct is not None and h.last_disk_pct > DISK_FULL_ABOVE:
        recs.append(Recommendation("disk_full", "warning", {"pct": round(h.last_disk_pct, 1)}))
    if c.expected_clients and h.last_clients is not None and h.last_clients > c.expected_clients:
        recs.append(Recommendation("clients_over", "warning",
                                   {"active": h.last_clients, "expected": c.expected_clients}))

    if not c.bandwidth_mbps:
        recs.append(Recommendation("bandwidth_unset", "info"))
    elif h.peak_net_mbps_7d is not None and h.peak_net_mbps_7d > c.bandwidth_mbps:
        recs.append(Recommendation("bandwidth_exceeded", "info",
                                   {"peak_mbps": round(h.peak_net_mbps_7d, 1), "width_mbps": c.bandwidth_mbps}))
    if not c.expected_clients:
        recs.append(Recommendation("clients_unset", "info"))
    if untracked:
        recs.append(Recommendation("untracked_protocols", "info", {"protocols": untracked}))

    level, _ = load_level(w, c, now)
    if level == "low" and len(utilisations(w, c, now)) == 4:
        p95s = [_as_pct(m, h.p95_7d.get(m), c) for m in ("cpu", "mem", "net", "clients")]
        days = [h.days_7d.get(m, 0) for m in ("cpu", "mem", "net", "clients")]
        if all(p is not None and p < MEDIUM_FROM for p in p95s) and all(d >= GROWTH_DAYS for d in days):
            recs.append(Recommendation("room_to_grow", "info"))
    return recs
