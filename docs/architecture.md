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
5. **AI-ready surface** — MCP tools and event webhooks as integration points
   for agents and automation.

## Runtime processes

| Process | Image / command | Responsibility |
|---|---|---|
| `tiqora-api` | `api` | HTTP: agent/portal/admin BFF, `/api/v1`, compat routes, `/health`, `/ready`, `/metrics` |
| `tiqora-worker` | `worker` | taskiq consumers: indexer, mailer, pollers, postmaster/escalation/GA (feature-flagged) |
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
  - `alembic/versions_owned/` — gated until schema-ownership mode is enabled
    after cutover (additive indexes, FKs, orphan reports)

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

### Channels

`channels/` holds one package per inbound/outbound communication path; every
plugin funnels through `domain/ticket_write_service.{create_ticket,add_article}`
— it never writes tickets/articles itself. `channels/common.py` has the
building blocks shared across the non-email plugins: `communication_channel`
row registration, phone-number → `customer_user` resolution, and
follow-up-or-create ticket dispatch (`detect_followup` subject/body scan,
falling back to "most recent non-closed ticket for this customer").

| Package | Direction | Transport |
|---|---|---|
| `channels/email/` | in+out | IMAP/POP fetch, SMTP send (postmaster pipeline) |
| `channels/sms/` | in+out | Generic HTTP webhook gateway (`SmsGateway` protocol) |
| `channels/whatsapp/` | in+out | Meta WhatsApp Cloud API (Graph API) |
| `channels/phone/` | in only | Thin CTI logging API (no gateway) |

SMS/WhatsApp/Phone are mounted under `/api/v1/channels/{sms,whatsapp,phone}`,
disabled by default (`channel.<name>.enabled` in `tiqora_settings`), and
configured via `/api/v1/admin/channels`. Full details, endpoints, and config
keys: [channels.md](./channels.md).

### Read path

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
  spnego, ldap}`) the agent login page uses to decide which buttons to show.
- **OIDC/SSO** (authlib): `/api/v1/auth/oidc/login` redirects to the
  provider's authorization endpoint (state stored in Redis, short TTL);
  `/callback` exchanges the code, fetches `userinfo`, and maps a configurable
  claim (`TIQORA_OIDC_CLAIM`, default `preferred_username`) to `users.login`.
  **No auto-provisioning in v1** — the claim must match an existing,
  `valid_id = 1` user or the login is rejected (403). Same Redis session as
  password login is created on success.
- **Kerberos/SPNEGO** (optional `kerberos` extra / `gssapi`):
  `/api/v1/auth/spnego` implements the Negotiate challenge/response
  (`401 WWW-Authenticate: Negotiate` → `Authorization: Negotiate <token>`).
  All `gssapi` calls run in an executor (it's a sync, C-extension-backed
  library). Feature-flagged off by default (`TIQORA_SPNEGO_ENABLED`); returns
  `501` if `gssapi` isn't installed. Principal's primary part maps to
  `users.login`.
- **TOTP 2FA** (pyotp, per-user opt-in): `tiqora_user_totp` stores a
  Fernet-encrypted secret (key derived from `TIQORA_SECRET_KEY`). After a
  successful password/SSO/SPNEGO login for a user with TOTP enabled, the
  session is created in a **pending-2FA state** — tagged so it is invisible
  to the normal session-resolve path (`get_current_user`) and cannot touch
  any other endpoint. `POST /api/v1/auth/totp/verify` (±1 step / ~90s
  window) promotes it to a full session. `GET /api/v1/auth/totp/enroll/qr`
  renders the pending enrollment's `otpauth://` URI as an `image/svg+xml` QR
  code (`qrcode` lib, SVG factory — no `PIL` dependency); the agent security
  page (`/agent/security`) consumes it as a plain cookie-authenticated
  `<img src>`. 404 with no pending enrollment.
- **LDAP/AD** (`ldap3`, bind-search-bind): ports
  `Kernel::System::Auth::LDAP` / `CustomerAuth::LDAP`
  (`domain/auth_ldap.py` + `domain/customer_auth_ldap.py`, sharing the
  bind-search-bind core in `domain/_ldap_core.py`). Tried as a **fallback**
  when local password auth fails and `TIQORA_LDAP_ENABLED` /
  `TIQORA_CUSTOMER_LDAP_ENABLED` is set. All `ldap3` calls run in an
  executor (sync library, same rule as `gssapi`). **No auto-provisioning in
  v1** — the LDAP UID resolved by the search must match an existing,
  `valid_id = 1` `users.login` / `customer_user.login` row, or the login is
  rejected. Optional group-membership gate (`GroupDN`/`AccessAttr`, mirrors
  the Perl module). Simplified vs. Znuny: no `Die`/`UserSuffix`/
  `UserLowerCase`/per-directory charset knobs.
