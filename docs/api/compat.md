# GenericInterface compatibility (`/znuny-compat`)

Full details live in [`../compatibility.md`](../compatibility.md) — this
page is the short "point your existing client at Tiqora" pointer into it, in
the API-reference index.

## Why this exists

If you have existing scripts, middleware, or third-party tools that talk to
a Znuny/OTRS install's GenericInterface REST webservices (peer DB 6.0–7.3)
(`nph-genericinterface.pl`), you do not have to rewrite them on day one.
Tiqora's `/znuny-compat` surface emulates the operations those clients
actually use, so you can repoint traffic at Tiqora and migrate integrations
to the native `/api/v1` (or MCP) at your own pace.

## Supported operations

| Operation | Purpose |
|---|---|
| `SessionCreate` | Authenticate; returns a `SessionID` |
| `SessionGet` | Return session key/value data |
| `SessionRemove` / `SessionDelete` | Invalidate a session |
| `TicketCreate` | Create a ticket (+ optional article) |
| `TicketUpdate` | Update fields / add an article |
| `TicketGet` | Fetch ticket(s) with articles |
| `TicketSearch` | Search by criteria |
| `TicketHistoryGet` | Ticket history lines (agent) |
| `TimeAccountingGet` | Accounted time for a user/date range |
| `OutOfOffice` | Set agent out-of-office prefs (admin) |

