# Amnezia Panel — Stage 1: Backend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working panel backend. It manages users, invite keys, servers, and AWG/WireGuard configs through SSH, with blocking, expiry, a config limit, and traffic accounting, and it ships as docker compose.

**Architecture:** FastAPI app with PostgreSQL as the source of truth. A job queue in PostgreSQL feeds an in-process worker that reconciles the desired state (active configs in the DB) onto the servers over asyncssh. Protocol drivers share one interface. Stage 1 implements the AWG (`amnezia-awg2` and legacy `amnezia-awg`) and WireGuard (`amnezia-wireguard`) drivers.

**Tech Stack:** Python 3.12+ (tested on 3.14), uv, FastAPI, SQLAlchemy 2 (async, asyncpg), Alembic, asyncssh, APScheduler 3, argon2-cffi, zxcvbn, cryptography, segno (QR), pydantic-settings, pytest + pytest-asyncio + httpx + testcontainers.

**Spec:** `docs/superpowers/specs/2026-09-26-amnezia-panel-design.md`

## Global Constraints

- All timestamps are stored in UTC (`timestamptz`). Dates are computed in the `PANEL_TZ` timezone.
- Secrets (`*_enc` fields) are encrypted with AES-256-GCM using `PANEL_MASTER_KEY` (32 bytes, base64).
- Passwords are hashed with argon2id. A password must be at least 12 characters and have a zxcvbn score of at least 3.
- An invite key is 128 random bits in base32, grouped in fours. Only its SHA-256 is stored. A key is single-use and can be revoked.
- Session tokens are opaque. Only their SHA-256 is stored. Admin tokens live 12 hours and user tokens 30 days; the TTL slides on use.
- Rate limits: login 5/min per login and 20/min per IP; invite endpoints 10/hour per IP.
- Errors use the JSON format `{"code": str, "message": str}`.
- Only one job runs per server at a time (`pg_advisory_xact_lock`). Retries back off 1, 2, 4 … minutes, capped at 30.
- Server commands mirror the Qt client:
  - `sudo docker exec -i <container> ...`;
  - AWG2 uses `awg` / `awg0` / `/opt/amnezia/awg/awg0.conf`;
  - legacy AWG uses `/opt/amnezia/awg/wg0.conf` with `wg` / `wg0`;
  - WireGuard uses `wg` / `wg0` / `/opt/amnezia/wireguard/wg0.conf`;
  - changes are applied with `<bin> syncconf <iface> <(<bin>-quick strip <conf>)`.
- `vpn://` is the base64url encoding (no padding) of `qCompress(json)`, which is a 4-byte big-endian length followed by a zlib stream (level 8). The JSON shape matches `SelfHostedAdminServerConfig::toJson`.
- The code lives in `panel/backend/`. Unit tests do not touch the network. Integration tests are marked `@pytest.mark.integration` and need Docker.

## Review Focus

1. **Two parallel "create config" requests at the limit.** Exactly one succeeds and the other gets `409 config_limit`. Tested in Task 12.
2. **Server unreachable while a config is created.** The client gets `503 server_unavailable` and no row stays in the DB. Tested in Task 12.
3. **Traffic counter goes down** after a peer is re-added or the container restarts. The delta is the new value, never negative. Tested in Task 4.
4. **Admin extends `expires_at` for an expiry-blocked user.** The block is lifted. An admin block survives the extension. Tested in Task 4 and Task 11.
5. **Peers unknown to the panel exist on the server.** They are imported as orphans and never removed by reconcile. Tested in Task 9.

---

## File Structure

