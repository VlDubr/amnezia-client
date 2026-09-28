# Amnezia Panel: server load monitoring — design

Date: 2026-09-28. Extends `2026-09-26-amnezia-panel-design.md`. Reviewed with GPT-6-Astra (changes in §9).

## 1. Goal

The panel learns what each VPN server is (hardware) and how busy it is, so that:

- **users** see a load level per server in their cabinet (low / medium / high / no data), servers are sorted from the least loaded, and the least loaded one is marked "recommended" when that is justified;
- **administrators** see, on the server page, the hardware, current values, 24-hour and 7-day charts, peaks and recommendations;
- the Qt admin shows the level next to each server when issuing a config.

The level only informs and orders; it never blocks config issuing (decided with the user).

## 2. Decisions

| Topic | Decision |
|---|---|
| Collection | The panel polls every server over the existing SSH access (pinned host keys). Nothing is installed on servers. One fixed, read-only command reads `/proc`, `/sys` and `df`; no `sudo`. |
| Frequency | Load: once a minute. Hardware facts: when the server is added and once a day. |
| Scheduling | A dedicated sampler loop, not the job queue: sampling is read-only and lossy, must not wait for the per-server lock that config issuing and reconcile hold, and must not add a `jobs` row per minute. |
| Metrics | CPU %, memory %, root disk %, load average, main interface throughput in and out (Mbit/s), recently active configs. |
| Capacity | Set by the admin per server: channel width (Mbit/s), expected active configs, and optionally the interface to measure. All optional. Hints: interface link speed and the 7-day peak throughput. No speed test (it would load the channel of live users and measure the path to a test node, not the tariff). |
| Level | Maximum over the metrics' own 15-minute averages (CPU, memory, channel, clients). A metric qualifies only with at least 5 valid points in the window, its latest valid point at most 10 minutes old, and its capacity set (channel, clients). Low < 50 %, medium 50–80 % inclusive, high > 80 %. The level is `unknown` when CPU or memory does not qualify: these two are always measurable, so their absence means the data is stale or broken. |
| Recommended | The first server in the user's order, only when its level is low or medium and every metric whose capacity is set also qualifies (no missing telemetry behind a good-looking level). |
| Retention | Minute samples for 30 days. The 7-day chart averages them into 15-minute buckets in SQL; gaps stay gaps. Peaks and p95 come from minute samples. |
| Privacy | Users get only `load` and `recommended`, from an allow-listed response; never numbers, errors or hardware. |

## 3. Data model (migration 0004)

```
servers            + bandwidth_mbps INT NULL        -- admin: channel width, each direction
                   + expected_clients INT NULL      -- admin: active configs the server is sized for
                   + metrics_iface VARCHAR(15) NULL -- admin: interface override; default = default-route interface
                   + specs_json JSONB NULL, specs_at TIMESTAMPTZ NULL
                   + metrics_error TEXT NULL, metrics_error_at TIMESTAMPTZ NULL  -- admin only, 500 chars max

server_containers  + traffic_read_at TIMESTAMPTZ NULL  -- last successful read of this container's counters

configs            + counter_read_at TIMESTAMPTZ NULL  -- last time this config's counter was seen
                   + last_active_at TIMESTAMPTZ NULL

server_samples     id BIGINT PK, server_id FK servers ON DELETE CASCADE, ts TIMESTAMPTZ (panel clock),
                   boot_id VARCHAR(36), uptime_s DOUBLE, iface VARCHAR(15) NULL,
                   cpu_busy BIGINT, cpu_total BIGINT,          -- raw jiffies (all CPUs)
                   net_rx BIGINT NULL, net_tx BIGINT NULL,     -- raw bytes of iface
                   cpu_pct REAL NULL, rx_mbps REAL NULL, tx_mbps REAL NULL,  -- vs the previous sample
                   mem_pct REAL NULL, disk_pct REAL NULL, load1 REAL NULL,
                   active_clients INT NULL                     -- NULL = unknown, not zero
                   INDEX (server_id, ts)
```

`specs_json`: `cpu_model`, `cores`, `mem_bytes`, `disk_bytes` (root filesystem), `os`, `kernel`, `iface`, `link_mbps` (null if unknown), `uptime_s`, and `traffic_protocols` / `untracked_protocols` (installed containers with and without traffic counters).

### Rates

