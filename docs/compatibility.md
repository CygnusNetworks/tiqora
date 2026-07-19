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

Golden-master tests (Phase 2) compare request/response pairs and DB side effects
against a real Znuny container.

## Auth surface for integrators

1. **Compat SessionCreate** — legacy tools, SessionID cookie/header style.
2. **API keys** — preferred for MCP and modern automation (same permission engine).
3. **OIDC / Kerberos** — UI and `/api/v1` (Phase 3); not required for basic
   GenericInterface parity.

## What is not emulated

- Full GenericInterface provider/consumer framework
- Arbitrary custom operations registered only as Znuny packages
- SOAP envelope processing
- Package Manager remote install

Integrators needing those should migrate to `/api/v1` or MCP.

## Migration guidance

1. Point a non-production webservice at Tiqora’s compat layer.
2. Run the golden-master suite and a soak of real client traffic.
3. Move production webservice routes (or reverse proxy) when diffs are clean.
4. Plan a later move to `/api/v1` for new integrations.