- Planned: GenericInterface SessionIDs against Znuny `sessions`.

### Webhooks

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

### Customer portal

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

### Knowledge base

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

### Admin CRUD API

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

### Stats / reporting

`tiqora/stats/` — a modern equivalent of Znuny's `Kernel::System::Stats`
(AgentStatistics), not a port: instead of Znuny's dynamic report-object
framework (arbitrary user-defined X/Y-axis stat objects), `StatsService`
provides a fixed set of purpose-built, permission-filtered (`ro` queues, same
scoping as `TicketService`) reports with typed params (`StatsFilters`) and
dataclass results:

- ticket volume (created vs. closed per day/week/month bucket)
- open-ticket snapshot by queue/state/priority/owner
- SLA/escalation: escalated count + first-response/update/solution breach
  counts (from the ticket `escalation_*` epoch columns) plus raw
  first-response/solution time-to-event samples (minutes) for
  percentile/histogram display client-side
- agent workload (open tickets owned + tickets closed in the filtered period)
- backlog trend (running open-ticket count, derived from the volume report's
  created/closed deltas)

Bucketing is done in Python after fetching bare `(id, timestamp)` rows rather
than with dialect-specific SQL date-trunc functions, so the same code runs
against both the MySQL and PostgreSQL backends. `/api/v1/stats/*`
(`api/v1/stats.py`) exposes one JSON endpoint plus a `.csv` streaming-export
sibling per report, gated on any authenticated agent (`CurrentUser`) — access
control is enforced by the queue scoping inside `StatsService`, not by a
separate route-level permission check. The frontend `/agent/stats` page
(`routes/agent/StatsPage.tsx`) renders a filter bar (queue, date range,
granularity), mono-numeral stat tiles, and hand-rolled SVG bar/line charts
(`components/agent/stats/{BarChart,LineChart}.tsx` — no charting dependency)
plus a CSV-downloadable agent-workload table.

### Calendar / appointments

`tiqora/calendar/` (`CalendarService`, `recurrence.py`, `ics.py`) plus
`api/v1/calendar.py` (`/api/v1/calendar/*`). Reuses Znuny's **existing**
schema — `calendar`, `calendar_appointment`, `calendar_appointment_ticket`
(mapped in `db/legacy/calendar.py`) — verbatim, so rows written by Tiqora are
visible unmodified to a running Znuny 6.5 and vice versa. `calendar_id` reuses
the same `permission_groups` table as queues, so `PermissionEngine.
groups_for_permission()` gates calendar/appointment access exactly like queue
access (`ro` to view, `rw` to create/edit/delete); `calendar_appointment_
plugin` (Znuny's per-appointment plugin JSON blob) is not mapped — Tiqora has
no plugin system.

Simplifications vs. Znuny's Perl `Kernel::System::Calendar::Appointment`:

- **Recurrence** — Znuny's `AppointmentCreate` *materialises* one
  `calendar_appointment` row per occurrence (`recur_id`/`parent_id` chain).
  Tiqora keeps a single parent row and expands occurrences on read
  (`recurrence.expand_occurrences`) from `recur_type`/`recur_interval`/
  `recur_count`/`recur_until` — the common RRULE subset Znuny's own UI
  exposes (`Daily`/`Weekly`/`Monthly`/`Yearly`). This is O(1) writes instead
  of O(n) row materialisation, at the cost of not supporting Znuny's
  "edit/detach a single occurrence" (which diverges a child row); Tiqora
  instead supports deleting a single occurrence via a JSON-encoded exclusion
  list in `recur_exclude`.
- **Ticket links** — `calendar_appointment_ticket` rows are written with
  `rule_id='manual'`; Znuny's automatic "ticket appointment rules" (deriving
  `rule_id` from a configured queue/SLA rule) are out of scope.
- **ICS export/subscription** — `GET /calendar/calendars/{id}/export.ics`
  (authenticated) and a token-gated `GET /calendar/calendars/{id}/feed.ics`
  (unauthenticated, for pasting into external calendar clients) both emit
  RFC 5545 via a small hand-rolled writer (`calendar/ics.py`, one `VEVENT`
  per parent appointment with a native `RRULE`/`EXDATE`, not one per expanded
  occurrence). The feed token is `md5(f"{login}-{calendar.salt_string}")` —
  bit-for-bit the same scheme as Znuny's
  `Kernel::System::Calendar::GetAccessToken`, so a token minted by either
  system authenticates against the other.
- **Cache invalidation** — appointments do not touch the ticket search index
  or `tiqora_event_outbox`; calendars are a separate Znuny subsystem with no
  ticket-cache interaction, so no invalidation hook is wired here.

