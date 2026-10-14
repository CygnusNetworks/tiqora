# Wave 3 — Dashboards Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the dashboards' phase-2 row: report-based widgets (any built-in or saved report as chart/table, SLA gauge, backlog aging, hour×weekday heatmap), the other phase-2 catalogue widgets that have a data source today (AI status/budget, web-chat queue), dashboard-wide filters (queue set + time range) that widgets inherit unless pinned, drill-down from numbers, lists and chart points to an exact ticket list (save as search, add as widget), template update merge ("Neue Version der Vorlage"), and the wallboard: TV mode for logged-in agents and read-only kiosk links with their own revocable token, rotation through several dashboards, styled after the approved `Wallboard` artboard.

**Architecture:** Backend: extends `tiqora.dashboard` — `DashboardFilters` stored in the existing `tiqora_dashboard.filters` column and threaded through `run_batch`/`run_widget` into `ProviderContext.filters` (a widget row's new `pinned` flag opts out); phase-1 ticket providers narrow by an append-only `queue_ids` argument of `TicketService`; new providers `core.report` / `core.sla-gauge` / `core.backlog-aging` / `core.heatmap` (one shared runner over the reporting seam `run_report_ref`, D12 of the wave-2 reporting plan), `core.queue-overview` (moves server-side, adds escalated per queue), `core.chat-queue`, `core.ai-status`; a drill-down endpoint that lists the tickets behind a widget (or one chart point) by the same code path as the widget; template update merge via two new widget columns (`template_widget_id` on copies, `added_in_version` on template widgets); kiosk links (`tiqora_dashboard_kiosk_token` + `_kiosk_item`) managed by admins and read by a public, token-authenticated `/api/v1/kiosk/*` API that runs providers as the link's creator, redacts personal data unless the link allows details, caches per token in Redis and rate-limits misses. Frontend: filter bar + `pinned` switch, report/gauge/heatmap/chat/AI widgets, a drill-down page `/agent/tickets/drill`, the template-update banner, a `WallboardView` (fit-to-screen 12-column grid, header with name/"aktualisiert vor"/clock, rotation) used by TV mode `/agent/wallboard` and the public kiosk page `/wallboard#<token>`, and kiosk-link management (tab menu dialog + admin page).

**Tech Stack:** FastAPI + SQLAlchemy 2 async (MariaDB and PostgreSQL), Alembic, pydantic v2, pytest; React 18, TanStack Query/Router, Tailwind (existing tokens), i18next, Vitest, Playwright (demo mode). No chart, grid or drag library.

**Spec:** `docs/superpowers/specs/2026-10-08-dashboard-widgets-design.md` — "Phases" row 2 ("Report-based widgets, dashboard filters, drill-down, wallboard + kiosk links, template update merge"), "Widget catalogue" row 2, "Beyond parity" items 4, 5, 8, 9, data model `tiqora_dashboard_kiosk_token`, API surface `/wallboard/{token}`, tests section (kiosk permission tests, wallboard rotation, template update merge). Visual contract for the wallboard: the **approved** `Wallboard` artboard (`docs/superpowers/mockups/2026-10-10-dashboards/Wallboard.dc.html`). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (Waves table: wave 3 "Dashboards 2"; dependency "reporting (2) ──> dashboards (3) phase 2+").

**Builds on (all merged into `roadmap-wave1`, head `c843f5ae`):** wave-2 dashboards phase 1 (`docs/superpowers/plans/2026-10-10-wave2-dashboards.md`, Design D1–D10 **as amended by** `.superpowers/sdd/2026-10-10-wave2-dashboards/rulings.md` — every ruling there stays in force unless this plan's "Deviations" lists it as superseded), wave-2 reporting builder (D6 `RunOverrides`/`run_report_ref`, D12 dashboards seam), wave-2 plugin system (P13 widget contract, unchanged), wave-2 web chat (`tiqora.webchat`, SSE `chat_*` events).

---

## Open questions for the product owner

Each item below was decided conservatively so the plan can run without waiting; the matching "Assumption:" in the plan names where it applies. A different answer changes only the named task.

1. **"Team" in dashboard filters = a set of queues** (Znuny has no team object). Assumption: filters are `queue_ids` + `time_range` only (E4, Task 3).
2. **Who changes dashboard filters.** Assumption: filters are saved on the dashboard and only people who may edit it change them (one click in view mode, saved immediately); read-only viewers of shared/locked dashboards see the active filters but cannot override them for themselves (E4, Task 13).
3. **Time-range filter scope.** Assumption: the dashboard time range applies to report-based widgets only; ticket lists, counters, escalations, reminders, queue overview and chat queue always show the current state (E4).
4. **Kiosk links: who may create them.** Assumption: admins only (a kiosk link publishes data with the creator's rights to anyone holding the link); the link dies when its creator is no longer a valid admin (E9, Tasks 10–11, 21).
5. **Kiosk links: personal data.** Assumption: a link hides ticket titles, customer names, agent names, chat visitor names, calendar entries and person dimensions of reports unless "Details anzeigen" is switched on per link (default off). The approved mockup shows titles and names — that is the "Details an" state (E9).
6. **Kiosk when Redis is down.** Assumption: kiosk endpoints fail closed (503; the TV keeps the last picture and retries), like the public web-chat API (E9, Task 11).
7. **Large reports on dashboards.** Assumption: a report too large to run inline is computed as a background job started by the agent's browser (button-free, automatic once per settings change); on a kiosk no job is started — the widget shows a cached result if one exists, otherwise "Dieser Bericht ist für das Wallboard zu umfangreich." (E6, Tasks 7, 14, 20).
8. **Process overview widget** (catalogue row 2, "process analytics") has no data source until process phase 4 (roadmap wave 5). Assumption: deferred to wave 5; not built here (Deviations).
9. **Team status on the wallboard.** The mockup shows "am Telefon", "Pause", "Urlaub", "im Chat". Assumption: only "verfügbar" (online) and "im Chat" (web chat) have a data source today; phone/pause/vacation stay a later item. The mockup's "Ziel 30 min" target line on a KPI tile is not built (no target field on reports) (E7, Deviations).
10. **Chart drill-down scope.** Assumption: a chart point opens a ticket list only for reports on the `tickets` and `open_tickets` sources whose dimensions and report filter all map to ticket-filter fields, and never for person dimensions (customer, customer user, owner, responsible, agent/actor) — this keeps personal values out of URLs (E5).
11. **Template merge scope.** Assumption: merge only **adds** widgets that the template gained after the copy was made; changed or removed template widgets are not propagated, the user's own widgets are never moved, changed or removed (E8).
12. **API keys for dashboards.** Assumption: `/api/v1/dashboards*` and `/api/v1/dashboard-widget-types*` stay outside every API-key area (`_unknown`: sessions and unrestricted keys only, R-DASH-APIKEY); kiosk access is a separate token, not an API key (E9).
13. **Dashboard export/import as JSON** (spec "API surface") is in no phase row. Assumption: still deferred (phase-1 Deviation 6 continues).
14. **Compatibility row.** Assumption: `docs/compatibility.md` "Configurable dashboard widgets" stays **partial** (customer-info widget and RSS are still missing) with the phase-2 features added to its note (Task 22).
15. **New widget visuals.** The approved mockup covers the wallboard but not the report/gauge/heatmap/chat/AI widgets nor the filter bar. Assumption: they follow the phase-1 chrome (D10) and the existing dataviz rules; Task 22 produces light/dark screenshots for review instead of a new mockup round.

---

## Global Constraints

- **Worktree.** Work in the worktree and branch the controller names in the implementer instructions (expected: a new worktree off `roadmap-wave1` at or after `c843f5ae`, e.g. `<worktree>`, branch `wave3-dashboards`). Never touch `<repo>` (user's `main`) or `<worktree>` directly; the controller merges. Never commit to `main`, never push (a push to origin main deploys production), no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit. Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with the `Co-Authored-By` trailer the executing session's attribution reminder prescribes.
- **Code read for this plan** was `<worktree>` (dashboards) and `<worktree>` (web chat, reporting). Symbols named in this plan were grepped there; re-grep before use and stop with NEEDS_CONTEXT if one is missing.
- **Backend tests:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit, all three: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora` (ruff format is what makes CI red). New DB test modules clean up their rows and are never added to `backend/tests/db_leak_baseline.txt`.
- **Both dialects:** every task that touches SQL tests on MariaDB AND PostgreSQL (`@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])`, pattern `backend/tests/test_dashboards_api.py` / `_dashboard_helpers.open_env`). No array/JSON-operator columns; JSON is TEXT / `MEDIUM_TEXT_VARIANT`.
- **Tables:** write only `tiqora_*` tables. Znuny tables are read only, except through existing Znuny write services — **this plan writes no Znuny table**. Never change Znuny DDL.
- **No JOIN between `tiqora_*` and Znuny tables** (prod MariaDB mixes `general_ci`/`unicode_ci` collations → error 1267): load keys, then an IN-list (integer IN-lists on `users.id`, `queue.id`, `roles.id`, `permission_groups.id` are fine). Applies in particular to kiosk creator names, chat-per-agent counts, AI usage per queue and per-queue escalation counts.
- **Sessions:** no `db.begin()` / `session.begin()` after autobegin (the request session has already begun). Providers never commit (the runner commits after `statement_timeout` is lifted); routes may commit.
- **No long synchronous endpoints.** Prod nginx cuts `/api/` at 90 s. Every new endpoint answers well below that: providers keep the runner budgets (batch 5 s default, report providers 8 s, batch deadline 20 s, single widget 25 s); anything larger goes through the wave-0 job system (`enqueue_report_ref`) + polling (`useJob`).
- **Migrations:** Alembic two-chain setup; the new migration lives in `backend/alembic/versions_tiqora` and takes the **next free number at implementation time** (head today: `20261010_0072` after the web chat merge; other wave-3 plans add migrations in parallel — check `ls backend/alembic/versions_tiqora | tail -3` and `cd backend && uv run alembic heads`; the controller re-chains at merge, R-DASH-MIGRATION pattern). Follow skills `alembic-two-chain-new-head-forks-graph` and `alembic-metadata-migration-default-drift` (server defaults must match the ORM exactly). Bump `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version ==` assert in `backend/tests/test_migrate_cli.py`.
- **Seed ids:** band **61200–61599** (provisional — five wave-3 plans are written in parallel; confirm free with `grep -rnwE "61[2-5][0-9]{2}" backend/tests` before first use and pick another free band if taken, recording it in the task report). Sub-bands per task in the task table. Logins/names contain `dash3`. Test helpers live in a new `backend/tests/_dashboard3_helpers.py` (reuse `FakeRedis`, `to_async_url` from `_dashboard_helpers.py` by import; never widen the 497xx band of phase 1).
- **Example data** only `example.org` / `.test` domains and made-up names (`Beispiel GmbH`, `Anna Beispiel`).
- **Timestamps** written into new `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- **Znuny SysConfig/YAML checkbox values** are Perl-truthy strings: read them with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- **OpenAPI:** after any API model/route change regenerate both openapi files and `schema.d.ts`: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build` (skill `tiqora-openapi-regen-or-ci-wipes-schema`); check `head -c 40 packages/api-client/openapi.json` starts `{"components"`; never hand-edit `schema.d.ts`. A Pydantic response field WITH a default becomes REQUIRED in the generated TS type — fixtures, the typed factories `demoTab`/`demoDashboard`/`demoTemplate` (R-DASH-TS-FIXTURES), demo mocks and `frontend/e2e/fixtures/mock-api.ts` carry it in the same commit (`cd frontend && pnpm lint` finds them).
- **Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc). Never run prettier/biome on `frontend/`. New i18n keys go into `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). **Frontend tasks that add i18n keys run sequentially, never in parallel** (R-I18NSEQ). New screens get demo-mode mocks (`VITE_DEMO=1`) and are checked in demo mode with Playwright at 1280 px and 390 px (wallboard additionally 1920×1080), light + dark (delete `.playwright-mcp` before committing). Colours only via existing tokens / `themeColor()` — **this plan adds no colour token**. API wrappers are hand-written in `frontend/src/lib/dashboardsApi.ts` / new `frontend/src/lib/kioskApi.ts` with `Schemas["…"]` types.
- **UI copy** German first, plain words, the user is addressed with **"Sie/Ihr"** (R-GERMAN-SIE). No system jargon in UI text (never "Provider", "Token-Hash", "Kiosk-Token", "type_key", "Breakpoint"; the user-facing word for a kiosk link is "Wallboard-Link").
- **Widget contract:** P13 (`frontend/src/plugins/dashboardWidgets.ts`) is consumed unchanged; core widgets are registered with `defineCoreWidget` (R-DASH-WIDGET-CONTRACT). Data reaches components only through `useWidgetData()`; filters and kiosk state reach them through host contexts, never new `WidgetProps` fields.
- **Znuny reference source** (6.5 core) at `<repo>/znuny-6.5.22/` (read-only).

## Review Focus

1. **A dashboard filter never widens what anyone sees.** Filters are ANDed after the viewer's (or kiosk creator's) queue scope; a queue id the viewer cannot read yields 0 rows/count 0, never another user's numbers; a shared dashboard whose owner filtered to queue Q shows a viewer without `ro` on Q nothing from Q. Pinned in Task 4 (`test_filter_queue_outside_scope_yields_nothing`, `test_shared_dashboard_filter_runs_in_viewer_scope`) and Task 6 (`test_report_widget_common_filter_only_narrows`).
2. **Kiosk token surfaces.** The token never appears in a server-side URL (fragment + header only), is stored as SHA-256, and unknown / expired / revoked / creator-invalid / creator-no-longer-admin all answer the **same** 404 body; a revocation takes effect on the next poll (cache keyed per token and checked before the cache); misses are rate-limited per IP hash; personal fields are dropped by a **whitelist** unless the link allows details; plugin/unknown widgets never run on a kiosk. Pinned in Task 10 (`test_token_stored_hashed_and_shown_once`, `test_redaction_whitelist_drops_unknown_fields`) and Task 11 (`test_invalid_states_are_indistinguishable`, `test_revoked_link_stops_on_next_poll_despite_cache`, `test_miss_rate_limit_per_ip_hash`).
3. **Report widgets never stall or 500 the page.** A report beyond the inline estimate answers `deferred` (never runs a job inside a provider), a report that runs out of budget is `pending`, a deleted (410) / invisible (404) report and missing report rights become an error/forbidden tile with a German message, a shared dashboard's report runs in the **viewer's** report visibility. Pinned in Task 6 (`test_report_widget_deferred_never_enqueues`, `test_report_widget_404_410_403_become_tiles`, `test_report_widget_runs_as_viewer`) and Task 7 (`test_report_job_result_is_served_from_cache`).
4. **Template merge never touches the user's own work.** Merge only appends template widgets added after the copy; existing widgets keep ids, settings, layout and order; a stale version is 409; a deleted or no-longer-applicable template offers nothing; dismiss is remembered. Pinned in Task 8 (`test_merge_appends_only_new_template_widgets`, `test_merge_never_moves_or_changes_user_widgets`, `test_dismiss_hides_until_next_template_change`).
5. **Drill-down is exact and re-scoped.** The drill list for a counter/list/escalation widget returns exactly the widget's tickets (same code path, viewer scope, dashboard filters, `pinned` honoured); a chart point's list is built server-side from stored widget settings — the URL carries only ids and a dimension value, never a filter; "Als Suche speichern" is offered only when an exact filter exists (preset parity proven per preset). Pinned in Task 9 (`test_drill_total_equals_counter_value`, `test_drill_point_is_rebuilt_server_side`, `test_preset_filter_parity_per_preset`).

Also pinned (not top five): `filters` on a stale version is 409 and leaves widgets untouched (Task 3); counter history of a filtered counter never mixes with the unfiltered one (Task 4); a kiosk with several dashboards skips one the creator can no longer see (Task 11); TV mode keeps rotating when one dashboard's data fails (Task 19); the kiosk page never redirects to `/login` and never sends cookies (Task 20).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Names from phase 1 are used exactly as they exist in code: `DashboardProvider`, `ProviderContext`, `ProviderForbidden`, `DASHBOARD_PROVIDERS`, `core_providers` (`tiqora.dashboard.providers`); `run_batch`, `run_widget`, `WidgetRow`, `BATCH_DEADLINE_SECONDS`, `SINGLE_WIDGET_TIMEOUT_SECONDS` (`tiqora.dashboard.runner`); `WidgetIn`, `WidgetOut`, `DashboardOut`, `DashboardSaveIn`, `DashboardTabOut`, `WidgetDataOut`, `DashboardDataOut`, `WidgetPreviewIn`, `ZnunyIdIn`, `DashboardIdIn` (`tiqora.dashboard.schemas`); `CORE_WIDGET_SETTINGS`, `validate_widget_settings`, `SettingsError`, `TicketSource`, `PresetSource` (`tiqora.dashboard.settings_models`); `normalize_rects`, `Rect`, `GRID_COLS` (`tiqora.dashboard.layout`); `TICKET_PRESETS`, `preset_kwargs` (`providers/presets.py`); `resolve_source`, `compile_for_viewer` (`providers/sources.py`); `record_and_history` (`tiqora.dashboard.samples`); store functions `get_dashboard`, `save_dashboard`, `list_visible`, `user_role_ids`, `validate_source_settings`, `_retry_races` (`tiqora.dashboard.store`); `ensure_start` (`tiqora.dashboard.start`); `TicketService.list_tickets` / `count_filtered` / `page_from_stmt` (`tiqora.domain.ticket_service`); `QueueService.list_queues` (`tiqora.domain.queue_service`); `compile_ticket_filter`, `ticket_query_scope`, `count_tickets` (`tiqora.filters.tickets`); `Filter`, `Condition`, `AllOf`, `FilterError` (`tiqora.filters`); reporting: `run_report_ref`, `RunOverrides`, `RunRefOutcome`, `RequestError`, `enqueue_report_ref`, `TooManyReportJobs`, `COMMON_FILTER_KEYS` (`tiqora.reporting.run_service`), `QueryBudget` (`reporting.engine`), `ReportCache`, `read_cache_generation` (`reporting.cache`), `load_reporting_settings` (`reporting.settings`), `has_report_access` (`reporting.access`), `ReportQueryTimeout` (`reporting.sql`), `TimeRange` (`reporting.definition`), `BUILTIN_REPORTS` (`reporting.catalogue`); web chat: `list_inbox` (`tiqora.webchat.agent`), `TiqoraChatSession` (`tiqora.webchat.models`), `ChatRateLimiter`, `RateLimited`, `ChatBackendUnavailable` (`tiqora.webchat.ratelimit`), `hash_ip` (`tiqora.webchat.tokens`); `client_ip` (`tiqora.security.ratelimit`); AI: `collect_limits`, `LimitStatus` (`tiqora.ai.limits`), `TiqoraAiTicketState` (`tiqora.ai.models`). Frontend: `useDashboardData`, `useWidgetData`, `WidgetDataProvider` (`dashboard/data.tsx`), `DashboardGrid`, `WidgetFrame`, `WidgetChromeProvider`, `isInteractive`/`rendersLinks`/`linkTabIndex` (`dashboard/chrome/mode.ts`), `defineCoreWidget`, `registerCoreWidgets`, `galleryGroupOf`, `canUseWidget` (`dashboard/definitions.ts`), `firstFreeSpot` (`dashboard/layout/grid.ts`), `ReportView`, `ReportParameterControls`, `useReportRun` (`components/reports/*`), `HeatmapChart`, `BarChart`, `StackedBarChart`, `DonutChart`, `KpiTile` (`components/agent/stats/*`), `useJob` (`components/jobs/useJob.ts`), `TicketTable` (`components/agent/TicketTable.tsx`), `savedSearchesApi`, `reportsApi`, `subscribeSSE`.

### E1. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_dashboards_phase2.py` | E2 |
| modify `backend/src/tiqora/db/tiqora/models.py` | widget columns, `TiqoraDashboardKioskToken`, `TiqoraDashboardKioskItem` |
| modify `backend/src/tiqora/dashboard/schemas.py` | E3 models |
| `backend/src/tiqora/dashboard/filters.py` | pure: `DashboardFilters`, `TimeRangePreset`, `report_time_range`, `filters_key`, `effective_filters` |
| modify `backend/src/tiqora/dashboard/settings_models.py` | settings of the new widgets (E6, E7) |
| modify `backend/src/tiqora/dashboard/layout.py` | pure `first_free_spot` (mirror of frontend `firstFreeSpot`) |
| modify `backend/src/tiqora/dashboard/store.py` | filters, `pinned`, template links, `set_filters` |
| `backend/src/tiqora/dashboard/template_update.py` | E8 preview / merge / dismiss |
| modify `backend/src/tiqora/dashboard/start.py` | copies set `template_widget_id` |
| modify `backend/src/tiqora/dashboard/runner.py` | filters, `pinned`, context services, kiosk flag |
| modify `backend/src/tiqora/dashboard/providers/__init__.py` | `ProviderContext` fields (append-only), new providers in `core_providers()` |
| modify `backend/src/tiqora/dashboard/providers/{counter,ticket_list,escalations,reminders}.py` | honour `queue_ids` |
| `backend/src/tiqora/dashboard/providers/queue_overview.py` | `core.queue-overview` |
| modify `backend/src/tiqora/dashboard/providers/team_presence.py` | `in_chat` status |
| `backend/src/tiqora/dashboard/providers/chat_queue.py` | `core.chat-queue` |
| `backend/src/tiqora/dashboard/providers/ai_status.py` | `core.ai-status` |
| `backend/src/tiqora/dashboard/providers/report.py` | shared report runner + `core.report`, `core.sla-gauge`, `core.backlog-aging`, `core.heatmap` |
| `backend/src/tiqora/dashboard/drill.py` | E5: widget → ticket page, preset → `Filter`, report point → `Filter` |
| `backend/src/tiqora/dashboard/kiosk.py` | E9: token create/hash/resolve, store, redaction, data cache |
| modify `backend/src/tiqora/api/v1/dashboards.py` | routes of E3 |
| `backend/src/tiqora/api/v1/kiosk.py` | public `/kiosk/*` routes |
| `backend/src/tiqora/api/v1/admin/dashboard_kiosk_links.py` | admin `/admin/dashboard-kiosk-links*` |
| modify `backend/src/tiqora/domain/ticket_service.py` | append-only `queue_ids` kwarg, `count_by_queue` |
| modify `backend/src/tiqora/reporting/run_service.py` | append-only `RunRefOutcome.definition` |
| modify `backend/src/tiqora/api/v1/__init__.py`, `backend/src/tiqora/api/v1/admin/__init__.py` | router registration (append after the last `include_router`) |

Frontend (new unless marked):

| Path | Responsibility |
|---|---|
| modify `frontend/src/lib/dashboardsApi.ts` | new routes, types |
| `frontend/src/lib/kioskApi.ts` | cookie-less kiosk client |
| `frontend/src/dashboard/filters/{FilterBar.tsx,filtersContext.tsx,timeRanges.ts}` | E4 UI |
| `frontend/src/dashboard/widgets/{ReportWidget,SlaGaugeWidget,BacklogAgingWidget,HeatmapWidget,ChatQueueWidget,AiStatusWidget}.tsx` | new widgets |
| `frontend/src/components/agent/stats/GaugeChart.tsx` | semicircle gauge |
| `frontend/src/dashboard/settings/editors/ReportSettings.tsx` | report picker + parameters |
| `frontend/src/dashboard/drill/{drillLinks.ts,DrillPage.tsx}` + modify chart components (`onSelect`, additive) | E5 UI |
| `frontend/src/dashboard/template/TemplateUpdateBanner.tsx` | E8 UI |
| `frontend/src/dashboard/wallboard/{WallboardView.tsx,useRotation.ts,fit.ts,WallboardHeader.tsx,kioskContext.tsx}` | E10 |
| `frontend/src/routes/agent/WallboardPage.tsx` | TV mode `/agent/wallboard` |
| `frontend/src/routes/KioskWallboardPage.tsx` | public `/wallboard` |
| `frontend/src/dashboard/kiosk/{KioskLinksDialog.tsx,KioskLinkForm.tsx}` | link management |
| `frontend/src/routes/admin/DashboardKioskLinksPage.tsx` | admin overview |
| modify `frontend/src/dashboard/widgets/{index.ts,CounterWidget,TicketListWidget,EscalationsWidget,QueueOverviewWidget,TeamPresenceWidget,RemindersWidget}.tsx`, `dashboard/definitions.ts`, `dashboard/data.tsx`, `dashboard/chrome/mode.ts`, `dashboard/settings/SettingsDrawer.tsx`, `dashboard/tabs/TabMenu.tsx`, `routes/agent/DashboardPage.tsx`, `routes/agent/SavedSearchEditorPage.tsx` (initial criteria from router state), `router.tsx`, `lib/adminSearch.ts`, `demo/dashboardsMock.ts`, `demo/handlers.ts`, `e2e/fixtures/mock-api.ts` | wiring |

### E2. Tables (one migration, Task 1)

```
tiqora_dashboard_widget  (ALTER, three new columns)
  pinned              BOOL NOT NULL server_default false   -- true: this widget ignores the dashboard filters
  template_widget_id  VARCHAR(36) NULL                     -- on a copy: id of the template widget it came from (no FK)
  added_in_version    INT NULL                             -- on a template widget: template version that first contained it; NULL = existed before phase 2

tiqora_dashboard_kiosk_token
  id              BIGINT PK autoincrement
  token_hash      VARCHAR(64) NOT NULL                     -- sha256 hex of the secret
  token_hint      VARCHAR(8) NOT NULL                      -- last 4 characters, shown in lists ("…k3Qz")
  name            VARCHAR(120) NOT NULL
  created_by      INT NOT NULL                             -- Znuny users.id; data runs with this user's rights
  rotate_seconds  INT NOT NULL server_default 60           -- 15..900
  show_details    BOOL NOT NULL server_default false
  expires_at      DATETIME NOT NULL
  revoked_at      DATETIME NULL
  last_used_at    DATETIME NULL
  created_at      DATETIME NOT NULL
  UNIQUE uq_tiqora_dashboard_kiosk_token_hash (token_hash)
  INDEX ix_tiqora_dashboard_kiosk_token_creator (created_by)

tiqora_dashboard_kiosk_item                                -- dashboards of a link, in rotation order
  token_id      BIGINT NOT NULL FK fk_tiqora_dashboard_kiosk_item_token -> tiqora_dashboard_kiosk_token.id ON DELETE CASCADE
  dashboard_id  BIGINT NOT NULL FK fk_tiqora_dashboard_kiosk_item_dashboard -> tiqora_dashboard.id ON DELETE CASCADE
  position      INT NOT NULL server_default 0
  PRIMARY KEY (token_id, dashboard_id)  INDEX ix_tiqora_dashboard_kiosk_item_dashboard (dashboard_id)
```

`tiqora_dashboard.filters` (TEXT, `'{}'`) exists since phase 1 and is used as is. Booleans `server_default=false()`; junction FK columns `autoincrement=False`. Revocation (`POST …/revoke`, idempotent) sets `revoked_at` (row kept for the admin list); admins can delete a revoked or expired link (row + items) with `DELETE`. No row is seeded.

### E3. API models and routes

```python
# backend/src/tiqora/dashboard/filters.py  (pure)
TimeRangePreset = Literal["today", "7d", "30d", "90d", "this_month", "last_month"]
class DashboardFilters(BaseModel):                        # extra="forbid"
QueueIdIn = Annotated[int, Field(ge=1, le=2**31 - 1)]    # own alias: filters.py must not import schemas.py (schemas.py imports DashboardFilters)
    queue_ids: list[QueueIdIn] = Field(default_factory=list, max_length=50)   # empty = no queue narrowing; deduplicated + sorted by a validator
    time_range: TimeRangePreset | None = None             # None = each report widget's own range
    def is_empty(self) -> bool
def report_time_range(preset: TimeRangePreset) -> TimeRange   # mapping table, pinned per value by a test (verify `TimeRange` semantics of include_current)
def filters_key(f: DashboardFilters) -> str | None            # "q:<sha1(sorted ids)[:8]>" or None when no queue filter (counter history key suffix)
def effective_filters(f: DashboardFilters, *, pinned: bool) -> DashboardFilters   # pinned -> empty

# backend/src/tiqora/dashboard/schemas.py  (additions)
class WidgetIn(BaseModel):  ... + pinned: bool = False             # WidgetOut inherits
class DashboardOut(DashboardTabOut):  ... + filters: DashboardFilters = Field(default_factory=DashboardFilters) + template_update: TemplateUpdateHint | None = None   # hint filled only by GET /dashboards/{id}
class TemplateUpdateHint(BaseModel):  template_id: int; template_name: str; template_version: int; new_widget_count: int
class DashboardSaveIn(BaseModel):  ... + filters: DashboardFilters | None = None   # None = keep the stored filters
class DashboardFiltersIn(BaseModel):  filters: DashboardFilters; version: int = Field(ge=1)   # extra="forbid"
class WidgetPreviewIn(BaseModel):  settings: ...; + filters: DashboardFilters | None = None    # the open dashboard's filters (they only narrow)
class TemplateUpdateOut(BaseModel):  template_id: int; template_name: str; template_version: int; widgets: list[WidgetOut]  # the widgets a merge would add, placed
class TemplateUpdateApplyIn(BaseModel):  action: Literal["merge", "dismiss"]; version: int = Field(ge=1)   # extra="forbid"
class DrillPageOut(BaseModel):
    title: str                                    # German, e.g. "Meine offenen Tickets" / "Bericht „Volumen“ · Queue Support"
    items: list[TicketListItem]; total: int
    filter: dict[str, Any] | None                 # exact wave-0 Filter for "Als Suche speichern"; None when not expressible
    note: str | None                              # e.g. "Der Bericht kann einige Minuten älter sein als diese Liste."
class KioskLinkIn(BaseModel):                     # admin; extra="forbid"
    name: _Name; dashboard_ids: list[DashboardIdIn] = Field(min_length=1, max_length=10)
    rotate_seconds: int = Field(60, ge=15, le=900); show_details: bool = False
    expires_in_days: int = Field(90, ge=1, le=365)
class KioskLinkOut(BaseModel):
    id: int; name: str; dashboards: list[KioskDashboardRef]   # {id, name, visible: bool}
    rotate_seconds: int; show_details: bool; token_hint: str
    created_by: int; created_by_name: str | None
    created_at: UtcDateTime; expires_at: UtcDateTime; revoked_at: UtcDateTime | None; last_used_at: UtcDateTime | None
    active: bool                                   # not revoked and not expired
class KioskLinkCreatedOut(KioskLinkOut):  token: str      # shown exactly once
class KioskBoardOut(BaseModel):
    name: str; rotate_seconds: int; refresh_seconds: int; show_details: bool
    dashboards: list[KioskDashboardOut]            # {id, name, filters, widgets: list[WidgetOut]} in rotation order
    server_time: UtcDateTime
```

Routes (`api/v1/dashboards.py` unless marked; static segments before `/{dashboard_id}` as in phase 1):

```
PATCH  /api/v1/dashboards/{id}/filters                         -> DashboardOut     (owner / admin-on-template; 409 stale; 403 read-only)
GET    /api/v1/dashboards/{id}/template-update                 -> TemplateUpdateOut (404 when nothing to merge)
POST   /api/v1/dashboards/{id}/template-update                 -> DashboardOut     (body TemplateUpdateApplyIn; 409 stale; 422 > 40 widgets)
GET    /api/v1/dashboards/{id}/widgets/{wid}/tickets?offset&limit&sort&order&point   -> DrillPageOut  (limit ≤ 50; 422 "Für dieses Widget gibt es keine Ticketliste.")
POST   /api/v1/dashboards/{id}/widgets/{wid}/report-job        -> 202 JobAccepted  (report widgets only; 429 job cap)
GET    /api/v1/admin/dashboard-kiosk-links                     -> list[KioskLinkOut]           (api/v1/admin/dashboard_kiosk_links.py, AdminUser)
POST   /api/v1/admin/dashboard-kiosk-links                     -> 201 KioskLinkCreatedOut
POST   /api/v1/admin/dashboard-kiosk-links/{id}/revoke         -> KioskLinkOut  (idempotent: sets revoked_at once)
DELETE /api/v1/admin/dashboard-kiosk-links/{id}                -> 204  (only a revoked or expired link; an active one -> 422 "Bitte den Link zuerst widerrufen.")
GET    /api/v1/kiosk/board                                     -> KioskBoardOut                (api/v1/kiosk.py; header X-Tiqora-Kiosk)
GET    /api/v1/kiosk/dashboards/{id}/data                      -> DashboardDataOut             (header X-Tiqora-Kiosk)
```

`point` is a JSON object `{"<dimension key>": <value>}` with at most 3 keys, ≤ 500 bytes (TanStack parses search values as JSON — skill `tanstack-router-search-params-json-parsed`; the API reads the raw string and `json.loads` it with a size check). Error shapes are the phase-1 ones (422 `{"detail": {"message", "path"}}`, 409 `{"detail": {"message", "current_version"}}`).

### E4. Dashboard filters

- **Storage:** `tiqora_dashboard.filters` holds `DashboardFilters.model_dump_json()`; a row with `'{}'` or unparsable JSON reads as empty filters (logged once, never a 500). `DashboardOut.filters` always present.
- **Changing:** `PATCH /dashboards/{id}/filters` with the read `version` → stored, `version + 1` (same optimistic lock and edit rights as `PUT`; a locked template's viewers get 403). `PUT /dashboards/{id}` keeps the stored filters when `filters` is `None`. Queue ids are not checked against the editor's rights (filters only narrow, and a viewer with more rights must still see the filtered set) — but they are bounded integers (`QueueIdIn`) and at most 50.
- **Applying:** the routes pass the dashboard's filters to `run_batch`/`run_widget`; per widget the runner uses `effective_filters(filters, pinned=widget.pinned)` and puts `queue_ids` and `time_range` into `ProviderContext.filters` (`{"queue_ids": [...], "time_range": "30d" | None}`).
  - `core.ticket-list`, `core.counter`, `core.escalations`, `core.reminders`: preset path → `TicketService` with the new `queue_ids` kwarg; saved-search/own-filter path → the compiled statement gets an extra `Ticket.queue_id.in_(queue_ids)` (`queue_ids` is always ANDed after `ticket_query_scope`).
  - `core.queue-overview`: shows only queues in the set.
  - `core.ai-status`: queue budgets and counts restricted to the set.
  - Report widgets: `RunOverrides.common = {"queue": queue_ids}` and, when `time_range` is set, `RunOverrides.time_range = report_time_range(...)` (the widget's own time range is used when the dashboard has none). `outcome.ignored_common` is returned as `ignored_filters` and shown as a footnote ("Der Queue-Filter gilt für diesen Bericht nicht.").
  - `core.chat-queue`, `core.team-presence`, `core.calendar-agenda`, frontend-only widgets: ignore filters (Assumption; the chat inbox has no queue column to narrow by).
- **Counter history:** a counter's `counter_key` gets the suffix `|<filters_key>` when a queue filter is effective, so filtered and unfiltered values never share a history row (`counter_key` stays ≤ 120 chars).
- **Preview:** `WidgetPreviewIn.filters` lets the settings drawer show the widget as it will look on the open dashboard.
- **Frontend:** a filter bar under the tab row, view mode only (in edit mode it is disabled with the hint "Filter im Ansichtsmodus ändern" — a PATCH would invalidate the open edit session's version). Chips: "Queues: Alle" / "Queues: Support, 2nd Level" (popover: queue tree with checkboxes, "Übernehmen", "Zurücksetzen") and "Zeitraum: wie im Bericht" / "Letzte 30 Tage". Shown when `can_edit` or when filters are active (read-only chips with the tooltip "Nur wer das Dashboard bearbeiten darf, ändert die Filter."). A pinned widget shows a small pin glyph in its title row while filters are active (`aria-label="Dashboard-Filter gelten hier nicht"`). The settings drawer has the switch "Dashboard-Filter auf dieses Widget anwenden" (on = not pinned) for filter-aware types (`FILTER_AWARE_KEYS` in `definitions.ts`).

### E5. Drill-down

- **Principle:** the list behind a widget is computed **by the same code path as the widget**, so a number and its list agree by construction. The browser never sends a filter; it sends the widget id and, for charts, one dimension value; the server rebuilds everything from stored settings, the viewer's scope and the dashboard filters.
- `drill.widget_tickets(session, viewer, widget, filters, *, offset, limit, sort, order, point) -> DrillPageOut`:
  - `core.counter`, `core.ticket-list`: the provider's source (preset kwargs + `queue_ids`, or the resolved saved search / own filter) as a page (`list_tickets` / `page_from_stmt`), `limit ≤ 50`, `total` = the counter's value.
  - `core.escalations`, `core.reminders`: the provider's query with paging (reminders: the provider's due-window filter applied before paging — verify the provider's post-filter and move it into the query or page over the filtered list; `total` must equal the widget's total).
  - Report widgets (`core.report`, `core.backlog-aging`, `core.heatmap`, `core.sla-gauge`): only when the report's source is `tickets` or `open_tickets` and every part maps to a ticket-filter field via `REPORT_DIMENSION_TO_TICKET_FIELD` (queue→`queue_id`, state/state_type, priority→`priority_id`, type→`type_id`, service→`service_id`, sla→`sla_id`, channel; a `created` time bucket → `created` between bucket bounds in the report's zone). The report's own filter, its `queues` parameter and the dashboard queue filter are mapped the same way; anything unmappable (person dimensions — Assumption, Open question 10 —, measures-only filters, other sources, `time_field` other than `created`) → 422 "Für diesen Wert gibt es keine Ticketliste." and the chart point is not clickable (the provider's data carries `drillable_dimensions`). The list is compiled with `compile_ticket_filter` in the viewer's scope; `note` = "Der Bericht kann einige Minuten älter sein als diese Liste." (facts lag).
  - other widget types → 422.
- **Exact filter for saving:** `preset_filter(preset, uid) -> Filter | None` in `providers/presets.py`, derived from what `TicketService._filtered_ticket_stmt` does for each preset's kwargs (read it; `state_type="open"` there may mean a set of state types). Task 9 proves parity per preset on both dialects (same ticket id set as `list_tickets`); a preset that cannot be expressed exactly returns `None` (no "Als Suche speichern" for it — the drill list itself is still exact). Saved search → its criteria; own filter → its filter; both ANDed with the effective queue filter.
- **Frontend:** `/agent/tickets/drill?d=<dashboard id>&w=<widget id>&point=<json>&sort&order&offset` (`DrillPage`): title, `TicketTable` with paging, buttons "Als Suche speichern" (when `filter` is set; navigates to `/agent/searches/new` passing the filter in **router state**, not the URL — `SavedSearchEditorPage` reads `location.state.criteria` as initial value) and "Als Widget hinzufügen" (when the viewer can edit the dashboard and `filter` is set: appends a `core.ticket-list` widget with `{"source": {"kind": "filter", "filter": …}}` at `firstFreeSpot` via `GET` + `PUT /dashboards/{id}`; 409 → "Das Dashboard wurde inzwischen geändert. Bitte erneut versuchen."). Entry points: counter value (whole tile is a link in view/wallboard-TV mode), ticket-list footer "Alle N anzeigen" (now always a link), escalations/reminders title link, chart bars/segments/cells/slices with `drillable_dimensions` (chart components get an additive optional `onSelect(point)` prop; keyboard: points are buttons only when `onSelect` is set). Kiosk: no links.

### E6. Report widgets

- **Settings** (`settings_models.py`, all `_Strict`):

  ```python
  REPORT_KEY_RE = r"^(builtin\.[a-z0-9_]{1,60}|[1-9][0-9]{0,18})$"
  class ReportWidgetSettings(_Strict):            # core.report
      report_key: str = Field(pattern=REPORT_KEY_RE)          # required (gallery adds it with the settings drawer open)
      time_range: TimeRangePreset | None = None               # None = the report's own range
      parameters: dict[Annotated[str, Field(max_length=40)], ParamValue] = Field(default_factory=dict, max_length=20)
      dimension_choices: dict[Annotated[str, Field(max_length=3)], str] = Field(default_factory=dict, max_length=5)
      display: Literal["auto", "table"] = "auto"              # auto = the report's visualisation
      max_rows: int = Field(10, ge=1, le=50)
  class SlaGaugeSettings(_Strict):                # core.sla-gauge
      time_range: TimeRangePreset = "30d"; target_percent: int = Field(90, ge=50, le=100)
  class BacklogAgingSettings(_Strict):            # core.backlog-aging
      max_rows: int = Field(8, ge=1, le=15)
  class HeatmapSettings(_Strict):                 # core.heatmap
      time_range: Literal["30d", "90d"] = "90d"
  ```

  `ParamValue` reuses the reporting builder's parameter value type (grep `ParameterValue` in `reporting/schemas.py`). Save validation (`store.validate_source_settings`, second pass): `core.report` resolves `report_key` for the **saver** (`resolve_report_ref` with the saver's admin flag) → unknown/invisible → 422 "Diesen Bericht gibt es nicht oder er ist für Sie nicht freigegeben." (path `widgets[i].settings.report_key`), deleted → 422 "Der Bericht „…“ wurde gelöscht.".
