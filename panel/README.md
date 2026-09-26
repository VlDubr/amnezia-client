# Amnezia Panel

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

Stage 1 supports **AmneziaWG** (`amnezia-awg2`, legacy `amnezia-awg`) and **WireGuard** (`amnezia-wireguard`). The design is in `docs/superpowers/specs/2026-09-26-amnezia-panel-design.md`.

## Install

You need a Linux VPS with Docker and the compose plugin, plus a DNS name that points to it.

1. Get the code:

   ```bash
   git clone https://github.com/amnezia-vpn/amnezia-client.git
   cd amnezia-client/panel/deploy
   ```

2. Create the settings file:

   ```bash
   cp .env.example .env
   ```

   In `.env`, set `PANEL_DOMAIN`, `POSTGRES_PASSWORD` and `PANEL_TZ`, and generate the master key:

   ```bash
   echo "PANEL_MASTER_KEY=$(openssl rand -base64 32)" >> .env
   ```

   > **Warning:** `PANEL_MASTER_KEY` encrypts the SSH credentials of your servers and the private keys of all configs in the database. Store a copy outside the server. Without it, a database backup cannot be restored, and every user will need new configs.

3. Build the web UI once (it needs Node.js 20 or later; `panel/web` is part of stage 2):

   ```bash
   (cd ../web && npm ci && npm run build)
   ```

4. Start the stack:

   ```bash
   docker compose up -d --build
   ```

5. Create the first administrator:

   ```bash
   docker compose exec backend python -m app.cli create-admin --login admin
   ```

Open `https://<PANEL_DOMAIN>` and sign in as the administrator.

## Add a server

In the admin UI (or with `POST /api/admin/servers`), give the server's address, the SSH user and a password or private key.

The panel stores the server's host key on first contact. It then imports the AmneziaWG and WireGuard containers it finds, and the clients already on them. Imported clients appear under "Configs without owner" and keep working. Assign them to users when you are ready.

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
