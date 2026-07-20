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

**API key** (bearer token, used by scripts/automation — issue one via the
admin API, or reuse the CLI/session token as a bearer token for quick
testing):

```sh
curl "$TIQORA_URL/api/v1/tickets" \
  -H "Authorization: Bearer $TIQORA_API_KEY"
```

**Discover enabled auth methods** (what the login page should offer):

```sh
curl "$TIQORA_URL/api/v1/auth/methods"
# {"password": true, "oidc": false, "spnego": false, "ldap": false}
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

Response shape: `{"items": [...], "total": N}`. Filters: `queue_id`,
`state_id`, `state_type`, `owner_id`. A streaming, unpaginated CSV export of
the same filter set is available at `GET /api/v1/tickets/export.csv`.

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
# List an article summaries for a ticket
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

## Drafts

Per-ticket, per-action reply/note drafts (autosave for the compose box):

```sh
curl -b cookies.txt "$TIQORA_URL/api/v1/tickets/4711/drafts"

curl -b cookies.txt -X PUT "$TIQORA_URL/api/v1/tickets/4711/drafts/reply" \
  -H 'Content-Type: application/json' \
  -d '{"action": "reply", "content": "{\"body\": \"Draft text...\"}"}'

curl -b cookies.txt -X DELETE "$TIQORA_URL/api/v1/tickets/4711/drafts/reply"
```

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
permission model). All follow the same list/get/create/update pattern:

| Resource | Path |
|---|---|
| Users | `/api/v1/admin/users` |
| Groups | `/api/v1/admin/groups` |
| Roles | `/api/v1/admin/roles` |
| Queues | `/api/v1/admin/queues` |
| States | `/api/v1/admin/states` |
| Priorities | `/api/v1/admin/priorities` |
| Customers | `/api/v1/admin/customers` |
| Templates | `/api/v1/admin/templates` |
| Auto-responses | `/api/v1/admin/auto-responses` |
| Dynamic fields | `/api/v1/admin/dynamic-fields` |
| Read-only reference data (ACLs, etc.) | `/api/v1/admin/readonly/*` |
| Webhooks | `/api/v1/admin/webhooks` |
| Channels (SMS/WhatsApp/phone config) | `/api/v1/admin/channels` |

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
