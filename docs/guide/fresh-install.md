# Fresh install (greenfield)

This runbook covers a **standalone Tiqora install on an empty database** — no
existing Znuny instance. If you already run Znuny 6.5 and want to co-exist or
migrate, use [znuny-to-tiqora.md](znuny-to-tiqora.md) and
[parallel-operation.md](../parallel-operation.md) instead.

## What `tiqora bootstrap` does

On an empty database, `tiqora bootstrap`:

1. Loads the **Znuny 6.5 base schema** in installer order
   (`schema` → `initial_insert` → `schema-post`). The SQL ships as package data
   under `tiqora/bootstrap/schema/` (GPL-3.0 upstream artefacts).
2. Runs the **tiqora Alembic chain** only (`versions_tiqora` → `tiqora_*` tables).
   Schema ownership stays off; no `versions_owned` migrations run.
3. Sets the **admin password** for `root@localhost` (or `--admin-login`) using
   the Znuny-compatible `BCRYPT:` hash scheme, and ensures that user is in the
   `admin` group with `rw`.
4. Optionally **seeds** fake customers/tickets (`--seed`) for local demos.

The command is **idempotent**: re-running on a populated DB skips the base
schema (with a warning), re-applies migrations, and updates the admin password.

## Prerequisites

- Docker and Docker Compose
- A copy of [`docker-compose.example.yml`](../../docker-compose.example.yml)
- Strong secrets for `POSTGRES_PASSWORD` / `MEILI_MASTER_KEY` / `TIQORA_SECRET_KEY`

## Steps

### 1. Start the stack with the bundled empty database

```bash
cp docker-compose.example.yml docker-compose.yml
# Edit secrets: POSTGRES_PASSWORD, MEILI_MASTER_KEY, TIQORA_SECRET_KEY, DATABASE_URL
docker compose up -d
```

The example file starts Postgres (or MariaDB if you switch the commented
blocks), Redis, Meilisearch, and the Tiqora API/worker/MCP images. The
bundled DB is **empty** — the API container will run `tiqora migrate upgrade`
on start (creating only `tiqora_*` tables), but the Znuny base tables and the
seeded `root@localhost` user appear only after bootstrap.

### 2. Bootstrap the database

```bash
docker compose run --rm tiqora-api \
  tiqora bootstrap \
  --admin-password 'choose-a-strong-password' \
  --seed
```

Useful flags:

| Flag | Purpose |
|---|---|
| `--database-url URL` | Override `DATABASE_URL` |
| `--admin-login LOGIN` | Default `root@localhost` |
| `--skip-schema` | Skip base-schema load (migrate + admin only) |
| `--seed` | Create fake customers/tickets after bootstrap |
| `--customers N` / `--tickets M` | Counts for `--seed` |
| `--seed-value S` | RNG seed for reproducible fake data |

Without Docker:

```bash
cd backend
export DATABASE_URL='postgresql+asyncpg://tiqora:…@localhost:5432/tiqora'
uv run tiqora bootstrap --admin-password '…' --seed
```

### 3. Open the app and sign in

- UI: `http://localhost:8000/` (or your reverse-proxy URL)
- Login: `root@localhost`
- Password: the value you passed to `--admin-password`

### 4. Production hardening (HTTPS / config)

- Terminate TLS at a reverse proxy (nginx, Caddy, Traefik). Do not expose
  Postgres/Redis/Meili ports publicly — the example file leaves them
  unpublished by default.
- Set `TIQORA_SESSION_COOKIE_SECURE=1` when serving over HTTPS.
- Rotate `TIQORA_SECRET_KEY` and Meili/DB passwords; never keep the
  `change-me` defaults.
- See [deploy/docker-compose.md](../deploy/docker-compose.md) for env vars,
  external DB, reverse proxy, and TLS notes.

## MariaDB instead of Postgres

Uncomment the `mariadb` service in the compose file, comment out `postgres`,
and set:

```yaml
DATABASE_URL: mysql+aiomysql://tiqora:YOUR_PASSWORD@mariadb:3306/tiqora
```

Then run the same `tiqora bootstrap` command.

## Related

| Path | When to use it |
|---|---|
| [parallel-operation.md](../parallel-operation.md) | Co-run with an existing Znuny DB |
| [znuny-to-tiqora.md](znuny-to-tiqora.md) | Staged migration from Znuny |
| [cutover.md](../cutover.md) | After parallel op: take schema ownership |
| [deploy/docker-compose.md](../deploy/docker-compose.md) | Full Compose reference |
