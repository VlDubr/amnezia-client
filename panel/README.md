# Amnezia Panel

English | [Русский](README_RU.md)

A web panel for self-hosted Amnezia servers.

What administrators can do:

- Create users and give each of them an invite key.
- Group several VPN servers.
- Set an access period and a config limit for each user.
- Block and delete users and configs.
- See traffic per user and per server.

What users can do:

- Register with the invite key.
- Create, block, delete and re-download their configs in a web cabinet.

The panel runs on its own VPS. It manages VPN servers over SSH with the same scripts the AmneziaVPN desktop client uses (`client/server_scripts`), so it works with servers that were set up by the app.

The design is in `docs/superpowers/specs/2026-09-26-amnezia-panel-design.md`.

## Supported protocols

| Container | Install from panel | Block / unblock | Traffic | Config issued as |
|---|---|---|---|---|
| AmneziaWG (`amnezia-awg2`, legacy `amnezia-awg`) | yes (awg2) | peer removed / re-added | yes | `vpn://` key, `.conf`, QR |
| WireGuard (`amnezia-wireguard`) | yes | peer removed / re-added | yes | `vpn://` key, `.conf`, QR |
| XRay VLESS (`amnezia-xray`) | yes (REALITY) | client removed / re-added, Xray restarts | yes (stats API) | `vpn://` key, `vless://` link, QR |
| OpenVPN (`amnezia-openvpn`) | yes | `ccd` disable / enable; delete revokes into the CRL | yes (per session) | `vpn://` key, `.ovpn`, QR |
| SOCKS5 (`amnezia-socks5proxy`) | yes (auth always on) | user line removed / re-added | yes (from the 3proxy log) | `socks5://` link, QR |
| Telemt (`amnezia-telemt`) | yes | user removed / re-added | not available | `tg://proxy` link, QR |
| MTProxy (`amnezia-mtproxy`) | yes | additional secret removed / re-added (the admin's main secret is not managed) | not available | `tg://proxy` link, QR |
| IKEv2 (`amnezia-ipsec`) | no, install it from the AmneziaVPN app | serial added to / removed from the CA's CRL; delete also removes the certificate | not available | `vpn://` key, `.mobileconfig` |

Changing the client list of Xray, SOCKS5, Telemt and MTProxy restarts that container, which drops its connections for a moment.

OpenVPN over Cloak or ShadowSocks and ShadowSocks over Xray are not supported: the current AmneziaVPN sources no longer contain server scripts for them.

Imported clients (created outside the panel) can be issued again only when the server holds everything the client needs: Xray, SOCKS5, Telemt and MTProxy clients can be; WireGuard, AmneziaWG, OpenVPN and IKEv2 clients cannot, because their private keys stayed on the client device.

## Install

You need a Linux VPS with Docker and the compose plugin, plus a DNS name that points to it.

1. Get the code:

   ```bash
   git clone https://github.com/VlDubr/amnezia-client.git
   cd amnezia-client/panel/deploy
   ```

2. Create the settings file:

   ```bash
   cp .env.example .env
   ```

   In `.env`, set `PANEL_DOMAIN`, `POSTGRES_PASSWORD` and `PANEL_TZ`. Generate the master key and put it into the `PANEL_MASTER_KEY=` line:

   ```bash
   openssl rand -base64 32
   ```

   > **Warning:** `PANEL_MASTER_KEY` encrypts the SSH credentials of your servers and the private keys of all configs in the database. Store a copy outside the server. Without it, a database backup cannot be restored, and every user will need new configs.

3. Start the stack (the Caddy image builds the web UI):

   ```bash
   docker compose up -d --build
   ```

4. Create the first administrator:

   ```bash
   docker compose exec backend python -m app.cli create-admin --login admin
   ```

Open `https://<PANEL_DOMAIN>/admin/login` and sign in as the administrator. The page at `https://<PANEL_DOMAIN>/` is the sign-in for users; it does not accept administrator accounts.

## Add a server

In the admin UI (or with `POST /api/admin/servers`), give the server's address, the SSH user and a password or private key.

The panel stores the server's host key on first contact. It then imports every supported container it finds (see the table above) and the clients already on them. Imported clients appear under "Configs without owner" and keep working. Assign them to users when you are ready.

The SSH user needs `sudo` without a password, as for the desktop app.

If a server's host key changes (for example, after a reinstall), the panel stops working with that server until an administrator accepts the new key.

## Operations

- **Logs:** `docker compose logs -f backend`.
- **Backups:** a `pg_dump` runs every day into the `backups` volume and is kept for `BACKUP_KEEP_DAYS` days. To restore:

  ```bash
  gunzip -c panel-XXXX.sql.gz | docker compose exec -T db psql -U panel panel
  ```

  Use the same `PANEL_MASTER_KEY` as before.
- **Update:**

  ```bash
  git pull
  docker compose up -d --build
  ```

  Migrations run automatically on start.
- **If the panel goes down**, VPN servers keep working. Only the web UI, config issuing and automatic expiry pause, and pending work catches up after a restart.

## Managing users from the AmneziaVPN app

Open Settings → **Amnezia Panel** in the AmneziaVPN app and sign in with the panel address and an administrator login.

From there you can do the same user work as in the web admin area:

- list and create users (the invite key is shown once);
- set the config limit and the last day of access;
- block, unblock or delete a user, or issue a new invite key;
- issue configs on any server and show, block or delete them;
- see the traffic per server.

Servers are added and protocols are installed in the web admin area only.

## API

The REST API lives under `/api`. Its OpenAPI description is served at `/api/openapi.json`, and interactive docs are at `/api/docs`.

Clients authenticate with `POST /api/auth/login`:

- Browsers get an HttpOnly session cookie plus a CSRF cookie. Requests that change data must send the CSRF cookie back in the `X-CSRF-Token` header.
- Other clients use the returned token as `Authorization: Bearer <token>`.

## Development

Backend (`panel/backend`, Python 3.12+, [uv](https://docs.astral.sh/uv/)):

```bash
cd panel/backend
uv sync
uv run pytest                 # unit + API tests; starts PostgreSQL in Docker via testcontainers
uv run pytest -m integration  # real AmneziaWG/WireGuard containers driven over SSH; needs Docker
```

To reuse an existing database instead of testcontainers, set `PANEL_TEST_DATABASE_URL=postgresql+asyncpg://...`.

Web UI (`panel/web`, Node.js 20+, React + Mantine):

```bash
cd panel/web
npm ci
npm run dev        # http://localhost:5173, proxies /api to a backend on :8000 (override with PANEL_API)
npm test           # component tests (Vitest + Testing Library + MSW)
npx playwright install chromium
npm run e2e        # browser tests: real backend with an in-memory VPN server + the production build
```

The user cabinet is at `/`; the administrator area is at `/admin` (sign in at `/admin/login`).

### Try it locally without VPN servers

The e2e launcher runs the real backend with an in-memory AmneziaWG server in place of every VPN server, so any address and password work when you add a server. Docker must be running.

```bash
cd panel/backend
uv run python -m tests.e2e_server      # API on http://127.0.0.1:8765, a temporary database
```

It creates the administrator `admin` with the password `Adm1n-Pa55w0rd!xyz`. The data is lost when it stops.

In a second terminal:

```bash
cd panel/web
npm ci
PANEL_API=http://127.0.0.1:8765 npm run dev   # PowerShell: $env:PANEL_API="http://127.0.0.1:8765"; npm run dev
```

Open `http://localhost:5173/admin/login`. In the AmneziaVPN app, use the panel address `http://127.0.0.1:8765` (plain http is accepted only for this machine).