```
panel/backend/
  pyproject.toml
  alembic.ini
  Dockerfile
  app/
    __init__.py
    main.py                 # create_app(), lifespan: scheduler + worker
    config.py               # Settings (pydantic-settings)
    errors.py               # ApiError + handlers
    cli.py                  # `panel create-admin`, `panel migrate`
    security/
      passwords.py          # hash/verify argon2id, validate_password
      secretbox.py          # SecretBox AES-GCM
      tokens.py             # new_token(), sha256_hex(), new_invite_key(), normalize_invite_key()
      ratelimit.py          # RateLimiter (in-memory sliding window)
    db/
      base.py               # Base, engine/session factory
      models.py             # all ORM models
      migrations/           # alembic env + versions
    domain/
      clock.py              # Clock protocol, SystemClock, FixedClock
      rules.py              # pure rules: activity, unblock rights, expiry, limit, traffic delta
    api/
      deps.py               # get_session, current_admin, current_user, csrf
      auth.py               # /api/auth/*
      admin_users.py        # /api/admin/users*
      admin_servers.py      # /api/admin/servers*
      configs.py            # /api/admin/configs*, /api/me/configs*
      me.py                 # /api/me, /api/me/servers, /api/me/password
      traffic.py            # /api/admin/traffic
      jobs.py               # /api/jobs/{id}
      audit.py              # /api/admin/audit + audit() helper
      schemas.py            # pydantic request/response models
    ssh/
      conn.py               # SshTarget, open_conn(), Remote (run, read_file, write_file)
    drivers/
      base.py               # Driver protocol, dataclasses, registry
      wg.py                 # WgFamilyDriver (awg2, awg legacy, wireguard)
      wgconf.py             # parse/serialize wg .conf, peers
    render/
      vpnkey.py             # encode_vpn_key / decode_vpn_key
      qr.py                 # qr_svg()
    services/
      reconcile.py          # reconcile_server(), import_server()
      configs.py            # create_config(), render_config()
      servers.py            # add_server(), discover_containers()
      traffic.py            # collect_traffic()
      expiry.py             # expire_users()
    jobs/
      queue.py              # enqueue(), claim(), finish(), fail()
      worker.py             # Worker loop, handlers registry
      scheduler.py          # APScheduler wiring
  tests/
    conftest.py             # pg container, app client, factories, FakeDriver/FakeRemote
    unit/...
    api/...
    integration/...         # ssh host container + real amnezia-awg2/wireguard
    docker/sshhost/Dockerfile
panel/deploy/
  docker-compose.yml  Caddyfile  .env.example
panel/README.md
```

---

### Task 1: Project scaffold and health endpoint

**Files:**
- Create: `panel/backend/pyproject.toml`, `panel/backend/app/{__init__,main,config,errors}.py`, `panel/backend/tests/conftest.py`, `panel/backend/tests/api/test_health.py`, `panel/.gitignore`

**Interfaces:**
- Produces:
  - `Settings` with the fields `database_url: str`, `master_key: str`, `tz: str = "UTC"`, `cookie_secure: bool = True`, `scheduler_enabled: bool = True`, `env_prefix="PANEL_"`;
  - `get_settings()`;
  - `create_app(settings: Settings | None = None) -> FastAPI`;
  - `ApiError(status: int, code: str, message: str)`.

- [ ] **Step 1: Write the failing test.**

```python
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}

async def test_api_error_format(client):
    r = await client.get("/api/nope")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
```

- [ ] **Step 2:** Run `uv run pytest tests/api/test_health.py`. It fails because the app is missing.
- [ ] **Step 3:** Write the minimal app with `/api/health` and error handlers that map `ApiError`, `HTTPException` (404 becomes `not_found`) and `RequestValidationError` (`validation_error`) to `{code, message}`. The `client` fixture uses `httpx.AsyncClient(transport=ASGITransport(app))`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): scaffold backend`.

### Task 2: Security primitives

**Files:**
- Create: `app/security/{passwords,secretbox,tokens,ratelimit}.py`, `tests/unit/test_security.py`

**Interfaces:**
- Produces:
  - `hash_password(p) -> str` and `verify_password(hash, p) -> bool`;
  - `validate_password(p, user_inputs=()) -> list[str]` returns error codes: `too_short` or `too_weak`;
  - `SecretBox(key_b64)` with `.encrypt(bytes|str) -> str` (base64 of nonce‖ct) and `.decrypt(str) -> bytes`;
  - `new_token() -> str` (32 bytes, urlsafe) and `sha256_hex(s) -> str`;
  - `new_invite_key() -> str` in the form `XXXX-XXXX-…` (26 base32 characters, grouped by 4);
  - `normalize_invite_key(s) -> str` (uppercase, dashes and spaces removed);
  - `RateLimiter(limit, window_s, clock)` with `.hit(key) -> bool` (False when the limit is exceeded).

- [ ] **Step 1: Write the failing tests.**

```python
def test_password_roundtrip():
    h = hash_password("correct horse battery staple 9")
    assert h.startswith("$argon2id$") and verify_password(h, "correct horse battery staple 9")
    assert not verify_password(h, "wrong")

def test_password_policy():
    assert validate_password("short") == ["too_short"]
    assert validate_password("aaaaaaaaaaaa") == ["too_weak"]
    assert validate_password("Vq7#mZ2!rT9p@Lx") == []

def test_secretbox_roundtrip_and_tamper():
    box = SecretBox(base64.b64encode(os.urandom(32)).decode())
    c = box.encrypt("secret"); assert box.decrypt(c) == b"secret"
    bad = base64.b64encode(b"x" * 40).decode()
    with pytest.raises(Exception): box.decrypt(bad)

