# GenericInterface compatibility layer

Tiqora exposes a **compatibility surface** that emulates the Znuny 6.5
GenericInterface operations most integrators use. The native API is always
`/api/v1` (OpenAPI). Compat exists so existing scripts, middleware, and
third-party tools can migrate gradually.

## Scope (V1)

| Operation | Purpose |
|---|---|
| `SessionCreate` | Authenticate; return SessionID usable with subsequent calls |
| `TicketCreate` | Create ticket (+ optional article) |
| `TicketUpdate` | Update fields / add article |
| `TicketGet` | Fetch ticket(s) with articles |
| `TicketSearch` | Search by criteria |

SOAP is **out of scope** for V1. Transport is the REST-style GenericInterface
endpoints Znuny documents for the HTTP::REST connector.

## Routing model

Operations are **fixed implementations** in `api/compat/`. Routes are mounted
**dynamically** from `gi_webservice_config` (`RouteOperationMapping`), with
sensible default routes as fallback when no webservice is configured.

This mirrors Znuny’s flexibility: operators can keep their existing webservice
URL paths pointing at Tiqora after a reverse-proxy cutover.

## Session handling

- Prefer validating SessionID against Znuny’s `sessions` table so a session
  created in Znuny still works when traffic is split or cut over.
- Tiqora-native sessions remain Redis-backed for `/api/v1` and the UI.
- Password verification must accept all Znuny hash schemes in use on the dump.

## Known gotchas (regression-tested)

These issues appear in real Znuny deployments and must not regress:

| Topic | Correct behaviour |
|---|---|
| `StateType` vs `StateTypes` | TicketSearch expects **singular** `StateType`; plural is silently ignored in Znuny |
| `IsVisibleForCustomer` | Defaults and article visibility must match Znuny wire defaults |
| SessionID | Must resolve against Znuny `sessions` when present |
| Error shape | Error codes/messages should stay parseable by common clients |
| Empty search | Empty result sets return the same structure as Znuny (not HTTP 404) |

Phase 2c golden-behaviour tests (16 in `tests/test_compat_operations.py`) cover
all five operations including the gotchas above with seeded MariaDB data.

## Implemented routes

### Canonical fallback (always available)

| Method | Path | Operation |
|--------|------|-----------|
| POST | `/znuny-compat/Session` | SessionCreate |
| POST | `/znuny-compat/Ticket` | TicketCreate |
| GET | `/znuny-compat/Ticket/{ticket_id}` | TicketGet |
| PATCH | `/znuny-compat/Ticket/{ticket_id}` | TicketUpdate |
| GET | `/znuny-compat/TicketSearch` | TicketSearch |
| POST | `/znuny-compat/admin/reload` | Re-mount dynamic routes (auth required) |

### Dynamic routes (from `gi_webservice_config`)

On startup, Tiqora reads all valid `gi_webservice_config` rows, YAML-parses
`Provider.Transport.Config.RouteOperationMapping`, and registers routes under:

```
/znuny-compat/Webservice/{webservice_name}{route}
/znuny-compat/WebserviceID/{webservice_id}{route}
```

Unsupported operation types (not in the 5-op set above) return HTTP 501.
Znuny `:VariableName` path segments are converted to FastAPI `{VariableName}`.

Query-string AND JSON body parameters are merged (body wins on collision),
matching Znuny `HTTP::REST` transport behaviour.

### Admin reload

`POST /znuny-compat/admin/reload` (requires tiqora auth) re-validates and logs
the current webservice configuration. A full restart is required to actually
hot-reload dynamic routes in running processes.

## StateType / StateTypes gotcha (documented deviation)

Znuny TicketSearch expects `StateType` as a **singular string** (e.g. `"open"`).
The plural `StateTypes` is a Tiqora extension that accepts a list; Znuny ignores it.
Both forms are supported in Tiqora for convenience.

## Auth surface for integrators

1. **Compat SessionCreate** — legacy tools, SessionID cookie/header style.
2. **SessionID** — validated against Znuny `sessions` key-value table (not Redis).
3. **API keys** — preferred for MCP and modern automation (same permission engine).
4. **OIDC / Kerberos** — UI and `/api/v1` (Phase 3); not required for basic
   GenericInterface parity.

## What is not emulated

- Full GenericInterface provider/consumer framework
- Arbitrary custom operations registered only as Znuny packages
- SOAP envelope processing
- Package Manager remote install
- TicketHistoryGet, TimeAccountingGet (return 501)

Integrators needing those should migrate to `/api/v1` or MCP.

## Migration guidance

1. Point a non-production webservice at Tiqora’s compat layer.
2. Run the golden-master suite and a soak of real client traffic.
3. Move production webservice routes (or reverse proxy) when diffs are clean.
4. Plan a later move to `/api/v1` for new integrations.

## Phase 2c uncertainties

