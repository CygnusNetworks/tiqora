# Wave 4 — Plugin System Phase 2a (remaining backend extension points + sync hooks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Tiqora plugin can do what a Znuny OPM package does in the remaining backend registries — channel drivers, password auth providers (agent + customer), customer sources, dynamic field types, process transition actions and condition modules, GenericInterface operations/invokers/mappings, report sources and built-in reports, notification transports, inbound/outbound mail filters, template placeholders, ticket number generators — plus synchronous "before" hooks (validate / veto / enrich, strict time budget) on selected agent operations. Every point has an SDK surface, a namespace rule, failure isolation, and a contract test through one fixture plugin on MariaDB and PostgreSQL.

**Architecture:** Each point is an `ExtensionPoint` with `sdk_stable=True` and one `PluginRegistrar` method (staged, all-or-nothing, phase-1 check order). Five of them already exist as core points and are only opened (`core.process.transition_actions`, `core.process.condition_modules`, `core.reporting.sources`, `core.reporting.builtin_reports`) or wrapped (`core.channels` stays core; plugin channels get `core.channels.drivers`); the rest are new points next to the core code they extend. Plugin items are always reached through one choke point per core subsystem (e.g. `_resolve_tag_raw`, `ticket_number_build`, `set_ticket_dynamic_field`, `SmtpMailSender.send`, `TicketWriteService`), and every plugin call goes through `tiqora.plugins.calls.call_plugin` (timeout, exception containment, log). Anything a plugin item stores in Znuny-shaped configuration uses the reserved prefix `Plugin_<short_id>_` so it can never shadow a core or Znuny name. The SDK grows write-capable domain services (`ctx.tickets.create/add_article/set_state/move/set_dynamic_field`, read-only `ctx.customers`) so the points are usable without raw SQL. The ai-worker becomes a plugin host (role `ai-worker`) because it writes articles and sends mail.

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLAlchemy 2 async, Pydantic v2, structlog, aiosmtplib, httpx (`MockTransport` in tests), pytest + testcontainers (MariaDB + PostgreSQL), golden suite against Znuny 6.5 and 7.3 peers; React 19 + TanStack Query/Router, i18next, Vitest + Testing Library, MSW demo mode.

**Spec:** `docs/superpowers/specs/2026-10-08-plugin-system-design.md` — phase-2 row of "Phases": *remaining backend points (channels, auth, customer backends, DF types, process, GI, reporting, notification transports, mail filters, placeholders, number generator), sync hooks*; §3 (stable SDK, no core overrides, ground rules), §4 (extension catalogue rows for these points), §6 (isolation of failures: timeouts, circuit breaker, sync hooks fail open/closed as declared), "Tests" (contract test per point with a fixture plugin, both dialects). The other phase-2 parts (plugin migrations / `tqp_` tables, runtime install, signatures, repository + updates) are the sibling plan **plugins2b**, implemented after this one (`.superpowers/sdd/wave4-plans/2026-10-12-wave4-plugins-install-repos.md`). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (table "Waves", row 4). Builds on phase 1: plan `docs/superpowers/plans/2026-10-10-wave2-plugin-system.md` (Design P0–P15, Deviations 1–15) and its rulings `<repo>/.superpowers/sdd/2026-10-10-wave2-plugin-system/rulings.md` — every ruling stays binding (R-REG-NAMES check order, R-TICK-CONTEXT, R-BOOT-ANYERR, R-MCP-PERMS, R-TRUST-WORDING, R-SHORTID-RESERVED, R-TS-REQUIRED-DEFAULTS, R-GERMAN-SIE, R-TESTS, R-STATUS-ROLES …).

**Scope marker:** backend Tasks 1–19, frontend Tasks 20–21, golden Task 22, docs + verification Task 23. **No migration** in this plan (every state fits existing tables; see D1.6).

---

## Open questions for the product owner

Every item is decided conservatively and marked **Assumption:** where it is used. None blocks Task 1.

1. **Write-capable SDK services belong to this plan** (channels, GI operations and process actions are useless without them); plugins2b adds none. Writes with `as_agent` go through `TicketWriteService` (permissions + sync hooks); writes without it run as system user 1 through the module-level write functions (no permission check, no sync hooks). (D3)
2. **Sync hooks fire only on agent-initiated operations** (`TicketWriteService`: UI, REST `/api/v1`, MCP, AI paths that use the service) — never on Znuny writes, the inbound mail pipeline, GI provider operations, process transition actions, or plugin system writes. Inbound mail has its own point (mail filters). (D4)
3. **Sync-hook operations in phase 2a:** `ticket.create`, `article.create`, `ticket.queue_move`, `ticket.state_change`, `ticket.dynamic_fields_update`. Only `ticket.create` may enrich (title, priority, type, dynamic fields); all five may veto. (D4)
4. **A tripped fail-closed sync hook keeps vetoing** ("Erweiterung „…“ ist angehalten; Vorgang abgelehnt") until an admin presses "Erneut versuchen" or switches the plugin off — a closed hook exists to enforce a rule. A tripped fail-open hook is skipped. (D4)
5. **The ai-worker loads plugins** (new role `ai-worker`): it writes articles, sends mail and renders templates, so number generators, placeholders, outbound filters and DF types must apply there too. (D16)
6. **Auth providers are a password-chain slot only:** after Tiqora's own password check and the LDAP chain, a plugin may confirm `login + password` and name an **existing, valid** account. No account creation (JIT), no SSO redirect flows, no 2FA replacement (2FA still applies). (D12)
7. **Customer backends are "lookup + take over", not live backends:** a plugin source is asked on explicit agent search/import and (only when the plugin opts in) on an unknown inbound mail sender; a hit is written once into `customer_user`/`customer_company` through the existing write services. Rows are never updated automatically later; no virtual `customer_user` table (80+ modules read it directly). (D13)
8. **Plugin DF types are stored as a Znuny base type** (`Text`, `Dropdown`, …) with the plugin type in the field's config YAML (`TiqoraPluginType`); the agent UI uses the base type's input; there is no plugin frontend widget slot in this plan. Znuny keeps working with such fields (it ignores the extra key; editing the field in Znuny's admin drops it). A plugin validator that crashes (as opposed to rejecting a value) does not block saving. (D8)
9. **Plugin ticket number generators are selected in Tiqora** (`tiqora_settings` key `tickets.number_generator`), not in Znuny's SysConfig `Ticket::NumberGenerator` (Znuny could no longer create tickets). Tickets Znuny creates in parallel keep Znuny's scheme; follow-up detection accepts the numbers of every registered plugin generator and Znuny's. (D7)
10. **Report export formats are not opened** (the `Literal["xlsx","csv","pdf"]` stays); plugins get report sources and built-in reports. A plugin report source reads either Znuny tables or its own `tqp_<short_id>_*` tables — never `tiqora_*` tables and never a mix of Znuny and plugin tables (collation 1267). (D15)
11. **Outbound mail filters may only add `X-…` headers or veto**, never change recipients, subject or body (S/MIME/PGP signatures and stored articles must stay identical to what was sent). A veto is reported like a permanent SMTP rejection (550). (D9)
12. **Inbound mail filters are fail-open:** a crashing or slow plugin filter never drops a mail; post filters run after the mail's transaction committed. (D9)
13. **Plugin notification transports write no ticket history** and get the rendered subject/body plus the recipient; how to address the recipient (Slack id, phone number) is the plugin's job. The notification engine stays behind `daemon.notifications.enabled` (default off). (D10)
14. **Plugin channels reply "send then store"** with a 20 s default and 30 s maximum driver timeout inside the request (nginx 90 s); the communication channel row is registered like the WebChat row (`Kernel::System::CommunicationChannel::Internal`, package `Tiqora`) so Znuny can read the articles. (D11)
15. **Not in this plan (not in the spec's phase-2 list):** ACL/permission modules, dashboard data providers (backend), AI tools/context providers/PII recognisers, search index enrichers, webhook event types, feature/permission keys, ticket title/subject builders, frontend DF widget slot, report export formats, CMDB extension points. *Question:* should `core.dashboard.providers` be opened now? It is a one-line `sdk_stable` flip plus a namespace rule (~2 h), but no task needs it.
16. **Placeholder syntax** is `<TIQORA_PLUGIN_<SHORTID>_<NAME>>` (the `<OTRS_PLUGIN_…>` spelling also resolves); in Znuny such a tag renders as `-` (unknown `OTRS_` tag) or stays raw (`TIQORA_`), documented. (D6)

---

## Global Constraints

- **Where:** the worktree the controller assigns (a new worktree off `roadmap-wave1`; this plan runs in parallel with itsm1, cmdb, scim; plugins2b starts after this plan is merged). Never touch `<repo>` (main checkout) unless it is the assigned worktree; never commit to main; never push (a push to `origin main` deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Use `rtk git`; stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index); check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. zsh has `noclobber`: overwrite with `>|` or the Write tool.
- **Only made-up example data:** `example.org`, `example.net`, `.test` domains; documentation IPs `192.0.2.0/24`, `198.51.100.0/24`, `2001:db8::/32`; phone numbers `+49 30 555 0100`–`0199` style fictional ranges. Never real names, mail addresses or customer data.
- **Backend tests:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit — all three, `pytest` green means nothing on its own: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk`. New DB test modules clean up their own rows and are never added to `backend/tests/db_leak_baseline.txt`.
- **Both dialects:** every test that touches SQL runs on MariaDB AND PostgreSQL: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern `backend/tests/test_admin_api.py`).
- **Write only `tiqora_*` tables, except through the existing Znuny write services** (`tiqora.domain.ticket_write_service`, `tiqora.domain.dynamic_field_values`, `tiqora.domain.user_provisioning`, `tiqora.znuny.history`, the new `tiqora.domain.customer_company_service` extracted from existing route code in Task 16). Plugin code writes Znuny tables only through the SDK services of D3. Never change Znuny DDL.
- **Never JOIN `tiqora_*` tables with Znuny tables** (prod MariaDB mixes `general_ci`/`unicode_ci` → error 1267): load keys from one side, then query the other with an `IN` list. Integer joins inside Znuny tables are fine. The same rule is enforced on plugin report sources (D15).
- **No `db.begin()` after autobegin:** a session that already executed a statement is in a transaction — use `commit()`/`rollback()`; savepoints via `session.begin_nested()` are allowed (used in D5).
- **No long synchronous endpoints:** production nginx cuts `/api/` after 90 s; nothing in a request may exceed ~60 s. Every plugin call inside a request has a hard timeout far below that (sync hooks 5 s total, auth chain 20 s, channel send 30 s, GI operation 55 s, customer source 10 s); anything longer belongs in a plugin job (phase 1 `job_handler`) + polling.
- **Migrations:** this plan adds none. If an implementer finds one unavoidable, it takes the **next free number at implementation time** (files currently run to `20261010_0076`, while the primary head is `20261010_0073` — check `cd backend && uv run alembic heads`, never trust ids written here), follows skill `alembic-two-chain-new-head-forks-graph` and R-MIGRATION-SERIAL, and the controller is told before writing it.
- **Znuny golden peers 6.5 and 7.3** exist under `tests/golden` (`peers.yaml`, `README-multi-peer.md`); Task 22 runs on both.
- **Znuny reference source** (6.5 core) at `<repo>/znuny-6.5.22/` (read-only).
- **Perl-truthy:** Znuny SysConfig/YAML checkbox values are strings (`'0'` is false): `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- **Timestamps** written into `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- **OpenAPI:** after any API model/route change regenerate both files and the TS schema: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build` (skill `tiqora-openapi-regen-or-ci-wipes-schema`); `head -c 40 packages/api-client/openapi.json` must start `{"components"`; never hand-edit `schema.d.ts`. A response field WITH a default becomes required in TS — fixtures, `frontend/src/demo/mockData.ts` and `frontend/e2e/fixtures/mock-api.ts` must include it (R-TS-REQUIRED-DEFAULTS). Plugin HTTP routes never appear in the core OpenAPI document.
- **Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc); **never** run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). German UI strings address the user with **"Sie"**. Colour tokens only via `themeColor()`. API wrappers are hand-written `frontend/src/lib/*Api.ts` with `api.request<T>(…)` + `Schemas["…"]`. Changed screens are checked in demo mode (`VITE_DEMO=1`) with Playwright; delete `.playwright-mcp` before committing. Every endpoint the shells call on every page is answered in `frontend/e2e/fixtures/mock-api.ts`.
- **UI copy:** German first, plain words, what admins recognise: "Erweiterung" (not Plugin), "Prüfung vor dem Speichern" (sync hook), "Versandweg" (transport), "Kanal", "Kundenverzeichnis" (customer source), "Ticketnummer-Schema". No "Hook", "Driver", "Registry" in UI text.
- **Plugin surface:** `tiqora_sdk` (+ its submodules' `__all__`) and `@tiqora/plugin-sdk` are owned by this plan in wave 4. Every surface change is in the snapshot tests (`backend/tests/test_tiqora_sdk_surface.py`, `frontend/src/plugins/runtime/runtime.test.ts`). `SDK_VERSION` becomes `0.2.0` in Task 1 (Python and npm together) and is not bumped again by later tasks of this plan.
- **Seed ids** only in this plan's band **55000–55499** (sub-bands per task in "Tasks"); confirm free with `grep -rnoE "\b55[0-4][0-9]{2}\b" backend/tests` before seeding (free on 2026-10-12 in the read worktree; other wave-4 plans pick bands in parallel — shift inside the band if needed). Seeded logins/names contain `pp55`. Autoincrement rows created by services (tickets, articles, history) are removed with `tests._row_cleanup.snapshot_marks` / `delete_above_marks` (pattern `backend/tests/_gi_fixtures.py`).

## Review Focus

1. **A sync hook that hangs, raises, vetoes or is tripped** → the agent action answers within 5 s: a veto or a failing fail-closed hook gives 422 with the plugin's German message and **nothing was written or sent** (no article row, no SMTP/Telegram/channel send, no history); a failing fail-open hook is skipped and the action succeeds; five failures in a row trip the hook and it shows "angehalten" on the plugin page. → Task 4 (`test_timeout_fail_open_skips`, `test_fail_closed_vetoes`, `test_budget_shared_across_hooks`, `test_trip_after_five_and_reset`), Task 5 (`test_veto_before_email_send`, `test_veto_writes_nothing`).
2. **A plugin password provider that raises, hangs, returns an unknown or invalid login, or is slow** → that provider counts as "no", the chain continues, the whole chain is cut at 20 s, failed attempts still count for the rate limiter, the password never appears in logs, and an account with 2FA still gets the 2FA step. → Task 15 (`test_provider_crash_is_no` — parametrized over raise/hang/unknown_login/invalid_login, `test_chain_budget`, `test_password_never_logged`, `test_2fa_still_required`, `test_rate_limit_counts_plugin_failures`).
3. **Plugin names cannot shadow core or Znuny names** — a plugin registering transition action `TicketStateSet`, GI type `Ticket::TicketCreate`, mapping `Simple`, transport `Email`, placeholder `TICKET_TITLE`, number generator `DateChecksum`, DF type `Text` or channel `email` is rejected at registration with a plain reason and core keeps working. → Task 1 (`test_qualified_names`, `test_core_never_uses_plugin_prefix`), and the registration test of each point task (`test_registration_rules`, `test_action_rejects_core_name`, `test_name_rules`).
4. **A broken plugin ticket number generator** (raises, returns `""`, a 60-char string, a number not matching its own pattern, or always the same number) → tickets are still created, with Znuny's configured generator as fallback, and follow-up mails find tickets of both schemes. → Task 8 (`test_broken_generator_falls_back`, `test_collision_loop_uses_offset`, `test_followup_matches_plugin_and_znuny_numbers`).
5. **No mail is lost or silently changed by a plugin filter**: an inbound pre filter that raises or hangs → the mail is processed as without it; an outbound filter that raises or hangs → the mail is sent unchanged; only an explicit `MailVetoed` stops a send, and the caller sees the same error as a 550 rejection. → Task 10 (`test_pre_filter_crash_mail_still_processed`, `test_pre_filter_can_ignore`), Task 11 (`test_filter_crash_sends_unchanged`, `test_veto_is_permanent_550`, `test_only_x_headers`).

Also pinned (not top five): a report source over `tiqora_*` tables or mixing Znuny and plugin tables is rejected at registration and every plugin source is filtered by the viewer's queues (Task 19 `test_source_scope_enforced`); a channel driver whose send times out stores no article and answers 502 (Task 14); a GI plugin operation that times out returns Znuny's fault shape, never a hung request (Task 17); a customer source hit whose e-mail already belongs to another customer is not created twice (Task 16); a DF validator that crashes does not block saving but one that rejects does (Task 9).

---
## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Existing names are used exactly as they are in code (verified 2026-10-12 in `<repo>`; if one moved, use its new location and say so in the commit body): `Registry`, `ExtensionPoint`, `register_core`, `get_registry`, `set_registry_for_tests` (`tiqora.extensions`); `PluginRegistrar`, `RegistrationRejected`, `NAME_RE`, `_stage`, `_require_declared`, `_check_name`, `_check_timeout`, `_require_async`, `shield_job_run` (`tiqora.plugins.registrar`); `PluginContext`, `PluginHttp`, `PluginTickets`, `TicketSummary`, `minimal_context`, `make_plugin_context` (`tiqora.plugins.context`); `init_registry` (`tiqora.plugins.bootstrap`); `PluginRole`, `PLUGIN_ROLES`, `RuntimeState`, `loaded_plugins`, `is_active`, `summarize_status` (`tiqora.plugins.state`); `ensure_hook`, `load_hooks`, `record_hook_success`, `record_hook_failure`, `reset_plugin` (`tiqora.plugins.store`); `PluginHookOut`, `PluginAdminOut` (`tiqora.api.v1.admin.plugins`); `import_and_register`, `commit_registrar` (`tiqora.plugins.loader`); `install_fixture_plugin` (`backend/tests/plugins/helpers.py`); `TicketWriteService`, `create_ticket`, `add_article`, `move_queue`, `change_state`, `update_dynamic_field`, `update_dynamic_fields_strict`, `TicketIn`, `ArticleIn`, `InvalidInput`, `TicketAccessDenied`, `TicketNotFound`, `_ticket_must_exist` (`tiqora.domain.ticket_write_service`); `_map_exc` (`tiqora.api.v1.tickets`); `set_ticket_dynamic_field`, `normalize_df_values`, `df_display_value`, `load_ticket_dynamic_field`, `DynamicFieldConfig`, `DynamicFieldValueError`, `DF_EDITABLE_TYPES` (`tiqora.domain.dynamic_field_values`); `config_from_yaml` (`tiqora.domain.dynamic_field_config`); `ticket_number_build`, `ticket_create_number`, `ticket_number_counter_add`, `generator_short_name` (`tiqora.znuny.ticket_number`); `get_tn_by_string`, `detect_followup` (`tiqora.znuny.followup`); `expand_placeholders`, `_resolve_tag_raw`, `_resolve_tag`, `_SECRET_RE`, `_PASSWORD_RE`, `PlaceholderContext`, `load_placeholder_context` (`tiqora.channels.email.placeholder`); `apply_filters` (`tiqora.channels.email.filters`); `_process_message_inner`, `process_message`, `_resolve_customer`, `PipelineResult` (`tiqora.channels.email.pipeline`); `SmtpMailSender`, `MailSender`, `smtp_reply_codes`, `is_permanent_smtp_failure` (`tiqora.channels.email.smtp`); `build_outbound_sender` (`tiqora.domain.mail_outbound`); `process_event`, `_render_message`, `_Recipient`, `_resolve_recipients` (`tiqora.worker.notifications`); `CHANNELS`, `ChannelDescriptor` (`tiqora.channels.registry`); `ensure_channel_row`, `ensure_webchat_channel_row`, `EMPTY_TABLES_CHANNEL_DATA`, `resolve_ticket_for_inbound`, `resolve_customer_by_phone` (`tiqora.channels.common`); `TIQORA_ONLY_CHANNELS` (`tiqora.ai.gate`); `ldap_sign_in`, `LdapSignInUnavailable` (`tiqora.identity.ldap_login`); `AuthService.get_user_by_login` (`tiqora.domain.auth`), `CustomerAuthService.get_customer_by_login` (`tiqora.domain.customer_auth`); `SYSTEM_USER_ID` (`tiqora.identity.types`); `CustomerService` (`tiqora.domain.customer_service`); `create_customer_user`, `update_customer_profile` (`tiqora.domain.user_provisioning`); `unusable_password_hash` (`tiqora.domain.password_setup`); `OPERATION_TYPES`, `OperationConfig`, `run_mapper` (`tiqora.api.compat.webservices`); `_dispatch_operation` (`tiqora.api.compat.router`); `run_provider`, `OperationFault` (`tiqora.api.compat.provider`); `_auth_from_params` (`tiqora.api.compat.operations`); `MAPPER_FACTORIES`, `build_mapper`, `Mapper`, `MappingError`, `MappingConfigError` (`tiqora.api.compat.mapping`); `SUPPORTED_INVOKER_TYPES`, `InvokerConfig` (`tiqora.gi.config`); `prepare_request`, `handle_response` (`tiqora.gi.invoker_generic`); `run_requester` (`tiqora.gi.requester`); `REPORT_SOURCES`, `ReportSource`, `Dimension`, `Measure`, `FilterOnlyField`, `CompileContext` (`tiqora.reporting.semantic`); `BUILTIN_REPORTS`, `BuiltinReport`, `ReportCategory` (`tiqora.reporting.catalogue`); `ReportDefinition` (`tiqora.reporting.definition`); `TRANSITION_ACTIONS`, `TransitionActionModule`, `ActionParam`, `ParamKind`, `ZNUNY_MODULE_PREFIX`, `resolve_action`, `module_short_name` (`tiqora.process.actions.registry`); `CONDITION_MODULES`, `ConditionModule`, `ZNUNY_VALIDATION_PREFIX`, `resolve_condition_module` (`tiqora.process.condition_modules`); `execute_transition_action` (`tiqora.process.engine`); `run_ai_worker` (`tiqora.ai.worker`); `get_setting`, `set_setting` (`tiqora.domain.settings_store`); `utc_now` (`tiqora.jobs.service`); frontend `PluginsPage` + `POINT_KEYS` (`frontend/src/routes/admin/PluginsPage.tsx`), `pluginsApi`, `ensurePluginI18n` (`frontend/src/plugins/i18n.ts`), `ReplyDialog` (`frontend/src/components/agent/ReplyDialog.tsx`), `NotificationEventsPage`, `DF_KIND`/`dynamicFieldToDef` (`frontend/src/components/fields/dynamicFields.ts`), `helloPlugin()` (`frontend/src/test/pluginFixtures.ts`).

### D0. File map

Backend (new unless "mod"):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/plugins/naming.py` | `PLUGIN_PREFIX`, `CAMEL_NAME_RE`, `TAG_NAME_RE`, `qualified()`, `is_plugin_name()`, `channel_key()` (D1.1) |
| `backend/src/tiqora/plugins/calls.py` | `CallOutcome`, `call_plugin`, `call_plugin_sync`, `PluginCallBudget` (D1.2) |
| `backend/src/tiqora/plugins/writes.py` | `TicketDraft`, `PluginWriteError`, `PluginPermissionDenied`, ticket write implementation behind `PluginTickets` (D3) |
| `backend/src/tiqora/plugins/customers.py` | `CustomerSummary`, `PluginCustomers` (read-only) (D3) |
| `backend/src/tiqora/plugins/sync_hooks.py` | `SyncOperation`, op dataclasses, `TicketCreatePatch`, `OperationVetoed`, `HookContext`, `SyncHook`, `SYNC_HOOKS`, `run_sync_hooks`, `SyncBreaker` (D4) |
| `backend/src/tiqora/plugins/process_points.py` | `ActionContext`, `TransitionActionRun`, `ConditionRun`, adapters to `TransitionActionModule`/`ConditionModule` (D5) |
| `backend/src/tiqora/plugins/placeholders.py` | `PlaceholderRequest`, `Placeholder`, `PLACEHOLDERS`, `resolve_plugin_placeholder` (D6) |
| `backend/src/tiqora/plugins/number_generators.py` | `NumberRequest`, `TicketNumberGenerator`, `TICKET_NUMBER_GENERATORS`, `SETTING_KEY`, `active_generator`, `build_with_plugin`, `plugin_tn_patterns` (D7) |
| `backend/src/tiqora/plugins/df_types.py` | `DynamicFieldType`, `DYNAMIC_FIELD_TYPES`, `PLUGIN_TYPE_CONFIG_KEY`, `PLUGIN_CONFIG_KEY`, `plugin_type_of`, `validate_plugin_values`, `display_plugin_value` (D8) |
| `backend/src/tiqora/plugins/mail_filters.py` | `InboundMail`, `InboundMailResult`, `OutboundMail`, `MailVetoed`, `OutboundMailVetoed`, `MailFilter`, `INBOUND_MAIL_FILTERS`, `OUTBOUND_MAIL_FILTERS`, `run_inbound_pre_filters`, `run_inbound_post_filters`, `run_outbound_filters` (D9) |
| `backend/src/tiqora/plugins/notification_transports.py` | `NotificationRecipient`, `NotificationMessage`, `TransportContext`, `NotificationTransport`, `NOTIFICATION_TRANSPORTS`, `send_via_plugin_transport` (D10) |
| `backend/src/tiqora/plugins/channels.py` | `ChannelCapabilities`, `OutboundChannelMessage`, `DeliveryResult`, `InboundChannelMessage`, `IngestResult`, `ChannelDriver`, `CHANNEL_DRIVERS`, `ChannelDeliveryError`, `PluginChannels`, `driver_for`, `deliver_plugin_channel_reply`, `ensure_tiqora_channel_row` (D11) |
| `backend/src/tiqora/plugins/auth_providers.py` | `PasswordCheck`, `AuthContext`, `PasswordAuthProvider`, `AUTH_PROVIDERS`, `plugin_password_sign_in` (D12) |
| `backend/src/tiqora/plugins/customer_sources.py` | `ExternalCompany`, `ExternalCustomer`, `CustomerSourceContext`, `CustomerSource`, `CUSTOMER_SOURCES`, `search_sources`, `lookup_by_email`, `materialize`, `CustomerSourceConflict` (D13) |
| `backend/src/tiqora/domain/customer_company_service.py` | `ensure_customer_company` (extracted from `api/v1/admin/customers.py` create route; D13) |
| `backend/src/tiqora/plugins/gi_points.py` | `GiPrincipal`, `GiOperationContext`, `GiOperation`, `GI_OPERATIONS`, `GiMapping`, `GI_MAPPINGS`, `InvokerContext`, `InvokerResponse`, `GiInvoker`, `GI_INVOKERS`, `PLUGIN_HANDLER_PREFIX`, `run_plugin_operation`, `plugin_mapper_factory`, `plugin_invoker` (D14) |
| `backend/src/tiqora/plugins/report_points.py` | `check_plugin_source`, `plugin_builtin_report` (D15) |
| `backend/src/tiqora_sdk/{tickets,hooks,process,templates,fields,mail,notifications,channels,auth,customers,gi,reporting}.py` | SDK submodules, each with an exact `__all__` (D2) |
| mod `backend/src/tiqora_sdk/__init__.py` | `SDK_VERSION = "0.2.0"`; `PluginRegistry` protocol gains the D2 methods |
| mod `backend/src/tiqora/plugins/registrar.py` | one method per point (D2 table) |
| mod `backend/src/tiqora/plugins/context.py` | `PluginTickets` write methods; `PluginContext.customers`, `.channels` (defaults) |
| mod `backend/src/tiqora/plugins/state.py`, `plugins/bootstrap.py` (if it enumerates roles), `ai/worker.py` | role `ai-worker` (D16) |
| mod `backend/src/tiqora/plugins/store.py`, `api/v1/admin/plugins.py` | hook kind `"sync"`; number-generator admin endpoints (D17) |
| mod `backend/src/tiqora/domain/ticket_write_service.py` | sync-hook calls in `TicketWriteService`; plugin channel branch in `TicketWriteService.add_article`; plugin channel name in `add_article` (D4, D11) |
| mod `backend/src/tiqora/api/v1/tickets.py` | `_map_exc`: `ChannelDeliveryError` → 502 (veto is already 422 via `InvalidInput`) |
| mod `backend/src/tiqora/process/actions/registry.py`, `process/condition_modules.py` | `sdk_stable=True` (D5) |
| mod `backend/src/tiqora/channels/email/placeholder.py` | `PlaceholderContext.ticket_id`/`.user_id`; `PLUGIN_` branch in `_resolve_tag_raw` (D6) |
| mod `backend/src/tiqora/znuny/ticket_number.py`, `znuny/followup.py` | plugin generator in `ticket_number_build`; extra patterns in `get_tn_by_string`/`detect_followup` (D7) |
| mod `backend/src/tiqora/domain/dynamic_field_values.py`, `api/v1/admin/dynamic_fields.py`, `api/v1/admin/schemas.py`, `api/v1/reference.py` | plugin DF types (D8) |
| mod `backend/src/tiqora/channels/email/pipeline.py`, `channels/email/smtp.py` | mail filter calls (D9) |
| mod `backend/src/tiqora/worker/notifications.py`, `api/v1/admin/notification_events.py` | plugin transports (D10) |
| mod `backend/src/tiqora/channels/common.py`, `ai/gate.py`, new route module `backend/src/tiqora/api/v1/plugin_channels.py` | channels (D11) |
| mod `backend/src/tiqora/api/v1/auth.py`, `api/portal/auth.py` | plugin password chain (D12) |
| mod `backend/src/tiqora/api/v1/reference.py` (`customer_search`), `api/v1/customers.py`, `api/v1/admin/customers.py`, `channels/email/pipeline.py` (`_resolve_customer`) | customer sources (D13) |
| mod `backend/src/tiqora/api/compat/webservices.py`, `api/compat/router.py`, `api/compat/mapping/__init__.py`, `gi/config.py`, `gi/requester.py` | GI points (D14) |
| mod `backend/src/tiqora/reporting/semantic.py`, `reporting/catalogue.py` | `sdk_stable=True`; plugin report keys (D15) |
| `backend/tests/plugins/fixtures/tq_fixture_points/…` | fixture plugin `org.example.points` (short id `points`) with every point of this plan (D1.5) |
| `backend/tests/_plugin_points.py` | `boot_points_plugin()` test helper (D1.5) |

Frontend (new unless "mod"): mod `frontend/src/routes/admin/PluginsPage.tsx` (capability labels, hook kind "sync", number-generator switch), mod `frontend/src/routes/admin/NotificationEventsPage.tsx` (Versandwege), mod the admin dynamic-field create dialog (plugin types; locate with `grep -rln "field_type" frontend/src/routes/admin`), mod `frontend/src/components/agent/ReplyDialog.tsx` (plugin channels), mod the customer quick-search component (external hits; locate with `grep -rln "customer-search" frontend/src`), mod the report catalogue/builder pages (`ensurePluginI18n` for `plugin.*` keys), new `frontend/src/lib/pluginPointsApi.ts`, mod `frontend/src/plugins/sdkRuntime.tsx` + `packages/plugin-sdk/{package.json,src/index.ts,README.md}` (version 0.2.0 only), mod `frontend/src/demo/mockData.ts`, `frontend/e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` + propagation.

Golden: `tests/golden/test_plugin_artefacts.py`.

### D1. Rules every point follows

#### D1.1 Qualified names (`tiqora/plugins/naming.py`)

```python
PLUGIN_PREFIX: Final = "Plugin_"
CAMEL_NAME_RE: Final = re.compile(r"^[A-Z][A-Za-z0-9]{1,40}$")    # names a plugin passes for Znuny-shaped items
TAG_NAME_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]{1,40}$")        # placeholder names