def test_invite_key_format_and_normalize():
    k = new_invite_key()
    assert re.fullmatch(r"([A-Z2-7]{4}-){6}[A-Z2-7]{2}", k)
    assert normalize_invite_key(k.lower().replace("-", " ")) == k.replace("-", "")

def test_ratelimiter():
    clk = FixedClock(datetime(2026,1,1,tzinfo=UTC)); rl = RateLimiter(2, 60, clk)
    assert rl.hit("a") and rl.hit("a") and not rl.hit("a")
    clk.advance(61); assert rl.hit("a")
```

- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement. `domain/clock.py` gives `Clock` (`now() -> datetime` aware UTC), `SystemClock`, and `FixedClock(dt)` with `.advance(seconds)`. zxcvbn gets `user_inputs`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): security primitives`.

### Task 3: Database models and migration

**Files:**
- Create: `app/db/{base,models}.py`, `alembic.ini`, `app/db/migrations/{env.py,script.py.mako,versions/0001_initial.py}`, `tests/unit/test_models.py`
- Modify: `tests/conftest.py` (session-scoped `pg_url` from `testcontainers.postgres.PostgresContainer("postgres:16-alpine")`; the function-scoped `db` fixture runs Alembic upgrade once and truncates all tables per test)

**Interfaces:**
- Produces:
  - `Base`;
  - `make_engine(url)` and `make_sessionmaker(engine)`;
  - the ORM classes `Admin`, `User`, `InviteKey`, `Server`, `ServerContainer`, `Config`, `TrafficDaily`, `Session`, `Job`, `AuditLog`. Columns follow spec §5 exactly, plus:
    - `Config.material_enc`;
    - `Config.counter_session`;
    - `Server.host_key`, `Server.prepared_at`, `Server.imported_at`, `Server.last_ok_at`, `Server.last_error`;
    - `InviteKey.revoked_at`;
    - `Job.status` in `('queued','running','done','failed')`, `Job.dedupe_key`;
    - `Session.subject` in `('admin','user')`.
  - Enums are stored as `String` with CHECK constraints.
  - Unique constraints: `(server_id, container, client_id)` on configs; a partial unique index on `jobs.dedupe_key WHERE status IN ('queued','running')`.

- [ ] **Step 1: Write the failing tests.** Insert a user, a server and a config, then read them back. Inserting a duplicate `(server_id, container, client_id)` raises `IntegrityError`. Two queued jobs with the same `dedupe_key` raise `IntegrityError`.
- [ ] **Step 2:** Run the tests (integration marker off: the tests use Postgres via testcontainers). They fail.
- [ ] **Step 3:** Write the models and the Alembic migration (autogenerate from the models, then review).
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): database schema`.

### Task 4: Domain rules

**Files:**
- Create: `app/domain/rules.py`, `tests/unit/test_rules.py`

**Interfaces:**
- Produces:
  - `is_config_active(cfg_blocked_by, cfg_deleted_at, user: UserState | None, now) -> bool`, where `UserState(blocked_by, deleting_at, expires_at)`;
  - `user_can_unblock(cfg_blocked_by) -> bool` (True only for `'user'`);
  - `expiry_instant(d: date, tz: str) -> datetime`, which returns 00:00 of `d+1` in `tz`, converted to UTC;
  - `after_expiry_change(blocked_by, new_expires_at, now) -> str | None` clears `'expiry'` when the new instant is in the future or None, and keeps `'admin'`;
  - `is_expired(expires_at, now)`;
  - `traffic_delta(last: int|None, last_session, new: int, new_session) -> int`.

- [ ] **Step 1: Write the failing tests.**

```python
NOW = datetime(2026,9,26,12,tzinfo=UTC)
def test_active_matrix():
    u = UserState(None, None, None)
    assert is_config_active(None, None, u, NOW)
    assert not is_config_active("user", None, u, NOW)
    assert not is_config_active(None, NOW, u, NOW)
    assert not is_config_active(None, None, UserState("admin", None, None), NOW)
    assert not is_config_active(None, None, UserState(None, NOW, None), NOW)
    assert not is_config_active(None, None, UserState(None, None, NOW), NOW)  # expired at boundary
    assert is_config_active(None, None, None, NOW)                          # orphan
def test_expiry_instant_msk():
    assert expiry_instant(date(2026,9,30), "Europe/Moscow") == datetime(2026,9,30,21,tzinfo=UTC)
