# Wave 4 — ITSM phases 0 + 1 (schema survey, ITSM core, incident/problem, Mode A read-through) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tiqora covers what Znuny's ITSMCore and ITSMIncidentProblemManagement packages give agents, and sites that run Znuny ITSM can see and link their Znuny CIs in Tiqora without any data copy. Concretely: (0) the Znuny ITSM schema and behaviour of 6.5 and 7.3 is surveyed on real peers with the ITSM packages installed and recorded in `docs/itsm-import.md`, with test fixtures generated from it; (1) an admin can set up the ITSM ticket types, the state `closed with workaround` and the ITSM dynamic fields with Znuny-identical rows; impact × criticality sets the priority (Znuny's `cip_allocate` in Mode A, Tiqora's own matrix otherwise); services carry ITSM meta and a live incident state; ticket links can be deleted, typed and shown as a graph, and a note can go to all linked tickets in one action; problems link incidents, hold root cause and workaround, publish a known error as a KB draft and close linked incidents "with workaround" in one action; major incidents show a banner on linked tickets and post one update to all of them; and the setting `itsm.mode` (`off | znuny_readthrough | tiqora`) defaults automatically (Znuny ITSM present → `znuny_readthrough`, else `tiqora`). In Mode A the Znuny CMDB is visible read-only (search, detail, versions, service incident state) and tickets can be linked to Znuny CIs through `link_relation`, exactly as Znuny's `LinkObject::LinkAdd` writes it.

**Architecture:** A new backend package `tiqora.itsm` holds the mode resolver, the ITSM settings section, the **CI provider contract** (`tiqora.itsm.ci_provider`, owned by this plan, implemented here for Mode A and by the cmdb plan for Mode B), the read-only, version-aware Znuny adapter `tiqora.itsm.znuny_adapter` (one schema profile per surveyed Znuny ITSM line; readers for general catalog, classes, CIs, versions, attributes, links, `cip_allocate`, the ITSM columns on `service`/`sla`; bulk iterators the itsm-import plan consumes), and the incident/problem features. All new state lives in `tiqora_itsm_*` tables from one migration; Znuny rows are only written through existing write paths (`ticket_write_service`, `history_add`, the admin CRUD code extracted into `tiqora.domain.znuny_admin_writes`). Link handling is brought to `LinkObject.pm` parity (`LinkAdd` reverse-direction and type-group checks, `TicketLinkAdd`/`TicketLinkDelete` history, `LinkDelete` port). Every bulk action (note to linked tickets, major-incident update, close incidents with workaround) is a wave-0 job with polling. The frontend gets an `itsmApi.ts` wrapper, ticket-zoom panels (affected CIs, ITSM chips, problem, major incident, link graph), agent CI pages, admin pages and demo mocks. Phase 0 extends the golden toolkit with ITSM peer variants `znuny-6.5-itsm` and `znuny-7.3-itsm`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic two-chain, Pydantic v2, PyYAML, the wave-0 job system (`tiqora.jobs`), the wave-0 settings sections (`tiqora.settings_sections`), the wave-0 extension registry (`tiqora.extensions`), the wave-3 `xml_storage` hash builder (`tiqora.reporting.znuny_stats.xml_hash`), pytest + testcontainers (MariaDB + PostgreSQL), golden suite against real Znuny 6.5/7.3 peers with the ITSM packages; React 19 + TanStack Query/Router + Tailwind tokens, `@xyflow/react` (already a dependency since wave 3), Vitest, Playwright (demo mode).

**Spec:** `docs/superpowers/specs/2026-10-08-itsm-cmdb-design.md` — Fixed decisions 1 and 1a (Mode A part), Scope A, "Importer from Znuny ITSM" task 0, Phases rows 0 and 1. Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (wave 4; principles 1, 3, 5, 6; backlog item 1 "link overview, link-type admin, note to linked tickets"). Binding product decision (2026-10-12): `itsm.mode` defaults automatically (Znuny ITSM tables present → `znuny_readthrough`, otherwise `tiqora`); switching A → B is only ever an explicit admin action (the importer run, owned by the itsm-import plan); the docs recommend Mode B as the target. **Not in this plan:** the Tiqora CMDB itself (cmdb plan), the Znuny ITSM importer, GI `ConfigItem*`, MCP tools, the `TiqoraITSM` add-on and the coexistence runbook (itsm-import plan), change/catalogue/approvals (ITSM phase 3), NetBox.

**Scope marker:** phase 0 Tasks 1–3 (golden + survey), backend Tasks 4–21, frontend Tasks 22–27, golden Task 28, e2e Task 29, docs + verification Task 30. One migration (Task 4).

---

## Open questions for the product owner

Every item below is decided conservatively in this plan and marked **Assumption:** where it is used. Answers can change a task before it starts; none blocks Tasks 1–5.

