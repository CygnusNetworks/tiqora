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
regexes are possible. Every mapping runs in a worker thread, so a slow
mapping never blocks other requests.

**XSLT security note.** Templates run in a sandbox: no file access and no
network access, no `document()`, no `exsl:document`. The only external
file a template can import is `OTRSFunctions.xsl`; any href ending in that
name (`/opt/otrs/Kernel/...`, `/opt/znuny/Kernel/...`, relative `Kernel/...`)
is served from the copy bundled with Tiqora, so templates written for Znuny
keep working. Every other import or include fails with a readable error. The
output is capped at 256 MB. libxslt cannot be interrupted, so a template has no
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
mapping type, bad regex) only affects its own webservice.

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
- **Purge.** Expired `sessions` rows are removed by the worker loop
  `znuny_session_purge` (every 300 s, always on, interval
  `TIQORA_ZNUNY_SESSION_PURGE_INTERVAL_SECONDS`). It is a port of Znuny's
  `GetExpiredSessionIDs` (session start + `SessionMaxTime` or last request +
  `SessionMaxIdleTime` is in the past), the same job as Znuny's
  `Maint::Session::DeleteExpired`; running both is harmless.
- **Revocation.** Disabling an account, a password reset or setup, a customer
  password change in the portal and GDPR erasure delete the person's Znuny
  session rows (database first, then Redis).
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

### Admin reload

