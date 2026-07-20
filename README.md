# Tiqora

> **⚠️ Beta — parallel operation.**
>
> Phases 0–5 mechanics are implemented and covered by golden-master and
> compat test suites: legacy models/auth/permissions, read-only UI +
> indexing, TicketService write path + GenericInterface compat + MCP, portal
> + KB + admin + OIDC/Kerberos/TOTP, daemon takeover feature flags, and the
> cutover mechanics (schema-ownership gate, additive owned migrations,
> [cutover runbook](./docs/cutover.md), [AI integration
> contract](./docs/ai-integration.md)). No production cutover has been
> performed with this codebase yet — schema ownership defaults OFF and
> requires an explicit, gated operator action (see the runbook). APIs,
> schema conventions, and operational behaviour may still change without
> notice.

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
| Project scaffolding, CI, Docker images | ✅ Done (Phase 0) | This repository state |
| Dev stack (MariaDB, Postgres, Redis, Meili, Mailpit) | ✅ Done | `docker-compose.dev.yml` |
| Config, async DB engine, health/ready/metrics | ✅ Done | `backend/src/tiqora` |
| Znuny legacy models + schema conformance tests | ✅ Done (Phase 0) | ~45 V1 tables, `tests/test_schema_conformance.py` |
| Auth: legacy password hashes, Redis sessions, API keys | ✅ Done (Phase 0) | bcrypt / sha256 / md5-crypt |
| Permission engine (groups, roles, ACL) | ✅ Done (Phase 0) | Shared by UI / REST / MCP |
| Read-only agent UI + Meilisearch index | ✅ Done (Phase 1) | Znuny-write poller, queue/zoom/search |
| TicketService write path + Znuny invariants | ✅ Done (Phase 2) | Golden-master validated against real Znuny 6.5.22 |
| GenericInterface compatibility layer | ✅ Done (Phase 2c) | TicketCreate/Update/Get/Search, SessionCreate, dynamic router |
| MCP server tools | ✅ Done (Phase 2c) | ticket_*, customer_lookup, kb_* — see `docs/ai-integration.md` |
| Customer portal | ✅ API + UI (Phase 3a/3b) | REST at `/api/portal/*`, UI at `/portal` |
| Knowledge base (`tiqora_kb_*`, RAG-ready) | ✅ API + UI (Phase 3a/3b) | Markdown, chunking, Meilisearch; agent editor + portal search UI |
| Admin CRUD (queues, DF, ACL, GenericAgent) | ✅ API + UI (Phase 3a/3b) | REST at `/api/v1/admin/*`, UI at `/admin` |
| OIDC, Kerberos/SPNEGO, TOTP | ✅ Done (Phase 3c) | Feature-flagged, off by default |
| Daemon takeover (mail, escalation, notify, GA) | ✅ Done (Phase 4) | Per-function `daemon.*.enabled` flags, default OFF |
| Schema-ownership gate + CLI + preflight | ✅ Done (Phase 5) | `tiqora ownership status/enable/orphan-report` |
| First owned migration + orphan report | ✅ Done (Phase 5) | Additive composite indexes only; read-only orphan counts |
| Cutover runbook | ✅ Done (Phase 5) | [docs/cutover.md](./docs/cutover.md) — no cutover performed yet |
| AI integration contract (webhooks + MCP) | ✅ Done (Phase 5) | [docs/ai-integration.md](./docs/ai-integration.md); no LLM code |
| TiqoraSync Znuny addon (cache coherence) | ✅ Done | OPM package in `packages/znuny-addon/TiqoraSync/` — daemon cron clears stale Znuny ticket caches |
| SSE realtime + agent presence | ✅ Done | `GET /api/v1/events/stream`; presence chips + stale-reply warning on ticket zoom |
| CSV ticket export | ✅ Done | `GET /api/v1/tickets/export.csv` — permission-filtered, streaming, Excel-friendly |
| Dev tooling: seed + dump anonymizer | ✅ Done | `tiqora dev seed` / `tiqora dev anonymize` — [docs/development.md](./docs/development.md) |
| Reports/stats (volume, backlog, SLA, workload) | ✅ Done | `GET /api/v1/stats/*` (+ CSV export) — `/agent/stats` UI, permission-filtered by queue |
| Calendar / appointments (recurrence, ICS export/feed, ticket links) | ✅ Done | `GET/POST /api/v1/calendar/*` — `/agent/calendar` UI (month/week/agenda), reuses Znuny `calendar*` tables |
| Communication channels: SMS, WhatsApp Business, phone/CTI | ✅ Done | Channel plugin interface — [docs/channels.md](./docs/channels.md); enable-flagged, off by default |
| LDAP/AD auth (agent + customer) | ✅ Done | Bind-search-bind, no auto-provisioning; feature-flagged |
| GDPR tools (anonymization + retention) | ✅ Done | Ownership-gated; `tiqora gdpr *` — [docs/gdpr.md](./docs/gdpr.md) |
| PGP / S-MIME (verify/decrypt inbound, sign/encrypt outbound) | ✅ Done | Flag-gated — [docs/crypto.md](./docs/crypto.md) |
| Process management | 🔲 Planned (next) | BPM ticket processes |
| SOAP compat transport, package manager | 🔲 Planned | REST compat done; SOAP next |

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
| **5 — Cutover + ownership + AI** (2–3 w) | Runbook, ownership flag, additive indexes, webhooks | ✅ Mechanics complete; production cutover/rollback drill still pending |

