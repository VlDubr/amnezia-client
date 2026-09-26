# Amnezia Panel — Stage 3: Remaining Protocol Drivers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Users, blocking, deletion, limits, expiry and traffic work for Xray (VLESS), OpenVPN, SOCKS5, Telemt, MTProxy and IKEv2 as well as AWG/WireGuard, with the same panel flows.

**Architecture:**
- Each protocol gets one driver class in `app/drivers/` behind the Stage 1 `Driver` protocol.
- The driver interface gains a `revoked` set in `apply()`, because some protocols (OpenVPN, IKEv2) block and delete clients differently.
- Server changes that a protocol needs, such as the Xray stats API and the OpenVPN `client-config-dir`/`management`, are applied idempotently inside `apply()` the first time it runs. This is the spec's "prepare" step, needing no separate job.
- Services without a `vpn://` import in the Qt app (SOCKS5, MTProxy, Telemt) are exported as native links only.

**Tech Stack:** as in Stage 1. Integration tests build the real Amnezia containers from `client/server_scripts`.

**Spec:** `docs/superpowers/specs/2026-09-26-amnezia-panel-design.md` (§6)

## Global Constraints

- Drivers must issue the same server commands and use the same file paths as the Qt client.
- A driver may restart its container only when the client set or the one-time preparation actually changes.
- Clients on the server that the panel does not know are never removed.
- `revoked` ids (deleted configs and tombstones) are removed permanently. Other known-but-undesired ids (blocked) are removed reversibly.
- Current Qt sources ship scripts only for `awg`, `awg_legacy`, `wireguard`, `openvpn`, `xray`, `ipsec`, `socks5_proxy`, `mtproxy`, `telemt`, `tproxy`, `sftp`, `website_tor` and `dns`. OpenVPN over Cloak or ShadowSocks and SS over Xray have no server scripts left, so the panel cannot install or issue them. That is a deviation from the spec and is recorded.

## Review Focus

1. **Container restarts.** Xray, MTProxy and Telemt must not be restarted when nothing changed; reconcile runs every 15 minutes. Pinned in each driver's "apply without changes" test.
2. **OpenVPN block versus delete.** A blocked client must be able to come back. A deleted client must end up in the CRL, and its certificate is never reused. Tested in Task 3.
3. **Imported Xray/SOCKS5/Telemt clients.** Their secret is known, so they can be exported (`can_render`). Imported OpenVPN/IKEv2 clients cannot be exported, because their private keys stay with the client. Tested per driver.
4. **Traffic counters that reset on restart** (Xray stats, OpenVPN sessions) never produce negative or double counts. Covered by `traffic_delta` together with the driver session fields.
5. **Link rendering.** Hostnames and ports are rendered as the Qt client does; secrets and passwords are URL-encoded in links.

---

### Task 1: Driver interface: revoked set, renderable secrets, non-vpn exports

**Files:**
- Modify: `app/drivers/base.py`, `app/drivers/wg.py`, `app/services/reconcile.py`, `app/services/configs.py`, `app/services/materials.py`, `app/api/presenters.py`
- Tests: `tests/unit/test_wg_driver.py`, `tests/unit/test_reconcile.py`

**Interfaces:**
- `Driver.apply(remote, desired, known_ids, revoked=frozenset()) -> ApplyResult`.
  - `revoked` ⊆ `known_ids`.
  - Reconcile passes the ids of deleted configs plus tombstones.
  - Config creation passes the new id when it rolls back.
- `has_private_part(data)` is true when `data` has `private_key` or `secret`.
- `Rendered.vpn_key` may be `""` for services. The web UI hides the key field and QR when it is empty and shows `native` instead (Task 8).
- `Driver.installable: bool` and `Driver.install_vars(port) -> dict`. `install.py` uses the registry instead of the hard-coded `INSTALLABLE`.

Steps:

- [ ] Write the failing tests:
  - reconcile passes `revoked` for deleted configs;
  - `has_private_part({"secret": "x"})` is true;
  - install lists the installable containers from the registry.
- [ ] Run them and watch them fail. Implement. Run them and watch them pass. Run the full suite.
- [ ] Commit: `refactor(panel): driver interface for revoked clients and service exports`.

### Task 2: Xray (VLESS) driver

**Files:** `app/drivers/xray.py`, `tests/unit/test_xray_driver.py`, `tests/integration/test_xray_driver.py`, install support (the Qt client writes `server.json` itself, so the driver provides `write_initial_server_config`).