`POST /znuny-compat/admin/reload` (requires an admin) refreshes the webservice
registry immediately and wakes the other API processes through Redis. See
[Hot reload](#hot-reload); no restart is needed.

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

## What is not emulated (and why)

Compat is a **Provider-side bridge** for the GenericTicketConnector surface
and a few core ops — not a reimplementation of Znuny’s entire GenericInterface
framework. Items below are intentionally incomplete or out of scope.

### Out of scope (product / architecture)

| Gap | Why deferred |
|-----|----------------|
| Requester / outbound invokers (Tiqora calling external SOAP/REST) | Only the **server** side is emulated; outbound is a separate subsystem |
| Custom OPM operations (e.g. CMDB `ConfigItem*`) | Installation-specific; no stable core wire contract |
| Package Manager remote install | Deploy/admin concern, not ticket integrator wire |
| WSDL auto-serving | Znuny does not serve one either — see [SOAP transport](#soap-transport) |

### TicketCreate / TicketUpdate

`ArticleSend: 1` (agents, TicketCreate and TicketUpdate) sends the article as e-mail through the same outbound path as agent replies (`deliver_agent_email_reply`), including `EmailSecurity` — see [crypto.md](crypto.md). Multiple `Article` entries, `TimeUnit`, `AutoResponseType`, `HistoryType`/`HistoryComment`, article dynamic fields and `NoAgentNotify` are implemented like Znuny.

| Gap | Behaviour |
|-----|----------------|
| `ForceNotificationToUserID`, `ExcludeNotificationToUserID`, `ExcludeMuteNotificationToUserID` | Validated with Znuny's messages but, as in Znuny's GI, not passed on to the article creation |
| `SortOutbound` (operation config) | Not applied; response JSON keys are always sorted |
| `TicketCreate` without `Article` | Accepted (Znuny rejects it); existing Tiqora clients rely on it |

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
| Non-integer element in a TicketSearch id filter | whole search returns empty (as Znuny) | empty |
| Operation errors | keep Tiqora's `ErrorCode` to HTTP status mapping (401, 403, 400, 5xx), computed before the outbound mapping | HTTP 200 for operation errors |
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
  authenticated user), and it now refreshes the webservice registry.

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
- `ArticleSend`: the mail leaves after the commit. If it fails, the call
  returns Znuny's error text while the ticket and articles stay. The signature
  is converted to the article's content type and appended directly behind the
  body. Remaining mails are still sent after a failure.
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
  and writes the `Misc` history row "Reset of unlock time.".

**TicketSearch**

- No hit answers `{}` (no `TicketID` key) instead of `{"TicketID": []}`.
- A non-integer element in any id filter makes the whole search return empty
  (Znuny); an id outside its column range matches nothing instead of failing.
- `MIMEBase_*` and `Fulltext` match substrings case-insensitively (Znuny GI wraps the
  value in `*`); a value made only of `*` is ignored. `MIMEBase_Bcc` and, when
  `Ticket::Article::Backend::MIMEBase::IndexAttachmentNames` is on,
  `MIMEBase_AttachmentName` can be searched.
- New `Created*` filters and the time-window filters (`TicketCreate*`,
  `TicketChange*`, `TicketLastChange*`, `TicketClose*`, `TicketPending*`,
  escalation times) and sort keys. `SortBy` `PendingTime` / `EscalationTime` sorts
  0 last. Pending filters apply to pending states only.

**Sessions, time accounting, out of office**

- `SessionCreate` returns a Znuny-format 32-character `SessionID` backed by Znuny
  `sessions` rows (no Redis token) unless
  `TIQORA_COMPAT_SESSION_WRITE_ZNUNY=false` or Tiqora owns the schema; see
  [Session handling](#session-handling) for expiry, purge and the remote-IP caveat.
  GI calls do not refresh `UserLastRequest`, so the session ends at Znuny's idle limit.
- `SessionGet` returns only an allow-list of non-secret keys (no password hash,
  tokens or secrets).
- Sessions of disabled accounts stop authenticating; password reset, setup and
  change, and GDPR erasure delete the account's Znuny session rows.
- `TimeAccountingGet`: entries carry no `TicketID`; `Queue` is the queue at
  booking time (falling back to the current queue when the history has no
  `NewTicket` row); `TimeUnit` is a normalised string (`"1.5"`, Znuny shows
  `"1.50"`); `Created` is `YYYY-MM-DD HH:MM:SS`. Unparsable
  `TimeAccountingStart`/`End` answer `{}`.
- `OutOfOffice` answers `{"Success": 1}`; the per-entry result of
  `OutOfOfficeEntries` is gone and unknown entries are skipped. It accepts
  `OutOfOfficeEntriesCSVString` on the canonical route.

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
- Deferred relative to full Znuny admin breadth:
  `group_customer` (company-level) assignment endpoints
  (`group_customer_user` is implemented), SysConfig deploy UI, GI webservice
  admin, package manager.

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
| Saved searches | missing | Search supports filters/tokens, but they cannot be saved |
| Configurable dashboard widgets | missing | Fixed dashboard sections, no per-agent layout |
| Link overview / link-type admin | missing | Ticket links can be listed and created; no delete, overview page or FAQ/appointment targets |
| Note to linked tickets (`AgentTicketNoteToLinkedTicket`) | missing | |
| Stats framework | partial | Fixed reports under `/api/v1/stats` (volume, open snapshot, SLA, workload, backlog) with CSV export; no configurable statistics, no scheduled delivery |
| Ticket export | partial | `GET /tickets/export.csv` (synchronous) plus `POST /tickets/export-jobs` which runs the export as a background job with progress, cancel and download; no Znuny equivalent (Znuny has CSV via stats only) |
| Bulk action | partial | State, priority, owner, queue, lock only |
| Preferences | partial | Language, TOTP/passkeys; no queue favourites, ticket-list options or notification matrix |
| Free-text edit screen (`AgentTicketFreeText`) | partial | Dynamic fields are editable in ticket zoom, limited to the fields enabled in `Ticket::Frontend::AgentTicketFreeText###DynamicField` (stock Znuny enables none, so the "edit" button only appears once configured); ACL-filtered options; types Text, TextArea, Dropdown, Multiselect, Checkbox, Date, DateTime. Writes use Znuny's typed columns, no-op and history rules. Other types (e.g. ticket/article references) stay read-only |

### Admin

| Znuny feature | Status | Note |
|---|---|---|
| SysConfig edit/deploy UI | by design | Settings are read from `sysconfig_*`; change them in Znuny or via `Admin::Config::Update` |
| Package Manager (OPM), Support Data Collector, Cloud Services, OTRSBusiness, web installer | by design | |
| Sessions, SelectBox SQL, performance log, maintenance windows | missing | A user's sessions can be revoked from user admin |
| General system log / communication log | missing | Mail log and AI audit exist |
| Calendar admin, ticket appointment rules (queue/SLA) | missing | Agent calendar exists; only the calendar name is set on queue/SLA |
| DynamicFieldScreen admin | missing | Screen assignment is read, not editable |
| `group_customer` (company level) assignment | missing | `group_customer_user` is implemented |

### GenericInterface

| Znuny feature | Status | Note |
|---|---|---|
| Webservice admin / editor | by design | Webservices are loaded from the database; no editor |
| Requester side / invokers | by design | See [What is not emulated](#what-is-not-emulated-and-why) |
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
| Process designer | by design | Processes are imported and executed; admin UI is list/detail ([process-management.md](process-management.md)) |
| `Module` conditions, `ExecuteInvoker`/`Appointment*`/`ConfigItemUpdate` actions, smart tags | missing | Logged as unsupported |
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