def test_after_expiry_change():
    assert after_expiry_change("expiry", NOW + timedelta(days=1), NOW) is None
    assert after_expiry_change("expiry", NOW - timedelta(days=1), NOW) == "expiry"
    assert after_expiry_change("admin", NOW + timedelta(days=1), NOW) == "admin"
    assert after_expiry_change("expiry", None, NOW) is None
def test_traffic_delta():
    assert traffic_delta(None, None, 100, None) == 100
    assert traffic_delta(100, None, 150, None) == 50
    assert traffic_delta(150, None, 20, None) == 20      # counter reset
    assert traffic_delta(150, "s1", 170, "s2") == 170    # new session
def test_user_can_unblock():
    assert user_can_unblock("user") and not user_can_unblock("admin") and not user_can_unblock(None)
```

- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement the pure functions (stdlib `zoneinfo`).
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): domain rules`.

### Task 5: Auth API, sessions, CLI

**Files:**
- Create: `app/api/{deps,auth,me,audit,schemas}.py`, `app/cli.py`, `tests/api/test_auth.py`

**Interfaces:**
- Consumes: Task 2 and Task 3.
- Produces:
  - `POST /api/auth/login {login,password,role}` returns `{token, role}` and sets the cookie `panel_session` (HttpOnly, SameSite=Strict, Secure per setting) plus the readable cookie `panel_csrf`;
  - `POST /api/auth/logout`;
  - `POST /api/auth/invite/check {key}` returns `{ok:true}`, or 404 `invite_invalid`;
  - `POST /api/auth/invite/redeem {key,login,password}` returns `{token, role:"user"}`; its errors are `invite_invalid`, `login_taken`, `password_too_short`, `password_too_weak`;
  - `GET /api/me`;
  - `POST /api/me/password {old,new}` revokes other sessions;
  - deps `current_admin` and `current_user` (Bearer header or cookie; a cookie-authenticated unsafe method requires the header `X-CSRF-Token` equal to the `panel_csrf` cookie, otherwise 403 `csrf`);
  - `audit(session, actor, action, target, **details)`;
  - CLI `python -m app.cli create-admin --login L` (password from stdin or `--password`).
- A blocked user can log in. `/api/me` returns `status` in `active|blocked|expired`.

- [ ] **Step 1: Write the failing tests.**
  - Admin login: OK; wrong password → 401 `invalid_credentials`; 6th attempt in a minute → 429 `rate_limited`.
  - Invite:
    - check;
    - redeem, after which `used_at` is set;
    - a second redeem → 404 `invite_invalid`;
    - a weak password → 422 `password_too_weak`.
  - Bearer token works. A cookie without CSRF on POST → 403. `/api/me/password` rotates the password; the old token of another session gets 401 and the current token still works.
  - A user token on `/api/admin/users` → 403 `forbidden`.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement. Session lookup is `sha256_hex(token)`, not revoked, not expired. Every use sets `last_used_at` and `expires_at = now + ttl`. Rate limiters live in `app.state`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): auth, sessions, invite redeem`.

### Task 6: Job queue and worker

**Files:**
- Create: `app/jobs/{queue,worker}.py`, `app/api/jobs.py`, `tests/unit/test_queue.py`

**Interfaces:**
- Produces:
  - `enqueue(session, kind, payload, server_id=None, dedupe_key=None, run_after=None) -> Job`. When a queued or running job with the same `dedupe_key` exists, it returns that job.
  - `claim(session, now) -> Job | None` uses `FOR UPDATE SKIP LOCKED`, `status='queued' AND run_after<=now`, then sets `running` and `locked_at`.
  - `finish(session, job)`.
  - `fail(session, job, err, now)`: `attempts+1`. When `attempts < 8`, the job returns to `queued` with `run_after = now + min(2**(attempts-1), 30) min`; otherwise it becomes `failed`. The server's `last_error` is set.
  - `Worker(sessionmaker, handlers: dict[str, Callable[[AsyncSession, Job], Awaitable[None]]], clock)` with `.run_once() -> bool` and `.run_forever(stop_event)`. The handler runs inside `pg_advisory_xact_lock(server_id)` when `server_id` is set.
  - `GET /api/jobs/{id}` returns `{id, kind, status, attempts, last_error}` (admin only).

- [ ] **Step 1: Write the failing tests.**
  - `enqueue` dedupe returns the same id.
  - `claim` returns None when `run_after` is in the future.
  - `fail` backoff: 1, 2, 4 … 30 minutes, then `failed` after 8 attempts.
  - `run_once` calls the handler and marks the job `done`. A raising handler leads to `queued` with `last_error`.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): postgres job queue and worker`.

