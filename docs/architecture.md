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
- `GET /api/v1/auth/methods` is a discovery endpoint (`{password, oidc,
  spnego}`) the agent login page uses to decide which buttons to show.
- **OIDC/SSO** (Phase 3c, authlib): `/api/v1/auth/oidc/login` redirects to the
  provider's authorization endpoint (state stored in Redis, short TTL);
  `/callback` exchanges the code, fetches `userinfo`, and maps a configurable
  claim (`TIQORA_OIDC_CLAIM`, default `preferred_username`) to `users.login`.
  **No auto-provisioning in v1** — the claim must match an existing,
  `valid_id = 1` user or the login is rejected (403). Same Redis session as
  password login is created on success.
- **Kerberos/SPNEGO** (Phase 3c, optional `kerberos` extra / `gssapi`):
  `/api/v1/auth/spnego` implements the Negotiate challenge/response
  (`401 WWW-Authenticate: Negotiate` → `Authorization: Negotiate <token>`).
  All `gssapi` calls run in an executor (it's a sync, C-extension-backed
  library). Feature-flagged off by default (`TIQORA_SPNEGO_ENABLED`); returns
  `501` if `gssapi` isn't installed. Principal's primary part maps to
  `users.login`.
- **TOTP 2FA** (Phase 3c, pyotp, per-user opt-in): `tiqora_user_totp` stores a
  Fernet-encrypted secret (key derived from `TIQORA_SECRET_KEY`). After a
  successful password/SSO/SPNEGO login for a user with TOTP enabled, the
  session is created in a **pending-2FA state** — tagged so it is invisible
  to the normal session-resolve path (`get_current_user`) and cannot touch
  any other endpoint. `POST /api/v1/auth/totp/verify` (±1 step / ~90s
  window) promotes it to a full session.
- Planned: GenericInterface SessionIDs against Znuny `sessions`.

### Webhooks (Phase 3c)