**Behavior:**
- **Paths:** `/opt/amnezia/xray/server.json`, plus `xray_public.key`, `xray_short_id.key` and `xray_uuid.key`.
- **Clients:** `inbounds[0].settings.clients` holds `{id, flow, email=id, level=0}`.
- **Import:** `data = {"secret": id, "flow": flow}`, so imported configs can be exported.
- **Preparation** (idempotent, inside `apply`):
  - `api = {"tag": "api", "listen": "127.0.0.1:10085", "services": ["StatsService"]}`;
  - `stats = {}`;
  - `policy.levels["0"]` with user uplink and downlink stats;
  - `email` and `level` on every client.
- **Apply:** rewrite `server.json`, then `sudo docker restart amnezia-xray`, only when the JSON changed.
- **Traffic:** `xray api statsquery --server=127.0.0.1:10085 -pattern "user>>>"`. Uplink is the server's rx. `session` is None: counters reset on restart, which `traffic_delta` handles.
- **Render:**
  - The client JSON mirrors `XrayConfigurator::buildClientProtocolConfig`.
  - Stream settings are copied from the server inbound. Reality `realitySettings` become `{fingerprint, serverName: serverNames[0], publicKey, shortId: shortIds[0], spiderX: ""}`.
  - vpn:// = `{"container": "amnezia-xray", "xray": {"port", "transport_proto": "tcp", "last_config": <client json>}}`.
  - native = the `vless://` link (`type`, `security`, `pbk`, `sid`, `sni`, `fp`, `flow`, plus `path`/`host`/`mode` for xhttp).

Steps:

- [ ] Write the failing unit tests (FakeRemote):
  - prepare adds api, stats and policy once, and a second apply does not restart;
  - apply keeps unknown clients, removes known ones and adds new ones;
  - read_traffic parses statsquery JSON;
  - render gives a vless link with `pbk`/`sid`/`flow`, and the vpn:// `last_config` has `outbounds[0].settings.vnext[0].users[0].id`.
- [ ] Run them and watch them fail. Implement. Run them and watch them pass.
- [ ] Integration test:
  1. Install amnezia-xray through the panel installer: build, run, configure, write `server.json` (reality, tcp, vision, `www.googletagmanager.com`), start.
  2. Create a client and apply.
  3. Run an xray client container with the rendered JSON and fetch `http://<nginx container IP>/` through its SOCKS inbound: the request succeeds.
  4. Block the client: the fetch fails.
  5. Stats show traffic.
- [ ] Commit: `feat(panel): xray driver`.

### Task 3: OpenVPN driver

**Files:** `app/drivers/openvpn.py`, `tests/unit/test_openvpn_driver.py`, `tests/integration/test_openvpn_driver.py`

**Behavior:**
- **Create:**
  - The panel makes an RSA-2048 key and a CSR with CN = client id (32 hex characters) using `cryptography`.
  - It uploads `/opt/amnezia/openvpn/clients/<id>.req`, then runs `easyrsa import-req` and `easyrsa sign-req client <id>` (EASYRSA_BATCH=1), the same way as `OpenVpnConfigurator::signCert`.
  - It reads `pki/issued/<id>.crt`.
  - Material: `{private_key: pem, cert: pem}`.
- **List:** `pki/index.txt` lines with status `V` whose CN is not `AmneziaReq`.
- **Preparation:** `client-config-dir /opt/amnezia/openvpn/ccd` and `management 127.0.0.1 7505` in `server.conf`, then a container restart, once.
- **Block:** write `ccd/<cn>` containing `disable`, then send `kill <cn>` through management (`nc`).
- **Unblock:** remove the ccd file.
- **Revoke:** `easyrsa revoke <cn>`, `easyrsa gen-crl`, then copy `crl.pem` as `UsersController::revokeOpenVpn` does. The ccd file is also removed.
- **Traffic:** `openvpn-status.log` CLIENT LIST rows; `session` = "Connected Since".
- **Render:** `template.ovpn` with ca, cert, key and ta.key (`tls-auth`); `port`/`proto` come from `server.conf`. vpn:// = `{"container": "amnezia-openvpn", "openvpn": {"port", "transport_proto", "last_config": {"config": ovpn, ...}}}`.

Steps:

- [ ] Write the failing unit tests (FakeRemote):
  - the list parses index.txt;
  - apply blocks with ccd, unblocks by removing it, and revokes into the CRL;
  - preparation happens once;
  - the status parser gives counters and a session;
  - the ovpn render contains all four blocks.
- [ ] Run them and watch them fail. Implement. Run them and watch them pass.
- [ ] Integration test:
  1. Install amnezia-openvpn.
  2. Create a client.
  3. An openvpn client container connects ("Initialization Sequence Completed").
  4. Block: the connection is refused.
  5. Unblock: it connects again.
  6. Revoke: the certificate is listed as revoked in `index.txt`.
- [ ] Commit: `feat(panel): openvpn driver`.