A rate is computed against the previous sample of the same server only if it has the same `boot_id`, the uptime difference is 30–300 s, and the counters did not decrease. CPU and network are validated independently (network also needs the same `iface`). Elapsed time is the server's uptime difference, not the panel clock. Otherwise the rate is null.

- CPU busy = total − idle − iowait, from the aggregate `cpu` line.
- Memory % = 1 − MemAvailable / MemTotal.
- Disk % = used / (used + available) of `/`.
- Throughput in and out are separate Mbit/s values; channel utilisation = max(in, out) / width.

## 4. Collection

**Command.** A constant script (no interpolation) prints marked sections: `/proc/sys/kernel/random/boot_id`, `/proc/uptime`, the `cpu` line of `/proc/stat`, `/proc/meminfo`, `df -B1 -P /`, `/proc/loadavg`, `/proc/net/route`, `/proc/net/ipv6_route` and `/proc/net/dev`; the output is cut at 64 KiB. The link speed is read by a second command only when the interface name matches `^[A-Za-z0-9_.:@-]{1,15}$` and is shell-quoted. A pure parser turns the text into a `RawSample`.

**Interface.** The admin's override if set and present; otherwise the IPv4 default route with the lowest metric; otherwise the IPv6 default route; otherwise none (throughput unknown).

**Loop.** Started with the app when the scheduler is enabled.
- **One owner.** The loop holds a session-level PostgreSQL advisory lock dedicated to the sampler on its own connection for as long as it runs. A process that cannot take the lock does not sample; it tries again every tick, so another process takes over when the owner dies. A lost connection gives up ownership.
- **Ticks.** Ticks run every 60 s. A tick that overruns makes the next one start when it ends; ticks never overlap.
- **Parallelism and timeout.** Servers are sampled concurrently, at most 8 at a time. Each server has a 20 s budget covering connect, commands and close.
- **No transaction during SSH.** The server list is read in a short session. SSH work happens outside any transaction, and each result is written in its own short session.
- **Failures.** A server deleted meanwhile is skipped. A failure stores a sanitised `metrics_error` and leaves `last_error`, jobs and configs untouched. A database error ends the tick, is logged, and the next tick retries.
- **Shutdown** cancels the loop and waits at most 5 s.
- **Hardware facts** are refreshed by the same loop when `specs_at` is missing or older than 24 h, and right after a server is added.

**Driver contract.** Each driver declares `traffic_counters: bool` (false for IKEv2, MTProxy and Telemt). `read_traffic` must raise (`RemoteError` or `ValueError`) when the counters could not be read. It returns an empty dict only when the read succeeded and no client has traffic. This changes two drivers:

- Xray: an unavailable stats API now raises; it used to return `{}`;
- OpenVPN: a missing status log now raises; it used to read as empty.

SOCKS5 keeps treating a missing log as empty: 3proxy creates it on the first request. Tests check that a failed read never refreshes `traffic_read_at`.

**Recently active configs.** Freshness is tracked per container and per config, so one healthy container cannot hide another's failure:

- `collect_traffic` sets `server_containers.traffic_read_at = now` when a container's counters were read. A failed read leaves it unchanged; an empty result still counts as a successful read.
- For each config found in the counters it sets `counter_read_at = now`. It sets `last_active_at = now` only when the config's previous `counter_read_at` is at most 15 minutes old and the delta is positive. This excludes first readings after import and deltas that span an outage of that container or config.
- A sample stores `active_clients` = configs with `last_active_at` within 15 minutes, only when every container of the server that has traffic counters has `traffic_read_at` within 15 minutes and there is at least one such container. Otherwise it stores null (unknown, not zero).
- IKEv2, MTProxy and Telemt have no per-client counters, so their configs are not counted; the admin page names these protocols.

**Retention.** The daily `cleanup` job deletes samples older than 30 days, in batches of 10 000 rows.

## 5. Level and recommendations

`app/domain/load.py` holds pure functions over a `LoadSummary`: per-metric 15-minute averages with point counts, 24-hour and 7-day p95 and peaks, the last sample, and data coverage.

- `load_level(summary, capacity) -> (level, utilisation)`.
- `recommendations(summary, capacity, specs, now) -> list[Recommendation(code, severity, params)]`.