`tiqora_webhook` (admin CRUD under `/api/v1/admin/webhooks`) subscribes to
`tiqora_event_outbox` event types (empty list / `["*"]` = all events). The
worker's per-minute outbox drain (`worker/outbox_drain.py`) fans out each
batch to matching, valid webhooks via `worker/webhooks.py`:
`POST {event, ticket_id, payload, timestamp}` with an
`X-Tiqora-Signature: sha256=<hmac>` header (HMAC-SHA256 over the raw body,
keyed by the webhook's `secret`). Delivery retries up to
`TIQORA_WEBHOOK_MAX_ATTEMPTS` times with exponential backoff; exhausted
retries are logged and counted in `tiqora_webhook_deliveries_total{status}`
but never raised — webhook delivery must not block Meilisearch re-indexing.

### Customer portal (Phase 3a)

Mounted at `/api/portal` in the main API process (same `tiqora-api`), parallel
to `/api/v1`:

- **Auth:** separate identity plane from agents. `domain/customer_auth.py`
  (`CustomerAuthService`, `CustomerSessionStore`) verifies `customer_user.pw`
  (`valid_id = 1`) with the same `znuny.password` module used for agents, but
  keeps its own Redis key prefix (`tiqora:csession:`) and cookie
  (`tiqora_customer_session`) so agent and customer sessions never collide.
- **Scope:** a dedicated filter in `domain/portal_ticket_service.py` — not the
  agent `PermissionEngine` — restricts a customer to tickets where
  `ticket.customer_user_id == login`, plus (if the `tiqora_settings` flag for
  company-wide visibility is enabled) tickets whose `customer_id` matches any
  `customer_user_customer` row for that login.
- **Endpoints:** `/auth/login|logout`, `/auth/me`, `/tickets` (list/get/create),
  `/tickets/{id}/reply`, `/tickets/{id}/articles` (visibility-filtered to
  `is_visible_for_customer = 1`), `/tickets/{id}/attachments` (upload/download,
  same visibility filter). Ticket/article writes go through the existing
  `ticket_write_service` functions — the portal never hand-rolls history rows.
- **Reopen-on-followup:** ported from `Kernel/System/PostMaster/FollowUp.pm`.
  A customer reply to a ticket whose current state has StateType *closed*
  transitions the ticket to a configurable reopen state
  (`tiqora_settings` key `portal.followup_reopen_state`, default `"open"`)
  unless the ticket's queue has `follow_up_id == 2` ("reject"), in which case
  the reply is refused (HTTP 409). Splitting into a brand-new ticket
  (`follow_up_id == 3`) is not yet implemented — treated the same as reopen;
  see `docs/compatibility.md` uncertainties.

### Knowledge base (Phase 3a)

New module `tiqora.kb`, backed by dedicated `tiqora_kb_*` tables
(`versions_tiqora/20260719_0004_kb_tables.py`): category, article,
article_version, attachment, chunk, tag, article_tag, link. No Znuny tables
are touched.

- **Versioning:** every content-changing update to an article snapshots the
  prior row into `tiqora_kb_article_version` before applying the change and
  bumps `version`.
- **Chunker (`kb/chunker.py`):** pure function, no I/O. Splits `content_md` at
  H2/H3 boundaries targeting ~500 tokens (approximated as `len(text) // 4`),
  falling back to paragraph splits for oversized sections. Produces
  `heading_path` breadcrumbs (`"H1 > H2 > H3"`) and slugified, per-article-unique
  `anchor`s.
- **Publish:** `kb/service.py` `publish()` sets `state = "published"`, replaces
  the article's chunks, and pushes chunk documents to the Meilisearch `kb`
  index directly (not via `tiqora_event_outbox`, which is ticket-shaped) —
  acceptable since publishing is a low-frequency admin action.
- **Scoping:** agent search filters by `permission_group_id` (denormalized
  from category onto each chunk doc) via `PermissionEngine`; portal search is
  hard-restricted to `customer_visible = true AND state = "published"`.
- **Surfaces:** REST at `/api/v1/kb/*` (agent CRUD + publish + versions) and
  `/api/portal/kb/*` (customer search + read, published+visible only); MCP
  tools `kb_search` / `kb_get_article` in `mcp_server/server.py` call the same
  `kb/service.py` functions.

### Admin CRUD API (Phase 3a)

`/api/v1/admin/*`, guarded by `AdminUser` (`api/v1/admin/deps.py`): Znuny
"admin" semantics — membership (direct via `group_user`, or via
`role_user` → `group_role`) in the group literally named `admin` with `rw`,
checked by `PermissionEngine.is_admin()`. Non-admins get 403.

CRUD (soft-invalidate via `valid_id`/`valid`, never hard `DELETE`, matching
Znuny conventions) for users (+ group/role assignment, BCRYPT hash via
`znuny.password`), groups, roles, queues (incl. escalation minutes,
salutation/signature/system_address/follow_up), states, priorities,
customer_users, customer_companies (+ `customer_user_customer`),
salutations/signatures/standard templates (+ queue assignment),
auto_responses (+ `queue_auto_response`), and dynamic_fields (per-type YAML
`config` validation — e.g. Dropdown requires `PossibleValues`). Read-only
list/detail for postmaster_filter, ACL (edit deferred), and
generic_agent_jobs. Writes to queue/state/priority additionally invalidate
every currently-affected ticket (no per-config-row cache-invalidation entity
exists, so admin writes enumerate affected `ticket.id`s directly).

### Events and workers

- Writes emit events via a transactional outbox (`tiqora_event_outbox`).
- taskiq (Redis) drains the outbox for Meilisearch updates, mail, webhooks
  (later), and daemon-equivalent jobs once flags allow.

### Frontend

One Vite application with three route trees, each wrapped in its own shell
layout component (`components/layout/{Agent,Portal,Admin}Shell.tsx`):

| Path | Audience | Shell | Highlights |
|---|---|---|---|
| `/agent` | Agents (queue views, ticket zoom, compose, KB editor) | `AgentShell` | Queue tree, ticket zoom, search, `kb/*` article editor + publish flow |
| `/portal` | Customers | `PortalShell` | Ticket list/detail, follow-up replies, KB search + reader |
| `/admin` | Configuration | `AdminShell` | Generic CRUD over queues, users, dynamic fields, ACLs |

Code-split per tree. Theming uses CSS variables and `data-theme` (`light` /
`dark`) via a shared design-token stylesheet (`themes/tokens.css` +
`themes/theme.tsx`), consumed by all three shells so agent/portal/admin share
one visual language. i18n via `react-i18next` (English + German from day
one; `i18n/locales/{en,de}.json` are kept key-for-key in sync).

Admin CRUD screens are generated from a generic pattern rather than
hand-rolled per resource: `components/admin/DataTable.tsx` (sortable/paginated
table) plus `components/admin/CrudDrawer.tsx` (create/edit form drawer with
validation) are composed per resource in `components/admin/AdminResourcePage.tsx`,
with `DynamicFieldConfigEditor.tsx` handling the dynamic-field-specific
possible-values editing UI.

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
