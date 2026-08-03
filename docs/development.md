# Development

## Prerequisites

| Tool | Notes |
|---|---|
| Docker + Compose | Dev infrastructure |
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | Backend workspace |
| Node 20+ and pnpm | Frontend |
| just (optional) | Task runner; Makefile is equivalent |
| Git | Branch `main` |

Peer release trees for golden Layer B may live at the repo root as
`znuny-6.5.22/`, `znuny-7.3.5/`, … (gitignored). Multi-version support:
[support-matrix.md](support-matrix.md). Do **not**
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

`docker-compose.dev.yml` starts infrastructure only — you run the Tiqora
processes on the host (below) for fast hot-reload.

## Full stack in containers (no host toolchain)

When you just want the whole thing running — e.g. to test a change end-to-end
without pushing anywhere — use the full-stack compose. It builds the app image
(backend + SPA) from your checkout, brings up MariaDB/Redis/Meilisearch/Mailpit,
then a one-shot `init` service loads the Znuny base schema, runs the tiqora
migrations, sets the admin password, and seeds fake customers/tickets:

```bash
docker compose -f docker-compose.local.yml up --build -d
# open http://localhost:8000   (login: root@localhost / admin)
```

| Thing | Where |
|---|---|
| Web UI + API | http://localhost:8000 |
| MCP server | http://localhost:8001 |
| Mailpit (caught mail) | http://localhost:8025 |
| Admin login | `root@localhost` / `admin` |

Apply code changes by re-running `up --build -d`. To wipe the DB and re-seed
from scratch: `docker compose -f docker-compose.local.yml down -v` then bring it
up again. This uses MariaDB to match production; the seed data are real database
rows (via `tiqora bootstrap --seed`), **not** the frontend MSW mocks used for
screenshots/the public demo.

### Hot-reload in the container stack

To iterate without rebuilding the image, layer the override on top:

```bash
docker compose -f docker-compose.local.yml -f docker-compose.local.override.yml up -d
# Frontend (Vite HMR): http://localhost:5173   ← edit frontend/src → instant
# Backend (--reload):  http://localhost:8000    ← edit backend/src → auto-restart
```

It bind-mounts `backend/src` and runs the API under `uvicorn --reload`, and adds a
Node/Vite dev container that proxies `/api` to the compose backend. Do frontend work
against **:5173** (HMR); **:8000** still serves the built SPA. Workers/MCP get the mounted
source too, so `restart tiqora-worker tiqora-ai-worker tiqora-mcp` picks up backend changes
without an image rebuild.

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

See module docstrings under `backend/src/tiqora/`. Layout:

```
backend/src/tiqora/
  api/ domain/ permissions/ znuny/ db/ worker/ kb/ channels/ …
```

### Alembic

```bash
cd backend
uv run alembic -c alembic.ini history
# migrations live in alembic/versions_tiqora/
# alembic/versions_owned/ is gated — do not enable without ownership flag
```

### Tests and lint

```bash
make test   # pytest (unit always; db-marked tests when Docker is up)
make test-unit
make test-db   # schema conformance + permission engine (testcontainers)
make lint   # ruff + mypy
make fmt    # auto-fix
```

- **Unit tests** (`not db`): password hashes, SysConfig YAML resolution, config/health.
- **DB tests** (`pytest -m db`): start MariaDB 10.11 and Postgres 16 via testcontainers,
  load Znuny 6.5 DDL from `backend/tests/fixtures/znuny-schema/`, assert legacy models
  match the real schema, and exercise the permission engine.

If Docker is not running, db-marked tests are skipped automatically. CI runs the same
tools with MariaDB and Postgres service containers (or testcontainers where configured).

### Dev database tools: `tiqora dev seed` / `tiqora dev anonymize`

