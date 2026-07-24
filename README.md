# Tiqora

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](./LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend)
[![React + TypeScript](https://img.shields.io/badge/React-TypeScript-61DAFB?logo=react&logoColor=black)](./frontend)
[![Znuny 6.5 DB](https://img.shields.io/badge/Znuny%2FOTRS-6.5%20DB%20compatible-5B8CFF)](./docs/compatibility.md)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](./docs/deploy/docker-compose.md)
[![Live site](https://img.shields.io/badge/Live-demo%20%26%20product%20site-5B8CFF)](https://cygnusnetworks.github.io/tiqora/)

> **Still under active development.** Production use is not yet recommended.
> APIs, schema conventions, and operational behaviour may still change.

**Tiqora** is a modern, self-hosted ticket / helpdesk system that is **database-compatible
with Znuny / OTRS 6.5**. It is a clean-room reimplementation (Python FastAPI + React),
not a fork of Znuny — no Znuny source code is included or redistributed.

| | |
|---|---|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2 |
| **Frontend** | React + TypeScript + Vite, Tailwind, theming via CSS variables |
| **Search** | Meilisearch (full-text; hybrid / vector RAG planned later) |
| **Jobs** | taskiq on Redis |
| **AI surface** | MCP server (FastMCP) under the same permission engine as UI/REST |
| **License** | [AGPL-3.0](./LICENSE) — Copyright © 2026 Cygnus Networks GmbH |

## Why Tiqora

- **Modern web UI** — agent workspace, admin console, and customer portal that feel
  like a current product, not a 2000s helpdesk skin.
- **Znuny / OTRS 6.5 database compatibility** — same core tables; **parallel operation**
  on one shared database is a first-class path (additive `tiqora_*` tables only until
  you explicitly take schema ownership).
- **AI-ready ticket search** — Meilisearch indexing plus an **MCP server** so AI agents
  act with the same ACLs as humans.
- **AI agent assistance** — per-queue policies drive **draft replies**, **state-only
  ticket summaries** (document- and attachment-aware), and an optional **autonomous
  auto-reply** worker. Attachments get text extraction plus a **vision pre-pass** for
  images; sensitive data is **PII-masked (spaCy NER)** before any LLM call; every
  request lands in an **audit log** with per-subject ACLs and token/request limits.
  Bring your own OpenAI-compatible or Anthropic providers. Gated by the operation mode
  so nothing autonomous runs during parallel operation.
- **GDPR tooling** — anonymization, retention jobs, and audit trails in admin.
- **Modern design** — dark/light themes, EN + DE i18n, compact cobalt design system.
- **No Perl application stack** — Python FastAPI + React throughout Tiqora itself
  (optional small Znuny OPM addon only if you co-run Znuny for cache coherence).
- **Customer portal & knowledge base** — self-service tickets and Markdown KB.
- **Integration-friendly** — REST `/api/v1`, GenericInterface REST/SOAP compatibility,
  webhooks, channel plugins (email, SMS, WhatsApp, phone/CTI).
- **Modern auth** — legacy password hashes, OIDC, LDAP/AD, Kerberos/SPNEGO (with
  **seamless re-auth** when a session expires), enforceable TOTP, and passkeys.

## Live product site & demo

**[Product site](https://cygnusnetworks.github.io/tiqora/)** — overview, features, screenshots.

**[Interactive demo](https://cygnusnetworks.github.io/tiqora/demo/)** — full agent, admin, and
portal UI in the browser against mock data (nothing is saved). Built from `frontend/`
with [Mock Service Worker](https://mswjs.io/); local build:

```bash
VITE_BASE=/tiqora/demo/ pnpm --filter tiqora-frontend build:demo
```

## Screenshots

| Agent dashboard | Ticket zoom |
|---|---|
| ![Agent dashboard](./docs/images/agent-dashboard.png) | ![Ticket zoom](./docs/images/agent-ticket-zoom.png) |

| Queue view | Search |
|---|---|
| ![Queue view](./docs/images/agent-queues.png) | ![Search](./docs/images/agent-search.png) |

| Reporting & SLA | Knowledge base |
|---|---|
| ![Reporting](./docs/images/agent-stats.png) | ![Knowledge base](./docs/images/agent-kb.png) |

| Admin — queues | Privacy / GDPR |
|---|---|
| ![Admin queues](./docs/images/admin-queues.png) | ![GDPR](./docs/images/admin-gdpr.png) |

| Authentication / 2FA | Customer portal |
|---|---|
| ![2FA administration](./docs/images/admin-2fa.png) | ![Customer portal](./docs/images/portal.png) |

<sub>Generated with `SCREENSHOTS=1 pnpm exec playwright test screenshots`
(`e2e/fixtures/rich-mock.ts`) — no backend required. Use `THEME=dark` / `LANG_UI=de` for variants.</sub>

## Also included

| Area | Notes |
|---|---|
| Ticket write path + Znuny invariants | Golden-master tested against Znuny 6.5 behaviour |
| GenericInterface compatibility | TicketCreate/Update/Get/Search, SessionCreate; REST + SOAP |
| MCP tools | `ticket_*`, customer lookup, KB — see [docs/ai-integration.md](./docs/ai-integration.md) |
| AI assistance subsystem | Draft replies, summaries, auto-reply worker, attachment/vision, PII masking, per-subject ACL & audit — `/admin/ai/*`, [docs/ai-integration.md](./docs/ai-integration.md) |
| Daemon takeover (mail, escalation, notify, GA) | Per-function flags, off by default |
| Calendar / appointments | Month/week/agenda UI; reuses Znuny `calendar*` tables |
| Process management (BPM) | Reuses Znuny `pm_*` tables — [docs/process-management.md](./docs/process-management.md) |
| PGP / S-MIME | Flag-gated — [docs/crypto.md](./docs/crypto.md) |
| SSE realtime + agent presence | Live updates on ticket zoom |
| CSV ticket export | Permission-filtered streaming export |
| TiqoraSync Znuny addon | Optional OPM for cache coherence during parallel op |

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
         │  (optional parallel)  │◄── cache invalidation via TiqoraSync OPM
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

## Getting started — three ways to run

| Path | When | Doc |
|---|---|---|
| **Fresh standalone** | Empty database, no Znuny — greenfield install via `tiqora bootstrap` | [docs/guide/fresh-install.md](./docs/guide/fresh-install.md) |
| **Parallel to Znuny** | Co-run with an existing Znuny 6.5 database (same schema, additive `tiqora_*` only) | [docs/parallel-operation.md](./docs/parallel-operation.md), [docs/guide/znuny-to-tiqora.md](./docs/guide/znuny-to-tiqora.md) |
| **Migrate away** | After parallel operation: schema ownership, cutover checklist | [docs/cutover.md](./docs/cutover.md) |

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
| i18n | react-i18next | EN + DE |
| Observability | structlog JSON, Prometheus `/metrics` | Zabbix template placeholder under `deploy/zabbix/` |
| MCP | FastMCP (separate process) | Same permission engine as UI/REST |

## Status

Core functionality is implemented and covered by automated tests, including
golden-master checks against Znuny 6.5 behaviour. The product is still under
active development: production cutover against a live Znuny estate has not been
performed with this codebase, and APIs may still change. Schema ownership defaults
**off** and requires an explicit operator action — see [docs/cutover.md](./docs/cutover.md).

## Documentation

Full index: **[docs/README.md](./docs/README.md)**

**Getting started & operating**

| Document | Content |
|---|---|
| [docs/guide/znuny-to-tiqora.md](./docs/guide/znuny-to-tiqora.md) | Operator playbook: run alongside Znuny, then migrate onto Tiqora |
| [docs/deploy/docker-compose.md](./docs/deploy/docker-compose.md) | Docker Compose deployment — services, env vars, reverse proxy |
| [docs/deployment.md](./docs/deployment.md) · [docs/parallel-operation.md](./docs/parallel-operation.md) · [docs/cutover.md](./docs/cutover.md) | Deployment notes, parallel-operation invariants, cutover runbook |
| [docs/development.md](./docs/development.md) · [docs/testing.md](./docs/testing.md) | Local dev, seeding/anonymizing, test suites |

**API & integrations**

| Document | Content |
|---|---|
| [docs/api/README.md](./docs/api/README.md) | API surfaces overview (v1 / portal / compat / MCP) |
| [docs/api/rest-v1.md](./docs/api/rest-v1.md) | Guided `/api/v1` reference with curl examples |
| [docs/api/openapi.json](./docs/api/openapi.json) | Generated OpenAPI spec (`tiqora openapi`) |
| [docs/api/compat.md](./docs/api/compat.md) | GenericInterface compatibility layer |
| [docs/api/mcp.md](./docs/api/mcp.md) · [docs/ai-integration.md](./docs/ai-integration.md) | MCP tools, webhook contract, AI-agent patterns |
| [docs/channels.md](./docs/channels.md) | Communication channel plugins |
| [docs/gdpr.md](./docs/gdpr.md) | GDPR anonymization & retention tooling |

**Reference**

| Document | Content |
|---|---|
| [docs/architecture.md](./docs/architecture.md) | System components and data flow |
| [docs/specs/2026-07-19-tiqora-design.md](./docs/specs/2026-07-19-tiqora-design.md) | Historical design specification |
| [NOTICE.md](./NOTICE.md) | Licensing breakdown and trademark notes |

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
5. Prefer small, reviewable PRs.

## License

Tiqora is licensed under the **GNU Affero General Public License v3.0**
(AGPL-3.0) — see [LICENSE](./LICENSE) — with three documented exceptions
(the GPL-3.0 TiqoraSync Znuny add-on, the dual-licensed
`backend/src/tiqora/znuny/` compatibility modules, and the verbatim upstream
schema fixtures). See [NOTICE.md](./NOTICE.md) for the complete licensing
picture, the reimplementation statement, and trademark notes.

Copyright © 2026 Cygnus Networks GmbH.

"Znuny" is a trademark of Znuny GmbH; "OTRS" is a registered trademark of
OTRS AG. Tiqora is not affiliated with, endorsed by, or sponsored by either
company; the names are used solely to describe factual compatibility.