def qualified(short_id: str, name: str) -> str:
    """"Plugin_<short_id>_<Name>" — e.g. qualified("points", "SetContract") == "Plugin_points_SetContract"."""
def is_plugin_name(value: str) -> bool:      # value (or its last "::"-segment) starts with PLUGIN_PREFIX
def channel_key(short_id: str, name: str) -> str:   # "plugin_<short_id>_<name.lower()>" (ArticleIn.channel value)
```

Where the qualified name appears (the plugin passes only `<Name>`; the registrar builds the rest):

| Point | Stored/referenced as | Example |
|---|---|---|
| transition action | `TransitionActionModule.name`; process config `Module: Kernel::System::ProcessManagement::TransitionAction::<qualified>` | `Plugin_points_SetContract` |
| condition module | `ConditionModule.match` = `ZNUNY_VALIDATION_PREFIX + qualified` | `…TransitionValidation::Plugin_points_IsVip` |
| GI operation / invoker | webservice YAML `Type: <qualified>::<Name>` | `Plugin_points::ContractGet` |
| GI mapping | `MappingInbound/Outbound.Type: <qualified>` | `Plugin_points_Upper` |
| notification transport | `notification_event_item` key `Transports` value | `Plugin_points_Chat` |
| number generator | `tiqora_settings` `tickets.number_generator` value | `Plugin_points_Yearly` |
| DF type | DF config YAML `TiqoraPluginType` | `Plugin_points_Contract` |
| channel | `communication_channel.name` = qualified; `ArticleIn.channel` = `channel_key` | `Plugin_points_Chat` / `plugin_points_chat` |
| placeholder | tag `PLUGIN_<SHORT_ID upper>_<NAME>` | `<TIQORA_PLUGIN_POINTS_GREETING>` |

Core never registers a name starting with `Plugin_` / `plugin_` / `PLUGIN_` on these points (pinned by `test_core_never_uses_plugin_prefix`, Task 1, over `register_core`'s registry). Items keyed `"<plugin_id>:<name>"` (sync hooks, mail filters, auth providers, customer sources) use the phase-1 `NAME_RE` for `<name>`.

#### D1.2 Calling plugin code (`tiqora/plugins/calls.py`)

```python
@dataclass(frozen=True, slots=True)
class CallOutcome[T]:
    ok: bool
    value: T | None
    error: str | None          # f"{type(exc).__name__}: {exc}"[:500], or "Zeitüberschreitung nach {n} s"
    timed_out: bool = False

async def call_plugin[T](plugin_id: str, what: str, fn: Callable[[], Awaitable[T]], *, timeout: float) -> CallOutcome[T]
    # asyncio.wait_for(fn(), timeout); TimeoutError -> timed_out; Exception -> ok False;
    # non-Exception BaseException raised by plugin code (SystemExit, KeyboardInterrupt, GeneratorExit) -> ok False
    #   (same reasoning as shield_job_run); asyncio.CancelledError propagates ONLY when the calling task itself is
    #   being cancelled (asyncio.current_task().cancelling() > 0), otherwise it is a plugin failure;
    # every failure logs structlog "plugin_call_failed" plugin=… what=… error=… timed_out=… (never call arguments)
def call_plugin_sync[T](plugin_id: str, what: str, fn: Callable[[], T]) -> CallOutcome[T]
    # for pure synchronous callbacks (mapping, DF validate/display, number build, condition module); no timeout
    # is possible — documented: these callbacks must be pure and fast

class PluginCallBudget:
    """Shared deadline for a chain of calls (sync hooks: 5 s, auth chain: 20 s, customer sources: 5 s, transition actions: per action 1..10 s and 20 s per dialog submit, a timeout answers 422 and invalidates the session)."""
    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None
    def remaining(self) -> float          # >= 0
    def timeout_for(self, own: float) -> float   # min(own, remaining())
```

#### D1.3 Activation and processes

- An item is used only while its plugin is loaded in this process: `is_active(item.plugin_id)` (phase-1 `loaded_plugins`). Registry lookups go through `get_registry()` (frozen after `init_registry`); never cache items at import time.
- Process roles (which `init_registry(role=…)` must have run for the point to work):

| Point | api | worker | mcp | ai-worker |
|---|---|---|---|---|
| sync hooks (D4) | ✓ | ✓ (plugin tasks writing `as_agent`) | ✓ | ✓ |
| transition actions / conditions (D5) | ✓ | ✓ | ✓ | — |
| placeholders (D6) | ✓ | ✓ | ✓ | ✓ |
| number generators (D7) | ✓ | ✓ | ✓ | ✓ |
| DF types (D8) | ✓ | ✓ | ✓ | ✓ |
| inbound mail filters (D9) | — | ✓ | — | — |
| outbound mail filters (D9) | ✓ | ✓ | ✓ | ✓ |
| notification transports (D10) | — | ✓ | — | — |
| channel drivers (D11) | ✓ | ✓ (plugin tasks may ingest) | ✓ | ✓ |
| auth providers (D12) | ✓ | — | — | — |
| customer sources (D13) | ✓ | ✓ | — | — |
| GI operations / mappings (D14) | ✓ | ✓ (mappings) | — | — |
| GI invokers (D14) | — | ✓ | — | — |
| report sources / built-ins (D15) | ✓ | ✓ | — | — |

Every process loads every enabled plugin; the table only says where the point is exercised.

#### D1.4 Registration checks (phase-1 order, R-REG-NAMES)

`_stage(point, item, check=…)`: (1) `point.sdk_stable`, (2) point key declared in `manifest.capabilities.extension_points`, (3) name/namespace rule, (4) bounds (timeouts, sizes, enum values, async-ness), (5) staged duplicate. Then `commit_registrar` rejects a clash with an already registered key (core or another plugin). Every rejection message is German and names what was checked.

#### D1.5 Fixture plugin and test helper

`backend/tests/plugins/fixtures/tq_fixture_points/` — id `org.example.points`, short id `points`, dist `tq-fixture-points`, `sdk = ">=0.2,<0.3"`. Files: `tiqora-plugin.toml`, `__init__.py` (`plugin = PointsPlugin()`), `plugin.py` (`register()` calls one `_register_<point>(registry)` function per point; each task adds its function and the manifest's `extension_points` entry), `behaviour.py`:

```python
# tq_fixture_points/behaviour.py — test switches and call log (module state; reset() between tests)
MODE: dict[str, str] = {}          # e.g. MODE["hook.ticket.create"] = "veto" | "hang" | "raise" | "enrich" | "ok"
CALLS: list[tuple[str, object]] = []
def reset() -> None: MODE.clear(); CALLS.clear()
```

`backend/tests/_plugin_points.py`:

```python
def boot_points_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, role: PluginRole = "api",
                       manifest_overrides: Mapping[str, Any] | None = None) -> Registry
    # install_fixture_plugin(tmp_path, monkeypatch, "tq_fixture_points", plugin_id="org.example.points");
    # registry = Registry(); register_core(registry); registrar = import_and_register(discovered, make_plugin_context(d));
    # commit_registrar(registry, registrar); registry.freeze(); set_registry_for_tests(registry);
    # set_loaded_plugins([PluginLoadResult("org.example.points", "1.0.0", RuntimeState.ACTIVE, None, contributions)]);
    # finalizer: set_registry_for_tests(None); set_loaded_plugins([]); behaviour.reset()
def points_behaviour() -> ModuleType     # importlib.import_module("tq_fixture_points.behaviour") after boot
```

The spec's "each extension point: contract test with a fixture plugin" is satisfied by one `test_<point>_contract` per task that boots this fixture and exercises the point through the core code path (not by calling the plugin function directly).

#### D1.6 No new tables

- Sync-hook breaker state → existing `tiqora_plugin_hook` with `kind = "sync"` (`VARCHAR(20)` fits; no DDL).
- Number generator choice → `tiqora_settings` key `tickets.number_generator`.
- DF plugin type → Znuny `dynamic_field.config` YAML keys (written through the existing admin DF route).
- Customer sources keep no link table (Assumption 7: rows are taken over once).

### D2. SDK surface (Python 0.2.0)

`tiqora_sdk.__all__` gains nothing at top level except as listed; new names live in submodules (pattern of phase-1 `tiqora_sdk.mcp`), each with an exact `__all__` pinned in `test_tiqora_sdk_surface.py`:

| Submodule | `__all__` (exact) | Re-exported from |
|---|---|---|
| `tiqora_sdk.tickets` | `TicketDraft`, `PluginWriteError`, `PluginPermissionDenied`, `CustomerSummary` | `tiqora.plugins.writes`, `.customers` |
| `tiqora_sdk.hooks` | `SyncOperation`, `HookContext`, `TicketCreateOp`, `ArticleOp`, `QueueMoveOp`, `StateChangeOp`, `DynamicFieldsOp`, `TicketCreatePatch`, `OperationVetoed`, `SyncHandler` | `tiqora.plugins.sync_hooks` |
| `tiqora_sdk.process` | `ActionContext`, `ActionParam`, `ParamKind`, `TransitionActionRun`, `ConditionRun` | `tiqora.plugins.process_points`, `tiqora.process.actions.registry` |
| `tiqora_sdk.templates` | `PlaceholderRequest`, `PlaceholderResolve`, `NumberRequest`, `NumberBuild` | `.placeholders`, `.number_generators` |
| `tiqora_sdk.fields` | `DfBaseType`, `DfValidate`, `DfDisplay` | `.df_types` |
| `tiqora_sdk.mail` | `InboundMail`, `InboundMailResult`, `OutboundMail`, `MailVetoed`, `MailFilterStage`, `MailFilterContext` | `.mail_filters` |
| `tiqora_sdk.notifications` | `NotificationRecipient`, `NotificationMessage`, `TransportContext`, `TransportSend` | `.notification_transports` |
| `tiqora_sdk.channels` | `ChannelCapabilities`, `ChannelContext`, `OutboundChannelMessage`, `DeliveryResult`, `InboundChannelMessage`, `IngestResult`, `ChannelSend` | `.channels` |
| `tiqora_sdk.auth` | `PasswordCheck`, `AuthContext`, `PasswordAuthenticate` | `.auth_providers` |
| `tiqora_sdk.customers` | `ExternalCustomer`, `ExternalCompany`, `CustomerSourceContext`, `CustomerSearch`, `CustomerLookup` | `.customer_sources` |
| `tiqora_sdk.gi` | `GiPrincipal`, `GiOperationContext`, `GiOperationRun`, `InvokerContext`, `InvokerResponse`, `InvokerPrepare`, `InvokerHandle`, `Mapper`, `MappingError` | `.gi_points`, `tiqora.api.compat.mapping` |
| `tiqora_sdk.reporting` | `ReportSource`, `Dimension`, `Measure`, `FilterOnlyField`, `CompileContext`, `ReportDefinition`, `ReportCategory` | `tiqora.reporting.*` |

`PluginRegistry` protocol + `PluginRegistrar` gain exactly these methods (manifest capability key in brackets):

```python
def before(self, operation: SyncOperation, name: str, handler: SyncHandler, *,
           timeout_ms: int = 1000, on_error: Literal["allow", "deny"] = "allow", order: int = 100) -> None   # [core.events.sync_hooks]
def transition_action(self, name: str, run: TransitionActionRun, *, params: Iterable[ActionParam] = (),
                      timeout_seconds: int = 5) -> str            # returns the qualified name   [core.process.transition_actions]
def condition_module(self, name: str, run: ConditionRun) -> str     # returns the full match path  [core.process.condition_modules]
def placeholder(self, name: str, resolve: PlaceholderResolve, *, timeout_ms: int = 500) -> str   # returns the tag  [core.templates.placeholders]
def number_generator(self, name: str, build: NumberBuild, *, label: str, pattern: str,
                     uses_counter: bool = True, date_based: bool = False) -> str   # [core.tickets.number_generators]
def dynamic_field_type(self, name: str, *, label: str, base_type: DfBaseType, validate: DfValidate | None = None,
                       display: DfDisplay | None = None, config_model: type[BaseModel] | None = None) -> str   # [core.dynamic_fields.types]
def mail_filter(self, name: str, stage: MailFilterStage, run: Callable[..., Awaitable[None]], *,
                order: int = 100, timeout_seconds: int = 5) -> None   # [core.mail.inbound_filters] for "pre"/"post", [core.mail.outbound_filters] for "outbound"
def notification_transport(self, name: str, send: TransportSend, *, label: str, timeout_seconds: int = 15) -> str   # [core.notifications.transports]
def channel(self, name: str, *, label: str, capabilities: ChannelCapabilities = ChannelCapabilities(),
            send: ChannelSend | None = None, timeout_seconds: int = 20) -> str   # returns channel_key  [core.channels.drivers]
def password_provider(self, name: str, authenticate: PasswordAuthenticate, *,
                      audience: Literal["agent", "customer", "both"], order: int = 100, timeout_seconds: int = 5) -> None   # [core.auth.password_providers]
def customer_source(self, name: str, *, label: str, search: CustomerSearch, by_login: CustomerLookup,
                    by_email: CustomerLookup | None = None, materialize_on_inbound: bool = False,
                    timeout_seconds: int = 3) -> None   # [core.customers.sources]
