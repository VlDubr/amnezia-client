import pytest

from app.domain.metrics import Baseline, parse_sample, parse_specs, pick_iface, rates, sections

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
"""
ROUTE6_LO_ONLY = (
    "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
    "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo\n"
)
DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0
  eth0:123456789 1000    0    0    0     0          0         0 987654321     900    0    0    0     0       0          0
docker0:    500       5    0    0    0     0          0         0      700       7    0    0    0     0       0          0
vethab12:   300       3    0    0    0     0          0         0      400       4    0    0    0     0       0          0
"""


def sample_text(boot="b1", uptime="1000.00", cpu="cpu  4705 356 584 3699 23 23 0 0 0 0", route=ROUTE,
                route6=ROUTE6_LO_ONLY, dev=DEV) -> str:
    parts = {
        "boot": boot,
        "uptime": f"{uptime} 2000.00",
        "stat": cpu,
        "mem": "MemTotal:        2000000 kB\nMemFree:          300000 kB\nMemAvailable:    1500000 kB\n",
        "df": "Filesystem     1-blocks        Used   Available Capacity Mounted on\n"
              "/dev/vda1   40000000000 10000000000 30000000000      25% /\n",
        "load": "0.52 0.40 0.35 1/234 5678",
        "route": route,
        "route6": route6,
        "dev": dev,
    }
    return "".join(f"@@{name}\n{body}\n" for name, body in parts.items() if body is not None)


def test_sections_split_on_markers():
    assert sections("@@a\n1\n2\n@@b\n3\n") == {"a": "1\n2", "b": "3"}


def test_typical_host():
    s = parse_sample(sample_text(), None)
    assert s.boot_id == "b1" and s.uptime_s == 1000.0
    assert (s.cpu_total, s.cpu_busy) == (4705 + 356 + 584 + 3699 + 23 + 23, 4705 + 356 + 584 + 23)
    assert s.mem_pct == pytest.approx(25.0)
    assert s.disk_pct == pytest.approx(25.0)
    assert s.load1 == pytest.approx(0.52)
    assert (s.iface, s.net_rx, s.net_tx) == ("eth0", 123456789, 987654321)


def test_override_wins_when_present_and_is_ignored_when_missing():
    assert parse_sample(sample_text(), "docker0").iface == "docker0"
    assert parse_sample(sample_text(), "wg9").iface == "eth0"


def test_lowest_metric_default_route_wins():
    route = ROUTE + "ens4\t00000000\t0102A8C0\t0003\t0\t0\t50\t00000000\t0\t0\t0\n"
    dev = DEV + "  ens4:  10 1 0 0 0 0 0 0 20 2 0 0 0 0 0 0\n"
    assert pick_iface(route, "", {"eth0", "ens4"}, None) == "ens4"
    assert parse_sample(sample_text(route=route, dev=dev), None).iface == "ens4"


def test_ipv6_only_default_route():
    route6 = ROUTE6_LO_ONLY + (
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
        "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 ens3\n")
    dev = DEV + "  ens3: 50 1 0 0 0 0 0 0 60 1 0 0 0 0 0 0\n"
    s = parse_sample(sample_text(route="Iface\tDestination\n", route6=route6, dev=dev), None)
    assert (s.iface, s.net_rx, s.net_tx) == ("ens3", 50, 60)


def test_no_default_route_or_no_dev_section_means_no_throughput():
    assert parse_sample(sample_text(route="Iface\tDestination\n"), None).iface is None
    s = parse_sample(sample_text(dev=None), None)
    assert (s.iface, s.net_rx, s.net_tx) == (None, None, None)


@pytest.mark.parametrize("missing", ["boot", "uptime", "stat"])
def test_required_sections(missing):
    text = sample_text(**{{"boot": "boot", "uptime": "uptime", "stat": "cpu"}[missing]: None})
    with pytest.raises(ValueError):
        parse_sample(text, None)


def test_specs():
    text = ("@@cpu\nmodel name\t: AMD EPYC 7B13\n@@nproc\n2\n@@mem\nMemTotal:        2000000 kB\n"
            "@@df\nFilesystem 1-blocks Used Available Capacity Mounted on\n/dev/vda1 40000000000 1 2 1% /\n"
            '@@os\nPRETTY_NAME="Ubuntu 24.04 LTS"\n@@kernel\n6.8.0-45-generic\n@@uptime\n1234.5 99.0\n')
    assert parse_specs(text) == {"cpu_model": "AMD EPYC 7B13", "cores": 2, "mem_bytes": 2048000000,
                                 "disk_bytes": 40000000000, "os": "Ubuntu 24.04 LTS", "kernel": "6.8.0-45-generic",
                                 "uptime_s": 1234.5}
    assert parse_specs("@@nproc\n4\n")["cpu_model"] is None


def base(**kw) -> Baseline:
    values = {"boot_id": "b1", "uptime_s": 940.0, "iface": "eth0", "cpu_busy": 5568, "cpu_total": 9213,
              "net_rx": 123456789 - 75_000_000, "net_tx": 987654321 - 150_000_000}
    values.update(kw)
    return Baseline(**values)


def test_rates_against_the_previous_sample():
    cur = parse_sample(sample_text(cpu="cpu  4800 356 584 3699 23 23 0 0 0 0"), None)  # busy 5763, total 9485
    prev = base(cpu_busy=5663, cpu_total=9285)
    r = rates(prev, cur)
    assert r.cpu_pct == pytest.approx(50.0)
    assert r.rx_mbps == pytest.approx(75_000_000 * 8 / 60 / 1e6)
    assert r.tx_mbps == pytest.approx(150_000_000 * 8 / 60 / 1e6)


def test_no_rates_without_a_valid_baseline():
    cur = parse_sample(sample_text(), None)
    assert rates(None, cur) == rates(base(boot_id="other"), cur)
    assert rates(None, cur).cpu_pct is None and rates(None, cur).rx_mbps is None
    assert rates(base(uptime_s=990.0), cur).cpu_pct is None      # 10 s: too close
    assert rates(base(uptime_s=600.0), cur).cpu_pct is None      # 400 s: too far


def test_cpu_and_network_are_validated_independently():
    cur = parse_sample(sample_text(), None)
    reset = rates(base(net_rx=999_999_999_999), cur)             # network counter went down (interface reset)
    assert reset.rx_mbps is None and reset.cpu_pct is not None
    other_iface = rates(base(iface="ens4"), cur)
    assert other_iface.rx_mbps is None and other_iface.cpu_pct is not None
    cpu_reset = rates(base(cpu_busy=10**9, cpu_total=10**9), cur)
    assert cpu_reset.cpu_pct is None and cpu_reset.rx_mbps is not None