1. **CI history entry for links made in Tiqora (Mode A).** Znuny's `LinkObject::ITSMConfigItem` backend writes a `configitem_history` row ("LinkAdded"/"LinkDeleted") when a ticket is linked to a CI. The project rule allows Znuny writes only through existing write services, and there is none for `configitem_history`. **Assumption:** Tiqora writes only the `link_relation` row (through the existing `link_objects` service) and invalidates Znuny's caches; the CI's history in Znuny does not show links made in Tiqora. Task 3 records the exact Znuny side effects; Task 28 pins the difference. Alternative: a new, narrowly scoped `configitem_history` insert.
2. **Where root cause and workaround of a problem live.** **Assumption:** in `tiqora_itsm_problem` (Tiqora only). Znuny agents do not see them. Alternative: two new ticket dynamic fields (visible in Znuny, but new rows in Znuny's DF admin).
3. **ITSM setup is an explicit admin action.** **Assumption:** types/state/DFs are never created automatically; the admin page shows a preview and "Einrichten" creates what is missing. The `Change`/`Change::Task` types are a separate group, unchecked by default until ITSM phase 3 uses them.
4. **Mode A permissions.** **Assumption:** an agent sees Znuny CIs only with `ro` in Znuny's group `itsm-configitem` **and** `ro` on the class's permission group (`general_catalog_preferences` key `Permission`, when set); linking needs `rw` on the ticket and `ro` on the CI (Znuny's `AgentLinkObject` rule as recorded by Task 3).
5. **Auto default is resolved once and stored.** **Assumption:** the first start after the migration detects Znuny ITSM, stores the resulting mode with source `auto`, and never re-detects; installing Znuny ITSM later does not flip a `tiqora` site to Mode A. Admin transitions: any → `off`; `off` → the detected default; `tiqora` → `znuny_readthrough` only while the Tiqora CMDB is empty and Znuny ITSM is detected; `znuny_readthrough` → `tiqora` **only** through the importer (itsm-import plan).
6. **What `off` switches off.** **Assumption:** CI views/links, the priority matrix, service incident state, problem and major-incident panels and ITSM dialogs. Link delete/types/graph and "note to linked tickets" are general ticket features and stay on in every mode.
7. **Major-incident updates to customers.** **Assumption:** the default is an internal note on every linked ticket; an e-mail reply to each ticket's customer is possible only when the admin setting `itsm.major_incident_customer_reply` is on (default off) and always runs as a job.
8. **When the priority is recomputed.** **Assumption:** only in Tiqora writes (ticket PATCH, ITSM dialogs, new ticket form), only when impact or criticality changes in that write, and never when the same write sets the priority explicitly (that is the agent's override). Tiqora does not react to Znuny-side writes (Znuny ITSM does that itself when installed).
9. **Service criticality copied to the ticket.** Znuny ITSM may copy the service's criticality into `ITSMCriticality` when the service changes. **Assumption:** Tiqora does the same only if Task 3 confirms Znuny does it server-side or in its forms; otherwise not.
10. **"Minimum time between incidents".** Znuny stores it per SLA (`sla.min_time_bet_incidents`). **Assumption:** Tiqora keeps it per service (`tiqora_itsm_service_meta`) as a warning on new incidents ("Für diesen Service gab es vor 12 Minuten bereits einen Incident"), never blocking; Mode A shows Znuny's SLA value read-only.
11. **Znuny changes and work orders in Mode A.** **Assumption:** not shown in phase 1; the CI detail links to Znuny's screens. The read-only legacy change archive is ITSM phase 3.
12. **Known error.** **Assumption:** "Als bekannten Fehler veröffentlichen" creates a KB article in state `review` (not published) in the category set in `itsm.known_error_kb_category_id`; the KB review workflow publishes it.
13. **Problem ↔ incident link type.** **Assumption:** Znuny's `ParentChild` with the problem as parent.
14. **Znuny ITSM versions outside the surveyed lines.** **Assumption:** the binding auto rule still selects Mode A, but the adapter refuses unknown package versions (`unsupported_znuny_itsm_version`) and the UI shows "Diese Znuny-ITSM-Version wird noch nicht unterstützt" instead of CI data. The profiles cover what Task 2 proves (at least 6.5.x and 7.3.x).

---

## Global Constraints

- Work in the worktree the executor is given (never in `<repo>` unless that is the given worktree); never push (a push to `origin main` deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data (`example.org`, `example.com`, `.test` hostnames; RFC 5737 / RFC 3849 documentation IPs `192.0.2.0/24`, `198.51.100.0/24`, `2001:db8::/32`; CI names like `srv-db-01.example.org`). Use `rtk git`; stage explicit paths only; run `rtk git diff --cached --name-only` before every commit (other agents share the index); check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their own rows and are never added to `backend/tests/db_leak_baseline.txt`.
- **Write only `tiqora_*` tables, except through the existing Znuny write services**: `tiqora.domain.ticket_write_service` (incl. `link_objects`, `add_article`, `change_state`, `change_priority`, `change_type`, `update_dynamic_field(s)`), `tiqora.znuny.history.history_add`, `tiqora.domain.dynamic_field_values`, `tiqora.channels.email.outbound_reply.deliver_agent_email_reply`, and the admin CRUD for ticket types/states/dynamic fields (Task 15 moves that code into `tiqora.domain.znuny_admin_writes` without changing behaviour). **Mode A never writes Znuny ITSM tables** (`configitem*`, `general_catalog*`, `xml_storage`, `cip_allocate`, `change_*`, `imexport_*`) and never alters `service`/`sla`. Never change Znuny DDL.
- **Never JOIN `tiqora_*` tables with Znuny tables** (prod MariaDB mixes `general_ci`/`unicode_ci` collations → error 1267). Load keys from one side, then query the other with an `IN` list (chunks of ≤ 500). Integer joins **inside** Znuny tables (`configitem` ⋈ `configitem_version` ⋈ `general_catalog`, `link_relation` ⋈ `link_type`) are fine. Task 7 adds a static guard test for `tiqora/itsm/**`.
- **No `db.begin()` after autobegin**: a session that already executed a statement is in a transaction; call `commit()`/`rollback()`, never `async with session.begin()` on it.
- **No long synchronous endpoints**: production nginx cuts `/api/` after 90 s. Anything that touches more than one ticket (note to linked tickets, major-incident update, close incidents with workaround) is a `tiqora_job` + polling (`GET /api/v1/jobs/{id}`), whatever the count. Adapter reads are paged (`limit ≤ 100`) and capped (attribute search: ≤ 500 candidate versions).
- **Migrations:** Alembic two-chain setup; the one new migration goes into `backend/alembic/versions_tiqora` with revision = the **next free number at implementation time** after the current primary head. The task brief named `20261010_0073`; the read worktree is already at `20261010_0076_gi_debugger` — verify with `ls backend/alembic/versions_tiqora | tail -3` and `cd backend && uv run alembic heads`; never copy an id from this document. The cmdb plan adds its own migration in a parallel worktree: whoever merges second renumbers and re-chains (skill `alembic-two-chain-new-head-forks-graph`). Update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assertion in `backend/tests/test_migrate_cli.py`; ORM DDL must equal the migration (`backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB **and** PostgreSQL: every DB test runs on both dialects: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` + `_to_async_url` (pattern `backend/tests/test_tickets_telegram_api.py`). Tests that need Znuny ITSM tables use the Task 5 fixtures `itsm_db_url(dialect, profile)` instead (both dialects × both profiles).
- Seed ids only inside this plan's band **940000–941999** (sub-bands per task below). Confirm free before seeding: `grep -rlE "\b94[01][0-9]{3}\b" backend/tests` (free on 2026-10-12 in the read worktree). Suggested for the parallel wave-4 plans: cmdb 942000–943999, itsm-import 944000–945999 (they pick their own; shift within the band if needed). Seeded logins/names contain `itsm94`. Autoincrement rows created by services (tickets, articles, history, jobs, link rows) are removed with `tests._row_cleanup.snapshot_marks` / `delete_above_marks`.
- Znuny YAML/SysConfig checkbox values are Perl-truthy strings (`'0'` is false): use `tiqora.util.perl.perl_truthy` / `SysConfig.get_bool`, never `bool(...)`.
- Timestamps the application writes into `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; never compare them with `func.now()` (skill `db-session-timezone-vs-app-written-rows`). API datetimes use `tiqora.domain.schemas.UtcDateTime`. Znuny ITSM timestamps (`configitem_version.create_time` …) are serialized with the same rule as other Znuny timestamps.
- After any API model/route change: regenerate both openapi files and `schema.d.ts` (skill `tiqora-openapi-regen-or-ci-wipes-schema`): `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`; check `head -c 40 packages/api-client/openapi.json` starts with `{"components"` and the diff is small. A Pydantic response field WITH a default becomes required in TS — fixtures and demo mocks must include it.
- Frontend (`frontend/`): `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc); **never** run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then propagate (skill `tiqora-i18n-key-propagation`: `node frontend/scripts/propagate-i18n-keys.mjs`; never `scaffold-locale.mjs` on existing locales). **Frontend tasks that add i18n keys run one after another.** German UI strings address the user with **"Sie"**. New screens get demo-mode mocks (`VITE_DEMO=1`; this plan: `frontend/src/demo/itsmMock.ts`, wired with one line into `resolveData` in `mockData.ts`) and are checked in demo mode with Playwright (delete `.playwright-mcp` before committing). Colour tokens only via `frontend/src/themes/tokens.css` + `themeColor()`. API wrappers are hand-written `frontend/src/lib/*Api.ts` with `api.request<T>(method, path, {query, body, signal})` and `Schemas["…"]` types (pattern `frontend/src/lib/phoneApi.ts`).
- UI copy German first, plain words, in the terms Znuny/ITIL admins know: "Incident", "Major Incident", "Problem", "Bekannter Fehler", "Workaround", "Ursache", "Auswirkung", "Kritikalität", "Prioritätsmatrix", "Configuration Items (CIs)", "CMDB", "Verwendungsstatus" (Znuny: deployment state), "Vorfallstatus" (Znuny: incident state), "Verknüpfungstypen", "Notiz an verknüpfte Tickets", "In Znuny bearbeiten". Mode names: "Aus", "Znuny führt das ITSM (nur lesen)", "Tiqora führt das ITSM". No "Adapter", "Provider", "read-through", "Profile" in UI text (only in code/docs).
- Znuny reference source: core 6.5 at `<repo>/znuny-6.5.22` (core only). The ITSM package sources are fetched and unpacked by Task 1 into the gitignored `tests/golden/itsm-packages/src/<Package>-<version>/`. Every behaviour claim below names its Perl file; where a step says "verify in source", pin the result in a test before implementing.
- Plan-specific:
  - Pure modules (`perl_data`, `definitions`, `attributes.decode_*`, `priority.lookup`, `ci_provider` dataclasses, `mode.allowed_transitions`) are synchronous and touch no DB, network or file system.
  - Adapter SQL is written with `text()` + bound parameters per profile; table and column names come only from the profile constants, never from input.
  - Provider methods never commit; route handlers and job handlers commit.
  - Every endpoint that returns ticket numbers/titles filters by the caller's `ro` permission (`PermissionEngine`); every endpoint that returns CIs filters through the provider's permission check. A linked ticket or CI the caller may not read is omitted, never shown as "hidden".

## Review Focus

1. **Mode A writes nothing but `link_relation` (+ ticket history + Znuny cache invalidation).** After every Mode A operation the ITSM tables are byte-identical. → Task 13 `test_mode_a_writes_no_itsm_table` (row checksums of `configitem*`, `general_catalog*`, `xml_storage`, `cip_allocate`, `service`, `sla` before/after search, detail, versions, link, unlink), Task 28 golden.
2. **Mode transitions follow the binding decision.** First start stores the auto default once; a later Znuny ITSM install never flips it; `znuny_readthrough → tiqora` is impossible through the admin API and only `switch_to_tiqora(..., via="importer")` does it. → Task 6 `test_auto_default_persisted_once`, `test_admin_cannot_switch_a_to_b`, `test_importer_switch_records_run`.
3. **Link writes equal Znuny's `LinkAdd`/`LinkDelete`.** Reverse-direction duplicate for unpointed types, type-group conflict, `TicketLinkAdd`/`TicketLinkDelete` history names, cache invalidation; delete removes the row in either stored direction. → Task 11 tests, Task 28 `test_link_parity_both_directions`.
4. **No information leak through links.** Linked tickets, CIs, major-incident banners and graph nodes the caller cannot read are left out; bulk jobs re-check `rw` per ticket at run time (the permission may change between enqueue and run). → Tasks 14, 18, 20, 21 (`test_graph_omits_unreadable_ticket`, `test_job_skips_ticket_without_rw`, `test_banner_hidden_without_ro_on_major_ticket`).
5. **Priority matrix equals Znuny.** Mode A uses `cip_allocate` exactly like `ITSMCIPAllocate::PriorityAllocationGet`; the override rule (explicit priority wins) holds in PATCH. → Task 16, Task 28 `test_priority_matrix_equals_znuny` (all 25 cells).

Also pinned (not top five): no `tiqora_*` × Znuny JOIN in `tiqora/itsm/**` (Task 7 static guard); bulk jobs are idempotent per ticket and never resend after their checkpoint (Task 18); the adapter refuses unknown Znuny ITSM versions instead of guessing (Task 9); setup never modifies an existing row (Task 15).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Existing names used exactly as they are in code (verify at implementation time; if one moved, use its new location and note it in the commit body): `link_objects`, `link_tickets`, `_link_object_id`, `_link_type_id`, `_link_state_id`, `_emit_event`, `invalidate_ticket_cache`, `_ticket_must_exist`, `TicketWriteService` (`.link_tickets`, `.list_links`, `.change_priority`), `add_article`, `ArticleIn`, `change_state`, `change_priority`, `change_type`, `update_dynamic_field`, `update_dynamic_fields_strict` (`tiqora.domain.ticket_write_service`); `history_add` (`tiqora.znuny.history`); `invalidate_znuny_cache_types`, `TYPE_CACHE_TYPES`, `now` (`tiqora.api.v1.admin.common`); `validate_dynamic_field_config`, `config_to_yaml` (`tiqora.api.v1.admin.dynamic_fields`); `config_from_yaml` (`tiqora.domain.dynamic_field_config`); `screen_dynamic_fields`, `DynamicFieldScreen` (`tiqora.api.v1.reference`); `SysConfig` (`tiqora.znuny.sysconfig`); `get_setting`, `set_setting`, `get_setting_bool`, `get_setting_int` (`tiqora.domain.settings_store`); `SettingsSection`, `SETTINGS_SECTIONS`, `validate_section_schema` (`tiqora.settings_sections.section`); `load_section_values` (`tiqora.settings_sections.store`); `ExtensionPoint`, `Registry`, `register_core`, `get_registry`, `set_registry_for_tests` (`tiqora.extensions`); `enqueue_job`, `utc_now` (`tiqora.jobs.service`); `JobHandler`, `JOB_HANDLERS` (`tiqora.jobs.registry`); `JobContext`, `JobFailed` (`tiqora.jobs.context`); `build_xml_hash`, `XmlHashError` (`tiqora.reporting.znuny_stats.xml_hash`); `deliver_agent_email_reply` (`tiqora.channels.email.outbound_reply`); `KbService` (`create_article`, `update_article`; `tiqora.kb.service`), `ArticleIn as KbArticleIn` (`tiqora.kb.schemas`), `STATE_REVIEW`, `TiqoraKbLink` (`tiqora.kb.models`; the table exists but is unused so far); `PermissionEngine` (`tiqora.permissions.engine`); `snapshot_marks`, `delete_above_marks` (`backend/tests/_row_cleanup.py`); `UtcDateTime` (`tiqora.domain.schemas`); `perl_truthy` (`tiqora.util.perl`); `TiqoraBase` (`tiqora.db.tiqora`); frontend `api.request`, `Schemas`, `ADMIN_PAGES` (`lib/adminSearch.ts`), `TicketDynamicFields`, `DynamicFieldsForm`, `LinkedTickets`, `TicketLinkChip`, `ServiceViewPage`, `ServicesPage`, `SettingsSectionPage`, the wave-0 job progress component, `@xyflow/react`.

### I0. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/itsm/__init__.py` | package docstring (what lives where), no logic |
| `backend/src/tiqora/itsm/models.py` | ORM for I1 (registered in `tiqora/db/tiqora/__init__.py` like `tiqora.kb.models`) |
| `backend/src/tiqora/itsm/settings.py` | `ItsmSettings`, `ITSM_SECTION`, `load_itsm_settings()` (I2) |
| `backend/src/tiqora/itsm/mode.py` | `ItsmMode`, `ItsmModeState`, `get_itsm_mode()`, `ensure_itsm_mode_initialised()`, `set_itsm_mode()`, `switch_to_tiqora()`, `allowed_transitions()` (I2) |
| `backend/src/tiqora/itsm/ci_provider.py` | **cross-plan contract**: CI dataclasses, `CiProvider` protocol, errors (I3) |
| `backend/src/tiqora/itsm/providers.py` | `CI_PROVIDERS` extension point, `CiProviderFactory`, `get_ci_provider()` (I3) |
| `backend/src/tiqora/itsm/znuny_adapter/__init__.py` | adapter package docstring |
| `backend/src/tiqora/itsm/znuny_adapter/detect.py` | `ZnunyItsmInfo`, `detect_znuny_itsm()` (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/profiles.py` | `SchemaProfile`, `PROFILES`, `select_profile()` (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/perl_data.py` | pure: safe parser for Perl data literals (6.x class definitions) (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/definitions.py` | pure: `AttrDef`, `parse_definition()` for Perl and YAML formats (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/attributes.py` | attribute storage decoding + display resolution (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/catalog.py` | general catalog, classes, class permission groups (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/reader.py` | `ZnunyItsmReader` (search/get/versions/bulk iterators/links/cip/service+sla columns) (I4) |
| `backend/src/tiqora/itsm/znuny_adapter/provider.py` | `ZnunyCiProvider` (Mode A implementation of I3) |
| `backend/src/tiqora/itsm/deeplink.py` | Znuny agent URLs ("In Znuny bearbeiten") (I4) |
| `backend/src/tiqora/itsm/link_types.py` | `tiqora_itsm_link_type` service, Znuny link-type catalogue from SysConfig (I6) |
| `backend/src/tiqora/itsm/link_graph.py` | ticket/CI link graph (I12) |
| `backend/src/tiqora/itsm/setup.py` | ITSM setup preview/apply (I7) |
| `backend/src/tiqora/itsm/seed_definitions.py` | Znuny-identical type/state/DF definitions captured by Task 2 (I7) |
| `backend/src/tiqora/itsm/priority.py` | matrix read/write, `suggest_priority()`, PATCH hook (I8) |
| `backend/src/tiqora/itsm/services.py` | service meta, service incident state, recent incidents (I9) |
| `backend/src/tiqora/itsm/linked_articles.py` | job `core.itsm_linked_articles` (I10) |
| `backend/src/tiqora/itsm/problem.py` | problem record, known error, close incidents (I11) |
| `backend/src/tiqora/itsm/major_incident.py` | declare/end/update/timeline (I11) |
| `backend/src/tiqora/itsm/summary.py` | `ticket_itsm_summary()` (I12) |
| `backend/src/tiqora/domain/znuny_admin_writes.py` | `create_ticket_type_row`, `create_ticket_state_row`, `create_dynamic_field_row` extracted from the admin routes (I7) |
| `backend/src/tiqora/api/v1/itsm.py` + `itsm_schemas.py` | agent API (`router` prefix `/itsm`, `ticket_router` prefix `/tickets`) (I13) |
| `backend/src/tiqora/api/v1/admin/itsm.py` + `itsm_schemas.py` | admin API (I13) |
| Modify `backend/src/tiqora/domain/ticket_write_service.py` | `link_objects` parity, new `unlink_objects`, `TicketWriteService.unlink_tickets` (I5) |
| Modify `backend/src/tiqora/api/v1/tickets.py` | `DELETE /tickets/{id}/links/{target_id}`, PATCH priority hook call (I5, I8) |
| Modify `backend/src/tiqora/api/v1/reference.py` | `DynamicFieldScreen` gains `AgentTicketAddtlITSMField`, `AgentTicketDecision` with Tiqora fallbacks (I12) |
| Modify `backend/src/tiqora/api/v1/admin/types.py`, `states.py`, `dynamic_fields.py` | call `znuny_admin_writes` (no behaviour change) |
| Modify `backend/src/tiqora/reporting/znuny_stats/xml_hash.py` → move to `backend/src/tiqora/znuny/xml_hash.py` | shared `xml_storage` hash builder; old path re-exports |
| Modify `backend/src/tiqora/extensions/core.py`, `api/v1/__init__.py`, `api/v1/admin/__init__.py`, `db/tiqora/__init__.py`, `main.py` + `worker/__main__.py` (startup `ensure_itsm_mode_initialised`) | wiring |

Tests (new): `backend/tests/_itsm_db.py` (Task 5), `backend/tests/_itsm_fixtures.py`, `backend/tests/itsm_provider_contract.py` (contract kit, Task 7), `backend/tests/fixtures/znuny-itsm/<profile>/…` (Task 2), one `test_itsm_*.py` module per task.

Frontend (new unless marked): `frontend/src/lib/itsmApi.ts`, `frontend/src/demo/itsmMock.ts`, `frontend/src/components/itsm/{CiPicker,CiStateBadge,CiAttributeTree,TicketCisPanel,TicketItsmChips,ItsmDialogs,ProblemPanel,MajorIncidentBanner,MajorIncidentPanel,LinkGraph,LinkedNoteDialog}.tsx` (+ tests), `frontend/src/routes/agent/itsm/{CiListPage,CiDetailPage}.tsx` (+ tests), `frontend/src/routes/admin/{ItsmPage,LinkTypesPage}.tsx` (+ tests); modify `router.tsx`, `lib/adminSearch.ts`, agent navigation, `components/agent/{LinkedTickets,TicketDynamicFields,TicketHeader}.tsx`, `routes/agent/{TicketZoomPage,ServiceViewPage,NewTicketPage}.tsx`, `routes/admin/ServicesPage.tsx`, `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` + propagation.

Golden: `tests/golden/itsm-packages.yaml`, `tests/golden/fetch_itsm_packages.py`, `tests/golden/itsm_seed.pl`, `tests/golden/itsm_survey.py`, `tests/golden/test_itsm_mode_a.py`; modify `peers.yaml`, `peer_env.py`, `docker-compose.golden.yml`, `znuny-entrypoint.sh`, `README-multi-peer.md`, `.gitignore` (`tests/golden/itsm-packages/`).

Docs: `docs/itsm-import.md` (new, Tasks 2–3), `docs/itsm.md` (new, Task 30; chapter layout in "Cross-plan contracts"), `docs/compatibility.md`, `docs/architecture.md`, `docs/parallel-operation.md`.

### I1. Tables (one migration, Task 4)

No FK to Znuny tables. `service_id`, `ticket_id`, `priority_id`, `*_by` reference Znuny rows by value.

```
tiqora_itsm_link_type                       -- owner: itsm1; consumed by cmdb (tiqora_itsm_link) — see Cross-plan contracts
  id                     INTEGER PK autoincrement
  name                   VARCHAR(50)  NOT NULL  UNIQUE uq_tiqora_itsm_link_type_name   -- Znuny type name for origin=znuny
  origin                 VARCHAR(10)  NOT NULL  -- "znuny" (defined in Znuny SysConfig LinkObject::Type###<name>) | "tiqora"
  source_label           VARCHAR(100) NULL      -- admin override; NULL = Znuny SourceName / i18n default
  target_label           VARCHAR(100) NULL
  pointed                BOOLEAN NOT NULL DEFAULT 0   -- origin=znuny: mirrors SysConfig (read-only)
  propagation            VARCHAR(10)  NOT NULL DEFAULT 'none'  -- none|source|target|both (Znuny ITSM::Core::IncidentLinkTypeDirection)
  offer_ticket_ticket    BOOLEAN NOT NULL DEFAULT 0
  offer_ticket_ci        BOOLEAN NOT NULL DEFAULT 0
  offer_ci_ci            BOOLEAN NOT NULL DEFAULT 0
  offer_ci_service       BOOLEAN NOT NULL DEFAULT 0
  valid                  BOOLEAN NOT NULL DEFAULT 1
  create_time, create_by, change_time, change_by

tiqora_itsm_priority_matrix
  id                     INTEGER PK autoincrement
  criticality            VARCHAR(200) NOT NULL   -- DF ITSMCriticality key, e.g. "3 normal"
  impact                 VARCHAR(200) NOT NULL   -- DF ITSMImpact key
  priority_id            INTEGER NOT NULL        -- ticket_priority.id
  change_time, change_by
  UNIQUE uq_tiqora_itsm_priority_matrix_cell (criticality, impact)

tiqora_itsm_service_meta
  service_id             INTEGER PK              -- service.id
  service_type           VARCHAR(200) NULL       -- used when Znuny ITSM is absent; Mode A reads service.type_id
  criticality            VARCHAR(200) NULL       -- same rule (Mode A reads service.criticality)
  owner_user_id          INTEGER NULL
  description            TEXT NULL
  portal_visible         BOOLEAN NOT NULL DEFAULT 1
  min_minutes_between_incidents INTEGER NULL     -- NULL = settings default; 0 = off
  change_time, change_by

tiqora_itsm_problem
  ticket_id              BIGINT PK               -- the problem ticket
  root_cause             TEXT NULL
  workaround             TEXT NULL
  known_error            BOOLEAN NOT NULL DEFAULT 0
  known_error_article_id INTEGER NULL            -- tiqora_kb_article.id (Tiqora table: FK allowed, ON DELETE SET NULL)
  known_error_since      DATETIME NULL
  change_time, change_by

tiqora_itsm_major_incident
  ticket_id              BIGINT PK
  summary                VARCHAR(500) NULL       -- banner text
  declared_at            DATETIME NOT NULL
  declared_by            INTEGER NOT NULL
  ended_at               DATETIME NULL
  ended_by               INTEGER NULL
  index ix_tiqora_itsm_major_incident_active (ended_at)

tiqora_itsm_major_incident_update
  id                     BIGINT PK autoincrement
  ticket_id              BIGINT NOT NULL         -- major ticket; FK -> tiqora_itsm_major_incident.ticket_id ON DELETE CASCADE
  created_at             DATETIME NOT NULL
  created_by             INTEGER NOT NULL
  mode                   VARCHAR(10) NOT NULL    -- internal|note|reply
  body                   TEXT NOT NULL
  job_id                 BIGINT NULL             -- tiqora_job.id
  target_count           INTEGER NOT NULL DEFAULT 0
  index ix_tiqora_itsm_mi_update_ticket (ticket_id, id)
```

GDPR: `tiqora.gdpr.erasure` deletes `tiqora_itsm_problem`, `tiqora_itsm_major_incident` (+ updates by cascade) rows of an erased ticket (Task 4 adds them to the erasure order; test `test_gdpr_erasure_removes_itsm_rows`). Mode state lives in `tiqora_settings` (I2).

### I2. Mode and settings

`tiqora/itsm/mode.py`:

```python
ItsmMode = Literal["off", "znuny_readthrough", "tiqora"]
ModeSource = Literal["auto", "admin", "importer"]

@dataclass(frozen=True, slots=True)
class ItsmModeState:
    mode: ItsmMode
    source: ModeSource
    stored: bool                      # False only before ensure_itsm_mode_initialised ran (tests, first request race)
    detected: ZnunyItsmInfo           # what is installed in Znuny (I4)
    changed_at: datetime | None
    changed_by: int | None
    import_run_id: int | None         # set when source == "importer"

SETTING_MODE = "itsm.mode"            # tiqora_settings keys (not part of ITSM_SECTION: transitions are guarded)
SETTING_MODE_META = "itsm.mode_meta"  # JSON {"source","changed_at","changed_by","import_run_id"}

def auto_default(info: ZnunyItsmInfo) -> ItsmMode
    # info.core_installed (general_catalog + cip_allocate present) -> "znuny_readthrough" else "tiqora"
def allowed_transitions(state: ItsmModeState, *, tiqora_cmdb_empty: bool) -> frozenset[ItsmMode]
    # pure; open question 5:
    #   any -> "off"
    #   "off" -> auto_default(detected)
    #   "tiqora" -> "znuny_readthrough" iff detected.core_installed and tiqora_cmdb_empty
    #   "znuny_readthrough" -> "tiqora" NEVER (importer only)
async def get_itsm_mode(session: AsyncSession) -> ItsmModeState      # stored value, or computed auto default with stored=False
async def ensure_itsm_mode_initialised(session_factory: async_sessionmaker[AsyncSession]) -> ItsmModeState
    # own session; if no stored mode: detect, store auto default with source "auto", commit; idempotent
    # (insert-if-absent; a concurrent second writer reads the first value)
async def set_itsm_mode(session: AsyncSession, new: ItsmMode, *, user_id: int) -> ItsmModeState
    # admin path; raises ModeTransitionNotAllowed(current, new, reason) unless new in allowed_transitions
async def switch_to_tiqora(session: AsyncSession, *, user_id: int, import_run_id: int) -> ItsmModeState
    # importer path (itsm-import plan): from "znuny_readthrough" or "off" -> "tiqora", source "importer"; flush, no commit
class ModeTransitionNotAllowed(Exception): ...
```

`tiqora_cmdb_empty` comes from the registered Mode B provider (`CiProvider.is_empty()`); while no Mode B provider is registered it is `True`.

`tiqora/itsm/settings.py` (wave-0 settings section, admin form generated from the schema; only supported field types):

```python
class ItsmSettings(BaseModel):
    incident_type: str = "Incident"
    major_incident_type: str = "Incident::Major"      # verify Znuny's name in Task 3 (6.x may use another); default = surveyed name
    service_request_type: str = "ServiceRequest"
    problem_type: str = "Problem"
    change_type: str = "Change"
    change_task_type: str = "Change::Task"
    workaround_state: str = "closed with workaround"
    criticality_field: str = "ITSMCriticality"
    impact_field: str = "ITSMImpact"
    priority_matrix_enabled: bool = True
    ci_ticket_link_type: str = "RelevantTo"            # verify in Task 3 (Znuny PossibleLink ITSMConfigItem<->Ticket)
    problem_link_type: str = "ParentChild"
    known_error_kb_category_id: int = Field(0, ge=0)   # 0 = not configured -> button disabled with hint
    min_minutes_between_incidents: int = Field(0, ge=0, le=10080)   # default for services without own value; 0 = off
    linked_articles_max_tickets: int = Field(200, ge=1, le=1000)
    major_incident_customer_reply: bool = False
    znuny_agent_url: str = Field("", max_length=500)   # "" = derive from SysConfig HttpType/FQDN/ScriptAlias

ITSM_SECTION = SettingsSection(key="itsm", model=ItsmSettings,
                               title_key="admin.settings.itsm.title", i18n_prefix="admin.settings.itsm")
async def load_itsm_settings(session: AsyncSession, settings: Settings) -> ItsmSettings
```

Gating helper used by every ITSM route: `async def require_itsm_enabled(session) -> ItsmModeState` → `HTTPException(409, detail={"code": "itsm_off"})` when mode is `off`.

### I3. CI provider contract (owned here; Mode B implemented by the cmdb plan)

`tiqora/itsm/ci_provider.py` — pure dataclasses + protocol, no DB code:

```python
CiSource = Literal["znuny", "tiqora"]

@dataclass(frozen=True, slots=True, order=True)
class CiRef:
    source: CiSource
    id: int
    def __str__(self) -> str                       # "znuny:123"
    @classmethod
    def parse(cls, raw: str) -> CiRef              # ValueError on anything else

@dataclass(frozen=True, slots=True)
class CatalogValue:
    key: str                                       # Mode A: str(general_catalog.id); Mode B: tiqora catalog key
    name: str
    functionality: str | None                      # incident: operational|warning|incident; deployment: preproductive|productive|postproductive|… (Znuny pref "Functionality")

@dataclass(frozen=True, slots=True)
class CiClassInfo:
    key: str; name: str; icon: str | None; can_read: bool; can_write: bool

@dataclass(frozen=True, slots=True)
class CiSummary:
    ref: CiRef; number: str; name: str; class_key: str; class_name: str
    deployment_state: CatalogValue | None; incident_state: CatalogValue | None
    changed_at: datetime | None
    edit_url: str | None                           # Mode A: Znuny AgentITSMConfigItemEdit; Mode B: None

@dataclass(frozen=True, slots=True)
class CiAttribute:
    key: str; label: str
    kind: str                                      # text|textarea|integer|date|datetime|catalog|ci|customer|customer_company|user|service|sla|queue|priority|ticket_type|state|url|email|ip|other
    display: str                                   # resolved, human-readable
    raw: str | None                                # stored value as text
    ref: CiRef | None                              # kind == "ci"
    children: tuple[CiAttribute, ...] = ()

@dataclass(frozen=True, slots=True)
class CiVersionInfo:
    id: int; seq: int; name: str
    deployment_state: CatalogValue | None; incident_state: CatalogValue | None
    created_at: datetime; created_by: int | None; created_by_name: str | None; change_note: str | None

@dataclass(frozen=True, slots=True)
class CiDetail:
    summary: CiSummary
    version: CiVersionInfo | None                  # the version shown (current unless a version was requested)
    attributes: tuple[CiAttribute, ...]
    definition_version: int | None
    read_only: bool                                # True in Mode A

@dataclass(frozen=True, slots=True)
class CiQuery:
    text: str | None = None                        # number/name prefix-or-contains; Mode A also attribute values (>= 3 chars)
    class_keys: tuple[str, ...] = ()
    deployment_state_keys: tuple[str, ...] = ()
    incident_state_keys: tuple[str, ...] = ()
    refs: tuple[CiRef, ...] = ()
    limit: int = 25                                # 1..100
    offset: int = 0

@dataclass(frozen=True, slots=True)
class CiPage:
    items: tuple[CiSummary, ...]; total: int

@dataclass(frozen=True, slots=True)
class CiTicketLink:
    ticket_id: int; ci: CiSummary; link_type: str
    direction: Literal["source", "target", "both"]  # ticket's side; "both" for unpointed types
    created_at: datetime | None

@dataclass(frozen=True, slots=True)
class LinkTypeOption:
    name: str; source_label: str; target_label: str; pointed: bool

@dataclass(frozen=True, slots=True)
class ServiceIncidentState:
    service_id: int
    state: CatalogValue | None                     # worst functionality over the service's CIs; None = no CI linked
    cis_incident: int; cis_warning: int

class CiError(Exception): ...
class CiNotFound(CiError): ...
class CiPermissionDenied(CiError): ...
class CiReadOnly(CiError): ...                     # write attempted on read-only data (never for links)
class CiUnavailable(CiError):                      # provider cannot serve (unsupported Znuny version, tables gone)
    code: str                                      # "unsupported_znuny_itsm_version" | "znuny_itsm_missing" | "cmdb_not_installed"

@runtime_checkable
class CiProvider(Protocol):
    source: CiSource
    read_only: bool
    async def is_empty(self, session: AsyncSession) -> bool
    async def classes(self, session: AsyncSession, *, user_id: int) -> list[CiClassInfo]
    async def deployment_states(self, session: AsyncSession) -> list[CatalogValue]
    async def incident_states(self, session: AsyncSession) -> list[CatalogValue]
    async def search(self, session: AsyncSession, query: CiQuery, *, user_id: int) -> CiPage
    async def get(self, session: AsyncSession, ref: CiRef, *, user_id: int, version_id: int | None = None) -> CiDetail
    async def get_many(self, session: AsyncSession, refs: Sequence[CiRef], *, user_id: int) -> dict[CiRef, CiSummary]   # unreadable/missing omitted
    async def versions(self, session: AsyncSession, ref: CiRef, *, user_id: int, limit: int, offset: int) -> tuple[list[CiVersionInfo], int]
    async def ticket_links(self, session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int) -> dict[int, list[CiTicketLink]]
    async def ci_ticket_ids(self, session: AsyncSession, ref: CiRef, *, user_id: int) -> list[int]     # caller filters by ticket ro
    async def ticket_link_types(self, session: AsyncSession) -> list[LinkTypeOption]
    async def link_ticket(self, session: AsyncSession, *, ticket_id: int, ref: CiRef, link_type: str, user_id: int) -> bool
    async def unlink_ticket(self, session: AsyncSession, *, ticket_id: int, ref: CiRef, link_type: str | None, user_id: int) -> int
    async def service_incident_states(self, session: AsyncSession, service_ids: Sequence[int]) -> dict[int, ServiceIncidentState]
    async def service_cis(self, session: AsyncSession, service_id: int, *, user_id: int, limit: int = 100) -> list[CiSummary]
```

Rules every implementation follows (pinned by the contract kit `backend/tests/itsm_provider_contract.py`, Task 7, which the cmdb plan runs against Mode B):
- `ref.source != provider.source` → `CiNotFound`.
- Methods never commit; `link_ticket`/`unlink_ticket` assume the caller checked `rw` on the ticket and check `ro` on the CI themselves.
- `link_ticket` is idempotent (`False` when it already exists); `unlink_ticket` returns the number of removed links (0 = nothing).
- `search` returns `total` exactly; `limit` > 100 is clamped to 100.
- No `tiqora_*` × Znuny JOIN; lookups of Znuny names (users, services) via IN lists.

`tiqora/itsm/providers.py`:

```python
@dataclass(frozen=True, slots=True)
class CiProviderFactory:
    mode: Literal["znuny_readthrough", "tiqora"]
    create: Callable[[SysConfig], CiProvider]

CI_PROVIDERS: ExtensionPoint[CiProviderFactory] = ExtensionPoint(
    key="itsm.ci_providers", item_key=lambda f: f.mode,
    description="CI data source per ITSM mode (Mode A: Znuny read-through, Mode B: Tiqora CMDB).",
    sdk_stable=False)
async def get_ci_provider(session: AsyncSession, *, registry: Registry | None = None) -> CiProvider | None
    # None when mode is "off" or no factory is registered for the mode (e.g. "tiqora" before the cmdb plan lands)
async def tiqora_cmdb_empty(session: AsyncSession) -> bool       # registered "tiqora" provider .is_empty(), else True
```

This plan registers only `CiProviderFactory(mode="znuny_readthrough", create=ZnunyCiProvider)` in `register_core`.

### I4. Znuny adapter (Mode A, read-only)

`detect.py`:

```python
@dataclass(frozen=True, slots=True)
class ZnunyItsmInfo:
    packages: Mapping[str, str]          # package_repository rows with install_status "installed": name -> version
    core_installed: bool                 # tables general_catalog + cip_allocate present
    cmdb_installed: bool                 # configitem + configitem_version + configitem_definition present
    change_installed: bool               # change_item present (survey only; unused in phase 1)
    profile: str | None                  # select_profile(...) name, None when unknown
    unsupported_reason: str | None       # e.g. "ITSMConfigurationManagement 7.4.0 not surveyed"
async def detect_znuny_itsm(session: AsyncSession) -> ZnunyItsmInfo
    # table presence via SQLAlchemy inspector on the sync connection (run_sync); cached per process for 60 s
```

`profiles.py` — the Task 2 survey fills the constants; the shape is fixed:

```python
@dataclass(frozen=True, slots=True)
class SchemaProfile:
    name: str                                  # "itsm-6" | "itsm-7" (final names from Task 2)
    package_versions: Mapping[str, str]        # package -> version spec it covers, e.g. {"ITSMConfigurationManagement": ">=6.5,<6.6"}
    definition_format: Literal["perl", "yaml"]
    attribute_storage: Literal["xml_storage", "survey_defined"]   # Task 2 replaces "survey_defined" with the real 7.x mechanism
    xml_type_prefix: str                       # "ITSM::ConfigItem::" (+ class id); archive prefix recorded too
    version_has_name: bool
    version_has_states: bool
    service_itsm_columns: tuple[str, ...]      # e.g. ("type_id", "criticality")
    sla_itsm_columns: tuple[str, ...]          # e.g. ("type_id", "min_time_bet_incidents")
    ci_link_object: str                        # "ITSMConfigItem"
    catalog_classes: Mapping[str, str]         # role -> general_catalog_class, e.g. {"ci_class": "ITSM::ConfigItem::Class", "deployment_state": "ITSM::ConfigItem::DeploymentState", "incident_state": "ITSM::Core::IncidentState", "service_type": "ITSM::Service::Type", "sla_type": "ITSM::SLA::Type"}
PROFILES: Final[Mapping[str, SchemaProfile]]
def select_profile(packages: Mapping[str, str]) -> tuple[SchemaProfile | None, str | None]   # (profile, unsupported_reason)
```

`perl_data.py` (pure): `parse_perl_data(text: str) -> Any` for the subset Znuny writes with `Data::Dumper`/hand-written definitions: `[...]`, `{...}`, `=>`, `,`, single/double-quoted strings (escapes `\\ \' \" \n \t`, no interpolation), bare words as hash keys, integers/decimals, `undef`, comments `#…`. Anything else (`sub`, `qw`, `do`, `eval`, `\`, `$`, `@`, `%` sigils outside strings) → `PerlDataError` with offset. Never `eval`, never shells out.

`definitions.py` (pure):

```python
@dataclass(frozen=True, slots=True)
class AttrDef:
    key: str; name: str; searchable: bool
    input_type: str                       # Znuny Input.Type verbatim ("Text", "GeneralCatalog", "CI", …)
    input: Mapping[str, Any]              # rest of the Input hash (Class, Translation, ValueMin, …)
    count_min: int; count_max: int; count_default: int
    sub: tuple[AttrDef, ...]
def parse_definition(text: str, fmt: Literal["perl", "yaml"]) -> tuple[AttrDef, ...]
    # Znuny defaults when keys are missing: CountMin 1, CountMax 1, CountDefault 1 (verify in ITSMConfigItem/Definition.pm, Task 3)
```

`attributes.py`:
- `xml_storage` profiles: rows `(xml_type = prefix + class_id, xml_key = str(version_id))` → `build_xml_hash` (moved to `tiqora.znuny.xml_hash`) → Znuny's `XMLData` structure → `values_from_xml_data(defs, xml_data) -> list[AttrValue]` (nested, repeatable, in definition order, `Content` per instance).
- Other storage (if Task 2 finds 7.x differs): a second decoder with the same output type, selected by `profile.attribute_storage`.
- `resolve_display(values, defs, lookups) -> tuple[CiAttribute, ...]`: one batch lookup per input type (general catalog ids → names; CI ids → `CiRef("znuny", id)` + number/name; customer logins/company ids → names; user ids → full names; service/SLA/queue/priority/type/state ids → names). Unknown input types → `kind="other"`, `display=raw`.

`catalog.py`: `load_catalog(session, profile, role) -> list[CatalogValue]` (valid rows only, `functionality` from `general_catalog_preferences` key `Functionality`), `class_permission_groups(session, profile) -> dict[int, int | None]` (pref key `Permission`, verify in Task 3).

`reader.py` — `ZnunyItsmReader(profile)`; every method is read-only and paged:

```python
async def search(self, session, q: CiQuery, *, class_ids: Collection[int] | None) -> tuple[list[CiRow], int]
    # current version name (configitem.last_version_id -> configitem_version.name) / number LIKE;
    # attribute text: xml_storage xml_content_value LIKE :v AND xml_type IN (class prefixes) -> candidate xml_key set (cap 500)
    # -> CIs whose last_version_id IN candidates
async def get_ci(self, session, ci_id: int) -> CiRow | None
async def get_version(self, session, version_id: int) -> VersionRow | None
async def versions(self, session, ci_id: int, *, limit: int, offset: int) -> tuple[list[VersionRow], int]
async def version_attributes(self, session, version_rows: Sequence[VersionRow]) -> dict[int, list[AttrValue]]
async def definitions(self, session, definition_ids: Collection[int]) -> dict[int, DefinitionRow]
# Bulk iterators for the itsm-import plan (Cross-plan contracts):
async def iter_general_catalog(self, session) -> list[GeneralCatalogRow]
async def iter_class_definitions(self, session) -> list[DefinitionRow]                 # every definition version of every class
async def iter_config_items(self, session, *, after_id: int, limit: int) -> list[CiRow]   # id-ordered keyset paging
async def versions_for(self, session, ci_ids: Sequence[int]) -> dict[int, list[VersionRow]]   # all versions, id order
async def iter_ci_links(self, session, *, after: tuple[int, str, int, str, int] | None, limit: int) -> list[LinkRow]
async def cip_allocate(self, session) -> list[CipRow]
async def service_itsm_columns(self, session) -> dict[int, ServiceItsmRow]
async def sla_itsm_columns(self, session) -> dict[int, SlaItsmRow]
```

Row dataclasses (frozen, slots; field names fixed): `GeneralCatalogRow(id, catalog_class, name, valid_id, comments, preferences: Mapping[str, str])`, `DefinitionRow(id, class_id, version, fmt, text, created_at, created_by)`, `CiRow(id, number, class_id, last_version_id, cur_depl_state_id, cur_inci_state_id, created_at, created_by, changed_at, changed_by)`, `VersionRow(id, ci_id, name, definition_id, depl_state_id, inci_state_id, created_at, created_by)`, `LinkRow(source_object, source_key, target_object, target_key, link_type, state, created_at, created_by)`, `CipRow(criticality, impact, priority_id)`, `ServiceItsmRow(service_id, type_id, type_name, criticality)`, `SlaItsmRow(sla_id, type_id, type_name, min_time_bet_incidents)`. If Task 2 finds a column missing in a profile, the field is `None` for that profile (documented in `docs/itsm-import.md`).

`deeplink.py`: `async def znuny_agent_url(session, settings: ItsmSettings, action: str, **params: str | int) -> str | None` — `settings.znuny_agent_url` or `{HttpType}://{FQDN}/{ScriptAlias}index.pl` from SysConfig; `None` when FQDN is unset. Mode A edit link: `Action=AgentITSMConfigItemEdit;ConfigItemID=<id>` (verify the action names per version in Task 3).

`ZnunyCiProvider` (provider.py) implements I3 with `source="znuny"`, `read_only=True`; `is_empty()` = no row in `configitem`. Permission (open question 4): `PermissionEngine` `ro` on group `itsm-configitem` and on the class permission group; the class list is filtered first, and `search`/`get_many` only query readable class ids. Links: `link_objects(source_object="Ticket", source_key=str(ticket_id), target_object=profile.ci_link_object, target_key=str(ci_id), link_type=…)` — direction as Task 3 records for `AgentLinkObject` from a ticket; `unlink_objects` (I5) for removal. `service_incident_states` ports the ITSMCore algorithm Task 3 records (expected: CIs linked to the service through the propagating link types; worst `Functionality` wins: `incident` > `warning` > `operational`). Every unsupported/missing case raises `CiUnavailable` with its code.

### I5. Link parity (`LinkObject.pm`)

In `tiqora/domain/ticket_write_service.py`:

```python
async def link_objects(session, *, source_object, source_key, target_object, target_key,
                       link_type, user_id, state="Valid") -> bool          # existing signature kept
    # + reverse-direction existing check (LinkObject.pm LinkAdd lines "check if link already exists"):
    #   existing same-type row in either direction -> unpointed: return False; pointed and opposite direction: raise LinkConflict("opposite_direction")
    #   existing row with a different state -> raise LinkConflict("state")
    # + type-group check (PossibleType over SysConfig LinkObject::TypeGroup###*): raise LinkConflict("type_group")
    # + TicketLinkAdd history on each Ticket side whose OTHER object is a Ticket
    #   (LinkObject/Ticket.pm BackendLinkAddPost: Name "%%<TN of other>%%<other id>%%<this id>")
    # + invalidate Znuny cache type "LinkObject" (Cache::LinkListRaw keys) via invalidate_znuny_cache_types
async def unlink_objects(session, *, object1, key1, object2, key2, link_type, user_id) -> int
    # LinkDelete port: deletes rows of that type in both stored directions (all states, as LinkDelete does — verify),
    # TicketLinkDelete history like BackendLinkDeletePost, LinkDelete event + ticket cache invalidation; returns rows removed
class LinkConflict(InvalidInput):
    reason: Literal["opposite_direction", "state", "type_group"]
TicketWriteService.unlink_tickets(user_id, ticket_id, target_ticket_id, link_type: str | None) -> int
    # rw on both tickets (same rule as link_tickets); link_type None -> every type between the two
```

Existing callers of `link_objects` (`split_article`, process `LinkAdd`, `link_tickets`) keep working; the process `LinkAdd` action logs and continues on `LinkConflict` like Znuny's `return` (test in Task 11).

### I6. Link types

`tiqora/itsm/link_types.py`:

```python
@dataclass(frozen=True, slots=True)
class ZnunyLinkType:
    name: str; source_name: str; target_name: str; pointed: bool
    possible: frozenset[tuple[str, str]]      # (Object1, Object2) pairs from LinkObject::PossibleLink###*
    propagation: Literal["none", "source", "target", "both"]   # ITSM::Core::IncidentLinkTypeDirection (Mode A, read-only)
async def znuny_link_types(sysconfig: SysConfig) -> dict[str, ZnunyLinkType]
async def sync_link_type_rows(session: AsyncSession, *, user_id: int) -> int
    # inserts missing origin="znuny" rows (offer flags from PossibleLink: Ticket-Ticket -> offer_ticket_ticket, Ticket-ITSMConfigItem -> offer_ticket_ci);
    # inserts the Tiqora defaults DependsOn, Includes, ConnectedTo, AlternativeTo, RelevantTo as origin="tiqora" only when absent;
    # updates `pointed` of znuny rows from SysConfig; never deletes; never touches admin label/offer overrides
async def offered_link_types(session, *, pair: Literal["ticket_ticket", "ticket_ci", "ci_ci", "ci_service"], mode: ItsmMode) -> list[LinkTypeOption]
    # ticket_ticket and Mode A ticket_ci: only names that Znuny can store (SysConfig type + PossibleLink pair) AND row valid + offered
    # Mode B pairs: valid tiqora_itsm_link_type rows with the offer flag (cmdb plan consumes)
async def list_link_types / create_link_type / update_link_type / delete_link_type   # admin; origin="znuny" rows: only labels, offer flags, valid editable
```

Labels shown in the UI: `source_label`/`target_label` override, else i18n `itsm.linkTypes.<name>.source|target` when present, else the Znuny SysConfig name.

### I7. ITSM setup (types, state, dynamic fields)

`tiqora/domain/znuny_admin_writes.py` — moved verbatim out of the admin routes (behaviour unchanged, admin tests stay green):

```python
async def create_ticket_type_row(session, *, name: str, valid_id: int, user_id: int) -> TicketType
async def create_ticket_state_row(session, *, name: str, type_name: str, comments: str | None, valid_id: int, user_id: int, color: str | None) -> TicketState
async def create_dynamic_field_row(session, *, name: str, label: str, field_type: str, object_type: str, config: dict[str, Any], user_id: int, field_order: int | None = None) -> DynamicField
    # validate_dynamic_field_config + config_to_yaml + field_order = max+1 when None + cache invalidation (exactly as POST /admin/dynamic-fields)
```

`tiqora/itsm/seed_definitions.py` holds the exact rows the Znuny packages create (Task 2 captures them from the peers: name, state type, comments, DF label/type/config YAML decoded). Groups: `incident_problem` (types incident/major/service request/problem per `ItsmSettings` names; state `workaround_state`; DFs `ITSMCriticality`, `ITSMImpact`, `ITSMReviewRequired`, `ITSMDecisionResult`, `ITSMDecisionDate`, `ITSMRepairStartTime`, `ITSMRecoveryStartTime`, `ITSMDueDate`) and `change` (types `Change`, `Change::Task` — open question 3, default unchecked).

`tiqora/itsm/setup.py`:

```python
@dataclass(frozen=True, slots=True)
class SetupItem:
    group: Literal["incident_problem", "change"]
    kind: Literal["ticket_type", "ticket_state", "dynamic_field"]
    name: str
    status: Literal["present", "missing", "conflict", "invalid"]   # conflict: same name, different type/field_type; invalid: present but valid_id != 1
    detail: str | None
async def plan_setup(session, settings: ItsmSettings) -> list[SetupItem]
async def apply_setup(session, settings: ItsmSettings, *, groups: set[str], user_id: int) -> list[SetupItem]
    # creates "missing" items only; never changes present/conflict/invalid rows; one commit by the caller
```

### I8. Priority matrix

`tiqora/itsm/priority.py`:

```python
@dataclass(frozen=True, slots=True)
class MatrixCell:
    criticality: str; impact: str; priority_id: int
@dataclass(frozen=True, slots=True)
class MatrixView:
    source: Literal["znuny", "tiqora"]           # znuny = cip_allocate (Mode A, read-only)
    editable: bool
    criticality_values: tuple[str, ...]          # DF PossibleValues keys in DF order
    impact_values: tuple[str, ...]
    cells: tuple[MatrixCell, ...]
def lookup(cells: Sequence[MatrixCell], criticality: str | None, impact: str | None) -> int | None   # pure; PriorityAllocationGet semantics (verify in ITSMCIPAllocate.pm)
async def load_matrix(session, mode: ItsmModeState, settings: ItsmSettings) -> MatrixView
async def save_matrix(session, cells: Sequence[MatrixCell], *, user_id: int) -> None    # Mode A -> CiReadOnly; replaces all rows
async def replace_matrix_from_import(session, cells: Sequence[MatrixCell], *, user_id: int) -> None   # itsm-import plan (cip_allocate -> tiqora)
async def suggest_priority(session, mode, settings, *, criticality: str | None, impact: str | None) -> int | None
async def apply_matrix_after_write(session, *, ticket_id: int, changed_fields: Mapping[str, list[str]],
                                   explicit_priority: bool, user_id: int, sysconfig: SysConfig) -> int | None
    # called by PATCH /tickets/{id} and the ITSM dialogs after DFs were written: if criticality or impact is in changed_fields,
    # not explicit_priority, matrix enabled, mode != off -> change_priority(...) when the result differs; returns new priority id
```

Optional (open question 9, only if Task 3 confirms Znuny behaviour): `apply_service_criticality_after_write(...)` copies the service's criticality into `ITSMCriticality` on a service change, then runs `apply_matrix_after_write`.

### I9. Services: meta, incident state, recent incidents

`tiqora/itsm/services.py`:

```python
@dataclass(frozen=True, slots=True)
class ServiceItsmInfo:
    service_id: int; name: str
    service_type: str | None; criticality: str | None; znuny_owned: bool   # True in Mode A: type/criticality from service columns, read-only
    owner_user_id: int | None; owner_name: str | None; description: str | None; portal_visible: bool
    min_minutes_between_incidents: int | None; znuny_sla_min_times: Mapping[int, int]   # Mode A: per SLA, read-only display
    incident_state: ServiceIncidentState | None
async def service_info(session, service_id: int, *, user_id: int) -> ServiceItsmInfo
async def service_infos(session, service_ids: Sequence[int]) -> dict[int, ServiceItsmInfo]   # lists; no per-user CI data
async def save_service_meta(session, service_id: int, data: ServiceMetaIn, *, user_id: int) -> None   # Mode A: type/criticality rejected (CiReadOnly)
async def recent_incident(session, *, service_id: int, exclude_ticket_id: int | None, settings: ItsmSettings, user_id: int) -> RecentIncident | None
    # newest ticket of type incident_type/major_incident_type with this service created within the window; ro-filtered
```

Service type suggestions when Znuny ITSM is absent: the Znuny defaults recorded by Task 2 (`ITSM::Service::Type` names), free text allowed (Deviation D8).

### I10. Linked articles job (note to linked tickets, major-incident updates, close with workaround)

`tiqora/itsm/linked_articles.py`:

```python
class LinkedArticlesPayload(BaseModel):
    source_ticket_id: int
    target_ticket_ids: list[int]                     # 1..settings.linked_articles_max_tickets, de-duplicated, source excluded
    mode: Literal["note", "reply"]
    subject: str = Field(max_length=250)
    body_html: str
    visible_for_customer: bool = False               # note only
    set_state_name: str | None = None                # e.g. workaround_state (close with workaround)
    origin: Literal["linked_note", "major_incident_update", "problem_close_incidents"]
    major_update_id: int | None = None
    acting_user_id: int
class LinkedArticlesResult(BaseModel):
    done: list[int]; skipped: list[dict[str, Any]]   # {"ticket_id", "reason": "no_rw"|"not_linked"|"no_customer_address"|"error", "detail"}
LINKED_ARTICLES_JOB = JobHandler(kind="core.itsm_linked_articles", payload_model=LinkedArticlesPayload,
    run=run_linked_articles, title_key="jobs.kinds.core.itsm_linked_articles",
    timeout_seconds=1800, lane="default", max_attempts=1, requeue_if_interrupted=True)
```

Per target ticket, in its own transaction: re-check that it is still linked to the source and that `acting_user_id` has `rw` (and `note` for notes, Znuny's `AgentTicketNote` permission); write the article (`add_article`, channel Internal, sender agent) or the reply (`deliver_agent_email_reply` to the ticket's customer — reply mode only when allowed by I11/settings); optional `change_state`; commit; `ctx.progress(i, n)`; record the id in the result. `ctx.checkpoint()` before the first write. A re-run after interruption (requeue only before the checkpoint) starts over; after the checkpoint the job fails and the result lists what was done — never resent automatically. History and events come from the write services.

### I11. Problem and major incident

`tiqora/itsm/problem.py`:

```python
async def get_problem(session, ticket_id: int, *, user_id: int) -> ProblemView        # ro; ProblemView includes linked incidents (ro-filtered) and the KB article state
async def save_problem(session, ticket_id: int, data: ProblemIn, *, user_id: int) -> ProblemView   # rw; ticket type must be problem_type
async def link_incidents(session, problem_id: int, incident_ids: Sequence[int], *, user_id: int) -> int   # link_objects ParentChild, problem = source
async def publish_known_error(session, kb: KbService, problem_id: int, *, user_id: int) -> int
    # creates (or updates) a KB article in settings.known_error_kb_category_id, state STATE_REVIEW, Markdown from title/root cause/workaround,
    # TiqoraKbLink(object_type="ticket", object_id=problem_id); sets known_error=True, known_error_since; returns article id
async def enqueue_close_incidents(session, problem_id: int, *, user_id: int, note_html: str | None) -> TiqoraJob
    # LinkedArticlesPayload(origin="problem_close_incidents", set_state_name=settings.workaround_state, mode="note", targets = linked incidents not yet closed)
```

`tiqora/itsm/major_incident.py`:

```python
async def declare(session, ticket_id: int, *, summary: str | None, user_id: int, sysconfig: SysConfig) -> MajorIncidentView
    # rw; change_type -> major_incident_type (when not already); insert tiqora_itsm_major_incident; history via change_type only
async def end(session, ticket_id: int, *, user_id: int) -> MajorIncidentView          # sets ended_at; the type stays (Znuny has no "end")
async def post_update(session, ticket_id: int, *, mode: Literal["internal", "note", "reply"], body_html: str, user_id: int) -> tuple[MajorIncidentUpdate, TiqoraJob | None]
    # internal: note on the major ticket only; note/reply: + job to all linked tickets (reply only if settings.major_incident_customer_reply)
async def timeline(session, ticket_id: int, *, user_id: int) -> list[TimelineEntry]   # declared, updates, ended
async def active_banners(session, ticket_ids: Sequence[int], *, user_id: int) -> dict[int, MajorBanner]
    # link keys from link_relation (Znuny) -> active major rows by IN list (tiqora) -> ro-filtered major tickets
```

### I12. Ticket ITSM summary, link graph, ITSM dialogs

`tiqora/itsm/summary.py` — `async def ticket_itsm_summary(session, ticket_id, *, user_id) -> TicketItsm` (single round of IN-list queries; `enabled=False` fast path when mode is off or setup items are missing):

```
TicketItsm:
  enabled: bool
  mode: ItsmMode
  role: "incident"|"major_incident"|"service_request"|"problem"|"change"|"change_task"|None   # from ticket type vs ItsmSettings names
  criticality: str|None; impact: str|None
  matrix_priority_id: int|None             # what the matrix suggests (UI shows "Matrix: 2 low" when it differs)
  service: ServiceItsmInfo|None            # incl. incident_state
  major_incident: MajorIncidentView|None   # this ticket is a major incident
  major_banner: MajorBanner|None           # a linked active major incident the caller may read
  problem: ProblemRef|None                 # linked problem (incident side)
  ci_count: int|None                       # None when no provider
  recent_incident: RecentIncident|None     # incidents only, flood warning
  dialogs: {"itsm_fields": bool, "decision": bool}   # screens with at least one configured DF
```

`tiqora/itsm/link_graph.py` — `async def link_graph(session, ticket_id, *, user_id, depth: int = 2, max_nodes: int = 60) -> LinkGraph` (nodes: tickets ro-filtered + CIs via provider `get_many`; edges with link type and direction; BFS over `link_relation` ticket↔ticket and provider `ticket_links`; `truncated: bool`).

ITSM dialogs: `DynamicFieldScreen` gains `"AgentTicketAddtlITSMField"` and `"AgentTicketDecision"`; when the SysConfig key `Ticket::Frontend::<screen>###DynamicField` is absent (no Znuny ITSM), the endpoint uses Tiqora defaults recorded by Task 3 from the Znuny packages' XML (expected: AddtlITSMField → `ITSMCriticality`, `ITSMImpact`, `ITSMReviewRequired`, `ITSMRepairStartTime`, `ITSMRecoveryStartTime`, `ITSMDueDate`; Decision → `ITSMDecisionResult`, `ITSMDecisionDate`). Saving goes through the existing PATCH (DFs) + `apply_matrix_after_write`.

### I13. API surface

Agent (`CurrentUser`; every route except the link ones calls `require_itsm_enabled`):

| Method + path | Body / query | Response | Task |
|---|---|---|---|
| `GET /api/v1/itsm/status` | — | `ItsmStatusOut {mode, source, ci_available, ci_read_only, ci_source, unavailable_code, znuny:{core_installed, cmdb_installed, profile, packages}, setup_complete}` (no gate) | 14 |
| `GET /api/v1/itsm/catalog` | — | `{classes, deployment_states, incident_states}` | 14 |
| `GET /api/v1/itsm/cis` | `q, class[], depl[], inci[], limit, offset` | `CiPageOut` | 14 |
| `GET /api/v1/itsm/cis/{source}/{ci_id}` | `version_id?` | `CiDetailOut` | 14 |
| `GET /api/v1/itsm/cis/{source}/{ci_id}/versions` | `limit, offset` | `CiVersionPageOut` | 14 |
| `GET /api/v1/itsm/cis/{source}/{ci_id}/tickets` | — | `list[TicketLinkTargetOut]` (ro-filtered) | 14 |
| `GET /api/v1/tickets/{id}/cis` | — | `list[CiTicketLinkOut]` | 14 |
| `POST /api/v1/tickets/{id}/cis` | `{ci: "znuny:12", link_type?: str}` | 201 `CiTicketLinkOut` | 14 |
| `DELETE /api/v1/tickets/{id}/cis/{source}/{ci_id}` | `link_type?` | 204 | 14 |
| `DELETE /api/v1/tickets/{id}/links/{target_id}` | `link_type?` | 204 (no gate) | 11 |
| `GET /api/v1/itsm/link-types` | `pair=ticket_ticket|ticket_ci` | `list[LinkTypeOptionOut]` (no gate for ticket_ticket) | 12 |
| `GET /api/v1/itsm/priority-matrix` | — | `MatrixOut` | 16 |
| `GET /api/v1/itsm/priority-suggestion` | `criticality, impact` | `{priority_id: int|null}` | 16 |
| `GET /api/v1/itsm/services/{service_id}` | — | `ServiceItsmOut` | 17 |
| `GET /api/v1/itsm/services/incident-states` | `ids` (≤ 200) | `dict[int, ServiceIncidentStateOut]` | 17 |
| `GET /api/v1/itsm/services/{service_id}/recent-incident` | `exclude_ticket_id?` | `RecentIncidentOut|null` | 17 |
| `POST /api/v1/tickets/{id}/linked-articles` | `{target_ticket_ids, subject, body_html, visible_for_customer}` | 202 `JobAccepted` (no gate) | 18 |
| `GET/PUT /api/v1/tickets/{id}/problem` | `ProblemIn` | `ProblemOut` | 19 |
| `POST /api/v1/tickets/{id}/problem/incidents` | `{incident_ticket_ids}` | `ProblemOut` | 19 |
| `POST /api/v1/tickets/{id}/problem/known-error` | — | `ProblemOut` | 19 |
| `POST /api/v1/tickets/{id}/problem/close-incidents` | `{note_html?}` | 202 `JobAccepted` | 19 |
| `POST/DELETE /api/v1/tickets/{id}/major-incident` | `{summary?}` | `MajorIncidentOut` | 20 |
| `GET /api/v1/tickets/{id}/major-incident` | — | `MajorIncidentOut` incl. timeline | 20 |
| `POST /api/v1/tickets/{id}/major-incident/updates` | `{mode, body_html}` | 201 `{update, job_id?}` | 20 |
| `GET /api/v1/tickets/{id}/itsm` | — | `TicketItsmOut` (no gate; `enabled=false`) | 21 |
| `GET /api/v1/tickets/{id}/link-graph` | `depth ≤ 3` | `LinkGraphOut` (no gate) | 21 |

Admin (`AdminUser`):

| Method + path | Purpose | Task |
|---|---|---|
| `GET /api/v1/admin/itsm` | mode state + detection + allowed transitions + setup summary | 6 |
| `PUT /api/v1/admin/itsm/mode` | `{mode}`; 409 `{"code":"mode_transition_not_allowed","from","to","reason"}` | 6 |
| `GET /api/v1/admin/itsm/setup` / `POST /api/v1/admin/itsm/setup` | preview / apply `{groups}` | 15 |
| `GET/PUT /api/v1/admin/itsm/priority-matrix` | read / replace (409 `read_only` in Mode A) | 16 |
| `GET/POST /api/v1/admin/itsm/link-types`, `PATCH/DELETE /api/v1/admin/itsm/link-types/{id}` | link types | 12 |
| `GET/PUT /api/v1/admin/itsm/services/{service_id}/meta` | service meta | 17 |
| settings section `itsm` | via the existing settings-sections API/UI | 6 |

### I14. Frontend

- `lib/itsmApi.ts`: one function per route above; types from `Schemas`. Query keys: `["itsm","status"]`, `["itsm","cis",query]`, `["itsm","ci",source,id,versionId]`, `["tickets",id,"itsm"]`, `["tickets",id,"cis"]`, `["tickets",id,"link-graph"]`, `["itsm","services",id]`.
- Ticket zoom (`TicketZoomPage`/`TicketHeader`): `TicketItsmChips` (role chip, criticality/impact, "Matrix-Priorität", service incident state badge, flood warning), `MajorIncidentBanner` (top of zoom, linked active major incident), `TicketCisPanel` ("Betroffene CIs": list with state badges, "CI verknüpfen" → `CiPicker`, remove, "In Znuny bearbeiten" in Mode A), `ProblemPanel` (problem tickets), `MajorIncidentPanel` (major tickets), `ItsmDialogs` (buttons "ITSM-Felder", "Entscheidung" → `TicketDynamicFields` with `screen` prop).
- `LinkedTickets` gains: remove (with confirm), link-type select from `/itsm/link-types?pair=ticket_ticket`, "Notiz an verknüpfte Tickets" (`LinkedNoteDialog` → job progress), "Als Grafik" (`LinkGraph`, `@xyflow/react`, read-only, click navigates).
- Agent pages: `/agent/itsm/cis` (`CiListPage`: search, class/state filters, paging) and `/agent/itsm/cis/$source/$ciId` (`CiDetailPage`: attributes tree, version list with "Version ansehen", linked tickets, "In Znuny bearbeiten"); nav entry "CMDB" shown when `ci_available`. `ServiceViewPage`: incident-state badge, service meta, CIs of the service. `NewTicketPage`: impact/criticality → `priority-suggestion` sets the priority field unless the agent changed it; recent-incident warning after picking a service.
- Admin: `/admin/itsm` (`ItsmPage`: mode card with detection and allowed transitions + explanation that the switch to "Tiqora führt das ITSM" is done by the import, setup preview/apply, priority matrix grid editor or read-only view, link to settings section), `/admin/itsm/link-types` (`LinkTypesPage`), `ServicesPage` gains an "ITSM" section (type/criticality read-only in Mode A, owner, description, portal visibility, minimum minutes between incidents).
- All new pages/panels: loading/empty/error states; `CiUnavailable` codes map to plain German messages; demo mocks in `itsmMock.ts` cover Mode A with three CIs, a problem with two incidents, an active major incident.

### I15. Golden ITSM peers (Task 1)

- `tests/golden/itsm-packages.yaml`: per base peer (`znuny-6.5`, `znuny-7.3`) the ordered package list (`GeneralCatalog`, `ITSMCore`, `ITSMIncidentProblemManagement`, `ITSMConfigurationManagement`, `ImportExport`, `ITSMChangeManagement`, `ITSMServiceLevelManagement` — or the 6.x bundle, whatever Znuny ships for that line) with `version`, `url`, `sha256`. 7.3 versions from the spec (GeneralCatalog 7.3.1, ITSMCore 7.3.3, ITSMIncidentProblemManagement 7.3.2, ITSMConfigurationManagement 7.3.5, ITSMChangeManagement 7.3.4, ITSMServiceLevelManagement 7.3.1, ImportExport 7.3.3); 6.5 versions: the newest 6.5.x of each, recorded in Task 1.
- `fetch_itsm_packages.py <peer>`: downloads into `tests/golden/itsm-packages/<peer>/`, verifies sha256 (fails on mismatch; `--record` writes the hash on first fetch), unpacks each `.opm` (XML, base64 `<File>` entries) into `tests/golden/itsm-packages/src/<Package>-<version>/` for source reading (never executed by Tiqora). Fallback when no download exists: build the `.opm` from the `github.com/znuny/<Package>` tag with `Dev::Package::Build` inside the base peer (documented in the README).
- `peers.yaml`: `znuny-6.5-itsm` and `znuny-7.3-itsm` with `base: znuny-6.5|znuny-7.3` and `itsm: true`; `peer_env.py` exports `GOLDEN_ITSM=1`, `GOLDEN_ITSM_DIR`, and a separate compose project/volume so the plain peers stay ITSM-free.
- `docker-compose.golden.yml`: read-only bind mount `${GOLDEN_ITSM_DIR:-/dev/null}` → `/opt/itsm-packages`; `znuny-entrypoint.sh`: when `GOLDEN_ITSM=1` and the package list is not installed yet (`Admin::Package::List`), `Admin::Package::Install` each package in order, then `Maint::Config::Rebuild` and `Maint::Cache::Delete`.
- `itsm_seed.pl` (copied in with `znuny_copy_in`, run with `znuny_perl_eval`): service `itsm94-svc-web` with criticality `4 high`, SLA with `min_time_bet_incidents`, CIs in the default classes (`Computer` ×2 with 2 and 3 versions, one `Network` CI in incident state), links CI→service (DependsOn), CI↔CI (DependsOn), ticket↔CI (RelevantTo), all names `*.example.org`, IPs from `192.0.2.0/24`.

---

## Deviations from spec

- **D1 Editable dynamic fields in zoom already exist** (wave 0: `TicketDynamicFields` with Znuny's `AgentTicketFreeText` config). This plan only adds the ITSM screens `AgentTicketAddtlITSMField` and `AgentTicketDecision`.
- **D2 Mode A shows CIs only.** Spec §1a lists changes/work orders for Mode A; the phase-1 row says "CIs visible + linkable". Changes stay in Znuny (deep link) until the phase-3 legacy archive (open question 11). Task 2 still surveys `change_*` for later phases.
- **D3 Mode A link writes skip `configitem_history`** (open question 1).
- **D4 Root cause/workaround in `tiqora_itsm_problem`**, not as dynamic fields (open question 2).
- **D5 "Minimum time between incidents" per service as a warning**; Znuny's per-SLA value is shown read-only in Mode A (open question 10).
- **D6 Setup is explicit**, `Change`/`Change::Task` opt-in (open question 3).
- **D7 Link-type admin cannot create Znuny link types**: Znuny defines them in SysConfig (`LinkObject::Type###*`, `PossibleLink`), which Tiqora must not write. The admin manages `tiqora_itsm_link_type` (labels, which types Tiqora offers, Tiqora-only types and propagation for Mode B). Ticket ↔ ticket links can only use types Znuny can store.
- **D8 Service type outside Mode A is free text with suggestions**, not a general-catalog value (the Tiqora catalog belongs to the cmdb plan; it may later turn the field into a catalog reference).
- **D9 CI attachments are not shown in Mode A** (Znuny virtual FS); "In Znuny bearbeiten" covers them.
- **D10 ITSM statistics** (first-level solution rate, average solution time) are not in this plan (reporting design).
- **D11 `link_objects` parity fixes** (reverse-direction check, type groups, `TicketLinkAdd`/`TicketLinkDelete` history) are added here; the spec only asked for "delete". They are needed so link writes behave like Znuny's (ground rule 3); today Tiqora writes no `TicketLinkAdd` history.
- **D12 Migration head**: the brief named `20261010_0073`; the read worktree's head is `20261010_0076`. The plan always says "next free number at implementation time".

---

## Cross-plan contracts

| Contract | Owner | Consumers | Content |
|---|---|---|---|
| C1 CI provider | **itsm1** (`tiqora/itsm/ci_provider.py`, `providers.py`, contract kit `backend/tests/itsm_provider_contract.py`) | cmdb (implements `CiProvider` with `source="tiqora"`, registers `CiProviderFactory(mode="tiqora")` in `register_core`, runs the contract kit), itsm-import (MCP tools `cmdb_search/get/impact` call `get_ci_provider`) | I3 exactly. Additive changes only (new optional methods with defaults); renames need both plans updated. Until cmdb lands, mode `tiqora` has no provider → `ci_available=false`, `unavailable_code="cmdb_not_installed"`. |
| C2 Mode | **itsm1** (`tiqora/itsm/mode.py`) | cmdb (reads `get_itsm_mode`; UI hides CMDB editing unless `tiqora`), itsm-import (`switch_to_tiqora(session, user_id=…, import_run_id=…)` at the end of a non-dry-run import; reads `ZnunyItsmInfo`) | I2. Only the importer calls `switch_to_tiqora`. `tiqora_cmdb_empty()` uses the Mode B provider's `is_empty()`. |
| C3 Znuny adapter readers | **itsm1** (`tiqora/itsm/znuny_adapter/*`) | itsm-import (importer reads general catalog, definitions, CIs + all versions, attributes, CI links, `cip_allocate`, service/SLA ITSM columns through the bulk iterators and row dataclasses of I4; definition parsing via `parse_definition`; attributes via `version_attributes` + `values_from_xml_data`) | Row dataclass field names in I4 are fixed. Schema facts per version in `docs/itsm-import.md` (Tasks 2–3); itsm-import adds its importer chapters below them. |
| C4 Link types | **itsm1** (`tiqora_itsm_link_type`, `tiqora/itsm/link_types.py`) | cmdb (`tiqora_itsm_link.link_type` stores `tiqora_itsm_link_type.name`; propagation from `propagation`; offers from `offer_ci_ci`/`offer_ci_service`/`offer_ticket_ci`), itsm-import (maps Znuny link type names 1:1 to rows; `sync_link_type_rows` before import) | cmdb must not create a second link-type table. Name is the stable key. |
| C5 Priority matrix + service meta | **itsm1** | itsm-import (`replace_matrix_from_import`; service meta upsert via `save_service_meta(..., import_mode=True)` which bypasses the Mode A read-only rule because the switch happens in the same run) | I8, I9. |
| C6 Frontend pieces | **itsm1** (`lib/itsmApi.ts`, `components/itsm/CiPicker`, `CiStateBadge`, `CiAttributeTree`, `TicketCisPanel`, routes `/agent/itsm/cis`, `/agent/itsm/cis/$source/$ciId`) | cmdb (adds edit/create/class designer on top: its own `lib/cmdbApi.ts`; extends `CiDetailPage` through a `children`/slot prop `editActions` instead of forking it; reuses `CiPicker` for CI-reference attributes) | Routes and component props as named here; cmdb adds, does not rename. |
| C7 Docs | **itsm1** creates `docs/itsm.md` with chapters "1 Überblick und Modi", "2 Incident und Problem", "3 Service und Prioritätsmatrix", "4 Verknüpfungen", "5 Znuny führt das ITSM (Modus A)", "6 CMDB (Modus B)" (stub: "folgt"), "7 Parallelbetrieb und Umstieg" (stub with mode table) and `docs/itsm-import.md` sections "Schema per version" + "Behaviour" | cmdb fills chapter 6; itsm-import fills chapter 7 (runbook, add-on, "what you lose / keep") and adds importer chapters to `itsm-import.md` | Each plan edits only its chapters. |
| C8 Seed id bands | itsm1 uses 940000–941999 | others | suggestion only |
| C9 Migration | each plan its own migration | — | whoever merges second renumbers to the next free number and re-chains `down_revision`. |

Plugins (plugins2a/2b) and scim share no interface with this plan; `CI_PROVIDERS` is `sdk_stable=False` (a future connector extension point is the cmdb/plugins concern).

## Shared files (coordination with the other wave-4 plans)

Hotspots: `backend/src/tiqora/extensions/core.py` (register items), `backend/src/tiqora/api/v1/__init__.py`, `api/v1/admin/__init__.py`, `db/tiqora/__init__.py` (model module registration), `backend/tests/test_migration_gate.py`, `test_migrate_cli.py`, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `frontend/src/router.tsx`, `lib/adminSearch.ts`, agent navigation, `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` + propagation, `docs/compatibility.md`, `docs/architecture.md`, `tests/golden/{peers.yaml,peer_env.py,docker-compose.golden.yml,znuny-entrypoint.sh,README-multi-peer.md}` (itsm-import will add importer golden tests on the same ITSM peers). Add, never reorder; on conflict keep both sides.

---

## Tasks

Order: phase 0 (1–3) first; Task 4 can start immediately. Backend tasks may run in parallel when their "Needs" are met. **Frontend tasks 22–27 run one after another** (i18n). Task 28 needs a machine with Docker and the ITSM packages.

| # | Task | Stack | Needs |
|---|---|---|---|
| 1 | Golden ITSM peer variants (`znuny-6.5-itsm`, `znuny-7.3-itsm`) | golden | — |
| 2 | Schema survey: per-version schema, generated test fixtures, seed definitions | golden/docs | 1 |
| 3 | Behaviour survey: Znuny ITSM rules pinned as vectors + `docs/itsm-import.md` "Behaviour" | golden/docs | 1 |
| 4 | Migration + ORM (`tiqora_itsm_*`), GDPR erasure | backend | — |
| 5 | ITSM test databases (both dialects × both profiles) | backend tests | 2 |
| 6 | Mode, detection, ITSM settings section, admin mode API | backend | 4, 5 |
| 7 | CI provider contract, `CI_PROVIDERS`, contract kit, no-cross-join guard | backend | 6 |
| 8 | Perl data parser + definition parser (pure) | backend | 2 |
| 9 | Adapter: catalog, CI search/get, attribute decoding, profiles | backend | 5, 8 |
| 10 | Adapter: versions + bulk iterators (importer contract) | backend | 9 |
| 11 | Link parity (`LinkAdd`/`LinkDelete`), ticket link delete API | backend | 3 |
| 12 | Link types: table service, Znuny catalogue, APIs | backend | 4, 11 |
| 13 | `ZnunyCiProvider` (Mode A) | backend | 7, 10, 11, 12 |
| 14 | CI + ticket↔CI API (mode-agnostic) | backend | 13 |
| 15 | Admin write helpers + ITSM setup | backend | 3, 6 |
| 16 | Priority matrix + PATCH hook | backend | 6, 9, 15 |
| 17 | Service meta, incident state, recent incident | backend | 13, 16 |
| 18 | Linked-articles job + "note to linked tickets" API | backend | 11 |
| 19 | Problem management | backend | 15, 18 |
| 20 | Major incident | backend | 15, 18 |
| 21 | Ticket ITSM summary, link graph, ITSM dialog screens | backend | 14, 16, 17, 19, 20 |
| 22 | Frontend foundation: `itsmApi`, demo mocks, routes, nav | frontend | 21 |
| 23 | Frontend admin: ITSM page, link types, service meta | frontend | 22 |
| 24 | Frontend zoom links: delete, types, note to linked, graph | frontend | 23 |
| 25 | Frontend zoom ITSM: CIs, chips, ITSM dialogs | frontend | 24 |
| 26 | Frontend problem + major incident | frontend | 25 |
| 27 | Frontend CI pages, service view, new ticket | frontend | 26 |
| 28 | Golden Mode A on `znuny-6.5-itsm` + `znuny-7.3-itsm` | golden | 11, 13, 15, 16, 17 |
| 29 | E2E (demo) + screenshots | frontend | 27 |
| 30 | Docs + full test and lint run | all | all |

---

### Task 1: Golden ITSM peer variants

**Files:**
- Create: `tests/golden/itsm-packages.yaml`, `tests/golden/fetch_itsm_packages.py`, `tests/golden/itsm_seed.pl`, `tests/golden/test_itsm_peer_smoke.py`
- Modify: `tests/golden/peers.yaml`, `tests/golden/peer_env.py`, `tests/golden/docker-compose.golden.yml`, `tests/golden/znuny-entrypoint.sh`, `tests/golden/README-multi-peer.md`, `.gitignore` (`tests/golden/itsm-packages/`), `justfile` (recipe `golden-itsm-fetch PEER`)

**Interfaces:**
- Consumes: the golden toolkit (`_helpers.znuny_console`, `znuny_perl_eval`, `znuny_copy_in`), `peers.yaml` schema.
- Produces: I15; peers `znuny-6.5-itsm`, `znuny-7.3-itsm`; the unpacked package sources under `tests/golden/itsm-packages/src/` (input for Tasks 2–3).

- [ ] **Step 1: Write the failing smoke test** (`GOLDEN=1`, ITSM peers only; skipped otherwise)

```python
def test_itsm_packages_installed(peer) -> None
    # Admin::Package::List lists every package of itsm-packages.yaml for the base peer, status "installed"
def test_itsm_tables_exist(peer) -> None
    # configitem, configitem_version, configitem_definition, general_catalog, general_catalog_preferences, cip_allocate exist
def test_seed_created_cis_and_links(peer) -> None
    # after itsm_seed.pl: ConfigItemSearch finds the seeded numbers; LinkList(Ticket) contains ITSMConfigItem
def test_plain_peer_has_no_itsm(plain_peer) -> None   # the base peer volume stays ITSM-free
```

- [ ] **Step 2: Run** `GOLDEN_PEER=znuny-6.5-itsm just golden-up && GOLDEN=1 just golden-test -k itsm_peer_smoke` → FAIL (peer unknown).
- [ ] **Step 3: Implement** I15. Find the official download location for each line (Znuny's download server; record the URL per package) and the newest 6.5.x versions; `--record` the sha256 on first fetch and commit the yaml with the hashes. The entrypoint step is idempotent (second start installs nothing). If a download does not exist, use the `Dev::Package::Build` fallback and document it.
- [ ] **Step 4: Run** the smoke test on both ITSM peers and once on `znuny-6.5` (plain) → PASS. Record image build times and package versions in `README-multi-peer.md` ("ITSM peers").
- [ ] **Step 5: Commit**: `test(golden): Znuny ITSM peer variants for 6.5 and 7.3`.

---

### Task 2: Schema survey — per-version schema, generated fixtures, seed definitions

**Files:**
- Create: `tests/golden/itsm_survey.py` (CLI: `python tests/golden/itsm_survey.py <peer> --out backend/tests/fixtures/znuny-itsm/<profile>/`), `backend/tests/fixtures/znuny-itsm/<profile>/{schema.mysql.sql,schema.postgresql.sql,schema-post.mysql.sql,schema-post.postgresql.sql,initial_insert.sql,sysconfig.json,packages.json}` for each profile, `backend/tests/fixtures/znuny-itsm/README.md`, `docs/itsm-import.md` (sections "Supported versions", "Schema per version"), `backend/src/tiqora/itsm/seed_definitions.py` (data only), `backend/tests/test_itsm_survey_fixtures.py`

**Interfaces:**
- Consumes: Task 1 peers; Znuny's `Dev::Tools::Database::XML2SQL` (or the package's `<DatabaseInstall>` XML through Znuny's SQL generator) for both `mysql` and `postgresql`; `information_schema` of the peer DB.
- Produces: the facts I4's `SchemaProfile` constants need (final profile names, definition format, attribute storage, column lists, catalog classes, link objects); generated DDL + initial rows (general catalog incl. preferences, `cip_allocate` default matrix, link types/possible links, the ITSM `sysconfig_default` rows Tiqora reads); the exact ticket types/state/DF rows the packages create (→ `seed_definitions.py`); the `change_*`/`imexport_*` tables (documented only).

- [ ] **Step 1: Write the failing fixture test** (no Docker)

```python
@pytest.mark.parametrize("profile", ["itsm-6", "itsm-7"])     # final names from the survey
def test_fixture_files_complete(profile) -> None
    # every file listed above exists, is non-empty, and packages.json names each package + version
def test_fixtures_have_no_real_data(profile) -> None
    # initial_insert.sql contains only package defaults: no e-mail address, no hostname outside example.org/.test
def test_seed_definitions_match_fixture(profile) -> None
    # every DF in seed_definitions appears in the fixture's dynamic_field rows with the same type and config
```

- [ ] **Step 2: Run** → FAIL (files missing).
- [ ] **Step 3: Implement** `itsm_survey.py`: generate DDL per dialect from the package XML (never hand-written), dump the ITSM rows as dialect-neutral `INSERT` statements (explicit column lists, ISO timestamps), export the `sysconfig_default` rows whose names start with `ITSM`, `LinkObject::`, `Ticket::Frontend::AgentTicketAddtlITSMField`, `Ticket::Frontend::AgentTicketDecision`, `Ticket::Frontend::AgentITSM`, `Frontend::Module###AgentITSM` (as JSON `{name: effective_value}`), and write `docs/itsm-import.md` "Schema per version" (one table per Znuny line: tables, columns, types, indexes, which columns ITSMCore adds to `service`/`sla`, `xml_storage` usage and key layout, 7.x attribute storage, definition format with an anonymised example, counters, history tables, `change_*` and `imexport_*` tables). Decide the profile split (expected two: 6.x and 7.x; more if the schema differs inside a line — then say which versions each covers).
- [ ] **Step 4: Run** the fixture test → PASS. Load each generated DDL once into a throwaway MariaDB and PostgreSQL container (`itsm_survey.py --verify`) → no errors.
- [ ] **Step 5: Commit**: `docs(itsm): Znuny ITSM schema survey for 6.5 and 7.3 with generated fixtures`.

---

### Task 3: Behaviour survey — Znuny ITSM rules pinned as vectors

**Files:**
- Create: `backend/tests/fixtures/znuny-itsm/behaviour/<profile>.json` (vectors), `backend/tests/test_itsm_behaviour_vectors.py`
- Modify: `docs/itsm-import.md` (section "Behaviour"), `tests/golden/itsm_survey.py` (`--behaviour` captures vectors from the peer)

**Interfaces:**
- Consumes: package sources from Task 1 (`tests/golden/itsm-packages/src/…`), the core tree `znuny-6.5.22/`, the ITSM peers.
- Produces: pinned answers (file + line per answer, per profile) that later tasks implement against:
  1. `ITSMCIPAllocate::PriorityAllocationGet` lookup semantics; all 25 default cells.
  2. Service incident state: which link types/directions count, functionality ranking (ITSMCore `Service.pm` override / `ServiceGet(IncidentState)`); 3 seeded scenarios with expected results.
  3. Whether criticality is copied from the service into `ITSMCriticality` (server-side event module or form JS) — open question 9.
  4. Priority recalculation on impact/criticality change: where it happens (form AJAX vs event module).
  5. `LinkObject::ITSMConfigItem` backend side effects of `LinkAdd`/`LinkDelete` (history type names, incident recalculation, events) — open question 1.
  6. Ticket↔CI link from `AgentLinkObject`: which object is source, link type, state; possible-link config; permission checks (`itsm-configitem` group, class `Permission` preference) — open question 4.
  7. Definition defaults (`CountMin/Max/Default`), `Sub` semantics, input types list per version.
  8. Ticket types/state names created by the packages (incl. the major-incident type name on 6.5 vs 7.3) and the DFs per screen for `AgentTicketAddtlITSMField`/`AgentTicketDecision`.
  9. `LinkObject.pm` `LinkDelete`: which states it deletes, history format (`TicketLinkDelete`), cache keys.
  10. Znuny agent action names for CI zoom/edit per version (deep links).

- [ ] **Step 1: Write the failing vector test** (pure)

```python
def test_vectors_cover_every_question(profile) -> None      # keys 1..10 present with "source" (file:line) and "answer"
def test_cip_vectors_are_25_cells(profile) -> None
def test_service_incident_vectors_have_expected_state(profile) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**: read the sources, run the peer scenarios (`--behaviour`), write vectors + "Behaviour" section (one paragraph per question, with the answer, the source reference and the consequence for Tiqora; mark answers that resolve an open question of this plan). **Step 4: Run** → PASS.
- [ ] **Step 5: Commit**: `docs(itsm): Znuny ITSM behaviour survey with pinned vectors`.

---

### Task 4: Migration + ORM, GDPR erasure

**Files:**
- Create: `backend/src/tiqora/itsm/__init__.py`, `backend/src/tiqora/itsm/models.py`, `backend/alembic/versions_tiqora/<next free number>_itsm_core.py`, `backend/tests/test_itsm_models.py`
- Modify: `backend/src/tiqora/db/tiqora/__init__.py` (import `tiqora.itsm.models`), `backend/src/tiqora/gdpr/erasure.py`, `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`

**Interfaces:** I1 exactly. ORM classes `TiqoraItsmLinkType`, `TiqoraItsmPriorityMatrix`, `TiqoraItsmServiceMeta`, `TiqoraItsmProblem`, `TiqoraItsmMajorIncident`, `TiqoraItsmMajorIncidentUpdate`.

- [ ] **Step 1: Failing tests** (both dialects; band 940000–940099)

```python
async def test_migration_creates_itsm_tables(url_fixture, request) -> None
async def test_orm_ddl_equals_migration() -> None                     # extend test_tiqora_model_ddl coverage if it is table-list driven
async def test_priority_matrix_cell_unique(env) -> None
async def test_major_update_cascades_on_major_delete(env) -> None
async def test_problem_kb_article_set_null_on_article_delete(env) -> None
async def test_gdpr_erasure_removes_itsm_rows(env) -> None             # ticket 940010 with problem + major rows -> erased
async def test_downgrade_drops_itsm_tables(url_fixture, request) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (revision = next free number, see Global Constraints; `MEDIUM_TEXT_VARIANT` for TEXT columns if that is the house rule). **Step 4: Run** the new module + `test_migration_gate.py test_migrate_cli.py test_tiqora_model_ddl.py tests/test_gdpr*.py` → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): tables for link types, priority matrix, service meta, problems and major incidents`.

---

### Task 5: ITSM test databases (both dialects × both profiles)

**Files:**
- Create: `backend/tests/_itsm_db.py`, `backend/tests/_itsm_fixtures.py`, `backend/tests/test_itsm_test_db.py`
- Modify: `backend/tests/conftest.py` (`_SNAPSHOTS` keyed by a name instead of the dialect; `register_snapshot(name, sync_url, dialect)` used by `_itsm_db.py`; behaviour for existing keys unchanged)

**Interfaces:**
- Produces: fixtures `itsm_db_url(dialect: Literal["mysql","postgresql"], profile: str) -> str` (session-cached; a second database `tq_itsm_<profile>` inside the already running container with Znuny core schema + Task 2 fixture DDL/rows + ITSM `sysconfig_default` rows + `TiqoraBase.metadata.create_all`), registered for the module-restore/strict-leak check; helper `seed_ci(session, profile, *, number, class_name, name, versions: list[dict], depl_state, inci_state) -> int` and `seed_ci_link(...)` in `_itsm_fixtures.py` that insert **Znuny-shaped** rows directly (tests only; mirrors what Znuny's `ConfigItemAdd`/`VersionAdd` write per Task 2, incl. `xml_storage` rows).
- Parametrisation helper: `ITSM_PARAMS = [("mysql","itsm-6"), ("mysql","itsm-7"), ("postgresql","itsm-6"), ("postgresql","itsm-7")]`.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_itsm_db_has_core_and_itsm_tables(dialect, profile, request) -> None
async def test_main_test_db_has_no_itsm_tables(url_fixture, request) -> None   # auto-mode tests elsewhere stay valid
async def test_leak_in_itsm_db_is_reported(dialect, profile, request) -> None   # module restore covers the new snapshot
async def test_seed_ci_round_trip(dialect, profile, request) -> None            # seed_ci rows read back as Znuny would store them
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** MariaDB: connect as `root` with the container's root password (verify the testcontainers attribute) for `CREATE DATABASE` + `GRANT`; PostgreSQL: `CREATE DATABASE` with `AUTOCOMMIT`. If `CREATE DATABASE` is impossible in CI, fall back to a separate container per dialect (document the extra start-up time). **Step 4: Run** → PASS (full suite once with `TIQORA_STRICT_DB_LEAKS=1` to prove the conftest change is neutral); lint gates.
- [ ] **Step 5: Commit**: `test(itsm): Znuny ITSM test databases for both dialects and profiles`.

---

### Task 6: Mode, detection, ITSM settings section, admin mode API

**Files:**
- Create: `backend/src/tiqora/itsm/mode.py`, `backend/src/tiqora/itsm/settings.py`, `backend/src/tiqora/itsm/znuny_adapter/{__init__,detect,profiles}.py` (profiles with the Task 2 constants), `backend/src/tiqora/api/v1/admin/itsm.py`, `backend/src/tiqora/api/v1/admin/itsm_schemas.py`, `backend/tests/test_itsm_mode.py`, `backend/tests/test_itsm_detect.py`, `backend/tests/test_admin_itsm_mode_api.py`
- Modify: `backend/src/tiqora/extensions/core.py` (register `ITSM_SECTION`), `backend/src/tiqora/api/v1/admin/__init__.py`, `backend/src/tiqora/main.py` + `backend/src/tiqora/worker/__main__.py` (call `ensure_itsm_mode_initialised` at startup; failure logs `itsm_mode_init_failed` and never blocks startup)

**Interfaces:** I2, `detect.py` + `profiles.py` of I4, admin routes `GET /admin/itsm`, `PUT /admin/itsm/mode`.

- [ ] **Step 1: Failing tests** (band 940100–940199)

```python
def test_auto_default() -> None                                      # core_installed -> znuny_readthrough else tiqora
def test_allowed_transitions_matrix() -> None                        # every (from, to, cmdb_empty, detected) combination of open question 5
@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_detect_on_itsm_db(dialect, profile, request) -> None  # packages, flags, profile name
async def test_detect_plain_db(url_fixture, request) -> None         # nothing installed, profile None
async def test_detect_unsupported_version(…) -> None                 # package_repository version outside every profile -> unsupported_reason
async def test_auto_default_persisted_once(…) -> None                # ensure_* twice; second detection result ignored
async def test_later_znuny_itsm_install_does_not_flip(…) -> None
async def test_concurrent_initialisation_keeps_first(…) -> None
async def test_admin_cannot_switch_a_to_b(…) -> None                 # PUT mode tiqora from znuny_readthrough -> 409 mode_transition_not_allowed
async def test_admin_switch_to_off_and_back(…) -> None
async def test_b_to_a_needs_empty_cmdb(…) -> None                    # fake Mode B provider with is_empty False -> 409
async def test_importer_switch_records_run(…) -> None                # switch_to_tiqora stores source importer + run id
async def test_settings_section_defaults_and_validation(…) -> None
async def test_non_admin_forbidden(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): itsm.mode with automatic default and guarded transitions`.

---

### Task 7: CI provider contract, `CI_PROVIDERS`, contract kit, no-cross-join guard

**Files:**
- Create: `backend/src/tiqora/itsm/ci_provider.py`, `backend/src/tiqora/itsm/providers.py`, `backend/tests/itsm_provider_contract.py` (importable kit, not collected itself), `backend/tests/_itsm_fake_provider.py` (in-memory provider used by API tests), `backend/tests/test_itsm_provider_registry.py`, `backend/tests/test_itsm_no_cross_join.py`
- Modify: `backend/src/tiqora/extensions/core.py` (nothing registered yet; the Mode A factory comes in Task 13)

**Interfaces:**
- Produces: I3 (C1). The kit exposes `class CiProviderContract:` with abstract fixtures `provider`, `seed(session) -> SeededCis` (two readable CIs, one unreadable for user `itsm94-ro`, one service link) and ~15 test methods (`test_ref_source_mismatch_not_found`, `test_search_total_exact`, `test_search_limit_clamped`, `test_get_many_omits_unreadable`, `test_link_ticket_idempotent`, `test_unlink_counts`, `test_versions_paging`, `test_service_incident_state_worst_wins`, `test_no_commit_inside_provider`, …). The fake provider passes it.
- Static guard: parses every `text("…")`/`select(...)` source string in `backend/src/tiqora/itsm/**` and fails when one statement names a `tiqora_` table and a Znuny table (list from `backend/src/tiqora/bootstrap/schema/schema.mysql.sql` table names).

- [ ] **Step 1: Failing tests**

```python
def test_ci_ref_parse_and_str() -> None                      # "znuny:12" round trip; "x:1", "znuny:-1", "znuny:" -> ValueError
async def test_get_ci_provider_none_when_off(…) -> None
async def test_get_ci_provider_none_without_factory(…) -> None   # mode tiqora, no cmdb -> None
async def test_get_ci_provider_uses_registered_factory(…) -> None
async def test_tiqora_cmdb_empty_without_provider_is_true(…) -> None
class TestFakeProviderContract(CiProviderContract): ...      # the kit against the fake
def test_no_cross_join_in_itsm_package() -> None
def test_guard_detects_a_cross_join(tmp_path) -> None        # the guard itself fails on a planted statement
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): CI provider contract with conformance kit`.

---

### Task 8: Perl data parser + definition parser (pure)

**Files:**
- Create: `backend/src/tiqora/itsm/znuny_adapter/perl_data.py`, `backend/src/tiqora/itsm/znuny_adapter/definitions.py`, `backend/tests/test_itsm_perl_data.py`, `backend/tests/test_itsm_definitions.py`, `backend/tests/fixtures/znuny-itsm/definitions/` (the default class definitions of each profile, copied from the Task 2 fixture rows)

**Interfaces:** `parse_perl_data`, `PerlDataError`, `AttrDef`, `parse_definition` (I4).

- [ ] **Step 1: Failing tests** (pure)

```python
def test_parses_nested_structures_and_quotes() -> None
def test_bareword_keys_and_fat_comma() -> None
def test_undef_numbers_comments() -> None
@pytest.mark.parametrize("snippet", ["sub { 1 }", "qw(a b)", "do { }", "eval '1'", "$x", "@a", "%h", "\\'x'", "`ls`"])
def test_code_is_rejected(snippet) -> None                  # PerlDataError, never executed
def test_deeply_nested_input_bounded() -> None              # depth > 64 or > 1 MiB -> PerlDataError
@pytest.mark.parametrize("profile", PROFILES)
def test_default_class_definitions_parse(profile) -> None   # Computer, Hardware, Location, Network, Software
def test_count_defaults_from_behaviour_vectors() -> None    # Task 3 question 7
def test_sub_attributes_nested() -> None
def test_yaml_and_perl_same_semantics() -> None             # same definition in both formats -> equal AttrDefs
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (hand-written tokenizer + recursive descent; no `eval`, no regex catastrophes — linear scanning). **Step 4: Run** → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): safe parsers for Znuny CI class definitions`.

---

### Task 9: Adapter — catalog, CI search/get, attribute decoding

**Files:**
- Create: `backend/src/tiqora/itsm/znuny_adapter/{catalog,attributes,reader}.py`, `backend/src/tiqora/znuny/xml_hash.py` (moved from `reporting/znuny_stats/xml_hash.py`), `backend/tests/test_itsm_adapter_read.py`, `backend/tests/test_itsm_attributes.py`
- Modify: `backend/src/tiqora/reporting/znuny_stats/xml_hash.py` (re-export only), `backend/src/tiqora/itsm/znuny_adapter/profiles.py`

**Interfaces:** `catalog.py`, `attributes.py`, `ZnunyItsmReader.search/get_ci/get_version/version_attributes/definitions` (I4).

- [ ] **Step 1: Failing tests** (`ITSM_PARAMS`; band 940200–940399; CIs via `seed_ci`)

```python
async def test_catalog_states_with_functionality(…) -> None
async def test_class_permission_groups(…) -> None
async def test_search_by_number_name_class_states(…) -> None
async def test_search_attribute_value_min_three_chars(…) -> None     # IP 192.0.2.10 found; "19" -> ignored
async def test_search_attribute_candidates_capped(…) -> None         # > 500 candidates -> capped, total still exact for name search
async def test_get_ci_current_version_attributes(…) -> None
async def test_attributes_repeatable_and_nested(…) -> None           # CountMax 3 network adapters with Sub IP addresses
async def test_display_resolution_batched(…) -> None                 # general catalog, CI ref, user, customer, service names; query count bounded
def test_unknown_input_type_kept_as_other() -> None
async def test_unsupported_profile_raises(…) -> None                 # CiUnavailable("unsupported_znuny_itsm_version")
def test_xml_hash_move_keeps_reporting_import() -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/test_reporting_znuny_stats*.py` → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): read Znuny CIs, catalogs and attributes per schema version`.

---

### Task 10: Adapter — versions + bulk iterators (importer contract)

**Files:**
- Create: `backend/tests/test_itsm_adapter_versions.py`, `backend/tests/test_itsm_adapter_bulk.py`
- Modify: `backend/src/tiqora/itsm/znuny_adapter/reader.py`

**Interfaces:** `versions`, `iter_general_catalog`, `iter_class_definitions`, `iter_config_items`, `versions_for`, `iter_ci_links`, `cip_allocate`, `service_itsm_columns`, `sla_itsm_columns` and the row dataclasses (I4, C3).

- [ ] **Step 1: Failing tests** (`ITSM_PARAMS`; band 940400–940499)

```python
async def test_versions_paged_newest_first(…) -> None
async def test_version_detail_uses_its_definition_version(…) -> None   # CI whose class definition changed between versions
async def test_iter_config_items_keyset_paging(…) -> None              # 7 CIs, limit 3 -> 3 pages, no duplicates
async def test_versions_for_returns_all_versions(…) -> None
async def test_iter_ci_links_both_directions(…) -> None
async def test_cip_allocate_default_matrix(…) -> None                  # equals Task 3 vectors
async def test_service_and_sla_itsm_columns(…) -> None
def test_row_dataclass_field_names_are_the_contract() -> None          # pins C3 field names
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): Znuny CI versions and bulk readers for the importer`.

---

### Task 11: Link parity (`LinkAdd`/`LinkDelete`), ticket link delete API

**Files:**
- Create: `backend/tests/test_link_parity.py`, `backend/tests/test_ticket_link_delete_api.py`
- Modify: `backend/src/tiqora/domain/ticket_write_service.py` (I5), `backend/src/tiqora/api/v1/tickets.py` (DELETE route), `backend/src/tiqora/process/engine.py` (`LinkAdd` action: `LinkConflict` → log + continue)

**Interfaces:** I5; `DELETE /api/v1/tickets/{id}/links/{target_id}?link_type=`.

- [ ] **Step 1: Failing tests** (both dialects; band 940500–940599)

```python
async def test_unpointed_reverse_duplicate_not_inserted(env) -> None    # Normal B->A after A->B -> False, one row
async def test_pointed_opposite_direction_conflict(env) -> None         # ParentChild B->A after A->B -> LinkConflict
async def test_existing_other_state_conflict(env) -> None
async def test_type_group_conflict(env) -> None                         # SysConfig TypeGroup fixture
async def test_ticket_link_add_history_both_sides(env) -> None          # names "%%<TN>%%<id>%%<id>" like Ticket.pm BackendLinkAddPost
async def test_no_ticket_history_for_ticket_ci_link(env) -> None        # other object not a Ticket -> no TicketLinkAdd
async def test_unlink_removes_either_direction_and_writes_history(env) -> None
async def test_unlink_returns_zero_when_absent(env) -> None
async def test_link_cache_invalidated(env) -> None
async def test_delete_api_requires_rw_on_both(env) -> None              # 403 without rw on target
async def test_delete_api_link_type_filter(env) -> None
async def test_process_linkadd_conflict_logged_not_raised(env) -> None
async def test_split_article_still_links(env) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** I5, citing `LinkObject.pm` (`LinkAdd`, `LinkDelete`, `PossibleType`) and `LinkObject/Ticket.pm` line ranges from Task 3. **Step 4: Run** + `tests/test_tickets*link*.py tests/test_process_handler_parity.py tests/test_split*.py` → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `fix(links): LinkAdd/LinkDelete parity with Znuny incl. ticket history, and link removal`.

---

### Task 12: Link types — table service, Znuny catalogue, APIs

**Files:**
- Create: `backend/src/tiqora/itsm/link_types.py`, `backend/tests/test_itsm_link_types.py`, `backend/tests/test_admin_itsm_link_types_api.py`
- Modify: `backend/src/tiqora/api/v1/admin/itsm.py` (+ schemas), `backend/src/tiqora/api/v1/itsm.py` (create with `router`; `GET /itsm/link-types`), `backend/src/tiqora/api/v1/__init__.py`, `backend/src/tiqora/api/v1/tickets.py` (`POST /tickets/{id}/links` validates `link_type` against the offered list → 422 `link_type_not_offered`)

**Interfaces:** I6, C4.

- [ ] **Step 1: Failing tests** (both dialects + one `ITSM_PARAMS` case; band 940600–940699)

```python
async def test_znuny_types_from_sysconfig(…) -> None              # Normal + ParentChild (core); + ITSM types on the ITSM DB
async def test_sync_inserts_missing_and_keeps_overrides(…) -> None
async def test_sync_never_deletes(…) -> None
async def test_offered_ticket_ticket_only_storable_types(…) -> None   # a tiqora-origin type is never offered for ticket<->ticket
async def test_offered_ticket_ci_mode_a_uses_possible_links(…) -> None
async def test_admin_cannot_rename_znuny_type(…) -> None          # PATCH name on origin=znuny -> 422
async def test_admin_crud_tiqora_type(…) -> None
async def test_link_create_rejects_type_not_offered(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): link type admin on top of Znuny's link types`.

---

### Task 13: `ZnunyCiProvider` (Mode A)

**Files:**
- Create: `backend/src/tiqora/itsm/znuny_adapter/provider.py`, `backend/src/tiqora/itsm/deeplink.py`, `backend/tests/test_itsm_znuny_provider.py`
- Modify: `backend/src/tiqora/extensions/core.py` (register `CiProviderFactory(mode="znuny_readthrough", create=ZnunyCiProvider)`)

**Interfaces:** I3 implemented per I4; deep links per Task 3 question 10.

- [ ] **Step 1: Failing tests** (`ITSM_PARAMS`; band 940700–940899)

```python
class TestZnunyProviderContract(CiProviderContract): ...         # the Task 7 kit, all four combinations
async def test_permission_needs_itsm_configitem_group(…) -> None
async def test_permission_class_group(…) -> None
async def test_link_ticket_writes_link_relation_like_znuny(…) -> None   # object ITSMConfigItem, direction/type per Task 3
async def test_mode_a_writes_no_itsm_table(…) -> None            # checksums of configitem*, general_catalog*, xml_storage, cip_allocate, service, sla unchanged after search/get/versions/link/unlink
async def test_service_incident_state_vectors(…) -> None         # Task 3 question 2 scenarios
async def test_edit_url_from_sysconfig_and_override(…) -> None
async def test_unsupported_version_raises_ci_unavailable(…) -> None
async def test_is_empty(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): Mode A — Znuny CIs read-only and linkable in Tiqora`.

---

### Task 14: CI + ticket↔CI API (mode-agnostic)

**Files:**
- Create: `backend/src/tiqora/api/v1/itsm_schemas.py`, `backend/tests/test_itsm_ci_api.py`
- Modify: `backend/src/tiqora/api/v1/itsm.py` (`router` + `ticket_router`), `backend/src/tiqora/api/v1/__init__.py` (include `itsm.ticket_router` like `chat.ticket_router`)

**Interfaces:** the I13 rows for Task 14; error mapping: `CiNotFound` → 404, `CiPermissionDenied` → 404 (no existence leak), `CiUnavailable` → 409 `{"code": <code>}`, mode off → 409 `itsm_off`, `CiReadOnly` → 409 `read_only`, link to a ticket without `rw` → 403.

- [ ] **Step 1: Failing tests** (fake provider for both dialects; one Mode A end-to-end on `ITSM_PARAMS`; band 940900–940999)

```python
async def test_status_without_gate(…) -> None                   # off -> mode off, ci_available false
async def test_status_cmdb_not_installed(…) -> None             # mode tiqora, no factory
async def test_search_and_detail(…) -> None
async def test_detail_unreadable_is_404(…) -> None
async def test_versions_paging(…) -> None
async def test_ci_tickets_filtered_by_ro(…) -> None
async def test_ticket_cis_list_link_unlink(…) -> None
async def test_link_requires_rw_on_ticket(…) -> None
async def test_link_type_must_be_offered(…) -> None
async def test_off_returns_409(…) -> None
async def test_mode_a_end_to_end(…) -> None                     # search -> link -> ticket list shows it -> unlink
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): CI search, detail and ticket links API`.

---

### Task 15: Admin write helpers + ITSM setup

**Files:**
- Create: `backend/src/tiqora/domain/znuny_admin_writes.py`, `backend/src/tiqora/itsm/setup.py`, `backend/tests/test_znuny_admin_writes.py`, `backend/tests/test_itsm_setup.py`
- Modify: `backend/src/tiqora/api/v1/admin/{types,states,dynamic_fields}.py` (use the helpers), `backend/src/tiqora/api/v1/admin/itsm.py` (+ schemas: setup routes), `backend/src/tiqora/itsm/seed_definitions.py` (if Task 3 corrected names)

**Interfaces:** I7.

- [ ] **Step 1: Failing tests** (both dialects + `ITSM_PARAMS`; band 941000–941099)

```python
async def test_helpers_equal_admin_routes(…) -> None            # same rows/caches as before the move (existing admin tests also green)
async def test_plan_on_plain_db_all_missing(…) -> None
async def test_plan_on_itsm_db_all_present(…) -> None            # the packages already created them
async def test_conflict_detected_not_changed(…) -> None          # DF ITSMImpact exists as Text -> conflict, untouched
async def test_invalid_row_reported(…) -> None
async def test_apply_creates_znuny_identical_rows(…) -> None     # compare with the Task 2 fixture rows (name, type, config YAML decoded, label)
async def test_apply_change_group_only_when_selected(…) -> None
async def test_apply_is_idempotent(…) -> None
async def test_type_names_follow_settings(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/test_admin_*types*.py tests/test_admin_*states*.py tests/test_admin_dynamic_fields*.py` → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): ITSM setup creates Znuny-identical types, state and dynamic fields`.

---

### Task 16: Priority matrix + PATCH hook

**Files:**
- Create: `backend/src/tiqora/itsm/priority.py`, `backend/tests/test_itsm_priority.py`, `backend/tests/test_itsm_priority_api.py`
- Modify: `backend/src/tiqora/api/v1/tickets.py` (`patch_ticket`: after DFs/priority writes call `apply_matrix_after_write(..., explicit_priority=body.priority_id is not None)`), `backend/src/tiqora/api/v1/itsm.py`, `backend/src/tiqora/api/v1/admin/itsm.py` (+ schemas)

**Interfaces:** I8.

- [ ] **Step 1: Failing tests** (both dialects + `ITSM_PARAMS`; band 941100–941199)

```python
def test_lookup_vectors(profile) -> None                          # Task 3 question 1, all 25 cells
async def test_mode_a_reads_cip_allocate_read_only(…) -> None      # PUT admin matrix -> 409 read_only
async def test_tiqora_matrix_save_and_load(…) -> None
async def test_patch_impact_sets_priority(…) -> None               # history PriorityUpdate written by change_priority
async def test_patch_with_explicit_priority_wins(…) -> None
async def test_patch_unrelated_df_no_recompute(…) -> None
async def test_matrix_disabled_or_off_no_change(…) -> None
async def test_suggestion_endpoint(…) -> None
async def test_replace_matrix_from_import(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (+ `apply_service_criticality_after_write` only if Task 3 question 3 says Znuny does it). **Step 4: Run** + `tests/test_tickets_patch*.py` → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): priority from criticality × impact (Znuny matrix in Mode A)`.

---

### Task 17: Service meta, incident state, recent incident

**Files:**
- Create: `backend/src/tiqora/itsm/services.py`, `backend/tests/test_itsm_services.py`
- Modify: `backend/src/tiqora/api/v1/itsm.py`, `backend/src/tiqora/api/v1/admin/itsm.py` (+ schemas)

**Interfaces:** I9; I13 rows of Task 17.

- [ ] **Step 1: Failing tests** (both dialects + `ITSM_PARAMS`; band 941200–941299)

```python
async def test_mode_a_type_and_criticality_from_service_columns(…) -> None
async def test_mode_a_meta_type_change_rejected(…) -> None
async def test_tiqora_meta_round_trip(…) -> None
async def test_incident_state_via_provider(…) -> None
async def test_incident_states_batch_limit(…) -> None              # > 200 ids -> 422
async def test_recent_incident_window_and_type(…) -> None
async def test_recent_incident_ro_filtered(…) -> None
async def test_service_default_from_settings(…) -> None
async def test_import_mode_bypass_for_itsm_import(…) -> None       # save_service_meta(import_mode=True) in Mode A
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): service ITSM data, live incident state and incident flood warning`.

---

### Task 18: Linked-articles job + "note to linked tickets" API

**Files:**
- Create: `backend/src/tiqora/itsm/linked_articles.py`, `backend/tests/test_itsm_linked_articles.py`
- Modify: `backend/src/tiqora/extensions/core.py` (register `LINKED_ARTICLES_JOB`), `backend/src/tiqora/api/v1/itsm.py` (`POST /tickets/{id}/linked-articles`)

**Interfaces:** I10.

- [ ] **Step 1: Failing tests** (both dialects; band 941300–941399; mail sender stubbed)

```python
async def test_api_enqueues_job_and_returns_202(…) -> None
async def test_note_on_each_linked_ticket(…) -> None               # articles + history from add_article
async def test_job_skips_ticket_without_rw(…) -> None              # permission removed after enqueue
async def test_job_skips_unlinked_target(…) -> None                # link removed after enqueue
async def test_max_targets_enforced(…) -> None
async def test_set_state_after_note(…) -> None                     # close-with-workaround path
async def test_reply_mode_uses_deliver_agent_email_reply(…) -> None
async def test_interrupted_after_checkpoint_not_resent(…) -> None
async def test_result_lists_done_and_skipped(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/test_jobs*.py` → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(links): note to all linked tickets as a background job`.

---

### Task 19: Problem management

**Files:**
- Create: `backend/src/tiqora/itsm/problem.py`, `backend/tests/test_itsm_problem.py`
- Modify: `backend/src/tiqora/api/v1/itsm.py` (+ schemas)

**Interfaces:** I11 (problem part); I13 rows of Task 19.

- [ ] **Step 1: Failing tests** (both dialects; band 941400–941499; KB service with search indexing stubbed)

```python
async def test_save_problem_only_on_problem_type(…) -> None        # 409 not_a_problem otherwise
async def test_link_incidents_parentchild(…) -> None
async def test_linked_incidents_ro_filtered(…) -> None
async def test_known_error_creates_review_article_and_kb_link(…) -> None
async def test_known_error_updates_existing_article(…) -> None
async def test_known_error_needs_category(…) -> None               # 409 known_error_category_missing
async def test_close_incidents_job_sets_workaround_state(…) -> None
async def test_close_incidents_skips_closed_and_no_rw(…) -> None
async def test_off_returns_409(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** + `tests/test_kb*.py` → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): problem management with known errors and closing incidents with workaround`.

---

### Task 20: Major incident

**Files:**
- Create: `backend/src/tiqora/itsm/major_incident.py`, `backend/tests/test_itsm_major_incident.py`
- Modify: `backend/src/tiqora/api/v1/itsm.py` (+ schemas)

**Interfaces:** I11 (major part); I13 rows of Task 20.

- [ ] **Step 1: Failing tests** (both dialects; band 941500–941599)

```python
async def test_declare_changes_type_and_records(…) -> None          # TypeUpdate history via change_type
async def test_declare_twice_is_idempotent(…) -> None
async def test_end_keeps_type(…) -> None
async def test_internal_update_only_on_major_ticket(…) -> None
async def test_note_update_enqueues_job_for_linked(…) -> None
async def test_reply_update_refused_when_setting_off(…) -> None     # 409 customer_reply_disabled
async def test_timeline_order(…) -> None
async def test_banner_for_linked_ticket(…) -> None
async def test_banner_hidden_without_ro_on_major_ticket(…) -> None
async def test_banner_gone_after_end(…) -> None
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): major incidents with banner and one update for all linked tickets`.

---

### Task 21: Ticket ITSM summary, link graph, ITSM dialog screens

**Files:**
- Create: `backend/src/tiqora/itsm/summary.py`, `backend/src/tiqora/itsm/link_graph.py`, `backend/tests/test_itsm_summary.py`, `backend/tests/test_itsm_link_graph.py`, `backend/tests/test_reference_itsm_screens.py`
- Modify: `backend/src/tiqora/api/v1/itsm.py` (+ schemas), `backend/src/tiqora/api/v1/reference.py` (`DynamicFieldScreen` + fallbacks)

**Interfaces:** I12; I13 rows of Task 21.

- [ ] **Step 1: Failing tests** (both dialects + one `ITSM_PARAMS`; band 941600–941699)

```python
async def test_summary_disabled_when_off_or_setup_missing(…) -> None
async def test_summary_incident_with_service_state_and_matrix(…) -> None
async def test_summary_query_count_bounded(…) -> None                # no N+1 (statement counter)
async def test_summary_problem_and_banner(…) -> None
async def test_graph_depth_and_truncation(…) -> None
async def test_graph_omits_unreadable_ticket(…) -> None
async def test_graph_includes_cis_mode_a(…) -> None
async def test_itsm_screens_from_sysconfig(…) -> None                # ITSM DB: Znuny config used
async def test_itsm_screens_fallback_without_znuny_itsm(…) -> None   # plain DB: Task 3 defaults
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; regenerate OpenAPI; lint gates.
- [ ] **Step 5: Commit**: `feat(itsm): ticket ITSM summary, link graph and ITSM field screens`.

---

### Task 22: Frontend foundation — `itsmApi`, demo mocks, routes, nav

**Files:**
- Create: `frontend/src/lib/itsmApi.ts` (+ `itsmApi.test.ts`), `frontend/src/demo/itsmMock.ts` (+ test), `frontend/src/components/itsm/{CiStateBadge,CiAttributeTree}.tsx` (+ tests), empty route components for `/agent/itsm/cis`, `/agent/itsm/cis/$source/$ciId`, `/admin/itsm`, `/admin/itsm/link-types`
- Modify: `frontend/src/router.tsx`, agent navigation (entry "CMDB" when `ci_available`), `frontend/src/lib/adminSearch.ts` ("ITSM", "Verknüpfungstypen", "Prioritätsmatrix"), `frontend/src/demo/mockData.ts`, `frontend/e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` (namespace `itsm.*`, `admin.itsm.*`, `admin.settings.itsm.*`, `jobs.kinds.core.itsm_linked_articles`) + propagation

**Interfaces:** I14 (API wrapper, query keys); C6.

- [ ] **Steps:** tests first (`itsmApi` builds the right paths/queries; mock returns Mode A data; `CiStateBadge` maps functionality to tokens `success`/`warning`/`danger`; `CiAttributeTree` renders nested repeatable attributes and CI references as links) → run → implement → `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → commit `feat(itsm): frontend API, demo data and routes`.

---

### Task 23: Frontend admin — ITSM page, link types, service meta

**Files:**
- Create: `frontend/src/routes/admin/ItsmPage.tsx` (+ test), `frontend/src/routes/admin/LinkTypesPage.tsx` (+ test), `frontend/src/components/itsm/PriorityMatrixEditor.tsx` (+ test)
- Modify: `frontend/src/routes/admin/ServicesPage.tsx` (ITSM section), i18n + propagation

Behaviour: mode card shows the current mode, how it was set ("automatisch erkannt", "vom Administrator", "durch den Import"), what Znuny has installed, and only the allowed transitions as buttons; the text for Mode A explains "Wechsel zu „Tiqora führt das ITSM“ erfolgt über den ITSM-Import" (link target is a placeholder until the itsm-import plan adds the page; hide the link when the route does not exist). Setup: grouped table present/missing/conflict, checkbox per group, "Einrichten". Matrix: grid criticality × impact with priority selects; read-only with "In Znuny bearbeiten" in Mode A. Link types: list with origin badge ("aus Znuny"/"Tiqora"), edit labels/offer flags/valid; propagation only for Tiqora types (and only shown when mode is `tiqora`). Services: ITSM section fields; Mode A shows type/criticality read-only with a note.

- [ ] **Steps:** tests (allowed transitions render; 409 transition error shown; setup apply flow; matrix read-only in Mode A; link type edit; service meta save) → run → implement → demo check with Playwright → lint/test/i18n → commit `feat(itsm): admin pages for ITSM mode, setup, priority matrix and link types`.

---

### Task 24: Frontend zoom links — delete, types, note to linked, graph

**Files:**
- Create: `frontend/src/components/itsm/{LinkGraph,LinkedNoteDialog}.tsx` (+ tests)
- Modify: `frontend/src/components/agent/LinkedTickets.tsx` (+ test), `TicketLinkChip.tsx`, i18n + propagation

Behaviour: each linked ticket gets "Verknüpfung entfernen" (confirm dialog naming both tickets); the link dialog offers the types from `/itsm/link-types?pair=ticket_ticket`; "Notiz an verknüpfte Tickets" opens a dialog with all linked tickets preselected (checkboxes), subject, text (existing composer editor frame), "für Kunden sichtbar" switch, submit → job progress component → result list (done / skipped with reason). "Als Grafik" toggles the read-only graph (nodes: tickets with state colour, CIs with incident state; click navigates; "Weitere Verknüpfungen ausgeblendet" when truncated).

- [ ] **Steps:** tests (remove calls DELETE and refreshes; type list; dialog submits and shows progress/result; graph renders nodes/edges from mock and handles truncation) → run → implement → demo check → lint/test/i18n → commit `feat(links): remove links, link types, note to linked tickets and link graph in the ticket`.

---

### Task 25: Frontend zoom ITSM — CIs, chips, ITSM dialogs

**Files:**
- Create: `frontend/src/components/itsm/{CiPicker,TicketCisPanel,TicketItsmChips,ItsmDialogs}.tsx` (+ tests)
- Modify: `frontend/src/components/agent/TicketDynamicFields.tsx` (`screen` prop, default `AgentTicketFreeText`; no behaviour change for existing callers), `frontend/src/lib/phoneApi.ts` (`DynamicFieldScreen` union), `frontend/src/components/agent/TicketHeader.tsx`, `frontend/src/routes/agent/TicketZoomPage.tsx`, i18n + propagation

Behaviour: everything renders only when `GET /tickets/{id}/itsm` says `enabled`. Chips: role ("Incident", "Major Incident", "Problem", …), "Kritikalität"/"Auswirkung", "Matrix: <Priorität>" when it differs from the current priority, service incident state badge, flood warning ("Für diesen Service gab es vor 12 Minuten bereits einen Incident: #… — verknüpfen?" with a link action). CI panel: list (number, name, class, Verwendungsstatus, Vorfallstatus), "CI verknüpfen" → `CiPicker` (search, class filter, keyboard navigation, link type select), remove, "In Znuny bearbeiten" in Mode A, unavailable codes as plain messages. ITSM dialogs: "ITSM-Felder" and "Entscheidung" buttons open `TicketDynamicFields` with the screen; after save the ITSM summary and priority refresh.

- [ ] **Steps:** tests (hidden when disabled; chips; picker search + link; remove; Mode A edit link; unavailable message; dialogs use the right screen; existing `TicketDynamicFields` tests unchanged) → run → implement → demo check → lint/test/i18n → commit `feat(itsm): affected CIs, ITSM chips and ITSM field dialogs in the ticket`.

---

### Task 26: Frontend problem + major incident

**Files:**
- Create: `frontend/src/components/itsm/{ProblemPanel,MajorIncidentBanner,MajorIncidentPanel}.tsx` (+ tests)
- Modify: `frontend/src/routes/agent/TicketZoomPage.tsx`, i18n + propagation

Behaviour: problem tickets show "Ursache", "Workaround" (editable with rw), linked incidents with state, "Incidents verknüpfen" (ticket search picker), "Als bekannten Fehler veröffentlichen" (disabled with hint when no KB category is configured; after success links to the KB article and shows its state "Zur Prüfung"), "Verknüpfte Incidents mit Workaround schließen" (confirm with count → job progress → result). Major tickets: "Major Incident ausrufen"/"beenden", summary, "Update an alle Betroffenen" (modes "Nur intern", "Notiz an verknüpfte Tickets", "Antwort an Kunden" — the last only when enabled), timeline. Linked tickets of an active major incident show the banner with a link to it.

- [ ] **Steps:** tests (panels per role; known-error disabled state; close-incidents flow; declare/end; update modes incl. disabled reply; banner) → run → implement → demo check → lint/test/i18n → commit `feat(itsm): problem and major incident in the ticket`.

---

### Task 27: Frontend CI pages, service view, new ticket

**Files:**
- Modify: `frontend/src/routes/agent/itsm/{CiListPage,CiDetailPage}.tsx` (+ tests), `frontend/src/routes/agent/ServiceViewPage.tsx` (+ test), `frontend/src/routes/agent/NewTicketPage.tsx` (+ test), i18n + propagation

Behaviour: CI list with search (debounced, min 3 chars for attribute search hint), class/state filters, paging, empty state; detail with header (number, name, class, states), attribute tree, versions list ("Version ansehen" loads that version; "Aktuelle Version" badge), linked tickets, "In Znuny bearbeiten" in Mode A; the `editActions` slot (C6) renders nothing in this plan. Service view: incident-state badge, owner/description/type/criticality, CIs of the service. New ticket: when the form has impact/criticality fields, a change fetches the suggestion and sets the priority unless the agent touched the priority field; after a service is picked, the recent-incident warning appears.

- [ ] **Steps:** tests → run → implement → demo check → lint/test/i18n → commit `feat(itsm): CMDB pages, service ITSM view and matrix priority in new tickets`.

---

### Task 28: Golden Mode A on `znuny-6.5-itsm` + `znuny-7.3-itsm` (manual, `GOLDEN=1`)

**Files:**
- Create: `tests/golden/test_itsm_mode_a.py`
- Modify: `tests/golden/README-multi-peer.md`

Cases:

```python
def test_detect_profile_on_peer(peer) -> None                    # Tiqora detect == expected profile + package versions
def test_ci_search_and_detail_equal_znuny(peer) -> None           # ConfigItemSearchExtended / ConfigItemGet / VersionGet (perl eval) vs Tiqora adapter, modulo display formatting
def test_versions_equal_znuny(peer) -> None
def test_link_from_tiqora_visible_in_znuny(peer) -> None          # Znuny LinkList(Ticket) shows the CI; Znuny CI zoom lists the ticket
def test_link_from_znuny_visible_in_tiqora(peer) -> None
def test_link_parity_both_directions(peer) -> None                # same link_relation rows + ticket history as Znuny LinkAdd/LinkDelete for ticket<->ticket and ticket<->CI
def test_configitem_history_difference_documented(peer) -> None   # open question 1: Znuny writes LinkAdded, Tiqora not (asserted, so a change is noticed)
def test_priority_matrix_equals_znuny(peer) -> None               # PriorityAllocationGet for all 25 cells
def test_service_incident_state_equals_znuny(peer) -> None        # set CI incident state in Znuny -> ServiceGet(IncidentState) == Tiqora
def test_setup_on_itsm_peer_is_noop_and_on_plain_peer_identical(peer, plain_peer) -> None
def test_znuny_reads_tiqora_seeded_dynamic_fields(plain_peer) -> None   # DynamicFieldGet + YAML Load on the plain peer after Tiqora setup
def test_mode_a_writes_no_itsm_table_on_peer(peer) -> None
```

- [ ] **Step 1:** Write the tests. **Step 2:** Run on `znuny-6.5-itsm`, then `znuny-7.3-itsm`; fix Tiqora differences (record each version difference with its upstream file in the test docstring and in `docs/itsm-import.md`). **Step 3:** Commit: `test(golden): Mode A parity on Znuny 6.5 and 7.3 with ITSM`.

---

### Task 29: E2E (demo mode) + screenshots

**Files:**
- Create: `frontend/e2e/itsm.spec.ts`
- Modify: `frontend/e2e/fixtures/mock-api.ts`, `frontend/e2e/screenshots.spec.ts` (ticket zoom with CIs + major banner, CI detail, admin ITSM page; desktop light)

Flows: (1) incident ticket → link a CI with the picker → chips show service incident state → remove the CI; (2) linked tickets → "Notiz an verknüpfte Tickets" → job finishes → result list; (3) problem → link two incidents → publish known error → close incidents with workaround; (4) declare major incident → banner on a linked ticket → internal update in timeline; (5) admin → setup preview → apply → matrix edit (Mode B mock) and read-only (Mode A mock).

- [ ] **Steps:** write → `pnpm exec playwright test e2e/itsm.spec.ts` → green → delete `.playwright-mcp` → commit `test(e2e): ITSM incident, problem, major incident and CMDB flows`.

---

### Task 30: Docs + full test and lint run

**Files:**
- Create: `docs/itsm.md` (C7 chapters; chapters 1–5 full, 6–7 stubs; plain German-first wording in the UI terms above; a table "Was Znuny-Nutzer in jedem Modus sehen" for Mode A; the recommendation that Mode B is the target, as decided)
- Modify:
  - `docs/itsm-import.md`: review Tasks 2–3 output; add "How to re-run the survey".
  - `docs/compatibility.md`: rows "ITSM ticket types/state/dynamic fields", "Priority matrix (CIP)", "Service ITSM attributes / incident state", "Link delete / link types / note to linked tickets / link graph", "Problem / known error", "Major incident", "Znuny CMDB (read-only, Mode A)", "Znuny ITSM changes (not shown, phase 3)"; remove the three link items from the "missing" list; note D3, D5, D7.
  - `docs/architecture.md`: `tiqora.itsm` package, CI provider contract, Znuny adapter profiles, mode resolution.
  - `docs/parallel-operation.md`: short pointer to `docs/itsm.md` chapter 5/7.
  - Spec `2026-10-08-itsm-cmdb-design.md`: tick phase 0/1 items done here; add this plan's deviations under "Fixed decisions" with a pointer to this plan; replace the "Open (product owner): default ITSM mode" bullet with the 2026-10-12 decision.
- Run everything: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (full), the three lint gates, `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs && pnpm build`, `pnpm exec playwright test` (demo e2e), OpenAPI regeneration check (`git diff --stat packages/api-client/openapi.json` empty after a fresh regen).
- [ ] Commit: `docs(itsm): ITSM core, incident/problem and Mode A`.

---

## Self-review notes (for the executor)

- This plan writes Znuny tables only through: `link_objects`/`unlink_objects` (`link_relation`, `link_object`, ticket history), `ticket_write_service` (articles, states, types, priority, DFs), `history_add`, `deliver_agent_email_reply`, and `znuny_admin_writes` (ticket types, states, dynamic fields — moved, not new). If a task seems to need another Znuny write (e.g. `configitem_history`, `cip_allocate`, a SysConfig link type), stop and report it.
- Adapter SQL lives only in `znuny_adapter/reader.py`/`catalog.py`/`attributes.py`; nothing else queries Znuny ITSM tables. Each statement is checked by the Task 7 guard.
- Before Tasks 8–10, read `docs/itsm-import.md` and the vectors from Tasks 2–3; if the survey contradicts I4 (e.g. 7.x stores attributes elsewhere), adapt `SchemaProfile` and the attribute decoder, keep the row dataclasses (C3), and write the change into this document in the same commit.
- Every bulk change is a job; no request handler in this plan writes to more than one ticket.
- Seed bands: re-grep before using them. ITSM DB tests clean up after themselves like main-DB tests (the strict leak check covers the extra databases).
- The cmdb plan implements Mode B against C1 in parallel. Do not add Mode B code here; do not change C1–C7 without noting it in "Cross-plan contracts" in the same commit.
