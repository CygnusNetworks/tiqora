# Wave 3 — GenericInterface phase 2 (Requester, Invokers, Debugger) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Existing Znuny GenericInterface **outbound** integrations keep working when Tiqora takes over: `Ticket::Generic` invokers fire on ticket/article events from **both** Znuny and Tiqora writes (with Znuny's event conditions), call REST or SOAP endpoints with BasicAuth / OAuth2 token / client certificates / proxy / custom headers, apply the wave-1 Simple/XSLT mappings outbound and inbound, retry with `RequestRetry`, run the Znuny 6.5 response actions (`OTRS_TicketTitleUpdate` …), and every request/response is visible in a debugger (with "Erneut senden"). The `ExecuteInvoker` process action works. All of it is behind the takeover flag `daemon.gi_requester.enabled` (default off), mutually exclusive with Znuny's own GI event handler.

**Architecture:** A new backend package `tiqora.gi` holds the requester core (pure config model, condition checker, error handling, transports, `Ticket::Generic` invoker, debugger, pipeline). Webservice configs are read from Znuny's `gi_webservice_config` with the wave-1 parser (`tiqora.api.compat.webservices`), extended by a `Requester` section parser. Event source is a dedicated worker loop `gi_requester` that follows `ticket_history` (reusing the wave-2 plugin helpers `events_from_history` + `GapTracker`), evaluates every valid webservice's invoker events + conditions, and enqueues one `tiqora_job` (`kind="core.gi_invoke"`) per match **in the same transaction that advances its cursor** (exactly-once enqueue). The job handler runs the Znuny `Requester.pm` pipeline (`PrepareRequest` → outbound mapping → transport → inbound mapping → `HandleResponse`), writes debugger rows, and on failure runs `ErrorHandling`/`RequestRetry`, which enqueues a **new** job with `due_at` and `PastExecutionData` — exactly like Znuny's scheduler `TaskAdd`. Egress goes through `tiqora.security.outbound` (DNS-pinned, no redirects) with a Tiqora-side per-webservice allowlist for internal targets. The debugger is stored in Tiqora tables shaped like Znuny's `gi_debugger_entry*`; the debugger UI also reads Znuny's own debugger tables read-only, so one view shows both sides. The wave-1 provider pipeline gets the same debugger.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic two-chain, Pydantic v2, httpx (async, `MockTransport` in tests), lxml (SOAP envelopes; wave-1 dependency), PyYAML, the wave-0 job system (`tiqora.jobs`), the wave-1 mapping engines (`tiqora.api.compat.mapping`), the wave-2 history follower helpers (`tiqora.plugins.events`), pytest + testcontainers (MariaDB + PostgreSQL), golden suite against a real Znuny 6.5 peer with an HTTP sink; React 19 + TanStack Query/Router + Tailwind tokens, Vitest, Playwright (demo mode).

**Spec:** `docs/superpowers/specs/2026-10-08-generic-interface-design.md` — phase 2 row of "Phases" (Requester: `Ticket::Generic`, events + conditions, REST/SOAP transports, auth, `RequestRetry`, async via jobs, takeover flag, debugger write + UI, `ExecuteInvoker`) plus fixed decisions 3 (requester/invokers) and 4 (debugger). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (wave 3, principles 1, 3, 5, 6). Builds on: wave 0 (`docs/superpowers/plans/2026-10-08-wave0-foundations.md`: job system), wave 1 (`docs/superpowers/plans/2026-10-08-wave1-gi-mapping.md`: mapping engines D2–D7, webservice model/registry D8, provider pipeline D11, `ticket_get.py` D13), wave 2 (`docs/superpowers/plans/2026-10-10-wave2-plugin-system.md` P8: `ticket_history` follower helpers). **Not in this plan** (phase 3): webservice editor + YAML import/export + history, hot-reload UI, `WebserviceDropdown/Multiselect` dynamic field types, `ConfigItem*`, FAQ, per-webservice OpenAPI, MCP/webhook presets.

**Scope marker:** backend Tasks 1–16, frontend Tasks 17–18, golden Task 19, docs + verification Task 20. One migration (Task 1).

---

## Open questions for the product owner

Every item below is decided conservatively in this plan and marked **Assumption:** where it is used. Answers can change a task before it starts; none blocks the start of Task 1.