### Task 7: SSH layer

**Files:**
- Create: `app/ssh/conn.py`, `tests/integration/conftest.py`, `tests/docker/sshhost/Dockerfile`, `tests/integration/test_ssh.py`

**Interfaces:**
- Produces:
  - `SshTarget(host, port, user, password: str|None, private_key: str|None, host_key: str|None)`.
  - `async fetch_host_key(host, port) -> str` returns the OpenSSH public key line.
  - `class HostKeyMismatch(Exception)`.
  - `open_remote(target) -> AsyncContextManager[Remote]`. It rejects a host key that differs from `target.host_key`; with `host_key=None` it accepts any key.
  - `Remote.run(cmd, input: str|None=None, check=True, timeout=60) -> RunResult(stdout, stderr, exit_status)`.
  - `Remote.container_exec(container, script: str) -> str` runs `sudo docker exec -i <c> bash -s` with the script on stdin.
  - `Remote.read_container_file(container, path) -> str`.
  - `Remote.write_container_file(container, path, content)` writes via `docker exec -i <c> sh -c 'cat > path.tmp && mv path.tmp path'`.
  - `Remote.list_containers() -> list[str]` via `sudo docker ps --format '{{.Names}}'`.
  - `class RemoteError(Exception)`.
  - Test host image: alpine + openssh + sudo + docker-cli, user `panel` with NOPASSWD sudo, the docker socket mounted.

- [ ] **Step 1: Write the failing integration test.** Start the sshhost container with `/var/run/docker.sock` mounted. `fetch_host_key` returns a key. `run("echo hi")` returns `"hi\n"`. A wrong `host_key` raises `HostKeyMismatch`. Container file write and read work against a throwaway `alpine sleep` container. `list_containers` includes it.
- [ ] **Step 2:** Run `uv run pytest -m integration tests/integration/test_ssh.py`. It fails.
- [ ] **Step 3:** Implement with asyncssh (`known_hosts` built from the stored key).
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): ssh remote layer`.

### Task 8: WG-family driver and renderers

**Files:**
- Create:
  - `app/drivers/{base,wgconf,wg}.py`, `app/render/{vpnkey,qr}.py`;
  - `tests/unit/test_wgconf.py`, `tests/unit/test_vpnkey.py`, `tests/unit/test_wg_driver.py` (FakeRemote);
  - `tests/integration/test_wg_driver.py` plus an install helper that builds and runs `client/server_scripts/{awg,wireguard}` with substituted vars.
- Create: `app/services/install.py`. It builds, runs, configures and starts containers from `client/server_scripts` (the path comes from setting `scripts_dir`, default is the repo path, and the Docker image copies the scripts).

**Interfaces:**
- Produces:
  - `ClientMaterial(client_id: str, data: dict)`.
  - `ClientInfo(client_id: str, name: str|None, data: dict)`.
  - `Counter(rx: int, tx: int, session: str|None)`.
  - `Rendered(vpn_key: str, native: str, native_filename: str)`.
  - `Driver` protocol:
    - `container: str`;
    - `async read_params(remote) -> dict`;
    - `async list_clients(remote) -> dict[str, ClientInfo]`;
    - `async create_material(remote, params, taken: set[str]) -> ClientMaterial`;
    - `async apply(remote, desired: list[ClientMaterial], known_ids: set[str])` makes the server peer set equal to `desired ∪ (actual − known_ids)`: it removes only the peers whose id panel knows but which are not desired;
    - `async read_traffic(remote) -> dict[str, Counter]`;
    - `render(material, params, server_host, dns: tuple[str,str], description) -> Rendered`.
  - `get_driver(container: str) -> Driver`, and `SUPPORTED = {"amnezia-awg2","amnezia-awg","amnezia-wireguard"}`.
  - `wgconf.parse(text) -> WgConf(interface_lines: list[str], peers: list[Peer(public_key, psk, allowed_ips, extra_lines)])` and `wgconf.dump(conf) -> str`. The round trip preserves the interface section.
  - `encode_vpn_key(obj: dict) -> str` and `decode_vpn_key(s: str) -> dict`.
  - `qr_svg(text) -> str`.
  - `install_container(remote, container, vars: dict) -> None`.
- Material `data` holds `{private_key, public_key, psk, ip}`. Params hold `{port, subnet_address, subnet_cidr, server_public_key, psk, awg: {Jc..., H1..., ...}}`, read from the conf interface section and the key files. `create_material` picks the next free IP after the highest used one and skips `.0`, `.255` and the server `.1`, the same way the Qt configurator does.
- The native config follows `client/server_scripts/<awg|wireguard>/template.conf` with the empty-value lines dropped. The vpn JSON is:

```json
{"containers":[{"container":"amnezia-awg2","awg":{<server awg params>,"port":"<port>","transport_proto":"udp","last_config":"<compact json of client cfg>"}}],
 "defaultContainer":"amnezia-awg2","description":"<desc>","dns1":"1.1.1.1","dns2":"1.0.0.1","hostName":"<host>"}
