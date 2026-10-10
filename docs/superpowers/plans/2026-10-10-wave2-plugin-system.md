# Wave 2 — Plugin System Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the wave-0 internal extension registry into a plugin-facing system: Python plugins installed into the image are discovered by entry point, checked against a manifest, registered atomically through a staging registrar, and can contribute settings (generated admin form), HTTP routes, async ticket-event subscribers, scheduled tasks and job handlers, and MCP tools; their ES-module frontends fill four slots (sidebar entry + page, ticket panel, dashboard element); admins see and switch them on an "Erweiterungen" page; safe mode, import timeouts and crash quarantine keep a broken plugin from blocking startup or login; `tiqora plugins bake` produces a reproducible image with plugins.

**Architecture:** New backend package `tiqora.plugins` (manifest, no-import discovery, compatibility, staging registrar, loader with daemon-thread timeout, DB state + bootstrap, context, routes dispatcher, events, tasks, MCP binding, bake) plus a thin public facade package `tiqora_sdk` inside the same wheel. Every process that hosts extension points (api, worker, mcp) builds its registry with `await init_registry(role=…)` at startup: core first, then enabled plugins, then `freeze()`. Plugin routes live in one Starlette `Router` mounted at `/api/plugins` and filled after the registry is built; event subscribers follow `ticket_history` (Znuny and Tiqora writes alike) in a new worker loop; scheduled tasks run in a second worker loop. The frontend gets runtime shim modules at `/plugin-runtime/*.js` (re-exports of the SPA's own React/Query/Router/i18n/SDK chunks — no import map, no CSP change), a `PluginProvider` in the agent and admin shells, lazy per-slot loading with error boundaries, and the new workspace package `@tiqora/plugin-sdk` (types + Vite preset).

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLAlchemy 2 async, Alembic two-chain, Pydantic v2, `importlib.metadata` entry points, `tomllib`, `packaging` (version specifiers), FastMCP (4.0.5 in `uv.lock`), structlog; React 19, TanStack Router/Query, i18next, Vite 6 multi-entry build, Vitest + Testing Library, MSW demo mode, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-plugin-system-design.md` — phase 1 row of "Phases" (registry + SDK skeletons, manifest, entry-point loading, settings schema → admin form, async event subscribers, routes, worker jobs, MCP tools, frontend loader + slots nav/route, ticket sidebar, dashboard widget, plugin admin page, safe mode, baked image). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (principles, definition of done). Builds on `docs/superpowers/plans/2026-10-08-wave0-foundations.md` D1 (registry), D2 (settings sections), D3 (jobs) and D7 (settings frontend) — the names below are the ones that exist in code today (verified 2026-10-10), which differ from the wave-0 plan text in two point keys (`core.settings_sections`, `core.job_handlers`). Product decisions 2026-10-10: plugins come from the site's own repositories only for now; a marketplace must stay a pure add-on later (the repository index of phase 2 is marketplace-capable, so the phase-1 manifest already carries the marketplace fields).

## Global Constraints

- Work in the worktree `<repo>` on the local branch `roadmap-wave1`; never touch `<repo>`; never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data (example.org, .test). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk`. New DB test modules clean up their rows (pattern: `_seed_statements` + module-scoped autouse fixture, `backend/tests/test_ai_refine.py`) and never get added to `backend/tests/db_leak_baseline.txt`. Seed ids only inside this plan's band **49300–49699** (sub-bands per task below); confirm free with `grep -rn "49[3-6][0-9][0-9]" backend/tests` before use (current hits are phone numbers and a hash, not ids). Logins/names contain `plg49`.
- DB tests that touch SQL run on BOTH dialects: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` (pattern and `_to_async_url`: `backend/tests/test_admin_api.py`), and `TiqoraBase.metadata.create_all` for the new tables.
- Migrations: Alembic two-chain setup; the new migration goes into `backend/alembic/versions_tiqora`; `down_revision` = the CURRENT primary head at implementation time (today `20261009_0067`; check with `ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — never trust the id written here); follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version == …` assert in `backend/tests/test_migrate_cli.py`. Tables only `tiqora_*`; never change Znuny DDL; writes to Znuny tables must be behaviourally identical to Znuny (this plan writes none).
- ORM models in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL as the migration (`backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB AND PostgreSQL must both work. Prod MariaDB mixes collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list. (Integer joins between Znuny tables, e.g. `ticket_history.history_type_id = ticket_history_type.id`, are fine.)
- Znuny SysConfig/YAML checkbox values are Perl-truthy strings ('0' is false): always read them with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- Timestamps this plan writes (`tiqora_plugin*` columns) are naive UTC from `tiqora.jobs.service.utc_now()`; never compare against `func.now()`/`current_timestamp` (skill `db-session-timezone-vs-app-written-rows`). API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- Long-running endpoints (> a few seconds possible) use the wave-0 job system (prod nginx 90 s timeout). Job results that contain scoped data are `result_private`. (No endpoint in this plan is long-running; plugin authors are told the same rule in the docs.)
- After any API model/route change: regenerate both openapi files and schema.d.ts with `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build` (skill `tiqora-openapi-regen-or-ci-wipes-schema`); check `head -c 40 packages/api-client/openapi.json` starts `{"components"` and `rtk git diff --stat packages/api-client/openapi.json` is tens/hundreds of lines; never hand-edit `schema.d.ts`. A Pydantic response field WITH a default becomes required in the generated TS type — fixtures and demo mocks must include it. Plugin routes never appear in the core OpenAPI document (they live on per-plugin sub-apps).
- Frontend: `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). New screens get demo-mode mocks (`frontend/src/demo/mockData.ts` `resolveData`; `VITE_DEMO=1`) and are checked in demo mode with Playwright (delete `.playwright-mcp` before committing). Tailwind colour tokens only via `themeColor()` (`frontend/tailwind.config.js`, tokens in `frontend/src/themes/tokens.css`). API wrappers are hand-written `frontend/src/lib/*Api.ts` files using `api.request<T>(method, path, {query, body, signal})` with `Schemas["…"]` types (pattern `frontend/src/lib/phoneApi.ts`); error text via `apiErrorMessage` (`frontend/src/lib/avatarApi.ts`).
- Every endpoint the agent/admin shells call on every page must also be answered in `frontend/e2e/fixtures/mock-api.ts` (e2e runs only on release tags; an unmocked shell call turns the tag pipeline red — wave-1 ruling R-E2E).
- UI copy German first, plain words, named by what admins/agents recognise; no system jargon in UI text ("Erweiterung", not "Plugin"; "Neustart nötig", not "restart_required"; "Hintergrunddienst", not "Worker").
- Znuny reference source (6.5 core) at `<repo>/znuny-6.5.22/` (read-only) for behaviour checks.
- `tiqora_sdk` and `@tiqora/plugin-sdk` are the only plugin-facing API. Anything a plugin needs that is not in `tiqora_sdk.__all__` / the SDK `index.ts` is out of scope for phase 1 — do not export internals "because the fixture needs it"; extend the SDK deliberately (surface snapshot tests in Task 1 and Task 13 fail on any unplanned change).

## Review Focus

1. **A plugin whose import hangs, whose `register()` raises after it already added items, or which kills the process during import** → core still boots, login works; the hanging/raising plugin is marked "Fehler beim Laden" with no leftover registrations; a plugin that crashed the process three boots in a row is skipped ("Beim Start wiederholt abgestürzt") until an admin retries. Pinned in Task 3 (`test_register_raises_leaves_no_items`, `test_import_timeout_reports_failure`) and Task 4 (`test_crash_loop_quarantines_after_three_boots`, `test_db_unavailable_loads_no_plugins_core_boots`).
2. **A plugin registering outside its own namespace** — a job kind `core.ticket_export`, an MCP tool named like a core tool, another plugin's settings key, an extension point not declared in its manifest → that plugin fails to load with a plain reason; core items and other plugins are untouched. Pinned in Task 3 (`test_namespace_rules_reject`, `test_undeclared_point_rejected`, `test_duplicate_with_core_rejected_core_kept`, `test_mcp_tool_name_collision_with_core_rejected`) and Task 9 (`test_core_names_constant_matches_server`).
3. **A subscriber that keeps failing or timing out** → it is stopped after 5 consecutive failures ("angehalten"), other subscribers keep receiving events, and after "Erneut versuchen" it continues WITH the event that failed (nothing lost). Pinned in Task 7 (`test_failing_subscriber_trips_others_continue`, `test_reset_resumes_at_failed_event`, `test_timeout_counts_as_failure`).
4. **A cookie-authenticated cross-site POST to a plugin route, an anonymous request to a non-public plugin route, or any request to a disabled/unknown plugin** → 403 / 401 / 404 and the plugin handler never runs. Pinned in Task 5 (`test_cross_site_post_rejected`, `test_anonymous_rejected_unless_public`, `test_unknown_or_unloaded_plugin_404`).
5. **A plugin frontend bundle that 404s, never resolves, or throws while rendering** → only that slot shows "Erweiterung „…“ konnte nicht geladen werden"; sidebar, ticket view and the other plugins keep working; `?plugins=off` loads no plugin code at all for the browser session. Pinned in Task 13 (`loader times out`, `PluginBoundary contains a render error`, `safe mode skips fetch and import`).

Also pinned (not top five): a `ticket_history` id gap (rolled-back insert, Galera `auto_increment_increment`) stalls delivery at most `TIQORA_PLUGIN_EVENTS_GAP_HOLD_SECONDS` and a row that commits late inside that window is still delivered in id order (Task 6 `GapTracker` tests, Task 7 `test_late_commit_within_hold_delivered_in_order`); a plugin wheel that lost its `web/` files to `.gitignore` fails `tiqora plugins verify` at bake time (Task 11 `test_verify_fails_on_missing_web_entry`, Task 16 Step 6).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Wave-0/1 names are used exactly as they exist in code: `Registry`, `ExtensionPoint`, `Registration`, `DuplicateExtensionError`, `RegistryFrozenError`, `get_registry`, `register_core`, `set_registry_for_tests` (`tiqora.extensions`); `SettingsSection`, `SETTINGS_SECTIONS` (key `core.settings_sections`), `validate_section_schema`, `UnsupportedSettingsSchema`, `SECRET_MASK` (`tiqora.settings_sections.section`); `load_section_values`, `read_section_for_admin`, `save_section_values`, `SettingsValidationError` (`tiqora.settings_sections.store`); `JobHandler`, `JOB_HANDLERS` (key `core.job_handlers`), `JobContext`, `JobRetry`, `enqueue_job`, `utc_now` (`tiqora.jobs.*`); `DaemonService`, `DAEMON_SERVICES` (`tiqora.worker.services`); `_interval_loop` (`tiqora.worker.__main__`); `PermissionEngine.is_admin`; `pin_outbound_url`, `OutboundURLError` (`tiqora.security.outbound`); `CurrentUser`, `DbSession`, `get_current_user` (`tiqora.api.deps`), `AdminUser`, `get_admin_user` (`tiqora.api.v1.admin.deps`); `createExtensionPoint` (`frontend/src/extensions/registry.ts`); `SchemaForm` (`frontend/src/components/fields/SchemaForm.tsx`); `SettingsSectionPage`; `ADMIN_PAGES` (`frontend/src/lib/adminSearch.ts`).

### P0. File map

Backend (new unless marked "mod"):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora_sdk/__init__.py` | public Python SDK facade: `SDK_VERSION`, re-exports (P3) |
| `backend/src/tiqora_sdk/mcp.py` | `current_agent_id`, `mcp_session_factory`, `permission_engine` helpers for plugin MCP tools |
| `backend/src/tiqora/plugins/__init__.py` | docstring only |
| `backend/src/tiqora/plugins/manifest.py` | `PluginManifest`, `Capabilities`, `WebManifest`, contribution models, `parse_manifest`, `parse_web_manifest`, `ManifestError` |
| `backend/src/tiqora/plugins/discovery.py` | `DiscoveredPlugin`, `discover_plugins`, `discovered_plugins` (cached), `clear_discovery_cache`, `plugin_asset_path` |
| `backend/src/tiqora/plugins/compat.py` | `release_core_version`, `check_compat` |
| `backend/src/tiqora/plugins/registrar.py` | `PluginRegistrar` (staging), `RegistrationRejected`, namespace rules |
| `backend/src/tiqora/plugins/loader.py` | `PluginLoadTimeout`, `run_in_daemon_thread`, `import_and_register`, `commit_registrar`, `dependency_order` |
| `backend/src/tiqora/plugins/state.py` | `RuntimeState`, `PluginRole`, `PluginLoadResult`, `loaded_plugins`, `set_loaded_plugins`, `summarize_status` |
| `backend/src/tiqora/plugins/store.py` | DB access for `tiqora_plugin`, `tiqora_plugin_runtime`, `tiqora_plugin_hook` |
| `backend/src/tiqora/plugins/bootstrap.py` | `init_registry` (the only startup entry) |
| `backend/src/tiqora/plugins/context.py` | `PluginContext`, `PluginHttp`, `OutboundHostNotAllowed`, `PluginTickets`, `TicketSummary` |
| `backend/src/tiqora/plugins/routes.py` | `PluginRoute`, `PLUGIN_ROUTES`, `create_plugin_router`, `mount_plugin_routes` |
| `backend/src/tiqora/plugins/events.py` | `TicketEvent`, `EventContext`, `EventSubscriber`, `EVENT_SUBSCRIBERS`, `HISTORY_EVENT_MAP`, `events_from_history`, `GapTracker` |
| `backend/src/tiqora/plugins/event_dispatch.py` | `run_plugin_events_tick` |
| `backend/src/tiqora/plugins/tasks.py` | `Schedule`, `ScheduledTask`, `SCHEDULED_TASKS`, `TaskContext`, `next_due`, `run_plugin_tasks_tick` |
| `backend/src/tiqora/plugins/mcp.py` | `McpTool`, `MCP_TOOLS`, `register_plugin_mcp_tools` |
| `backend/src/tiqora/plugins/frontend.py` | `frontend_plugins_for(user_is_admin)` (builds the frontend list) |
| `backend/src/tiqora/plugins/bake.py` | `PluginsTxt`, `parse_plugins_txt`, `render_dockerfile`, `core_constraints`, `verify_installed_plugins` |
| `backend/src/tiqora/util/cron.py` | `CronExpr` (5-field, UTC) |
| `backend/src/tiqora/db/id_step.py` | `autoincrement_step(factory)` (moved out of `reporting/incremental.py` `_id_step`) |
| `backend/src/tiqora/api/v1/plugins.py` | `GET /api/v1/plugins/frontend`, `GET /api/v1/plugins/{plugin_id}/assets/{version}/{path:path}` |
| `backend/src/tiqora/api/v1/admin/plugins.py` | `/api/v1/admin/plugins` |
| `backend/src/tiqora/cli/plugins.py` | `tiqora plugins list|verify|constraints|bake` |
| `backend/alembic/versions_tiqora/<date>_<nnnn>_plugins.py` | `tiqora_plugin`, `tiqora_plugin_runtime`, `tiqora_plugin_hook` |
| mod `backend/src/tiqora/extensions/core.py` | `install_registry` |
| mod `backend/src/tiqora/jobs/registry.py`, `settings_sections/section.py` | `sdk_stable=True` on `JOB_HANDLERS`, `SETTINGS_SECTIONS` |
| mod `backend/src/tiqora/api/app.py` | lifespan uses `init_registry(role="api")`; `/api/plugins` mount |
| mod `backend/src/tiqora/security/csrf.py` | `/api/plugins` in `_PROTECTED_PREFIXES` |
| mod `backend/src/tiqora/worker/__main__.py`, `worker/services.py` | `init_registry(role="worker")`; loops `plugin_events`, `plugin_tasks` |
| mod `backend/src/tiqora/mcp_server/server.py` | `_app_lifespan` uses `init_registry(role="mcp")` + `register_plugin_mcp_tools` |
| mod `backend/src/tiqora/config.py` | `plugins_*` settings (P5) |
| mod `backend/src/tiqora/main.py` | `add_plugins_subparser` |
| mod `backend/src/tiqora/reporting/incremental.py` | imports `autoincrement_step` |
| mod `backend/pyproject.toml` | `packaging` dependency; wheel packages `src/tiqora_sdk`; mypy `packages` |
| mod `Dockerfile`, `docker/entrypoint.sh` | `core-constraints.txt`; `tiqora` passthrough role |
| `backend/tests/plugins/fixtures/tq_fixture_hello/…` | complete fixture plugin (all phase-1 points + web + i18n) |
| `backend/tests/plugins/fixtures/tq_fixture_broken/…`, `tq_fixture_slow/…`, `tq_fixture_undeclared/…` | failure fixtures |
| `backend/tests/plugins/helpers.py` | `install_fixture_plugin` (writes a real `.dist-info`, no pip) |

Frontend / packages (new unless "mod"):

| Path | Responsibility |
|---|---|
| `packages/plugin-sdk/package.json`, `tsconfig.json`, `README.md` | `@tiqora/plugin-sdk` (private workspace package, source exports) |
| `packages/plugin-sdk/src/index.ts` | SDK types + ambient runtime declarations (P12) |
| `packages/plugin-sdk/src/shared.ts` | `PLUGIN_RUNTIME_PATH`, `PLUGIN_SHARED_MODULES` |
| `packages/plugin-sdk/src/vite.ts` | `tiqoraPluginConfig` (Vite preset) |
| `packages/plugin-sdk/test-plugin/…` | tiny TSX plugin built with the preset by `pnpm --filter @tiqora/plugin-sdk check:preset` |
| `frontend/src/plugins/runtime/{react,react-jsx-runtime,react-dom,react-query,react-router,react-i18next,sdk}.ts` | shim entry modules → `dist/plugin-runtime/*.js` |
| `frontend/src/plugins/sdkRuntime.tsx` | host implementation of the SDK runtime |
| `frontend/src/plugins/safeMode.ts` | `isPluginSafeMode`, `applySafeModeFromUrl` |
| `frontend/src/plugins/loader.ts` | `loadPluginModule`, `PLUGIN_IMPORT_TIMEOUT_MS`, `setPluginImporterForTests` |
| `frontend/src/plugins/i18n.ts` | `pluginNamespace`, `ensurePluginI18n` |
| `frontend/src/plugins/PluginProvider.tsx` | `PluginProvider`, `usePlugins`, `usePluginContributions` |
| `frontend/src/plugins/PluginBoundary.tsx` | per-slot error boundary + suspense |
| `frontend/src/plugins/PluginComponent.tsx` | lazy component by name |
| `frontend/src/plugins/slots.tsx` | `PluginNavItems`, `PluginTicketPanels`, `PluginWidgetsSection` |
| `frontend/src/plugins/dashboardWidgets.ts` | `WidgetDefinition`, `DASHBOARD_WIDGETS`, `registerPluginWidgets`, `useDashboardWidgetDefinitions` |
| `frontend/src/routes/agent/PluginRoutePage.tsx` | `/agent/plugins/$pluginId/$` |
| `frontend/src/routes/admin/PluginsPage.tsx` | `/admin/plugins` |
| `frontend/src/lib/pluginsApi.ts` | `pluginsApi` (frontend list, admin list/enable/reset) |
| `frontend/src/demo/demoPlugin.tsx` | in-tree demo plugin module for `VITE_DEMO=1` |
| mod `frontend/vite.config.ts` | multi-entry runtime shims + dev rewrite |
| mod `frontend/package.json` | `@tiqora/plugin-sdk: workspace:*` |
| mod `frontend/src/router.tsx` | plugin route + admin plugins route |
| mod `frontend/src/components/layout/AgentShell.tsx`, `AdminShell.tsx` | `PluginProvider`; nav items |
| mod `frontend/src/routes/agent/TicketZoomPage.tsx` | `PluginTicketPanels` after `ProcessWidget` |
| mod `frontend/src/routes/agent/DashboardPage.tsx` | `PluginWidgetsSection` at the end |
| mod `frontend/src/routes/admin/SettingsSectionPage.tsx` | loads the plugin's i18n for `plugins.*` sections |
| mod `frontend/src/lib/adminSearch.ts` | `plugins` admin page entry |
| mod `frontend/src/demo/mockData.ts`, `frontend/e2e/fixtures/mock-api.ts` | mocks |

### P1. Manifest — `tiqora-plugin.toml` and `web/manifest.json`

`tiqora-plugin.toml` lives inside the plugin's Python package directory (next to its `__init__.py`). Fields (TOML, `manifest_version = 1`):

```toml
manifest_version = 1
id = "com.example.hello"            # reverse-DNS, ^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$, ≤ 100 chars, no hyphens
short_id = "hello"                   # ^[a-z][a-z0-9]{1,15}$; MCP tool prefix now, tqp_<short_id>_ tables in phase 2
name = "Hallo-Erweiterung"
version = "1.0.0"                    # PEP 440 / semver; must equal the wheel's distribution version
publisher = "Example GmbH"
publisher_id = "com.example"         # optional; reverse-DNS; marketplace/repository key in phase 2
description = "Zeigt eine Begrüßung in der Ticketansicht."
license = "MIT"                      # optional
homepage = "https://example.org/hello"     # optional
support = "support@example.org"           # optional
categories = ["tickets"]             # optional, free strings ≤ 40 chars each
entry = "tq_fixture_hello:plugin"    # must equal the entry point value
sdk = ">=0.1,<0.2"                   # required PEP 440 specifier against tiqora_sdk.SDK_VERSION
tiqora = ">=0.18"                    # optional PEP 440 specifier against the release version (P5 compat)

[requires]                           # optional: other plugin ids -> version specifier
# "com.example.base" = ">=1.0"

[capabilities]
extension_points = ["core.settings_sections", "core.http.routes", "core.events.subscribers",
                    "core.worker.scheduled_tasks", "core.job_handlers", "core.mcp.tools"]
outbound_hosts = ["api.example.org", "*.example.net"]   # PluginHttp allowlist
frontend_slots = ["agent.nav", "agent.route", "ticket.sidebar", "dashboard.widget"]
```

```python
# backend/src/tiqora/plugins/manifest.py
PLUGIN_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
SHORT_ID_RE: Final = re.compile(r"^[a-z][a-z0-9]{1,15}$")
FRONTEND_SLOTS: Final = ("agent.nav", "agent.route", "ticket.sidebar", "dashboard.widget")

class ManifestError(ValueError): ...      # message is German, shown to admins

class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension_points: list[str] = []
    outbound_hosts: list[str] = []        # "host" or "*.suffix"; lower-cased; no scheme/port
    frontend_slots: list[str] = []        # unknown values are kept (forward compat) but never rendered

class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_version: Literal[1]
    id: str; short_id: str; name: str; version: str; publisher: str
    publisher_id: str | None = None; description: str = ""; license: str | None = None
    homepage: str | None = None; support: str | None = None; categories: list[str] = []
    entry: str; sdk: str; tiqora: str | None = None
    requires: dict[str, str] = {}
    capabilities: Capabilities = Capabilities()
    # validators: id/short_id regex, version parses as packaging Version, sdk/tiqora/requires values parse as
    # SpecifierSet, entry matches ^[A-Za-z_][\w.]*:[A-Za-z_]\w*$, name 1..80 chars

def parse_manifest(text: str) -> PluginManifest     # tomllib + model_validate; any error -> ManifestError("tiqora-plugin.toml: <reason>")
```

`web/manifest.json` (optional; required as soon as `capabilities.frontend_slots` is non-empty):

```python
class NavContribution(BaseModel):          # slot "agent.nav"
    slot: Literal["agent.nav"]; key: str; route: str          # key of an agent.route contribution of the same plugin
    label_key: str; icon: str = "puzzle"
    group: Literal["workspace", "knowledge", "insights", "plugins"] = "plugins"
    order: int = 100; admin_only: bool = False
class RouteContribution(BaseModel):        # slot "agent.route"
    slot: Literal["agent.route"]; key: str
    path: str                              # ^[a-z0-9-]+(/[a-z0-9-]+)*$ -> /agent/plugins/<id>/<path>
    component: str; title_key: str; admin_only: bool = False
class TicketSidebarContribution(BaseModel):  # slot "ticket.sidebar"
    slot: Literal["ticket.sidebar"]; key: str; component: str; title_key: str; order: int = 100
class WidgetSize(BaseModel):
    default: tuple[int, int] = (4, 3); min: tuple[int, int] = (2, 2); max: tuple[int, int] = (12, 8)
class DashboardWidgetContribution(BaseModel):  # slot "dashboard.widget" (P13)
    slot: Literal["dashboard.widget"]; key: str; component: str; version: int = 1
    title_key: str; description_key: str | None = None
    category: Literal["tickets", "reports", "team", "ai", "itsm", "other"] = "other"
    icon: str = "puzzle"; size: WidgetSize = WidgetSize()
    settings_schema: dict[str, Any] | None = None   # must pass the settings-section JSON-schema subset (frontend validates on render)
    refresh_on: list[str] = []; permission: str | None = None
FrontendContribution = Annotated[NavContribution | RouteContribution | TicketSidebarContribution
                                 | DashboardWidgetContribution, Field(discriminator="slot")]

class WebManifest(BaseModel):
    manifest_version: Literal[1]
    entry: str = "index.js"                 # relative to web/, ^[A-Za-z0-9_./-]+\.js$, no ".."
    styles: list[str] = []                  # relative .css files
    contributions: list[FrontendContribution] = []

def parse_web_manifest(raw: bytes, capabilities: Capabilities) -> WebManifest
    # entries with an unknown slot are dropped with a warning (newer plugin on older core);
    # a contribution whose slot is not in capabilities.frontend_slots -> ManifestError;
    # keys unique per slot; every nav.route names an existing route key; route paths unique
```

### P2. Wheel layout and no-import discovery

A plugin is a normal wheel with one Python package (`tq_fixture_hello/` in the fixture) that contains `tiqora-plugin.toml`, optional `web/` (built ES modules + `manifest.json` + CSS) and optional `i18n/<lang>.json` (`de.json`, `en.json`, … — same JSON shape as core locale files, keys the plugin chooses). Entry point group `tiqora.plugins`, entry point **name = plugin id**, value = `module:attr` (equal to manifest `entry`).

**Hatch trap (memory "Hatch-Wheel verliert ignorierte Datenfiles"):** hatch drops files matched by `.gitignore` from wheels. Built `web/` output is usually git-ignored, so plugin `pyproject.toml` files MUST declare `[tool.hatch.build.targets.wheel] artifacts = ["src/<pkg>/web/**", "src/<pkg>/i18n/**"]`, and nothing a plugin needs may be named `*.pem`/`*.key`/`dist/`/`build/`/`lib/` in this repo's ignore list. `tiqora plugins verify` (Task 11) fails when a declared file is missing from the installed distribution, so a stripped wheel is caught at bake time.

```python
# backend/src/tiqora/plugins/discovery.py
ENTRY_POINT_GROUP: Final = "tiqora.plugins"

@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    plugin_id: str                 # entry point name
    entry: str                     # entry point value "module:attr"
    dist_name: str
    dist_version: str
    package_dir: Path | None       # directory that holds tiqora-plugin.toml (None if not found)
    manifest: PluginManifest | None
    web_manifest: WebManifest | None
    locales: tuple[str, ...]       # from i18n/*.json, sorted
    problem: str | None            # German reason when the package is unusable (missing/invalid manifest,
                                   # id/version/entry mismatch, missing web entry/styles); None when fine

def discover_plugins(entry_points: Iterable[EntryPoint] | None = None) -> list[DiscoveredPlugin]
    # never imports plugin code: uses EntryPoint.dist.files + dist.locate_file();
    # exactly one file named "tiqora-plugin.toml" whose parent dir is a top-level package of the dist;
    # duplicate plugin ids or short_ids across distributions -> problem on every duplicate;
    # sorted by plugin_id
@functools.cache-backed:
def discovered_plugins() -> tuple[DiscoveredPlugin, ...]
def clear_discovery_cache() -> None
def plugin_asset_path(plugin: DiscoveredPlugin, rel: str) -> Path | None
    # only "web/…" and "i18n/<lang>.json"; rejects "..", absolute, backslashes, symlinks leaving package_dir;
    # returns None if the file does not exist
```

Test seam (`backend/tests/plugins/helpers.py`): `install_fixture_plugin(tmp_path, monkeypatch, package: str, *, plugin_id: str, version: str = "1.0.0", dist_name: str | None = None) -> Path` copies `backend/tests/plugins/fixtures/<package>` into `tmp_path/site/`, writes `tmp_path/site/<dist>-<version>.dist-info/{METADATA,entry_points.txt,RECORD}` (RECORD lists every copied file), `monkeypatch.syspath_prepend(tmp_path/"site")`, calls `importlib.invalidate_caches()` and `clear_discovery_cache()`, and registers a finalizer that removes the package from `sys.modules`. Real `importlib.metadata.entry_points(group=…)` then finds it — no pip, no wheel build.

### P3. SDKs and stability

- Python: package `tiqora_sdk` at `backend/src/tiqora_sdk/`, shipped inside the tiqora wheel (`[tool.hatch.build.targets.wheel] packages = ["src/tiqora", "src/tiqora_sdk"]` + the matching `sources` entry) — a separate distribution follows at SDK 1.0 (phase 4). `SDK_VERSION: Final = "0.1.0"`. Plugins must NOT list `tiqora` or `tiqora-sdk` in `Requires-Dist` (verify rejects it: pip would try to fetch them).
- `tiqora_sdk.__all__` (exact; snapshot-tested in `backend/tests/test_tiqora_sdk_surface.py`):

```python
__all__ = [
    "SDK_VERSION",
    # registration
    "Plugin", "PluginRegistry", "RegistrationRejected",
    # runtime context
    "PluginContext", "PluginHttp", "OutboundHostNotAllowed", "TicketSummary",
    # events / tasks / jobs
    "TicketEvent", "EventContext", "EventHandler", "TaskContext", "TaskRun",
    "JobHandler", "JobContext", "JobRetry", "enqueue_job",
    # HTTP route dependencies
    "CurrentAgent", "AdminAgent", "DbSession",
]
```

where `Plugin` is a `Protocol` with `def register(self, registry: PluginRegistry) -> None`, `PluginRegistry` is a `Protocol` describing exactly the public methods of `PluginRegistrar` (P4), `CurrentAgent = tiqora.api.deps.CurrentUser`, `AdminAgent = tiqora.api.v1.admin.deps.AdminUser`, `DbSession = tiqora.api.deps.DbSession`, the rest re-exported from `tiqora.plugins.*` / `tiqora.jobs.*`. `tiqora_sdk.mcp` exports `current_agent_id(ctx) -> int`, `mcp_session_factory() -> async_sessionmaker[AsyncSession]`, `permission_engine(session) -> PermissionEngine`.
- npm: `@tiqora/plugin-sdk` (P12). Version `0.1.0`, same number as `SDK_VERSION`; a test asserts they match.
- Policy (written into `docs/plugins.md`): 0.x minors may break (until SDK 1.0 in phase 4); every change to either surface bumps `SDK_VERSION` and the npm version together and updates the snapshot tests; points with `sdk_stable=True` are the only ones a registrar accepts.

### P4. Registrar, loader and extension points

Points plugins may use in phase 1 (all `sdk_stable=True`; `JOB_HANDLERS` and `SETTINGS_SECTIONS` flip to `True` in Task 3):

| Constant | Module | `key` | Item | Item key | Namespace rule |
|---|---|---|---|---|---|
| `SETTINGS_SECTIONS` | `tiqora.settings_sections.section` | `core.settings_sections` | `SettingsSection` | `.key` | key == `plugins.<id>` (one per plugin) |
| `PLUGIN_ROUTES` | `tiqora.plugins.routes` | `core.http.routes` | `PluginRoute` | `.key` | key `<id>:<n>` (assigned by registrar) |
| `EVENT_SUBSCRIBERS` | `tiqora.plugins.events` | `core.events.subscribers` | `EventSubscriber` | `.key` | key `<id>:<name>` |
| `SCHEDULED_TASKS` | `tiqora.plugins.tasks` | `core.worker.scheduled_tasks` | `ScheduledTask` | `.key` | key `<id>:<name>` |
| `JOB_HANDLERS` | `tiqora.jobs.registry` | `core.job_handlers` | `JobHandler` | `.kind` | kind starts with `<id>.` |
| `MCP_TOOLS` | `tiqora.plugins.mcp` | `core.mcp.tools` | `McpTool` | `.name` | name `<short_id>_<name>` |

`<name>` everywhere: `^[a-z][a-z0-9_]{1,40}$`.

```python
# backend/src/tiqora/plugins/registrar.py
class RegistrationRejected(ValueError): ...    # German message for admins

class PluginRegistrar:                          # = tiqora_sdk.PluginRegistry
    def __init__(self, manifest: PluginManifest, context: PluginContext) -> None
    plugin_id: str                               # property
    context: PluginContext                       # property
    def settings(self, model: type[BaseModel]) -> SettingsSection
        # SettingsSection(key=f"plugins.{id}", model=model, title_key=f"plugin.{id}:settings.title",
        #                 i18n_prefix=f"plugin.{id}:settings"); validate_section_schema(); once per plugin
    def route(self, router: APIRouter, *, public: bool = False) -> None
    def on_event(self, name: str, events: Iterable[str], handler: EventHandler, *,
                 timeout_seconds: int = 30) -> None          # 1 <= timeout <= 300; events non-empty
    def scheduled_task(self, name: str, run: TaskRun, *, every_seconds: int | None = None,
                       daily_at: str | None = None, cron: str | None = None,
                       timeout_seconds: int = 300) -> None   # exactly one schedule; every_seconds >= 30
    def job_handler(self, handler: JobHandler) -> None
    def mcp_tool(self, name: str, fn: Callable[..., Awaitable[Any]], *, description: str,
                 write: bool = False, timeout_seconds: int = 60) -> None
    @property
    def staged(self) -> tuple[tuple[ExtensionPoint[Any], Any], ...]
    # every method: RegistrationRejected when the point's key is not in manifest.capabilities.extension_points,
    # the name breaks the namespace rule, or a staged duplicate exists. Nothing touches the real Registry.
```

```python
# backend/src/tiqora/plugins/loader.py
class PluginLoadTimeout(TimeoutError): ...
async def run_in_daemon_thread[T](fn: Callable[[], T], *, timeout: float) -> T
    # runs fn in threading.Thread(daemon=True); result/exception handed back via loop.call_soon_threadsafe;
    # raises PluginLoadTimeout after `timeout` and abandons the thread (daemon: never blocks interpreter exit)
def import_and_register(plugin: DiscoveredPlugin, context: PluginContext) -> PluginRegistrar
    # EntryPoint.load(); obj must have callable .register; registrar = PluginRegistrar(...); obj.register(registrar)
def commit_registrar(registry: Registry, registrar: PluginRegistrar) -> list[tuple[str, str]]
    # pre-checks every staged item against the registry (registry.get(point, key) is None) -> RegistrationRejected
    # naming the clash; only then registry.add(point, item, owner=plugin_id) for all; returns [(point_key, key)]
def dependency_order(plugins: Sequence[DiscoveredPlugin]) -> tuple[list[DiscoveredPlugin], dict[str, str]]
    # topological by manifest.requires; returns (ordered, {plugin_id: German problem}) for cycles
```

### P5. State, DB, bootstrap and safe mode

Tables (one migration, Task 4). `id` columns are `VARCHAR(190)` (utf8mb4 index limit on MariaDB):

```
tiqora_plugin
  id VARCHAR(190) PK            -- plugin id
  name VARCHAR(200) NOT NULL
  version VARCHAR(64) NOT NULL  -- version last seen in the image
  enabled BOOLEAN NOT NULL
  source VARCHAR(20) NOT NULL   -- "image" (phase 2: "upload", "repository")
  signature_status VARCHAR(20) NOT NULL DEFAULT 'unsigned'   -- phase 2 fills it
  installed_at DATETIME NOT NULL  -- first seen
  updated_at DATETIME NOT NULL    -- last change of version/enabled
  changed_by INTEGER NULL         -- agent who last switched enabled (NULL = system)

tiqora_plugin_runtime                       -- one row per (plugin, process role)
  plugin_id VARCHAR(190) NOT NULL
  role VARCHAR(20) NOT NULL                 -- "api" | "worker" | "mcp"
  state VARCHAR(20) NOT NULL                -- RuntimeState
  version VARCHAR(64) NULL
  error TEXT NULL                           -- German, ≤ 2000 chars, no traceback
  boot_attempts INTEGER NOT NULL DEFAULT 0
  updated_at DATETIME NOT NULL
  PK (plugin_id, role)

tiqora_plugin_hook                          -- subscribers and scheduled tasks
  hook_key VARCHAR(190) PK                  -- "event:<plugin_id>:<name>" or "task:<plugin_id>:<name>"
  plugin_id VARCHAR(190) NOT NULL, INDEX
  kind VARCHAR(20) NOT NULL                 -- "event" | "task"
  cursor BIGINT NULL                        -- events: last delivered ticket_history.id
  consecutive_failures INTEGER NOT NULL DEFAULT 0
  total_failures INTEGER NOT NULL DEFAULT 0
  last_error TEXT NULL
  last_error_at DATETIME NULL
  last_ok_at DATETIME NULL
  last_run_at DATETIME NULL                 -- tasks: start of the last run
  tripped_at DATETIME NULL                  -- circuit breaker; NULL = running
  created_at DATETIME NOT NULL
```

```python
# backend/src/tiqora/plugins/state.py
PluginRole = Literal["api", "worker", "mcp"]
class RuntimeState(StrEnum):
    LOADING = "loading"; ACTIVE = "active"; FAILED = "failed"; QUARANTINED = "quarantined"
    DISABLED = "disabled"; INCOMPATIBLE = "incompatible"; INVALID = "invalid"
@dataclass(frozen=True, slots=True)
class PluginLoadResult:
    plugin_id: str; version: str; state: RuntimeState; error: str | None
    contributions: tuple[tuple[str, str], ...]    # (point_key, item_key) committed
def loaded_plugins() -> Mapping[str, PluginLoadResult]          # this process
def set_loaded_plugins(results: Iterable[PluginLoadResult]) -> None
def is_active(plugin_id: str) -> bool
PluginStatus = Literal["active", "disabled", "failed", "quarantined", "incompatible", "invalid",
                       "restart_required", "unknown"]
def summarize_status(*, enabled: bool, problem: str | None, api_state: RuntimeState | None,
                     compat_problem: str | None) -> PluginStatus
    # invalid if problem; incompatible if compat_problem; quarantined/failed from api_state;
    # enabled & api_state ACTIVE -> active; not enabled & api_state not ACTIVE -> disabled;
    # enabled != (api_state == ACTIVE) -> restart_required; api_state None -> unknown (never booted)
QUARANTINE_AFTER: Final = 3
```

```python
# backend/src/tiqora/plugins/compat.py
def release_core_version(env: Mapping[str, str] = os.environ) -> Version | None
    # only TIQORA_VERSION of the exact form "v?X.Y.Z" (release image); anything else -> None
def check_compat(manifest: PluginManifest, *, core: Version | None, sdk: str = SDK_VERSION) -> str | None
    # German reason or None; sdk range always checked; tiqora range only when core is not None
    # (None -> structlog warning "plugin_core_version_unknown", treated as compatible)
```

```python
# backend/src/tiqora/plugins/bootstrap.py
async def init_registry(*, role: PluginRole, settings: Settings,
                        session_factory: async_sessionmaker[AsyncSession]) -> Registry
```

Algorithm (Task 4): `registry = Registry(); register_core(registry)` → if `settings.plugins_safe_mode`: log `plugins_safe_mode`, no DB access, `set_loaded_plugins([])`, freeze, `install_registry`, return. Otherwise `discovered_plugins()`; if none → same without DB. Else read/sync state with `store.prepare_boot(...)` (3 attempts, 2 s apart; on final failure log `plugins_state_unavailable` and load nothing). Per plugin in `dependency_order`: skip with `INVALID` (problem), `INCOMPATIBLE` (`check_compat`), `DISABLED` (`enabled` false or id in `settings.plugins_disabled_ids`), `QUARANTINED` (`boot_attempts >= QUARANTINE_AFTER`), `FAILED` (required plugin not active / version mismatch); otherwise `store.mark_loading` (boot_attempts + 1, committed) → `await run_in_daemon_thread(lambda: import_and_register(p, ctx), timeout=settings.plugins_import_timeout_seconds)` → `commit_registrar` → `store.mark_result(ACTIVE)` (boot_attempts 0); any exception → `FAILED` with `f"{type(exc).__name__}: {exc}"[:2000]` (timeout: "Laden dauerte länger als N Sekunden"), boot_attempts 0, full traceback only in the log (`plugin_load_failed`). Each skip/failure writes its state for this role (best effort). Finally `registry.freeze()`, `install_registry(registry)`, `set_loaded_plugins(results)`.

`install_registry(registry: Registry) -> None` is added to `tiqora.extensions.core` (sets the process-wide registry under the existing lock). `get_registry()` keeps its behaviour (core only when nothing was installed) — every process must call `init_registry` before anything captures the registry.

Settings (`backend/src/tiqora/config.py`):

```python
plugins_safe_mode: bool = Field(default=False, validation_alias="TIQORA_PLUGINS_SAFE_MODE")
plugins_disabled: str = Field(default="", validation_alias="TIQORA_PLUGINS_DISABLED")   # comma list of ids
plugins_auto_enable: bool = Field(default=True, validation_alias="TIQORA_PLUGINS_AUTO_ENABLE")
plugins_import_timeout_seconds: int = Field(default=20, ge=1, le=300,
                                            validation_alias="TIQORA_PLUGINS_IMPORT_TIMEOUT_SECONDS")
plugin_events_interval_seconds: int = Field(default=5, ge=1, le=300,
                                            validation_alias="TIQORA_PLUGIN_EVENTS_INTERVAL_SECONDS")
plugin_events_gap_hold_seconds: int = Field(default=60, ge=5, le=3600,
                                            validation_alias="TIQORA_PLUGIN_EVENTS_GAP_HOLD_SECONDS")
plugin_tasks_interval_seconds: int = Field(default=15, ge=5, le=300,
                                           validation_alias="TIQORA_PLUGIN_TASKS_INTERVAL_SECONDS")
@property
def plugins_disabled_ids(self) -> frozenset[str]   # split on ",", strip, drop empties
```

Safe-mode guarantees (all tested): no plugin module is imported (`safe mode` → `discovered_plugins()` is not even called); no plugin route is mounted (`/api/plugins/*` → 404); `/api/v1/plugins/frontend` returns `[]`; the admin page still lists discovered plugins (discovery never imports) with a "Sicherer Modus" banner; plugins can never add middleware, auth or login steps in phase 1, and the frontend never loads plugin code on `/login`, the portal, or before `AgentShell`/`AdminShell` mounted.

### P6. Plugin context

```python
# backend/src/tiqora/plugins/context.py
class OutboundHostNotAllowed(PermissionError): ...

@dataclass(frozen=True, slots=True)
class TicketSummary:
    id: int; tn: str; title: str; queue_id: int; queue_name: str; state: str; state_type: str
    priority: str; owner_id: int; customer_user_id: str | None; customer_id: str | None

class PluginHttp:
    def __init__(self, allowed_hosts: Sequence[str], *, resolver: HostResolver | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None   # keyword args: tests only
    def host_allowed(self, host: str) -> bool        # exact (case-insensitive) or "*.suffix" (not the bare suffix)
    async def request(self, method: str, url: str, *, json: Any = None, content: bytes | None = None,
                      headers: Mapping[str, str] | None = None, timeout: float = 10.0) -> httpx.Response
        # OutboundHostNotAllowed unless host_allowed(urlparse(url).hostname); pin_outbound_url(url);
        # httpx.AsyncClient(follow_redirects=False) request to pinned.request_url with
        # pinned.request_headers(headers) and extensions=pinned.request_extensions()

class PluginTickets:
    async def get(self, ticket_id: int, session: AsyncSession | None = None) -> TicketSummary | None
        # read-only, no permission check (system context; documented); integer joins between Znuny tables only;
        # without `session` it opens one from get_session_factory()

@dataclass(frozen=True)
class PluginContext:
    plugin_id: str
    version: str
    http: PluginHttp
    tickets: PluginTickets
    @property
    def logger(self) -> structlog.typing.FilteringBoundLogger     # structlog.get_logger("plugin").bind(plugin=id)
    def session_factory(self) -> async_sessionmaker[AsyncSession]  # tiqora.db.engine.get_session_factory()
    async def settings(self, session: AsyncSession | None = None) -> BaseModel
        # load_section_values(session, section, secret_key=get_settings().secret_key) for "plugins.<id>";
        # without `session` it opens one from get_session_factory();
        # LookupError("Erweiterung hat keine Einstellungen") if the plugin registered none
```

### P7. HTTP routes

```python
# backend/src/tiqora/plugins/routes.py
@dataclass(frozen=True, slots=True)
class PluginRoute:
    key: str            # "<plugin_id>:<n>"
    plugin_id: str
    router: APIRouter
    public: bool        # False -> every route depends on get_current_user (agent session or API key)
PLUGIN_ROUTES: ExtensionPoint[PluginRoute]   # key "core.http.routes", sdk_stable=True

def create_plugin_router() -> Router
    # starlette Router(routes=[], default=<JSON 404 {"detail": "Erweiterung nicht gefunden"}>)
def mount_plugin_routes(app: FastAPI, registry: Registry) -> list[str]
    # for each plugin_id with PLUGIN_ROUTES items and loaded_plugins()[id].state == ACTIVE:
    #   sub = FastAPI(title=f"Erweiterung {id}", docs_url=None, redoc_url=None,
    #                 openapi_url=None if app.state.settings.is_production else "/openapi.json")
    #   sub.state = app.state; sub.dependency_overrides = app.dependency_overrides  (shared objects)
    #   for r in routes: sub.include_router(r.router, dependencies=[] if r.public else [Depends(get_current_user)])
    #   app.state.plugin_router.routes.append(Mount(f"/{id}", app=sub))
    # returns the mounted plugin ids; called once from the api lifespan after init_registry
```

`create_app()` creates the router, stores it in `app.state.plugin_router` and mounts it with `app.mount("/api/plugins", router)` before `mount_spa(...)`. `tiqora.security.csrf._PROTECTED_PREFIXES` becomes `("/api/v1", "/api/portal", "/api/plugins")`. Spec path `/api/plugins/{id}/…` is kept.

### P8. Async event subscribers (ticket_history follower)

Source: Znuny's `ticket_history` (+ `ticket_history_type`), which both Znuny and Tiqora write (Tiqora's writes are Znuny-identical). Event names are Znuny's (`Kernel/System/Ticket.pm`):

```python
HISTORY_EVENT_MAP: Final[Mapping[str, str]] = MappingProxyType({
    "NewTicket": "TicketCreate", "StateUpdate": "TicketStateUpdate", "Move": "TicketQueueUpdate",
    "OwnerUpdate": "TicketOwnerUpdate", "ResponsibleUpdate": "TicketResponsibleUpdate",
    "PriorityUpdate": "TicketPriorityUpdate", "TypeUpdate": "TicketTypeUpdate",
    "ServiceUpdate": "TicketServiceUpdate", "SLAUpdate": "TicketSLAUpdate",
    "TitleUpdate": "TicketTitleUpdate", "CustomerUpdate": "TicketCustomerUpdate",
    "SetPendingTime": "TicketPendingTimeUpdate", "Lock": "TicketLockUpdate", "Unlock": "TicketLockUpdate",
    "ArchiveFlagUpdate": "TicketArchiveFlagUpdate", "TimeAccounting": "TicketAccountTime",
    "Merged": "TicketMerge", "Subscribe": "TicketSubscribe", "Unsubscribe": "TicketUnsubscribe",
})
ARTICLE_HISTORY_TYPES: Final = frozenset({
    "FollowUp", "SendAutoReject", "SendAutoReply", "SendAutoFollowUp", "Forward", "Bounce", "SendAnswer",
    "SendCustomerNotification", "EmailAgent", "EmailCustomer", "PhoneCallAgent", "PhoneCallCustomer",
    "AddNote", "WebRequestCustomer", "EmailResend",
})
# Rules (pure, events_from_history):
#  - history type in HISTORY_EVENT_MAP -> that event
#  - "TicketDynamicFieldUpdate" -> f"TicketDynamicFieldUpdate_{field}" with field parsed from name
#    "%%FieldName%%<field>%%Value%%…" (tiqora.domain.history_render.split_history_name); unparsable -> "HistoryAdd"
#  - type in ARTICLE_HISTORY_TYPES and article_id > 0 -> "ArticleCreate"
#  - TypeUpdate/ServiceUpdate/SLAUpdate rows with the same ticket_id and the same create_time as a NewTicket row
#    of the same batch -> dropped (Znuny's TicketCreate fires only TicketCreate)
#  - everything else -> "HistoryAdd" (Znuny fires HistoryAdd for every history row)

@dataclass(frozen=True, slots=True)
class TicketEvent:
    id: int                 # ticket_history.id
    event: str
    history_type: str
    ticket_id: int
    article_id: int | None  # None when 0/NULL
    name: str               # raw history name
    create_by: int
    create_time: datetime   # as stored (naive)

@dataclass(frozen=True, slots=True)
class EventContext:
    plugin: PluginContext
    attempt: int            # consecutive_failures + 1

EventHandler = Callable[[EventContext, TicketEvent], Awaitable[None]]

@dataclass(frozen=True, slots=True)
class EventSubscriber:
    key: str                # "<plugin_id>:<name>"
    plugin_id: str
    events: frozenset[str]  # Znuny event names, "*" = all, "TicketDynamicFieldUpdate_*" = any DF update
    handler: EventHandler
    timeout_seconds: int
    def matches(self, event: str) -> bool
EVENT_SUBSCRIBERS: ExtensionPoint[EventSubscriber]   # "core.events.subscribers", sdk_stable=True

class GapTracker:
    def __init__(self, *, hold_seconds: float, step: int = 1) -> None
    def horizon(self, after: int, ids: Sequence[int], now: float) -> int
        # highest id h >= after such that every expected id in (after, h] (expected = previous + step) is present
        # or its gap was first seen >= hold_seconds ago (then logged once "plugin_event_gap_skipped");
        # forgets gap entries <= the returned horizon
```

Dispatch (`run_plugin_events_tick(*, registry, settings, session_factory, now=utc_now, wall_clock=time.monotonic) -> dict[str, int]`, Task 7): active subscribers of active plugins → ensure hook rows (`event:<key>`; new rows start at `cursor = MAX(ticket_history.id)` — no replay) → skip tripped → read ≤ 500 history rows `id > min(cursor)` ordered by id → `horizon` → per subscriber (concurrently via `asyncio.gather`), events in `(cursor, horizon]` in id order: non-matching events advance the cursor; a matching event runs `handler` under `asyncio.timeout(timeout_seconds)`; success → cursor = id, `consecutive_failures = 0`, `last_ok_at`; exception/timeout → stop this subscriber for this tick, cursor stays, `consecutive_failures += 1`, `total_failures += 1`, `last_error`, `last_error_at`; at `TRIP_AFTER = 5` → `tripped_at = now` (log `plugin_subscriber_tripped`). One UPDATE per subscriber per tick. Returns `{"subscribers": n, "events": delivered, "failed": f, "tripped": t, "horizon": h}`. One worker replica is assumed (same as `report_facts`).

### P9. Scheduled tasks, job handlers, cron

```python
# backend/src/tiqora/util/cron.py
class CronExpr:
    @classmethod
    def parse(cls, expr: str) -> CronExpr          # 5 fields "m h dom mon dow"; *, a, a-b, */n, a-b/n, lists;
                                                   # dow 0-7 (0 and 7 = Sunday); names not supported; ValueError (German)
    def next_after(self, dt: datetime) -> datetime # naive UTC, minute resolution, strictly after dt;
                                                   # dom/dow: if both restricted, either matches (Vixie cron)

