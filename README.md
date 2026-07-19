# Tiqora

> **⚠️ Under active development — not production ready.**
>
> APIs, schema conventions, and operational behaviour may change without notice.
> Do not run Tiqora against production Znuny databases until Phase 2 write-path
> golden-master tests have been completed and documented.

**Tiqora** is a modern, self-hosted ticket / helpdesk system that is **database-compatible
with Znuny / OTRS 6.5**. It is a clean-room reimplementation (Python FastAPI + React),
not a fork of Znuny — no Znuny source code is included or redistributed.

| | |
|---|---|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2 |
| **Frontend** | React + TypeScript + Vite, Tailwind, theming via CSS variables |
| **Search** | Meilisearch (hybrid / vector RAG planned) |
| **Jobs** | taskiq on Redis |
| **AI surface** | MCP server (FastMCP) with full agent access under the same permission engine |
| **License** | [AGPL-3.0](./LICENSE) — Copyright © 2026 Cygnus Networks GmbH |

## What Tiqora is

Tiqora targets organisations that run (or want to leave) Znuny/OTRS 6.5 and need:

1. A modern agent UI, customer portal, and admin console.
2. A first-class REST API (`/api/v1`) and an MCP server for AI agents.
3. **Parallel operation** with an existing Znuny instance on the **same database**
   (PostgreSQL or MySQL/MariaDB), with zero schema changes to Znuny tables until
   an explicit post-cutover “schema ownership” mode is enabled.
4. A path to take over mail/escalation/notification/GenericAgent work from the
   Znuny daemon, feature-flag by feature-flag.

Tiqora only adds new tables under the `tiqora_*` prefix. Znuny keeps owning its
existing schema during parallel operation.

## Key features

| Area | Status | Notes |
|---|---|---|
| Project scaffolding, CI, Docker images | ✅ Scaffolded (Phase 0) | This repository state |
| Dev stack (MariaDB, Postgres, Redis, Meili, Mailpit) | ✅ Scaffolded | `docker-compose.dev.yml` |
| Config, async DB engine, health/ready/metrics | ✅ Minimal | `backend/src/tiqora` |
| Znuny legacy models + schema conformance tests | 🔲 Planned (Phase 0) | ~45 V1 tables |
| Auth: legacy password hashes, Redis sessions, API keys | 🔲 Planned (Phase 0) | bcrypt / sha256 / md5-crypt |
| Permission engine (groups, roles, ACL) | 🔲 Planned (Phase 0) | Shared by UI / REST / MCP |
| Read-only agent UI + Meilisearch index | 🔲 Planned (Phase 1) | Safe parallel entry |
| TicketService write path + Znuny invariants | 🔲 Planned (Phase 2) | Highest risk phase |
| GenericInterface compatibility layer | 🔲 Planned (Phase 2) | TicketCreate/Update/Get/Search, SessionCreate |
| MCP server tools | 🔲 Planned (Phase 2) | ticket_*, customer_lookup, kb_* |
| Customer portal | ✅ API + UI (Phase 3a/3b) | REST at `/api/portal/*`, UI at `/portal` |
| Knowledge base (`tiqora_kb_*`, RAG-ready) | ✅ API + UI (Phase 3a/3b) | Markdown, chunking, Meilisearch; agent editor + portal search UI |
| Admin CRUD (queues, DF, ACL, GenericAgent) | ✅ API + UI (Phase 3a/3b) | REST at `/api/v1/admin/*`, UI at `/admin` |
| OIDC, Kerberos/SPNEGO, TOTP | 🔲 Planned (Phase 3) | |
| Daemon takeover (mail, escalation, notify, GA) | 🔲 Planned (Phase 4) | Per-function feature flags |
| Schema ownership + AI webhooks | 🔲 Planned (Phase 5) | Cutover runbook |
| Process management, calendar, stats, PGP/S-MIME | ⏸ Deferred | Not in V1 |
| SOAP, package manager | ⏸ Deferred | Not in V1 |
| Phone/SMS, WhatsApp Business channels | 🔲 Planned (post-V1) | Plugin architecture |

## Architecture overview

