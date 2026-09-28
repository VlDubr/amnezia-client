"""Server load samples: the fixed read-only commands, their parsing, and rates between two samples (spec §3-4).

Pure functions only; the sampler runs the commands and stores the results.
"""

import re
from dataclasses import dataclass

# Constant scripts: nothing from the server or the database is interpolated into them.
_LIMIT = "head -c 65536"
SAMPLE_SCRIPT = (
    "( echo @@boot; cat /proc/sys/kernel/random/boot_id; "
    "echo @@uptime; cat /proc/uptime; "
    "echo @@stat; head -n 1 /proc/stat; "
    "echo @@mem; cat /proc/meminfo; "
    "echo @@df; df -B1 -P /; "
    "echo @@load; cat /proc/loadavg; "
    "echo @@route; cat /proc/net/route; "
    "echo @@route6; cat /proc/net/ipv6_route; "
    f"echo @@dev; cat /proc/net/dev ) 2>/dev/null | {_LIMIT}"
)
SPECS_SCRIPT = (
    "( echo @@cpu; grep -m 1 'model name' /proc/cpuinfo; "
    "echo @@nproc; nproc; "
    "echo @@mem; grep MemTotal /proc/meminfo; "
    "echo @@df; df -B1 -P /; "
    "echo @@os; grep -m 1 '^PRETTY_NAME=' /etc/os-release; "
    "echo @@kernel; uname -r; "
    f"echo @@uptime; cat /proc/uptime ) 2>/dev/null | {_LIMIT}"
)
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")

_RTF_REJECT = 0x0200
MIN_GAP_S, MAX_GAP_S = 30.0, 300.0


@dataclass(frozen=True)
class RawSample:
    boot_id: str
    uptime_s: float
    cpu_busy: int
    cpu_total: int
    mem_pct: float | None
    disk_pct: float | None
    load1: float | None
    iface: str | None
    net_rx: int | None
    net_tx: int | None


@dataclass(frozen=True)
class Baseline:
    """The previous stored sample of the same server."""

    boot_id: str
    uptime_s: float
    iface: str | None
    cpu_busy: int
    cpu_total: int
    net_rx: int | None
    net_tx: int | None


@dataclass(frozen=True)
class Rates:
    cpu_pct: float | None
    rx_mbps: float | None
    tx_mbps: float | None