- **Providers** (`providers/report.py`): one `run_report_widget(ctx, *, report_key | definition, overrides, max_rows, display)`; four registrations, `version=1`, `timeout_seconds=8.0`:
  - `core.report`: `report_key` from settings.
  - `core.sla-gauge`: inline `SLA_GAUGE_DEFINITION` (source `tickets`, no dimensions, measures `sla_met_rate`, `tickets`; validated by a test with `validate_definition`), `time_range` from settings (dashboard range wins unless pinned). Data adds `{"rate": float | None, "tickets": int, "target": int}` (check the unit of `sla_met_rate` — ratio or percent — and normalise to percent).
  - `core.backlog-aging`: `builtin.backlog_aging`, rows capped at `max_rows`.
  - `core.heatmap`: `builtin.created_heatmap` with `time_range` override.
- **Run:** `has_report_access(session, viewer, reporting)` first (False → `ProviderForbidden("Für Berichte fehlt Ihnen die Berechtigung.")`; never let `require_report_access`' `HTTPException` escape). `reporting = await load_reporting_settings(session, ctx.app_settings)`; cache: `ReportCache(ctx.report_cache_backend, reporting.cache_seconds, generation=await read_cache_generation(backend))` read **before** the first report query; `budget = QueryBudget(reporting.query_timeout_seconds, deadline=clock() + max(1, provider budget − 1.5))` (verify the dataclass field order). `run_report_ref(..., force_job=False)`:
  - `outcome.result` → `{"report": {key, kind, name | title_key}, "definition": …, "result": result rows capped to `max_rows` (pivot/heatmap: all rows up to 200, `truncated` set), "ignored_filters": [...], "drillable_dimensions": [...], "deferred": false}`.
  - `outcome.deferred` → `{"report": …, "deferred": true, "reason": outcome.deferred}` with status `ok`. **The provider never enqueues** (`enqueue_report_ref` commits; providers must not).
  - `RequestError` 404 → `ProviderForbidden("Diesen Bericht gibt es nicht oder er ist für Sie nicht freigegeben.")`; 410 → error tile with the message; `DefinitionError`/`FilterError` → error "Der Bericht kann nicht ausgeführt werden: <message>"; `ReportQueryTimeout` → `pending` (already mapped by the runner).
  - Append-only change in reporting: `RunRefOutcome.definition: ReportDefinition | None = None` set from `prepared.definition` (needed by `ReportView`; no second resolve).
- **Background job:** `POST /dashboards/{id}/widgets/{wid}/report-job` (request session, may commit): rebuilds the same `ReportRunIn` (report key, effective time range, parameters, dimension choices) plus the effective narrowing via `prepare_ref` → `enqueue_report_ref(..., extra_filter=outcome.extra_filter)`. Task 7 proves the job stores its result under the key the provider reads (next batch: `status ok`, `result.cached = true`). Frontend: a `deferred` report widget calls the endpoint once per `settingsHash + filters`, polls `useJob`, refetches the widget on success, shows "Wird im Hintergrund berechnet …" meanwhile; 429 → "Es laufen bereits mehrere Berichte im Hintergrund." with "Erneut versuchen".
- **Kiosk:** no job is ever started; `deferred` renders "Dieser Bericht ist für das Wallboard zu umfangreich." (Assumption, Open question 7).
- **Gallery:** new group **"Berichte"** (`galleryGroupOf`: category `reports` → `reports`); all four report widgets have `permission: "stats"` (`canUseWidget` → `can_view_reports`).

### E7. Other providers

| key | data | notes |
|---|---|---|
| `core.queue-overview` | `{"queues": [{"id", "name", "open", "new", "escalated"}], "total": int}` | `QueueService.list_queues(viewer)` counts + `TicketService.count_by_queue(viewer, state_type="open", escalating_within=0, queue_ids=…)` (one grouped `COUNT … GROUP BY queue_id` over `_filtered_ticket_stmt`); busiest first, `only_with_work`, `limit`; the frontend widget switches from the cached `["queues"]` query to `useWidgetData` (needed on the kiosk). |
| `core.team-presence` (changed) | items get `"status": "available" \| "in_chat"` | `in_chat` = the agent has an `active` `TiqoraChatSession` (`assigned_user_id IN (online ids)`, integer IN-list). |
| `core.chat-queue` | `{"enabled": bool, "waiting": int, "oldest_waiting_since": iso \| null, "mine": int, "bot": int, "items": [{"session_id","ticket_id","tn","title","topic_label","waiting_since"}]}` (`bot` added at the web chat 2 merge, ruling M5: count of `ChatInboxOut.bot`, never items) | reuses `list_inbox(session, user_id=viewer)` (already scoped to `rw` queues); `enabled=false` when no valid chat configuration exists (verify how the web chat module tells); `visitor_name` is never included; `refreshOn: chat_waiting, chat_assigned, chat_ended, interval:30`. Settings `ChatQueueSettings(limit: 1..10 = 5)`. |
| `core.ai-status` | `{"ai_escalated": int, "runs_today": {"<feature>": int}, "budgets": [{"kind","subject_name","window","used","limit","exhausted","resets_at","currency"}]}` | `ai_escalated` = preset count; `runs_today` = AI usage rows since 00:00 UTC grouped by feature for the viewer's `ro` queue ids (integer IN-list; verify the usage table name/columns in `tiqora.ai.usage`); budgets = `collect_limits` filtered to queue budgets of the viewer's `ro` queues, provider cost budgets only when the viewer is admin. Settings `AiStatusSettings()` (no fields). Category `ai`, gallery group "Team". |

### E8. Template update merge

- **Recording:** `save_dashboard` on a `kind == "template"` row sets `added_in_version = <version after this save>` on widget rows whose id is new; existing rows keep theirs. `ensure_start` (template copy) and `create_dashboard(copy_from=<template id>)` set `template_widget_id` on every copied widget to the template widget's id, plus `source_template_id/version` (phase 1 already sets them in `ensure_start`; verify `copy_from` and set them there too when the source is a template). Copies of personal dashboards carry no template link.
- **Pending update** for dashboard C (`kind == "personal"`, `source_template_id = T`): template T exists, `kind == "template"`, not locked, its roles intersect the viewer's valid roles (`user_role_ids`), `T.version > C.source_template_version`, and `new = [w in T.widgets if (w.added_in_version or 0) > C.source_template_version and w.id not in {c.template_widget_id for c in C.widgets}]` is non-empty. `DashboardOut.template_update` carries the hint only for the owner (`can_edit`) and is computed by `template_update.update_hint(...)` **only in the `GET /dashboards/{id}` route** (one extra read of T's row and widgets, only when `source_template_id` is set) — never inside `store.get_dashboard`, which every `/data` and single-widget fetch also calls; all other responses carry `None`.
- **Preview** `GET …/template-update` → the widgets with new ids-to-be (`uuid4`), `layout.lg` placed one after another with `first_free_spot` against C's current lg rects (`md` = `None`, derived client-side), settings copied.
- **Merge** `POST … {"action":"merge","version":v}`: lock C's version (409 on mismatch); recompute `new` (never trust the preview); `len(C.widgets) + len(new) > 40` → 422 "Das Dashboard hätte dann mehr als 40 Widgets." and nothing changes; append rows (new ids, `template_widget_id = w.id`, `position` after the last), set `source_template_version = T.version`, `version + 1`, commit; never update or delete an existing row. **Dismiss**: only `source_template_version = T.version`, `version + 1`. Retry on race errors like phase 1 (`_retry_races`).
- **Frontend:** banner above the grid for the owner: "Neue Version der Vorlage „{name}“: {n} neue Widgets." buttons "Übernehmen" (merge; widgets appear, banner gone) and "Ausblenden" (dismiss). 409 → reload + "Das Dashboard wurde inzwischen geändert." In edit mode the banner is hidden.

