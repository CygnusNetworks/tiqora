# GenericInterface compatibility layer

Tiqora exposes a **compatibility surface** that emulates the Znuny/OTRS
GenericInterface operations most integrators use (behaviour aligned with the
6.x/7.x wire surface; peer DB support is **6.0–7.3** — see
[support-matrix.md](support-matrix.md)). The native API is always `/api/v1`
(OpenAPI). Compat exists so existing scripts, middleware, and third-party tools
can migrate gradually.

## Scope

| Operation | Purpose |
|---|---|
| `SessionCreate` | Authenticate; return SessionID usable with subsequent calls |
| `SessionGet` | Return SessionData key/value list for a SessionID |
| `SessionRemove` / `SessionDelete` | Invalidate a SessionID (Znuny `sessions` + Tiqora Redis) |
| `TicketCreate` | Create ticket (+ optional article) |
| `TicketUpdate` | Update fields / add article |
| `TicketGet` | Fetch ticket(s) with articles |
| `TicketSearch` | Search by criteria |
| `TicketHistoryGet` | Agent-only ticket history dump |
| `TimeAccountingGet` | Accounted time rows (`time_accounting`) for a user/date range |
| `OutOfOffice` | Set agent out-of-office preferences (admin group) |

Both transports Znuny documents for these operations are supported:
the REST-style `HTTP::REST` connector, and `HTTP::SOAP` (see
[SOAP transport](#soap-transport) below).

## Routing model

Operations are **fixed implementations** in `api/compat/`. Webservice routes
are matched **per request** against the webservices in `gi_webservice_config`
(`RouteOperationMapping`), with sensible default routes as fallback when no
webservice is configured. Nothing is mounted at startup, so a webservice
that is added, renamed, edited or disabled in Znuny is picked up without a
restart (see [Hot reload](#hot-reload)).

This mirrors Znuny’s flexibility: operators can keep their existing webservice
URL paths pointing at Tiqora after a reverse-proxy cutover.

## Mappings

A webservice operation may carry an inbound and an outbound mapping
(`MappingInbound` / `MappingOutbound`). Tiqora runs them on the request data
before the operation and on the answer after it, like Znuny's provider.

| Mapping type | Supported | Notes |
|---|---|---|
| `Simple` | yes | Port of `Mapping::Simple`, checked against Znuny's own unit-test vectors |
| `XSLT` | yes | See the security note below |
| `OutOfOffice` | yes | CSV bulk entries (`OutOfOfficeEntriesCSVString`) |
| `Znuny`, `Test` | no | The operation answers with a 500 fault "Mapping type '...' is not supported by Tiqora" (kept on purpose; the recursive `Znuny` mapper belongs to `WebserviceDropdown` fields) |

Mapping regular expressions from webservice configs run on the `regex`
package with a per-match time limit (0.2 s) and are compiled when the
webservice is loaded. A pattern that Python cannot compile (Perl-only syntax)
makes that one webservice answer with a 500 fault naming the problem;
other webservices keep working. A pattern that runs into the time limit
fails the request with a mapping error. Small semantic differences from Perl
regexes are possible: for example `\Z` in a pattern is the absolute end of the
string in Tiqora, while Perl's `\Z` also matches before a trailing newline (Perl
`\z` behaves like Tiqora's `\Z`). Every mapping runs in a worker thread, so a
slow mapping never blocks other requests.

XML request data is read like Znuny's `XML::Simple`, with two differences:
internal entities declared in the request are dropped, not expanded (external
entities are never resolved), and `xmlns` declarations are not reported as
attributes when `KeepAttributes` is on. `DataInclude` entries are included when
they are Perl-true (an empty hash counts, `"0"` does not), as in `XSLT.pm`.

**XSLT security note.** Templates run in a sandbox: no file access and no
network access, no `document()`, no `exsl:document`. The only external
file a template can import is `OTRSFunctions.xsl`; any href ending in that
name (`/opt/otrs/Kernel/...`, `/opt/znuny/Kernel/...`, relative `Kernel/...`)
is served from the copy bundled with Tiqora, so templates written for Znuny
keep working. Every other import or include fails with a readable error
("External file '...' is not allowed in Tiqora"). The output is capped at
256 MB; the cap is checked when the transform has finished, not while it runs.
libxslt cannot be interrupted, so a template has no
wall-clock timeout; templates are written by the administrator and are
trusted. Documents above 10 MB per text node need the Znuny setting
`LibXML::EnableHugeXMLDataSupport`, exactly as in Znuny.

## Hot reload

Every `tiqora-api` process polls `gi_webservice_config` every 10 seconds
(`TIQORA_COMPAT_WEBSERVICE_RELOAD_SECONDS`) and compares a fingerprint of the
rows; only a change triggers a rebuild. `POST /znuny-compat/admin/reload` (admin
only) refreshes at once and wakes the other processes through Redis; polling
alone is enough if that message is lost. A webservice that is renamed or
disabled stops answering (404) after the next tick and the new name works;
requests already running finish on the old snapshot. A database error during a
refresh keeps the previous snapshot. A broken row (invalid YAML, unknown
mapping type, bad regex, or any unexpected error while it is built) only affects
its own webservice.

The fingerprint covers the `gi_webservice_config` rows only. A SysConfig change
that a webservice uses through an `<OTRS_CONFIG_...>` placeholder, or a change of
`LibXML::EnableHugeXMLDataSupport`, is picked up when a webservice row changes or
after `POST /znuny-compat/admin/reload`. Placeholders are resolved from the
SysConfig in the database; values set only in Znuny's `Config.pm` are not seen.

## Session handling

- Prefer validating SessionID against Znuny’s `sessions` table so a session
  created in Znuny still works when traffic is split or cut over.
- Tiqora-native sessions remain Redis-backed for `/api/v1` and the UI.
- **SessionCreate writes Znuny session rows.** By default the compat
  `SessionCreate` returns a Znuny-format 32-character SessionID backed by
  rows in the Znuny `sessions` table (the keys of Znuny's `AuthSession::DB`),
  with no Redis entry. The ID therefore works in Znuny and in Tiqora, with
  Znuny's idle and absolute expiry (`SessionMaxIdleTime`, `SessionMaxTime`).
  Set `TIQORA_COMPAT_SESSION_WRITE_ZNUNY=false` to fall back to Redis tokens;
  no rows are written either once Tiqora owns the schema. The password hash
  (`UserPw`) is deliberately not copied into the row, and preference rows are
  copied from an allow-list only (no 2FA seeds, tokens or API keys).
  Calls made through the GI do not refresh `UserLastRequest`.
- **Validity.** `SessionGet` checks the session first, like Znuny's
  `SessionGet.pm` (`CheckSessionID`): an expired session (idle or absolute
  limit), a disabled account or a failed remote-IP binding answers
  `SessionGet.SessionInvalid` instead of returning data. This is the same check
  every authenticated compat call makes.
- **Purge.** Expired `sessions` rows are removed by the worker loop
  `znuny_session_purge` (every 300 s, always on, interval
  `TIQORA_ZNUNY_SESSION_PURGE_INTERVAL_SECONDS`). It is a port of Znuny's
  `GetExpiredSessionIDs` (session start + `SessionMaxTime` or last request +
  `SessionMaxIdleTime` is in the past), the same job as Znuny's
  `Maint::Session::DeleteExpired`. Running both is harmless only while both use
  the same limits: Tiqora reads `SessionMaxTime` / `SessionMaxIdleTime` from the
  SysConfig in the database. If Znuny overrides them in `Config.pm`, Tiqora
  purges with the database values and may end Znuny web sessions earlier. The
  loop cannot be switched off; keep the limits in the SysConfig.
- **Revocation.** These delete the person's Znuny session rows first (own
  transaction), then the Redis sessions:
  - agents: disabling the account (edit or delete), an admin password reset,
    password setup;
  - customer users: disabling the account (edit, delete or bulk edit) and an
    admin password reset - Redis here means the portal sessions and the
    GenericInterface customer tokens;
  - a customer's own password change in the portal (Znuny rows only; the
    customer's portal session stays) and GDPR erasure (Znuny rows).
- **`SessionCheckRemoteIP` caveat.** Znuny's `SessionCheckRemoteIP` defaults
  to `1`. A session created by Tiqora stores the address Tiqora itself saw
  (the socket peer, or the trusted-proxy address), which can differ from what
  Znuny's web server sees for the same client. Znuny then rejects the session
  as coming from a different IP. If you let clients create a session on Tiqora
  and use it on Znuny, either set `SessionCheckRemoteIP` to `0` in Znuny or make
  sure both systems see the same client address (same reverse proxy,
  `TIQORA_TRUSTED_PROXIES`). Tiqora's own check is off by default
  (`TIQORA_COMPAT_SESSION_CHECK_REMOTE_IP=false`) for the same reason.
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

Golden-behaviour tests in `backend/tests/test_compat_operations.py` cover the
core operations including the gotchas above with seeded MariaDB data.

## Implemented routes

### Canonical fallback (always available)

| Method | Path | Operation |
|--------|------|-----------|
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
| POST | `/znuny-compat/admin/reload` | Refresh the webservice registry now (admin only) |

### Dynamic routes (from `gi_webservice_config`)

Tiqora reads all valid `gi_webservice_config` rows, YAML-parses
`Provider.Transport.Config` (`RouteOperationMapping`, `Operation` mappings) into
an in-memory registry, and answers requests under:

```
/znuny-compat/Webservice/{webservice_name}{route}
/znuny-compat/WebserviceID/{webservice_id}{route}
```

The route is matched at request time against the raw request path, the way
Znuny does (the path is unescaped once, so `%2F` and `%2520` work). The first
route, in operation-name order, whose method list and pattern accept the
request wins; an empty or missing `RequestMethod` matches any method, and a
bare string instead of a list also matches any method. Operations are
dispatched by their `Type`; an unsupported `Type` returns HTTP 501.
Znuny `:VariableName` path segments become request parameters.

Values from the URI and the query string win over the same key in the JSON
body (Znuny `HTTP::REST` behaviour; this is the reverse of what Tiqora did
before). `X-OTRS-Header-*` request headers override both. A first query value
of `0` becomes an empty string, JSON booleans become `1`/`0`, and numbers are
normalised like Perl. Transport errors (no matching route, body too large,
undecodable body) are answered as `text/plain`, like Znuny.

Transport differences from Znuny's `HTTP::REST`:

- A body sent with chunked transfer encoding and no `Content-Length` is not
  read: the operation gets the URI and query data only.
- `ParserBackend`, `PostParamName` and multipart form bodies are not supported;
  the body is always parsed as JSON.
- Raw (unescaped) UTF-8 bytes in the URL cannot reach Tiqora (the HTTP server
  rejects them); clients must percent-encode them, which Znuny also accepts.
- `HEAD` and `OPTIONS` requests answer 405 (Znuny: 500).
- The webservice name in `/Webservice/{name}` is matched case-sensitively
  (Znuny's lookup on MariaDB is case-insensitive). Use the exact name.

### Admin reload

`POST /znuny-compat/admin/reload` (requires an admin) refreshes the webservice
registry immediately and wakes the other API processes through Redis. See
[Hot reload](#hot-reload); no restart is needed. It answers
`{"status": "ok", "webservices_loaded": <n>, "webservices": [<names>]}`.

## SOAP transport

Znuny's `HTTP::SOAP` GenericInterface transport
(`Kernel/GenericInterface/Transport/HTTP/SOAP.pm`) is emulated by
`api/compat/soap.py` (codec) + the same routes/dispatch table in
`api/compat/router.py` — SOAP requests go through the *identical*
operation handlers as REST; only the wire format differs.

### Endpoints

| Method | Path | NameSpace used |
|--------|------|-----------------|
| POST | `/znuny-compat/soap/{webservice}` | Default (`http://www.otrs.org/TicketConnector/`) — always available, `{webservice}` is a free label |
| POST | `/znuny-compat/Webservice/{name}` | That webservice's `Provider.Transport.Config.NameSpace` (from `gi_webservice_config`) |
| POST | `/znuny-compat/WebserviceID/{id}` | Same, addressed by numeric ID |

Unlike REST, SOAP has **no per-operation route mapping** in Znuny — a single
endpoint per webservice accepts any of the supported operations. The
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
`ErrorCode` → status mapping REST uses (see [Deviations kept on
purpose](#deviations-kept-on-purpose)); operation errors are `Client` faults.
An operation the webservice does not configure answers a `Server` fault
"OperationType for operation '...' is not registered" with HTTP 500. A body
longer than the webservice's `MaxLength` answers 413 "Request Entity Too Large"
(`text/plain`) before it is parsed, like `SOAP.pm`; a body sent without
`Content-Length` (chunked) is counted while it streams in and refused as soon as it
passes the limit, without reading the rest.

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
- SOAP 1.2 is accepted (mirrors the request's Content-Type/envelope
  namespace in the response) but Znuny's SOAP transport is primarily
  exercised with SOAP 1.1 (`SOAP::Lite` default) in practice.

## StateType / StateTypes gotcha (documented deviation)

Znuny TicketSearch expects `StateType` as a **singular string** (e.g. `"open"`).
The plural `StateTypes` is a Tiqora extension that accepts a list; Znuny ignores it.
Both forms are supported in Tiqora for convenience.

## Auth surface for integrators

1. **Compat SessionCreate** — legacy tools, SessionID cookie/header style.
2. **SessionID** — validated against Znuny's `sessions` key-value table; a
   SessionID that has no row there is looked up in Tiqora's Redis token store
   (tokens issued while `TIQORA_COMPAT_SESSION_WRITE_ZNUNY=false`).
3. **API keys** — preferred for MCP and modern automation (same permission engine).
4. **OIDC / SAML / LDAP / Kerberos** — UI and `/api/v1` (agents) and
   `/api/portal` (customers); not required for basic GenericInterface parity.
   Details: [Identity providers and Znuny parity](#identity-providers-and-znuny-parity).

## Identity providers and Znuny parity

Tiqora's SSO covers what Znuny 7.3 offers through `Auth::SAML`, `CustomerAuth::SAML` and
`AuthSyncModule` (`Auth::Sync::LDAP`), and goes beyond it. Setup guide:
[guide/identity-providers.md](guide/identity-providers.md).

| Capability | Znuny 7.3 | Tiqora |
|---|---|---|
| SAML agents and customers | configured in SysConfig / `Config.pm` | any number of SAML and OIDC providers, each for agents, customers or both; SP- and IdP-initiated login, single logout (SP- and IdP-initiated) |
| Several IdPs | no | yes (login-page buttons per provider, sort order) |
| Env/`Config.pm` OIDC and LDAP | n/a | `TIQORA_OIDC_*` / `TIQORA_LDAP_*` stay supported, shown read-only as `env-oidc`, `env-ldap`, `env-customer-ldap` |
| Account creation on first login | `AuthSyncModule` creates / updates the user | JIT per provider, optional e-mail domain allow-list, e-mail validated like `CheckEmail`, `CustomerUserEmailUniqCheck` honoured |
| Attribute mapping | `UserSyncMap` | ordered source attributes per profile field (`login`, `email`, `first_name`, `last_name`, `customer_id`, `mobile`) |
| Initial groups on creation | `UserSyncInitialGroups` | "Gruppenrechte/Rollen für neue Konten" (first login only) |
| Role/group sync from LDAP groups | `UserSyncGroupsDefinition`, `UserSyncRolesDefinition` | rule attribute `ldap:group_dn` |
| Role/group sync from attributes | `UserSyncAttribute{Groups,Roles}Definition` | rules on any OIDC claim, SAML attribute or LDAP attribute |
| Permission write semantics | replaces the full permission key set per (user, group) | same per-group replacement; in managed mode the result is "wanted + manual keys", collapsed to `rw` when present |

Differences (deliberate):

- **Sync mode.** Default is `managed`: Tiqora removes only assignments that a rule created
  itself (tracked in `tiqora_identity_managed_assignment`) and never downgrades or removes
  manual ones. Znuny's replace-everything behaviour is available per provider as
  `authoritative` ("Wie Znuny"). Both skip the sync when the IdP did not release a rule's attribute.
- **No `NestedGroupSearch`**: nested AD groups are not resolved.
- **No customer group sync**: role/group sync is agent-only; customers are matched/created
  but never granted groups.
- **Group search errors** (timeouts, referrals, any non-success code other than `noSuchObject`)
  skip the sync for that login instead of stripping rights; `noSuchObject` counts as "not a member"
  (as in Znuny).
- **SAML SHA-1** XML signatures are rejected unless a provider opts in; **LDAP TLS** is verified by
  default for UI-configured directories (env directories: `TIQORA_LDAP_TLS_VERIFY`, default off, with
  a startup warning).
- **OIDC** id_tokens are always verified (discovery issuer, JWKS, audience, nonce); Entra's
  `{tenantid}` multi-tenant issuer is supported with an optional tenant allow-list.
- SSO logins skip TOTP/passkey 2FA unless the provider requires proof of a second factor from the IdP
  (`require_mfa_claim`, Tiqora-only, Znuny has no equivalent); then a missing proof falls back to
  Tiqora's TOTP/passkey or refuses the login. Customers can enroll TOTP and passkeys for password logins.
- Customer 2FA tables are removed only by the GDPR erasure, not when a customer user is deleted.

## SCIM 2.0 provisioning

Znuny has no SCIM endpoint; this is a Tiqora addition (`/scim/v2`, RFC 7643/7644) so an IdP can create,
update and deactivate agents and customers and push agent groups. Setup:
[guide/identity-providers.md](guide/identity-providers.md#bereitstellung-mit-scim). Accounts end up as
normal Znuny `users` / `customer_user` rows, so Znuny reads them like any other (golden test
`tests/golden/test_identity_scim.py`).

Supported subset:

| Area | Support |
|---|---|
| Auth | per-provider bearer token (`tqscim_…`), sha256-hashed, up to 5 active per provider, scoped to agents or customers |
| Resources | `Users`, `Groups` (agents only); GET/POST/PUT/PATCH/DELETE; discovery: `ServiceProviderConfig`, `ResourceTypes`, `Schemas` |
| User attributes | `userName`, `externalId`, `name.givenName`/`familyName`, `displayName`, `emails`, `phoneNumbers` (type `mobile`), `active`; the enterprise extension, `password` and `groups` are accepted and ignored |
| Filter | `eq` on `id`, `userName`, `externalId`, `emails.value` (Groups: `id`, `displayName`, `externalId`), one condition, at most 1000 characters; anything else answers 400 `invalidFilter` |
| Paging | `startIndex` / `count`, `count` capped at 200 (default 100) |
| PATCH | `add` / `replace` / `remove` with and without path; group member add/remove |

Deviations from the RFC (deliberate):

- **No Bulk, no ETag, no changePassword, no sorting**; `ServiceProviderConfig` says so.
- **`and` / `or` / `co` / `sw` / value-path filters** answer `400 invalidFilter`.
- **DELETE deactivates**: `DELETE /Users/{id}` sets `valid_id = 2` and hides the SCIM resource (404 afterwards);
  Znuny rows are never deleted. `active=false` is the same without hiding. GDPR erasure is a separate,
  explicit action; delete in the IdP first or it re-creates the account.
- **Groups do not grant anything themselves**; they feed role rules (`scim:groups`, `scim:group_ids`).
  No nested groups; customers have no groups.
- **No companies, no API-key area `scim`**: SCIM never creates customer companies.
- **Adoption is opt-in** (`scim_adopt_existing`); otherwise a clash with an existing login is `409 uniqueness`.
- **Uniqueness** is case-insensitive on `userName`; request bodies are capped (2 MB), group requests at 5000 members.

## Requester and invokers (Tiqora calls external systems)

Tiqora can run the **invokers** of the webservices stored in `gi_webservice_config`: when a ticket
changes, it calls the remote system exactly as Znuny's `Kernel::GenericInterface::Requester`
would. It reads the same webservice YAML; there is no editor in Tiqora (Admin → Webservices is
read-only plus dry run, debugger and a few Tiqora-only settings). The whole requester is **off**
until you switch Znuny's event handler off and Tiqora's on (admin → Dienste → "Ausgehende
Webservice-Aufrufe"); the procedure is in
[parallel-operation.md](parallel-operation.md#taking-over-genericinterface-invokers).

Tiqora verhält sich so: the requester runs in the worker, never inside the request that changed
the ticket, so a slow or dead remote system cannot block an agent.

### Supported

| Area | Support |
|---|---|
| Invoker type | `Ticket::Generic` (the stock `Invoker::Ticket::Generic`). Other invoker types, including `Generic::Tunnel`, are shown as unsupported and never fire |
| Transports | `HTTP::REST` (all methods except `CONNECT` and `TRACE`) and `HTTP::SOAP` |
| Mappings | `Simple`, `XSLT`, `OutOfOffice` on request and response (same engine as the provider side). `Znuny` and `Test` mappings are not supported |
| Events | Fired: `TicketCreate`, `TicketStateUpdate`, `TicketQueueUpdate`, `TicketOwnerUpdate`, `TicketResponsibleUpdate`, `TicketPriorityUpdate`, `TicketTypeUpdate`, `TicketServiceUpdate`, `TicketSLAUpdate`, `TicketTitleUpdate`, `TicketCustomerUpdate`, `TicketPendingTimeUpdate`, `TicketLockUpdate`, `TicketArchiveFlagUpdate`, `TicketAccountTime`, `TicketMerge`, `TicketSubscribe`, `TicketUnsubscribe`, `ArticleCreate` and `TicketDynamicFieldUpdate_<Name>`; synchronous (`Asynchronous: 0`) and background invokers |
| Retries | `ErrorHandling` of type `RequestRetry` (interval, factor, maximum interval, maximum count, maximum period, `ScheduleRetry`) |
| Conditions | Znuny's condition syntax (`Type`, `Fields`, `Fields->Match`, `Any`/`All`/`None`/`Only`, `and`/`or`/`xor`, regex `[Rr][Ee]#` and plain compare); evaluated with the ticket data at the time the worker processes the event |
| Response actions | `OTRS_TicketTitleUpdate`, `OTRS_TicketServiceSet`, `OTRS_TicketTypeSet`, `OTRS_TicketQueueSet`, `OTRS_TicketSLASet`, `OTRS_TicketStateSet`, `OTRS_TicketPrioritySet`, `OTRS_TicketCustomerSet`, `OTRS_TicketOwnerSet`, `OTRS_TicketResponsibleSet`, `OTRS_TicketHistoryAdd`, `OTRS_TicketArticleAdd`, `OTRS_TicketCreate`, `OTRS_TicketDynamicFieldSet_<Name>`, `OTRS_AsynchronousInvokerExecution`. All writes go through the normal ticket services (history, escalation, events), as user 1 like Znuny |
| `ExecuteInvoker` process action | Handler in `gi/process_action.py`, registered in the process `TRANSITION_ACTIONS` registry; see deviation 8 |

### Authentication matrix

| Method | REST | SOAP |
|---|---|---|
| None | yes | yes |
| BasicAuth (user, password) | yes | yes |
| Client certificate (`SSLCertificate`, `SSLKey`, `SSLPassword`, `SSLCAFile`, `SSLCADir`) | yes, files below `TIQORA_GI_CERT_DIRS` only | yes, same |
| Client certificate via `X509` (`UseX509`, `X509CertFile`, `X509KeyFile`, `X509CAFile`) | yes, files below `TIQORA_GI_CERT_DIRS` only | n/a |
| OAuth2 token (`AuthType: OAuth2Token`, `OAuth2TokenConfigID`; the token replaces `<OTRS_OAUTH2_TOKEN>` in `AdditionalHeaders`) | yes | n/a |
| Proxy with `ProxyUser`/`ProxyPassword` | yes | yes |
| `AdditionalHeaders` (REST) | yes | n/a |
| JWT | **nein** (configuration error) | n/a |
| NTLM | n/a | **nein** (configuration error) |

### Not fired / not supported ("nein")

| Item | Behaviour |
|---|---|
| `OTRS_TicketArticleCreateEvent` response action | **nein**: shown in the debugger as an error; no event is raised |
| Ticket events not in the fired list above: `Escalation*` (`EscalationResponseTimeStart/Stop/NotifyBefore`, `EscalationUpdateTime*`, `EscalationSolutionTime*`), `TicketFlagSet`/`TicketFlagDelete`, `TicketDelete`, `TicketUnlockTimeoutUpdate` and every `Article*` event other than `ArticleCreate` | **nein**: never fired, although Znuny writes the escalation events to `ticket_history`; invokers bound to them never run |
| Events of objects other than tickets (`Appointment`, `ConfigItem`, `CustomerUser`, `User`, ... event object types) | **nein**: not fired, the webservice page marks such invokers "wird von Tiqora noch nicht ausgelöst" |
| `Module` conditions | never match (Tiqora cannot load Perl modules) |
| `jq#` condition fields | never match (Znuny without Jq behaves the same) |
| JWT, NTLM | see matrix |

### Deviations from Znuny

Each is deliberate. Numbering is used in the code, the tests and the golden-master normaliser.

1. **Debugger storage.** Tiqora writes its own tables (`tiqora_gi_debug_entry`, `tiqora_gi_debug_content`), shaped like Znuny's `gi_debugger_entry*`, and never writes Znuny's. The Tiqora debugger view shows both and marks the source ("Tiqora" or "Znuny"). Provider calls to `/znuny-compat` are logged too.
2. **Event source.** Tiqora does not hook into ticket code; a follower reads new `ticket_history` rows and derives the events from them. The same rows already feed the plugin event system, so there is one source and no double events.
3. **Synchronous invokers run in the worker**, on a single-slot lane that keeps the order of events, not inside the request that changed the ticket. Conditions therefore see the ticket a few seconds later than in Znuny.
4. **The debugger never stores secrets.** Znuny's REST requester logs the raw request parameters including the `Authorization` header. Tiqora logs method, URL (user info and secret query parameters masked), masked headers and the body size, and masks every known secret value (Basic and proxy passwords, certificate passwords, all `AdditionalHeaders` values of four or more characters) in every row. Debugger content is JSON, not `Data::Dumper`. Znuny's own rows are masked on read with the same rules and marked "Von Tiqora maskiert".
5. **Stricter outbound rules.** No redirects are followed, `CONNECT` and `TRACE` are refused, environment proxies are ignored. Targets in private (RFC 1918/ULA) or carrier-grade NAT ranges are refused unless the webservice's "Interne Ziele erlauben" list (Tiqora-only setting, one host or network per line) names them. Loopback, link-local (including the cloud metadata address), multicast and reserved addresses are never allowed, even when listed. Certificate files are read only from `TIQORA_GI_CERT_DIRS`. `SSLNoHostnameVerification` only works after an admin switches "Unsichere TLS-Verbindung erlauben" on for that webservice. Timeout and response size are capped by `TIQORA_GI_MAX_TIMEOUT_SECONDS` and `TIQORA_GI_MAX_RESPONSE_BYTES`.
6. **`ReuseClient` and `KeepAlive` are ignored.** Every request uses a fresh connection, pinned to the address that was checked. With a configured proxy (`UseProxy: Yes`) the rules of deviation 5 apply to the proxy address; the target is resolved by the proxy and is not checked by Tiqora. An `http://` proxy is reached by its checked address. An `https://` proxy is reached by name, because TLS needs it, so its name is resolved a second time when the connection is opened (the check does not protect the proxy host against DNS rebinding).
7. **JWT (REST) and NTLM (SOAP)** are configuration errors with a plain message.
8. **`ExecuteInvoker` is always queued.** The process action takes Znuny's parameters (`Webservice` by name, `Invoker`, `Asynchronous`, `UserID`), but `Asynchronous` is accepted and ignored: the call never runs inside the dialog submit. It also works without the takeover flag, because it is an explicit action and not an event handler that Znuny also runs.
9. **`OldTicketData`** is not part of the event block (it cannot be rebuilt from history). `OTRS_TicketArticleCreateEvent` and non-ticket event objects are not supported (see above).
10. **`Module` and `jq#` conditions** never match.
11. **Retries are new jobs.** A retry is queued as a new job with a due time and the `PastExecutionData`, like Znuny's `TaskAdd`, instead of Znuny's in-place `JobRetry`. A job that was interrupted (worker crash) before the request was sent is claimed at most three times. When a call fails before anything was sent and is retried, the original job is marked `rescheduled` in the same database transaction that queues the retry, so a crash can neither start a second retry chain nor lose the retry. If the worker is interrupted after that point, the original job is failed with "Der Aufruf ist fehlgeschlagen und wurde danach unterbrochen; ein neuer Versuch ist bereits eingeplant." (a call interrupted after sending keeps "ob er angekommen ist, ist unklar").
12. **Two additions beyond Znuny.** "Erneut senden" in the debugger re-runs the invoker with the stored event data and the **current** ticket data. "Probelauf" builds the request for a ticket, shows method, URL, masked headers and body, and never sends it.
13. **`Tunnel`.** Znuny 6.5 has no `ErrorHandling::Tunnel`; `Invoker::Generic::Tunnel` is an invoker type for provider-to-requester tunnelling and is out of scope (shown as unsupported).
14. **`OTRS_AsynchronousInvokerExecution` is limited.** The remote system chooses webservice, invoker and data of every queued call, so Tiqora adds two limits that Znuny does not have. At most 10 calls are queued per response; further items are dropped. A call queued by a response action counts as one level deeper than the call whose response queued it (event, process and "Erneut senden" calls are level 0); items that would reach level 3 are refused, so a chain ends after two such hops. Both cases write an error line to the debugger, but the response itself still counts as successful (a failure would make `RequestRetry` repeat the call). `PastExecutionData` and `Asynchronous` from the remote are ignored: a queued call always starts as a first, background run. Znuny's `MaximumParallelInstances` is not honoured; the job lanes limit concurrency.
15. **Events during a worker outage are not sent.** Znuny queues the call inside the request that changed the ticket, so it loses nothing while its daemon is down. Tiqora's follower reads `ticket_history` and remembers when it last ran. If it has not run for 180 seconds (or longer with a long tick interval: 60 s plus three intervals), it starts again at the current end of the history and does not send the calls for the changes made in between: replaying a stale backlog to the remote systems (duplicate tickets there) is the riskier failure. The skipped history rows are logged as the worker warning `gi_requester_cursor_reset`; such calls have to be repeated by hand. See [parallel-operation.md](parallel-operation.md#enabling-the-takeover).

### Debugger

Admin → Webservices → a webservice → Protokoll lists provider and requester calls from both
sources in one view (filter by direction, source, time and IP). Tiqora writes to its own tables
only; rows of Znuny's `gi_debugger_entry*` are read, masked and shown beside them. Entries
older than `gi.debugger.retention_days` (default 14) are deleted by the worker (service
"Webservice-Protokoll aufräumen"). The GDPR erasure deletes entries linked to the erased
customer's tickets in every mode (not backed up). A provider call is linked to a ticket when
its request names exactly one `TicketID`, or when it created one (`TicketCreate`); other
provider calls, such as a `TicketSearch` over several tickets, are only covered by the
retention. Znuny's own `gi_debugger_*` rows, which the view also shows, are never erased by
Tiqora; Znuny's debugger cleanup removes them.

## What is not emulated (and why)

The `/znuny-compat` routes are a **Provider-side bridge** for the GenericTicketConnector surface
and a few core ops. The **Requester** side (Tiqora calling external systems) is a separate
subsystem, see [Requester and invokers](#requester-and-invokers-tiqora-calls-external-systems).
Items below are intentionally incomplete or out of scope.

### Out of scope (product / architecture)

| Gap | Why deferred |
|-----|----------------|
| Custom OPM operations (e.g. CMDB `ConfigItem*`) | Installation-specific; no stable core wire contract |
| Package Manager remote install | Deploy/admin concern, not ticket integrator wire |
| WSDL auto-serving | Znuny does not serve one either — see [SOAP transport](#soap-transport) |

### TicketCreate / TicketUpdate

`ArticleSend: 1` (agents, TicketCreate and TicketUpdate) stores the article like an agent reply (signature, threading headers, `EmailSecurity` — see [crypto.md](crypto.md)) and sends it from the worker, like Znuny's `MailQueue`: each mail of the request (every `ArticleSend` article and every auto response) is one background job (kind `gi_mail`) committed together with the ticket and articles, and the request answers without waiting for SMTP. The worker rebuilds the mail from the stored article (a signed or encrypted mail is sent from its stored bytes, unchanged). The `worker` process must run for these mails. Delivery follows `Kernel/System/MailQueue.pm` with its default settings:

- **Order:** the mails run in their own job lane with one slot, so they go out one after the other in the order they were queued (Znuny's single `MailQueue` daemon task), and long jobs such as ticket exports never delay them. A mail waiting for a retry does not hold back the mails behind it (Znuny skips items whose `DueTime` lies ahead too), so after a temporary error a later mail of the same request can leave first.
- **Retries:** at most 3 attempts (`MailQueue###ItemMaxAttempts`). A temporary error (a 4xx reply, a refused or dropped connection, a timeout, an error without any SMTP reply) is tried again after 2 minutes, then after 4 (`attempts × IncrementAttemptDelayInMinutes`, default 2); every failed attempt writes a `failed` mail-log row whose detail says that the mail will be tried again. A 5xx reply, or a temporary error on the third attempt, is final: the article's transmission error (`article_data_mime_send_error`) and a `failed` mail-log row, like `MailQueue::_SendError`; the job shows as failed. Tiqora uses Znuny's default values and does not read the `MailQueue` SysConfig hash.
- **Interruption:** the worker marks a mail as "sending" right before SMTP starts (after the connection settings and credentials, e.g. an OAuth token, were loaded). A mail whose worker stops or crashes, or whose job runs into its 5-minute time limit, before that mark goes back into the queue and is sent later (it keeps its attempt count, so a mail that keeps crashing the worker stops after 3 attempts). A mail interrupted after the mark is never sent again, because it may already have reached the relay; it is recorded like a final failure (transmission error "Sending was interrupted by a restart of the worker; the mail may not have been delivered." — or "Sending did not finish within the job's time limit; …" — and a `failed` mail-log row). A worker that dies in the instant after the `sent` mail-log row was committed but before the job was finished leaves that `sent` row plus such a transmission error behind; the mail is not sent twice. A mail job an administrator cancels while it waits for a retry ends cancelled (not sent, nothing recorded on the article). The worker's shutdown grace (20 s) is shorter than the SMTP timeout (60 s by default) on purpose: this rule decides what happens to a send cut off by a restart. Docker stops a container after 10 s unless `stop_grace_period` is raised; a send cut off by that ends the same way once the stale job is reaped (after 3 minutes). A temporary SMTP error after the relay accepted the data (for example a timeout while waiting for the final reply) is retried and can deliver the mail twice — Znuny behaves the same.
- **Auto responses:** queue and type are decided when the article is stored (Znuny `ArticleCreate` → `SendAutoResponse`): a later queue move, or a backlog in the worker, does not change which auto response goes out. The auto response article is stored only after a successful send; a failed attempt leaves a `failed` mail-log row for the ticket and nothing else (no article, no loop-protection entry). A mail that went out but could not be stored afterwards is not sent again: the job fails with "Die E-Mail wurde gesendet, danach ist ein Fehler aufgetreten …", a `sent` mail-log row carries the error and the loop protection counts the mail.

Multiple `Article` entries, `TimeUnit`, `AutoResponseType`, `HistoryType`/`HistoryComment`, article dynamic fields and `NoAgentNotify` are implemented like Znuny.

| Gap | Behaviour |
|-----|----------------|
| `ForceNotificationToUserID`, `ExcludeNotificationToUserID`, `ExcludeMuteNotificationToUserID` | Validated with Znuny's messages but, as in Znuny's GI, not passed on to the article creation |
| `SortOutbound` (operation config) | Not applied; response JSON keys are always sorted |
| `TicketCreate` without `Article` | Accepted (Znuny rejects it); existing Tiqora clients rely on it |
| Invalid `TimeUnit` (not a number) | Rejected before anything is written; Znuny's `TicketAccountTime` has already bumped the ticket's `change_time` when the insert fails |
| Time accounting without an article | The domain function accepts `article_id=None` (used by Tiqora's own API); Znuny's `TicketAccountTime` requires an article. The GI always passes the article |
| `ForceNotificationToUserID` agent without an e-mail address | Not added to the article's `To` line (Znuny writes `"First Last" <>`) |
| Escalation columns on `TicketUpdate` | Not rebuilt inside the call (only `TicketCreate` sets them); the escalation sweep (`daemon.escalation.enabled`) updates them, as for every other Tiqora write. TicketGet reads the stored columns |

### TicketGet / Search / Session / TimeAccounting — partial fidelity

| Gap | Why deferred / current behaviour |
|-----|-----------------------------------|
| Escalation detail keys in TicketGet (`EscalationDestination*`, `FirstResponseTime*`, `UpdateTime*`, `SolutionTime*`) and the `*InMin` working-time keys | Not returned; `EscalationTime` itself is |
| TicketSearch `Result=COUNT`, `TicketFlag`, `ArticleFlag`, mentions | Not Znuny GenericInterface parameters (`TicketSearch.pm` drops them and hard-codes `Result => 'ARRAY'`), so Tiqora ignores them too |
| Scalar types in responses | Tiqora returns integers where Znuny returns Perl strings for database values |

### Deviations kept on purpose

| Topic | Tiqora | Znuny |
|---|---|---|
| `TimeUnit` formatting | `"1.5"` (number normalised like Perl) | `"1.50"` |
| TimeAccountingGet queue when the history has no `NewTicket` row | the ticket's current queue | n/a (Znuny's snapshot has none) |
| Article text search (`MIMEBase_*`, `Fulltext`) | `LOWER(column) LIKE` on the article columns: no stop words, no tokenisation, no word index | search index (`article_search_index`) with tokenisation and stop words |
| Non-integer element in a TicketSearch id filter | whole search returns empty (as Znuny); an element of more than 16 digits counts as non-integer (`Quote(..., 'Integer')`) | empty |
| `_` in `MIMEBase_*` / `Fulltext` values | a literal character (as Znuny, `Quote(..., 'Like')`) | same |
| TicketSearch `*Minutes` beyond the date range (`"1e300"`, `"-1e12"`) | computed like CPAN DateTime: a huge positive value is a far-past date, a negative one a far-future date (a far-past one while the seconds wrap a signed 64-bit integer), and from about `-3.07e17` (`"-1e300"`) the start of today minus one second. Create / article / escalation times compare against the earliest / latest date instead of a far-past / far-future one; change / last change / close / pending times give an empty result for those, and search with the "start of today minus one second" date like Znuny. `"inf"`/`"nan"` count as 0 | the same dates; the far-past / far-future ones are written into the SQL as they are (`-292277022601-11-04 ...`, 5-digit years), which the database compares or rejects; "Invalid time format" (empty) for the history families |
| `PendingTime` that passes `ValidatePendingTime` but lies outside the signed 32-bit `until_time` (before 1901-12-13 20:45:52 or after 2038-01-19 03:14:07 UTC) or beyond the year 9999 | refused with "Ticket->PendingTime parameter is invalid!", whatever the target state | only written for a pending target state: the database write then fails on PostgreSQL and strict-mode MariaDB (TicketCreate: ticket without pending time; TicketUpdate: `Success => 0` fault), non-strict MariaDB stores a clamped value; with any other state the value is ignored and the request succeeds. A year written with fewer than four digits (e.g. `1`) never reaches the database because `Kernel::System::DateTime` cannot parse it: TicketCreate without pending time, TicketUpdate `Success => 0` fault |
| `PendingTime` refused by `TicketPendingTimeSet` (absolute form without `Hour` or `Minute`) on TicketUpdate to a pending state | 500 fault "OperationObject returned an error, cancelling Request" before anything is written | the same fault, after the ticket attributes handled before the state (title, queue, lock, type) were already saved |
| Operation errors | keep Tiqora's `ErrorCode` to HTTP status mapping, computed before the outbound mapping: `...AuthFail` 401, `...AccessDenied` 403, `...MissingParameter` / `...InvalidParameter` 400, `...RateLimited` 429 with `Retry-After`, `...NotImplemented` 501, every other error 200 | HTTP 200 for operation errors |
| Operation answers `Success => 0` (OutOfOffice without entries) | HTTP 500 fault `{"faultcode": "Server", "faultstring": ...}` | same (`Provider.pm` `_GenerateErrorResponse`) |
| OutOfOffice `UserSearch` | words AND-ed, each matching login / first name / last name as a whole value (`*` wildcard); Znuny's `|`, `AND`/`OR`, `!` and parentheses are not supported | `QueryCondition` |
| GI mails (`ArticleSend`, auto responses) | sent by the worker, `MailQueue` defaults (3 attempts, 2/4 min backoff, 5xx final); the `MailQueue` SysConfig values are not read; a send cut off by a worker restart after SMTP began is recorded as failed, not retried (before that it is requeued) | `MailQueue` with the SysConfig values |
| Unknown webservice | 404 JSON fault | bare 500 |
| SessionCreate | does not copy `UserPw` into the `sessions` row | copies it |
| OutOfOffice CSV with an unterminated quote | rejected with "Error while parsing" | silently truncated |
| Mapping regexes | `regex` package, compiled at load, 0.2 s per-match limit | Perl, lazily compiled, no limit |
| Request charset unknown | falls back to ISO-8859-1 (as Znuny) | same |

### Golden-master coverage

DB/unit tests cover SessionGet/Remove, TicketHistoryGet, OwnerIDs/SortBy, and
the original five ops. Peer golden (`tests/golden/test_compat_conformance.py`, repo root)
still focuses on SessionCreate / TicketSearch / StateType / empty-search —
not every new op. Treat production soak against real Znuny GI as required
before cutover of history/time-accounting/OutOfOffice clients.

Integrators needing full Znuny behaviour for deferred items should keep
traffic on Znuny GI or migrate to `/api/v1` / MCP.

## Changes for existing GenericInterface clients

Clients that already talk to Tiqora's `/znuny-compat` layer see the following
changes; each makes the answer or the side effect match Znuny. Check your
client against the list before updating.

**Routing and transport**

- Operations of a webservice are dispatched by their configured `Type`, and
  responses are JSON with sorted keys. Adjust clients that depended on key order.
- Values from the URI and query string now win over the same key in the JSON
  body (before: the body won).
- REST transport errors (no matching route, body too large, undecodable body)
  are `text/plain` instead of a JSON fault. Parse the status code rather than a body.
- An unknown webservice answers 404 with a JSON fault.
- `POST /znuny-compat/admin/reload` requires an admin (before: any
  authenticated user), and it now refreshes the webservice registry. Its answer is
  `{"status", "webservices_loaded", "webservices"}`; `routes_loaded`,
  `soap_webservices_loaded` and `message` are gone.
- SOAP webservices: a body longer than the webservice's `MaxLength` answers 413
  (`text/plain`); an operation the webservice does not configure answers a 500
  `Server` fault ("OperationType for operation '...' is not registered").
- An XSLT template that imports a file other than `OTRSFunctions.xsl` fails with
  "External file '...' is not allowed in Tiqora" (the text was German).

**TicketGet**

- Ticket keys follow Znuny: `CreateTime`/`ChangeTime` are now `Created`/`Changed`,
  both as `YYYY-MM-DD HH:MM:SS`. New keys: `Age`, `ChangeBy`, `CreateBy`,
  `GroupID`, `RealTillTimeNotUsed`, `UnlockTimeout`, `EscalationTime`, `TimeUnit`
  (always, not only with `Extended`). `UntilTime` is computed like Znuny.
  `Service`/`SLA` keys are left out when unset; `ResponsibleID`/`TypeID` fall back to 1.
- `EscalationTime` is the number of seconds to the nearest escalation destination
  time (before: the stored column).
- `DynamicField` lists only fields that have stored values, sorted by name;
  a Multiselect value is always a list. `Extended=1` returns
  `FirstResponse`/`Closed`/`FirstLock` in Znuny format (no `*InMin` keys).
- Articles gain `ArticleNumber`, `Bcc`, `ReplyTo`, `InReplyTo`, `References`,
  `IncomingTime`, `Charset`, `MimeType`, `CommunicationChannel`,
  `ChangeBy`/`ChangeTime`, `TimeUnit` and their dynamic fields; `CreateTime`
  uses the Znuny format.
- Attachments use `Content`, `ContentAlternative`, `ContentID`, `ContentType`,
  `Disposition`, `FileID`, `Filename`, `FilesizeRaw`. `ContentSize` is gone. The
  plain-text and HTML body parts are no longer listed as attachments (send
  `HTMLBodyAsAttachment=1` to get the HTML part back). The `Attachment` key is
  absent when there are none.
- Customers only receive customer-visible articles. `ArticleSenderType`
  matches case-sensitively and groups per requested type. `ArticleOrder: DESC`
  only applies together with `ArticleLimit` (a Znuny quirk). Flags such as
  `AllArticles` use Perl truthiness (`"0"` and `""` are false).
- If any requested `TicketID` is unknown or not readable, the whole call fails with
  `TicketGet.AccessDenied` ("TicketGet: User does not have access to the
  ticket!") instead of returning only the readable tickets. A missing
  `TicketID` answers "TicketGet: TicketID parameter is missing!", and an invalid
  one answers `TicketGet.WrongStructure` (it was a 500).
- `TicketHistoryGet`: `CreateTime` is `YYYY-MM-DD HH:MM:SS` instead of
  ISO-8601; a non-numeric `TicketID` answers `AccessDenied` for that id (was a 500).

**TicketCreate / TicketUpdate**

- `TicketCreate` with several articles creates all of them; `ArticleID` becomes a
  list. Both operations answer `ArticleID` / `ArticleIDs` like Znuny. Webservice
  operations with `IncludeTicketData` also answer `Ticket` (and the created articles).
- Article history type and comment come from SysConfig
  (`GenericInterface::Operation::TicketCreate/TicketUpdate`: `NewTicket`/`AddNote`,
  `%%GenericInterface Create|Note`). A default GI TicketCreate therefore no
  longer fires `NotificationNewTicket`, as in Znuny.
- `TicketUpdate`: an article's `IsVisibleForCustomer` defaults to the SysConfig
  value (`1`), it was `0`. Send `0` explicitly for internal notes.
- Unknown dynamic field names (matched exactly, case-sensitive) and invalid
  values are rejected with `<Operation>.InvalidParameter` instead of being ignored.
  A Multiselect list is stored as separate values (before: `"['a', 'b']"`).
- `TimeUnit` is validated and booked as time accounting, `NoAgentNotify` defaults
  to 1 on TicketCreate when an owner is given. Time accounting (in every caller)
  now bumps the ticket change time, writes a `TimeAccounting` history row and
  normalises the unit like Znuny (`0,256` becomes `0.25`).
- The queue's auto reply or auto follow up is sent (when an outbound relay is
  configured), as Znuny does; never for `ArticleSend` articles. Clients that
  relied on silence must turn the queue's auto response off.
- `Ticket->PendingTime` is validated like Znuny's `ValidatePendingTime`: `{Diff}`
  alone with digits only, or `Year`/`Month`/`Day` (plus `Hour`/`Minute`) forming a real
  date without empty or negative values; `Diff` together with other keys, or anything
  else, answers `<Operation>.InvalidParameter` "Ticket->PendingTime parameter is
  invalid!" before anything is written (before: silently ignored, or HTTP 500 for a huge
  `Diff`). `Diff` 0 sets no pending time, as in Znuny. Without `Hour` or `Minute` no pending
  time is set, as in Znuny's `TicketPendingTimeSet` ("Need Hour!"): TicketCreate creates the
  ticket without one, TicketUpdate to a pending state answers a 500 fault (before: midnight
  was assumed). Times Tiqora cannot store are refused (see the deviations).
- TicketSearch with a negative `Limit` returns no tickets (Znuny's database refuses
  `LIMIT -5`; before: HTTP 500).
- `ArticleSend` and auto responses are sent by the worker, not inside the call
  (Znuny's `MailQueue` does the same): the call answers at once, the mails follow
  within a few seconds, one after the other (after a temporary SMTP error the mail
  is tried again 2 and 4 minutes later). A mail that cannot be sent no longer turns
  the answer into `<Operation>.OperationFailed`; the call succeeds, and a final
  failure is recorded on the article (transmission error,
  `article_data_mime_send_error`) and in the mail log. Check the mail log instead of the response. The signature is converted to
  the article's content type and appended directly behind the body.
- `TicketCreate` without `Owner` makes the calling agent the owner, unlocked
  (before: `root@localhost`), with an `OwnerUpdate` history row. With an
  `Owner` and no `Lock`/`LockID` the ticket is created locked, history
  `OwnerUpdate` then `Lock`. A `Lock` on a ticket created in a closed state ends
  unlocked (history `Lock` then `Unlock`); a closed ticket with an owner and no
  `Lock` ends locked. `OwnerID` `"0"` means no owner. A `LockID` other than
  lock/unlock is written after creation with a history row. `Responsible` /
  `ResponsibleID` writes a `ResponsibleUpdate` history row after owner and lock.
- `TicketCreate` with `CustomerUser`: `CustomerID` defaults to the customer
  user's company, and the user is also found by e-mail address. The article `To`
  is the queue name unless `ArticleSend`; request `To`/`Cc`/`Bcc` are dropped for
  articles without `ArticleSend`. A missing `From` is filled from the customer user.
  The escalation columns are set at creation.
- `TicketCreate` without permission on the queue answers "TicketCreate: Can not
  create tickets in given Queue or QueueID!". `TicketUpdate` with an empty,
  non-numeric or `"0"` `TicketID` answers `TicketUpdate.AccessDenied` (was a 500).
- Locking a ticket (and assigning an owner with lock) resets the unlock timeout
  and writes the `Misc` history row "Reset of unlock time.". An agent article
  resets the unlock timeout too, as in Znuny. Every reset emits the
  `TicketUnlockTimeoutUpdate` event (outbox, webhooks), like Znuny's event of the
  same name.

**TicketSearch**

- No hit answers `{}` (no `TicketID` key) instead of `{"TicketID": []}`.
- A non-integer element in any id filter makes the whole search return empty
  (Znuny); an element of more than 16 digits counts as non-integer. An id outside
  its column range matches nothing instead of failing.
- `MIMEBase_*` and `Fulltext` match substrings case-insensitively (Znuny GI wraps the
  value in `*`); a value made only of `*` is ignored. `MIMEBase_Bcc` and, when
  `Ticket::Article::Backend::MIMEBase::IndexAttachmentNames` is on,
  `MIMEBase_AttachmentName` can be searched. `_` is a literal character, not a
  wildcard. A Perl-true `Fulltext` always joins the text filters with OR, even
  when its own value is only `*`.
- New `Created*` filters and the time-window filters (`TicketCreate*`,
  `TicketChange*`, `TicketLastChange*`, `TicketClose*`, `TicketPending*`,
  escalation times) and sort keys. `SortBy` `PendingTime` / `EscalationTime` sorts
  0 last. Pending filters apply to pending states only.
- `TicketChangeTime*` and `TicketCloseTime*`: a Newer and an Older bound must both
  hold for the same history row (Znuny's single history alias per family). "Changed
  between 7 and 3 days ago" no longer matches a ticket whose only history rows are
  10 days and 1 day old.

**Sessions, time accounting, out of office**

- `SessionCreate` returns a Znuny-format 32-character `SessionID` backed by Znuny
  `sessions` rows (no Redis token) unless
  `TIQORA_COMPAT_SESSION_WRITE_ZNUNY=false` or Tiqora owns the schema; see
  [Session handling](#session-handling) for expiry, purge and the remote-IP caveat.
  GI calls do not refresh `UserLastRequest`, so the session ends at Znuny's idle limit.
- `SessionGet` returns only an allow-list of non-secret keys (no password hash,
  tokens or secrets). It answers `SessionGet.SessionInvalid` for an expired
  session or a disabled account (before: the data of the stale row).
- Sessions of disabled accounts stop authenticating. Disabling an agent or a
  customer user, an admin password reset (agents and customer users), password
  setup, a customer's own password change and GDPR erasure delete the account's
  Znuny session rows; see [Session handling](#session-handling) for the Redis side.
- `TimeAccountingGet`: entries carry no `TicketID`; `Queue` is the queue at
  booking time (falling back to the current queue when the history has no
  `NewTicket` row); `TimeUnit` is a normalised string (`"1.5"`, Znuny shows
  `"1.50"`); `Created` is `YYYY-MM-DD HH:MM:SS`. Unparsable
  `TimeAccountingStart`/`End` answer `{}`.
- `OutOfOffice` answers `{"Success": 1}`; the per-entry result of
  `OutOfOfficeEntries` is gone and unknown entries are skipped. It accepts
  `OutOfOfficeEntriesCSVString` on the canonical route.
- `OutOfOffice` follows `OutOfOffice.pm`: `"0"` and `""` switch out-of-office off
  (before: on); an entry whose `UserSearch` / `UserEmail` / `UserLogin` finds more
  than one agent is skipped (before: the first match was set); `UserSearch` wins
  over `UserEmail`, which wins over `UserLogin`; a date is found anywhere in the
  value, and an unusable `StartDate` also drops `EndDate`. The Znuny user cache is
  refreshed. A request without entries answers an HTTP 500 fault "Payload does not
  contain out-of-office entries or they could not be parsed." (before: 200 with
  `OutOfOffice.WrongInputStructure`).

## Migration guidance

1. Point a non-production webservice at Tiqora’s compat layer.
2. Run the golden-master suite and a soak of real client traffic.
3. Move production webservice routes (or reverse proxy) when diffs are clean.
4. Plan a later move to `/api/v1` for new integrations.

## Golden-master validation (2026-07-19)

The golden-master suite (`tests/golden/`, see docs/testing.md) runs a REAL
peer container (default Znuny 6.5.22; multi-peer matrix 6.0–7.3) on the same
MariaDB and validated:

- SessionCreate / TicketSearch / empty-search wire shapes against Znuny's
  shipped `GenericTicketConnectorREST` webservice.
- The `StateType` singular gotcha on both sides.
- **Divergence found and fixed**: a compat-issued SessionID (Redis) could not
  authenticate follow-up compat calls (`_auth_from_params` only consulted the
  Znuny `sessions` table); it now falls back to the Tiqora session store.
- **Divergence found and fixed**: compat TicketUpdate auto-locked the ticket
  on owner change; Znuny's GI TicketUpdate never does.

## Known limitations (compat layer)

- **SessionID TTL**: Znuny `sessions` rows are checked for idle and absolute
  expiry (`UserLastRequest` / `UserSessionStart`, ported from Znuny's
  `CheckSessionID`); rows with missing or unparseable timestamps are treated
  as expired.
- **CustomerUserLogin auth**: Customer principals use sentinel `user_id=0` for
  ACL (never elevated to root/agent). Ticket/article writes still attribute
  `create_by` via the portal system user (`PORTAL_SYSTEM_USER_ID`, typically
  root) so history remains agent-shaped in the legacy schema.
- **DynamicField_X search**: `Equals`, `Like`, `GreaterThan`, `SmallerThan`,
  `GreaterThanEquals`, `SmallerThanEquals` are implemented (numeric compare
  when the value is numeric; otherwise string compare).
- **Attachment storage**: Attachments are stored inline in `article_data_mime_attachment`
  (DB storage), not offloaded to a file backend. For large attachments this may
  be a performance concern.
- **Hot-reload**: Webservice changes are picked up without a restart, see
  [Hot reload](#hot-reload).
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
- Preferences (`/portal/preferences`) cover Znuny's default customer groups
  Language (`UserLanguage`), Password and — while `PGP` / `SMIME` are on —
  PGP key / S/MIME certificate upload ([crypto.md](crypto.md#customer-keys)).
  Time zone, Google Authenticator and other `CustomerPreferencesGroups` are
  not offered; groups deactivated in SysConfig are hidden. The new password
  follows Tiqora's length policy, not the `Password` group's `PasswordRegExp`
  / `PasswordMin*` options.

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
- ACL admin is full CRUD (YAML `config_match` / `config_change`) plus runtime
  picker filtering via `domain/ticket_acl.py` and field-options; ticket
  attribute relations (`acl_ticket_attribute_relations`) have CSV admin + the
  same field-options chain. Queue *access* remains group/role.
- ACL edits in Tiqora write `acl_sync` like Znuny's AdminACL (`not_sync` on
  create/update, `deleted` on delete) and are drafts until "Bereitstellen" in
  Znuny (or in Tiqora, once Znuny no longer runs; see
  [process-management.md](process-management.md#deployed-revisions-tiqora-runs-what-was-deployed)).
  ACLs run from the deployed state (`tiqora_acl_revision`); before the first
  revision they run from the live `acl` rows. After cutover Tiqora deploys on
  its own.
- Deferred relative to full Znuny admin breadth:
  `group_customer` (company-level) assignment endpoints
  (`group_customer_user` is implemented), SysConfig deploy UI, GI webservice
  admin, package manager.

## Web chat in Znuny

Chats created by Tiqora's web chat show in Znuny's ticket view like internal-note articles: the `WebChat`
`communication_channel` row uses Znuny's `Internal` backend module (Tiqora needs MIME storage for attachments and
Znuny's own `Chat` backend stores lines in `article_data_otrs_chat` without attachments). This is expected, not a
bug. Znuny's own chat engine is not used.

- The `WebChat` row is unregistered (no `CommunicationChannel###...` setting) and has `package_name` `Tiqora`.
  `Admin::CommunicationChannel::Sync` deletes such a row only while no article uses it: `ChannelDrop` bails out as
  soon as any article references the channel (its DROP TABLE branch is dead code in Znuny 6.5.22). Tiqora
  re-creates a deleted row on the next chat write. Since 99fa0b43 the row's `channel_data` is YAML with empty
  `ArticleDataTables`. The golden test runs `Admin::CommunicationChannel::Sync` after a chat article and asserts
  that the row and the article survive. Registering `WebChat` in TiqoraSync is a follow-up.
- Web chat tickets are created with the Postmaster default state and priority and owner/creator user 1, not with
  the `CustomerTicketMessage` defaults or `CustomerPanelUserID`, and they never trigger Znuny's web-ticket
  auto-response (`AutoResponseForWebTickets`, a Perl boolean that would have to be read with `get_bool` if it
  were ever honoured).
- **Assistant replies are normal articles.** A reply of the chat assistant is an ordinary `WebChat` article
  (sender type `agent`, visible for the customer, `create_by` = the queue's AI service user) and the handoff
  summary is an ordinary internal note (an article on Znuny's internal channel, not visible for the customer, same
  author). Znuny's ticket view, `TicketGet`/`ArticleList` and the article search show them like any
  other article; they survive `Admin::CommunicationChannel::Sync`. Tiqora-only state (who is chatting, the bot
  claim, the handoff reason, identity source) lives in `tiqora_chat_session` and is not visible in Znuny. The
  golden test `test_bot_reply_and_handoff_note_are_normal_znuny_articles` pins this against the real peer.
- The chat retention purge (see [gdpr.md](gdpr.md#web-chat)) clears the visitor's data in Tiqora's session table
  only. Name and e-mail also live on the chat articles and the ticket; those are the GDPR engine's scope.

## Znuny features not implemented

*Last reviewed: 2026-10-01. This is the single maintained parity list — update
the row when a gap is closed (and mirror the summary in the README if the
headline changes).*

Tiqora covers the agent, customer-portal and admin surface used in day-to-day
operation. Status values: **missing** (open work), **partial** (exists, but
narrower than Znuny), **by design** (deliberate platform choice).

### Agent UI

| Znuny feature | Status | Note |
|---|---|---|
| Saved searches | yes | Saved ticket searches ("Gespeicherte Suchen") with sharing by group (a colleague sees a shared search when holding `ro` on one of its groups), sidebar entries with ticket counts (counted are the first 15 entries that show a count), GDPR erasure of customer logins in saved criteria (see [GDPR](gdpr.md#saved-searches-and-reports)) and the import of Znuny search profiles ("Suchvorlagen aus Znuny übernehmen"; read-only and one-way, full-text and future-relative times are not taken over, see [Search profiles](#search-profiles)) |
| Configurable dashboard widgets | partial | Several dashboards per agent with drag/resize/keyboard layout, widget gallery and settings with live preview, sharing by group: ticket lists with filters, columns and limits (presets, saved searches, own filters), counters with sparkline, escalations, reminders, calendar agenda, queue overview, recent views, team presence, notes and links; admin role templates incl. mandatory ones; widgets from plugins. See [Dashboards](dashboards.md). Phase 2 adds dashboard filters (queues, time range), report widgets (chart, table, SLA gauge, backlog aging, heatmap), AI status and chat queue widgets, drill-down to tickets, template update merge, TV mode and read-only wallboard links. **Missing**: customer-info widget, RSS |
| ITSM ticket types, state, dynamic fields | partial | Admin "ITSM" > "Einrichtung" creates `Incident`, `Incident::Major`, `ServiceRequest`, `Problem`, the state "closed with workaround" and the eight `ITSM*` dynamic fields with Znuny's names and values (`Change` / `Change::Task` opt-in; `RfC` is not created). `AgentTicketAddtlITSMField` and `AgentTicketDecision` are dialogs in ticket zoom. See [ITSM](itsm.md) chapter 2 |
| Priority matrix (CIP) | yes | Criticality x impact gives the priority when one of them changes in a Tiqora write and the same write sets no priority; Znuny's `cip_allocate` is shown read-only in Mode A, editable in Mode B. Znuny itself only preselects the priority in its forms and does not enforce it in the API. See [ITSM](itsm.md) chapter 3 |
| Service ITSM attributes / incident state | partial | Type, criticality, owner, portal visibility, minimum time between incidents (as a warning per service; Znuny keeps it per SLA, shown read-only in Mode A). Incident state is computed from linked CIs like `CurInciStateRecalc` (productive CIs, worst functionality wins). Znuny's copy of the service criticality into the ticket field is not done |
| Link delete, link types, note to linked tickets, link graph | yes | Link delete ports `LinkObject::LinkDelete` including the ticket history; link creation follows `LinkAdd` (reverse direction, type groups, `TicketLinkAdd`). Link-type admin (labels, offered pairs, validity; Znuny types themselves stay in SysConfig). "Notiz an verknüpfte Tickets" runs as a background job and always uses e-mail for customer replies. Link graph up to depth 2 / 60 nodes |
| Problem / known error | partial | Root cause and workaround live in `tiqora_itsm_problem` (not visible in Znuny); the known error is a Tiqora KB article in review state. Znuny has no problem management beyond ticket type and links |
| Major incident | partial | Banner on linked tickets, timeline and one update for all linked tickets (internal / note / customer reply, reply off by default). Customer reply is always e-mail, also for plugin channels |
| Znuny CMDB (read-only, Mode A) | partial | CI search, detail, versions and attributes from `configitem*` for Znuny ITSM 6.5 and 7.3, link to tickets; no CI edit, no attachments, no CI history entry for links made in Tiqora. See [ITSM](itsm.md) chapter 5 and [ITSM schema survey](itsm-import.md) |
| Znuny ITSM changes (not shown, phase 3) | missing | Changes, work orders and the change calendar of `ITSMChangeManagement` are not shown; the CI detail links to Znuny. A read-only legacy archive follows in ITSM phase 3 |
| ITSM configuration management (`ITSMConfigurationManagement`): classes and definitions | yes | Ja (Tiqora CMDB, Modus B): typed, versioned attribute definitions with designer and templates, import of Znuny class definitions (YAML 6.5 / 7.x); see [cmdb.md](cmdb.md) |
| ITSM CIs: versions, deployment / incident states | yes | Ja (Tiqora CMDB, Modus B): every change is a version with note, diff and history; incident state derived along the link types (cycle-safe, bounded); optional version archive |
| ITSM CIs: links, search, bulk edit | yes | Ja (Tiqora CMDB, Modus B): links to CIs, tickets, services, customers, KB articles; attribute search and saved searches; bulk edit of states and a link as a background job; impact graph and affected objects |
| ITSM CIs: customer portal view | yes | Ja (Tiqora CMDB, Modus B): "Meine Geräte und Systeme", portal-flagged attributes only, ticket form with system picker |
| ITSM ImportExport (CI CSV/XLSX) | yes | Ja (Tiqora CMDB, Modus B): wizard with column mapping (`attr:NIC[2].IPAddress[1]`), dry run, mapping templates, CSV/XLSX export as background jobs. Znuny's own ImportExport mapping objects are not read; transferring a Znuny CMDB is the job of the ITSM import (see [itsm.md](itsm.md)) |
| Stats framework | partial | Report builder ("Bericht erstellen": rows, columns, values, filter, period) with saved reports, sharing by group, runtime parameters (Znuny "changeable at runtime"), JSON/YAML report files and the `stats` group rights; 25 built-in reports incl. Znuny `TicketAccountedTime`-style time accounting, SLA in working minutes from the Znuny calendars, XLSX/CSV/PDF export, import of Znuny statistics (from `xml_storage` or an exported XML file; the four dynamic objects and `StateAction`, with a list of what was approximated or dropped) and scheduled delivery by e-mail (daily to yearly, several reports per mail, PDF/XLSX/CSV, run history). Not taken over: ITSM stats, dynamic-field axes, cron expressions. See [Reporting differences](#reporting-differences-to-znuny-stats), [Importing Znuny statistics](#importing-znuny-statistics) and [Scheduled reports](#scheduled-reports-vs-the-statistic-reports-add-on) |
| Ticket export | partial | `GET /tickets/export.csv` (synchronous) plus `POST /tickets/export-jobs` which runs the export as a background job with progress, cancel and download; no Znuny equivalent (Znuny has CSV via stats only) |
| Bulk action | partial | State, priority, owner, queue, lock only |
| Preferences | partial | Language, TOTP/passkeys; no queue favourites, ticket-list options or notification matrix |
| Free-text edit screen (`AgentTicketFreeText`) | partial | Dynamic fields are editable in ticket zoom, limited to the fields enabled in `Ticket::Frontend::AgentTicketFreeText###DynamicField` (stock Znuny enables none, so the "edit" button only appears once configured); ACL-filtered options; types Text, TextArea, Dropdown, Multiselect, Checkbox, Date, DateTime. Writes use Znuny's typed columns, no-op and history rules. Other types (e.g. ticket/article references) stay read-only |

### Admin

| Znuny feature | Status | Note |
|---|---|---|
| SysConfig edit/deploy UI | by design | Settings are read from `sysconfig_*`; change them in Znuny or via `Admin::Config::Update` |
| Package Manager (OPM), Support Data Collector, Cloud Services, OTRSBusiness, web installer | by design | OPM itself is by design (Perl packages cannot run). Tiqora plugins replace OPM add-ons (phase 1: settings, routes, ticket events, scheduled tasks, background jobs, MCP tools and four UI slots; phase 2a, SDK 0.2.0: sync hooks, transition actions and conditions, placeholders, ticket number generators, dynamic field types, mail filters, notification transports, channels, password providers, customer sources, GenericInterface operations, invokers and mappings, report sources and built-in reports, write services); see [plugins.md](plugins.md). |
| Sessions, SelectBox SQL, performance log, maintenance windows | missing | A user's sessions can be revoked from user admin |
| General system log / communication log | missing | Mail log and AI audit exist |
| Calendar admin, ticket appointment rules (queue/SLA) | missing | Agent calendar exists; only the calendar name is set on queue/SLA |
| DynamicFieldScreen admin | missing | Screen assignment is read, not editable |
| `group_customer` (company level) assignment | missing | `group_customer_user` is implemented |

### GenericInterface

| Znuny feature | Status | Note |
|---|---|---|
| Webservice admin / editor | by design | Webservices are loaded from the database; no editor |
| Requester side / invokers | partial | Ticket-event invokers (`Invoker::Ticket::Generic`) over REST and SOAP, retries, conditions, response actions and a debugger; see [Requester and invokers](#requester-and-invokers-tiqora-calls-external-systems). Off until the takeover flag is set ([parallel-operation.md](parallel-operation.md#taking-over-genericinterface-invokers)) |
| Mappings `Simple`, `XSLT`, `OutOfOffice` | yes | `Znuny` and `Test` mappings are not supported, see [Mappings](#mappings) |
| Hot reload of webservice configs | yes | Polling, no restart, see [Hot reload](#hot-reload) |
| `TimeUnit`, multiple articles in TicketCreate/Update | yes | See [TicketCreate / TicketUpdate](#ticketcreate--ticketupdate) |
| `ArticleSend` (outbound e-mail on create/update) | yes | Agents only, like Znuny |
| TicketSearch `Created*`, time-window and escalation filters, article text filters | yes | Article text uses LIKE, see [deviations](#deviations-kept-on-purpose); `Result=COUNT`, flags and mentions are not GI parameters |
| SessionCreate writes Znuny `sessions` rows | yes | See [Session handling](#session-handling) |
| OutOfOffice CSV bulk (`OutOfOfficeEntriesCSVString`) | yes | |

### Processes, daemon, calendar

| Znuny feature | Status | Note |
|---|---|---|
| Process designer | yes | Canvas with panels for steps, forms, transitions and actions, drafts in `pm_*` like Znuny, validation, versions with diff and restore ([process-management.md](process-management.md#process-designer)). Desktop only (read-only on phones); no test run (Probelauf) |
| Process import/export (Znuny YAML) | yes | Znuny 6.0 to 7.3 process files in and out; an import is a draft and writes `pm_entity_sync` rows (unlike Znuny's `ProcessImport`). Limits: 1 MB, 1 000 entities, no YAML aliases |
| Process deploy ("Bereitstellen") | yes | Engine and ACLs run from the deployed state only; Tiqora detects a Znuny deploy within two watch intervals. "Bereitstellen" in Tiqora (`POST /api/v1/admin/deploy`) while Znuny still runs needs TiqoraSync 1.2.0 (Znuny writes its own deploy files, status `ok`/`error` is shown in Tiqora); otherwise only after cutover or with the setting "Prozesse und ACLs in Tiqora bereitstellen (Znuny läuft nicht mehr)"; after cutover Tiqora deploys by itself |
| Process state gate (Active / FadeAway / Inactive) | yes | Same rules as Znuny: only Active starts, FadeAway keeps running tickets, Inactive refuses dialogs and runs no transitions |
| ACLs on process list, dialogs, action menu and bulk | yes | Agents as the agent, portal customers as the customer (never as root); Properties on `Process` entity ids work |
| New process ticket (agent and portal), enroll via start dialog | yes | "Neues Prozessticket" / "Prozess starten"; Znuny's create order, `Process::Default*` for hidden fields, immediate first transition |
| Compat (GenericInterface) API and ACLs | by design | No ACL filtering, exactly as Znuny's GenericInterface (it never calls `TicketAcl`) |
| Smart tags in actions and conditions | yes | `<OTRS_TICKET_*>`, dynamic fields, first/last article, template placeholders; conditions of the Base types only, like Znuny |
| `Module` conditions | partial | Python re-implementations registered by Perl module path (`ValidateDemo`); unknown modules never match and the designer warns |
| Appointment actions (`AppointmentCreate/Update/Remove`) | yes | `Custom*` recurrence, `RecurrenceFrequency`, `RecurrenceExclude`, notification keys and `TicketAppointmentRuleID` are ignored |
| `ExecuteInvoker` action | yes | Always queued as a background job; `Asynchronous` is accepted and ignored (see [Requester](#requester-and-invokers-tiqora-calls-external-systems), deviation 8) |
| `ConfigItemUpdate` action | missing | Logged as unsupported (needs the CMDB) |
| Dialog attachments and time units | yes | Article uploads, `TimeUnits`, Znuny's existing-attachment selection (`Attachments` field, `AttachmentsReuse`), option refresh when a field changes |
| Transitions after every ticket change | missing | Evaluated after process dialogs and new process tickets only |
| Znuny 7.x process YAML | verified | Same format as 6.5 (no renamed keys); `pm_process_preferences` (7.2+) is snapshotted. Golden tests passed on Znuny 6.5 and on 7.3.5 |
| GenericAgent execution | partial | Pragmatic subset: no absolute `TimeSlot`, `Next`/`After`, dynamic-field criteria; off by default |
| Edit single occurrence of a recurring appointment | missing | Exclusion list (`recur_exclude`) instead |

### Customer portal

| Znuny feature | Status | Note |
|---|---|---|
| Print view | missing | |
| Follow-up "new ticket" (`queue.follow_up_id == 3`) | missing | Behaves like reopen |

### Search / AI

| Znuny feature | Status | Note |
|---|---|---|
| Hybrid (vector + keyword) retrieval for KB/RAG | missing | Planned |

## Upgrade notes (next release)

- **Process and ACL revisions.** New tables `tiqora_process_revision` and
  `tiqora_acl_revision` come with the regular Tiqora migration. Processes and
  ACLs now run from the deployed state. Process admins must press "Bereitstellen" in Znuny for edits to
  take effect ([process-management.md](process-management.md)). Since the process designer, edits can also be made and deployed in Tiqora; while Znuny runs that needs the add-on TiqoraSync 1.2.0 (see [parallel-operation.md](parallel-operation.md#processes-and-acls-deploy-in-znuny)).
- Dialog fields with `Display: 1` are now optional and `2` mandatory (Znuny
  semantics; it was the other way round).

- **DateTime dynamic fields are now stored in UTC.** Values entered through the
  phone and new-ticket forms (and the ticket-zoom editor) are converted from the
  agent's time zone to UTC wall time, exactly as Znuny stores them. DateTime
  values written by earlier Tiqora versions from those forms were stored as
  local time and therefore display shifted by the zone offset (1-2 h in
  Europe/Berlin); correct them by hand if it matters. Date fields are unaffected.
- **Dynamic-field writes use Znuny's typed columns** (`value_text`,
  `value_date`, `value_int`) and follow Znuny's rules: unchanged values are
  no-ops (no history entry, no `change_time` bump), an empty Checkbox stores
  `0`, invalid input is rejected with 422. Existing rows are not rewritten.
- New background-job tables (`tiqora_job`, `tiqora_job_file`) are created by the
  regular Tiqora migration; the `worker` process must be running for exports.
- Editing dynamic fields in ticket zoom requires enabling them in
  `Ticket::Frontend::AgentTicketFreeText###DynamicField` (SysConfig).

## Reporting differences to Znuny stats

Reports (`/api/v1/reports`, and the `/api/v1/stats/*` wrappers that now run the
same built-ins) read Tiqora's own fact tables, not the live Znuny tables, so some
figures differ from Znuny's `Kernel::System::Stats`:

- **One close per state transition.** A close is a transition from a non-closed
  to a closed state, detected by diffing the state column over the history
  (writer-independent). Znuny's `_TicketGetClosed` instead takes the MAX over all
  `StateUpdate`/`NewTicket` rows with a closed state, so a closed-to-closed
  sub-state change moves Znuny's "Closed" time. For a few tickets the solution
  time therefore differs from Znuny's by such a sub-state change.
- **Solution minutes run to the last close**, not the first one (the old
  `/api/v1/stats` used the first close). A reopened and closed-again ticket counts
  its full open time.
- **Owner names, not logins**, label the agent dimensions in reports. The owner
  dimension of `/api/v1/stats/open-snapshot` (JSON and CSV `Label`) shows
  "First Last" instead of the login; agent-workload keeps both columns (`Login`,
  `Name`).
- **Numbers are incomplete until the first backfill finishes** (worker loop
  `report_facts`, or `tiqora report backfill`). Until then reports show a hint
  and the figures cover only what is built so far.
- **Truncation.** When a row cap cuts a `/api/v1/stats/*` result the response
  carries `X-Tiqora-Truncated: 1`; a warning is logged once per request.
- **"Open now" ignores date filters.** The open-ticket snapshot and the "owned
  open" side of the agent workload describe the current state, so
  `date_from`/`date_to` do not apply to them (closed-in-period still uses them).
- **Merges.** Znuny's `TicketMerge` moves the merged ticket's article-linked
  history rows to the main ticket (stamping `change_time`); they still carry the
  merged ticket's queue, state and owner. Reports skip those rows when deriving
  the main ticket's queue moves, closes, reopens and owner changes (the moved
  articles still count for the main ticket, as in Znuny's first-response time).
  A merge within the same second as the moved row's creation cannot be told
  apart and is read as the main ticket's own row.
- **SLA breaches of ended tickets.** A ticket without a decision whose due time
  has passed counts as breached only while it is open; merged, removed and closed
  tickets are not (Znuny stops escalation for them). Such a ticket counts as
  "met" in `sla_met_rate`, like an open ticket still inside its target.
- **Accounted time includes archived tickets** (historically correct totals);
  Znuny's `TicketAccountedTime` excludes them by default when
  `Ticket::ArchiveSystem` is on (`ArchiveFlags => ['n']`). Znuny's
  TicketAverage/Min/Max and Article* kinds are phase-2 builder measures.
- **Access.** `reporting.access` defaults to `all_agents` (today's `/stats`
  behaviour); `stats_group` restricts reports to admins and members (`ro`) of
  Znuny's `stats` group, the Znuny parity setting (stock Znuny gives `stats` to
  admins only). `GET /api/v1/auth/me` returns `can_view_reports`.
- **Closed count is a creation cohort.** In `ticket_kpis`, `volume_by_queue`,
  `sla_by_customer` and `top_customers`, "closed tickets" are tickets created in
  the window and by now closed, not tickets closed in the window.
- **First-contact resolution.** `first_contact_resolution` counts as closed the
  tickets that are currently closed; the rates are taken over the tickets with a
  close inside the window. Figures differ from a pure close-in-window view only for
  tickets reopened after their close.
- **Job results are private.** The result of a report job (run or export) can be
  fetched by its creator only, administrators included. A user may have at most 3
  report jobs queued or running; further requests get 429. A job whose cancel
  was requested no longer counts: it ends within about a second, although the
  database may finish its running statement for up to
  `reporting.query_timeout_seconds`.
- **Inline runs have a 75 s budget** (below the 90 s nginx `/api/` timeout) and
  answer 504 with a JSON `detail.message` when it is used up; large reports
  run as background jobs instead.
- **API keys** need the `stats` scope: `GET` calls work read-only, running reports
  (`POST /reports/run`, exports) needs read-write. The same key polls the jobs it
  started (`GET /api/v1/jobs/{id}`) and downloads their files: `/api/v1/jobs` accepts
  a `tickets` **or** a `stats` scope (cancelling is a `POST` and needs `:rw`); a job
  stays visible to its creator only.
- **Cache.** Results are cached for up to `reporting.cache_seconds` (default 300,
  0 = off) per viewer scope. Results that depend on "now" (relative date filters,
  ages of open tickets) can therefore be that stale. Results computed while the
  facts were not ready are never cached. GDPR erasure, `tiqora gdpr
  anonymize-customer` and every new backfill or rebuild invalidate the whole
  report cache; a report that was computing at that moment cannot store its
  result where later requests look (cache generation).
- **Very wide results are cut.** One report query loads at most 200 000 groups;
  a wider result keeps an arbitrary subset and is flagged truncated.
- **Admin rebuild.** Rebuilding the report data from the admin page restarts the
  build: reports show a hint and are incomplete until the rebuild has finished.
- **Saved reports and rights.** Who may do what (`reporting/rights.py`). With
  `reporting.access = all_agents` every agent may view reports and **save private
  ones**; sharing (to groups or to everybody) needs `rw` on Znuny's `stats` group or
  admin. With `stats_group`, viewing needs `ro` and every save needs `rw` on that
  group; admins may do everything. This differs from Znuny, where only the `stats`
  group (admins in a stock install) may touch statistics at all. `GET
  /api/v1/auth/me` returns `can_view_reports`, `can_create_reports` and
  `can_share_reports` (each implies the one before it).
- **Owner-only editing.** Only the owner (with the save right) and admins change a
  saved report; a shared report is read-only for everybody else, who can copy it
  ("Kopieren") into their own. `PUT` replaces the whole report and uses the `version`
  as optimistic lock (a stale version answers 409). Keeping the stored sharing needs
  no share right; widening it does. Sharing groups are limited to the agent's own
  groups (admins: all valid groups).
- **Admins see everything by id.** An admin can open, run, edit and delete any saved
  report by id (private ones included); the report list hides other people's private
  reports unless asked for (`all=true`). Everybody else gets 404 for a report they
  cannot see, so existence is not revealed.
- **Soft delete.** Deleting sets the report invalid: it disappears from lists and
  answers 410 when addressed directly (dashboards or bookmarks that still point at
  it). Owners may always delete their own report, even after `reporting.access`
  changed to `stats_group`. The worker purges reports deleted more than 90 days ago
  for good. Each agent can own 500 valid reports.
- **Runtime parameters.** A parameter whose operator is `eq` takes exactly one
  value (also as default). A yes/no parameter (`is_true` / `is_false`) with a
  default is a fixed filter: it is shown as "Immer an (Vorgabe des Berichts)" and the
  viewer cannot switch it off; without a default it is an optional switch. Values
  outside `allowed_values` are rejected. A parameter marked required must be
  filled.
- **Scope.** Saved or built-in, a report always runs in the viewer's own queue
  scope (`ro` queues), also when someone else owns it, so a shared report shows
  different numbers to different people. Cached results are kept per viewer scope.
- **Report files.** Reports export to and import from JSON or YAML files
  (`GET /reports/{key}/file`, `POST /reports/import-file`). The import only fills the
  builder; nothing is saved before "Speichern". Znuny stats XML is read by the Znuny import (next section), not by this file import.

### Importing Znuny statistics

Agents with the save right (Berichte, "Statistiken aus Znuny übernehmen" = import
statistics from Znuny) can copy Znuny statistics into Tiqora. Two origins exist:
the Znuny database (`xml_storage`, read-only, the same data the Znuny stats module
uses) and a Znuny export file (`Stats.*.xml`, up to 1 MB). A preview lists every
statistic the agent may see with its target, the issues found and whether it was
imported before; "Übernehmen" (apply) writes saved reports and saved searches only.
The import is synchronous but bounded: 500 statistics, 200,000 stored values per
read, a 75 s database budget (below the 90 s nginx limit); when the budget is spent
the call answers 504 with a German message and rolls back. Non-admins see a count
of unreadable statistics, admins the ids. Znuny's `Permission` groups of a statistic
are honoured; re-importing a statistic updates name, description and definition but
keeps sharing and sorting.

Every mapping decision is reported per element as **dropped** ("Nicht übernommen")
or **approximated** ("Angepasst"); nothing is silently ignored.

| Znuny object | Target | Measures / notes |
|---|---|---|
| `Dynamic::Ticket` | report on `tickets` | count of tickets |
| `Dynamic::TicketSolutionResponseTime` | report on `tickets` | `SolutionAverageAllOver` solution clock avg, `NumberOfTicketsAllOver` closed tickets, `SolutionWorkingTimeAverage` solution avg, `ResponseWorkingTimeAverage` first response avg; min/max kinds dropped (Tiqora offers P50/P90); escalation-dependent kinds dropped |
| `Dynamic::TicketAccountedTime` | report on `accounted_time` | `TotalTime` time units, `NumberOfTickets` tickets; other kinds dropped |
| `Dynamic::TicketList` | **saved search** | the list shows its fixed columns; column selection and `Limit` are approximated; `OrderBy`/`SortSequence` map to the search sort |
| static `StateAction` | report on `ticket_events` | approximated: Tiqora counts the event kinds of the report data, not every Znuny history type |
| any other static file or object | none | "Diese Art von Statistik kann Tiqora nicht übernehmen." |

Elements (ticket and solution/response objects; `accounted_time` supports a subset):

| Znuny element | Tiqora field |
|---|---|
| `QueueIDs`, `CreatedQueueIDs` | `queue`, `queue_at_create` |
| `StateIDs`, `StateTypeIDs` | `state`, `state_type` (ids mapped through the lookups; unknown types dropped) |
| `PriorityIDs`, `TypeIDs`, `ServiceIDs`, `SLAIDs` | `priority`, `type`, `service`, `sla` |
| `OwnerIDs`, `ResponsibleIDs` | `owner`, `responsible` |
| `CustomerID`, `CustomerIDRaw` / `CustomerUserLogin`, `CustomerUserLoginRaw`, `CustomerUserID` | `customer` / `customer_user` (`*` dropped) |
| `CreateTime` | `created` |
| `CloseTime`, `CloseTime2` | `closed` (`CloseTime2` is the period of the solution-time objects) |
| `SearchInArchive` | nothing for "not archived"; archived tickets are not counted in reports, so other values are dropped |
| `ChangeTime`, `LastChangeTime`, `PendingUntilTime`, `Escalation*Time` | dropped (no such time field in the report data) |
| `LockIDs`, `Title`, `Created{State,Priority,User}IDs`, `MIMEBase_*` | dropped |
| `DynamicField_*` | dropped (dynamic-field dimensions are not available in reports yet) |

Axes and restrictions: `UseAsXvalue` becomes the first dimension, up to two
`UseAsValueSeries` elements the next ones (a fourth dimension is dropped). Selected
values become a fixed `in` filter; elements Znuny lets the viewer change at runtime
become report parameters (time elements need none, the viewer can always change the
period). Axis rows without tickets are not shown (approximated). Only one period per
report; a second one is dropped. Time: absolute ranges are rounded to whole days,
relative ranges keep year/quarter/month/week/day, half years become 6 months, hour,
minute and second ranges are dropped, upcoming periods only keep the current one (approximated). Scales day to
year map to the grain; "several units" scales are approximated and second/minute/
hour/half-year scales dropped. Stat level: `SumCol`/`SumRow` become totals, `D3::BarChart`
/ `LineChart` / `StackedAreaChart` become bar / line / area, otherwise pivot (two or
more dimensions) or table; an invalid statistic is imported as a normal report
(approximated); "show as dashboard widget" is dropped (add the report as a widget in the
dashboard instead).

Statistics from a file carry names instead of ids. Names are resolved through the
lookups; an unknown name is dropped. Ids that only make sense in the exporting system
(an `id` element outside the file-name elements) are approximated and turned into a
runtime parameter instead of a fixed filter, so nothing silently counts the wrong
queue.

**Never taken over:** ITSM statistics (CI, change), dynamic-field axes, Znuny output
formats and cache flags, per-statistic permission groups as such (Tiqora's report
sharing applies instead), cron-based generation from the Znuny stats module.

### Scheduled reports vs. the Statistic Reports add-on

Znuny itself only generates statistics on demand or by `Maint::Stats::Generate` on
cron; mailing scheduled reports is the job of third-party add-ons. Tiqora ships it:

- **Bundles.** One schedule sends 1 to 10 reports (saved or built-in) in one e-mail;
  each item may override the period and grain. PDF produces one document with a
  section per report, XLSX one workbook (one sheet per report), CSV one file per
  report, zipped when there is more than one.
- **Recurrence presets, no cron.** Daily, weekly (weekdays), monthly (day 1 to 31, in
  shorter months the last day; optional "Montag bis Freitag" only), yearly, at a time
  of day in a chosen time zone; daylight-saving safe. A "Nächste Termine" preview
  shows the next runs.
- **Recipients.** Agents, groups, roles and (if the admin enabled
  `reporting.schedule_external_recipients`) external e-mail addresses. Anything other
  than the owner needs the share right (Znuny group `stats`, `rw`) and is checked again
  at every run. All real recipients go in Bcc (`undisclosed-recipients:;` in To), so
  nobody sees the others and nothing is sent to the system sender.
- **Owner permissions.** A schedule runs with its owner's queue scope and rights; the
  mail footer and the PDF say so. If the owner loses report access or the share right,
  or an item is no longer available, the run fails whole, the owner gets a mail and
  after 3 failed runs the schedule pauses itself ("Nach 3 Fehlversuchen angehalten").
- **Options.** Subject and body with placeholders (`{{schedule}}`, `{{period}}`,
  `{{date}}`, `{{reports}}`, `{{owner}}`), `only_if_nonempty` (nothing is sent when
  every report is empty), "Jetzt senden" (send now: a test to the owner only, or to
  all recipients).
- **Run history** with status, row count, recipient count and the stored files
  (download for owner and admins); retention `reporting.run_retention_days`
  (default 90, max 365).
- **Limits.** 50 schedules per agent, 50 recipients per delivery by default, 15 MB of
  attachments by default, PDF capped at 1000 rows per report and 5000 per document
  (a note marks the cut). Unattended runs use the server text bundle in German or
  English only.

### Search profiles

Znuny keeps the saved searches of an agent as `search_profile` rows (login
`TicketSearch::<login>`). Tiqora imports them on demand (`GET|POST
/api/v1/saved-searches/import/znuny`, "Suchvorlagen aus Znuny übernehmen" in the
searches sidebar) and never writes the Znuny table:

- **One-way and read-only.** The import copies; later changes in Tiqora do not go
  back to Znuny. Only the importing agent's own profiles are read (login compared
  case-insensitively); `last-search` is skipped. The result is a private saved search
  (marked "aus Znuny") named like the profile.
- **Re-import updates in place.** Importing a profile again (it was changed in Znuny
  afterwards) replaces the criteria and the archive flag of the search created
  earlier and raises its version; name, sharing and sort order are kept.
- **Partly mapped profiles are not preselected.** A profile with anything that
  could not be taken over is still importable but carries the badge "nur teilweise
  übernommen" and is not ticked in the dialog; the list shows each skipped key with
  the reason. Fully mapped profiles are preselected. A profile where nothing maps (or
  whose result is not a valid Tiqora filter) cannot be imported.
- **Not taken over:** full-text search (`Fulltext`, `From`, `To`, `Cc`, `Subject`,
  `Body`, attachment name, article dynamic fields), time points in the future
  (`Next`/`After`), close, last-change, escalation and article times, search
  operators in patterns, dynamic fields that are unknown or of an unsupported type,
  and every key Tiqora does not know.

Mapping (conditions are ANDed; keys with an empty value are ignored, Znuny stores
every form field):

| Znuny key | Tiqora condition | Not mappable |
|---|---|---|
| `QueueIDs` (+ `UseSubQueues`: all queues below the selected ones) | queue is one of | non-integer: "Kein gültiger Wert." |
| `StateIDs`, `PriorityIDs`, `TypeIDs`, `ServiceIDs`, `SLAIDs`, `OwnerIDs`, `ResponsibleIDs` | state / priority / type / service / SLA / owner / responsible is one of | non-integer |
| `StateTypeIDs` | state type is one of | unknown state type |
| `WatchUserIDs` | watched by one of | non-integer |
| `LockIDs` | only locked kinds (`lock`, `tmp_lock`) -> locked; only `unlock` -> not locked; both -> no condition | unknown lock type |
| `TicketNumber` | number equals / starts with (`abc*`) / contains (`*abc*`); several values -> any | operators (`\|\|`, `&&`, `!`, brackets, quotes, other `*` placements) |
| `Title` | every word: title contains the word | operators |
| `CustomerIDRaw`, `CustomerUserLoginRaw` | customer id / login equals (several: is one of) | |
| `CustomerID`, `CustomerUserLogin` (only without the Raw key) | same pattern rule as ticket number | operators |
| `SearchInArchive` | `AllTickets` -> include archived; `ArchivedTickets` -> include archived and archived only; `NotArchivedTickets` -> nothing | non-admins: archived tickets are only searchable by admins, reported as skipped |
| `TicketCreateTime*`, `TicketChangeTime*`, `TicketPendingTime*` | created / changed / pending until: "last N units" -> within last, "before N units" -> older than, time slot -> between / after / before | future time points; invalid or inverted dates; invalid amount |
| `TicketCloseTime*`, `TicketLastChangeTime*`, `TicketEscalationTime*`, `ArticleCreateTime*` | none | no such field in Tiqora's search |
| `Search_DynamicField_<Name>` | Text/TextArea: pattern rule; Dropdown/Multiselect: is one of; Checkbox: yes / no; Date/DateTime: time point or slot as above | unknown field, unsupported type, operators |

Details: a time point of N months counts as 30 days each and N years as 365
days; a time slot is read in the importing agent's time zone, and, like Znuny, its
end day is included. A single `*` matches everything and adds no condition.
Pattern values with other `*` placements or operators are reported, not guessed.