Two CLI subcommands under `tiqora dev` support local development and
"debug a copy of prod" workflows. Both live in
`backend/src/tiqora/cli/dev.py` and depend on
[Faker](https://faker.readthedocs.io/), which ships in the backend `dev`
dependency group (`uv sync --all-extras` or `uv sync --group dev`) — running
the CLI without it prints a clear error instead of an import traceback.

#### `tiqora dev seed` — fake customers, tickets, and articles

```bash
cd backend
uv run tiqora dev seed --customers 10 --tickets 20 --seed 42
```

- Creates *N* customer companies (1–3 customer users each) via direct
  `customer_company`/`customer_user` inserts.
- Creates *M* tickets (1–4 articles each), distributed across the queues,
  states, and priorities that already exist in the target database — it
  fails with a clear error if the schema has no queues/states/priorities
  (assumes an initialized Znuny install), or if the acting agent
  (`--agent-user-id`, default `1` / `root@localhost`) has no `create`
  permission on any queue's group.
- Tickets and articles are written through
  `TicketWriteService.create_ticket` / `add_article` — the same path the
  API uses — so history rows, escalation columns, cache invalidation, and
  outbox events are all created exactly as they would be via a real request.
- `--seed` makes the *generated content* (names, titles, bodies, which
  queue/state/priority each ticket gets) reproducible: it seeds both
  Faker's instance RNG and the stdlib `random` module. Primary-key-facing
  identifiers (`customer_id`, login) still carry a random per-run nonce, so
  repeated invocations against the same database never collide on
  uniqueness constraints — **except** that re-running with the *exact same*
  `--seed` against the *same* database can still hit real unique
  constraints such as `customer_company.name` (identical Faker sequence ->
  identical company name). Use a fresh dev database, or vary `--seed`,
  for repeated runs.
- `--database-url` overrides the configured `DATABASE_URL` for one-off runs
  against a different (e.g. throwaway) database.

#### `tiqora dev anonymize` — scrub PII in a restored dump copy

```bash
cd backend
uv run tiqora dev anonymize --database-url mysql+aiomysql://user:pass@host/dbname --seed 1
```

`--database-url` is **required** — this command never falls back to the
configured `DATABASE_URL`, precisely so it can't accidentally be pointed at
a live/production database. It is meant to run against a **restored dump
copy** (e.g. after `mysqldump`/`pg_dump` + restore to a scratch instance).

It performs bulk, direct SQL updates (no history/outbox invariants — there
is no live Znuny process to keep consistent once you're scrubbing a dump):

- `customer_user`: first/last name, email, login.
- `customer_company`: company name.
- `users` (agents): first/last name only — **logins are kept intact** so a
  restored/anonymized dump stays correlatable against the live system for
  debugging (auth wiring, escalation ownership, audit trails all key off
  login).
- `article_data_mime`: email addresses found in `a_from`/`a_to`/`a_cc` are
  replaced in place (the rest of the header string is left alone); `a_body`
  is replaced with lorem text that preserves the original's line count and
  roughly the per-line length.

Referential consistency: the same original value always maps to the same
replacement everywhere it occurs (e.g. a customer's email in
`customer_user.email` and inside an article's `a_from`/`a_to`). This is a
pure function of `(--seed, value kind, original value)` — see
`ValueMapper` in `backend/src/tiqora/domain/dev_anonymize.py` — so the same
`--seed` also reproduces the same anonymized output across runs, which is
useful for diffing or testing. Updates are batched (`--batch-size`,
default 500) with progress output and a final summary of rows updated per
table.

## Frontend

```bash
cd frontend
pnpm install
pnpm dev
```

Route stubs: `/agent`, `/portal`, `/admin`. i18n: registry in
`src/i18n/locales.ts` (full Znuny set; 15 shipped UI languages). Locales under
`src/i18n/locales/*.json`. Commands: `pnpm i18n:check`, `pnpm i18n:scaffold`,
`pnpm i18n:translate`. Theme via `data-theme` on `<html>`; UI language sets
`lang` + `dir` and persists to Znuny `UserLanguage` when logged in.

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
- **i18n**: user-visible strings go through keys from day one. Add keys to
  `en.json` and `de.json` together; run `pnpm i18n:check`. Prefer
  `toBcp47(i18n.language)` for `Intl` formatting and `setAppLanguage()` for
  switches (not ad-hoc `localStorage` / `startsWith("de")`).

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

## Release checklist

Day-to-day pushes run **CI** and an **amd64 Docker** image. Marketing site,
multi-arch Docker, and Playwright e2e run on **`v*` tags** (or manual
workflow dispatch where available). Before tagging a release:

1. **Screenshots** (if UI changed meaningfully):
   ```bash
   cd frontend && pnpm build
   SCREENSHOTS=1 pnpm exec playwright test screenshots --project=chromium
   ```
   **AI audit sample PDF** (`site/ai-audit-sample.pdf`, if `buildAuditPdfHtml`
   in `frontend/src/routes/admin/auditPdf.ts` changed):
   ```bash
   cd frontend && AUDIT_SAMPLE=1 pnpm exec playwright test e2e/audit-sample.spec.ts --project=chromium
   ```
2. **OpenAPI + typed client** (if REST models/routes changed):
   ```bash
   cd backend && uv run tiqora openapi -o ../docs/api/openapi.json
   # also refresh packages/api-client/openapi.json and regenerate types
   pnpm --filter @tiqora/api-client generate && pnpm --filter @tiqora/api-client build
   ```
3. **Tag** `vX.Y.Z` → CI (with e2e), multi-arch Docker push, product site + demo deploy.
4. **Optional:** run golden-master via Actions (`workflow_dispatch`) if ticket-write
   invariants or the GenericInterface layer changed.
5. **Optional:** deploy the product site without a tag via
   **Actions → Product site (GitHub Pages) → Run workflow**.
