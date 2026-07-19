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

- **Meilisearch** holds ticket indexes (`tickets` by default). Document shape:
  id, tn, title, queue, state/state_type, priority, owner, customer, escalation
  flags, latest article excerpt, flattened dynamic fields.
- **Filterable:** `queue_id`, `state_type`, `owner_id`, `customer_id`.
  **Sortable:** `changed`, `created`. Search always applies
  `queue_id IN [allowed]` from the permission engine.
- **Bulk backfill:** `tiqora index rebuild` (taskiq task too) batches of 500
  with resumable watermark in `tiqora_settings` (`index.rebuild.ticket_id`).
- **Znuny-write poller** (worker, default every 15s): watermarks on
  `ticket_history.id` and `article.id` in `tiqora_settings`; re-indexes distinct
  ticket ids; exposes Prometheus lag gauges
  (`tiqora_poller_history_lag`, `tiqora_poller_article_lag`).
- **Attachments** stay in the DB as LONGBLOB/bytea behind `StorageBackend`
  (`DbMimeStorage` in V1). REST streams content with correct Content-Type and
  disposition; `cid:` bodies rewrite to
  `/api/v1/tickets/{id}/articles/{aid}/attachments/by-cid/{cid}`.

### Read path (Phase 1a)

```
Browser / agent UI
    → REST /api/v1 (session cookie or API-key Bearer)
    → domain services (QueueService, TicketService, CustomerService, SearchIndexService)
    → permissions.PermissionEngine (ro on queue group)
    → db/legacy (Znuny tables, read-only) + Meilisearch
```

Key endpoints: `/auth/login|me|logout`, `/queues`, `/tickets`,
`/tickets/{id}/articles`, attachments, `/tickets/{id}/history`,
`/customers/{login}`, `/search?q=`. HTML article bodies are sanitised server-side
(`nh3` allowlist, cid rewrite, external images → `data-external-src`).

### Auth and sessions

- Password verification reuses Znuny hash formats (`BCRYPT:…`, sha256/512,
  md5-crypt) against `users.pw` (`valid_id = 1` only).
- Sessions are **server-side in Redis** (opaque token, httpOnly cookie
  `tiqora_session`, sliding TTL). Not JWT.
- API keys: `tiqora_api_key` (sha256 of key, linked to `user_id`);
  `Authorization: Bearer`.
- Planned: OIDC, Kerberos/SPNEGO (Linux KDC), optional TOTP per user;
  GenericInterface SessionIDs against Znuny `sessions`.

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

### MCP server (Phase 2c)

```
MCP client (Claude Code, Claude Desktop, ...)
    → HTTP  Authorization: Bearer <tiqora_api_key>
    → TiqoraBearerAuth middleware (SHA-256 hash lookup → user_id)
    → FastMCP tools (in tiqora.mcp_server.server)
    → domain services (ticket_write_service, TicketService)
    → permissions.PermissionEngine (same as REST)
    → db/legacy (async SQLAlchemy only)
```

**Process:** `tiqora-mcp` (entrypoint `tiqora.mcp_server.__main__:run_mcp`).
Runs as a standalone Starlette app on port 8001, mounted at `/mcp`.
Uses `fastmcp.http_app(transport="streamable-http")`.

**Auth:** Same `tiqora_api_key` table as REST. Bearer token in
`Authorization: Bearer` header validated on every request via `TiqoraBearerAuth`
middleware. The `user_id` is injected into `request.state.user_id` for tools.

**Tools (10):**

| Tool | Description |
|------|-------------|
| `ticket_search` | Meilisearch + DB fallback, permission-filtered |
| `ticket_get` | Markdown-rendered ticket with articles (visibility-aware) |
| `ticket_create` | Create ticket with optional first article |
| `ticket_reply` | Add customer-visible reply (default `is_visible_for_customer=True`) |
| `ticket_note` | Add internal note (default `is_visible_for_customer=False`) |
| `ticket_update_state` | Change state by state_id |
| `ticket_update_queue` | Move ticket to queue_id |
| `ticket_update_priority` | Change priority by priority_id |
| `ticket_update_owner` | Assign owner by user_id |
| `customer_lookup` | Look up customer user details |

**Constraints:** All tool handlers are `async`. No sync SQLAlchemy, no `requests`,
no `time.sleep` anywhere in `mcp_server/`. Unavoidable sync operations (none currently)
would use an executor helper.

### GenericInterface compat layer (Phase 2c)

Mounted at `/znuny-compat` in the main API process (same `tiqora-api`).

- **Canonical routes** always available (see `docs/compatibility.md`).
- **Dynamic routes** loaded from `gi_webservice_config` at startup via
  `mount_dynamic_compat_routes()` called in the lifespan.
- Auth: UserLogin+Password, CustomerUserLogin+Password, or Znuny SessionID
  (validated against `sessions` key-value table).
- Error shape: `{"Error": {"ErrorCode": "Op.ErrorType", "ErrorMessage": "..."}}`.
- Parameter merging: query string + JSON body, body wins.

## Non-goals (V1)

- ProcessManagement, calendar, stats, PGP/S-MIME
- SOAP GenericInterface
- Znuny package manager / OPM marketplace (except the small TiqoraSync addon)
- Shipping any Znuny source code in this repository