# backend/src/tiqora/plugins/tasks.py
@dataclass(frozen=True, slots=True)
class Schedule:
    every_seconds: int | None = None
    daily_at: str | None = None       # "HH:MM" UTC
    cron: str | None = None
    def describe(self) -> str         # German, e.g. "alle 5 Minuten", "täglich um 03:00 UTC", "Zeitplan 0 */2 * * *"
def next_due(schedule: Schedule, *, last_run_at: datetime | None, created_at: datetime) -> datetime
    # interval: last_run_at + every (never run -> created_at, i.e. right away);
    # daily/cron: next occurrence strictly after (last_run_at or created_at)

@dataclass(frozen=True, slots=True)
class TaskContext:
    plugin: PluginContext
    scheduled_for: datetime
    last_run_at: datetime | None
TaskRun = Callable[[TaskContext], Awaitable[dict[str, Any] | None]]

@dataclass(frozen=True, slots=True)
class ScheduledTask:
    key: str; plugin_id: str; schedule: Schedule; run: TaskRun; timeout_seconds: int
SCHEDULED_TASKS: ExtensionPoint[ScheduledTask]   # "core.worker.scheduled_tasks", sdk_stable=True

async def run_plugin_tasks_tick(*, registry: Registry, session_factory, now=utc_now) -> dict[str, int]
    # hook rows "task:<key>"; due & not tripped -> write last_run_at=now (committed BEFORE running, so a crash
    # never hot-loops) -> run (at most 4 concurrently, asyncio.timeout) -> ok/failure bookkeeping like P8,
    # TRIP_AFTER = 5; returns {"tasks": n, "ran": r, "failed": f, "tripped": t}
```

Job handlers: plugins register ordinary `JobHandler`s (`kind = "<plugin_id>.<name>"`, `title_key = "plugin.<plugin_id>:jobs.<name>"`); the existing `JobRunner` runs them. Jobs of a plugin that is later disabled fail with the runner's existing unknown-kind handling (documented).

Two `DaemonService` rows (Tasks 7 and 8): `plugin_events` (`interval_settings_attr="plugin_events_interval_seconds"`) and `plugin_tasks` (`interval_settings_attr="plugin_tasks_interval_seconds"`), both `enabled_key=None, toggleable=False, default_enabled=True, schedule_kind="interval"`; i18n `admin.daemons.services.plugin_events.*` / `plugin_tasks.*`.

### P10. MCP tools

```python
# backend/src/tiqora/plugins/mcp.py
@dataclass(frozen=True, slots=True)
class McpTool:
    name: str            # "<short_id>_<name>"
    plugin_id: str
    fn: Callable[..., Awaitable[Any]]   # first parameter `ctx: fastmcp.Context`, like core tools
    description: str
    write: bool
    timeout_seconds: int
MCP_TOOLS: ExtensionPoint[McpTool]      # "core.mcp.tools", sdk_stable=True
CORE_MCP_TOOL_NAMES: Final[frozenset[str]]   # literal set of every core tool name in mcp_server/server.py
                                             # (test asserts it equals the core server's list_tools())

def register_plugin_mcp_tools(server: FastMCP, registry: Registry) -> list[str]
    # for each McpTool of an active plugin: wrapper = functools.wraps(tool.fn)(async def …) that calls
    # tiqora.mcp_server.server._assert_tool_allowed(tool.name), _assert_mcp_write_scope() when write,
    # then awaits tool.fn under asyncio.timeout(tool.timeout_seconds);
    # server.tool(name=tool.name, description=tool.description,
    #             annotations={"readOnlyHint": not tool.write})(wrapper)
    # idempotent per server instance (module-level WeakKeyDictionary[FastMCP, set[str]]); returns names
```

The registrar rejects names in `CORE_MCP_TOOL_NAMES`. The MCP process binds tools in `_app_lifespan` after `init_registry(role="mcp", …)`. `functools.wraps` keeps the plugin function's signature and `__wrapped__`, so FastMCP builds the schema from the plugin's annotations (also with `from __future__ import annotations`, because `typing.get_type_hints` follows `__wrapped__` for globals). `test_mcp_every_tool_checks_allowlist` keeps checking only core tools on the global `mcp`; plugin-tool tests use a fresh `FastMCP("test")`.

### P11. API surface

Agent (`backend/src/tiqora/api/v1/plugins.py`, router `prefix="/plugins"`, `tags=["plugins"]`):

```python
class PluginFrontendOut(BaseModel):
    id: str
    name: str
    version: str
    asset_base: str                 # f"/api/v1/plugins/{id}/assets/{version}/"
    entry: str | None               # "web/" + web_manifest.entry; None without web/manifest.json
    styles: list[str]               # "web/" + each style
    locales: list[str]
    contributions: list[FrontendContribution]   # admin_only ones only for admins; only slots in capabilities

GET /api/v1/plugins/frontend -> list[PluginFrontendOut]          (CurrentUser; [] in safe mode)
    # every plugin ACTIVE in this api process that has a web manifest or at least one locale
    # (a plugin with only settings still needs its i18n for the settings form); sorted by id
GET /api/v1/plugins/{plugin_id}/assets/{version}/{path:path}       (CurrentUser)
    404 unless the plugin is ACTIVE in this process, version == loaded version and plugin_asset_path() resolves;
    media type by suffix: .js/.mjs text/javascript, .css text/css, .json application/json, .svg image/svg+xml,
    .png image/png, .woff2 font/woff2, .map application/json; anything else 404;
    headers: Cache-Control "private, max-age=31536000, immutable", X-Content-Type-Options nosniff;
    .svg additionally Content-Security-Policy "sandbox"
```

Admin (`backend/src/tiqora/api/v1/admin/plugins.py`, router `prefix="/plugins"`, `tags=["admin:plugins"]`, all `AdminUser`):

```python
class PluginRuntimeOut(BaseModel):
    role: Literal["api", "worker", "mcp"]; state: str; version: str | None; error: str | None
    boot_attempts: int; updated_at: UtcDateTime
class PluginHookOut(BaseModel):
    key: str; kind: Literal["event", "task"]; name: str; schedule: str | None
    tripped: bool; consecutive_failures: int; total_failures: int; last_error: str | None
    last_error_at: UtcDateTime | None; last_ok_at: UtcDateTime | None; last_run_at: UtcDateTime | None
class PluginCapabilityOut(BaseModel):
    kind: Literal["extension_point", "outbound_host", "frontend_slot"]; value: str
class PluginContributionOut(BaseModel):
    point_key: str; key: str
class PluginAdminOut(BaseModel):
    id: str; name: str; version: str; publisher: str; description: str
    license: str | None; homepage: str | None; support: str | None; categories: list[str]
    enabled: bool; status: PluginStatus; status_message: str | None
    source: str; installed_at: UtcDateTime | None
    runtime: list[PluginRuntimeOut]; hooks: list[PluginHookOut]
    capabilities: list[PluginCapabilityOut]; contributions: list[PluginContributionOut]
    settings_section: str | None    # "plugins.<id>" when registered in this (api) process
class PluginAdminListOut(BaseModel):
    safe_mode: bool; plugins: list[PluginAdminOut]
class PluginEnabledIn(BaseModel):
    enabled: bool

GET  /api/v1/admin/plugins                         -> PluginAdminListOut
PUT  /api/v1/admin/plugins/{plugin_id}/enabled     -> PluginAdminOut   (404 unknown id; commits; changed_by)
POST /api/v1/admin/plugins/{plugin_id}/reset       -> PluginAdminOut   (boot_attempts=0 + QUARANTINED rows -> DISABLED
                                                                        for every role; every tripped hook of the plugin:
                                                                        tripped_at=NULL, consecutive_failures=0)
