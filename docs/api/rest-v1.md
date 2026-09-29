# REST v1 guided reference (`/api/v1`)

This is a task-oriented "how to actually use it" guide with curl examples.
For exhaustive field-level detail (every request/response schema, every
optional parameter), load [`openapi.json`](openapi.json) into Swagger UI /
Redoc / Postman, or browse `GET /docs` on a running instance.

All examples assume a Tiqora instance at `https://tickets.example.com`. Set
it once:

```sh
export TIQORA_URL=https://tickets.example.com
```

## Auth: login, current user, API keys

**Session login** (cookie-based, used by the agent UI):

```sh
curl -c cookies.txt -X POST "$TIQORA_URL/api/v1/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"login": "agent1", "password": "YOUR_PASSWORD"}'

curl -b cookies.txt "$TIQORA_URL/api/v1/auth/me"
```

**API key** (bearer token, used by scripts/automation). Issue and manage keys
via the admin API or the CLI:

```sh
# Admin API (session cookie from an admin agent)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/admin/api-keys" \
  -H 'Content-Type: application/json' \
  -d '{"name": "ci-bot", "user_id": 1}'
# → 201 with {"id": …, "key": "tiqora_…", …}  (raw key shown only once)

# Read-only key (all areas :ro) or custom area scopes (CSV):
# -d '{"name": "ro-bot", "user_id": 1, "scopes": "tickets:ro,kb:ro,mcp:ro"}'
# -d '{"name": "writer", "user_id": 1, "scopes": "tickets:rw,customers:ro"}'
# Null/empty scopes = unrestricted (full agent privileges of the bound user).
# Legacy tokens still work: read | write | mcp | *

curl -b cookies.txt "$TIQORA_URL/api/v1/admin/api-keys"
curl -b cookies.txt -X PATCH "$TIQORA_URL/api/v1/admin/api-keys/42" \
  -H 'Content-Type: application/json' -d '{"valid": false}'
curl -b cookies.txt -X DELETE "$TIQORA_URL/api/v1/admin/api-keys/42"

# CLI (same lifecycle against the configured database)
tiqora api-key create --user 1 --name ci-bot
tiqora api-key list
tiqora api-key revoke 42
tiqora api-key delete 42
```

Use the key (or, for quick testing, a session token) as a bearer token:

```sh
curl "$TIQORA_URL/api/v1/tickets" \
  -H "Authorization: Bearer $TIQORA_API_KEY"
```

**Discover enabled auth methods** (what the login page should offer):

```sh
curl "$TIQORA_URL/api/v1/auth/methods"
# {"password": true, "oidc": false, "spnego": false, "ldap": false,
#  "webauthn": false, "portal_enabled": true}
```

`POST /api/v1/auth/logout` clears the session. If TOTP 2FA is enrolled for
the user, `login` returns `{"pending_2fa": true}` and the flow continues at
`POST /api/v1/auth/totp/verify` with the 6-digit code, before a full session
cookie is issued.

## Queues

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/queues"
```

Returns the queue tree the caller has at least `ro` permission on (group-based,
mirroring Znuny's `group_user`/`role_user` → `group_role` permission model).

## Tickets

**List** (paginated, filterable):

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets?queue_id=3&state_type=open&limit=50&offset=0&sort=age&order=desc"
```

Response shape: `{"items": [...], "total": N}`. `limit` is 1–200 (default
50). Filters (all optional, combined with AND):

| Param | Meaning |
|---|---|
| `queue_id`, `state_id`, `state_type`, `owner_id`, `responsible_id`, `service_id`, `customer_id` | Exact match on the ticket field. |
| `locked` | `true` = `lock`/`tmp_lock` only; `false` = unlocked only. |
| `watcher_user_id` | Tickets watched by this agent. |
| `unassigned` | `true` = still owned by the root account (user id 1); `false` = owned by a real agent. |
| `escalated` | `true` = any SLA escalation time already passed. |
| `escalating_within` | Seconds: an escalation time is due before now + this window (overdue included). |
| `ai_escalated` | `true` = the AI handed the ticket to a human and nobody has taken over yet. |
| `channel` | Repeatable: `email`, `telegram`, `webchat` (any of them matches). |
| `include_archived` | Also list archived tickets — admins only, ignored otherwise. |

