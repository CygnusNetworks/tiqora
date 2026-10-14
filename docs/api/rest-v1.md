# REST v1 guided reference (`/api/v1`)

This is a task-oriented "how to actually use it" guide with curl examples.
For exhaustive field-level detail (every request/response schema, every
optional parameter), load [`openapi.json`](openapi.json) into Swagger UI /
Redoc / Postman, or browse `GET /docs` on a running instance.

The sections follow the work you do with the API: authentication, tickets and
articles, search, ticket queries and saved searches, AI assist, the knowledge
base, background jobs, reports and report schedules, dashboards and
wallboards, agent processes, ITSM and CMDB, the admin resources, web chat, and
the realtime, customer and SSO endpoints. The public surfaces that are not
under `/api/v1` (SCIM, the visitor chat API, plugin routes) are summarised in
the [API overview](README.md); which API-key area covers which path is listed
there too.

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
# -d '{"name": "reporter", "user_id": 1, "scopes": "stats:rw,tickets:ro"}'
# -d '{"name": "cmdb-sync", "user_id": 1, "scopes": "cmdb:rw,tickets:ro"}'
# Null/empty scopes = unrestricted (full agent privileges of the bound user).
# Areas: tickets customers kb calendar stats ai process channels events agents
#        cmdb admin mcp compat plugins (see the API overview for the path map).
# Legacy tokens still work: read | write | mcp | *  (read/write do not cover
# mcp, compat or plugins)

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

Use the key as a bearer token. Only `tiqora_…` API keys are accepted in the
`Authorization` header; a session token belongs in the session cookie:

```sh
curl "$TIQORA_URL/api/v1/tickets" \
  -H "Authorization: Bearer $TIQORA_API_KEY"
```

**Discover enabled auth methods** (what the login page should offer):

```sh
curl "$TIQORA_URL/api/v1/auth/methods"
# {"password": true, "oidc": false, "spnego": false, "ldap": false,
#  "webauthn": false, "portal_enabled": false}   (true only with TIQORA_PORTAL_ENABLED)
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
| `channel` | Repeatable: `email`, `telegram`, `webchat`, `phone` (any of them matches; `phone` = first article is a phone call). |
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

**Phone ticket** (Znuny AgentTicketPhone): `phone_call` logs the call as the
first article in the same transaction; `pending_time` is required for a
pending `state_id` then; `send_auto_response` sends the queue's "auto reply"
to the customer after an inbound call (SysConfig `AutoResponseForWebTickets`,
loop protection as in the email pipeline):

```sh
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Drucker streikt", "queue_id": 2, "state_id": 4, "priority_id": 3,
    "owner_id": 5, "customer_id": "ACME", "customer_user_id": "jane.doe",
    "phone_call": {"direction": "inbound", "body": "Papierstau im 2. OG",
                   "time_unit": 4, "caller_number": "+49 228 5550101"},
    "send_auto_response": true
  }'
```

**Which queue a new ticket starts in** (the New-ticket form's preselection;
`screen` is `phone` or `email`):

```sh
# With a customer: queue of their newest ticket → their company's newest
# ticket → the screen's QueueDefault → first non-intake queue
curl -b cookies.txt "$TIQORA_URL/api/v1/customers/jane.doe/suggested-queue?screen=phone"
# -> {"queue_id": 4, "source": "customer"}   (customer|company|default|fallback)

# Before (or without) a customer
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/new/default-queue?screen=email"
```

Only queues the caller may `create` in are returned; Znuny's intake queues
(`Junk`, `Raw`, `Postmaster` and their sub-queues) never count as history
and are never the default. Both fields are `null` when the caller may create
tickets nowhere.

**Log a phone call on a ticket** (Znuny AgentTicketPhoneInbound/Outbound —
article, time accounting, dynamic fields, attachments and next state in one
transaction; needs `rw`; outbound takes the RequiredLock and answers 409 with
`detail.locked_by_name` when another agent holds it):

```sh
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/phone-calls" \
  -H 'Content-Type: application/json' \
  -d '{"direction": "outbound", "subject": "Anruf an Jane Doe",
       "body": "Techniker kommt Dienstag", "time_unit": 6,
       "state_id": 6, "pending_time": "2026-10-01T07:00:00Z"}'
# -> {"article_id": 99, "ticket_id": 4711, "time_accounting_id": 12, "locked": true}
```

Details: [`../channels.md`](../channels.md) (Phone / CTI).

**Incoming-call popup (CTI).** The PBX posts call events (shared secret,
JSON or form-encoded); agents with that extension get an SSE `call_event`:

```sh
curl -X POST "$TIQORA_URL/api/v1/channels/phone/events" \
  -H 'X-Tiqora-Phone-Secret: change-me' -H 'Content-Type: application/json' \
  -d '{"event": "ringing", "call_id": "1727600000.42",
       "caller_number": "+49 228 5550101", "extension": "100"}'
# -> 202 {"accepted": true, "delivered_to": 1}   (0 = unknown extension, ignored)

# The agent's running calls + those ended <= 15 min ago (popup restore)
curl -b cookies.txt "$TIQORA_URL/api/v1/phone/calls/active"
# -> [{"call_id": "1727600000.42", "state": "answered", "number": "+49 228 5550101",
#      "extension": "100", "direction": "inbound", "user_ids": [7],
#      "ringing_at": "…", "answered_at": "…", "ended_at": null}]