```

`status_message` texts (German, built in `api/v1/admin/plugins.py`): `restart_required` → "Änderung wird nach einem Neustart von Tiqora wirksam."; `quarantined` → "Beim Start wiederholt abgestürzt – wird übersprungen, bis Sie „Erneut versuchen“ wählen."; `failed` → the api runtime error; `invalid`/`incompatible` → the discovery/compat problem; `unknown` → "Noch nicht gestartet.".

### P12. Frontend loader, runtime shims, slots, CSP

CSP stays `script-src 'self'`: inline import maps would need a hash, so there is none. Instead the host build emits **runtime shim modules** at fixed URLs that re-export the SPA's own module instances; plugin bundles import those URLs.

```ts
// packages/plugin-sdk/src/shared.ts
export const PLUGIN_RUNTIME_PATH = "/plugin-runtime/";
export const PLUGIN_SHARED_MODULES = [
  { specifier: "react", file: "react" },
  { specifier: "react/jsx-runtime", file: "react-jsx-runtime" },
  { specifier: "react-dom", file: "react-dom" },
  { specifier: "@tanstack/react-query", file: "react-query" },
  { specifier: "@tanstack/react-router", file: "react-router" },
  { specifier: "react-i18next", file: "react-i18next" },
  { specifier: "@tiqora/plugin-sdk", file: "sdk" },
] as const;
```

- Host (`frontend/vite.config.ts`): `build.rollupOptions.input = { main: "index.html", "plugin-runtime/<file>": "src/plugins/runtime/<file>.ts" … }`, `preserveEntrySignatures: "exports-only"`, `output.entryFileNames: (c) => c.name.startsWith("plugin-runtime/") ? "[name].js" : "assets/[name]-[hash].js"`. The shim and the app import the same vendor chunk, so React/Query/Router/i18n are single instances. `dist/plugin-runtime/*.js` is unhashed and served by `spa_fallback` with `no-cache` (never `/assets`, whose responses are `immutable`). Dev: a Vite plugin `pluginRuntimeDev` rewrites `/plugin-runtime/<file>.js` to `/src/plugins/runtime/<file>.ts`.
- Shim content: CJS packages (`react`, `react/jsx-runtime`, `react-dom`) get explicit named re-exports (`import * as M from "react"; export default M; export const { Children, … } = M;`); ESM packages use `export * from "…"` (+ `export { default } from` where the package has one); `sdk.ts` is `export * from "../sdkRuntime";`. `frontend/src/plugins/runtime/runtime.test.ts` asserts every public key (not starting `__`) of the real module is exported by the shim, so a library upgrade that adds exports fails the test.
- Plugin build (`@tiqora/plugin-sdk/vite`): `tiqoraPluginConfig({ entry, outDir })` → Vite library build, `formats: ["es"]`, `fileName: "index"`, `rollupOptions.external` = every specifier, `output.paths` = specifier → `PLUGIN_RUNTIME_PATH + file + ".js"`, `cssCodeSplit: false`.
- Loader: `GET /api/v1/plugins/frontend` (React Query key `["plugins", "frontend"]`, `staleTime: Infinity`, `retry: false`; error → zero plugins, no toast), fetched only inside `AgentShell` and `AdminShell` (`PluginProvider`), never on login/portal. `loadPluginModule(p)` → `import(/* @vite-ignore */ p.asset_base + p.entry)` raced against `PLUGIN_IMPORT_TIMEOUT_MS = 10000`; result must be `{ default: { components: Record<string, ComponentType> } }`; cached per `id@version`; style links appended once (`<link rel="stylesheet">`, same origin). i18n: `ensurePluginI18n(p, lang)` fetches `asset_base + "i18n/<lang>.json"` (fallback `en`, then nothing) into namespace `plugin.<id>` (`i18n.addResourceBundle(lang, ns, data, true, true)`), so keys like `plugin.com.example.hello:settings.title` resolve anywhere.
- Safe mode: `?plugins=off` stores `sessionStorage["tiqora.plugins.off"]="1"` (until `?plugins=on` or the tab closes); `PluginProvider` then neither fetches nor imports; the admin plugins page shows "Erweiterungen sind in diesem Browserfenster ausgeschaltet".
- Containment: every slot instance renders inside `<PluginBoundary pluginId pluginName>` (class error boundary + `Suspense`); failures render "Erweiterung „{{name}}“ konnte nicht geladen werden." (+ "Details" with the message) and log `console.error` once.
- Slots: `agent.nav` → `PluginNavItems group=…` inside each existing `NavGroup` plus a new group "Erweiterungen" (`sidebar.plugins`) for `group: "plugins"`, links to `/agent/plugins/$pluginId/<route.path>`, rendered from the manifest without loading plugin code (labels from the plugin namespace, fallback: contribution key); `agent.route` → one TanStack route `/agent/plugins/$pluginId/$` (`PluginRoutePage`, longest `path` prefix match of the splat, props `{ path, subPath }`); `ticket.sidebar` → `PluginTicketPanels ticketId` between `ProcessWidget` and `PresenceBar` in `TicketZoomPage` (each panel a bordered card with title, ordered by `order`; nothing else in the zoom changes — memory "Ticketansicht: fertige Teile"); `dashboard.widget` → P13.
- SDK runtime surface (`packages/plugin-sdk/src/index.ts`, all runtime values `declare`d, implemented by `frontend/src/plugins/sdkRuntime.tsx`; `sdkRuntime.tsx` ends with `export const runtimeCheck = { … } satisfies SdkRuntime` so `pnpm lint` (tsc) fails on drift):

```ts
export const SDK_VERSION: "0.1.0";                                   // declared; runtime value from host
export interface PluginUser { id: number; login: string; isAdmin: boolean }
export interface PluginContextValue { pluginId: string; version: string; locale: string; user: PluginUser | null }
export interface PluginApiRequestOptions { query?: Record<string, string | number | boolean | undefined>; signal?: AbortSignal }
export interface PluginApi {
  get<T>(path: string, opts?: PluginApiRequestOptions): Promise<T>;
  post<T>(path: string, body?: unknown, opts?: PluginApiRequestOptions): Promise<T>;
  put<T>(path: string, body?: unknown, opts?: PluginApiRequestOptions): Promise<T>;
  delete<T>(path: string, opts?: PluginApiRequestOptions): Promise<T>;
}                                                    // paths are relative to /api/plugins/<pluginId>/
export interface RoutePageProps { path: string; subPath: string }
export interface TicketPanelProps { ticketId: number }
// WidgetProps, WidgetSize, WidgetCategory, WidgetMode: exactly as defined in P13
export interface PluginDefinition { components: Record<string, ComponentType<any>> }
export interface SdkRuntime {
  SDK_VERSION: "0.1.0";
  definePlugin(def: PluginDefinition): PluginDefinition;
  usePluginContext(): PluginContextValue;
  usePluginApi(): PluginApi;
  usePluginTranslation(): { t: (key: string, options?: Record<string, unknown>) => string };
  Button: ComponentType<{ variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md";
                          type?: "button" | "submit"; disabled?: boolean; onClick?: () => void; children: ReactNode }>;
  Badge: ComponentType<{ tone?: "default" | "accent" | "warn" | "danger" | "success" | "muted"; children: ReactNode }>;
  Spinner: ComponentType<Record<string, never>>;
  Switch: ComponentType<{ checked: boolean; onChange: (checked: boolean) => void; disabled?: boolean; label: string }>;
  Dialog: ComponentType<{ open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }>;
  Panel: ComponentType<{ title: string; actions?: ReactNode; children: ReactNode }>;
  EmptyState: ComponentType<{ title: string; hint?: string }>;
}
export declare const definePlugin: SdkRuntime["definePlugin"];   // … one declare per SdkRuntime member
```

### P13. Dashboard widget contract (shared with the dashboards plan)

The dashboards plan (wave 2, written later) owns layout, gallery, persistence and data providers; it consumes this contract unchanged and registers its core widgets into the same point. Phase 1 renders plugin widgets in a simple "Erweiterungen" section at the end of today's `DashboardPage`; the dashboards plan replaces that section with its grid.

```ts
// frontend/src/plugins/dashboardWidgets.ts  (types re-exported by @tiqora/plugin-sdk: WidgetProps, WidgetSize)
export type WidgetCategory = "tickets" | "reports" | "team" | "ai" | "itsm" | "other";
export type WidgetMode = "view" | "edit" | "preview" | "wallboard";
export interface WidgetSize { default: [number, number]; min: [number, number]; max: [number, number] } // [w, h] in 12-col grid cells
export interface WidgetProps<S = Record<string, unknown>> {
  instanceId: string;                        // stable id of the placed widget; "preview" in the gallery
  settings: S;                               // defaults from settingsSchema applied
  size: { w: number; h: number };            // cells currently occupied
  filters: Readonly<Record<string, unknown>>; // dashboard-wide filters ({} until dashboards phase 2)
  mode: WidgetMode;
}
export interface WidgetDefinition<S = Record<string, unknown>> {
  key: string;                               // core: "core.<name>"; plugin: "<plugin_id>.<key>"
  owner: string;                             // "core" or plugin id
  version: number;
  titleKey: string;                          // resolved in `ns` (plugin) or the default namespace (core)
  descriptionKey?: string;
  ns?: string;                               // "plugin.<id>" for plugins
  category: WidgetCategory;
  icon: string;
  size: WidgetSize;
  settingsSchema: Record<string, unknown> | null;  // JSON schema, SchemaForm subset
  dataEndpoint: string | null;               // backend provider key (dashboards plan adds core.dashboard.providers);
                                             // null for plugin widgets in phase 1 (they call their own routes)
  refreshOn: string[];                       // SSE event types
  permission: string | null;
  component: ComponentType<WidgetProps<S>>;  // plugin: lazy, wrapped in PluginBoundary by the host
  migrate?: (old: unknown, fromVersion: number) => S;
}
export const DASHBOARD_WIDGETS = createExtensionPoint<WidgetDefinition>("core.dashboard.widgets", (w) => w.key);
export function registerPluginWidgets(plugins: FrontendPlugin[]): void   // idempotent; owner = plugin id
export function useDashboardWidgetDefinitions(): WidgetDefinition[]      // re-renders when plugins finished loading
```

### P14. Baked image and CLI

- Core `Dockerfile` runtime stage (as root, after the `ENV PATH=…` block): `RUN python -m tiqora.main plugins constraints > /app/backend/core-constraints.txt` — one `Name==version` line per installed distribution (`core_constraints()` from `importlib.metadata`, sorted, no URLs, so pip accepts it).
- `docker/entrypoint.sh` gets a role `tiqora) shift; exec python -m tiqora.main "$@" ;;` so `docker run --rm IMAGE tiqora plugins bake …` works.
- `tiqora plugins list [--json]` — discovered plugins with problems/compat (no import, no DB).
- `tiqora plugins verify [--import] [--strict]` — exit 1 when any plugin has a `problem` or compat problem; `--import` additionally loads each entry point object (no `register()`, no DB) in a daemon thread with the import timeout; `--strict` also fails on distributions listing `tiqora`/`tiqora-sdk` in `Requires-Dist`.
- `tiqora plugins constraints` — prints `core_constraints()`.
- `tiqora plugins bake PLUGINS_TXT --output DIR [--base-image IMAGE]` — `plugins.txt`: blank lines, `# comments`, `--index-url URL`, `--extra-index-url URL`, requirement lines (`name==1.0`, `name @ https://…/x.whl`, `name @ git+https://…`), local wheel paths (`./wheels/x-1.0-py3-none-any.whl`, relative to the file). Rejected (exit 2 with German message): `-e`, `-r`, `-c`, `--no-deps`, `--pre`, any other option, a missing local wheel. Writes `DIR/plugins.txt` (local wheels rewritten to `/tmp/tiqora-plugins/wheels/<name>`), copies local wheels to `DIR/wheels/`, writes `DIR/Dockerfile`. Default base image `ghcr.io/cygnusnetworks/tiqora:<X.Y.Z>` from `release_core_version()`; without a release version `--base-image` is required.

Generated Dockerfile (exact template, `render_dockerfile(base_image, has_wheels)`):

```dockerfile
# Generated by `tiqora plugins bake` — edit plugins.txt and run it again instead of editing this file.
FROM {base_image}
USER root
COPY plugins.txt /tmp/tiqora-plugins/plugins.txt
{COPY wheels/ /tmp/tiqora-plugins/wheels/   <- only when has_wheels}
RUN /app/.venv/bin/python -m ensurepip --upgrade \
 && /app/.venv/bin/python -m pip install --no-cache-dir \
      --constraint /app/backend/core-constraints.txt \
      -r /tmp/tiqora-plugins/plugins.txt \
 && rm -rf /tmp/tiqora-plugins
USER tiqora
RUN python -m tiqora.main plugins verify --import --strict
```

### P15. i18n keys (de shown; en added alongside)

`sidebar.plugins` "Erweiterungen"; `plugins.slotError` "Erweiterung „{{name}}“ konnte nicht geladen werden."; `plugins.slotErrorDetails` "Details"; `plugins.pageNotFound` "Diese Seite der Erweiterung gibt es nicht."; `plugins.safeModeBrowser` "Erweiterungen sind in diesem Browserfenster ausgeschaltet."; `plugins.widgetsTitle` "Erweiterungen"; `admin.nav.plugins` "Erweiterungen"; `admin.pageDescriptions.plugins` "Zusätzliche Funktionen, die in dieses Tiqora eingebaut sind: ein- und ausschalten, Zustand prüfen, Einstellungen öffnen."; `admin.plugins.*`: `title`, `empty` ("Es sind keine Erweiterungen eingebaut. Wie Erweiterungen in das Tiqora-Image kommen, steht in der Anleitung."), `safeMode` ("Sicherer Modus: Tiqora wurde ohne Erweiterungen gestartet (TIQORA_PLUGINS_SAFE_MODE)."), `enabled`, `settings` ("Einstellungen"), `retry` ("Erneut versuchen"), `publisher` ("Herausgeber"), `version`, `status.<PluginStatus>` (active "Aktiv", disabled "Ausgeschaltet", failed "Fehler beim Laden", quarantined "Übersprungen", incompatible "Passt nicht zu dieser Tiqora-Version", invalid "Paket fehlerhaft", restart_required "Neustart nötig", unknown "Noch nicht gestartet"), `roles.api` ("Weboberfläche und Schnittstelle"), `roles.worker` ("Hintergrunddienst"), `roles.mcp` ("KI-Schnittstelle (MCP)"), `hooks.title` ("Automatische Abläufe"), `hooks.stopped` ("Angehalten nach {{count}} Fehlern"), `hooks.lastError`, `capabilities.title` ("Was diese Erweiterung angibt"), `capabilities.note` ("Erweiterungen laufen mit vollen Rechten in Tiqora. Die Liste beschreibt, was die Erweiterung von sich sagt – sie ist keine Absicherung."), `capabilities.point.<key>` (settings "Hat eigene Einstellungen", routes "Bietet eigene Schnittstellen an", subscribers "Reagiert auf Ticketänderungen", tasks "Führt zeitgesteuerte Aufgaben aus", jobs "Führt Hintergrundaufgaben aus", mcp "Stellt Werkzeuge für KI-Assistenten bereit"), `capabilities.host` ("Ruft {{host}} auf"), `capabilities.slot.<slot>` (agent.nav "Fügt einen Eintrag in der Seitenleiste hinzu", agent.route "Fügt eine eigene Seite hinzu", ticket.sidebar "Zeigt ein Feld in der Ticketansicht", dashboard.widget "Bietet ein Element für die Startseite"); `admin.daemons.services.plugin_events` (name "Erweiterungen: Ticketänderungen weitergeben", description "Gibt neue Ticketänderungen an Erweiterungen weiter, die darauf reagieren."), `admin.daemons.services.plugin_tasks` (name "Erweiterungen: zeitgesteuerte Aufgaben", description "Startet die regelmäßigen Aufgaben der Erweiterungen.").

---

## Deviations from spec

1. **Event source is `ticket_history`, not the outbox.** `tiqora_event_outbox` only sees Tiqora's own writes; the spec requires "both Znuny and Tiqora writes". The history follower (P8) maps history types to Znuny event names. The outbox is not touched (feeding it from the poller would make `ai/auto_worker.py` auto-reply to Znuny-created articles). Late rows are delivered in id order after a bounded hold; a row committing more than `TIQORA_PLUGIN_EVENTS_GAP_HOLD_SECONDS` after a higher id is skipped and logged.
2. **Plugin settings live in `tiqora_settings` via settings sections** (`plugins.<id>`), not in a `tiqora_plugin.settings` JSON column — one settings model (roadmap principle 3) with the existing secret contract, admin API and `SchemaForm`. Per-queue/per-group setting scopes: phase 2.
3. **`health JSON` → two tables** `tiqora_plugin_runtime` (per process role, enables crash quarantine) and `tiqora_plugin_hook` (subscriber/task cursors and circuit breakers); several processes writing one JSON column would lose updates.
4. **No import map.** CSP `script-src 'self'` blocks inline import maps; runtime shim modules at `/plugin-runtime/*.js` give the same "host provides externals" result with no CSP change. Externals also include `@tanstack/react-router` (plugins need `Link`/`useNavigate` on the host router) and `@tiqora/plugin-sdk` itself; `i18next` is reached through the SDK's `usePluginTranslation` instead of being shared directly.
5. **`tiqora_sdk` ships inside the tiqora wheel** (own `SDK_VERSION`, own snapshot test) until SDK 1.0; the Docker build installs only `backend/`. Compatibility is checked with the manifest `sdk` range; the `tiqora` range only on release images (`TIQORA_VERSION` = clean tag), because the dev/package version (`0.2.2`) is meaningless.
6. **Routes** keep the spec path `/api/plugins/{id}/…`, but the CSRF origin check is extended to that prefix; the asset and frontend-list endpoints live under `/api/v1/plugins/…`.
7. **Circuit-breaker state is shown on the plugin page** (per subscriber/task, with "Erneut versuchen"); the Daemons page gets two rows for the two loops, not one row per hook.
8. **No coordinated restart in phase 1**: switching a plugin on/off says "Neustart nötig"; the coordinated restart arrives with runtime install (phase 2).
9. **Not in phase 1** (phase 2 per spec): `.tqplugin` zip, signatures/trust levels, repository index, runtime install, plugin Alembic branches/tables (`short_id` is reserved for `tqp_<short_id>_`), sync "before" hooks, the remaining extension points, write-capable domain services. The manifest already carries every marketplace field (§8a: publisher id, licence, homepage, support, categories), so the phase-2 index can copy it without a format change.
10. **Manifest has no "settings schema reference"**; settings are registered in code (`registry.settings(Model)`) and the manifest declares the capability `core.settings_sections`.
11. **Validator scope**: `tiqora plugins verify` checks manifest, files, compatibility, imports and forbidden requirements; raw-SQL-against-Znuny and monkeypatch warnings come with the phase-2 validator.
12. **The ai-worker does not load plugins** in phase 1 (no AI extension points yet).
13. **Frontend slots**: agent shell only (portal/admin routes later); the "ticket sidebar" is a panel stack in the existing zoom layout, not a new sidebar; the dashboard widget renders in an "Erweiterungen" section of today's home page until the dashboards plan lands.
14. **Domain services**: phase 1 offers only read access (`ctx.tickets.get`); write services (`sdk.tickets` notes etc.) come in phase 2.
15. **Spec silent → decided**: newly discovered plugins are enabled by default (`TIQORA_PLUGINS_AUTO_ENABLE=1`; set `0` to require a click) — an image with plugins is a deliberate operator choice, like an installed OPM package.

## Shared files (coordination with other wave-2 plans)

Migration (`versions_tiqora`, head bump), `config.py`, `api/app.py` (lifespan, mount), `security/csrf.py`, `api/v1/__init__.py` + `api/v1/admin/__init__.py` (router registration), `worker/__main__.py` + `worker/services.py` (two loops), `mcp_server/server.py` (lifespan), `extensions/core.py`, `jobs/registry.py`, `settings_sections/section.py`, `reporting/incremental.py` (`_id_step` move), `main.py`, `backend/pyproject.toml` + `uv.lock`, `backend/tests/conftest.py` (fixture import), `Dockerfile`, `docker/entrypoint.sh`, `frontend/vite.config.ts`, `frontend/eslint.config.js`, `frontend/package.json` + `pnpm-lock.yaml`, `router.tsx`, `AgentShell.tsx` (its `NavItem`/`NavCountBadge` move to `components/layout/NavItem.tsx`), `AdminShell.tsx`, `components/ui/icons.tsx`, `TicketZoomPage.tsx`, `DashboardPage.tsx` (the dashboards plan will replace the plugin section), `SettingsSectionPage.tsx`, `lib/adminSearch.ts`, `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, i18n `en.json`/`de.json` + propagation, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `docs/architecture.md`, `docs/deployment.md`.

---

## Tasks

Order: 1–11 backend (1–3 and 6 need no DB), 12–15 frontend, 16 docs + full verification. Each task ends with its lint gate and one commit. Fixture plugin id is `org.example.hello` (`short_id = "hello"`, distribution `tq-fixture-hello`, package `tq_fixture_hello`) everywhere.

### Task 1: Manifest, no-import discovery, compatibility, SDK package skeleton (backend-only)

**Files:**
- Create: `backend/src/tiqora/plugins/__init__.py`, `backend/src/tiqora/plugins/manifest.py`, `backend/src/tiqora/plugins/discovery.py`, `backend/src/tiqora/plugins/compat.py`, `backend/src/tiqora_sdk/__init__.py` (only `SDK_VERSION: Final = "0.1.0"` and a docstring in this task; the facade is filled in Task 3)
- Create fixture: `backend/tests/plugins/__init__.py` (empty), `backend/tests/plugins/helpers.py`, `backend/tests/plugins/fixtures/tq_fixture_hello/{__init__.py,plugin.py,tiqora-plugin.toml,web/manifest.json,web/index.js,web/hello.css,i18n/de.json,i18n/en.json}`
- Modify: `backend/pyproject.toml` (dependency `"packaging>=24"`; `[tool.hatch.build.targets.wheel] packages = ["src/tiqora", "src/tiqora_sdk"]` and `sources` `"src/tiqora_sdk" = "tiqora_sdk"`; `[tool.mypy] packages = ["tiqora", "tiqora_sdk"]`), `uv.lock` (`uv lock` — `packaging` is already locked transitively, so the diff is one dependency line)
- Test: `backend/tests/test_plugins_manifest.py`, `backend/tests/test_plugins_discovery.py`, `backend/tests/test_plugins_compat.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: P1 (`PluginManifest`, `Capabilities`, `WebManifest`, contribution models, `FrontendContribution`, `parse_manifest`, `parse_web_manifest`, `ManifestError`, `PLUGIN_ID_RE`, `SHORT_ID_RE`, `FRONTEND_SLOTS`), P2 (`DiscoveredPlugin`, `ENTRY_POINT_GROUP`, `discover_plugins`, `discovered_plugins`, `clear_discovery_cache`, `plugin_asset_path`), P5 compat (`release_core_version`, `check_compat`), `tiqora_sdk.SDK_VERSION`, test helper `install_fixture_plugin(tmp_path, monkeypatch, package="tq_fixture_hello", *, plugin_id="org.example.hello", version="1.0.0", dist_name=None, entry=None, drop=(), manifest_overrides=None, requires_dist=()) -> Path`.

- [ ] **Step 1: Write the fixture plugin files (data only)**

`backend/tests/plugins/fixtures/tq_fixture_hello/tiqora-plugin.toml`:

```toml
manifest_version = 1
id = "org.example.hello"
short_id = "hello"
name = "Hallo-Erweiterung"
version = "1.0.0"
publisher = "Example GmbH"
publisher_id = "org.example"
description = "Testerweiterung mit allen Erweiterungspunkten der Phase 1."
license = "MIT"
homepage = "https://example.org/hello"
support = "support@example.org"
categories = ["tickets"]
entry = "tq_fixture_hello:plugin"
sdk = ">=0.1,<0.2"

[capabilities]
extension_points = ["core.settings_sections", "core.http.routes", "core.events.subscribers",
                    "core.worker.scheduled_tasks", "core.job_handlers", "core.mcp.tools"]
outbound_hosts = ["api.example.org", "*.example.net"]
frontend_slots = ["agent.nav", "agent.route", "ticket.sidebar", "dashboard.widget"]
```

`web/manifest.json`:

```json
{
  "manifest_version": 1,
  "entry": "index.js",
  "styles": ["hello.css"],
  "contributions": [
    {"slot": "agent.route", "key": "overview", "path": "overview", "component": "HelloPage", "title_key": "page.title"},
    {"slot": "agent.route", "key": "admin", "path": "admin", "component": "HelloPage", "title_key": "page.title", "admin_only": true},
    {"slot": "agent.nav", "key": "overview", "route": "overview", "label_key": "nav.overview", "group": "plugins"},
    {"slot": "agent.nav", "key": "admin", "route": "admin", "label_key": "nav.admin", "group": "plugins", "admin_only": true},
    {"slot": "ticket.sidebar", "key": "info", "component": "HelloPanel", "title_key": "panel.title", "order": 50},
    {"slot": "dashboard.widget", "key": "weather", "component": "HelloWidget", "title_key": "widget.title",
     "category": "other", "size": {"default": [4, 2], "min": [2, 2], "max": [6, 4]},
     "settings_schema": {"type": "object", "properties": {"city": {"type": "string", "default": "Bonn"}}},
     "refresh_on": ["ticket_changed"]}
  ]
}
```

`web/index.js` (hand-written ES module; no build step; uses only shim URLs):

```js
import { jsx } from "/plugin-runtime/react-jsx-runtime.js";
import { definePlugin, usePluginTranslation, usePluginContext, Panel } from "/plugin-runtime/sdk.js";

function HelloPanel({ ticketId }) {
  const { t } = usePluginTranslation();
  return jsx(Panel, { title: t("panel.title"), children: `Ticket ${ticketId}` });
}
function HelloPage({ path }) {
  const { pluginId } = usePluginContext();
  return jsx("p", { "data-testid": "hello-page", children: `${pluginId}:${path}` });
}
function HelloWidget({ settings }) {
  return jsx("p", { "data-testid": "hello-widget", children: String(settings.city ?? "") });
}
export default definePlugin({ components: { HelloPanel, HelloPage, HelloWidget } });
```

`web/hello.css`: `.tq-hello { font-weight: 600; }`. `i18n/de.json`:
`{"nav": {"overview": "Hallo", "admin": "Hallo (Admin)"}, "page": {"title": "Hallo-Seite"}, "panel": {"title": "Hallo"}, "widget": {"title": "Hallo-Wetter"}, "settings": {"title": "Hallo-Erweiterung", "fields": {"greeting": {"label": "Begrüßung"}, "api_token": {"label": "Zugangsschlüssel"}}}, "jobs": {"echo": "Hallo-Echo"}}` — `en.json` same keys in English. `__init__.py`: `from tq_fixture_hello.plugin import plugin` + `__all__ = ["plugin"]`; `plugin.py` for now:

```python
"""Fixture plugin (filled in Task 3)."""

from __future__ import annotations

from typing import Any


class HelloPlugin:
    def register(self, registry: Any) -> None:
        return None


plugin = HelloPlugin()
```

- [ ] **Step 2: Write the test helper** `backend/tests/plugins/helpers.py`:

```python
"""Install a fixture plugin as a real (pip-less) distribution for importlib.metadata."""

from __future__ import annotations

import importlib
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return '"' + v.replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def install_fixture_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str = "tq_fixture_hello",
    *,
    plugin_id: str = "org.example.hello",
    version: str = "1.0.0",
    dist_name: str | None = None,
    entry: str | None = None,
    drop: Sequence[str] = (),
    manifest_overrides: Mapping[str, Any] | None = None,
    requires_dist: Sequence[str] = (),
) -> Path:
    """Copy fixtures/<package> to tmp_path/site/, write a .dist-info, put it on sys.path."""
    site = tmp_path / "site"
    pkg_dir = site / package
    shutil.copytree(FIXTURES / package, pkg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in drop:
        (pkg_dir / rel).unlink()
    if manifest_overrides:
        path = pkg_dir / "tiqora-plugin.toml"
        data = tomllib.loads(path.read_text())
        top = {k: v for k, v in data.items() if not isinstance(v, dict)}
        top.update({k: v for k, v in manifest_overrides.items() if not isinstance(v, dict)})
        tables = {k: v for k, v in data.items() if isinstance(v, dict)}
        tables.update({k: v for k, v in manifest_overrides.items() if isinstance(v, dict)})
        lines = [f"{k} = {_toml_value(v)}" for k, v in top.items()]
        for name, table in tables.items():
            lines.append(f"[{name}]")
            lines.extend(f'"{k}" = {_toml_value(v)}' for k, v in table.items())
        path.write_text("\n".join(lines) + "\n")
    dist = dist_name or package.replace("_", "-")
    info = site / f"{dist.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    meta = ["Metadata-Version: 2.1", f"Name: {dist}", f"Version: {version}"]
    meta += [f"Requires-Dist: {r}" for r in requires_dist]
    (info / "METADATA").write_text("\n".join(meta) + "\n")
    (info / "entry_points.txt").write_text(
        f"[tiqora.plugins]\n{plugin_id} = {entry or package + ':plugin'}\n"
    )
    files = [p.relative_to(site).as_posix() for p in sorted(pkg_dir.rglob("*")) if p.is_file()]
    files += [f"{info.name}/METADATA", f"{info.name}/entry_points.txt", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{f},,\n" for f in files))
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    from tiqora.plugins.discovery import clear_discovery_cache

    clear_discovery_cache()
    return pkg_dir


Installer = Callable[..., Path]


@pytest.fixture
def fixture_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Installer]:
    installed: list[str] = []

    def install(package: str = "tq_fixture_hello", **kw: Any) -> Path:
        installed.append(package)
        sub = tmp_path / f"p{len(installed)}"
        sub.mkdir()
        return install_fixture_plugin(sub, monkeypatch, package, **kw)

    yield install
    for package in installed:
        for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
            del sys.modules[name]
    from tiqora.plugins.discovery import clear_discovery_cache

    clear_discovery_cache()
```

(Imports at the top of `helpers.py`: `from collections.abc import Callable, Iterator, Mapping, Sequence`.) The fixture guarantees that after each test the fixture packages are gone from `sys.modules` and the discovery cache is cleared; `monkeypatch` undoes the `sys.path` change. Make the fixture available to every test by adding `from tests.plugins.helpers import fixture_plugins  # noqa: F401` to `backend/tests/conftest.py` (`backend/tests/__init__.py` exists, so `tests.plugins.helpers` is importable; importing it into test modules instead would trip ruff F811 on the parameter name). Verify with `cd backend && uv run python -m pytest --collect-only -q tests/test_plugins_discovery.py`. Add `backend/tests/conftest.py` to this task's staged files.

- [ ] **Step 3: Write the failing tests**

`backend/tests/test_plugins_manifest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from tiqora.plugins.manifest import Capabilities, ManifestError, parse_manifest, parse_web_manifest

FIX = Path(__file__).parent / "plugins" / "fixtures" / "tq_fixture_hello"


def _manifest_text(**replace: str) -> str:
    text = (FIX / "tiqora-plugin.toml").read_text()
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def test_parse_valid_manifest() -> None:
    m = parse_manifest(_manifest_text())
    assert m.id == "org.example.hello"
    assert m.short_id == "hello"
    assert m.sdk == ">=0.1,<0.2"
    assert "core.mcp.tools" in m.capabilities.extension_points
    assert m.capabilities.outbound_hosts == ["api.example.org", "*.example.net"]


@pytest.mark.parametrize("bad", ["Org.Example.Hello", "hello", "org.example-hello", "org..hello"])
def test_invalid_id_rejected(bad: str) -> None:
    with pytest.raises(ManifestError, match="id"):
        parse_manifest(_manifest_text(**{'id = "org.example.hello"': f'id = "{bad}"'}))


@pytest.mark.parametrize("bad", ["h", "Hello", "1hello", "hello_world_too_long_x"])
def test_bad_short_id_rejected(bad: str) -> None:
    with pytest.raises(ManifestError, match="short_id"):
        parse_manifest(_manifest_text(**{'short_id = "hello"': f'short_id = "{bad}"'}))


def test_unknown_key_rejected() -> None:
    with pytest.raises(ManifestError, match="colour"):
        parse_manifest(_manifest_text(**{"[capabilities]": 'colour = "blue"\n\n[capabilities]'}))


def test_bad_specifier_rejected() -> None:
    with pytest.raises(ManifestError, match="sdk"):
        parse_manifest(_manifest_text(**{'sdk = ">=0.1,<0.2"': 'sdk = "banana"'}))


def test_toml_syntax_error_is_manifest_error() -> None:
    with pytest.raises(ManifestError, match="tiqora-plugin.toml"):
        parse_manifest("id = ")


def _web(contribs: str) -> bytes:
    return ('{"manifest_version": 1, "contributions": [' + contribs + "]}").encode()


def test_web_manifest_slot_not_declared() -> None:
    caps = Capabilities(frontend_slots=["agent.route"])
    raw = _web('{"slot": "ticket.sidebar", "key": "x", "component": "X", "title_key": "t"}')
    with pytest.raises(ManifestError, match="ticket.sidebar"):
        parse_web_manifest(raw, caps)


def test_web_manifest_unknown_slot_dropped() -> None:
    caps = Capabilities(frontend_slots=["agent.route", "portal.page"])
    raw = _web('{"slot": "portal.page", "key": "x"},'
               '{"slot": "agent.route", "key": "a", "path": "a", "component": "A", "title_key": "t"}')
    web = parse_web_manifest(raw, caps)
    assert [c.slot for c in web.contributions] == ["agent.route"]


def test_nav_must_name_existing_route() -> None:
    caps = Capabilities(frontend_slots=["agent.nav", "agent.route"])
    raw = _web('{"slot": "agent.nav", "key": "n", "route": "missing", "label_key": "l"}')
    with pytest.raises(ManifestError, match="missing"):
        parse_web_manifest(raw, caps)


@pytest.mark.parametrize("entry", ["../evil.js", "/abs.js", "a\\\\b.js", "index.mjs.txt"])
def test_entry_path_rejected(entry: str) -> None:
    raw = ('{"manifest_version": 1, "entry": "' + entry + '"}').encode()
    with pytest.raises(ManifestError, match="entry"):
        parse_web_manifest(raw, Capabilities())


def test_fixture_web_manifest_parses() -> None:
    m = parse_manifest(_manifest_text())
    web = parse_web_manifest((FIX / "web" / "manifest.json").read_bytes(), m.capabilities)
    assert {c.slot for c in web.contributions} == {
        "agent.route", "agent.nav", "ticket.sidebar", "dashboard.widget"
    }
    assert web.styles == ["hello.css"]
```

