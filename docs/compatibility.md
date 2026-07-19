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