def gi_operation(self, name: str, run: GiOperationRun, *, timeout_seconds: int = 30) -> str   # returns "Plugin_<sid>::<Name>"  [core.gi.operations]
def gi_invoker(self, name: str, prepare: InvokerPrepare, *, handle_response: InvokerHandle | None = None,
               timeout_seconds: int = 30) -> str          # [core.gi.invokers]
def gi_mapping(self, name: str, factory: Callable[[dict[str, Any] | None], Mapper]) -> str   # [core.gi.mappings]
def report_source(self, source: ReportSource) -> None                     # [core.reporting.sources]
def builtin_report(self, name: str, *, category: ReportCategory, definition: ReportDefinition,
                   overview: bool = False) -> None                         # [core.reporting.builtin_reports]
```

npm `@tiqora/plugin-sdk` changes only its version (`0.2.0`, `SDK_VERSION` in `src/index.ts` and `frontend/src/plugins/sdkRuntime.tsx`); no frontend slot is added.

### D3. Write-capable domain services

```python
# tiqora/plugins/writes.py
class PluginWriteError(ValueError): ...           # German: "Queue „X“ gibt es nicht", "Status „Y“ gibt es nicht", …
class PluginPermissionDenied(PermissionError): ... # as_agent lacks the queue permission

@dataclass(frozen=True, slots=True)
class TicketDraft:
    title: str
    queue: str                                    # queue name (Znuny full name, e.g. "Raw" or "Support::Level 2")
    customer_user: str | None = None
    customer_id: str | None = None
    state: str = "new"
    priority: str = "3 normal"
    type: str | None = None
    body: str = ""                                # first article; "" = ticket without article
    subject: str | None = None                    # default: title
    content_type: str = "text/plain; charset=utf-8"
    sender_type: Literal["agent", "customer", "system"] = "agent"
    channel: str = "internal"                     # "internal" | "phone" | a channel_key of the SAME plugin (D11)
    from_address: str | None = None
    visible_for_customer: bool = False
    dynamic_fields: Mapping[str, Sequence[str]] = field(default_factory=dict)
```

`PluginTickets` (phase-1 class, `get` unchanged) gains:

```python
async def create(self, draft: TicketDraft, *, as_agent: int | None = None, session: AsyncSession | None = None) -> int
async def add_article(self, ticket_id: int, *, body: str, subject: str = "", sender_type: Literal["agent", "customer", "system"] = "agent",
                      channel: str = "internal", visible_for_customer: bool = False, from_address: str | None = None,
                      content_type: str = "text/plain; charset=utf-8", as_agent: int | None = None,
                      session: AsyncSession | None = None) -> int
async def set_state(self, ticket_id: int, state: str, *, as_agent: int | None = None, session: AsyncSession | None = None) -> None
async def move(self, ticket_id: int, queue: str, *, as_agent: int | None = None, session: AsyncSession | None = None) -> None
async def set_dynamic_field(self, ticket_id: int, name: str, values: Sequence[str], *, as_agent: int | None = None,
                            session: AsyncSession | None = None) -> None
```

Rules: names are resolved to ids with plain `SELECT id FROM <table> WHERE name = :n AND valid_id = 1` (Znuny tables only, no join with `tiqora_*`); unknown → `PluginWriteError`. `as_agent=None` → module-level write functions as `SYSTEM_USER_ID` (no permission check, no sync hooks — the plugin is trusted, documented); `as_agent=<id>` → `TicketWriteService(session, get_session_factory(), SysConfig(session))` methods (permission checks → `PluginPermissionDenied`; sync hooks fire, a veto reaches the plugin as `PluginWriteError` with the veto text — `_translated` wraps every `InvalidInput`, `OperationVetoed` included; a plugin route answers 422 itself, Tiqora maps neither to a status). `PluginTickets(plugin_id)` is built by `make_plugin_context`/`minimal_context` with the owning plugin id; `_check_channel(channel, plugin_id)` accepts `internal`, `phone` and a key of a loaded channel whose driver belongs to that plugin. Anything else (`email`, other core channels, another plugin's channel, or a `PluginTickets()` without id) → `PluginWriteError("Kanal „X“ ist hier nicht erlaubt: eine Erweiterung schreibt nur über „internal“, „phone“ oder einen eigenen Kanal")` — plugins never send mail through `add_article`. On an own channel: `as_agent=None` stores only; `as_agent=<id>` with a visible agent article sends through `deliver_plugin_channel_reply` (`to=None`); `create` stores its first article on the channel and never sends. `session=None` → own session from `get_session_factory()`, `commit()` on success, `rollback()` on any exception; `session` given → caller commits, never `begin()`.

```python
# tiqora/plugins/customers.py
@dataclass(frozen=True, slots=True)
class CustomerSummary:
    login: str; email: str; first_name: str; last_name: str; customer_id: str | None; company_name: str | None; valid: bool
class PluginCustomers:
    async def get(self, login: str, *, session: AsyncSession | None = None) -> CustomerSummary | None    # CustomerService.get_by_login
    async def search(self, q: str, *, limit: int = 10, session: AsyncSession | None = None) -> list[CustomerSummary]
        # CustomerService.quick_search contacts only; DB only (never asks plugin customer sources — no recursion)
```

`PluginContext` gains `customers: PluginCustomers = field(default_factory=PluginCustomers)` and `channels: PluginChannels | None = None` (D11). `make_plugin_context` and `minimal_context` always set `channels=PluginChannels(plugin_id)`, so every context a plugin receives has it; the `None` default only keeps other construction sites (phase-1 tests) compiling. Existing construction sites keep compiling.

### D4. Sync hooks

```python
# tiqora/plugins/sync_hooks.py
SyncOperation = Literal["ticket.create", "article.create", "ticket.queue_move", "ticket.state_change",
                        "ticket.dynamic_fields_update"]
SYNC_OPERATIONS: Final[tuple[SyncOperation, ...]] = get_args(SyncOperation)

@dataclass(frozen=True, slots=True)
class ArticleOp:
    ticket_id: int | None              # None inside ticket.create
    channel: str; sender_type: str; visible_for_customer: bool
    subject: str; body: str; content_type: str
    to: str | None; cc: str | None
    attachment_names: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class TicketCreateOp:
    queue_id: int; queue: str; title: str; state: str; priority: str; type: str | None
    customer_user: str | None; customer_id: str | None
    dynamic_fields: Mapping[str, tuple[str, ...]]
    article: ArticleOp | None
@dataclass(frozen=True, slots=True)
class QueueMoveOp:   ticket: TicketSummary; new_queue_id: int; new_queue: str
@dataclass(frozen=True, slots=True)
class StateChangeOp: ticket: TicketSummary; new_state: str; new_state_type: str; pending_until: datetime | None
@dataclass(frozen=True, slots=True)
class DynamicFieldsOp: ticket: TicketSummary; values: Mapping[str, tuple[str, ...]]

@dataclass(frozen=True, slots=True)
class TicketCreatePatch:               # only honoured for ticket.create; anything else returned -> hook error
    title: str | None = None; priority: str | None = None; type: str | None = None
    dynamic_fields: Mapping[str, Sequence[str]] = field(default_factory=dict)

class OperationVetoed(InvalidInput):   # -> 422 via _map_exc; message German, ≤ 300 chars, shown to the agent
    def __init__(self, message: str, *, plugin_id: str | None = None) -> None

@dataclass(frozen=True, slots=True)
class HookContext:
    plugin: PluginContext
    operation: SyncOperation
    agent_id: int

SyncHandler = Callable[[HookContext, Any], Awaitable[TicketCreatePatch | None]]

@dataclass(frozen=True, slots=True)
class SyncHook:
    key: str                           # "<plugin_id>:<name>"
    plugin_id: str
    operation: SyncOperation
    handler: SyncHandler
    timeout_ms: int                    # 50..3000
    on_error: Literal["allow", "deny"]
    order: int                         # 0..1000
    context: PluginContext | None = None

SYNC_HOOKS: Final[ExtensionPoint[SyncHook]]      # key "core.events.sync_hooks", item_key .key, sdk_stable=True
OPERATION_BUDGET_SECONDS: Final = 5.0
TRIP_AFTER: Final = 5
BREAKER_REFRESH_SECONDS: Final = 30

async def run_sync_hooks(operation: SyncOperation, op: Any, *, agent_id: int,
                         registry: Registry | None = None) -> Any
    # returns op unchanged, or (ticket.create only) a new TicketCreateOp with the merged patches
```

Algorithm of `run_sync_hooks`: hooks of `operation` whose plugin `is_active`, sorted by `(order, key)`; one `PluginCallBudget(5.0)`. Per hook: if tripped (`SyncBreaker`) → `deny` raises `OperationVetoed("Erweiterung „{name}“ ist angehalten; Vorgang abgelehnt")`, `allow` skips. Else `call_plugin(timeout=budget.timeout_for(timeout_ms/1000))`; budget exhausted counts as timeout. Handler raised `OperationVetoed` → re-raise unchanged (a decision, breaker untouched; `plugin_id` filled in). Other failure → breaker `failure()`; `deny` → `OperationVetoed("Prüfung durch Erweiterung „{name}“ fehlgeschlagen; Vorgang abgelehnt")`; `allow` → continue. Success → breaker `success()`; a `TicketCreatePatch` for `ticket.create` is validated (priority/type names exist → else hook failure; in the wired service these name lookups and the later name→id mapping use the caller's session `self._session`, so they see the same transaction) and merged so later hooks see it; a patch for another operation is a hook failure. Hooks get `op` objects only — never a session; they read with `ctx.plugin.tickets.get(...)` (own session, sees committed data only) and must not write (documented; not enforced).

`SyncBreaker` (module singleton per process): in-memory `consecutive` and `tripped` per hook key; on `failure()` that reaches `TRIP_AFTER` it marks tripped and schedules `_persist(hook_key, error)` as a fire-and-forget task with its **own** session (`ensure_hook(kind="sync")` + `record_hook_failure(trip_after=TRIP_AFTER)`); never touches the caller's session. Every `BREAKER_REFRESH_SECONDS` (lazy, on the next `run_sync_hooks`) it reloads `tripped_at`/`consecutive_failures` of `kind="sync"` rows (`load_hooks`) so an admin's "Erneut versuchen" (`reset_plugin`) and trips in other processes take effect. At boot (every role; idempotent) `ensure_hook` creates a `sync` row per registered hook so the plugin page lists them.

Placement (one place, no double firing): **only** in `TicketWriteService` methods, after the permission assert and before anything is written or sent:

| Method | Operation | `op` built from |
|---|---|---|
| `create_ticket` | `ticket.create` | `params: TicketIn` (+ names via id lookups); the returned patch is applied to `params` (`dataclasses.replace`) before `create_ticket(...)` |
| `add_article` | `article.create` | `article: ArticleIn` — before the email/Telegram/WebChat/plugin-channel branches |
| `move_queue` | `ticket.queue_move` | `PluginTickets().get(ticket_id, session=self._session)` + new queue |
| `change_state` | `ticket.state_change` | after `validate_state_change` |
| `update_dynamic_field`, `update_dynamic_fields` | `ticket.dynamic_fields_update` | the requested values |

Module-level write functions never call hooks. Paths that bypass the service (inbound mail, GI provider ops, process actions, Znuny) are documented as not hooked.

### D5. Process: transition actions and condition modules

```python
# tiqora/plugins/process_points.py
@dataclass(frozen=True, slots=True)
class ActionContext:
    plugin: PluginContext
    ticket_id: int
    user_id: int
    config: Mapping[str, Any]          # smart tags already replaced (replace_action_config)
    session: AsyncSession              # the engine's session; pass it to ctx.plugin.tickets.*(…, session=ac.session)
TransitionActionRun = Callable[[ActionContext], Awaitable[None]]
ConditionRun = Callable[[Mapping[str, str | list[str] | None], str], bool]     # pure, sync: (condition data, field) -> match
```

- `TRANSITION_ACTIONS` and `CONDITION_MODULES` get `sdk_stable=True`.
- `registrar.transition_action("SetContract", run, params=…)` stages `TransitionActionModule(name="Plugin_points_SetContract", run=<adapter>, params=tuple(params), tiqora_only=True)`. `resolve_action("Kernel::System::ProcessManagement::TransitionAction::Plugin_points_SetContract")` finds it through the existing short-name lookup — no resolver change. The designer catalogue lists it automatically.
- Adapter `(session, config, ticket_id, user_id, sysconfig)`: `async with session.begin_nested():` (savepoint) around `call_plugin(timeout=timeout_seconds)`; on failure the savepoint is rolled back, `plugin_transition_action_failed` is logged and the adapter returns normally (Znuny logs a failing TransitionAction and continues the transition; **Assumption**: the dialog submit does not fail).
- `registrar.condition_module("IsVip", run)` stages `ConditionModule(match=ZNUNY_VALIDATION_PREFIX + "Plugin_points_IsVip", validate=<adapter>, tiqora_only=True)`; adapter = `call_plugin_sync`, failure → `False` (no match), result coerced with `bool()` only when it is a `bool` (anything else → failure → `False`).

### D6. Placeholders

```python
# tiqora/plugins/placeholders.py
@dataclass(frozen=True, slots=True)
class PlaceholderRequest:
    plugin: PluginContext
    ticket_id: int | None
    user_id: int | None
    recipient: Mapping[str, str]      # notification recipient map (lower-cased keys), empty outside notifications
    time_zone: str
    bracket: str | None               # the "[n]" suffix of the tag, if any
PlaceholderResolve = Callable[[PlaceholderRequest], Awaitable[str]]

@dataclass(frozen=True, slots=True)
class Placeholder:
    tag: str                          # "PLUGIN_<SHORTID>_<NAME>" (upper)
    plugin_id: str
    resolve: PlaceholderResolve
    timeout_ms: int                   # 50..2000
    context: PluginContext | None = None
PLACEHOLDERS: Final[ExtensionPoint[Placeholder]]   # "core.templates.placeholders", item_key .tag, sdk_stable=True
MAX_VALUE_CHARS: Final = 10_000

async def resolve_plugin_placeholder(tag_upper: str, bracket: str | None, ctx: PlaceholderContext) -> str
    # unknown tag or inactive plugin -> ""; call_plugin(timeout); failure -> ""; non-str -> ""; value[:MAX_VALUE_CHARS]
```

- Registration: `name` must match `TAG_NAME_RE` and must NOT match `_SECRET_RE`/`_PASSWORD_RE` (the choke point `_resolve_tag` would mask it to `xxx`) → `RegistrationRejected("Platzhaltername … würde als geheim maskiert")`.
- `PlaceholderContext` gains `ticket_id: int | None = None` and `user_id: int | None = None`, set by `load_placeholder_context` and by the notification context builder.
- `_resolve_tag_raw`: new first branch `if tag_u.startswith("PLUGIN_"): return await resolve_plugin_placeholder(tag_u, bracket, ctx)` — so it passes through `_resolve_tag` masking and the existing HTML-escape / Znuny-dash handling in `_expand_placeholders_inner` exactly like a core tag. Both `<TIQORA_PLUGIN_…>` and `<OTRS_PLUGIN_…>` resolve (`_TAG_RE` already accepts both prefixes).

### D7. Ticket number generators

```python
# tiqora/plugins/number_generators.py
@dataclass(frozen=True, slots=True)
class NumberRequest:
    counter: int | None               # ticket_number_counter value (None when uses_counter is False)
    system_id: str
    now_local: datetime               # aware, OTRSTimeZone
    offset: int                       # collision-loop offset (0 on the first try)
NumberBuild = Callable[[NumberRequest], str]     # pure, sync

@dataclass(frozen=True, slots=True)
class TicketNumberGenerator:
    key: str                          # qualified, e.g. "Plugin_points_Yearly"
    plugin_id: str
    label: str
    build: NumberBuild
    pattern: str                      # regex for the WHOLE number, e.g. r"\d{4}-\d{6}"; compiled at registration,
                                      # no capturing groups allowed (wrapped by core), length of pattern ≤ 200
    uses_counter: bool
    date_based: bool
    context: PluginContext | None = None
TICKET_NUMBER_GENERATORS: Final[ExtensionPoint[TicketNumberGenerator]]   # "core.tickets.number_generators", sdk_stable=True
SETTING_KEY: Final = "tickets.number_generator"      # tiqora_settings; "" / missing = Znuny SysConfig generator
TN_MAX_LEN: Final = 50                               # ticket.tn VARCHAR(50)

async def active_generator(session_factory) -> TicketNumberGenerator | None
    # reads SETTING_KEY with a fresh session; returns the item only if registered AND plugin active, else None
    # (a configured but missing/disabled generator logs "plugin_number_generator_unavailable" once per process)
async def build_with_plugin(gen, session_factory, sysconfig, offset: int) -> str | None
    # counter via ticket_number_counter_add(…, is_date_based=gen.date_based) if gen.uses_counter;
    # call_plugin_sync(build); valid iff str, 1..TN_MAX_LEN chars, re.fullmatch(pattern); invalid -> None + log
def plugin_tn_patterns(registry: Registry | None = None) -> tuple[str, ...]   # patterns of ALL registered generators
```

- `ticket_number_build(session_factory, sysconfig, offset)`: first `gen = await active_generator(session_factory)`; if set, `tn = await build_with_plugin(...)`; `tn is None` → fall through to the unchanged Znuny path with the same `offset` (fail-open). `ticket_create_number`'s collision loop stays as it is (it calls `ticket_number_build` with growing offsets). A plugin generator that keeps returning an existing number would otherwise loop 16 000 times, so `ticket_number_build` skips the plugin when `offset >= 3`: after three collisions the Znuny generator takes over for that ticket (logged `plugin_ticket_number_collisions`). Counter values consumed by a rejected or colliding plugin number are not reused (Znuny burns counter values on failed creates as well).
- `get_tn_by_string(…, extra_patterns: Sequence[str] = ())`: each extra pattern is tried first in both accepted shapes (`<Hook><Divider>(pattern)` and `<Hook>:\s{0,2}(pattern)`), then the Znuny generator's pattern. `detect_followup` passes `plugin_tn_patterns()`.
- Admin endpoints (D17) read/write `SETTING_KEY`.

### D8. Dynamic field types

```python
# tiqora/plugins/df_types.py
DfBaseType = Literal["Text", "TextArea", "Dropdown", "Multiselect", "Checkbox", "Date", "DateTime"]
DfValidate = Callable[[list[str], Mapping[str, Any]], list[str]]   # (normalized base values, plugin config) -> values;
                                                                   # raise ValueError("<German reason>") to reject
DfDisplay = Callable[[list[str], Mapping[str, Any]], str]
@dataclass(frozen=True, slots=True)
class DynamicFieldType:
    key: str                          # qualified, e.g. "Plugin_points_Contract"
    plugin_id: str
    label: str
    base_type: DfBaseType
    validate: DfValidate | None
    display: DfDisplay | None
    config_model: type[BaseModel] | None
    context: PluginContext | None = None
DYNAMIC_FIELD_TYPES: Final[ExtensionPoint[DynamicFieldType]]   # "core.dynamic_fields.types", sdk_stable=True
PLUGIN_TYPE_CONFIG_KEY: Final = "TiqoraPluginType"
PLUGIN_CONFIG_KEY: Final = "TiqoraPluginConfig"

def plugin_type_of(config: Mapping[str, Any]) -> DynamicFieldType | None    # registered + active, else None
def validate_plugin_values(field: DynamicFieldConfig, values: list[str]) -> list[str]
    # no plugin type -> values; validate raises ValueError -> DynamicFieldValueError(str(exc)[:300]);
    # any other failure (call_plugin_sync not ok) -> values unchanged + log (fail-open, Assumption 8);
    # returned list must be list[str] and pass normalize_df_values(base_type, …) again, else fail-open
def display_plugin_value(field: DynamicFieldConfig, values: list[str]) -> str | None   # None -> core display
```

- `DynamicFieldConfig` gains `config: Mapping[str, Any] = field(default_factory=dict)` and property `plugin_type -> str | None`. `_load_field` today selects only `id, name, field_type, object_type`: it additionally selects `config` and parses it with `config_from_yaml` only when the raw text contains `TiqoraPluginType` (cheap substring check, so core fields pay no YAML parse on every write).
- `set_ticket_dynamic_field`: after `normalize_df_values(...)` → `values = validate_plugin_values(field, values)`. `df_display_value`: plugin display first (fail → core).
- Admin create (`POST /api/v1/admin/dynamic-fields`): `DynamicFieldCreate.plugin_type: str | None = None`; when set: must be a registered, active type, `field_type` is forced to its `base_type` (a different `field_type` in the body → 422), `config[TiqoraPluginType] = key`, `config[TiqoraPluginConfig]` validated with `config_model` (422 with the Pydantic message in German prefix "Einstellungen der Erweiterung: …"). `DynamicFieldOut.plugin_type: str | None = None`. `DynamicFieldUpdate` cannot change `TiqoraPluginType` (a `config` update keeps the stored value; tries to change it → 422).
- `GET /api/v1/admin/dynamic-fields/plugin-types` → `[{key, label, base_type, plugin_id, config_schema}]` (`config_schema` = `config_model.model_json_schema()` or `null`).
- Reference `DynamicFieldDefOut.plugin_type: str | None = None` (agent forms keep the base type's input).

### D9. Mail filters

```python
# tiqora/plugins/mail_filters.py
MailFilterStage = Literal["pre", "post", "outbound"]

@dataclass(frozen=True, slots=True)
class InboundMail:                     # "pre": after Znuny DB postmaster filters (apply_filters), before ticket match
    headers: MutableMapping[str, str]  # the pipeline's get_param dict itself; may set X-OTRS-* (incl. X-OTRS-Ignore: yes)
    subject: str; from_address: str; to: str | None
    body: str                          # read-only plain text
    attachment_names: tuple[str, ...]
    account_id: int; trusted: bool
@dataclass(frozen=True, slots=True)
class InboundMailResult:               # "post": after the message's transaction committed
    outcome: str                       # PipelineResult.outcome
    ticket_id: int | None; article_id: int | None
    headers: Mapping[str, str]         # copy of get_param after all filters
    account_id: int
@dataclass(slots=True)
class OutboundMail:                    # "outbound": in SmtpMailSender.send, before the SMTP call
    to: tuple[str, ...]; cc: tuple[str, ...]; subject: str; from_address: str; message_id: str | None
    headers: Mapping[str, str]         # read-only view of the message headers
    def add_header(self, name: str, value: str) -> None
        # only ^X-[A-Za-z0-9-]{1,60}$ (not X-OTRS-*), value without CR/LF, ≤ 998 chars; else ValueError