# Hide a card in all of the agent's tabs
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/phone/calls/1727600000.42/dismiss"   # 204
```

The agent ↔ extension mapping is admin-managed: `phone_extension`
(comma separated) on `GET/PATCH /api/v1/admin/users/{id}`.

API keys reach `/api/v1/phone/*` with the `events` scope.

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

## Ticket queries and saved searches

Besides the parameter-based `GET /tickets`, there is a structured **filter
language**: a JSON document `{"version": 1, "where": <node>}`. A node is
`{"all": [...]}`, `{"any": [...]}`, `{"not": <node>}` or a condition
`{"field": "...", "op": "...", "value": ...}`. The field catalogue (keys, types,
allowed operators, option sources) is `GET /api/v1/ticket-filters/fields`;
dynamic fields are addressed as `df.<Name>`. Operators depend on the field
type (`eq`, `neq`, `in`, `not_in`, `contains`, `starts_with`, `is_empty`,
`is_not_empty`, `lt`, `lte`, `gt`, `gte`, `between`, `before`, `after`,
`within_last`, `older_than`, `is_true`, `is_false`); relative times are
`{"amount": 7, "unit": "day"}` with `minute`, `hour`, `day` or `week`. A filter
may nest up to 6 levels and hold 100 conditions. It can only **narrow**: the
result is always limited to the queues the caller can read first.

```sh
# Tickets in queues 3 or 5 that changed in the last 7 days and are not in state 2
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/query" \
  -H 'Content-Type: application/json' \
  -d '{
    "filter": {"version": 1, "where": {"all": [
      {"field": "queue_id", "op": "in", "value": [3, 5]},
      {"field": "changed",  "op": "within_last", "value": {"amount": 7, "unit": "day"}},
      {"not": {"field": "state_id", "op": "eq", "value": 2}}
    ]}},
    "sort": "changed", "order": "desc", "limit": 50, "offset": 0
  }'
# -> {"items": [...], "total": N}   (same page shape as GET /tickets)

# Only count (permission-scoped), e.g. for a badge
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/ticket-filters/count" \
  -H 'Content-Type: application/json' \
  -d '{"filter": {"version": 1, "where": {"field": "title", "op": "contains", "value": "VPN"}}}'
# -> {"count": 17}
```

`POST /tickets/query` takes `limit` 1-200 (default 50), `sort` like
`GET /tickets`, and `include_archived` (admins only, ignored otherwise). An
invalid filter answers `422 {"detail": {"message": "...", "path": "where.all[1]"}}`
with the JSON path of the offending node; a query that runs too long answers
`504`. Because it is a `POST`, an API key needs `tickets:rw`.

**Saved searches** ("Gespeicherte Suchen" in the agent sidebar) store such a
filter under a name. There is no "run" endpoint: take `criteria` from the saved
search and send it to `POST /tickets/query`.

```sh
# Save from criteria (or send "list_params" instead: the queue list's own
# parameters, which is what "Ansicht speichern" does; exactly one of the two)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/saved-searches" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Open VPN tickets",
       "criteria": {"version": 1, "where": {"field": "title", "op": "contains", "value": "VPN"}},
       "sort_by": "changed", "sort_order": "desc",
       "group_ids": [], "show_in_sidebar": true, "show_count": true}'

# Sidebar counts for the entries that show one (poll at most once a minute)
curl -b cookies.txt "$TIQORA_URL/api/v1/saved-searches/counts"
```

| Method and path | Purpose |
|---|---|
| `GET /saved-searches` | Own searches and those shared with a group the caller holds `ro` on. |
| `POST /saved-searches` | Create; `group_ids` shares with groups (needs access to those groups, else `422`). `show_in_sidebar` / `show_count` are the caller's own display settings and are only read on create. |
| `GET /saved-searches/{id}` | One search (`criteria_error` is set when a stored criteria can no longer be read). |
| `PUT /saved-searches/{id}` | Full replace by the owner or an admin; `version` is the optimistic lock (`409` with `current_version`). |
| `PUT /saved-searches/{id}/prefs` | The caller's own sidebar entry: `show_in_sidebar`, `show_count`, `position`. |
| `DELETE /saved-searches/{id}` | Delete (`204`). |
| `GET /saved-searches/counts` | Counts for the first 15 sidebar entries with a count, cached 30 s per viewer, in the viewer's own scope. |
| `GET /saved-searches/import/znuny` | Preview: the caller's own Znuny search profiles as they would be imported (read-only on Znuny's tables). |
| `POST /saved-searches/import/znuny` | `{"names": [...]}` imports them as private searches; a re-import updates in place. |

All of it belongs to the API-key area `tickets`. Product behaviour (sidebar,
sharing, Znuny profile mapping): [`../compatibility.md`](../compatibility.md#search-profiles).

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

# Pause / resume all automatic AI actions on this ticket (204, idempotent)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/pause"
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/ai/unpause"

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
`pause` stops auto-reply, triage and auto-summary on the ticket until
`unpause` (Manual Assist, refine and manual summaries stay available); each
real state change leaves one internal note, `GET …/ai` reports
`ai_paused_at` / `ai_paused_by_name`. Messages that arrived during the pause
are not answered retroactively.

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

`tone` is `standard` (default), `formal`, `friendly` or `concise`.
`mode: "call_note"` (with `language`, e.g. `"de"`) turns phone-call notes
into an internal note under *Anliegen / Vereinbart / Nächste Schritte*
(headings in that language) instead of polishing a message; tone is ignored
there. Use
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
| `/admin/ai/providers` (+ `/{id}/test`, `/{id}/duplicate`, `/{id}/remote-models`) | LLM providers (access, currency, cost budgets). |
| `/admin/ai/models` (+ `/{id}/test`), `/admin/ai/profiles`, `/admin/ai/task-defaults` | Model catalog (model@provider, capabilities, prices), fallback profiles, global profile per task; per-queue overrides are `task_profiles` on the queue policy. |
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

## Background jobs

Work that can take longer than a request (the reverse proxy cuts requests at
90 s) runs as a **job** in the worker. A feature endpoint validates the request,
queues the job and answers `202 Accepted` with `{"job_id": 17, "status":
"queued"}` (`"running"` when it joined an identical job that is already
running). There is deliberately no generic "create job" endpoint; the jobs API
only reads, cancels and downloads.

```sh
# 1. Start something that is a job: the ticket CSV export
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/export-jobs" \
  -H 'Content-Type: application/json' \
  -d '{"params": {"queue_id": 3, "state_type": "open"}, "sort": "age", "order": "desc"}'
# -> 202 {"job_id": 17, "status": "queued"}

# 2. Poll (the UI polls every second for 30 s, then every 3 s)
curl -b cookies.txt "$TIQORA_URL/api/v1/jobs/17"
# -> {"id": 17, "kind": "core.ticket_export", "status": "succeeded",
#     "progress_done": 812, "progress_total": 812, "result": {...}, "error": null,
#     "files": [{"id": 5, "name": "tickets.csv", "content_type": "text/csv", "size_bytes": 91244}], ...}

# 3. Download the file (only the creator may; administrators get 404 here)
curl -b cookies.txt -OJ "$TIQORA_URL/api/v1/jobs/17/files/5"
```

| Method and path | Purpose |
|---|---|
| `GET /jobs` | Your own jobs, newest first (`limit` 1-200, default 50). `all=true` lists everybody's, admins only (`403` otherwise). |
| `GET /jobs/{id}` | One job; `404` for jobs of other users unless you are an admin. |
| `POST /jobs/{id}/cancel` | Request cancellation (a queued job is cancelled at once, a running one stops at its next check); `409` when it already finished. |
| `GET /jobs/{id}/files/{file_id}` | Download a result file (`Content-Disposition: attachment`). Creator only: files hold data selected with the creator's permissions. |

`status` is one of `queued`, `running`, `succeeded`, `failed`, `cancelled`.
`progress_done` / `progress_total` / `progress_message` report progress
(`progress_total` can be `null`), `error` is a plain German message (details
are in the worker log only). `result` is a JSON object; for jobs whose result
holds data selected with the creator's permissions (report runs) it is shown to
the creator only. `title_key` is the frontend text key of the job kind.

**Scope.** `/api/v1/jobs` is reachable with the API-key area `tickets` **or**
`stats`, so the key that started a job can also poll it. A key with only `stats`
sees only report jobs (`core.report_run`, `core.report_export`,
`core.report_backfill`); every other kind answers `403`, and `all=true` is
refused. A key with only `cmdb` cannot poll at all and needs `tickets:ro` as well.

**Kinds** (`kind`), by the feature that starts them:

| Kind | Started by |
|---|---|
| `core.ticket_export` | `POST /tickets/export-jobs` (the same export asked twice while one is pending joins it; at most 2 active exports per user, then `429`) |
| `core.report_run`, `core.report_export` | `POST /reports/run` (large reports), `POST /reports/export`, dashboard report widgets; at most 3 active report jobs per user (`429`) |
| `core.report_delivery` | A report schedule, or "Jetzt senden" |
| `core.report_backfill` | `POST /admin/reporting/backfill` |
| `core.cmdb_import`, `core.cmdb_export`, `core.cmdb_bulk` | `POST /cmdb/import-jobs`, `/export-jobs`, `/bulk-jobs` (3 active of each per user) |
| `core.cmdb_propagation`, `core.cmdb_reindex`, `core.cmdb_archive` | CMDB saves, class changes, the nightly archive; admin recompute |
| `core.itsm_linked_articles` | `POST /tickets/{id}/linked-articles`, "close the linked incidents" |
| `core.itsm_import`, `core.itsm_switch` | `POST /admin/itsm/import/runs` and `/switch` |
| `core.plugin_install`, `core.plugin_uninstall`, `core.plugin_fetch`, `core.plugin_repository_refresh` | The plugin admin API |
| `core.gi_invoke`, `core.gi_invoke_sync`, `gi_mail` | GenericInterface requester calls and outgoing GI mails (internal; not started through the jobs API) |
| `<plugin id>.<name>` | Jobs registered by plugins |

Operator settings (the generic admin settings screen `/admin/settings/jobs`, API `/api/v1/admin/settings-sections`): `max_concurrency`
(parallel jobs per worker in the default lane, default 2, 1 to 8) and
`retention_days` (finished jobs and their files are purged after this many
days, default 14, 1 to 365). The worker polls for new jobs every
`TIQORA_JOBS_POLL_INTERVAL_SECONDS` (default 2). A running job whose worker
stopped sending a heartbeat for 180 s is reaped. Details:
[`../architecture.md`](../architecture.md#shared-foundations).

## Reports

The reporting engine runs **built-in reports** (25 in the catalogue, key
`builtin.<name>`, for example `builtin.volume` or `builtin.sla_compliance`; plugins
add `plugin.<plugin id>.<name>`), **saved reports** (key = the decimal id) and
**inline definitions** (a `ReportDefinition` sent with the request). Everything
is evaluated in the caller's scope: only tickets in queues the caller can read
count, and no filter can widen that. Who may use reports at all is the admin
setting `reporting.access` (`all_agents`, the default, or `stats_group`: admins
and agents with `ro` on the Znuny group `stats`). While the report data is
built for the first time, `facts.ready` is `false` and the numbers are
incomplete (see [`../deployment.md`](../deployment.md#reporting-data)).

**Run a report.** `POST /api/v1/reports/run` takes exactly one of `report_key`
or `definition`, plus optional overrides, and answers either `200 {"result": …}`
or `202 {"job": {"job_id": …, "status": …}}` when the report is too large to run
inside the request (estimated above `reporting.inline_row_threshold`, default
200,000 rows, or an event-based report over a window longer than 366 days) or
when `force_job` is set. In the 202 case poll
`GET /jobs/{job_id}`: the job `result` is the report result.

```sh
# A built-in report with a runtime override of the window and the grain
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/reports/run" \
  -H 'Content-Type: application/json' \
  -d '{"report_key": "builtin.volume",
       "time_range": {"kind": "relative", "unit": "week", "count": 12, "include_current": true},
       "grain": "week", "compare_previous": true}'
# -> 200 {"result": {"columns": [...], "rows": [[...]], "totals": {...}, "previous": {...},
#                    "row_count": 24, "truncated": false, "time_window": {...},
#                    "cached": false, "facts": {"ready": true, ...}}}

# An inline definition: closed tickets per queue, last 30 days
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/reports/run" \
  -H 'Content-Type: application/json' \
  -d '{"definition": {
         "schema_version": 1, "source": "ticket_events",
         "dimensions": [{"key": "occurred", "grain": "day"}, {"key": "kind"}],
         "measures": [{"key": "events"}],
         "visualization": {"type": "line", "series_dimension": 1},
         "time_range": {"kind": "relative", "unit": "day", "count": 30, "include_current": true}}}'
```

Run overrides: `time_range` (relative `{"kind": "relative", "unit", "count",
"include_current"}` with unit `day`/`week`/`month`/`quarter`/`year`, or absolute
`{"kind": "absolute", "start": "2026-09-01", "end": "2026-09-30"}`, at most 3660
days), `grain` (`day`, `week`, `month`, `quarter`, `year`), `time_zone`,
`parameters` (values for the report's runtime parameters, "änderbar zur
Laufzeit" in Znuny terms), `dimension_choices`, `compare_previous` (adds the
previous period), `no_cache` (skip the Redis result cache, `reporting.cache_seconds`
default 300) and `force_job`. Unknown fields are rejected. Errors: `422
{"detail": {"message", "path"}}` for an invalid request or definition, `429`
when the caller already has 3 active report jobs, `504` when the query hit
`reporting.query_timeout_seconds` (default 60).

**Export.** `POST /api/v1/reports/export` takes the same body plus `format`
(`xlsx` default, `csv`, `pdf`), `title`, `locale` (`de`; anything else is
English) and the frontend-translated `column_labels` / `value_labels`. It
**always** answers `202` with a job; the file is `job.files[0]` (download via
`/jobs/{id}/files/{file_id}`). The request is checked immediately (`422`), the
worker checks access and scope again when it runs.

```sh
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/reports/export" \
  -H 'Content-Type: application/json' \
  -d '{"report_key": "builtin.sla_compliance", "format": "pdf", "title": "SLA", "locale": "de"}'
# -> 202 {"job_id": 31, "status": "queued"}
```

| Method and path | Purpose |
|---|---|
| `GET /reports` | The catalogue: built-ins first, then the saved reports the caller may open (`all=true`: also others' private reports, admins only), plus the `facts` status. |
| `GET /reports/{key}` | One report with its `definition` (`builtin.<name>` or a saved id). `404` for an unknown key, `410` for a deleted saved report. |
| `POST /reports/run`, `POST /reports/export` | See above. |
| `POST /reports` | Save a report: `name`, `description`, `definition`, `visibility` (`private`, `groups`, `all`), `group_ids`. Sharing needs `rw` on the Znuny group `stats` or admin. `201`. |
| `PUT /reports/{id}` | Replace a saved report completely; `version` is the optimistic lock (`409` with `current_version`). |
| `DELETE /reports/{id}` | Soft delete (`204`; owner always, admins any; purged after 90 days). |
| `GET /reports/{key}/file?format=json\|yaml` | The report as a downloadable report file. |
| `POST /reports/import-file` | `{"content": "<json or yaml text>"}`: parse and validate a report file, saves nothing. |
| `GET /reporting/sources` | Sources with their dimensions, measures and filter fields (what the builder offers). |
| `GET /reporting/status` | Whether the report data is ready (`ready`, `as_of`, `backfill_status`, `backfill_progress`). |

**Znuny statistics import** (maps Znuny `Stats` to reports or saved searches;
re-imports update by the Znuny stat id; see
[`../compatibility.md`](../compatibility.md#importing-znuny-statistics)):

| Method and path | Purpose |
|---|---|
| `GET /reports/import/znuny` | Preview of the Znuny statistics the caller may run, mapped, with every dropped or approximated element listed. Saves nothing. |
| `POST /reports/import/znuny/file` | `{"content_base64": "..."}`: the same preview for an exported Znuny statistics XML file. |
| `POST /reports/import/znuny/apply` | `{"stat_ids": [...], "file_content_base64": null}` saves the chosen ones as private reports / saved searches. |

API-key area: `stats` (`/reports`, `/reporting`, `/report-schedules`,
`/report-runs`); running or exporting is a `POST`, so it needs `stats:rw`.

The older fixed-report endpoints (`/api/v1/stats/*`) are described under
[Stats](#stats). The product guide for everything in this section (builder,
sharing, scheduled delivery, limits) is [`../reporting.md`](../reporting.md);
its API chapter lists the same routes.

## Report schedules

A schedule ("Zeitplan" in the report area) delivers one or more reports as
PDF, XLSX or CSV by e-mail on a recurrence. It runs with the **owner's**
permissions (the data is selected in the owner's scope), whoever the
recipients are.

```sh
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/report-schedules" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Weekly SLA",
    "items": [{"report_key": "builtin.sla_compliance",
               "overrides": {"time_range": {"kind": "relative", "unit": "week", "count": 1}}}],
    "format": "pdf",
    "recurrence": {"kind": "weekly", "time": "07:30", "weekdays": [1]},
    "time_zone": "Europe/Berlin", "language": "de",
    "subject": "SLA report", "body": "", "only_if_nonempty": false, "active": true,
    "recipients": {"user_ids": [7], "group_ids": [], "role_ids": [], "emails": []}
  }'

# "Jetzt senden" (send now). owner_only=true (default) is the test send "an mich"
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/report-schedules/5/run" \
  -H 'Content-Type: application/json' -d '{"owner_only": true}'
# -> 202 {"run_id": 88, "job": {"job_id": 90, "status": "queued"}}
```

`recurrence.kind` is `daily` (optional `workdays_only`), `weekly`
(`weekdays` 1 = Monday to 7 = Sunday), `monthly` (`day_of_month`, clamped to the
month's last day) or `yearly` (`month` and `day_of_month`); `time` is `HH:MM`
in the schedule's `time_zone` (default: the owner's zone) and DST-safe.
`items` holds 1 to 10 reports, `format` is `pdf` (default), `xlsx` or `csv`,
`language` `de` or `en`. Recipients are agents, groups, roles and e-mail
addresses (up to 50 / 20 / 20 / 50); external addresses are accepted only when
the admin setting `reporting.schedule_external_recipients` is on (default off).
One mail goes to all recipients in Bcc. After 3 failed scheduled runs in a row
the schedule is paused and the owner is mailed.

| Method and path | Purpose |
|---|---|
| `GET /report-schedules` | Own schedules (`all=true`: everybody's, admins only). |
| `POST /report-schedules` | Create (`201`). |
| `GET /report-schedules/{id}`, `PUT`, `DELETE` | Read, replace (`PUT` carries the `version`), delete (`204`). |
| `POST /report-schedules/preview-recurrence` | The next five occurrences and a sentence in the requested language; computes only. |
| `POST /report-schedules/{id}/run` | "Jetzt senden": one delivery as a background job; one active manual run per schedule (`409`). |
| `GET /report-schedules/{id}/runs` | Run history, newest first (no e-mail addresses). |
| `GET /report-runs/{run_id}/files/{file_id}` | A generated file of a run (selected with the owner's permissions, so owner only). |

Run history and files are purged after `reporting.run_retention_days` (default
90). API-key area `stats`. Operator notes (PDF rendering, attachment size limit
`reporting.delivery_max_attachment_mb`):
[`../deployment.md`](../deployment.md#pdf-reports-and-scheduled-delivery).

## Dashboards and wallboards

Widget dashboards (product guide: [`../dashboards.md`](../dashboards.md), in
German) are stored per user. All routes need a session or an **unrestricted**
API key: `/api/v1/dashboards` and `/api/v1/dashboard-widget-types` belong to no
API-key area, so a key with scopes gets `403`.

```sh
# Tabs of the caller (personal, shared and role-template dashboards)
curl -b cookies.txt "$TIQORA_URL/api/v1/dashboards"

# Data of every widget in ONE request; widgets=a,b limits it. Each widget has a
# status: ok, pending (still computing, ask again), error or forbidden.
curl -b cookies.txt "$TIQORA_URL/api/v1/dashboards/12/data"
# -> {"widgets": {"w1": {"status": "ok", "data": {...}}, "w2": {"status": "pending"}},
#     "generated_at": "..."}

# Narrow the whole dashboard to two queues and the last 30 days
curl -b cookies.txt -X PATCH "$TIQORA_URL/api/v1/dashboards/12/filters" \
  -H 'Content-Type: application/json' \
  -d '{"filters": {"queue_ids": [3, 5], "time_range": "30d"}, "version": 4}'
```

| Method and path | Purpose |
|---|---|
| `GET /dashboards` | The caller's tabs (`kind` `personal`, `template` or `shared`, `locked`). |
| `POST /dashboards/ensure-start` | First visit: creates the start dashboard (copy of the first unlocked role template, else the built-in default). |
| `POST /dashboards` | New dashboard (`name`, `icon`, optional `copy_from`); at most 20 per user. |
| `PUT /dashboards/order` | `{"ids": [...]}` tab order. |
| `GET /dashboards/{id}` | The dashboard with its widgets, filters and (for template copies) a `template_update` hint. |
| `PUT /dashboards/{id}` | Save the whole dashboard (`name`, `icon`, `widgets` up to 40, `filters`, and the read `version`); `409` with `current_version` on a conflict. |
| `PATCH /dashboards/{id}/filters` | Dashboard-wide filters: `queue_ids` (up to 50) and `time_range` preset (`today`, `7d`, `30d`, `90d`, `this_month`, `last_month`). Widgets marked `pinned` ignore them. |
| `PATCH /dashboards/{id}/widgets/{widget_id}/settings` | Change one widget's settings (`settings`, `version`). |
| `PUT /dashboards/{id}/share` | `{"group_ids": [...]}` share with groups. |
| `GET`/`POST /dashboards/{id}/template-update` | Preview and apply (`{"action": "merge" or "dismiss", "version"}`) an update of the role template a copy came from. |
| `DELETE /dashboards/{id}` | Delete (`204`); needs edit rights on the dashboard, and the last remaining own dashboard cannot be deleted (`422`). |
| `GET /dashboards/{id}/data` | Data of all widgets (or `widgets=` ids). Each provider runs as the viewer, at most four at once, five seconds each. |
| `GET /dashboards/{id}/widgets/{widget_id}/data` | One widget. |
| `GET /dashboards/{id}/widgets/{widget_id}/tickets` | Drill-down: the tickets behind a tile or one chart point (`point` as a small JSON object, `offset`, `limit` up to 50, `sort`, `order`). |
| `POST /dashboards/{id}/widgets/{widget_id}/report-job` | Run a report widget's report in the background when it answered `deferred`; `202` job, poll `/jobs/{id}`. |
| `GET /dashboard-widget-types` | The 16 core widget types: `core.ticket-list`, `counter`, `queue-overview`, `escalations`, `reminders`, `recent-views`, `calendar-agenda`, `team-presence`, `notes`, `links`, `report`, `sla-gauge`, `backlog-aging`, `heatmap`, `chat-queue`, `ai-status` (`has_data` tells whether a server-side provider exists). |
| `POST /dashboard-widget-types/{key}/data` | Data for **unsaved** settings (the settings drawer's preview); writes nothing. |

Admin side (area `admin`): `GET/POST /admin/dashboard-templates` and
`PUT/DELETE /admin/dashboard-templates/{id}` manage role templates (`name`,
`icon`, `role_ids`, `locked`, `sort`); `GET/POST /admin/dashboard-kiosk-links`,
`POST /admin/dashboard-kiosk-links/{id}/revoke` and `DELETE
/admin/dashboard-kiosk-links/{id}` manage wallboard links.

### Kiosk (wallboard) data

The wallboard page of a **wallboard link** reads two unauthenticated routes. The
secret of the link travels in the `X-Tiqora-Kiosk` header (the page takes it from
the URL fragment, so it never reaches an access log); cookies and bearer tokens
are ignored.

```sh
# Create a link (admin). The secret ("token") is in this response only.
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/admin/dashboard-kiosk-links" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Service desk TV", "dashboard_ids": [12, 14], "rotate_seconds": 60,
       "show_details": false, "expires_in_days": 90}'

curl -H "X-Tiqora-Kiosk: $KIOSK_TOKEN" "$TIQORA_URL/api/v1/kiosk/board"
curl -H "X-Tiqora-Kiosk: $KIOSK_TOKEN" "$TIQORA_URL/api/v1/kiosk/dashboards/12/data"
```

`GET /kiosk/board` returns the link's dashboards in rotation order with the
widgets reduced to what rendering needs; `GET /kiosk/dashboards/{id}/data`
returns redacted widget data, computed as the link's creator and cached for 20
seconds. Every invalid, expired, revoked or unknown link (and a dashboard id not on
the link) answers the same `404`, every answer carries `Cache-Control: no-store`
and `Referrer-Policy: no-referrer`, misses are limited to 30 per 10 minutes and
all requests to 240 per minute per client address (`429` with `Retry-After`;
behind a proxy `trusted_proxies` decides the address). A link needs 1 to 10
dashboards, `rotate_seconds` 15 to 900 (default 60) and `expires_in_days` 1 to
365 (default 90). Without `show_details` ticket fields and error texts are cut
down. Operator notes: [`../deployment.md`](../deployment.md#wallboard-links).

## Processes (agent API)

Znuny-style business processes, run by agents inside a ticket (product guide:
[`../process-management.md`](../process-management.md)). The routes live under
`/api/v1/process` (API-key area `process`) and see only the **deployed**
revision of the process configuration; what an agent may start also depends on
the process state, the dialog's interface and Permission, and the ticket ACLs.

```sh
# Processes the agent may start
curl -b cookies.txt "$TIQORA_URL/api/v1/process/"

# The start dialog as a server-built form (visible fields in order, option lists)
curl -b cookies.txt "$TIQORA_URL/api/v1/process/Process-4f2a/start-form"

# Create a ticket from the start dialog; field_values are the dialog's fields
# (ids and field names here are placeholders: take them from the start-form)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/process/tickets" \
  -H 'Content-Type: application/json' \
  -d '{"process_entity_id": "Process-4f2a", "field_values": {"Title": "New laptop", "Queue": "Hardware"}}'
# -> 201 {"ticket_id": 4720, "ticket_number": "...", "activity_changed": false,
#         "unsupported_actions": [], "skipped_actions": [], "state": {...}}

# Submit the dialog of the ticket's current activity
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/process/ticket/4720/submit" \
  -H 'Content-Type: application/json' \
  -d '{"activity_dialog_entity_id": "ActivityDialog-9c1b", "field_values": {"Article": "Approved"}}'
```

| Method and path | Purpose |
|---|---|
| `GET /process/` | Startable processes. |
| `GET /process/{process_entity_id}` | One process with its activities and their dialogs (transition internals are not exposed). |
| `GET /process/{process_entity_id}/start-form`, `POST …/start-form/options` | The start dialog as a form; `options` refreshes dependent option lists for the values entered so far (for example Owner follows Queue). |
| `GET /process/activity-dialog/{id}` | One activity dialog definition. |
| `GET /process/ticket/{ticket_id}/state` | The ticket's current process and activity (`ro`). |
| `GET /process/ticket/{ticket_id}/path` | The activities it has been through, oldest first. |
| `GET /process/ticket/{ticket_id}/dialog/{id}/form`, `POST …/form/options` | A dialog of the current activity as a form (`403` when hidden for this agent). |
| `POST /process/tickets` | New process ticket from the start dialog (needs `create` on the resolved queue, `403`; process not startable `409`; mandatory fields missing `400`). |
| `POST /process/ticket/{ticket_id}/start` | Enrol an existing ticket in a process (needs `rw`). |
| `POST /process/ticket/{ticket_id}/submit` | Submit an activity dialog (needs `rw` as a coarse gate; the dialog's own Permission, interface and ACLs are enforced again). |

Create, start and submit answer what the transition did: `activity_changed`,
`unsupported_actions` (action modules Tiqora does not implement) and
`skipped_actions` (actions that did not run on this pass although the transition
went through, each with `module`, a stable `reason` such as `vetoed`, `itsm_off`
or `link_refused`, and a German `detail`), plus the fresh `state`.

**Designing and deploying processes** is an admin job and not part of this
agent API: `/api/v1/admin/process-designer/*` (catalogue, processes, entities
for activities, dialogs, transitions and actions, validation, Znuny-format YAML
`export`/`import`, deployed `revisions` with `diff` and `restore` as a draft) and
`POST /api/v1/admin/deploy` with `{"kind": "process"}` or `{"kind": "acl"}`
("Bereitstellen"). Deploy is refused with `409` while Znuny still runs without
the TiqoraSync add-on 1.2.0 or the setting "Prozesse und ACLs in Tiqora
bereitstellen (Znuny läuft nicht mehr)"; `GET /api/v1/admin/deploy-status` shows
the state and `GET /api/v1/admin/processes` the read-only list. See
[`../process-management.md`](../process-management.md#process-designer).

## ITSM

The ITSM core (incident, problem, major incident, services, priority matrix,
CI links) works in three modes, `off`, `znuny_readthrough` ("Mode A": Znuny
keeps the ITSM data and Tiqora reads it) and `tiqora` ("Mode B": Tiqora's own
CMDB). Product guide: [`../itsm.md`](../itsm.md) (German). Two route families:

- `/api/v1/itsm/*` (API-key area **`cmdb`**): status, priority matrix, the CI
  search and CI detail that work in either mode, and services.
- Ticket sub-resources under `/api/v1/tickets/{ticket_id}/...` (API-key area
  **`tickets`**): linked CIs, problem, major incident, linked-ticket notes.

The ITSM routes answer `409 {"detail": {"code": "itsm_off"}}` while the mode is
`off`. A few always answer so the UI can decide what to show: `GET /itsm/status`,
`GET /tickets/{id}/itsm`, `GET /tickets/{id}/link-graph` and
`GET /itsm/link-types` for the ticket-to-ticket pair.

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/itsm/status"
# -> {"mode": "tiqora", "source": "auto", "ci_available": true, "ci_read_only": false,
#     "ci_source": "tiqora", "unavailable_code": null, ...}

# Search CIs in either mode (class, depl = deployment state, inci = incident state are repeatable keys)
curl -b cookies.txt "$TIQORA_URL/api/v1/itsm/cis?q=mail&class=server&inci=incident&limit=25"

# Link a CI to a ticket ("tiqora:12" or "znuny:12"; idempotent, needs rw on the ticket)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/cis" \
  -H 'Content-Type: application/json' -d '{"ci": "tiqora:12"}'

# Declare a major incident (idempotent) and post a status update to the linked tickets
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/major-incident" \
  -H 'Content-Type: application/json' -d '{"summary": "Mail outage"}'
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/tickets/4711/major-incident/updates" \
  -H 'Content-Type: application/json' \
  -d '{"mode": "note", "body_html": "<p>Fix in progress.</p>"}'
```

| Method and path | Purpose |
|---|---|
| `GET /itsm/status` | Mode, who set it (`auto`, `admin`, `importer`), CI availability, read-only flag. |
| `GET /itsm/link-types` | The link types the picker offers for a pair of objects in the current mode. |
| `GET /itsm/priority-matrix`, `GET /itsm/priority-suggestion?criticality=&impact=` | The criticality x impact grid and the priority it suggests for a pair (`null` for an unknown pair or a disabled matrix). |
| `GET /itsm/catalog` | Readable classes and the deployment / incident states for the search filters. |
| `GET /itsm/cis` | Search CIs by number and name (Mode A also by attribute values) and state filters. |
| `GET /itsm/cis/{source}/{ci_id}` | One CI with attributes (`source` is `znuny` or `tiqora`; `version_id` selects an older version). Also `/versions` and `/tickets`. |
| `GET /itsm/services/{id}`, `/services/{id}/cis`, `/services/{id}/recent-incident`, `GET /itsm/services/incident-states` | Service details, its CIs, the flood warning for a recent incident on the same service, live incident state of up to 200 services. |
| `GET`/`POST /tickets/{id}/cis`, `DELETE /tickets/{id}/cis/{source}/{ci_id}` | CIs linked to a ticket (readable ones only); link and unlink. |
| `POST /tickets/{id}/linked-articles` | Queue an internal note on linked tickets (`target_ticket_ids`, `subject`, `body_html`, `visible_for_customer`); a job. |
| `GET`/`PUT /tickets/{id}/problem` | Root cause and workaround (`409 not_a_problem` on other ticket types). |
| `POST /tickets/{id}/problem/incidents`, `…/problem/known-error`, `…/problem/close-incidents` | Link up to 50 incidents, publish the problem as a known error (a KB article in review), queue "close the linked incidents with workaround". |
| `GET`/`POST`/`DELETE /tickets/{id}/major-incident`, `POST …/major-incident/updates` | Declaration with timeline, declare, end, post an update (`mode` `internal`, `note`, or `reply` which needs an admin setting). |
| `GET /tickets/{id}/itsm`, `GET /tickets/{id}/link-graph` | What the ticket zoom shows about ITSM, and linked tickets (and CIs) around the ticket. Not gated by the mode. |

Admin (area `admin`): `GET /admin/itsm`, `PUT /admin/itsm/mode`, link types
(`GET`, `POST`, `PATCH`/`DELETE …/{id}`, `POST …/link-types/sync` =
"Aus Znuny aktualisieren"), `GET`/`POST /admin/itsm/setup` ("Einrichten": what
exists and what would be created), `GET`/`PUT /admin/itsm/priority-matrix` (read-only
in Mode A: `409 read_only`) and `GET`/`PUT /admin/itsm/services/{id}/meta`.

**Import from Znuny ITSM** (`/api/v1/admin/itsm/import/*`, admin): `GET
…/status`, `POST …/runs` with `{"dry_run": true}` (dry run or import, a job), `GET
…/runs` and `…/runs/{id}`, `POST …/switch` (final import under Znuny's
read-only flag, then switch to Mode B; body `confirm: "UMSTELLEN"`,
`addon_installed`, `edits_stopped`), `POST …/rollback` (back to Mode A; `confirm:
"ZURÜCK"`) and `POST …/release-lock` ("Sperre aufheben"). See
[`../itsm-import.md`](../itsm-import.md).

## CMDB

The Tiqora CMDB (ITSM Mode B) lives under `/api/v1/cmdb` (API-key area
**`cmdb`**). `GET /cmdb/status` is the only ungated route; **every other** route
answers `404` while ITSM is off and `409 {"detail": {"code": "cmdb_in_znuny"}}`
while Znuny still keeps the CMDB (Mode A, read-only through `/api/v1/itsm`).
Access is per class: the class's Znuny group (`ro` reads, `rw` writes), plus an
optional extra gate group (`cmdb.access_group`). A CI of a class you cannot
read looks like an unknown CI. Product guide: [`../cmdb.md`](../cmdb.md)
(German).

```sh
# Search: a POST because the body holds the filter (so an API key needs cmdb:rw)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/cmdb/cis/search" \
  -H 'Content-Type: application/json' \
  -d '{"class_ids": [3], "q": "mail", "sort": "number", "order": "asc", "limit": 50, "offset": 0}'

# Create a CI; later updates carry expected_lock_version (409 conflict otherwise)
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/cmdb/cis" \
  -H 'Content-Type: application/json' \
  -d '{"class_id": 3, "name": "mail01", "deployment_state_id": 2, "incident_state_id": 1,
       "attributes": {"Owner": "IT"}, "change_note": "initial"}'

# Import: multipart, the JSON "request" form field holds class, mapping and options.
# dry_run defaults to true: the first call only reports what would happen.
curl -b cookies.txt -X POST "$TIQORA_URL/api/v1/cmdb/import-jobs" \
  -F 'file=@servers.csv' \
  -F 'request={"class_id": 3, "file_format": "csv", "dry_run": true, "time_zone": "Europe/Berlin"}'
# -> 202 {"job_id": 40, "status": "queued"}   (poll /api/v1/jobs/40 for the report)
```

| Method and path | Purpose |
|---|---|
| `GET /cmdb/status` | Mode, readable class count, `can_admin`, whether the add-on views exist. Ungated. |
| `GET /cmdb/classes`, `GET /cmdb/classes/{id}/definitions/{version}`, `GET /cmdb/catalog`, `GET /cmdb/form-options`, `GET /cmdb/filter-fields`, `GET /cmdb/companies/lookup` | Classes, definitions, state catalogues and the option lists for forms and search filters. |
| `POST /cmdb/cis/search`, `GET /cmdb/cis/lookup` | Search (criteria: `class_ids`, a `filter`, free text `q`, `sort`, `order`, `offset`, `limit` up to 200); lookup is the type-ahead (`q`, `class_keys`, `limit` up to 100). |
| `POST /cmdb/cis`, `GET`/`PUT`/`DELETE /cmdb/cis/{id}` | Create, read, update (`expected_lock_version` is the optimistic lock; omitting `attributes` keeps the current values, `drop_unknown` confirms losing attributes the class no longer knows), delete. |
| `GET /cmdb/cis/{id}/versions`, `…/versions/{version_id}`, `…/diff`, `…/history` | Version history, one version, difference between two, change history. |
| `GET /cmdb/cis/{id}/impact?depth=&direction=`, `…/affected`, `…/tickets`, `…/links` | Impact graph (depth 1 to 5, `both`/`up`/`down`), affected services and customers, linked tickets, links. |
| `GET /cmdb/link-types`, `GET`/`POST /cmdb/links`, `DELETE /cmdb/links/{id}` | CI to CI, CI to service and ticket to CI links. |
| `GET`/`POST /cmdb/saved-searches`, `PUT`/`DELETE …/{id}` | CMDB saved searches (private or shared); separate from ticket saved searches. |
| `POST /cmdb/attachments`, `GET /cmdb/cis/{id}/attachments`, `GET /cmdb/attachments/{id}` (+ `/meta`), `DELETE …/{id}` | Attachments of CIs (uploads without `ci_id` are staged until bound). |
| `GET`/`POST /cmdb/import-templates`, `GET`/`PUT`/`DELETE …/{id}` | Saved column mappings for imports. |
| `POST /cmdb/import-jobs`, `POST /cmdb/export-jobs`, `POST /cmdb/bulk-jobs` | Jobs (see below). |

**Jobs.** All three answer `202 {"job_id", "status"}`; poll `/api/v1/jobs/{id}`
(which a pure `cmdb` key cannot do: add `tickets:ro`). At most 3 active imports,
exports and bulk edits per user each (`429`). *Import*: CSV or XLSX up to
`cmdb.import_max_mb` (default 25, at most 25) and 200,000 rows; mapping errors
answer `422 invalid_mapping` immediately, permissions are checked again when the
worker runs. *Export*: one class (`class_id`), optional `filter` / `q`,
`file_format` `csv` or `xlsx`, up to 200,000 rows. *Bulk edit*: up to 5000 CIs
(`ci_ids`) get the same `deployment_state_id` / `incident_state_id` and/or the
same `link` (`link_type_key`, `target_kind`, `target_key`, `ci_side`); CIs
without `rw` are skipped and reported.

**Admin** (`/api/v1/admin/cmdb`, area `admin`): `status`, `seed-defaults`
(idempotent: missing catalogue rows and link types, then the two add-on
views), `propagation/recompute` (job), `catalog-classes` / `catalog` (CRUD of
state catalogues), `classes` (CRUD) with `definitions` (versions, diff),
`definitions/validate`, `definition-import` (preview of a Znuny YAML/Perl or
Tiqora JSON definition, writes nothing) and `class-templates` with `…/{key}/apply`.
Customers see their own CIs through `/api/portal/config-items`.

## Plugin channels

Plugins can add communication channels (see
[`../plugins.md`](../plugins.md#channel-drivers)). `GET
/api/v1/channels/plugin` lists the channels of loaded, enabled plugins that an
agent can answer on or at least see, so the UI can offer them in the reply
composer. Replies on such a channel go through the ordinary `POST
/api/v1/tickets/{id}/articles`. Plugins' own HTTP routes are separate: they sit
under `/api/plugins/<plugin id>/…` and need the API-key area `plugins` (see the
[API overview](README.md)).

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/channels/plugin"
```

## Admin CRUD overview

Every admin resource lives under `/api/v1/admin/*` and requires `rw` on the
group literally named `admin` (see [`compat.md`](compat.md) for the
permission model); an API key needs the area `admin`. Installing and
uninstalling plugins and changing plugin repositories additionally require a
**browser session**: an API key is refused with `403` whatever its scope. Most follow the same list/get/create/update/delete
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
| PGP / S/MIME keys | `/api/v1/admin/crypto-keys` (`/status`, `/pgp`, `/smime/…`, `/sign-key-options`; see [`../crypto.md`](../crypto.md#admin-ui-and-api)) |
| PGP / S/MIME settings (+ PGP passphrases) | `/api/v1/admin/crypto-settings` (`GET`/`PUT`, `/pgp-passphrases/{key}`) |
| Agent SSO eligibility / 2FA | `/api/v1/admin/auth-config` (+ `/global`, `/{user_id}`, `/{user_id}/reset-2fa`) |
| Identity providers (OIDC/SAML/LDAP) | `/api/v1/admin/identity-providers` (`GET`/`POST`, `/{id}` `GET`/`PATCH`/`DELETE`; `/kinds`, `/presets`, `/capabilities`; `/{id}/refresh-metadata`; `/{id}/tests` + `/{id}/tests/{test_id}`; `/{id}/preview`; `/{id}/links` (+ `DELETE /{link_id}`); `/{id}/managed-assignments`; see [identity guide](../guide/identity-providers.md)) |
| SCIM tokens and log (per identity provider) | `/api/v1/admin/identity-providers/{id}/scim` (status), `…/scim/tokens` (`POST` creates a `tqscim_…` token shown once, `DELETE …/{token_id}` revokes), `…/scim/accounts`, `…/scim/groups`, `…/scim/events`; the SCIM endpoint itself is `/scim/v2` (see the [API overview](README.md)) |
| Customer 2FA reset | `POST /api/v1/admin/customer-users/{id}/reset-2fa` (TOTP + passkeys, revokes the customer's sessions) |
| Feature grants (who may use an agent feature) | `/api/v1/admin/feature-grants/{feature}` (`GET`/`PUT` with `user_ids`, `group_ids`, `role_ids`; empty = admins only) |
| Settings sections (jobs, avatars, process, reporting, itsm, cmdb, plugin sections) | `/api/v1/admin/settings-sections`, `…/{key}` (`GET`/`PUT`; secrets read back as `********`) |
| Webservices (GenericInterface) | `/api/v1/admin/webservices` (`GET`), `…/{id}`, `PUT …/{id}/egress` (outbound allow list), `…/{id}/debugger` (+ `…/{source}/{entry_id}`), `POST …/{id}/debugger/tiqora/{entry_id}/resend`, `POST …/{id}/invokers/{invoker}/dry-run` (builds and shows the masked request, sends nothing); see [webservices guide](../guide/webservices.md) |
| Processes and ACL deploy | `/api/v1/admin/process-designer/*` (designer, see "Processes (agent API)"), `POST /api/v1/admin/deploy` (`{"kind": "process"\|"acl"}`, "Bereitstellen"), `GET /api/v1/admin/deploy-status`, `GET /api/v1/admin/processes` |
| Plugins | `/api/v1/admin/plugins` (`GET`; `PUT …/{id}/enabled`, `POST …/{id}/reset`), `…/plugins/number-generators` (`GET`/`PUT`), `…/plugins/packages` (`POST` upload, `GET`/`DELETE …/{sha256}`, `POST …/{sha256}/install`), `POST …/plugins/{id}/uninstall` (`delete_data`: "Daten behalten" or "Daten löschen"), `GET`/`POST …/plugins/restart` (coordinated restart); see [plugins guide](../plugins.md) |
| Plugin repositories | `/api/v1/admin/plugin-repositories` (CRUD), `…/catalog`, `POST …/{id}/refresh`, `POST …/{id}/download` (jobs) |
| Extension registry (read-only) | `GET /api/v1/admin/extensions` (extension points and what is registered) |
| ITSM | `/api/v1/admin/itsm` (`GET`, `PUT …/mode`, link types, `setup`, `priority-matrix`, `services/{id}/meta`), `/api/v1/admin/itsm/import/*` (status, runs, switch, rollback, release-lock); see "ITSM" above |
| CMDB admin | `/api/v1/admin/cmdb` (status, `seed-defaults`, `propagation/recompute`, catalogues, classes, definitions, definition import, class templates); see "CMDB" above |
| Dashboard templates, wallboard links | `/api/v1/admin/dashboard-templates` (CRUD), `/api/v1/admin/dashboard-kiosk-links` (`GET`/`POST`, `POST …/{id}/revoke`, `DELETE …/{id}`; the link secret is returned once by `POST`) |
| Reporting data | `GET /api/v1/admin/reporting/status`, `POST /api/v1/admin/reporting/backfill` (`since`, `restart`, `snapshots`; a second request joins the pending job) |
| Chat configs | `/api/v1/admin/chat-configs` (CRUD, `POST …/{id}/rotate-key`, `POST`/`DELETE …/{id}/identity-secret` for signed website sign-in, `GET …/{id}/ai-status` = per topic whether the AI assistant would answer now, else why not); see [web chat guide](../guide/webchat.md) |
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

**Legacy fixed-report endpoints.** These five endpoints, each with a streaming
`.csv` variant, predate the report engine. They are thin wrappers that run the
matching **built-in reports** and keep their original response shapes for
existing integrations; the web interface no longer uses them. New integrations
should use [`POST /reports/run`](#reports), which also offers all other built-in
and saved reports, runtime parameters, previous-period comparison and export.

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/volume"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/open-snapshot"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/sla"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/agent-workload"
curl -b cookies.txt "$TIQORA_URL/api/v1/stats/backlog"
```

They need the same access as reports (`reporting.access`, `403` otherwise), are
scoped to the caller's readable queues, share one inline time budget of at most
75 s per request (`504` with `detail.message` when it is used up) and mark a
result cut by a row cap with the response header `X-Tiqora-Truncated: 1`. An
API key needs the area `stats`.

## Webhooks (outbound, AI/automation integration)

Configured via `/api/v1/admin/webhooks`; delivery payload shape, HMAC
signing, and retry semantics are documented in
[`../ai-integration.md`](../ai-integration.md#1-webhook-payload-schema-versioned-envelope).

## Web chat

Two surfaces; the guide is [guide/webchat.md](../guide/webchat.md).

**Public visitor API** (`/api/chat/v1`, no login). It is called only by the chat frame on Tiqora's own origin:
unsafe methods need a same-origin `Origin` header (a foreign one is `403 cross_origin`), there is no CORS and no
cookie. A visitor proves a session with the `X-Chat-Token` header returned when the session is created (only its
SHA-256 is stored). Errors are `{"detail": "<code>"}`; `429`/`503` carry `Retry-After`.

| Method and path | Purpose |
|---|---|
| `GET /config?k=<key>&origin=<website>` | What the widget shows: topics, pre-chat fields, theme, online state. The `origin` must be allowed by the chat. |
| `POST /sessions` | Start a chat with the first message; creates the ticket (`409 offline` when nobody can take it). |
| `GET /sessions/{id}` | Status (`bot`, `waiting`, `active`, or ended), agent name, `can_send`, `identified`, and while the AI answers `bot_name` and `can_handoff: true`. |
| `GET /sessions/{id}/messages?after=` | Transcript lines after a sequence number. |
| `GET /sessions/{id}/stream` | Server-sent events (`message` with `id` = sequence, `session`, `typing`, `end`); resume with `Last-Event-ID`. A heartbeat comment every 25 s; at most 30 min per stream. |
| `POST /sessions/{id}/messages`, `/typing`, `/attachments`, `/contact`, `/end` | Visitor actions. Uploads: png, jpeg, gif, webp, pdf, txt; the chat's own size limit. |
| `POST /sessions/{id}/handoff` | The visitor asks for a human while the AI answers (the widget button "Mit einem Menschen sprechen", talk to a human). `204`: the chat is `waiting` (the agents ring) or, when nobody is available, ended as `no_agent` (the e-mail prompt follows). `409 not_bot` when the AI is not answering any more (for example an agent took over first). At most 3 per minute and chat. |
| `POST /sessions/{id}/identify` | Attach a signed website identity (`{"identity": "<token>"}`) or a portal assertion to a running chat. |
| `POST /identity/portal` | A short-lived assertion for the customer who is logged in to the portal (called from the portal on Tiqora's own origin, uses the portal session cookie). |
| `GET /sessions/{id}/messages/{seq}/attachments/{index}` | Download a transcript attachment. |
| `POST /offline` | The message form when nobody is available (creates a ticket). |

**Agent endpoints** (`/api/v1/chat`, session cookie or API key). They map to the API-key area `tickets`
(`GET` = `ro`, `POST`/`PUT` = `rw`). The chats a topic's AI assistant answers first (status `bot`) can be taken
over by any agent with `rw` on the queue (from the inbox, with `accept`, or simply by replying).

| Method and path | Purpose |
|---|---|
| `GET /chat/agent-state`, `PUT /chat/agent-state` | The agent's availability ("Chats annehmen"), topic filter and `max_concurrent`. |
| `GET /chat/topics` | Topics whose queue the agent holds `rw` on. |
| `GET /chat/inbox` | Three lists: `waiting` (chats the agent may accept), `mine` (the agent's own active chats) and `bot` (chats the AI is answering, newest first, no ring). Items carry `session_id`, `ticket_id`, `tn`, `title`, `topic_label`, `visitor_name`, `status` (`bot`, `waiting`, `active`), `waiting_since`, `accepted_at`. |
| `POST /chat/sessions/{id}/accept` | Take a **waiting** chat **or one the AI is answering** (first accept wins; owner and lock are set like in Znuny). Errors: `403 forbidden`, `404 not_found`, `409` `already_taken`, `at_capacity` or `not_waiting`. Taking over a `bot` chat records `handoff_reason = "takeover"` and stops the AI; the ticket is not marked as AI-escalated. |
| `POST /chat/sessions/{id}/end` | End a live chat, optionally closing the ticket (`{"close_ticket": true}`; idempotent, `204`). |
| `GET /tickets/{id}/chat` | The ticket's newest chat for the conversation header: visitor, `identity_source`, `visitor_email_verified`, `status`, `end_reason`, and for AI chats `bot_name`, `handoff_reason` (`visitor`, `ai`, `limit`, `error`, `unavailable` or `takeover`) and `handoff_at`. The AI's own handoff summary is the internal note on the ticket, not part of this response. `404` when the ticket has no chat. |
| `POST /tickets/{id}/chat/typing` | The agent is typing in the live chat (needs `note`; at most one signal per 2 s). `409 chat_not_live` without a live chat. |

Replying into a chat uses the ordinary `POST /tickets/{id}/articles`. A reply to a **waiting** chat accepts it
first, and a reply to a chat the **AI is answering** is a takeover; if the accept is refused, the reply is refused
with that error (`409`) and nothing is written. Over MCP `ticket_reply` into a waiting chat **or** one the AI is
still answering returns `chat_not_accepted`; there is no MCP accept tool, so a human accepts or takes over first.

Admin CRUD for chats is under `/api/v1/admin/chat-configs` (including `POST /{id}/rotate-key`, the website sign-in
secret and `GET /{id}/ai-status`, see the admin overview).

## Realtime events (SSE)

```sh
curl -b cookies.txt -N "$TIQORA_URL/api/v1/events/stream"
```

Server-Sent Events stream of ticket change notifications, used by the UI to
drive live invalidation. `call_event` messages (CTI popup) are delivered only to
the agents listed in their `user_ids`. Also see `GET/PUT /api/v1/tickets/{id}/presence`
for the "who's viewing this ticket" indicator. A long-lived idle connection
sends a `: heartbeat` comment every 25s — reverse proxies must not buffer or
time out this connection early (see
[`../deploy/docker-compose.md`](../deploy/docker-compose.md) for the nginx
settings this requires).

## Customer lookup

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/customers/jdoe"

# Who is calling? Customers whose phone/mobile ends in the same digits and
# their open tickets in readable queues
curl -b cookies.txt "$TIQORA_URL/api/v1/reference/caller?number=%2B49%20228%205550101"

# Click-to-call scheme for agent UIs: {"dial_scheme": "tel"|"sip"}
curl -b cookies.txt "$TIQORA_URL/api/v1/reference/phone-config"

# Ticket dynamic fields of a phone screen (###DynamicField config, 2 = required)
curl -b cookies.txt "$TIQORA_URL/api/v1/reference/dynamic-fields?screen=AgentTicketPhoneOutbound"
```

`/reference/customers` and `/reference/customer-search` also match phone and
mobile numbers once the query contains five or more digits.

Customer write endpoints (any authenticated agent):

- `POST /api/v1/customers` creates a customer user; `email` and `first_name`
  may be empty, `mobile` and `comments` are accepted, and a known e-mail
  answers `409 {"detail": {"message": "...", "login": "..."}}` with the login of
  the existing customer.
- `POST /api/v1/customers/companies` creates a customer company.
- `PATCH /api/v1/customers/{login}` is fill-only: it sets empty fields but
  never overwrites existing values; `comments_append` adds a line to the
  comments and skips it when that line is already present.

## Integrations (external tools)

```sh
# Tickets of one customer account (login plus #N-suffixed variants), newest
# first, archived included, only readable queues; tickets:ro key suffices
curl -H "Authorization: Bearer $KEY" \
  "$TIQORA_URL/api/v1/integrations/customer-tickets?login=jdoe&limit=50"
```

Per ticket: number, title (+ a conservative `title_pii_free` flag), queue,
state, e-mail and message counts, first channel, stored AI summary. Details:
[`../architecture.md`](../architecture.md#customer-ticket-history-for-external-tools-netadmin).

## SSO endpoints (browser flows)

These are navigation endpoints (redirects and form posts), not JSON APIs. `<slug>` is the provider's
slug; configuration lives in the admin API above and the
[identity guide](../guide/identity-providers.md).

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/auth/methods` | login discovery: `password`, `oidc`, `spnego`, `ldap`, `webauthn`, `saml` (SAML usable), `providers` (enabled OIDC/SAML providers for agents: `slug`, `kind`, `label`, `icon`) |
| `GET /api/v1/auth/sso/<slug>/login?next=/path` | start OIDC or SAML login (302 to the IdP); `?test=<id>` is an admin test run |
| `GET /api/v1/auth/oidc/<slug>/callback` | OIDC redirect URI (env provider also `GET /api/v1/auth/oidc/callback`) |
| `POST /api/v1/auth/saml/<slug>/acs` | SAML assertion consumer service (form post) |
| `GET /api/v1/auth/saml/<slug>/metadata` | SP metadata (also the entity ID) |
| `GET`/`POST /api/v1/auth/saml/<slug>/sls` | SAML single logout (requests and responses) |
| `POST /api/v1/auth/oidc/<slug>/backchannel-logout` | OIDC back-channel logout (logout token) |
| `POST /api/v1/auth/sso/logout` | logout that continues at the IdP when the session came from SSO |

Failed logins redirect to `/login?sso_error=<code>&next=<path>`; the codes are listed in the
identity guide. Customer portal equivalents live under `/api/portal/auth`:
`GET /methods`, `GET /sso/<slug>/login`, `GET /oidc/<slug>/callback`,
`POST /saml/<slug>/acs`, `POST /sso/logout`, and the second-factor routes
`/totp/*` and `/passkey/*` (TOTP enroll/confirm/verify/delete, passkey
register/authenticate); a password login with 2FA answers `pending_2fa: true`.

## Customer portal API (`/api/portal`)

Only available when the deployment sets `TIQORA_PORTAL_ENABLED=true` (and
the admin switch is on); otherwise every `/api/portal/*` route answers 404.
Separate session (`POST /api/portal/auth/login`, `GET /api/portal/auth/me`),
scoped to a `customer_user` rather than an agent. Ticket endpoints mirror a
restricted subset of the agent API: `GET/POST /api/portal/tickets`, `GET
/api/portal/tickets/{id}`, `GET .../articles`, `POST
.../tickets/{id}/reply`, plus `/api/portal/kb/*` for the customer-facing
knowledge base and `/api/portal/tickets/{id}/attachments` for uploads.
