# Server Load Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The panel samples each VPN server's hardware and load over SSH, derives a load level for users (sorted, "recommended") and a detailed load page with recommendations for admins.

**Architecture:** Pure parsers and load rules in `app/domain/`, an in-process sampler loop with a lifetime PostgreSQL leader lock writing minute samples to `server_samples`, SQL summaries feeding the user and admin APIs, a load section and badges in the web UI, and the level in the Qt issue list.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, asyncssh, PostgreSQL (`date_bin`, `percentile_cont`), React 19 + Mantine 9 + recharts (already a dependency), Qt 6 QML.

**Spec:** `docs/superpowers/specs/2026-09-28-panel-server-load-design.md`

## Global Constraints

- Sampling runs one fixed read-only command; no `sudo`; no interpolation of server data into the command except a validated, shell-quoted interface name (`^[A-Za-z0-9_.:@-]{1,15}$`) for the link speed.
- Output read from a server is cut at 64 KiB.
- No database transaction is open while an SSH command runs.
- Users receive only `load` and `recommended` for servers; never numbers, errors or hardware.
- Level: max of qualifying per-metric 15-minute averages; qualifying = ≥ 5 valid points, latest ≤ 10 min old, capacity set (channel, clients); low < 50, medium 50–80 inclusive, high > 80; `unknown` unless CPU and memory both qualify.
- Recommended: first in user order, level low or medium, and every metric with a set capacity qualifies.
- Samples kept 30 days; cleanup in batches of 10 000.
- Commit messages: Conventional Commits, English, ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
- Backend tests: `cd panel/backend && uv run pytest -q -p no:logging` (Docker running). Web: `cd panel/web && npx vitest run && npm run build`.

## Review Focus

1. A server rebooting between samples (boot_id changes): rates must be null, not negative or huge.
2. A traffic read failure (Xray stats API down): must not refresh freshness nor count zero activity.
3. Two panel processes started at once: only one samples.
4. A user response must not leak `load_pct`, errors or specs.
5. A capacity cleared with explicit null: must stop counting that metric and show the "unset" recommendation.

---

### Task 1: Schema (migration 0004) and models

**Files:**
- Create: `panel/backend/app/db/migrations/versions/0004_server_load.py`
- Modify: `panel/backend/app/db/models.py`
- Test: `panel/backend/tests/unit/test_migration_0004.py`

**Interfaces — Produces:**
- `Server.bandwidth_mbps: int|None`, `Server.expected_clients: int|None`, `Server.metrics_iface: str|None`, `Server.specs_json: dict|None`, `Server.specs_at`, `Server.metrics_error: str|None`, `Server.metrics_error_at`
- `ServerContainer.traffic_read_at`, `Config.counter_read_at`, `Config.last_active_at`
- `ServerSample` model: `id, server_id, ts, boot_id, uptime_s, iface, cpu_busy, cpu_total, net_rx, net_tx, cpu_pct, rx_mbps, tx_mbps, mem_pct, disk_pct, load1, active_clients`, index `ix_server_samples_server_ts (server_id, ts)`