```
                    ┌──────────────────────────────────────────┐
                    │              Clients                      │
                    │  Agent UI · Portal · Admin · AI agents    │
                    └───────────┬──────────────┬────────────────┘
                                │              │
                     /api/v1    │              │  MCP (SSE)
                     /compat/*  │              │
                                ▼              ▼
                    ┌────────────────┐  ┌─────────────┐
                    │  tiqora-api    │  │ tiqora-mcp  │
                    │  (FastAPI)     │  │ (FastMCP)   │
                    └───────┬────────┘  └──────┬──────┘
                            │                  │
                            │   domain/*       │
                            │   permissions/*  │
                            ▼                  ▼
              ┌─────────────────────────────────────────────┐
              │              Shared domain layer             │
              │  TicketService · ACL · sessions · outbox     │
              └───────────┬───────────────────┬─────────────┘
                          │                   │
           ┌──────────────▼──────┐   ┌────────▼────────┐
           │  Znuny 6.5 tables   │   │  tiqora_* tables│
           │  (read/write, no    │   │  (Alembic chain │
           │   schema changes)   │   │   versions_tiqora)│
           └──────────┬──────────┘   └────────┬────────┘
                      │                       │
         ┌────────────▼──────────┐            │
         │  Znuny instance       │            │
         │  (parallel operation) │◄── cache invalidation via TiqoraSync OPM
         └───────────────────────┘
                      │
         ┌────────────▼────────────────────────────────────┐
         │  tiqora-worker (taskiq) · Redis · Meilisearch   │
         └─────────────────────────────────────────────────┘
```

```mermaid
flowchart TB
  subgraph clients [Clients]
    AgentUI[Agent UI]
    Portal[Customer Portal]
    Admin[Admin]
    AI[AI Agents via MCP]
  end

  subgraph tiqora [Tiqora]
    API[tiqora-api FastAPI]
    MCP[tiqora-mcp FastMCP]
    Worker[tiqora-worker taskiq]
    Domain[domain + permissions]
  end

  subgraph data [Shared data plane]
    ZTables[(Znuny tables)]
    TTables[(tiqora_* tables)]
    Redis[(Redis)]
    Meili[(Meilisearch)]
  end

  Znuny[Znuny 6.5 instance]

  AgentUI --> API
  Portal --> API
  Admin --> API
  AI --> MCP
  API --> Domain
  MCP --> Domain
  Domain --> ZTables
  Domain --> TTables
  Worker --> ZTables
  Worker --> TTables
  Worker --> Redis
  Worker --> Meili
  Znuny --> ZTables
  Domain -.->|tiqora_cache_invalidation| Znuny
```

Package layout (backend):

```
backend/src/tiqora/
  db/legacy/        # Hand-written models for Znuny tables + conformance tests
  db/tiqora/        # tiqora_* models (Alembic: versions_tiqora / versions_owned)
  znuny/            # Invariants: ticket numbers, history, escalation, follow-up, …
  domain/           # Services — sole write paths, bundling invariants
  permissions/      # Groups/roles + ACL for UI, REST, MCP
  events/           # Async bus + transactional outbox
  channels/         # Channel plugin protocol (email, web, …)
  storage/          # StorageBackend interface (DB MIME in V1)
  api/              # v1 routers + GenericInterface compat layer
  mcp_server/       # FastMCP process
  worker/           # taskiq jobs
  kb/               # Knowledge base
```

## Parallel operation with Znuny

Tiqora and Znuny can share **one** PostgreSQL or MariaDB/MySQL database:

| Rule | Detail |
|---|---|
| No Znuny schema changes | Tiqora never alters Znuny tables until post-cutover ownership mode |
| New tables only as `tiqora_*` | Alembic chain `versions_tiqora/` |
| Behavioural parity | Ticket numbers, history formats, escalation columns, search flags must match Znuny |
| Daemon ownership | Znuny keeps mail/escalation/notifications/GenericAgent until feature flags hand each over |
| Cache coherence | Optional `TiqoraSync` Znuny OPM reads `tiqora_cache_invalidation`; or lower Znuny cache TTLs |

See [docs/parallel-operation.md](./docs/parallel-operation.md) for the full invariant list.

## Quick start (development)

### Prerequisites