### E9. Kiosk links (wallboard for screens without a login)

- **Creation** (admin only, Assumption): `POST /admin/dashboard-kiosk-links` checks every dashboard id with `store.get_dashboard(session, id, admin_id, admin=True)` (404 if not visible to the creator), creates `secrets.token_urlsafe(32)` (≥ 256 bit), stores `sha256(token).hexdigest()` and the last 4 characters as `token_hint`, `expires_at = utc_now() + expires_in_days`. The response is the only place the token appears; the UI shows the URL `https://<host>/wallboard#<token>` with "Kopieren" and the note "Der Link zeigt die Daten so, wie Sie sie sehen. Wer den Link hat, sieht das Wallboard ohne Anmeldung." Logs never contain the token (structlog events carry the link id).
- **Public API** (`api/v1/kiosk.py`, no `CurrentUser`, GET only): header `X-Tiqora-Kiosk: <token>`; every response sets `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. Order per request:
  1. per-IP-hash backstop (`ChatRateLimiter` scope `kiosk_ip`, 240 / 60 s) — Redis error → 503 (fail closed, Assumption);
  2. resolve: hash → row; not found / `revoked_at` set / `expires_at <= now` / creator `users.valid_id != 1` / creator no longer admin → count a miss (`kiosk_miss`, 30 / 600 s per IP hash; over → 429 with `Retry-After`) and answer **404 `{"detail": {"message": "Dieser Wallboard-Link ist ungültig oder abgelaufen."}}`** — identical for every case;
  3. `last_used_at` updated at most every 10 minutes (conditional `UPDATE … WHERE last_used_at IS NULL OR last_used_at < :cutoff`), committed;
  4. board: the link's dashboards in `position` order that are still visible to the creator (`get_dashboard(..., admin=True)`); none left → the same 404;
  5. data: the dashboard must be one of the link's visible dashboards (else 404); Redis cache `tiqora:dash:kiosk:data:<link id>:<dashboard id>:<dashboard version>` TTL 20 s, filled under a `SET NX` lock (TTL 30 s); a request that finds neither value nor lock computes; one that finds the lock but no value waits ≤ 2 s for it, then computes. The cache is consulted **after** step 2, so revocation is effective on the next poll. Computation = `run_batch(factory, registry, creator_id, widgets, filters=…, kiosk=True)` then `redact(type_key, data, show_details)`.
- **Redaction** (`kiosk.redact`, pure, whitelist): `show_details=False` → ticket items keep only `id, tn, queue_id, queue_name, state, state_type, priority_id, priority, created, changed, until_time, escalation_*` (every other `TicketListItem` field — present or added later — is dropped); team presence keeps `total_online` and per-status counts, drops people; chat queue keeps counts, drops `items`; AI status drops provider budgets and `currency`; `core.calendar-agenda` → status `forbidden` with "Auf dem Wallboard ausgeblendet."; report results whose definition uses a person dimension (`PERSON_DIMENSIONS`, a constant covering every source dimension key that names a person — customer, customer_user, owner, responsible, actor/agent; a test enumerates the registered sources and fails on an unclassified dimension) → `forbidden` with the same message. `show_details=True` → data unchanged except chat `visitor_name` (never present) and AI provider costs (always dropped on kiosks). Any provider key without a redaction rule → `forbidden` (fail closed).
- **Runtime context:** `ProviderContext.kiosk = True` → report widgets never defer to a job (E6), the counter does not write history (`persist=False`, like previews).
- **Admin overview:** `/admin/dashboard-kiosk-links` lists all links (active first), shows creator, dashboards (with "nicht mehr sichtbar" when the creator lost one), expiry, last use, "Widerrufen" / "Löschen".

### E10. Frontend contracts

```ts
// frontend/src/dashboard/filters/filtersContext.tsx
export interface DashboardFiltersValue { filters: DashboardFilters; active: boolean }
export const DashboardFiltersProvider; export function useDashboardFilters(): DashboardFiltersValue;   // read by WidgetFrame (pin glyph) and settings drawer
// frontend/src/dashboard/wallboard/kioskContext.tsx
export function KioskProvider(props: { children: ReactNode }): JSX.Element; export function useIsKiosk(): boolean;
// chrome/mode.ts (changed): rendersLinks(mode, kiosk = false) -> false in "preview" and on kiosks; isInteractive unchanged
// frontend/src/dashboard/wallboard/fit.ts  (pure)
export function wallboardRowHeight(viewport: { width: number; height: number }, rows: number, headerPx: number): number;  // clamp(48, 160); GAP = 24 on the wallboard
export function wallboardRows(widgets: EditWidget[]): number;   // max(y + h) of lg rects (>= 1)
export const WALLBOARD_MAX_ROWS = 14;                           // above: the link dialog warns "zu hoch für ein Wallboard"
// frontend/src/dashboard/wallboard/useRotation.ts
export function useRotation(count: number, seconds: number, opts?: { paused?: boolean }): { index: number; next: () => void; prev: () => void };
// WallboardView props
{ name: string; widgets: EditWidget[]; data: Record<string, WidgetDataState>; filters: DashboardFilters;
  generatedAt: string | null; stale: boolean; position?: { index: number; count: number }; onRetry: (id: string) => void }