`sort`: `age` (default), `created`, `changed`, `tn`, `title`, `priority`,
`activity` (newest article), `deadline` (nearest SLA deadline); `order`:
`asc`/`desc`. A streaming, unpaginated CSV export with the same filters and
sort (no `limit`/`offset`) is available at `GET /api/v1/tickets/export.csv`.

**Get one ticket** (fields, no articles):

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711"
```

**Create**:

```sh
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Cannot log in",
    "queue_id": 2,
    "state_id": 1,
    "priority_id": 3,
    "owner_id": 1
  }'
# -> {"ticket_id": 4712}
```

`state_id`, `priority_id`, `queue_id`, `owner_id` reference the corresponding
admin resources (`GET /api/v1/admin/states`, `/priorities`, `/queues`,
`/users`). `dynamic_fields` accepts `{"<FieldName>": ["value", ...]}`.

**Update — one endpoint for every field mutation.** `PATCH
/api/v1/tickets/{id}` takes a sparse body; only the keys you send are
applied, each as its own permission-checked, history-logged operation, all
inside one transaction:

```sh
# Move to a different queue
curl -b cookies.txt -X PATCH "$TIQORA_URL/api/v1/tickets/4711" \
  -H 'Content-Type: application/json' -d '{"queue_id": 5}'

# Change state (with an optional pending_time for pending states)
curl -b cookies.txt -X PATCH "$TIQORA_URL/api/v1/tickets/4711" \
  -H 'Content-Type: application/json' -d '{"state_id": 4}'

# Change priority
curl -b cookies.txt -X PATCH "$TIQORA_URL/api/v1/tickets/4711" \
  -H 'Content-Type: application/json' -d '{"priority_id": 5}'

# Reassign owner
curl -b cookies.txt -X PATCH "$TIQORA_URL/api/v1/tickets/4711" \
  -H 'Content-Type: application/json' -d '{"owner_id": 7}'

# Lock / unlock, archive, watch, dynamic field, title, customer — all via
# the same PATCH body shape: {"lock": "lock"}, {"archive": true},
# {"watcher_user_id": 7}, {"field_name": "Category", "field_values": ["billing"]},
# {"title": "..."}, {"customer_id": "...", "customer_user_id": "..."}.
```

Multiple keys can be combined in a single PATCH call (e.g. move queue *and*
change state at once).

**Merge**: `POST /api/v1/tickets/{ticket_id}/merge` with
`{"main_ticket_id": <target>}` merges `ticket_id` into `main_ticket_id`.
Requires `rw` permission on both tickets' queues.

## Articles, attachments, body

```sh
# List article summaries for a ticket
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/articles"

# Full body of one article
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/articles/9001/body"

# Attachment metadata + download
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/articles/9001/attachments"
curl -b cookies.txt -OJ "$TIQORA_URL/api/v1/tickets/4711/articles/9001/attachments/1"

# Post a customer-visible reply
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/articles" \
  -H 'Content-Type: application/json' \
  -d '{
    "sender_type": "agent",
    "is_visible_for_customer": true,
    "subject": "Re: Cannot log in",
    "body": "Please try resetting your password.",
    "channel": "note"
  }'

# Post an internal-only note
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/articles" \
  -H 'Content-Type: application/json' \
  -d '{"is_visible_for_customer": false, "subject": "Internal", "body": "Escalating to L2."}'
```

`GET /api/v1/tickets/{id}/history` returns the full Znuny-compatible
`ticket_history` audit trail.

## Search

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/search?q=cannot+log+in&limit=20"
```

Meilisearch-backed full-text search across ticket titles and article bodies,
permission-filtered to the caller's readable queues.