@dataclass(frozen=True, slots=True)
class MailFilterContext: plugin: PluginContext
class MailVetoed(Exception):           # plugin raises it in an outbound filter; message German ≤ 300 chars
class OutboundMailVetoed(aiosmtplib.SMTPResponseException):   # code 550, message "Versand durch Erweiterung „…“ abgelehnt: …"

@dataclass(frozen=True, slots=True)
class MailFilter:
    key: str                           # "<plugin_id>:<name>"
    plugin_id: str; stage: MailFilterStage
    run: Callable[[MailFilterContext, Any], Awaitable[None]]
    order: int; timeout_seconds: int   # 1..30
    context: PluginContext | None = None
INBOUND_MAIL_FILTERS: Final[ExtensionPoint[MailFilter]]    # "core.mail.inbound_filters" (stages pre/post)
OUTBOUND_MAIL_FILTERS: Final[ExtensionPoint[MailFilter]]   # "core.mail.outbound_filters"

async def run_inbound_pre_filters(get_param: dict[str, str], parsed: ParsedEmail, account: MailAccount) -> None
async def run_inbound_post_filters(result: PipelineResult, get_param: Mapping[str, str], account_id: int) -> None
async def run_outbound_filters(message: EmailMessage) -> None   # raises OutboundMailVetoed on MailVetoed
```

- Pre: in `_process_message_inner` directly after `await apply_filters(session, get_param)`; the `X-OTRS-Ignore` check that follows sees plugin changes. Every failure → log, next filter (fail-open). Filters run in `(order, key)` order.
- Post: in `process_message`, after the message's transaction is committed (locate the commit in `process_message`; if the commit happens in the caller, place the call in the caller right after it — note the exact place in the commit body). Failures logged only.
- Outbound: first statement of `SmtpMailSender.send` (`run_outbound_filters` returns immediately when `registry.items(OUTBOUND_MAIL_FILTERS)` is empty, so sites without plugins pay one dict lookup) (the one choke point for every real SMTP send: replies, notifications, auto-responses, report delivery, phone-mail, the direct `SmtpMailSender(...)` in `outbound_reply.py`). Header additions are applied with `message[name] = value` (replacing an existing value of the same name). Failure/timeout → log, send unchanged. `MailVetoed` → `raise OutboundMailVetoed(550, …)`; `smtp_reply_codes` already reads `.code` of any `aiosmtplib.SMTPResponseException` (verified 2026-10-12), so `is_permanent_smtp_failure` classifies the veto as permanent and every caller's existing permanent-failure handling applies; if that function changed meanwhile, extending it to read `.code` is part of Task 11.

### D10. Notification transports

```python
# tiqora/plugins/notification_transports.py
@dataclass(frozen=True, slots=True)
class NotificationRecipient:
    kind: Literal["agent", "customer"]
    user_id: int | None
    customer_login: str | None        # ticket.customer_user_id for customer recipients
    email: str; full_name: str; language: str
@dataclass(frozen=True, slots=True)
class NotificationMessage:
    notification_id: int; notification_name: str; event: str
    ticket: TicketSummary
    recipient: NotificationRecipient
    subject: str; body: str; content_type: str         # rendered exactly as for the Email transport
@dataclass(frozen=True, slots=True)
class TransportContext: plugin: PluginContext
TransportSend = Callable[[TransportContext, NotificationMessage], Awaitable[None]]
@dataclass(frozen=True, slots=True)
class NotificationTransport:
    key: str                          # qualified, e.g. "Plugin_points_Chat"
    plugin_id: str; label: str; send: TransportSend; timeout_seconds: int   # 1..60
    context: PluginContext | None = None
NOTIFICATION_TRANSPORTS: Final[ExtensionPoint[NotificationTransport]]   # "core.notifications.transports", sdk_stable=True
```

`process_event`: `transports = set(items["Transports"])`; `email_on = not transports or "Email" in transports`; `plugin_keys = sorted(k for k in transports if <registered and active>)`; unknown keys are ignored (logged once). Skip the notification only when `not email_on and not plugin_keys`. Recipients are resolved once. Per recipient: Email path unchanged when `email_on`; for each plugin key: render with `_render_message` (same output as the mail), `call_plugin(timeout)`; ok → `sent += 1`, failure → `failed += 1` + `NOTIFICATIONS_ERRORS.inc()`. No ticket history for plugin transports (Assumption 13). `GET /api/v1/admin/notification-events/transports` → `[{key: "Email", label: "E-Mail", plugin_id: null}, …plugin transports]`.

### D11. Channel drivers

```python
# tiqora/plugins/channels.py
@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    subject: bool = False; html: bool = False; attachments: bool = False
    max_length: int | None = None          # characters of the plain body; None = unlimited
@dataclass(frozen=True, slots=True)
class OutboundChannelMessage:
    ticket: TicketSummary; agent_id: int
    to: str | None                         # ArticleIn.to_address (the composer fills it from the replied-to article)
    subject: str; body: str; content_type: str
    attachments: tuple[tuple[str, str, bytes], ...]
@dataclass(frozen=True, slots=True)
class DeliveryResult: external_id: str | None = None; to: str | None = None
@dataclass(frozen=True, slots=True)
class ChannelContext: plugin: PluginContext
ChannelSend = Callable[[ChannelContext, OutboundChannelMessage], Awaitable[DeliveryResult]]
@dataclass(frozen=True, slots=True)
class InboundChannelMessage:
    sender: str                            # address/handle as the channel knows it (stored as from_address)
    body: str
    sender_display: str | None = None
    customer_user: str | None = None       # explicit customer login, if the plugin knows it
    subject: str | None = None
    content_type: str = "text/plain; charset=utf-8"
    attachments: tuple[tuple[str, str, bytes], ...] = ()
    external_id: str | None = None         # idempotency key (webhook retries)
    queue: str | None = None               # queue name for new tickets; None = resolve_ticket_for_inbound's default
@dataclass(frozen=True, slots=True)
class IngestResult: ticket_id: int; article_id: int; created: bool; duplicate: bool
@dataclass(frozen=True, slots=True)
class ChannelDriver:
    key: str                               # channel_key, e.g. "plugin_points_chat"
    plugin_id: str; name: str              # qualified, = communication_channel.name, e.g. "Plugin_points_Chat"
    label: str; capabilities: ChannelCapabilities
    send: ChannelSend | None               # None = inbound only
    timeout_seconds: int                   # 1..30
    context: PluginContext | None = None
CHANNEL_DRIVERS: Final[ExtensionPoint[ChannelDriver]]    # "core.channels.drivers", item_key .key, sdk_stable=True
class ChannelDeliveryError(Exception): ...               # -> 502 "Zustellung über „{label}“ fehlgeschlagen: …"

def driver_for(channel: str) -> ChannelDriver | None     # by ArticleIn.channel (case-insensitive), active only
async def ensure_tiqora_channel_row(session: AsyncSession, name: str) -> int
    # generalisation of ensure_webchat_channel_row: module "Kernel::System::CommunicationChannel::Internal",
    # package "Tiqora", channel_data EMPTY_TABLES_CHANNEL_DATA; ensure_webchat_channel_row becomes a one-line wrapper
class PluginChannels:                                    # ctx.channels
    async def ingest(self, name: str, msg: InboundChannelMessage, *, session: AsyncSession | None = None) -> IngestResult
async def deliver_plugin_channel_reply(session, sysconfig, driver, *, ticket_id: int, user_id: int, article: ArticleIn) -> int
```

- `ingest`: `name` must be a channel of the calling plugin (else `PluginWriteError`). Duplicate check: when `external_id` is set, `message_id = f"<{driver.key}.{external_id}@tiqora.invalid>"`; an existing article with that `a_message_id_md5`/message id on the comm channel → `IngestResult(duplicate=True)` with the existing ids, nothing written. Customer: `customer_user` if it exists; else e-mail match (sender contains `@`) via `CustomerService`/the pipeline's lookup; else `resolve_customer_by_phone`; else `None`. Ticket: `resolve_ticket_for_inbound(channel=driver.key, queue_name=msg.queue, …)` (its default queue behaviour unchanged); article `sender_type="customer"`, `is_visible_for_customer=True`, `channel=driver.key`, `from_address=msg.sender_display or msg.sender`. Runs as `SYSTEM_USER_ID`; commits when it opened the session.
- `add_article` (module-level): `channel_name_map` miss + `driver_for(article.channel)` → `comm_channel_id = await ensure_tiqora_channel_row(session, driver.name)`.
- `TicketWriteService.add_article`: after the sync hook and before the email branch: `driver = driver_for(article.channel)`; if `driver and article.sender_type == "agent"`: `send is None` → `InvalidInput("Über diesen Kanal kann nicht geantwortet werden")`; else `deliver_plugin_channel_reply`: body length vs `max_length` → `InvalidInput`; attachments when `not capabilities.attachments` → `InvalidInput`; `call_plugin(timeout=min(driver.timeout_seconds, 30))`; failure → `ChannelDeliveryError`, nothing stored; success → `add_article(...)` with `message_id` from `external_id` and `to_address = result.to or article.to_address` (send-then-store, like Telegram).
- `GET /api/v1/channels/plugin` (any agent) → `[{key, name, label, plugin_id, capabilities, can_reply}]` (new `backend/src/tiqora/api/v1/plugin_channels.py`).
- `ai/gate.py`: `TIQORA_ONLY_CHANNELS` membership checks go through a new `is_tiqora_only_channel(name: str) -> bool` (= core set or `driver_for(name) is not None`) — Znuny cannot answer on a plugin channel.

### D12. Password auth providers

```python
# tiqora/plugins/auth_providers.py
@dataclass(frozen=True, slots=True)
class PasswordCheck:
    login: str
    password: str = field(repr=False)
    user_kind: Literal["agent", "customer"]
    client_ip: str | None
@dataclass(frozen=True, slots=True)
class AuthContext: plugin: PluginContext
PasswordAuthenticate = Callable[[AuthContext, PasswordCheck], Awaitable[str | None]]   # login of an existing account, or None
@dataclass(frozen=True, slots=True)
class PasswordAuthProvider:
    key: str                               # "<plugin_id>:<name>"
    plugin_id: str; audience: Literal["agent", "customer", "both"]
    authenticate: PasswordAuthenticate
    order: int; timeout_seconds: int       # 1..15
    context: PluginContext | None = None
AUTH_PROVIDERS: Final[ExtensionPoint[PasswordAuthProvider]]   # "core.auth.password_providers", sdk_stable=True
CHAIN_BUDGET_SECONDS: Final = 20.0

async def plugin_password_sign_in(session: AsyncSession, *, login: str, password: str,
                                  user_kind: Literal["agent", "customer"], client_ip: str | None) -> tuple[str, str] | None
    # providers of the audience (is_active), sorted (order, key), one PluginCallBudget(20);
    # first provider returning a str whose account exists and is valid (agent: users.login, valid_id = 1;
    # customer: customer_user.login, valid_id = 1) wins -> (account_login, plugin_id);
    # anything else -> next provider; logs "plugin_auth_rejected"/"plugin_auth_failed" with plugin and login, never the password
```

Wiring: `api/v1/auth.py login` — after the LDAP block, `if user is None:` → `found = await plugin_password_sign_in(...)`; `found` → `user = await auth.get_user_by_login(found[0], auth_method="plugin")`. `api/portal/auth.py login` — same with `user_kind="customer"` and `get_customer_by_login`. Rate limiter, 2FA, enrollment and session creation below are unchanged (they now also cover plugin logins). If `auth_method` values are matched anywhere (grep `auth_method ==`), `"plugin"` behaves like `"ldap"` (no local password change).

### D13. Customer sources

```python
# tiqora/plugins/customer_sources.py
@dataclass(frozen=True, slots=True)
class ExternalCompany:
    customer_id: str; name: str
    street: str | None = None; zip: str | None = None; city: str | None = None; country: str | None = None; url: str | None = None
@dataclass(frozen=True, slots=True)
class ExternalCustomer:
    login: str; email: str; first_name: str; last_name: str
    customer_id: str | None = None
    title: str | None = None; phone: str | None = None; mobile: str | None = None
    street: str | None = None; zip: str | None = None; city: str | None = None; country: str | None = None
    company: ExternalCompany | None = None
@dataclass(frozen=True, slots=True)
class CustomerSourceContext: plugin: PluginContext
CustomerSearch = Callable[[CustomerSourceContext, str, int], Awaitable[Sequence[ExternalCustomer]]]
CustomerLookup = Callable[[CustomerSourceContext, str], Awaitable[ExternalCustomer | None]]
@dataclass(frozen=True, slots=True)
class CustomerSource:
    key: str                               # "<plugin_id>:<name>"
    plugin_id: str; label: str
    search: CustomerSearch; by_login: CustomerLookup; by_email: CustomerLookup | None
    materialize_on_inbound: bool; timeout_seconds: int   # 1..10
    context: PluginContext | None = None
CUSTOMER_SOURCES: Final[ExtensionPoint[CustomerSource]]   # "core.customers.sources", sdk_stable=True
class CustomerSourceConflict(ValueError): ...             # German: e-mail belongs to another customer, invalid data

async def search_sources(q: str, limit: int) -> list[tuple[CustomerSource, ExternalCustomer]]
    # all active sources in parallel (asyncio.gather of call_plugin), shared budget 5 s; failures skipped
async def lookup_by_email(email: str, *, inbound_only: bool) -> tuple[CustomerSource, ExternalCustomer] | None
async def materialize(session: AsyncSession, ext: ExternalCustomer, *, actor_id: int) -> str
    # login exists -> return it (no update, Assumption 7); e-mail used by another login -> CustomerSourceConflict;
    # login/email/name validated (login ≤ 200, e-mail via user_provisioning.check_email); the "e-mail used by another login" check is
    # its own SELECT on customer_user (do not import the private _customer_email_in_use); company: ensure_customer_company
    # (insert only when missing); create_customer_user(…, pw_hash=unusable_password_hash(), actor_id=actor_id); no commit
```

```python
# tiqora/domain/customer_company_service.py  (extracted from api/v1/admin/customers.py create_customer_company)
async def ensure_customer_company(session: AsyncSession, *, customer_id: str, name: str, actor_id: int,
                                  street: str | None = None, zip: str | None = None, city: str | None = None,
                                  country: str | None = None, url: str | None = None, comments: str | None = None,
                                  valid_id: int = 1) -> bool
    # True when inserted, False when the customer_id already existed (nothing changed); Znuny CustomerCompanyAdd columns
    # (create/change time + by), CustomerCompany cache invalidation exactly as the admin route does today; no commit
```

Wiring: `GET /api/v1/reference/customer-search` gains `external: list[ExternalContactOut] = []` (filled only when `len(contacts) < limit`; `ExternalContactOut{source, source_label, login, email, first_name, last_name, company_name}`); `POST /api/v1/customers/external-import {source: str, login: str}` → `by_login` → `materialize` → commit → `CustomerUserOut` (same permission dependency as `create_customer`; 404 when the source finds nothing, 409 on `CustomerSourceConflict`). Mail pipeline `_resolve_customer`: on a miss, `lookup_by_email(from_address, inbound_only=True)` (only sources with `materialize_on_inbound`) → `materialize(actor_id=SYSTEM_USER_ID)` inside the pipeline transaction; any failure → behaviour as today (unknown customer).

### D14. GenericInterface: operations, invokers, mappings

```python
# tiqora/plugins/gi_points.py
PLUGIN_HANDLER_PREFIX: Final = "plugin:"
@dataclass(frozen=True, slots=True)
class GiPrincipal: user_id: int; login: str; user_type: Literal["User", "Customer"]
@dataclass(frozen=True, slots=True)
class GiOperationContext:
    plugin: PluginContext; webservice_id: int; webservice: str; operation: str; remote_ip: str | None
    authenticate: Callable[[dict[str, Any]], Awaitable[GiPrincipal | dict[str, Any]]]
        # wraps _auth_from_params (SessionID / UserLogin+Password / CustomerUserLogin+Password); dict = Znuny error result
GiOperationRun = Callable[[GiOperationContext, dict[str, Any]], Awaitable[dict[str, Any]]]
@dataclass(frozen=True, slots=True)
class GiOperation:
    type: str                              # "Plugin_<sid>::<Name>"
    plugin_id: str; run: GiOperationRun; timeout_seconds: int   # 1..55
    context: PluginContext | None = None
GI_OPERATIONS: Final[ExtensionPoint[GiOperation]]     # "core.gi.operations", item_key .type
@dataclass(frozen=True, slots=True)
class GiMapping:
    type: str                              # "Plugin_<sid>_<Name>"
    plugin_id: str; factory: Callable[[dict[str, Any] | None], Mapper]
    context: PluginContext | None = None
GI_MAPPINGS: Final[ExtensionPoint[GiMapping]]         # "core.gi.mappings"
@dataclass(frozen=True, slots=True)
class InvokerContext: plugin: PluginContext; webservice_id: int; invoker: str; ticket_id: int | None
@dataclass(frozen=True, slots=True)
class InvokerResponse: success: bool; data: Any; error_message: str | None
InvokerPrepare = Callable[[InvokerContext, dict[str, Any]], Awaitable[dict[str, Any] | None]]  # None = StopCommunication
InvokerHandle = Callable[[InvokerContext, InvokerResponse], Awaitable[None]]
@dataclass(frozen=True, slots=True)
class GiInvoker:
    type: str                              # "Plugin_<sid>::<Name>"
    plugin_id: str; prepare: InvokerPrepare; handle_response: InvokerHandle | None; timeout_seconds: int   # 1..120
    context: PluginContext | None = None
