# GenericInterface: complete compatibility where it makes sense — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

Every existing Znuny/OTRS GenericInterface integration — inbound **and**
outbound — keeps working against Tiqora without changes, and webservices can
be managed in Tiqora. The compat layer stays a bridge: new integrations are
steered to `/api/v1`, webhooks, MCP or plugins.

**This reverses two documented non-goals** (`docs/compatibility.md`: webservice
editor and requester "by design" absent). Both docs change when this ships.

## Current state (2026-10-08)

- Provider only (`api/compat/*`): `SessionCreate/Get/Remove(Delete)`,
  `TicketCreate/Update/Get/Search`, `TicketHistoryGet`,
  `TimeAccountingGet`, `OutOfOffice`; everything else → 501.
- Transports: REST (fixed routes + dynamic routes from
  `gi_webservice_config` `RouteOperationMapping`) and SOAP 1.1/1.2 with XXE
  protection. Webservice changes need a **restart** (`/admin/reload` only
  validates).
- **No mapping** (neither Simple nor XSLT) — requests/responses are passed
  as if the webservice had no mapping configured.
- **No requester/invoker**, no `gi_debugger_entry` logging, no
  `gi_webservice_config_history`.
- Known gaps (`docs/compatibility.md`): notification overrides
  (`NoAgentNotify` …), `TimeUnit` → `time_accounting`, only first `Article`,
  `AutoResponseType`/`HistoryType` overrides, `AppendSignatureToBody`,
  `HTMLBodyAsAttachment` ignored, no article-level dynamic fields in
  TicketGet, TimeAccounting queue is the current queue, SessionCreate writes
  Redis only, TicketSearch lacks `Created*` filters, flags, mentions,
  attachment-name search and `Result=COUNT`, OutOfOffice without CSV bulk.
- **Stale doc**: `ArticleSend` is listed as not emulated but is implemented
  (`operations.py` `_wants_article_send` / `_deliver_article`).
- Native alternatives exist: `/api/v1`, HMAC-signed webhooks
  (`tiqora_webhook`, `worker/webhooks.py`), MCP.

## Znuny 6.5/7.x GI surface (reference, from source)

- Operations: `Session::*`, `Ticket::TicketCreate/Get/HistoryGet/Search/
  Update`, `Ticket::TimeAccountingGet`, `User::OutOfOffice`, `Test::Test`;
  ITSM adds `ConfigItem::ConfigItemCreate/Get/Search/Update/Delete`; FAQ
  add-on adds `FAQ::*`.
- Invokers: `Ticket::Generic` (event-triggered, with event conditions),
  `Test::*`, the invokers behind the `WebserviceDropdown` /
  `WebserviceMultiselect` dynamic field types.
- Transports: `HTTP::REST`, `HTTP::SOAP` — requester auth BasicAuth and
  `OAuth2Token` (6.5+), proxy, SSL client certs, custom headers.
- Mapping: `Simple` (key/value maps with default rules), `XSLT` (with
  pre/post regex, data includes).
- Error handling: `RequestRetry` (retry schedule, max count/period),
  `Tunnel` (hand the error to another module); asynchronous invocation via the
  scheduler (`scheduler_task`, type `GenericInterface`).
- Debugger (`gi_debugger_entry`, `gi_debugger_entry_content`) with
  debug levels; webservice history (`gi_webservice_config_history`).

## Boundary ("soweit es Sinn macht")

| In scope | Out of scope (with reason) |
|---|---|
| Every core provider operation incl. all documented parameter gaps | Custom Perl operations/invokers from OPM packages — Perl cannot run; use plugins (see plugin design) |
| Simple + XSLT mapping, inbound and outbound | WSDL serving — Znuny does not serve one either |
| Requester: `Ticket::Generic` invoker, event triggers with conditions, sync + async, `RequestRetry` | Package Manager remote install over GI |
| REST + SOAP requester transports with BasicAuth, OAuth2 token, client certs, proxy, headers | `Test::*` operations/invokers beyond what golden tests need |
| `ExecuteInvoker` process action | |
| `WebserviceDropdown` / `WebserviceMultiselect` dynamic field types | |
| Debugger log in Znuny's tables + UI | |
| Webservice editor with YAML import/export, history, hot reload | |
| `ConfigItem*` operations (on the Tiqora CMDB, see ITSM design) | |
| FAQ operations mapped to the Tiqora KB (phase 3, only if a pilot needs it) | |

## Fixed decisions

### 1. Webservice config stays in Znuny's tables

- Read and write `gi_webservice_config` and `gi_webservice_config_history`
  exactly as `Kernel::System::GenericInterface::Webservice` does (YAML
  format, history row per change), so a webservice edited in Tiqora also
  works in Znuny and vice versa (ground rule 3).
- Hot reload: the API re-mounts dynamic routes when the config's `change_time`
  changes (checked every 10 s through a cheap max(change_time) query and
  broadcast via Redis to all API workers) — no restart.

### 2. Mapping engine

- `tiqora/api/compat/mapping/simple.py`: Znuny `Mapping::Simple` semantics
  (KeyMapExact, KeyMapRegEx, KeyMapDefault Keep/Ignore/MapTo, same for
  values), golden-tested against Znuny's own unit test vectors.
- `tiqora/api/compat/mapping/xslt.py`: `lxml` XSLT with
  `XSLTAccessControl.DENY_ALL` (no file/network access), no extension
  functions except a reimplementation of Znuny's `OTRSFunctions.xsl` helpers
  (date conversion), pre/post regex, `DataInclude`. The data ↔ XML conversion
  follows Znuny's `XML::Simple`-style rules (array handling is the classic
  trap — test with Znuny's vectors).
