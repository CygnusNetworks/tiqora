# Tiqora design specification

**Date:** 2026-07-19  
**Status:** Accepted plan → implementation scaffold  
**Language of this document:** English  
**Source plan:** internal planning note (German); this file is the authoritative
design adaptation for the repository.

## Context

Znuny 6.5 (Perl, ~122 database tables) is to be replaced by a modern
implementation: Python/FastAPI backend, React frontend with theming, MCP server,
channel plugin architecture, and later AI agents.

**Hard requirement: parallel operation.** In V1, Tiqora and Znuny run
simultaneously against the **same database** (PostgreSQL or MySQL/MariaDB). A
local Znuny reference tree may exist at `znuny-6.5.22/` for developers; it is
never committed.

## Decided points

| Topic | Decision |
|---|---|
| Name | **Tiqora** |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic v2 |
| Frontend | React + TS + Vite, Tailwind + shadcn/ui (later), themes via CSS variables |
| DB | PostgreSQL **and** MySQL/MariaDB (both in the test matrix) |
| Parallel operation | No schema changes to Znuny tables; new data only in `tiqora_*`; Znuny daemon keeps mail/escalation/notifications/GenericAgent until per-function feature flags; post-cutover “schema ownership” unlocks a second Alembic chain |
| Search | Meilisearch (later hybrid/vector for RAG) |
| Attachments | Remain in DB (LONGBLOB/bytea) behind `StorageBackend` (S3/FS later) |
| API | Native REST `/api/v1` (OpenAPI) + GenericInterface compat (TicketCreate/Update/Get/Search, SessionCreate) |
| MCP | V1 full agent access, API keys, same permission layer as UI (FastMCP, own process) |
| Auth | Reuse Znuny password hashes (`BCRYPT:`, sha256/512, md5-crypt), OIDC, Kerberos/SPNEGO (Linux KDC), optional TOTP; Redis server sessions (no JWT) |
| V1 scope | Full agent workflow, customer portal, admin core, ACL engine, GenericAgent, **new knowledge base** (LLM/RAG-ready, `tiqora_kb_*`) |
| Deferred | ProcessManagement, calendar, stats, PGP/S-MIME, SOAP, package manager |
| Channels | Plugin interface; V1: email + web; planned: phone/SMS, WhatsApp Business |
| License | AGPL-3.0 |

## Verified Znuny mechanics (from source review)

These must be ported behaviourally — not by copying Perl.

### Ticket numbers

`Kernel/System/Ticket/NumberBase.pm`: lock-free counter over
`ticket_number_counter` (insert 0 → ~50 ms settle → idempotent fill-up with
`WHERE counter = 0` → read-back, collision retry). Short autocommit transactions
per step. Safe under concurrent Znuny writers.

### Escalation

`Ticket.pm` / `TicketEscalationIndexBuild`: four epoch-int columns on the ticket,
from SLA/queue + working-time calendar; recompute on every relevant write.
Daemon `RebuildEscalationIndexOnline` remains a safety net.

### History

`ticket_history` name formats (`%%…%%…`) are parsed by Znuny (merge chains,
first response). Exact format strings, centralised in `znuny/history.py`.

### Search index

Set `article.search_index_needs_rebuild = 1`; Znuny daemon builds
`article_search_index`. `ticket_index` only when `Ticket::IndexModule = StaticDB`
(recommend RuntimeDB).

### Follow-up

TN regex in subject, `a_message_id_md5` / References, merge chain depth 10.

### Integrity

Base schema has no FKs; `schema-post` (applied after `initial_insert`) adds them
on real installs. Write FK-safe; tolerate orphans on legacy upgraded DBs;
application-side integrity remains required.

### Cache staleness

Direct DB writes appear delayed in Znuny. Small Perl OPM `TiqoraSync` (~150
lines): daemon cron reads `tiqora_cache_invalidation` and clears ticket caches.
Fallback: document low cache TTLs.

## Monorepo layout

```
backend/src/tiqora/
  db/legacy/        # ~45 hand-written models for Znuny tables + conformance tests
  db/tiqora/        # tiqora_* models (Alembic: versions_tiqora/ + versions_owned/ gated)
  znuny/            # invariants: ticket_number, history, escalation, followup,
                    # sysconfig reader, password, ticket_index, search_flag, cache_invalidation
  domain/           # services (TicketService etc.) — sole write paths
  permissions/      # one engine: group/role (+ ACL documented ambition) for UI/REST/MCP
  events/           # async bus + transactional outbox (tiqora_event_outbox)
  channels/         # channel plugin protocol; email/, web/
  storage/          # StorageBackend; v1 DB MIME
  api/              # v1/ routers + compat/ (GenericInterface emulation)
  mcp_server/       # FastMCP, own process, imports domain/ directly
  worker/           # taskiq (Redis): indexer, mailer, later postmaster/escalation/GA
  kb/               # knowledge base
frontend/           # one Vite app: /agent, /portal, /admin (code-split)
packages/api-client/        # generated TS client from OpenAPI (later)
packages/znuny-addon/TiqoraSync/   # Perl OPM cache invalidation (later)
tests/              # testcontainers (MariaDB+PG), golden-master vs Znuny container
docker-compose.dev.yml
```

### Further design decisions

- **Worker:** taskiq (asyncio-native, DI similar to FastAPI, cron for daemon
  takeover) instead of Celery/arq.