Frontend: `/agent/calendar` (`routes/agent/CalendarPage.tsx`) — month grid
(`components/agent/calendar/MonthGrid.tsx`, a hand-rolled 42-cell grid, no
calendar library), week and agenda views, a calendar-switcher sidebar
(checkbox per calendar, permission-filtered), date navigation, and a
create/edit `AppointmentDialog` (title, calendar, start/end, all-day,
location, description, recurrence). i18n: EN + DE (`calendar.*` keys).

### Process management (BPM ticket processes)

`tiqora/process/` (`config.py` Pydantic models for the YAML `config` blobs,
`graph.py`'s `ProcessRepository` for read-only process loading,
`ticket_state.py` for the two-Dynamic-Field ticket<->process link,
`engine.py` for execution — transitions, conditions, TransitionActions,
activity dialog submission) plus `api/v1/process.py`
(`/api/v1/process/*`, 6 endpoints). Reuses Znuny's **existing** `pm_process`
/ `pm_activity` / `pm_activity_dialog` / `pm_transition` /
`pm_transition_action` tables verbatim (`db/legacy/process_management.py`)
— same shared-schema, no-migration approach as Calendar above.

Engine flow, one line: start a process at its `StartActivity` -> agent
submits an activity dialog -> validate required fields + the dialog's
`Permission` -> apply submitted field changes to the ticket (reusing
`ticket_write_service`, so history stays Znuny-shaped) -> evaluate the
current activity's outgoing transitions in declared order, first
`Condition` match wins -> run that transition's `TransitionAction`s ->
advance `ProcessManagementActivityID` to the target activity.

Frontend: a `ProcessWidget` + `StartProcessDialog` + `ActivityDialogModal`
on ticket zoom (`components/agent/process/`), and a read-only
`/admin/processes` list/detail pair — there is no visual process designer;
processes are still authored via Znuny's admin UI or direct DB/YAML.

Condition types and TransitionAction modules are a documented subset of
Znuny's (String/Regexp/Contains/NotContains/Equal/NotEqual conditions;
ten of the most common TransitionAction modules) — see
[docs/process-management.md](process-management.md) for the full
supported-vs-deferred breakdown and REST endpoint list.

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

### MCP server

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

**Tools (25):** ticket read (`ticket_search`, `ticket_get`, `ticket_get_by_number`),
ticket write (`ticket_create`, `ticket_reply`, `ticket_note`,
`ticket_update_state`/`queue`/`priority`/`owner`, `ticket_set_title`/`customer`/
`dynamic_field`, `ticket_lock`/`unlock`), reference/discovery (`list_queues`,
`list_states`, `list_priorities`, `list_agents`), knowledge base (`kb_search`,
`kb_get_article`, `kb_list`, `kb_upsert_article`, `kb_publish_article`), and
`customer_lookup`. Full catalogue: [`api/mcp.md`](api/mcp.md).

MCP deliberately does **not** mirror admin/portal/calendar/BPM/stats/GDPR.

**Constraints:** All tool handlers are `async`. No sync SQLAlchemy, no `requests`,
no `time.sleep` anywhere in `mcp_server/`. Unavoidable sync operations (none currently)
would use an executor helper.

### GenericInterface compat layer

Mounted at `/znuny-compat` in the main API process (same `tiqora-api`).

- **Canonical routes** always available (see `docs/compatibility.md`).
- **Dynamic routes** loaded from `gi_webservice_config` at startup via
  `mount_dynamic_compat_routes()` called in the lifespan.
- Auth: UserLogin+Password, CustomerUserLogin+Password, or Znuny SessionID
  (validated against `sessions` key-value table).
- Error shape: `{"Error": {"ErrorCode": "Op.ErrorType", "ErrorMessage": "..."}}`.
- Parameter merging: query string + JSON body, body wins.
- **SOAP transport** (`api/compat/soap.py`): a second wire codec for the
  same 5 operations/dispatch table — parses `HTTP::SOAP` envelopes (Body
  wrapper element name → operation, via `defusedxml` to prevent XXE) and
  serializes `<OperationNameResponse>`/`<Fault>` envelopes. Mounted at
  `POST /znuny-compat/soap/{webservice}` (fallback) plus dynamic
  `Webservice`/`WebserviceID` routes per `gi_webservice_config` row with
  `Provider.Transport.Type == 'HTTP::SOAP'`. No separate operation
  implementations — REST and SOAP both call `op_session_create` /
  `op_ticket_*` directly. See `docs/compatibility.md#soap-transport`.

## Non-goals (V1)

- calendar, stats, PGP/S-MIME
- Znuny package manager / OPM marketplace (except the small TiqoraSync addon)
- Shipping any Znuny source code in this repository