### Task 4: SOCKS5 (3proxy) driver

**Files:** `app/drivers/socks5.py` plus tests

**Behavior:**
- The config is `/usr/local/3proxy/conf/3proxy.cfg`.
- Users go on `users` lines `login:CL:password`, with `auth strong`. Preparation switches `auth none` to `auth strong` if there are users.
- **Apply:** rewrite the users lines, then `docker restart amnezia-socks5proxy` only on change.
- **Import:** `data = {"login", "secret": password}`.
- **Create:** login = `u` + 8 hex characters, password = 24 URL-safe characters.
- **Traffic:** parse the JSON log lines in `/usr/local/3proxy/logs/3proxy.log`; per user, sum `bytes.sent`/`bytes.received` since the last read offset, stored as a byte offset session.
- **Render:** `socks5://login:password@host:port`, with no vpn:// key.

Steps:

- [ ] Write the failing unit tests.
- [ ] Integration test: install the container and create a user. `curl --socks5-hostname user:pass@…` reaches nginx. After a block, `curl` fails.
- [ ] Commit: `feat(panel): socks5 driver`.

### Task 5: Telemt driver

**Files:** `app/drivers/telemt.py` plus tests

**Behavior:**
- `/data/config.toml` `[access.users]` holds one `name = "32hex"` line per client. The client id is the user name (`p` + 10 hex characters).
- **Apply:** rewrite that section, then `docker restart` only on change.
- **Import:** the existing users, with `data = {"secret": hex}`.
- **Render:** a `tg://proxy?server=&port=&secret=` link, using `ee`+secret+domain-hex in TLS mode and `dd`+secret in secure mode, as in `telemt/configure_container.sh`.
- **Traffic:** "n/a" unless the Telemt API (`127.0.0.1:9091`) reports per-user bytes. Probe it in the integration test; if it has no per-user bytes, `read_traffic` returns `{}`.

Steps:

- [ ] Unit tests, then integration: install, add a user, and check that `config.toml` contains the user and the container is running.
- [ ] Commit: `feat(panel): telemt driver`.

### Task 6: MTProxy driver

**Files:** `app/drivers/mtproxy.py` plus tests

**Behavior:**
- The secrets are the main `/data/secret` plus `MTPROXY_ADDITIONAL_SECRETS`, baked into `/opt/amnezia/start.sh`.
- The panel stores its own list in `/data/panel_secrets` and rewrites the `start.sh` line `MTPROXY_ADDITIONAL_SECRETS` value. The replaced text is the `for S in $(echo "<list>"` line.
- Then `docker restart`, only on change.
- The main secret is imported as an orphan and is never removed unless revoked.
- **Traffic:** `{}` (no per-secret stats).
- **Render:** a `tg://proxy` link, with the `dd`/`ee` prefix read from `/data/*` state (`mode=`, `domain=`).

Steps:

- [ ] Unit tests, then integration: install, add a secret, and check that the process arguments contain `-S <secret>`.
- [ ] Commit: `feat(panel): mtproxy driver`.

### Task 7: IKEv2 driver

**Files:** `app/drivers/ikev2.py` plus tests

**Behavior:**
- Client certificates are created in the NSS DB with `certutil`, as in `Ikev2Configurator` (`/opt/amnezia/ikev2/clients/<id>.p12`). The p12 and its password are stored as material.
- **Block and revoke:** `ipsec` certificate revocation with `crlutil`. Unblock re-creates the CRL without the serial.
- **Fallback:** if the integration test shows that libreswan does not re-read the CRL without a restart, the driver runs `ipsec whack --rereadcrls`. If that is unreliable too, block = delete the certificate and unblock = re-issue it; the ruling is recorded.
- **Render:** a `.p12` bundle plus the connection data in the vpn:// `ikev2` section, as `Ikev2Configurator` builds it.

Steps:

- [ ] Unit tests, then integration: install ipsec, create a client, check that `certutil -L` lists it, block it and check the CRL.
- [ ] Commit: `feat(panel): ikev2 driver`.

### Task 8: Web and docs for the new protocols

**Files:** `panel/web/src/components/ShareModal.tsx`, `panel/web/src/pages/admin/ServerPage.tsx`, i18n, `panel/README.md`, `AGENTS.md`

- [ ] Write the failing tests:
  - `ShareModal` without `vpn_key` shows the native link or config only (no QR of an empty key; the QR is made from the native link for tg:// and socks5://);
  - the install dialog lists every installable protocol that `GET /api/admin/servers/installable` returns (a new endpoint).
- [ ] Implement. Update the docs with the supported protocol table.
- [ ] Commit: `feat(panel): expose new protocols in web and docs`.