- **Compat API:** operations fixed; routes dynamic from `gi_webservice_config`
  plus default fallbacks; known gotchas as regression tests (`StateType`
  singular, visibility defaults, SessionID vs Znuny `sessions`).
- **HTML mail rendering:** sandboxed iframe + CSP, `cid:` → attachment endpoint,
  external images click-to-load.
- **Znuny write detection:** poller watermarks on `ticket_history.id` and
  `article.id`; nightly reconcile on `ticket.change_time`.
- **KB:** Markdown source, versioning, heading-aware chunking (~500 tokens) in
  `tiqora_kb_chunk` → Meilisearch; citable for RAG/MCP from day one.
- **MCP tools V1:** ticket_search/get/create/reply/note/update, customer_lookup,
  kb_search/get; all async; CI ban on sync blocking in MCP tools (SSE starvation).
- **PermissionEngine runtime (current state, deliberate):** evaluates **group/role
  only** (`group_user` / `role_user` → `group_role`, with `rw` implying other keys).
  Znuny `acl` table rows (`config_match` / `config_change`) are **not** evaluated
  at runtime. Admin exposes ACL as read-only (`GET /api/v1/admin/acl`); full ACL
  editor + runtime evaluation remain deferred. Frame as a known limitation of the
  current cut, not as a bug in MCP/REST scoping.

## Phases

### Phase 0 — Foundation (2–3 weeks)

Scaffolding, dev stack, legacy models + schema conformance (both DBs),
SysConfig reader, auth (legacy hashes + sessions + API keys), permission engine,
CI matrix.

**Done when:** login against a real Znuny dump with all hash schemes.

### Phase 1 — Read-only agent UI (3–4 weeks)

REST read endpoints, Meilisearch bulk index + Znuny write poller, queue views /
zoom / search / dashboard read-only. Safest parallel entry (zero writes).

**Done when:** side-by-side diff vs Znuny zoom; Playwright smoke.

### Phase 2 — Write path + compat API + MCP (5–6 weeks)

Highest-risk phase. Full TicketService, all `znuny/` invariants, TiqoraSync
addon, SMTP (mail_queue format or own send), compat API, MCP, outbox indexing.

**Done when:** golden-master API and DB effect diffs vs real Znuny; parallel soak;
TN concurrency with mixed writers.

### Phase 3 — Portal + KB + Admin (4–5 weeks)

Customer portal, KB + chunker, admin CRUD (dynamic fields, ACL editor,
GenericAgent editor), OIDC/Kerberos/TOTP.

**Done when:** queue created in Tiqora appears in Znuny (proves cache invalidation).

### Phase 4 — Daemon takeover via flags (4–6 weeks)

Postmaster pipeline, escalation sweep, notification engine, GenericAgent
executor, auto-responses — each switchable, mutually exclusive with Znuny daemon.

**Done when:** mail round-trip via Mailpit; notification diffs.

### Phase 5 — Cutover + schema ownership + AI hooks (2–3 weeks)

Runbook, ownership flag (DB marker + config), first owned migrations (additive
FKs/indexes/orphan report), event webhooks + MCP as AI integration surface.

**Done when:** regression suite on production clone; rollback drill.

## Additional agreed scope

| Area | Notes |
|---|---|
| i18n DE+EN day one | react-i18next; backend notification/mail templates; `UserLanguage` compatible |
| Realtime + collision | SSE from event bus + Znuny poller; Redis presence TTLs; compose warnings |
| Observability | structlog JSON, `/health` `/ready` `/metrics`, Zabbix template under `deploy/zabbix/` |
| Webhooks | `tiqora_webhook`, outbox delivery, HMAC-SHA256, retry/backoff, admin UI (Phase 3) |
| GDPR tools | anonymisation, retention jobs, audit report (Phase 5+, after ownership) |
| Agent productivity | keyboard shortcuts, CSV export, WCAG basics |
| Dev tools | `tiqora dev seed`, `tiqora dev anonymize-dump` |

## Repository, Docker, CI

- Public GitHub intent: `CygnusNetworks/tiqora` (repo creation is an operator step;
  this scaffold does not create or push remotes).
- All docs/README/code comments in **English**.
- Multi-stage Docker image; compose for dev and example production.
- CI: ruff/mypy, pytest with MariaDB+Postgres services, frontend build;
  Docker buildx multi-arch push to GHCR and Docker Hub on main/semver.
- Znuny reference and tarball are **gitignored**; Tiqora is an independent
  implementation, not a derived work shipping Znuny code.

## Critical Znuny reference files (local only)

| Path under `znuny-6.5.22/` | Use |
|---|---|
| `Kernel/System/Ticket/NumberBase.pm`, `Number/*.pm` | Counter algorithm |
| `Kernel/System/Ticket.pm` | History formats, escalation, merge chains |
| `Kernel/System/Auth/DB.pm` | Hash verification |
| `Kernel/GenericInterface/Operation/Ticket/*.pm`, `Session/SessionCreate.pm` | Compat wire format |
| `scripts/database/schema.xml` (+ generated SQL) | Schema source for legacy models |

## Scaffolding status (this commit)

Phase 0 **skeleton only**: repository layout, docs, backend package shell with
config/engine/app, Alembic two-chain layout, frontend stubs, Docker, CI.
Business logic, legacy models, and tests against Znuny dumps are **not**
implemented yet.