// frontend/src/lib/kioskApi.ts — never uses api.request (no cookies, no 401 → login redirect)
export const kioskApi = { board(token, signal), data(token, dashboardId, signal) };   // fetch(..., { credentials: "omit", headers: { "X-Tiqora-Kiosk": token } })
export class KioskLinkInvalidError extends Error {}
```

Core widget registry additions/changes (`defineCoreWidget`; sizes `default / min / max`):

| key | category | icon | size | `dataEndpoint` | `refreshOn` | permission |
|---|---|---|---|---|---|---|
| `core.report` | reports | chart | [6,5] / [4,3] / [12,10] | `core.report` | `interval:300` | `stats` |
| `core.sla-gauge` | reports | gauge | [3,3] / [3,3] / [6,5] | `core.sla-gauge` | `interval:300` | `stats` |
| `core.backlog-aging` | reports | layers | [6,5] / [4,4] / [12,8] | `core.backlog-aging` | `interval:300` | `stats` |
| `core.heatmap` | reports | grid | [6,4] / [6,4] / [12,6] | `core.heatmap` | `interval:900` | `stats` |
| `core.chat-queue` | team | chat | [4,3] / [3,2] / [6,6] | `core.chat-queue` | `chat_waiting`, `chat_assigned`, `chat_ended`, `interval:30` | null |
| `core.ai-status` | ai | spark | [4,3] / [3,3] / [6,6] | `core.ai-status` | `interval:300` | null |
| `core.queue-overview` (changed) | tickets | queue | unchanged | `core.queue-overview` (was null) | `ticket_changed`, `ticket_new_in_queue` | null |

`galleryGroupOf`: `tickets → tickets`, `team | ai → team`, `reports → reports`, else `me`; gallery order Tickets / Für mich / Team / Berichte. `type_version` of every existing widget stays 1 (settings unchanged; `pinned` is a row column, not a setting).

### E11. Wallboard visual contract (approved `Wallboard` artboard)

- Full viewport, dark theme forced on the wallboard root (`data-theme="dark"` scoped to the wallboard container), padding 40/48 px at 1920×1080 (scaled with the viewport width), gap 24–28 px, `bg-page` token (mockup `#0b0d12`), widget cards `bg-surface border-hairline rounded-[20px] px-[30px] py-[26px]`.
- **Header:** dashboard name `h1` 40 px / 650, then muted 20 px "Wallboard · aktualisiert vor 12 s" (`· 2 / 3` appended when rotating; "Verbindung unterbrochen – Stand 10:41" in `text-danger` when the last success is older than 3 refresh intervals); clock right, mono 44 px / 600, `HH:MM` in the browser's locale and zone, updated every 15 s.
- **Grid:** the dashboard's `lg` layout, 12 columns, row height from `wallboardRowHeight` so the whole board fits without scrolling; overflow hidden (never scrolls).
- **Widgets in `mode: "wallboard"`** (type scale via CSS variable `--wb-scale = rowHeight / 64`): counter = label 22 px muted, value mono 96 px (tone colours as in view mode), one sub line 20 px (delta text "+6 seit gestern"; no sparkline); queue overview = rows "name 22 px | 22 px bar with three segments open (accent) / new (success) / escalated (danger) | `82 / 19 neu` mono" + legend "offen · neu · eskaliert"; team presence = two-column cards with status dot (available = success, in chat = accent) and status text "verfügbar" / "im Chat"; escalations at `h ≤ 2` = one banner "Nächste Frist: {title} · {Kunde} · 25 min" (danger tint; without details: "Nächste Frist: #{tn} · 25 min"), taller = the list at wallboard size; ticket lists, report widgets and the others render their view-mode body scaled. No hover actions, no links on kiosks; TV mode (logged in) keeps links.
- Respects `prefers-reduced-motion` (rotation switches without crossfade); otherwise a 300 ms opacity crossfade between dashboards.

---

## Deviations from spec