```

  The client cfg JSON contains `config` (native text), `hostName`, `port` (int), `client_ip`, `client_priv_key`, `client_pub_key`, `server_pub_key`, `psk_key`, `clientId`, `allowed_ips`, `persistent_keep_alive`, `mtu`, and the AWG params. Exact key strings are copied from `client/core/utils/constants/configKeys.h` during implementation.

- [ ] **Step 1: Write the failing unit tests.**
  - `wgconf`: parse and dump round trip; add or remove peer.
  - `vpnkey`: `decode(encode(x)) == x`. The first 4 decoded bytes are the big-endian length of the JSON. A fixture string produced by Qt `qCompress` (hex written into the test) decodes.
  - Driver with FakeRemote (an in-memory filesystem that records commands):
    - `apply` keeps unknown peers and removes known-undesired peers;
    - `apply` calls `syncconf`;
    - `create_material` skips taken IPs;
    - `read_traffic` parses the `wg show <iface> dump` format.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement. X25519 keys come from `cryptography`.
- [ ] **Step 4:** Unit tests pass.
- [ ] **Step 5: Write the integration test** (for `amnezia-awg2` and `amnezia-wireguard`):
  1. Install the container through the sshhost.
  2. `create_material` + `apply`: the peer appears in `wg show`.
  3. `apply` without it: the peer is gone.
  4. `apply` again: the peer is back.
  5. `read_traffic` returns counters.
  6. The rendered native config parses.

  Run it. It must pass.
- [ ] **Step 6:** Commit: `feat(panel): wireguard/awg driver, vpn:// encoder`.

### Task 9: Reconcile and import services

**Files:**
- Create: `app/services/reconcile.py`, `app/services/servers.py`, `tests/unit/test_reconcile.py`

**Interfaces:**
- Consumes: the Driver protocol and the models.
- Produces:
  - `async reconcile_server(session, server_id, remote_factory, clock) -> None`. For each `ServerContainer` with a supported driver:
    1. `actual = list_clients`.
    2. Unknown ids are imported as orphan configs (`user_id=None`, `material_enc=None`, `name` from clientsTable or `"Imported <id[:8]>"`).
    3. `desired` = the active configs with material (`is_config_active`) plus the active orphans without material, as passthrough.
    4. `apply(desired, known_ids = all panel config client_ids for this container)`.
    5. Configs with `deleted_at` are hard-deleted after `apply` succeeds.
    6. Users with `deleting_at` whose configs are all gone are hard-deleted.
    7. `server.last_ok_at = now`, `last_error = None`.
  - Orphans without material stay on the server while they are active. They are removed only when blocked or deleted (they are "known").
  - `async import_server(...)` is the same as reconcile with `apply` skipped when `server.imported_at` is None; afterwards `imported_at` is set.
  - `async discover_containers(session, server, remote)` upserts `ServerContainer` for each supported container in `list_containers()` together with `read_params`.
  - `remote_factory(server) -> AsyncContextManager[Remote]` decrypts credentials with the SecretBox.