## Dynamic fields

Dynamic field *values* are set via the ticket `PATCH` endpoint's
`field_name`/`field_values` keys (see above). Dynamic field *definitions*
(create/edit the fields themselves) are an admin resource: `GET/POST/PATCH
/api/v1/admin/dynamic-fields`.

## Compose-box drafts (autosave)

Per-ticket, per-action reply/note drafts — the compose box's own autosave, **not**
the AI subsystem (see "AI assist" below):

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/drafts"

curl -b cookies.txt -X PUT "$TIQORA_URL/api/v1/tickets/4711/drafts/reply" \
  -H 'Content-Type: application/json' \
  -d '{"action": "reply", "content": "{\"body\": \"Draft text...\"}"}'

curl -b cookies.txt -X DELETE "$TIQORA_URL/api/v1/tickets/4711/drafts/reply"
```

## AI assist (per ticket)

Draft replies and summaries from the built-in AI subsystem (gated by the queue's
AI policy + ACL; see [`../ai-integration.md`](../ai-integration.md) §5). AI drafts
are their own entity, distinct from the compose-box autosave above.

```sh
# Current AI state for a ticket (summary + open drafts + what's enabled)
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/ai"

# Generate an AI draft reply (agent reviews/accepts before anything is sent)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/draft"

# (Re)generate the stored ticket summary; optional body {"detail": "standard"|"detailed"}
# (omitted = the queue policy's summary_detail)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/summarize" \
  -H 'Content-Type: application/json' -d '{"detail": "detailed"}'

# One-off summary along your own instruction (returned only, never stored)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/summarize/custom" \
  -H 'Content-Type: application/json' \
  -d '{"instruction": "For the housing office, with a timeline"}'

# Discard an AI draft
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/drafts/42/discard"

# Accept a pending AI triage proposal (id from GET …/ai → triage.id);
# both halves default to true
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/triage/7/accept" \
  -H 'Content-Type: application/json' -d '{"queue": true, "customer": false}'

# Reject it (optional note; the row is kept for threshold calibration)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/triage/7/reject" \
  -H 'Content-Type: application/json' -d '{"note": "belongs to L2"}'

# Hand an AI-escalated ticket back to the AI without a customer reply
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/resume"

# Tool trace / origin of an AI-written article
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/articles/9001/ai-origin"
```

The `POST` endpoints require `note` permission on the ticket's queue (the
`GET`s only read access; `ai-origin` returns 404 for an article without an
AI origin); draft and summaries additionally need the feature enabled on
the queue's AI policy (409 otherwise) and allowed by the AI ACL. Triage accept runs with the
caller's own permissions (403 if they may not move into the target queue)
and, like reject, is a no-op 204 once the proposal is no longer open.
`resume` clears the AI hand-off flag and leaves an internal note.

**Text refine** (composer "polish my text", not tied to a stored ticket
field; nothing is persisted):

```sh
# Is the refine button available here? (exactly one of ticket_id / queue_id)
curl -b cookies.txt "$TIQORA_URL/api/v1/ai/refine/availability?ticket_id=4711"

# Refine the agent's own text; quotes are context only and never returned
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/ai/refine" \
  -H 'Content-Type: application/json' \
  -d '{
    "ticket_id": 4711,
    "tone": "friendly",
    "segments": [
      {"kind": "quote", "text": "> Does the VPN work again?"},
      {"kind": "own", "text": "yes its fixed sinse this morning"}
    ]
  }'