| Code | Severity | When |
|---|---|---|
| `no_data` | warning | no sample in 10 min (params: last error, last sample time) |
| `cpu_high` / `memory_high` / `channel_high` | warning | 24-hour p95 > 80 % with at least 12 hours of valid points |
| `disk_full` | warning | disk > 85 % in the last sample |
| `clients_over` | warning | active configs in the last sample > expected clients |
| `bandwidth_unset` | info | channel width not set |
| `bandwidth_exceeded` | info | 7-day peak of max(in, out) > channel width |
| `clients_unset` | info | expected clients not set |
| `untracked_protocols` | info | the server has containers without traffic counters |
| `room_to_grow` | info | level low, all four metrics qualify now, every 7-day p95 < 50 %, at least 5 days of valid points for each |

The web translates codes and fills params.

## 6. API

- `GET /api/me/servers` (user): each server gains `load` (`low|medium|high|unknown`) and `recommended`. Order: level (low, medium, high, unknown), then utilisation, then name. The response is built from an allow-list; no other load fields.
- `GET /api/admin/servers` (admin): each server gains `load` and `load_pct`. Summaries for all servers come from one batched query.
- `PATCH /api/admin/servers/{id}` accepts `bandwidth_mbps` (1–1 000 000), `expected_clients` (1–100 000) and `metrics_iface` (the pattern above). An explicit null clears a value; an absent field leaves it.
- `GET /api/admin/servers/{id}/load?range=24h|7d` (admin):
  - `specs`, `current` (last sample), `window` (15-minute averages with point counts);
  - `level`, `utilisation`, `capacity`, `hints` (`link_mbps`, `peak_mbps_7d`);
  - `series`: timestamps with cpu, mem, rx, tx, clients; minute points for 24 h, 15-minute buckets for 7 d;
  - `peaks`, `recommendations`, `metrics_error`, `metrics_error_at`.

## 7. UI

- **Web, user dashboard:** a load badge per server (green / yellow / red / grey "no data"), servers in the API order, a "Recommended" mark.
- **Web, admin servers list:** the level badge.
- **Web, admin server page, new "Load" section:**
  - hardware card;
  - current values with the level;
  - capacity form (channel width, expected clients, interface) with hints;
  - a 24 h / 7 d switch with four small SVG line charts (no chart library: the bundle is already large);
  - peaks;
  - recommendations.

  Texts name the measure precisely: "recently active configs", "per-direction channel use".
- **Qt admin:** the level text next to each server in the "Issue a config" list (from `GET /api/admin/servers`).
- Texts in ru and en (web), ru and uk plus unfinished entries (Qt).

## 8. Testing

- **Parser:** captured outputs; missing sections; veth and docker interfaces; IPv6-only default route; several default routes; the override; oversized output.
- **Rates:** first sample, reboot (`boot_id` change), interface change, counter decrease, short and long gaps, CPU and network validated independently.
- **Level:** boundaries 50 and 80, coverage below 5 points, missing capacities, `unknown`.
- **Recommendations:** each rule, including suppression with too little data.
- **Activity:** first reading after import and a delta after an outage do not count; unsupported-only servers give null.
- **Sampler:**
  - a second owner skips its tick;
  - no overlapping ticks;
  - a failing server does not stop the others and does not change `last_error`;
  - a server deleted during a tick;
  - bounded shutdown;
  - cleanup in batches.
- **API:**
  - users see only `load` and `recommended`, in the right order;
  - no "recommended" when the first server is high or unknown;
  - admin load page;
  - capacity validation and clearing with null;
  - a user cannot read `/api/admin/servers/{id}/load`.
- **Integration:** sampling a real host over SSH (the existing SSH test host).
- **Web:** badges and order, "Recommended", the admin load section and the capacity form.

## 9. Changes after review with GPT-6-Astra

The first draft was reviewed on 2026-09-28. Accepted:

- a single sampler owner holding a lifetime leader lock, no overlapping ticks, bounded timeouts and shutdown, no transactions held during SSH;
- traffic freshness per container and per config; level gated on per-metric freshness; "recommended" and `room_to_grow` only with complete coverage (second round);
- `boot_id` and uptime-based elapsed time; CPU and network baselines validated separately;
- activity counted only from real deltas between recent readings, unknown kept apart from zero, untracked protocols named;
- level as the maximum of per-metric averages with minimum coverage and exact boundaries;
- "recommended" only for a low or medium server;
- interface override and the IPv6 fallback;
- a fixed command, bounded output, sanitised admin-only errors, allow-listed user responses;
- null clears a capacity; batched summaries; batched cleanup.
