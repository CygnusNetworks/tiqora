# Tiqora

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](./LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend)
[![React + TypeScript](https://img.shields.io/badge/React-TypeScript-61DAFB?logo=react&logoColor=black)](./frontend)
[![Znuny/OTRS 6.0–7.3 DB](https://img.shields.io/badge/Znuny%2FOTRS-6.0--7.3%20DB-5B8CFF)](./docs/parallel-operation.md)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](./docs/deploy/docker-compose.md)
[![Live site](https://img.shields.io/badge/Live-demo%20%26%20product%20site-5B8CFF)](https://cygnusnetworks.github.io/tiqora/)
[![Backend coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CygnusNetworks/tiqora/badges/backend-coverage.json)](./backend)
[![Frontend coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/CygnusNetworks/tiqora/badges/frontend-coverage.json)](./frontend)

**Tiqora** is a modern, self-hosted ticket / helpdesk system that is
**database-compatible with OTRS 6.0.x and Znuny 6.0–7.3** (MariaDB/MySQL and
PostgreSQL). It is a clean-room reimplementation (Python FastAPI + React), not a
fork of Znuny — no Znuny source code is included or redistributed.

| | |
|---|---|
| **Backend** | Python 3.12+, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2 |
| **Frontend** | React + TypeScript + Vite, Tailwind, theming via CSS variables |
| **Search** | Meilisearch (full-text; hybrid / vector RAG planned later) |
| **Jobs** | Plain asyncio worker loops (`tiqora-worker`, `tiqora-ai-worker`), Redis for sessions/pub-sub |
| **AI surface** | MCP server (FastMCP) under the same permission engine as UI/REST |
| **License** | [AGPL-3.0](./LICENSE) — Copyright © 2026 Cygnus Networks GmbH |

## Try it

**In the browser, nothing to install:** the [interactive demo](https://cygnusnetworks.github.io/tiqora/demo/)
runs the full agent, admin and portal UI against mock data.

**On your machine, one command** (Docker with Compose v2):

```bash
curl -fsSLO https://raw.githubusercontent.com/CygnusNetworks/tiqora/main/docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d
```

Open <http://localhost:8000/> and sign in as `root@localhost` / `tiqora-demo`. The first
start creates the database and seeds 40 fake tickets; `docker compose -f
docker-compose.quickstart.yml down -v` removes everything again. The quickstart is for
evaluation only (fixed passwords, plain HTTP). For a real installation see
[Getting started](#getting-started--three-ways-to-run).

Running OTRS or Znuny already? Tiqora can work on **your existing database, next to
Znuny** — read [Moving off OTRS/Znuny without a big-bang migration](https://cygnusnetworks.github.io/tiqora/znuny-migration.html)
or the [comparison with Znuny, OTRS, Zammad and others](https://cygnusnetworks.github.io/tiqora/compare.html).

Questions, ideas, war stories from your OTRS migration:
[GitHub Discussions](https://github.com/CygnusNetworks/tiqora/discussions).

## Why Tiqora

- **Modern web UI** — agent workspace, admin console, and customer portal that feel
  like a current product, not a 2000s helpdesk skin.
- **Znuny / OTRS 6.0–7.3 database compatibility** — same core ticket tables; a
  runtime schema profile detects the peer (see
  [docs/support-matrix.md](./docs/support-matrix.md)). **Parallel operation** on
  one shared database is a first-class path (additive `tiqora_*` tables only until
  you explicitly take schema ownership). Preferred peers: **Znuny 6.5** (LTS) or
  **7.3**.
- **AI-ready ticket search** — Meilisearch indexing plus an **MCP server** so AI agents
  act with the same ACLs as humans.
- **AI agent assistance** — per-queue policies drive **draft replies**, **state-only
  ticket summaries** (document- and attachment-aware), **AI triage** (queue routing
  for new tickets), **text refine** with a word-level diff to review (and a
  call-note mode for phone notes), and an optional **autonomous auto-reply** worker.
  Attachments get text extraction plus a **vision pre-pass** for images; sensitive
  data is **PII-masked (spaCy NER)** before any LLM call; every request lands in an
  **audit log** with per-subject ACLs, token/request limits and per-provider **cost
  budgets** (day/week/month). Every AI-written article is marked 🤖 in the ticket
  and carries the **tool trace** behind it — each call with the arguments it was
  made with. The agent can **hand off to a human**, which really stops the
  auto-reply until someone takes over, and agents can **pause** all automatic AI
  actions per ticket. Bring your own OpenAI-compatible endpoints: a **model
  catalog** with fallback **profiles** and **per-task routing** (research, answer,
  triage, summary, refine, vision — globally or per queue). Gated by the operation
  mode so nothing autonomous runs during parallel operation (except auto-replies on
  Tiqora-only channels such as Telegram).
- **PGP and S/MIME** — Znuny-compatible key stores shared with a running Znuny;
  inbound mail verified and decrypted (security badge per article), outbound
  replies, forwards, new email tickets and event notifications signed and/or
  encrypted, with per-queue defaults (sign by default; encryption off / when
  possible / required), customer keys, and settings editable in the admin UI
  (Znuny's SysConfig still wins where it is set).
- **Telegram chat & phone/CTI** — a messenger-style chat composer for Telegram
  tickets (attachments, quote replies, answer buttons that resolve the ticket,
  edit/retract, chat snippets), and Znuny phone parity with an incoming-call
  popup fed by a PBX webhook, caller lookup, click-to-call and a compact phone
  ticket form.
- **GDPR tooling** — anonymization, retention jobs, and audit trails in admin.
- **Modern design** — light, dark and system themes, compact cobalt design system.
- **49 UI languages** — full Znuny language catalogue (48 Znuny `.po` codes +
  English source), RTL included; agent preference syncs with Znuny
  `UserLanguage` (see [docs/i18n.md](./docs/i18n.md)).
- **No Perl application stack** — Python FastAPI + React throughout Tiqora itself
  (optional small Znuny OPM addon only if you co-run Znuny for cache coherence).
- **Customer portal & knowledge base** — self-service tickets (opt-in via
  `TIQORA_PORTAL_ENABLED`) and Markdown KB.
- **Integration-friendly** — REST `/api/v1`, GenericInterface REST/SOAP compatibility,
  webhooks, channel plugins (email, SMS, WhatsApp, Telegram, phone/CTI).
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

![Inbox with channel filters and an incoming-call popup](./docs/images/agent-cti-popup.png)
<sub>Inbox sorted by last activity, with e-mail / Telegram / phone filters — and a CTI popup for the call that is ringing through.</sub>

Grouped by topic; click a section to expand it, click a picture for full size. The
[product site](https://cygnusnetworks.github.io/tiqora/#screenshots) shows the same set as a gallery with a light/dark switch.

<details>
<summary><b>AI assistance</b> — summaries, drafts with MCP tools, auto-replies, refine, model routing (12)</summary>

| Ticket summary | Draft grounded in MCP tools | Auto-reply with its tool trace |
|---|---|---|
| ![AI summary](./docs/images/agent-ai-assist.png) | ![AI assist + MCP](./docs/images/agent-ai-mcp.png) | ![AI origin trace](./docs/images/agent-ai-origin.png) |
| **Refine, reviewed as a diff** | **Model catalog** | **Task → profile routing** |
| ![Refine diff](./docs/images/agent-reply-refine.png) | ![AI models](./docs/images/admin-ai-models.png) | ![AI routing](./docs/images/admin-ai-routing.png) |
| **Per-queue policies** | **AI settings** | **Providers** |
| ![Queue AI policies](./docs/images/admin-ai-queue-policies.png) | ![AI settings](./docs/images/admin-ai-settings.png) | ![LLM providers](./docs/images/admin-ai-providers.png) |
| **Cost budget & tool rounds** | **MCP tool sources** | **LLM request audit** |
| ![Provider budget](./docs/images/admin-ai-provider-budget.png) | ![MCP clients](./docs/images/admin-ai-mcp.png) | ![AI audit](./docs/images/admin-ai-audit.png) |

</details>

<details>
<summary><b>Agent workspace</b> — inbox, ticket view, dashboard, search, reports, calendar, KB (7)</summary>

| Inbox | Ticket view | Dashboard |
|---|---|---|
| ![Queue view](./docs/images/agent-queues.png) | ![Ticket zoom](./docs/images/agent-ticket-zoom.png) | ![Agent dashboard](./docs/images/agent-dashboard.png) |
| **Search** | **Reporting & SLA** | **Calendar** |
| ![Search](./docs/images/agent-search.png) | ![Reporting](./docs/images/agent-stats.png) | ![Calendar](./docs/images/agent-calendar.png) |
| **Knowledge base** | | |
| ![Knowledge base](./docs/images/agent-kb.png) | | |

</details>

<details>
<summary><b>Chat &amp; phone</b> — Telegram chat, CTI call popup, compact phone ticket (3)</summary>

| Telegram chat | CTI call popup | Phone ticket with caller lookup |
|---|---|---|
| ![Telegram chat](./docs/images/agent-telegram-chat.png) | ![CTI popup](./docs/images/agent-cti-popup.png) | ![Phone ticket](./docs/images/agent-phone-ticket.png) |

</details>

<details>
<summary><b>PGP &amp; S/MIME</b> — verified mail, signed &amp; encrypted replies, queue defaults, key admin (5)</summary>

| Verified signature on arrival | Sign &amp; encrypt in the composer | Per-queue security defaults |
|---|---|---|
| ![Security badge](./docs/images/agent-crypto-badge.png) | ![Security control](./docs/images/agent-reply-refine.png) | ![Queue security](./docs/images/admin-queue-security.png) |
| **PGP keys** | **S/MIME certificates** | |
| ![PGP keys](./docs/images/admin-pgp.png) | ![S/MIME certificates](./docs/images/admin-smime.png) | |

</details>

<details>
<summary><b>Administration</b> — queues &amp; escalation, agents, groups, customers, fields, GDPR, 2FA (10)</summary>

| Queues | Escalation matrix | Agents |
|---|---|---|
| ![Admin queues](./docs/images/admin-queues.png) | ![Escalation matrix](./docs/images/admin-queue-escalation.png) | ![Admin users](./docs/images/admin-users.png) |
| **Groups** | **Roles ↔ groups** | **Customer users** |
| ![Admin groups](./docs/images/admin-groups.png) | ![Admin role groups](./docs/images/admin-role-groups.png) | ![Admin customer users](./docs/images/admin-customer-users.png) |
| **Customer user groups** | **Dynamic fields** | **Privacy / GDPR** |
| ![Admin customer user groups](./docs/images/admin-customer-user-groups.png) | ![Admin dynamic fields](./docs/images/admin-dynamic-fields.png) | ![GDPR](./docs/images/admin-gdpr.png) |
| **Two-factor** | | |
| ![2FA administration](./docs/images/admin-2fa.png) | | |

</details>

<details>
<summary><b>Sign-in &amp; portal</b> — login, account security, account menu, customer portal (4)</summary>

| Login | Account security | Account menu | Customer portal |
|---|---|---|---|
| ![Login](./docs/images/login.png) | ![Security](./docs/images/agent-security.png) | ![User menu](./docs/images/user-menu.png) | ![Customer portal](./docs/images/portal.png) |

</details>

<sub>Generated with `SCREENSHOTS=1 pnpm exec playwright test screenshots`
(`e2e/fixtures/rich-mock.ts`) — no backend required. Use `THEME=dark` / `LANG_UI=de` for variants.</sub>

## Also included

| Area | Notes |
|---|---|
| Ticket write path + Znuny invariants | Golden-master multi-peer matrix (OTRS/Znuny 6.0–7.3) |
| GenericInterface compatibility | Session*, TicketCreate/Update/Get/Search/HistoryGet, TimeAccountingGet, OutOfOffice; REST + SOAP |
| MCP tools | `ticket_*`, customer lookup, KB — see [docs/ai-integration.md](./docs/ai-integration.md) |
| AI assistance subsystem | Draft replies, summaries, triage, refine (diff review, call notes), auto-reply worker, human handoff, per-ticket pause, 🤖 origin traces, attachment/vision, PII masking, model catalog/profiles/task routing, per-subject ACL, cost budgets & audit — `/admin/ai/*`, [docs/ai-integration.md](./docs/ai-integration.md) |
| Daemon takeover (mail, escalation, notify, GA) | Per-function flags, off by default |
| Calendar / appointments | Month/week/agenda UI; reuses Znuny `calendar*` tables |
| Process management (BPM) | Reuses Znuny `pm_*` tables — [docs/process-management.md](./docs/process-management.md) |
| PGP / S-MIME | Off by default (Znuny's `PGP` / `SMIME` switches). Shared Znuny key stores, admin pages with overview/keys/settings, inbound verify/decrypt, outbound sign/encrypt with per-queue defaults and per-sender sign keys, signed/encrypted notifications, customer keys, public S/MIME roots trusted out of the box — [docs/crypto.md](./docs/crypto.md) |
| Telegram, SMS, WhatsApp, phone/CTI | Telegram chat composer; phone tickets, call logging and CTI incoming-call popup — [docs/channels.md](./docs/channels.md) |
| New-ticket helpers | Queue suggested from the customer's history, property bar, compact phone ticket with call strip |
| Integrations endpoint | Per-customer ticket history for external tools (`/api/v1/integrations/customer-tickets`) |
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
                     /api/v1    │              │  MCP (streamable HTTP)
              /znuny-compat/*   │              │
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
           │  OTRS/Znuny tables  │   │  tiqora_* tables│
           │  (6.0–7.3; R/W, no  │   │  (Alembic chain │
           │   schema changes)   │   │   versions_tiqora)│
           └──────────┬──────────┘   └────────┬────────┘
                      │                       │
         ┌────────────▼──────────┐            │
         │  Peer instance        │            │
         │  (optional parallel)  │◄── cache invalidation via TiqoraSync OPM
         └───────────────────────┘
                      │
         ┌────────────▼────────────────────────────────────┐
         │  tiqora-worker · ai-worker · Redis · Meili      │
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
    Worker[tiqora-worker + ai-worker]
    Domain[domain + permissions]
  end

  subgraph data [Shared data plane]
    ZTables[(Znuny tables)]
    TTables[(tiqora_* tables)]
    Redis[(Redis)]
    Meili[(Meilisearch)]
  end

  Znuny[OTRS/Znuny 6.0–7.3 peer]

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
  channels/         # Channel plugins (email, SMS, WhatsApp, Telegram, phone)
  storage/          # StorageBackend interface (DB MIME in V1)
  api/              # v1 routers + GenericInterface compat layer
  mcp_server/       # FastMCP process
  worker/           # asyncio worker loops (daemon takeover, outbox, pollers)
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
| **Quickstart** | Evaluate locally in two minutes, demo data included | [`docker-compose.quickstart.yml`](./docker-compose.quickstart.yml) — see [Try it](#try-it) |
| **Fresh standalone** | Empty database, no Znuny — greenfield install via `tiqora bootstrap` | [docs/guide/fresh-install.md](./docs/guide/fresh-install.md) |
| **Parallel to Znuny** | Co-run with an existing OTRS/Znuny **6.0–7.3** database (additive `tiqora_*` only) | [docs/support-matrix.md](./docs/support-matrix.md), [docs/parallel-operation.md](./docs/parallel-operation.md), [docs/guide/znuny-to-tiqora.md](./docs/guide/znuny-to-tiqora.md) |
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

### Toggling the customer portal

The customer portal is **off by default**: `/api/portal/*` answers 404 and `/`, `/portal`,
and `/portal/login` all redirect to the agent login. Two switches control it:

- **At deployment level**, `TIQORA_PORTAL_ENABLED=true` enables it. Without it the portal is
  hard-off and the admin switch cannot override that; the UI control is disabled, and a
  write attempt against the API returns 409.
- **In the admin UI** (only once the env var is set), on the *Authentication / 2FA* page →
  "Customer portal available". Takes effect immediately: turning it off makes the portal
  API answer 404, stops running customer sessions, and sends the start page to the agent
  login.

Customer records, customer companies, and email tickets are unaffected either way. The
Znuny-compat GenericInterface (`/znuny-compat/*`) is also unaffected — it's an integration
API, not part of the portal, so customers with credentials can still reach their own
tickets through it while the portal is off.

## Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| API | FastAPI + Pydantic v2 | Async-native, OpenAPI-first |
| ORM | SQLAlchemy 2 async | Dual drivers: asyncpg + aiomysql |
| Migrations | Alembic (two chains) | Own tables now; owned Znuny schema only after cutover |
| Jobs | Plain asyncio loops | Feature-flagged daemon takeover without an extra task queue; AI in its own worker |
| Search | Meilisearch | Fast full-text; later hybrid/vector for RAG |
| Sessions | Redis server-side | No JWT; Znuny-compatible session table for compat API |
| Frontend | Vite, React, TS, Tailwind | One app, three route trees, code-split |
| i18n | react-i18next | 49 locales (Znuny catalogue parity; [docs/i18n.md](./docs/i18n.md)) |
| Observability | structlog JSON, Prometheus `/metrics` | Zabbix template placeholder under `deploy/zabbix/` |
| MCP | FastMCP (separate process) | Same permission engine as UI/REST |

**Browser floor:** the UI's theme tokens are CSS variables tinted with
`color-mix()`, so it needs Chrome/Edge 111+, Safari 16.4+, or Firefox 113+
(all shipped in 2023). Older browsers render an uncoloured, but still usable,
approximation.

## Status

Core functionality is implemented and covered by automated tests, including
schema-matrix (Layer A) and multi-peer golden-master (Layer B) checks for
OTRS/Znuny **6.0–7.3**. Schema ownership defaults **off** and requires an
explicit operator action — see [docs/cutover.md](./docs/cutover.md).

### Znuny feature parity

Day-to-day agent, customer-portal and admin work is covered. What is still
missing or narrower than Znuny (last reviewed 2026-10-01):

- **Missing:** saved searches, configurable dashboard widgets, link overview
  and link-type admin, admin screens for sessions / SQL box / maintenance /
  system log, calendar admin and appointment rules, portal print view.
- **Partial:** statistics (fixed reports instead of the Znuny stats framework),
  GenericAgent execution, bulk actions, user preferences, GenericInterface
  edge cases, process conditions/actions.
- **By design:** Package Manager (OPM), process designer, SysConfig edit UI,
  GenericInterface webservice editor/requester.

The maintained, detailed list lives in
[docs/compatibility.md](./docs/compatibility.md#znuny-features-not-implemented).

## Documentation

Full index: **[docs/README.md](./docs/README.md)**

**Getting started & operating**

| Document | Content |
|---|---|
| [docs/guide/znuny-to-tiqora.md](./docs/guide/znuny-to-tiqora.md) | Operator playbook: run alongside Znuny, then migrate onto Tiqora |
| [docs/deploy/docker-compose.md](./docs/deploy/docker-compose.md) | Docker Compose deployment — services, env vars, reverse proxy |
| [docs/support-matrix.md](./docs/support-matrix.md) · [docs/parallel-operation.md](./docs/parallel-operation.md) · [docs/cutover.md](./docs/cutover.md) | Peer support matrix, parallel-op invariants, cutover runbook |
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

- **Target**: OTRS **6.0.x** and Znuny **6.0–7.3** database schemas (MariaDB/MySQL
  and PostgreSQL). Full matrix: [docs/support-matrix.md](./docs/support-matrix.md).
  Preferred peers: **Znuny 6.5** (LTS) or **7.3** (current release). Unknown schemas refuse to start
  unless overridden (`TIQORA_ALLOW_UNKNOWN_LEGACY_SCHEMA` /
  `TIQORA_LEGACY_SCHEMA_PROFILE=<profile_id>`).
- **Behaviour**: Ticket numbering, history name formats, escalation columns, and
  search-index flags must remain readable and writable by a co-running peer during
  parallel operation (golden-validated across the multi-peer matrix).
- **Code**: Tiqora is an independent implementation. Local peer release trees
  (`znuny-6.5.22/`, `znuny-7.3.5/`, …) are **gitignored** and never published in
  this repository.

## Contributing

Contributions are welcome. Read **[CONTRIBUTING.md](./CONTRIBUTING.md)** first, especially
the clean-room rule (never copy Znuny/OTRS source) and the rule about test data
(no real tickets, names or addresses). Questions go to [GitHub Discussions](https://github.com/CygnusNetworks/tiqora/discussions);
security problems go through [SECURITY.md](./SECURITY.md), not public issues.

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