- [ ] **Step 1: Write the failing tests** with FakeDriver (in-memory server state):
  - An unknown peer is imported as an orphan and kept.
  - A blocked user's config is removed from the server, and the row stays.
  - Unblock returns it.
  - A deleted config is removed from the server, then the row is gone.
  - A `deleting_at` user is gone after reconcile.
  - The first import never removes anything.
  - A remote error propagates and does not hard-delete anything.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): reconcile and import`.

### Task 10: Admin servers API

**Files:**
- Create: `app/api/admin_servers.py`, `tests/api/test_admin_servers.py`

**Interfaces:**
- Produces:
  - `POST /api/admin/servers {name, host, ssh_port=22, ssh_user, ssh_password?|ssh_private_key?}` calls `fetch_host_key`, stores the server with `host_key`, and enqueues `server_import` (dedupe `import:<id>`). It returns `202 {server, job_id}`.
  - `GET /api/admin/servers` and `GET /api/admin/servers/{id}` (containers, `last_ok_at`, `last_error`, `enabled_for_users`, `host_key`).
  - `PATCH {name?, enabled_for_users?, ssh_*?}`.
  - `DELETE` removes the server from the panel only (configs cascade).
  - `POST /{id}/sync` enqueues `reconcile` (dedupe `reconcile:<id>`) and returns 202.
  - `POST /{id}/host-key/accept` fetches and stores the current key and returns 200.
  - `POST /{id}/containers {container, port?}` enqueues `install_container` and returns 202 (awg2/wireguard only in stage 1; otherwise 422 `unsupported_container`).
  - Job handlers: `server_import` (discover + import), `reconcile`, `install_container` (install + discover + reconcile). `HostKeyMismatch` fails the job with `host_key_mismatch` and does not retry until accepted.
  - The SSH secret is never returned in responses.

- [ ] **Step 1: Write the failing tests** (`fetch_host_key` and remote_factory monkeypatched to fakes): create returns 202 and a job exists; the response has no secret; the list works; sync dedupes; a user token → 403.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): admin servers api`.

### Task 11: Admin users API

**Files:**
- Create: `app/api/admin_users.py`, `tests/api/test_admin_users.py`

**Interfaces:**
- Produces:
  - `POST /api/admin/users {display_name, note?, expires_on?: date, max_configs}` returns `201 {user, invite_key}`.
  - `GET /api/admin/users?status=&q=` lists users with `configs_count` and `traffic_total`.
  - `GET /{id}` returns the user, configs and traffic per server.
  - `PATCH {display_name?, note?, expires_on?: date|null, max_configs?}` applies `after_expiry_change`. `max_configs` below the current count is allowed and only blocks new configs.
  - `POST /{id}/block` sets `blocked_by='admin'`. `POST /{id}/unblock` sets `blocked_by=None`, or `'expiry'` if expired.
  - `POST /{id}/invite` revokes the old key and returns the new one. When the user is already registered → 409 `already_registered`.
  - `DELETE /{id}` sets `deleting_at` and `configs.deleted_at` and enqueues reconcile for each affected server. It returns 202.
  - Every change enqueues reconcile for the affected servers and writes the audit log.
  - The user JSON: `{id, display_name, note, login, registered, status, blocked_by, expires_on, max_configs, configs_count, created_at}`.

- [ ] **Step 1: Write the failing tests.**
  - Create returns a key; that key redeems.
  - Block enqueues reconcile per server of the user's configs.
  - Extending an expiry-blocked user → `status=active`. Extending an admin-blocked user → still blocked.
  - Reissuing the invite invalidates the old key.
  - Delete → `deleting_at` is set and login is refused with 401.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): admin users api`.

### Task 12: Configs API (admin and me)

**Files:**
- Create: `app/services/configs.py`, `app/api/configs.py`, `tests/api/test_configs.py`

**Interfaces:**
- Produces:
  - `async create_config(session, user_id|None, server_id, container, name, remote_factory, clock) -> Config`. In one transaction it:
    1. Locks the user row (`FOR UPDATE`) and checks the limit (409 `config_limit`), the user status (403 `user_blocked` / `user_expired`), `server.enabled_for_users` (for user callers) and container support.
    2. Takes the advisory lock on the server.
    3. Opens the remote, runs `create_material` with `taken` = used IPs, then `apply(desired + new)`.
    4. Inserts the config with `material_enc`.

    A remote failure or a timeout (30 s) raises 503 `server_unavailable` and rolls the transaction back.
  - `render_config(cfg, server, params) -> {vpn_key, native, native_filename, qr_svg}`. Without material → 409 `no_material`.
  - Admin routes:
    - `POST /api/admin/users/{id}/configs {server_id, container, name?}`;
    - `GET /api/admin/configs?orphan=true`;
    - `GET /api/admin/configs/{id}` (rendered);
    - `POST /{id}/block|unblock` (admin block);
    - `DELETE /{id}` (`deleted_at` + reconcile);
    - `POST /{id}/assign {user_id}`, which respects the limit.
  - Me routes:
    - `GET /api/me/servers` returns the enabled servers with their supported containers;
    - `GET /api/me/configs` returns the configs with `traffic_total`;
    - `POST /api/me/configs {server_id, container, name?}`;
    - `GET /api/me/configs/{id}`, which gives 403 `user_blocked` / `user_expired` when inactive;
    - `POST /{id}/block|unblock`: unblock only when `blocked_by == 'user'`, otherwise 403 `blocked_by_admin`;
    - `DELETE /{id}`.

    A user cannot touch another user's config (404).

- [ ] **Step 1: Write the failing tests** (FakeDriver and FakeRemote):
  - Create and render; the `vpn_key` decodes to JSON with `hostName`.
  - Limit: two concurrent `POST /api/me/configs` at `max_configs-1` → one 201 and one 409.
  - A remote raising → 503, and the configs count is unchanged.
  - A user unblocks an admin-blocked config → 403.
  - A blocked user renders → 403 `user_blocked`.
  - Another user's config → 404.
  - Assigning an orphan over the limit → 409.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): configs api`.