GI_INVOKERS: Final[ExtensionPoint[GiInvoker]]         # "core.gi.invokers"
```

- Operations: when the webservice config is parsed, `handler = OPERATION_TYPES.get(op_type) or (PLUGIN_HANDLER_PREFIX + op_type if <registered> else None)` (core types always win; core dict untouched). `_dispatch_operation`: `if operation.startswith(PLUGIN_HANDLER_PREFIX): return await run_plugin_operation(...)` before the core chain. `run_plugin_operation`: inactive plugin → `{"Error": {"ErrorCode": "Operation.NotImplemented", …}}`; `call_plugin(timeout)`; timeout/exception → `OperationFault("OperationObject returned an error, cancelling Request")` (Znuny's text, `OPERATION_FAILED_MESSAGE`); a non-dict result → same fault. Inbound/outbound mapping, debugger and transports are the unchanged provider pipeline.
- Mappings: `build_mapper`: `factory = MAPPER_FACTORIES.get(type) or plugin_mapper_factory(type)`; the plugin factory raising → `MappingConfigError(str)`; the adapter mapper wraps `map()` so any non-`MappingError` exception becomes `MappingError(f"{type}: {exc}")`. Mappers stay pure/sync and run in `run_mapper` (`asyncio.to_thread`) — no timeout possible (documented).
- Invokers: `InvokerConfig.supported` = `type in SUPPORTED_INVOKER_TYPES or plugin_invoker(type) is not None`. In `run_requester`, steps "prepare" and "response actions" dispatch on the invoker type: plugin → `prepare` (input: a deep copy of the event data; `None` → Znuny's `StopCommunication`: success, nothing sent, debugger notice "Erweiterung hat den Aufruf übersprungen") and `handle_response` (input: mapped incoming data or the error). Failure/timeout in `prepare` → the existing `handle_failure("RequesterRequestPrepare", …)` path; in `handle_response` → `handle_failure("RequesterResponseProcess", …)`. Retries, transports, mappings, debugger unchanged.

### D15. Reporting

- `REPORT_SOURCES` and `BUILTIN_REPORTS` get `sdk_stable=True`.
- `registrar.report_source(source)`: `source.key` must be `f"plugin.{short_id}.{name}"` (`name` per `NAME_RE`); `label_key` and every dimension/measure/filter `label_key` must start with `f"plugin.{plugin_id}:"`; `requires_facts` must be `False`; `check_plugin_source(source, short_id)` compiles `source.from_clause(ctx)` with a dummy `CompileContext(now=utc_now(), time_zone="UTC", tz=(), near_miss_percent=0, window=None)` and collects every `Table` (walk with `sqlalchemy.sql.visitors.iterate`): all names must be either Znuny tables (not starting with `tiqora_`/`tqp_`) **or** all start with `f"tqp_{short_id}_"`; any `tiqora_*` table, another plugin's `tqp_` table, or a mix → `RegistrationRejected("Berichtsquelle darf nur Znuny-Tabellen oder eigene Tabellen (tqp_<kurzname>_) lesen, nicht gemischt")`. `scope_column` is mandatory already (dataclass) and is enforced by the unchanged compiler.
- `registrar.builtin_report(name, …)`: key `f"plugin.{plugin_id}.{name}"`; `BuiltinReport` gains `plugin_id: str | None = None`; `name`/`title_key`/`description_key` properties: core unchanged; plugin → `name` = last segment, `title_key = f"plugin.{plugin_id}:reports.{name}.title"`, `description_key = f"plugin.{plugin_id}:reports.{name}.description"`. Its `definition.source` must be a core source or one of the same plugin's sources.
- Frontend: report catalogue/builder call `ensurePluginI18n(pluginId)` for every `plugin.*` key before rendering labels (Task 21).

### D16. ai-worker hosts plugins

`PluginRole = Literal["api", "worker", "mcp", "ai-worker"]`; `PLUGIN_ROLES` gains `"ai-worker"` (string equals the Docker role name and plugins2b's `tiqora_process.role`). `run_ai_worker` awaits `init_registry(role="ai-worker", settings=…, session_factory=get_session_factory())` before it starts its loops (same place in the startup sequence as `worker/__main__.py`). `summarize_status` treats the role like `mcp` (R-STATUS-ROLES: a FAILED ai-worker row makes the plugin `failed`). The ai-worker hosts no subscribers/tasks/routes (those loops stay in worker/api). Admin label `admin.plugins.roles.ai-worker` = "KI-Hintergrunddienst". A site that never runs an ai-worker container never gets an ai-worker runtime row, which `summarize_status` already treats as "not booted" for that role only (verify; add a test).

### D17. Admin/API additions (all admin-only unless noted)

| Endpoint | Response |
|---|---|
| `GET /api/v1/admin/plugins` (existing) | `PluginHookOut.kind: Literal["event", "task", "sync"]`; for `sync` rows `schedule` = operation key (frontend maps it to a label) |
| `GET /api/v1/admin/plugins/number-generators` | `NumberGeneratorsOut{active: str | None, znuny_generator: str, available: [{key, label, plugin_id, example}]}` (`example` = `build` with counter 1, today; `None` on failure) |
| `PUT /api/v1/admin/plugins/number-generators` | body `{key: str | null}`; unknown/inactive key → 422; writes `tiqora_settings` `tickets.number_generator` |
| `GET /api/v1/admin/notification-events/transports` | `[{key, label, plugin_id}]` |
| `GET /api/v1/admin/dynamic-fields/plugin-types` | `[{key, label, base_type, plugin_id, config_schema}]` |
| `GET /api/v1/channels/plugin` (agent) | `[{key, name, label, plugin_id, capabilities, can_reply}]` |
| `GET /api/v1/reference/customer-search` (agent, existing) | `+ external: [ExternalContactOut]` |
| `POST /api/v1/customers/external-import` (agent) | `CustomerUserOut` |

### D18. i18n keys (de shown; en added alongside, then propagate)

`admin.plugins.capabilities.point.<key>`: `sync_hooks` "Prüft Ticketänderungen vor dem Speichern (kann sie ablehnen)", `transition_actions` "Liefert Aktionen für Prozesse", `condition_modules` "Liefert Bedingungen für Prozesse", `placeholders` "Liefert Platzhalter für Vorlagen", `number_generators` "Liefert ein Ticketnummer-Schema", `df_types` "Liefert Arten von Zusatzfeldern", `inbound_mail_filters` "Bearbeitet eingehende E-Mails", `outbound_mail_filters` "Prüft ausgehende E-Mails (kann den Versand ablehnen)", `notification_transports` "Liefert Versandwege für Benachrichtigungen", `channel_drivers` "Liefert einen Kanal", `password_providers` "Prüft Kennwörter bei der Anmeldung", `customer_sources` "Liefert ein Kundenverzeichnis", `gi_operations` "Liefert Webservice-Operationen", `gi_invokers` "Liefert Webservice-Aufrufe (Invoker)", `gi_mappings` "Liefert Webservice-Zuordnungen (Mapping)" (`builtin_reports`, `report_sources` exist). `admin.plugins.roles.ai-worker` "KI-Hintergrunddienst". `admin.plugins.hooks.kind.sync` "Prüfung vor dem Speichern"; `admin.plugins.hooks.operation.<op>` (`ticket.create` "Ticket anlegen", `article.create` "Nachricht/Notiz hinzufügen", `ticket.queue_move` "Queue wechseln", `ticket.state_change` "Status ändern", `ticket.dynamic_fields_update` "Zusatzfelder ändern"). `admin.plugins.numberGenerator.title` "Ticketnummer-Schema", `.use` "Für neue Tickets verwenden", `.active` "Wird für neue Tickets verwendet", `.example` "Beispiel: {{example}}", `.znunyNote` "Tickets, die Znuny parallel anlegt, bekommen weiter Nummern nach dem Znuny-Schema ({{generator}}). Antworten auf beide Arten von Nummern werden erkannt.", `.reset` "Znuny-Schema verwenden". `admin.notificationEvents.transports` "Versandwege", `.transportEmail` "E-Mail". `admin.dynamicFields.pluginType` "Art aus Erweiterung", `.pluginTypeHint` "Wird in Tiqora geprüft; in Znuny erscheint das Feld als „{{base}}“.". `ticket.reply.pluginChannel` "Antwort über {{label}}", `.pluginChannelTooLong` "Die Nachricht ist zu lang für {{label}} (höchstens {{max}} Zeichen).", `.pluginChannelNoAttachments` "{{label}} kann keine Anhänge übertragen.". `customers.external.title` "Aus Kundenverzeichnis", `.source` "aus {{label}}", `.import` "Übernehmen", `.imported` "Kunde übernommen", `.conflict` "Diese E-Mail-Adresse gehört schon zu einem anderen Kunden.".

---
## Parallel operation with Znuny (what Znuny sees of plugin items)

| Plugin item | Stored where Znuny reads it | Znuny's behaviour (documented in `docs/plugins.md` + `docs/parallel-operation.md`) |
|---|---|---|
| transition action / condition module | process config `Module: …::Plugin_<sid>_<Name>` | Znuny cannot load the module: logs an error, skips the action / the condition never matches — same as today's `tiqora_only` modules |
| GI operation / invoker / mapping | webservice YAML `Type: Plugin_<sid>…` | Znuny reports the type as unknown for that operation/invoker only; other operations keep working |
| notification transport | `notification_event_item` `Transports` | Znuny skips an unknown transport (its `Notification::Transport` registry has no such key) |
| DF type | `dynamic_field.config` extra keys | ignored by Znuny; field behaves as its base type; Znuny admin save drops the keys (field becomes a plain base-type field in Tiqora too) — pinned by Task 22 |
| channel | `communication_channel` row (Internal module, package Tiqora) | Znuny reads plugin-channel articles like WebChat articles — pinned by Task 22 |
| number generator | `tiqora_settings` only | Znuny keeps its own generator for tickets it creates |
| placeholder | template text | `<OTRS_PLUGIN_…>` renders `-` in Znuny, `<TIQORA_PLUGIN_…>` stays raw |
| sync hooks, mail filters, auth, customer sources, report items | not stored in Znuny | Znuny is unaffected |

## Deviations from spec

1. **Customer backends are lookup + take-over sources**, not live replacement backends (spec row "customer user + company backends"); see Assumption 7.
2. **Auth providers are password-chain only** (spec "Auth providers (agent/customer)"); SSO-style plugin providers (redirect flows) are not offered (Assumption 6).
3. **DF types have no frontend widget slot in this plan** (spec "frontend widget"; frontend slot table "Dynamic field widgets"): the base type's input is used (Assumption 8); a DF widget slot is phase 3 together with the dev kit.
4. **Sync hooks cover five agent-side operations** and only `TicketWriteService` paths (Assumptions 2, 3); Znuny-side and pipeline writes are visible to plugins only through phase-1 async subscribers.
5. **Reporting: no export formats** (spec "export formats"; Assumption 10); dimensions/measures come as part of a plugin report source.
6. **Number generator selection lives in Tiqora** (Assumption 9); "ticket title/subject builders" from the same spec row are not in this plan.
7. **Outbound filters cannot change content** (Assumption 11).
8. **The ai-worker becomes a plugin host** (reverses phase-1 Deviation 12, Assumption 5).
9. **No CMDB extension point** — the spec's phase-2 row does not list one; the cmdb plan owns any `core.cmdb.*` point (Cross-plan contracts).
10. **Write-capable SDK services** (phase-1 Deviation 14 deferred them to phase 2) are delivered here with a deliberately small surface (create, add article, state, queue, dynamic field).

## Cross-plan contracts

| Contract | Owner | Consumers / rule |
|---|---|---|
| `tiqora_sdk` surface, submodules, `SDK_VERSION = "0.2.0"`, `@tiqora/plugin-sdk` version `0.2.0`, `PluginRegistry` protocol | **plugins2a** | plugins2b adds no SDK symbol and does not bump the version (stated in its plan); its fixtures declare `sdk = ">=0.2,<0.3"` |
| Manifest `capabilities.extension_points` keys of D2 and their admin labels (`admin.plugins.capabilities.point.*`, D18) | **plugins2a** | plugins2b's install dialog and package report reuse the same keys/labels for "what this package declares" |
| `PluginRole` / `PLUGIN_ROLES` including `"ai-worker"` | **plugins2a** (Task 2) | plugins2b's coordinated restart (`tiqora_process.role`, its open question 15) uses `PLUGIN_ROLES`, so the ai-worker is restarted after a plugin change |
| `tiqora_plugin_hook.kind ∈ {"event","task","sync"}`, `PluginHookOut.kind` | **plugins2a** | plugins2b's uninstall cleans hook rows by `plugin_id` (all kinds) |
| Plugin tables are named `tqp_<short_id>_*` | spec §5 / **plugins2b** (creates them) | plugins2a's report-source check (D15) accepts exactly that prefix and nothing else of the plugin's own; it needs no table to exist at registration |
| Fixture plugin `org.example.points` (`tq_fixture_points`) | **plugins2a** | plugins2b may package it as a `.tqplugin` in its tests; it must not change its `register()` |
| GI: `OPERATION_TYPES` stays the core dict; plugin types resolve through `GI_OPERATIONS`/`GI_INVOKERS`/`GI_MAPPINGS` with handler prefix `plugin:` | **plugins2a** (points) | **itsm-import** adds `ConfigItem::*` entries to `OPERATION_TYPES` and dispatch branches in `_dispatch_operation` as core code (not via the registrar). Both plans edit `api/compat/webservices.py` and `api/compat/router.py`: plugins2a adds one `or plugin…` expression at the `handler=` line and one `if operation.startswith(PLUGIN_HANDLER_PREFIX)` block at the top of `_dispatch_operation`; itsm-import appends dict entries/branches — whoever merges second rebases those lines |
| Process transition actions / condition modules | core points owned by the process area; **plugins2a** flips `sdk_stable` and adds the `Plugin_` namespace | **itsm1/itsm-import** may add core actions (e.g. `ConfigItemUpdate`) via `register_core`; core names never start with `Plugin_` (Task 1 test) |
| `core.cmdb.*` extension points | **cmdb** | not opened to plugins in wave 4 (`sdk_stable=False`); plugins2a adds no CMDB stub |
| DF types apply to object types `Ticket`/`Article` only | **plugins2a** | cmdb/itsm CI attribute types are their own concept and do not consume `core.dynamic_fields.types` |
| `TicketWriteService` sync-hook calls | **plugins2a** | **itsm1** may add new `TicketWriteService` methods; they fire no sync hooks unless this plan's operation list is extended (an SDK change for a later wave). Edits to `domain/ticket_write_service.py` are append-only at the named methods |
| `tiqora.domain.customer_company_service.ensure_customer_company(session, *, customer_id, name, actor_id, street=None, zip=None, city=None, country=None, url=None, comments=None, valid_id=1) -> bool` | **plugins2a** (Task 16) | **scim**: if it needs to create companies it calls this function; if scim lands first and already extracted an equivalent, plugins2a reuses scim's function instead and adapts the name in this plan's Task 16 (whoever lands second adapts). Customer users are created by both only via `user_provisioning.create_customer_user` |
| API-key scope areas | **scim** may add an area (`scim`) | plugins2a adds no area (`/api/v1/channels/plugin`, `/api/v1/customers/external-import` fall under the existing `/api/v1/*` mapping of their path) |

## Shared files (coordination with other wave-4 plans)

`domain/ticket_write_service.py` (itsm1), `api/compat/webservices.py` + `api/compat/router.py` (itsm-import), `process/actions/registry.py` (itsm-import adds `ConfigItemUpdate`), `extensions/core.py` (everyone registering core items — this plan does not edit it except if a point needs a core default; none does), `plugins/state.py` + `api/v1/admin/plugins.py` + `plugins/store.py` (plugins2b later), `api/v1/admin/customers.py` (scim may touch customer admin), `api/v1/auth.py` + `api/portal/auth.py` (scim/identity unlikely; append-only after the LDAP block), `channels/email/pipeline.py`, `channels/email/smtp.py`, `worker/notifications.py`, `api/v1/reference.py`, `config.py` (no change planned), `frontend/src/routes/admin/PluginsPage.tsx` (plugins2b later), i18n `en.json`/`de.json` + propagation, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `docs/plugins.md`, `docs/parallel-operation.md`, `docs/architecture.md`, `docs/compatibility.md`.

---

## Tasks

Order: Task 1 first (SDK version, helpers, fixture, test helper). Task 2 (ai-worker) and Task 3 (write services) next — Task 3 is needed by 4, 6, 13, 17. 4 → 5. Then 6–19 in any order (each adds one `_register_<point>` function to the fixture and one entry to the surface snapshot; run them one after another, not in parallel worktrees, because they share `registrar.py`, `tiqora_sdk/__init__.py`, the fixture and the snapshot test). 13 → 14. 16 before 21. 20 needs 5, 8, 9, 12 (OpenAPI). 21 needs 14, 16, 19. 22 needs 9, 13. 23 closes. Every backend task regenerates OpenAPI when it changes an API model and commits the regenerated files with it.

Seed sub-bands (band 55000–55499, logins contain `pp55`): T3 55000–55039 · T5 55040–55079 · T6 55080–55109 · T7 55110–55129 · T8 55130–55169 · T9 55170–55209 · T10 55210–55239 · T11 55240–55259 · T12 55260–55299 · T13 55300–55339 · T14 55340–55369 · T15 55370–55389 · T16 55390–55429 · T17 55430–55449 · T18 55450–55469 · T19 55470–55489 · T22 55490–55499.

Regression globs named in the tasks (`tests/test_compat*.py` …) are approximate: list the real files with `ls backend/tests | grep -i <area>` and run those. Every backend task ends with: the task's tests on both dialects → PASS; `cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk` → clean; `rtk git diff --stat backend/tests/db_leak_baseline.txt` → empty; stage explicit paths; `rtk git diff --cached --name-only`; commit; `rtk git log -1 --oneline`. The commit command shape (shown once):

```bash
rtk git add <explicit paths>
rtk git diff --cached --name-only
rtk git commit -m "$(printf '<subject>\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
rtk git log -1 --oneline
```

### Task 1: SDK 0.2.0, qualified names, `call_plugin`, fixture plugin `org.example.points`, test helper (backend + npm version)

**Files:**
- Create: `backend/src/tiqora/plugins/naming.py` (D1.1), `backend/src/tiqora/plugins/calls.py` (D1.2), `backend/tests/plugins/fixtures/tq_fixture_points/{__init__.py,plugin.py,behaviour.py,tiqora-plugin.toml}` (D1.5; `extension_points = []` for now, `register()` with no calls yet), `backend/tests/_plugin_points.py` (D1.5)
- Modify: `backend/src/tiqora_sdk/__init__.py` (`SDK_VERSION = "0.2.0"`), every fixture manifest `backend/tests/plugins/fixtures/*/tiqora-plugin.toml` (`sdk = ">=0.2,<0.3"`), `backend/tests/test_plugins_loader.py`, `backend/tests/test_plugins_registrar.py`, `backend/tests/_plugins_state_helpers.py` (manifest literals `">=0.1,<0.2"` → `">=0.2,<0.3"`; check `grep -rn '0\.1,<0\.2' backend/tests` → only `test_plugins_manifest.py`'s string-parsing tests may keep it), `backend/tests/test_tiqora_sdk_surface.py` (version `0.2.0`; new helper `SUBMODULES: dict[str, set[str]]`, empty until later tasks), `packages/plugin-sdk/package.json` + `packages/plugin-sdk/src/index.ts` (`SDK_VERSION: "0.2.0"`), `packages/plugin-sdk/README.md` (version text), `frontend/src/plugins/sdkRuntime.tsx` (`SDK_VERSION = "0.2.0"`), `frontend/src/plugins/sdkRuntime.test.tsx` (`toBe("0.2.0")`)
- Test: `backend/tests/test_plugins_naming.py`, `backend/tests/test_plugins_calls.py` (pure), `backend/tests/test_plugins_points_fixture.py`

**Interfaces:**
- Consumes: phase-1 `install_fixture_plugin`, `import_and_register`, `commit_registrar`, `make_plugin_context`, `set_loaded_plugins`, `PluginLoadResult`, `RuntimeState`, `register_core`, `set_registry_for_tests`.
- Produces: D1.1 and D1.2 names exactly; `boot_points_plugin`, `points_behaviour` (D1.5); `SUBMODULES` in the surface test (later tasks add one entry each: `SUBMODULES["tiqora_sdk.hooks"] = {...}`).

- [ ] **Step 1: Failing tests.**
  ```python
  # test_plugins_naming.py
  def test_qualified_names() -> None:
      assert qualified("points", "SetContract") == "Plugin_points_SetContract"
      assert channel_key("points", "Chat") == "plugin_points_chat"
      assert is_plugin_name("Kernel::System::ProcessManagement::TransitionAction::Plugin_points_X")
      assert is_plugin_name("Plugin_points::ContractGet") and not is_plugin_name("Ticket::TicketCreate")
      for bad in ("setContract", "Set_Contract", "S", "X" * 42, "Set-Contract"):
          assert not CAMEL_NAME_RE.fullmatch(bad)
  def test_core_never_uses_plugin_prefix() -> None:
      from tiqora.extensions.registry import Registry
      registry = Registry(); register_core(registry)
      for reg in registry.registrations():
          assert not reg.key.lower().startswith(("plugin_", "plugin.")), reg   # core keys stay outside the plugin namespace
  ```
  ```python
  # test_plugins_calls.py
  async def test_ok_and_value() -> None:
      out = await call_plugin("org.example.x", "t", lambda: _ret(5), timeout=1)
      assert out.ok and out.value == 5 and out.error is None
  async def test_timeout() -> None:
      out = await call_plugin("org.example.x", "t", lambda: asyncio.sleep(5), timeout=0.05)
      assert not out.ok and out.timed_out and "Zeitüberschreitung" in (out.error or "")
  async def test_exception_and_systemexit_contained() -> None:
      assert not (await call_plugin("p", "t", _raise(ValueError("kaputt")), timeout=1)).ok
      out = await call_plugin("p", "t", _raise(SystemExit(3)), timeout=1)
      assert not out.ok and out.error.startswith("SystemExit")
  async def test_caller_cancellation_propagates() -> None:
      task = asyncio.create_task(call_plugin("p", "t", lambda: asyncio.sleep(5), timeout=10))
      await asyncio.sleep(0.01); task.cancel()
      with pytest.raises(asyncio.CancelledError):
          await task
  async def test_plugin_raised_cancelled_is_failure() -> None:
      out = await call_plugin("p", "t", _raise(asyncio.CancelledError()), timeout=1)
      assert not out.ok
  def test_sync_variant_and_budget() -> None:
      assert call_plugin_sync("p", "t", lambda: 1).value == 1
      assert not call_plugin_sync("p", "t", lambda: 1 / 0).ok
      clock = iter([0.0, 0.0, 4.0, 4.0, 6.0]).__next__
      budget = PluginCallBudget(5.0, clock=clock)
      assert budget.timeout_for(3.0) == 3.0 and budget.timeout_for(3.0) == 1.0 and budget.remaining() == 0.0
  async def test_failure_logged_without_arguments() -> None:
      with structlog.testing.capture_logs() as logs:
          await call_plugin("p", "auth", _raise(ValueError("geheim123")), timeout=1)
      entry = next(e for e in logs if e["event"] == "plugin_call_failed")
      assert entry["plugin"] == "p" and entry["what"] == "auth"
  ```
  ```python
  # test_plugins_points_fixture.py
  def test_points_fixture_boots(tmp_path, monkeypatch) -> None:
      registry = boot_points_plugin(tmp_path, monkeypatch)
      assert registry.frozen
      from tiqora.plugins.state import loaded_plugins
      assert loaded_plugins()["org.example.points"].state == RuntimeState.ACTIVE
      assert points_behaviour().CALLS == []
  ```
  `test_tiqora_sdk_surface.py`: `assert tiqora_sdk.SDK_VERSION == "0.2.0"`; new `test_submodules_are_exact()` iterating `SUBMODULES` (passes vacuously now, real from Task 3 on).
- [ ] **Step 2: Run → FAIL** (`ImportError`, version mismatch): `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_plugins_naming.py tests/test_plugins_calls.py tests/test_plugins_points_fixture.py tests/test_tiqora_sdk_surface.py`.
- [ ] **Step 3: Implement** D1.1, D1.2, the fixture skeleton, the helper; bump versions (Python, npm, frontend runtime, README, every fixture/test manifest literal).
- [ ] **Step 4: Run → PASS**, plus the whole phase-1 plugin suite: `uv run python -m pytest -q tests/test_plugins_*.py tests/test_admin_plugins_api.py` (proves the version bump broke no fixture). Frontend: `cd frontend && pnpm test --run src/plugins && pnpm lint`; `cd ../packages/plugin-sdk && pnpm check:preset`.
- [ ] **Step 5: Commit** `feat(plugins): SDK 0.2.0, plugin-qualified names, contained plugin calls, points fixture`.

### Task 2: The ai-worker hosts plugins (backend)

**Files:**
- Modify: `backend/src/tiqora/plugins/state.py` (`PluginRole`, `PLUGIN_ROLES`, `summarize_status`), `backend/src/tiqora/ai/worker.py` (`run_ai_worker` startup), `backend/src/tiqora/api/v1/admin/plugins.py` (role literal in response models, if any)
- Test: `backend/tests/test_plugins_state.py` (extend or create), `backend/tests/test_ai_worker_plugins.py`, `backend/tests/test_admin_plugins_api.py` (extend; both dialects, no seed ids — runtime rows keyed by the fixture plugin id); i18n key `admin.plugins.roles.ai-worker` is added in Task 20

**Interfaces:**
- Consumes: `init_registry`, `summarize_status`.
- Produces: role `"ai-worker"` (D16, Cross-plan contracts).

- [ ] **Step 1: Read** `run_ai_worker` and the worker's `init_registry` call (`worker/__main__.py:438`) to place the call identically (before any loop starts, after logging/settings setup; R-BOOT-ANYERR already guarantees no crash). Read `grep -n "def summarize_status" -A12 backend/src/tiqora/plugins/state.py` — today it takes `api_state` plus `other_states: Iterable[RuntimeState]`; the ai-worker row's state goes into `other_states` at its call site in `api/v1/admin/plugins.py`.
- [ ] **Step 2: Failing tests.**
  ```python
  def test_ai_worker_role_listed() -> None:
      assert "ai-worker" in PLUGIN_ROLES
  @pytest.mark.parametrize(("rows", "expected"), [
      ({"api": RuntimeState.ACTIVE, "ai-worker": RuntimeState.FAILED}, "failed"),
      ({"api": RuntimeState.ACTIVE}, "active"),          # no ai-worker container: not a problem
  ])
  def test_summarize_status_with_ai_worker(rows, expected) -> None:
      status = summarize_status(enabled=True, problem=None, compat_problem=None, api_state=rows.get("api"),
                                other_states=[v for k, v in rows.items() if k != "api"])
      assert status == expected
  async def test_admin_list_passes_ai_worker_rows(...)   # GET /api/v1/admin/plugins with an ai-worker FAILED runtime row -> status "failed",
                                                         # status_message names "KI-Hintergrunddienst"
  async def test_run_ai_worker_inits_registry(monkeypatch) -> None:
      calls = []
      async def fake_init(**kw): calls.append(kw["role"]); return Registry()
      monkeypatch.setattr("tiqora.ai.worker.init_registry", fake_init)
      # stop the worker right after startup (patch its first loop to raise a sentinel), then assert
      assert calls == ["ai-worker"]
  ```
- [ ] **Step 3: Run → FAIL. Step 4: Implement** D16. **Step 5: Run → PASS**, lint gates; regenerate OpenAPI if a response model's literal changed; commit `feat(plugins): ai-worker loads plugins (role ai-worker)`.

### Task 3: Write-capable SDK domain services (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/writes.py`, `backend/src/tiqora/plugins/customers.py`, `backend/src/tiqora_sdk/tickets.py`
- Modify: `backend/src/tiqora/plugins/context.py` (`PluginTickets` methods delegate to `writes.py`; `PluginContext.customers`, `.channels` field with `None` default — the `PluginChannels` class itself arrives in Task 13, typed via `TYPE_CHECKING` import until then), `backend/tests/test_tiqora_sdk_surface.py`
- Test: `backend/tests/test_plugins_writes_db.py` (both dialects, band 55000–55039)