Phases 0–5 mechanics are complete. **Project status: beta — parallel
operation.** No production cutover (Phase 5's runbook) has been executed
against a real Znuny instance with this codebase; that remains the next
real-world milestone, not a code deliverable.

Detailed design: [docs/specs/2026-07-19-tiqora-design.md](./docs/specs/2026-07-19-tiqora-design.md).

## Documentation

Full index: **[docs/README.md](./docs/README.md)** — API reference
(`docs/api/`), the Znuny-to-Tiqora migration playbook (`docs/guide/`),
Docker Compose deployment (`docs/deploy/`), architecture, parallel
operation, cutover, and feature-area docs.

**Getting started & operating**

| Document | Content |
|---|---|
| [docs/guide/znuny-to-tiqora.md](./docs/guide/znuny-to-tiqora.md) | Operator playbook: run alongside Znuny, then migrate onto Tiqora (5 stages) |
| [docs/deploy/docker-compose.md](./docs/deploy/docker-compose.md) | Docker Compose deployment — services, env vars, external DB, reverse proxy |
| [docs/deployment.md](./docs/deployment.md) · [docs/parallel-operation.md](./docs/parallel-operation.md) · [docs/cutover.md](./docs/cutover.md) | Deployment notes, parallel-operation invariants, cutover runbook |
| [docs/development.md](./docs/development.md) · [docs/testing.md](./docs/testing.md) | Local dev, seeding/anonymizing, running the test suites incl. golden-master |

**API & integrations**

| Document | Content |
|---|---|
| [docs/api/README.md](./docs/api/README.md) | API surfaces overview (v1 / portal / compat / MCP), auth, conventions |
| [docs/api/rest-v1.md](./docs/api/rest-v1.md) | Guided `/api/v1` reference with curl examples |
| [docs/api/openapi.json](./docs/api/openapi.json) | Generated OpenAPI spec (`tiqora openapi`) |
| [docs/api/compat.md](./docs/api/compat.md) | GenericInterface compatibility layer for existing Znuny REST clients |
| [docs/api/mcp.md](./docs/api/mcp.md) · [docs/ai-integration.md](./docs/ai-integration.md) | MCP tools, webhook contract, AI-agent patterns, prompt-injection guidance |
| [docs/channels.md](./docs/channels.md) | Communication channel plugins (SMS, WhatsApp, phone/CTI) |
| [docs/gdpr.md](./docs/gdpr.md) | GDPR anonymization & retention tooling |

**Reference**

| Document | Content |
|---|---|
| [docs/README.md](./docs/README.md) | **Full documentation index** |
| [docs/architecture.md](./docs/architecture.md) | System components and data flow |
| [docs/specs/2026-07-19-tiqora-design.md](./docs/specs/2026-07-19-tiqora-design.md) | Full design specification |
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
5. Prefer small, reviewable PRs aligned with the phase roadmap.

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