- **SessionID TTL**: The compat layer validates `UserID`/`UserLogin`/`UserType`
  from the `sessions` table but does not check `UserLastRequest` or TTL. Expired
  but un-purged sessions will still authenticate. Mitigated: Znuny’s session
  cleanup daemon removes stale rows; a future phase can add TTL checks.
- **CustomerUserLogin auth**: Customer users authenticated via compat ops are
  mapped to `user_id=1` (system) internally since they have no Znuny agent ID.
  This means all compat customer writes appear as system-initiated in history.
  Phase 3 will address this with a proper customer principal.
- **DynamicField_X search**: Only `Equals` and `Like` operators are implemented;
  `GreaterThan`, `SmallerThan`, `GreaterThanEquals`, `SmallerThanEquals` are not.
- **Attachment storage**: Attachments are stored inline in `article_data_mime_attachment`
  (DB storage), not offloaded to a file backend. For large attachments this may
  be a performance concern.
- **Hot-reload**: Dynamic webservice routes require a process restart to take effect.
  The `/admin/reload` endpoint logs and validates but cannot actually re-register
  FastAPI routes in a running process without a restart.

## Phase 3a uncertainties

**Customer portal**

- `queue.follow_up_id == 3` ("new ticket" split) is not implemented — treated
  identically to `1` (reopen same ticket). `POST /portal/tickets/{id}/reply`
  always replies in-place; a real split-into-new-ticket flow is deferred.
- Reject-on-followup (`follow_up_id == 2`) returns HTTP 409 (business-state
  conflict), not 403 (authz failure) — spec allowed either.
- `portal.default_queue_id` unset falls back to Znuny seed queue id 2 ("Raw");
  `portal.followup_reopen_state` unset falls back to state name `"open"`.
- Portal-originated tickets/articles record `create_by`/owner as user id 1
  (root@localhost), matching the existing convention for postmaster-style
  writes elsewhere, since portal writes have no `users` row to attribute to.
- `POST /portal/tickets/{id}/attachments` always creates a new customer
  article carrying the file (subject to the same reopen/reject rules) — Znuny
  has no "attachment without an article" concept.

**Knowledge base**

- Chunk indexing on `publish()` is synchronous, direct-to-Meilisearch —
  it does not go through `tiqora_event_outbox` (which is ticket-shaped,
  keyed by `ticket_id`). Acceptable since publish is a low-frequency admin
  action; revisit if KB write volume grows.
- KB tables have no FK constraints, matching the existing `tiqora_*` migration
  style (e.g. `tiqora_form_draft.ticket_id`) — referential integrity is
  application-enforced only.
- Soft deletes: categories → `valid = False`; articles → `state = "archived"`
  (content/chunks retained for audit/citation, not indexed for search).
- Migration `20260719_0001_api_key_and_settings.py` (pre-existing, Phase 2)
  fails to apply on PostgreSQL (`server_default=sa.text("1")` on a boolean
  column → `DatatypeMismatchError`). Discovered while validating the new KB
  migration (0004) on Postgres; 0004 itself applies/downgrades cleanly on
  MariaDB. The Postgres leg of the full migration chain needs a follow-up fix
  to 0001, tracked separately from Phase 3a.

**Admin CRUD API**

- Admin check: `PermissionEngine.is_admin()` requires `rw` on the group
  literally named `admin` (direct `group_user`, or via `role_user` →
  `group_role`) — there is no separate "is superuser" flag in Znuny's schema.
- Fixed a latent bug while wiring `dynamic_fields` writes:
  `db/legacy/dynamic_field.py`'s `DynamicField.config` was mapped as
  `LargeBinary`, but Znuny's real column is `TEXT`/`LONGBLOB`-as-text
  (same convention already documented for `Acl.config_match`). Writing
  through the `LargeBinary` mapping caused PostgreSQL to implicitly hex-encode
  the bytea parameter on INSERT, silently corrupting stored YAML — only read
  paths had previously exercised this column. Column now mapped as `Text`.
- Dynamic field YAML `config` keys per type (validated on admin
  create/update): Text/TextArea — `DefaultValue`, `Link`, `RegExList`
  (TextArea adds `Rows`, `Cols`); Checkbox — `DefaultValue`; Dropdown/
  Multiselect — `PossibleValues` (required dict), `PossibleNone`,
  `TranslatableValues`, `DefaultValue`, `Link`; Date/DateTime —
  `DefaultValue`, `YearsPeriod`, `YearsInPast`, `YearsInFuture`. Unknown keys
  are rejected; per-type required keys are enforced (422 on violation).
- No per-config-row cache-invalidation entity exists in `tiqora_*`; admin
  writes to queues/states/priorities enumerate and invalidate every
  currently-affected `ticket.id` directly instead.
- Deferred: ACL *editing* (list/detail only, as specified), and
  `group_customer`/`group_customer_user` assignment endpoints (not in the
  originally requested resource list).
