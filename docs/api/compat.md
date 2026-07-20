# GenericInterface compatibility (`/znuny-compat`)

Full details live in [`../compatibility.md`](../compatibility.md) — this
page is the short "point your existing client at Tiqora" pointer into it, in
the API-reference index.

## Why this exists

If you have existing scripts, middleware, or third-party tools that talk to
a Znuny 6.5 install's GenericInterface REST webservices
(`nph-genericinterface.pl`), you do not have to rewrite them on day one.
Tiqora's `/znuny-compat` surface emulates the operations those clients
actually use, so you can repoint traffic at Tiqora and migrate integrations
to the native `/api/v1` (or MCP) at your own pace.

## Supported operations (V1)

| Operation | Purpose |
|---|---|
| `SessionCreate` | Authenticate; returns a `SessionID` |
| `TicketCreate` | Create a ticket (+ optional article) |
| `TicketUpdate` | Update fields / add an article |
| `TicketGet` | Fetch ticket(s) with articles |
| `TicketSearch` | Search by criteria |

SOAP is out of scope; only the REST-style GenericInterface transport is
emulated. Full details, gotchas, and known deviations:
[`../compatibility.md`](../compatibility.md).

## Routing model

Routes are mounted **dynamically** at startup from each valid
`gi_webservice_config` row's `Provider.Transport.Config.RouteOperationMapping`
(YAML), under two prefixes:

```
/znuny-compat/Webservice/{webservice_name}{route}
/znuny-compat/WebserviceID/{webservice_id}{route}
```

...plus a fixed canonical fallback that is always available even with no
webservice configured:

| Method | Path | Operation |
|---|---|---|
| POST | `/znuny-compat/Session` | SessionCreate |
| POST | `/znuny-compat/Ticket` | TicketCreate |
| GET | `/znuny-compat/Ticket/{ticket_id}` | TicketGet |
| PATCH | `/znuny-compat/Ticket/{ticket_id}` | TicketUpdate |
| GET | `/znuny-compat/TicketSearch` | TicketSearch |

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

## Migrating a webservice from Znuny's own GenericInterface

1. Point a non-production webservice consumer at Tiqora's compat layer.
2. Run a soak of real client traffic; compare against Znuny's own responses.
3. Move the production reverse-proxy route (see
   [`../cutover.md`](../cutover.md) Stage 3) once diffs are clean.
4. Plan a later move to `/api/v1` (see [`rest-v1.md`](rest-v1.md)) or MCP
   (see [`mcp.md`](mcp.md)) for new integrations — compat is a bridge, not
   the long-term integration surface.

## What is not emulated

- The full GenericInterface provider/consumer framework and SOAP envelopes.
- Custom operations only available as Znuny packages.
- Package Manager remote install.
- `TicketHistoryGet`, `TimeAccountingGet` (return HTTP 501).

See [`../compatibility.md`](../compatibility.md) for the complete,
up-to-date list including known uncertainties and golden-master validation
notes.