def sections(text: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("@@"):
            current = out.setdefault(line[2:].strip(), [])
        elif current is not None:
            current.append(line)
    return {name: "\n".join(lines).strip("\n") for name, lines in out.items()}


def _devices(dev: str) -> dict[str, tuple[int, int]]:
    """Interface -> (received bytes, transmitted bytes) from /proc/net/dev."""
    out: dict[str, tuple[int, int]] = {}
    for line in dev.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 9 and fields[0].isdigit() and fields[8].isdigit():
            out[name.strip()] = (int(fields[0]), int(fields[8]))
    return out


def pick_iface(route4: str, route6: str, devices: set[str], override: str | None) -> str | None:
    """The admin's interface if it exists, else the default route with the lowest metric (IPv4, then IPv6)."""
    if override and override in devices:
        return override
    best: tuple[int, str] | None = None
    for line in route4.splitlines()[1:]:
        f = line.split()
        if len(f) >= 8 and f[1] == "00000000" and f[7] == "00000000" and f[0] in devices:
            candidate = (int(f[6]), f[0])
            best = min(best, candidate) if best else candidate
    if best:
        return best[1]
    for line in route6.splitlines():
        f = line.split()
        if (len(f) >= 10 and f[0] == "0" * 32 and f[1] == "00" and f[9] != "lo"
                and not int(f[8], 16) & _RTF_REJECT and f[9] in devices):
            candidate = (int(f[5], 16), f[9])
            best = min(best, candidate) if best else candidate
    return best[1] if best else None


def _meminfo(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        parts = value.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * 1024
    return out


def _df(text: str) -> tuple[int, int, int] | None:
    """(size, used, available) bytes of the filesystem line of `df -B1 -P`."""
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    f = lines[-1].split()
    if len(f) >= 4 and f[1].isdigit() and f[2].isdigit() and f[3].isdigit():
        return int(f[1]), int(f[2]), int(f[3])
    return None


def parse_sample(text: str, iface_override: str | None) -> RawSample:
    s = sections(text)
    boot_id = s.get("boot", "").strip()
    uptime = s.get("uptime", "").split()
    cpu = s.get("stat", "").split()
    if not boot_id or not uptime or len(cpu) < 5 or cpu[0] != "cpu":
        raise ValueError("the sample lacks boot id, uptime or CPU counters")
    ticks = [int(v) for v in cpu[1:9]]
    total = sum(ticks)
    busy = total - ticks[3] - ticks[4]  # minus idle and iowait

    mem = _meminfo(s.get("mem", ""))
    mem_pct = None
    if mem.get("MemTotal") and "MemAvailable" in mem:
        mem_pct = (1 - mem["MemAvailable"] / mem["MemTotal"]) * 100
    disk = _df(s.get("df", ""))
    disk_pct = disk[1] / (disk[1] + disk[2]) * 100 if disk and disk[1] + disk[2] else None
    load = s.get("load", "").split()
    load1 = float(load[0]) if load else None

    devices = _devices(s.get("dev", ""))
    iface = pick_iface(s.get("route", ""), s.get("route6", ""), set(devices), iface_override)
    net = devices.get(iface) if iface else None
    return RawSample(boot_id=boot_id[:36], uptime_s=float(uptime[0]), cpu_busy=busy, cpu_total=total,
                     mem_pct=mem_pct, disk_pct=disk_pct, load1=load1, iface=iface if net else None,
                     net_rx=net[0] if net else None, net_tx=net[1] if net else None)


def parse_specs(text: str) -> dict:
    s = sections(text)
    cpu = s.get("cpu", "")
    nproc = s.get("nproc", "").strip()
    disk = _df(s.get("df", ""))
    os_name = s.get("os", "").partition("=")[2].strip().strip('"') or None
    uptime = s.get("uptime", "").split()
    return {
        "cpu_model": cpu.partition(":")[2].strip() or None,
        "cores": int(nproc) if nproc.isdigit() else None,
        "mem_bytes": _meminfo(s.get("mem", "")).get("MemTotal"),
        "disk_bytes": disk[0] if disk else None,
        "os": os_name,
        "kernel": s.get("kernel", "").strip() or None,
        "uptime_s": float(uptime[0]) if uptime else None,
    }


def parse_link_speed(text: str) -> int | None:
    """/sys/class/net/<iface>/speed: Mbit/s, or -1 / nothing for virtual interfaces."""
    value = text.strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def rates(prev: Baseline | None, cur: RawSample) -> Rates:
    """Rates since the previous sample: only across one boot and a 30-300 s gap, CPU and network each only when
    its counters did not go down (network also on the same interface)."""
    if prev is None or prev.boot_id != cur.boot_id:
        return Rates(None, None, None)
    elapsed = cur.uptime_s - prev.uptime_s
    if not MIN_GAP_S <= elapsed <= MAX_GAP_S:
        return Rates(None, None, None)
    cpu_pct = None
    d_busy, d_total = cur.cpu_busy - prev.cpu_busy, cur.cpu_total - prev.cpu_total
    if d_total > 0 and 0 <= d_busy <= d_total:
        cpu_pct = d_busy / d_total * 100
    rx = tx = None
    if (cur.iface and cur.iface == prev.iface and None not in (cur.net_rx, cur.net_tx, prev.net_rx, prev.net_tx)
            and cur.net_rx >= prev.net_rx and cur.net_tx >= prev.net_tx):
        rx = (cur.net_rx - prev.net_rx) * 8 / elapsed / 1e6
        tx = (cur.net_tx - prev.net_tx) * 8 / elapsed / 1e6
    return Rates(cpu_pct, rx, tx)