`backend/tests/test_plugins_discovery.py` (uses the `fixture_plugins` fixture):

```python
def test_discovers_installed_fixture_without_importing(fixture_plugins) -> None:
    fixture_plugins()
    (p,) = [d for d in discover_plugins() if d.plugin_id == "org.example.hello"]
    assert p.problem is None
    assert p.manifest is not None and p.manifest.short_id == "hello"
    assert p.web_manifest is not None and len(p.web_manifest.contributions) == 6
    assert p.locales == ("de", "en")
    assert p.dist_name == "tq-fixture-hello" and p.dist_version == "1.0.0"
    assert "tq_fixture_hello" not in sys.modules


def test_entry_mismatch_is_problem(fixture_plugins) -> None:
    fixture_plugins(entry="tq_fixture_hello.plugin:plugin")
    (p,) = discover_plugins()
    assert p.problem is not None and "entry" in p.problem


def test_version_mismatch_is_problem(fixture_plugins) -> None:
    fixture_plugins(version="2.0.0")
    (p,) = discover_plugins()
    assert p.problem is not None and "2.0.0" in p.problem and "1.0.0" in p.problem


def test_missing_web_entry_is_problem(fixture_plugins) -> None:
    fixture_plugins(drop=["web/index.js"])
    (p,) = discover_plugins()
    assert p.problem is not None and "web/index.js" in p.problem


def test_missing_manifest_is_problem(fixture_plugins) -> None:
    fixture_plugins(drop=["tiqora-plugin.toml"])
    (p,) = discover_plugins()
    assert p.manifest is None and p.problem is not None and "tiqora-plugin.toml" in p.problem


def test_duplicate_ids_flag_both(fixture_plugins) -> None:
    fixture_plugins(dist_name="tq-fixture-hello")
    fixture_plugins(dist_name="tq-fixture-hello-copy")
    found = [d for d in discover_plugins() if d.plugin_id == "org.example.hello"]
    assert len(found) == 2 and all(d.problem and "mehrfach" in d.problem for d in found)


def test_cache_and_clear(fixture_plugins) -> None:
    assert all(d.plugin_id != "org.example.hello" for d in discovered_plugins())
    fixture_plugins()
    clear_discovery_cache()
    assert any(d.plugin_id == "org.example.hello" for d in discovered_plugins())


@pytest.mark.parametrize("rel", ["web/../tiqora-plugin.toml", "/etc/passwd", "i18n/../plugin.py",
                                 "plugin.py", "web\\index.js", "i18n/de.txt", "web/missing.js"])
def test_asset_path_rejects(fixture_plugins, rel: str) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    assert plugin_asset_path(p, rel) is None


def test_asset_path_resolves(fixture_plugins) -> None:
    pkg = fixture_plugins()
    (p,) = discover_plugins()
    assert plugin_asset_path(p, "web/index.js") == (pkg / "web" / "index.js").resolve()
    assert plugin_asset_path(p, "i18n/de.json") == (pkg / "i18n" / "de.json").resolve()
```

(The duplicate test needs the helper to accept two installs of the same package under different dist names into separate site dirs — `fixture_plugins` uses a sub-directory per call; both copies have package `tq_fixture_hello`; that is fine because discovery never imports.)

`backend/tests/test_plugins_compat.py`:

```python
@pytest.mark.parametrize(("raw", "expected"), [
    ("v0.18.2", "0.18.2"), ("0.18.0", "0.18.0"), ("v0.17.1+327.g9f845be8", None),
    ("9f845be8", None), ("", None),
])
def test_release_core_version(raw: str, expected: str | None) -> None:
    got = release_core_version({"TIQORA_VERSION": raw})
    assert (str(got) if got else None) == expected


def test_sdk_range_checked() -> None:
    m = _manifest(sdk=">=0.2")
    reason = check_compat(m, core=None, sdk="0.1.0")
    assert reason is not None and "0.1.0" in reason


def test_core_range_checked_only_on_release() -> None:
    m = _manifest(tiqora=">=1.0")
    assert check_compat(m, core=Version("0.18.0")) is not None
    assert check_compat(m, core=None) is None


def test_compatible() -> None:
    assert check_compat(_manifest(tiqora=">=0.18,<1"), core=Version("0.18.3")) is None
```

(`_manifest(**overrides)` builds a `PluginManifest` from the fixture TOML via `parse_manifest` + `model_copy(update=…)`.)

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && uv run python -m pytest -q tests/test_plugins_manifest.py tests/test_plugins_discovery.py tests/test_plugins_compat.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tiqora.plugins'`.

- [ ] **Step 5: Implement** `manifest.py`, `discovery.py`, `compat.py`, `tiqora_sdk/__init__.py`, pyproject changes exactly as P1/P2/P3/P5. Notes:
  - Discovery: `importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)`; for each `ep`: `dist = ep.dist` (None → problem "Paket ohne Metadaten"); manifest = the single entry of `dist.files` whose `PackagePath.name == "tiqora-plugin.toml"` and `len(parts) == 2`; read via `dist.locate_file(path).read_text("utf-8")`; `package_dir = Path(dist.locate_file(path)).parent.resolve()`; checks in this order, first failing wins, message German: manifest missing → "tiqora-plugin.toml fehlt im Paket"; invalid → the `ManifestError` text; `manifest.id != ep.name` → "Kennung im Manifest (…) passt nicht zum Einstiegspunkt (…)"; `manifest.entry != ep.value` → "entry im Manifest (…) passt nicht zum Einstiegspunkt (…)"; `Version(manifest.version) != Version(dist.version)` → "Version im Manifest (1.0.0) passt nicht zum Paket (2.0.0)"; `frontend_slots` non-empty and `web/manifest.json` missing → "web/manifest.json fehlt"; invalid web manifest → its text; entry/styles files missing → "web/index.js fehlt im Paket (beim Bauen verloren? siehe Anleitung „Dateien im Wheel“)". Duplicates: after the loop, ids or short_ids occurring more than once get problem "Kennung org.example.hello ist mehrfach installiert" (resp. short_id).
  - `plugin_asset_path`: reject unless `rel` matches `^web/[A-Za-z0-9_./-]+$` or `^i18n/[a-z]{2}(_[A-Z]{2})?\.json$`, contains no `..` segment and no backslash; `candidate = (package_dir / rel).resolve()`; must be `is_file()` and `is_relative_to(package_dir)`.
  - `discovered_plugins` is `functools.cache` on a zero-arg function returning a tuple; `clear_discovery_cache` calls its `cache_clear()`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run python -m pytest -q tests/test_plugins_manifest.py tests/test_plugins_discovery.py tests/test_plugins_compat.py`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk
rtk git add backend/src/tiqora/plugins/__init__.py backend/src/tiqora/plugins/manifest.py backend/src/tiqora/plugins/discovery.py backend/src/tiqora/plugins/compat.py backend/src/tiqora_sdk/__init__.py backend/tests/plugins backend/tests/conftest.py backend/tests/test_plugins_manifest.py backend/tests/test_plugins_discovery.py backend/tests/test_plugins_compat.py backend/pyproject.toml uv.lock
rtk git diff --cached --name-only
rtk git commit -m "feat(plugins): manifest, entry-point discovery without import, compatibility check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
rtk git log -1 --oneline
```

### Task 2: Cron expressions and schedules (backend-only, pure)

**Files:**
- Create: `backend/src/tiqora/util/cron.py`, `backend/src/tiqora/plugins/tasks.py` (only `Schedule` and `next_due` in this task)
- Test: `backend/tests/test_cron.py`, `backend/tests/test_plugins_schedule.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CronExpr.parse(expr) -> CronExpr`, `CronExpr.next_after(dt) -> datetime`; `Schedule(every_seconds=…, daily_at=…, cron=…)` with `Schedule.validate() -> None` (raises `ValueError`, German) and `Schedule.describe() -> str`; `next_due(schedule, *, last_run_at, created_at) -> datetime`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_cron.py`:

```python
from datetime import datetime

import pytest

from tiqora.util.cron import CronExpr

D = datetime


@pytest.mark.parametrize(("expr", "after", "expected"), [
    ("*/15 * * * *", D(2026, 10, 10, 10, 7), D(2026, 10, 10, 10, 15)),
    ("*/15 * * * *", D(2026, 10, 10, 10, 45), D(2026, 10, 10, 11, 0)),
    ("0 3 * * *", D(2026, 10, 10, 3, 0), D(2026, 10, 11, 3, 0)),        # strictly after
    ("0 3 * * *", D(2026, 10, 10, 2, 59, 30), D(2026, 10, 10, 3, 0)),
    ("30 2 1 * *", D(2026, 10, 10, 0, 0), D(2026, 11, 1, 2, 30)),
    ("0 0 * * 0", D(2026, 10, 10, 12, 0), D(2026, 10, 11, 0, 0)),       # 2026-10-11 is a Sunday
    ("0 0 * * 7", D(2026, 10, 10, 12, 0), D(2026, 10, 11, 0, 0)),
    ("0 0 13 * 5", D(2026, 10, 10, 12, 0), D(2026, 10, 13, 0, 0)),      # dom OR dow (13th wins)
    ("0 0 13 * 5", D(2026, 10, 13, 12, 0), D(2026, 10, 16, 0, 0)),      # next Friday
    ("0 0 29 2 *", D(2026, 3, 1, 0, 0), D(2028, 2, 29, 0, 0)),          # leap year
    ("5,35 8-10/2 * * 1-5", D(2026, 10, 12, 8, 6), D(2026, 10, 12, 8, 35)),
    ("5,35 8-10/2 * * 1-5", D(2026, 10, 12, 10, 36), D(2026, 10, 13, 8, 5)),
])
def test_next_after(expr: str, after: datetime, expected: datetime) -> None:
    assert CronExpr.parse(expr).next_after(after) == expected


@pytest.mark.parametrize("expr", ["60 * * * *", "* * *", "*/0 * * * *", "a * * * *",
                                  "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8", "5-1 * * * *"])
def test_invalid(expr: str) -> None:
    with pytest.raises(ValueError, match="Zeitplan"):
        CronExpr.parse(expr)
```

`backend/tests/test_plugins_schedule.py`:

```python
CREATED = datetime(2026, 10, 10, 4, 0)


def test_interval_never_run_is_due_at_creation() -> None:
    assert next_due(Schedule(every_seconds=300), last_run_at=None, created_at=CREATED) == CREATED


def test_interval_after_last_run() -> None:
    last = datetime(2026, 10, 10, 10, 0)
    assert next_due(Schedule(every_seconds=300), last_run_at=last, created_at=CREATED) == datetime(2026, 10, 10, 10, 5)


def test_daily_not_due_at_creation() -> None:
    assert next_due(Schedule(daily_at="03:00"), last_run_at=None, created_at=CREATED) == datetime(2026, 10, 11, 3, 0)


def test_cron_after_last_run() -> None:
    s = Schedule(cron="0 */2 * * *")
    assert next_due(s, last_run_at=datetime(2026, 10, 10, 4, 0), created_at=CREATED) == datetime(2026, 10, 10, 6, 0)


@pytest.mark.parametrize("kwargs", [{}, {"every_seconds": 60, "daily_at": "03:00"},
                                    {"every_seconds": 10}, {"daily_at": "25:00"}, {"daily_at": "3:00"},
                                    {"cron": "nope"}])