- Docker / Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python)
- Node 20+ and [pnpm](https://pnpm.io/) (frontend)
- Optional: [just](https://github.com/casey/just)

### 1. Start infrastructure

```bash
docker compose -f docker-compose.dev.yml up -d
# MariaDB :3306, Postgres :5432, Redis :6379, Meilisearch :7700, Mailpit :8025/:1025
```

### 2. Backend

```bash
cd backend
uv sync
export DATABASE_URL=postgresql+asyncpg://tiqora:tiqora@localhost:5432/tiqora
# or: mysql+aiomysql://tiqora:tiqora@localhost:3306/tiqora
export REDIS_URL=redis://localhost:6379/0
export MEILI_URL=http://localhost:7700
uv run uvicorn tiqora.api.app:create_app --factory --reload --host 0.0.0.0 --port 8000
```

Health checks:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/ready
curl -s http://localhost:8000/metrics | head
```

### 3. Frontend

```bash
cd frontend
pnpm install
pnpm dev
# http://localhost:5173  — agent, portal, and admin UI
```

### Makefile / just shortcuts

```bash
make dev-up    # or: just dev-up
make sync
make api
make test
make lint
```

## Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| API | FastAPI + Pydantic v2 | Async-native, OpenAPI-first |
| ORM | SQLAlchemy 2 async | Dual drivers: asyncpg + aiomysql |
| Migrations | Alembic (two chains) | Own tables now; owned Znuny schema only after cutover |
| Jobs | taskiq + Redis | Asyncio-native, FastAPI-like DI, cron for daemon takeover |
| Search | Meilisearch | Fast full-text; later hybrid/vector for RAG |
| Sessions | Redis server-side | No JWT; Znuny-compatible session table for compat API |
| Frontend | Vite, React, TS, Tailwind | One app, three route trees, code-split |
| i18n | react-i18next | EN + DE from day one |
| Observability | structlog JSON, Prometheus `/metrics` | Zabbix template planned under `deploy/zabbix/` |
| MCP | FastMCP (separate process) | Same permission engine as UI/REST |

## Project status / roadmap

Summarised from the design plan. Durations are indicative.

| Phase | Focus | Exit criteria (summary) |
|---|---|---|
| **0 — Foundation** (2–3 w) | Scaffolding, legacy models, auth, permissions, CI matrix | Login against a real Znuny dump with all hash schemes |
| **1 — Read-only agent UI** (3–4 w) | REST reads, Meili bulk index, Znuny write poller, queue/zoom/search | Side-by-side diff vs Znuny zoom; Playwright smoke |
| **2 — Write path + compat + MCP** (5–6 w) | TicketService, invariants, TiqoraSync, SMTP, compat API, MCP | Golden-master API/DB diffs; TN concurrency with mixed writers |
| **3 — Portal + KB + Admin** (4–5 w) | Portal, KB, admin CRUD, OIDC/Kerberos/TOTP | Queue created in Tiqora appears in Znuny (cache path proven) |
| **4 — Daemon takeover** (4–6 w) | Postmaster, escalation, notifications, GenericAgent, auto-responses | Mail round-trip via Mailpit; notification diffs |
| **5 — Cutover + ownership + AI** (2–3 w) | Runbook, ownership flag, additive FKs/indexes, webhooks | Regression on production clone; rollback drill |

Detailed design: [docs/specs/2026-07-19-tiqora-design.md](./docs/specs/2026-07-19-tiqora-design.md).

## Documentation

| Document | Content |
|---|---|
| [docs/architecture.md](./docs/architecture.md) | System components and data flow |
| [docs/parallel-operation.md](./docs/parallel-operation.md) | Znuny invariants and coexistence rules |
| [docs/compatibility.md](./docs/compatibility.md) | GenericInterface compatibility layer |
| [docs/deployment.md](./docs/deployment.md) | Production-oriented deployment notes |
| [docs/development.md](./docs/development.md) | Local development workflow |
| [docs/specs/2026-07-19-tiqora-design.md](./docs/specs/2026-07-19-tiqora-design.md) | Full design specification |

## Compatibility statement

- **Target**: Znuny / OTRS **6.5** database schema (MariaDB/MySQL and PostgreSQL).
- **Behaviour**: Ticket numbering, history name formats, escalation columns, and
  search-index flags must remain readable and writable by a co-running Znuny 6.5
  instance during parallel operation.
- **Code**: Tiqora is an independent implementation. The Znuny reference tree
  (`znuny-6.5.22/`, tarball) is **gitignored** and never copied into this repository.

## Contributing

1. Open an issue or discuss the change before large design work.
2. Keep all documentation, user-facing strings (via i18n keys), and code comments in **English**.
3. Do **not** copy any Znuny/OTRS source into the tree (AGPL cleanliness for Znuny; Tiqora is AGPL-3.0 of its own).
4. Run `make lint` and `make test` before opening a PR.
5. Prefer small, reviewable PRs aligned with the phase roadmap.

## License

Tiqora is licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0).
See [LICENSE](./LICENSE).

Copyright © 2026 Cygnus Networks GmbH.

Znuny is a trademark of its respective owners. Tiqora is not affiliated with or
endorsed by the Znuny project.