1. **Debugger in Znuny's tables?** Spec decision 4 says Tiqora writes `gi_debugger_entry*` so Znuny's debugger shows Tiqora traffic. The project rule "write only `tiqora_*` tables except via existing Znuny write services" forbids it (there is no Znuny write service for the debugger). **Assumption:** Tiqora writes its own `tiqora_gi_debug_entry` / `tiqora_gi_debug_content` (same columns as Znuny's) and Tiqora's debugger view reads **both** (Znuny's read-only). Znuny's own debugger GUI does not show Tiqora traffic. Switching later is one target-table change in `tiqora.gi.debugger.store` (G3).
2. **Takeover granularity.** Spec risk note: "flag flip is atomic per webservice". Znuny can only switch its GI event handler off globally (`Ticket::EventModulePost###9900-GenericInterface`), and the webservice YAML is shared, so a per-webservice switch has no Znuny counterpart. **Assumption:** one global flag `daemon.gi_requester.enabled`; the Daemons page warns in both mismatch directions (G12).
3. **Flag on while Znuny's GI event handler is still valid.** **Assumption:** warn (red notice on the Daemons page + `gi_requester_znuny_handler_active` log line each tick), do not refuse — refusing would silently drop events the operator expects. Alternative: refuse to send until the Znuny setting is invalid.
4. **`ExecuteInvoker` in Tiqora process dialogs without the takeover flag.** Znuny never sees Tiqora's dialog submits, so there is no double-send risk. **Assumption:** `ExecuteInvoker` runs whenever the webservice is valid, independent of `daemon.gi_requester.enabled`, and is **always** queued as a job (never inside the dialog-submit request, nginx 90 s).
5. **Internal targets (RFC1918/ULA/CGNAT).** GI is often used on internal networks. **Assumption:** blocked unless a Tiqora admin lists the target (hostname or CIDR) in the webservice's Tiqora settings; loopback, link-local (incl. 169.254.169.254), multicast, unspecified and reserved addresses are never allowed, even when listed.
6. **`SSLNoHostnameVerification` / self-signed endpoints.** **Assumption:** honoured only when the webservice's Tiqora setting "Unsichere TLS-Verbindung erlauben" is on (admin opt-in in Tiqora); otherwise the request fails with a clear debugger error.
7. **Client certificate / key / CA files** (`X509CertFile`, `X509KeyFile`, `X509CAFile`, `SSL.SSLCertificate`, `SSL.SSLKey`, `SSL.SSLCAFile`, `SSL.SSLCADir`). These are file paths on the Znuny host. **Assumption:** Tiqora reads them only below directories listed in `TIQORA_GI_CERT_DIRS` (default empty = client certificates disabled, clear error); operators mount Znuny's cert directory read-only into the worker. No upload UI in phase 2.
8. **HTTP redirects.** Znuny's LWP follows redirects for GET/HEAD. **Assumption:** Tiqora never follows redirects (SSRF guard); a 3xx is a transport error with the `Location` in the debugger.
9. **Authentication types beyond the spec.** Znuny 6.5 REST also has `JWT`; SOAP has `NTLM`. **Assumption:** both are reported as a configuration error ("Anmeldung per JWT/NTLM wird von Tiqora nicht unterstützt") in the debugger; no silent unauthenticated send.
10. **Synchronous invokers (`Asynchronous: 0`).** In Znuny they run inside the request that changed the ticket. **Assumption:** in Tiqora they run in the worker a few seconds later, in a one-slot job lane (`gi_sync`) so their order equals the event order; async invokers use lane `gi` (2 slots, no order guarantee — as in Znuny's scheduler).
11. **`OldTicketData` in the event block.** Znuny passes the full ticket hash before the change for `TicketStateUpdate`, `TicketQueueUpdate`, `TicketSLAUpdate`, `ArticleCreate`. `ticket_history` has no before-image. **Assumption:** the `Event` block carries only the keys Tiqora can derive (G9); `OldTicketData` is omitted and documented.
12. **Response action `OTRS_TicketArticleCreateEvent`** (re-raises `ArticleCreate` in Znuny, which fires notifications and other invokers). **Assumption:** not supported in phase 2 — logged as a debugger error, nothing else happens.
13. **Event types beyond Ticket/Article** (`Queue`, `CustomerUser`, `CustomerCompany`, `DynamicField`, `LinkObject`, `Appointment`, `Package` event modules). **Assumption:** not fired in phase 2; the webservice detail lists such invoker events as "wird von Tiqora noch nicht ausgelöst".
14. **Debugger retention.** Znuny's daily cleanup keeps 14 days. **Assumption:** Tiqora's cleanup of its own debugger tables runs daily at 03:02 UTC with 14 days (`gi.debugger.retention_days`), always on (it only touches Tiqora tables); GDPR erasure of a ticket also deletes Tiqora debugger entries that reference it.
15. **Debug content format.** **Assumption:** sorted, indented JSON (not Perl `Data::Dumper`), secrets masked; Znuny rows are shown as stored.

---

## Global Constraints

- Work in the worktree the executor is given (never in `<repo>` unless that is the given worktree); never push (a push to `origin main` deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data (`example.org`, `.test`, `example.com` hostnames; RFC 5737 / RFC 3849 documentation IPs `192.0.2.0/24`, `198.51.100.0/24`, `2001:db8::/32` in tests). Use `rtk git`; stage explicit paths only; run `rtk git diff --cached --name-only` before every commit (other agents share the index); check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their own rows and are never added to `backend/tests/db_leak_baseline.txt`.
- **Write only `tiqora_*` tables, except through the existing Znuny write services** (`tiqora.domain.ticket_write_service`, `tiqora.znuny.history.history_add`, `tiqora.domain.dynamic_field_values`). This plan reads `gi_webservice_config`, `gi_debugger_entry*`, `oauth2_token_config`/`oauth2_token` (via `tiqora.domain.oauth2_mail`, which already writes refreshed tokens the Znuny way), `ticket_history`, `ticket`, `article*`, `sysconfig_*`; it never writes Znuny tables directly. Never change Znuny DDL.
- **Never JOIN `tiqora_*` tables with Znuny tables** (prod MariaDB mixes `general_ci`/`unicode_ci` collations → error 1267). Load keys from one side, then query the other with an `IN` list. (Integer-only joins inside Znuny tables, like `ticket_history` ⋈ `ticket_history_type`, are fine.)
- **No `db.begin()` after autobegin**: a session that already executed a statement is in a transaction; call `commit()`/`rollback()`, never `async with session.begin()` on it.
- **No long synchronous endpoints**: anything that can exceed a few seconds (a send, a resend, a dry run with attachments) is a background job + polling (`GET /api/v1/jobs/{id}`); production nginx cuts `/api/` after 90 s. No HTTP request handler in this plan performs an outbound call.
- **Migrations:** Alembic two-chain setup; the new migration goes into `backend/alembic/versions_tiqora` with revision = the **next free number at implementation time** after the current primary head (on 2026-10-11 the head is `20261010_0072` after the web chat merge — verify with `ls backend/alembic/versions_tiqora | tail -3` and `cd backend && uv run alembic heads`; never copy an id from this document); follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assertion in `backend/tests/test_migrate_cli.py`; ORM DDL must equal the migration (`backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB **and** PostgreSQL: every DB test runs on both dialects: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern `backend/tests/test_tickets_telegram_api.py`).
- Seed ids only inside this plan's band **51000–51499** (sub-bands per task below). Confirm free before seeding: `grep -rnE "\b51[0-4][0-9]{2}\b" backend/tests` (free on 2026-10-11 in the read worktree; other wave-3 plans pick bands in parallel — shift within the band if needed). Seeded logins/names contain `gi51`. Autoincrement rows created by services (tickets, articles, history, jobs, debugger rows) are removed with `tests._row_cleanup.snapshot_marks` / `delete_above_marks` (shared helper `backend/tests/_gi_fixtures.py`, Task 1).
- Znuny YAML/SysConfig checkbox values are Perl-truthy strings (`'0'` is false): use `tiqora.api.compat.mapping.perl.perl_truthy` / `SysConfig.get_bool`, never `bool(...)`.
- Timestamps the application writes into `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; never compare them with `func.now()` (skill `db-session-timezone-vs-app-written-rows`). API datetimes use `tiqora.domain.schemas.UtcDateTime`. Znuny debugger rows (`gi_debugger_entry.create_time`) are Znuny-local time as stored (OTRSTimeZone=UTC in practice) and are serialized with the same `UtcDateTime` rule as other Znuny timestamps.
- After any API model/route change: regenerate both openapi files and `schema.d.ts` (skill `tiqora-openapi-regen-or-ci-wipes-schema`): `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; check `head -c 40 packages/api-client/openapi.json` starts with `{"components"` and the diff is small. A Pydantic response field WITH a default becomes required in TS — fixtures and demo mocks must include it.
- Frontend (`frontend/`): `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc); **never** run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then propagate (skill `tiqora-i18n-key-propagation`: `node frontend/scripts/propagate-i18n-keys.mjs`; never `scaffold-locale.mjs` on existing locales). German UI strings address the user with **"Sie"**. New screens get demo-mode mocks (`VITE_DEMO=1`; this plan: `frontend/src/demo/giMock.ts`, wired with one line into `resolveData` in `mockData.ts`) and are checked in demo mode with Playwright (delete `.playwright-mcp` before committing). Colour tokens only via `frontend/src/themes/tokens.css` + `themeColor()`. API wrappers are hand-written `frontend/src/lib/*Api.ts` with `api.request<T>(method, path, {query, body, signal})` and `Schemas["…"]` types (pattern `frontend/src/lib/phoneApi.ts`).
- UI copy German first, plain words, named by what admins recognise: "Webservices", "Ausgehende Aufrufe", "Protokoll" (debugger tab — Znuny calls it "Debugger"; the tab label is "Protokoll (Debugger)"), "Erneut senden", "Probelauf", "Interne Ziele erlauben", "Unsichere TLS-Verbindung erlauben". No "SSRF", "Invoker-Backend", "Lane", "Cursor" in UI text (only in code/docs). "Invoker" stays as the Znuny term admins know, explained once in the page description.
- Znuny reference source (6.5 core) is at `znuny-6.5.22/` in the main checkout (`<repo>/znuny-6.5.22`). Every behaviour claim below names its Perl file; where a step says "verify in source", pin the result in a test before implementing.
- Plan-specific:
  - **Secrets never leave memory**: Basic-auth passwords, proxy passwords, `SSLPassword`, OAuth2 access tokens and any header value built from them are never written to logs, debugger content, job payloads, job results, API responses or exceptions. Masking is centralised in `tiqora.gi.redact` (G3) and pinned by tests in Tasks 3, 7, 8, 11, 16.
  - Every pure module (`conditions`, `error_handling`, `config`, `soap_envelope`, `redact`) is synchronous and touches no DB, network or file system.
  - XSLT mappings keep running through `run_mapper` (`asyncio.to_thread`) from wave 1.
  - Every outbound request: `pin_outbound_url(...)` (or the proxy variant), `follow_redirects=False`, explicit timeout from the transport config (Znuny `Timeout`, seconds; capped at `TIQORA_GI_MAX_TIMEOUT_SECONDS`, default 300), response body read with a size cap (`TIQORA_GI_MAX_RESPONSE_BYTES`, default 20 MiB).
  - The worker imports `tiqora.api.compat.webservices` / `.mapping` / `.ticket_get` lazily inside functions (precedent: `tiqora/jobs/handlers/gi_mail.py` imports `tiqora.api.compat.article_input`). Moving these shared modules out of `tiqora.api` is a phase-3 follow-up, not done here.
  - `tiqora.gi` imports `tiqora.plugins.events` (`events_from_history`, `GapTracker`, `HistoryRow`, `TicketEvent`) — a documented layering exception until those helpers move to a neutral `tiqora.events.history` module (follow-up).

## Review Focus

1. **Egress cannot be turned into an SSRF or file-read primitive through the shared YAML.** A Znuny admin (or anyone who can edit `gi_webservice_config`) sets `Host: http://169.254.169.254`, `http://127.0.0.1:6379`, a public hostname that resolves to `10.0.0.5`, a 302 to an internal URL, a `ProxyHost` of `http://localhost:3128`, or `X509KeyFile: /etc/shadow` → nothing is sent / read; the debugger shows a plain error. With the Tiqora allowlist containing `10.0.0.0/8`, only that range opens; loopback/metadata stay closed. → Task 6 (`test_metadata_never_allowed_even_if_listed`, `test_rebinding_host_pinned_and_rejected`, `test_cert_path_outside_allowed_dirs_rejected`, `test_proxy_host_checked`), Task 7 (`test_redirect_not_followed`).
2. **Secrets never reach the debugger, logs or job rows.** Basic auth, proxy auth, OAuth2 bearer tokens and `<OTRS_OAUTH2_TOKEN>`-substituted headers are masked as `***` everywhere; Znuny itself logs the `Authorization` header — Tiqora must not. → Task 3 (`test_redact_headers_and_config`), Task 7 (`test_debugger_never_contains_basic_auth`), Task 11 (`test_job_payload_and_result_contain_no_secret`), Task 16 (`test_webservice_detail_masks_secrets`).
3. **No double sends, no lost events across the takeover switch.** Flag off → cursor tracks the head, nothing is sent; flag on → events after the switch fire exactly once even if the worker crashes between enqueue and commit (cursor and jobs commit together); a job interrupted after its `sending` checkpoint is never resent automatically; history id gaps from in-flight Znuny transactions are held, not skipped. → Task 13 (`test_enqueue_and_cursor_commit_atomically`, `test_flag_off_moves_cursor_without_sending`, `test_gap_is_held_then_delivered`), Task 12 (`test_interrupted_after_checkpoint_not_resent`).
4. **Retry schedule equals Znuny's `RequestRetry`** for every config (factor 1/1.5/2, `RetryIntervalMax`, `RetryCountMax`, `RetryPeriodMax` clamp to initial + period, "not in the past → now + 1 s"), and a retried call is a new job carrying `PastExecutionData`. → Task 5 (Znuny `RequestRetry.t` vectors), Task 12 (`test_reschedule_enqueues_new_job_with_past_execution_data`), Task 19 (golden schedule comparison).
5. **Payload parity**: the `Ticket::Generic` request body for the same ticket and event is equal to Znuny's modulo ids/timestamps — including `TicketDeepGet` cleanup (keys matching `::|config|secret|passw|userpw|auth|token` removed), the single-article rule, attachments, `OmittedFields`/`Base64EncodedFields`, the REST controller interpolation (`:TicketID`, dot keys) and SOAP envelope (namespace, `SOAPAction`, `Sort`). → Task 9, Task 7, Task 8, Task 19.

Also pinned (not top five): a broken webservice config never stops other webservices' invokers (Task 2/13); a remote that hangs never blocks the worker beyond its timeout and never holds the follower tick (jobs, Task 12); the debugger threshold is honoured (Task 3); provider requests are logged with the client IP behind the trusted proxy (Task 14); `ExecuteInvoker` never sends inside the dialog-submit request (Task 15).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Existing names used exactly as they are in code (verify at implementation time; if one moved, use its new location and note it in the commit body): `pin_outbound_url`, `PinnedOutboundURL`, `OutboundURLError`, `is_blocked_ip`, `HostResolver` (`tiqora.security.outbound`); `enqueue_job`, `utc_now`, `purge_finished_jobs` (`tiqora.jobs.service`); `JobHandler`, `JOB_HANDLERS`, `LANE_SLOTS`, `DEFAULT_LANE` (`tiqora.jobs.registry`); `JobContext`, `JobFailed`, `JobCancelled` (`tiqora.jobs.context`); `events_from_history`, `GapTracker`, `HistoryRow`, `TicketEvent`, `HISTORY_EVENT_MAP` (`tiqora.plugins.events`); `autoincrement_step` (`tiqora.db.id_step`); `DaemonService`, `DAEMON_SERVICES` (`tiqora.worker.services`); `_interval_loop` (`tiqora.worker.__main__`); `record_tick_status` (`tiqora.worker.status`); `get_setting`, `get_setting_bool`, `get_setting_int`, `set_setting` (`tiqora.domain.settings_store`); `SysConfig` (`tiqora.znuny.sysconfig`); `build_mapper`, `MappingError`, `MappingConfigError`, `Mapper` (`tiqora.api.compat.mapping`); `perl_truthy`, `perl_is_string`, `compile_perl_regex` (`tiqora.api.compat.mapping.perl`); `parse_webservice`, `Webservice`, `replace_config_placeholders`, `run_mapper`, `load_fingerprint`, `_load_yaml_rows`, `WebserviceRegistry`, `get_registry` (`tiqora.api.compat.webservices`); `run_provider`, `ProviderOutcome` (`tiqora.api.compat.provider`); `build_ticket_entry`, `build_article_entries`, `attachment_index`, `znuny_ts`, `load_ticket_get_context` (`tiqora.api.compat.ticket_get`); `get_access_token`, `OAuth2MailError` (`tiqora.domain.oauth2_mail`); `history_add` (`tiqora.znuny.history`); `change_title`, `change_service`, `change_type`, `move_queue`, `change_sla`, `set_customer`, `change_state`, `assign_owner`, `assign_responsible`, `change_priority`, `update_dynamic_field`, `add_article`, `ArticleIn`, `create_ticket`, `TicketIn` (`tiqora.domain.ticket_write_service`); `execute_transition_action`, `_ACTION_HANDLERS` (`tiqora.process.engine`); `replace_action_config` (`tiqora.process.smart_tags`); `client_ip` (`tiqora.security.ratelimit`); `_TICKET_DELETE_ORDER` (`tiqora.gdpr.erasure`); `UtcDateTime` (`tiqora.domain.schemas`); `GiWebserviceConfig` (`tiqora.db.legacy.config`); frontend `api.request`, `Schemas`, `ADMIN_PAGES` (`frontend/src/lib/adminSearch.ts`), `DaemonsPage`, the wave-0 job progress component.

### G0. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/gi/__init__.py` | package docstring (what lives where), no logic |
| `backend/src/tiqora/gi/config.py` | pure: `RequesterConfig`, `InvokerConfig`, `InvokerEvent`, `RequesterTransport`, `ErrorHandlingModule`, `DebuggerConfig`, `parse_requester()` (G2) |
| `backend/src/tiqora/gi/loader.py` | worker-side snapshot of valid webservices incl. requester config (`GiWebservices`, `load_gi_webservices`) (G2) |
| `backend/src/tiqora/gi/redact.py` | pure: secret masking for headers, config dumps, URLs (G3) |
| `backend/src/tiqora/gi/debugger.py` | `GiDebugger` (Znuny `Debugger.pm` semantics) + `DebugSink` protocol (G3) |
| `backend/src/tiqora/gi/debug_store.py` | writes `tiqora_gi_debug_*`; search/detail over Tiqora + Znuny tables; retention cleanup; GDPR delete (G3) |
| `backend/src/tiqora/gi/conditions.py` | pure: `serialize_config()` (`_SerializeConfig`), `condition_check()` (`_ConditionCheck`) (G4) |
| `backend/src/tiqora/gi/error_handling.py` | pure: `request_retry()` (`ErrorHandling/RequestRetry.pm`), `handle_error()` (`ErrorHandling.pm`) (G5) |
| `backend/src/tiqora/gi/egress.py` | per-webservice egress policy (allowlist, insecure TLS, cert dirs), httpx client factory (G6) |
| `backend/src/tiqora/gi/ws_settings.py` | Tiqora-side per-webservice settings in `tiqora_settings` (G6) |
| `backend/src/tiqora/gi/transport_rest.py` | `HTTP::REST` `RequesterPerformRequest` port (G7) |
| `backend/src/tiqora/gi/soap_envelope.py` | pure: `_SOAPOutputRecursion` port, envelope build, response parse (G8) |
| `backend/src/tiqora/gi/transport_soap.py` | `HTTP::SOAP` `RequesterPerformRequest` port (G8) |
| `backend/src/tiqora/gi/deep_get.py` | `ticket_deep_get()` (`Ticket.pm` `TicketDeepGet`) + `data_structure_remove_elements()`, `base64_deep_encode()` (`Kernel::System::Util`) (G9) |
| `backend/src/tiqora/gi/invoker_generic.py` | `Ticket::Generic` `prepare_request()` / `handle_response()` (G9, G10) |
| `backend/src/tiqora/gi/requester.py` | `run_requester()` (`Requester.pm` `Run` + `_HandleError`) (G10) |
| `backend/src/tiqora/gi/jobs.py` | job handler `core.gi_invoke`, `enqueue_invocation()` (G11) |
| `backend/src/tiqora/gi/events.py` | event data per Znuny event, `match_invokers()` (event → invoker list incl. conditions) (G12) |
| `backend/src/tiqora/gi/follower.py` | `run_gi_requester_tick()` (G12) |
| `backend/src/tiqora/gi/takeover.py` | flag + Znuny-side handler check + Daemons notices (G12) |
| `backend/src/tiqora/api/v1/admin/webservices.py` | admin API (G15) |
| `backend/src/tiqora/api/v1/admin/webservices_schemas.py` | API models (G15) |
| Modify `backend/src/tiqora/security/outbound.py` | `allow_networks` parameter (G6) |
| Modify `backend/src/tiqora/jobs/service.py` | `enqueue_job(..., due_at=None)` (G11) |
| Modify `backend/src/tiqora/jobs/registry.py` | `LANE_SLOTS` gains `gi: 2`, `gi_sync: 1` (G11) |
| Modify `backend/src/tiqora/api/compat/provider.py`, `router.py` | debugger for provider traffic (G13) |
| Modify `backend/src/tiqora/process/engine.py` | `ExecuteInvoker` handler (G14) |
| Modify `backend/src/tiqora/worker/__main__.py`, `worker/services.py`, `domain/settings_store.py`, `config.py`, `api/v1/admin/daemons.py`, `api/v1/admin/schemas.py`, `api/v1/admin/__init__.py`, `extensions/core.py` (job handler registration, if that is where core handlers are registered — follow `gi_mail`), `gdpr/erasure.py` | wiring |
| Modify `backend/src/tiqora/db/tiqora/models.py` + migration | G1 tables |

Frontend (new unless marked): `frontend/src/lib/webservicesApi.ts`, `frontend/src/demo/giMock.ts`, `frontend/src/routes/admin/WebservicesPage.tsx` (+ test), `frontend/src/routes/admin/WebserviceDetailPage.tsx` (+ test; tabs "Übersicht", "Ausgehende Aufrufe", "Protokoll (Debugger)", "Einstellungen"), `frontend/src/routes/admin/gi/DebuggerList.tsx`, `DebuggerEntryDrawer.tsx`, `DryRunDialog.tsx` (+ tests); modify `router.tsx`, `lib/adminSearch.ts`, `routes/admin/DaemonsPage.tsx` (notices), `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` + propagation.

Golden: `tests/golden/gi_sink.py`, `tests/golden/webservices/GoldenRequesterREST.yml`, `tests/golden/webservices/GoldenRequesterSOAP.yml`, `tests/golden/test_gi_requester.py`; modify `tests/golden/docker-compose.golden.yml` (service `gisink`), `tests/golden/_helpers.py`.

### G1. Tables (one migration, Task 1)

Same columns as Znuny's `gi_debugger_entry` / `gi_debugger_entry_content` (`scripts/database/schema.xml` lines 1978–2014) plus three Tiqora columns, so a later switch to Znuny's tables (open question 1) changes only the target table names.

```
tiqora_gi_debug_entry
  id                  BIGINT PK autoincrement
  communication_id    VARCHAR(32)  NOT NULL   UNIQUE uq_tiqora_gi_debug_entry_comm   -- secrets.token_hex(16)
  communication_type  VARCHAR(50)  NOT NULL   -- "Provider" | "Requester"
  remote_ip           VARCHAR(50)  NULL
  webservice_id       INTEGER      NOT NULL   -- gi_webservice_config.id, no FK (Znuny table)
  create_time         DATETIME     NOT NULL   -- utc_now()
  -- Tiqora additions
  name                VARCHAR(200) NULL       -- invoker or operation name
  ticket_id           BIGINT       NULL       -- when known (GDPR delete, filter)
  replay              TEXT/MEDIUMTEXT NULL    -- requester only: JSON {"invoker":…, "data":…, "asynchronous":bool}; no secrets (event data only)
  index ix_tiqora_gi_debug_entry_ws_time (webservice_id, create_time)
  index ix_tiqora_gi_debug_entry_ticket (ticket_id)
  index ix_tiqora_gi_debug_entry_time (create_time)

tiqora_gi_debug_content
  id                  BIGINT PK autoincrement
  entry_id            BIGINT NOT NULL FK -> tiqora_gi_debug_entry.id ON DELETE CASCADE
  debug_level         VARCHAR(50)  NOT NULL   -- debug|info|notice|error
  subject             VARCHAR(255) NOT NULL
  content             LONGBLOB (MariaDB LONGBLOB / PG BYTEA)  NULL   -- UTF-8 text
  create_time         DATETIME     NOT NULL
  index ix_tiqora_gi_debug_content_entry (entry_id, id)
  index ix_tiqora_gi_debug_content_time (create_time)
  index ix_tiqora_gi_debug_content_level (debug_level)
```

ORM classes `TiqoraGiDebugEntry`, `TiqoraGiDebugContent` in `backend/src/tiqora/db/tiqora/models.py` (use the existing `MEDIUM_TEXT_VARIANT` for `replay` and the existing large-binary variant used for job files for `content`; if none exists, `LargeBinary().with_variant(mysql.LONGBLOB(), "mysql")`).

No other DDL: the follower cursor, the per-webservice settings and the retention days live in `tiqora_settings`; jobs in `tiqora_job`.

### G2. Requester config model and worker snapshot

`tiqora/gi/config.py` (pure). Parses `config["Requester"]` and `config["Debugger"]` of an already YAML-decoded + placeholder-replaced webservice config (wave-1 `replace_config_placeholders`). Unknown keys are ignored here and kept in `raw`.

```python
DebugLevel = Literal["debug", "info", "notice", "error"]
DEBUG_LEVELS: Final = {"debug": 1, "info": 2, "notice": 3, "error": 4}

@dataclass(frozen=True, slots=True)
class DebuggerConfig:
    threshold: DebugLevel          # Debugger.DebugThreshold, default "error"; invalid -> "error" + parse warning
    test_mode: bool                # Debugger.TestMode (perl_truthy) -> nothing written

@dataclass(frozen=True, slots=True)
class InvokerEvent:
    event: str                     # "TicketCreate", "TicketDynamicFieldUpdate_Foo", ...
    asynchronous: bool             # perl_truthy(Asynchronous)
    condition: dict[str, Any] | None   # the raw {ConditionLinking, Condition:{...}} hash, None when empty

@dataclass(frozen=True, slots=True)
class InvokerConfig:
    name: str
    type: str                      # "Ticket::Generic", ...
    supported: bool                # type in SUPPORTED_INVOKER_TYPES = {"Ticket::Generic"}
    events: tuple[InvokerEvent, ...]   # Events list; non-dict / empty Event entries skipped (Handler.pm)
    mapping_outbound: Mapper | None
    mapping_inbound: Mapper | None
    mapping_error: str | None      # MappingConfigError text (outbound or inbound)
    get_all_article_attachments: bool
    sort_outbound: Any             # SortOutbound (SOAP Sort override), raw
    operation_name: str | None     # SOAP OperationName override
    raw: dict[str, Any]

@dataclass(frozen=True, slots=True)
class ErrorHandlingModule:
    key: str                       # ErrorHandling hash key
    type: str                      # "RequestRetry" | other (unsupported -> skipped with log, as Znuny "Can't load")
    invoker_filter: tuple[str, ...]
    error_stage_filter: tuple[str, ...]
    error_message_content_filter: str | None
    stop_after_match: str | None   # "all" | "backend" | None
    config: dict[str, Any]         # the module hash (ScheduleRetry, RetryIntervalStart, ...)

@dataclass(frozen=True, slots=True)
class RequesterTransport:
    type: str                      # "HTTP::REST" | "HTTP::SOAP" | other
    config: dict[str, Any]         # Requester.Transport.Config verbatim (secrets inside! never dump it unredacted)

@dataclass(frozen=True, slots=True)
class RequesterConfig:
    transport: RequesterTransport | None
    invokers: dict[str, InvokerConfig]       # sorted by name
    error_handling: tuple[ErrorHandlingModule, ...]   # in ErrorHandlingPriority order; keys missing in ErrorHandling skipped
    debugger: DebuggerConfig
    unsupported_events: tuple[tuple[str, str], ...]   # (invoker, event) whose event is not a Ticket/Article event (open question 13)

SUPPORTED_INVOKER_TYPES: Final = frozenset({"Ticket::Generic"})
def parse_requester(config: dict[str, Any], *, huge_xml: bool) -> RequesterConfig
def parse_debugger(config: dict[str, Any]) -> DebuggerConfig     # also used by the provider (G13)
def is_ticket_event(event: str) -> bool
    # True for every value of HISTORY_EVENT_MAP, "ArticleCreate", "TicketDynamicFieldUpdate_<name>"
```

Mappers are built with wave-1 `build_mapper` exactly as `_parse_operation` does for operations (same `huge_xml`, same error capture into `mapping_error`).

`tiqora/gi/loader.py`:

```python
@dataclass(frozen=True, slots=True)
class GiWebservice:
    id: int
    name: str
    requester: RequesterConfig
    debugger: DebuggerConfig
    config: dict[str, Any]          # full parsed config (secrets inside)

class GiWebservices:
    """Worker-side snapshot of all VALID webservices with a Requester section; swapped atomically."""
    def __init__(self) -> None
    def by_id(self, ws_id: int) -> GiWebservice | None
    def by_name(self, name: str) -> GiWebservice | None
    def all(self) -> tuple[GiWebservice, ...]        # sorted by id (Handler.pm: sort keys %WebserviceList)
    async def refresh(self, session: AsyncSession, sysconfig: SysConfig, *, force: bool = False) -> bool
        # same fingerprint rule as WebserviceRegistry.refresh (load_fingerprint); rows via _load_yaml_rows;
        # replace_config_placeholders; parse_requester; a row that fails parsing is logged
        # ("gi_webservice_invalid", ws_id, name, error) and skipped — the others stay served.

async def load_gi_webservice(session: AsyncSession, sysconfig: SysConfig, ws_id: int) -> GiWebservice | None
    # single valid row (job handler, admin dry run); None when missing/invalid
```

### G3. Debugger, redaction, store

`tiqora/gi/redact.py` (pure):

```python
MASK: Final = "***"
SECRET_KEY_RE: Final = re.compile(r"passw|secret|token|auth|userpw|apikey|api_key|x509keyfile|sslkey", re.I)
def redact_headers(headers: Mapping[str, str], *, secret_values: Iterable[str] = ()) -> dict[str, str]
    # Authorization, Proxy-Authorization, Cookie, Set-Cookie -> "***" (scheme kept: "Basic ***", "Bearer ***");
    # any header whose value contains one of secret_values (len >= 4) -> that substring replaced by "***"
def redact_config(value: Any) -> Any
    # deep copy; dict keys matching SECRET_KEY_RE -> "***" when the value is a non-empty str
def redact_text(text: str, secret_values: Iterable[str]) -> str
    # every occurrence of each secret value (len >= 4) -> "***"; used on error messages and response echoes
def redact_url(url: str) -> str     # userinfo removed; query values of keys matching SECRET_KEY_RE -> "***"
```

`tiqora/gi/debugger.py` — port of `Kernel/GenericInterface/Debugger.pm`:

```python
class DebugSink(Protocol):
    async def add(self, *, communication_id: str, communication_type: str, webservice_id: int,
                  remote_ip: str | None, name: str | None, ticket_id: int | None,
                  debug_level: str, summary: str, content: str) -> None

class GiDebugger:
    def __init__(self, *, sink: DebugSink, config: DebuggerConfig, webservice_id: int,
                 communication_type: Literal["Provider", "Requester"], remote_ip: str | None = None,
                 communication_id: str | None = None, name: str | None = None,
                 secret_values: Iterable[str] = ()) -> None
        # communication_id default secrets.token_hex(16) (Znuny: md5 hex, 32 chars)
    communication_id: str
    ticket_id: int | None                       # settable; written with the next row
    async def log(self, level: DebugLevel, summary: str, data: Any = None) -> None
        # level < threshold or test_mode -> nothing written (error rows still go to structlog, Znuny Log);
        # summary truncated to 255; data: dict/list -> json.dumps(redact_config(data), sort_keys=True, indent=2,
        # ensure_ascii=False, default=str); str -> redact_text(data); None/empty -> "No data provided";
        # content longer than TIQORA_GI_DEBUG_MAX_CONTENT_BYTES (default 200 KiB, Znuny ResponseLoggingMaxSize) ->
        # cut + "\n[… gekürzt, N Bytes]"
    async def debug(self, summary: str, data: Any = None) -> None
    async def info(...); async def notice(...)
    async def error(self, summary: str, data: Any = None) -> dict[str, Any]
        # logs at error and returns {"Success": 0, "ErrorMessage": summary} like Znuny's Error()
```

`tiqora/gi/debug_store.py`:

```python
class DbDebugSink:                         # implements DebugSink
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None
    # each add() runs in its OWN short session and commits (Znuny's LogAdd is outside the business
    # transaction; a rolled-back ticket write must still leave its debugger trail). First add() of a
    # communication_id inserts the entry row (unique key; on IntegrityError re-read), later adds only content.

DebugSource = Literal["tiqora", "znuny"]
@dataclass(frozen=True, slots=True)
class DebugEntryRow: source: DebugSource; id: int; communication_id: str; communication_type: str;
                     remote_ip: str | None; webservice_id: int; create_time: datetime; name: str | None;
                     ticket_id: int | None; max_level: str; can_resend: bool
async def search_entries(session, *, webservice_id: int, communication_type: str | None,
                         created_after: datetime | None, created_before: datetime | None,
                         remote_ip: str | None, source: DebugSource | None, limit: int,
                         before: tuple[datetime, str, int] | None) -> list[DebugEntryRow]
    # two separate queries (NO join/union across tiqora_* and gi_debugger_*): each ORDER BY create_time DESC, id DESC
    # LIMIT limit+1 with the keyset `before` (create_time, source, id); merged in Python; max_level from a
    # second IN-list query per side over the content table (MAX by DEBUG_LEVELS rank)
async def get_entry(session, *, source: DebugSource, entry_id: int, webservice_id: int)
    -> tuple[DebugEntryRow, list[DebugContentRow]] | None   # content ordered by id; Znuny content bytes decoded utf-8 (errors="replace")
async def purge_debug_entries(session, *, older_than: datetime) -> int     # Tiqora tables only, batches of 1000 ids
async def delete_debug_entries_for_tickets(session, ticket_ids: Sequence[int]) -> int
```

`can_resend` = source `tiqora` and type `Requester` and `replay` not null. Znuny rows never get "Erneut senden" (their input is Data::Dumper text).

Retention: `run_gi_debug_cleanup()` daily at 03:02 UTC (`DaemonService` slug `gi_debug_cleanup`, `schedule_kind="daily"`, always on, not toggleable), deletes Tiqora rows older than `gi.debugger.retention_days` (`tiqora_settings`, default 14, min 1). GDPR: `gdpr/erasure.py` deletes `tiqora_gi_debug_entry` rows by `ticket_id` (content cascades) wherever ticket rows are hard-deleted — add to `_TICKET_DELETE_ORDER` if its `(table, fk_col, scope)` shape fits, else call `delete_debug_entries_for_tickets` next to it.

### G4. Event conditions (`tiqora/gi/conditions.py`) — port of `Event/Handler.pm` `_SerializeConfig` + `_ConditionCheck`

```python
def serialize_config(data: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]
    # _SerializeConfig exactly: hash keys sorted; falsy keys/items skipped (Perl `next if !$ConfigItem`:
    # "", "0", 0, None) — a skipped array item does NOT advance the array counter;
    # nested non-empty dict/list -> recurse with prefix "<key>_"; scalar or EMPTY dict/list -> "<prefix><key>": value;
    # array scalars -> "<prefix><index>": item.
    # Perl quirk: a dict/list INSIDE an array gets prefix "HASH(0x…)_" (stringified ref, unmatchable). Tiqora
    # skips such nested containers entirely (their keys could never match in Znuny either) — pinned by a test.
def condition_check(condition: Mapping[str, Any], data: Mapping[str, Any]) -> bool
    # condition = InvokerEvent.condition = {"ConditionLinking": "and|or|xor", "Condition": {name: {"Type": ..., "Fields": {...}}}}
    # exact port incl. early returns; field shorthand: scalar -> {Type: String, Match: v}; list -> {Type: Array, Match: v}
    # String: Match must be a non-empty string or "0" else config error (-> False + warning);
    #   data value scalar and equal -> match; data value list -> any equal; else "<Field>_<n>" serialized
    #   array keys (fullmatch \A<Field>_\d+\z) with equal scalar -> match
    # Array: every Match item present in data[field] (list) -> match
    # Hash: data[field] dict and all Match keys equal -> match (non-dict -> FieldFail but NOT the and/and early return — Znuny quirk, pinned)
    # Regexp: compile_perl_regex(Match, verbose=False) (Perl qr{$Match} without /x); scalar re.search /
    #   list any / serialized array keys; compile error -> False + warning
    # Module: always non-match + warning "Bedingung vom Typ Module wird in Tiqora nicht ausgewertet"
    #   (precedent tiqora/process/conditions.py)
    # Field names starting "jq#": non-match + warning (Tiqora has no Jq; Znuny without Jq behaves the same)
    # invalid ConditionLinking / Type / empty Fields -> False + warning (Znuny returns undef = no fire)
@dataclass(frozen=True, slots=True)
class ConditionWarning: invoker: str; event: str; message: str
```

Condition data for `Ticket::Generic` (Znuny `ObjectType/Ticket.pm` / `Article.pm` with `InvokerType eq 'Ticket::Generic'` and no Jq → `_SerializeConfig(TicketDeepGet)`): `serialize_config(await ticket_deep_get(session, ticket_id, article_id=…, all_attachments=False))`. For any other invoker type the invoker is unsupported anyway (G2) and never fires.

### G5. Error handling (`tiqora/gi/error_handling.py`) — ports of `ErrorHandling.pm` + `ErrorHandling/RequestRetry.pm`

```python
@dataclass(frozen=True, slots=True)
class PastExecutionData:
    initial_request_at: datetime        # naive UTC ("InitialRequestDateTime")
    retry_count: int                    # "RetryCount"
    retry_interval: int | None          # "RetryInterval" (seconds)
    retry_at: datetime | None           # "RetryDateTime"
    def to_json(self) -> dict[str, Any]; @classmethod from_json(cls, raw: Any) -> PastExecutionData | None

@dataclass(frozen=True, slots=True)
class RetryDecision:
    reschedule: bool
    execution_time: datetime | None
    past: PastExecutionData | None
    data: dict[str, Any]                # Znuny Data: ReSchedule, InitialRequestDateTime, CurrentRequestDateTime,
                                        # CurrentRetryCount, MaximumRetryCountReached, MaximumRetryPeriodReached
class ErrorHandlingConfigError(Exception): ...   # _ModuleConfigCheck messages verbatim

def request_retry(module_config: Mapping[str, Any], past: PastExecutionData | None, *, now: datetime) -> RetryDecision
    # exact RequestRetry::Run: config check (ScheduleRetry '0'|'1'; RetryIntervalFactor '1'|'1.5'|'2';
    # RetryIntervalStart '0' or positive int; RetryIntervalMax ''/'0'/positive; RetryCountMax, RetryPeriodMax ''/positive);
    # RetryCount = past.retry_count or 0; current = past.retry_at or now; initial = past.initial_request_at or now;
    # not ScheduleRetry -> no reschedule; RetryCountMax and count >= max -> MaximumRetryCountReached;
    # RetryPeriodMax and (current - initial) >= max -> MaximumRetryPeriodReached;
    # interval = int(past.retry_interval * factor) capped at RetryIntervalMax, else RetryIntervalStart;
    # target = initial + RetryPeriodMax when (current - initial) + interval >= RetryPeriodMax, else current + interval;
    # target <= now -> now + 1 s; past' = (initial, count + 1, interval, target)

@dataclass(frozen=True, slots=True)
class HandleErrorResult:
    error_message: str
    reschedule: RetryDecision | None
    module_data: list[dict[str, Any]]   # Znuny ReturnData (ModuleConfig, ModuleData, ModuleName) for the invoker HandleError mapping

async def handle_error(*, modules: Sequence[ErrorHandlingModule], invoker: str, error_stage: str,
                       summary: str, data: Any, past: PastExecutionData | None,
                       debugger: GiDebugger, now: datetime) -> HandleErrorResult
    # ErrorHandling::HandleError: debugger.error(summary, data); no modules -> {Success: 1} equivalent
    # (error_message = summary, no reschedule); loop in priority order with StopAfterMatch (all|backend),
    # InvokerFilter, ErrorStageFilter, ErrorMessageContentFilter (Perl qr{} via compile_perl_regex(verbose=False),
    # searched in summary and in json.dumps(data, sort_keys=True) when data is not a string);
    # unsupported Type -> log + skip; module config error -> _LogAndReturn (error_message = that message, no reschedule);
    # reschedule set -> debugger.info("Got reschedule decision …") and debugger.notice("Request will be retried again at:", time)
ERROR_STAGES: Final = ("RequesterRequestPrepare", "RequesterRequestMap", "RequesterRequestPerform",
                       "RequesterResponseMap", "RequesterResponseProcess")
```

### G6. Egress policy, Tiqora-side webservice settings

`security/outbound.py` gains (existing callers unchanged):

```python
def pin_outbound_url(url: str, *, require_https: bool = False, resolver: HostResolver | None = None,
                     allow_private_networks: bool = False,
                     allow_networks: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network] = ()) -> PinnedOutboundURL
    # an address that is_blocked_ip(...) rejects is still allowed when it lies in one of allow_networks AND is
    # private/ULA/CGNAT (100.64.0.0/10) — never when loopback, link-local, multicast, unspecified or reserved.
```

`tiqora/gi/ws_settings.py` (keys in `tiqora_settings`, JSON value):

```python
KEY_PREFIX: Final = "gi.webservice."                 # gi.webservice.<id>.egress
@dataclass(frozen=True, slots=True)
class WebserviceEgressSettings:
    allowed_targets: tuple[str, ...]                  # hostnames (exact, case-insensitive) and CIDRs
    allow_insecure_tls: bool
async def get_egress_settings(session, ws_id: int) -> WebserviceEgressSettings     # default: (), False
async def set_egress_settings(session, ws_id: int, value: WebserviceEgressSettings) -> None
def validate_targets(raw: Sequence[str]) -> tuple[str, ...]
    # each entry: CIDR (ipaddress.ip_network(strict=False)) or hostname (IDNA, no scheme/port/path, no wildcard);
    # rejects loopback/link-local/multicast/unspecified/reserved networks; max 50 entries
```

A hostname entry allows the private addresses that this hostname resolves to (resolution still pinned); a CIDR entry allows private addresses inside it for any hostname.

`tiqora/gi/egress.py`:

```python
class EgressError(Exception): ...        # German plain-words message for the debugger, no secrets
@dataclass(frozen=True, slots=True)
class EgressPolicy:
    allow_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    allow_hosts: frozenset[str]
    allow_insecure_tls: bool
    cert_dirs: tuple[Path, ...]          # settings.gi_cert_dirs (resolved, absolute)
    max_timeout: float
    max_response_bytes: int
async def load_policy(session, ws_id: int, settings: Settings) -> EgressPolicy
def pin(url: str, policy: EgressPolicy, *, resolver: HostResolver | None = None) -> PinnedOutboundURL
    # host in allow_hosts -> pin_outbound_url(..., allow_private_networks=True) but still never loopback/link-local etc.;
    # else pin_outbound_url(..., allow_networks=policy.allow_networks); OutboundURLError -> EgressError
    # ("Ziel {host} ist nicht erlaubt: {reason}. Interne Ziele können unter Webservices → Einstellungen freigegeben werden.")
def resolve_cert_path(raw: str, policy: EgressPolicy) -> Path
    # Path(raw).resolve(strict=True) must be inside one of cert_dirs (is_relative_to after resolve — symlinks out
    # are rejected), a regular file (or dir for SSLCADir), <= 1 MiB; else EgressError (message names the setting,
    # never the file content)
def build_ssl_context(*, policy: EgressPolicy, verify_hostname: bool, cert_file: str | None, key_file: str | None,
                      key_password: str | None, ca_file: str | None, ca_dir: str | None) -> ssl.SSLContext
    # verify_hostname False (SSLNoHostnameVerification) only when policy.allow_insecure_tls, else EgressError
def proxy_for(transport_config: Mapping[str, Any], policy: EgressPolicy) -> tuple[str | None, dict[str, str]]
    # Proxy.UseProxy == "Yes": ProxyExclude == "Yes" -> (None, {}); ProxyHost set -> proxy URL validated with pin()
    # (the PROXY host must pass the guard; the target is then NOT resolved locally — documented),
    # Proxy-Authorization Basic header from ProxyUser/ProxyPassword (both non-empty); else (None, {})
def client(*, policy: EgressPolicy, ssl_context: ssl.SSLContext | None, proxy: str | None,
           transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient
    # follow_redirects=False, trust_env=False (no env proxies), verify=ssl_context or True; transport injectable for tests
async def read_capped(response: httpx.Response, limit: int) -> bytes
    # streams; > limit -> EgressError("Antwort größer als … MiB")
```

New settings (`config.py`): `gi_cert_dirs: list[str]` (`TIQORA_GI_CERT_DIRS`, colon-separated, default empty), `gi_max_timeout_seconds: int = 300` (`TIQORA_GI_MAX_TIMEOUT_SECONDS`, ge=1, le=900), `gi_max_response_bytes: int = 20 MiB` (`TIQORA_GI_MAX_RESPONSE_BYTES`), `gi_debug_max_content_bytes: int = 200 KiB`, `gi_requester_interval_seconds: int = 5` (`TIQORA_GI_REQUESTER_INTERVAL`), `gi_requester_gap_hold_seconds: int = 30`.

### G7. REST transport (`tiqora/gi/transport_rest.py`) — port of `Transport/HTTP/REST.pm` `RequesterPerformRequest` (lines 505–1290)

```python
@dataclass(frozen=True, slots=True)
class TransportResult:
    success: bool
    error_message: str | None
    data: Any                      # decoded response (dict/list/None, or {"Body": text} for text/*)
    size_exceeded: bool
    status_code: int | None
    secret_values: tuple[str, ...] # values the caller must mask in later debugger rows

async def rest_perform_request(*, transport_config: Mapping[str, Any], operation: str, data: Any,
                               debugger: GiDebugger, policy: EgressPolicy, session: AsyncSession,
                               http_transport: httpx.AsyncBaseTransport | None = None,
                               resolver: HostResolver | None = None) -> TransportResult
def flatten_data_structure(data: Any) -> dict[str, Any]      # _FlattenDataStructure ("." separator; same falsy-skip and
                                                             # array-container quirk as G4 — containers in arrays skipped)
def interpolate_controller(controller: str, data: dict[str, Any]) -> tuple[str, set[str]]
    # returns (uri, top-level keys to delete); query part handled like Znuny (":Name" followed by & or end)
def build_query(data: Mapping[str, Any]) -> str              # REST::Client buildQuery: sorted? -> verify in REST/Client.pm; pin by test
```

Rules (verify each in source; test each):
- Required `Host`, `DefaultCommand`, `Timeout` (non-empty strings) else `"REST Transport: Have no <X> in config"`; `data` not dict/None → `"REST Transport: Invalid Data"`.
- Command: `InvokerControllerMapping.<op>.Command` or `DefaultCommand`, upper-cased, must be one of `GET POST PUT PATCH DELETE HEAD OPTIONS CONNECT TRACE`; `CONNECT`/`TRACE` → `EgressError` (Tiqora refuses them).
- Controller required (`"REST Transport: Have no Invoker <-> Controller mapping for Invoker '<op>'."`). URL = `Host` (trailing `/` kept as Znuny concatenates) + controller after interpolation; `pin(url, policy)`.
- Headers start `{"Content-Type": "application/json; charset=UTF-8"}`; for POST/PUT/PATCH `ContentType` `JSON|FORM|XML` → `application/json` / `application/x-www-form-urlencoded` / `application/xml`; `__RequestHeaders` (replace) and `__RequestHeadersAppend` (merge) popped from data.
- Auth: `BasicAuth` with non-empty user + password → `Authorization: Basic …`; `OAuth2Token` + `OAuth2TokenConfigID` → `get_access_token(session, config_id=…)`; failure → `"OAuth2 token for config with ID <n> could not be retrieved."`; the token is substituted only into `AdditionalHeaders` values containing `<OTRS_OAUTH2_TOKEN>` (Znuny does not add a bearer header itself); `JWT` → `EgressError` (open question 9). Every remaining `<OTRS_…>` placeholder in `AdditionalHeaders` is removed. `AdditionalHeaders` merge last.
- Client certs: `X509.UseX509 == "Yes"` → `X509CertFile`/`X509KeyFile`/`X509CAFile`; `SSL.UseSSL == "Yes"` → `SSLCertificate`/`SSLKey`/`SSLPassword`/`SSLCAFile`/`SSLCADir` — all through `resolve_cert_path` + `build_ssl_context`. `SSLNoHostnameVerification` → `verify_hostname=False` (policy-gated).
- Proxy via `proxy_for`. **Pinning rule:** without a proxy the target URL goes through `pin()` (connect to the pinned IP, `Host` header + SNI from `PinnedOutboundURL`). With `UseProxy=Yes` + `ProxyHost`, **only the proxy URL** goes through `pin()`; the target URL is sent unmodified to the proxy (the proxy resolves it; pinning the target to an IP would make the proxy `CONNECT` to a literal IP and break TLS name checks), so the SSRF guard for the target is the proxy's own policy — documented in `compatibility.md`. Never call `pin()` on both. The same rule applies to SOAP (G8). `ReuseClient`/`KeepAlive` ignored (one client per request; documented).
- Body: POST/PUT/PATCH with remaining data → FORM `build_query`, XML `<?xml …?>` + XMLout with `NoAttr=0, RootName=undef` (port; wave-1 `xml_out` has `NoAttr=1` — add a `no_attr`/`root_name=None` option there rather than a second converter), else JSON (`json.dumps(sort_keys=False, ensure_ascii=False)` — Znuny `JSON->Encode` without SortKeys; verify key order expectation in golden); other commands → data appended as query string.
- Debugger rows (same summaries as Znuny where it has them): "URI after interpolating URI params from outgoing data", "Remaining outgoing data to be sent", "Request params from outgoing data" (Tiqora: `{"method", "url": redact_url(url), "headers": redact_headers(...), "body_bytes": n}` — **never** the raw header list, deviation 4), "JSON data received from remote system" (or "… was too large for logging" above `GenericInterface::Operation::ResponseLoggingMaxSize` KiB, default 200).
- Response: status not `20\d` → error `"Error while performing REST '<CMD>' request to Controller '<controller>' on Host '<host>'. Response code '<n>'."` (+ `" No content provided."` when the body is empty and status != 204); 3xx → same message + debugger row with the `Location`; text/plain|html|xml → `{"Body": text}`; 204 → `None`; else `znuny_json_decode` (wave 1: JSON true/false → 1/0) and decode failure → `"… Error while parsing JSON data."`.
- Network errors / timeouts → `"Error while performing REST '<CMD>' request to Controller '<controller>' on Host '<host>'."` + debugger error with the exception class and `redact_text(str(exc))`.

### G8. SOAP transport (`soap_envelope.py`, `transport_soap.py`) — port of `Transport/HTTP/SOAP.pm` `RequesterPerformRequest` (570–1106) + `_SOAPOutputRecursion`/`_SOAPOutputHashRecursion`/`_SOAPOutputProcessString`/`_SOAPOutputTypesGet` (1345–1696)

```python
def soap_output(data: Mapping[str, Any], *, sort: Any) -> list[etree._Element]
    # _SOAPOutputRecursion: element order from Sort (list of single-key hashes, recursive) else sorted keys;
    # strings escaped as _SOAPOutputProcessString; lists -> repeated elements; verify with Znuny Transport tests
def build_request_envelope(*, operation_request: str, namespace: str | None, no_namespace: bool,
                           fixed_prefix: str | None, body: list[etree._Element], soap_version: str = "1.1") -> bytes
    # SOAP::Lite default envelope (soap:Envelope with xsi/xsd/soapenc/soap namespaces as SOAP::Lite 1.x writes them;
    # verify against a golden capture) — method element named operation_request with xmlns = NameSpace
def soap_action(config: Mapping[str, Any], *, operation: str, operation_name: str) -> str
    # SOAPAction absent -> "Yes" + NameSpaceSeparatorOperation + "#"; "No" -> ""; FreeText/Operation/
    # SeparatorOperation/NameSpaceSeparatorOperation exactly; header value wrapped in double quotes
def request_name(config, operation_name) -> str      # RequestNameScheme Plain|Request|Append(+FreeText)
def response_name(config, operation_name) -> str     # ResponseNameScheme Response|Append|Replace, ResponseTagSuffix
def parse_response(xml: bytes, *, response_name: str, encoding: str | None) -> tuple[Any, str | None]
    # XXE-safe parser (resolve_entities=False, no_network=True, load_dtd=False — reuse compat/soap.py parser flags);
    # Fault -> error "k: v, k: v" sorted by key; body element named response_name -> data via the same
    # element->value rules the provider uses (compat/soap.py _element_to_value) — verify against SOAP::Deserializer
    # behaviour with the golden capture; missing -> "SOAP Transport: Response name not found" (verify exact text in source)
async def soap_perform_request(*, transport_config, operation, operation_name, data, sort, debugger, policy,
                               http_transport=None, resolver=None) -> TransportResult
    # Endpoint/NameSpace/Timeout required (NameSpace optional with NoNameSpace); BasicAuth; NTLM -> EgressError;
    # Encoding (non-UTF-8 response conversion); SSL/Proxy via egress with the G7 pinning rule (proxy -> only the
    # proxy URL is pinned, Endpoint sent unmodified); POST text/xml; charset=utf-8 (SOAP 1.1)
```

### G9. `Ticket::Generic` PrepareRequest and event data (`deep_get.py`, `invoker_generic.py`, `events.py`)

```python
DEEP_GET_CLEANUP_RE: Final = re.compile(r"::|config|secret|passw|userpw|auth|token", re.I)   # _TicketDeepGetDataCleanUp
async def ticket_deep_get(session: AsyncSession, ticket_id: int, *, article_id: int | None,
                          all_attachments: bool, sysconfig: SysConfig) -> dict[str, Any] | None
    # Ticket.pm TicketDeepGet (lines 1525–1830): TicketGet(DynamicFields=1, Extended=1) via wave-1 build_ticket_entry
    # (+ Znuny's DynamicField_<Name> flat keys: TicketGet with DynamicFields=1 returns "DynamicField_X" keys, not the
    # GI list shape — verify and add a flag to build_ticket_entry if needed), TimeUnit;
    # Articles = every article (ArticleGet DynamicFields=1 RealNames=1; with all_attachments: Attachment list via
    # attachment_index(ExcludePlainText=0, ExcludeHTMLBody=0, ExcludeInline=0), base64, FileID), each cleaned;
    # CustomerUser, CustomerCompany, QueueData, TypeData, PriorityData, ServiceData, SLAData, OwnerData,
    # ResponsibleData, CreateByData — each the Znuny *Get hash, cleaned (keys matching DEEP_GET_CLEANUP_RE removed),
    # {} when the id is empty; Article = {} unless exactly one article or article_id matches exactly one, then that
    # article + its attachments; UntilTimeDateTimeParts added by prepare_request when UntilTime.
    # User/customer hashes: port the key set of GetUserData / CustomerUserDataGet / CustomerCompanyGet / QueueGet /
    # TypeGet / PriorityGet / ServiceGet / SLAGet from source; preferences included as Znuny does (verify), then cleaned.
    # Every query loads keys then IN-lists; no cross-schema joins.
def data_structure_remove_elements(data: Any, hash_keys: Sequence[str]) -> None   # Kernel::System::Util (verify)
def base64_deep_encode(data: Any, hash_keys: Sequence[str]) -> None               # Kernel::System::Util (verify)

async def prepare_request(session, *, invoker_name: str, invoker: InvokerConfig, data: dict[str, Any],
                          sysconfig: SysConfig) -> dict[str, Any]
    # returns {"Ticket": ticket_or_{}, "Event": data} after OmittedFields / Base64EncodedFields
    # (SysConfig "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::OmittedFields" / "::Base64EncodedFields",
    # hash keyed by invoker name, value split on \s*;\s*) applied to BOTH Ticket and Event
```

Event data (`events.py`), built from a `TicketEvent` (G12): `{"TicketID": ticket_id}` for every ticket event; `ArticleCreate` and `TicketAccountTime` add `ArticleID`; `TicketDynamicFieldUpdate_<Name>` adds `FieldName` (from the history name) and, when the history name carries them, `Value`/`OldValue` (verify the `TicketDynamicFieldUpdate` history name format `%%FieldName%%X%%Value%%Y%%OldValue%%Z` in `DynamicField/ObjectType/Ticket.pm`); `TicketMerge` adds `MainTicketID` (from the history name); `UserID` = `create_by` of the history row when Znuny passes it for that event (verify per event; only `ArticleFlagSet` etc. pass it — the ticket events above do not). The requester adds `"Event": <name>` (Handler.pm). `OldTicketData` is never present (open question 11).

### G10. Requester pipeline (`tiqora/gi/requester.py`) — port of `Requester.pm` `Run` + `_HandleError`

```python
@dataclass(frozen=True, slots=True)
class RequesterOutcome:
    success: bool
    error_message: str | None
    data: Any
    reschedule: RetryDecision | None
    communication_id: str

async def run_requester(*, session_factory: async_sessionmaker[AsyncSession], ws: GiWebservice, invoker: str,
                        data: dict[str, Any], asynchronous: bool, past: PastExecutionData | None,
                        sysconfig_factory: Callable[[AsyncSession], SysConfig], sink: DebugSink,
                        settings: Settings, before_send: Callable[[], Awaitable[None]] | None = None,
                        dry_run: bool = False,
                        http_transport: httpx.AsyncBaseTransport | None = None,
                        resolver: HostResolver | None = None) -> RequesterOutcome
```

Steps (Znuny order and summaries; every failure goes through `_handle_error` with its `ErrorStage`):
1. Debugger (`communication_type="Requester"`, `name=invoker`, `ticket_id=data.get("TicketID")`); `debug("Communication sequence started", data)`; `debug(f"Using invoker '{invoker}'")`.
2. Invoker unknown or `not supported` or `mapping_error` → `debugger.error(...)` and return failure (Znuny: "InvokerObject could not be initialized" — no error handling).
3. `prepare_request` (in a session; `StopCommunication` is not produced by `Ticket::Generic`). Failure → stage `RequesterRequestPrepare`.
4. `debug("Outgoing data before mapping", out)`; outbound mapper with `DataInclude={"RequesterRequestInput": data, "RequesterRequestPrepareOutput": out}` via `run_mapper`; failure → `RequesterRequestMap`; `debug("Outgoing data after mapping", out)`.
5. `dry_run` → return success with `data={"request": <what would be sent: method, redacted url, redacted headers, body>}` built by the transport's request builder (transports expose `build_*_request(...)` used by `*_perform_request` and by the dry run — no network).
6. `before_send()` (the job's `sending` checkpoint) then transport (`rest_perform_request` / `soap_perform_request`; other transport types → error "Transport '<type>' wird von Tiqora nicht unterstützt"); failure → `RequesterRequestPerform` **and** `handle_response(success=False, error=…)` (only its non-Success/ErrorMessage keys are merged when `asynchronous`, Znuny quirk; for `Ticket::Generic` it still executes response actions with the error data — verify: `HandleResponse` is called with `ResponseSuccess => 0` and no `Data`, so no actions run).
7. `DataInclude["RequesterResponseInput"]`; debug in/size-exceeded rows; inbound mapper → `RequesterResponseMap`.
8. `handle_response(success=True, data=…)` (G10b) → failure → `RequesterResponseProcess`.
9. Return success with data.

`_handle_error`: `handle_error(...)` (G5); then, because `Ticket::Generic` can `HandleError`, error data (string or deep copy) → `debug("Error data before mapping")` → inbound mapper with `{"Fault": data}` and DataInclude + `RequesterErrorHandlingOutput` → `debug("Error data after mapping")` → `handle_response(success=False, data=mapped)` (Generic `HandleError`) — failure only logged.

**G10b. `handle_response`** (`invoker_generic.py`, port of `Generic.pm` `HandleResponse`): data not a non-empty dict → success unchanged. Keys in sorted order, all as UserID 1 through existing write services (each call `flush` only; the handler commits once at the end; any failure → debugger error `"Error on response action '<Key>' with value '<v>'. Failed to execute!"`, rollback of that response's writes, return failure):

| Key | Tiqora call |
|---|---|
| `OTRS_TicketDynamicFieldSet_<Name>` (case-insensitive prefix) | `update_dynamic_field(session, ticket_id, field_name=Name, values=[v] or v if list, user_id=1)`; unknown field → skipped (Znuny `next`) |
| `OTRS_TicketTitleUpdate` | `change_title` |
| `OTRS_TicketServiceSet` / `TypeSet` / `QueueSet` / `SLASet` / `StateSet` / `PrioritySet` | `change_service` / `change_type` / `move_queue` / `change_sla` / `change_state` / `change_priority` — names resolved to ids like the write services expect (by name, as Znuny `*Set` accept names) |
| `OTRS_TicketCustomerSet` (`"No;User"`) | `set_customer(customer_id=No, customer_user=User)` |
| `OTRS_TicketOwnerSet` / `ResponsibleSet` (login) | `assign_owner` / `assign_responsible` |
| `OTRS_TicketHistoryAdd` (`{HistoryType, Name|HistoryComment}`) | `history_add(session, ticket_id, history_type or "AddNote", name or comment or " ", create_by=1)` |
| `OTRS_TicketArticleAdd` (`Subject`, `Body` required; `Attachment` dict or list, base64) | `add_article(ArticleIn(channel="Internal", is_visible_for_customer=False, sender_type="agent", charset utf-8, mime text/plain, from "Generic Interface", **mapped keys))` — only keys `ArticleIn` knows; unknown keys → debugger notice and ignored |
| `OTRS_TicketCreate` | `create_ticket(TicketIn(...))` for the keys `TicketIn` supports (Title, Queue/QueueID, State/StateID, Priority/PriorityID, Lock, Type, Service, SLA, CustomerID, CustomerUser, OwnerID/Owner, ResponsibleID); anything else → error, nothing created |
| `OTRS_AsynchronousInvokerExecution` (list of `{WebserviceID, Invoker, Data}`) | `enqueue_invocation(...)` per item (G11), `asynchronous=True`; invalid shape → Znuny error text |
| `OTRS_TicketArticleCreateEvent` | debugger error "wird von Tiqora nicht unterstützt" (open question 12) |

Then `ResponseSuccess=0` → `{Success 0, ErrorMessage}` (missing message → `"Got response error, but no response error message!"`).

### G11. Job handler and lanes (`tiqora/gi/jobs.py`)

```python
GI_INVOKE_KIND: Final = "core.gi_invoke"
GI_LANE: Final = "gi"            # async invokers, 2 slots
GI_SYNC_LANE: Final = "gi_sync"  # Asynchronous: 0 invokers, 1 slot, strict id order
CHECKPOINT_SENDING: Final = "sending"

class GiInvokePayload(BaseModel):
    webservice_id: int
    invoker: str
    data: dict[str, Any]                   # event data incl. "Event", or ExecuteInvoker data — never secrets
    asynchronous: bool
    origin: Literal["event", "transition_action", "response_action", "resend", "dry_run"]
    history_id: int | None = None          # source ticket_history.id (idempotency key, debugger)
    past_execution: dict[str, Any] | None = None   # PastExecutionData.to_json()
    dry_run: bool = False

async def enqueue_invocation(session: AsyncSession, *, payload: GiInvokePayload, created_by: int | None,
                             due_at: datetime | None = None) -> TiqoraJob
    # kind = GI_INVOKE_KIND but lane chosen by payload: two registered handlers share run code —
    # "core.gi_invoke" (lane gi) and "core.gi_invoke_sync" (lane gi_sync); dedupe_key
    # f"gi:{history_id}:{webservice_id}:{invoker}" ONLY when origin == "event" and past_execution is None
    # (protects a re-enqueue within the same active window; the real exactly-once comes from the cursor
    # transaction, G12). Reschedules (past_execution set), resends, dry runs, response actions and
    # transition actions pass dedupe_key=None — enqueue_job dedupes against queued/RUNNING jobs, and the
    # reschedule is enqueued while the original job is still running, so a shared key would silently
    # return the running job and no retry row would ever exist.
```

Handlers (`JobHandler`): `kind="core.gi_invoke"` lane `gi`, and `kind="core.gi_invoke_sync"` lane `gi_sync`; `title_key="jobs.kinds.core.gi_invoke"`; `timeout_seconds = settings.gi_max_timeout_seconds + 120`; `max_attempts=3`; `requeue_if_interrupted=True` (an interruption **before** the checkpoint requeues; after it, the job fails "Der Aufruf wurde unterbrochen; ob er angekommen ist, ist unklar." and is never resent automatically); `result_private=True` (dry-run results may contain ticket data).

Run: load webservice (`load_gi_webservice`; missing/invalid → `JobFailed("Der Webservice ist nicht mehr gültig.")`), `run_requester(..., before_send=lambda: ctx.checkpoint(CHECKPOINT_SENDING))`; success → result `{"communication_id", "status": "ok"}` (dry run: `{"communication_id", "request": …}`); failure with `reschedule` → `enqueue_invocation(payload with past_execution=decision.past.to_json(), due_at=decision.execution_time)` in a fresh session + commit, then the job **succeeds** with result `{"status": "rescheduled", "next_at": …}` (it did its work: decided to retry — like Znuny's task ending after `TaskAdd`); failure without reschedule → `JobFailed(redacted error_message)`. Znuny's `FutureTaskTimeDiff` fallback (300 s, SysConfig `Daemon::SchedulerGenericInterfaceTaskManager::FutureTaskTimeDiff`) applies when the decision has no execution time.

`jobs/service.py`: `enqueue_job(..., due_at: datetime | None = None)` sets `TiqoraJob.due_at` (the runner already honours it). `jobs/registry.py`: `LANE_SLOTS = {MAIL_LANE: 1, "gi": 2, "gi_sync": 1}`.

### G12. Event follower, takeover flag, notices (`follower.py`, `events.py`, `takeover.py`)

Settings keys (`domain/settings_store.py`): `KEY_GI_REQUESTER_ENABLED = "daemon.gi_requester.enabled"` (default OFF), `KEY_GI_REQUESTER_INTERVAL_SECONDS = "daemon.gi_requester.interval_seconds"`, `KEY_GI_REQUESTER_CURSOR = "daemon.gi_requester.history_cursor"`, `KEY_GI_REQUESTER_LAST_ENABLED_TICK = "daemon.gi_requester.last_enabled_tick"`, `KEY_GI_DEBUG_RETENTION_DAYS = "gi.debugger.retention_days"`.

`DaemonService(slug="gi_requester", enabled_key=KEY_GI_REQUESTER_ENABLED, default_enabled=False, toggleable=True, schedule_kind="interval", interval_key=KEY_GI_REQUESTER_INTERVAL_SECONDS, interval_settings_attr="gi_requester_interval_seconds", tick_timeout_seconds=60)` and `DaemonService(slug="gi_debug_cleanup", enabled_key=None, default_enabled=True, toggleable=False, schedule_kind="daily", daily_at="03:02")`. Worker: `_interval_loop("gi_requester", gi_requester_tick, …)` and the daily loop helper used by the GDPR jobs for cleanup.

```python
async def run_gi_requester_tick(*, settings: Settings, session_factory, webservices: GiWebservices,
                                tracker: GapTracker | None = None, now: float | None = None) -> dict[str, int]
```

Tick (`_interval_loop` calls the tick every interval regardless of the flag; the tick reads the flag itself):
1. Flag off → `set cursor = MAX(ticket_history.id)` (commit), delete `daemon.gi_requester.last_enabled_tick`, return `{"enabled": 0}` — no replay ever (Review Focus 3). Cursor NULL on the first enabled tick → same (start at head). Belt and braces, because a worker may be down while the flag is off or the flag may flip off→on between two ticks: (a) `api/v1/admin/daemons.py` `update_daemon` sets the cursor to `MAX(ticket_history.id)` in the same transaction whenever `gi_requester` changes from disabled to enabled; (b) every enabled tick writes `daemon.gi_requester.last_enabled_tick` (utc iso), and an enabled tick that finds this marker missing or older than 3 × the effective interval first resets the cursor to head (logged `gi_requester_cursor_reset`).
2. `webservices.refresh(...)` (fingerprint; cheap). None with invokers having events → advance cursor to the gap-safe horizon, return.
3. Read up to 500 rows `id > cursor` (same SQL as `tiqora.plugins.event_dispatch._HISTORY_SQL`; copy the constant, do not import a private name), `horizon = tracker.horizon(cursor, ids, now)` (process-level `GapTracker(hold_seconds=settings.gi_requester_gap_hold_seconds, step, offset)` from `autoincrement_step`), `events = events_from_history(rows <= horizon)`.
4. For each event in id order, for each webservice (`all()` sorted by id), for each invoker (sorted by name) with an `InvokerEvent.event == event.event` (exact, Handler.pm) and the invoker `supported` and no `mapping_error`: if `condition` → `condition_check(condition, serialize_config(deep_get))` (deep_get loaded once per (ticket, article) per tick; conditions evaluated with the ticket's state **at tick time** — documented: Znuny evaluates at event time); warnings logged once per tick per (invoker, message). Match → collect `GiInvokePayload(origin="event", history_id=event.id, data={**event_data, "Event": name}, asynchronous=…)`.
5. In **one** session: `enqueue_invocation` for all collected payloads (async → `core.gi_invoke`, sync → `core.gi_invoke_sync`), `set_setting(cursor, horizon)`, `commit()`. A failure rolls both back (next tick retries the same rows — enqueue is exactly-once because cursor and jobs commit together).
6. Return counts `{"events": n, "enqueued": m, "cursor": horizon}` (status via `record_tick_status` in `_interval_loop`).

Tick budget: at most 500 rows and 30 s wall time (stop early, cursor = last fully processed event id; never past the horizon).

`takeover.py`:

```python
ZNUNY_GI_EVENT_HANDLER: Final = "Ticket::EventModulePost###9900-GenericInterface"
async def znuny_gi_handler_active(sysconfig: SysConfig) -> bool     # await sysconfig.get(name) is not None (valid row)
async def webservices_with_event_invokers(session, sysconfig) -> list[tuple[int, str]]
async def daemon_notices(session, sysconfig) -> list[str]
    # i18n keys for the Daemons page row "gi_requester":
    #  - flag off and webservices_with_event_invokers non-empty -> "admin.daemons.notices.giRequesterOff"
    #    ("Webservices mit ausgehenden Aufrufen sind eingerichtet, aber Tiqora löst sie nicht aus. Änderungen, die in
    #     Tiqora gemacht werden, erreichen diese Systeme nicht.")
    #  - flag on and znuny_gi_handler_active -> "admin.daemons.notices.giRequesterDouble"
    #    ("Znuny löst die ausgehenden Aufrufe ebenfalls aus – Aufrufe werden doppelt gesendet. Schalten Sie in Znuny
    #     die Einstellung Ticket::EventModulePost###9900-GenericInterface ab.")
```

`DaemonServiceOut` gains `notices: list[str] = []` (i18n keys; filled only for `gi_requester` via `daemon_notices`). The worker logs `gi_requester_znuny_handler_active` (warning) on each enabled tick while the Znuny handler is valid (open question 3).

### G13. Provider debugger (wave-1 pipeline)

`run_provider(ws, operation, data, *, run_operation, debugger: GiDebugger | None = None)` — when given: `debug("Communication sequence started", {"RequestMethod", path})` is written by the router before `run_provider`; inside: "Incoming data before mapping", "Incoming data after mapping", "Outgoing data before mapping", "Outgoing data after mapping", mapping/operation faults as `error(...)` — the Znuny `Provider.pm` summaries (verify each in `Provider.pm`). Request bodies with `Password`, `SessionID`, `UserLogin` + `Password` pairs are redacted (`redact_config` matches `passw`; `SessionID` is added to `SECRET_KEY_RE` usage via an explicit provider key list `{"Password", "SessionID", "TwoFactorToken"}`). The router builds the debugger per request with `parse_debugger(ws.config)`, `remote_ip=client_ip(request, settings.trusted_proxies)` (use the existing trusted-proxy setting name), `communication_type="Provider"`, `name=operation`. Canonical routes without a webservice are not logged (no config, no threshold). Writes go through `DbDebugSink` (own sessions), so a failing operation transaction never loses its trail.

### G14. `ExecuteInvoker` process action

`_ACTION_HANDLERS["ExecuteInvoker"] = _action_execute_invoker` in `tiqora/process/engine.py` (handler signature as the others: `(session, config, ticket_id, user_id, sysconfig)`; the config is already smart-tag-replaced by `execute_transition_action` — Znuny `_ReplaceTicketAttributes` + `_ReplaceAdditionalAttributes`). Port of `ProcessManagement/TransitionAction/ExecuteInvoker.pm`: `Webservice` + `Invoker` required (missing → logged warning, no-op, like `_CheckParams` returning); `DynamicField_UserID` overrides `UserID`; webservice looked up **by name** among valid rows (`load_gi_webservice` by name — add `load_gi_webservice_by_name`); not found → logged error, no-op. Then `enqueue_invocation(session, payload=GiInvokePayload(webservice_id, invoker, data={"Origin": "TransitionAction", "TransitionActionConfig": config, "TicketID": ticket_id}, asynchronous=True, origin="transition_action"), created_by=user_id)` — in the caller's transaction (flush only), so the job exists exactly when the dialog submit commits. The module docstring's "deferred" list and `compatibility.md` lose `ExecuteInvoker`.

### G15. Admin API (`/api/v1/admin/webservices`, admin only — `AdminUser` dependency like `daemons.py`)

```
GET  /api/v1/admin/webservices                                   -> WebserviceListOut
GET  /api/v1/admin/webservices/{ws_id}                           -> WebserviceDetailOut
PUT  /api/v1/admin/webservices/{ws_id}/egress                    WebserviceEgressIn -> WebserviceEgressOut
GET  /api/v1/admin/webservices/{ws_id}/debugger                  ?type=Provider|Requester&source=tiqora|znuny
                                                                 &from=&to=&remote_ip=&before=<opaque>&limit=50 (max 200)
                                                                 -> DebugEntryListOut
GET  /api/v1/admin/webservices/{ws_id}/debugger/{source}/{entry_id}  -> DebugEntryDetailOut
POST /api/v1/admin/webservices/{ws_id}/debugger/tiqora/{entry_id}/resend  -> JobRefOut (202)
POST /api/v1/admin/webservices/{ws_id}/invokers/{invoker}/dry-run     DryRunIn{ticket_id:int, article_id:int|None,
                                                                 event:str="TicketCreate"} -> JobRefOut (202)
```

```python
class InvokerOut(BaseModel):
    name: str; type: str; supported: bool
    events: list[InvokerEventOut]            # event, asynchronous, has_condition, fired_by_tiqora: bool
    mapping_outbound: str | None             # "Simple" | "XSLT" | None
    mapping_inbound: str | None
    problem: str | None                      # German text: unsupported type / mapping error
class WebserviceListItemOut(BaseModel):
    id: int; name: str; valid: bool; has_provider: bool; invoker_count: int; event_invoker_count: int
    transport: str | None; problem: str | None    # parse error text (row invalid YAML etc.)
class WebserviceDetailOut(BaseModel):
    id: int; name: str; valid: bool
    requester_transport: RequesterTransportOut | None
        # type, endpoint (Host+… or Endpoint, redact_url), auth_type, has_proxy, uses_client_certificate,
        # insecure_tls_requested: bool — NEVER user names/passwords/token config ids' secrets
    invokers: list[InvokerOut]
    error_handling: list[ErrorHandlingOut]   # key, type, filters, retry summary
    debug_threshold: str
    egress: WebserviceEgressOut
    warnings: list[str]                      # German texts: cert dir not configured, JWT/NTLM, insecure TLS without opt-in
class WebserviceEgressIn(BaseModel):  allowed_targets: list[str] (validate_targets); allow_insecure_tls: bool
class DebugEntryOut(BaseModel): source, id, communication_id, communication_type, name, remote_ip, ticket_id,
                                created: UtcDateTime, max_level, can_resend
class DebugEntryListOut(BaseModel): entries: list[DebugEntryOut]; next_before: str | None
class DebugContentOut(BaseModel): id: int; debug_level: str; subject: str; content: str; created: UtcDateTime
class DebugEntryDetailOut(BaseModel): entry: DebugEntryOut; contents: list[DebugContentOut]
class JobRefOut(BaseModel): job_id: int
```

Rules: list/detail read `gi_webservice_config` rows (all, incl. invalid — `valid` flag; `problem` from parse errors) via the same parsing as G2 (no registry needed); a webservice id that does not exist → 404. Resend: entry must be source tiqora, type Requester, `replay` set, same `ws_id` → `enqueue_invocation(origin="resend", asynchronous=True, data=replay.data, created_by=admin.id)` → 202; flag state does not matter (explicit admin action) but the webservice must be valid. Dry run: `GiInvokePayload(dry_run=True, origin="dry_run", data={"TicketID", "ArticleID"?, "Event": event})` → job; nothing is sent; the job result holds the would-be request (redacted). Rate limit: max 10 queued resend/dry-run jobs per admin (`lock_user_job_quota` + `count_active_jobs`, pattern from reporting).

### G16. Frontend

- `adminSearch.ts`: entry `{slug: "webservices", route: "/admin/webservices", group: "automation", nameKey: "admin.nav.webservices", descriptionKey: "admin.pageDescriptions.webservices", keywords: ["webservice", "genericinterface", "gi", "invoker", "requester", "debugger", "schnittstelle", "soap", "rest", "integration"]}` next to `webhooks`.
- `WebservicesPage`: table (Name, Status gültig/ungültig, Eingehend ja/nein, Ausgehende Aufrufe n, ausgelöst durch Ereignisse n, Hinweis) — read-only; page description: "Webservices werden in Znuny angelegt und hier angezeigt. Bearbeiten folgt in einer späteren Version." Banner when `daemon.gi_requester` is off and any webservice has event invokers (reuse the notice text; link to Dienste).
- `WebserviceDetailPage` tabs: **Übersicht** (transport summary, warnings), **Ausgehende Aufrufe** (invokers: type, events with "sofort"/"im Hintergrund", "Bedingung" badge, "wird von Tiqora nicht ausgelöst" badge, mapping types, problem; button "Probelauf" → `DryRunDialog`: ticket number or id + optional article → job progress component → shows method, URL, headers (masked) and body pretty-printed), **Protokoll (Debugger)** (`DebuggerList`: filters Richtung Eingehend/Ausgehend, Quelle Tiqora/Znuny, Zeitraum, IP; infinite "Weitere laden" via `next_before`; row click → `DebuggerEntryDrawer` with each content row collapsible, level colour via tokens, copy button; "Erneut senden" with confirm dialog "Der Aufruf wird mit den aktuellen Ticketdaten erneut gesendet. Fortfahren?" → job progress → link to the new protocol entry), **Einstellungen** (Interne Ziele erlauben: textarea one entry per line with validation errors from 422; Unsichere TLS-Verbindung erlauben: switch with warning text; save).
- `DaemonsPage`: render `notices` under the service row (warning style); service name/description i18n for `gi_requester` ("Ausgehende Webservice-Aufrufe") and `gi_debug_cleanup` ("Webservice-Protokoll aufräumen").
- Job kind title `jobs.kinds.core.gi_invoke` / `core.gi_invoke_sync`: "Webservice-Aufruf".
- Demo mocks (`giMock.ts`): two webservices (`ExampleCRM` REST with 2 invokers, one with a condition; `LegacySOAP` invalid), 6 debugger entries (Tiqora + Znuny, one error), dry-run job result.

### G17. i18n namespaces

`admin.nav.webservices`, `admin.pageDescriptions.webservices`, `admin.webservices.*` (list, detail tabs, invokers, debugger, egress, dryRun, resend, warnings), `admin.daemons.services.gi_requester|gi_debug_cleanup.*`, `admin.daemons.notices.giRequesterOff|giRequesterDouble`, `jobs.kinds.core.gi_invoke`, `jobs.kinds.core.gi_invoke_sync`. German first ("Sie"), English alongside, then propagate.

---

## Deviations from spec

1. **Debugger storage**: Tiqora tables shaped like Znuny's instead of writing `gi_debugger_entry*` (project rule; open question 1). Tiqora's view reads both.
2. **Event source**: `ticket_history` follower (as the wave-2 plugin system) instead of "outbox + poller" — one source for both sides, no double events, no feed into `ai/auto_worker.py`.
3. **Synchronous invokers run in the worker** (one-slot lane, event order kept), not inside the request that changed the ticket (open question 10). Conditions are evaluated with ticket data at tick time (seconds later).
4. **The debugger never stores secrets**: Znuny's REST requester logs the raw request params including the `Authorization` header; Tiqora logs method, redacted URL, redacted headers and the body size, and masks known secret values in every row. Content is JSON, not Data::Dumper (open question 15).
5. **No redirects**, **no `CONNECT`/`TRACE`**, **no environment proxies**, per-webservice allowlist for internal targets kept in Tiqora, client-certificate files only below `TIQORA_GI_CERT_DIRS`, `SSLNoHostnameVerification` only with Tiqora opt-in (open questions 5–8).
6. **`ReuseClient` / `KeepAlive`** are ignored (a fresh pinned client per request).
7. **JWT (REST) and NTLM (SOAP)** are configuration errors (not in the spec's auth list; open question 9).
8. **`ExecuteInvoker` is always queued** (never synchronous inside the dialog submit), regardless of `Asynchronous`, and does not need the takeover flag (open question 4).
9. **`OldTicketData`** is not in the event block (open question 11); `OTRS_TicketArticleCreateEvent` is not supported (open question 12); non-ticket event object types are not fired (open question 13).
10. **`Module` and `jq#` conditions** never match (Tiqora has no Perl modules and no Jq; Znuny without Jq behaves the same for `jq#`).
11. **Retries are new jobs** with `due_at` + `PastExecutionData` (Znuny `TaskAdd`), not in-place `JobRetry`; the `Attempts => 10` of Znuny's scheduler maps to `max_attempts=3` claims for crash/interrupt requeues before the `sending` checkpoint.
12. **"Erneut senden"** (Beyond Znuny) re-runs the invoker with the stored event data and **current** ticket data; **"Probelauf"** (dry run) is a background job and never sends.
13. **`Tunnel`** (spec "`RequestRetry` and `Tunnel`"): Znuny 6.5 has no `ErrorHandling::Tunnel`; `Invoker::Generic::Tunnel` is an invoker type for provider→requester tunnelling and is out of scope (unsupported invoker type, shown as such).

## Shared files (coordination with other wave-3 plans)

Migration (`versions_tiqora`, head bump, `test_migration_gate.py`, `test_migrate_cli.py`, `test_tiqora_model_ddl.py`), `db/tiqora/models.py`, `config.py`, `domain/settings_store.py`, `worker/__main__.py` + `worker/services.py` (two services), `api/v1/admin/daemons.py` + `api/v1/admin/schemas.py` (`notices`), `api/v1/admin/__init__.py`, `jobs/service.py` (`due_at`), `jobs/registry.py` (`LANE_SLOTS`), `extensions/core.py` (job handler registration, if used), `security/outbound.py`, `process/engine.py` (**the wave-3 process plan also edits `_ACTION_HANDLERS` and the module docstring — rebase carefully, add one dict entry only**), `api/compat/provider.py` + `router.py` (wave-1 files), `api/compat/mapping/xml_simple.py` (`no_attr` option), `api/compat/ticket_get.py` (DF flat keys flag, if needed), `gdpr/erasure.py`, frontend `router.tsx`, `lib/adminSearch.ts`, `routes/admin/DaemonsPage.tsx`, `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` + propagation, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `docs/compatibility.md`, `docs/parallel-operation.md`, `docs/architecture.md`, `docs/deployment.md`, `tests/golden/docker-compose.golden.yml`, `tests/golden/_helpers.py`.

---

## Tasks

Order: Task 1 first. Pure Tasks 2–5 in any order after 1 (2 needs nothing from 1 except the package; 3 needs 1's tables). 6 → 7 → 8 (transports). 9 → 10 (invoker). 11 needs 2, 3, 5, 7, 8, 9, 10. 12 needs 11. 13 needs 4, 9, 12. 14 needs 3. 15 needs 12. 16 needs 3, 6, 12. Frontend 17–18 need 16 (openapi). 19 needs 13 + 14. 20 closes. Backend-only: 1–16. Frontend-only: 17–18. Golden: 19.

Seed sub-bands: T1 51000–51019 · T3 51020–51059 · T6 51060–51079 · T9 51080–51159 · T10 51160–51219 · T11 51220–51269 · T12 51270–51309 · T13 51310–51369 · T14 51370–51389 · T15 51390–51409 · T16 51410–51469 · spare 51470–51499. Webservice ids (`gi_webservice_config.id`) use the same numbers as the task's band.

### Task 1: Debugger tables, ORM, migration, job `due_at`, lanes, test fixtures (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_gi_debugger.py` (revision = next free number after the current head at implementation time; `down_revision` = that head — check, never copy)
- Modify: `backend/src/tiqora/db/tiqora/models.py` (`TiqoraGiDebugEntry`, `TiqoraGiDebugContent`, G1), `backend/src/tiqora/jobs/service.py` (`due_at`), `backend/src/tiqora/jobs/registry.py` (`LANE_SLOTS`), `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`, `backend/tests/test_tiqora_model_ddl.py`
- Create: `backend/src/tiqora/gi/__init__.py`, `backend/tests/_gi_fixtures.py`
- Test: `backend/tests/test_gi_tables.py` (both dialects, band 51000–51019), `backend/tests/test_jobs_due_at.py` (both dialects, band 51000–51019)

**Interfaces:**
- Consumes: `TiqoraBase`, `MEDIUM_TEXT_VARIANT`, `enqueue_job`, `tests._row_cleanup`.
- Produces: G1 tables + ORM; `enqueue_job(..., due_at=None)`; `LANE_SLOTS["gi"] == 2`, `LANE_SLOTS["gi_sync"] == 1`; fixtures:
  ```python
  # backend/tests/_gi_fixtures.py
  GI_MARK_TABLES: tuple[str, ...] = (
      "tiqora_gi_debug_content", "tiqora_gi_debug_entry", "tiqora_job", "tiqora_event_outbox",
      "tiqora_cache_invalidation", "ticket_history", "dynamic_field_value", "article_data_mime_attachment",
      "article_data_mime_plain", "article_data_mime", "article", "ticket", "ticket_number_counter",
  )
  def insert_webservice(conn, *, ws_id: int, name: str, config: dict[str, Any], valid: bool = True) -> None
      # gi_webservice_config row: YAML via yaml.safe_dump(config, sort_keys=True), create/change by 1, valid_id 1|2
  def requester_config(*, host: str = "https://crm.example.org", invokers: dict[str, Any], transport: str = "HTTP::REST",
                       error_handling: dict[str, Any] | None = None, debug_threshold: str = "debug",
                       transport_extra: dict[str, Any] | None = None) -> dict[str, Any]
      # minimal valid Znuny config: Debugger, Provider {Transport {Type: ""}}, Requester {Transport {Type, Config{Host,
      # DefaultCommand: POST, Timeout: "30", InvokerControllerMapping{<each invoker>: {Controller: "/Ticket/:TicketID"}}}},
      # Invoker, ErrorHandling, ErrorHandlingPriority}
  def cleanup_webservices(conn, ws_ids: Iterable[int]) -> None
      # deletes tiqora_settings 'gi.webservice.<id>.%' and the gi_webservice_config rows
  ```

- [ ] **Step 1: Write the failing tests.** `test_tiqora_model_ddl.py`:
  ```python
  def test_gi_debug_ddl() -> None:
      from tiqora.db.tiqora.models import TiqoraGiDebugContent, TiqoraGiDebugEntry
      assert "VARCHAR(32)" in _column_ddl(TiqoraGiDebugEntry.__table__, "communication_id", mysql.dialect())
      uniques = {tuple(c.name for c in ix.columns) for ix in TiqoraGiDebugEntry.__table__.indexes if ix.unique}
      uniques |= {tuple(c.name for c in uc.columns) for uc in TiqoraGiDebugEntry.__table__.constraints
                  if isinstance(uc, UniqueConstraint)}
      assert ("communication_id",) in uniques
      fk = next(iter(TiqoraGiDebugContent.__table__.c.entry_id.foreign_keys))
      assert fk.ondelete == "CASCADE"
  ```
  `test_gi_tables.py` (both dialects, `create_all` of the two tables):
  ```python
  async def test_entry_content_round_trip_and_cascade(url_fixture, request) -> None:
      # insert entry (communication_id "a"*32, "Requester", ws 51001, name "SendTicket", ticket_id 51002,
      #   replay json, create_time utc_now()) + two content rows (debug/error, content "Grüße €".encode())
      # read back content decoded == "Grüße €"; second entry with same communication_id -> IntegrityError
      # DELETE entry -> content rows gone (ON DELETE CASCADE works on both dialects)
  ```
  `test_jobs_due_at.py`:
  ```python
  async def test_enqueue_with_due_at_is_not_claimed_early(url_fixture, request) -> None:
      # register a test handler (kind "test.gi_due", lane "gi") on a fresh Registry, enqueue with due_at = now + 60 s
      # -> row.due_at set; the runner's claim query (call the runner's claim function the way
      #    backend/tests/test_jobs_runner.py does) returns nothing; with due_at = now - 1 s it is claimed
  def test_gi_lanes() -> None:
      assert lane_slots("gi", 4) == 2 and lane_slots("gi_sync", 4) == 1
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_tables.py tests/test_jobs_due_at.py tests/test_tiqora_model_ddl.py` (ImportError / missing column).
- [ ] **Step 3: Migration** exactly as G1 (named indexes/unique), `downgrade` drops content then entry. Update `TIQORA_HEAD` + the CLI assertion. `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py` → PASS.
- [ ] **Step 4: Implement** ORM, `due_at` parameter (validated: naive UTC; tz-aware input converted), `LANE_SLOTS`, package stub, fixtures module.
- [ ] **Step 5: Run** Step 2 + migration tests → PASS; lint gates; commit:
  ```bash
  rtk git add backend/alembic/versions_tiqora backend/src/tiqora/db/tiqora/models.py backend/src/tiqora/jobs/service.py backend/src/tiqora/jobs/registry.py backend/src/tiqora/gi/__init__.py backend/tests/_gi_fixtures.py backend/tests/test_gi_tables.py backend/tests/test_jobs_due_at.py backend/tests/test_tiqora_model_ddl.py backend/tests/test_migration_gate.py backend/tests/test_migrate_cli.py
  rtk git diff --cached --name-only
  rtk git commit -m "$(printf 'feat(gi): debugger tables, job due_at, GI job lanes\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
  rtk git log -1 --oneline
  ```

### Task 2: Requester config model and worker-side webservice snapshot (backend)

**Files:**
- Create: `backend/src/tiqora/gi/config.py` (G2), `backend/src/tiqora/gi/loader.py` (G2)
- Test: `backend/tests/test_gi_config.py` (pure), `backend/tests/test_gi_loader.py` (both dialects, band 51000–51019 — reuse T1 band, ids 51010–51019)

**Interfaces:**
- Consumes: wave-1 `build_mapper`, `MappingConfigError`, `perl_truthy`, `load_fingerprint`, `_load_yaml_rows`, `replace_config_placeholders`; `HISTORY_EVENT_MAP`.
- Produces: G2 names exactly.

- [ ] **Step 1: Read** `Kernel/GenericInterface/Event/Handler.pm` `Run` (which invoker/event entries are skipped), `Requester.pm` (where `MappingOutbound`/`MappingInbound` must be non-empty hashes), `ErrorHandling.pm` (priority/modules lookup).
- [ ] **Step 2: Failing tests** (`test_gi_config.py`):
  ```python
  def test_parse_minimal_requester() -> None:
      cfg = requester_config(invokers={"SendTicket": {"Type": "Ticket::Generic", "Events": [
          {"Event": "TicketCreate", "Asynchronous": "1"},
          {"Event": "TicketStateUpdate", "Asynchronous": "0", "Condition": {"ConditionLinking": "and",
              "Condition": {"1": {"Type": "and", "Fields": {"Queue": {"Type": "String", "Match": "Raw"}}}}}}]}})
      r = parse_requester(cfg, huge_xml=False)
      inv = r.invokers["SendTicket"]
      assert inv.supported and [e.event for e in inv.events] == ["TicketCreate", "TicketStateUpdate"]
      assert inv.events[0].asynchronous is True and inv.events[1].asynchronous is False
      assert inv.events[1].condition is not None and inv.events[0].condition is None
  def test_events_skip_bad_entries() -> None:            # non-dict, empty Event -> skipped (Handler.pm)
  def test_unsupported_type_and_mapping_error_flagged() -> None:
      # Type "Test::Test" -> supported False; MappingOutbound {"Type": "Nope"} -> mapping_error set; others fine
  def test_error_handling_priority_order_and_missing_keys() -> None:
      # ErrorHandlingPriority ["b", "missing", "a"] with modules a/b -> keys ("b", "a")
  def test_debugger_threshold_defaults_to_error() -> None:
      assert parse_debugger({}).threshold == "error"
      assert parse_debugger({"Debugger": {"DebugThreshold": "loud"}}).threshold == "error"
      assert parse_debugger({"Debugger": {"DebugThreshold": "notice", "TestMode": "0"}}).test_mode is False
  def test_non_ticket_events_listed_unsupported() -> None:
      # Events [{"Event": "QueueUpdate"}] -> unsupported_events == (("SendTicket", "QueueUpdate"),)
  @pytest.mark.parametrize(("event", "ok"), [("TicketCreate", True), ("ArticleCreate", True),
      ("TicketDynamicFieldUpdate_Foo", True), ("TicketQueueUpdate", True), ("QueueUpdate", False), ("ArticleSend", False)])
  def test_is_ticket_event(event, ok) -> None:
      assert is_ticket_event(event) is ok
  ```
  `test_gi_loader.py`: two valid webservices (one with requester, one provider-only) + one invalid YAML + one `valid_id=2` → `all()` returns only the requester one; `refresh` returns False on an unchanged fingerprint and True after `change_time` changes; a webservice whose YAML is broken does not hide the other (`gi_webservice_invalid` logged, check with `structlog.testing.capture_logs`); `<OTRS_CONFIG_FQDN>` in `Host` is replaced from SysConfig.
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_config.py tests/test_gi_loader.py`
- [ ] **Step 4: Implement** G2.
- [ ] **Step 5: Run → PASS**, lint gates, commit `feat(gi): requester config model and worker webservice snapshot`.

### Task 3: Redaction, debugger, debug store, retention, GDPR (backend)

**Files:**
- Create: `backend/src/tiqora/gi/redact.py`, `backend/src/tiqora/gi/debugger.py`, `backend/src/tiqora/gi/debug_store.py` (G3)
- Modify: `backend/src/tiqora/gdpr/erasure.py` (delete debugger rows by ticket), `backend/src/tiqora/domain/settings_store.py` (`KEY_GI_DEBUG_RETENTION_DAYS`), `backend/src/tiqora/worker/services.py` + `worker/__main__.py` (`gi_debug_cleanup` daily service), `backend/src/tiqora/config.py` (`gi_debug_max_content_bytes`)
- Test: `backend/tests/test_gi_redact.py` (pure), `backend/tests/test_gi_debugger.py` (pure with an in-memory sink), `backend/tests/test_gi_debug_store.py` (both dialects, band 51020–51059), extend the GDPR erasure test that covers `_TICKET_DELETE_ORDER` (one assertion that a debug entry with that `ticket_id` is gone)

**Interfaces:**
- Consumes: Task 1 tables; `GiDebugEntry` columns from Znuny (`gi_debugger_entry`, `gi_debugger_entry_content`) via `text()` SQL (read-only).
- Produces: G3 names exactly.

- [ ] **Step 1: Read** `Kernel/GenericInterface/Debugger.pm` (threshold, TestMode, error → Log) and `Kernel/System/GenericInterface/DebugLog.pm` `LogAdd`/`LogSearch`/`LogGet` (entry row created on first content; `LogSearch` filters `CommunicationType`, `RemoteIP`, `CreatedAtOrAfter`/`CreatedAtOrBefore`, `WebserviceID`, sort by create_time).
- [ ] **Step 2: Failing tests.**
  ```python
  # test_gi_redact.py
  def test_redact_headers_and_config() -> None:
      h = redact_headers({"Authorization": "Basic dXNlcjpzZWNyZXQ=", "Proxy-Authorization": "Basic eA==",
                          "X-Api": "Bearer tok-123456", "Accept": "application/json"}, secret_values=["tok-123456"])
      assert h["Authorization"] == "Basic ***" and h["Proxy-Authorization"] == "Basic ***"
      assert h["X-Api"] == "Bearer ***" and h["Accept"] == "application/json"
      c = redact_config({"Authentication": {"AuthType": "BasicAuth", "BasicAuthUser": "u", "BasicAuthPassword": "pw-secret"},
                         "Proxy": {"ProxyPassword": "p2-secret", "ProxyHost": "http://proxy.example.org"}, "Host": "https://x.example.org"})
      assert "pw-secret" not in json.dumps(c) and "p2-secret" not in json.dumps(c)
      assert c["Host"] == "https://x.example.org"
  def test_redact_url_and_text() -> None:
      assert redact_url("https://u:p@crm.example.org/a?token=abc&x=1") == "https://crm.example.org/a?token=***&x=1"
      assert redact_text("failed for pw-secret at host", ["pw-secret"]) == "failed for *** at host"
      assert redact_text("abc", ["ab"]) == "abc"          # values shorter than 4 chars are not masked
  # test_gi_debugger.py
  async def test_threshold_and_test_mode() -> None:
      sink = MemorySink()
      d = GiDebugger(sink=sink, config=DebuggerConfig("notice", False), webservice_id=1, communication_type="Requester")
      await d.debug("a"); await d.info("b"); await d.notice("c", {"x": 1}); await d.error("d")
      assert [r.summary for r in sink.rows] == ["c", "d"]
      t = GiDebugger(sink=sink, config=DebuggerConfig("debug", True), webservice_id=1, communication_type="Requester")
      await t.error("nothing written")
      assert len(sink.rows) == 2
  async def test_content_json_sorted_redacted_truncated() -> None:
      # dict data -> json with sorted keys and indent 2; secret_values masked; 300 KiB string -> cut + "gekürzt" note;
      # summary > 255 chars truncated; None -> "No data provided"; communication_id is 32 lowercase hex
  async def test_error_returns_znuny_shape() -> None:
      assert await d.error("boom") == {"Success": 0, "ErrorMessage": "boom"}
  # test_gi_debug_store.py (both dialects)
  async def test_sink_writes_entry_once_and_contents_in_order(url_fixture, request) -> None
  async def test_sink_commits_independently_of_caller_rollback(url_fixture, request) -> None
      # caller session inserts a ticket row, sink.add(...), caller rolls back -> debug rows still there
  async def test_search_merges_tiqora_and_znuny_rows_by_time(url_fixture, request) -> None
      # seed gi_debugger_entry/_content rows (Znuny tables exist in the test schema) for ws 51021 and Tiqora rows;
      # search(limit=3) returns the newest 3 across both, next page via `before` continues without duplicates;
      # filters: communication_type, remote_ip, source, time window; max_level computed per entry
  async def test_get_entry_decodes_znuny_content(url_fixture, request) -> None
  async def test_purge_and_gdpr_delete(url_fixture, request) -> None
      # purge(older_than) removes only Tiqora rows older than the cut-off and never touches gi_debugger_*;
      # delete_debug_entries_for_tickets([51030]) removes that entry + contents only
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_redact.py tests/test_gi_debugger.py tests/test_gi_debug_store.py`
- [ ] **Step 4: Implement** G3; daily service `gi_debug_cleanup` (pattern: the GDPR daily loops) calling `purge_debug_entries(older_than=utc_now() - timedelta(days=max(1, retention)))`; GDPR hook. Update the Daemons catalog test that pins the slug list.
- [ ] **Step 5: Run → PASS** (+ `tests/test_admin_daemons*.py`, the GDPR erasure test module), lint gates, commit `feat(gi): debugger with secret masking, debug log store, retention and GDPR cleanup`.

### Task 4: Event conditions — `_SerializeConfig` + `_ConditionCheck` with Znuny's vectors (backend, pure)

**Files:**
- Create: `backend/src/tiqora/gi/conditions.py` (G4)
- Test: `backend/tests/test_gi_conditions.py`

**Interfaces:**
- Consumes: `compile_perl_regex`.
- Produces: `serialize_config`, `condition_check`, `ConditionWarning` (G4).

- [ ] **Step 1: Read** `Event/Handler.pm` `_SerializeConfig`, `_ConditionCheck` and the test files `scripts/test/GenericInterface/Event/SerializeConfig.t`, `ConditionCheck.t` (skip `ConditionCheckJq.t` except to pin "jq# never matches").
- [ ] **Step 2: Failing tests.** Transcribe **all** cases of `SerializeConfig.t` and `ConditionCheck.t` into `CASES` lists (`name, condition, data, expected`; Perl `undef` → `None`; `qr{…}` Match values → the pattern string with a note; `Module` cases → expected `False` with a comment "Tiqora: Module never matches" where Znuny's test expects true via `ValidateDemo`). Plus:
  ```python
  def test_serialize_skips_falsy_and_does_not_advance_index() -> None:
      assert serialize_config({"A": ["x", "0", "", "y"]}) == {"A_0": "x", "A_1": "y"}
  def test_serialize_skips_containers_inside_arrays() -> None:     # Perl would emit unmatchable HASH(0x…) keys
      assert serialize_config({"A": [{"k": "v"}, "s"]}) == {"A_1": "s"}
  def test_serialize_empty_container_kept_as_value() -> None:
      assert serialize_config({"A": {}, "B": []}) == {"A": {}, "B": []}
  def test_string_matches_serialized_array_keys() -> None:
      cond = {"ConditionLinking": "and", "Condition": {"1": {"Type": "and", "Fields": {"Tag": "b"}}}}
      assert condition_check(cond, serialize_config({"Tag": ["a", "b"]}))
  def test_hash_type_non_dict_is_fail_without_early_return() -> None:   # Znuny quirk: `next FIELDLNAME`, no early return
  def test_or_linking_short_circuits() -> None
  def test_xor_condition_needs_exactly_one() -> None
  def test_regexp_is_not_verbose() -> None:      # Perl qr{$Match} has no /x: a space is significant
      cond = {"Condition": {"1": {"Fields": {"Title": {"Type": "Regexp", "Match": "a b"}}}}}
      assert condition_check(cond, {"Title": "xa by"}) and not condition_check(cond, {"Title": "xaby"})
  def test_module_and_jq_never_match_with_warning(caplog_structlog) -> None
  def test_invalid_linking_or_type_is_false() -> None
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_gi_conditions.py`
- [ ] **Step 4: Implement** G4 as a line-by-line port (keep the early-return structure; docstring names `Handler.pm` line ranges).
- [ ] **Step 5: Run → PASS**, lint gates, commit `feat(gi): Znuny invoker event conditions with ported vectors`.

### Task 5: Error handling — `RequestRetry` schedule and module chain (backend, pure)

**Files:**
- Create: `backend/src/tiqora/gi/error_handling.py` (G5)
- Test: `backend/tests/test_gi_error_handling.py`

**Interfaces:**
- Consumes: `ErrorHandlingModule` (Task 2), `GiDebugger` + memory sink (Task 3), `compile_perl_regex`.
- Produces: `PastExecutionData`, `RetryDecision`, `request_retry`, `handle_error`, `HandleErrorResult`, `ErrorHandlingConfigError`, `ERROR_STAGES`.

- [ ] **Step 1: Read** `ErrorHandling.pm`, `ErrorHandling/RequestRetry.pm` and `scripts/test/GenericInterface/ErrorHandling/RequestRetry.t` + `ErrorHandling.t`.
- [ ] **Step 2: Failing tests.** Transcribe every `RequestRetry.t` case (config, PastExecutionData, fixed "now" via the test's `FixedTimeSet` → `now=` parameter, expected Data/ReScheduleData) into `RETRY_CASES`; and the `ErrorHandling.t` cases for filters/StopAfterMatch into `CHAIN_CASES`. Plus:
  ```python
  NOW = datetime(2026, 1, 15, 10, 0, 0)
  def cfg(**kw: str) -> dict[str, str]:
      return {"ScheduleRetry": "1", "RetryIntervalStart": "60", "RetryIntervalFactor": "2", **kw}
  def test_first_failure_schedules_start_interval() -> None:
      d = request_retry(cfg(), None, now=NOW)
      assert d.reschedule and d.execution_time == NOW + timedelta(seconds=60)
      assert d.past == PastExecutionData(NOW, 1, 60, NOW + timedelta(seconds=60))
  def test_factor_and_cap() -> None:
      past = PastExecutionData(NOW, 3, 240, NOW)
      d = request_retry(cfg(RetryIntervalMax="300"), past, now=NOW)
      assert d.past.retry_interval == 300                    # int(240*2)=480 capped
  def test_factor_one_point_five_truncates() -> None:
      d = request_retry(cfg(RetryIntervalFactor="1.5"), PastExecutionData(NOW, 1, 75, NOW), now=NOW)
      assert d.past.retry_interval == 112                    # int(112.5)
  def test_period_clamps_to_initial_plus_period() -> None:
      past = PastExecutionData(NOW, 2, 120, NOW + timedelta(seconds=500))
      d = request_retry(cfg(RetryPeriodMax="600"), past, now=NOW + timedelta(seconds=500))
      assert d.execution_time == NOW + timedelta(seconds=600)
  def test_count_and_period_reached() -> None
  def test_target_in_past_becomes_now_plus_one() -> None
  @pytest.mark.parametrize("bad", [{"ScheduleRetry": "yes"}, {"RetryIntervalFactor": "3"}, {"RetryIntervalStart": "-1"},
                                   {"RetryCountMax": "0"}, {"RetryPeriodMax": "x"}])
  def test_config_errors(bad) -> None:
      with pytest.raises(ErrorHandlingConfigError):
          request_retry(cfg(**bad), None, now=NOW)
  async def test_chain_filters_and_stop_after_match() -> None
      # InvokerFilter excludes, ErrorStageFilter excludes, ErrorMessageContentFilter matches json-dumped data,
      # StopAfterMatch "backend" skips a second RequestRetry module, "all" stops the loop
  async def test_no_modules_returns_summary_without_reschedule() -> None
  def test_past_execution_json_round_trip() -> None   # Znuny DateTime strings "YYYY-MM-DD HH:MM:SS"
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_gi_error_handling.py`
- [ ] **Step 4: Implement** G5 (port; messages verbatim from Perl).
- [ ] **Step 5: Run → PASS**, lint gates, commit `feat(gi): ErrorHandling and RequestRetry ported with Znuny's vectors`.

### Task 6: Egress policy — allowlist in the SSRF guard, cert directories, TLS, proxy, Tiqora webservice settings (backend)

**Files:**
- Modify: `backend/src/tiqora/security/outbound.py` (`allow_networks`), `backend/src/tiqora/config.py` (G6 settings)
- Create: `backend/src/tiqora/gi/ws_settings.py`, `backend/src/tiqora/gi/egress.py` (G6)
- Test: `backend/tests/test_outbound_allow_networks.py` (pure), `backend/tests/test_gi_egress.py` (pure + `tmp_path`), `backend/tests/test_gi_ws_settings.py` (both dialects, band 51060–51079)

**Interfaces:**
- Consumes: `pin_outbound_url`, `is_blocked_ip`, `HostResolver`, `get_setting`/`set_setting`.
- Produces: G6 names exactly.

- [ ] **Step 1: Failing tests.**
  ```python
  def fake(ips: dict[str, list[str]]) -> HostResolver:
      return lambda host, port: ips[host]
  def test_private_blocked_by_default_allowed_in_listed_network() -> None:
      r = fake({"crm.example.org": ["10.1.2.3"]})
      with pytest.raises(OutboundURLError):
          pin_outbound_url("https://crm.example.org/x", resolver=r)
      p = pin_outbound_url("https://crm.example.org/x", resolver=r, allow_networks=[ipaddress.ip_network("10.0.0.0/8")])
      assert p.pinned_ip == "10.1.2.3"
  @pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "::1", "0.0.0.0", "224.0.0.1", "fe80::1"])
  def test_metadata_never_allowed_even_if_listed(ip) -> None:
      with pytest.raises(OutboundURLError):
          pin_outbound_url(f"http://h.example.org/", resolver=fake({"h.example.org": [ip]}),
                           allow_networks=[ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0")])
  def test_rebinding_host_pinned_and_rejected() -> None:     # any one blocked A record rejects the URL
      with pytest.raises(OutboundURLError):
          pin_outbound_url("https://a.example.org/", resolver=fake({"a.example.org": ["198.51.100.7", "10.0.0.5"]}))
  def test_cgnat_allowed_only_when_listed() -> None
  # test_gi_egress.py
  def test_validate_targets() -> None:
      assert validate_targets(["10.0.0.0/8", "CRM.example.org"]) == ("10.0.0.0/8", "crm.example.org")
      for bad in ["127.0.0.0/8", "169.254.0.0/16", "https://x.example.org", "*.example.org", "x.example.org:443"]:
          with pytest.raises(ValueError):
              validate_targets([bad])
  def test_host_allowlist_allows_its_private_ip_only() -> None
  def test_cert_path_outside_allowed_dirs_rejected(tmp_path) -> None:
      inside = tmp_path / "certs" / "client.pem"; inside.parent.mkdir(); inside.write_text("x")
      outside = tmp_path / "other.pem"; outside.write_text("x")
      link = tmp_path / "certs" / "link.pem"; link.symlink_to(outside)
      pol = policy(cert_dirs=(tmp_path / "certs",))
      assert resolve_cert_path(str(inside), pol) == inside.resolve()
      for p in (str(outside), str(link), "/etc/shadow", str(tmp_path / "certs" / ".." / "other.pem")):
          with pytest.raises(EgressError):
              resolve_cert_path(p, pol)
  def test_no_cert_dirs_means_client_certs_disabled() -> None
  def test_insecure_tls_needs_opt_in() -> None
  def test_proxy_host_checked() -> None:
      # Proxy {UseProxy: Yes, ProxyHost: http://localhost:3128} -> EgressError; ProxyExclude Yes -> (None, {});
      # ProxyUser/ProxyPassword -> Proxy-Authorization "Basic …" header
  def test_client_never_follows_redirects_or_env_proxies() -> None:
      c = client(policy=policy(), ssl_context=None, proxy=None)
      assert c.follow_redirects is False and c._trust_env is False     # attribute name per httpx version; assert via behaviour if private
  async def test_read_capped() -> None
  # test_gi_ws_settings.py (both dialects): default (), False; set/get round trip; per-webservice isolation
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_outbound_allow_networks.py tests/test_gi_egress.py tests/test_gi_ws_settings.py`
- [ ] **Step 3: Implement** G6. Re-run the existing outbound/webhook tests (`tests/test_outbound*.py`, `tests/test_webhooks*.py`) to prove existing callers are unchanged.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(gi): egress policy with internal-target allowlist, cert directories and TLS opt-in`.

### Task 7: REST requester transport (backend)

**Files:**
- Create: `backend/src/tiqora/gi/transport_rest.py` (G7)
- Modify: `backend/src/tiqora/api/compat/mapping/xml_simple.py` (`xml_out(..., no_attr=True, root_name="RootElement")` options; existing callers unchanged)
- Test: `backend/tests/test_gi_transport_rest.py` (httpx `MockTransport` + fake resolver; OAuth2 case with a DB fixture → both dialects, band 51060–51079 shared with Task 6 ids 51070–51079)

**Interfaces:**
- Consumes: Task 3 debugger (memory sink), Task 6 egress, `get_access_token`, wave-1 `znuny_json_decode`, `xml_out`.
- Produces: `TransportResult`, `rest_perform_request`, `build_rest_request` (dry run), `flatten_data_structure`, `interpolate_controller`, `build_query`.

- [ ] **Step 1: Read** `Transport/HTTP/REST.pm` lines 505–1290 and 1473–end, `Kernel/cpan-lib/REST/Client.pm` `buildQuery` (key order, encoding), `scripts/test/GenericInterface/Transport/HTTP/REST*.t` if present (port controller interpolation cases).
- [ ] **Step 2: Failing tests** (the mock records method, URL, headers, body; resolver maps `crm.example.org` → `198.51.100.10`):
  ```python
  async def test_post_json_with_controller_interpolation() -> None:
      cfg = {"Host": "https://crm.example.org/api", "DefaultCommand": "POST", "Timeout": "30",
             "InvokerControllerMapping": {"SendTicket": {"Controller": "/Ticket/:TicketID?x=:Ticket.TicketNumber"}}}
      res = await rest_perform_request(transport_config=cfg, operation="SendTicket",
                                       data={"TicketID": 7, "Ticket": {"TicketNumber": "2026100110000017"}, "Title": "Hallo"}, ...)
      req = sink_requests[0]
      assert req.method == "POST" and req.url.path == "/api/Ticket/7" and req.url.params["x"] == "2026100110000017"
      assert json.loads(req.content) == {"Ticket": {"TicketNumber": "2026100110000017"}, "Title": "Hallo"}  # TicketID removed, dot key kept
      assert req.headers["host"] == "crm.example.org"                       # pinned IP, original Host header
  async def test_get_puts_data_into_query() -> None
  async def test_content_type_form_and_xml() -> None
  async def test_request_headers_replace_and_append() -> None   # __RequestHeaders / __RequestHeadersAppend
  async def test_basic_auth_header_and_debugger_never_contains_basic_auth() -> None:
      # BasicAuthUser "gi51user" / BasicAuthPassword "gi51-secret" -> Authorization sent;
      # every debugger row content and summary free of "gi51-secret" and of base64("gi51user:gi51-secret")
  async def test_oauth2_token_placeholder(url_fixture, request) -> None:
      # seed oauth2_token_config/oauth2_token (valid, token "tok-gi51-abcdef", not expired) id 51071;
      # AuthType OAuth2Token + OAuth2TokenConfigID 51071 + AdditionalHeaders {"Authorization": "Bearer <OTRS_OAUTH2_TOKEN>",
      #   "X-Other": "<OTRS_SOMETHING>"} -> header "Bearer tok-gi51-abcdef", X-Other ""; token absent from debugger;
      # missing token row -> error "OAuth2 token for config with ID 51072 could not be retrieved."
  async def test_jwt_is_config_error() -> None
  async def test_non_2xx_error_message_znuny_text() -> None:
      # 500 with body -> "Error while performing REST 'POST' request to Controller '/Ticket/7' on Host 'https://crm.example.org/api'. Response code '500'."
  async def test_redirect_not_followed() -> None   # 302 Location http://10.0.0.1 -> failure, mock saw exactly one request
  async def test_204_and_text_plain_and_bad_json() -> None
  async def test_response_size_cap() -> None
  async def test_timeout_is_failure_with_redacted_message() -> None
  async def test_connect_and_trace_refused() -> None
  def test_flatten_and_interpolate_quirks() -> None:
      assert flatten_data_structure({"A": {"B": "1"}, "L": ["x", "0", "y"], "Z": ""}) == {"A.B": "1", "L.0": "x", "L.1": "y"}
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_transport_rest.py`
- [ ] **Step 4: Implement** G7. Every debugger row goes through `redact_*` with the collected `secret_values` (password, proxy password, token).
- [ ] **Step 5: Run → PASS** (+ `tests/test_compat_xml_simple.py`), lint gates, commit `feat(gi): REST requester transport with pinned egress and masked debugger rows`.

### Task 8: SOAP requester transport (backend)

**Files:**
- Create: `backend/src/tiqora/gi/soap_envelope.py`, `backend/src/tiqora/gi/transport_soap.py` (G8)
- Test: `backend/tests/test_gi_soap_envelope.py` (pure), `backend/tests/test_gi_transport_soap.py` (MockTransport)

**Interfaces:**
- Consumes: Task 3, Task 6, wave-1 `compat/soap.py` parser flags and `_element_to_value` (import the public helper if one exists; otherwise expose `element_to_value` there with a one-line rename — note in commit).
- Produces: G8 names exactly; `TransportResult` reused from Task 7.

- [ ] **Step 1: Read** `Transport/HTTP/SOAP.pm` 570–1106 and 1345–1696; `scripts/test/GenericInterface/Transport/HTTP/SOAP*.t` (port `_SOAPOutputRecursion` sort cases).
- [ ] **Step 2: Failing tests.**
  ```python
  def test_soap_action_schemes() -> None:
      base = {"NameSpace": "http://ws.example.org/Conn"}
      assert soap_action(base, operation="Inv", operation_name="Op") == "http://ws.example.org/Conn#Op"
      assert soap_action({**base, "SOAPAction": "No"}, operation="Inv", operation_name="Op") == ""
      assert soap_action({**base, "SOAPAction": "Yes", "SOAPActionScheme": "Operation"}, operation="Inv", operation_name="Op") == "Inv"
      assert soap_action({**base, "SOAPAction": "Yes", "SOAPActionScheme": "SeparatorOperation", "SOAPActionSeparator": "/"},
                         operation="Inv", operation_name="Op") == "/Op"
      assert soap_action({**base, "SOAPAction": "Yes", "SOAPActionScheme": "FreeText", "SOAPActionFreeText": "urn:x"},
                         operation="Inv", operation_name="Op") == "urn:x"
  def test_request_and_response_names() -> None
  def test_output_sort_order() -> None:       # Sort [{"B": …}, {"A": …}] -> B before A; without Sort keys sorted
  def test_escaping_and_lists() -> None
  def test_envelope_namespaces_no_namespace_and_fixed_prefix() -> None
  def test_parse_fault_and_response() -> None:
      # Fault -> "faultcode: Server, faultstring: boom"; response element "<Op>Response" -> dict
  def test_parse_response_rejects_xxe() -> None
  async def test_soap_post_with_basic_auth_masked() -> None   # header SOAPAction quoted, Content-Type text/xml; charset=utf-8
  async def test_ntlm_is_config_error() -> None
  async def test_non_utf8_encoding_converted() -> None        # Encoding iso-8859-1 response with "Grüße"
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_gi_soap_envelope.py tests/test_gi_transport_soap.py`
- [ ] **Step 4: Implement** G8. Envelope byte layout is pinned in Task 19 against Znuny; here pin structure (namespaces, element order, text).
- [ ] **Step 5: Run → PASS**, lint gates, commit `feat(gi): SOAP requester transport`.

### Task 9: `Ticket::Generic` PrepareRequest — `TicketDeepGet`, omitted/base64 fields, event data (backend)

**Files:**
- Create: `backend/src/tiqora/gi/deep_get.py`, `backend/src/tiqora/gi/invoker_generic.py` (`prepare_request` only), `backend/src/tiqora/gi/events.py` (event data builder; `match_invokers` comes in Task 13)
- Modify (only if needed, flag-guarded, default unchanged): `backend/src/tiqora/api/compat/ticket_get.py`
- Test: `backend/tests/test_gi_deep_get.py` (both dialects, band 51080–51159), `backend/tests/test_gi_event_data.py` (pure), `backend/tests/test_gi_util_ports.py` (pure)

**Interfaces:**
- Consumes: wave-1 `build_ticket_entry`, `build_article_entries`, `attachment_index`, `load_ticket_get_context`; `SysConfig`; `TicketEvent`.
- Produces: `ticket_deep_get`, `DEEP_GET_CLEANUP_RE`, `data_structure_remove_elements`, `base64_deep_encode`, `prepare_request`, `event_data(event: TicketEvent) -> dict[str, Any]`.

- [ ] **Step 1: Read** `Kernel/System/Ticket.pm` `TicketDeepGet` + `_TicketDeepGetDataCleanUp` + `_GetBase64EncodedArticleAttachments`; `TicketGet` with `DynamicFields => 1` (flat `DynamicField_<Name>` keys); `Kernel/System/User.pm` `GetUserData`, `CustomerUser.pm` `CustomerUserDataGet`, `CustomerCompany.pm` `CustomerCompanyGet`, `Queue.pm` `QueueGet`, `Type.pm`, `Priority.pm`, `Service.pm`, `SLA.pm` key sets; `Kernel/System/Util.pm` (or wherever `DataStructureRemoveElements`/`Base64DeepEncode` live in 6.5); `Invoker/Ticket/Generic.pm` `PrepareRequest`; the history name formats for `TicketDynamicFieldUpdate` and `Merged`.
- [ ] **Step 2: Failing tests.**
  ```python
  async def test_deep_get_shape(url_fixture, request) -> None:
      # seed: queue/user/customer user (gi51…@example.org)/company, ticket 51080 with 2 articles (one with a pdf
      # attachment and an html body), a ticket DF "GiTag" = "a"
      d = await ticket_deep_get(session, 51080, article_id=None, all_attachments=False, sysconfig=sc)
      assert d["TicketID"] == 51080 and d["DynamicField_GiTag"] == "a" and "TimeUnit" in d
      assert len(d["Articles"]) == 2 and d["Article"] == {}            # 2 articles, no id -> empty
      for part in ("CustomerUser", "CustomerCompany", "QueueData", "TypeData", "PriorityData", "ServiceData",
                   "SLAData", "OwnerData", "ResponsibleData", "CreateByData"):
          assert isinstance(d[part], dict)
      flat = json.dumps(d)
      assert "UserPw" not in flat and "Password" not in flat and "::" not in "".join(_all_keys(d))
  async def test_single_article_becomes_article_with_attachments(url_fixture, request) -> None
  async def test_article_id_selects_article(url_fixture, request) -> None
  async def test_all_attachments_base64_including_plain_and_html(url_fixture, request) -> None
  async def test_prepare_request_omitted_and_base64_fields(url_fixture, request) -> None:
      # SysConfig (sysconfig_modified) OmittedFields {"SendTicket": "Body; Attachment"},
      # Base64EncodedFields {"SendTicket": "Title"} -> applied to Ticket and Event; other invoker names untouched
  def test_cleanup_regex() -> None:
      for k in ("UserPw", "Config::X", "AuthBackend", "OAuth2Token", "SecretKey", "UserConfigItem"):
          assert DEEP_GET_CLEANUP_RE.search(k)
  @pytest.mark.parametrize(...)   # one row per event type
  def test_event_data(...) -> None:
      # TicketCreate -> {"TicketID": 5}; ArticleCreate -> + ArticleID; TicketDynamicFieldUpdate_GiTag -> + FieldName
      # (+ Value/OldValue when present in the history name); TicketMerge -> + MainTicketID; never "OldTicketData"
  def test_util_ports() -> None    # data_structure_remove_elements / base64_deep_encode on nested dicts and lists
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_deep_get.py tests/test_gi_event_data.py tests/test_gi_util_ports.py`
- [ ] **Step 4: Implement** G9 (key IN-lists only; no cross-schema joins; reuse wave-1 builders, add the smallest flags needed).
- [ ] **Step 5: Run → PASS** (+ `tests/test_compat_ticket_get*.py` unchanged), lint gates, commit `feat(gi): Ticket::Generic request data (TicketDeepGet port, omitted/base64 fields, event data)`.

### Task 10: `Ticket::Generic` HandleResponse — response actions through the write services (backend)

**Files:**
- Modify: `backend/src/tiqora/gi/invoker_generic.py` (`handle_response`, `handle_error`)
- Test: `backend/tests/test_gi_handle_response.py` (both dialects, band 51160–51219)

**Interfaces:**
- Consumes: write services listed in G10b, `history_add`, `enqueue_invocation` (Task 11 provides it — implement `handle_response` with an injected `enqueue: Callable[[GiInvokePayload], Awaitable[None]]` so this task does not depend on Task 11).
- Produces: `async def handle_response(session, *, ticket_id: int | None, success: bool, data: Any, error_message: str | None, debugger: GiDebugger, enqueue: Callable[..., Awaitable[None]], session_factory, sysconfig) -> dict[str, Any]` (Znuny result shape) and `handle_error(...)`.

- [ ] **Step 1: Read** `Invoker/Ticket/Generic.pm` `HandleResponse`/`HandleError` (above) and each called Znuny function's accepted parameters (`TicketQueueSet` with `Queue` name, `TicketOwnerSet` `NewUser` login, …).
- [ ] **Step 2: Failing tests** — one test per key, each asserting the Znuny-equivalent history row type and `change_by = 1`:
  ```python
  async def test_title_state_queue_owner_actions(url_fixture, request) -> None:
      res = await handle_response(session, ticket_id=51160, success=True, data={
          "OTRS_TicketTitleUpdate": "Neu", "OTRS_TicketStateSet": "open", "OTRS_TicketQueueSet": "GI51Queue",
          "OTRS_TicketOwnerSet": "gi51agent"}, ...)
      assert res["Success"] == 1
      # ticket title/state/queue/owner changed; history types TitleUpdate, StateUpdate, Move, OwnerUpdate, create_by 1
  async def test_dynamic_field_set_case_insensitive_and_unknown_skipped(url_fixture, request) -> None
  async def test_article_add_with_base64_attachment(url_fixture, request) -> None
  async def test_history_add_defaults(url_fixture, request) -> None
  async def test_ticket_create_supported_keys_and_rejects_unknown(url_fixture, request) -> None
  async def test_async_invoker_execution_enqueues(url_fixture, request) -> None
  async def test_article_create_event_unsupported_is_error(url_fixture, request) -> None
  async def test_failed_action_rolls_back_this_response(url_fixture, request) -> None
      # "OTRS_TicketTitleUpdate" ok then "OTRS_TicketQueueSet": "NoSuchQueue" -> error message Znuny text,
      # title NOT changed (rollback), debugger error row written (own session)
  async def test_response_error_without_message(url_fixture, request) -> None
  async def test_non_dict_data_passes_through() -> None
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_handle_response.py`
- [ ] **Step 4: Implement** G10b. All writes in one session, `flush` per call, one `commit()` at the end, `rollback()` on the first failure. Names → ids via the same lookups the write services/process actions use (reuse `process/engine.py` helpers if they are public; otherwise copy the minimal lookup with a comment).
- [ ] **Step 5: Run → PASS**, lint gates, commit `feat(gi): Ticket::Generic response actions via the ticket write services`.

### Task 11: Requester pipeline (backend)

**Files:**
- Create: `backend/src/tiqora/gi/requester.py` (G10)
- Test: `backend/tests/test_gi_requester.py` (both dialects, band 51220–51269; transports via `MockTransport`)

**Interfaces:**
- Consumes: Tasks 2, 3, 5, 6, 7, 8, 9, 10; `run_mapper`.
- Produces: `RequesterOutcome`, `run_requester` (G10).

- [ ] **Step 1: Read** `Requester.pm` `Run` + `_HandleError` (above) and `scripts/test/GenericInterface/Requester.t`.
- [ ] **Step 2: Failing tests.**
  ```python
  async def test_happy_path_debug_sequence(url_fixture, request) -> None:
      # webservice 51220 (REST, threshold debug), invoker SendTicket, ticket 51221
      out = await run_requester(..., invoker="SendTicket", data={"TicketID": 51221, "Event": "TicketCreate"}, asynchronous=True, past=None)
      assert out.success
      subjects = [r.subject for r in rows(out.communication_id)]
      assert subjects[:4] == ["Communication sequence started", "Using invoker 'SendTicket'",
                              "Outgoing data before mapping", "Outgoing data after mapping"] or …  # exact list pinned
      body = json.loads(mock.requests[0].content)
      assert set(body) == {"Ticket", "Event"} and body["Event"]["Event"] == "TicketCreate"
  async def test_outbound_and_inbound_mapping_with_data_include(url_fixture, request) -> None
      # XSLT outbound reading DataInclude/RequesterRequestInput; Simple inbound renaming a response key
  async def test_transport_failure_runs_error_handling_and_reschedules(url_fixture, request) -> None
      # 503 + RequestRetry module -> outcome.reschedule.execution_time == now + 60 s; debugger notice row
  async def test_mapping_error_stage_and_no_send(url_fixture, request) -> None
  async def test_unsupported_invoker_type_is_error_without_send(url_fixture, request) -> None
  async def test_response_actions_run_after_inbound_mapping(url_fixture, request) -> None
  async def test_dry_run_sends_nothing_and_masks(url_fixture, request) -> None
  async def test_before_send_called_exactly_once_before_network(url_fixture, request) -> None
  async def test_job_payload_and_result_contain_no_secret(url_fixture, request) -> None
      # BasicAuth password, proxy password and OAuth2 token absent from: outcome (repr), all debugger rows, logs (capture_logs)
  async def test_replay_column_written_without_secrets(url_fixture, request) -> None
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_requester.py`
- [ ] **Step 4: Implement** G10. The entry's `replay` column is set by the requester on the first debugger row (`{"invoker", "data", "asynchronous"}` — event data only).
- [ ] **Step 5: Run → PASS**, lint gates, commit `feat(gi): requester pipeline (prepare, map, send, map, handle, error handling)`.

### Task 12: Job handlers `core.gi_invoke` / `core.gi_invoke_sync` with rescheduling (backend)

**Files:**
- Create: `backend/src/tiqora/gi/jobs.py` (G11)
- Modify: job handler registration (where `gi_mail` is registered — `extensions/core.py` or `jobs/handlers/__init__.py`), `frontend` not touched (title key lands in Task 17)
- Test: `backend/tests/test_gi_jobs.py` (both dialects, band 51270–51309)

**Interfaces:**
- Consumes: Task 11, `enqueue_job(due_at=)`, `JobContext.checkpoint`, `load_gi_webservice`.
- Produces: `GI_INVOKE_KIND`, `GI_LANE`, `GI_SYNC_LANE`, `GiInvokePayload`, `enqueue_invocation` (G11).

- [ ] **Step 1: Failing tests** (run the handler through the real runner like `tests/test_jobs_gi_mail*.py` does):
  ```python
  async def test_success_result_and_lane(url_fixture, request) -> None:
      job = await enqueue_invocation(session, payload=GiInvokePayload(webservice_id=51270, invoker="SendTicket",
                 data={"TicketID": 51271, "Event": "TicketCreate"}, asynchronous=False, origin="event", history_id=1),
                 created_by=None)
      assert job.kind == "core.gi_invoke_sync"
      # run -> status succeeded, result {"status": "ok", "communication_id": …}
  async def test_reschedule_enqueues_new_job_with_past_execution_data(url_fixture, request) -> None:
      # event-origin job WITH history_id (so it carries a dedupe_key); 503 + RequestRetry start 60 ->
      # first job succeeded {"status": "rescheduled"}; a NEW queued job (different id, dedupe_key None) with
      # due_at ≈ now + 60 s and payload.past_execution.RetryCount == 1; running it again (forced due) with 503 -> RetryCount 2
  async def test_final_failure_fails_job_with_redacted_message(url_fixture, request) -> None
  async def test_interrupted_before_checkpoint_requeued(url_fixture, request) -> None
  async def test_interrupted_after_checkpoint_not_resent(url_fixture, request) -> None
      # simulate interruption after ctx.checkpoint("sending") -> job failed with the German "unterbrochen" text;
      # MockTransport saw at most one request; no new job enqueued
  async def test_invalid_webservice_fails_cleanly(url_fixture, request) -> None
  async def test_future_task_time_diff_fallback(url_fixture, request) -> None   # decision without execution time -> now + 300 s
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_jobs.py`
- [ ] **Step 3: Implement** G11.
- [ ] **Step 4: Run → PASS** (+ `tests/test_jobs*.py`), lint gates, commit `feat(gi): invoker jobs with RequestRetry rescheduling and send checkpoint`.

### Task 13: Event follower, takeover flag, Daemons notices (backend)

**Files:**
- Create: `backend/src/tiqora/gi/follower.py`, `backend/src/tiqora/gi/takeover.py`; extend `backend/src/tiqora/gi/events.py` (`match_invokers`)
- Modify: `backend/src/tiqora/domain/settings_store.py` (G12 keys), `backend/src/tiqora/config.py` (`gi_requester_interval_seconds`, `gi_requester_gap_hold_seconds`), `backend/src/tiqora/worker/services.py`, `backend/src/tiqora/worker/__main__.py`, `backend/src/tiqora/api/v1/admin/daemons.py`, `backend/src/tiqora/api/v1/admin/schemas.py` (`DaemonServiceOut.notices`), `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client` build output
- Test: `backend/tests/test_gi_follower.py` (both dialects, band 51310–51369), `backend/tests/test_gi_takeover.py` (both dialects, same band), update the Daemons API tests for the new slugs and `notices`

**Interfaces:**
- Consumes: `events_from_history`, `GapTracker`, `autoincrement_step`, Tasks 2, 4, 9, 12.
- Produces: `run_gi_requester_tick`, `match_invokers(ws_list, event, deep_get_loader) -> list[GiInvokePayload]`, `znuny_gi_handler_active`, `webservices_with_event_invokers`, `daemon_notices`, the G12 settings keys and services.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_flag_off_moves_cursor_without_sending(url_fixture, request) -> None:
      # webservice with TicketCreate invoker; create ticket (history rows) -> tick -> no jobs, cursor == MAX(id)
  async def test_first_enabled_tick_starts_at_head(url_fixture, request) -> None
  async def test_reenable_after_gap_does_not_replay(url_fixture, request) -> None:
      # enabled tick (marker written) -> disable WITHOUT a tick in between -> create tickets -> enable via the
      # daemons API -> tick -> zero jobs; same with a stale last_enabled_tick marker and no API call -> zero jobs
  async def test_event_fires_matching_invokers_once(url_fixture, request) -> None:
      # enable; create ticket via create_ticket -> tick -> one job core.gi_invoke (Asynchronous 1) with
      # data {"TicketID": t, "Event": "TicketCreate"}, history_id = NewTicket row id; second tick -> no new job
  async def test_sync_invoker_goes_to_sync_lane_in_order(url_fixture, request) -> None
  async def test_condition_filters(url_fixture, request) -> None
      # Condition Queue == "GI51Queue": ticket in other queue -> no job; in that queue -> job
  async def test_enqueue_and_cursor_commit_atomically(url_fixture, request, monkeypatch) -> None:
      # make set_setting raise after enqueue -> no job rows remain and the cursor is unchanged; next tick enqueues once
  async def test_gap_is_held_then_delivered(url_fixture, request) -> None
      # pattern from tests/test_plugins_event_dispatch.py: hole in ids -> cursor stops before it until hold elapses
  async def test_broken_webservice_does_not_stop_others(url_fixture, request) -> None
  async def test_dynamic_field_event_name(url_fixture, request) -> None   # TicketDynamicFieldUpdate_GiTag fires
  async def test_unsupported_and_mapping_error_invokers_skipped(url_fixture, request) -> None
  # test_gi_takeover.py
  async def test_znuny_handler_detection(url_fixture, request) -> None
      # sysconfig_default row for ZNUNY_GI_EVENT_HANDLER valid -> True; sysconfig_modified is_valid=0 override -> False
      # (match the precedence SysConfig.get implements)
  async def test_notices_both_directions(url_fixture, request) -> None
  async def test_daemons_api_lists_gi_services_with_notices(url_fixture, request) -> None
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_gi_follower.py tests/test_gi_takeover.py`
- [ ] **Step 3: Implement** G12; wire `_interval_loop("gi_requester", …)` with a process-level `GiWebservices()` and the tracker.
- [ ] **Step 4: Regenerate openapi** (Global Constraints command) — `DaemonServiceOut.notices` has a default → required in TS: fix `frontend` fixtures/mocks that build `DaemonServiceOut` (`DaemonsPage.test.tsx`, demo daemons mock, `e2e/fixtures/mock-api.ts`) by adding `notices: []` so `pnpm lint` stays green.
- [ ] **Step 5: Run → PASS** (+ `tests/test_admin_daemons*.py`, `tests/test_worker_services*.py`; `cd frontend && pnpm lint && pnpm test --run`), lint gates, commit `feat(gi): ticket event follower firing invokers behind the gi_requester takeover flag`.

### Task 14: Debugger for provider traffic (backend)

**Files:**
- Modify: `backend/src/tiqora/api/compat/provider.py` (`run_provider(..., debugger=None)`), `backend/src/tiqora/api/compat/router.py` (build the debugger for webservice routes, REST and SOAP)
- Test: `backend/tests/test_compat_provider_debugger.py` (both dialects, band 51370–51389)

**Interfaces:**
- Consumes: `GiDebugger`, `DbDebugSink`, `parse_debugger`, `client_ip`.
- Produces: provider rows in `tiqora_gi_debug_*` with `communication_type="Provider"`, `remote_ip`, `name=operation`.

- [ ] **Step 1: Read** `Kernel/GenericInterface/Provider.pm` (`Debug`/`Error` summaries) and pin the exact list.
- [ ] **Step 2: Failing tests.**
  ```python
  async def test_rest_provider_call_logged_with_client_ip(url_fixture, request) -> None:
      # webservice 51370 (Debugger threshold debug) with SessionCreate; POST via ASGI client with
      # X-Forwarded-For 203.0.113.9 from a trusted proxy -> one entry, remote_ip "203.0.113.9",
      # subjects == Znuny's Provider.pm sequence; "Password" value masked in content
  async def test_threshold_error_logs_only_faults(url_fixture, request) -> None
  async def test_operation_fault_keeps_trail_after_rollback(url_fixture, request) -> None
  async def test_canonical_routes_not_logged(url_fixture, request) -> None
  async def test_soap_provider_logged(url_fixture, request) -> None
  ```
- [ ] **Step 3: Run → FAIL**, then implement G13.
- [ ] **Step 4: Run → PASS** (+ `tests/test_compat_*.py` — the whole compat suite stays green), lint gates, commit `feat(compat): provider requests in the GenericInterface debugger`.

### Task 15: `ExecuteInvoker` process action (backend)

**Files:**
- Modify: `backend/src/tiqora/process/engine.py` (`_action_execute_invoker`, `_ACTION_HANDLERS`, module docstring), `backend/src/tiqora/gi/loader.py` (`load_gi_webservice_by_name`)
- Test: `backend/tests/test_process_execute_invoker.py` (both dialects, band 51390–51409)

**Interfaces:**
- Consumes: `execute_transition_action`, `enqueue_invocation`.
- Produces: G14.

- [ ] **Step 1: Read** `Kernel/System/ProcessManagement/TransitionAction/ExecuteInvoker.pm` and `TransitionAction/Base.pm` `_CheckParams`/`_OverrideUserID`.
- [ ] **Step 2: Failing tests.**
  ```python
  async def test_execute_invoker_enqueues_in_caller_transaction(url_fixture, request) -> None:
      action = TransitionActionConfig.from_dict({"Module": "Kernel::System::ProcessManagement::TransitionAction::ExecuteInvoker",
          "Config": {"Webservice": "GI51Crm", "Invoker": "SendTicket", "Asynchronous": "0", "Note": "<OTRS_TICKET_Title>"}})
      skipped = await execute_transition_action(session, action=action, ticket_id=51391, ...)
      assert skipped is None
      # before commit: job visible in this session; after rollback: no job. After commit: one job core.gi_invoke with
      # data {"Origin": "TransitionAction", "TransitionActionConfig": {..., "Note": "<title>"}, "TicketID": 51391}
  async def test_unknown_webservice_or_missing_invoker_is_noop(url_fixture, request) -> None
  async def test_dynamic_field_user_id_override(url_fixture, request) -> None
  async def test_runs_without_takeover_flag(url_fixture, request) -> None
  ```
- [ ] **Step 3: Run → FAIL**, implement G14, remove `ExecuteInvoker` from the deferred list in the engine docstring.
- [ ] **Step 4: Run → PASS** (+ `tests/test_process_*.py`), lint gates, commit `feat(process): ExecuteInvoker transition action queues the GenericInterface invoker`.

### Task 16: Admin API — webservices, egress settings, debugger, resend, dry run (backend)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/webservices.py`, `backend/src/tiqora/api/v1/admin/webservices_schemas.py` (G15)
- Modify: `backend/src/tiqora/api/v1/admin/__init__.py` (router), `packages/api-client/openapi.json`, `docs/api/openapi.json`, api-client build
- Test: `backend/tests/test_admin_webservices_api.py` (both dialects, band 51410–51469)

**Interfaces:**
- Consumes: Tasks 2, 3, 6, 12; `AdminUser`, `DbSession`, `lock_user_job_quota`, `count_active_jobs`.
- Produces: G15 routes and models exactly.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_list_includes_invalid_and_problem(url_fixture, request) -> None
  async def test_webservice_detail_masks_secrets(url_fixture, request) -> None:
      # BasicAuthPassword "gi51-secret", ProxyPassword "gi51-proxy", OAuth2 config id -> response JSON contains none of
      # the secrets, contains auth_type "BasicAuth", has_proxy true; X509 without TIQORA_GI_CERT_DIRS -> warning text
  async def test_egress_put_validates_and_persists(url_fixture, request) -> None   # 422 on "127.0.0.0/8"
  async def test_debugger_search_pagination_and_filters(url_fixture, request) -> None
  async def test_debugger_detail_both_sources(url_fixture, request) -> None
  async def test_resend_enqueues_job_and_rejects_znuny_or_provider_entries(url_fixture, request) -> None
  async def test_dry_run_job_never_sends(url_fixture, request) -> None
      # run the job; MockTransport saw nothing; job result has request.url redacted and headers masked
  async def test_quota_limits_resend_and_dry_run(url_fixture, request) -> None
  async def test_non_admin_forbidden(url_fixture, request) -> None
  async def test_unknown_webservice_404(url_fixture, request) -> None
  ```
- [ ] **Step 2: Run → FAIL**, implement G15, regenerate openapi (Global Constraints).
- [ ] **Step 3: Run → PASS**, lint gates, `cd frontend && pnpm lint` (types compile), commit `feat(gi): admin API for webservices, debugger, resend and dry run`.

### Task 17: Frontend — API layer, demo mocks, Webservices list + detail (Übersicht, Ausgehende Aufrufe, Einstellungen), Daemons notices (frontend)

**Files:**
- Create: `frontend/src/lib/webservicesApi.ts`, `frontend/src/demo/giMock.ts`, `frontend/src/routes/admin/WebservicesPage.tsx` (+ `.test.tsx`), `frontend/src/routes/admin/WebserviceDetailPage.tsx` (+ `.test.tsx`), `frontend/src/routes/admin/gi/DryRunDialog.tsx` (+ test)
- Modify: `frontend/src/router.tsx`, `frontend/src/lib/adminSearch.ts`, `frontend/src/routes/admin/DaemonsPage.tsx` (+ test), `frontend/src/demo/mockData.ts` (one line), `frontend/e2e/fixtures/mock-api.ts`, `frontend/src/i18n/locales/de.json`, `en.json` (+ propagation)

**Interfaces:**
- Consumes: G15 `Schemas["WebserviceListOut"]` etc.; the wave-0 job progress component and `GET /api/v1/jobs/{id}` polling hook.
- Produces: routes `/admin/webservices`, `/admin/webservices/$wsId` (tab via search param `tab=overview|invokers|debugger|settings`; the `debugger` tab renders a placeholder until Task 18 — or Task 18 runs right after in the same session).

- [ ] **Step 1: Failing tests** (Vitest + Testing Library, API mocked like `DaemonsPage.test.tsx`):
  - `WebservicesPage.test.tsx`: renders rows from mock; invalid webservice shows "ungültig" and its problem text; banner "Tiqora löst … nicht aus" visible when the daemons mock has `gi_requester` disabled with the `giRequesterOff` notice; row click navigates to detail.
  - `WebserviceDetailPage.test.tsx`: invokers tab shows event chips "sofort"/"im Hintergrund", "Bedingung", the "wird von Tiqora noch nicht ausgelöst" badge; secrets never rendered (mock has none — assert no "password" label); settings tab: entering `127.0.0.1/32` shows the 422 message from the API; saving valid targets calls `PUT …/egress` with the trimmed list; the insecure-TLS switch shows its warning text before saving.
  - `DryRunDialog.test.tsx`: submit → shows job progress → renders method/URL/headers/body from the job result; headers show `***`.
  - `DaemonsPage.test.tsx`: `notices` render under the `gi_requester` row with warning styling; empty `notices` render nothing.
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm test --run src/routes/admin/WebservicesPage.test.tsx src/routes/admin/WebserviceDetailPage.test.tsx src/routes/admin/gi src/routes/admin/DaemonsPage.test.tsx`
- [ ] **Step 3: Implement** G16 parts above, i18n G17 (German first, "Sie"), demo mocks; `node frontend/scripts/propagate-i18n-keys.mjs`.
- [ ] **Step 4: Verify in demo mode** with Playwright (`VITE_DEMO=1`): list → detail → each tab → dry run; screenshot check of dark and light theme; delete `.playwright-mcp`.
- [ ] **Step 5: Run** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; commit `feat(admin): Webservices pages with invokers, egress settings, dry run and daemon notices`.

### Task 18: Frontend — debugger list, entry drawer, "Erneut senden" (frontend)

**Files:**
- Create: `frontend/src/routes/admin/gi/DebuggerList.tsx`, `frontend/src/routes/admin/gi/DebuggerEntryDrawer.tsx` (+ tests)
- Modify: `frontend/src/routes/admin/WebserviceDetailPage.tsx` (debugger tab), `frontend/src/demo/giMock.ts`, `frontend/e2e/fixtures/mock-api.ts`, i18n `de.json`/`en.json` (+ propagation)
- Create: `frontend/e2e/webservices.spec.ts` (mock API flow: open debugger, filter, open entry, resend, see job finished)

**Interfaces:**
- Consumes: G15 debugger endpoints, job polling.
- Produces: the "Protokoll (Debugger)" tab.

- [ ] **Step 1: Failing tests.**
  - `DebuggerList.test.tsx`: filters (Richtung, Quelle, Zeitraum, IP) map to query params; "Weitere laden" passes `before=next_before` and appends without duplicates; level badge colours use token classes; Znuny rows show "Znuny" source and no resend button.
  - `DebuggerEntryDrawer.test.tsx`: content rows collapsible, JSON pretty, copy button copies the content; "Erneut senden" opens the confirm dialog with the exact German text, confirm posts and shows progress; API error shows the message.
- [ ] **Step 2: Run → FAIL**, implement, i18n + propagate.
- [ ] **Step 3: Verify in demo mode** with Playwright (also keyboard: drawer closes on Escape, focus returns to the row); delete `.playwright-mcp`.
- [ ] **Step 4: Run** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` and `pnpm exec playwright test e2e/webservices.spec.ts` → PASS; commit `feat(admin): GenericInterface debugger view with resend`.

### Task 19: Golden tests against the real Znuny peer — payloads, SOAP envelope, retry schedule, provider debugger (golden)

**Files:**
- Create: `tests/golden/gi_sink.py` (stdlib `http.server`: records method, path, query, headers, body to memory; `GET /_requests` returns them as JSON, `DELETE /_requests` clears; `/status/<code>` answers that code), `tests/golden/webservices/GoldenRequesterREST.yml`, `tests/golden/webservices/GoldenRequesterSOAP.yml`, `tests/golden/test_gi_requester.py`
- Modify: `tests/golden/docker-compose.golden.yml` (service `gisink`: `python:3.12-slim` running `gi_sink.py`, port `8199:8080`, on the compose network as `gisink`), `tests/golden/_helpers.py` (`gisink_requests()`, `gisink_clear()`, `znuny_requester_run(ws_name, invoker, data) -> dict` via `znuny_perl_eval` calling `Kernel::GenericInterface::Requester->new->Run(WebserviceID => …, Invoker => …, Data => …)`)

**Interfaces:**
- Consumes: everything above. Tiqora side runs `run_requester` against the golden DB (`golden_session_factory`) with an httpx transport that forwards to `http://127.0.0.1:8199` (the sink) — the SSRF guard is bypassed only through the injected `http_transport` + `resolver` in this test, never by configuration. Znuny side calls `http://gisink:8080`. Hosts differ → compare after normalising `Host`, `Content-Length`, `User-Agent`, ids, ticket numbers, timestamps, `Age`, `UntilTime`, `CommunicationID`.

- [ ] **Step 1: Write** the two YAMLs: REST invoker `SendTicket` (Ticket::Generic, POST `/Ticket/:TicketID`, BasicAuth with example credentials, AdditionalHeaders, MappingOutbound XSLT renaming `Ticket/Title` → `Subject`, ErrorHandling RequestRetry start 60 factor 2 count 3), invoker `GetStatus` (GET with query); SOAP invoker `SendSoap` (NameSpace `http://ws.example.org/Golden`, SOAPAction default, Sort config).
- [ ] **Step 2: Tests** (marked `golden`):
  - `test_rest_payload_parity`: one ticket with two articles + attachment created through Znuny; both sides run `SendTicket` with `{"TicketID": id, "Event": "TicketCreate"}` → sink bodies equal after normalisation; headers equal except Host/User-Agent; Tiqora's debugger rows contain no password while Znuny's `gi_debugger_entry_content` does (documents deviation 4 — assert, do not normalise away).
  - `test_get_query_parity`.
  - `test_soap_envelope_parity`: XML compared after canonicalisation (`lxml` C14N) — namespaces, element order, `SOAPAction` header.
  - `test_retry_schedule_parity`: sink `/status/503`; Znuny `Run` returns `ReSchedule`/`ExecutionTime`/`PastExecutionData`; Tiqora's `RequesterOutcome.reschedule` equal (times compared as deltas from each side's start).
  - `test_condition_parity`: same `Condition` hash; Znuny `Kernel::GenericInterface::Event::Handler->_ConditionCheck(...)` on `_SerializeConfig(TicketDeepGet)` vs Tiqora `condition_check(serialize_config(ticket_deep_get(...)))` for 6 conditions → same booleans.
  - `test_response_actions_parity`: sink answers `{"OTRS_TicketTitleUpdate": "Golden", "OTRS_TicketDynamicFieldSet_GoldenDF": "x"}` → history type sequence on both sides equal (pattern `test_history_diff.py`).
- [ ] **Step 3: Run** `GOLDEN_PEER=znuny-6.5 just golden-run` (or `just golden-up && just golden-seed && GOLDEN=1 just golden-test`). Fix real diffs in the owning module, not in the normaliser; accepted deviations are normalised explicitly with a comment naming the deviation number.
- [ ] **Step 4: Commit** `test(golden): GI requester payload, SOAP envelope, retry and condition parity against Znuny 6.5`.

### Task 20: Docs + full verification

**Files:**
- Modify: `docs/compatibility.md` (GenericInterface: requester supported — transports, auth matrix with JWT/NTLM "nein", RequestRetry, conditions with Module/jq notes, response actions table incl. `OTRS_TicketArticleCreateEvent` "nein", event list incl. non-ticket events "nein"; remove "Requester side / invokers — by design" rows (~412, ~811); process row (~831) loses `ExecuteInvoker`; debugger section: Tiqora tables + combined view; all Deviations 1–13 in plain words), `docs/parallel-operation.md` (table row "GenericInterface invokers | Znuny GI event handler | `gi_requester`"; new section "Taking over GenericInterface invokers": enabling order — 1. Znuny SysConfig `Ticket::EventModulePost###9900-GenericInterface` → invalid, 2. wait until Znuny's scheduler has no `GenericInterface` tasks (`SELECT COUNT(*) FROM scheduler_task WHERE task_type = 'GenericInterface'`), 3. Admin → Dienste → "Ausgehende Webservice-Aufrufe" on; rollback; safety flags; known gap until enabled: Tiqora changes do not reach outbound integrations), `docs/architecture.md` (`tiqora.gi` package, follower loop, job lanes, debugger tables), `docs/deployment.md` (env vars `TIQORA_GI_CERT_DIRS`, `TIQORA_GI_MAX_TIMEOUT_SECONDS`, `TIQORA_GI_MAX_RESPONSE_BYTES`, `TIQORA_GI_REQUESTER_INTERVAL`; mounting Znuny's cert directory read-only into the worker), `docs/api/compat.md` (debugger for provider webservices), user/admin guide page for "Webservices" if an admin guide index exists (follow the dashboards plan's docs task pattern)
- Do NOT edit `site/compare.html` or README claims (the compare page changes with the release, per roadmap).

- [ ] **Step 1: Write the docs.** Plain language; every open-question assumption appears once as "Tiqora verhält sich so: …".
- [ ] **Step 2: Full backend run:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → green; `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`.
- [ ] **Step 3: Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; e2e `pnpm exec playwright test e2e/webservices.spec.ts`.
- [ ] **Step 4: openapi check:** regenerate; expect no diff beyond Tasks 13/16.
- [ ] **Step 5: Grep for leftovers:** `grep -rn "ExecuteInvoker" backend/src/tiqora/process/engine.py` shows only the handler; `grep -rni "requester.*by design" docs` → no hits; `grep -rn "gi51-secret\|tok-gi51" backend/src` → no hits.
- [ ] **Step 6: Commit** `docs(gi): requester, invokers, debugger and the gi_requester takeover`.

---

## Self-review notes (for the executor)

- Every Znuny behaviour claim was read from `znuny-6.5.22/` on 2026-10-11 (`Requester.pm`, `ErrorHandling.pm`, `ErrorHandling/RequestRetry.pm`, `Event/Handler.pm`, `Event/ObjectType/Ticket.pm`/`Article.pm`, `Invoker/Ticket/Generic.pm`, `Transport/HTTP/REST.pm`, `Transport/HTTP/SOAP.pm`, `Debugger.pm`, `Ticket.pm TicketDeepGet`, `SchedulerTaskWorker/GenericInterface.pm`, `TransitionAction/ExecuteInvoker.pm`). Items marked "verify" were not fully traced (REST::Client `buildQuery` order, SOAP::Lite envelope bytes, `Provider.pm` debug summaries, `Util` helpers, history name formats) — pin each with a test before implementing; Task 19 is the final arbiter.
- Wave-1/2 code names were checked in the worktree `tiqora-wave2-dash` on 2026-10-11. If the executor's worktree differs, re-check signatures (`run_provider`, `build_ticket_entry`, `events_from_history`, `GapTracker`) before Task 9/13/14.
- No task performs an outbound call inside an HTTP request handler; no task writes a Znuny table except through the listed write services; no query joins a `tiqora_*` table with a Znuny table (Task 3 search is two queries merged in Python).
- Sizing: Tasks 3, 9 and 13 are the largest (roughly 6–8 h each). If a subagent cannot finish one in a session, split at these seams and commit each half separately: Task 3 → (a) `redact.py` + `debugger.py` (pure) / (b) `debug_store.py` + retention + GDPR (DB); Task 9 → (a) `deep_get.py` / (b) `prepare_request` + `events.py` + Util ports; Task 13 → (a) follower + `match_invokers` / (b) takeover, notices, Daemons API cursor reset, openapi + frontend fixture fix.
- Task 13 also modifies `api/v1/admin/daemons.py` `update_daemon` (cursor reset on enable, G12 step 1a) — covered by `test_reenable_after_gap_does_not_replay`.
- If the product owner answers open question 1 with "write Znuny's debugger tables", only `DbDebugSink` (Task 3) and `search_entries` change; the tables of Task 1 can then be dropped in a later migration.