def test_validate_rejects(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Schedule(**kwargs).validate()


@pytest.mark.parametrize(("s", "text"), [
    (Schedule(every_seconds=60), "jede Minute"), (Schedule(every_seconds=300), "alle 5 Minuten"),
    (Schedule(every_seconds=90), "alle 90 Sekunden"), (Schedule(every_seconds=3600), "jede Stunde"),
    (Schedule(every_seconds=7200), "alle 2 Stunden"), (Schedule(daily_at="03:00"), "täglich um 03:00 UTC"),
    (Schedule(cron="0 3 * * 1"), "nach Zeitplan „0 3 * * 1“ (UTC)"),
])
def test_describe(s: Schedule, text: str) -> None:
    assert s.describe() == text
```

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run python -m pytest -q tests/test_cron.py tests/test_plugins_schedule.py` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement.** `CronExpr` stores five `frozenset[int]` (minute 0–59, hour 0–23, dom 1–31, month 1–12, dow 0–6 with 7 mapped to 0) plus `dom_restricted`/`dow_restricted` flags (field text != `*`). `parse` splits on whitespace (exactly 5 fields), each field on `,`, each part `*`, `*/n`, `a`, `a-b`, `a-b/n`, `a/n` (= `a-max/n`); `n >= 1`; `a <= b`; out of range → `ValueError(f"Ungültiger Zeitplan „{expr}“: …")`. `next_after(dt)`: start = `dt.replace(second=0, microsecond=0) + timedelta(minutes=1)`; loop over days `day = start.date()` for at most 366 * 8 days: day matches when `month in months` and (dom/dow rule: both restricted → `dom ok or dow ok`; else both must match, `*` always matches; dow from `(day.weekday() + 1) % 7`); on the first matching day pick the first `(hour, minute)` in sorted product that is `>= (start.hour, start.minute)` when `day == start.date()`, else the first overall; none left that day → next day. Raise `ValueError` if nothing found (cannot happen for valid input). `Schedule.validate`: exactly one of the three set; `every_seconds >= 30`; `daily_at` matches `^([01]\d|2[0-3]):[0-5]\d$`; `cron` parses. `next_due` as in P9 (daily → `CronExpr.parse(f"{mm} {hh} * * *")`).

- [ ] **Step 4: Run to verify pass** — same command → PASS.

- [ ] **Step 5: Lint and commit** (gate as in Task 1; stage `backend/src/tiqora/util/cron.py backend/src/tiqora/plugins/tasks.py backend/tests/test_cron.py backend/tests/test_plugins_schedule.py`; message `feat(plugins): cron expressions and task schedules`).

### Task 3: Registrar, plugin context, loader, extension points, SDK facade (backend-only, no DB)

**Files:**
- Create: `backend/src/tiqora/plugins/registrar.py`, `backend/src/tiqora/plugins/loader.py`, `backend/src/tiqora/plugins/context.py`, `backend/src/tiqora/plugins/routes.py` (only `PluginRoute`, `PLUGIN_ROUTES`), `backend/src/tiqora/plugins/events.py` (only `TicketEvent`, `EventContext`, `EventHandler`, `EventSubscriber` incl. `matches`, `EVENT_SUBSCRIBERS`), `backend/src/tiqora/plugins/mcp.py` (only `McpTool`, `MCP_TOOLS`, `CORE_MCP_TOOL_NAMES`), `backend/src/tiqora_sdk/mcp.py`
- Modify: `backend/src/tiqora/plugins/tasks.py` (add `TaskContext`, `TaskRun`, `ScheduledTask`, `SCHEDULED_TASKS`), `backend/src/tiqora/jobs/registry.py` (`JOB_HANDLERS` gets `sdk_stable=True`), `backend/src/tiqora/settings_sections/section.py` (`SETTINGS_SECTIONS` gets `sdk_stable=True`), `backend/src/tiqora_sdk/__init__.py` (full facade, P3), `backend/tests/plugins/fixtures/tq_fixture_hello/plugin.py` (complete fixture), `backend/tests/test_admin_extensions_api.py` (it asserts `sdk_stable is False` for `core.channels` — still true; add an assert that `core.job_handlers` is now `True`)
- Create fixtures: `backend/tests/plugins/fixtures/tq_fixture_broken/{__init__.py,tiqora-plugin.toml}`, `tq_fixture_slow/{__init__.py,tiqora-plugin.toml}`, `tq_fixture_undeclared/{__init__.py,tiqora-plugin.toml}`
- Test: `backend/tests/test_plugins_registrar.py`, `backend/tests/test_plugins_loader.py`, `backend/tests/test_plugins_context_http.py`, `backend/tests/test_tiqora_sdk_surface.py`

**Interfaces:**
- Consumes: Task 1 (`DiscoveredPlugin`, `PluginManifest`, `discover_plugins`, fixture installer), Task 2 (`Schedule`), wave 0 (`Registry`, `ExtensionPoint`, `SettingsSection`, `validate_section_schema`, `UnsupportedSettingsSchema`, `JobHandler`, `JOB_HANDLERS`, `SETTINGS_SECTIONS`, `pin_outbound_url`).
- Produces: P4 (`PluginRegistrar` + methods, `RegistrationRejected`, `PluginLoadTimeout`, `run_in_daemon_thread`, `import_and_register`, `commit_registrar`, `dependency_order`), P6 (`PluginContext`, `PluginHttp`, `OutboundHostNotAllowed`, `PluginTickets`, `TicketSummary` — `PluginTickets.get` and `PluginContext.settings` are implemented here but DB-tested in Task 5), point constants `PLUGIN_ROUTES`, `EVENT_SUBSCRIBERS`, `SCHEDULED_TASKS`, `MCP_TOOLS`, `CORE_MCP_TOOL_NAMES`, `make_plugin_context(plugin: DiscoveredPlugin) -> PluginContext` (in `context.py`), `tiqora_sdk.__all__` (P3).

- [ ] **Step 1: Write the fixture plugins**

`tq_fixture_hello/plugin.py` (complete; used by every later backend task):

```python
"""Fixture plugin using every phase-1 extension point through the public SDK only."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastmcp import Context
from pydantic import BaseModel, Field, SecretStr

from tiqora_sdk import (
    CurrentAgent,
    EventContext,
    JobContext,
    JobHandler,
    PluginRegistry,
    TaskContext,
    TicketEvent,
)

#: test hooks (module state, reset by tests)
RECEIVED: list[tuple[str, int, str]] = []      # (subscriber, history id, event)
FAIL_ON: set[int] = set()                       # history ids the "flaky" subscriber fails on
SLOW_SECONDS: list[float] = [0.0]               # sleep in the flaky subscriber
TASK_RUNS: list[str] = []
TASK_FAIL: list[bool] = [False]
ECHO_CALLS: list[str] = []


class HelloSettings(BaseModel):
    greeting: str = Field("Hallo", title="Begrüßung")
    api_token: SecretStr | None = Field(None, title="Zugangsschlüssel")


class EchoPayload(BaseModel):
    text: str


router = APIRouter()
public_router = APIRouter()


@router.get("/hello")
async def hello(user: CurrentAgent) -> dict[str, str]:
    return {"hello": user.first_name}


@router.post("/echo")
async def echo(body: EchoPayload) -> dict[str, str]:
    ECHO_CALLS.append(body.text)
    return {"text": body.text}


@public_router.get("/ping")
async def ping(request: Request) -> dict[str, Any]:
    return {"pong": True, "app": request.app.state.settings.app_name}


async def on_ticket(ctx: EventContext, event: TicketEvent) -> None:
    RECEIVED.append(("notify", event.id, event.event))


async def flaky(ctx: EventContext, event: TicketEvent) -> None:
    if SLOW_SECONDS[0]:
        await asyncio.sleep(SLOW_SECONDS[0])
    if event.id in FAIL_ON:
        raise RuntimeError(f"kaputt bei {event.id}")
    RECEIVED.append(("flaky", event.id, event.event))


async def tick(ctx: TaskContext) -> dict[str, Any]:
    TASK_RUNS.append(ctx.scheduled_for.isoformat())
    if TASK_FAIL[0]:
        raise RuntimeError("Aufgabe fehlgeschlagen")
    return {"ok": 1}


async def run_echo_job(ctx: JobContext, payload: EchoPayload) -> dict[str, Any]:
    return {"echo": payload.text}


async def echo_tool(ctx: Context, text: str, times: int = 1) -> dict[str, Any]:
    """Echo text back.

    Parameters
    ----------
    text: The text to echo.
    times: How often.
    """
    return {"text": text * times}


class HelloPlugin:
    def register(self, registry: PluginRegistry) -> None:
        registry.settings(HelloSettings)
        registry.route(router)
        registry.route(public_router, public=True)
        registry.on_event("notify", ["TicketCreate", "TicketStateUpdate"], on_ticket)
        registry.on_event("flaky", ["*"], flaky, timeout_seconds=1)
        registry.scheduled_task("tick", tick, every_seconds=60)
        registry.job_handler(
            JobHandler(kind="org.example.hello.echo", payload_model=EchoPayload, run=run_echo_job,
                       title_key="plugin.org.example.hello:jobs.echo")
        )
        registry.mcp_tool("echo", echo_tool, description="Gibt den Text zurück.")


plugin = HelloPlugin()
```

`tq_fixture_broken` (id `org.example.broken`, short_id `broken`, capabilities `["core.job_handlers"]`, no frontend): `register` calls `registry.job_handler(JobHandler(kind="org.example.broken.one", …))` and then `raise RuntimeError("register kaputt")`. `tq_fixture_slow` (id `org.example.slow`, short_id `slow`, no capabilities): module body `time.sleep(float(os.environ.get("TQ_SLOW_SECONDS", "3")))`, then a `plugin` whose `register` does nothing. `tq_fixture_undeclared` (id `org.example.undeclared`, short_id `undecl`, capabilities `["core.job_handlers"]`): `register` calls `registry.mcp_tool("x", fn, description="x")`.

- [ ] **Step 2: Write the failing tests**

`backend/tests/test_plugins_registrar.py` (build registrars directly from parsed fixture manifests; `ctx = make_plugin_context(discovered)` or a `PluginContext` built with `PluginHttp([])`, `PluginTickets()`):

```python
def test_settings_registers_section_with_plugin_key(registrar: PluginRegistrar) -> None:
    section = registrar.settings(HelloSettings)
    assert section.key == "plugins.org.example.hello"
    assert section.title_key == "plugin.org.example.hello:settings.title"
    assert section.i18n_prefix == "plugin.org.example.hello:settings"
    assert (SETTINGS_SECTIONS, section) in registrar.staged


def test_settings_twice_rejected(registrar) -> None:
    registrar.settings(HelloSettings)
    with pytest.raises(RegistrationRejected, match="Einstellungen"):
        registrar.settings(HelloSettings)


def test_settings_field_without_default_rejected(registrar) -> None:
    class Bad(BaseModel):
        x: int
    with pytest.raises(RegistrationRejected, match="x"):
        registrar.settings(Bad)


@pytest.mark.parametrize("kind", ["core.ticket_export", "org.example.other.x", "org.example.hello", "org.example.hello.Bad"])
def test_namespace_rules_reject(registrar, kind: str) -> None:
    with pytest.raises(RegistrationRejected):
        registrar.job_handler(JobHandler(kind=kind, payload_model=EchoPayload, run=run_echo_job, title_key="t"))


@pytest.mark.parametrize("name", ["Bad", "x", "bad-name", "a" * 42])
def test_bad_names_rejected(registrar, name: str) -> None:
    with pytest.raises(RegistrationRejected):
        registrar.on_event(name, ["TicketCreate"], on_ticket)


def test_undeclared_point_rejected(undeclared_registrar) -> None:
    with pytest.raises(RegistrationRejected, match="core.mcp.tools"):
        undeclared_registrar.mcp_tool("x", echo_tool, description="x")


def test_mcp_tool_name_collision_with_core_rejected() -> None:
    reg = _registrar(short_id="list")
    with pytest.raises(RegistrationRejected, match="list_queues"):
        reg.mcp_tool("queues", echo_tool, description="x")


def test_route_keys_numbered(registrar) -> None:
    registrar.route(APIRouter())
    registrar.route(APIRouter(), public=True)
    keys = [item.key for point, item in registrar.staged if point is PLUGIN_ROUTES]
    assert keys == ["org.example.hello:0", "org.example.hello:1"]


def test_scheduled_task_requires_exactly_one_schedule(registrar) -> None:
    with pytest.raises(RegistrationRejected):
        registrar.scheduled_task("t", tick)
    with pytest.raises(RegistrationRejected, match="Zeitplan"):
        registrar.scheduled_task("t", tick, cron="61 * * * *")


@pytest.mark.parametrize("timeout", [0, 301])
def test_event_timeout_bounds(registrar, timeout: int) -> None:
    with pytest.raises(RegistrationRejected):
        registrar.on_event("n", ["TicketCreate"], on_ticket, timeout_seconds=timeout)


def test_empty_events_rejected(registrar) -> None:
    with pytest.raises(RegistrationRejected):
        registrar.on_event("n", [], on_ticket)


def test_internal_point_refused(registrar) -> None:
    with pytest.raises(RegistrationRejected, match="core.channels"):
        registrar._stage(CHANNELS, object())  # the only generic entry; refuses sdk_stable=False points
```

`backend/tests/test_plugins_loader.py`:

```python
def test_import_and_register_fixture(fixture_plugins) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = import_and_register(p, make_plugin_context(p))
    points = sorted({point.key for point, _ in reg.staged})
    assert points == ["core.events.subscribers", "core.http.routes", "core.job_handlers",
                      "core.mcp.tools", "core.settings_sections", "core.worker.scheduled_tasks"]


def test_commit_registers_with_owner(fixture_plugins) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    registry = Registry(); register_core(registry)
    committed = commit_registrar(registry, import_and_register(p, make_plugin_context(p)))
    assert ("core.mcp.tools", "hello_echo") in committed
    owners = {r.owner for r in registry.registrations() if r.key.startswith(("org.example.hello", "plugins.org", "hello_"))}
    assert owners == {"org.example.hello"}
    assert registry.get(CHANNELS, "telegram") is not None          # core untouched


def test_register_raises_leaves_no_items(fixture_plugins) -> None:
    fixture_plugins("tq_fixture_broken", plugin_id="org.example.broken")
    (p,) = discover_plugins()
    registry = Registry(); register_core(registry)
    before = len(registry.registrations())
    with pytest.raises(RuntimeError, match="register kaputt"):
        import_and_register(p, make_plugin_context(p))
    assert len(registry.registrations()) == before
    assert registry.get(JOB_HANDLERS, "org.example.broken.one") is None


def test_duplicate_with_core_rejected_core_kept(fixture_plugins) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    registry = Registry(); register_core(registry)
    squatter = JobHandler(kind="org.example.hello.echo", payload_model=EchoPayload, run=run_echo_job, title_key="t")
    registry.add(JOB_HANDLERS, squatter)                               # owner "core"
    with pytest.raises(RegistrationRejected, match="org.example.hello.echo"):
        commit_registrar(registry, import_and_register(p, make_plugin_context(p)))
    assert registry.get(SETTINGS_SECTIONS, "plugins.org.example.hello") is None   # nothing half-added
    assert registry.get(JOB_HANDLERS, "org.example.hello.echo") is squatter


async def test_run_in_daemon_thread_returns_and_raises() -> None:
    assert await run_in_daemon_thread(lambda: 42, timeout=1) == 42
    def boom() -> None: raise ValueError("x")
    with pytest.raises(ValueError, match="x"):
        await run_in_daemon_thread(boom, timeout=1)


async def test_import_timeout_reports_failure(fixture_plugins, monkeypatch) -> None:
    monkeypatch.setenv("TQ_SLOW_SECONDS", "2")
    fixture_plugins("tq_fixture_slow", plugin_id="org.example.slow")
    (p,) = discover_plugins()
    started = time.monotonic()
    with pytest.raises(PluginLoadTimeout):
        await run_in_daemon_thread(lambda: import_and_register(p, make_plugin_context(p)), timeout=0.2)
    assert time.monotonic() - started < 1.0
    assert all(t.daemon for t in threading.enumerate() if t.name.startswith("tiqora-plugin-load"))


def test_dependency_order_and_cycle() -> None:
    a = _discovered("org.example.a", requires={"org.example.b": ">=1.0"})
    b = _discovered("org.example.b")
    ordered, problems = dependency_order([a, b])
    assert [p.plugin_id for p in ordered] == ["org.example.b", "org.example.a"] and problems == {}
    c = _discovered("org.example.c", requires={"org.example.d": ">=0"})
    d = _discovered("org.example.d", requires={"org.example.c": ">=0"})
    ordered, problems = dependency_order([c, d])
    assert ordered == [] and set(problems) == {"org.example.c", "org.example.d"}
```

(`_discovered(...)` builds a `DiscoveredPlugin` dataclass in-memory with a `PluginManifest` from `model_validate`; threads are started with `name=f"tiqora-plugin-load-{id}"`.)

`backend/tests/test_plugins_context_http.py`:

```python
@pytest.mark.parametrize(("host", "ok"), [
    ("api.example.org", True), ("API.Example.ORG", True), ("a.example.net", True), ("x.y.example.net", True),
    ("example.net", False), ("evil-example.net", False), ("api.example.org.evil.test", False),
])
def test_host_allowed(host: str, ok: bool) -> None:
    assert PluginHttp(["api.example.org", "*.example.net"]).host_allowed(host) is ok


async def test_disallowed_host_never_connects() -> None:
    called: list[httpx.Request] = []
    http = PluginHttp(["api.example.org"], transport=httpx.MockTransport(lambda r: called.append(r) or httpx.Response(200)))
    with pytest.raises(OutboundHostNotAllowed):
        await http.request("GET", "https://other.example.org/x")
    assert called == []


async def test_private_ip_blocked() -> None:
    http = PluginHttp(["api.example.org"], resolver=lambda host, port: ["127.0.0.1"])
    with pytest.raises(OutboundURLError):
        await http.request("GET", "https://api.example.org/x")


async def test_request_goes_to_pinned_ip_with_host_header() -> None:
    seen: list[httpx.Request] = []
    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r); return httpx.Response(200, json={"ok": True})
    http = PluginHttp(["api.example.org"], resolver=lambda h, p: ["93.184.215.14"], transport=httpx.MockTransport(handler))
    resp = await http.request("POST", "https://api.example.org/v1/x", json={"a": 1})
    assert resp.json() == {"ok": True}
    assert seen[0].url.host == "93.184.215.14" and seen[0].headers["host"] == "api.example.org"
```

(`PluginHttp.__init__(self, allowed_hosts, *, resolver: HostResolver | None = None, transport: httpx.AsyncBaseTransport | None = None)` — the two keyword arguments exist for tests only and are passed through to `pin_outbound_url(resolver=…)` / `httpx.AsyncClient(transport=…)`.)

`backend/tests/test_tiqora_sdk_surface.py`:

```python
EXPECTED = {"SDK_VERSION", "Plugin", "PluginRegistry", "RegistrationRejected", "PluginContext", "PluginHttp",
            "OutboundHostNotAllowed", "TicketSummary", "TicketEvent", "EventContext", "EventHandler",
            "TaskContext", "TaskRun", "JobHandler", "JobContext", "JobRetry", "enqueue_job",
            "CurrentAgent", "AdminAgent", "DbSession"}


def test_surface_is_exactly_the_planned_one() -> None:
    import tiqora_sdk
    assert set(tiqora_sdk.__all__) == EXPECTED
    for name in EXPECTED:
        assert getattr(tiqora_sdk, name) is not None
    assert tiqora_sdk.SDK_VERSION == "0.1.0"


def test_registry_protocol_matches_registrar() -> None:
    from tiqora.plugins.registrar import PluginRegistrar
    from tiqora_sdk import PluginRegistry
    public = {n for n in vars(PluginRegistrar) if not n.startswith("_") and n != "staged"}
    proto = {n for n in vars(PluginRegistry) if not n.startswith("_")}
    assert public == proto == {"plugin_id", "context", "settings", "route", "on_event",
                               "scheduled_task", "job_handler", "mcp_tool"}


def test_mcp_helpers() -> None:
    from tiqora_sdk import mcp
    assert {"current_agent_id", "mcp_session_factory", "permission_engine"} <= set(mcp.__all__)


def test_phase1_points_are_sdk_stable() -> None:
    assert all(p.sdk_stable for p in (SETTINGS_SECTIONS, JOB_HANDLERS, PLUGIN_ROUTES, EVENT_SUBSCRIBERS,
                                      SCHEDULED_TASKS, MCP_TOOLS))
    assert CHANNELS.sdk_stable is False
```

- [ ] **Step 3: Run to verify failure** — `cd backend && uv run python -m pytest -q tests/test_plugins_registrar.py tests/test_plugins_loader.py tests/test_plugins_context_http.py tests/test_tiqora_sdk_surface.py` → FAIL (imports).

- [ ] **Step 4: Implement** per P4/P6/P8/P9/P10 data definitions. Notes:
  - `PluginRegistrar._stage(point, item)` is the single internal staging path: refuses `point.sdk_stable is False` ("Erweiterungspunkt core.channels ist für Erweiterungen nicht freigegeben"), refuses points not in `manifest.capabilities.extension_points` ("Erweiterungspunkt core.mcp.tools ist im Manifest nicht angegeben"), refuses a staged duplicate key.
  - `CORE_MCP_TOOL_NAMES`: copy the `@mcp.tool` names from `backend/src/tiqora/mcp_server/server.py` (`grep -n "^async def " …` under each decorator) as a literal frozenset; Task 9 adds the equality test against the live server.
  - `run_in_daemon_thread`: `loop = asyncio.get_running_loop(); fut = loop.create_future()`; thread target sets `loop.call_soon_threadsafe(_set, result_or_exc)` guarded by `if not fut.done()`; `await asyncio.wait_for(asyncio.shield(fut), timeout)` → on `TimeoutError` raise `PluginLoadTimeout(f"Laden dauerte länger als {timeout:g} Sekunden")`.
  - `import_and_register` calls `ep.load()` on the `EntryPoint` found again via `importlib.metadata.entry_points(group=…, name=plugin.plugin_id)` (keep the `DiscoveredPlugin` free of non-picklable objects); object without callable `register` → `RegistrationRejected("Einstiegspunkt liefert kein Erweiterungsobjekt mit register()")`.
  - `make_plugin_context(plugin)` → `PluginContext(plugin_id, version, http=PluginHttp(manifest.capabilities.outbound_hosts), tickets=PluginTickets())`.
  - `tiqora_sdk/__init__.py` re-exports exactly `EXPECTED`; `Plugin`/`PluginRegistry` are `typing.Protocol` classes defined in `tiqora_sdk/__init__.py` (the registry protocol lists the 8 members with the same signatures as P4 — `plugin_id`/`context` as read-only properties).
  - `PluginTickets.get` SQL: `SELECT t.id, t.tn, t.title, t.queue_id, q.name, s.name, st.name, p.name, t.user_id, t.customer_user_id, t.customer_id FROM ticket t JOIN queue q ON q.id = t.queue_id JOIN ticket_state s ON s.id = t.ticket_state_id JOIN ticket_state_type st ON st.id = s.type_id JOIN ticket_priority p ON p.id = t.ticket_priority_id WHERE t.id = :id` (Znuny tables only, integer joins).
  - `PluginContext.settings()`: looks up `get_registry().get(SETTINGS_SECTIONS, f"plugins.{plugin_id}")`.

- [ ] **Step 5: Run to verify pass** — same command → PASS; also `uv run python -m pytest -q tests/test_admin_extensions_api.py tests/test_settings_sections.py tests/test_jobs_service.py` → PASS.

- [ ] **Step 6: Lint and commit** — gate; stage the files listed above explicitly; message `feat(plugins): staging registrar, loader with import timeout, plugin context and SDK facade`.

### Task 4: Plugin state tables, bootstrap with safe mode and crash quarantine, startup wiring (backend-only)

Seed: no Znuny rows; plugin ids `org.example.plg49a` … `org.example.plg49z` (cleanup `DELETE … WHERE plugin_id LIKE 'org.example.plg49%'` / `id LIKE …` in all three tables). The fixture plugin rows (`org.example.hello`, `org.example.broken`, `org.example.slow`) are deleted by the same module fixture.

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_plugins.py`, `backend/src/tiqora/plugins/state.py`, `backend/src/tiqora/plugins/store.py`, `backend/src/tiqora/plugins/bootstrap.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (`TiqoraPlugin`, `TiqoraPluginRuntime`, `TiqoraPluginHook`), `backend/src/tiqora/extensions/core.py` + `extensions/__init__.py` (`install_registry`), `backend/src/tiqora/config.py` (P5 fields + `plugins_disabled_ids`), `backend/src/tiqora/api/app.py` (lifespan: replace the `get_registry()` call with `await init_registry(role="api", settings=settings, session_factory=app.state.session_factory)`), `backend/src/tiqora/worker/__main__.py` (`run_worker`: replace `get_registry()` with `loop.run_until_complete(init_registry(role="worker", settings=settings, session_factory=get_session_factory()))`, before `_ensure_legacy_schema_at_worker_start`), `backend/src/tiqora/mcp_server/server.py` (`_app_lifespan`: after `McpState`, `await init_registry(role="mcp", settings=settings, session_factory=_mcp_state.session_factory)`), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (version assert)
- Test: `backend/tests/test_plugins_store.py`, `backend/tests/test_plugins_bootstrap.py`, `backend/tests/test_tiqora_model_ddl.py` (runs unchanged; must pass)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: P5 tables/models; `state.py` (P5); `store.py`:

```python
async def prepare_boot(factory, plugins: Sequence[DiscoveredPlugin], *, role: PluginRole, auto_enable: bool,
                       attempts: int = 3, delay_seconds: float | None = None) -> dict[str, BootInfo]
    # delay None -> module constant BOOT_RETRY_DELAY_SECONDS (2.0; tests monkeypatch it)
    # sync_discovered + runtime rows for role, in one session; retries the whole thing on OperationalError/OSError
@dataclass(frozen=True) class BootInfo: enabled: bool; boot_attempts: int
async def sync_discovered(session, plugins, *, auto_enable: bool, now: datetime) -> dict[str, TiqoraPlugin]
    # insert missing (enabled=auto_enable, source="image", installed_at=updated_at=now); update name/version
    # (+updated_at) when changed; never touches enabled of existing rows; IntegrityError from a concurrent boot
    # -> rollback and re-read; plugins with manifest None use dist name as name
async def mark_loading(factory, plugin_id, role, version, now) -> int        # upsert, boot_attempts + 1, commit
async def mark_result(factory, plugin_id, role, *, state: RuntimeState, version: str | None,
                      error: str | None, now) -> None                        # upsert; boot_attempts 0 unless QUARANTINED
async def set_enabled(session, plugin_id, enabled: bool, *, by: int | None, now) -> TiqoraPlugin | None   # flush only
async def reset_plugin(session, plugin_id, *, now) -> None
    # runtime rows: boot_attempts 0, QUARANTINED -> DISABLED; hook rows: tripped_at NULL, consecutive_failures 0; flush
async def list_plugin_rows(session) -> dict[str, TiqoraPlugin]
async def list_runtime_rows(session, plugin_ids: Sequence[str] | None = None) -> list[TiqoraPluginRuntime]
```

`bootstrap.init_registry` (P5), `extensions.core.install_registry(registry)`, settings `plugins_*`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_plugins_store.py` (both dialects; tables via `TiqoraBase.metadata.create_all`):

```python
async def test_sync_inserts_with_auto_enable_and_keeps_enabled(session) -> None:
    plugins = [_discovered("org.example.plg49a", version="1.0.0")]
    rows = await sync_discovered(session, plugins, auto_enable=True, now=T0)
    assert rows["org.example.plg49a"].enabled is True and rows["org.example.plg49a"].source == "image"
    await set_enabled(session, "org.example.plg49a", False, by=1, now=T0)
    rows = await sync_discovered(session, [_discovered("org.example.plg49a", version="1.1.0")],
                                 auto_enable=True, now=T1)
    assert rows["org.example.plg49a"].enabled is False            # admin choice survives
    assert rows["org.example.plg49a"].version == "1.1.0" and rows["org.example.plg49a"].updated_at == T1


async def test_auto_enable_off(session) -> None:
    rows = await sync_discovered(session, [_discovered("org.example.plg49b")], auto_enable=False, now=T0)
    assert rows["org.example.plg49b"].enabled is False


async def test_mark_loading_counts_and_result_resets(factory) -> None:
    assert await mark_loading(factory, "org.example.plg49c", "api", "1.0.0", T0) == 1
    assert await mark_loading(factory, "org.example.plg49c", "api", "1.0.0", T0) == 2
    await mark_result(factory, "org.example.plg49c", "api", state=RuntimeState.ACTIVE, version="1.0.0", error=None, now=T1)
    (row,) = await _runtime(factory, "org.example.plg49c")
    assert (row.state, row.boot_attempts, row.updated_at) == ("active", 0, T1)


async def test_reset_plugin(factory) -> None:
    await mark_result(factory, "org.example.plg49d", "worker", state=RuntimeState.QUARANTINED, version="1", error="x", now=T0)
    # one tripped hook row
    async with factory() as s:
        s.add(TiqoraPluginHook(hook_key="event:org.example.plg49d:n", plugin_id="org.example.plg49d", kind="event",
                               cursor=5, consecutive_failures=5, total_failures=7, tripped_at=T0, created_at=T0))
        await s.commit()
    async with factory() as s:
        await reset_plugin(s, "org.example.plg49d", now=T1); await s.commit()
    (row,) = await _runtime(factory, "org.example.plg49d")
    assert (row.state, row.boot_attempts) == ("disabled", 0)
    hook = await _hook(factory, "event:org.example.plg49d:n")
    assert hook.tripped_at is None and hook.consecutive_failures == 0 and hook.cursor == 5 and hook.total_failures == 7
```

`backend/tests/test_plugins_bootstrap.py` (both dialects; `settings = Settings(environment="test", database_url=<dialect url>)` with overrides via `model_copy(update=…)`; each test ends with `set_registry_for_tests(None)` and `set_loaded_plugins([])`; helpers shared with `test_plugins_store.py`: `_runtime(factory, plugin_id, role=None) -> list[TiqoraPluginRuntime]`, `_hook(factory, key) -> TiqoraPluginHook`, `_seed_plugin_row(factory, plugin_id, *, enabled)` inserts a `tiqora_plugin` row with `version="1.0.0"`, `source="image"`, timestamps `T0`; `_discovered(plugin_id, version="1.0.0")` builds an in-memory `DiscoveredPlugin` with a minimal valid manifest; `T0 = datetime(2026, 10, 10, 8, 0)`, `T1 = T0 + timedelta(hours=1)`):

```python
async def test_safe_mode_imports_nothing(fixture_plugins, factory, settings) -> None:
    fixture_plugins()
    reg = await init_registry(role="api", settings=settings.model_copy(update={"plugins_safe_mode": True}),
                              session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert {r.owner for r in reg.registrations()} == {"core"}
    assert loaded_plugins() == {} and reg.frozen and get_registry() is reg


async def test_loads_enabled_plugin(fixture_plugins, factory, settings) -> None:
    fixture_plugins()
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    assert reg.get(SETTINGS_SECTIONS, "plugins.org.example.hello") is not None
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.ACTIVE
    (row,) = await _runtime(factory, "org.example.hello", role="api")
    assert (row.state, row.boot_attempts, row.version) == ("active", 0, "1.0.0")


async def test_disabled_plugin_not_imported(fixture_plugins, factory, settings) -> None:
    fixture_plugins()
    await _seed_plugin_row(factory, "org.example.hello", enabled=False)
    await init_registry(role="worker", settings=settings, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.DISABLED


async def test_env_disabled_not_imported(fixture_plugins, factory, settings) -> None:
    fixture_plugins()
    s = settings.model_copy(update={"plugins_disabled": " org.example.hello , other"})
    await init_registry(role="api", settings=s, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules


async def test_crash_loop_quarantines_after_three_boots(fixture_plugins, factory, settings) -> None:
    fixture_plugins()
    for _ in range(3):                         # three boots that died between mark_loading and mark_result
        await mark_loading(factory, "org.example.hello", "api", "1.0.0", T0)
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.QUARANTINED
    (row,) = await _runtime(factory, "org.example.hello", role="api")
    assert row.state == "quarantined" and row.boot_attempts == 3


async def test_two_crashes_still_loads(fixture_plugins, factory, settings) -> None:
    fixture_plugins()
    for _ in range(2):
        await mark_loading(factory, "org.example.hello", "api", "1.0.0", T0)
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.ACTIVE


async def test_failed_register_marks_failed_core_ok(fixture_plugins, factory, settings) -> None:
    fixture_plugins("tq_fixture_broken", plugin_id="org.example.broken")
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    result = loaded_plugins()["org.example.broken"]
    assert result.state is RuntimeState.FAILED and "RuntimeError: register kaputt" in (result.error or "")
    assert reg.get(JOB_HANDLERS, "org.example.broken.one") is None
    assert reg.get(CHANNELS, "telegram") is not None


async def test_slow_import_fails_with_timeout(fixture_plugins, factory, settings, monkeypatch) -> None:
    monkeypatch.setenv("TQ_SLOW_SECONDS", "3")
    fixture_plugins("tq_fixture_slow", plugin_id="org.example.slow")
    await init_registry(role="api", settings=settings.model_copy(update={"plugins_import_timeout_seconds": 1}),
                        session_factory=factory)
    assert loaded_plugins()["org.example.slow"].state is RuntimeState.FAILED
    assert "länger als 1 Sekunden" in (loaded_plugins()["org.example.slow"].error or "")


async def test_db_unavailable_loads_no_plugins_core_boots(fixture_plugins, settings, monkeypatch) -> None:
    fixture_plugins()
    monkeypatch.setattr("tiqora.plugins.store.BOOT_RETRY_DELAY_SECONDS", 0.0)
    dead = async_sessionmaker(create_async_engine("mysql+aiomysql://u:p@127.0.0.1:1/x"))
    reg = await init_registry(role="api", settings=settings, session_factory=dead)
    assert "tq_fixture_hello" not in sys.modules
    assert reg.get(CHANNELS, "telegram") is not None and loaded_plugins() == {}


async def test_missing_required_plugin_fails(fixture_plugins, factory, settings) -> None:
    fixture_plugins(manifest_overrides={"requires": {"org.example.base": ">=1.0"}})
    await init_registry(role="api", settings=settings, session_factory=factory)
    result = loaded_plugins()["org.example.hello"]
    assert result.state is RuntimeState.FAILED and "org.example.base" in (result.error or "")
    assert "tq_fixture_hello" not in sys.modules


async def test_incompatible_sdk_not_loaded(fixture_plugins, factory, settings) -> None:
    fixture_plugins(manifest_overrides={"sdk": ">=9"})
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.INCOMPATIBLE
    assert "tq_fixture_hello" not in sys.modules


def test_startup_wiring_calls_init_registry() -> None:
    import inspect
    from tiqora.api import app as app_mod
    from tiqora.mcp_server import server as mcp_mod
    from tiqora.worker import __main__ as worker_mod
    for fn, role in ((app_mod.lifespan, "api"), (worker_mod.run_worker, "worker"),
                     (mcp_mod._app_lifespan, "mcp")):
        src = inspect.getsource(fn)
        assert "init_registry(" in src and f'role="{role}"' in src
```

Also add to `test_plugins_bootstrap.py` a pure test of `summarize_status` covering every branch listed in P5 (8 parametrized cases, e.g. `(enabled=True, problem=None, api_state=ACTIVE, compat=None) -> "active"`, `(False, None, ACTIVE, None) -> "restart_required"`, `(True, None, DISABLED, None) -> "restart_required"`, `(False, None, DISABLED, None) -> "disabled"`, `(True, "x", None, None) -> "invalid"`, `(True, None, None, "y") -> "incompatible"`, `(True, None, QUARANTINED, None) -> "quarantined"`, `(True, None, None, None) -> "unknown"`).

- [ ] **Step 2: Run to verify failure** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_plugins_store.py tests/test_plugins_bootstrap.py` → FAIL (imports).

- [ ] **Step 3: Write the migration and models.** Migration (`down_revision` = current head; check first): the three tables exactly as P5 (`sa.Boolean()` for `enabled`, `server_default=sa.text("'unsigned'")` for `signature_status`, `server_default="0"` for the integer counters, `sa.PrimaryKeyConstraint("plugin_id", "role")`, `op.create_index("ix_tiqora_plugin_hook_plugin_id", "tiqora_plugin_hook", ["plugin_id"])`); downgrade drops in reverse. Models with identical DDL. Update `TIQORA_HEAD` and the `test_migrate_cli.py` assert. Run `cd backend && uv run python -m pytest -q tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py` → PASS.

- [ ] **Step 4: Implement** `state.py`, `store.py` (`BOOT_RETRY_DELAY_SECONDS: float = 2.0` module constant used by `prepare_boot`), `bootstrap.py` (P5 algorithm; per-plugin `structlog` events `plugin_loaded` / `plugin_load_failed` / `plugin_skipped` with `plugin`, `role`, `state`), `install_registry`, config fields, and the three startup call sites. In `api/app.py` keep the comment "Build + freeze the extension registry now so a broken registration fails at boot" next to the new call.

- [ ] **Step 5: Run to verify pass** — the Step 2 command → PASS; then `uv run python -m pytest -q tests/test_admin_extensions_api.py tests/test_mcp_server.py tests/test_jobs_runner.py` → PASS (startup changes must not break them).

- [ ] **Step 6: Lint and commit** — gate; stage migration, models, `plugins/state.py`, `plugins/store.py`, `plugins/bootstrap.py`, `extensions/core.py`, `extensions/__init__.py`, `config.py`, `api/app.py`, `worker/__main__.py`, `mcp_server/server.py`, the two head tests and the two new test modules; message `feat(plugins): plugin state tables, bootstrap with safe mode and crash quarantine`.

### Task 5: Plugin HTTP routes, settings form and context data access (backend-only)

Seed band **49300–49349**: ticket `49300` (tn `plg4949300`, title `plg49 ticket`, queue 1, state 1, priority 3, owner 1, customer `plg49-cust` / `plg49.cust@example.org`); settings rows `plugins.org.example.hello.%` deleted before/after.

**Files:**
- Modify: `backend/src/tiqora/plugins/routes.py` (`create_plugin_router`, `mount_plugin_routes`), `backend/src/tiqora/api/app.py` (`create_app`: `app.state.plugin_router = create_plugin_router()`; `app.mount("/api/plugins", app.state.plugin_router)` right before `mount_spa`; lifespan: `mount_plugin_routes(app, registry)` right after `init_registry`), `backend/src/tiqora/security/csrf.py` (`_PROTECTED_PREFIXES`), `backend/src/tiqora/plugins/context.py` (`settings(session=None)`, `PluginTickets.get(ticket_id, session=None)`)
- Test: `backend/tests/test_plugins_routes.py`, `backend/tests/test_plugins_context_db.py`, `backend/tests/test_csrf.py` (or whichever existing module tests `csrf_origin_middleware` — `grep -rln "_PROTECTED_PREFIXES\|csrf_origin" backend/tests`; add one case there)

**Interfaces:**
- Consumes: Task 3 (`PLUGIN_ROUTES`, `PluginRoute`, `make_plugin_context`, fixture routes `/hello`, `/echo`, public `/ping`), Task 4 (`init_registry`, `set_loaded_plugins`, `PluginLoadResult`, `RuntimeState`).
- Produces: `create_plugin_router() -> Router`, `mount_plugin_routes(app, registry) -> list[str]`; `PluginContext.settings(session: AsyncSession | None = None) -> BaseModel`; `PluginTickets.get(ticket_id: int, session: AsyncSession | None = None) -> TicketSummary | None` (without `session` both open one from `tiqora.plugins.context.get_session_factory()` — tests monkeypatch that name).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_plugins_routes.py` (no DB; helper `_app_with_hello(fixture_plugins, *, state=RuntimeState.ACTIVE)` → installs the fixture, builds `Registry` + `register_core` + `commit_registrar(import_and_register(...))`, freezes, `set_registry_for_tests(reg)`, `set_loaded_plugins([PluginLoadResult("org.example.hello", "1.0.0", state, None, ())])`, `app = create_app(Settings(environment="test"))`, `mount_plugin_routes(app, reg)`; teardown resets registry and loaded state, clears `ECHO_CALLS`):

```python
ROOT = AuthenticatedUser(id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session")
BASE = "/api/plugins/org.example.hello"


async def test_authenticated_get_reaches_plugin(fixture_plugins) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r = await c.get(f"{BASE}/hello")
    assert r.status_code == 200 and r.json() == {"hello": "Root"}


async def test_anonymous_rejected_unless_public(fixture_plugins) -> None:
    app = _app_with_hello(fixture_plugins)
    async with _client(app) as c:
        assert (await c.get(f"{BASE}/hello")).status_code == 401
        pub = await c.get(f"{BASE}/ping")
    assert pub.status_code == 200 and pub.json() == {"pong": True, "app": "Tiqora"}   # parent app.state shared


async def test_cross_site_post_rejected(fixture_plugins) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    cookie = Settings(environment="test").session_cookie_name
    async with _client(app) as c:
        r = await c.post(f"{BASE}/echo", json={"text": "x"}, cookies={cookie: "abc"},
                         headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403 and ECHO_CALLS == []


async def test_same_origin_post_allowed(fixture_plugins) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    cookie = Settings(environment="test").session_cookie_name
    async with _client(app) as c:
        r = await c.post(f"{BASE}/echo", json={"text": "x"}, cookies={cookie: "abc"},
                         headers={"Origin": "http://test"})
    assert r.status_code == 200 and ECHO_CALLS == ["x"]


@pytest.mark.parametrize("state", [RuntimeState.FAILED, RuntimeState.DISABLED])
async def test_unknown_or_unloaded_plugin_404(fixture_plugins, state: RuntimeState) -> None:
    app = _app_with_hello(fixture_plugins, state=state)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r1 = await c.get(f"{BASE}/hello")
        r2 = await c.get("/api/plugins/org.example.nope/x")
    assert r1.status_code == 404 and r2.status_code == 404
    assert r2.json() == {"detail": "Erweiterung nicht gefunden"}


async def test_plugin_routes_not_in_core_openapi(fixture_plugins) -> None:
    app = _app_with_hello(fixture_plugins)
    assert not [p for p in app.openapi()["paths"] if p.startswith("/api/plugins")]


async def test_subapp_openapi_only_outside_production(fixture_plugins) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r = await c.get(f"{BASE}/openapi.json")
    assert r.status_code == 200 and "/hello" in r.json()["paths"]
```

(`_client(app)` = `AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`.)

In the CSRF test module add: `assert "/api/plugins" in tiqora.security.csrf._PROTECTED_PREFIXES`.

`backend/tests/test_plugins_context_db.py` (both dialects; seeds per band; uses the `fixture_plugins` fixture + a registry containing the hello plugin's settings section; monkeypatch `tiqora.plugins.context.get_session_factory` → the test factory; also an HTTP part against `/api/v1/admin/settings-sections` with `get_admin_user`/`get_db` overrides like `backend/tests/test_admin_settings_sections_api.py`):

```python
async def test_ticket_summary(factory, hello_ctx) -> None:
    t = await hello_ctx.tickets.get(49300)
    assert t is not None
    assert (t.id, t.tn, t.title, t.queue_id, t.owner_id) == (49300, "plg4949300", "plg49 ticket", 1, 1)
    assert t.customer_user_id == "plg49.cust@example.org" and t.state_type in {"new", "open"}
    assert await hello_ctx.tickets.get(49349) is None


async def test_settings_defaults_then_saved_values(factory, hello_ctx, admin_client) -> None:
    s = await hello_ctx.settings()
    assert s.greeting == "Hallo" and s.api_token is None
    listing = (await admin_client.get("/api/v1/admin/settings-sections")).json()
    assert {"key": "plugins.org.example.hello", "title_key": "plugin.org.example.hello:settings.title",
            "owner": "org.example.hello"} in listing
    r = await admin_client.put("/api/v1/admin/settings-sections/plugins.org.example.hello",
                               json={"values": {"greeting": "Moin", "api_token": "geheim-plg49"}})
    assert r.status_code == 200
    body = r.json()
    assert body["values"]["greeting"] == "Moin" and body["values"]["api_token"] == "********"
    assert "geheim-plg49" not in r.text
    s = await hello_ctx.settings()
    assert s.greeting == "Moin" and s.api_token.get_secret_value() == "geheim-plg49"


async def test_settings_missing_section_raises(factory) -> None:
    ctx = PluginContext("org.example.nosettings", "1.0.0", http=PluginHttp([]), tickets=PluginTickets())
    with pytest.raises(LookupError, match="keine Einstellungen"):
        await ctx.settings()
```

- [ ] **Step 2: Run to verify failure** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_plugins_routes.py tests/test_plugins_context_db.py` → FAIL (`ImportError: create_plugin_router` / 404 instead of 200).

- [ ] **Step 3: Implement** P7 + the context changes. `create_plugin_router()` returns `Router(routes=[], default=_not_found)` where `_not_found` is an ASGI callable sending `JSONResponse({"detail": "Erweiterung nicht gefunden"}, status_code=404)`. `mount_plugin_routes` groups `registry.items(PLUGIN_ROUTES)` by `plugin_id`, skips plugins whose `loaded_plugins()` state is not `ACTIVE`, builds the sub-app (P7) and appends `Mount(f"/{plugin_id}", app=sub)`; it is idempotent per app (skip ids already mounted). Logs `plugin_routes_mounted` with the ids.

- [ ] **Step 4: Run to verify pass** — Step 2 command plus `uv run python -m pytest -q tests/test_admin_settings_sections_api.py` and the CSRF module → PASS.

- [ ] **Step 5: Lint and commit** — gate; stage `plugins/routes.py`, `plugins/context.py`, `api/app.py`, `security/csrf.py`, the CSRF test module and the two new test modules; message `feat(plugins): plugin HTTP routes under /api/plugins, settings and ticket read access`.

### Task 6: Ticket events from `ticket_history` — mapping and gap tracking (backend-only, pure)

**Files:**
- Modify: `backend/src/tiqora/plugins/events.py` (add `HISTORY_EVENT_MAP`, `ARTICLE_HISTORY_TYPES`, `HistoryRow`, `events_from_history`, `GapTracker`)
- Test: `backend/tests/test_plugins_events.py`

**Interfaces:**
- Consumes: Task 3 (`TicketEvent`, `EventSubscriber`), `tiqora.domain.history_render.split_history_name`.
- Produces: `@dataclass(frozen=True, slots=True) class HistoryRow: id: int; history_type: str; ticket_id: int; article_id: int | None; name: str; create_by: int; create_time: datetime`; `events_from_history(rows: Sequence[HistoryRow]) -> list[TicketEvent]` (input sorted by id; output keeps order, drops only the create-time duplicates); `GapTracker` (P8).

- [ ] **Step 1: Write the failing tests** `backend/tests/test_plugins_events.py`:

```python
T = datetime(2026, 10, 10, 9, 0)

#: ticket_history_type names from znuny-6.5.22/scripts/database/initial_insert.mysql.sql (ids 1-52)
ZNUNY_HISTORY_TYPES = frozenset({
    "NewTicket", "FollowUp", "SendAutoReject", "SendAutoReply", "SendAutoFollowUp", "Forward", "Bounce",
    "SendAnswer", "SendAgentNotification", "SendCustomerNotification", "EmailAgent", "EmailCustomer",
    "PhoneCallAgent", "PhoneCallCustomer", "AddNote", "Move", "Lock", "Unlock", "Remove", "TimeAccounting",
    "CustomerUpdate", "PriorityUpdate", "OwnerUpdate", "LoopProtection", "Misc", "SetPendingTime",
    "StateUpdate", "TicketDynamicFieldUpdate", "WebRequestCustomer", "TicketLinkAdd", "TicketLinkDelete",
    "SystemRequest", "Merged", "ResponsibleUpdate", "Subscribe", "Unsubscribe", "TypeUpdate",
    "ServiceUpdate", "SLAUpdate", "ArchiveFlagUpdate", "EscalationSolutionTimeStop",
    "EscalationResponseTimeStart", "EscalationUpdateTimeStart", "EscalationSolutionTimeStart",
    "EscalationResponseTimeNotifyBefore", "EscalationUpdateTimeNotifyBefore",
    "EscalationSolutionTimeNotifyBefore", "EscalationResponseTimeStop", "EscalationUpdateTimeStop",
    "TitleUpdate", "EmailResend", "Bulk",
})


def _row(i: int, htype: str, *, ticket: int = 1, article: int | None = None, name: str = "%%",
         at: datetime = T) -> HistoryRow:
    return HistoryRow(id=i, history_type=htype, ticket_id=ticket, article_id=article, name=name,
                      create_by=1, create_time=at)


def test_map_only_uses_real_znuny_history_types() -> None:
    assert set(HISTORY_EVENT_MAP) <= ZNUNY_HISTORY_TYPES
    assert ARTICLE_HISTORY_TYPES <= ZNUNY_HISTORY_TYPES


@pytest.mark.parametrize(("htype", "article", "name", "event"), [
    ("NewTicket", None, "%%", "TicketCreate"),
    ("StateUpdate", None, "%%new%%open", "TicketStateUpdate"),
    ("Move", None, "%%Raw%%2%%Misc%%3", "TicketQueueUpdate"),
    ("Lock", None, "%%lock", "TicketLockUpdate"),
    ("Unlock", None, "%%unlock", "TicketLockUpdate"),
    ("TicketDynamicFieldUpdate", None, "%%FieldName%%Vertrag%%Value%%A%%OldValue%%", "TicketDynamicFieldUpdate_Vertrag"),
    ("TicketDynamicFieldUpdate", None, "garbled", "HistoryAdd"),
    ("AddNote", 77, "%%Notiz", "ArticleCreate"),
    ("AddNote", None, "%%Notiz", "HistoryAdd"),
    ("EmailCustomer", 78, "%%x", "ArticleCreate"),
    ("Misc", None, "%%x", "HistoryAdd"),
    ("TicketLinkAdd", None, "%%x", "HistoryAdd"),
])
def test_single_row_mapping(htype: str, article: int | None, name: str, event: str) -> None:
    (ev,) = events_from_history([_row(1, htype, article=article, name=name)])
    assert ev.event == event and ev.history_type == htype and ev.article_id == article


def test_article_id_zero_is_none() -> None:
    (ev,) = events_from_history([_row(1, "AddNote", article=0)])
    assert ev.article_id is None and ev.event == "HistoryAdd"


def test_create_time_service_type_sla_rows_dropped() -> None:
    rows = [_row(1, "NewTicket"), _row(2, "TypeUpdate"), _row(3, "ServiceUpdate"), _row(4, "SLAUpdate"),
            _row(5, "TypeUpdate", at=T + timedelta(minutes=1)), _row(6, "ServiceUpdate", ticket=2)]
    assert [(e.id, e.event) for e in events_from_history(rows)] == [
        (1, "TicketCreate"), (5, "TicketTypeUpdate"), (6, "TicketServiceUpdate")]


@pytest.mark.parametrize(("events", "event", "ok"), [
    ({"*"}, "HistoryAdd", True), ({"TicketCreate"}, "TicketCreate", True),
    ({"TicketCreate"}, "TicketStateUpdate", False),
    ({"TicketDynamicFieldUpdate_*"}, "TicketDynamicFieldUpdate_Vertrag", True),
    ({"TicketDynamicFieldUpdate_Vertrag"}, "TicketDynamicFieldUpdate_Andere", False),
])
def test_subscriber_matches(events: set[str], event: str, ok: bool) -> None:
    sub = EventSubscriber(key="org.example.hello:x", plugin_id="org.example.hello", events=frozenset(events),
                          handler=_noop, timeout_seconds=5)
    assert sub.matches(event) is ok


def test_gap_tracker_contiguous() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [11, 12, 13], now=0.0) == 13


def test_gap_tracker_holds_then_skips() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [11, 13, 14], now=0.0) == 11
    assert g.horizon(11, [13, 14], now=59.0) == 11
    assert g.horizon(11, [13, 14], now=60.0) == 14


def test_gap_tracker_late_row_fills_gap() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [11, 13], now=0.0) == 11
    assert g.horizon(11, [12, 13], now=5.0) == 13


def test_gap_tracker_step() -> None:
    g = GapTracker(hold_seconds=60, step=2)
    assert g.horizon(10, [12, 14, 18], now=0.0) == 14


def test_gap_tracker_empty_and_forgets() -> None:
    g = GapTracker(hold_seconds=1)
    assert g.horizon(10, [], now=0.0) == 10
    g.horizon(10, [12], now=0.0); g.horizon(10, [12], now=2.0)
    assert g.pending_gaps() == {}
```

(`GapTracker.pending_gaps() -> dict[int, float]` is a read-only copy used by tests and the tick result.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run python -m pytest -q tests/test_plugins_events.py` → FAIL (`ImportError`).
- [ ] **Step 3: Implement** per P8. `GapTracker.horizon`: `expected = after + step`; iterate sorted unique ids `> after`: equal → advance; greater → `first = self._seen.setdefault(expected, now)`; `now - first >= hold` → log `plugin_event_gap_skipped` (`from_id=expected`, `to_id=id - step`) once, drop the key, treat as present and continue with `expected = id + step`; else stop. Afterwards drop `_seen` keys `<= horizon`.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Lint and commit** — message `feat(plugins): Znuny event names from ticket history, gap-safe horizon`.

### Task 7: Event dispatcher loop with per-subscriber cursor and circuit breaker (backend-only)

Seed band **49400–49499**: tickets `49400`, `49401` (as in Task 5's ticket row, titles `plg49 events`), `ticket_history` ids `49400–49440` (seeded per test, deleted per test), hook rows `event:org.example.plg49%`.

**Files:**
- Create: `backend/src/tiqora/plugins/event_dispatch.py`, `backend/src/tiqora/db/id_step.py`
- Modify: `backend/src/tiqora/reporting/incremental.py` (replace the private `_id_step` with `from tiqora.db.id_step import autoincrement_step`; keep behaviour), `backend/src/tiqora/plugins/store.py` (hook helpers below), `backend/src/tiqora/worker/__main__.py` (`_run_all_loops`: `plugin_events` interval loop), `backend/src/tiqora/worker/services.py` (`plugin_events` row), `frontend/src/i18n/locales/en.json` + `de.json` (`admin.daemons.services.plugin_events.*`, P15) + propagation
- Test: `backend/tests/test_plugins_event_dispatch.py`; update any test enumerating `DAEMON_SERVICES` (`grep -rn "DAEMON_SERVICES\|daemon_services" backend/tests frontend/src --include=*.py --include=*.ts --include=*.tsx`)

**Interfaces:**
- Consumes: Tasks 3, 4, 6.
- Produces: `autoincrement_step(factory) -> tuple[int, int]`; store `ensure_hook(session, *, hook_key, plugin_id, kind, initial_cursor: int | None, now) -> TiqoraPluginHook`, `load_hooks(session, *, plugin_id: str | None = None, kind: str | None = None) -> dict[str, TiqoraPluginHook]`, `record_hook_success(session, hook, *, cursor: int | None, now)`, `record_hook_failure(session, hook, *, error: str, now, trip_after: int) -> bool` (True when it tripped now); `run_plugin_events_tick(*, registry, settings, session_factory, tracker: GapTracker | None = None, now: Callable[[], datetime] = utc_now, wall_clock: Callable[[], float] = time.monotonic) -> dict[str, int]`; module constants `TRIP_AFTER = 5`, `EVENT_BATCH = 500`.

- [ ] **Step 1: Write the failing tests** `backend/tests/test_plugins_event_dispatch.py` (both dialects). Fixture `subs` builds a `Registry` with two `EventSubscriber`s owned by `org.example.plg49a` (`good`: events `{"TicketCreate", "TicketStateUpdate"}`, records `(key, id, event)` into a list; `flaky`: events `{"*"}`, fails for ids in a `fail_on` set, sleeps `slow` seconds, timeout 1 s) and sets `set_loaded_plugins([PluginLoadResult("org.example.plg49a", "1.0.0", ACTIVE, None, ())])`; `_hist(sync_url, id, htype, ticket)` inserts one `ticket_history` row (type id looked up by name; `queue_id` 1, `owner_id` 1, `priority_id` 3, `state_id` 1, `type_id` 1, `article_id` NULL). Before each test: `ensure_hook(..., initial_cursor=49399)` for both subscribers. Assertions filter received events to tickets 49400/49401 (other modules may leave rows above the band).

Module fixtures (names used below): `url` (sync dialect URL), `f` (async session factory), `reg` (the registry with `good` and `flaky`), `got` (list the handlers append `(key, history id, event)` to), `fail_on` (set of ids `flaky` fails on), `slow` (`[seconds]` `flaky` sleeps), `tick` (`async def tick(**kw)` → `run_plugin_events_tick(registry=reg, settings=Settings(environment="test"), session_factory=f, tracker=kw.get("tracker") or GapTracker(hold_seconds=60), **{k: v for k, v in kw.items() if k != "tracker"})`).

```python
async def test_delivers_in_order_to_matching_subscribers(url, f, reg, got, fail_on, slow, tick) -> None:
    _hist(url, 49400, "NewTicket", 49400); _hist(url, 49401, "OwnerUpdate", 49400)
    _hist(url, 49402, "StateUpdate", 49400)
    res = await tick()
    assert [(i, e) for k, i, e in got if k.endswith(":good")] == [(49400, "TicketCreate"), (49402, "TicketStateUpdate")]
    assert [(i, e) for k, i, e in got if k.endswith(":flaky")] == [
        (49400, "TicketCreate"), (49401, "TicketOwnerUpdate"), (49402, "TicketStateUpdate")]
    hooks = await _hooks(f)
    assert hooks["event:org.example.plg49a:good"].cursor >= 49402
    assert res["subscribers"] == 2 and res["failed"] == 0


async def test_failing_subscriber_trips_others_continue(url, f, reg, got, fail_on, slow, tick) -> None:
    _hist(url, 49400, "NewTicket", 49400); _hist(url, 49401, "StateUpdate", 49400)
    fail_on.add(49401)
    for _ in range(5):
        await tick()
    flaky = (await _hooks(f))["event:org.example.plg49a:flaky"]
    assert flaky.tripped_at is not None and flaky.consecutive_failures == 5 and flaky.total_failures == 5
    assert flaky.cursor == 49400 and "kaputt bei 49401" in (flaky.last_error or "")
    _hist(url, 49402, "StateUpdate", 49401)
    await tick()
    assert (49402, "TicketStateUpdate") in [(i, e) for k, i, e in got if k.endswith(":good")]
    assert 49402 not in [i for k, i, e in got if k.endswith(":flaky")]          # stopped


async def test_reset_resumes_at_failed_event(url, f, reg, got, fail_on, slow, tick) -> None:
    _hist(url, 49400, "NewTicket", 49400); _hist(url, 49401, "StateUpdate", 49400)
    fail_on.add(49401)
    for _ in range(5):
        await tick()
    async with f() as session:
        await reset_plugin(session, "org.example.plg49a", now=utc_now()); await session.commit()
    fail_on.clear()
    await tick()
    flaky_ids = [i for k, i, e in got if k.endswith(":flaky")]
    assert flaky_ids == [49400, 49401]                                          # failed event delivered, once


async def test_timeout_counts_as_failure(url, f, reg, got, fail_on, slow, tick) -> None:
    slow[0] = 3.0
    _hist(url, 49400, "NewTicket", 49400)
    await tick()
    flaky = (await _hooks(f))["event:org.example.plg49a:flaky"]
    assert flaky.consecutive_failures == 1 and "Zeitlimit" in (flaky.last_error or "")
    assert flaky.cursor == 49399


async def test_new_subscriber_starts_at_max_history_id(url, f, reg, got, fail_on, slow, tick) -> None:
    async with f() as session:
        await session.execute(delete(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == "org.example.plg49a"))
        await session.commit()
    _hist(url, 49400, "NewTicket", 49400)
    await tick()
    hooks = await _hooks(f)
    assert hooks["event:org.example.plg49a:good"].cursor >= 49400 and got == []


async def test_late_commit_within_hold_delivered_in_order(url, f, reg, got, fail_on, slow, tick) -> None:
    clock = [0.0]
    tracker = GapTracker(hold_seconds=60)
    _hist(url, 49400, "NewTicket", 49400); _hist(url, 49402, "StateUpdate", 49400)
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert [i for k, i, e in got if k.endswith(":flaky")] == [49400]
    _hist(url, 49401, "OwnerUpdate", 49400)                                    # the late commit
    clock[0] = 5.0
    await tick(tracker=tracker, wall_clock=lambda: clock[0])
    assert [i for k, i, e in got if k.endswith(":flaky")] == [49400, 49401, 49402]


async def test_inactive_plugin_subscribers_skipped(url, f, reg, got, fail_on, slow, tick) -> None:
    set_loaded_plugins([PluginLoadResult("org.example.plg49a", "1.0.0", RuntimeState.FAILED, "x", ())])
    _hist(url, 49400, "NewTicket", 49400)
    res = await tick()
    assert got == [] and res["subscribers"] == 0


def test_daemon_catalog_and_loop_wiring() -> None:
    assert DAEMON_SERVICES_BY_SLUG["plugin_events"].interval_settings_attr == "plugin_events_interval_seconds"
    assert '"plugin_events"' in inspect.getsource(worker_main._run_all_loops)
```

(The tracker in tests must be created with `step` from `autoincrement_step` when not passed; the tick creates a module-level tracker lazily per process when `tracker is None`, using `settings.plugin_events_gap_hold_seconds`.)

- [ ] **Step 2: Run to verify failure** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_plugins_event_dispatch.py` → FAIL.
- [ ] **Step 3: Implement** per P8: SQL `SELECT th.id, tht.name, th.ticket_id, th.article_id, th.name, th.create_by, th.create_time FROM ticket_history th JOIN ticket_history_type tht ON tht.id = th.history_type_id WHERE th.id > :after ORDER BY th.id LIMIT :n`; `MAX(id)` for new hooks; handler errors stored as `f"{type(exc).__name__}: {exc}"[:2000]`; timeout stored as `"Zeitlimit von {n} Sekunden überschritten"`; each subscriber's bookkeeping in its own session/commit (one failing UPDATE never blocks the others); the tick never raises for subscriber errors. Worker: `_interval_loop("plugin_events", plugin_events_tick, settings.plugin_events_interval_seconds, None, stop)` where `plugin_events_tick` calls `run_plugin_events_tick(registry=get_registry(), settings=settings, session_factory=get_session_factory())`. Move `_id_step` to `tiqora/db/id_step.py` unchanged (name `autoincrement_step`) and import it in `reporting/incremental.py`.
- [ ] **Step 4: Run to verify pass** — Step 2 command, then `uv run python -m pytest -q tests/test_reporting_incremental*.py` (whatever modules import `incremental`) → PASS; `cd frontend && node scripts/check-i18n-keys.mjs` → PASS.
- [ ] **Step 5: Lint and commit** — gate; stage the files above incl. i18n locale files touched by propagation (list them with `rtk git status --short frontend/src/i18n/locales`); message `feat(plugins): deliver ticket events to plugin subscribers with circuit breaker`.

### Task 8: Scheduled tasks and plugin job handlers (backend-only)

**Files:**
- Modify: `backend/src/tiqora/plugins/tasks.py` (`run_plugin_tasks_tick`), `backend/src/tiqora/worker/__main__.py` (`plugin_tasks` loop), `backend/src/tiqora/worker/services.py` (`plugin_tasks` row), i18n `admin.daemons.services.plugin_tasks.*` + propagation
- Test: `backend/tests/test_plugins_tasks.py` (both dialects; no Znuny seeds; hook keys `task:org.example.plg49%`), `backend/tests/test_plugins_jobs.py`

**Interfaces:**
- Consumes: Task 2 (`Schedule`, `next_due`), Task 3 (`ScheduledTask`, `SCHEDULED_TASKS`, `TaskContext`), Task 7 (hook store helpers, `TRIP_AFTER`).
- Produces: `run_plugin_tasks_tick(*, registry, session_factory, now: Callable[[], datetime] = utc_now) -> dict[str, int]`; constant `MAX_PARALLEL_TASKS = 4`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_plugins_tasks.py` (registry with tasks of `org.example.plg49b`: `every` = every 300 s, `daily` = `daily_at="03:00"`, `bad` = every 60 s and raises, `slow` = every 60 s, sleeps 3 s, timeout 1; `set_loaded_plugins(ACTIVE)`; `clock` list for `now`):

Module fixtures: `f`, `reg`, `runs` (`dict[str, int]` counting runs per task name), `clock` (`[datetime]`), `tick` (`async def tick()` → `run_plugin_tasks_tick(registry=reg, session_factory=f, now=lambda: clock[0])`).

```python
async def test_interval_runs_now_then_waits(f, reg, runs, clock, tick) -> None:
    clock[0] = datetime(2026, 10, 10, 10, 0)
    await tick()
    assert runs["every"] == 1
    clock[0] += timedelta(seconds=299)
    await tick()
    assert runs["every"] == 1
    clock[0] += timedelta(seconds=1)
    await tick()
    assert runs["every"] == 2
    hook = (await _hooks(f))["task:org.example.plg49b:every"]
    assert hook.last_run_at == clock[0] and hook.last_ok_at == clock[0]


async def test_daily_waits_for_next_occurrence(f, reg, runs, clock, tick) -> None:
    clock[0] = datetime(2026, 10, 10, 4, 0)
    await tick()
    assert runs["daily"] == 0
    clock[0] = datetime(2026, 10, 11, 3, 0)
    await tick()
    assert runs["daily"] == 1


async def test_failure_written_and_trips_after_five(f, reg, runs, clock, tick) -> None:
    clock[0] = datetime(2026, 10, 10, 10, 0)
    for i in range(5):
        await tick()
        clock[0] += timedelta(seconds=60)
    hook = (await _hooks(f))["task:org.example.plg49b:bad"]
    assert hook.consecutive_failures == 5 and hook.tripped_at is not None
    assert hook.last_run_at is not None                       # written before the run
    before = runs["bad"]
    await tick()
    assert runs["bad"] == before                              # tripped: not run


async def test_timeout(f, reg, runs, clock, tick) -> None:
    clock[0] = datetime(2026, 10, 10, 10, 0)
    res = await tick()
    hook = (await _hooks(f))["task:org.example.plg49b:slow"]
    assert "Zeitlimit" in (hook.last_error or "") and res["failed"] >= 1


def test_daemon_catalog_and_loop_wiring() -> None:
    assert DAEMON_SERVICES_BY_SLUG["plugin_tasks"].interval_settings_attr == "plugin_tasks_interval_seconds"
    assert '"plugin_tasks"' in inspect.getsource(worker_main._run_all_loops)
```

`backend/tests/test_plugins_jobs.py` (MariaDB is enough — the job tables are covered on both dialects by wave 0): install the hello fixture, build the registry via `commit_registrar`, then `enqueue_job(session, kind="org.example.hello.echo", payload={"text": "hi"}, created_by=None, registry=reg)` and drive the `JobRunner` exactly like `backend/tests/test_jobs_runner.py` does (reuse its helpers by name after reading it); assert the job ends `succeeded` with `result == {"echo": "hi"}`; and `enqueue_job(..., kind="org.example.hello.nope")` raises `UnknownJobKind`.

- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_plugins_tasks.py tests/test_plugins_jobs.py` → FAIL.
- [ ] **Step 3: Implement** per P9 (hooks `task:<key>`; `created_at` of the hook row is the `created_at` passed to `next_due`; `last_run_at` committed before the run; semaphore `MAX_PARALLEL_TASKS`; `asyncio.timeout(task.timeout_seconds)`; result `{"tasks", "ran", "failed", "tripped"}`). Worker loop `_interval_loop("plugin_tasks", …, settings.plugin_tasks_interval_seconds, None, stop)`.
- [ ] **Step 4: Run to verify pass**; `node frontend/scripts/check-i18n-keys.mjs` → PASS.
- [ ] **Step 5: Lint and commit** — message `feat(plugins): scheduled plugin tasks and plugin job handlers`.

### Task 9: Plugin MCP tools (backend-only)

**Files:**
- Modify: `backend/src/tiqora/plugins/mcp.py` (`register_plugin_mcp_tools`), `backend/src/tiqora/mcp_server/server.py` (`_app_lifespan`: `registry = await init_registry(...)` from Task 4, then `register_plugin_mcp_tools(mcp, registry)`), `backend/src/tiqora_sdk/mcp.py` (helpers delegate to `tiqora.mcp_server.server._get_user_id`, `_get_state().session_factory`, `PermissionEngine`)
- Test: `backend/tests/test_plugins_mcp.py`

**Interfaces:**
- Consumes: Task 3 (`McpTool`, `MCP_TOOLS`, `CORE_MCP_TOOL_NAMES`, fixture tool `echo_tool` → name `hello_echo`), Task 4 (`set_loaded_plugins`), core `_assert_tool_allowed`, `_assert_mcp_write_scope`, `_current_api_key_scopes`.
- Produces: `register_plugin_mcp_tools(server: FastMCP, registry: Registry) -> list[str]`.

- [ ] **Step 1: Write the failing tests** `backend/tests/test_plugins_mcp.py` (no DB; FastMCP 4.0.5 is locked — check `inspect.signature(FastMCP.tool)` / `Client.call_tool` before relying on details; skill `fastmcp-3x-conditional-tool-registration` still applies for `await client.list_tools()` and `.inputSchema` on the client-side tool objects; `FastMCP.tool` also has a native `timeout=` — keep the explicit `asyncio.timeout` in the wrapper so the error text is ours):

```python
async def test_core_names_constant_matches_server() -> None:
    async with Client(core_mcp) as client:
        assert {t.name for t in await client.list_tools()} == CORE_MCP_TOOL_NAMES


def _hello_registry(fixture_plugins, *, state: RuntimeState = RuntimeState.ACTIVE) -> Registry:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    set_loaded_plugins([PluginLoadResult("org.example.hello", "1.0.0", state, None, ())])
    return reg


def _tool_registry(tool: McpTool) -> Registry:
    reg = Registry()
    reg.add(MCP_TOOLS, tool, owner=tool.plugin_id)
    set_loaded_plugins([PluginLoadResult(tool.plugin_id, "1.0.0", RuntimeState.ACTIVE, None, ())])
    return reg


async def sleepy(ctx: Context) -> dict[str, str]:
    await asyncio.sleep(2)
    return {}


async def test_registers_plugin_tool_on_fresh_server(fixture_plugins) -> None:
    server = FastMCP("t")
    assert register_plugin_mcp_tools(server, _hello_registry(fixture_plugins)) == ["hello_echo"]
    async with Client(server) as client:
        (tool,) = [t for t in await client.list_tools() if t.name == "hello_echo"]
    assert set(tool.inputSchema["properties"]) == {"text", "times"}       # ctx excluded, annotations resolved
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert "The text to echo." in tool.inputSchema["properties"]["text"].get("description", "")


async def test_tool_call_respects_allowlist(fixture_plugins) -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _hello_registry(fixture_plugins))
    token = _current_api_key_scopes.set(frozenset({"mcp:rw", "tool:ticket_get"}))
    try:
        async with Client(server) as client:
            denied = await client.call_tool("hello_echo", {"text": "x"}, raise_on_error=False)
    finally:
        _current_api_key_scopes.reset(token)
    assert denied.is_error
    async with Client(server) as client:
        ok = await client.call_tool("hello_echo", {"text": "ab", "times": 2})
    assert ok.data == {"text": "abab"}


async def test_write_tool_requires_rw() -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _tool_registry(McpTool(
        name="hello_put", plugin_id="org.example.hello", fn=echo_tool, description="x", write=True,
        timeout_seconds=5)))
    token = _current_api_key_scopes.set(frozenset({"mcp:ro"}))
    try:
        async with Client(server) as client:
            res = await client.call_tool("hello_put", {"text": "x"}, raise_on_error=False)
    finally:
        _current_api_key_scopes.reset(token)
    assert res.is_error
    async with Client(server) as client:                       # unrestricted key (scopes None) may write
        assert (await client.call_tool("hello_put", {"text": "x"})).data == {"text": "x"}


async def test_tool_timeout() -> None:
    server = FastMCP("t")
    register_plugin_mcp_tools(server, _tool_registry(McpTool(
        name="hello_sleepy", plugin_id="org.example.hello", fn=sleepy, description="x", write=False,
        timeout_seconds=1)))
    started = time.monotonic()
    async with Client(server) as client:
        res = await client.call_tool("hello_sleepy", {}, raise_on_error=False)
    assert res.is_error and "Zeitlimit" in str(res.content[0].text)
    assert time.monotonic() - started < 1.9


async def test_idempotent_and_inactive(fixture_plugins) -> None:
    server = FastMCP("t")
    reg = _hello_registry(fixture_plugins)
    assert register_plugin_mcp_tools(server, reg) == ["hello_echo"]
    assert register_plugin_mcp_tools(server, reg) == []
    other = FastMCP("u")
    set_loaded_plugins([PluginLoadResult("org.example.hello", "1.0.0", RuntimeState.FAILED, "x", ())])
    assert register_plugin_mcp_tools(other, reg) == []


def test_lifespan_binds_plugin_tools() -> None:
    src = inspect.getsource(mcp_server._app_lifespan)
    assert "register_plugin_mcp_tools(mcp" in src
```

Also run the existing `test_mcp_every_tool_checks_allowlist` unchanged (plugin tools never reach the global `mcp` in tests).

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run python -m pytest -q tests/test_plugins_mcp.py` → FAIL.
- [ ] **Step 3: Implement** per P10. On `TimeoutError` the wrapper raises `fastmcp.exceptions.ToolError(f"Zeitlimit von {n} Sekunden überschritten")` so the client gets a clean tool error; `TicketAccessDenied` from the scope checks propagates exactly as in core tools.
- [ ] **Step 4: Run to verify pass** — Step 2 command plus `uv run python -m pytest -q tests/test_mcp_server.py` → PASS.
- [ ] **Step 5: Lint and commit** — message `feat(plugins): plugin MCP tools with the core allowlist and scope checks`.

### Task 10: Admin plugin API, frontend plugin list and asset serving (backend-only)

Seed: none in Znuny tables (admin/agent users via dependency overrides; `PermissionEngine.is_admin` monkeypatched for the non-admin case); plugin rows of `org.example.hello` cleaned by the module fixture.

**Files:**
- Create: `backend/src/tiqora/api/v1/plugins.py`, `backend/src/tiqora/api/v1/admin/plugins.py`, `backend/src/tiqora/plugins/frontend.py`
- Modify: `backend/src/tiqora/api/v1/__init__.py` (include `plugins.router`), `backend/src/tiqora/api/v1/admin/__init__.py` (include admin `plugins.router`), `packages/api-client/openapi.json`, `docs/api/openapi.json` (regenerated), `packages/api-client/src/schema.d.ts` only if the build regenerates it locally
- Test: `backend/tests/test_admin_plugins_api.py` (both dialects), `backend/tests/test_plugins_frontend_api.py` (no DB)

**Interfaces:**
- Consumes: Tasks 1, 3, 4 (store, `summarize_status`, `loaded_plugins`), 7/8 (hook rows), P11.
- Produces: P11 models and routes exactly; `frontend_plugins_for(*, is_admin: bool) -> list[PluginFrontendOut]`; schema names in the generated client: `PluginFrontendOut`, `PluginAdminOut`, `PluginAdminListOut`, `PluginRuntimeOut`, `PluginHookOut`, `PluginCapabilityOut`, `PluginContributionOut`, `PluginEnabledIn`, `NavContribution`, `RouteContribution`, `TicketSidebarContribution`, `DashboardWidgetContribution`, `WidgetSize`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_plugins_frontend_api.py`:

```python
async def test_frontend_list_for_agent_hides_admin_only(fixture_plugins, agent_app) -> None:
    async with _client(agent_app) as c:
        (p,) = (await c.get("/api/v1/plugins/frontend")).json()
    assert p["id"] == "org.example.hello" and p["asset_base"] == "/api/v1/plugins/org.example.hello/assets/1.0.0/"
    assert p["entry"] == "web/index.js" and p["styles"] == ["web/hello.css"] and p["locales"] == ["de", "en"]
    keys = {(c["slot"], c["key"]) for c in p["contributions"]}
    assert ("agent.route", "admin") not in keys and ("agent.nav", "admin") not in keys
    assert ("ticket.sidebar", "info") in keys and ("dashboard.widget", "weather") in keys


async def test_frontend_list_for_admin_includes_admin_only(fixture_plugins, admin_app) -> None:
    async with _client(admin_app) as c:
        (p,) = (await c.get("/api/v1/plugins/frontend")).json()
    assert ("agent.route", "admin") in {(c["slot"], c["key"]) for c in p["contributions"]}


async def test_frontend_list_empty_in_safe_mode_or_inactive(fixture_plugins) -> None:
    for app in (_agent_app(safe_mode=True), _agent_app(state=RuntimeState.FAILED)):
        async with _client(app) as c:
            assert (await c.get("/api/v1/plugins/frontend")).json() == []


async def test_asset_served_with_headers(fixture_plugins, agent_app) -> None:
    async with _client(agent_app) as c:
        r = await c.get("/api/v1/plugins/org.example.hello/assets/1.0.0/web/index.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert b"definePlugin" in r.content


@pytest.mark.parametrize("path", ["2.0.0/web/index.js", "1.0.0/web/../tiqora-plugin.toml",
                                  "1.0.0/plugin.py", "1.0.0/web/missing.js", "1.0.0/i18n/de.txt"])
async def test_asset_404s(fixture_plugins, agent_app, path: str) -> None:
    async with _client(agent_app) as c:
        assert (await c.get(f"/api/v1/plugins/org.example.hello/assets/{path}")).status_code == 404


async def test_asset_requires_login(fixture_plugins) -> None:
    async with _client(_agent_app(authenticated=False)) as c:
        assert (await c.get("/api/v1/plugins/org.example.hello/assets/1.0.0/web/index.js")).status_code == 401


async def test_i18n_asset(fixture_plugins, agent_app) -> None:
    async with _client(agent_app) as c:
        r = await c.get("/api/v1/plugins/org.example.hello/assets/1.0.0/i18n/de.json")
    assert r.status_code == 200 and r.json()["panel"]["title"] == "Hallo"
```

(Fixture `seeded_runtime` in `test_admin_plugins_api.py`: installs the hello fixture, builds the registry via `import_and_register` + `commit_registrar`, `install_registry`/`set_registry_for_tests`, `set_loaded_plugins([... ACTIVE ...])`, then `sync_discovered(..., auto_enable=True)` and `mark_result(factory, "org.example.hello", "api", state=ACTIVE, version="1.0.0", error=None, now=T0)`; teardown deletes the `tiqora_plugin*` rows of `org.example.hello` and resets registry/loaded state. `admin_client_safe_mode` builds the app with `Settings(environment="test", plugins_safe_mode=True)`.) (`_agent_app(...)` installs the fixture, builds the registry, `set_loaded_plugins`, `create_app(Settings(environment="test", plugins_safe_mode=…))`, overrides `get_current_user`; `is_admin` is decided by monkeypatching `PermissionEngine.is_admin`. For a test that wants an `.svg`, add `web/logo.svg` to the fixture and assert `content-security-policy == "sandbox"`.)

`backend/tests/test_admin_plugins_api.py` (both dialects; `get_admin_user` + `get_db` overrides as in `test_admin_settings_sections_api.py`):

```python
async def test_list_active_plugin(fixture_plugins, admin_client, seeded_runtime) -> None:
    body = (await admin_client.get("/api/v1/admin/plugins")).json()
    assert body["safe_mode"] is False
    (p,) = [x for x in body["plugins"] if x["id"] == "org.example.hello"]
    assert p["status"] == "active" and p["enabled"] is True and p["publisher"] == "Example GmbH"
    assert {"kind": "outbound_host", "value": "api.example.org"} in p["capabilities"]
    assert {"kind": "frontend_slot", "value": "ticket.sidebar"} in p["capabilities"]
    assert {"point_key": "core.mcp.tools", "key": "hello_echo"} in p["contributions"]
    assert p["settings_section"] == "plugins.org.example.hello"
    assert {r["role"] for r in p["runtime"]} == {"api"}


async def test_disable_needs_restart(fixture_plugins, admin_client, seeded_runtime, factory) -> None:
    r = await admin_client.put("/api/v1/admin/plugins/org.example.hello/enabled", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["status"] == "restart_required"
    assert r.json()["status_message"] == "Änderung wird nach einem Neustart von Tiqora wirksam."
    row = await _plugin_row(factory, "org.example.hello")
    assert row.enabled is False and row.changed_by == 1


async def test_reset_clears_quarantine_and_hooks(fixture_plugins, admin_client, factory) -> None:
    await mark_result(factory, "org.example.hello", "worker", state=RuntimeState.QUARANTINED, version="1.0.0",
                      error=None, now=T0)
    await _seed_tripped_hook(factory, "event:org.example.hello:flaky")
    r = await admin_client.post("/api/v1/admin/plugins/org.example.hello/reset")
    assert r.status_code == 200
    worker = [x for x in r.json()["runtime"] if x["role"] == "worker"][0]
    assert worker["state"] == "disabled" and worker["boot_attempts"] == 0
    (hook,) = [h for h in r.json()["hooks"] if h["key"] == "event:org.example.hello:flaky"]
    assert hook["tripped"] is False and hook["consecutive_failures"] == 0


async def test_safe_mode_lists_without_import(fixture_plugins, admin_client_safe_mode) -> None:
    body = (await admin_client_safe_mode.get("/api/v1/admin/plugins")).json()
    assert body["safe_mode"] is True and [p["id"] for p in body["plugins"]] == ["org.example.hello"]
    assert "tq_fixture_hello" not in sys.modules


async def test_unknown_plugin_404(admin_client) -> None:
    assert (await admin_client.put("/api/v1/admin/plugins/org.example.nope/enabled", json={"enabled": True})).status_code == 404
    assert (await admin_client.post("/api/v1/admin/plugins/org.example.nope/reset")).status_code == 404


async def test_non_admin_403(agent_client) -> None:
    assert (await agent_client.get("/api/v1/admin/plugins")).status_code == 403
```

- [ ] **Step 2: Run to verify failure** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_plugins_frontend_api.py tests/test_admin_plugins_api.py` → FAIL (404s).
- [ ] **Step 3: Implement** per P11. The admin list joins discovery (`discovered_plugins()`), DB rows (`list_plugin_rows`, `list_runtime_rows`, `load_hooks`), compat (`check_compat(manifest, core=release_core_version())`), this process's registry (contributions: `registry.registrations()` filtered by `owner == id`; `settings_section` when `SETTINGS_SECTIONS` has `plugins.<id>`), and `summarize_status(api_state=…)` using the `api` runtime row. Plugins that have a DB row but are no longer discovered are listed with `status="invalid"` and message "Nicht mehr im Image enthalten". Hook names: part after the plugin id; `schedule` = `Schedule.describe()` of the registered task when present in this process's registry, else `None`. All lists sorted (plugins by name, runtime by role order api/worker/mcp, hooks by key).
- [ ] **Step 4: Run to verify pass**, then regenerate the OpenAPI files (Global Constraints command) and check the diff size; `cd frontend && pnpm lint` (types still compile) → PASS.
- [ ] **Step 5: Lint and commit** — stage the new modules, the two router `__init__` files, tests, `packages/api-client/openapi.json`, `docs/api/openapi.json` (and `schema.d.ts` if it changed); message `feat(plugins): admin plugin API, frontend plugin list and asset serving`.

### Task 11: `tiqora plugins` CLI and the baked image (backend + Docker)

**Files:**
- Create: `backend/src/tiqora/plugins/bake.py`, `backend/src/tiqora/cli/plugins.py`
- Modify: `backend/src/tiqora/main.py` (`add_plugins_subparser(sub)`), `Dockerfile` (runtime stage: `RUN python -m tiqora.main plugins constraints > /app/backend/core-constraints.txt` after the `ENV PATH=…` block and before `WORKDIR /app/backend` / `USER tiqora`), `docker/entrypoint.sh` (`tiqora)` passthrough role; update the "Unknown role" message to list it)
- Test: `backend/tests/test_plugins_bake.py`

**Interfaces:**
- Consumes: Tasks 1 (`discover_plugins`, `release_core_version`, `check_compat`), 3 (`run_in_daemon_thread`, `PluginLoadTimeout`).
- Produces (P14): `@dataclass PluginsTxt: lines: list[str]; wheels: list[Path]`; `parse_plugins_txt(path: Path) -> PluginsTxt` (raises `BakeError(ValueError)` with German message incl. line number); `render_dockerfile(base_image: str, *, has_wheels: bool) -> str`; `default_base_image(env=os.environ) -> str | None`; `bake(plugins_txt: Path, output: Path, *, base_image: str | None) -> None`; `core_constraints(dists: Iterable[Distribution] | None = None) -> list[str]`; `async verify_installed_plugins(*, import_check: bool, strict: bool, timeout: float) -> list[str]` (one German line per problem, prefixed with the plugin id).

- [ ] **Step 1: Write the failing tests** `backend/tests/test_plugins_bake.py`:

```python
def test_parse_plugins_txt(tmp_path) -> None:
    (tmp_path / "wheels").mkdir()
    (tmp_path / "wheels" / "hello-1.0.0-py3-none-any.whl").write_bytes(b"PK")
    txt = tmp_path / "plugins.txt"
    txt.write_text("# Kommentar\n\n--index-url https://pypi.example.org/simple\n"
                   "--extra-index-url https://git.example.org/api/packages/x/pypi/simple\n"
                   "org-example-crm==2.1.0\nmatrix-plugin @ https://example.org/m-1.0-py3-none-any.whl\n"
                   "./wheels/hello-1.0.0-py3-none-any.whl\n")
    parsed = parse_plugins_txt(txt)
    assert parsed.lines == ["--index-url https://pypi.example.org/simple",
                            "--extra-index-url https://git.example.org/api/packages/x/pypi/simple",
                            "org-example-crm==2.1.0", "matrix-plugin @ https://example.org/m-1.0-py3-none-any.whl",
                            "/tmp/tiqora-plugins/wheels/hello-1.0.0-py3-none-any.whl"]
    assert [w.name for w in parsed.wheels] == ["hello-1.0.0-py3-none-any.whl"]


@pytest.mark.parametrize("line", ["-e ./src", "-r other.txt", "-c c.txt", "--no-deps", "--pre",
                                  "--trusted-host x", "./missing-1.0-py3-none-any.whl"])
def test_parse_rejects(tmp_path, line: str) -> None:
    txt = tmp_path / "plugins.txt"; txt.write_text(line + "\n")
    with pytest.raises(BakeError, match="Zeile 1"):
        parse_plugins_txt(txt)


def test_render_dockerfile_exact() -> None:
    assert render_dockerfile("ghcr.io/cygnusnetworks/tiqora:0.18.2", has_wheels=True) == (
        "# Generated by `tiqora plugins bake` — edit plugins.txt and run it again instead of editing this file.\n"
        "FROM ghcr.io/cygnusnetworks/tiqora:0.18.2\n"
        "USER root\n"
        "COPY plugins.txt /tmp/tiqora-plugins/plugins.txt\n"
        "COPY wheels/ /tmp/tiqora-plugins/wheels/\n"
        "RUN /app/.venv/bin/python -m ensurepip --upgrade \\\n"
        " && /app/.venv/bin/python -m pip install --no-cache-dir \\\n"
        "      --constraint /app/backend/core-constraints.txt \\\n"
        "      -r /tmp/tiqora-plugins/plugins.txt \\\n"
        " && rm -rf /tmp/tiqora-plugins\n"
        "USER tiqora\n"
        "RUN python -m tiqora.main plugins verify --import --strict\n"
    )
    assert "COPY wheels/" not in render_dockerfile("x", has_wheels=False)


@pytest.mark.parametrize(("raw", "image"), [("v0.18.2", "ghcr.io/cygnusnetworks/tiqora:0.18.2"),
                                            ("v0.17.1+3.gabc", None), ("", None)])
def test_default_base_image(raw: str, image: str | None) -> None:
    assert default_base_image({"TIQORA_VERSION": raw}) == image


def test_bake_writes_output(tmp_path) -> None:
    wheel = tmp_path / "hello-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"PK\x03\x04plg49")
    txt = _txt(tmp_path, "hello-1.0.0-py3-none-any.whl\nmatrix==1.0\n")
    out = tmp_path / "out"
    bake(txt, out, base_image="registry.example.org/tiqora:0.18.2")
    assert (out / "Dockerfile").read_text() == render_dockerfile("registry.example.org/tiqora:0.18.2", has_wheels=True)
    assert (out / "plugins.txt").read_text() == (
        "/tmp/tiqora-plugins/wheels/hello-1.0.0-py3-none-any.whl\nmatrix==1.0\n")
    assert (out / "wheels" / "hello-1.0.0-py3-none-any.whl").read_bytes() == b"PK\x03\x04plg49"



def test_bake_without_release_needs_base_image(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TIQORA_VERSION", raising=False)
    with pytest.raises(BakeError, match="--base-image"):
        bake(_txt(tmp_path, "a==1\n"), tmp_path / "out", base_image=None)


def test_core_constraints() -> None:
    fake = [_dist("FastAPI", "0.141.1"), _dist("de-core-news-sm", "3.8.0"), _dist("FastAPI", "0.141.1")]
    assert core_constraints(fake) == ["FastAPI==0.141.1", "de-core-news-sm==3.8.0"]
    assert any(line.lower().startswith("fastapi==") for line in core_constraints())


async def test_verify_ok(fixture_plugins) -> None:
    fixture_plugins()
    assert await verify_installed_plugins(import_check=True, strict=True, timeout=5) == []


async def test_verify_fails_on_missing_web_entry(fixture_plugins) -> None:
    fixture_plugins(drop=["web/index.js"])
    (problem,) = await verify_installed_plugins(import_check=False, strict=False, timeout=5)
    assert problem.startswith("org.example.hello:") and "web/index.js" in problem


async def test_verify_strict_rejects_core_requirement(fixture_plugins) -> None:
    fixture_plugins(requires_dist=["tiqora>=0.1"])
    assert await verify_installed_plugins(import_check=False, strict=False, timeout=5) == []
    (problem,) = await verify_installed_plugins(import_check=False, strict=True, timeout=5)
    assert "tiqora" in problem


async def test_verify_import_timeout(fixture_plugins, monkeypatch) -> None:
    monkeypatch.setenv("TQ_SLOW_SECONDS", "2")
    fixture_plugins("tq_fixture_slow", plugin_id="org.example.slow")
    (problem,) = await verify_installed_plugins(import_check=True, strict=False, timeout=0.2)
    assert "Sekunden" in problem


def test_cli_list_json(fixture_plugins, capsys) -> None:
    fixture_plugins()
    with pytest.raises(SystemExit) as exc:
        main(["plugins", "list", "--json"])
    assert exc.value.code in (0, None)
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "org.example.hello" and data[0]["problem"] is None


def test_cli_verify_exit_code(fixture_plugins) -> None:
    fixture_plugins(drop=["web/index.js"])
    with pytest.raises(SystemExit) as exc:
        main(["plugins", "verify"])
    assert exc.value.code == 1


def test_entrypoint_has_passthrough_role() -> None:
    text = (Path(__file__).parents[2] / "docker" / "entrypoint.sh").read_text()
    assert 'tiqora)' in text and 'exec python -m tiqora.main "$@"' in text
    dockerfile = (Path(__file__).parents[2] / "Dockerfile").read_text()
    assert "plugins constraints > /app/backend/core-constraints.txt" in dockerfile
```

(Helpers in the module: `_txt(d, content)` writes `d / "plugins.txt"` and returns it; `_dist(name, version)` returns `types.SimpleNamespace(metadata={"Name": name}, version=version)`. Check how `tiqora.main.main` exits for subcommands — follow `cli/report.py`'s `set_defaults(func=…)` pattern; adapt the `SystemExit` expectations to it.)

- [ ] **Step 2: Run to verify failure** — `cd backend && uv run python -m pytest -q tests/test_plugins_bake.py` → FAIL.
- [ ] **Step 3: Implement** per P14. `core_constraints`: `{dist.metadata["Name"]: dist.version}` over `importlib.metadata.distributions()` (first wins), sorted case-insensitively, `Name==version` lines. `verify --import` loads each plugin's entry point object via `run_in_daemon_thread(ep.load, timeout=…)` (no `register()`, no DB); `--strict` reads `dist.requires` and flags requirement names `tiqora`/`tiqora-sdk` (normalised with `packaging.utils.canonicalize_name`).
- [ ] **Step 4: Run to verify pass.** If Docker is available, also `docker build -t tiqora:plugins-check .` from the worktree root and `docker run --rm tiqora:plugins-check tiqora plugins constraints | head -3` (expect `Name==version` lines) — record the result in the task report; skip with a note when Docker is unavailable.
- [ ] **Step 5: Lint and commit** — stage `plugins/bake.py`, `cli/plugins.py`, `main.py`, `Dockerfile`, `docker/entrypoint.sh`, the test; message `feat(plugins): tiqora plugins list/verify/bake and core constraints in the image`.

### Task 12: `@tiqora/plugin-sdk`, runtime shim modules and the host SDK runtime (frontend-only)

**Files:**
- Create: `packages/plugin-sdk/package.json`, `packages/plugin-sdk/tsconfig.json`, `packages/plugin-sdk/README.md`, `packages/plugin-sdk/src/index.ts`, `packages/plugin-sdk/src/shared.ts`, `packages/plugin-sdk/src/vite.ts`, `packages/plugin-sdk/test-plugin/src/index.tsx`, `packages/plugin-sdk/test-plugin/vite.config.ts`, `packages/plugin-sdk/scripts/check-preset.mjs`
- Create: `frontend/src/plugins/runtime/react.ts`, `react-jsx-runtime.ts`, `react-dom.ts`, `react-query.ts`, `react-router.ts`, `react-i18next.ts`, `sdk.ts`; `frontend/src/plugins/instanceContext.ts`; `frontend/src/plugins/sdkRuntime.tsx`; `frontend/scripts/check-plugin-runtime.mjs`
- Modify: `frontend/vite.config.ts`, `frontend/package.json` (`"@tiqora/plugin-sdk": "workspace:*"`), `pnpm-lock.yaml` (`pnpm install`), `frontend/eslint.config.js` (if `react-refresh/only-export-components` flags `src/plugins/runtime/**` or `sdkRuntime.tsx`, add a file-scoped override turning that rule off for exactly those files)
- Test: `frontend/src/plugins/runtime/runtime.test.ts`, `frontend/src/plugins/sdkRuntime.test.tsx`, `frontend/src/plugins/pluginPreset.test.ts`

**Interfaces:**
- Consumes: P12, `api` (`frontend/src/lib/api.ts`), `useAuth` (`frontend/src/auth/AuthContext`), UI components `Button`, `Badge`, `Spinner`, `Switch`, `Dialog` (`frontend/src/components/ui/*`).
- Produces: `PLUGIN_RUNTIME_PATH`, `PLUGIN_SHARED_MODULES` (`@tiqora/plugin-sdk/shared`), `tiqoraPluginConfig({ entry, outDir }): UserConfig` (`@tiqora/plugin-sdk/vite`), all types of P12/P13 from `@tiqora/plugin-sdk` (host code imports them with `import type` only — a value import from the root entry would get the ambient declarations, which have no runtime value); `PluginInstanceContext` (`React.Context<{ pluginId: string; version: string } | null>`) in `instanceContext.ts`; `sdkRuntime.tsx` exports every `SdkRuntime` member by name plus `runtimeCheck`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/plugins/runtime/runtime.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import * as reactShim from "./react";
import * as jsxShim from "./react-jsx-runtime";
import * as domShim from "./react-dom";
import * as queryShim from "./react-query";
import * as routerShim from "./react-router";
import * as i18nShim from "./react-i18next";
import * as sdkShim from "./sdk";

const publicKeys = (m: object) => Object.keys(m).filter((k) => !k.startsWith("__") && k !== "default").sort();

describe("plugin runtime shims", () => {
  it.each([
    ["react", reactShim],
    ["react/jsx-runtime", jsxShim],
    ["react-dom", domShim],
    ["@tanstack/react-query", queryShim],
    ["@tanstack/react-router", routerShim],
    ["react-i18next", i18nShim],
  ] as const)("%s shim re-exports every public export", async (specifier, shim) => {
    const real = await import(/* @vite-ignore */ specifier);
    expect(publicKeys(shim)).toEqual(expect.arrayContaining(publicKeys(real)));
  });

  it("re-exports the same instances, not copies", async () => {
    const React = await import("react");
    expect(reactShim.useState).toBe(React.useState);
    expect((reactShim as { default: unknown }).default).toBeDefined();
  });

  it("sdk shim exposes exactly the SdkRuntime surface", () => {
    expect(publicKeys(sdkShim)).toEqual(
      ["Badge", "Button", "Dialog", "EmptyState", "Panel", "SDK_VERSION", "Spinner", "Switch",
       "definePlugin", "usePluginApi", "usePluginContext", "usePluginTranslation"].sort(),
    );
  });
});
```

`frontend/src/plugins/sdkRuntime.test.tsx` (mock `@/lib/api` → `{ api: { request: vi.fn() } }`; mock `@/auth/AuthContext` → `useAuth: () => ({ user: { id: 7, login: "agent", is_admin: false } })`; render hooks inside `<PluginInstanceContext.Provider value={{ pluginId: "org.example.hello", version: "1.0.0" }}>`):

```tsx
it("usePluginApi calls the plugin's route prefix", async () => {
  request.mockResolvedValue({ ok: true });
  const { result } = renderHook(() => usePluginApi(), { wrapper });
  await result.current.get("/status", { query: { a: 1 } });
  await result.current.post("items", { x: 1 });
  expect(request).toHaveBeenNthCalledWith(1, "GET", "/api/plugins/org.example.hello/status", { query: { a: 1 }, signal: undefined });
  expect(request).toHaveBeenNthCalledWith(2, "POST", "/api/plugins/org.example.hello/items", { body: { x: 1 }, query: undefined, signal: undefined });
});

it.each(["../admin", "a/../../b", "", "a//b"])("rejects path %j", async (path) => {
  const { result } = renderHook(() => usePluginApi(), { wrapper });
  await expect(result.current.get(path)).rejects.toThrow(/Pfad/);
  expect(request).not.toHaveBeenCalled();
});

it("usePluginContext gives id, version, locale and user", () => {
  const { result } = renderHook(() => usePluginContext(), { wrapper });
  expect(result.current).toEqual({ pluginId: "org.example.hello", version: "1.0.0", locale: i18n.language,
                                   user: { id: 7, login: "agent", isAdmin: false } });
});

it("usePluginContext outside a plugin throws", () => {
  expect(() => renderHook(() => usePluginContext())).toThrow(/Erweiterung/);
});

it("usePluginTranslation uses the plugin namespace", () => {
  i18n.addResourceBundle("de", "plugin.org.example.hello", { panel: { title: "Hallo" } }, true, true);
  const { result } = renderHook(() => usePluginTranslation(), { wrapper });
  expect(result.current.t("panel.title")).toBe("Hallo");
});

it("SDK_VERSION equals the package version", async () => {
  const pkg = await import("../../../packages/plugin-sdk/package.json");
  expect(SDK_VERSION).toBe(pkg.version);
  expect(SDK_VERSION).toBe("0.1.0");
});

it("Panel and EmptyState render titles", () => {
  render(<Panel title="Hallo"><p>Inhalt</p></Panel>);
  expect(screen.getByRole("heading", { name: "Hallo" })).toBeInTheDocument();
  render(<EmptyState title="Nichts da" hint="Später wieder" />);
  expect(screen.getByText("Nichts da")).toBeInTheDocument();
});
```

`frontend/src/plugins/pluginPreset.test.ts`:

```ts
import { tiqoraPluginConfig } from "../../../packages/plugin-sdk/src/vite";
import { PLUGIN_SHARED_MODULES } from "../../../packages/plugin-sdk/src/shared";

it("externalises every shared module and maps it to the runtime path", () => {
  const cfg = tiqoraPluginConfig({ entry: "src/index.tsx", outDir: "out/web" });
  const rollup = cfg.build!.rollupOptions!;
  expect(rollup.external).toEqual(PLUGIN_SHARED_MODULES.map((m) => m.specifier));
  expect((rollup.output as { paths: Record<string, string> }).paths["react"]).toBe("/plugin-runtime/react.js");
  expect((rollup.output as { paths: Record<string, string> }).paths["@tiqora/plugin-sdk"]).toBe("/plugin-runtime/sdk.js");
  expect(cfg.build!.lib).toMatchObject({ formats: ["es"], fileName: "index" });
  expect(cfg.build!.outDir).toBe("out/web");
});
```

The backend side of the version pin: add to `backend/tests/test_tiqora_sdk_surface.py`:

```python
def test_python_and_npm_sdk_versions_match() -> None:
    pkg = json.loads((Path(__file__).parents[2] / "packages" / "plugin-sdk" / "package.json").read_text())
    assert pkg["version"] == tiqora_sdk.SDK_VERSION
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && pnpm test --run src/plugins` → FAIL (missing modules).

- [ ] **Step 3: Implement.**
  - `packages/plugin-sdk/package.json`: `name "@tiqora/plugin-sdk"`, `version "0.1.0"`, `private: true`, `type: "module"`, `exports: {".": "./src/index.ts", "./vite": "./src/vite.ts", "./shared": "./src/shared.ts"}`, `scripts.check:preset: "vite build -c test-plugin/vite.config.ts && node scripts/check-preset.mjs"`, `peerDependencies.react "^19.0.0"`, `devDependencies` `vite`, `typescript`, `@types/react` with the same ranges as `frontend/package.json`. `tsconfig.json` mirrors `packages/api-client/tsconfig.json` with `"jsx": "react-jsx"`, `"noEmit": true`.
  - `index.ts`: the P12 types, P13 types (`WidgetProps`, `WidgetSize`, `WidgetCategory`, `WidgetMode`), `SdkRuntime`, and one `export declare const <member>: SdkRuntime["<member>"]` per member. `README.md`: three short sections (what it is, the preset, the stability rule from P3).
  - `vite.ts`: `tiqoraPluginConfig` per P12 (`build.lib = { entry, formats: ["es"], fileName: "index" }`, `cssCodeSplit: false`, `emptyOutDir: true`, `rollupOptions.external`, `rollupOptions.output.paths`).
  - `test-plugin/src/index.tsx`: a 20-line plugin using `definePlugin`, `usePluginTranslation`, `Panel`, `useQuery` and `useNavigate`; `test-plugin/vite.config.ts` = `defineConfig(tiqoraPluginConfig({ entry: "test-plugin/src/index.tsx", outDir: "test-plugin/dist" }))` + `@vitejs/plugin-react` (add it to devDependencies). `scripts/check-preset.mjs`: reads `test-plugin/dist/index.js`, extracts every `from "…"`/`import("…")` specifier, asserts each starts with `/plugin-runtime/` or `./`, asserts the file does not contain `react.production` or `__SECRET_INTERNALS` / `__CLIENT_INTERNALS`, exits 1 with a message otherwise. Add `test-plugin/dist/` is covered by the root `.gitignore` (`dist/`) — do not commit build output.
  - Shims: `react.ts`, `react-jsx-runtime.ts`, `react-dom.ts` — generate the name list once with `cd frontend && node -e "console.log(Object.keys(require('react')).filter(k=>!k.startsWith('__')).sort().join(', '))"` (same for `react/jsx-runtime`, `react-dom`) and write `import * as M from "react"; export default M; export const { <names> } = M;`. `react-query.ts` = `export * from "@tanstack/react-query";`, `react-router.ts` = `export * from "@tanstack/react-router";`, `react-i18next.ts` = `export * from "react-i18next";`, `sdk.ts` = `export { Badge, Button, Dialog, EmptyState, Panel, SDK_VERSION, Spinner, Switch, definePlugin, usePluginApi, usePluginContext, usePluginTranslation } from "../sdkRuntime";`.
  - `sdkRuntime.tsx`: implementations; `usePluginApi` validates `path` (strip one leading `/`; reject empty, any empty segment, `.` or `..` segments → `throw new Error("Ungültiger Pfad für die Schnittstelle der Erweiterung")`), then `api.request(method, \`/api/plugins/${pluginId}/${path}\`, …)`; `usePluginTranslation` = `useTranslation(\`plugin.${pluginId}\`)` returning `{ t }`; `Button`/`Badge`/`Spinner`/`Dialog` wrap the host components with exactly the SDK props; `Switch` maps `label` → `aria-label`; `Panel` = `<section className="rounded-xl border border-hairline bg-surface p-3"><h3 className="text-sm font-semibold text-ink">{title}</h3>…</section>` (actions right of the title); `EmptyState` = muted centred text. End of file: `export const runtimeCheck = { SDK_VERSION, definePlugin, … } satisfies SdkRuntime;`.
  - `frontend/vite.config.ts`: import `PLUGIN_SHARED_MODULES` from `../packages/plugin-sdk/src/shared`; `build.rollupOptions` per P12 (`input.main = path.resolve(__dirname, "index.html")`); dev plugin `pluginRuntimeDev` (`configureServer` middleware rewriting `^/plugin-runtime/([a-z0-9-]+)\.js` to `/src/plugins/runtime/$1.ts`).
  - `frontend/scripts/check-plugin-runtime.mjs`: after a build, for each module in `PLUGIN_SHARED_MODULES` assert `dist/plugin-runtime/<file>.js` exists, that every relative import inside it resolves to an existing file under `dist/`, and that the export list contains a marker name (`useState` for react, `jsx` for jsx-runtime, `createPortal` for react-dom, `useQuery` for react-query, `Link` for react-router, `useTranslation` for react-i18next, `definePlugin` for sdk); exit 1 otherwise.

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && pnpm test --run src/plugins && pnpm lint && pnpm build && node scripts/check-plugin-runtime.mjs && cd ../packages/plugin-sdk && pnpm check:preset`, and `cd backend && uv run python -m pytest -q tests/test_tiqora_sdk_surface.py`.
Expected: all PASS; `frontend/dist/plugin-runtime/` holds 7 files; `dist/assets/` names stay hashed.

- [ ] **Step 5: Commit** — stage `packages/plugin-sdk` (without `test-plugin/dist`), `frontend/src/plugins/runtime`, `frontend/src/plugins/instanceContext.ts`, `frontend/src/plugins/sdkRuntime.tsx`, the three tests, `frontend/scripts/check-plugin-runtime.mjs`, `frontend/vite.config.ts`, `frontend/package.json`, `pnpm-lock.yaml`, `frontend/eslint.config.js` (if changed), `backend/tests/test_tiqora_sdk_surface.py`; message `feat(plugins): plugin SDK package, runtime shim modules and host SDK runtime`.

### Task 13: Frontend plugin loader, provider, error boundary, i18n, safe mode, dashboard widget point (frontend-only)

**Files:**
- Create: `frontend/src/lib/pluginsApi.ts`, `frontend/src/plugins/safeMode.ts`, `frontend/src/plugins/loader.ts`, `frontend/src/plugins/i18n.ts`, `frontend/src/plugins/PluginProvider.tsx`, `frontend/src/plugins/PluginBoundary.tsx`, `frontend/src/plugins/PluginComponent.tsx`, `frontend/src/plugins/dashboardWidgets.ts`
- Modify: i18n `en.json`/`de.json` (`plugins.slotError`, `plugins.slotErrorDetails`, `plugins.safeModeBrowser`) + propagation
- Test: `frontend/src/plugins/safeMode.test.ts`, `loader.test.ts`, `i18n.test.ts`, `PluginProvider.test.tsx`, `PluginBoundary.test.tsx`, `dashboardWidgets.test.tsx`

**Interfaces:**
- Consumes: Task 10 schema types (`Schemas["PluginFrontendOut"]` etc.), Task 12 (`PluginInstanceContext`, types).
- Produces:

```ts
// lib/pluginsApi.ts
export type FrontendPlugin = Schemas["PluginFrontendOut"];
export type FrontendContribution = FrontendPlugin["contributions"][number];
export type PluginAdmin = Schemas["PluginAdminOut"];
export type PluginAdminList = Schemas["PluginAdminListOut"];
export const pluginsApi = {
  frontend(signal?: AbortSignal): Promise<FrontendPlugin[]>,
  adminList(signal?: AbortSignal): Promise<PluginAdminList>,
  setEnabled(id: string, enabled: boolean): Promise<PluginAdmin>,
  reset(id: string): Promise<PluginAdmin>,
};
// plugins/safeMode.ts
export const PLUGIN_SAFE_MODE_KEY = "tiqora.plugins.off";
export function applySafeModeFromUrl(search?: string): void;   // ?plugins=off / ?plugins=on
export function isPluginSafeMode(): boolean;                   // try/catch around sessionStorage
// plugins/loader.ts
export const PLUGIN_IMPORT_TIMEOUT_MS = 10_000;
export type PluginModule = { components: Record<string, ComponentType<any>> };
export function setPluginImporterForTests(importer: ((url: string) => Promise<unknown>) | null): void;
export function setPluginImporter(importer: (url: string) => Promise<unknown>): void;  // demo mode
export function loadPluginModule(plugin: FrontendPlugin): Promise<PluginModule>;      // cached per id@version
export function clearPluginModuleCacheForTests(): void;
// plugins/i18n.ts
export function pluginNamespace(pluginId: string): string;      // `plugin.${pluginId}`
export function ensurePluginI18n(plugin: FrontendPlugin, lang: string): Promise<void>;  // never rejects
// plugins/PluginProvider.tsx
export function PluginProvider({ children }: { children: ReactNode }): JSX.Element;
export function usePlugins(): { plugins: FrontendPlugin[]; ready: boolean; safeMode: boolean };
export function usePluginContributions<S extends FrontendContribution["slot"]>(slot: S):
  { plugin: FrontendPlugin; contribution: Extract<FrontendContribution, { slot: S }> }[];  // sorted by order, then plugin id
// plugins/PluginBoundary.tsx
export function PluginBoundary(props: { plugin: FrontendPlugin; children: ReactNode }): JSX.Element;
// plugins/PluginComponent.tsx
export function PluginComponent(props: { plugin: FrontendPlugin; name: string; props: object }): JSX.Element;
// plugins/dashboardWidgets.ts  — P13 exactly
```

- [ ] **Step 1: Write the failing tests**

```ts
// safeMode.test.ts
it("?plugins=off switches plugins off for the session, ?plugins=on back on", () => {
  applySafeModeFromUrl("?plugins=off");
  expect(isPluginSafeMode()).toBe(true);
  applySafeModeFromUrl("?foo=1");
  expect(isPluginSafeMode()).toBe(true);
  applySafeModeFromUrl("?plugins=on");
  expect(isPluginSafeMode()).toBe(false);
});
it("blocked storage counts as not switched off", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  expect(isPluginSafeMode()).toBe(false);
});

// loader.test.ts
const plugin = { id: "org.example.hello", version: "1.0.0", name: "Hallo", asset_base: "/api/v1/plugins/org.example.hello/assets/1.0.0/",
                 entry: "web/index.js", styles: ["web/hello.css"], locales: ["de"], contributions: [] } as FrontendPlugin;
it("imports the entry once and returns its components", async () => {
  const Comp = () => null;
  const importer = vi.fn().mockResolvedValue({ default: { components: { HelloPanel: Comp } } });
  setPluginImporterForTests(importer);
  const [a, b] = await Promise.all([loadPluginModule(plugin), loadPluginModule(plugin)]);
  expect(a.components.HelloPanel).toBe(Comp);
  expect(b).toBe(a);
  expect(importer).toHaveBeenCalledTimes(1);
  expect(importer).toHaveBeenCalledWith("/api/v1/plugins/org.example.hello/assets/1.0.0/web/index.js");
  expect(document.querySelectorAll('link[href="/api/v1/plugins/org.example.hello/assets/1.0.0/web/hello.css"]')).toHaveLength(1);
});
it("loader times out", async () => {
  vi.useFakeTimers();
  setPluginImporterForTests(() => new Promise(() => {}));
  const p = loadPluginModule(plugin);
  vi.advanceTimersByTime(PLUGIN_IMPORT_TIMEOUT_MS + 1);
  await expect(p).rejects.toThrow(/Zeit/);
  vi.useRealTimers();
});
it("rejects a module without components", async () => {
  setPluginImporterForTests(() => Promise.resolve({ default: {} }));
  await expect(loadPluginModule(plugin)).rejects.toThrow(/ungültig/);
});
it("a failed load is not cached forever", async () => {
  const importer = vi.fn().mockRejectedValueOnce(new Error("404")).mockResolvedValue({ default: { components: {} } });
  setPluginImporterForTests(importer);
  await expect(loadPluginModule(plugin)).rejects.toThrow();
  await expect(loadPluginModule(plugin)).resolves.toEqual({ components: {} });
});

// i18n.test.ts (fetch mocked with vi.stubGlobal)
it("loads the language file into the plugin namespace", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ panel: { title: "Hallo" } }), { status: 200 }));
  await ensurePluginI18n(plugin, "de");
  expect(i18n.getResource("de", "plugin.org.example.hello", "panel.title")).toBe("Hallo");
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/plugins/org.example.hello/assets/1.0.0/i18n/de.json", expect.anything());
});
it("falls back to English, then to nothing, without throwing", async () => {
  const p = { ...plugin, locales: ["en"] };
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ panel: { title: "Hello" } }), { status: 200 }));
  await ensurePluginI18n(p, "fr");
  expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/i18n\/en\.json$/), expect.anything());
  await expect(ensurePluginI18n({ ...plugin, locales: [] }, "fr")).resolves.toBeUndefined();
});