Both the REST-style (`HTTP::REST`) and SOAP (`HTTP::SOAP`) GenericInterface
transports are emulated for these operations — see
[SOAP transport](#soap-transport) below. Full details, gotchas, and known
deviations: [`../compatibility.md`](../compatibility.md).

## Routing model

Webservice routes are matched **at request time** against the valid
`gi_webservice_config` rows (`Provider.Transport.Config.RouteOperationMapping`,
YAML), under two prefixes:

```
/znuny-compat/Webservice/{webservice_name}{route}
/znuny-compat/WebserviceID/{webservice_id}{route}
```

...plus a fixed canonical fallback that is always available even with no
webservice configured:

| Method | Path | Operation |
|---|---|---|
| POST | `/znuny-compat/Session` | SessionCreate |
| GET | `/znuny-compat/Session/{session_id}` | SessionGet |
| DELETE | `/znuny-compat/Session/{session_id}` | SessionRemove |
| POST | `/znuny-compat/Ticket` | TicketCreate |
| GET | `/znuny-compat/Ticket/{ticket_id}` | TicketGet |
| PATCH | `/znuny-compat/Ticket/{ticket_id}` | TicketUpdate |
| GET | `/znuny-compat/TicketSearch` | TicketSearch |
| GET | `/znuny-compat/Ticket/History/{ticket_id}` | TicketHistoryGet |
| GET | `/znuny-compat/TimeAccountingGet` | TimeAccountingGet |
| POST | `/znuny-compat/OutOfOffice` | OutOfOffice |

Matching follows Znuny: the first route (operations in name order) whose
method list and pattern accept the raw path wins, and **URI and query values
win over the same key in the JSON body**. Operations are dispatched by their
configured `Type`, and `MappingInbound` / `MappingOutbound` (`Simple`, `XSLT`,
`OutOfOffice`) run around the operation.

Webservice changes made in Znuny (add, rename, edit, disable) are picked up
without a restart: every API process polls every
`TIQORA_COMPAT_WEBSERVICE_RELOAD_SECONDS` seconds (default 10), and
`POST /znuny-compat/admin/reload` (admin) refreshes at once.
`TIQORA_COMPAT_SESSION_WRITE_ZNUNY` (default `true`) lets `SessionCreate` write
Znuny `sessions` rows; see [`../compatibility.md`](../compatibility.md#session-handling)
for expiry, purge and the `SessionCheckRemoteIP` caveat.

This means operators can keep the exact webservice URL paths their
integrators already call, pointed at Tiqora after a reverse-proxy cutover —
see the nginx rewrite example in
[`../cutover.md`](../cutover.md#stage-3--repoint-nginx-genericinterface-locations-to-tiqora-compat).

## The `StateType` gotcha

Znuny's `TicketSearch` GenericInterface operation expects the filter key
`StateType` as a **singular string** (e.g. `"open"`); the plausible-looking
plural `StateTypes` is silently *ignored* by real Znuny. This is a
regression-tested behaviour in Tiqora's compat layer (both `StateType`
singular and a Tiqora-only `StateTypes` list extension are honoured — but
only `StateType` matches genuine Znuny wire behaviour). See
[`../compatibility.md`](../compatibility.md#known-gotchas-regression-tested)
for the full gotcha table (article visibility defaults, session resolution,
empty-search response shape, error format).

## Example: SessionCreate + TicketSearch

```sh
export TIQORA_URL=https://tickets.example.com

SESSION_ID=$(curl -s -X POST "$TIQORA_URL/znuny-compat/Session" \
  -H 'Content-Type: application/json' \
  -d '{"UserLogin": "agent1", "Password": "YOUR_PASSWORD"}' \
  | jq -r .SessionID)

curl -s "$TIQORA_URL/znuny-compat/TicketSearch?SessionID=$SESSION_ID&StateType=open" \
  -H 'Content-Type: application/json'
```

## SOAP transport

Point existing SOAP GenericInterface clients at:

```
POST /znuny-compat/soap/{webservice}          # canonical fallback, default NameSpace
POST /znuny-compat/Webservice/{name}          # from gi_webservice_config, own NameSpace
POST /znuny-compat/WebserviceID/{id}
```

The operation is dispatched from the SOAP Body wrapper element (e.g.
`<TicketGet>...</TicketGet>`), not the URL — one endpoint per webservice
handles all supported operations, matching Znuny's own `HTTP::SOAP` transport.
Auth (`UserLogin`/`Password`, `CustomerUserLogin`/`Password`, or
`SessionID`) is passed as elements inside the operation wrapper, same as
REST body params. See
[`../compatibility.md#soap-transport`](../compatibility.md#soap-transport)
for the full request/response example, namespace configuration, XXE
mitigation (`defusedxml`), and documented deviations from Znuny's transport
(no `SOAPAction` strict-match enforcement, no WSDL auto-serving — Znuny
doesn't serve one either).

## Migrating a webservice from Znuny's own GenericInterface

1. Point a non-production webservice consumer at Tiqora's compat layer.
2. Run a soak of real client traffic; compare against Znuny's own responses.
3. Move the production reverse-proxy route (see
   [`../cutover.md`](../cutover.md) Stage 3) once diffs are clean.
4. Plan a later move to `/api/v1` (see [`rest-v1.md`](rest-v1.md)) or MCP
   (see [`mcp.md`](mcp.md)) for new integrations — compat is a bridge, not
   the long-term integration surface.

## Debugger for provider webservices

Calls to `/znuny-compat` webservices are logged in the Tiqora debugger (Admin → Webservices →
Protokoll, direction "Eingehend"), next to the entries Znuny wrote itself. Rows show the
operation, remote IP and the request and response lines with secrets (passwords, session ids,
tokens, `Authorization`) masked. Entries older than `gi.debugger.retention_days` (default 14)
are removed. See [compatibility.md](../compatibility.md#debugger).

## What is not emulated

Short list — full tables with rationale live in
[`../compatibility.md#what-is-not-emulated-and-why`](../compatibility.md#what-is-not-emulated-and-why):

- Requester/outbound GenericInterface is a separate, partial subsystem (see [Requester and invokers](../compatibility.md#requester-and-invokers-tiqora-calls-external-systems))
- Custom OPM operations; Package Manager
- `Mapping::Znuny` and `Mapping::Test` (500 fault "not supported")
- `Force*`/`Exclude*` notification user ids are validated but not applied (as in Znuny)
- Escalation detail keys in TicketGet; `SortOutbound`

See [`../compatibility.md`](../compatibility.md) for parameter-level gaps,
the list of [changes for existing clients](../compatibility.md#changes-for-existing-genericinterface-clients),
and golden-master coverage notes.