**Interfaces:**
- Consumes: `TicketWriteService`, `create_ticket`, `add_article`, `change_state`, `move_queue`, `update_dynamic_field`, `TicketIn`, `ArticleIn`, `SYSTEM_USER_ID`, `CustomerService`.
- Produces: D3 exactly; `SUBMODULES["tiqora_sdk.tickets"] = {"TicketDraft", "PluginWriteError", "PluginPermissionDenied", "CustomerSummary"}`.

- [ ] **Step 1: Failing tests** (seed: queue `pp55-q1` id 55001 in group 55002, agent `pp55-agent` id 55003 with `rw` on 55002, agent `pp55-ro` id 55004 with `ro` only, customer `pp55-cust` (`pp55-cust@example.org`), a Text DF `PP55Contract` id 55005, a Date DF `PP55Due` id 55006):
  ```python
  async def test_create_as_system(url_fixture, request) -> None:
      tid = await PluginTickets().create(TicketDraft(title="Vertrag", queue="pp55-q1", customer_user="pp55-cust",
                                                     body="Hallo", dynamic_fields={"PP55Contract": ["V-1"]}))
      # ticket row: queue 55001, user_id/create_by 1, one article (channel Internal), DF value "V-1", NewTicket history
  async def test_create_as_agent_checks_permission(url_fixture, request) -> None:
      with pytest.raises(PluginPermissionDenied):
          await PluginTickets().create(TicketDraft(title="x", queue="pp55-q1"), as_agent=55004)
  async def test_unknown_names_are_plain_errors(url_fixture, request) -> None:
      with pytest.raises(PluginWriteError, match="Queue „gibt-es-nicht“"):
          await PluginTickets().create(TicketDraft(title="x", queue="gibt-es-nicht"))
      with pytest.raises(PluginWriteError, match="Status"):
          await PluginTickets().set_state(tid, "kaputt")
  async def test_email_channel_refused(url_fixture, request) -> None:
      with pytest.raises(PluginWriteError, match="eigenen Kanal"):
          await PluginTickets().add_article(tid, body="x", channel="email")
  async def test_caller_session_not_committed(url_fixture, request) -> None:
      async with factory() as s:
          await PluginTickets().add_article(tid, body="note", session=s)
          await s.rollback()
      # article count unchanged
  async def test_own_session_rolls_back_on_error(url_fixture, request) -> None:
      # set_dynamic_field(tid, "PP55Due", ["kein Datum"]) -> PluginWriteError, no partial rows
  async def test_customers_read_only(url_fixture, request) -> None:
      assert (await PluginCustomers().get("pp55-cust")).email == "pp55-cust@example.org"
      assert [c.login for c in await PluginCustomers().search("pp55-cu")] == ["pp55-cust"]
  ```
  Clean up: `snapshot_marks`/`delete_above_marks` for ticket/article/history/DF value tables; seeded rows by id.
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D3 (`set_state` maps name→id and calls `change_state`; `move` → `move_queue`; `set_dynamic_field` → `update_dynamic_field`, `DynamicFieldValueError`/`InvalidInput` → `PluginWriteError`; `TicketAccessDenied` → `PluginPermissionDenied`). Update `tiqora_sdk/tickets.py` and the surface snapshot.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(plugins): write-capable ticket services and read-only customers for plugins`.

### Task 4: Sync hooks — point, runner, budget, breaker (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/sync_hooks.py` (D4), `backend/src/tiqora_sdk/hooks.py`
- Modify: `backend/src/tiqora/plugins/registrar.py` (`before`), `backend/src/tiqora_sdk/__init__.py` (`PluginRegistry.before`), `backend/src/tiqora/plugins/bootstrap.py` (after commit: `ensure_hook(kind="sync")` per registered hook, best effort, every role — idempotent), fixture (`_register_sync_hooks`: one hook per operation, behaviour by `MODE[f"hook.{op}"]`; manifest `core.events.sync_hooks`), `backend/tests/test_tiqora_sdk_surface.py`
- Test: `backend/tests/test_plugins_sync_hooks.py` (pure, fake clock where needed), `backend/tests/test_plugins_sync_hooks_store_db.py` (both dialects; no seed ids — hook rows keyed by the fixture id, cleaned by `plugin_id`)

**Interfaces:**
- Consumes: `call_plugin`, `PluginCallBudget`, `is_active`, `ensure_hook`, `record_hook_failure`, `record_hook_success`, `load_hooks`.
- Produces: D4 names exactly; `SyncBreaker.reset_for_tests()`; `SUBMODULES["tiqora_sdk.hooks"]`.

- [ ] **Step 1: Failing tests** (fixture booted with `boot_points_plugin`; ops built directly):
  ```python
  async def test_ok_returns_op_unchanged(...)                 # MODE ok -> same object
  async def test_veto_raises_with_plugin_message(...)         # MODE veto -> OperationVetoed("Vertragsnummer fehlt"), plugin_id set
  async def test_timeout_fail_open_skips(...)                 # hook timeout_ms=100, MODE hang -> returns op, elapsed < 0.5 s
  async def test_fail_closed_vetoes(...)                      # on_error="deny", MODE raise -> OperationVetoed("Prüfung durch Erweiterung … fehlgeschlagen…")
  async def test_budget_shared_across_hooks(monkeypatch)      # OPERATION_BUDGET_SECONDS patched to 0.3, two hanging fail-open hooks
                                                              # with timeout 3000 -> total elapsed < 0.6 s, both counted as failures
  async def test_enrich_only_on_ticket_create(...)            # MODE enrich: ticket.create -> title patched; same patch on
                                                              # ticket.state_change -> counted as failure (fail-open skip)
  async def test_patch_with_unknown_priority_is_failure(...)  # needs DB names? -> validate via injected lookup; see Step 3
  async def test_trip_after_five_and_reset(...)               # 5x raise -> 6th call: fail-open skipped without calling the plugin
                                                              # (CALLS length stays 5); deny-hook tripped -> veto "angehalten";
                                                              # SyncBreaker.reset(hook_key) -> called again
  async def test_inactive_plugin_hooks_ignored(...)           # set_loaded_plugins([... DISABLED]) -> not called
  def test_registration_rules(...)                            # unknown operation, timeout_ms 0/4000, on_error "maybe",
                                                              # sync handler (def), undeclared capability -> RegistrationRejected
  ```
  `test_plugins_sync_hooks_store_db.py`: a trip persists a `kind="sync"` row with `tripped_at` (own session; the test passes no session to `run_sync_hooks`); after `reset_plugin(...)` and a breaker refresh (patch `BREAKER_REFRESH_SECONDS` to 0) the hook is called again; `ensure_hook` at boot created one `sync` row per hook.
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D4 runner, breaker, registrar method (checks: operation in `SYNC_OPERATIONS`, `_check_name`, async handler, `50 <= timeout_ms <= 3000`, `on_error` literal, `0 <= order <= 1000`). Patch validation takes an injected async `name_exists(table, name) -> bool` (default: one SELECT on `ticket_priority`/`ticket_type` with a fresh session) so the pure tests stub it.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(plugins): synchronous before-hooks with time budget and circuit breaker`.

### Task 5: Sync hooks in `TicketWriteService`; admin shows sync hooks (backend)

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (`TicketWriteService.create_ticket`, `.add_article`, `.move_queue`, `.change_state`, `.update_dynamic_field`, `.update_dynamic_fields` — D4 placement table), `backend/src/tiqora/api/v1/admin/plugins.py` (`PluginHookOut.kind` + sync rows), `packages/api-client/openapi.json`, `docs/api/openapi.json`
- Test: `backend/tests/test_plugins_sync_hooks_wiring_db.py` (both dialects, band 55040–55079), extend `backend/tests/test_admin_plugins_api.py`

**Interfaces:**
- Consumes: `run_sync_hooks`, `OperationVetoed`, `PluginTickets.get`.
- Produces: hooked service methods; `PluginHookOut.kind: Literal["event", "task", "sync"]`.

- [ ] **Step 1: Failing tests** (fixture booted; seed queue/agent/customer in the band; outbound mail via an injected `CapturingMailSender` as `TicketWriteService(mail_sender=…)`):
  ```python
  async def test_veto_before_email_send(url_fixture, request) -> None:
      behaviour.MODE["hook.article.create"] = "veto"
      sender = CapturingMailSender()
      svc = TicketWriteService(session, factory, SysConfig(session), mail_sender=sender)
      with pytest.raises(OperationVetoed):
          await svc.add_article(55043, tid, ArticleIn(sender_type="agent", channel="email", subject="Re", body="x",
                                                      to_address="pp55-cust@example.org", is_visible_for_customer=True))
      assert sender.sent == []                       # nothing left the house
  async def test_veto_writes_nothing(url_fixture, request) -> None:
      # ticket.create veto -> no ticket row, no ticket_number_counter consumption is acceptable (Znuny also burns numbers
      # on failed creates) but no ticket/history/article rows; state_change veto -> state unchanged, no history row
  async def test_enrich_applies_to_created_ticket(url_fixture, request) -> None:
      behaviour.MODE["hook.ticket.create"] = "enrich"   # fixture patch: title "[geprüft] " + title, DF PP55Flag=["1"]
      # created ticket title and DF reflect the patch
  async def test_module_level_functions_never_hook(url_fixture, request) -> None:
      behaviour.MODE["hook.ticket.create"] = "veto"
      await create_ticket(session, factory, SysConfig(session), params=..., user_id=1)   # succeeds
  async def test_api_returns_422_with_message(url_fixture, request) -> None:
      # POST /api/v1/tickets/{id}/articles via the app client -> 422, detail == fixture veto text
  ```
  Admin API: the plugin list shows `kind == "sync"` hooks with `schedule == "ticket.create"` etc.
- [ ] **Step 2: Run → FAIL. Step 3: Implement** the six call sites exactly per D4 (build ops from the inputs, convert ids to names with the existing `_queue_name`/`_state_name`/`_priority_name` helpers; `dataclasses.replace(params, …)` for the patch — map patched names back to ids). Regenerate OpenAPI.
- [ ] **Step 4: Run → PASS** + the existing ticket write/API suites (`uv run python -m pytest -q tests/test_ticket_write_service*.py tests/test_tickets_api*.py` — adjust the glob to the real names) to prove no regression without plugins; lint gates; commit `feat(plugins): before-hooks on agent ticket operations`.

### Task 6: Process transition actions and condition modules from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/process_points.py` (D5), `backend/src/tiqora_sdk/process.py`
- Modify: `backend/src/tiqora/process/actions/registry.py`, `backend/src/tiqora/process/condition_modules.py` (`sdk_stable=True`), `registrar.py` (`transition_action`, `condition_module`), `tiqora_sdk/__init__.py`, fixture (`_register_process`: action `SetContract` sets DF `PP55Contract` from `config["Contract"]` via `ac.plugin.tickets.set_dynamic_field(..., session=ac.session)`; MODE `action=raise` writes the DF then raises; condition `IsVip` returns `data.get("CustomerID") == "pp55-vip"`, MODE `cond=raise`), surface snapshot
- Test: `backend/tests/test_plugins_process_db.py` (both dialects, band 55080–55109)

**Interfaces:**
- Consumes: `TRANSITION_ACTIONS`, `CONDITION_MODULES`, `resolve_action`, `resolve_condition_module`, `execute_transition_action`, `call_plugin`, `call_plugin_sync`.
- Produces: D5; `SUBMODULES["tiqora_sdk.process"]`.

- [ ] **Step 1: Failing tests.**
  ```python
  def test_action_resolves_by_znuny_module_path(tmp_path, monkeypatch) -> None:
      boot_points_plugin(tmp_path, monkeypatch)
      entry = resolve_action(ZNUNY_MODULE_PREFIX + "Plugin_points_SetContract")
      assert entry is not None and entry.tiqora_only
  def test_action_rejects_core_name(...)        # registrar.transition_action("TicketStateSet", …) -> stages
                                                # "Plugin_points_TicketStateSet" (never "TicketStateSet"); "ticketStateSet" -> rejected
  async def test_contract_action_runs_in_engine_session(url_fixture, request) -> None:
      # execute_transition_action(session, action=TransitionActionConfig(module=…Plugin_points_SetContract,
      #   config={"Contract": "V-<OTRS_TICKET_TicketNumber>"}), ticket_id=…) -> DF value "V-<tn>" (smart tag replaced),
      # visible after session.commit()
  async def test_failing_action_rolls_back_savepoint_and_continues(url_fixture, request) -> None:
      # MODE action=raise -> returns None (no exception), the DF write done before the raise is NOT persisted,
      # an earlier write in the same session (before the action) IS persisted; log "plugin_transition_action_failed"
  def test_condition_module(...)                # resolve_condition_module(ZNUNY_VALIDATION_PREFIX + "Plugin_points_IsVip")
                                                # .validate({"CustomerID": "pp55-vip"}, "CustomerID") is True; MODE raise -> False;
                                                # returning "yes" (non-bool) -> False
  def test_designer_catalogue_lists_plugin_items(...)   # build_designer_catalog (process/designer/catalog.py) lists both
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D5. **Step 4: Run → PASS**, plus `uv run python -m pytest -q tests/test_process_*.py` (no regression); lint gates; commit `feat(plugins): process transition actions and condition modules from plugins`.

### Task 7: Template placeholders from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/placeholders.py` (D6)
- Modify: `backend/src/tiqora/channels/email/placeholder.py` (`PlaceholderContext.ticket_id`/`.user_id`, set in `load_placeholder_context`; `PLUGIN_` branch first in `_resolve_tag_raw`), `backend/src/tiqora/worker/notifications.py` (`_build_notification_context` sets `ticket_id`), `registrar.py` (`placeholder`), `tiqora_sdk/__init__.py`, `tiqora_sdk/templates.py` (create; `PlaceholderRequest`, `PlaceholderResolve` now — `NumberRequest`, `NumberBuild` join in Task 8), fixture (`_register_placeholders`: `GREETING` → `f"Hallo aus Ticket {req.ticket_id}"`, `SLOW` sleeps 5 s, `BROKEN` raises, `HTMLISH` returns `"<b>&</b>"`), surface snapshot
- Test: `backend/tests/test_plugins_placeholders_db.py` (both dialects, band 55110–55129)

**Interfaces:**
- Consumes: `expand_placeholders`, `PlaceholderContext`, `call_plugin`.
- Produces: D6; `SUBMODULES["tiqora_sdk.templates"]` (partial).

- [ ] **Step 1: Failing tests** (ticket seeded in band):
  ```python
  async def test_plugin_tag_resolves_both_prefixes(url_fixture, request) -> None:
      out = await expand_placeholders(session, SysConfig(session),
                                      "A <TIQORA_PLUGIN_POINTS_GREETING> B <OTRS_PLUGIN_POINTS_GREETING>", ticket_id=tid)
      assert out == f"A Hallo aus Ticket {tid} B Hallo aus Ticket {tid}"
  async def test_slow_and_broken_render_empty(url_fixture, request) -> None:
      # SLOW (timeout_ms=100) and BROKEN -> "" ; whole expansion < 0.5 s; core tags in the same text still resolve
  async def test_unknown_plugin_tag_empty_and_dash_in_znuny_mode(url_fixture, request) -> None:
      # <OTRS_PLUGIN_POINTS_NOPE> -> "" ; with PlaceholderContext(znuny_dash=True) -> "-"
  async def test_html_escaped_when_context_escapes(url_fixture, request) -> None:
      # escape_html=True -> "&lt;b&gt;&amp;&lt;/b&gt;"
  def test_secret_looking_names_rejected(...)    # registrar.placeholder("API_TOKEN", …) -> RegistrationRejected("…maskiert")
  def test_name_rules(...)                       # "greeting" (lowercase), "TICKET_TITLE" stays inside PLUGIN_POINTS_ (no shadowing):
                                                 # tag == "PLUGIN_POINTS_TICKET_TITLE"; core <OTRS_TICKET_TITLE> unaffected
  async def test_notification_context_has_ticket_id(url_fixture, request) -> None:
      # render a notification body containing <TIQORA_PLUGIN_POINTS_GREETING> through _render_message -> resolved
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D6. **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_placeholder*.py tests/test_notifications*.py` (no regression); lint gates; commit `feat(plugins): template placeholders from plugins`.

### Task 8: Ticket number generators from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/number_generators.py` (D7)
- Modify: `backend/src/tiqora/znuny/ticket_number.py` (`ticket_number_build`), `backend/src/tiqora/znuny/followup.py` (`get_tn_by_string(extra_patterns=…)`, `detect_followup` passes `plugin_tn_patterns()`), `registrar.py` (`number_generator`), `tiqora_sdk/__init__.py`, `tiqora_sdk/templates.py` (+ `NumberRequest`, `NumberBuild`), `backend/src/tiqora/api/v1/admin/plugins.py` (D17 number-generator endpoints + models), fixture (`_register_number_generators`: `Yearly` → `f"{now_local:%Y}-{counter:06d}"`, pattern `r"\d{4}-\d{6}"`; `Broken` driven by `MODE["tn"]` ∈ `raise|empty|long|nomatch|same`), surface snapshot, OpenAPI files
- Test: `backend/tests/test_plugins_number_generators_db.py` (both dialects, band 55130–55169), `backend/tests/test_followup_plugin_patterns.py` (pure)

**Interfaces:**
- Consumes: `ticket_number_counter_add`, `get_setting`, `set_setting`, `call_plugin_sync`.
- Produces: D7; endpoints of D17.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_plugin_generator_used_when_selected(url_fixture, request) -> None:
      await set_setting(session, SETTING_KEY, "Plugin_points_Yearly"); await session.commit()
      tn = await ticket_create_number(factory, SysConfig(session))
      assert re.fullmatch(r"\d{4}-\d{6}", tn)
  async def test_unset_uses_znuny_generator(url_fixture, request) -> None:    # setting "" -> DateChecksum-shaped number
  @pytest.mark.parametrize("mode", ["raise", "empty", "long", "nomatch"])
  async def test_broken_generator_falls_back(url_fixture, request, mode) -> None:
      # selected Broken + MODE["tn"]=mode -> number from the Znuny generator; log "plugin_ticket_number_invalid"
  async def test_collision_loop_uses_offset(url_fixture, request) -> None:
      # MODE "same" returns an existing ticket's tn -> after 3 collisions the Znuny generator is used; returns a fresh tn
  async def test_disabled_plugin_generator_falls_back(url_fixture, request) -> None:   # loaded state DISABLED -> Znuny scheme
  def test_registration_rules(...)    # pattern with capture group / invalid regex / > 200 chars, name "DateChecksum"
                                      # -> qualified "Plugin_points_DateChecksum" (no shadowing), label empty -> rejected
  async def test_admin_endpoints(url_fixture, request) -> None:
      # GET lists Yearly with an example matching its pattern and znuny_generator from SysConfig;
      # PUT {"key": "Plugin_points_Nope"} -> 422; PUT Yearly -> 200, setting stored; PUT null -> setting ""
  ```
  ```python
  # test_followup_plugin_patterns.py
  def test_followup_matches_plugin_and_znuny_numbers() -> None:
      kw = dict(hook="Ticket#", hook_divider="", generator="Kernel::System::Ticket::Number::DateChecksum",
                system_id="10")
      assert get_tn_by_string("Re: [Ticket#2026-000042] x", extra_patterns=[r"\d{4}-\d{6}"], **kw) == "2026-000042"
      assert get_tn_by_string("Re: [Ticket#2026101210000017] x", extra_patterns=[r"\d{4}-\d{6}"], **kw) == "2026101210000017"
      assert get_tn_by_string("Ticket: 2026-000042", extra_patterns=[r"\d{4}-\d{6}"], **kw) == "2026-000042"
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D7 + endpoints; regenerate OpenAPI.
- [ ] **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_ticket_number*.py tests/test_followup*.py`; lint gates; commit `feat(plugins): ticket number generators from plugins`.

### Task 9: Dynamic field types from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/df_types.py` (D8), `backend/src/tiqora_sdk/fields.py`
- Modify: `backend/src/tiqora/domain/dynamic_field_values.py` (`DynamicFieldConfig.config` + `plugin_type`; `_load_field` fills config; `set_ticket_dynamic_field` validation; `df_display_value` plugin display), `backend/src/tiqora/api/v1/admin/dynamic_fields.py` + `api/v1/admin/schemas.py` (`plugin_type` in create/out; `GET …/plugin-types`), `backend/src/tiqora/api/v1/reference.py` (`DynamicFieldDefOut.plugin_type`), `registrar.py` (`dynamic_field_type`), `tiqora_sdk/__init__.py`, fixture (`_register_df_types`: `Contract`, base `Text`, validate: `^V-\d{1,6}$` else `ValueError("Vertragsnummer muss wie V-123 aussehen")`, upper-cases input; display `f"Vertrag {v}"`; `config_model` with `prefix: str = "V-"`; MODE `df=raise` raises `RuntimeError`), surface snapshot, OpenAPI files
- Test: `backend/tests/test_plugins_df_types_db.py` (both dialects, band 55170–55209)