### Task 13: Traffic, expiry, scheduler, audit and traffic APIs

**Files:**
- Create: `app/services/{traffic,expiry}.py`, `app/jobs/scheduler.py`, `app/api/traffic.py`, `tests/unit/test_traffic_expiry.py`, `tests/api/test_traffic_audit.py`
- Modify: `app/main.py` (lifespan starts the Worker task and the scheduler when `scheduler_enabled`)

**Interfaces:**
- Produces:
  - `async collect_traffic(session, server_id, remote_factory, clock)`. For each config it computes `traffic_delta` and upserts `traffic_daily(day in PANEL_TZ)` with `+rx`, `+tx`. It then updates `last_rx`, `last_tx` and `counter_session`. rx and tx are taken from the server's point of view but stored as the user's download (server tx) and upload (server rx), named `rx`/`tx` from the user's view.
  - `async expire_users(session, clock) -> list[int]` sets `blocked_by='expiry'` and enqueues reconcile for the affected servers.
  - The scheduler enqueues:
    - `expire` every 60 s;
    - `traffic:<server>` every 300 s;
    - `reconcile:<server>` every 900 s;
    - `cleanup` daily (sessions expired, jobs done or failed older than 30 days).
  - `GET /api/admin/traffic?user_id=&server_id=&from=&to=` returns `[{day, server_id, rx, tx}]` plus totals.
  - `GET /api/admin/audit?limit=&before=`.

- [ ] **Step 1: Write the failing tests.**
  - Traffic: two collections accumulate; a counter reset adds the new value; the day boundary in `PANEL_TZ` splits the rows.
  - Expiry: an expired user gets blocked and a reconcile is enqueued; a non-expired user is untouched.
  - The traffic API sums per server.
  - The audit list returns the entries written by earlier actions.
- [ ] **Step 2:** Run the tests. They fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit: `feat(panel): traffic, expiry, scheduler`.

### Task 14: Deploy and end-to-end integration

**Files:**
- Create: `panel/backend/Dockerfile`, `panel/deploy/{docker-compose.yml,Caddyfile,.env.example}`, `panel/README.md`, `tests/integration/test_e2e.py`
- Modify: `AGENTS.md` (a short Panel section: layout, how to run the tests, how to deploy)

**Interfaces:**
- The Docker image copies `client/server_scripts` to `/app/server_scripts` (build context = repo root). The entrypoint runs `alembic upgrade head`, then `uvicorn app.main:app`.
- Compose services:
  - `caddy` (ports 80/443, `PANEL_DOMAIN`, serves `/srv/web` static files, proxies `/api/*`);
  - `backend`;
  - `db` (postgres:16-alpine, volume);
  - `backup` (a daily `pg_dump` loop into the `backups` volume, keeps 14 days).
- The README covers:
  - install steps and first admin creation;
  - the master key warning (without `PANEL_MASTER_KEY` a backup cannot be restored);
  - adding a server;
  - where the logs are.

- [ ] **Step 1: Write the failing end-to-end test.** It uses the real app, the sshhost and a real `amnezia-awg2`:
  1. Admin creates a server through the API and the worker runs the import job.
  2. Admin creates a user and gets the invite.
  3. The user redeems it, creates a config and gets `vpn_key`.
  4. The peer is in `wg show`.
  5. Admin blocks the user and reconcile runs: the peer is gone.
  6. Unblock: the peer is back.
  7. Delete the user: the peer is gone and the rows are gone.
- [ ] **Step 2:** Run it and fix whatever fails.
- [ ] **Step 3:** Write the Dockerfile and compose. Verify with `docker compose -f panel/deploy/docker-compose.yml config`, build the backend image, then `up db backend` and check that `curl /api/health` returns ok.
- [ ] **Step 4:** Run the full test suite: `uv run pytest` and `uv run pytest -m integration`.
- [ ] **Step 5:** Commit: `feat(panel): deploy files, e2e test, docs`.
