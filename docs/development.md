# Development

## Prerequisites

| Tool | Notes |
|---|---|
| Docker + Compose | Dev infrastructure |
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | Backend workspace |
| Node 20+ and pnpm | Frontend |
| just (optional) | Task runner; Makefile is equivalent |
| Git | Branch `main` |

The Znuny reference tree may live at `znuny-6.5.22/` (gitignored). Do **not**
copy Znuny code into packages.

## Clone and layout

```
aurix/   # repository root (Tiqora monorepo)
  backend/          # Python package tiqora
  frontend/         # Vite React app
  docs/             # English documentation
  packages/         # api-client, znuny-addon (later)
  docker-compose.dev.yml
  docker-compose.example.yml
  Dockerfile
```

## Start infrastructure

```bash
docker compose -f docker-compose.dev.yml up -d
# or: make dev-up / just dev-up
```

Services:

| Service | Port | Credentials (dev only) |
|---|---|---|
| MariaDB 10.11 | 3306 | `tiqora` / `tiqora`, DB `tiqora` |
| PostgreSQL 16 | 5432 | `tiqora` / `tiqora`, DB `tiqora` |
| Redis 7 | 6379 | none |
| Meilisearch | 7700 | master key `tiqora-dev-master-key` |
| Mailpit UI / SMTP | 8025 / 1025 | none |

Both databases are started so you can switch `DATABASE_URL` for dual-stack work.

## Backend

```bash
cd backend
uv sync

export DATABASE_URL=postgresql+asyncpg://tiqora:tiqora@localhost:5432/tiqora
export REDIS_URL=redis://localhost:6379/0
export MEILI_URL=http://localhost:7700
export MEILI_MASTER_KEY=tiqora-dev-master-key

uv run uvicorn tiqora.api.app:create_app --factory --reload --host 0.0.0.0 --port 8000
```

Import check:

```bash
uv run python -c 'import tiqora; print(tiqora.__version__)'
```

### Package map

See module docstrings under `backend/src/tiqora/`. Stub packages (`znuny/`,
`domain/`, …) exist so imports and CI pass while Phase 0 logic is implemented.

### Alembic

```bash
cd backend
uv run alembic -c alembic.ini history
# migrations live in alembic/versions_tiqora/
# alembic/versions_owned/ is gated — do not enable without ownership flag
```

### Tests and lint

```bash
make test   # pytest
make lint   # ruff + mypy
make fmt    # auto-fix
```

CI runs the same tools plus MariaDB and Postgres service containers.

## Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Route stubs: `/agent`, `/portal`, `/admin`. i18n resources in
`src/i18n/locales/{en,de}.json`. Theme via `data-theme` on `<html>`.

## Environment files

Copy examples when they exist; never commit `.env`. Common variables are
documented in `tiqora.config.Settings` and [deployment.md](./deployment.md).

## Coding conventions

- **Language**: English for code, comments, commits, and docs.
- **No Znuny source** in the tree.
- **Domain owns writes** — no multi-table Znuny writes from routers or MCP tools
  directly.
- **Async**: prefer async I/O; avoid blocking the event loop in MCP tools
  (SSE starvation risk).
- **i18n**: user-visible strings go through keys from day one.

## Useful commands

| Command | Action |
|---|---|
| `make dev-up` / `just dev-up` | Start compose stack |
| `make dev-down` | Stop stack |
| `make api` | Run API with reload |
| `make worker` | Run worker process |
| `make mcp` | Run MCP process |
| `make compose-check` | Validate compose YAML |
| `make build` | Local Docker image `tiqora:local` |