**Interfaces:**
- Consumes: `normalize_df_values`, `config_from_yaml`, `call_plugin_sync`.
- Produces: D8; `SUBMODULES["tiqora_sdk.fields"]`.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_admin_creates_plugin_field_as_base_type(url_fixture, request) -> None:
      # POST /api/v1/admin/dynamic-fields {name "PP55Vertrag", label, field_order, field_type "Text",
      #   plugin_type "Plugin_points_Contract", config {"TiqoraPluginConfig": {"prefix": "V-"}}} -> 201
      # dynamic_field row: field_type "Text"; config YAML contains TiqoraPluginType: Plugin_points_Contract
  async def test_create_rejects_mismatched_base_type_and_bad_config(url_fixture, request) -> None:
      # field_type "Dropdown" with that plugin_type -> 422; config {"TiqoraPluginConfig": {"prefix": 5}} -> 422 German text
  async def test_validate_normalizes_and_rejects(url_fixture, request) -> None:
      await update_dynamic_field(session, ticket_id=tid, field_name="PP55Vertrag", values=["v-12"], user_id=1)
      # stored "V-12"; values=["12"] -> InvalidInput("PP55Vertrag: Vertragsnummer muss wie V-123 aussehen"), nothing stored
  async def test_crashing_validator_fails_open(url_fixture, request) -> None:
      # MODE df=raise -> value stored as base Text would store it; log "plugin_call_failed"
  async def test_missing_plugin_behaves_as_base_type(url_fixture, request) -> None:
      # loaded state DISABLED -> "12" accepted
  async def test_display_and_reference(url_fixture, request) -> None:
      # df_display_value -> "Vertrag V-12"; GET /api/v1/reference/… dynamic field defs -> plugin_type set
  async def test_update_cannot_change_plugin_type(url_fixture, request) -> None:   # PATCH config without the key -> key kept;
                                                                                   # with another TiqoraPluginType -> 422
  def test_registration_rules(...)   # base_type "Password" rejected; name "Text" -> "Plugin_points_Text" (no shadowing)
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D8; regenerate OpenAPI.
- [ ] **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_dynamic_field*.py tests/test_admin_dynamic_fields*.py`; lint gates; commit `feat(plugins): dynamic field types from plugins`.

### Task 10: Inbound mail filters (pre/post) (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/mail_filters.py` (D9: all types and both points `INBOUND_MAIL_FILTERS`/`OUTBOUND_MAIL_FILTERS`, the inbound runners; `run_outbound_filters` and its wiring follow in Task 11), `backend/src/tiqora_sdk/mail.py` (complete D2 list)
- Modify: `backend/src/tiqora/channels/email/pipeline.py` (pre call after `apply_filters`; post call after commit), `registrar.py` (`mail_filter` — stage `pre`/`post` need `core.mail.inbound_filters`, `outbound` needs `core.mail.outbound_filters`; the outbound runner arrives in Task 11, registration already accepts it), `tiqora_sdk/__init__.py`, fixture (`_register_mail_filters`: pre `tagger` sets `X-OTRS-Priority: 5 very high` when subject contains "DRINGEND"; pre `spam` sets `X-OTRS-Ignore: yes` when `From` ends with `@spam.example.net`; pre `broken` driven by MODE `mail.pre` ∈ `raise|hang`; post `recorder` appends `(outcome, ticket_id)` to `CALLS`), surface snapshot
- Test: `backend/tests/test_plugins_mail_inbound_db.py` (both dialects, band 55210–55239)

**Interfaces:**
- Consumes: `apply_filters`, `_process_message_inner`, `process_message`, `PipelineResult`, `call_plugin`.
- Produces: D9 inbound; `SUBMODULES["tiqora_sdk.mail"]`.

- [ ] **Step 1: Read** `process_message` to find where the message's transaction commits (the post call goes right after it; note the location in the commit body).
- [ ] **Step 2: Failing tests** (mail account seeded in band; raw mails built with `email.message.EmailMessage`, addresses `@example.org`):
  ```python
  async def test_pre_filter_sets_priority(url_fixture, request) -> None:     # subject "DRINGEND: Drucker" -> ticket priority 5
  async def test_pre_filter_can_ignore(url_fixture, request) -> None:        # From x@spam.example.net -> outcome "ignored", no ticket
  async def test_pre_filter_crash_mail_still_processed(url_fixture, request) -> None:
      # MODE mail.pre=raise and =hang (timeout 1 s) -> ticket created as without the filter; elapsed < 3 s
  async def test_db_filters_run_before_plugin_filters(url_fixture, request) -> None:
      # a postmaster_filter row sets X-OTRS-Priority "2 low"; tagger overrides to "5 very high" -> plugin wins (runs after)
  async def test_post_filter_sees_committed_ticket(url_fixture, request) -> None:
      # recorder got ("new_ticket", tid) and the ticket is readable from a fresh session inside the filter
      # (fixture post filter calls ctx.tickets.get(tid) and records the title)
  async def test_post_filter_failure_does_not_change_result(url_fixture, request) -> None:
  ```
- [ ] **Step 3: Run → FAIL. Step 4: Implement** D9 inbound. **Step 5: Run → PASS** + `uv run python -m pytest -q tests/test_email_pipeline*.py tests/test_postmaster*.py`; lint gates; commit `feat(plugins): inbound mail filters before and after ticket matching`.

### Task 11: Outbound mail filters (backend)

**Files:**
- Modify: `backend/src/tiqora/plugins/mail_filters.py` (`run_outbound_filters`, `OutboundMail.add_header` rules), `backend/src/tiqora/channels/email/smtp.py` (`SmtpMailSender.send` first statement), fixture (`_register_outbound`: `stamp` adds `X-Points-Checked: 1`; `guard` raises `MailVetoed("Empfänger gesperrt")` when any recipient ends with `@blocked.example.net`; MODE `mail.out` ∈ `raise|hang|bad_header`)
- Test: `backend/tests/test_plugins_mail_outbound.py` (no DB: `SmtpMailSender` with `aiosmtplib.send` monkeypatched to capture the message)

**Interfaces:**
- Consumes: `SmtpMailSender`, `smtp_reply_codes`, `is_permanent_smtp_failure`.
- Produces: D9 outbound.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_stamp_header_added(monkeypatch) -> None:
      sent = _capture_aiosmtplib(monkeypatch)
      await SmtpMailSender(_settings()).send(_msg(to="kunde@example.org"))
      assert sent[0]["X-Points-Checked"] == "1"
  async def test_veto_is_permanent_550(monkeypatch) -> None:
      with pytest.raises(OutboundMailVetoed) as exc:
          await SmtpMailSender(_settings()).send(_msg(to="x@blocked.example.net"))
      assert smtp_reply_codes(exc.value) == [550] and is_permanent_smtp_failure(exc.value)
      assert "Empfänger gesperrt" in str(exc.value) and sent == []
  @pytest.mark.parametrize("mode", ["raise", "hang"])
  async def test_filter_crash_sends_unchanged(monkeypatch, mode) -> None:   # message bytes identical except no stamp from the
                                                                             # broken filter; hang bounded by timeout_seconds=1
  async def test_only_x_headers(monkeypatch) -> None:
      # MODE bad_header: filter tries add_header("Subject", "x") and add_header("X-Bad", "a\r\nBcc: y") -> ValueError inside
      # the filter -> filter failure -> mail sent with original Subject and without X-Bad / Bcc
  async def test_signed_message_body_untouched(monkeypatch) -> None:
      # a multipart/signed message: after the stamp, every MIME part's bytes are unchanged
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement.** **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_smtp*.py tests/test_outbound_reply*.py`; lint gates; commit `feat(plugins): outbound mail filters (headers and veto only)`.

### Task 12: Notification transports from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/notification_transports.py` (D10), `backend/src/tiqora_sdk/notifications.py`
- Modify: `backend/src/tiqora/worker/notifications.py` (`process_event` transport loop), `backend/src/tiqora/api/v1/admin/notification_events.py` (`GET …/transports`), `registrar.py` (`notification_transport`), `tiqora_sdk/__init__.py`, fixture (`_register_transports`: `Chat` appends `(recipient.kind, recipient.email, subject)` to `CALLS`; MODE `transport` ∈ `raise|hang`), surface snapshot, OpenAPI files
- Test: `backend/tests/test_plugins_notification_transports_db.py` (both dialects, band 55260–55299)

**Interfaces:**
- Consumes: `process_event`, `_render_message`, `_resolve_recipients`, `_Recipient`, `call_plugin`.
- Produces: D10; `SUBMODULES["tiqora_sdk.notifications"]`.

- [ ] **Step 1: Failing tests** (notification_event rows seeded in band with `Transports` lists; outbox event built like the existing notification tests do; `CapturingMailSender`):
  ```python
  async def test_plugin_only_transport(url_fixture, request) -> None:
      # Transports ["Plugin_points_Chat"] -> CALLS has one entry per recipient with the rendered subject; mail sender empty
  async def test_email_and_plugin(url_fixture, request) -> None:   # ["Email", "Plugin_points_Chat"] -> both, sent == 2 per recipient
  async def test_unknown_transport_ignored(url_fixture, request) -> None:   # ["SMS"] -> nothing sent, no error
  async def test_failing_transport_counted(url_fixture, request) -> None:   # MODE raise -> (sent, failed) == (n_email, n_recipients)
  async def test_hanging_transport_bounded(url_fixture, request) -> None:   # timeout 1 s -> tick returns within ~1 s per recipient
  async def test_no_history_for_plugin_transport(url_fixture, request) -> None:
  async def test_admin_lists_transports(url_fixture, request) -> None:
      # GET /api/v1/admin/notification-events/transports -> [{"key": "Email", "label": "E-Mail", "plugin_id": None},
      #   {"key": "Plugin_points_Chat", "label": "Chat (Test)", "plugin_id": "org.example.points"}]
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D10; regenerate OpenAPI. **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_notifications*.py`; lint gates; commit `feat(plugins): notification transports from plugins`.

### Task 13: Channel drivers — point, channel row, inbound `ingest` (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/channels.py` (D11: point, types, `PluginChannels.ingest`, `ensure_tiqora_channel_row`, `driver_for`), `backend/src/tiqora_sdk/channels.py`
- Modify: `backend/src/tiqora/channels/common.py` (`ensure_webchat_channel_row` → wrapper of `ensure_tiqora_channel_row`), `backend/src/tiqora/domain/ticket_write_service.py` (module-level `add_article`: plugin channel name → `ensure_tiqora_channel_row`), `backend/src/tiqora/plugins/context.py` (`make_plugin_context`/`minimal_context` set `channels=PluginChannels(plugin_id)`), `registrar.py` (`channel`), `tiqora_sdk/__init__.py`, fixture (`_register_channels`: channel `Chat` with `ChannelCapabilities(max_length=500)`, send arrives in Task 14 — register with `send=_send` already, `_send` records to `CALLS` and returns `DeliveryResult(external_id="m-1")`; a public route `POST /inbound` (capability `core.http.public_routes` already in phase 1) that calls `ctx.channels.ingest("Chat", InboundChannelMessage(...))`), surface snapshot
- Test: `backend/tests/test_plugins_channels_inbound_db.py` (both dialects, band 55300–55339)

**Interfaces:**
- Consumes: `ensure_channel_row`, `EMPTY_TABLES_CHANNEL_DATA`, `resolve_ticket_for_inbound`, `resolve_customer_by_phone`, `add_article`, `CustomerService`.
- Produces: D11 inbound parts; `SUBMODULES["tiqora_sdk.channels"]`.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_ingest_creates_ticket_on_plugin_channel(url_fixture, request) -> None:
      res = await ctx.channels.ingest("Chat", InboundChannelMessage(sender="pp55-cust@example.org", body="Hallo",
                                                                    external_id="e-1"))
      # res.created; communication_channel row "Plugin_points_Chat" with module …CommunicationChannel::Internal,
      # package_name "Tiqora", channel_data == EMPTY_TABLES_CHANNEL_DATA; article channel id = that row, sender customer,
      # customer_user_id = pp55-cust (e-mail match)
  async def test_ingest_is_idempotent(url_fixture, request) -> None:   # same external_id twice -> duplicate=True, 1 article
  async def test_ingest_appends_to_open_ticket(url_fixture, request) -> None:   # second message, other external_id -> same ticket
  async def test_ingest_phone_match_and_unknown_sender(url_fixture, request) -> None:
  async def test_foreign_channel_name_refused(url_fixture, request) -> None:    # ingest("Fremd", …) -> PluginWriteError
  async def test_public_inbound_route(url_fixture, request) -> None:            # POST /api/plugins/org.example.points/inbound -> 200
  def test_registration_rules(...)    # name "email"/"Email" -> key "plugin_points_email" (no shadowing of "email");
                                      # max_length 0 -> rejected; timeout_seconds 31 -> rejected; sync send -> rejected
  async def test_webchat_row_unchanged(url_fixture, request) -> None:  # ensure_webchat_channel_row still yields the same row shape
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement.** **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_webchat*.py tests/test_channels*.py`; lint gates; commit `feat(plugins): channel drivers and inbound messages for plugin channels`.

### Task 14: Channel drivers — agent replies, composer list, AI gate (backend)

**Files:**
- Modify: `backend/src/tiqora/plugins/channels.py` (`deliver_plugin_channel_reply`, `ChannelDeliveryError`), `backend/src/tiqora/domain/ticket_write_service.py` (`TicketWriteService.add_article` plugin branch after the sync hook), `backend/src/tiqora/api/v1/tickets.py` (`_map_exc`: `ChannelDeliveryError` → 502 with its German text), `backend/src/tiqora/ai/gate.py` (`is_tiqora_only_channel` used where `TIQORA_ONLY_CHANNELS` is tested; grep all uses incl. `ai/auto_worker.py`), create `backend/src/tiqora/api/v1/plugin_channels.py` (`GET /api/v1/channels/plugin`) + register it in `api/v1/__init__.py`, fixture send behaviour by MODE `channel.send` ∈ `raise|hang`, OpenAPI files
- Test: `backend/tests/test_plugins_channels_outbound_db.py` (both dialects, band 55340–55369)

**Interfaces:**
- Consumes: Task 13 names, `run_sync_hooks` (already in `add_article`), `call_plugin`.
- Produces: D11 outbound; endpoint of D17.

- [ ] **Step 1: Failing tests.**
  ```python
  async def test_agent_reply_sends_then_stores(url_fixture, request) -> None:
      aid = await svc.add_article(55343, tid, ArticleIn(sender_type="agent", channel="plugin_points_chat", subject="",
                                  body="Antwort", to_address="pp55-cust@example.org", is_visible_for_customer=True))
      # CALLS has the OutboundChannelMessage (ticket summary, agent id, to, body); stored article: channel row
      # Plugin_points_Chat, sender agent, message id "<plugin_points_chat.m-1@tiqora.invalid>"
  async def test_send_failure_stores_nothing(url_fixture, request) -> None:
      # MODE raise -> ChannelDeliveryError; article count unchanged; API -> 502 with "Zustellung über „Chat (Test)“ fehlgeschlagen"
  async def test_send_timeout_bounded(url_fixture, request) -> None:    # MODE hang, driver timeout 1 s -> error within ~1 s
  async def test_capability_limits(url_fixture, request) -> None:       # body 501 chars -> InvalidInput (422); attachment -> 422
  async def test_inbound_only_channel_cannot_reply(url_fixture, request) -> None:
  async def test_veto_hook_runs_before_channel_send(url_fixture, request) -> None:   # MODE hook.article.create=veto -> no CALLS entry
  async def test_composer_list(url_fixture, request) -> None:
      # GET /api/v1/channels/plugin -> [{"key": "plugin_points_chat", "name": "Plugin_points_Chat", "label": "Chat (Test)",
      #   "plugin_id": "org.example.points", "capabilities": {...max_length 500...}, "can_reply": true}]
  def test_plugin_channels_are_tiqora_only(tmp_path, monkeypatch) -> None:
      boot_points_plugin(tmp_path, monkeypatch)
      assert is_tiqora_only_channel("plugin_points_chat") and is_tiqora_only_channel("telegram")
      assert not is_tiqora_only_channel("email")
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement**; regenerate OpenAPI. **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_tickets_telegram_api.py tests/test_ai_gate*.py`; lint gates; commit `feat(plugins): agent replies over plugin channels`.

### Task 15: Password auth providers for agents and customers (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/auth_providers.py` (D12), `backend/src/tiqora_sdk/auth.py`
- Modify: `backend/src/tiqora/api/v1/auth.py` (`login`), `backend/src/tiqora/api/portal/auth.py` (`login`), `registrar.py` (`password_provider`), `tiqora_sdk/__init__.py`, fixture (`_register_auth`: provider `directory` (audience both) accepts password `"richtig-<login>"` and returns the login unchanged; MODE `auth` ∈ `raise|hang|other_login|unknown_login|invalid_login` — `other_login` returns `"pp55-agent2"`, `unknown_login` `"gibt-es-nicht"`, `invalid_login` a login whose `valid_id = 2`), surface snapshot
- Test: `backend/tests/test_plugins_auth_db.py` (both dialects, band 55370–55389; agent `pp55-agent` with no local password match, agent `pp55-2fa` with TOTP enabled, customer `pp55-portal`)

**Interfaces:**
- Consumes: `AuthService.get_user_by_login`, `CustomerAuthService.get_customer_by_login`, `ldap_sign_in`, the rate limiter, `call_plugin`, `PluginCallBudget`.
- Produces: D12; `SUBMODULES["tiqora_sdk.auth"]`.

- [ ] **Step 1: Failing tests** (through the app client, like the existing login tests; LDAP has no providers configured, so `ldap_sign_in` returns `None`):
  ```python
  async def test_agent_login_via_plugin(url_fixture, request) -> None:
      r = await client.post("/api/v1/auth/login", json={"login": "pp55-agent", "password": "richtig-pp55-agent"})
      assert r.status_code == 200 and r.json()["user"]["login"] == "pp55-agent"
  async def test_customer_login_via_plugin(url_fixture, request) -> None:   # /api/portal/auth/login -> 200
  @pytest.mark.parametrize("mode", ["raise", "hang", "unknown_login", "invalid_login"])
  async def test_provider_crash_is_no(url_fixture, request, mode) -> None:   # -> 401 "Invalid credentials"; hang bounded by
                                                                             # timeout_seconds=1
  async def test_other_login_mapping_allowed(url_fixture, request) -> None:  # MODE other_login -> logged in as pp55-agent2, logged
  async def test_password_never_logged(url_fixture, request) -> None:
      with structlog.testing.capture_logs() as logs:
          await client.post(..., json={"login": "pp55-agent", "password": "falsch-geheim-55"})
      assert "falsch-geheim-55" not in repr(logs)
  async def test_2fa_still_required(url_fixture, request) -> None:   # pp55-2fa -> pending_2fa True, no full session
  async def test_rate_limit_counts_plugin_failures(url_fixture, request) -> None:   # N failed plugin attempts -> 429 like local ones
  async def test_local_password_wins_without_calling_plugin(url_fixture, request) -> None:   # CALLS empty when users.pw matches
  async def test_audience_respected(url_fixture, request) -> None:   # a customer-only provider is never asked on the agent login
  async def test_chain_budget(url_fixture, request, monkeypatch) -> None:   # CHAIN_BUDGET_SECONDS=0.5, two hanging providers -> < 1 s
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D12 (grep `auth_method ==` / `auth_method in` and treat `"plugin"` like `"ldap"`). **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_auth*.py tests/test_portal_auth*.py tests/test_ldap*.py`; lint gates; commit `feat(plugins): password auth providers for agent and customer login`.

### Task 16: Customer sources and the customer-company write service (backend)

**Files:**
- Create: `backend/src/tiqora/domain/customer_company_service.py` (D13; move the insert code out of `api/v1/admin/customers.py create_customer_company`, the route calls it — behaviour unchanged), `backend/src/tiqora/plugins/customer_sources.py` (D13), `backend/src/tiqora_sdk/customers.py`
- Modify: `backend/src/tiqora/api/v1/admin/customers.py`, `backend/src/tiqora/api/v1/reference.py` (`customer_search` → `external`), `backend/src/tiqora/api/v1/customers.py` (`POST /external-import`), `backend/src/tiqora/channels/email/pipeline.py` (`_resolve_customer` miss), `registrar.py` (`customer_source`), `tiqora_sdk/__init__.py`, fixture (`_register_customer_source`: directory of three made-up people `pp55-ext1` … at `example.org`, one with company `PP55-FIRMA`; `by_email` set; `materialize_on_inbound=True`; MODE `cs` ∈ `raise|hang`), surface snapshot, OpenAPI files
- Test: `backend/tests/test_customer_company_service_db.py`, `backend/tests/test_plugins_customer_sources_db.py` (both dialects, band 55390–55429)

**Interfaces:**
- Consumes: `create_customer_user`, `check_email`, `unusable_password_hash`, `SYSTEM_USER_ID`, `CustomerService.quick_search`, `call_plugin`, `PluginCallBudget`.
- Produces: `ensure_customer_company` (Cross-plan contract signature), D13; `SUBMODULES["tiqora_sdk.customers"]`.

- [ ] **Step 1: Failing tests.**
  ```python
  # test_customer_company_service_db.py
  async def test_insert_then_noop(url_fixture, request) -> None:
      assert await ensure_customer_company(session, customer_id="PP55-FIRMA", name="Beispiel GmbH", actor_id=1) is True
      assert await ensure_customer_company(session, customer_id="PP55-FIRMA", name="Anders", actor_id=1) is False
      # row name still "Beispiel GmbH"; valid_id 1; create_by 1; cache invalidation row written as the admin route did
  async def test_admin_route_unchanged(url_fixture, request) -> None:  # existing admin create test still passes (run it)
  ```
  ```python
  # test_plugins_customer_sources_db.py
  async def test_search_returns_external_when_db_has_few(url_fixture, request) -> None:
      # GET /api/v1/reference/customer-search?q=pp55-ext -> contacts [], external 3 entries with source label
  async def test_failing_source_is_skipped(url_fixture, request) -> None:   # MODE raise/hang -> external [], 200, < 6 s
  async def test_import_creates_customer_and_company(url_fixture, request) -> None:
      # POST /api/v1/customers/external-import {"source": "org.example.points:directory", "login": "pp55-ext2"} -> 200;
      # customer_user row with unusable password; customer_company PP55-FIRMA inserted once
  async def test_import_existing_login_returns_it_unchanged(url_fixture, request) -> None:
  async def test_email_conflict_409(url_fixture, request) -> None:   # local customer already owns pp55-ext3@example.org
  async def test_unknown_login_404(url_fixture, request) -> None:
  async def test_inbound_mail_unknown_sender_taken_over(url_fixture, request) -> None:
      # pipeline with From pp55-ext1@example.org -> ticket customer_user_id == "pp55-ext1", row created
  async def test_inbound_lookup_failure_keeps_old_behaviour(url_fixture, request) -> None:   # MODE raise -> unknown customer as today
  ```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3a: Extract** `ensure_customer_company`; the admin route calls it. Run `test_customer_company_service_db.py` and the existing admin customer tests (`ls backend/tests | grep -i customer`) → PASS; lint gates; commit `refactor(customers): customer company insert as a domain service`.