- Applied in both directions for provider and requester.

### 3. Requester / invokers

- Tiqora worker takes over invokers behind a feature flag
  `daemon.gi_requester` — **mutually exclusive** with Znuny's daemon, like
  every other takeover (`docs/parallel-operation.md`).
- **Why this matters in parallel operation today:** Znuny invokers fire only
  on events raised inside Znuny. Ticket changes made in Tiqora do not raise
  Znuny events, so an existing outbound integration silently misses them.
  With the takeover, Tiqora fires invokers for changes from **both** sides
  (Tiqora writes via the outbox, Znuny writes via the existing
  `ticket_history` poller) — documented as a known gap until then, with an
  operator warning on the Daemons page when a valid webservice has invokers
  configured and the flag is off.
- Event mapping: Znuny event names (`TicketCreate`, `TicketStateUpdate`,
  `ArticleCreate`, …) with Znuny's event condition filters
  (`Condition` with `Type` String/Regexp/Module-less); `Asynchronous` yes/no.
- Async execution via the generic job table (`tiqora_job`, see master
  roadmap) with `RequestRetry` semantics (schedule, max retries, max
  period) and `Tunnel`.
- Transports: `httpx` through `security/outbound.py` (SSRF guard, with an
  explicit per-webservice allowlist for internal targets, since GI is often
  used on internal networks), BasicAuth, OAuth2 token (reuse
  `domain/oauth2_mail.py` token store / Znuny `oauth2_token_config`),
  client certificates, custom headers, proxy, timeouts. SOAP requester builds
  envelopes the way `HTTP::SOAP` does (namespace, SOAPAction, sort order).
- `Ticket::Generic` invoker payload identical to Znuny's (ticket + articles
  + dynamic fields per its config keys), golden-tested.
- `ExecuteInvoker` process action and `WebserviceDropdown/Multiselect`
  dynamic field types use the same requester core.

### 4. Debugger

- Write `gi_debugger_entry` / `gi_debugger_entry_content` in Znuny's format
  with the webservice's `DebugThreshold`, so Znuny's debugger shows Tiqora
  traffic too. PII: content is stored as Znuny does (it is the operator's
  explicit choice of debug level); retention cleanup like Znuny's
  `Maint::GenericInterface::DebugLog::Cleanup`.
- Tiqora UI: debugger view per webservice (filter by type, time, remote IP;
  request/response detail; "Erneut senden" for requester entries).

### 5. Provider gaps

All gaps listed above get closed with golden tests against real peers:
`TimeUnit` → `time_accounting`, all articles, notification override flags
honoured (they map to Tiqora's notification takeover), `AutoResponseType`,
`HistoryType/HistoryComment`, `AppendSignatureToBody`,
`HTMLBodyAsAttachment`, article dynamic fields in TicketGet, TimeAccounting
queue at entry time (from history), `SessionCreate` also writing Znuny's
`sessions` table when Znuny is still serving (so a session works on both),
all TicketSearch filters incl. `Result=COUNT`, OutOfOffice CSV.

### 6. Webservice editor

- Admin **Integrationen → Webservices**: list, create, YAML import/export
  (Znuny's format; also `Admin::WebService::Add/Update/Dump` console
  equivalents in `tiqora` CLI), provider operations with route mapping,
  requester invokers with events, mapping editors (Simple: table editor;
  XSLT: code editor with a "test with sample data" button), error handling,
  debugger tab, history with diff and restore.
- Every save validates the YAML against a Pydantic model of Znuny's config
  structure; unknown keys are kept verbatim (round-trip safe).

## Beyond Znuny

- "Test with sample data" for mappings and invokers (dry run, no send).
- Debugger "replay" of a failed request after fixing a mapping.
- Each webservice can be exposed as an MCP tool set or a webhook preset —
  integrations see one model.
- OpenAPI document generated for each REST provider webservice (from route
  mapping + operation schemas) — Znuny has no machine-readable description.

## Tests

- Golden: same requests to Znuny 6.5/7.3 and Tiqora with the same
  webservice config (incl. Simple/XSLT mappings) → byte-equal responses
  modulo timestamps/ids; requester payloads captured from both by a test
  HTTP sink and compared.
- Mapping engines: Znuny's unit-test vectors ported.
- Security: XSLT access control (no `document()`, no file reads), SSRF guard,
  SOAP XXE (existing tests extended to the requester).

## Phases

| Phase | Content | Size |
|---|---|---|
| 1 | Simple + XSLT mapping (provider), hot reload, provider gap list, stale-doc fix | 2 weeks |
| 2 | Requester: `Ticket::Generic`, events + conditions, REST/SOAP transports, auth, `RequestRetry`, async via jobs, takeover flag, debugger write + UI, `ExecuteInvoker` | 3 weeks |
| 3 | Webservice editor + history, `WebserviceDropdown/Multiselect` DF types, `ConfigItem*` (after CMDB), optional FAQ ops, per-webservice OpenAPI | 2–3 weeks |

## Risks / open

- XSLT data ↔ XML conversion fidelity is the hard part; budget time for
  golden comparisons with real customer mappings.
- Requester takeover must avoid double sends during the switch (flag flip
  is atomic per webservice; document the order: disable in Znuny, then enable
  in Tiqora).