# -> {"sections": [{"id": 1, "text": "Yes, it has been fixed since this morning."}]}
```

`tone` is `standard` (default), `formal`, `friendly` or `concise`. Use
`queue_id` (plus optional `customer_user_id`) instead of `ticket_id` from a
New-ticket form; that needs `create` permission on the queue, `ticket_id`
needs `note`. Total text is capped at 60,000 characters. Refine must be
enabled on the queue's AI policy (`enabled_refine`, 409 `refine_disabled`)
and allowed by the AI ACL (403, or 429 over the limit).

Admin-side AI configuration lives under `/api/v1/admin/ai/*` — see the
generated [OpenAPI schema](openapi.json) and
[`../ai-integration.md`](../ai-integration.md) §5:

| Path | Purpose |
|---|---|
| `/admin/ai/settings` | Operation mode, auto-reply kill-switch, disclosure default, global hourly cap, audit retention. |
| `/admin/ai/providers` (+ `/{id}/test`, `/{id}/duplicate`) | LLM providers, pricing, cost budgets. |
| `/admin/ai/mcp-clients` (+ `/{id}/discover`, `/{id}/tools/{tool_name}`) | External MCP tool sources and per-tool switches. |
| `/admin/ai/queue-policies` | Per-queue AI policy (autonomy, features, triage, PII masking, …). |
| `/admin/ai/queues/{policy_id}/prompt-parts` (+ `/reorder`, `/{part_id}`) | Ordered system-prompt fragments of a policy (path id = policy id). |
| `/admin/ai/escalation-test` | Dry-run escalation rules against a sample tool result (`POST`, nothing stored). |
| `/admin/ai/acl` | Per-user/group/role AI feature access and limits. |
| `/admin/ai/usage` | Usage rows with token totals and `cost_hint` (`queue_id`, `feature`, `from`, `to`, `page`, `page_size`). |
| `/admin/ai/triage`, `/admin/ai/triage/stats` | Triage decisions (`status_filter`, `source_queue_id`, `limit`) and accept rate per confidence bucket. |
| `/admin/ai/audit` (+ `/stats`, `/{entry_id}`, `/{entry_id}/reveal-pii`) | LLM request audit log; `reveal-pii` decrypts the entry's placeholder map (logged). |
| `/admin/ai/drafts/{id}`, `/admin/ai/summaries/{ticket_id}` | `DELETE` only — admin cleanup of a draft or a stored summary. |

## Knowledge base

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/kb/search?q=vpn+setup"
curl -b cookies.txt "$TIQORA_URL/api/v1/kb/articles/42"
```

Admin CRUD for categories/articles (draft → publish → versions) lives under
`/api/v1/kb/categories` and `/api/v1/kb/articles`.

## Admin CRUD overview

Every admin resource lives under `/api/v1/admin/*` and requires `rw` on the
group literally named `admin` (see [`compat.md`](compat.md) for the
permission model). Most follow the same list/get/create/update/delete
pattern; settings-style resources (subject config, outbound mail, channels,
daemons, system info) are `GET`/`PUT` or read-only:

| Resource | Path |
|---|---|
| Users | `/api/v1/admin/users` |
| Groups | `/api/v1/admin/groups` |
| Roles | `/api/v1/admin/roles` |
| Queues | `/api/v1/admin/queues` |
| States | `/api/v1/admin/states` |
| Priorities | `/api/v1/admin/priorities` |
| Types / Services / SLAs | `/api/v1/admin/types`, `…/services`, `…/slas` |
| Customers | `/api/v1/admin/customer-users`, `…/customer-companies` |
| Customer-user ↔ company / group assignments | `…/customer-users/{login}/companies`, `…/customer-users/{login}/groups` |
| Customer fields (placeholder registry) | `/api/v1/admin/customer-fields` |
| Queue variables (`<TIQORA_QUEUE_X>` placeholders) | `/api/v1/admin/queue-variables` |
| Queue customer links (external CRM button) | `/api/v1/admin/queue-customer-links` |
| Templates (+ queue / attachment assignment) | `/api/v1/admin/templates`, `…/queues/{id}/templates`, `…/templates/{id}/attachments` |
| Signatures / Salutations | `/api/v1/admin/signatures`, `…/salutations` |
| Standard attachments | `/api/v1/admin/attachments` |
| Auto-responses (+ queue assignment) | `/api/v1/admin/auto-responses`, `…/queues/{id}/auto-responses` |
| Dynamic fields | `/api/v1/admin/dynamic-fields` |
| Ticket ACL | `/api/v1/admin/acl` (YAML match/change; runtime via field-options) |
| Ticket attribute relations | `/api/v1/admin/ticket-attribute-relations` (CSV) |
| Postmaster filters | `/api/v1/admin/postmaster-filters` |
| GenericAgent jobs | `/api/v1/admin/generic-agent-jobs` |
| Notification events | `/api/v1/admin/notification-events` |
| System addresses | `/api/v1/admin/system-addresses` |
| Subject hook / format | `/api/v1/admin/subject-config` (`GET`/`PUT`) |
| Incoming mail accounts (IMAP/POP) | `/api/v1/admin/mail-accounts` |
| Outbound SMTP settings (+ `/test`) | `/api/v1/admin/mail/outbound` |
| Mail communication log | `/api/v1/admin/mail/log` (read-only) |
| OAuth2 mail token configs | `/api/v1/admin/oauth2-token-configs` (+ `/{id}/authorize-url`, `/{id}/refresh`) |
| PGP / S/MIME keys | `/api/v1/admin/crypto-keys` (list, `/pgp-import`, `/smime-register`) |
| Agent SSO eligibility / 2FA | `/api/v1/admin/auth-config` (+ `/global`, `/{user_id}`, `/{user_id}/reset-2fa`) |
| API keys | `/api/v1/admin/api-keys` |
| Webhooks | `/api/v1/admin/webhooks` |
| Channels (SMS/WhatsApp/Telegram/phone config) | `/api/v1/admin/channels` |
| GDPR erasure jobs | `/api/v1/admin/gdpr/preview`, `…/jobs` (+ rollback, backup download/purge) |
| Background daemons (enable/interval + tick status) | `/api/v1/admin/daemons` (`GET`, `PUT /{slug}`) |
| System info (build, daemons, datastores, host) | `/api/v1/admin/system` (read-only) |
| Reference lists | `/api/v1/admin/state-types`, `…/follow-up-possible` (read-only) |
| AI subsystem | `/api/v1/admin/ai/*` (see "AI assist" above) |

Agent ticket pickers use `GET /api/v1/reference/…` and
`GET /api/v1/tickets/{id}/field-options` (TicketACL + attribute relations).
See `openapi.json` for the exact field set of each resource.

## Stats

Dashboard/reporting endpoints, each with a `.csv` streaming export variant:

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/volume"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/open-snapshot"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/sla"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/agent-workload"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/backlog"
```

## Webhooks (outbound, AI/automation integration)

Configured via `/api/v1/admin/webhooks`; delivery payload shape, HMAC
signing, and retry semantics are documented in
[`../ai-integration.md`](../ai-integration.md#1-webhook-payload-schema-versioned-envelope).

## Realtime events (SSE)

```sh
curl -b cookies.txt -N "$TIQORA_URL/api/v1/events/stream"
```

Server-Sent Events stream of ticket change notifications, used by the UI to
drive live invalidation. Also see `GET/PUT /api/v1/tickets/{id}/presence`
for the "who's viewing this ticket" indicator. A long-lived idle connection
sends a `: heartbeat` comment every 25s — reverse proxies must not buffer or
time out this connection early (see
[`../deploy/docker-compose.md`](../deploy/docker-compose.md) for the nginx
settings this requires).

## Customer lookup

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/customers/jdoe"
```

## Customer portal API (`/api/portal`)

Separate session (`POST /api/portal/auth/login`, `GET /api/portal/auth/me`),
scoped to a `customer_user` rather than an agent. Ticket endpoints mirror a
restricted subset of the agent API: `GET/POST /api/portal/tickets`, `GET
/api/portal/tickets/{id}`, `GET .../articles`, `POST
.../tickets/{id}/reply`, plus `/api/portal/kb/*` for the customer-facing
knowledge base and `/api/portal/tickets/{id}/attachments` for uploads.
