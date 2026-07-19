# Architecture

This document describes the high-level architecture of Tiqora. For coexistence
rules with Znuny, see [parallel-operation.md](./parallel-operation.md). For the
full design rationale, see [specs/2026-07-19-tiqora-design.md](./specs/2026-07-19-tiqora-design.md).

## Goals

1. **Modern stack** — FastAPI + React, while remaining Znuny 6.5 DB-compatible.
2. **Parallel operation** — Run alongside Znuny on one shared database without
   altering Znuny tables until an explicit schema-ownership cutover.
3. **Single permission plane** — UI, REST (`/api/v1`), GenericInterface compat,
   and MCP share one ACL/group/role engine.
4. **Incremental daemon takeover** — Mail, escalation, notifications, and
   GenericAgent move to Tiqora behind feature flags.
5. **AI-ready surface** — MCP tools and (later) event webhooks as integration
   points for agents and automation.

## Runtime processes

| Process | Image / command | Responsibility |
|---|---|---|
| `tiqora-api` | `api` | HTTP: agent/portal/admin BFF, `/api/v1`, compat routes, `/health`, `/ready`, `/metrics` |
| `tiqora-worker` | `worker` | taskiq consumers: indexer, mailer, pollers, later postmaster/escalation/GA |
| `tiqora-mcp` | `mcp` | FastMCP over SSE; imports `domain/` directly (no second business layer) |

All three share the same container image with a switchable entrypoint.

## Logical layers

```
api / mcp_server          presentation & protocol adapters
        │
domain                    only write path; orchestrates invariants
        │
permissions               group/role + ACL evaluation
        │
znuny/*                   pure behavioural ports of Znuny mechanics
        │
db/legacy + db/tiqora     SQLAlchemy models
storage / channels        pluggable backends
events                    outbox + async bus
worker / kb               async jobs and knowledge base
```

### Domain services

Domain services (e.g. `TicketService`) are the **only** modules that perform
multi-table writes against Znuny tables. They always bundle:

- ticket number allocation (`znuny/ticket_number`),
- history row formats (`znuny/history`),
- escalation recompute (`znuny/escalation`),
- search-index flags,
- cache invalidation markers,
- transactional outbox events.

Adapters (REST, MCP, workers) must not invent partial writes.

### Database dual-stack

- Drivers selected from `DATABASE_URL`:
  - `postgresql+asyncpg://…` → asyncpg
  - `mysql+aiomysql://…` → aiomysql
- Two Alembic version chains:
  - `alembic/versions_tiqora/` — active; creates only `tiqora_*` tables
  - `alembic/versions_owned/` — empty until schema-ownership mode is enabled
    after cutover (FKs, indexes, orphan reports)

### Search and attachments

- **Meilisearch** holds ticket/article/KB indexes. During parallel operation a
  poller watches Znuny write watermarks (`ticket_history.id`, `article.id`) and
  a nightly reconcile uses `ticket.change_time`.
- **Attachments** stay in the DB as LONGBLOB/bytea behind a `StorageBackend`
  interface (S3/filesystem later).

### Auth and sessions

- Password verification reuses Znuny hash formats (`BCRYPT:…`, sha256/512,
  md5-crypt).
- Sessions are **server-side in Redis** (not JWT). The GenericInterface compat
  layer additionally validates SessionIDs against Znuny’s `sessions` table.
- Planned: OIDC, Kerberos/SPNEGO (Linux KDC), optional TOTP per user.

### Events and workers

- Writes emit events via a transactional outbox (`tiqora_event_outbox`).
- taskiq (Redis) drains the outbox for Meilisearch updates, mail, webhooks
  (later), and daemon-equivalent jobs once flags allow.

### Frontend

One Vite application with three route trees:

| Path | Audience |
|---|---|
| `/agent` | Agents (queue views, ticket zoom, compose) |
| `/portal` | Customers |
| `/admin` | Configuration |

Code-split per tree. Theming uses CSS variables and `data-theme` (`light` /
`dark`). i18n via `react-i18next` (English + German from day one).

## Observability

| Endpoint / artifact | Purpose |
|---|---|
| `GET /health` | Liveness (process up) |
| `GET /ready` | Readiness (DB/Redis connectivity) |
| `GET /metrics` | Prometheus metrics (latencies, queue depth, poller lag) |
| structlog JSON | Request and worker logs |
| `deploy/zabbix/` | Zabbix template (HTTP agent on metrics/JSON) |

## Non-goals (V1)

- ProcessManagement, calendar, stats, PGP/S-MIME
- SOAP GenericInterface
- Znuny package manager / OPM marketplace (except the small TiqoraSync addon)
- Shipping any Znuny source code in this repository
