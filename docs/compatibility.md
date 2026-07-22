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

Both transports Znuny documents for these operations are supported:
the REST-style `HTTP::REST` connector, and `HTTP::SOAP` (see
[SOAP transport](#soap-transport) below).

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

Golden-behaviour tests (16 in `tests/test_compat_operations.py`) cover
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
the current webservice configuration (REST routes and SOAP webservices). A
full restart is required to actually hot-reload dynamic routes in running
processes.

## SOAP transport

Znuny's `HTTP::SOAP` GenericInterface transport
(`Kernel/GenericInterface/Transport/HTTP/SOAP.pm`) is emulated by
`api/compat/soap.py` (codec) + the same routes/dispatch table in
`api/compat/router.py` — SOAP requests go through the *identical*
`op_session_create` / `op_ticket_create` / `op_ticket_update` / `op_ticket_get`
/ `op_ticket_search` handlers as REST; only the wire format differs.

### Endpoints

| Method | Path | NameSpace used |
|--------|------|-----------------|
| POST | `/znuny-compat/soap/{webservice}` | Default (`http://www.otrs.org/TicketConnector/`) — always available, `{webservice}` is a free label |
| POST | `/znuny-compat/Webservice/{name}` | That webservice's `Provider.Transport.Config.NameSpace` (from `gi_webservice_config`) |
| POST | `/znuny-compat/WebserviceID/{id}` | Same, addressed by numeric ID |

Unlike REST, SOAP has **no per-operation route mapping** in Znuny — a single
endpoint per webservice accepts any of the 5 supported operations. The
operation is dispatched from the **SOAP Body wrapper element's local name**
(namespace-prefix agnostic), matching Znuny's
`$Operation = (sort keys %{$Body})[0]` (`SOAP.pm`). The `SOAPAction` HTTP
header is accepted as a fallback hint only.

### Example: TicketGet request/response

Request (`Content-Type: text/xml; charset=utf-8`):

```xml
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <TicketGet>
      <SessionID>abc123</SessionID>
      <TicketID>42</TicketID>
    </TicketGet>
  </soapenv:Body>
</soapenv:Envelope>
```

Response (HTTP 200, same `text/xml`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <TicketGetResponse xmlns="http://www.otrs.org/TicketConnector/">
      <Ticket>
        <TicketID>42</TicketID>
        <TicketNumber>2026072000001</TicketNumber>
        <Title>Broken printer</Title>
        ...
      </Ticket>
    </TicketGetResponse>
  </soap:Body>
</soap:Envelope>
```

Errors become a SOAP `<Fault>` (matching `ProviderGenerateResponse`'s
`OperationResponse = 'Fault'` path) with the HTTP status set from the same
`ErrorCode` → status mapping REST uses (401 AuthFail, 403 AccessDenied, 400
Missing/InvalidParameter, 501 NotImplemented).

### Namespace configuration

`Provider.Transport.Config.NameSpace` in the webservice's YAML config (same
key Znuny uses) controls the `xmlns` on the `<OperationNameResponse>`
wrapper element. Default when unset:
`http://www.otrs.org/TicketConnector/` (Znuny's own sample
`GenericTicketConnectorSOAP` webservice default).

### Auth in the SOAP body

Same as REST: `UserLogin`/`Password`, `CustomerUserLogin`/`Password`, or
`SessionID` as top-level elements inside the operation wrapper (not SOAP
WS-Security headers) — this matches Znuny's own GenericInterface auth
model, which is operation-parameter-based, not a SOAP header mechanism.

### XXE mitigation

Requests are parsed exclusively via `defusedxml.ElementTree`, which rejects
external entities (XXE / SSRF via `SYSTEM`/`PUBLIC` DTD references) and
internal entity-expansion bombs. A rejected envelope returns HTTP 400 with a
SOAP Fault; the malicious payload is never resolved.

### WSDL

Znuny's GenericInterface provider does not auto-serve a WSDL for the generic
ticket connector either — the WSDL shipped in Znuny's source
(`scripts/test/Console/Command/Admin/WebService/GenericTicketConnectorSOAP.wsdl`)
is a hand-maintained sample, not generated on request. Tiqora matches this:
there is no `?wsdl` route. Point a SOAP client at the endpoint URL directly
(most clients, e.g. Python `zeep`, accept a local/adapted WSDL file plus an
explicit `address` override), or build envelopes by hand as the compat test
suite does.

### Differences vs Znuny's SOAP transport (documented deviations)

- Znuny's `SOAPActionScheme`/`SOAPActionFreeText`/`SOAPActionSeparator`
  config knobs (multiple ways to validate the `SOAPAction` header against an
  expected string) are **not enforced** — the header is only used as a
  fallback operation-name hint when the Body wrapper is unusable. Real
  clients set `SOAPAction` correctly but Znuny's strict-match rejection path
  is not reproduced.
- `ResponseNameScheme`/`ResponseNameFreeText` (`Append`/`Plain`/`Replace`)
  are not configurable — Tiqora always uses Znuny's *default*
  (`Response` → `Append` + `Response`, i.e. `TicketGet` → `TicketGetResponse`).
- `MaxLength` (request body size cap) is not enforced by the codec itself
  (rely on the ASGI server / reverse proxy body-size limit instead).
- SOAP 1.2 is accepted (mirrors the request's Content-Type/envelope
  namespace in the response) but Znuny's SOAP transport is primarily
  exercised with SOAP 1.1 (`SOAP::Lite` default) in practice.

## StateType / StateTypes gotcha (documented deviation)

Znuny TicketSearch expects `StateType` as a **singular string** (e.g. `"open"`).
The plural `StateTypes` is a Tiqora extension that accepts a list; Znuny ignores it.
Both forms are supported in Tiqora for convenience.

## Auth surface for integrators

1. **Compat SessionCreate** — legacy tools, SessionID cookie/header style.
2. **SessionID** — validated against Znuny `sessions` key-value table (not Redis).
3. **API keys** — preferred for MCP and modern automation (same permission engine).
4. **OIDC / Kerberos** — UI and `/api/v1`; not required for basic
   GenericInterface parity.

## What is not emulated

- Full GenericInterface provider/consumer framework (e.g. Requester-side
  outbound SOAP/REST calls — only the Provider/server side is emulated)
- Arbitrary custom operations registered only as Znuny packages
- Package Manager remote install
- TicketHistoryGet, TimeAccountingGet (return 501)
- WSDL auto-serving (Znuny doesn't serve one either — see
  [SOAP transport](#soap-transport))

Integrators needing those should migrate to `/api/v1` or MCP.

## Migration guidance

1. Point a non-production webservice at Tiqora’s compat layer.
2. Run the golden-master suite and a soak of real client traffic.
3. Move production webservice routes (or reverse proxy) when diffs are clean.
4. Plan a later move to `/api/v1` for new integrations.

## Golden-master validation (2026-07-19)

The golden-master suite (`tests/golden/`, see docs/testing.md) runs a REAL
Znuny 6.5.22 container on the same MariaDB and validated:

- SessionCreate / TicketSearch / empty-search wire shapes against Znuny's
  shipped `GenericTicketConnectorREST` webservice.
- The `StateType` singular gotcha on both sides.
- **Divergence found and fixed**: a compat-issued SessionID (Redis) could not
  authenticate follow-up compat calls (`_auth_from_params` only consulted the
  Znuny `sessions` table); it now falls back to the Tiqora session store.
- **Divergence found and fixed**: compat TicketUpdate auto-locked the ticket
  on owner change; Znuny's GI TicketUpdate never does.

## Known limitations (compat layer)

- **SessionID TTL**: The compat layer validates `UserID`/`UserLogin`/`UserType`
  from the `sessions` table but does not check `UserLastRequest` or TTL. Expired
  but un-purged sessions will still authenticate. Mitigated: Znuny’s session
  cleanup daemon removes stale rows; a future release can add TTL checks.
- **CustomerUserLogin auth**: Customer users authenticated via compat ops are
  mapped to `user_id=1` (system) internally since they have no Znuny agent ID.
  This means all compat customer writes appear as system-initiated in history.
  A later revision will address this with a proper customer principal.
- **DynamicField_X search**: Only `Equals` and `Like` operators are implemented;
  `GreaterThan`, `SmallerThan`, `GreaterThanEquals`, `SmallerThanEquals` are not.
- **Attachment storage**: Attachments are stored inline in `article_data_mime_attachment`
  (DB storage), not offloaded to a file backend. For large attachments this may
  be a performance concern.
- **Hot-reload**: Dynamic webservice routes require a process restart to take effect.
  The `/admin/reload` endpoint logs and validates but cannot actually re-register
  FastAPI routes in a running process without a restart.
- **SOAP `SOAPAction` strict validation**: Znuny's `SOAPActionScheme` config
  (validating the header against an expected `NameSpace#Operation` string) is
  not enforced — see [SOAP transport differences](#differences-vs-znunys-soap-transport-documented-deviations).

## Known limitations (portal / customers)

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
- Migration `20260719_0001_api_key_and_settings.py`
  fails to apply on PostgreSQL (`server_default=sa.text("1")` on a boolean
  column → `DatatypeMismatchError`). Discovered while validating the new KB
  migration (0004) on Postgres; 0004 itself applies/downgrades cleanly on
  MariaDB. The Postgres leg of the full migration chain needs a follow-up fix
  to 0001, tracked separately.

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