- [ ] **Step 3b: Implement** the sources (D13); regenerate OpenAPI.
- [ ] **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_admin_customers*.py tests/test_reference*.py tests/test_email_pipeline*.py`; lint gates; commit `feat(plugins): customer sources with search, import and inbound take-over`.

### Task 17: GenericInterface operations and mappings from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/gi_points.py` (D14: operations, mappings, and all D14 types incl. the invoker types used by Task 18), `backend/src/tiqora_sdk/gi.py` (complete D2 list)
- Modify: `backend/src/tiqora/api/compat/webservices.py` (`handler=` line), `backend/src/tiqora/api/compat/router.py` (`_dispatch_operation` first block), `backend/src/tiqora/api/compat/mapping/__init__.py` (`build_mapper` plugin factory), `registrar.py` (`gi_operation`, `gi_mapping`), `tiqora_sdk/__init__.py`, fixture (`_register_gi`: operation `ContractGet` authenticates via `ctx.authenticate(data)` and returns `{"Success": 1, "Data": {"Contract": "V-1", "UserID": principal.user_id}}`, MODE `gi.op` ∈ `raise|hang|nondict`; mapping `Upper` upper-cases every string value, MODE `gi.map=raise`), surface snapshot
- Test: `backend/tests/test_plugins_gi_provider_db.py` (both dialects, band 55430–55449; webservice row inserted with `tests/_gi_fixtures.insert_webservice`)

**Interfaces:**
- Consumes: `OPERATION_TYPES`, `_dispatch_operation`, `run_provider`, `OperationFault`, `_auth_from_params`, `MAPPER_FACTORIES`, `build_mapper`, `call_plugin`, `call_plugin_sync`.
- Produces: D14 operations/mappings; `SUBMODULES["tiqora_sdk.gi"]`.

- [ ] **Step 1: Failing tests** (webservice `PP55Contracts`, REST, operation `ContractGet` with `Type: Plugin_points::ContractGet`, route `/Contract/:TicketID`, inbound mapping `Type: Plugin_points_Upper`):
  ```python
  async def test_plugin_operation_via_rest(url_fixture, request) -> None:
      # POST /znuny-compat/Webservice/PP55Contracts/Contract/1 with UserLogin/Password of a seeded agent ->
      # 200 {"Contract": "V-1", "UserID": <agent id>}; debugger rows written like a core operation
  async def test_auth_failure_returns_znuny_error(url_fixture, request) -> None:   # wrong password -> Znuny AuthFail shape, 401
  @pytest.mark.parametrize("mode", ["raise", "hang", "nondict"])
  async def test_operation_failure_is_fault(url_fixture, request, mode) -> None:
      # -> 500 with faultstring "OperationObject returned an error, cancelling Request"; hang bounded (timeout 1 s)
  async def test_core_type_wins(…)                  # registrar refuses "Ticket::TicketCreate"-like names anyway; a webservice with
                                                    # Type Ticket::TicketGet still routes to the core handler
  async def test_disabled_plugin_not_implemented(…) # loaded DISABLED -> Operation.NotImplemented (501) at parse time handler None
  def test_plugin_mapping(...)                      # build_mapper({"Type": "Plugin_points_Upper", "Config": {...}}).map({"a": "x"})
                                                    # == {"a": "X"}; MODE raise -> MappingError("Plugin_points_Upper: …")
  def test_unknown_mapping_type_unchanged(...)      # "Nope" -> MappingConfigError exactly as before
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D14 operations + mappings. **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_compat*.py tests/test_gi_mapping*.py`; lint gates; commit `feat(plugins): GenericInterface operations and mappings from plugins`.

### Task 18: GenericInterface invokers from plugins (backend)

**Files:**
- Modify: `backend/src/tiqora/plugins/gi_points.py` (invokers), `backend/src/tiqora/gi/config.py` (`supported`), `backend/src/tiqora/gi/requester.py` (prepare / response steps dispatch), `registrar.py` (`gi_invoker`), `tiqora_sdk/__init__.py`, `tiqora_sdk/gi.py` (no new names: Task 17 already defines and exports every D14 type incl. `InvokerContext`, `InvokerResponse`, `InvokerPrepare`, `InvokerHandle`; this task adds `GiInvoker`, `GI_INVOKERS` and the wiring), fixture (`_register_gi_invoker`: `ContractSync` prepare returns `{"TicketID": data["TicketID"], "Hello": "Welt"}` or `None` when `MODE["gi.inv"] == "skip"`; `raise|hang` modes; `handle_response` records `(success, data)` in `CALLS`)
- Test: `backend/tests/test_plugins_gi_invoker_db.py` (both dialects, band 55450–55469; REST transport via `httpx.MockTransport` as in `backend/tests/test_gi_requester*.py`)

**Interfaces:**
- Consumes: `SUPPORTED_INVOKER_TYPES`, `InvokerConfig`, `run_requester`, `prepare_request`, `handle_response`, Task 17 types.
- Produces: D14 invokers.

- [ ] **Step 1: Failing tests.**
  ```python
  def test_plugin_invoker_supported(...)            # parse_requester with Type "Plugin_points::ContractSync" -> supported True;
                                                    # with the plugin disabled -> supported False ("nicht unterstützt" as today)
  async def test_requester_runs_plugin_invoker(url_fixture, request) -> None:
      # run_requester(...) for a ticket -> the mock remote received {"TicketID": …, "Hello": "Welt"} (after outbound mapping);
      # handle_response recorded (True, <mapped response>)
  async def test_prepare_none_is_stop_communication(url_fixture, request) -> None:
      # MODE skip -> nothing sent, outcome success, debugger notice "Erweiterung hat den Aufruf übersprungen"
  @pytest.mark.parametrize("mode", ["raise", "hang"])
  async def test_prepare_failure_uses_existing_error_path(url_fixture, request, mode) -> None:
      # -> handle_failure("RequesterRequestPrepare", …): debugger error row, retry scheduling as for core invokers
  async def test_core_generic_invoker_unchanged(url_fixture, request) -> None:   # Ticket::Generic run still byte-identical
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement.** **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_gi_*.py`; lint gates; commit `feat(plugins): GenericInterface invokers from plugins`.

### Task 19: Report sources and built-in reports from plugins (backend)

**Files:**
- Create: `backend/src/tiqora/plugins/report_points.py` (D15), `backend/src/tiqora_sdk/reporting.py`
- Modify: `backend/src/tiqora/reporting/semantic.py`, `backend/src/tiqora/reporting/catalogue.py` (`sdk_stable=True`; `BuiltinReport.plugin_id` + properties), `registrar.py` (`report_source`, `builtin_report`), `tiqora_sdk/__init__.py`, fixture (`_register_reports`: source `plugin.points.articles_per_channel` over Znuny `article` ⋈ `ticket` (integer join) with dimension `channel` (`article.communication_channel_id`), measure `count`, `scope_column = ticket.queue_id`, labels `plugin.org.example.points:reports.…`; built-in report `articles_by_channel` on that source; a second source kept in the fixture **unregistered** for negative tests), surface snapshot, OpenAPI files if `BuiltinReport` output models change
- Test: `backend/tests/test_plugins_reports_db.py` (both dialects, band 55470–55489)

**Interfaces:**
- Consumes: `REPORT_SOURCES`, `BUILTIN_REPORTS`, `ReportSource`, `CompileContext`, the report compiler/runner used by `backend/tests/test_reporting_*.py`.
- Produces: D15; `SUBMODULES["tiqora_sdk.reporting"]`.

- [ ] **Step 1: Failing tests.**
  ```python
  def test_source_tables_checked(...) -> None:
      # registrar.report_source over tiqora_report_fact -> RegistrationRejected; over article + tqp_points_x -> rejected (mix);
      # over tqp_other_x -> rejected; over tqp_points_x only -> accepted (table need not exist); key "articles" (no prefix) ->
      # rejected; label_key "reports.x" -> rejected; requires_facts=True -> rejected
  async def test_source_scope_enforced(url_fixture, request) -> None:
      # two tickets in queues A (agent has ro) and B (no access) with articles; run the built-in report as that agent ->
      # only queue-A articles counted
  async def test_builtin_report_keys(...) -> None:
      # catalogue lists key "plugin.org.example.points.articles_by_channel", name "articles_by_channel",
      # title_key "plugin.org.example.points:reports.articles_by_channel.title"; core built-ins unchanged
  async def test_disabled_plugin_reports_hidden(…)   # inactive plugin -> source/report not listed, a saved report on it answers
                                                     # with the existing "unknown source" error (no 500)
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement** D15 (inactive-plugin filtering where the catalogue/sources are listed: filter by `is_active(owner)` using `registry.registrations(point)` owners). **Step 4: Run → PASS** + `uv run python -m pytest -q tests/test_reporting_*.py`; lint gates; regenerate OpenAPI if needed; commit `feat(plugins): report sources and built-in reports from plugins`.

### Task 20: Frontend — admin: capability labels, sync hooks, number-generator switch, notification Versandwege, plugin DF types (frontend)

**Files:**
- Create: `frontend/src/lib/pluginPointsApi.ts` (wrappers: `numberGenerators()`, `setNumberGenerator(key | null)`, `notificationTransports()`, `dfPluginTypes()`, `pluginChannels()`, `externalCustomerImport(source, login)` — the last two used in Task 21)
- Modify: `frontend/src/routes/admin/PluginsPage.tsx` (`POINT_KEYS` for every D2 capability key → D18 label keys; hook rows of kind `sync` show `admin.plugins.hooks.kind.sync` + the operation label; role `ai-worker` label; a "Ticketnummer-Schema" box on the card of a plugin that declares `core.tickets.number_generators`: example, "Für neue Tickets verwenden" button / "Wird für neue Tickets verwendet" chip / "Znuny-Schema verwenden", with `numberGenerator.znunyNote`), `frontend/src/routes/admin/NotificationEventsPage.tsx` (checkbox group "Versandwege" bound to the item `Transports`; existing values not in the list stay untouched and are shown read-only with their raw key), `frontend/src/routes/admin/DynamicFieldsPage.tsx` + `frontend/src/components/admin/DynamicFieldConfigEditor.tsx` (create: optional select "Art aus Erweiterung"; choosing one fixes the base type select, shows `pluginTypeHint`, renders `config_schema` with `SchemaForm` under `TiqoraPluginConfig`), `frontend/src/demo/mockData.ts`, `frontend/e2e/fixtures/mock-api.ts`, `frontend/src/test/pluginFixtures.ts` (hook kind sync, ai-worker role row), i18n `en.json`/`de.json` (D18 admin keys) + propagation
- Test: `PluginsPage.test.tsx`, `NotificationEventsPage.test.tsx`, `DynamicFieldsPage.test.tsx` (extend or create)

**Interfaces:**
- Consumes: OpenAPI schemas from Tasks 5, 8, 9, 12 (`Schemas["NumberGeneratorsOut"]`, …).
- Produces: `pluginPointsApi`.

- [ ] **Step 1: Failing tests.**
  ```tsx
  it("labels every phase-2 capability", async () => {
    // helloPlugin({ capabilities: [ext("core.events.sync_hooks"), ext("core.tickets.number_generators"), …all D2 keys] })
    // -> each D18 German text visible; no "other" fallback text rendered
  });
  it("shows sync hooks with their operation", async () => {
    // hooks: [{ kind: "sync", schedule: "ticket.create", tripped: true, … }] -> "Prüfung vor dem Speichern", "Ticket anlegen",
    // "Angehalten nach 5 Fehlern", "Erneut versuchen" button
  });
  it("switches the ticket number scheme", async () => {
    // GET number-generators -> {active: null, znuny_generator: "DateChecksum", available: [{key: "Plugin_points_Yearly", …,
    // example: "2026-000001"}]}; click "Für neue Tickets verwenden" -> PUT {key: "Plugin_points_Yearly"}; note text visible
  });
  it("edits Versandwege and keeps unknown transports", async () => {
    // item Transports ["Email", "SMS"]; transports list Email + Plugin_points_Chat -> check Chat -> save body
    // Transports ["Email", "SMS", "Plugin_points_Chat"]
  });
  it("creates a dynamic field with a plugin type", async () => {
    // pick "Vertragsnummer (Test)" -> base type select disabled on "Text"; POST includes plugin_type and TiqoraPluginConfig
  });
  ```
- [ ] **Step 2: Run → FAIL** (`cd frontend && pnpm test --run src/routes/admin`). **Step 3: Implement**; i18n de+en, then `node frontend/scripts/propagate-i18n-keys.mjs`. **Step 4:** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; demo-mode Playwright check of the three admin pages (light + dark); delete `.playwright-mcp`.
- [ ] **Step 5: Commit** `feat(plugins): admin UI for plugin extension points`.

### Task 21: Frontend — agent: replies over plugin channels, external customers, plugin report labels (frontend)

**Files:**
- Modify: `frontend/src/components/agent/ReplyDialog.tsx` (when `channelName` equals a plugin channel's `name` from `pluginChannels()`: send `channel: <key>`; hide subject/Cc/Bcc unless `capabilities.subject`; show a live character counter and block send above `max_length` with `ticket.reply.pluginChannelTooLong`; hide the attachment button unless `capabilities.attachments`; title `ticket.reply.pluginChannel`; 502 error text shown as returned), `frontend/src/routes/agent/NewTicketPage.tsx` / `frontend/src/components/agent/SmartSearchBar.tsx` (wherever `/reference/customer-search` results are listed: a section "Aus Kundenverzeichnis" with `aus {{label}}` badges and an "Übernehmen" button → `externalCustomerImport` → select the returned customer; 409 → `customers.external.conflict`), `frontend/src/routes/agent/ReportsPage.tsx`, `ReportBuilderPage.tsx`, `ReportViewPage.tsx` (before rendering a label key starting `plugin.`, `await ensurePluginI18n(pluginId)` — derive the id from the key `plugin.<id>:…`; render the key's English fallback until loaded), `frontend/src/demo/mockData.ts`, `frontend/e2e/fixtures/mock-api.ts` (`/api/v1/channels/plugin` → `[]` default), i18n (D18 agent keys) + propagation
- Test: `ReplyDialog.test.tsx`, `NewTicketPage.test.tsx` (or the search component's test), `ReportsPage.test.tsx`

**Interfaces:**
- Consumes: `pluginPointsApi` (Task 20), OpenAPI from Tasks 14, 16, 19, `ensurePluginI18n`.
- Produces: none.

- [ ] **Step 1: Failing tests.**
  ```tsx
  it("replies over a plugin channel with its limits", async () => {
    // channelName "Plugin_points_Chat"; pluginChannels -> [{key: "plugin_points_chat", name: "Plugin_points_Chat",
    // label: "Chat (Test)", capabilities: {subject: false, html: false, attachments: false, max_length: 10}, can_reply: true}]
    // -> no subject field, no attach button; typing 11 chars disables "Senden" and shows the limit text;
    // 10 chars -> POST body.channel === "plugin_points_chat"
  });
  it("offers external customers and imports one", async () => {
    // customer-search -> {companies: [], contacts: [], external: [{source: "org.example.points:directory",
    // source_label: "Testverzeichnis", login: "pp55-ext1", …}]} -> badge "aus Testverzeichnis"; click "Übernehmen" ->
    // POST external-import; the customer is selected in the form
  });
  it("loads plugin i18n for plugin reports", async () => {
    // catalogue with key "plugin.org.example.points.articles_by_channel" -> ensurePluginI18n called with "org.example.points"
  });
  ```
- [ ] **Step 2: Run → FAIL. Step 3: Implement**; i18n + propagation. **Step 4:** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS; demo-mode Playwright check of the reply dialog on a plugin-channel article and the new-ticket customer search; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** `feat(plugins): agent UI for plugin channels, external customers and plugin reports`.

### Task 22: Golden — Znuny reads plugin-channel articles and plugin DF fields (golden, peers 6.5 and 7.3)

**Files:**
- Create: `tests/golden/test_plugin_artefacts.py` (band 55490–55499, pattern `tests/golden/test_webchat_articles.py`)

**Interfaces:**
- Consumes: `znuny_perl_eval`, `znuny_console`, Task 13 `ensure_tiqora_channel_row` + `add_article`, Task 9 admin create path.
- Produces: golden evidence for the "Parallel operation" table.

- [ ] **Step 1: Write the tests** (run on both peers per `tests/golden/README-multi-peer.md`):
  ```python
  def test_znuny_reads_plugin_channel_article(...) -> None:
      # Tiqora: ensure_tiqora_channel_row("Plugin_points_Chat") + add_article(channel "plugin_points_chat", body "Hallo Welt",
      # one attachment) via the write service (fixture booted in-process like the webchat golden test does)
      # Znuny perl: BackendForArticle -> ref ends with "::Internal" (not "::Invalid"); ArticleGet body == "Hallo Welt";
      # attachment listed; `Admin::CommunicationChannel::Sync` keeps the row; TicketDelete returns 1
  def test_znuny_reads_plugin_dynamic_field(...) -> None:
      # Tiqora admin route creates DF "PP55Vertrag" (Text, TiqoraPluginType) and sets "V-12" on a ticket;
      # Znuny perl: DynamicFieldGet(Name => "PP55Vertrag") -> FieldType "Text", Config->{TiqoraPluginType} eq "Plugin_points_Contract";
      # DynamicFieldValueGet / backend ValueGet -> "V-12"; DisplayValueRender does not die
  ```
- [ ] **Step 2: Run** for each peer: `GOLDEN_PEER=znuny-6.5 just golden-up && GOLDEN_PEER=znuny-6.5 just golden-seed`, then `eval "$(python3 tests/golden/peer_env.py znuny-6.5)" && export GOLDEN=1 GOLDEN_PEER GOLDEN_COMPOSE_PROJECT GOLDEN_DB_URL GOLDEN_DB_ASYNC_URL && cd backend && uv run python -m pytest -q -m golden ../tests/golden/test_plugin_artefacts.py`, `just golden-clean`; repeat with `znuny-7.3` (in a worktree without source trees start the cached peer image as `tests/golden/README-multi-peer.md` → "A peer without its source tree" describes) → PASS on 6.5 and 7.3 (if Docker/peers are unavailable in the executor's environment, record "skipped: no golden peers" in the report — the controller runs it before release). **Step 3: Commit** `test(golden): Znuny reads plugin channel articles and plugin dynamic fields`.

### Task 23: Documentation and full verification

**Files:**
- Modify: `docs/plugins.md` (developer part: one section per point — what it does, registrar call, SDK types, failure behaviour, process roles (D1.3), a runnable example taken from `tq_fixture_points`; the "Plugin-qualified names" table (D1.1); sync hooks incl. budget/breaker/fail open-closed and the list of NOT hooked paths; write services and `as_agent`; admin part (German): capability texts, "Prüfung vor dem Speichern", Ticketnummer-Schema, Versandwege, Kundenverzeichnis, Anmeldung über Erweiterung; limits = Deviations 1–10 in plain words; SDK 0.2.0 change list), `docs/parallel-operation.md` (the "Parallel operation with Znuny" table), `docs/architecture.md` (Plugins subsection: list of points with their choke points; ai-worker role), `docs/compatibility.md` (OPM row: phase-2a registries now covered), `docs/superpowers/specs/2026-10-08-plugin-system-design.md` (status line only: "Phase 2a implemented 2026-10-xx — see plan …"), `packages/plugin-sdk/README.md` (version), `docs/deployment.md` (ai-worker now loads plugins: same image/volume as the other roles)
- No change: `site/compare.html` (release task of the wave)

- [ ] **Step 1: Write the docs** (English developer part, German admin part, examples only with `example.org`/`.test`).
- [ ] **Step 2: Full backend run** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → all pass; lint gates clean; `rtk git diff --stat backend/tests/db_leak_baseline.txt` empty.
- [ ] **Step 3: Full frontend run** — `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs && pnpm build && node scripts/check-plugin-runtime.mjs`; `cd ../packages/plugin-sdk && pnpm check:preset` → all pass.
- [ ] **Step 4: OpenAPI drift check** — regenerate both files; `rtk git diff --stat packages/api-client/openapi.json docs/api/openapi.json` empty.
- [ ] **Step 5: Surface check** — `uv run python -m pytest -q tests/test_tiqora_sdk_surface.py` and `grep -c "def " backend/src/tiqora_sdk/__init__.py` vs. D2's method list: every registrar method of D2 has a protocol twin (the snapshot test asserts it).
- [ ] **Step 6: Commit** `docs(plugins): phase 2a extension points, sync hooks, write services`.

---

## Self-review notes (for the executor)

- **Spec coverage (phase-2 row, this plan's half):** channels → Tasks 13, 14, 21; auth → 15; customer backends → 16, 21; DF types → 9, 20, 22; process → 6; GI → 17, 18; reporting → 19, 21; notification transports → 12, 20; mail filters → 10, 11; placeholders → 7; number generator → 8, 20; sync hooks → 4, 5, 20. Spec "Tests: each extension point: contract test with a fixture plugin, both dialects" → `tq_fixture_points` + one DB test module per point (pure modules where no SQL is involved: Task 11 runs without DB because `SmtpMailSender` touches none). §6 "isolation of failures … sync hooks fail open or closed as the hook declares" → D1.2 + D4.
- **Not in this plan:** plugin migrations/`tqp_` tables, runtime install, signatures, repository (plugins2b); the items of open question 15.
- **Type/name consistency:** the registrar method names in D2 are the ones the fixture and the snapshot test use; qualified names are produced only by `naming.qualified`/`channel_key`; every point's `item_key` matches the "stored as" column of D1.1.
- **Every plugin call** goes through `call_plugin`/`call_plugin_sync`; a reviewer should reject any `await item.<callback>(…)` outside `tiqora/plugins/calls.py`.
- **Order matters for shared files:** Tasks 4–19 each touch `registrar.py`, `tiqora_sdk/__init__.py`, the fixture plugin and `test_tiqora_sdk_surface.py` — run them sequentially in one worktree.

<!-- END -->