1. **Kiosk URL** is `/wallboard#<token>` instead of `/wallboard/{token}`: a fragment never reaches the server, so the token cannot land in nginx/proxy access logs or `Referer` headers; the SPA sends it as header `X-Tiqora-Kiosk`. A bookmark on the TV still works.
2. **Kiosk links are admin-only** and run as the creating admin (spec: "kiosk runs with the creating user's rights, stated in the UI" — kept; who may create is Open question 4). Several dashboards per link (rotation) are a junction table `tiqora_dashboard_kiosk_item` (arrays are not portable), and the token table gained `token_hint`, `name`, `rotate_seconds`, `show_details`, `last_used_at`, `revoked_at`.
3. **Dashboard filters** are queue set + time range (spec: "team/queue set, time range"; Open question 1); the time range applies to report widgets only (Open question 3).
4. **Drill-down target** is a dedicated read-only page `/agent/tickets/drill` driven by widget id + point, not a free filter URL; "Als Suche speichern"/"Als Widget hinzufügen" turn it into a saved search / ticket-list widget (spec item 5). Supersedes phase-1 rulings **R-DASH-COUNTER-LINKS** and **R-DASH-LIST-MORE-LINK**: counters and every "Alle N anzeigen" now link, because the drill list is exact by construction.
5. **Process overview widget** (catalogue row 2) is deferred to wave 5 (process analytics does not exist yet; Open question 8).
6. **Gallery live previews** remain static cards (phase-1 Deviation 3 continues); a new gallery group "Berichte" is added, which the approved Edit mockup does not show.
7. **Report widgets** are four widget types over one provider runner (`core.report` + three presets: SLA gauge on an inline definition, backlog aging and heatmap on the built-in reports `builtin.backlog_aging` / `builtin.created_heatmap`) rather than four independent data sources.
8. **Large reports** run as background jobs started from the agent's browser through a dashboard-specific endpoint (the HTTP `ReportRunIn` has no narrowing-filter field by design, reporting D12); kiosks never start jobs (Open question 7).
9. **Template merge** only adds new template widgets (spec: "merge new widgets"); changes/removals are not propagated (Open question 11). Copies made before this plan carry no `template_widget_id` and template widgets saved before it carry no `added_in_version`, so only widgets added to a template after the deployment are offered.
10. **Wallboard team status** shows online and "im Chat" only; "am Telefon", "Pause", "Urlaub" and the KPI "Ziel" line of the mockup have no data source (Open question 9). Visual regression stays "artefacts + `screenshots.spec.ts`", no `toHaveScreenshot` baselines (R-DASH-E2E-VISUAL).
11. **API-key area for dashboards** stays `_unknown` (R-DASH-APIKEY said "phase 2"; Open question 12): `/data` still mixes the tickets, calendar, agents, chat, AI and reports areas.
12. **Export/import JSON** stays deferred (Open question 13).
13. **`core.queue-overview` becomes server-backed** (was frontend-only in phase 1) so it can run on kiosks and show escalated counts per queue.
14. **Notes and links never render on a wallboard link**, with or without details (final fix wave; the ruling allowed them with `show_details`). Their settings are free text and URLs, and a link on the kiosk would be clickable; the board sends no settings for them and the tile reads "Auf dem Wallboard nicht verfügbar".
15. **Kiosk results without details** (final fix wave): error texts become the runner's generic text (a provider error may quote a saved report's name or a stored filter literal); the web-chat tile never sends `mine` (it would be the link creator's own chats); queue token budgets of the AI tile stay with and without details (E9 drops only provider cost budgets and the currency). The last good value that replaces a `pending` widget is kept 30 min instead of 6 h, because it carries no stale marker (no `stale_widgets` field in `DashboardDataOut`).

## Shared files (coordination with other wave-3 plans)

Migration head + `test_migration_gate.py` + `test_migrate_cli.py` (all wave-3 plans), `db/tiqora/models.py` (append after the last model), `domain/ticket_service.py` (append-only kwarg/method), `reporting/run_service.py` (append-only `RunRefOutcome.definition` — the wave-3 reporting plan (Znuny stats import, scheduled PDF) may also touch this file; keep the change one field + one assignment), `api/v1/__init__.py` and `api/v1/admin/__init__.py` (append `include_router` lines), `router.tsx` (two routes + one admin route), `lib/adminSearch.ts` (one entry), `components/agent/stats/*` (additive `onSelect` props), `routes/agent/SavedSearchEditorPage.tsx` (router-state initial criteria), `demo/mockData.ts` / `demo/handlers.ts` / `e2e/fixtures/mock-api.ts` (one delegation line each into `dashboardsMock.ts` / new `kioskMock.ts`), i18n `en.json`/`de.json` + propagation and both openapi files (regenerate/propagate, never hand-merge), `docs/dashboards.md`, `docs/architecture.md`, `docs/compatibility.md`, `docs/deployment.md`.

---

## Tasks

Order and dependencies below. Backend Tasks 1–11 can run before any frontend task; Task 12 needs the route shapes of Tasks 3, 6–11. **Frontend tasks 13–21 add i18n keys and therefore run one after another** (R-I18NSEQ). Seed sub-bands (band 61200–61599, provisional): Task 1 61200–61209, Task 3 61210–61239, Task 4 61240–61279, Task 5 61280–61319, Tasks 6–7 61320–61379, Task 8 61380–61409, Task 9 61410–61449, Task 10 61450–61469, Task 11 61470–61519, spare 61520–61599.

| # | Task | Stack | Needs |
|---|---|---|---|
| 1 | Tables, ORM, migration | backend | — |
| 2 | Pure layer: filters model, schemas, new settings models, `first_free_spot` | backend | — |
| 3 | Store + API: filters, `pinned`, preview filters | backend | 1, 2 |
| 4 | Runner and phase-1 providers honour filters (`queue_ids`, counter keys) | backend | 3 |
| 5 | Providers: queue overview, team chat status, chat queue, AI status | backend | 4 |
| 6 | Report widget provider (`core.report`), context services, save validation | backend | 4 |
| 7 | Report job endpoint; SLA gauge, backlog aging, heatmap providers | backend | 6 |
| 8 | Template update merge | backend | 1–3 |
| 9 | Drill-down endpoint, preset filter parity, report point mapping | backend | 4, 6, 7 |
| 10 | Kiosk links: store, admin API, redaction whitelist | backend | 1, 2, 5, 7 |
| 11 | Kiosk public API: auth, rate limits, cache, redaction | backend | 5, 7, 10 |
| 12 | API layer, types, demo and e2e mocks | frontend | 3, 6–11 |
| 13 | Filter bar, `pinned` switch, filters context | frontend | 12 |
| 14 | Report widget, report settings editor, background job flow, "Berichte" group | frontend | 13 |
| 15 | Gauge chart; SLA gauge, backlog aging, heatmap widgets | frontend | 14 |
| 16 | Queue overview from the server, team chat status, chat queue and AI status widgets | frontend | 15 |
| 17 | Drill-down page and entry points | frontend | 16 |
| 18 | Template update banner | frontend | 17 |
| 19 | Wallboard view, wallboard widget styles, TV mode with rotation | frontend | 18 |
| 20 | Public kiosk page `/wallboard` | frontend | 19 |
| 21 | Wallboard-link management (dialog + admin page) | frontend | 20 |
| 22 | E2E, screenshots, documentation, full verification | all | all |

---

### Task 1: Tables, ORM, migration (backend-only)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_dashboards_phase2.py`, `backend/tests/test_dashboard3_migration_db.py`, `backend/tests/test_dashboard3_tables.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (three columns on `TiqoraDashboardWidget`; append `TiqoraDashboardKioskToken`, `TiqoraDashboardKioskItem` after the last model), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (`version ==`)

**Interfaces:**
- Consumes: nothing.
- Produces: the ORM attributes and constraint names of E2 (`uq_tiqora_dashboard_kiosk_token_hash`, `ix_tiqora_dashboard_kiosk_token_creator`, `fk_tiqora_dashboard_kiosk_item_token`, `fk_tiqora_dashboard_kiosk_item_dashboard`, `ix_tiqora_dashboard_kiosk_item_dashboard`).

- [ ] **Step 1: Write the failing tests.** `test_dashboard3_migration_db.py` is copied from `backend/tests/test_dashboard_migration_db.py` with `_PREVIOUS` = the head before this migration, `_NEW` = this revision: upgrade to previous → the three widget columns and both kiosk tables absent; upgrade to new → present and `_assert_matches_orm` (columns, nullability, type family, lengths, server defaults, PK, unique, indexes, both FKs with `ON DELETE CASCADE`, every FK target starts with `tiqora_`); existing widget rows (insert one at `_PREVIOUS`) get `pinned = false`, `template_widget_id`/`added_in_version` NULL; downgrade → absent; upgrade again; leave at head. Both dialects. `test_dashboard3_tables.py` (both dialects, band 61200–61209, cleans up):

```python
async def test_kiosk_items_cascade_with_token_and_with_dashboard(env) -> None:
    # token T with items for dashboards D1, D2; delete D1 -> only D2's item left; delete T -> no item left
    ...
async def test_token_hash_is_unique(env) -> None:
    # two tokens with the same token_hash -> IntegrityError; different hashes fine
    ...
def test_widget_columns_defaults() -> None:
    widget = TiqoraBase.metadata.tables["tiqora_dashboard_widget"]
    assert widget.c.pinned.nullable is False and widget.c.template_widget_id.type.length == 36
    assert widget.c.added_in_version.nullable is True
```

- [ ] **Step 2: Run to verify they fail** — `cd backend && uv run python -m pytest tests/test_dashboard3_tables.py tests/test_dashboard3_migration_db.py -q` → FAIL (`ImportError` / missing columns).
- [ ] **Step 3: Implement** the ORM changes and the migration (`op.add_column` ×3 with `server_default=sa.false()` for `pinned`, `op.create_table` ×2, indexes, unique; `down_revision` = current primary head; `downgrade` drops in reverse order). Bump `TIQORA_HEAD` and the CLI assert.
- [ ] **Step 4: Run tests** — the two new modules plus `tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_dashboard_migration_db.py tests/test_dashboard_tables.py` → PASS; lint gates.
- [ ] **Step 5: Commit** — `feat(dashboard): kiosk link tables, widget pin and template-link columns`.

---

### Task 2: Pure layer — filters model, new settings models, `first_free_spot` (backend-only, no DB, no API-model change)

**Files:**
- Create: `backend/src/tiqora/dashboard/filters.py`, `backend/tests/test_dashboard_filters_model.py`, `backend/tests/test_dashboard3_settings_models.py`, `backend/tests/fixtures/first_free_spot_cases.json` (shared with the frontend test in Task 12)
- Modify: `backend/src/tiqora/dashboard/settings_models.py` (E6/E7 models, registered in `CORE_WIDGET_SETTINGS`), `backend/src/tiqora/dashboard/layout.py` (`first_free_spot`), `backend/tests/test_dashboard_layout.py`. **`schemas.py` is not touched here**: each API model of E3 is added by the task that first serves it (Task 3: `pinned`, `filters`, `DashboardFiltersIn`, preview `filters`; Task 8: template-update models; Task 9: `DrillPageOut`; Task 10: kiosk link models; Task 11: `KioskBoardOut`), together with the openapi/`schema.d.ts` regeneration and the frontend fixture updates in the same commit.

**Interfaces:**
- Consumes: `TimeRange` (`reporting.definition`), `ParameterValue` (`reporting.schemas`, verify name).
- Produces: `QueueIdIn`, `DashboardFilters`, `TimeRangePreset`, `report_time_range`, `filters_key`, `effective_filters` (all in `filters.py`, which imports nothing from `schemas.py`); `ReportWidgetSettings`, `SlaGaugeSettings`, `BacklogAgingSettings`, `HeatmapSettings`, `ChatQueueSettings`, `AiStatusSettings`, `REPORT_KEY_RE`; `first_free_spot(rects: Sequence[Rect], w: int, h: int, *, cols: int = GRID_COLS) -> tuple[int, int]` (same scan as the frontend `firstFreeSpot`: `y = 0..`, `x = 0..cols-w`, first non-colliding; always terminates below the lowest rect).

- [ ] **Step 1: Write the failing tests**

```python
# test_dashboard_filters_model.py
def test_queue_ids_deduplicated_sorted_and_bounded() -> None:
    assert DashboardFilters(queue_ids=[5, 3, 5]).queue_ids == [3, 5]
    with pytest.raises(ValidationError): DashboardFilters(queue_ids=list(range(1, 52)))
    with pytest.raises(ValidationError): DashboardFilters(queue_ids=[0])
    with pytest.raises(ValidationError): DashboardFilters(queue_ids=[2**31])
def test_extra_keys_rejected() -> None:
    with pytest.raises(ValidationError): DashboardFilters.model_validate({"owner": [1]})
@pytest.mark.parametrize("preset", ["today", "7d", "30d", "90d", "this_month", "last_month"])
def test_report_time_range_mapping_resolves_to_expected_window(preset) -> None:
    # resolve with the reporting window helper for a fixed today (2026-10-14, Europe/Berlin) and assert start/end dates per preset
    ...
def test_filters_key_only_for_queue_filters() -> None:
    assert filters_key(DashboardFilters()) is None
    assert filters_key(DashboardFilters(queue_ids=[3, 5])) == filters_key(DashboardFilters(queue_ids=[5, 3]))
    assert filters_key(DashboardFilters(time_range="7d")) is None
def test_effective_filters_pinned_is_empty() -> None:
    assert effective_filters(DashboardFilters(queue_ids=[3]), pinned=True).is_empty()

# test_dashboard3_settings_models.py
def test_report_key_pattern() -> None: ...            # builtin.volume ok, "42" ok, "0", "builtin.", "../x", "builtin.A" -> SettingsError via validate_widget_settings, path settings.report_key
def test_report_settings_bounds() -> None: ...       # max_rows 0/51 -> German message "Der Wert muss zwischen 1 und 50 liegen."
def test_new_types_registered() -> None:
    assert {"core.report", "core.sla-gauge", "core.backlog-aging", "core.heatmap", "core.chat-queue", "core.ai-status"} <= set(CORE_WIDGET_SETTINGS)

# test_dashboard_layout.py (append)
@pytest.mark.parametrize("case", json.loads(Path(FIXTURES / "first_free_spot_cases.json").read_text()))
def test_first_free_spot_matches_shared_cases(case) -> None: ...
```

- [ ] **Step 2: Run to verify they fail** — `uv run python -m pytest tests/test_dashboard_filters_model.py tests/test_dashboard3_settings_models.py tests/test_dashboard_layout.py -q`.
- [ ] **Step 3: Implement.** Fixture cases: empty grid, a full first row, a gap that is too narrow, a gap exactly wide enough, `w = 12`, a tall item at `x = 0`. Mapping table for `report_time_range` documented inline.
- [ ] **Step 4: Run tests** — the three modules plus every existing `tests/test_dashboard_*.py` and `tests/test_dashboards_api.py` (the widget-types list grows) → PASS; lint gates.
- [ ] **Step 5: Commit** — `feat(dashboard): filter model, phase-2 widget settings and first-free-spot helper`.

---

### Task 3: Store and API — filters, `pinned`, preview filters (backend-only)

**Files:**
- Create: `backend/tests/_dashboard3_helpers.py` (world for band 61200–61599: users `dash3a` 61210 (agent, `ro`+`rw` on queue Q1), `dash3b` 61211 (agent, `ro` on Q2 only), `dash3admin` 61212; two queues Q1/Q2 61213/61214 in groups 61215/61216; `cleanup_world` deleting every `tiqora_dashboard*` and `tiqora_dashboard_kiosk_*` row of band owners plus band Znuny rows; imports `FakeRedis`, `to_async_url` from `_dashboard_helpers`), `backend/tests/test_dashboard_filters_api.py`
- Modify: `backend/src/tiqora/dashboard/schemas.py` (`WidgetIn.pinned`, `DashboardOut.filters`/`template_update` (hint type `TemplateUpdateHint` declared here, filled in Task 8), `DashboardSaveIn.filters`, `DashboardFiltersIn`, `WidgetPreviewIn.filters`), `backend/src/tiqora/dashboard/store.py` (`filters` read/write, `pinned` round trip, `set_filters`), `backend/src/tiqora/api/v1/dashboards.py` (`PATCH /{id}/filters`; preview passes `body.filters`), openapi files + `schema.d.ts`, `frontend/src/demo/dashboardsMock.ts` factories + `frontend/e2e/fixtures/mock-api.ts` (new required TS fields)

**Interfaces:**
- Consumes: Task 1 columns, Task 2 models.
- Produces: `store.set_filters(session, dashboard_id, user_id, filters: DashboardFilters, version: int, *, admin: bool) -> DashboardOut`; `store.dashboard_filters(row) -> DashboardFilters` (tolerant parse); `DashboardOut.filters` and `WidgetOut.pinned` filled everywhere (`get_dashboard`, `save_dashboard`, `ensure_start`, `create_dashboard`, templates).

- [ ] **Step 1: Write the failing tests** (both dialects, band 61210–61239)

```python
async def test_filters_round_trip_and_bump_version(env) -> None:
    d = await env.create_personal(owner=A)
    out = await env.patch_filters(A, d.id, {"queue_ids": [Q1], "time_range": "30d"}, version=d.version)
    assert out.filters.queue_ids == [Q1] and out.version == d.version + 1
    assert (await env.get(A, d.id)).filters.time_range == "30d"
async def test_filters_stale_version_is_409_and_nothing_changes(env) -> None: ...
async def test_filters_read_only_viewer_403_invisible_404(env) -> None: ...   # B on A's shared dashboard -> 403; C on A's personal -> 404 (same body as unknown id)
async def test_put_without_filters_keeps_them_put_with_filters_replaces(env) -> None: ...
async def test_pinned_round_trips_through_put_and_copy(env) -> None: ...
async def test_broken_filters_json_reads_as_empty(env) -> None:
    # write filters='{"queue_ids": "x"}' directly, GET -> filters == DashboardFilters(), no 500
    ...
async def test_preview_accepts_filters(client) -> None: ...                   # POST /dashboard-widget-types/core.counter/data with filters -> 200
def test_widget_in_pinned_defaults_false_and_save_in_filters_optional() -> None: ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** (optimistic lock exactly like `save_dashboard`: `UPDATE … SET filters = :f, version = version + 1 WHERE id = :id AND version = :v`, `rowcount == 0` → `DashboardConflict`; `_retry_races` around it). Regenerate openapi; update `frontend/src/demo/dashboardsMock.ts` factories so `pnpm lint` stays green (new required TS fields `filters`, `pinned`, `template_update`).
- [ ] **Step 4: Run tests** — new module + `tests/test_dashboard_store.py tests/test_dashboards_api.py` → PASS; lint gates; `cd frontend && pnpm lint`.
- [ ] **Step 5: Commit** — `feat(dashboard): dashboard-wide filters and pinned widgets in store and API`.

---

### Task 4: Runner and phase-1 providers honour filters (backend-only)

**Files:**
- Create: `backend/tests/test_dashboard_filters_providers.py`
- Modify: `backend/src/tiqora/domain/ticket_service.py` (append-only: `queue_ids: Sequence[int] | None = None` on `_filtered_ticket_stmt`, `list_tickets`, `count_filtered`; new `count_by_queue(user_id, **filter_kwargs) -> dict[int, int]`), `backend/src/tiqora/dashboard/runner.py` (`filters` param on `run_batch`/`run_widget`, per-widget `effective_filters`, `WidgetRow.pinned`), `backend/src/tiqora/dashboard/providers/__init__.py` (`ProviderContext` append-only fields `app_settings: Settings | None = None`, `report_cache_backend: Any | None = None`, `kiosk: bool = False`; `filters` now filled), providers `counter.py`, `ticket_list.py`, `escalations.py`, `reminders.py`, `sources.py` (`compile_for_viewer(..., queue_ids=…)`), `api/v1/dashboards.py` (pass the dashboard's filters; `_PreviewWidget.pinned = False`)

**Interfaces:**
- Consumes: Task 3.
- Produces: `run_batch(..., filters: DashboardFilters | None = None, app_settings=None, report_cache_backend=None, kiosk: bool = False)`; same for `run_widget`; `ProviderContext.filters == {"queue_ids": [...], "time_range": ... }`; counter keys `"preset:mine_open|q:1a2b3c4d"`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 61240–61279; seeds open tickets for A in Q1 and Q2 is not visible to A; B sees only Q2)

```python
async def test_counter_and_list_narrow_to_filter_queues(env) -> None:
    # A: mine_open = 3 (2 in Q1, 1 in Q1b); filter [Q1] -> counter 2, list total 2, items only Q1
    ...
async def test_filter_queue_outside_scope_yields_nothing(env) -> None:
    # A filters to Q2 (no ro) -> counter 0, list empty, escalations empty, never an error
    ...
async def test_shared_dashboard_filter_runs_in_viewer_scope(env) -> None:
    # A shares a dashboard filtered to [Q1, Q2]; B (ro Q2 only) sees only Q2 tickets; A sees Q1 (+Q2 if A had it)
    ...
async def test_pinned_widget_ignores_filters(env) -> None: ...
async def test_saved_search_and_own_filter_sources_are_narrowed(env) -> None: ...
async def test_filtered_counter_history_is_separate(env) -> None:
    # same counter with and without filter on the same day -> two sample rows with different counter_key
    ...
async def test_empty_filters_keep_phase1_parity(env) -> None:
    # count_dashboard_summary parity test of phase 1 still holds (queue_ids=None path unchanged)
    ...
async def test_count_by_queue_matches_per_queue_counts(env) -> None: ...
```

Runner unit test (pure, append to `tests/test_dashboard_runner.py`): a fake provider records `ctx.filters`; pinned widget receives `{"queue_ids": [], "time_range": None}`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** `queue_ids` is ANDed (`Ticket.queue_id.in_(queue_ids)`) after the scope; an empty list means "no narrowing", `None` likewise. Do not reorder existing kwargs. Reminders: pass `queue_ids` into the scan query.
- [ ] **Step 4: Run tests** — new module + all `tests/test_dashboard_*.py` + `tests/test_ticket_service*.py` → PASS; lint gates.
- [ ] **Step 5: Commit** — `feat(dashboard): widgets narrow by the dashboard's queue filter unless pinned`.

---

### Task 5: Providers — queue overview, team chat status, chat queue, AI status (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/providers/{queue_overview,chat_queue,ai_status}.py`, `backend/tests/test_dashboard3_providers.py`
- Modify: `backend/src/tiqora/dashboard/providers/team_presence.py`, `backend/src/tiqora/dashboard/providers/__init__.py` (`core_providers()`), `backend/src/tiqora/api/v1/dashboards.py` (widget-types list now includes the new keys via `CORE_WIDGET_SETTINGS`)

**Interfaces:**
- Consumes: Task 4 (`count_by_queue`, `ctx.filters`), `list_inbox`, `collect_limits`, `TiqoraChatSession`.
- Produces: E7 data shapes exactly.

- [ ] **Step 1: Write the failing tests** (both dialects, band 61280–61319)

```python
async def test_queue_overview_counts_and_escalated(env) -> None:
    # Q1: 3 open (1 new, 1 breached escalation_time in the past) -> {"open": 3, "new": 1, "escalated": 1}; invisible queue absent
    ...
async def test_queue_overview_respects_filter_and_only_with_work(env) -> None: ...
async def test_team_presence_marks_agents_in_chat(env) -> None:
    # FakeRedis presence for A and B; active chat session assigned to B -> B status in_chat, A available
    ...
async def test_chat_queue_scoped_to_rw_queues_and_never_returns_visitor_name(env) -> None:
    # waiting chats on tickets in Q1 (A rw) and Q2 (A no rw) -> A sees 1; "visitor_name" not in any item
    ...
async def test_chat_queue_disabled_when_no_chat_config(env) -> None: ...
async def test_ai_status_budgets_scoped_and_provider_costs_admin_only(env) -> None:
    # queue budget for Q1 and Q2 + one provider cost budget; A (agent) sees Q1 budget only; admin sees Q1, Q2 and provider
    ...
async def test_ai_status_counts_runs_today_for_viewer_queues(env) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Never JOIN `tiqora_chat_session`/`tiqora_ai_*` with Znuny tables: load ticket/queue ids, then integer IN-lists. `collect_limits` is filtered in Python by `subject_id in ro_queue_ids` for `kind == "queue"` (verify the kind strings).
- [ ] **Step 4: Run tests** → PASS; lint gates; regenerate openapi if `WidgetTypeOut` lists changed.
- [ ] **Step 5: Commit** — `feat(dashboard): queue overview, chat queue and AI status data; agents in chat`.

---

### Task 6: Report widget provider `core.report`, context services, save validation (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/providers/report.py`, `backend/tests/test_dashboard_report_widget.py`
- Modify: `backend/src/tiqora/reporting/run_service.py` (append-only `RunRefOutcome.definition`), `backend/src/tiqora/dashboard/providers/__init__.py`, `backend/src/tiqora/dashboard/store.py` (`validate_source_settings` second pass for `core.report`), `backend/src/tiqora/api/v1/dashboards.py` (pass `app_settings` (`AppSettings` dep) and the Redis client as `report_cache_backend` to the runner)

**Interfaces:**
- Consumes: reporting seam (E6), Task 4 context fields.
- Produces: `run_report_widget(ctx, *, report_key: str | None, definition: ReportDefinition | None, time_range: TimeRange | None, parameters, dimension_choices, max_rows: int, display: str) -> dict[str, Any]`; `report_provider() -> DashboardProvider` (key `core.report`, `timeout_seconds=8.0`); data shape of E6; `drillable_dimensions(definition) -> list[str]` (used by Task 9; implement here as a pure helper returning `[]` for non-ticket sources).

- [ ] **Step 1: Write the failing tests** (both dialects, band 61320–61379; reporting test helpers for facts: reuse what `tests/test_reports_api.py` seeds; `FakeRedis`-backed cache or the in-memory cache backend the report tests use)

```python
async def test_builtin_report_widget_returns_result_and_definition(env) -> None: ...
async def test_report_widget_runs_as_viewer(env) -> None:
    # saved report shared with group G1; A in G1 sees result, B not in G1 -> forbidden "…nicht freigegeben."
    ...
async def test_report_widget_404_410_403_become_tiles(env) -> None:
    # unknown id -> forbidden; soft-deleted -> error "Der Bericht „…“ wurde gelöscht."; reporting.access = stats group and A not in it -> forbidden "Für Berichte fehlt Ihnen die Berechtigung."
    ...
async def test_report_widget_deferred_never_enqueues(env, monkeypatch) -> None:
    # inline_row_threshold = 0 -> data {"deferred": true}; enqueue_report_ref spy not called; no job row
    ...
async def test_report_widget_common_filter_only_narrows(env) -> None:
    # dashboard filter [Q1] -> totals equal the Q1 slice; filter [Q2] for A (no ro) -> zero rows, not other users' data
    ...
async def test_dashboard_time_range_overrides_report_range_unless_pinned(env) -> None: ...
async def test_rows_capped_and_truncated_flag(env) -> None: ...
async def test_save_rejects_invisible_report_for_saver(env) -> None: ...   # 422 path widgets[0].settings.report_key
async def test_report_widget_kiosk_flag_never_defers_to_job(env) -> None: ...   # kiosk=True + deferred -> {"deferred": true, "kiosk": true}
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E6. Map exceptions inside the provider (never let `HTTPException` reach the runner). Verify `QueryBudget` constructor order and that the runner's outer `statement_timeout` and the engine's per-statement timeouts compose (the inner one must not exceed the remaining provider budget — if the engine issues `SET LOCAL statement_timeout` per statement, the smaller value wins; write a short comment with what you verified).
- [ ] **Step 4: Run tests** — new module + `tests/test_reports_api.py tests/test_reporting_run_service*.py tests/test_dashboard_runner*.py` → PASS; lint gates; openapi unchanged or regenerated.
- [ ] **Step 5: Commit** — `feat(dashboard): report widget runs any visible report in the viewer's scope`.

---

### Task 7: Report job endpoint; SLA gauge, backlog aging, heatmap providers (backend-only)

**Files:**
- Modify: `backend/src/tiqora/dashboard/providers/report.py` (`SLA_GAUGE_DEFINITION`, three providers), `backend/src/tiqora/dashboard/providers/__init__.py`, `backend/src/tiqora/api/v1/dashboards.py` (`POST /{id}/widgets/{wid}/report-job`), `backend/tests/test_dashboard_report_widget.py`, openapi files

**Interfaces:**
- Consumes: Task 6.
- Produces: providers `core.sla-gauge`, `core.backlog-aging`, `core.heatmap`; route returns `202 JobAccepted`.

- [ ] **Step 1: Write the failing tests** (append; band 61320–61379)

```python
def test_sla_gauge_definition_validates() -> None:
    validate_definition(SLA_GAUGE_DEFINITION, get_registry())
async def test_sla_gauge_rate_in_percent_and_target(env) -> None: ...
async def test_backlog_aging_rows_capped(env) -> None: ...
async def test_heatmap_time_range_setting(env) -> None: ...
async def test_report_job_result_is_served_from_cache(env, job_runner) -> None:
    # force deferral; POST report-job -> 202; run the job (wave-0 job runner fixture); next batch -> status ok, result.cached True, values equal an inline run without threshold
    ...
async def test_report_job_rejects_non_report_widgets_and_respects_job_cap(env) -> None: ...   # 422; 4th job -> 429
async def test_report_job_invisible_dashboard_404(env) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** The route loads the widget like `widget_data` (`_visible_widgets`), validates settings, builds `ReportRunIn` (+ effective time range), calls `prepare_ref` for the effective narrowing, then `enqueue_report_ref` (it commits itself; the route does not commit again). `TooManyReportJobs` → 429 with its message.
- [ ] **Step 4: Run tests** → PASS; lint gates; regenerate openapi.
- [ ] **Step 5: Commit** — `feat(dashboard): SLA gauge, backlog aging and heatmap widgets; background runs for large reports`.

---

### Task 8: Template update merge (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/template_update.py`, `backend/tests/test_dashboard_template_update.py`
- Modify: `backend/src/tiqora/dashboard/store.py` (`added_in_version` on template saves; `template_widget_id` and source fields on `copy_from` a template; `DashboardOut.template_update` hint in `get_dashboard`), `backend/src/tiqora/dashboard/start.py` (`template_widget_id` on first-visit copies), `backend/src/tiqora/api/v1/dashboards.py` (two routes), openapi files

**Interfaces:**
- Consumes: Task 1 columns, Task 2 `first_free_spot`, phase-1 `ensure_start`.
- Produces: `pending_update(session, dashboard_row, viewer) -> tuple[TiqoraDashboard, list[TiqoraDashboardWidget]] | None`, `preview_update(...) -> TemplateUpdateOut`, `apply_update(session, dashboard_id, viewer, action, version) -> DashboardOut`.

- [ ] **Step 1: Write the failing tests** (both dialects, band 61380–61409)

```python
async def test_first_visit_copy_records_template_widget_ids(env) -> None: ...
async def test_widgets_added_to_template_are_offered(env) -> None:
    # template T (role R, A in R); A's copy; admin adds widget X to T -> A's DashboardOut.template_update.new_widget_count == 1; GET template-update lists X placed in a free spot
    ...
async def test_merge_appends_only_new_template_widgets(env) -> None: ...
async def test_merge_never_moves_or_changes_user_widgets(env) -> None:
    # A moved and re-configured copied widgets and added own ones; after merge every pre-existing row is byte-identical (id, settings, layout, position)
    ...
async def test_removed_copy_widget_is_not_offered_again(env) -> None:
    # A deleted a widget that came from T before the update -> not offered (added_in_version <= source version)
    ...
async def test_dismiss_hides_until_next_template_change(env) -> None: ...
async def test_stale_version_409_and_widget_limit_422(env) -> None: ...
async def test_deleted_locked_or_unassigned_template_offers_nothing(env) -> None: ...   # T deleted / T locked / A lost role R -> hint None, GET 404
async def test_only_owner_sees_hint(env) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E8 (read T's widgets with a `WHERE dashboard_id = :t` query — no JOIN needed; all tables are `tiqora_*`).
- [ ] **Step 4: Run tests** — new module + `tests/test_dashboard_store.py tests/test_admin_dashboard_templates_api.py tests/test_dashboards_api.py` → PASS; lint gates; regenerate openapi.
- [ ] **Step 5: Commit** — `feat(dashboard): offer and merge widgets added to a role template`.

---

### Task 9: Drill-down endpoint, preset filter parity, report point mapping (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/drill.py`, `backend/tests/test_dashboard_drill.py`
- Modify: `backend/src/tiqora/dashboard/providers/presets.py` (`preset_filter`), `backend/src/tiqora/dashboard/providers/report.py` (`drillable_dimensions` real mapping, `REPORT_DIMENSION_TO_TICKET_FIELD`), `backend/src/tiqora/dashboard/providers/reminders.py` (paging-safe due filter if needed), `backend/src/tiqora/api/v1/dashboards.py` (route), openapi files

**Interfaces:**
- Consumes: Tasks 4, 6, 7.
- Produces: `widget_tickets(...) -> DrillPageOut` (E5), `preset_filter(preset: str, uid: int) -> Filter | None`, `point_filter(definition, params, point) -> Filter` (raises `DrillNotPossible(message)`).

- [ ] **Step 1: Write the failing tests** (both dialects, band 61410–61449; seeds every state type (new, open, pending reminder, pending auto, closed, merged), owners A / unassigned, escalation epochs past/future/0, one `tiqora_ai_ticket_state` with `ai_escalated_at`)

```python
@pytest.mark.parametrize("preset", list(TICKET_PRESETS))
async def test_preset_filter_parity_per_preset(env, preset) -> None:
    flt = preset_filter(preset, A)
    if flt is None:
        pytest.skip(f"{preset}: not expressible; recorded in the task report")   # allowed, but must be justified in the report
    via_kwargs = await ids_via_list_tickets(env, A, preset_kwargs(preset, A))
    via_filter = await ids_via_compiled_filter(env, A, flt)
    assert via_filter == via_kwargs
async def test_drill_total_equals_counter_value(env) -> None: ...          # every preset, with and without dashboard filter, pinned and not
async def test_drill_pages_and_sorts(env) -> None: ...                    # limit 2, offset 2; limit 51 -> 422
async def test_drill_escalations_and_reminders_totals_match_widget(env) -> None: ...
async def test_drill_saved_search_widget_offers_its_criteria_anded_with_queue_filter(env) -> None: ...
async def test_drill_point_is_rebuilt_server_side(env) -> None:
    # report "volume_by_queue" bar Q1 -> list == tickets of Q1 in the report window; point {"queue": Q2} for A (no ro) -> empty list, not 403
    ...
async def test_person_dimension_and_foreign_source_not_drillable(env) -> None: ...   # sla_by_customer point, ticket_events source -> 422 German message
async def test_point_size_and_shape_bounds(env) -> None: ...                         # > 3 keys, > 500 bytes, non-object -> 422
async def test_drill_on_invisible_dashboard_or_foreign_widget_404(env) -> None: ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E5. Derive `preset_filter` from `_filtered_ticket_stmt` (read it; reproduce its `state_type` expansion with the `state_type` filter field's `in` operator if needed; `escalated` → `escalation_due_within lte 0`; `ai_escalated` → `ai_escalated is_true`; `unowned_new` → `owner_id eq 1`). Record in the task report which presets returned `None` and why.
- [ ] **Step 4: Run tests** → PASS; lint gates; regenerate openapi.
- [ ] **Step 5: Commit** — `feat(dashboard): drill-down from widgets and chart points to the exact ticket list`.

---

### Task 10: Kiosk links — store, admin API, redaction whitelist (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/kiosk.py` (token, store and pure redaction parts), `backend/src/tiqora/api/v1/admin/dashboard_kiosk_links.py`, `backend/tests/test_dashboard_kiosk_links_api.py`, `backend/tests/test_dashboard_kiosk_redaction.py`
- Modify: `backend/src/tiqora/api/v1/admin/__init__.py`, `backend/src/tiqora/dashboard/schemas.py` (`KioskLinkIn`, `KioskLinkOut`, `KioskLinkCreatedOut`, `KioskDashboardRef`), `backend/tests/test_api_key_scopes.py` (`/api/v1/admin/dashboard-kiosk-links` → `admin`), openapi files

**Interfaces:**
- Consumes: Task 1 tables, `store.get_dashboard`.
- Produces: `new_token() -> tuple[str, str, str]` (token, sha256 hex, hint), `hash_token(token) -> str`, `create_link(session, admin_id, body) -> tuple[KioskLinkOut, str]`, `list_links(session) -> list[KioskLinkOut]`, `revoke_link(session, link_id) -> KioskLinkOut` (idempotent), `delete_link(session, link_id) -> None` (active link → `DashboardInvalid`), plus the pure redaction layer of E9: `redact(type_key, data, show_details) -> WidgetDataOut`, `PERSON_DIMENSIONS`, `NON_PERSON_DIMENSIONS`, `KIOSK_SAFE_TICKET_FIELDS`.

- [ ] **Step 1: Write the failing tests** (API tests on both dialects, band 61450–61469; redaction tests pure)

```python
# test_dashboard_kiosk_redaction.py (pure)
def test_redaction_whitelist_drops_unknown_fields() -> None:
    item = {"id": 1, "tn": "2026101410000011", "title": "VPN gesperrt", "customer_id": "Beispiel GmbH", "future_field": "x"}
    out = redact("core.ticket-list", {"items": [item], "total": 1}, show_details=False)
    assert out.data["items"][0].keys() <= KIOSK_SAFE_TICKET_FIELDS and "future_field" not in out.data["items"][0]
def test_unknown_provider_key_is_forbidden_on_kiosk() -> None: ...
def test_calendar_hidden_without_details_and_team_reduced_to_counts() -> None: ...
def test_person_dimensions_cover_every_registered_source() -> None:
    # every dimension key of every REPORT_SOURCES item is either in PERSON_DIMENSIONS or in NON_PERSON_DIMENSIONS; fails on a new unclassified one
    ...
def test_ai_provider_costs_always_dropped() -> None: ...

# test_dashboard_kiosk_links_api.py
async def test_token_stored_hashed_and_shown_once(client, env) -> None:
    out = await client.post("/api/v1/admin/dashboard-kiosk-links", json={...}, headers=admin)
    token = out.json()["token"]
    row = await env.kiosk_row(out.json()["id"])
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest() and token not in row.token_hash
    assert len(token) >= 43 and row.token_hint == token[-4:]
    listed = await client.get("/api/v1/admin/dashboard-kiosk-links", headers=admin)
    assert "token" not in listed.json()[0]
async def test_only_admins_and_only_visible_dashboards(client, env) -> None: ...   # agent -> 403; another user's personal dashboard -> 404
async def test_bounds(client) -> None: ...          # 0 or 11 dashboards, rotate 14/901, expires 0/366 -> 422
async def test_revoke_is_idempotent_and_delete_needs_revoked(client, env) -> None: ...   # revoke twice -> same revoked_at; DELETE active -> 422; DELETE revoked -> 204, items gone
async def test_token_never_logged(client, caplog_structlog) -> None: ...          # no log record contains the token
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Creator names: integer IN-list read of `users`. Redaction per E9 (whitelists; any key without a rule → `forbidden` "Auf dem Wallboard ausgeblendet.").
- [ ] **Step 4: Run tests** → PASS; lint gates; regenerate openapi.
- [ ] **Step 5: Commit** — `feat(dashboard): admins create and revoke wallboard links; kiosk redaction rules`.

---

### Task 11: Kiosk public API — auth, rate limits, cache (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/v1/kiosk.py`, `backend/tests/test_dashboard_kiosk_public_api.py`
- Modify: `backend/src/tiqora/dashboard/schemas.py` (`KioskBoardOut`, `KioskDashboardOut`), `backend/src/tiqora/dashboard/kiosk.py` (resolve, data cache; applies Task 10's `redact`), `backend/src/tiqora/api/v1/__init__.py`, `backend/src/tiqora/dashboard/providers/counter.py` (no history on kiosk), `backend/tests/test_api_key_scopes.py` (`/api/v1/kiosk/board` stays `_unknown`; a bearer API key alone gets 404 there because the kiosk header is required), openapi files

**Interfaces:**
- Consumes: Tasks 5, 7, 10; `ChatRateLimiter`, `hash_ip`, `client_ip`.
- Produces: `resolve_link(session, token, now) -> ResolvedLink | None` (row + creator id + visible dashboard ids), `kiosk_data(...)`; routes of E3.

- [ ] **Step 1: Write the failing tests**

```python
# test_dashboard_kiosk_public_api.py (both dialects, band 61470–61519, FakeRedis)
async def test_board_lists_visible_dashboards_in_order(client, env) -> None: ...
async def test_invalid_states_are_indistinguishable(client, env) -> None:
    # unknown, expired, revoked, creator invalid (valid_id=2), creator no longer admin, no dashboard visible -> identical status and body
    ...
async def test_revoked_link_stops_on_next_poll_despite_cache(client, env) -> None:
    # data call fills the cache; revoke; next data call -> 404
    ...
async def test_data_runs_as_creator_and_never_writes_counter_history(client, env) -> None: ...
async def test_data_for_dashboard_not_on_link_404(client, env) -> None: ...
async def test_miss_rate_limit_per_ip_hash(client, env) -> None: ...            # 31st miss -> 429 with Retry-After; a valid token from another IP still works
async def test_redis_down_fails_closed(client, env) -> None: ...               # 503
async def test_headers_no_store_and_no_referrer_and_no_cookie_needed(client, env) -> None: ...
async def test_last_used_at_updated_at_most_every_ten_minutes(client, env) -> None: ...
async def test_cache_lock_single_computation(client, env, monkeypatch) -> None: ...   # two concurrent data calls -> run_batch called once
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E9. The router has no `CurrentUser` dependency and ignores cookies/bearer; the kiosk header is required (missing → same 404). Use `client_ip(request, settings.trusted_proxies)` (verify the settings name) and `hash_ip(ip, settings.secret_key)`. Session factory via `get_session_factory_dep`; the request session commits only the `last_used_at` update.
- [ ] **Step 4: Run tests** → PASS; lint gates; regenerate openapi.
- [ ] **Step 5: Commit** — `feat(dashboard): public wallboard API with token links and caching`.

---

### Task 12: API layer, types, demo and e2e mocks (frontend-only)

**Files:**
- Create: `frontend/src/lib/kioskApi.ts`, `frontend/src/lib/kioskApi.test.ts`, `frontend/src/demo/kioskMock.ts`, `frontend/src/dashboard/layout/firstFreeSpot.cases.test.ts` (reads `backend/tests/fixtures/first_free_spot_cases.json`, same import style as `layout/normalizeCases.json`)
- Modify: `frontend/src/lib/dashboardsApi.ts` (`patchFilters`, `templateUpdate`, `applyTemplateUpdate`, `drillTickets`, `startReportJob`, admin `kioskLinks.{list,create,remove}`; types `DashboardFilters`, `DrillPage`, `KioskLink`, `KioskLinkCreated`, `TemplateUpdate`), `frontend/src/demo/dashboardsMock.ts` (factories carry `filters`, `pinned`, `template_update`; demo data for every new widget; a demo template update; a demo drill page), `frontend/src/demo/handlers.ts` (bodies for the new PATCH/POST routes, R-DASH-DEMO-BODY), `frontend/e2e/fixtures/mock-api.ts` (stateful per test id, R-DASH-E2E-STORE)

**Interfaces:**
- Consumes: regenerated `schema.d.ts`.
- Produces: everything later frontend tasks import from `dashboardsApi`/`kioskApi`; demo widget data for `core.report` (a built-in "volume_by_queue" result), `core.sla-gauge` (87 %, target 90), `core.backlog-aging`, `core.heatmap`, `core.chat-queue` (3 waiting, oldest 4 min), `core.ai-status`.

- [ ] **Step 1: Write the failing tests** — `kioskApi.test.ts`: sends `X-Tiqora-Kiosk`, `credentials: "omit"`, no `Authorization`, maps 404 to `KioskLinkInvalidError`, 429/503 to a retryable error; `firstFreeSpot.cases.test.ts` runs the shared cases; `dashboardsApi` tests for `patchFilters` 409 → `DashboardConflictError`.
- [ ] **Step 2: Run to verify they fail** — `cd frontend && pnpm test --run src/lib/kioskApi.test.ts src/dashboard/layout/firstFreeSpot.cases.test.ts`.
- [ ] **Step 3: Implement.** No i18n keys in this task.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run` → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): API client, kiosk client and demo data for phase 2`.

---

### Task 13: Filter bar, `pinned` switch, filters context (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/filters/{FilterBar.tsx,FilterBar.test.tsx,filtersContext.tsx,timeRanges.ts}`
- Modify: `frontend/src/routes/agent/DashboardPage.tsx` (bar under the tab row; provider), `frontend/src/dashboard/chrome/WidgetFrame.tsx` (pin glyph), `frontend/src/dashboard/settings/SettingsDrawer.tsx` (switch; preview sends filters), `frontend/src/dashboard/edit/useEditSession.ts` + `layout/session.ts` (`pinned` in `EditWidget`, `{type: "pin"}` action with undo), `frontend/src/dashboard/definitions.ts` (`FILTER_AWARE_KEYS`), `frontend/src/dashboard/model.ts`, i18n `en.json`/`de.json` + propagate

**Interfaces:**
- Consumes: Task 12.
- Produces: E10 filters context; `EditWidget.pinned: boolean`.

- [ ] **Step 1: Write the failing tests**

```ts
it("owner changes the queue filter in view mode and the dashboard data refetches", ...)   // PATCH body {filters:{queue_ids:[3],time_range:null}, version}, then data query invalidated
it("read-only viewer sees active filter chips but no popover", ...)
it("filter bar is disabled in edit mode with the hint", ...)
it("409 on filter change shows the conflict message and reloads", ...)
it("pinned switch toggles and is undoable; pin glyph only while filters are active", ...)
it("settings preview sends the dashboard filters", ...)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E4 (German labels: "Queues", "Alle Queues", "Zeitraum", "Wie im Bericht", "Heute", "Letzte 7 Tage", "Letzte 30 Tage", "Letzte 90 Tage", "Dieser Monat", "Letzter Monat", "Übernehmen", "Zurücksetzen", "Dashboard-Filter auf dieses Widget anwenden", "Dashboard-Filter gelten hier nicht"). Demo check at 1280/390 px, light/dark.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): dashboard-wide queue and time filters with per-widget opt-out`.

---

### Task 14: Report widget, report settings editor, background job flow, "Berichte" group (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/widgets/ReportWidget.tsx`, `frontend/src/dashboard/settings/editors/ReportSettings.tsx`, tests next to them
- Modify: `frontend/src/dashboard/widgets/index.ts` (register `core.report`), `frontend/src/dashboard/definitions.ts` (`galleryGroupOf` → `reports`), `frontend/src/dashboard/gallery/{galleryModel.ts,GalleryPanel.tsx}` (group order + label "Berichte"), `frontend/src/dashboard/settings/registry.ts`, i18n + propagate

**Interfaces:**
- Consumes: `ReportView` (`compact`), `ReportParameterControls`, `reportsApi` catalogue, `useJob`, Task 12 `startReportJob`.
- Produces: report widget states: result / deferred-running / deferred-failed / 429 / forbidden / error; footnote for `ignored_filters`.

- [ ] **Step 1: Write the failing tests**

```ts
it("renders the report through ReportView in compact mode with the report's title", ...)
it("deferred result starts one background job per settings and filters, then refetches the widget on success", ...)
it("job cap 429 shows the message and 'Erneut versuchen'", ...)
it("shows 'Der Queue-Filter gilt für diesen Bericht nicht.' for ignored filters", ...)
it("settings editor lists built-in and saved reports and edits runtime parameters", ...)
it("gallery shows the 'Berichte' group only to users who may view reports", ...)   // canUseWidget with can_view_reports false -> hidden
it("in preview mode a deferred report shows the 'zu umfangreich für die Vorschau' text and starts no job", ...)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E6. A newly added `core.report` opens the settings drawer immediately (no `report_key` yet → the drawer's "Übernehmen" is disabled until a report is chosen). Texts: "Bericht", "Bericht wählen", "Darstellung", "Wie im Bericht", "Tabelle", "Anzahl Zeilen", "Wird im Hintergrund berechnet …", "Zu umfangreich für die Vorschau – wird nach dem Speichern im Hintergrund berechnet.".
- [ ] **Step 4: Run** frontend gates → PASS; demo check.
- [ ] **Step 5: Commit** — `feat(dashboard): report widget with report picker and background runs`.

---

### Task 15: Gauge chart; SLA gauge, backlog aging, heatmap widgets (frontend-only)

**Files:**
- Create: `frontend/src/components/agent/stats/GaugeChart.tsx` (+ test), `frontend/src/dashboard/widgets/{SlaGaugeWidget,BacklogAgingWidget,HeatmapWidget}.tsx` (+ tests)
- Modify: `frontend/src/dashboard/widgets/index.ts`, i18n + propagate

**Interfaces:**
- Consumes: Task 14 report data shape; `HeatmapChart`, `StackedBarChart`.
- Produces: `GaugeChart({ value: number | null; target?: number; label: string; formatValue?: (v) => string; testId? })` — semicircle arc (SVG, `role="img"`, `aria-label` "SLA-Erfüllung 87 %, Ziel 90 %"), mono value, target tick; colour `success` ≥ target, `warn` within 5 points below, `danger` below that; `null` → muted "Keine Tickets im Zeitraum".

- [ ] **Step 1: Write the failing tests** — gauge thresholds and aria label; SLA gauge shows "87 %" and "Ziel 90 %"; backlog aging renders the stacked bars with the age buckets' labels; heatmap renders 7 × 24 cells (weekday labels in the locale's order, Monday first for `de`); all three render the forbidden tile for users without report rights.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** following the dataviz rules already used in `components/agent/stats/*` (mono numerals, validated palette, both themes; follow skill `dataviz` for the gauge).
- [ ] **Step 4: Run** frontend gates → PASS; demo check light/dark.
- [ ] **Step 5: Commit** — `feat(dashboard): SLA gauge, backlog aging and created heatmap widgets`.

---

### Task 16: Queue overview from the server, team chat status, chat queue and AI status widgets (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/widgets/{ChatQueueWidget,AiStatusWidget}.tsx` (+ tests)
- Modify: `frontend/src/dashboard/widgets/{QueueOverviewWidget,TeamPresenceWidget,index.ts}.tsx`, `frontend/src/dashboard/widgets/widgets.test.tsx`, i18n + propagate

**Interfaces:**
- Consumes: E7 shapes.
- Produces: `core.queue-overview` reads `useWidgetData` (no more `api.listQueues()` in the widget); bars show open/new/escalated segments with a legend "offen · neu · eskaliert".

- [ ] **Step 1: Write the failing tests**

```ts
it("queue overview renders server data with an escalated segment and makes no queues request", ...)
it("team presence shows 'im Chat' for agents in a chat", ...)
it("chat queue shows waiting count, oldest wait and items; 'Web-Chat ist nicht eingerichtet.' when disabled", ...)
it("chat queue refreshes on chat_waiting / chat_assigned / chat_ended", ...)   // via subscribeSSE
it("AI status shows escalated count, runs today and budget bars; exhausted budget in danger tone", ...)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Texts: "Web-Chat", "warten", "wartet seit {{d}}", "Meine Chats", "KI-Status", "An Menschen übergeben", "KI-Läufe heute", "Budget", "aufgebraucht", "verfügbar", "im Chat".
- [ ] **Step 4: Run** frontend gates → PASS; demo check.
- [ ] **Step 5: Commit** — `feat(dashboard): chat queue and AI status widgets; queue overview with escalations`.

---

### Task 17: Drill-down page and entry points (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/drill/{drillLinks.ts,DrillPage.tsx}` (+ tests)
- Modify: `frontend/src/router.tsx` (`/agent/tickets/drill` with `validateSearch` for `d`, `w`, `point`, `sort`, `order`, `offset`), `frontend/src/dashboard/widgets/{CounterWidget,TicketListWidget,EscalationsWidget,RemindersWidget,ReportWidget,BacklogAgingWidget,HeatmapWidget}.tsx`, chart components `BarChart`, `StackedBarChart`, `HorizontalBarChart`, `DonutChart`, `HeatmapChart` (additive `onSelect?: (point) => void`), `frontend/src/components/reports/ReportView.tsx` (pass-through `onSelectPoint?`), `frontend/src/routes/agent/SavedSearchEditorPage.tsx` (initial criteria from `location.state`), i18n + propagate

**Interfaces:**
- Consumes: Task 12 `drillTickets`.
- Produces: `drillHref(dashboardId, widgetId, point?) -> { to: "/agent/tickets/drill"; search: {...} }`.

- [ ] **Step 1: Write the failing tests**

```ts
it("counter tile links to the drill page in view mode, not in edit/preview/kiosk", ...)   // replaces phase-1 'counter renders no link role'
it("ticket list footer 'Alle N anzeigen' always links to the drill page", ...)            // replaces R-DASH-LIST-MORE-LINK behaviour
it("drill page lists tickets with paging and shows the note", ...)
it("'Als Suche speichern' navigates with the filter in router state, never in the URL", ...)
it("'Als Widget hinzufügen' appends a ticket-list widget at the first free spot; 409 shows the message", ...)
it("buttons hidden when filter is null or the viewer cannot edit", ...)
it("chart bars are buttons only for drillable dimensions and are keyboard reachable", ...)
it("charts without onSelect render exactly as before", ...)                               // existing reports pages unchanged
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E5. Update the phase-1 tests that asserted "no link" (they encoded R-DASH-COUNTER-LINKS) and say so in the commit body. Texts: "Tickets zu „{{title}}“", "Als Suche speichern", "Als Widget hinzufügen", "Zurück zum Dashboard".
- [ ] **Step 4: Run** frontend gates → PASS; demo check 1280/390.
- [ ] **Step 5: Commit** — `feat(dashboard): drill down from numbers, lists and charts to the tickets behind them`.

---

### Task 18: Template update banner (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/template/TemplateUpdateBanner.tsx` (+ test)
- Modify: `frontend/src/routes/agent/DashboardPage.tsx`, i18n + propagate

**Interfaces:**
- Consumes: `DashboardOut.template_update`, Task 12 routes.

- [ ] **Step 1: Write the failing tests** — banner text with name and count for the owner; hidden for viewers and in edit mode; "Übernehmen" posts `merge` with the version and the new widgets appear; "Ausblenden" posts `dismiss`; 409 reloads and shows "Das Dashboard wurde inzwischen geändert."; 422 shows the 40-widget message.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** (`role="status"`, accent-tinted strip above the grid; texts "Neue Version der Vorlage „{{name}}“: {{count}} neue Widgets.", "Übernehmen", "Ausblenden").
- [ ] **Step 4: Run** frontend gates → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): template update banner with merge and dismiss`.

---

### Task 19: Wallboard view, wallboard widget styles, TV mode with rotation (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/wallboard/{WallboardView.tsx,WallboardHeader.tsx,fit.ts,useRotation.ts,kioskContext.tsx}` (+ tests), `frontend/src/routes/agent/WallboardPage.tsx` (+ test)
- Modify: `frontend/src/router.tsx` (`/agent/wallboard`, `validateSearch` `ids` = comma list of positive ints ≤ 10, `rotate` 15..900), `frontend/src/dashboard/chrome/mode.ts` (`rendersLinks(mode, kiosk)`), `frontend/src/dashboard/widgets/{CounterWidget,QueueOverviewWidget,TeamPresenceWidget,EscalationsWidget,TicketListWidget}.tsx` (wallboard styles), `frontend/src/dashboard/tabs/TabMenu.tsx` ("Als Wallboard anzeigen"), i18n + propagate

**Interfaces:**
- Consumes: `useDashboardData` (TV mode keeps SSE + intervals), E10/E11.
- Produces: `WallboardView` reused by Task 20.

- [ ] **Step 1: Write the failing tests**

```ts
it("wallboardRowHeight fits the rows into the viewport and clamps to 48..160", ...)
it("useRotation advances every N seconds, wraps, pauses while hidden, no timer for one dashboard", ...)   // fake timers
it("header shows name, 'aktualisiert vor 12 s', position 2 / 3 and a clock", ...)
it("stale data (> 3 intervals) shows 'Verbindung unterbrochen – Stand 10:41'", ...)
it("counter in wallboard mode: 96px mono value, no sparkline", ...)
it("escalations at h<=2 render the single 'Nächste Frist' banner", ...)
it("TV mode keeps rotating when one dashboard's data fails", ...)
it("reduced motion: no crossfade", ...)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** per E11. TV mode adds a small top-right control cluster visible on pointer move only ("Vollbild", "Beenden"); uses the Fullscreen API and `navigator.wakeLock` when available (ignore failures). Playwright demo check at 1920×1080 (dark forced) and 1280×720.
- [ ] **Step 4: Run** frontend gates → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): wallboard view and TV mode with rotation`.

---

### Task 20: Public kiosk page `/wallboard` (frontend-only)

**Files:**
- Create: `frontend/src/routes/KioskWallboardPage.tsx` (+ test), `frontend/src/dashboard/wallboard/useKioskData.ts` (+ test)
- Modify: `frontend/src/router.tsx` (top-level route `/wallboard` under the root route, no auth guard, outside the agent layout like `/login`), `frontend/src/dashboard/widgets/*` (kiosk fallbacks: recent views / plugin widgets / widgets without data → "Auf dem Wallboard nicht verfügbar"; links rendered as text), `frontend/src/demo/handlers.ts` (kiosk routes), i18n + propagate

**Interfaces:**
- Consumes: `kioskApi`, `WallboardView`, `KioskProvider`.
- Produces: kiosk page behaviour: token from `location.hash` (`#<token>`), board once per 10 minutes, data every `refresh_seconds` (30) for the current dashboard + prefetch of the next one 5 s before rotation, no SSE, no cookies, `<meta name="referrer" content="no-referrer">` while mounted.

- [ ] **Step 1: Write the failing tests**

```ts
it("reads the token from the hash and never puts it into a request URL", ...)        // fetch spy: URL has no token; header has it
it("invalid link shows 'Dieser Wallboard-Link ist ungültig oder abgelaufen.' and stops polling", ...)
it("429/503 keep the last picture, show the stale banner and retry with backoff", ...)
it("never navigates to /login on errors", ...)
it("plugin and recent-views widgets render 'Auf dem Wallboard nicht verfügbar'", ...)
it("a deferred report renders 'Dieser Bericht ist für das Wallboard zu umfangreich.'", ...)
it("no element is a link on the kiosk", ...)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Widgets that still call authenticated APIs must be fixed or fall back (grep `frontend/src/dashboard/widgets` for `useQuery`/`api.`; the kiosk page wraps its tree in a `QueryClient` whose default `queryFn` throws, so a leftover becomes a visible error tile, never a login redirect).
- [ ] **Step 4: Run** frontend gates → PASS; Playwright demo check `/wallboard#demo-token` at 1920×1080.
- [ ] **Step 5: Commit** — `feat(dashboard): public wallboard page for screens without a login`.

---

### Task 21: Wallboard-link management — dialog and admin page (frontend-only)

**Files:**
- Create: `frontend/src/dashboard/kiosk/{KioskLinksDialog.tsx,KioskLinkForm.tsx}` (+ tests), `frontend/src/routes/admin/DashboardKioskLinksPage.tsx` (+ test), `frontend/src/lib/adminSearch.test.ts` (extend)
- Modify: `frontend/src/dashboard/tabs/TabMenu.tsx` ("Wallboard-Link erstellen …" for admins), `frontend/src/router.tsx` (admin route `/admin/dashboard-kiosk-links`), `frontend/src/lib/adminSearch.ts` (one entry "Wallboard-Links"), `AdminShell.test.tsx`/`AdminHomePage.test.tsx` counts if they assert `ADMIN_PAGES` length, i18n + propagate

**Interfaces:**
- Consumes: Task 12 admin API.

- [ ] **Step 1: Write the failing tests** — form defaults (current dashboard preselected, rotation 60 s, expiry 90 days, details off) and bounds; created link shows the URL once with "Kopieren" (clipboard API may be missing on http LAN pages — skill `clipboard-api-missing-on-http-lan-pages`: fall back to a selected read-only input) and the rights note; the "zu hoch für ein Wallboard" warning when a chosen dashboard has more than 14 rows; admin page lists active first, "Widerrufen" then "Löschen" with confirmation; non-admins see neither menu item nor route.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Texts: "Wallboard-Link erstellen", "Name", "Dashboards", "Wechsel alle", "Sekunden", "Gültig für", "Tage", "Details anzeigen (Tickettitel, Kunden, Namen)", "Der Link zeigt die Daten so, wie Sie sie sehen. Wer den Link hat, sieht das Wallboard ohne Anmeldung.", "Dieser Link wird nur jetzt angezeigt.", "Widerrufen", "Löschen", "zuletzt verwendet", "läuft ab".
- [ ] **Step 4: Run** frontend gates → PASS; demo check 1280/390 light/dark.
- [ ] **Step 5: Commit** — `feat(dashboard): create, list and revoke wallboard links`.

---

### Task 22: End-to-end flows, screenshots, documentation, full verification

**Files:**
- Create: `frontend/e2e/dashboard-phase2.spec.ts`
- Modify: `frontend/e2e/screenshots.spec.ts` (wallboard 1920×1080 dark; dashboard with filter bar and a report widget, light + dark), `docs/dashboards.md` (filters, report widgets, drill-down, template updates, wallboard and links incl. rights and privacy note, limits), `docs/architecture.md` (dashboards section: filters, report seam use, kiosk API, cache and rate limits, redaction whitelist), `docs/compatibility.md` (row stays partial; note extended; "Missing: customer-info widget, RSS"), `docs/deployment.md` (wallboard links need Redis; nothing to configure; nginx needs no change because the token is a fragment + header), `docs/api/openapi.json` (already regenerated)

- [ ] **Step 1: Write the e2e specs** (demo/mock API, no `toHaveScreenshot` — R-DASH-E2E-VISUAL; layout invariants + `page.screenshot` artefacts): set a queue filter and see a counter change; add a report widget from the "Berichte" group and choose a report; click a counter → drill page → "Als Suche speichern" lands on the editor with conditions filled; template update banner → "Übernehmen" adds a widget; TV mode rotates between two dashboards (fake clock); kiosk page with a valid demo token renders the 4 KPI tiles at `gridColumn: span 3` and no `a` element; invalid token shows the message.
- [ ] **Step 2: Run** `cd frontend && CI=1 pnpm exec playwright test e2e/dashboard-phase2.spec.ts` (port 4173 free first — skill `playwright-webserver-adopts-foreign-server`) → PASS.
- [ ] **Step 3: Documentation** as listed (German user guide sections, English summary at the top stays updated; Sie-form).
- [ ] **Step 4: Full verification** — backend full suite with `TIQORA_STRICT_DB_LEAKS=1`, all three lint gates, `alembic heads` single head; frontend `pnpm install --frozen-lockfile`, api-client build, `pnpm lint`, `pnpm vitest run`, `pnpm build`, `node scripts/check-i18n-keys.mjs`, `check-plugin-runtime.mjs`; Playwright full e2e; screenshots spec with its env var; delete `.playwright-mcp`, `test-results/`.
- [ ] **Step 5: Commit** — `docs(dashboard): phase 2 user guide, architecture and compatibility; e2e flows`.

---

## Self-review notes (for the executor)

- **Spec coverage (phase-2 row):** report-based widgets → Tasks 6, 7, 14, 15 (catalogue: report chart/table, SLA gauge, backlog aging, heatmap); AI status/budget and chat queue → Tasks 5, 16; process overview → Deviation 5; dashboard filters → Tasks 3, 4, 13; drill-down → Tasks 9, 17; wallboard + kiosk links (fullscreen, larger type, rotation, read-only kiosk link with its own token, creator rights stated in the UI) → Tasks 1, 10, 11, 19, 20, 21; template update merge → Tasks 8, 18; spec tests "kiosk permission tests", "template update merge", "wallboard rotation" → Tasks 11, 8, 19, 22.
- **Compatibility row "Missing: stats/report widgets (phase 2)"** is closed by Tasks 6–7/14–15; the row stays partial for customer-info widget and RSS.
- **Phase-1 rulings superseded:** R-DASH-COUNTER-LINKS, R-DASH-LIST-MORE-LINK (Deviation 4). All others (R-DASH-RUNNER-POOL budgets and global slots, R-DASH-APIKEY, R-DASH-SAMPLES-PREVIEW, R-DASH-WIDGETS-PARAM, R-DASH-SSE-LOAD, R-DASH-TS-FIXTURES, R-DASH-DEMO-BODY, R-DASH-E2E-STORE, R-DASH-E2E-VISUAL, R-GERMAN-SIE) apply unchanged.
- **Things an implementer must verify, not assume:** `_filtered_ticket_stmt`'s `state_type` semantics (Task 9), `sla_met_rate` unit (Task 7), `QueryBudget` field order and timeout composition (Task 6), how the web chat module signals "configured" (Task 5), the AI usage table's columns and limit `kind` strings (Task 5), `client_ip`'s trusted-proxy setting name (Task 11), `copy_from` source fields (Task 8), `TimeRange` `include_current` semantics (Task 2). Each is named in its task; stop with NEEDS_CONTEXT rather than guess.
- **Load:** every new provider is bounded (COUNT/GROUP BY/LIMIT, report runs under `QueryBudget` + estimate → job); kiosk data is cached 20 s per link and dashboard version and computed under a lock; TV mode reuses the phase-1 SSE spacing (R-DASH-SSE-LOAD).