- [ ] **Step 1: Failing test** — upgrade a fresh database to head, insert a server and a `server_samples` row, delete the server, assert the sample is gone (cascade); downgrade to 0003 and assert `server_samples` does not exist and `servers` has no `bandwidth_mbps`. Reuse `_fresh_database`/`_run` from `test_migration_0003.py` (move them to `tests/migration_helpers.py`).
- [ ] **Step 2: Run** `uv run pytest -q -p no:logging tests/unit/test_migration_0004.py` — Expected: FAIL (`server_samples` missing).
- [ ] **Step 3: Implement** the migration (add columns; create table with `sa.BigInteger` PK, FK `ondelete="CASCADE"`, index) and the model fields exactly as listed.
- [ ] **Step 4: Run** the test and the full suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): schema for server load samples and capacities`

### Task 2: Sample parser and rates (pure)

**Files:**
- Create: `panel/backend/app/domain/metrics.py`
- Test: `panel/backend/tests/unit/test_metrics_parse.py`, fixtures in `panel/backend/tests/data/metrics/*.txt`

**Interfaces — Produces:**
```python
SAMPLE_SCRIPT: str          # constant, prints sections "@@boot", "@@uptime", "@@stat", "@@mem", "@@df",
                            # "@@load", "@@route", "@@route6", "@@dev"; wrapped in "( ... ) 2>/dev/null | head -c 65536"
SPECS_SCRIPT: str           # "@@cpu" (first "model name" of /proc/cpuinfo), "@@nproc", "@@mem", "@@df", "@@os"
                            # (PRETTY_NAME), "@@kernel" (uname -r), "@@uptime"
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")

@dataclass(frozen=True)
class RawSample:
    boot_id: str; uptime_s: float; cpu_busy: int; cpu_total: int
    mem_pct: float | None; disk_pct: float | None; load1: float | None
    iface: str | None; net_rx: int | None; net_tx: int | None

@dataclass(frozen=True)
class Baseline:  # the previous stored sample
    boot_id: str; uptime_s: float; iface: str | None
    cpu_busy: int; cpu_total: int; net_rx: int | None; net_tx: int | None

@dataclass(frozen=True)
class Rates:
    cpu_pct: float | None; rx_mbps: float | None; tx_mbps: float | None

def sections(text: str) -> dict[str, str]
def pick_iface(route4: str, route6: str, devices: set[str], override: str | None) -> str | None
def parse_sample(text: str, iface_override: str | None) -> RawSample     # ValueError without boot/uptime/stat
def parse_specs(text: str) -> dict                                       # cpu_model, cores, mem_bytes, disk_bytes, os, kernel, uptime_s
def rates(prev: Baseline | None, cur: RawSample) -> Rates
```
Rules: CPU busy = user+nice+system+irq+softirq+steal (total − idle − iowait); total = sum of the first 8 fields. Memory % = (1 − MemAvailable/MemTotal)·100. Disk % = used/(used+avail)·100 of the `df -B1 -P /` data line. Interface: override if present in `/proc/net/dev`; else IPv4 default route (destination `00000000`, mask `00000000`) with the lowest metric; else IPv6 default route (`::/0`, i.e. destination 32 zeros and prefix `00`) with the lowest metric; else None. Rates: valid only with same boot_id, 30 ≤ Δuptime ≤ 300, non-decreasing counters; network additionally same iface and non-null counters; Mbit/s = Δbytes·8 / Δuptime / 1e6; CPU % = Δbusy/Δtotal·100 (null if Δtotal = 0).

- [ ] **Step 1: Failing tests** with captured fixtures:
  - `ubuntu_eth0.txt` — typical host with docker0, veth*, amn0 and eth0 as default → iface eth0, values match hand-computed;
  - `ipv6_only.txt` → iface from `ipv6_route`;
  - `two_defaults.txt` → lower metric wins;
  - override present / absent;
  - missing `@@dev` → iface/net None;
  - missing `@@boot` → ValueError;
  - rates: None baseline; same boot 60 s (numbers); boot change; Δuptime 10 s and 400 s; counter decrease on net only (CPU still valid); iface change (net None, CPU valid).
- [ ] **Step 2: Run** `uv run pytest -q -p no:logging tests/unit/test_metrics_parse.py` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `app/domain/metrics.py`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): parse server load samples and compute rates`

### Task 3: Load level and recommendations (pure)

**Files:**
- Create: `panel/backend/app/domain/load.py`
- Test: `panel/backend/tests/unit/test_load_rules.py`

**Interfaces — Produces:**
```python
Level = Literal["low", "medium", "high", "unknown"]
LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "unknown": 3}

@dataclass(frozen=True)
class MetricWindow: avg: float | None; points: int; last_at: datetime | None
@dataclass(frozen=True)
class LoadWindow: cpu: MetricWindow; mem: MetricWindow; net_mbps: MetricWindow; clients: MetricWindow
@dataclass(frozen=True)
class Capacity: bandwidth_mbps: int | None; expected_clients: int | None
@dataclass(frozen=True)
class History:            # from minute samples
    p95_24h: dict[str, float | None]; hours_24h: dict[str, float]   # keys cpu, mem, net
    p95_7d: dict[str, float | None]; days_7d: dict[str, float]      # net in % of width when set
    peak_net_mbps_7d: float | None
    last_at: datetime | None; last_disk_pct: float | None; last_clients: int | None
@dataclass(frozen=True)
class Recommendation: code: str; severity: Literal["warning", "info"]; params: dict

def utilisations(w: LoadWindow, c: Capacity, now: datetime) -> dict[str, float]   # qualifying metrics, in %
def load_level(w: LoadWindow, c: Capacity, now: datetime) -> tuple[Level, float | None]
def recommended_eligible(w: LoadWindow, c: Capacity, now: datetime) -> bool
def recommendations(w, h: History, c: Capacity, untracked: list[str], metrics_error: str | None,
                    now: datetime) -> list[Recommendation]
```
Qualifying metric: `points >= 5` and `last_at >= now - 10 min` and capacity set for `net` (width) and `clients` (expected). Utilisation: cpu = avg, mem = avg, net = avg / width · 100, clients = avg / expected · 100. Level from the max; boundaries `< 50` low, `<= 80` medium, else high; `unknown` if cpu or mem not qualifying. Recommendation rules exactly as spec §5 (`no_data` when `h.last_at` is None or older than 10 min; `*_high` when p95_24h > 80 and hours_24h ≥ 12; `disk_full` when last_disk_pct > 85; `clients_over` when last_clients > expected; `bandwidth_unset`, `clients_unset`; `bandwidth_exceeded` when peak > width; `untracked_protocols` when list non-empty; `room_to_grow` when level low, all four qualify, every p95_7d < 50 and every days_7d ≥ 5).

- [ ] **Step 1: Failing tests:** boundaries 49.9 / 50 / 80 / 80.1; 4 points → metric excluded; stale 11 min → excluded; cpu missing → unknown; width unset → net ignored; eligibility false when width set but net not qualifying; each recommendation rule on and off; `room_to_grow` suppressed with 4 days of data.
- [ ] **Step 2: Run** `uv run pytest -q -p no:logging tests/unit/test_load_rules.py` — Expected: FAIL.
- [ ] **Step 3: Implement** `app/domain/load.py`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): load level and recommendation rules`

### Task 4: Traffic freshness and the driver contract

**Files:**
- Modify: `panel/backend/app/drivers/base.py` (add `traffic_counters: bool` to the protocol), each driver (`wg.py` True, `xray.py` True, `openvpn.py` True, `socks5.py` True, `ikev2.py`/`mtproxy.py`/`telemt.py` False), `xray.py` read_traffic (raise), `openvpn.py` read_traffic (raise on missing status log), `panel/backend/app/services/traffic.py`
- Test: `panel/backend/tests/unit/test_traffic_freshness.py`, adjust `tests/unit/test_xray_driver.py`, `tests/unit/test_openvpn_driver.py`

**Interfaces — Produces:** `ServerContainer.traffic_read_at` set on a successful read; `Config.counter_read_at`, `Config.last_active_at` per spec §4; `async def active_clients(db, server_id, now) -> int | None` in `app/services/traffic.py`.

- [ ] **Step 1: Failing tests:**
  - Xray stats API error raises `RemoteError`.
  - OpenVPN missing status log raises `RemoteError`.
  - A failed read leaves `traffic_read_at` unchanged.
  - First reading sets `counter_read_at` but not `last_active_at`.
  - A second reading 5 min later with a delta sets `last_active_at`.
  - A reading 20 min after the previous one with a delta does not.
  - `active_clients`: 2 recent configs → 2; one traffic-capable container stale → None; only IKEv2 containers → None.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests and the full suite. Existing traffic tests may need fixtures for the stricter drivers: fix the fixtures, not the contract.
- [ ] **Step 5: Commit** `feat(panel): per-container traffic freshness and recently active configs`

### Task 5: Sampling service and the sampler loop

**Files:**
- Create: `panel/backend/app/services/metrics.py`, `panel/backend/app/jobs/sampler.py`
- Modify: `panel/backend/app/main.py` (start/stop the sampler with the scheduler), `panel/backend/app/jobs/periodic.py` (`cleanup` deletes samples older than 30 days in batches), `panel/backend/app/api/admin_servers.py` (`add_server` schedules an immediate specs refresh via `app.state.sampler.kick(server_id)` when present), `panel/backend/tests/fakes.py` (`FakeRemote.run` returns `self.host_outputs` by substring)
- Test: `panel/backend/tests/unit/test_sampler.py`

**Interfaces — Produces:**
```python
# app/services/metrics.py
SAMPLE_TIMEOUT_S = 20
async def sample_server(sessionmaker, server_id: int, remote_factory, clock) -> None
    # 1) short session: load Server (return if gone), previous sample as Baseline, capacities, specs_at
    # 2) SSH (no session open): run SAMPLE_SCRIPT; SPECS_SCRIPT + link speed when specs_at missing/older than 24 h
    # 3) short session: compute rates, active_clients, insert ServerSample; update specs; clear metrics_error;
    #    IntegrityError (server deleted) → rollback and return
    # on RemoteError/ValueError/TimeoutError: short session sets metrics_error (sanitised, 500 chars) and metrics_error_at
async def cleanup_samples(db, now) -> int   # batches of 10 000, returns rows deleted

# app/jobs/sampler.py
SAMPLER_LOCK_KEY = 0x70616E656C6C6F61  # "panelloa"
class Sampler:
    def __init__(self, engine, sessionmaker, remote_factory, clock, interval_s=60, concurrency=8): ...
    async def run_forever(self, stop: asyncio.Event) -> None   # leader lock on a dedicated connection; ticks never overlap
    async def tick(self) -> None                                # samples all servers, bounded by concurrency
    def kick(self, server_id: int) -> None                      # sample one server soon (after add)
```

- [ ] **Step 1: Failing tests:**
  - A sample is stored with rates from the previous one.
  - `specs_json` is filled on the first sample and not refreshed within 24 h.
  - A `RemoteError` sets `metrics_error` and leaves `last_error` unchanged.
  - A server deleted between steps 1 and 3 is skipped without error.
  - Two `Sampler`s on one database: only the leader's `tick` runs (the second sees the lock held).
  - A slow tick delays the next one instead of overlapping (injected clock and a fake sleeping remote).
  - `run_forever` returns within 5 s after `stop` is set.
  - `cleanup_samples` removes only rows older than 30 days, in several batches (batch size patched to 2).
- [ ] **Step 2: Run** `uv run pytest -q -p no:logging tests/unit/test_sampler.py` — Expected: FAIL.
- [ ] **Step 3: Implement.** Per-server work is wrapped in `asyncio.timeout(SAMPLE_TIMEOUT_S)`. The leader connection:
  - It is `engine.connect()` in AUTOCOMMIT isolation, held open. It runs `SELECT pg_try_advisory_lock(:k)`, and nothing else ever uses this connection.
  - A tick that overruns is allowed to finish; the next tick starts when it ends (ticks never overlap, nothing is cut short).
  - While a tick runs, a watchdog task runs `SELECT 1` on the leader connection every 10 s (and once before the tick). If it fails, the watchdog cancels the tick's outstanding work and gives up leadership: it closes the connection with `invalidate()`, so the pool never reuses a connection that may still hold the lock. The loop then retries acquiring the lock every tick.
  - On stop: `SELECT pg_advisory_unlock(:k)`, then close.
  - Tests add connection loss followed by a takeover from a second `Sampler`.
- [ ] **Step 4: Run** the tests and the full suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): sample server load every minute`

### Task 6: Load summaries (SQL)

**Files:**
- Create: `panel/backend/app/services/load_summary.py`
- Test: `panel/backend/tests/unit/test_load_summary.py`

**Interfaces — Produces:**
```python
async def windows(db, server_ids: list[int], now) -> dict[int, LoadWindow]       # one query, FILTER per metric
async def history(db, server: Server, now) -> History
async def series(db, server_id: int, now, range_: Literal["24h", "7d"]) -> list[dict]  # minute points or date_bin 15 min
async def levels(db, servers: list[Server], now) -> dict[int, tuple[Level, float | None, bool]]  # level, pct, eligible
```
Window SQL: rows with `ts > now - 15 min`, per metric `avg(x) FILTER (WHERE x IS NOT NULL)`, `count(x)`, `max(ts) FILTER (WHERE x IS NOT NULL)`; net = `greatest(rx_mbps, tx_mbps)`. History: `percentile_cont(0.95)` over 24 h and 7 d; hours/days of valid points = `count(x) / 60.0` and `/ 1440.0`; peak = `max(greatest(rx_mbps, tx_mbps))` over 7 d; last sample row.

- [ ] **Step 1: Failing tests** seeding samples at fixed times:
  - window averages and counts;
  - stale points outside 15 min ignored;
  - gaps are explicit: the 7-day series has every 15-minute bucket of the range, with null values where there were no samples (`generate_series` LEFT JOIN); the 24-hour series inserts one all-null point wherever two consecutive samples are more than 2 minutes apart; recharts draws a gap only for null points (`connectNulls={false}`);
  - p95 and peak values;
  - `levels` for three servers in one call.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): load summaries for levels, history and charts`

### Task 7: API

**Files:**
- Modify: `panel/backend/app/api/configs.py` (`my_servers`), `panel/backend/app/api/admin_servers.py` (`server_out` + `list_servers` levels, `ServerPatch` capacities, new `GET /{id}/load`)
- Test: `panel/backend/tests/api/test_server_load.py`

**Interfaces — Produces:**
- `/api/me/servers` → `[{id, name, containers, load, recommended}]` ordered per spec.
- `/api/admin/servers` and `/{id}` → adds `load`, `load_pct`, `bandwidth_mbps`, `expected_clients`, `metrics_iface`.
- `PATCH /api/admin/servers/{id}` → `bandwidth_mbps` (1–1 000 000 | null), `expected_clients` (1–100 000 | null), `metrics_iface` (IFACE_RE | null); explicit null clears (`model_fields_set`).
- `GET /api/admin/servers/{id}/load?range=24h|7d` → `{specs, specs_at, current, window, level, utilisation, capacity, hints: {link_mbps, peak_mbps_7d}, series, peaks, recommendations: [{code, severity, params}], metrics_error, metrics_error_at, untracked_protocols}`.

- [ ] **Step 1: Failing tests:**
  - User order: low, medium, high, unknown.
  - `recommended` goes to the first server in the order, and only when that server is eligible; nobody gets it when the first server is high, or lacks telemetry while the second one qualifies.
  - User response keys are exactly `{id, name, containers, load, recommended}`.
  - Admin gets `load_pct`.
  - PATCH validation (0, 1 000 001, a bad iface → 422); null clears.
  - The load endpoint shape for 24h and 7d.
  - A user gets 403 on the admin load endpoint.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): load levels in the server APIs and the admin load page API`

### Task 8: Web UI

**Files:**
- Modify: `panel/web/src/api/types.ts`, `panel/web/src/api/hooks.ts` (`useServerLoad(id, range)`), `panel/web/src/pages/user/DashboardPage.tsx`, `panel/web/src/pages/admin/ServersPage.tsx`, `panel/web/src/pages/admin/ServerPage.tsx`, `panel/web/src/i18n/{ru,en}.json`
- Create: `panel/web/src/components/LoadBadge.tsx`, `panel/web/src/components/ServerLoadSection.tsx`
- Test: `panel/web/src/pages/user/dashboard.test.tsx`, `panel/web/src/pages/admin/servers.test.tsx`

**Interfaces — Consumes:** Task 7 shapes. **Produces:** `LoadBadge({ level })` (green low, yellow medium, red high, gray unknown); `ServerLoadSection({ serverId })`: hardware card, current values + level, capacity form (NumberInput width, NumberInput clients, TextInput iface; empty → null), 24h/7d SegmentedControl, four recharts `LineChart`s (CPU %, memory %, channel Mbit/s in/out, active configs), peaks, recommendations as Alerts translated from `load.rec.<code>` with params.

- [ ] **Step 1: Failing tests:**
  - Dashboard shows badges in API order and a "Рекомендуем" mark on the recommended server only.
  - Admin servers list shows the level badge.
  - Server page load section: hardware shown; recommendation text rendered from its code; the capacity form sends `{bandwidth_mbps: 500, expected_clients: null, metrics_iface: null}` after the clients field is cleared; lines use `connectNulls={false}` so null points appear as gaps.
- [ ] **Step 2: Run** `npx vitest run src/pages` — Expected: FAIL.
- [ ] **Step 3: Implement.** Keep i18n keys under `load.*` in both languages.
- [ ] **Step 4: Run** `npx vitest run && npm run build && npx playwright test` — Expected: PASS.
- [ ] **Step 5: Commit** `feat(panel): load badges, recommended server and the admin load section`

### Task 9: Qt admin

**Files:**
- Modify: `client/ui/qml/Pages2/PagePanelUser.qml` (issue list text shows the level), `client/translations/*.ts` (new strings; ru, uk translated, others unfinished)

- [ ] **Step 1:** Add a `loadText(level)` function (Low / Medium / High load, No load data) and append it to each server row in the issue list.
- [ ] **Step 2:** Add the 4 strings to every `.ts` file (script as before).
- [ ] **Step 3: Verify** by building the client (`%TEMP%\panel-build.bat`), `qmllint` on the page and `lrelease` on the ru/uk files — Expected: build succeeds, 0 errors. (The client has no test suite; ruling recorded in the ledger.)
- [ ] **Step 4: Commit** `feat(panel): show the server load level in the Qt issue list`

### Task 10: Integration test and docs

**Files:**
- Create: `panel/backend/tests/integration/test_metrics_sampling.py`
- Modify: `panel/README.md`, `panel/README_RU.md`

- [ ] **Step 1: Test** — sample the SSH test host twice through `open_remote` (as the other integration tests do) 35 s apart with a patched uptime threshold of 1 s; assert non-null cpu_pct, mem_pct, disk_pct, a detected iface, and specs with cores ≥ 1.
- [ ] **Step 2: Run** `uv run pytest -q -p no:logging -m integration tests/integration/test_metrics_sampling.py` — Expected: PASS.
- [ ] **Step 3: Docs** — a "Server load" section in both READMEs: what is measured, capacities to set, levels, recommendations, why the channel width is manual.
- [ ] **Step 4: Commit** `test(panel): sampling a real host; docs(panel): server load`