// PluginProvider.test.tsx (vi.mock("@/lib/pluginsApi"))
it("fetches plugins and sorts slot contributions by order", async () => {
  const panel = (key: string, order: number) => ({ slot: "ticket.sidebar", key, component: "P", title_key: "t", order });
  frontend.mockResolvedValue([
    { ...plugin, id: "org.example.a", contributions: [panel("late", 200)] },
    { ...plugin, id: "org.example.b", contributions: [panel("early", 50)] },
  ]);
  const Slots = () => {
    const items = usePluginContributions("ticket.sidebar");
    return <p data-testid="slots">{items.map((i) => `${i.plugin.id}:${i.contribution.key}`).join(",")}</p>;
  };
  render(<Wrapper><Slots /></Wrapper>);
  await waitFor(() => expect(screen.getByTestId("slots")).toHaveTextContent("org.example.b:early,org.example.a:late"));
});
it("in browser safe mode fetches nothing", async () => {
  sessionStorage.setItem(PLUGIN_SAFE_MODE_KEY, "1");
  render(<Wrapper><Probe /></Wrapper>);
  expect(frontend).not.toHaveBeenCalled();
  expect(await screen.findByTestId("probe")).toHaveTextContent("0 safe");
});
it("an API error means no plugins, no crash", async () => {
  frontend.mockRejectedValue(new Error("500"));
  render(<Wrapper><Probe /></Wrapper>);
  expect(await screen.findByTestId("probe")).toHaveTextContent("0 ready");
});

// PluginBoundary.test.tsx
it("PluginBoundary contains a render error", async () => {
  const Boom = () => { throw new Error("kaputt"); };
  vi.spyOn(console, "error").mockImplementation(() => {});
  render(<div><PluginBoundary plugin={plugin}><Boom /></PluginBoundary><p>Nachbar</p></div>);
  expect(screen.getByText("Erweiterung „Hallo“ konnte nicht geladen werden.")).toBeInTheDocument();
  expect(screen.getByText("Nachbar")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(screen.getByText(/kaputt/)).toBeInTheDocument();
});
it("PluginComponent with an unknown name shows the boundary message", async () => {
  setPluginImporterForTests(() => Promise.resolve({ default: { components: {} } }));
  render(<PluginBoundary plugin={plugin}><PluginComponent plugin={plugin} name="Nope" props={{}} /></PluginBoundary>);
  expect(await screen.findByText(/konnte nicht geladen werden/)).toBeInTheDocument();
});
it("provides the plugin instance context to the component", async () => {
  const Who = () => <span>{useContext(PluginInstanceContext)?.pluginId}</span>;
  setPluginImporterForTests(() => Promise.resolve({ default: { components: { Who } } }));
  render(<PluginBoundary plugin={plugin}><PluginComponent plugin={plugin} name="Who" props={{}} /></PluginBoundary>);
  expect(await screen.findByText("org.example.hello")).toBeInTheDocument();
});

// dashboardWidgets.test.tsx
it("registers plugin widgets once with owner and namespace", () => {
  registerPluginWidgets([withWidget]); registerPluginWidgets([withWidget]);
  const w = DASHBOARD_WIDGETS.get("org.example.hello.weather")!;
  expect(w.owner).toBe("org.example.hello");
  expect(w.ns).toBe("plugin.org.example.hello");
  expect(w.size).toEqual({ default: [4, 2], min: [2, 2], max: [6, 4] });
  expect(w.dataEndpoint).toBeNull();
  expect(DASHBOARD_WIDGETS.list().filter((x) => x.owner === "org.example.hello")).toHaveLength(1);
});
it("useDashboardWidgetDefinitions re-renders after plugins are registered", () => {
  DASHBOARD_WIDGETS.clearForTests();
  const { result } = renderHook(() => useDashboardWidgetDefinitions().filter((w) => w.owner !== "core"));
  expect(result.current).toHaveLength(0);
  act(() => registerPluginWidgets([withWidget]));
  expect(result.current.map((w) => w.key)).toEqual(["org.example.hello.weather"]);
});
```

- [ ] **Step 2: Run to verify failure** — `cd frontend && pnpm test --run src/plugins` → FAIL.
- [ ] **Step 3: Implement** per P12/P13. Component maps that must accept any props (`PluginModule.components`, `PluginDefinition`) use `ComponentType<any>` with a single-line `// eslint-disable-next-line @typescript-eslint/no-explicit-any` (a narrower type is rejected by `strictFunctionTypes` for components with specific props). `PluginProvider`: calls `applySafeModeFromUrl(window.location.search)` once; `useQuery({ queryKey: ["plugins", "frontend"], queryFn, enabled: !safeMode, staleTime: Infinity, retry: false })`; on data: `registerPluginWidgets`, and `ensurePluginI18n` for each plugin with locales for `i18n.language` (again on `i18n.on("languageChanged")`); `ready` becomes true when the query settled (success or error) or safe mode. `PluginBoundary`: class component with `getDerivedStateFromError`, renders `<Suspense fallback={<Spinner />}>` and `<PluginInstanceContext.Provider value={{ pluginId, version }}>`, logs `console.error("[plugin org.example.hello]", error)` once per error. `PluginComponent`: `useMemo(() => lazy(() => loadPluginModule(plugin).then((m) => { const C = m.components[name]; if (!C) throw new Error(\`Baustein „${name}“ fehlt\`); return { default: C }; })), [plugin.id, plugin.version, name])`. `registerPluginWidgets` maps each `dashboard.widget` contribution to a `WidgetDefinition` whose `component` renders `<PluginBoundary plugin><PluginComponent plugin name props /></PluginBoundary>`; `settingsSchema` from the contribution; skips keys already registered by the same owner; bumps a module-level version used by `useDashboardWidgetDefinitions` (`useSyncExternalStore`).
- [ ] **Step 4: Run to verify pass** — `pnpm test --run src/plugins && pnpm lint && node scripts/check-i18n-keys.mjs` → PASS.
- [ ] **Step 5: Commit** — stage the listed files + touched locale files; message `feat(plugins): frontend plugin loader with per-slot containment and safe mode`.

### Task 14: Slots — sidebar entry and page, ticket panels, home-page widgets; demo plugin (frontend-only)

**Files:**
- Create: `frontend/src/plugins/slots.tsx`, `frontend/src/routes/agent/PluginRoutePage.tsx`, `frontend/src/demo/demoPlugin.tsx`, `frontend/src/components/layout/NavItem.tsx` (moved from `AgentShell.tsx`)
- Modify: `frontend/src/components/layout/AgentShell.tsx` (wrap with `PluginProvider`; `<PluginNavItems group="workspace" />` at the end of the "workspace" `NavGroup`, likewise `knowledge`, `insights`; a new `NavGroup id="plugins" title={t("sidebar.plugins")}` after "insights", rendered only when it has items), `frontend/src/components/layout/AdminShell.tsx` (wrap with `PluginProvider` — needed by Task 15's settings i18n), `frontend/src/router.tsx` (`agentPluginRoute`: `getParentRoute: () => agentLayoutRoute, path: "/plugins/$pluginId/$"`, component `PluginRoutePage`; add to the agent children), `frontend/src/routes/agent/TicketZoomPage.tsx` (`<PluginTicketPanels ticketId={ticketId} />` between `ProcessWidget` and `PresenceBar`), `frontend/src/routes/agent/DashboardPage.tsx` (`<PluginWidgetsSection />` as the last child of the page container), `frontend/src/demo/mockData.ts` (`/api/v1/plugins/frontend` → one demo plugin `org.example.demo` with nav+route+ticket.sidebar+dashboard.widget; `/api/v1/plugins/org.example.demo/assets/1.0.0/i18n/de.json` and `en.json`), the demo bootstrap (where `VITE_DEMO` starts MSW — `grep -rn "VITE_DEMO" frontend/src/main.tsx frontend/src/demo`) calls `setPluginImporter((url) => url.includes("/org.example.demo/") ? import("@/demo/demoPlugin") : Promise.reject(new Error("unbekannt")))`, `frontend/e2e/fixtures/mock-api.ts` (`GET /api/v1/plugins/frontend` → `[]`), i18n (`sidebar.plugins`, `plugins.pageNotFound`, `plugins.widgetsTitle`) + propagation
- Test: `frontend/src/plugins/slots.test.tsx`, `frontend/src/routes/agent/PluginRoutePage.test.tsx`; update `AgentShell.test.tsx`, `TicketZoomPage` and `DashboardPage` tests (add `vi.mock("@/lib/pluginsApi", () => ({ pluginsApi: { frontend: vi.fn().mockResolvedValue([]) } }))` where they render the shell/page, so no real request is made)

**Interfaces:**
- Consumes: Task 13 (`usePluginContributions`, `PluginBoundary`, `PluginComponent`, `useDashboardWidgetDefinitions`, `pluginNamespace`).
- Produces: `PluginNavItems({ group, onNavigate? })`, `PluginTicketPanels({ ticketId })`, `PluginWidgetsSection()` in `slots.tsx`; `PluginRoutePage`; `resolvePluginRoute(contributions, splat) -> { contribution, subPath } | null` (exported from `PluginRoutePage.tsx` for tests).

- [ ] **Step 1: Write the failing tests**

```tsx
// slots.test.tsx — PluginProvider fed by a mocked pluginsApi.frontend returning HELLO (the fixture's contributions)
it("renders nav entries of a group as links to the plugin page, hiding admin-only ones for agents", async () => {
  renderWithRouter(<PluginNavItems group="plugins" />, { user: { is_admin: false } });
  const link = await screen.findByRole("link", { name: "Hallo" });
  expect(link).toHaveAttribute("href", "/agent/plugins/org.example.hello/overview");
  expect(screen.queryByRole("link", { name: "Hallo (Admin)" })).toBeNull();
});
it("nav label falls back to the contribution key without translations", async () => {
  frontend.mockResolvedValue([{ ...HELLO, id: "org.example.untranslated", locales: [] }]);
  renderWithRouter(<PluginNavItems group="plugins" />, { user: { is_admin: false } });
  expect(await screen.findByRole("link", { name: "overview" }))
    .toHaveAttribute("href", "/agent/plugins/org.example.untranslated/overview");
});
it("renders ticket panels in order with the ticket id", async () => {
  setPluginImporterForTests(() => Promise.resolve({ default: { components: { HelloPanel: ({ ticketId }: { ticketId: number }) => <p>Ticket {ticketId}</p> } } }));
  render(<Providers><PluginTicketPanels ticketId={42} /></Providers>);
  expect(await screen.findByText("Ticket 42")).toBeInTheDocument();
  expect(screen.getByTestId("plugin-ticket-panel-org.example.hello-info")).toBeInTheDocument();
});
it("a broken panel does not hide the others", async () => {
  const BROKEN = { ...HELLO, id: "org.example.broken", name: "Kaputt", asset_base: "/api/v1/plugins/org.example.broken/assets/1.0.0/" };
  frontend.mockResolvedValue([HELLO, BROKEN]);
  vi.spyOn(console, "error").mockImplementation(() => {});
  setPluginImporterForTests((url) => url.includes("org.example.broken")
    ? Promise.reject(new Error("404"))
    : Promise.resolve({ default: { components: { HelloPanel: ({ ticketId }: { ticketId: number }) => <p>Ticket {ticketId}</p> } } }));
  render(<Providers><PluginTicketPanels ticketId={7} /></Providers>);
  expect(await screen.findByText("Ticket 7")).toBeInTheDocument();
  expect(await screen.findByText("Erweiterung „Kaputt“ konnte nicht geladen werden.")).toBeInTheDocument();
});
it("widgets section is absent without plugin widgets and lists them otherwise", async () => {
  DASHBOARD_WIDGETS.clearForTests();
  frontend.mockResolvedValue([]);
  const { unmount } = render(<Providers><PluginWidgetsSection /></Providers>);
  await waitFor(() => expect(frontend).toHaveBeenCalled());
  expect(screen.queryByTestId("plugin-widgets")).toBeNull();
  unmount();
  frontend.mockResolvedValue([HELLO]);
  setPluginImporterForTests(() => Promise.resolve({ default: { components: {
    HelloWidget: ({ settings }: { settings: { city: string } }) => <p>Stadt {settings.city}</p> } } }));
  render(<Providers fresh><PluginWidgetsSection /></Providers>);
  expect(await screen.findByRole("heading", { name: "Erweiterungen" })).toBeInTheDocument();
  expect(await screen.findByText("Stadt Bonn")).toBeInTheDocument();
});

// PluginRoutePage.test.tsx
it("resolves the longest matching path and passes the rest as subPath", () => {
  const cs = [route("overview"), route("overview/details"), route("admin")];
  expect(resolvePluginRoute(cs, "overview/details/7")).toMatchObject({ contribution: { path: "overview/details" }, subPath: "7" });
  expect(resolvePluginRoute(cs, "overview")).toMatchObject({ contribution: { path: "overview" }, subPath: "" });
  expect(resolvePluginRoute(cs, "overviewx")).toBeNull();
});
it("unknown plugin page shows the not-found text", async () => {
  renderAt("/agent/plugins/org.example.hello/nope");
  expect(await screen.findByText("Diese Seite der Erweiterung gibt es nicht.")).toBeInTheDocument();
});

// AgentShell.test.tsx (added case)
it("shows an Erweiterungen group with plugin entries", async () => {
  frontend.mockResolvedValue([HELLO]);
  renderShell();
  expect(await screen.findByText("Erweiterungen")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Hallo" })).toHaveAttribute("href", "/agent/plugins/org.example.hello/overview");
});
```

(`Providers` = `QueryClientProvider` with a fresh `QueryClient` per render (`fresh` forces a new one) + `I18nextProvider` + `PluginProvider` + a memory router; `renderWithRouter(ui, { user })` additionally mocks `useAuth` with that user; `HELLO` is the fixture plugin's frontend entry as Task 10 returns it, with the de bundle pre-loaded via `i18n.addResourceBundle`.)

- [ ] **Step 2: Run to verify failure** — `cd frontend && pnpm test --run src/plugins src/routes/agent/PluginRoutePage.test.tsx src/components/layout/AgentShell.test.tsx` → FAIL.
- [ ] **Step 3: Implement** per P12 slots. `PluginNavItems` reuses the existing `NavItem` component: move `NavItem` and `NavCountBadge` unchanged from `AgentShell.tsx` into `frontend/src/components/layout/NavItem.tsx` (exporting them from `AgentShell.tsx` would create an import cycle with `slots.tsx`), import them in both places. Plugin entries use `icon={<PuzzleIcon />}` (add a `PuzzleIcon` to `frontend/src/components/ui/icons.tsx` if none exists; unknown `icon` names fall back to it), label `t(\`${pluginNamespace(id)}:${label_key}\`, { defaultValue: key })`, hides `admin_only` when `!user?.is_admin`. `PluginTicketPanels`: one `<div data-testid="plugin-ticket-panel-<id>-<key>">` per contribution, each `<PluginBoundary plugin><PluginComponent plugin name={component} props={{ ticketId }} /></PluginBoundary>`; renders nothing when there are none. `PluginWidgetsSection`: plugin-owned `useDashboardWidgetDefinitions()`; `<section data-testid="plugin-widgets">` with heading `plugins.widgetsTitle` and a responsive grid (`grid-cols-1 md:grid-cols-12`; each widget spans `size.default[0]` columns on md+ via a literal class map `{1: "md:col-span-1", …, 12: "md:col-span-12"}` — Tailwind cannot see template-built class names), props `{ instanceId: \`home-${key}\`, settings: defaults from settingsSchema, size: { w, h }, filters: {}, mode: "view" }`. `PluginRoutePage`: `useParams({ strict: false })` → `pluginId`, `_splat`; title `t(ns:title_key)` as page heading; `admin_only` route for non-admins → not-found text. Demo plugin: `demoPlugin.tsx` default-exports `{ components: { DemoPage, DemoPanel, DemoWidget } }` built from host components (it is in-tree, so it imports `@/plugins/sdkRuntime` directly instead of the shim URLs).
- [ ] **Step 4: Run to verify pass**: `pnpm test --run && pnpm lint && node scripts/check-i18n-keys.mjs` → PASS. Demo check: `cd frontend && VITE_DEMO=1 pnpm dev`, then with Playwright open `/agent` (sidebar shows "Erweiterungen" → "Demo"), click it (page renders inside the shell), open any demo ticket (panel "Demo" between process widget and presence bar), go back to `/agent` (section "Erweiterungen" with the demo widget), then `/agent?plugins=off` (no "Erweiterungen" group; reload keeps it off). Screenshots light + dark. Delete `.playwright-mcp` before committing.
- [ ] **Step 5: Commit** — stage the listed files, updated tests, `e2e/fixtures/mock-api.ts`, locale files; message `feat(plugins): plugin slots in sidebar, ticket view and home page`.

### Task 15: Admin page "Erweiterungen" and plugin settings forms (frontend-only)

**Files:**
- Create: `frontend/src/routes/admin/PluginsPage.tsx`
- Modify: `frontend/src/router.tsx` (`adminPluginsRoute` `/plugins` under the admin layout), `frontend/src/lib/adminSearch.ts` (entry `{ slug: "plugins", route: "/admin/plugins", group: "system", nameKey: "admin.nav.plugins", descriptionKey: "admin.pageDescriptions.plugins", keywords: ["erweiterungen", "plugins", "add-ons", "module", "pakete", "opm", "sicherer modus", "safe mode"] }` after `daemons`), `frontend/src/routes/admin/SettingsSectionPage.tsx` (for keys starting `plugins.`: wait for `ensurePluginI18n` of the matching plugin from `usePlugins()` before rendering the form; a plugin missing from the list renders the form with schema titles), `frontend/src/demo/mockData.ts` (`/api/v1/admin/plugins` with one active, one quarantined, one "restart_required" plugin; `/api/v1/admin/settings-sections` gains `plugins.org.example.demo`; `/api/v1/admin/settings-sections/plugins.org.example.demo` with a two-field schema incl. a secret), i18n `admin.nav.plugins`, `admin.pageDescriptions.plugins`, `admin.plugins.*` (P15) + propagation
- Test: `frontend/src/routes/admin/PluginsPage.test.tsx`, `frontend/src/routes/admin/SettingsSectionPage.test.tsx` (add a case), `frontend/src/routes/admin/AdminHomePage.test.tsx` / `AdminShell.test.tsx` (whatever asserts the page list — add `admin-nav-plugins`)

**Interfaces:**
- Consumes: Task 10 (`pluginsApi.adminList/setEnabled/reset`, admin schemas), Task 13 (`usePlugins`, `ensurePluginI18n`, `isPluginSafeMode`).
- Produces: `PluginsPage` (testids: `admin-plugins-page`, `plugin-card-<id>`, `plugin-status-<id>`, `plugin-enabled-<id>`, `plugin-settings-<id>`, `plugin-retry-<id>`, `plugin-details-<id>`, `admin-plugins-safe-mode`, `admin-plugins-browser-off`, `admin-plugins-empty`).

- [ ] **Step 1: Write the failing tests** `PluginsPage.test.tsx` (mock `@/lib/pluginsApi`; fixtures `ACTIVE` (hello, status active, settings_section set, capabilities incl. host and slot), `QUARANTINED` (status quarantined, worker runtime state quarantined, boot_attempts 3), `TRIPPED` (active with one hook `tripped: true, consecutive_failures: 5, last_error: "RuntimeError: kaputt"`)):

```tsx
it("lists plugins with plain-language status", async () => {
  adminList.mockResolvedValue({ safe_mode: false, plugins: [ACTIVE, QUARANTINED] });
  renderPage();
  expect(await screen.findByTestId("plugin-status-org.example.hello")).toHaveTextContent("Aktiv");
  expect(screen.getByTestId("plugin-status-org.example.crashy")).toHaveTextContent("Übersprungen");
  expect(screen.getByText("Beim Start wiederholt abgestürzt – wird übersprungen, bis Sie „Erneut versuchen“ wählen.")).toBeInTheDocument();
});
it("switching off calls the API and shows the restart note", async () => {
  adminList.mockResolvedValue({ safe_mode: false, plugins: [ACTIVE] });
  setEnabled.mockResolvedValue({ ...ACTIVE, enabled: false, status: "restart_required",
                                 status_message: "Änderung wird nach einem Neustart von Tiqora wirksam." });
  renderPage();
  fireEvent.click(await screen.findByRole("checkbox", { name: /Hallo-Erweiterung/ }));
  await waitFor(() => expect(setEnabled).toHaveBeenCalledWith("org.example.hello", false));
  expect(await screen.findByText("Änderung wird nach einem Neustart von Tiqora wirksam.")).toBeInTheDocument();
  expect(screen.getByTestId("plugin-status-org.example.hello")).toHaveTextContent("Neustart nötig");
});
it("retry resets quarantine and stopped hooks", async () => {
  adminList.mockResolvedValue({ safe_mode: false, plugins: [QUARANTINED, TRIPPED] });
  reset.mockResolvedValue({ ...QUARANTINED, status: "disabled" });
  renderPage();
  fireEvent.click(await screen.findByTestId("plugin-retry-org.example.crashy"));
  await waitFor(() => expect(reset).toHaveBeenCalledWith("org.example.crashy"));
  fireEvent.click(screen.getByTestId("plugin-details-org.example.tripped"));
  expect(screen.getByText("Angehalten nach 5 Fehlern")).toBeInTheDocument();
  expect(screen.getByText("RuntimeError: kaputt")).toBeInTheDocument();
  expect(screen.getByTestId("plugin-retry-org.example.tripped")).toBeInTheDocument();
});
it("shows capabilities in plain words with the trust note", async () => {
  adminList.mockResolvedValue({ safe_mode: false, plugins: [ACTIVE] });
  renderPage();
  fireEvent.click(await screen.findByTestId("plugin-details-org.example.hello"));
  expect(screen.getByText("Ruft api.example.org auf")).toBeInTheDocument();
  expect(screen.getByText("Zeigt ein Feld in der Ticketansicht")).toBeInTheDocument();
  expect(screen.getByText(/keine Absicherung/)).toBeInTheDocument();
  expect(screen.getByText("Weboberfläche und Schnittstelle")).toBeInTheDocument();
});
it("settings button links to the generated form", async () => {
  adminList.mockResolvedValue({ safe_mode: false, plugins: [ACTIVE] });
  renderPage();
  expect(await screen.findByTestId("plugin-settings-org.example.hello")).toHaveAttribute("href", "/admin/settings/plugins.org.example.hello");
});
it("safe mode banner, browser-off note and empty state", async () => {
  adminList.mockResolvedValue({ safe_mode: true, plugins: [] });
  sessionStorage.setItem(PLUGIN_SAFE_MODE_KEY, "1");
  renderPage();
  expect(await screen.findByTestId("admin-plugins-safe-mode")).toBeInTheDocument();
  expect(screen.getByTestId("admin-plugins-browser-off")).toBeInTheDocument();
  expect(screen.getByTestId("admin-plugins-empty")).toBeInTheDocument();
});
```

`SettingsSectionPage.test.tsx` added case: route `/admin/settings/plugins.org.example.hello`, `usePlugins` mocked to return HELLO, `ensurePluginI18n` mocked to add `{settings: {fields: {greeting: {label: "Begrüßung"}}}}` to namespace `plugin.org.example.hello`; assert the label "Begrüßung" is rendered and `ensurePluginI18n` was called with HELLO before the form appeared.

- [ ] **Step 2: Run to verify failure** — `cd frontend && pnpm test --run src/routes/admin/PluginsPage.test.tsx src/routes/admin/SettingsSectionPage.test.tsx` → FAIL.
- [ ] **Step 3: Implement.** One card per plugin (name, version, publisher, description, status chip via the existing `StatusChip`, `status_message`, `Switch` with `aria-label` = plugin name, buttons "Einstellungen" (a `Link` to `/admin/settings/$section`, only when `settings_section`) and "Erneut versuchen" (when status is `quarantined`/`failed` or any hook is tripped), a "Details" disclosure with: process table (role label, state label, version, error), "Automatische Abläufe" list (name, schedule text or "bei Ticketänderungen", stopped note, last error), "Was diese Erweiterung angibt" list + note. Mutations invalidate `["admin", "plugins"]`; errors via `apiErrorMessage`. Page header text uses `admin.nav.plugins`; banner texts from P15.
- [ ] **Step 4: Run to verify pass**: `pnpm test --run && pnpm lint && node scripts/check-i18n-keys.mjs`. Demo check with Playwright (`VITE_DEMO=1`): `/admin/plugins` light + dark, open details, toggle, "Einstellungen" → generated form with labels from the demo plugin bundle; also the ⌘K palette finds "Erweiterungen". Delete `.playwright-mcp`.
- [ ] **Step 5: Commit** — message `feat(plugins): admin page for plugins and plugin settings forms`.

### Task 16: Documentation and full verification

**Files:**
- Create: `docs/plugins.md` (German-first admin part + English developer part, like other docs in `docs/`)
- Modify: `docs/deployment.md` (section "Erweiterungen einbauen (Plugins)": bake workflow, env vars `TIQORA_PLUGINS_SAFE_MODE`, `TIQORA_PLUGINS_DISABLED`, `TIQORA_PLUGINS_AUTO_ENABLE`, `TIQORA_PLUGINS_IMPORT_TIMEOUT_SECONDS`, `TIQORA_PLUGIN_EVENTS_INTERVAL_SECONDS`, `TIQORA_PLUGIN_EVENTS_GAP_HOLD_SECONDS`, `TIQORA_PLUGIN_TASKS_INTERVAL_SECONDS`; the `tiqora` entrypoint role), `docs/deploy/docker-compose.md` (env table rows), `docs/architecture.md` (new "Plugins" subsection after "Extension registry": discovery → bootstrap → registrar → points, process roles, state tables, safe mode/quarantine, history follower, runtime shims + CSP; replace "a later plugin loader will call …" with the real flow; Non-goals line "Znuny package manager / OPM marketplace" → "Znuny package manager (OPM): Perl packages cannot run; Tiqora has its own plugin system (docs/plugins.md)"; line 560–561 "Tiqora has no plugin system" → "…is not mapped (Znuny calendar plugins are Perl)"), `docs/compatibility.md` (row "Package Manager (OPM) …" at the "by design" table: keep "by design" for OPM itself, add a sentence that Tiqora plugins (phase 1: settings, routes, ticket events, scheduled tasks, background jobs, MCP tools, four UI slots) replace OPM add-ons), `docs/superpowers/specs/2026-10-08-plugin-system-design.md` (status line: "Phase 1 implemented 2026-10-xx — see plan …"; no other change)
- No change: `site/compare.html`, `README.md`

**Interfaces:** none.

- [ ] **Step 1: Write `docs/plugins.md`** with these sections: (1) Was Erweiterungen sind (tier 1, full trust, own repositories only, marketplace later); (2) Erweiterungen einbauen — `plugins.txt` example with an internal index and a local wheel, `docker run --rm ghcr.io/cygnusnetworks/tiqora:<ver> tiqora plugins bake /work/plugins.txt --output /work/build` (with `-v "$PWD:/work"`), `docker build -t tiqora-mit-erweiterungen build/`, switch the compose image, restart; (3) Ein- und ausschalten, Neustart, Zustände (each `PluginStatus` explained), "Erneut versuchen"; (4) Sicherer Modus (`TIQORA_PLUGINS_SAFE_MODE=1`, `TIQORA_PLUGINS_DISABLED`, `?plugins=off`); (5) Developer guide (English): package layout, `pyproject.toml` with `[project.entry-points."tiqora.plugins"]` and the hatch `artifacts` rule (**files ignored by git are dropped from the wheel otherwise**), `tiqora-plugin.toml` reference (P1), `register()` with every registrar method and one example each (settings, route, on_event incl. the event-name table from P8, scheduled_task, job_handler, mcp_tool), `PluginContext` (`settings()`, `http` + outbound allowlist, `tickets.get`, logger), frontend: `@tiqora/plugin-sdk/vite` preset, `web/manifest.json` reference, slots and props, `WidgetProps`, CSP consequences (no external scripts/fetches from the browser — call your own backend route), i18n namespace `plugin.<id>`, SDK stability policy (P3), long requests → background jobs (90 s proxy limit); (6) Limits of phase 1 (Deviations 8, 9, 12–14 in plain words).
- [ ] **Step 2: Update the other docs** as listed.
- [ ] **Step 3: Full backend run** — `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → all pass; `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora src/tiqora_sdk` → clean; `git diff --stat backend/tests/db_leak_baseline.txt` → empty.
- [ ] **Step 4: Full frontend run** — `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs && pnpm build && node scripts/check-plugin-runtime.mjs`; `cd ../packages/plugin-sdk && pnpm check:preset` → all pass.
- [ ] **Step 5: OpenAPI drift check** — regenerate both files (Global Constraints command) and confirm `rtk git diff --stat packages/api-client/openapi.json docs/api/openapi.json` is empty (Task 10 already committed them).
- [ ] **Step 6: Baked-image smoke test (when Docker is available; otherwise record "skipped: no Docker")** — in the scratchpad (never in the repo): copy `backend/tests/plugins/fixtures/tq_fixture_hello` into `<scratch>/hello/src/tq_fixture_hello`, add a `pyproject.toml` (hatchling, name `tq-fixture-hello`, version `1.0.0`, `[project.entry-points."tiqora.plugins"] "org.example.hello" = "tq_fixture_hello:plugin"`, `[tool.hatch.build.targets.wheel] packages = ["src/tq_fixture_hello"]`, `artifacts = ["src/tq_fixture_hello/web/**", "src/tq_fixture_hello/i18n/**"]`), `uv build --wheel`; `docker build -t tiqora:plugins-base .` (worktree root); `docker run --rm -v <scratch>:/work tiqora:plugins-base tiqora plugins bake /work/plugins.txt --output /work/build --base-image tiqora:plugins-base` with `plugins.txt` = `./hello/dist/tq_fixture_hello-1.0.0-py3-none-any.whl`; `docker build -t tiqora:plugins-baked <scratch>/build` (the final `verify --import --strict` step must pass); `docker run --rm tiqora:plugins-baked tiqora plugins list --json` shows `org.example.hello` with `"problem": null`. Then rebuild once with `artifacts` removed from the plugin `pyproject.toml` and with `web/` added to a scratch `.gitignore` + `git init` in `<scratch>/hello` — the baked build must FAIL at the verify step naming `web/index.js` (this proves the hatch trap is caught). Record both outcomes in the task report.
- [ ] **Step 7: Commit** — stage the docs explicitly; message `docs(plugins): plugin system phase 1 — admin and developer guide, deployment, architecture`.

---

## Self-review notes (for the executor)

- Spec coverage (phase 1 row): registry + SDK skeletons → Tasks 1, 3, 12; manifest → 1; entry-point loading → 1, 3, 4; settings schema → admin form → 3, 5, 15; async event subscribers → 6, 7; routes → 5; worker jobs (interval/daily/cron/job handlers) → 2, 8; MCP tools → 9; frontend loader + slots → 12–14; plugin admin page → 10, 15; safe mode → 4 (backend), 13 (browser), 10/15 (visible); baked image → 11, 16.
- The fixture plugin in `backend/tests/plugins/fixtures/tq_fixture_hello` is the contract test for every phase-1 extension point (spec "Tests": "each extension point: contract test with a fixture plugin in the repo"); keep it using only `tiqora_sdk` imports.
- Every process that hosts points calls `init_registry` before anything captures `get_registry()`; the ai-worker deliberately does not.
- Interfaces that later wave-2 plans consume: `WidgetDefinition` / `DASHBOARD_WIDGETS` / `WidgetProps` (dashboards plan), `PluginRegistrar` methods + `sdk_stable` points (plugin phase 2 adds more), `tiqora_plugin*` tables (phase 2 adds columns for runtime install and signatures).

<!-- END -->
