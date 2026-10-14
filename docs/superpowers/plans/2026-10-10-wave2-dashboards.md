# Wave 2 — Dashboards Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed agent home page with a widget dashboard: several dashboards per agent (tabs), a 12-column grid with direct-manipulation edit mode (drag, resize, keyboard, undo/redo, draft), a widget gallery, generated settings forms with live preview, one batched data endpoint, the ten phase-1 widgets, a built-in default template that reproduces today's home page, and admin role templates (optionally locked) plus sharing with groups.

**Architecture:** Backend: new package `tiqora.dashboard` (schemas + pure layout normalisation, widget-settings models, default template, store with optimistic locking, role/template resolution, provider registry `DASHBOARD_PROVIDERS`, runner with per-provider timeout and own DB session per provider, counter samples for delta/sparkline) behind `/api/v1/dashboards*`, `/api/v1/dashboard-widget-types*` and `/api/v1/admin/dashboard-templates*`. Five tables (`tiqora_dashboard`, `_role`, `_group`, `_widget`, `_counter_sample`). Frontend: new `frontend/src/dashboard/` module — a pure layout engine (grid maths, breakpoint mapping, undo/redo reducer), `defineWidget` adapter that registers the core widgets into the plugin plan's `DASHBOARD_WIDGETS` point with the shared `WidgetDefinition` contract, widget chrome, view grid with data hook (batch + per-widget refresh via SSE/interval), edit mode (toolbar, drag/resize with pointer events, keyboard, draft in localStorage), gallery panel, settings drawer, tab row, share dialog; `DashboardPage` becomes the host. Wallboard/kiosk, report widgets, dashboard-wide filters, drill-down and template-update merge are phase 2 and only get hooks.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (MariaDB and PostgreSQL), Alembic, pydantic v2, pytest; React 18, TanStack Query/Router, Tailwind (existing tokens), i18next, Vitest, Playwright (demo mode). No grid or drag library.

**Spec:** `docs/superpowers/specs/2026-10-08-dashboard-widgets-design.md` (phase 1 row of "Phases"). Visual and interaction design is the **approved phase-0 mockup** (`Main`, `ViewLight`, `Edit`, `Settings`, `Mobile` of the dashboard mockup project; `Wallboard` is phase 2 and is not built here).

## Global Constraints

- Work in the worktree `<worktree>` on the local branch `roadmap-wave1`; never touch `<repo>`; never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data (example.org, .test). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with the `Co-Authored-By` trailer the executing session's attribution reminder prescribes.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows and never get added to `backend/tests/db_leak_baseline.txt`.
- Seed ids only inside this plan's band **49700–49999** (sub-bands per task below; confirm free with `grep -rn "497[0-9][0-9]\|498[0-9][0-9]\|499[0-9][0-9]" backend/tests` before use, skill `pytest-session-container-id-band-collisions`). Logins/names contain `dash497`.
- DB tests that touch SQL run on BOTH dialects (`@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])`, pattern `backend/tests/test_reports_api.py`) and create the new tables with `TiqoraBase.metadata.create_all`; the module deletes every row it wrote.
- Migrations: Alembic two-chain setup; new migration in `backend/alembic/versions_tiqora`; `down_revision` = the CURRENT primary head at implementation time (`ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — never hardcode; other wave-2 plans add migrations before this one lands); follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and the `version ==` assert in `backend/tests/test_migrate_cli.py`. ORM in `backend/src/tiqora/db/tiqora/models.py` must produce the migration's DDL (`backend/tests/test_tiqora_model_ddl.py`). Tables only `tiqora_*`; never change Znuny DDL; **this plan writes no Znuny table**.
- MySQL/MariaDB AND PostgreSQL must both work (no array/JSON-operator columns; JSON is stored as TEXT / `MEDIUM_TEXT_VARIANT`). Prod MariaDB mixes collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list (integer IN-lists on `users.id`, `roles.id`, `permission_groups.id` are fine).
- Znuny SysConfig/YAML checkbox values are Perl-truthy strings ('0' is false): always read them with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- Long-running endpoints use the wave-0 job system (prod nginx 90 s timeout). **Every phase-1 provider is a bounded `COUNT` / `LIMIT` query**, so the batched data endpoint stays synchronous with a 5 s per-provider budget (single-widget fetch 25 s); a provider that cannot meet that (report widgets, phase 2) must go through `run_report_ref` / jobs instead.
- Timestamps written into new `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- After any API model/route change: regenerate both openapi files and schema.d.ts with `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build` per skill `tiqora-openapi-regen-or-ci-wipes-schema`; check `head -c 40 packages/api-client/openapi.json` starts `{"components"` and the diff stat is tens/hundreds of lines; never hand-edit schema.d.ts. A Pydantic response field WITH a default becomes REQUIRED in the generated TS type — fixtures, demo mocks and `frontend/e2e/fixtures/mock-api.ts` must carry it in the same commit (`cd frontend && pnpm lint` finds them). Existing schema name `DashboardSummary` (ticket KPI counts, `api/v1/tickets.py`) stays; new models avoid that name.
- Frontend: `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; never run prettier/biome on frontend/. New i18n keys in en.json + de.json, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). Frontend tasks that add i18n keys run sequentially, never in parallel (wave-1 ruling R-I18NSEQ). New screens get demo-mode mocks (`VITE_DEMO=1`) and are checked in demo mode with Playwright at 1280 px and 390 px, light + dark (delete `.playwright-mcp` before committing). Tailwind colour tokens only via `themeColor()` / the existing tokens — **this plan adds no new colour token**. API wrappers are hand-written `frontend/src/lib/*Api.ts` using `api.request<T>(method, path, {query, body, signal})` with `Schemas["…"]` types.
- UI copy German first, plain words ("Anpassen", "Teilen", "Widget hinzufügen", "Entwurf gesichert", "Verwerfen", "Speichern", "Übernehmen"); no system jargon in UI text (never "Provider", "Layout-JSON", "Breakpoint", "type_key").
- Znuny reference source (6.5 core) at `<repo>/znuny-6.5.22/` (read-only) for behaviour checks; Znuny 7.3 golden peer image exists for golden tests.
- Dependencies on the other wave-2 plans are stated per task as "needs reporting-builder Task N committed" / "needs plugin-system Task N committed". Tasks 1–9 do not need either plan and come first.

## Review Focus

1. **A shared or locked dashboard viewed by someone without rights to a queue** — the owner built a ticket list / counter / escalation widget over queue Q, the viewer has no `ro` on Q → the viewer sees 0 rows / count 0, never the owner's numbers and never ticket titles; this holds for batch, single and preview data and also after a role template is applied. Pinned in Task 6 (`test_shared_dashboard_data_runs_in_viewer_scope`, `test_escalations_hide_foreign_queue_tickets`) and Task 5 (`test_batch_data_uses_viewer_not_owner`).
2. **A widget type that no longer exists** (plugin disabled/removed, key renamed, `type_version` newer than the code) — the dashboard still loads; the server keeps the stored row on every save; the grid shows a "Widget nicht verfügbar" tile in the same slot with a "Entfernen" action in edit mode; nothing is dropped silently on "Speichern". Pinned in Task 4 (`test_save_keeps_unknown_widget_types`) and Task 10 (`WidgetUnavailable` test) and Task 11.
3. **Two tabs (or owner and admin) edit the same dashboard** — the second "Speichern" gets 409 with a German "inzwischen geändert" message, nothing is overwritten, the edit session and the local draft survive so the user can reload and re-apply. Pinned in Task 3 (`test_save_with_stale_version_is_409`) and Task 12 (`conflict keeps the draft`).
4. **Hostile or broken stored/sent layout and settings** — `x + w > 12`, overlapping rectangles, `w = 0`, a 5 000-widget body, a link `javascript:alert(1)`, a 1 MB note, markdown with `<script>` — clamped or answered 422 with a German message and a field path; never stored, never rendered as HTML/links. Pinned in Task 2 (`test_layout_is_clamped_and_overlaps_resolved`, `test_link_url_scheme_whitelist`, `test_widget_count_and_settings_size_limits`) and Task 10 (`notes render sanitised markdown`).
5. **One provider is slow or throws** — the batch still returns every other widget within the budget, the slow one comes back `pending` and is fetched alone, an exception becomes an error tile with "Erneut versuchen"; no 500 for the page. Pinned in Task 5 (`test_slow_provider_is_pending_others_ok`, `test_provider_exception_is_error_status`) and Task 11 (`slow provider does not block the page: error tile for one widget, 'Erneut versuchen' refetches only that one`).

Also pinned (not top five): double first-visit (two tabs at once) creates exactly one "Mein Start" (Task 3, unique `(owner_user_id, source_key)`); a saved search deleted or invalid behind a ticket-list widget → error tile "Die Suche gibt es nicht mehr — Suche wählen", no 500 (Task 17); a user with no queue rights at all gets 0 / empty states, not an error (Task 6); a locked template cannot be edited or deleted by its viewers (Task 4); a role template whose role was deleted still resolves to nobody, not everybody (Task 3).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Names from earlier waves are used exactly as they exist in code: `Registry`, `ExtensionPoint`, `get_registry`, `register_core` (`tiqora.extensions`); `TiqoraBase` (`tiqora.db.tiqora.base`); `MEDIUM_TEXT_VARIANT` (grep `backend/src/tiqora/db/tiqora/models.py`); `CurrentUser`, `DbSession` (`tiqora.api.deps`), `AdminUser` (`tiqora.api.v1.admin.deps`); `PermissionEngine.is_admin`, `groups_for_permission`; `TicketService.list_tickets` / `count_dashboard_summary` (`tiqora.domain.ticket_service`); `PaginatedTickets`, `TicketListItem`, `UtcDateTime` (`tiqora.domain.schemas`); `utc_now` (`tiqora.jobs.service`); `CalendarService.list_occurrences`, `OccurrenceOut`; `RoleUser`, `Roles` (`tiqora.db.legacy.user`); `createExtensionPoint` (`frontend/src/extensions/registry.ts`); `SchemaForm` (`frontend/src/components/fields/SchemaForm.tsx`); `MarkdownView` (`frontend/src/components/kb/MarkdownView.tsx`); `getLastViews` (`frontend/src/lib/lastViews.ts`); `handleSSEMessage` (`frontend/src/lib/useSSE.ts`); `ADMIN_PAGES` (`frontend/src/lib/adminSearch.ts`).

From the **plugin-system plan** (P13, `docs/superpowers/plans/2026-10-10-wave2-plugin-system.md`; file `frontend/src/plugins/dashboardWidgets.ts`, created by plugin Task 13): `WidgetCategory`, `WidgetMode`, `WidgetSize`, `WidgetProps`, `WidgetDefinition`, `DASHBOARD_WIDGETS`, `useDashboardWidgetDefinitions`, `registerPluginWidgets`. **This plan consumes P13 unchanged** (no field is added to those types).

From the **reporting-builder plan** (`docs/superpowers/plans/2026-10-10-wave2-reporting-builder.md`): `POST /api/v1/tickets/query` and `TicketService.page_from_stmt` (Task 5), saved-search store `tiqora.saved_searches.store.get_visible` and `savedSearchesApi` (Task 6 / Task 10), `FilterBuilder` + `fieldsFromTicketCatalog` (Tasks 11), `tiqora.permissions.sharing.share_groups_for` / `check_share_groups` and `GET /api/v1/reference/share-groups` (Tasks 2–3), `run_report_ref` + `RunOverrides` (Task 4; used only in phase 2).

### D1. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_dashboards.py` | five tables of D2 |
| modify `backend/src/tiqora/db/tiqora/models.py` | `TiqoraDashboard`, `TiqoraDashboardRole`, `TiqoraDashboardGroup`, `TiqoraDashboardWidget`, `TiqoraDashboardCounterSample` |
| `backend/src/tiqora/dashboard/__init__.py` | docstring only |
| `backend/src/tiqora/dashboard/layout.py` | pure: `GRID_COLS`, `WidgetRect`, `normalize_rects` |
| `backend/src/tiqora/dashboard/settings_models.py` | core widget settings models, `validate_widget_settings`, `safe_link_url`, `WIDGET_KEY_RE` |
| `backend/src/tiqora/dashboard/schemas.py` | API models (D3) |
| `backend/src/tiqora/dashboard/defaults.py` | built-in default template (D5) |
| `backend/src/tiqora/dashboard/store.py` | CRUD, visibility, optimistic lock, copy, share |
| `backend/src/tiqora/dashboard/start.py` | `ensure_start`, role resolution |
| `backend/src/tiqora/dashboard/providers/__init__.py` | `DashboardProvider`, `DASHBOARD_PROVIDERS`, `ProviderContext`, `core_providers()` |
| `backend/src/tiqora/dashboard/providers/presets.py` | `TICKET_PRESETS`, `preset_kwargs` |
| `backend/src/tiqora/dashboard/providers/{counter,ticket_list,escalations,reminders,calendar_agenda,team_presence}.py` | the six data providers |
| `backend/src/tiqora/dashboard/samples.py` | counter samples (delta, sparkline) |
| `backend/src/tiqora/dashboard/runner.py` | `run_widget`, `run_batch`, result statuses |
| `backend/src/tiqora/api/v1/dashboards.py` | `/dashboards*`, `/dashboard-widget-types*` |
| `backend/src/tiqora/api/v1/admin/dashboard_templates.py` | `/admin/dashboard-templates*` |
| modify `backend/src/tiqora/extensions/core.py` | register the providers |
| modify `backend/src/tiqora/api/v1/__init__.py`, `backend/src/tiqora/api/v1/admin/__init__.py` | router registration |
| modify `backend/src/tiqora/api/v1/agents.py` | extract public `list_online_agents_for(redis, session)` used by route and provider |

Frontend (new unless marked):

| Path | Responsibility |
|---|---|
| `frontend/src/lib/dashboardsApi.ts` | typed API wrapper, `Schemas["…"]` aliases |
| `frontend/src/dashboard/model.ts` | `EditWidget`, `Rect`, constants (`ROW_H = 64`, `GAP = 16`) |
| `frontend/src/dashboard/layout/grid.ts` | pure grid maths (D8) |
| `frontend/src/dashboard/layout/breakpoints.ts` | `layoutForBreakpoint`, `smOrder`, `breakpointForWidth` |
| `frontend/src/dashboard/layout/session.ts` | `editReducer`, undo/redo |
| `frontend/src/dashboard/layout/keyboard.ts` | key → action mapping |
| `frontend/src/dashboard/definitions.ts` | `defineWidget` adapter, `registerCoreWidgets`, `settingsWithDefaults`, `galleryGroupOf` |
| `frontend/src/dashboard/data.tsx` | `WidgetDataProvider`, `useWidgetData`, `useDashboardData`, `usePreviewData` |
| `frontend/src/dashboard/chrome/*.tsx` | `WidgetFrame`, `WidgetSkeleton`, `WidgetError`, `WidgetUnavailable`, `Sparkline` |
| `frontend/src/dashboard/widgets/*.tsx` | the ten phase-1 widgets |
| `frontend/src/dashboard/DashboardGrid.tsx` | view + edit rendering of the grid |
| `frontend/src/dashboard/edit/*` | `EditToolbar`, `useDragResize`, `draft.ts`, `PhoneControls` |
| `frontend/src/dashboard/gallery/GalleryPanel.tsx` | side panel |
| `frontend/src/dashboard/settings/*` | `SettingsDrawer`, editors registry, `TicketListSettings`, `LinksSettings`, `NotesSettings` |
| `frontend/src/dashboard/tabs/*` | `DashboardTabs`, `NewDashboardDialog`, `TabMenu`, `ShareDialog` |
| modify `frontend/src/routes/agent/DashboardPage.tsx` | host page (rewrite); old exports move to `frontend/src/dashboard/legacy.ts` |
| `frontend/src/routes/admin/DashboardTemplatesPage.tsx` | admin role templates |
| modify `frontend/src/lib/useSSE.ts` | `subscribeSSE` bus |
| modify `frontend/src/router.tsx`, `frontend/src/lib/adminSearch.ts`, `frontend/src/demo/mockData.ts` (+ new `frontend/src/demo/dashboardsMock.ts`), `frontend/e2e/fixtures/mock-api.ts` | wiring |

### D2. Tables (one migration, Task 1)

```
tiqora_dashboard
  id                BIGINT PK autoincrement
  owner_user_id     INT NULL                       -- Znuny users.id (no FK); NULL for templates
  name              VARCHAR(120) NOT NULL
  icon              VARCHAR(40) NULL
  sort              INT NOT NULL default 0         -- tab order among the owner's dashboards; templates: priority (low wins)
  kind              VARCHAR(10) NOT NULL default 'personal'   -- personal | template | shared
  locked            BOOL NOT NULL default false    -- templates only: viewers cannot change it
  source_key        VARCHAR(60) NULL               -- 'core.default' | 'tpl:<template id>' (idempotent first-visit copy)
  source_template_id       BIGINT NULL             -- no FK: the template may be deleted later
  source_template_version  INT NULL                -- template.version when copied (phase 2 merge)
  filters           TEXT NOT NULL default '{}'     -- dashboard-wide filters; reserved, always '{}' in phase 1
  version           INT NOT NULL default 1         -- optimistic lock, +1 per save
  created_at        DATETIME NOT NULL
  changed_at        DATETIME NOT NULL
  INDEX ix_tiqora_dashboard_owner (owner_user_id, kind, sort)
  UNIQUE uq_tiqora_dashboard_source (owner_user_id, source_key)    -- NULLs never collide

tiqora_dashboard_role                              -- roles a template applies to
  dashboard_id BIGINT NOT NULL FK fk_tiqora_dashboard_role_dashboard -> tiqora_dashboard.id ON DELETE CASCADE
  role_id      INT NOT NULL                        -- Znuny roles.id (no FK)
  PRIMARY KEY (dashboard_id, role_id)   INDEX ix_tiqora_dashboard_role_role (role_id)

tiqora_dashboard_group                             -- groups a 'shared' dashboard is shared with
  dashboard_id BIGINT NOT NULL FK fk_tiqora_dashboard_group_dashboard -> tiqora_dashboard.id ON DELETE CASCADE
  group_id     INT NOT NULL                        -- Znuny permission_groups.id (no FK)
  PRIMARY KEY (dashboard_id, group_id)  INDEX ix_tiqora_dashboard_group_group (group_id)

tiqora_dashboard_widget
  id             VARCHAR(36) PK                    -- client-generated (crypto.randomUUID); = WidgetProps.instanceId
  dashboard_id   BIGINT NOT NULL FK fk_tiqora_dashboard_widget_dashboard -> tiqora_dashboard.id ON DELETE CASCADE
  type_key       VARCHAR(120) NOT NULL
  type_version   INT NOT NULL default 1
  title_override VARCHAR(200) NULL
  settings       TEXT NOT NULL default '{}'        -- JSON, <= 20 000 bytes
  layout         TEXT NOT NULL                     -- {"lg":{x,y,w,h},"md":{x,y,w,h}?}
  position       INT NOT NULL default 0            -- = phone order (array index of the widget list)
  INDEX ix_tiqora_dashboard_widget_dashboard (dashboard_id, position)

tiqora_dashboard_counter_sample                    -- one row per user, counter and UTC day: the day's latest value
  user_id     INT NOT NULL
  counter_key VARCHAR(120) NOT NULL                -- 'preset:<name>' (phase 1); 'search:<id>' / 'filter:<sha1[:16]>' (Task 17)
  sample_day  DATE NOT NULL
  value       INT NOT NULL
  sampled_at  DATETIME NOT NULL
  PRIMARY KEY (user_id, counter_key, sample_day)   INDEX ix_tiqora_dashboard_sample_day (sample_day)
```

Booleans `server_default=false()`; FK columns of junction tables `autoincrement=False`. No row is seeded by the migration (the built-in default template is code, D5). `tiqora_dashboard_kiosk_token` (spec) is **not** created here — wallboard/kiosk is phase 2.

### D3. API models and routes (Tasks 2, 4, 5, 7)

```python
# backend/src/tiqora/dashboard/schemas.py
WIDGET_KEY_RE: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{1,118}$")
WIDGET_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{8,36}$")
MAX_WIDGETS_PER_DASHBOARD: Final = 40
MAX_WIDGET_SETTINGS_BYTES: Final = 20_000
MAX_DASHBOARDS_PER_USER: Final = 20

class Rect(BaseModel):                                # extra="forbid"
    x: int = Field(ge=0, le=11); y: int = Field(ge=0, le=999)
    w: int = Field(ge=1, le=12); h: int = Field(ge=1, le=24)
class WidgetLayouts(BaseModel):                       # extra="forbid"
    lg: Rect                                          # 12 columns
    md: Rect | None = None                            # 8 columns; None = derived on the client
class WidgetIn(BaseModel):                            # extra="forbid"
    id: str = Field(pattern=WIDGET_ID_RE.pattern)
    type_key: str = Field(pattern=WIDGET_KEY_RE.pattern)
    type_version: int = Field(1, ge=1, le=1000)
    title_override: str | None = Field(None, max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict)
    layout: WidgetLayouts
class WidgetOut(WidgetIn): ...                        # same fields, settings as stored
class DashboardTabOut(BaseModel):
    id: int; name: str; icon: str | None; kind: Literal["personal", "template", "shared"]
    locked: bool; is_owner: bool; can_edit: bool; can_share: bool
    owner_name: str | None                            # set when not is_owner
    version: int; sort: int; group_ids: list[int]; source_template_id: int | None
class DashboardOut(DashboardTabOut):
    widgets: list[WidgetOut]                          # in phone order
class DashboardSaveIn(BaseModel):                     # extra="forbid"
    name: str = Field(min_length=1, max_length=120)   # stripped; blank -> 422
    icon: str | None = Field(None, max_length=40)
    version: int = Field(ge=1)
    widgets: list[WidgetIn] = Field(max_length=MAX_WIDGETS_PER_DASHBOARD)
class DashboardCreateIn(BaseModel):                   # extra="forbid"
    name: str = Field(min_length=1, max_length=120)
    icon: str | None = Field(None, max_length=40)
    copy_from: int | None = None                      # duplicate a visible dashboard (widgets get new ids)
class DashboardOrderIn(BaseModel):  ids: list[int] = Field(max_length=MAX_DASHBOARDS_PER_USER)
class WidgetSettingsPatchIn(BaseModel):  settings: dict[str, Any]; version: int = Field(ge=1)
class DashboardShareIn(BaseModel):  group_ids: list[int] = Field(max_length=50)       # Task 16
class WidgetTypeOut(BaseModel):  key: str; version: int; has_data: bool              # has_data = a provider is registered
class WidgetDataOut(BaseModel):
    status: Literal["ok", "pending", "error", "forbidden"]
    data: dict[str, Any] | None = None
    error: str | None = None                          # German, user-presentable
class DashboardDataOut(BaseModel):
    widgets: dict[str, WidgetDataOut]                 # widget id -> result; widgets without a provider are absent
    generated_at: UtcDateTime
class WidgetPreviewIn(BaseModel):  settings: dict[str, Any] = Field(default_factory=dict)
class TemplateIn(BaseModel):                          # admin; extra="forbid"
    name: str = Field(min_length=1, max_length=120); icon: str | None = None
    role_ids: list[int] = Field(max_length=50); locked: bool = False; sort: int = Field(0, ge=0, le=1000)
class TemplateOut(DashboardTabOut):  role_ids: list[int]; widget_count: int
```

Routes (`api/v1/dashboards.py`, `router = APIRouter(tags=["dashboards"])`, `CurrentUser`; static paths registered before `/{dashboard_id}`):

```
GET    /api/v1/dashboards                              -> list[DashboardTabOut]
POST   /api/v1/dashboards/ensure-start                 -> DashboardOut   (201 first visit, 200 afterwards)
PUT    /api/v1/dashboards/order                        -> 204
POST   /api/v1/dashboards                              -> 201 DashboardOut
GET    /api/v1/dashboards/{dashboard_id}               -> DashboardOut                 (404 not visible)
PUT    /api/v1/dashboards/{dashboard_id}               -> DashboardOut                 (full save; 409 stale; 403 read-only; 422)
PATCH  /api/v1/dashboards/{dashboard_id}/widgets/{widget_id}/settings -> WidgetOut     (notes edit in view mode; 409 stale)
DELETE /api/v1/dashboards/{dashboard_id}               -> 204
PUT    /api/v1/dashboards/{dashboard_id}/share         -> DashboardOut                 (Task 16)
GET    /api/v1/dashboards/{dashboard_id}/data?widgets=a,b   -> DashboardDataOut        (no `widgets` = all)
GET    /api/v1/dashboards/{dashboard_id}/widgets/{widget_id}/data -> WidgetDataOut     (25 s budget)
GET    /api/v1/dashboard-widget-types                  -> list[WidgetTypeOut]
POST   /api/v1/dashboard-widget-types/{key}/data       -> WidgetDataOut                (preview: body WidgetPreviewIn; 404 unknown key)
GET    /api/v1/admin/dashboard-templates               -> list[TemplateOut]
POST   /api/v1/admin/dashboard-templates               -> 201 TemplateOut              (body TemplateIn; starts with the built-in default widgets)
PUT    /api/v1/admin/dashboard-templates/{id}          -> TemplateOut                  (properties only; layout is saved through PUT /dashboards/{id})
DELETE /api/v1/admin/dashboard-templates/{id}          -> 204
```

Error shape: validation failures `422 {"detail": {"message": <German>, "path": "widgets[3].settings.limit"}}` (the existing `ApiError.detail` body shape, skill `apierror-detail-is-body-not-string`); stale version `409 {"detail": {"message": "Dieses Dashboard wurde inzwischen geändert.", "current_version": n}}`; read-only `403` with a German message; invisible `404`.

Export/import of dashboards as JSON (spec "API surface") is **not** in phase 1 (Deviations).

### D4. Visibility, rights, first visit (Task 3)

- **Visible to user U**: `owner_user_id == U` (personal/shared); `kind == 'shared'` with a `tiqora_dashboard_group` row in `groups_for_permission(U, "ro")`; `kind == 'template'` AND `locked` AND a `tiqora_dashboard_role.role_id` in U's valid roles (`RoleUser` joined with `Roles.valid_id == 1`, loaded as an int list). An unlocked template is **not** a tab — it is copied once at first visit (below). Admin (`PermissionEngine.is_admin`) additionally sees every template by id (`GET /dashboards/{id}`, admin API) but never lists others' personal dashboards.
- **Edit rights**: personal/shared → owner only (admin does not edit foreign personal dashboards); template → admin only (a locked template is read-only for every non-admin; an unlocked template is invisible to non-admins). `can_share` = owner of a personal/shared dashboard.
- **Tab order** in `GET /dashboards`: own (`sort`, `id`), then shared-with-me (name), then locked templates (`sort`, `id`).
- **`ensure_start(session, user_id) -> tuple[DashboardOut, bool]`**: if the user owns any personal/shared dashboard → return the first by `(sort, id)`, `created=False`. Else pick the source: unlocked templates whose roles intersect the user's roles, ordered by `(sort, id)`, first wins → `source_key = f"tpl:{id}"`, widgets copied with new ids, `source_template_id/version` set; none → built-in default (`source_key = "core.default"`). The new dashboard is named `Mein Start`, `kind='personal'`. A concurrent second call hits `uq_tiqora_dashboard_source` → roll back, re-read, return the existing one with `created=False`.
- **Limits**: `MAX_DASHBOARDS_PER_USER = 20` own dashboards (create/copy beyond → 422 "Es sind höchstens 20 Dashboards möglich.").
- **Save (`PUT`)**: `SELECT … version`; `body.version != row.version` → `DashboardConflict(row.version)`; widgets are replaced wholesale inside the transaction (delete rows not in the body, update/insert the rest; `position` = list index); `UPDATE tiqora_dashboard SET version = version + 1 … WHERE id = :id AND version = :v`, `rowcount == 0` → conflict. A widget id that exists in **another** dashboard → 422 "Diese Widget-ID ist schon vergeben." Unknown `type_key`s are kept (D6 rule 3).

### D5. Built-in default template (Task 2) — reproduces today's home page

`defaults.py`: `DEFAULT_TEMPLATE_KEY = "core.default"`, `DEFAULT_TEMPLATE_VERSION = 1`, `def default_widgets() -> list[WidgetIn]` (fresh uuid4 ids per call). Today's page → widgets (lg 12 columns, one row = 64 px):

| Today | Widget (`type_key`) | lg `x,y,w,h` | settings |
|---|---|---|---|
| chip "Meine offenen" | `core.counter` | 0,0,3,2 | `{"source":{"kind":"preset","preset":"mine_open"}}` |
| chip "Meine neuen" | `core.counter` | 3,0,3,2 | `…"mine_new"` |
| chip "Zum Übernehmen" | `core.counter` | 6,0,3,2 | `…"unowned_new"` |
| KPI "Eskaliert" + escalation strip | `core.counter` | 9,0,3,2 | `…"escalated"`, `"tone":"danger"` |
| escalated group | `core.escalations` | 0,2,12,3 | `{"limit":8}` |
| group "Meine neuen" | `core.ticket-list` | 0,5,6,5 | preset `mine_new`, `sort:"age"`, `order:"asc"`, `limit:8` |
| group "Meine offenen" | `core.ticket-list` | 6,5,6,5 | preset `mine_open`, `sort:"age"`, `order:"desc"`, `limit:8` |
| group "Team-Eingang (zum Übernehmen)" | `core.ticket-list` | 0,10,6,5 | preset `unowned_new`, `sort:"age"`, `order:"asc"`, `limit:8`, title override "Team-Eingang (zum Übernehmen)" |
| "Erinnerungen fällig" | `core.reminders` | 6,10,6,5 | `{"window_hours":24,"limit":6}` |
| "Zuletzt angesehen" | `core.recent-views` | 0,15,12,2 | `{"limit":8}` |
| "Queues" shortcuts | `core.queue-overview` | 0,17,12,3 | `{"limit":8,"only_with_work":true}` |

The greeting ("Guten Morgen, Anna" + date) stays above the grid in `DashboardPage` (page header, not a widget). Calendar, team presence, notes and links are **not** in the default (today's page has none) — they are in the gallery. Phone order = this table's order.

### D6. Providers, runner, batched endpoint (Tasks 5–6)

```python
# backend/src/tiqora/dashboard/providers/__init__.py
@dataclass(frozen=True, slots=True)
class ProviderContext:
    user_id: int
    session: AsyncSession             # OWN session per provider run (never the request's; AsyncSession is not concurrency-safe)
    now: datetime                     # naive UTC
    filters: Mapping[str, Any]        # dashboard-wide filters; {} in phase 1 (phase 2: RunOverrides.common)
    redis: Redis | None
class ProviderForbidden(Exception):             # -> status "forbidden"; `message` is shown in the tile
    def __init__(self, message: str = "Dafür fehlt Ihnen die Berechtigung.") -> None
@dataclass(frozen=True, slots=True)
class DashboardProvider:
    key: str                                    # == WidgetDefinition.dataEndpoint == widget type_key for core widgets
    version: int                                # == type_version of the widget it serves
    settings_model: type[BaseModel]             # validated copy of the stored settings (defaults applied)
    run: Callable[[ProviderContext, Any], Awaitable[dict[str, Any]]]
    timeout_seconds: float = 5.0                # batch budget
DASHBOARD_PROVIDERS: Final = ExtensionPoint[DashboardProvider](
    "core.dashboard.providers", lambda p: p.key, "Datenquellen für Dashboard-Widgets")
def core_providers() -> list[DashboardProvider]
```

Rules:
1. A provider receives **only the viewer's id** — never the dashboard owner's. Shared dashboards, locked templates and previews therefore run in the viewer's scope.
2. `run_batch(factory, registry, user_id, widgets, *, timeout=5.0, redis=None, now=None) -> dict[str, WidgetDataOut]`: one `asyncio.gather` over widgets that have a provider; each task `async with factory() as session`, `asyncio.wait_for(run, provider.timeout_seconds)` under `tiqora.reporting.sql.statement_timeout(session, ceil(timeout)+1)` (verify the helper's signature); `TimeoutError` → `{"status":"pending"}`; `ProviderForbidden` → `forbidden` with `error = exc.message`; `ValidationError` of the stored settings → `error` "Die Einstellungen dieses Widgets sind ungültig."; any other exception → logged (`dashboard_provider_failed`, key, no payload) and `error` "Diese Daten konnten nicht geladen werden." Widgets whose key has no provider (frontend-only: recent-views, notes, links, plugin widgets) are absent from the result. `run_widget(...)` is the same for one widget with `timeout=25.0`.
3. **Unknown or newer `type_key`s are stored and returned unchanged** and get no data entry; `type_version` greater than the provider's → `error` "Dieses Widget braucht eine neuere Version." (never silently migrated server-side; the client `migrate` handles older versions).
4. Output shapes (the frontend types in Task 9 mirror these; all are plain `dict`):
   - `core.counter` → `{"value": int, "delta": int | None, "spark": list[int]}` (`spark` = last ≤ 14 daily values, oldest first, ending today; `delta` = today − the newest earlier day within 7 days, else `None`).
   - `core.ticket-list` → `{"items": list[TicketListItem-dump], "total": int}`.
   - `core.escalations` → `{"items": [{"ticket": TicketListItem-dump, "epoch": int}], "total": int, "oldest_epoch": int | None}` (`epoch` = smallest set `escalation_*` epoch, past or future; oldest first).
   - `core.reminders` → `{"items": [{"ticket": …, "until": int, "overdue": bool}], "total": int}`.
   - `core.calendar-agenda` → `{"items": [OccurrenceOut-dump]}` (today in the viewer's display zone, or `days` days).
   - `core.team-presence` → `{"online": [OnlineAgentOut-dump], "total_online": int}`.
5. `TICKET_PRESETS` (`providers/presets.py`) map a preset name to `TicketService.list_tickets` keyword arguments for viewer `uid` — the **same arguments** `count_dashboard_summary` and today's `DashboardPage` use, so counters and lists agree with the numbers on the old home page:

   | preset | kwargs |
   |---|---|
   | `mine_open` | `state_type="open", owner_id=uid` |
   | `mine_new` | `state_type="new", owner_id=uid` |
   | `unowned_new` | `state_type="new", owner_id=1` |
   | `escalated` | `state_type="open", escalating_within=0` (SLA breach only — matches the summary's `escalated`, NOT the list filter `escalated=True`, which also includes the AI handoff) |
   | `ai_escalated` | `state_type="open", ai_escalated=True` |

### D7. Counter samples (Task 5)

`samples.py`: `async def record_and_history(session, *, user_id, counter_key, value, today: date, keep_days: int = 35) -> tuple[int | None, list[int]]`. Upserts today's row (select → update/insert; `IntegrityError` on the insert race → update), deletes rows of that user older than `keep_days` only when it inserted a new day, then reads the last 14 days. `delta` = `value − (newest row with sample_day < today and ≥ today − 7)`, else `None`; `spark` = values of the existing days oldest→newest with today's value last (a gap day is simply absent — the line connects neighbours). History therefore starts when a user first opens a dashboard; the UI shows no delta/sparkline until there are ≥ 2 days (honest, no invented history). The daily-snapshot table has no owner dimension, so it cannot serve "meine offenen" (record in Deviations).

### D8. Frontend engine API (Task 8, pure)

```ts
// frontend/src/dashboard/model.ts
export const ROW_H = 64, GAP = 16;
export const COLS = { lg: 12, md: 8 } as const;
export type Breakpoint = "lg" | "md" | "sm";
export interface Rect { x: number; y: number; w: number; h: number }
export interface LayoutItem { id: string; rect: Rect; min: [number, number]; max: [number, number] }
export interface EditWidget {
  id: string; type_key: string; type_version: number; title_override: string | null;
  settings: Record<string, unknown>; layout: { lg: Rect; md?: Rect };
}
// frontend/src/dashboard/layout/grid.ts
export function clampRect(r: Rect, cols: number, min: [number, number], max: [number, number]): Rect;
   // w in [min.w, min(max.w, cols)], h in [min.h, max.h], x in [0, cols - w], y >= 0
export function collides(a: Rect, b: Rect): boolean;                       // strict overlap; touching edges do not collide
export function resolveCollisions(items: LayoutItem[], fixedId: string, cols: number): LayoutItem[];
   // `fixedId` keeps its rect; any item overlapping a fixed/pushed item is pushed down below it; cascades; deterministic (sort by y, x, id)
export function compactUp(items: LayoutItem[], cols: number, fixedId?: string): LayoutItem[];
   // vertical gravity in (y, x, id) order: each item moves up while free; `fixedId` is not moved
export function moveItem(items: LayoutItem[], id: string, to: { x: number; y: number }, cols: number): LayoutItem[];
export function resizeItem(items: LayoutItem[], id: string, size: { w: number; h: number }, cols: number): LayoutItem[];
export function firstFreeSpot(items: LayoutItem[], w: number, h: number, cols: number): { x: number; y: number };
   // scans y = 0.., x = 0..cols-w; always terminates (appends below the lowest item)
export function pixelsToCell(p: { x: number; y: number }, grid: { width: number; cols: number }): { x: number; y: number };
   // cellW = (width - GAP*(cols-1)) / cols; x = round(p.x / (cellW + GAP)), y = round(p.y / (ROW_H + GAP)), clamped >= 0
// frontend/src/dashboard/layout/breakpoints.ts
export function breakpointForWidth(containerWidth: number): Breakpoint;    // >= 900 lg, >= 520 md, else sm
export function layoutForBreakpoint(widgets: EditWidget[], bp: "lg" | "md", mins: (key: string) => [number, number]): Map<string, Rect>;
   // lg: stored lg (clamped to 12). md: stored md when present, else lg scaled x' = round(x*8/12), w' = max(1, round(w*8/12)),
   // then resolveCollisions + compactUp in (y, x) order so no two md rects overlap
export function smOrder(widgets: EditWidget[]): string[];                   // array order (= phone order)
// frontend/src/dashboard/layout/session.ts
export interface EditState {
  name: string; widgets: EditWidget[]; selectedId: string | null;
  past: Snapshot[]; future: Snapshot[]; lastCoalesce: { key: string; at: number } | null;
  baseVersion: number; dirty: boolean;
}
export type EditAction =
  | { type: "reset"; name: string; widgets: EditWidget[]; version: number }
  | { type: "add"; def: WidgetLike; id: string; at?: { x: number; y: number }; bp: "lg" | "md" }
  | { type: "move"; id: string; to: { x: number; y: number }; bp: "lg" | "md"; now?: number; coalesce?: boolean }
  | { type: "resize"; id: string; size: { w: number; h: number }; bp: "lg" | "md"; now?: number; coalesce?: boolean }
  | { type: "remove"; id: string } | { type: "duplicate"; id: string; newId: string }
  | { type: "settings"; id: string; settings: Record<string, unknown>; titleOverride: string | null }
  | { type: "rename"; name: string } | { type: "shift"; id: string; dir: "up" | "down" }   // phone order
  | { type: "select"; id: string | null } | { type: "undo" } | { type: "redo" };
export function editReducer(s: EditState, a: EditAction): EditState;       // pure; undo stack capped at 100; `select` never creates history
```

`WidgetLike = { key: string; version: number; size: { default: [number, number]; min: [number, number]; max: [number, number] }; settingsDefaults: Record<string, unknown> }`. Editing at `md` writes `layout.md`; at `lg` writes `layout.lg` only (an explicit `md` of that widget stays). Phone has no rect editing.

### D9. Widget contract wiring (Task 10) — consumes P13

`frontend/src/dashboard/definitions.ts`:

```ts
// spec-shaped input -> P13 WidgetDefinition (no new fields on the shared type)
export function defineWidget<S extends Record<string, unknown>>(d: {
  key: string; version: number;
  title: string;                         // i18n key            -> WidgetDefinition.titleKey
  description?: string;                  // i18n key            -> descriptionKey
  category: WidgetCategory; icon: string; size: WidgetSize;
  settings: Record<string, unknown> | null;     // JSON schema -> settingsSchema
  data: { endpoint: string } | null;            //             -> dataEndpoint (endpoint string)
  refreshOn: string[];                   // SSE types ("ticket_changed") and "interval:<seconds>"
  component: ComponentType<WidgetProps<S>>;
  permission?: string | null; migrate?: (old: unknown, fromVersion: number) => S;
}): WidgetDefinition<S>;                 // owner: "core", ns undefined
export function registerCoreWidgets(): void;                 // idempotent; DASHBOARD_WIDGETS.register(def, "core")
export function settingsWithDefaults(def: WidgetDefinition, settings: unknown, storedVersion: number): Record<string, unknown>;
   // runs def.migrate when storedVersion < def.version, then fills schema defaults; unknown/invalid -> defaults
export function galleryGroupOf(def: WidgetDefinition): "tickets" | "me" | "team";
   // category "tickets" -> tickets; "team" -> team; everything else -> me ("Für mich"); core.recent-views, reminders, calendar-agenda are category "other"
```

Data reaches components through a context — `WidgetProps` stays untouched: `useWidgetData<T>(): { status: "loading" | "ok" | "pending" | "error" | "forbidden" | "none"; data: T | undefined; error?: string; refetch: () => void }` (provider: `WidgetDataProvider` wrapped around each widget by the host; in the gallery/settings preview it is fed by `POST /dashboard-widget-types/{key}/data`). Bespoke settings editors for core widgets are looked up in `frontend/src/dashboard/settings/registry.ts` (`CORE_SETTINGS_EDITORS: Record<string, ComponentType<SettingsEditorProps>>`), **not** in the shared type; plugin widgets use `SchemaForm` with their `settingsSchema` (flat scalars and string arrays — the existing field engine).

Core widget registry (all `owner: "core"`, `permission: null` in phase 1):

| `key` | category | icon | size default / min / max | `dataEndpoint` | `refreshOn` |
|---|---|---|---|---|---|
| `core.ticket-list` | tickets | list | [6,5] / [4,3] / [12,10] | `core.ticket-list` | `ticket_changed`, `ticket_new_in_queue` |
| `core.counter` | tickets | counter | [3,2] / [2,2] / [4,3] | `core.counter` | `ticket_changed`, `ticket_new_in_queue` |
| `core.queue-overview` | tickets | queue | [12,3] / [4,2] / [12,6] | null (uses the cached `["queues"]` query) | `ticket_changed` |
| `core.escalations` | tickets | alert | [12,3] / [4,2] / [12,8] | `core.escalations` | `ticket_changed`, `interval:60` |
| `core.reminders` | other | clock | [6,5] / [3,3] / [12,8] | `core.reminders` | `ticket_changed`, `interval:300` |
| `core.recent-views` | other | history | [12,2] / [3,2] / [12,4] | null (`getLastViews()`) | — |
| `core.calendar-agenda` | other | calendar | [4,3] / [3,2] / [8,8] | `core.calendar-agenda` | `interval:300` |
| `core.team-presence` | team | users | [4,3] / [3,2] / [6,8] | `core.team-presence` | `interval:30` |
| `core.notes` | team | note | [4,3] / [3,2] / [12,10] | null (content is in `settings`) | — |
| `core.links` | team | link | [4,2] / [2,2] / [12,6] | null | — |

### D10. Visual and interaction contract (from the approved mockup)

- **View mode**: page header = greeting (`h1`) + tab row (`role="tablist"`, "Mein Start", team dashboards, circular "+" with `aria-label="Neues Dashboard"`) on the left, "Teilen" (outline) and "Anpassen" (primary) on the right. Grid: 12 columns, `gap 16px`, row height 64 px. Widget chrome: `rounded-[14px] border border-hairline bg-surface px-[18px] py-4`, title row (14 px / 600) with an optional right-hand link ("Alle 12 anzeigen", accent), no handles. KPI counter: uppercase 12 px muted label, delta text right ("+2 seit gestern", muted; for counters where fewer is better the negative delta is green `text-success`), value in the mono face (30 px / 600; `tone: "danger"` → `text-danger`), 96×30 sparkline (`polyline`, 2 px, round caps) to the right of the value.
- **Edit mode**: dark toolbar (inverse tokens `bg-ink text-surface`, sticky, `role="toolbar" aria-label="Bearbeiten"`): "{name} bearbeiten", "Entwurf gesichert · 10:42", spacer, undo, redo (icon buttons with `aria-label="Rückgängig"` / `"Wiederholen"`), "Verwerfen" (outline), "Speichern" (accent). Grid shows the 12 column guide (`repeating` background) inside a dashed `outline`; each widget shows a header with grab handle `⋮⋮`, title, buttons Einstellungen / Duplizieren / Entfernen (danger tint) and a corner resize handle (bottom-right, 12 × 12, accent borders). While dragging: the widget is lifted (`transform`, 2 px accent border, shadow, slight rotation, opacity .96) and the drop target is a dashed accent rectangle with `bg-accent/10`-style tint at the snapped cell. Under the grid the hint: "Tastatur: Widget fokussieren, Pfeiltasten verschieben, Umschalt + Pfeiltasten ändern die Größe, Eingabe öffnet die Einstellungen." Gallery side panel 340 px (`role="complementary"` `aria-label="Widget hinzufügen"`): title "Widget hinzufügen" + close "×", search field "Suchen" (placeholder "z. B. Eskalation, Notiz, Queue"), groups **Tickets / Für mich / Team**, each card = tinted 34 px glyph tile + name + one-line description, foot hint "Ins Raster ziehen oder anklicken – das Widget landet in der ersten freien Lücke."
- **Settings drawer** (right, 420 px, `role="dialog"`): small widget-type name above "Einstellungen", close ×; fields: "Titel"; "Welche Tickets?" (radio cards: "Gespeicherte Suche" / "Eigener Filter"), "Spalten" (checkbox chips: Ticketnummer, Titel, Kunde, Status, Fällig, Queue, Besitzer, Priorität), "Sortierung" select + "Anzahl Zeilen" number (1–50), "Darstellung" radio group "Kompakt" / "Bequem", "Vorschau" box with the live widget; footer "Abbrechen" / "Übernehmen".
- **Phone** (container < 520 px): one column in phone order; consecutive counters form a 2-column grid; in edit mode each widget header offers only "↑ Nach oben", "↓ Nach unten" and ⚙ Einstellungen — no gallery, drag, duplicate or remove (toolbar still has Verwerfen/Speichern).
- **States**: skeleton sized like the final content (`h * 64 px`), error tile with "Erneut versuchen" (refetches only that widget), empty states say what to do ("Noch keine Tickets in dieser Suche — Filter ändern", the action link only when the viewer can edit), forbidden tile "Dafür fehlt Ihnen die Berechtigung".
- **Accessibility**: tabs follow the WAI-ARIA tab pattern (arrow keys, `aria-selected`); every widget in edit mode is a focusable group (`tabIndex=0`, `aria-roledescription="verschiebbares Widget"`), arrow = move 1 cell, Shift+arrow = resize 1 cell, Enter = settings; every move/resize announces in an `aria-live="polite"` region ("„Meine offenen Tickets“ ist jetzt in Spalte 4, Zeile 3, 6 mal 5"); drop targets and handles are not the only way (the buttons and keyboard cover every action); respects `prefers-reduced-motion` (no lift rotation/transition); RTL: the grid mirrors via `direction`-aware `grid-column` (columns count from the inline start) — pointer maths uses `getBoundingClientRect` and flips x when `getComputedStyle(grid).direction === "rtl"`.

---

## Deviations from spec

1. **Widget contract names.** The spec's `defineWidget({ title, settings, data: { endpoint } })` and the committed plugin plan's `WidgetDefinition` (`titleKey`, `settingsSchema`, `dataEndpoint`, plus `owner`/`ns`) differ only in field names. P13 is the shared, already-planned type, so it is consumed **unchanged**; `defineWidget` (D9) accepts the spec's shape and maps it onto P13 (`title→titleKey`, `settings→settingsSchema`, `data.endpoint→dataEndpoint`, `owner: "core"`). Anything the spec's list has that P13 lacks (per-widget bespoke settings editors, data delivery) lives host-side (`CORE_SETTINGS_EDITORS`, `WidgetDataProvider`), so plugin and core widgets still share one type. **Controller: confirm that P13 stays as it is.**
2. **`defineWidget` data delivery.** `WidgetProps` has no `data` field; components read provider data through `useWidgetData()` (context) instead of props.
3. **Gallery previews.** The spec asks for "live previews on the user's real data" in the gallery; the approved mockup shows static cards (glyph, name, description). The mockup wins; the live preview lives in the settings drawer (`mode: "preview"`, `instanceId: "preview"`).
4. **Tables.** `template_role_ids[]` / `shared_group_ids[]` become the junction tables `tiqora_dashboard_role` / `_group` (arrays are not portable across MariaDB/PostgreSQL). `tiqora_dashboard_kiosk_token` is deferred to phase 2 with the wallboard. Added: `source_key` (+ unique index) for an idempotent first-visit copy, and `tiqora_dashboard_counter_sample` for KPI deltas/sparklines. Spec column `layout JSON … sm 1 col order` is stored as the row `position`.
5. **Filter chips are gone.** Today's chips ("Alle / Meine offenen / Meine neuen / Zum Übernehmen") and the single switchable list become the four counters plus separate list widgets (D5); a counter links to the matching queue view. The `localStorage` key `tiqora-dashboard-filter` is no longer read.
6. **Export/import JSON** (`/dashboards/{id}/export|import`) is not in phase 1 (not in the spec's phase-1 row); template copying and "Als eigenes kopieren" cover the cases.
7. **Drafts are per browser.** The autosaved edit draft lives in `localStorage` (convenience, keyed by user + dashboard + base version), not in a table; a draft never crosses devices.
8. **Team presence** shows who is online (Redis presence, `GET /agents/online` data); "am Telefon", "Pause", "Urlaub bis Mo" of the mockup have no data source today (the call store is per user) — later item. KPI `hint` lines of the mockup ("davon 3 warten auf Kunden") are not built in phase 1.
9. **Notes** are stored in the widget's `settings.markdown`; "personal or shared" follows the dashboard's sharing (a note on a shared dashboard is shared); only people who can edit the dashboard edit the note (inline, via `PATCH …/settings`).
10. **Wallboard / TV mode, kiosk tokens, rotation** are phase 2; the widget model already carries `mode: "wallboard"` (P13) and nothing else is added. The `Wallboard` mockup only reuses the same widget kinds (counter tiles, queue bars, team list) at larger type, which confirms the model scales to it; nothing wallboard-only leaks into phase 1.
11. **Plugin "Erweiterungen" section** on the home page (plugin Task 14, `PluginWidgetsSection`) is superseded: plugin widgets arrive through `DASHBOARD_WIDGETS` and are offered in the gallery. Task 11 removes the section from `DashboardPage` and deletes `PluginWidgetsSection` plus its test if plugin Task 14 has landed (if not, nothing to remove).

## Shared files (coordination with other wave-2 plans)

Migration (`versions_tiqora`, head bump), `db/tiqora/models.py`, `extensions/core.py` (providers), `api/v1/__init__.py` + `api/v1/admin/__init__.py` (router registration), `api/v1/agents.py` (extract helper), `frontend/src/routes/agent/DashboardPage.tsx` (**rewritten**; plugin Task 14 inserts `PluginWidgetsSection` into it — this plan removes it), `router.tsx` (`validateSearch` for `d`, admin route), `lib/useSSE.ts` (`subscribeSSE`), `lib/adminSearch.ts` (`ADMIN_PAGES`), `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, `e2e/screenshots.spec.ts` (home-page screenshot content changes — CI commits screenshots), i18n `en.json`/`de.json` + propagation, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `docs/architecture.md`.

---

## Tasks

Order: Tasks 1–9 need neither the plugin nor the reporting plan and come first; Tasks 10–15 and 18 need plugin-system Task 13 committed (the `DASHBOARD_WIDGETS` point); Task 16 needs reporting-builder Tasks 2–3 committed (`share_groups_for`, `check_share_groups`, `GET /reference/share-groups`); Task 17 needs reporting-builder Tasks 5, 6, 10 and 11 committed (`POST /tickets/query`, saved-search store and API, `savedSearchesApi`, `FilterBuilder`). Seed sub-bands (band 49700–49999): Task 1 49700–49709, Task 3 49710–49749, Task 4 49750–49789, Tasks 5–6 49790–49869, Task 7 49870–49889, Task 16 49890–49909, Task 17 49910–49949, spare 49950–49999.

| # | Task | Stack | Needs |
|---|---|---|---|
| 1 | Tables, ORM, migration | backend | — |
| 2 | Pure layer: layout normalisation, widget settings models, default template, API schemas | backend | — |
| 3 | Store, visibility, first-visit start, optimistic lock | backend | — |
| 4 | Dashboards HTTP API (CRUD, order, widget settings patch, widget types) | backend | — |
| 5 | Provider framework, runner, counter samples, batched data endpoints, counter provider | backend | — |
| 6 | Providers: ticket list (presets), escalations, reminders, calendar, team presence | backend | — |
| 7 | Admin role templates API | backend | — |
| 8 | Layout engine and edit-session reducer | frontend (pure) | — |
| 9 | API layer, types, demo and e2e mocks | frontend | Tasks 4–7 route shapes |
| 10 | Widget contract wiring, chrome, the ten widgets | frontend | plugin-system Task 13 |
| 11 | Host page: tabs, view grid, data hook, SSE, phone view | frontend | Tasks 8–10 |
| 12 | Edit mode: toolbar, drag/resize, keyboard, draft, save and conflict, phone edit | frontend | Tasks 8, 11 |
| 13 | Widget gallery | frontend | Task 12 |
| 14 | Settings drawer with generated forms, bespoke editors and live preview | frontend | Tasks 10, 12 |
| 15 | Dashboard management (new, rename, copy, delete, order) and admin templates page | frontend | Tasks 7, 11 |
| 16 | Sharing with groups ("Teilen") | backend + frontend | reporting-builder Tasks 2–3; Task 11 |
| 17 | Ticket-list and counter sources: saved search and own filter | backend + frontend | reporting-builder Tasks 5, 6, 10, 11; Tasks 6, 14 |
| 18 | E2E, screenshots, documentation, full verification | all | all |


### Task 1: Tables, ORM, migration (backend-only)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_dashboards.py`, `backend/tests/test_dashboard_tables.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (append the five models after the last `Tiqora*` model, same imports style: `Mapped`, `mapped_column`, `MEDIUM_TEXT_VARIANT`), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (`version ==`)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: ORM classes `TiqoraDashboard`, `TiqoraDashboardRole`, `TiqoraDashboardGroup`, `TiqoraDashboardWidget`, `TiqoraDashboardCounterSample` with exactly the columns, constraint names and indexes of D2 (`uq_tiqora_dashboard_source`, `fk_tiqora_dashboard_role_dashboard`, `fk_tiqora_dashboard_group_dashboard`, `fk_tiqora_dashboard_widget_dashboard`, `ix_tiqora_dashboard_owner`, `ix_tiqora_dashboard_widget_dashboard`, `ix_tiqora_dashboard_role_role`, `ix_tiqora_dashboard_group_group`, `ix_tiqora_dashboard_sample_day`).

- [ ] **Step 1: Write the failing tests** (`test_dashboard_tables.py`, both dialects via the `params=["mariadb_znuny_url", "postgres_znuny_url"]` fixture of `test_reports_api.py`; band 49700–49709; module deletes its rows in a `finally`)

```python
LO, HI = 49700, 49709

async def test_widgets_roles_and_groups_cascade_with_the_dashboard(env) -> None:
    async with env.session() as s:
        d = TiqoraDashboard(owner_user_id=49700, name="dash497 a", kind="personal", sort=0, version=1,
                            created_at=utc_now(), changed_at=utc_now(), filters="{}", locked=False)
        s.add(d); await s.flush()
        s.add_all([
            TiqoraDashboardWidget(id="w49700-aaaa", dashboard_id=d.id, type_key="core.counter", type_version=1,
                                  settings="{}", layout='{"lg":{"x":0,"y":0,"w":3,"h":2}}', position=0),
            TiqoraDashboardRole(dashboard_id=d.id, role_id=49701),
            TiqoraDashboardGroup(dashboard_id=d.id, group_id=49702),
        ])
        await s.commit()
        await s.execute(delete(TiqoraDashboard).where(TiqoraDashboard.id == d.id)); await s.commit()
        for model in (TiqoraDashboardWidget, TiqoraDashboardRole, TiqoraDashboardGroup):
            assert (await s.execute(select(func.count()).select_from(model))).scalar_one() == 0   # per-dashboard filter on d.id

async def test_unique_source_key_blocks_a_second_first_visit_copy_but_not_null_keys(env) -> None:
    async with env.session() as s:
        s.add(dash(owner=49700, source_key="core.default")); await s.commit()
        s.add(dash(owner=49700, source_key="core.default"))
        with pytest.raises(IntegrityError):
            await s.commit()                                               # second first-visit copy for the same owner
        await s.rollback()
        s.add_all([dash(owner=49700, source_key=None), dash(owner=49700, source_key=None), dash(owner=49701, source_key="core.default")])
        await s.commit()                                                   # NULL keys never collide; another owner may hold the same key
        n = (await s.execute(select(func.count()).select_from(TiqoraDashboard).where(TiqoraDashboard.owner_user_id.in_([49700, 49701])))).scalar_one()
    assert n == 4

async def test_counter_sample_primary_key_is_user_counter_day(env) -> None:
    async with env.session() as s:
        s.add(TiqoraDashboardCounterSample(user_id=49700, counter_key="preset:mine_open", sample_day=date(2026, 10, 1), value=3, sampled_at=utc_now()))
        await s.commit()
        s.add(TiqoraDashboardCounterSample(user_id=49700, counter_key="preset:mine_open", sample_day=date(2026, 10, 1), value=4, sampled_at=utc_now()))
        with pytest.raises(IntegrityError):
            await s.commit()                                               # same (user, counter, day)
        await s.rollback()
        s.add(TiqoraDashboardCounterSample(user_id=49700, counter_key="preset:mine_open", sample_day=date(2026, 10, 2), value=4, sampled_at=utc_now()))
        await s.commit()                                                   # a different day is fine

def test_models_are_registered_with_the_expected_columns() -> None:
    tables = TiqoraBase.metadata.tables
    assert {"tiqora_dashboard", "tiqora_dashboard_role", "tiqora_dashboard_group", "tiqora_dashboard_widget", "tiqora_dashboard_counter_sample"} <= set(tables)
    widget = tables["tiqora_dashboard_widget"]
    assert [c.name for c in widget.primary_key.columns] == ["id"] and widget.c.id.type.length == 36
    assert widget.c.dashboard_id.nullable is False and widget.c.title_override.nullable is True
    assert [c.name for c in tables["tiqora_dashboard_counter_sample"].primary_key.columns] == ["user_id", "counter_key", "sample_day"]
    # the migration-vs-model DDL equality is asserted by the existing tests/test_tiqora_model_ddl.py, run in Step 4
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && uv run python -m pytest tests/test_dashboard_tables.py -q` → FAIL `ImportError: cannot import name 'TiqoraDashboard'`.
- [ ] **Step 3: Implement** the five models per D2 (`kind` `String(10)`, `locked` `Boolean(server_default=false())`, `filters` `Text` with `MEDIUM_TEXT_VARIANT` for `settings`/`layout`/`filters`; `sample_day` `Date`; widget `id` `String(36)` primary key; junction FKs `ondelete="CASCADE"`, `autoincrement=False`). Write the migration with `op.create_table` + `op.create_index` + the unique constraint, `down_revision` = current primary head, `downgrade` drops in reverse order. Bump `TIQORA_HEAD` and the `test_migrate_cli.py` version assert.
- [ ] **Step 4: Run tests** — `uv run python -m pytest tests/test_dashboard_tables.py tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_tiqora_model_ddl.py -q` → PASS. Then lint gates (Global Constraints).
- [ ] **Step 5: Commit** — `rtk git add backend/alembic/versions_tiqora/<file> backend/src/tiqora/db/tiqora/models.py backend/tests/test_dashboard_tables.py backend/tests/test_migration_gate.py backend/tests/test_migrate_cli.py && rtk git commit -m "feat(dashboard): tables for dashboards, widgets, role/group links and counter samples"`.

---

### Task 2: Pure layer — layout normalisation, widget settings models, default template, API schemas (backend-only, no DB)

**Files:**
- Create: `backend/src/tiqora/dashboard/__init__.py`, `layout.py`, `settings_models.py`, `schemas.py`, `defaults.py`; `backend/tests/test_dashboard_layout.py`, `backend/tests/test_dashboard_settings_models.py`, `backend/tests/test_dashboard_defaults.py`

**Interfaces:**
- Consumes: pydantic v2; `tiqora.filters.model.Filter` is **not** used yet (Task 17).
- Produces (names exactly as in D1/D3/D5):
  - `layout.py`: `GRID_COLS: Final = 12`, `normalize_rects(items: Sequence[tuple[str, Rect]]) -> dict[str, Rect]` — clamp every rect (`w = min(w, 12)`, `x = min(x, 12 - w)`, `h >= 1`), then walk items in `(y, x, id)` order and push any rect that overlaps an already placed one down to the first free `y` below it (deterministic, never changes `x/w/h`).
  - `settings_models.py`: `class SettingsError(ValueError)` with `.message`, `.path`; models `TicketListSettings`, `CounterSettings`, `QueueOverviewSettings`, `EscalationsSettings`, `RemindersSettings`, `RecentViewsSettings`, `CalendarAgendaSettings`, `TeamPresenceSettings`, `NotesSettings`, `LinksSettings` (all `extra="forbid"`); `CORE_WIDGET_SETTINGS: dict[str, type[BaseModel]]` keyed by the ten core `type_key`s of D9; `validate_widget_settings(type_key: str, settings: Mapping[str, Any]) -> dict[str, Any]` (known core key → `model.model_validate(settings).model_dump(mode="json")`, defaults applied; unknown key → the dict unchanged but `len(json.dumps) <= MAX_WIDGET_SETTINGS_BYTES`; failure → `SettingsError(message_de, path="settings.limit")`); `safe_link_url(url: str) -> str` (allows `/path` but not `//host`, `http://`, `https://`, `mailto:`; everything else incl. `javascript:`, `data:`, ` javascript:` with leading whitespace/control characters raises `SettingsError`); `LINK_SCHEMES`.
  - Settings fields (D6 table in Task 6 uses them): `TicketListSettings.source: TicketSource` = `PresetSource{kind:"preset", preset: Literal["mine_open","mine_new","unowned_new","escalated","ai_escalated"]}` (Task 17 widens the union with `SavedSearchSource{kind:"saved_search", id:int}` and `FilterSource{kind:"filter", filter: dict}`), default `mine_open`; `columns: list[Literal["tn","title","customer","state","due","queue","owner","priority","changed","created"]]` default `["tn","title","customer","state","due"]` (1–8 entries, unique); `sort: Literal["age","changed","priority","deadline"] = "age"` (the `TicketService` sort keys the dashboard offers); `order: Literal["asc","desc"] = "desc"`; `limit: int` 1–50 default 8; `density: Literal["compact","comfortable"] = "comfortable"`. `CounterSettings{source: PresetSource (default mine_open; Task 17 widens it to the same union as the ticket list), tone: Literal["neutral","danger","ok"] = "neutral"}`. `QueueOverviewSettings{limit 1–12 = 8, only_with_work: bool = True}`. `EscalationsSettings{limit 1–20 = 8, within_hours 0–72 = 0}`. `RemindersSettings{window_hours 1–168 = 24, limit 1–20 = 6}`. `RecentViewsSettings{limit 1–12 = 8}`. `CalendarAgendaSettings{days 1–14 = 1, limit 1–20 = 6}`. `TeamPresenceSettings{limit 1–30 = 8}`. `NotesSettings{markdown: str = "" (max_length 5000)}`. `LinksSettings{items: list[LinkItem{label 1–80, url ≤ 500, validated by safe_link_url}] max 12}`.
  - `schemas.py`: every model of D3 verbatim; `WIDGET_KEY_RE`, `WIDGET_ID_RE`, `MAX_*` constants. `DashboardSaveIn` rejects duplicate widget ids (`model_validator`, message "Widget-IDs müssen eindeutig sein.").
  - `defaults.py`: `DEFAULT_TEMPLATE_KEY`, `DEFAULT_TEMPLATE_VERSION`, `default_widgets() -> list[WidgetIn]` — exactly the eleven widgets of the D5 table.

- [ ] **Step 1: Write the failing tests**

```python
# test_dashboard_layout.py
def test_layout_is_clamped_and_overlaps_resolved() -> None:
    out = normalize_rects([
        ("a", Rect(x=10, y=0, w=6, h=2)),     # x + w = 16 -> x becomes 6
        ("b", Rect(x=6, y=0, w=6, h=2)),      # now collides with a at (6,0): pushed below
        ("c", Rect(x=0, y=0, w=3, h=1)),
    ])
    assert out["a"] == Rect(x=6, y=0, w=6, h=2)
    assert out["b"] == Rect(x=6, y=2, w=6, h=2)
    assert out["c"] == Rect(x=0, y=0, w=3, h=1)
def test_normalize_is_deterministic_and_idempotent() -> None:
    items = [("b", Rect(x=0, y=0, w=4, h=2)), ("a", Rect(x=0, y=0, w=4, h=2))]
    first = normalize_rects(items)
    assert normalize_rects(list(reversed(items))) == first          # order of the input list does not matter
    assert normalize_rects(list(first.items())) == first
def test_touching_rects_do_not_collide() -> None:
    out = normalize_rects([("a", Rect(x=0, y=0, w=6, h=2)), ("b", Rect(x=6, y=0, w=6, h=2)), ("c", Rect(x=0, y=2, w=12, h=1))])
    assert out["b"].y == 0 and out["c"].y == 2
def test_zero_and_oversized_sizes_are_rejected_by_the_model() -> None:
    with pytest.raises(ValidationError): Rect(x=0, y=0, w=0, h=1)
    with pytest.raises(ValidationError): Rect(x=0, y=0, w=13, h=1)
    with pytest.raises(ValidationError): Rect(x=12, y=0, w=1, h=1)

# test_dashboard_settings_models.py
@pytest.mark.parametrize("url", ["javascript:alert(1)", " javascript:alert(1)", "JaVaScRiPt:1", "data:text/html,x", "//evil.example.org", "vbscript:x", "\x01javascript:x"])
def test_link_url_scheme_whitelist(url) -> None:
    with pytest.raises(SettingsError) as e: safe_link_url(url)
    assert "Adresse" in e.value.message
@pytest.mark.parametrize("url", ["/agent/queues", "https://example.org/a?b=1", "http://example.org", "mailto:team@example.org"])
def test_safe_link_urls_pass(url) -> None: assert safe_link_url(url) == url
def test_widget_count_and_settings_size_limits() -> None:
    body = {"name": "x", "version": 1, "widgets": [_widget(i) for i in range(41)]}
    with pytest.raises(ValidationError): DashboardSaveIn.model_validate(body)         # > MAX_WIDGETS_PER_DASHBOARD
    with pytest.raises(SettingsError) as e: validate_widget_settings("org.example.big", {"x": "a" * 21_000})
    assert e.value.path == "settings"
    with pytest.raises(SettingsError): validate_widget_settings("core.notes", {"markdown": "a" * 5001})
def test_defaults_are_applied_and_unknown_fields_rejected() -> None:
    assert validate_widget_settings("core.ticket-list", {})["columns"] == ["tn", "title", "customer", "state", "due"]
    assert validate_widget_settings("core.ticket-list", {})["source"] == {"kind": "preset", "preset": "mine_open"}
    with pytest.raises(SettingsError) as e: validate_widget_settings("core.counter", {"colour": "red"})
    assert e.value.path == "settings.colour"
def test_ticket_list_limit_and_columns_bounds() -> None:
    for bad in ({"limit": 0}, {"limit": 51}, {"columns": []}, {"columns": ["tn", "tn"]}, {"columns": ["bogus"]}):
        with pytest.raises(SettingsError): validate_widget_settings("core.ticket-list", bad)
def test_unknown_widget_type_settings_pass_through_unchanged() -> None:
    assert validate_widget_settings("org.example.weather", {"city": "Bonn"}) == {"city": "Bonn"}

# test_dashboard_defaults.py
def test_default_template_reproduces_todays_home_page() -> None:
    ws = default_widgets()
    assert [w.type_key for w in ws] == ["core.counter"] * 4 + ["core.escalations", "core.ticket-list", "core.ticket-list",
        "core.ticket-list", "core.reminders", "core.recent-views", "core.queue-overview"]
    assert [w.settings["source"]["preset"] for w in ws[:4]] == ["mine_open", "mine_new", "unowned_new", "escalated"]
    assert ws[3].settings["tone"] == "danger"
    assert {w.id for w in ws}.isdisjoint({w.id for w in default_widgets()})            # fresh ids per call
def test_default_template_is_valid_and_overlap_free() -> None:
    ws = default_widgets()
    for w in ws: validate_widget_settings(w.type_key, w.settings)
    rects = {w.id: w.layout.lg for w in ws}
    assert normalize_rects(list(rects.items())) == rects                                # nothing to clamp or push
    assert max(r.y + r.h for r in rects.values()) == 20
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_dashboard_layout.py tests/test_dashboard_settings_models.py tests/test_dashboard_defaults.py -q` → FAIL (modules missing).
- [ ] **Step 3: Implement** per Interfaces. `SettingsError.path` for a pydantic `ValidationError` = `"settings." + ".".join(map(str, first_error["loc"]))`; messages are German ("Der Wert muss zwischen 1 und 50 liegen.", "Diese Adresse ist nicht erlaubt.", "Die Einstellungen sind zu groß.").
- [ ] **Step 4: Run tests + lint gates** → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): layout normalisation, widget settings models, default template`.

---

### Task 3: Store, visibility, first-visit start, optimistic lock (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/store.py`, `backend/src/tiqora/dashboard/start.py`, `backend/tests/_dashboard_helpers.py`, `backend/tests/test_dashboard_store.py`
- Modify: nothing else.

**Interfaces:**
- Consumes: Task 1 models; Task 2 (`schemas`, `normalize_rects`, `validate_widget_settings`, `default_widgets`); `PermissionEngine.groups_for_permission`, `RoleUser`, `Roles`, `Users`.
- Produces (all `async`, caller commits unless stated):

```python
class DashboardNotFound(LookupError): ...                  # unknown id or not visible
class DashboardForbidden(PermissionError):                 # German message
    def __init__(self, message: str) -> None
class DashboardConflict(Exception):
    def __init__(self, current_version: int) -> None
class DashboardInvalid(ValueError):                        # -> 422 {"message","path"}
    def __init__(self, message: str, path: str) -> None
async def user_role_ids(session, user_id: int) -> list[int]                  # valid roles of the user (RoleUser x Roles.valid_id == 1)
async def list_visible(session, user_id: int, *, admin: bool) -> list[DashboardTabOut]     # D4 order
async def get_dashboard(session, dashboard_id: int, user_id: int, *, admin: bool) -> DashboardOut
async def create_dashboard(session, user_id: int, body: DashboardCreateIn, *, admin: bool) -> DashboardOut
async def save_dashboard(session, dashboard_id: int, user_id: int, body: DashboardSaveIn, *, admin: bool) -> DashboardOut
async def patch_widget_settings(session, dashboard_id: int, widget_id: str, user_id: int, body: WidgetSettingsPatchIn, *, admin: bool) -> WidgetOut
async def delete_dashboard(session, dashboard_id: int, user_id: int, *, admin: bool) -> None
async def set_order(session, user_id: int, ids: Sequence[int]) -> None                # own personal/shared only; others -> DashboardForbidden
# start.py
async def ensure_start(session, user_id: int) -> tuple[DashboardOut, bool]            # D4; commits itself (needs the IntegrityError retry)
# tests/_dashboard_helpers.py
USER_A, USER_B, USER_C = 49710, 49711, 49712     # A: ro on G1 (queue Q1) and member of ROLE_1; B: ro on G2 (queue Q2); C: no groups, member of the invalid ROLE_2 (roles.valid_id = 2)
GROUP_1, GROUP_2, QUEUE_1, QUEUE_2, ROLE_1, ROLE_2 = 49715, 49716, 49717, 49718, 49720, 49721
TICKETS: dict[str, int]    # 49730..: every a_* ticket is in QUEUE_1 and owned by USER_A (a_open: state open; a_new: state new; a_escalated: open, escalation_solution_time = now - 3600; a_pending: state pending, until_time = now + 2h); unowned_new: QUEUE_1, state new, owner 1 (root); b_open: QUEUE_2, state open, owner USER_B
def seed_world(sync_url: str) -> None      # idempotent; patterned on _seed_dashboard in tests/test_read_api_db.py
def cleanup_world(sync_url: str) -> None   # deletes tiqora_dashboard* rows of owner ids / names LIKE 'dash497%', then the seeded Znuny rows
```

`_dashboard_helpers.py` also provides the `Env` object every dashboard DB/API test module uses (pattern: `Env` in `tests/test_reports_api.py`; both dialects; each module's fixture builds it and calls `cleanup_world`): `session()` (async context manager), `run(fn, *args, **kw)` (runs a store function in its own session and commits), `client` / `anon` / `admin` (httpx clients; `as_user(uid)` re-points `client` and returns it), store shortcuts `list/get/create/save/patch/delete/set_order(user_id, …)` calling the Task 3 functions with `admin=False`, builders `make_personal(user_id, *, name, widgets=(), share_group_ids=())`, `make_template(*, name, role_ids, locked, sort=0, widgets=None)`, `make_copy_of_template(user_id, template_id)`, `make_saved_search(...)` and `make_appointment(...)` (the last two are only used from Tasks 6 and 17 and are added there), inspection helpers `widget_ids(dashboard_id)`, `count_dashboards(user_id)`, `count_rows(table, **where)`, `sample_rows(user_id)`, `sample_keys(user_id)`, `set_escalation(ticket_id, solution)` (restored by the fixture), `body_of(dashboard, widgets=None)` (→ `DashboardSaveIn` with the current version), constants `foreign_dashboard_id`, `url_of_b_dashboard`, `body_with(source=…)`, `now`, `today_noon_utc`. Small test-local helpers named in the sketches (`w(id, type_key, settings=…, lg=(x, y, w, h))`, `W(id, type_key, settings=…, type_version=1)`, `registry_with`, `provider`, `fake_factory`, `recording_factory`, `slow_sleep`, `raises`, `LimitModel`, `queue_filter`, `counter_widget`, `dash`) are defined at the top of the module that uses them.

- [ ] **Step 1: Write the failing tests** (`test_dashboard_store.py`, both dialects, band 49710–49749)

```python
async def test_ensure_start_creates_the_default_once(env) -> None:
    async with env.session() as s:
        d1, created1 = await ensure_start(s, USER_A)
        d2, created2 = await ensure_start(s, USER_A)
    assert (created1, created2) == (True, False) and d1.id == d2.id
    assert d1.name == "Mein Start" and d1.kind == "personal" and len(d1.widgets) == 11
    assert [w.position if hasattr(w, "position") else i for i, w in enumerate(d1.widgets)][0] == 0
async def test_concurrent_first_visits_create_exactly_one(env) -> None:
    results = await asyncio.gather(*(env.run(ensure_start, USER_B) for _ in range(4)))      # each with its own session
    assert sum(1 for _, created in results if created) == 1
    assert len({d.id for d, _ in results}) == 1
    assert len(await env.count_dashboards(USER_B)) == 1
async def test_unlocked_role_template_is_copied_at_first_visit(env) -> None:
    tpl = await env.make_template(name="dash497 Support", role_ids=[ROLE_1], locked=False, widgets=[counter_widget("mine_new")])
    async with env.session() as s: d, created = await ensure_start(s, USER_A)
    assert created and d.source_template_id == tpl.id and [w.type_key for w in d.widgets] == ["core.counter"]
    assert d.widgets[0].id != tpl.widgets[0].id                    # copied widgets get new ids
async def test_template_priority_is_sort_then_id(env) -> None:
    low = await env.make_template(name="dash497 low", role_ids=[ROLE_1], locked=False, sort=1)
    await env.make_template(name="dash497 high", role_ids=[ROLE_1], locked=False, sort=5)
    async with env.session() as s:
        d, _ = await ensure_start(s, USER_A)
    assert d.source_template_id == low.id                                  # sort 1 beats sort 5
async def test_template_of_a_deleted_role_applies_to_nobody(env) -> None:
    await env.make_template(name="dash497 tot", role_ids=[ROLE_2], locked=False, widgets=[counter_widget("mine_new")])      # ROLE_2 is seeded with valid_id = 2 (invalid); USER_C is its member
    async with env.session() as s:
        d, _ = await ensure_start(s, USER_C)
    assert d.source_template_id is None and len(d.widgets) == 11              # the built-in default, never the template
    assert ROLE_2 not in await user_role_ids(s, USER_C)
async def test_locked_template_is_listed_read_only_for_role_members(env) -> None:
    tpl = await env.make_template(name="dash497 Pflicht", role_ids=[ROLE_1], locked=True)
    tabs = await env.list(USER_A); assert [t.id for t in tabs if t.kind == "template"] == [tpl.id]
    t = next(t for t in tabs if t.id == tpl.id); assert (t.can_edit, t.locked, t.is_owner) == (False, True, False)
    assert all(t.kind != "template" for t in await env.list(USER_C))                  # non-member does not see it
    with pytest.raises(DashboardNotFound): await env.get(tpl.id, USER_C)
    with pytest.raises(DashboardForbidden): await env.save(tpl.id, USER_A, env.body_of(tpl))
async def test_shared_dashboard_is_visible_to_group_members_read_only(env) -> None:
    d = await env.make_personal(USER_A, name="dash497 Team", share_group_ids=[GROUP_2])
    tab = next(t for t in await env.list(USER_B) if t.id == d.id)
    assert tab.kind == "shared" and tab.can_edit is False and tab.owner_name           # B is ro in G2
    assert all(t.id != d.id for t in await env.list(USER_C))
    with pytest.raises(DashboardForbidden): await env.save(d.id, USER_B, env.body_of(d))
async def test_save_with_stale_version_is_409(env) -> None:
    d = await env.make_personal(USER_A, name="dash497 a")
    body = env.body_of(d); await env.save(d.id, USER_A, body)                          # version 1 -> 2
    with pytest.raises(DashboardConflict) as e: await env.save(d.id, USER_A, body)     # still sends version 1
    assert e.value.current_version == 2
async def test_save_replaces_widgets_and_orders_by_list_index(env) -> None:
    d = await env.make_personal(USER_A, name="dash497 b", widgets=[w("aaaaaaaa", "core.counter"), w("bbbbbbbb", "core.links")])
    out = await env.save(d.id, USER_A, env.body_of(d, widgets=[w("bbbbbbbb", "core.links"), w("cccccccc", "core.notes")]))
    assert [x.id for x in out.widgets] == ["bbbbbbbb", "cccccccc"] and out.version == 2
    assert await env.widget_ids(d.id) == {"bbbbbbbb", "cccccccc"}                      # aaaaaaaa deleted
async def test_save_keeps_unknown_widget_types(env) -> None:
    out = await env.save(d.id, USER_A, env.body_of(d, widgets=[w("dddddddd", "org.example.weather", settings={"city": "Bonn"})]))
    assert out.widgets[0].type_key == "org.example.weather" and out.widgets[0].settings == {"city": "Bonn"}
    again = await env.save(d.id, USER_A, env.body_of(out))                              # a second save is unchanged
    assert again.widgets[0].settings == {"city": "Bonn"}
async def test_save_normalises_hostile_layout(env) -> None:
    out = await env.save(d.id, USER_A, env.body_of(d, widgets=[w("eeeeeeee", "core.counter", lg=(10, 0, 6, 2)), w("ffffffff", "core.counter", lg=(6, 0, 6, 2))]))
    assert out.widgets[0].layout.lg == Rect(x=6, y=0, w=6, h=2) and out.widgets[1].layout.lg.y == 2
async def test_widget_id_of_another_dashboard_is_rejected(env) -> None:
    with pytest.raises(DashboardInvalid) as e: await env.save(d2.id, USER_A, env.body_of(d2, widgets=[w("aaaaaaaa", "core.counter")]))
    assert e.value.path == "widgets[0].id"
async def test_invalid_core_settings_report_a_path(env) -> None:
    with pytest.raises(DashboardInvalid) as e: await env.save(d.id, USER_A, env.body_of(d, widgets=[w("gggggggg", "core.ticket-list", settings={"limit": 99})]))
    assert e.value.path == "widgets[0].settings.limit"
async def test_patch_widget_settings_checks_rights_and_version(env) -> None:
    d = await env.make_personal(USER_A, name="dash497 n", widgets=[w("nnnnnnnn", "core.notes", settings={"markdown": "alt"})], share_group_ids=[GROUP_2])
    out = await env.patch(d.id, "nnnnnnnn", USER_A, {"markdown": "neu"}, version=d.version)
    assert out.settings == {"markdown": "neu"} and (await env.get(d.id, USER_A)).version == d.version + 1
    with pytest.raises(DashboardConflict):
        await env.patch(d.id, "nnnnnnnn", USER_A, {"markdown": "x"}, version=d.version)            # stale version
    with pytest.raises(DashboardForbidden):
        await env.patch(d.id, "nnnnnnnn", USER_B, {"markdown": "x"}, version=d.version + 1)        # shared viewer may not edit
    with pytest.raises(DashboardInvalid) as e:
        await env.patch(d.id, "nnnnnnnn", USER_A, {"markdown": "a" * 5001}, version=d.version + 1)
    assert e.value.path == "settings.markdown"
async def test_dashboard_limit_and_order(env) -> None:
    ids = [(await env.make_personal(USER_C, name=f"dash497 c{i}")).id for i in range(20)]
    with pytest.raises(DashboardInvalid) as e:
        await env.make_personal(USER_C, name="dash497 c20")
    assert "höchstens 20" in e.value.message
    await env.set_order(USER_C, list(reversed(ids)))
    assert [t.id for t in await env.list(USER_C)] == list(reversed(ids))
    with pytest.raises(DashboardForbidden):
        await env.set_order(USER_C, [ids[0], env.foreign_dashboard_id])                            # not the caller's
async def test_copy_from_visible_dashboard_gets_new_widget_ids(env) -> None:
    src = await env.make_personal(USER_A, name="dash497 src", widgets=[w("ssssssss", "core.links", settings={"items": []})], share_group_ids=[GROUP_2])
    cp = await env.create(USER_B, DashboardCreateIn(name="dash497 Kopie", copy_from=src.id))
    assert cp.kind == "personal" and cp.is_owner and cp.id != src.id
    assert [x.type_key for x in cp.widgets] == ["core.links"] and cp.widgets[0].id != "ssssssss"
    with pytest.raises(DashboardNotFound):
        await env.create(USER_C, DashboardCreateIn(name="x", copy_from=src.id))                     # not visible to C
async def test_delete_removes_widgets_and_hides_for_others(env) -> None:
    d = await env.make_personal(USER_A, name="dash497 del", widgets=[w("dddddddd", "core.links")], share_group_ids=[GROUP_2])
    with pytest.raises(DashboardForbidden):
        await env.delete(d.id, USER_B)                                                              # visible via group, not the owner
    await env.delete(d.id, USER_A)
    assert await env.widget_ids(d.id) == set() and all(t.id != d.id for t in await env.list(USER_B))
```

- [ ] **Step 2: Run to verify it fails** — `uv run python -m pytest tests/test_dashboard_store.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** store and start per D4. Details: `list_visible` runs these steps: own rows; shared rows via `TiqoraDashboardGroup.group_id IN groups_for_permission(user, "ro")` (int IN-list); locked templates via `user_role_ids` ∩ `TiqoraDashboardRole.role_id`; `owner_name` resolved with one `Users.id IN (…)` query (`"First Last"`, login fallback). `save_dashboard`: normalise the `lg` rects with `normalize_rects` (and `md` rects when present, 8 columns), validate each widget with `validate_widget_settings` catching `SettingsError` → `DashboardInvalid(message, f"widgets[{i}].{e.path}")`, check widget-id collisions with one `SELECT id FROM tiqora_dashboard_widget WHERE id IN (…) AND dashboard_id != :d`, `UPDATE … WHERE id AND version` guard. `settings` stored with `json.dumps(sort_keys=True)`; the `layout` column holds `{"lg":…,"md":…}` (omit `md` when `None`).
- [ ] **Step 4: Run tests + lint gates** → PASS (`TIQORA_STRICT_DB_LEAKS=1`).
- [ ] **Step 5: Commit** — `feat(dashboard): store with visibility, optimistic locking and first-visit start`.

---

### Task 4: Dashboards HTTP API (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/v1/dashboards.py`, `backend/tests/test_dashboards_api.py`
- Modify: `backend/src/tiqora/api/v1/__init__.py` (register `dashboards.router`), `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client/src/schema.d.ts` (regenerated)

**Interfaces:**
- Consumes: Task 3 store/start; Task 2 schemas; `CurrentUser`, `DbSession`; `PermissionEngine(session).is_admin(user.id)`.
- Produces: the routes of D3 except the three data routes (Task 5), the share route (Task 16) and admin routes (Task 7): `GET /dashboards`, `POST /dashboards/ensure-start`, `PUT /dashboards/order`, `POST /dashboards`, `GET/PUT/DELETE /dashboards/{id}`, `PATCH /dashboards/{id}/widgets/{wid}/settings`, `GET /dashboard-widget-types`. Error mapping: `DashboardNotFound`→404 "Dashboard nicht gefunden.", `DashboardForbidden`→403 (message), `DashboardConflict`→409 `{"message":"Dieses Dashboard wurde inzwischen geändert.","current_version":n}`, `DashboardInvalid`→422 `{"message","path"}`. `GET /dashboard-widget-types` returns `WidgetTypeOut` for the ten core keys from `CORE_WIDGET_SETTINGS` (`has_data` = key present in `DASHBOARD_PROVIDERS`, so Task 5/6 flip it).

- [ ] **Step 1: Write the failing tests** (HTTP via `ASGITransport`, `get_current_user`/`get_db` overrides as in `test_reports_api.py`; `env.as_user(uid)` switches the user)

```python
async def test_list_requires_login_and_returns_tabs(env) -> None:
    assert (await env.anon.get("/api/v1/dashboards")).status_code == 401
    r = await env.client.get("/api/v1/dashboards")
    assert r.status_code == 200 and r.json() == []                                    # nothing until ensure-start
async def test_ensure_start_201_then_200_with_widgets(env) -> None:
    first = await env.client.post("/api/v1/dashboards/ensure-start"); second = await env.client.post("/api/v1/dashboards/ensure-start")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"] and len(first.json()["widgets"]) == 11
    assert first.json()["can_edit"] is True and first.json()["is_owner"] is True
async def test_put_roundtrip_and_409(env) -> None:
    d = (await env.client.post("/api/v1/dashboards/ensure-start")).json()
    body = {"name": "dash497 Neu", "icon": None, "version": d["version"], "widgets": d["widgets"][:2]}
    ok = await env.client.put(f"/api/v1/dashboards/{d['id']}", json=body)
    assert ok.status_code == 200 and ok.json()["version"] == d["version"] + 1 and len(ok.json()["widgets"]) == 2
    stale = await env.client.put(f"/api/v1/dashboards/{d['id']}", json=body)
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == d["version"] + 1
    assert "inzwischen geändert" in stale.json()["detail"]["message"]
async def test_put_422_has_german_message_and_path(env) -> None:
    bad = {**body, "widgets": [{**d["widgets"][0], "settings": {"limit": 99}, "type_key": "core.ticket-list"}]}
    r = await env.client.put(url, json=bad)
    assert r.status_code == 422 and r.json()["detail"]["path"] == "widgets[0].settings.limit" and r.json()["detail"]["message"]
async def test_put_rejects_extra_fields_and_too_many_widgets(env) -> None:
    assert (await env.client.put(url, json={**body, "owner_user_id": 1})).status_code == 422
    assert (await env.client.put(url, json={**body, "widgets": [_w(i) for i in range(41)]})).status_code == 422
async def test_invisible_dashboard_is_404_on_every_route(env) -> None:
    d = (await env.client.post("/api/v1/dashboards/ensure-start")).json()                    # USER_A
    env.as_user(USER_C)
    for method, path in (("get", ""), ("put", ""), ("delete", ""), ("patch", f"/widgets/{d['widgets'][0]['id']}/settings")):
        r = await getattr(env.client, method)(f"/api/v1/dashboards/{d['id']}{path}", **({"json": {...}} if method in ("put", "patch") else {}))
        assert r.status_code == 404, (method, r.status_code)
async def test_locked_template_is_403_on_put_and_delete_for_a_role_member(env) -> None:
    tpl = await env.make_template(name="dash497 Pflicht", role_ids=[ROLE_1], locked=True)
    env.as_user(USER_A)
    got = (await env.client.get(f"/api/v1/dashboards/{tpl.id}")).json()
    assert got["can_edit"] is False and got["locked"] is True
    put = await env.client.put(f"/api/v1/dashboards/{tpl.id}", json={"name": "x", "icon": None, "version": got["version"], "widgets": got["widgets"]})
    assert put.status_code == 403
    assert (await env.client.delete(f"/api/v1/dashboards/{tpl.id}")).status_code == 403
async def test_save_keeps_unknown_widget_types(env) -> None:
    d = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 plug"})).json()
    weather = {"id": "wwwwwwww", "type_key": "org.example.weather", "type_version": 3, "title_override": None, "settings": {"city": "Bonn"},
               "layout": {"lg": {"x": 0, "y": 0, "w": 4, "h": 2}}}
    first = await env.client.put(f"/api/v1/dashboards/{d['id']}", json={"name": d["name"], "icon": None, "version": d["version"], "widgets": [weather]})
    assert first.status_code == 200 and first.json()["widgets"][0]["settings"] == {"city": "Bonn"} and first.json()["widgets"][0]["type_version"] == 3
    got = (await env.client.get(f"/api/v1/dashboards/{d['id']}")).json()
    again = await env.client.put(f"/api/v1/dashboards/{d['id']}", json={"name": got["name"], "icon": None, "version": got["version"], "widgets": got["widgets"]})
    assert again.status_code == 200 and again.json()["widgets"][0]["type_key"] == "org.example.weather"
async def test_patch_settings_edits_one_note(env) -> None:
    d = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 note"})).json()
    note = {"id": "nnnnnnnn", "type_key": "core.notes", "type_version": 1, "title_override": None, "settings": {"markdown": ""}, "layout": {"lg": {"x": 0, "y": 0, "w": 4, "h": 3}}}
    d = (await env.client.put(f"/api/v1/dashboards/{d['id']}", json={"name": d["name"], "icon": None, "version": d["version"], "widgets": [note]})).json()
    url = f"/api/v1/dashboards/{d['id']}/widgets/nnnnnnnn/settings"
    ok = await env.client.patch(url, json={"settings": {"markdown": "Hallo"}, "version": d["version"]})
    assert ok.status_code == 200 and ok.json()["settings"] == {"markdown": "Hallo"}
    assert (await env.client.get(f"/api/v1/dashboards/{d['id']}")).json()["version"] == d["version"] + 1
    bad = await env.client.patch(url, json={"settings": {"markdown": "a" * 6000}, "version": d["version"] + 1})
    assert bad.status_code == 422 and bad.json()["detail"]["path"] == "settings.markdown"
async def test_order_and_create_copy_and_delete(env) -> None:
    a = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 A"})).json()
    b = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 B", "copy_from": a["id"]})).json()
    assert b["id"] != a["id"]
    assert (await env.client.put("/api/v1/dashboards/order", json={"ids": [b["id"], a["id"]]})).status_code == 204
    assert [t["id"] for t in (await env.client.get("/api/v1/dashboards")).json()][:2] == [b["id"], a["id"]]
    assert (await env.client.delete(f"/api/v1/dashboards/{a['id']}")).status_code == 204
    assert (await env.client.get(f"/api/v1/dashboards/{a['id']}")).status_code == 404
async def test_widget_types_lists_ten_core_keys(env) -> None:
    keys = [t["key"] for t in (await env.client.get("/api/v1/dashboard-widget-types")).json()]
    assert keys == sorted(keys) and len(keys) == 10 and "core.ticket-list" in keys
async def test_openapi_has_no_schema_name_clash(env) -> None:
    schemas = (await env.client.get("/openapi.json")).json()["components"]["schemas"]
    assert "DashboardOut" in schemas and "DashboardTabOut" in schemas and "DashboardSummary" in schemas   # ticket KPI model untouched
```

- [ ] **Step 2: Run to verify it fails** → 404 on the routes.
- [ ] **Step 3: Implement** the router (static paths `ensure-start` and `order` before `/{dashboard_id}`; `ensure-start` returns 201 only when created; `admin = await PermissionEngine(session).is_admin(user.id)`; commit after each write). Register in `api/v1/__init__.py`. If the API-key scope map (grep `apiKeyScopes` in `frontend/src/lib` and the backend equivalent used by `/saved-searches`) lists areas per path prefix, put `/api/v1/dashboards` and `/api/v1/dashboard-widget-types` in the same area as `/api/v1/saved-searches`/`/api/v1/tickets`; if there is no such per-path map, nothing to do.
- [ ] **Step 4: Regenerate openapi + client** (Global Constraints), run `uv run python -m pytest tests/test_dashboards_api.py -q` and lint gates → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): dashboards API (list, start, save, order, widget settings, widget types)` with the explicit openapi/schema paths.

---

### Task 5: Provider framework, runner, counter samples, batched data endpoints, counter provider (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/providers/__init__.py`, `providers/presets.py`, `providers/counter.py`, `backend/src/tiqora/dashboard/samples.py`, `backend/src/tiqora/dashboard/runner.py`, `backend/tests/test_dashboard_runner.py`, `backend/tests/test_dashboard_samples.py`, `backend/tests/test_dashboard_counter.py`
- Modify: `backend/src/tiqora/api/deps.py` (`get_session_factory_dep`), `backend/src/tiqora/extensions/core.py` (register `core_providers()` — `registry.add(DASHBOARD_PROVIDERS, p)` for each, imported inside `register_core` like the other items), `backend/src/tiqora/api/v1/dashboards.py` (three data routes), `backend/tests/test_dashboards_api.py` (data route tests), openapi files

**Interfaces:**
- Test wiring: the providers open their own sessions, so the `get_db` override alone does not reach them. The data routes obtain the factory through a new dependency `get_session_factory_dep(request) -> async_sessionmaker` in `backend/src/tiqora/api/deps.py` (`request.app.state.session_factory` if set, else `get_session_factory()` — the same lookup `get_db` uses); `Env` overrides it next to `get_db` with the test engine's factory. Isolation: the module fixture of every dashboard DB test module is function-scoped for the dashboard rows — it deletes `tiqora_dashboard*` rows of the band's owner ids before each test (pattern `_RESET` in `test_reports_api.py`), so first-visit and limit tests do not see each other's dashboards.
- Consumes: Task 3 (`get_dashboard`, helpers), D6/D7; `get_session_factory`/`request.app.state.session_factory` (see `api/deps.py:get_db`), `get_redis`, `tiqora.reporting.sql.statement_timeout` (verify signature before use), `TicketService.list_tickets`.
- Produces: `ProviderContext`, `ProviderForbidden`, `DashboardProvider`, `DASHBOARD_PROVIDERS`, `core_providers()` (this task returns `[counter_provider()]`; Task 6 appends); `TICKET_PRESETS: dict[str, Callable[[int], dict[str, Any]]]`, `preset_kwargs(preset: str, user_id: int) -> dict[str, Any]`; `record_and_history` (D7); `run_batch`, `run_widget` (D6 rule 2). Routes: `GET /api/v1/dashboards/{id}/data` (query `widgets` = comma list of widget ids, max 40, unknown ids ignored), `GET /api/v1/dashboards/{id}/widgets/{wid}/data` (404 when the widget is not on the dashboard; a widget without provider → `404 "Dieses Widget hat keine Daten."`), `POST /api/v1/dashboard-widget-types/{key}/data` (body `WidgetPreviewIn`; unknown key → 404; settings validated by `validate_widget_settings` → 422 `{"message","path"}`).

- [ ] **Step 1: Write the failing tests**

```python
# test_dashboard_runner.py — pure asyncio, a private Registry with fake providers, a fake session factory (async context manager yielding a stub)
async def test_slow_provider_is_pending_others_ok() -> None:
    reg = registry_with(provider("t.fast", fast), provider("t.slow", slow_sleep(0.5), timeout_seconds=0.05))
    out = await run_batch(fake_factory, reg, user_id=1, widgets=[W("a", "t.fast"), W("b", "t.slow")])
    assert out["a"].status == "ok" and out["a"].data == {"n": 1}
    assert out["b"].status == "pending" and out["b"].data is None
async def test_provider_exception_is_error_status() -> None:
    out = await run_batch(fake_factory, registry_with(provider("t.boom", raises(RuntimeError("secret detail")))), 1, [W("a", "t.boom")])
    assert out["a"].status == "error" and out["a"].error == "Diese Daten konnten nicht geladen werden."
    assert "secret detail" not in (out["a"].error or "")                    # never leaks exception text
async def test_forbidden_and_invalid_settings_statuses() -> None:
    reg = registry_with(provider("t.deny", raises(ProviderForbidden("Nein."))), provider("t.lim", fast, settings_model=LimitModel))   # LimitModel: limit: int = Field(le=50)
    out = await run_batch(fake_factory, reg, 1, [W("a", "t.deny"), W("b", "t.lim", settings={"limit": 99})])
    assert (out["a"].status, out["a"].error) == ("forbidden", "Nein.")
    assert (out["b"].status, out["b"].error) == ("error", "Die Einstellungen dieses Widgets sind ungültig.")
async def test_widgets_without_provider_are_absent_and_newer_version_is_an_error() -> None:
    out = await run_batch(fake_factory, reg, 1, [W("a", "org.example.weather"), W("b", "t.fast", type_version=2)])
    assert "a" not in out and out["b"].status == "error" and "neuere Version" in out["b"].error
async def test_each_provider_gets_its_own_session() -> None:
    seen: list[int] = []
    async def record(ctx, settings):
        seen.append(id(ctx.session)); return {}
    log: list[str] = []
    await run_batch(recording_factory(log), registry_with(provider("t.a", record), provider("t.b", record)), 1, [W("a", "t.a"), W("b", "t.b")])
    assert len(seen) == 2 and seen[0] != seen[1]                            # never the request's or each other's session
    assert log.count("enter") == 2 and log.count("exit") == 2
async def test_cancelled_provider_session_is_closed() -> None:
    log: list[str] = []
    factory = recording_factory(log)                                        # appends "enter" / "exit" per session
    await run_batch(factory, registry_with(provider("t.slow", slow_sleep(0.5), timeout_seconds=0.05)), 1, [W("a", "t.slow")])
    assert log == ["enter", "exit"]                                         # the timed-out provider's session was closed

# test_dashboard_samples.py (both dialects, user 49790+)
async def test_first_day_has_no_delta_and_one_point(env) -> None:
    assert await record_and_history(s, user_id=U, counter_key="preset:mine_open", value=5, today=date(2026, 10, 10)) == (None, [5])
async def test_delta_is_against_the_newest_earlier_day_within_seven_days(env) -> None:
    for d, v in ((date(2026, 10, 3), 4), (date(2026, 10, 8), 6)): await record(..., value=v, today=d)
    assert await record(..., value=9, today=date(2026, 10, 10)) == (3, [4, 6, 9])               # 9 - 6; Oct 3 is exactly 7 days back and still in the spark
    assert (await record(..., value=9, today=date(2026, 10, 20)))[0] is None                  # nothing within 7 days
async def test_same_day_is_overwritten_not_duplicated_and_old_rows_pruned(env) -> None:
    await record(..., value=1, today=t); await record(..., value=2, today=t)
    assert await env.sample_rows(U) == [(t, 2)]
    await record(..., value=3, today=t + timedelta(days=40)); assert await env.sample_rows(U) == [(t + timedelta(days=40), 3)]    # > 35 days dropped
async def test_concurrent_first_write_of_a_day_does_not_raise(env) -> None:
    results = await asyncio.gather(*(env.run(record_and_history, user_id=U, counter_key="preset:mine_new", value=v, today=T) for v in (1, 2, 3, 4)))
    assert len(results) == 4 and len(await env.sample_rows(U)) == 1

# test_dashboard_counter.py (both dialects; world of Task 3)
SUMMARY_KEY = {"mine_open": "my_open", "mine_new": "my_new", "unowned_new": "unowned_new", "escalated": "escalated", "ai_escalated": "ai_escalated"}   # preset -> key of count_dashboard_summary
@pytest.mark.parametrize("preset", list(SUMMARY_KEY))
async def test_counter_equals_dashboard_summary(env, preset) -> None:
    summary = await TicketService(s).count_dashboard_summary(USER_A)
    out = await run_widget(factory, registry, USER_A, W("c", "core.counter", settings={"source": {"kind": "preset", "preset": preset}}))
    assert out.status == "ok" and out.data["value"] == summary[SUMMARY_KEY[preset]]             # parity with today's home-page numbers
async def test_counter_for_a_user_without_queues_is_zero(env) -> None:
    out = await run_widget(factory, registry, USER_C, W("c", "core.counter", settings={"source": {"kind": "preset", "preset": "mine_open"}}))
    assert out.status == "ok" and out.data["value"] == 0 and out.data["spark"] == [0] and out.data["delta"] is None
async def test_batch_data_uses_viewer_not_owner(env) -> None:
    # USER_A (owner, has Q1 tickets) shares a dashboard with a counter mine_open source? Presets are per-viewer: USER_B views A's shared dashboard
    # -> counter value == B's own count (1: b_open) not A's (2); GET /dashboards/{id}/data as B
    r = await env.as_user(USER_B).get(f"/api/v1/dashboards/{shared.id}/data")
    assert r.json()["widgets"][counter_id]["data"]["value"] == 1
```

API tests (in `test_dashboards_api.py`): `test_data_batch_returns_only_widgets_with_providers` (default dashboard: result keys == ids of the 4 counters now (Task 6 adds the rest; assert `keys ⊇ counters`), `recent-views`/`queue-overview` ids absent); `test_data_widgets_filter` (`?widgets=a,b` limits); `test_single_widget_404s` (foreign id 404; widget without provider 404); `test_preview_validates_settings` (`{"settings":{"source":{"kind":"preset","preset":"nope"}}}` → 422 path `settings.source.preset`); `test_preview_runs_in_viewer_scope`.

- [ ] **Step 2: Run to verify they fail** (modules missing).
- [ ] **Step 3: Implement.** `run_batch` creates one `asyncio.Task` per runnable widget; each does `async with factory() as session: ctx = ProviderContext(…); settings = provider.settings_model.model_validate(w.settings); await asyncio.wait_for(provider.run(ctx, settings), provider.timeout_seconds)`. In the routes the factory comes from `Depends(get_session_factory_dep)` (see Test wiring), `redis = None` on failure to obtain it. The counter provider: `kwargs = preset_kwargs(preset, uid)`; `page = await TicketService(session).list_tickets(uid, limit=1, offset=0, **kwargs)`; `delta, spark = await record_and_history(…, counter_key=f"preset:{preset}", value=page.total, today=utc_now().date())`; commit the sample write; return the D6 shape. Register the provider in `core_providers()`.
- [ ] **Step 4: Run tests + openapi regeneration + lint gates** → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): provider registry, runner with per-provider budget, batched data endpoints, counters with history`.

---

### Task 6: Providers — ticket list (presets), escalations, reminders, calendar agenda, team presence (backend-only)

**Files:**
- Create: `backend/src/tiqora/dashboard/providers/{ticket_list,escalations,reminders,calendar_agenda,team_presence}.py`, `backend/tests/test_dashboard_providers.py`
- Modify: `backend/src/tiqora/dashboard/providers/__init__.py` (`core_providers()` returns all six), `backend/src/tiqora/api/v1/agents.py` (extract `async def list_online_agents_for(redis_client, session) -> list[OnlineAgentOut]` from the route body; the route calls it; behaviour unchanged incl. the "Redis down → []" guard)

**Interfaces:**
- Consumes: Task 5 framework; `TicketService.list_tickets` (kwargs of D6 rule 5, `sort`/`order`/`limit`), `CalendarService.list_occurrences`, `resolve_user_time_zone` (`tiqora.domain.timezones`; verify the signature), `display time zone` of the user.
- Produces: providers keyed `core.ticket-list`, `core.escalations`, `core.reminders`, `core.calendar-agenda`, `core.team-presence`; output shapes of D6 rule 4. `settings.sort` (`age|changed|priority|deadline`) is passed as-is as the `TicketService` sort key.
  - `core.ticket-list`: `page = list_tickets(uid, offset=0, limit=settings.limit, sort=…, order=…, **preset_kwargs(preset, uid))` → `{"items": [i.model_dump(mode="json") for i in page.items], "total": page.total}`; a non-preset source raises `ProviderForbidden`-free `ValueError` until Task 17 extends it (the settings model rejects it first, so unreachable).
  - `core.escalations`: `list_tickets(uid, state_type="open", escalating_within=within_hours*3600, sort="deadline", order="asc", limit=settings.limit)`; `epoch` = smallest `escalation_*` value > 0 of the item; `oldest_epoch` = `min(epoch)` or `None`.
  - `core.reminders`: `list_tickets(uid, owner_id=uid, state_type="pending", sort="age", order="asc", limit=50)` then keep items with `0 < until_time <= now_epoch + window_hours*3600`, sort by `until_time`, cut to `limit`; `total` = number kept before the cut; `overdue = until_time <= now_epoch` (same rule as today's `remindersDue`).
  - `core.calendar-agenda`: window = start of today in the viewer's display zone → `+days` days (converted to naive UTC datetimes as `list_occurrences` expects), `items = occurrences[:limit]` sorted by `start_time`.
  - `core.team-presence`: `online = await list_online_agents_for(ctx.redis, session)`, own entry first then by `full_name`, cut to `limit`; `total_online = len(all)`. Without Redis (`ctx.redis is None`) → `{"online": [], "total_online": 0}`.

- [ ] **Step 1: Write the failing tests** (`test_dashboard_providers.py`, both dialects, world of Task 3; time-sensitive rows seeded relative to `time.time()` — never a fixed date, skill `test-date-literal-time-bomb`)

```python
async def test_ticket_list_preset_matches_todays_dashboard_queries(env) -> None:
    out = await run_widget(f, reg, USER_A, W("t", "core.ticket-list", settings={"source": {"kind": "preset", "preset": "mine_open"}, "sort": "age", "order": "desc", "limit": 8}))
    ids = [i["id"] for i in out.data["items"]]
    expected = await TicketService(s).list_tickets(USER_A, owner_id=USER_A, state_type="open", sort="age", order="desc", limit=8)   # exactly today's lockedQ query
    assert ids == [i.id for i in expected.items] and out.data["total"] == expected.total
@pytest.mark.parametrize("preset,key", [("mine_new", "a_new"), ("unowned_new", "unowned_new"), ("escalated", "a_escalated")])
async def test_ticket_list_presets_return_the_seeded_ticket(env, preset, key) -> None: assert TICKETS[key] in ids
async def test_ticket_list_limit_is_applied_and_total_is_not(env) -> None:
    out = await run_widget(factory, registry, USER_A, W("t", "core.ticket-list", settings={"source": {"kind": "preset", "preset": "mine_open"}, "limit": 1}))
    assert len(out.data["items"]) == 1 and out.data["total"] == 2          # a_open and a_escalated are both open and owned by A
async def test_shared_dashboard_data_runs_in_viewer_scope(env) -> None:
    # USER_A's shared dashboard (GROUP_2 members see it) with core.ticket-list preset mine_open; USER_B has ro only on Q2, owns b_open
    out = await env.as_user(USER_B).get_data(d.id)
    assert [i["id"] for i in out["widgets"][wid]["data"]["items"]] == [TICKETS["b_open"]]        # never A's tickets
async def test_escalations_hide_foreign_queue_tickets(env) -> None:
    a = await run_widget(..., USER_A, W("e", "core.escalations")); b = await run_widget(..., USER_B, W("e", "core.escalations"))
    assert TICKETS["a_escalated"] in [x["ticket"]["id"] for x in a.data["items"]]
    assert b.data["items"] == [] and b.data["total"] == 0 and b.data["oldest_epoch"] is None
async def test_escalations_sorted_oldest_first_with_epoch(env) -> None:
    await env.set_escalation(TICKETS["a_open"], solution=env.now - 7200)   # older breach than a_escalated's (now - 3600); restored by the fixture
    out = await run_widget(factory, registry, USER_A, W("e", "core.escalations"))
    epochs = [x["epoch"] for x in out.data["items"]]
    assert len(epochs) == 2 and epochs == sorted(epochs) and out.data["oldest_epoch"] == epochs[0]
async def test_escalations_within_hours_includes_upcoming(env) -> None:
    await env.set_escalation(TICKETS["a_open"], solution=env.now + 1800)   # deadline in 30 minutes
    none = await run_widget(factory, registry, USER_A, W("e", "core.escalations", settings={"within_hours": 0}))
    hour = await run_widget(factory, registry, USER_A, W("e", "core.escalations", settings={"within_hours": 1}))
    assert TICKETS["a_open"] not in [x["ticket"]["id"] for x in none.data["items"]]
    assert TICKETS["a_open"] in [x["ticket"]["id"] for x in hour.data["items"]]
async def test_reminders_window_and_overdue_flag(env) -> None:
    out = await run_widget(..., USER_A, W("r", "core.reminders", settings={"window_hours": 24, "limit": 6}))
    item = next(i for i in out.data["items"] if i["ticket"]["id"] == TICKETS["a_pending"])
    assert item["overdue"] is False and item["until"] > time.time()
    short = await run_widget(..., W("r", "core.reminders", settings={"window_hours": 1, "limit": 6}))   # until = now + 2h
    assert TICKETS["a_pending"] not in [i["ticket"]["id"] for i in short.data["items"]]
async def test_calendar_agenda_uses_the_viewers_calendars_and_today_in_their_zone(env) -> None:
    await env.make_appointment(calendar_group=GROUP_1, title="dash497 heute", start=env.today_noon_utc)
    await env.make_appointment(calendar_group=GROUP_1, title="dash497 morgen", start=env.today_noon_utc + timedelta(days=1))
    a = await run_widget(factory, registry, USER_A, W("k", "core.calendar-agenda", settings={"days": 1, "limit": 6}))
    assert [i["title"] for i in a.data["items"]] == ["dash497 heute"]       # tomorrow is outside days=1
    b = await run_widget(factory, registry, USER_B, W("k", "core.calendar-agenda"))
    assert b.data["items"] == []                                            # B has no ro on GROUP_1's calendar
async def test_team_presence_lists_online_agents_own_first(env) -> None:
    redis = FakeRedis({online_key(USER_A): "{}", online_key(USER_B): "{}"})      # same fake as tests of /agents/online
    out = await run_widget(..., USER_B, ..., redis=redis); assert [a["id"] for a in out.data["online"]][0] == USER_B and out.data["total_online"] == 2
async def test_team_presence_without_redis_is_empty_not_an_error(env) -> None:
    out = await run_widget(factory, registry, USER_A, W("p", "core.team-presence"), redis=None)
    assert out.status == "ok" and out.data == {"online": [], "total_online": 0}
async def test_online_route_and_helper_return_the_same_list(env) -> None:
    redis = FakeRedis({online_key(USER_A): '{"avatar_url": null}'})        # same fake as the /agents/online tests
    async with env.session() as s:
        helper = [a.model_dump(mode="json") for a in await list_online_agents_for(redis, s)]
    assert helper == (await env.client.get("/api/v1/agents/online")).json() and [a["id"] for a in helper] == [USER_A]
async def test_user_without_any_queue_gets_empty_states_not_errors(env) -> None:
    for key in ("core.ticket-list", "core.escalations", "core.reminders", "core.counter"):
        assert (await run_widget(..., USER_C, W("x", key))).status == "ok"
async def test_default_dashboard_batch_returns_data_for_every_provider_widget(env) -> None:
    d = (await env.as_user(USER_A).post("/api/v1/dashboards/ensure-start")).json()
    data = (await env.client.get(f"/api/v1/dashboards/{d['id']}/data")).json()["widgets"]
    provider_types = {"core.counter", "core.escalations", "core.ticket-list", "core.reminders"}
    assert set(data) == {w["id"] for w in d["widgets"] if w["type_key"] in provider_types} and len(data) == 9     # 4 counters, escalations, 3 lists, reminders
    assert all(r["status"] == "ok" for r in data.values())                  # recent-views and queue-overview are frontend-only and absent
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the five providers + the `agents.py` extraction + `core_providers()`.
- [ ] **Step 4: Run tests** (`tests/test_dashboard_providers.py`, the existing agents tests, `tests/test_read_api_db.py::test_dashboard_summary_counts`) + lint gates + openapi regeneration if models changed → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): data providers for ticket lists, escalations, reminders, calendar and team presence`.

---

### Task 7: Admin role templates API (backend-only)

**Files:**
- Create: `backend/src/tiqora/api/v1/admin/dashboard_templates.py`, `backend/tests/test_admin_dashboard_templates_api.py`
- Modify: `backend/src/tiqora/dashboard/store.py` (add `list_templates`, `create_template`, `update_template`, `delete_template`), `backend/src/tiqora/api/v1/admin/__init__.py` (register), openapi files

**Interfaces:**
- Consumes: Task 3 store, `AdminUser`, `Roles` (role existence check: unknown or invalid role ids → 422 "Die Rolle … gibt es nicht." path `role_ids`).
- Produces: store functions `list_templates(session) -> list[TemplateOut]`, `create_template(session, admin_id: int, body: TemplateIn) -> TemplateOut` (copies `default_widgets()`; `owner_user_id = NULL`, `kind = 'template'`), `update_template(session, template_id, body: TemplateIn, *, version: int | None = None) -> TemplateOut` (properties + role rows; bumps `version`), `delete_template(session, template_id) -> None`. Routes of D3 (admin block). The template **layout** is saved with `PUT /api/v1/dashboards/{id}` by an admin (Task 3 rights: templates are editable by admins only).

- [ ] **Step 1: Write the failing tests** (band 49870–49889)

```python
async def test_templates_are_admin_only(env) -> None:
    assert (await env.as_user(USER_A).get("/api/v1/admin/dashboard-templates")).status_code == 403
async def test_create_template_starts_with_default_widgets_and_roles(env) -> None:
    r = await env.admin.post("/api/v1/admin/dashboard-templates", json={"name": "dash497 Support", "role_ids": [ROLE_1], "locked": False, "sort": 0})
    assert r.status_code == 201 and r.json()["widget_count"] == 11 and r.json()["role_ids"] == [ROLE_1] and r.json()["kind"] == "template"
async def test_unknown_role_is_422(env) -> None:
    r = await env.admin.post("/api/v1/admin/dashboard-templates", json={"name": "dash497 x", "role_ids": [999999], "locked": False, "sort": 0})
    assert r.status_code == 422 and r.json()["detail"]["path"] == "role_ids" and "gibt es nicht" in r.json()["detail"]["message"]
async def test_update_properties_and_delete_cascades(env) -> None:
    tpl = (await env.admin.post("/api/v1/admin/dashboard-templates", json={"name": "dash497 t", "role_ids": [ROLE_1], "locked": False, "sort": 0})).json()
    upd = await env.admin.put(f"/api/v1/admin/dashboard-templates/{tpl['id']}", json={"name": "dash497 t2", "role_ids": [ROLE_1, ROLE_2], "locked": True, "sort": 3})
    assert upd.status_code == 200 and (upd.json()["name"], upd.json()["locked"], upd.json()["sort"]) == ("dash497 t2", True, 3) and sorted(upd.json()["role_ids"]) == [ROLE_1, ROLE_2]
    copy = await env.make_copy_of_template(USER_A, tpl["id"])               # a personal dashboard with source_template_id = tpl.id
    assert (await env.admin.delete(f"/api/v1/admin/dashboard-templates/{tpl['id']}")).status_code == 204
    assert await env.count_rows("tiqora_dashboard_widget", dashboard_id=tpl["id"]) == 0 and await env.count_rows("tiqora_dashboard_role", dashboard_id=tpl["id"]) == 0
    assert (await env.get(copy.id, USER_A)).source_template_id == tpl["id"]  # the copy lives on
async def test_admin_saves_template_layout_through_dashboards_put(env) -> None:
    got = (await env.admin.get(f"/api/v1/dashboards/{tpl['id']}")).json()                    # admin can read an unlocked template by id
    ok = await env.admin.put(f"/api/v1/dashboards/{tpl['id']}", json={"name": got["name"], "icon": None, "version": got["version"], "widgets": got["widgets"][:3]})
    assert ok.status_code == 200 and len(ok.json()["widgets"]) == 3
async def test_locking_a_template_makes_it_a_read_only_tab_for_role_members(env) -> None:
    await env.admin.put(f"/api/v1/admin/dashboard-templates/{tpl['id']}", json={**props, "locked": True})
    tabs = (await env.as_user(USER_A).get("/api/v1/dashboards")).json()
    assert [t["id"] for t in tabs if t["kind"] == "template"] == [tpl["id"]] and next(t for t in tabs if t["id"] == tpl["id"])["can_edit"] is False
```

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement.** **Step 4: Run tests + lint + openapi regeneration → PASS.** **Step 5: Commit** — `feat(dashboard): admin API for role templates`.

---

### Task 8: Layout engine and edit-session reducer (frontend-only, pure)

**Files:**
- Create: `frontend/src/dashboard/model.ts`, `frontend/src/dashboard/layout/grid.ts`, `breakpoints.ts`, `session.ts`, `keyboard.ts` and tests `grid.test.ts`, `breakpoints.test.ts`, `session.test.ts`, `keyboard.test.ts`

**Interfaces:** D8 verbatim. Additionally `keyboard.ts`: `export function keyToAction(e: { key: string; shiftKey: boolean }): { kind: "move" | "resize"; dx: number; dy: number } | { kind: "settings" } | null` (Arrow* → move/resize by one cell, Enter → settings, everything else null). Consumes nothing from the other tasks (no React, no i18n).

- [ ] **Step 1: Write the failing tests**

```ts
// grid.test.ts
const it = (id: string, x: number, y: number, w: number, h: number): LayoutItem => ({ id, rect: { x, y, w, h }, min: [2, 1], max: [12, 8] });
test("clampRect keeps the rect inside the grid and inside min/max", () => {
  expect(clampRect({ x: 10, y: -3, w: 6, h: 20 }, 12, [2, 1], [12, 8])).toEqual({ x: 6, y: 0, w: 6, h: 8 });
  expect(clampRect({ x: 0, y: 0, w: 1, h: 1 }, 12, [3, 2], [12, 8])).toEqual({ x: 0, y: 0, w: 3, h: 2 });
});
test("touching rects do not collide, overlapping ones do", () => {
  expect(collides({ x: 0, y: 0, w: 6, h: 2 }, { x: 6, y: 0, w: 6, h: 2 })).toBe(false);
  expect(collides({ x: 0, y: 0, w: 6, h: 2 }, { x: 5, y: 1, w: 6, h: 2 })).toBe(true);
});
test("moveItem pushes the item below down and cascades", () => {
  const out = moveItem([it("a", 0, 0, 6, 2), it("b", 0, 2, 6, 2), it("c", 0, 4, 6, 2)], "c", { x: 0, y: 0 }, 12);
  expect(rects(out)).toEqual({ c: { x: 0, y: 0, w: 6, h: 2 }, a: { x: 0, y: 2, w: 6, h: 2 }, b: { x: 0, y: 4, w: 6, h: 2 } });
});
test("compactUp closes holes left by a move, never past the fixed item", () => {
  const out = compactUp([it("a", 0, 0, 6, 2), it("b", 0, 6, 6, 2)], 12);
  expect(rects(out).b.y).toBe(2);
});
test("resizeItem respects min and max and pushes neighbours", () => {
  const out = resizeItem([it("a", 0, 0, 3, 2), it("b", 0, 2, 3, 2)], "a", { w: 20, h: 5 }, 12);
  expect(rects(out).a).toEqual({ x: 0, y: 0, w: 12, h: 5 }); expect(rects(out).b.y).toBe(5);
});
test("firstFreeSpot finds the first gap row-major and appends below when full", () => {
  expect(firstFreeSpot([it("a", 0, 0, 6, 2)], 6, 2, 12)).toEqual({ x: 6, y: 0 });
  expect(firstFreeSpot([it("a", 0, 0, 12, 2)], 6, 2, 12)).toEqual({ x: 0, y: 2 });
  expect(firstFreeSpot([], 3, 2, 12)).toEqual({ x: 0, y: 0 });
});
test("pixelsToCell snaps to the nearest cell and clamps at zero", () => {
  // width 1200, 12 cols, gap 16 -> cellW = (1200 - 176) / 12 = 85.33; pitch x = 101.33, y = 80
  expect(pixelsToCell({ x: 205, y: 165 }, { width: 1200, cols: 12 })).toEqual({ x: 2, y: 2 });
  expect(pixelsToCell({ x: -40, y: -9 }, { width: 1200, cols: 12 })).toEqual({ x: 0, y: 0 });
});
test("layout results are deterministic whatever the input order", () => {
  const items = [it("a", 0, 0, 6, 2), it("b", 0, 0, 6, 2), it("c", 3, 1, 6, 2)];
  expect(rects(resolveCollisions(items, "a", 12))).toEqual(rects(resolveCollisions([...items].reverse(), "a", 12)));
});
test("resolveCollisions leaves a clean layout unchanged and terminates on 40 stacked items", () => {
  const clean = [it("a", 0, 0, 6, 2), it("b", 6, 0, 6, 2)];
  expect(resolveCollisions(clean, "a", 12)).toEqual(clean);
  const out = resolveCollisions(Array.from({ length: 40 }, (_, i) => it(`i${i}`, 0, 0, 4, 2)), "i0", 12);
  for (const [i, a] of out.entries()) for (const b of out.slice(i + 1)) expect(collides(a.rect, b.rect)).toBe(false);
});

// breakpoints.test.ts
test("breakpointForWidth", () => { expect([1200, 900, 899, 520, 519].map(breakpointForWidth)).toEqual(["lg", "lg", "md", "md", "sm"]); });
test("md layout scales lg and never overlaps", () => {
  const ws = [w("a", { x: 0, y: 0, w: 3, h: 2 }), w("b", { x: 3, y: 0, w: 3, h: 2 }), w("c", { x: 6, y: 0, w: 3, h: 2 }), w("d", { x: 9, y: 0, w: 3, h: 2 })];
  const m = layoutForBreakpoint(ws, "md", () => [1, 1]);
  expect([...m.values()].every((r) => r.x + r.w <= 8)).toBe(true);
  for (const [i, a] of [...m.values()].entries()) for (const b of [...m.values()].slice(i + 1)) expect(collides(a, b)).toBe(false);
});
test("an explicit md rect wins over the derived one", () => {
  const ws = [{ ...w("a", { x: 0, y: 0, w: 6, h: 2 }), layout: { lg: { x: 0, y: 0, w: 6, h: 2 }, md: { x: 0, y: 5, w: 8, h: 2 } } }];
  expect(layoutForBreakpoint(ws, "md", () => [1, 1]).get("a")).toEqual({ x: 0, y: 5, w: 8, h: 2 });
});
test("an lg rect wider than 12 from a bad payload is clamped", () => {
  expect(layoutForBreakpoint([w("a", { x: 10, y: 0, w: 6, h: 2 })], "lg", () => [1, 1]).get("a")).toEqual({ x: 6, y: 0, w: 6, h: 2 });
});
test("smOrder is the array order, not the visual order", () => {
  expect(smOrder([w("b", { x: 0, y: 5, w: 3, h: 1 }), w("a", { x: 0, y: 0, w: 3, h: 1 })])).toEqual(["b", "a"]);
});

// session.test.ts
test("add places the widget in the first free spot with the default size", () => {
  const s = editReducer(state([]), { type: "add", def: COUNTER, id: "n1", bp: "lg" });
  expect(s.widgets[0].layout.lg).toEqual({ x: 0, y: 0, w: 3, h: 2 }); expect(s.dirty).toBe(true); expect(s.past).toHaveLength(1);
});
test("add at a drop cell honours it and pushes colliding widgets", () => {
  const s = editReducer(state([w("a", { x: 0, y: 0, w: 6, h: 2 })]), { type: "add", def: COUNTER, id: "n1", at: { x: 0, y: 0 }, bp: "lg" });
  expect(s.widgets.find((x) => x.id === "n1")!.layout.lg).toMatchObject({ x: 0, y: 0 });
  expect(s.widgets.find((x) => x.id === "a")!.layout.lg.y).toBe(2);
});
test("undo and redo restore exactly, redo is cleared by a new action", () => {
  let s = editReducer(state([w1]), { type: "move", id: "w1", to: { x: 3, y: 0 }, bp: "lg" });
  s = editReducer(s, { type: "undo" }); expect(s.widgets[0].layout.lg.x).toBe(0);
  s = editReducer(s, { type: "redo" }); expect(s.widgets[0].layout.lg.x).toBe(3);
  s = editReducer(editReducer(s, { type: "undo" }), { type: "remove", id: "w1" }); expect(s.future).toHaveLength(0);
});
test("keyboard moves within 800 ms coalesce into one undo step, later ones do not", () => {
  let s = state([w1]); for (const t of [0, 100, 200]) s = editReducer(s, { type: "move", id: "w1", to: { x: t / 100 + 1, y: 0 }, bp: "lg", now: t, coalesce: true });
  expect(s.past).toHaveLength(1);
  s = editReducer(s, { type: "move", id: "w1", to: { x: 9, y: 0 }, bp: "lg", now: 5000, coalesce: true }); expect(s.past).toHaveLength(2);
});
test("the undo stack is capped at 100", () => {
  let s = state([w1]);
  for (let i = 0; i < 120; i++) s = editReducer(s, { type: "rename", name: `n${i}` });
  expect(s.past).toHaveLength(100);
  expect(s.past[0].name).toBe("n19");
});
test("select never creates history; settings, rename, duplicate and remove each create exactly one", () => {
  let s = editReducer(state([w1]), { type: "select", id: "w1" });
  expect(s.past).toHaveLength(0);
  const actions: EditAction[] = [{ type: "settings", id: "w1", settings: { limit: 3 }, titleOverride: "X" }, { type: "rename", name: "N" }, { type: "duplicate", id: "w1", newId: "w2" }, { type: "remove", id: "w2" }];
  for (const [i, a] of actions.entries()) { s = editReducer(s, a); expect(s.past).toHaveLength(i + 1); }
});
test("duplicate gets the new id, same settings, first free spot, and a deep-copied settings object", () => {
  const src = { ...w1, settings: { columns: ["tn"] } };
  const dup = editReducer(state([src]), { type: "duplicate", id: "w1", newId: "w9" }).widgets.find((x) => x.id === "w9")!;
  expect(dup.settings).toEqual({ columns: ["tn"] });
  expect(dup.settings).not.toBe(src.settings);
  expect(dup.layout.lg).not.toEqual(src.layout.lg);
});
test("editing at md writes layout.md and leaves lg untouched", () => {
  const s = editReducer(state([w1]), { type: "move", id: "w1", to: { x: 4, y: 3 }, bp: "md" });
  expect(s.widgets[0].layout.md).toMatchObject({ x: 4, y: 3 });
  expect(s.widgets[0].layout.lg).toEqual(w1.layout.lg);
});
test("shift moves a widget one place in phone order and is a no-op at the ends", () => {
  const s = editReducer(state([w("a"), w("b"), w("c")]), { type: "shift", id: "b", dir: "up" });
  expect(s.widgets.map((x) => x.id)).toEqual(["b", "a", "c"]);
  const top = editReducer(s, { type: "shift", id: "b", dir: "up" });
  expect(top.widgets.map((x) => x.id)).toEqual(["b", "a", "c"]);
  expect(top.past).toHaveLength(s.past.length);
});
test("reset replaces everything, clears history and sets baseVersion", () => {
  const s = editReducer(editReducer(state([w1]), { type: "rename", name: "X" }), { type: "reset", name: "Neu", widgets: [w("z")], version: 7 });
  expect(s).toMatchObject({ name: "Neu", baseVersion: 7, dirty: false, past: [], future: [] });
  expect(s.widgets.map((x) => x.id)).toEqual(["z"]);
});

// keyboard.test.ts
test("keys", () => {
  expect(keyToAction({ key: "ArrowRight", shiftKey: false })).toEqual({ kind: "move", dx: 1, dy: 0 });
  expect(keyToAction({ key: "ArrowDown", shiftKey: true })).toEqual({ kind: "resize", dx: 0, dy: 1 });
  expect(keyToAction({ key: "Enter", shiftKey: false })).toEqual({ kind: "settings" });
  expect(keyToAction({ key: "a", shiftKey: false })).toBeNull();
});
```

- [ ] **Step 2: Run** `cd frontend && pnpm test --run src/dashboard/layout` → FAIL (modules missing).
- [ ] **Step 3: Implement** per D8 (pure functions, no mutation of inputs — tests assert the input arrays are unchanged via `structuredClone` comparison in one extra test).
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run` → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): grid engine, breakpoint mapping and edit-session reducer`.

---

### Task 9: API layer, types, demo and e2e mocks (frontend-only)

**Files:**
- Create: `frontend/src/lib/dashboardsApi.ts`, `frontend/src/lib/dashboardsApi.test.ts`, `frontend/src/demo/dashboardsMock.ts`, `frontend/src/demo/dashboardsMock.test.ts`
- Modify: `frontend/src/demo/mockData.ts` (route `/api/v1/dashboards*`, `/api/v1/dashboard-widget-types*`, `/api/v1/admin/dashboard-templates*`, `/api/v1/saved-searches` stays untouched), `frontend/e2e/fixtures/mock-api.ts` (same routes returning the demo default dashboard so `/agent` renders), `frontend/src/lib/useSSE.ts` (`subscribeSSE` bus)

**Interfaces:**
- Consumes: regenerated `Schemas` from Tasks 4–7.
- Produces:

```ts
export type DashboardTab = Schemas["DashboardTabOut"]; export type Dashboard = Schemas["DashboardOut"];
export type DashboardWidgetRow = Schemas["WidgetOut"]; export type WidgetResult = Schemas["WidgetDataOut"];
export type DashboardTemplate = Schemas["TemplateOut"];
export class DashboardConflictError extends Error { currentVersion: number }
export const dashboardsApi = {
  list(signal?): Promise<DashboardTab[]>, ensureStart(): Promise<Dashboard>, get(id, signal?): Promise<Dashboard>,
  create(body: { name: string; icon?: string | null; copy_from?: number | null }): Promise<Dashboard>,
  save(id, body: Schemas["DashboardSaveIn"]): Promise<Dashboard>,        // 409 -> throws DashboardConflictError
  patchWidgetSettings(id, widgetId, body: { settings: Record<string, unknown>; version: number }): Promise<DashboardWidgetRow>,
  remove(id): Promise<void>, order(ids: number[]): Promise<void>, share(id, groupIds: number[]): Promise<Dashboard>,
  data(id, widgetIds?: string[], signal?): Promise<{ widgets: Record<string, WidgetResult> }>,
  widgetData(id, widgetId, signal?): Promise<WidgetResult>,
  previewData(key: string, settings: Record<string, unknown>, signal?): Promise<WidgetResult>,
  widgetTypes(signal?): Promise<Schemas["WidgetTypeOut"][]>,
  templates: { list(), create(body), update(id, body), remove(id) },
};
export const dashboardKeys = { all: ["dashboards"] as const, list: () => [...] , one: (id) => [...], data: (id) => [...], widget: (id, wid) => [...], preview: (key, hash) => [...] };
// useSSE.ts
export function subscribeSSE(listener: (m: SSEMessage) => void): () => void;   // handleSSEMessage calls every listener with the parsed message after its own invalidations
```

- [ ] **Step 1: Write the failing tests**

```ts
test("save maps 409 to DashboardConflictError with the current version", async () => {
  mockFetch(409, { detail: { message: "Dieses Dashboard wurde inzwischen geändert.", current_version: 7 } });
  await expect(dashboardsApi.save(1, body)).rejects.toMatchObject({ name: "DashboardConflictError", currentVersion: 7 });
});
test("data joins widget ids into one query parameter and omits it when none", async () => {
  await dashboardsApi.data(3, ["a", "b"]);
  expect(lastUrl()).toBe("/api/v1/dashboards/3/data?widgets=a,b");
  await dashboardsApi.data(3);
  expect(lastUrl()).toBe("/api/v1/dashboards/3/data");
});
test("previewData posts the settings to /dashboard-widget-types/{key}/data", async () => {
  await dashboardsApi.previewData("core.counter", { tone: "ok" });
  expect(lastCall()).toMatchObject({ method: "POST", url: "/api/v1/dashboard-widget-types/core.counter/data", body: { settings: { tone: "ok" } } });
});
// dashboardsMock.test.ts
test("the demo store is stateful: save bumps the version, create adds a tab, ensureStart returns the default", async () => {
  const d = await demo.ensureStart();
  expect(d.widgets).toHaveLength(11);
  expect((await demo.save(d.id, { name: "X", icon: null, version: d.version, widgets: d.widgets.slice(0, 2) })).version).toBe(d.version + 1);
  await demo.create({ name: "Zweites" });
  expect((await demo.list()).map((t) => t.name)).toEqual(["X", "Zweites"]);
});
test("demo data providers return plausible shapes for counter, ticket-list, escalations, reminders, calendar, team", () => {
  expect(demoWidgetData("core.counter", { source: { kind: "preset", preset: "mine_open" } })).toMatchObject({ status: "ok", data: { value: expect.any(Number), spark: expect.any(Array) } });
});
// useSSE.test.ts (extend)
test("subscribeSSE listeners get parsed messages and can unsubscribe", () => {
  const seen: string[] = []; const off = subscribeSSE((m) => seen.push(m.type));
  handleSSEMessage(qc, JSON.stringify({ type: "ticket_changed", ticket_id: 1, event: "x" })); off();
  handleSSEMessage(qc, JSON.stringify({ type: "ticket_changed", ticket_id: 2, event: "x" })); expect(seen).toEqual(["ticket_changed"]);
});
```

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement** (the demo store holds the default dashboard built from a TS copy of D5; demo user's counters get a fixed `spark`/`delta` so screenshots are stable). **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` → PASS. **Step 5: Commit** — `feat(dashboard): frontend API layer, demo and e2e mocks, SSE bus`.

### Task 10: Widget contract wiring, chrome and the ten widgets (frontend-only)

**Needs plugin-system Task 13 committed** (`frontend/src/plugins/dashboardWidgets.ts`: `WidgetDefinition`, `WidgetProps`, `WidgetSize`, `WidgetCategory`, `DASHBOARD_WIDGETS`, `useDashboardWidgetDefinitions`). Needs Task 9 (`dashboardsApi` types).

**Files:**
- Create: `frontend/src/dashboard/definitions.ts`, `data.tsx` (context part only: `WidgetDataProvider`, `useWidgetData`), `chrome/WidgetFrame.tsx`, `chrome/WidgetSkeleton.tsx`, `chrome/WidgetError.tsx`, `chrome/WidgetUnavailable.tsx`, `chrome/Sparkline.tsx`, `widgets/{TicketListWidget,CounterWidget,QueueOverviewWidget,EscalationsWidget,RemindersWidget,RecentViewsWidget,CalendarAgendaWidget,TeamPresenceWidget,NotesWidget,LinksWidget}.tsx`, `widgets/index.ts` (the ten `defineWidget(...)` calls of the D9 table and `registerCoreWidgets` wiring), `legacy.ts` (moved from `DashboardPage.tsx`: `formatDurationSince`, `greetingKey`, `queueHasWork`, `selectQueueShortcuts` — signatures unchanged), matching `*.test.tsx`; i18n keys `dashboards.widgets.<name>.{title,description,empty}` and shared `dashboards.state.*` in `en.json` + `de.json` + propagation
- Modify: `frontend/src/components/agent/dashboard/DashboardTicketRow.tsx` (accept `density?: "compact" | "comfortable"` and `columns?: ColumnKey[]`, default = today's look), `frontend/src/routes/agent/DashboardPage.test.tsx` (the four helper `describe`s move to `frontend/src/dashboard/legacy.test.tsx` unchanged)

**Interfaces:**
- Consumes: P13 types (unchanged); `getLastViews` (`@/lib/lastViews`), `api.listQueues`, `api.getOnlineAgents`'s data shape via provider output, `MarkdownView`, `DashboardTicketRow`, `QueueShortcutCard`, `Avatar`.
- Produces: `defineWidget`, `registerCoreWidgets`, `settingsWithDefaults`, `galleryGroupOf` (D9 signatures); `useWidgetData<T>()`; `WidgetDataProvider({ value, children })` with `value: { status; data?; error?; refetch }`; `WidgetFrame({ title, link?, actions?, mode, children, testId })`; `WidgetError({ message, onRetry })`; `WidgetUnavailable({ typeKey, mode, onRemove? })`; `Sparkline({ points: number[], tone })`; `CORE_WIDGET_KEYS: readonly string[]` (the ten keys); `ColumnKey` (same literal list as the backend). Each widget component is `ComponentType<WidgetProps<S>>` and reads data via `useWidgetData()`; in `mode: "edit"` interactive elements are inert (`pointer-events-none`, links `tabIndex={-1}`), in `"preview"` additionally no links at all.

- [ ] **Step 1: Write the failing tests** (RTL; wrap in a `renderWidget(key, settings, data, mode)` helper that mounts `WidgetDataProvider` + a memory router + i18n)

```tsx
test("defineWidget maps the spec shape onto the shared WidgetDefinition", () => {
  const def = defineWidget({ key: "core.x", version: 2, title: "dashboards.widgets.x.title", category: "tickets", icon: "list",
    size: { default: [3, 2], min: [2, 1], max: [6, 4] }, settings: { type: "object", properties: {} }, data: { endpoint: "core.x" },
    refreshOn: ["ticket_changed"], component: () => null });
  expect(def).toMatchObject({ key: "core.x", owner: "core", version: 2, titleKey: "dashboards.widgets.x.title", settingsSchema: { type: "object", properties: {} }, dataEndpoint: "core.x", refreshOn: ["ticket_changed"] });
  expect(defineWidget({ ...base, data: null }).dataEndpoint).toBeNull();
});
test("registerCoreWidgets registers exactly the ten core keys once, with the D9 sizes, and is idempotent", () => {
  DASHBOARD_WIDGETS.clearForTests(); registerCoreWidgets(); registerCoreWidgets();
  expect(DASHBOARD_WIDGETS.list().map((w) => w.key).sort()).toEqual([...CORE_WIDGET_KEYS].sort());
  expect(DASHBOARD_WIDGETS.get("core.counter")!.size).toEqual({ default: [3, 2], min: [2, 2], max: [4, 3] });
  expect(DASHBOARD_WIDGETS.owner("core.counter")).toBe("core");
});
test("galleryGroupOf: tickets and team map to themselves, everything else is 'Für mich'", () => {
  expect(["core.ticket-list", "core.reminders", "core.team-presence", "core.notes"].map((k) => galleryGroupOf(DASHBOARD_WIDGETS.get(k)!))).toEqual(["tickets", "me", "team", "team"]);
  expect(galleryGroupOf({ ...anyDef, category: "reports" })).toBe("me");
});
test("settingsWithDefaults migrates old versions, fills defaults and falls back on garbage", () => {
  const def = { ...anyDef, version: 2, settingsSchema: { type: "object", properties: { limit: { type: "integer", default: 8 } } }, migrate: (o: any) => ({ limit: o.max ?? 8 }) };
  expect(settingsWithDefaults(def, { max: 3 }, 1)).toEqual({ limit: 3 });
  expect(settingsWithDefaults(def, {}, 2)).toEqual({ limit: 8 });
  expect(settingsWithDefaults(def, "garbage", 2)).toEqual({ limit: 8 });
});
test("useWidgetData outside a provider reports status 'none' instead of throwing", () => {
  const { result } = renderHook(() => useWidgetData());
  expect(result.current.status).toBe("none");
  expect(result.current.data).toBeUndefined();
});

// chrome
test("WidgetError shows the message and calls only its own retry", () => {
  const onRetry = vi.fn();
  render(<WidgetError message="Diese Daten konnten nicht geladen werden." onRetry={onRetry} />);
  fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  expect(onRetry).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Diese Daten konnten nicht geladen werden.")).toBeVisible();
});
test("WidgetUnavailable names the missing type, keeps a remove button only in edit mode", () => {
  render(<WidgetUnavailable typeKey="org.example.weather" mode="edit" onRemove={rm} />);
  expect(screen.getByText("Widget nicht verfügbar")).toBeVisible(); expect(screen.getByText("org.example.weather")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Entfernen" })); expect(rm).toHaveBeenCalled();
  cleanup(); render(<WidgetUnavailable typeKey="org.example.weather" mode="view" />); expect(screen.queryByRole("button", { name: "Entfernen" })).toBeNull();
});
test("Sparkline draws a polyline scaled to 96x30 and nothing for fewer than two points", () => {
  const { container } = render(<Sparkline points={[1, 2, 3]} tone="neutral" />);
  expect(container.querySelector("polyline")!.getAttribute("points")!.split(" ")).toHaveLength(3);
  expect(container.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
  cleanup(); expect(render(<Sparkline points={[5]} tone="neutral" />).container.querySelector("polyline")).toBeNull();
});
test("a flat series does not divide by zero", () => {
  const pts = render(<Sparkline points={[4, 4, 4]} tone="neutral" />).container.querySelector("polyline")!.getAttribute("points")!.split(" ").map((p) => p.split(",").map(Number));
  expect(pts.flat().every(Number.isFinite)).toBe(true);
  expect(new Set(pts.map((p) => p[1])).size).toBe(1);
});

// widgets
test("counter shows label, value, delta and sparkline; fewer-is-better counters colour a drop green", () => {
  renderWidget("core.counter", { source: { kind: "preset", preset: "escalated" }, tone: "danger" }, { status: "ok", data: { value: 2, delta: -1, spark: [3, 3, 2] } });
  expect(screen.getByText("2")).toHaveClass("text-danger"); expect(screen.getByText("−1 seit gestern")).toHaveClass("text-success");
});
test("counter without history shows no delta text and no sparkline", () => {
  const { container } = renderWidget("core.counter", { source: { kind: "preset", preset: "mine_open" } }, { status: "ok", data: { value: 4, delta: null, spark: [4] } });
  expect(screen.getByText("4")).toBeVisible();
  expect(screen.queryByText(/seit gestern/)).toBeNull();
  expect(container.querySelector("polyline")).toBeNull();
});
test("counter links to the matching queue view", () => {
  renderWidget("core.counter", { source: { kind: "preset", preset: "mine_new" } }, { status: "ok", data: { value: 3, delta: 0, spark: [3, 3] } });
  const href = screen.getByRole("link", { name: /Meine neuen/ }).getAttribute("href")!;     // target = the queue view the old chip filtered to; reuse the QueuesPage search params
  expect(href).toContain("/agent/queues");
  expect(href).toContain("state_type=new");
});
test("ticket list renders the configured columns only, in order, and the density class", () => {
  renderWidget("core.ticket-list", { ...preset("mine_open"), columns: ["tn", "title", "queue"], density: "compact" }, { status: "ok", data: { items: [ticket(1)], total: 1 } });
  expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Ticket", "Titel", "Queue"]); expect(screen.getByTestId("ticket-list-table")).toHaveClass("density-compact");
});
test("ticket list: 'Alle 12 anzeigen' only when total > rows; empty state says what to do", () => {
  renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: rows(8), total: 12 } }); expect(screen.getByText("Alle 12 anzeigen")).toBeVisible();
  cleanup(); renderWidget("core.ticket-list", preset("mine_open"), { status: "ok", data: { items: [], total: 0 } }, "view", { canEdit: true });
  expect(screen.getByText(/Noch keine Tickets in dieser Suche/)).toBeVisible(); expect(screen.getByRole("button", { name: "Filter ändern" })).toBeVisible();
});
it.each(DATA_WIDGET_KEYS)("%s renders skeleton, error with retry, forbidden message and pending as skeleton", (key) => {
  const show = (st: WidgetDataState) => { cleanup(); return renderWidget(key, {}, st); };
  show({ status: "loading" });
  expect(screen.getByTestId("widget-skeleton")).toBeVisible();
  show({ status: "pending" });
  expect(screen.getByTestId("widget-skeleton")).toBeVisible();
  show({ status: "error", error: "Boom" });
  expect(screen.getByText("Boom")).toBeVisible();
  expect(screen.getByRole("button", { name: "Erneut versuchen" })).toBeVisible();
  show({ status: "forbidden", error: "Nein." });
  expect(screen.getByText("Nein.")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Erneut versuchen" })).toBeNull();
});
test("escalations show the elapsed time since the breach and an empty state at zero", () => {
  vi.setSystemTime(new Date("2026-10-10T12:00:00Z"));
  const epoch = Date.parse("2026-10-10T08:40:00Z") / 1000;
  renderWidget("core.escalations", {}, { status: "ok", data: { items: [{ ticket: ticket(1), epoch }], total: 1, oldest_epoch: epoch } });
  expect(screen.getByText("3h20m")).toBeVisible();
  cleanup();
  renderWidget("core.escalations", {}, { status: "ok", data: { items: [], total: 0, oldest_epoch: null } });
  expect(screen.getByText("Keine eskalierten Tickets.")).toBeVisible();
});
test("reminders colour overdue entries danger", () => {
  renderWidget("core.reminders", {}, { status: "ok", data: { items: [{ ticket: ticket(1), until: 1, overdue: true }, { ticket: ticket(2), until: 4e9, overdue: false }], total: 2 } });
  expect(screen.getByTestId("reminder-1-due")).toHaveClass("text-danger");
  expect(screen.getByTestId("reminder-2-due")).not.toHaveClass("text-danger");
});
test("calendar agenda lists times in the viewer's zone and links to the calendar", () => {
  setDisplayTimeZone("Europe/Berlin");
  renderWidget("core.calendar-agenda", {}, { status: "ok", data: { items: [occ({ title: "Rückruf", start_time: "2026-10-10T08:00:00Z" })] } });
  expect(screen.getByText("10:00")).toBeVisible();
  expect(screen.getByText("Rückruf")).toBeVisible();
  expect(screen.getByRole("link", { name: /Kalender/ })).toHaveAttribute("href", "/agent/calendar");
});
test("team presence shows avatars with initials and the online count", () => {
  renderWidget("core.team-presence", {}, { status: "ok", data: { online: [agent("Clara Lenz"), agent("Ben Krause")], total_online: 2 } });
  expect(screen.getByText("CL")).toBeVisible();
  expect(screen.getByText("Ben Krause")).toBeVisible();
  expect(screen.getByText("2 online")).toBeVisible();
});
test("queue overview uses the cached queue tree, hides empty queues when only_with_work, caps at limit", () => {
  seedQueryData(["queues"], [q(1, "Support", 38, 6), q(2, "Leer", 0, 0), q(3, "Buchhaltung", 9, 2)]);
  renderWidget("core.queue-overview", { limit: 1, only_with_work: true }, { status: "none" });
  expect(screen.getByText("Support")).toBeVisible();
  expect(screen.queryByText("Leer")).toBeNull();
  expect(screen.queryByText("Buchhaltung")).toBeNull();
});
test("recent views reads localStorage (getLastViews) and shows an empty state without entries", () => {
  localStorage.setItem("tiqora.lastViews", JSON.stringify([{ id: 5, tn: "2026100912000231", title: "VPN", at: 1 }]));
  renderWidget("core.recent-views", {}, { status: "none" });
  expect(screen.getByText("VPN")).toBeVisible();
  cleanup(); localStorage.clear();
  renderWidget("core.recent-views", {}, { status: "none" });
  expect(screen.getByText("Noch nichts angesehen.")).toBeVisible();
});
test("notes render sanitised markdown", () => {
  renderWidget("core.notes", { markdown: "**fett** <script>window.__x=1</script> [a](javascript:alert(1))" }, { status: "none" });
  expect(screen.getByText("fett").tagName).toBe("STRONG"); expect(document.querySelector("script")).toBeNull(); expect((window as any).__x).toBeUndefined();
  expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
});
test("links: renders only safe urls, external links open in a new tab with noopener noreferrer", () => {
  renderWidget("core.links", { items: [{ label: "Wiki", url: "https://example.org/wiki" }, { label: "Böse", url: "javascript:alert(1)" }, { label: "Intern", url: "/agent/queues" }] }, { status: "none" });
  const wiki = screen.getByRole("link", { name: "Wiki" }); expect(wiki).toHaveAttribute("target", "_blank"); expect(wiki).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.queryByText("Böse")).toBeNull(); expect(screen.getByRole("link", { name: "Intern" })).not.toHaveAttribute("target");
});
test("in edit mode nothing inside a widget is a tab stop or navigates", () => {
  renderWidget("core.links", { items: [{ label: "Wiki", url: "https://example.org" }] }, { status: "none" }, "edit");
  expect(screen.getByRole("link", { name: "Wiki", hidden: true })).toHaveAttribute("tabindex", "-1");
  expect(screen.getByTestId("widget-body")).toHaveClass("pointer-events-none");
});
```

- [ ] **Step 2: Run to verify it fails** — `cd frontend && pnpm test --run src/dashboard` → FAIL (modules missing).
- [ ] **Step 3: Implement** per D9/D10. Reuse `DashboardTicketRow`/`QueueShortcutCard` where the look matches; the ticket-list widget is a table (as in the mockup: Ticket / Kunde / Status / Fällig) built on the same row data; "Fällig" = nearest escalation epoch formatted via `formatCountdown` from `@/lib/status`. Counter `deltaTone`: counters with presets `escalated`, `ai_escalated`, `unowned_new` treat negative as good. All colours via existing tokens (`text-danger`, `text-success`, `text-accent`, `text-muted`, `border-hairline`, `bg-surface`). Strings through i18n; German first.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (after `propagate-i18n-keys.mjs`) → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): widget contract wiring, widget chrome and the ten phase-1 widgets`.

---

### Task 11: Host page — tabs, view grid, data hook, SSE, phone view (frontend-only)

**Needs plugin-system Task 13 committed** (`useDashboardWidgetDefinitions`); needs Tasks 8–10.

**Files:**
- Create: `frontend/src/dashboard/DashboardGrid.tsx`, `frontend/src/dashboard/tabs/DashboardTabs.tsx`, `frontend/src/dashboard/useGridBreakpoint.ts`, `frontend/src/dashboard/useSelectedDashboard.ts`, tests for each
- Modify: `frontend/src/dashboard/data.tsx` (add `useDashboardData`, `usePreviewData`), `frontend/src/routes/agent/DashboardPage.tsx` (rewrite as host; keep `export function DashboardPage()` and `data-testid="dashboard"` on the root), `frontend/src/routes/agent/DashboardPage.test.tsx` (rewrite), `frontend/src/router.tsx` (`agentIndexRoute.validateSearch` accepting `d?: number`, following the existing `validateSearch` patterns — router search values are JSON-parsed, skill `tanstack-router-search-params-json-parsed`), `frontend/src/plugins/slots.tsx` + its test (remove `PluginWidgetsSection` **if plugin Task 14 has landed**; plugin widgets now come through `DASHBOARD_WIDGETS`), `frontend/e2e/screenshots.spec.ts` (no code change expected; note the changed home page for CI screenshots), i18n keys `dashboards.tabs.*`, `dashboards.view.*`

**Interfaces:**
- Consumes: Tasks 8–10; `dashboardsApi`; `useAuth`; `subscribeSSE`.
- Produces:

```ts
export function useDashboardData(dashboardId: number, widgets: EditWidget[], opts: { enabled: boolean }):
  { byWidget: Record<string, WidgetDataState>; refetch: (widgetId: string) => void };
type WidgetDataState = { status: "loading" | "ok" | "pending" | "error" | "forbidden" | "none"; data?: unknown; error?: string };
export function usePreviewData(typeKey: string, settings: Record<string, unknown>, opts?: { enabled?: boolean; debounceMs?: number }): WidgetDataState & { refetch: () => void };
export function useGridBreakpoint(ref: RefObject<HTMLElement>): Breakpoint;     // ResizeObserver; breakpointForWidth(contentRect.width)
export function useSelectedDashboard(tabs: DashboardTab[] | undefined, search: { d?: number }): { selectedId: number | null; select: (id: number) => void };
   // order of preference: ?d= (if visible or the user is admin and it exists) -> localStorage "tiqora.dashboard.last" -> first tab; storage errors are swallowed
export function DashboardGrid(props: { widgets: EditWidget[]; mode: "view" | "edit"; breakpoint: Breakpoint; dashboardId: number; canEdit: boolean;
  data: Record<string, WidgetDataState>; onRetry: (id: string) => void; edit?: EditHandlers }): JSX.Element;
```

Behaviour: data hook issues **one** `GET /dashboards/{id}/data` per dashboard view (query key `dashboardKeys.data(id)`), then one `GET …/widgets/{wid}/data` for each widget the batch returned `pending` (retry 2 s, then every 5 s up to 6 times, then error "Das dauert zu lange. Erneut versuchen."); widgets without a data entry whose definition has `dataEndpoint === null` get `status: "none"`; widgets whose `type_key` has no registered definition render `WidgetUnavailable`. Refresh: `subscribeSSE` — for each message type collect the widget ids whose definition `refreshOn` contains it; debounce 2 s; fetch `data(id, thoseIds)` and merge into the cache entry with `setQueryData`; `interval:N` entries start one `setInterval` per distinct N (cleared on unmount, paused when `document.hidden`). With `enabled: false` (edit mode) no requests are made and cached data stays.

- [ ] **Step 1: Write the failing tests**

```tsx
// DashboardPage.test.tsx (mocked dashboardsApi; real DashboardGrid)
test("first visit: empty tab list triggers ensure-start and renders the default dashboard", async () => {
  api.list.mockResolvedValue([]); api.ensureStart.mockResolvedValue(DEFAULT_DASHBOARD);
  render(<DashboardPage />); expect(await screen.findByRole("tab", { name: "Mein Start" })).toHaveAttribute("aria-selected", "true");
  expect(api.ensureStart).toHaveBeenCalledTimes(1); expect(screen.getByTestId("dashboard-grid").children).toHaveLength(11);
});
test("greeting and date stay above the grid", async () => {
  vi.setSystemTime(new Date("2026-10-10T07:00:00Z"));                      // 09:00 in Europe/Berlin -> morning
  render(<DashboardPage />);
  const h1 = await screen.findByRole("heading", { level: 1 });
  expect(h1).toHaveTextContent("Guten Morgen, Anna");
  expect(screen.getByText(/Samstag, 10. Oktober 2026/)).toBeVisible();
  expect(h1.compareDocumentPosition(screen.getByTestId("dashboard-grid")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
test("tabs: roving arrow-key focus, aria-selected, '+' button label, locked template tab shows a lock hint, shared tab shows the owner", async () => {
  fireEvent.keyDown(screen.getByRole("tab", { name: "Mein Start" }), { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: /Team Support/ })).toHaveFocus(); expect(screen.getByRole("button", { name: "Neues Dashboard" })).toBeVisible();
});
test("selecting a tab loads that dashboard and remembers it in localStorage; a broken storage is ignored", async () => {
  await user.click(await screen.findByRole("tab", { name: "Team Support" }));
  expect(api.get).toHaveBeenLastCalledWith(2, expect.anything());
  expect(localStorage.getItem("tiqora.dashboard.last")).toBe("2");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  await user.click(screen.getByRole("tab", { name: "Mein Start" }));
  expect(screen.getByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
});
test("?d=<id> selects that dashboard; an id the user may not see falls back to the first tab", async () => {
  renderPage({ d: 2 });
  expect(await screen.findByRole("tab", { name: "Team Support", selected: true })).toBeVisible();
  cleanup();
  api.get.mockRejectedValueOnce(notFound());
  renderPage({ d: 99 });
  expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
});
test("view mode is clean: no handles, no toolbar; 'Anpassen' only when can_edit; 'Teilen' only when can_share", async () => {
  expect(screen.queryByTestId(/widget-.*-handle/)).toBeNull(); expect(screen.getByRole("button", { name: "Anpassen" })).toBeVisible();
  mockDashboard({ can_edit: false, can_share: false, kind: "shared" }); expect(screen.queryByRole("button", { name: "Anpassen" })).toBeNull(); expect(screen.queryByRole("button", { name: "Teilen" })).toBeNull();
});
test("widgets are placed from layout.lg on a wide container: grid-column/row styles", async () => {
  const el = screen.getByTestId("widget-w-counter-1"); expect(el.style.gridColumn).toBe("1 / span 3"); expect(el.style.gridRow).toBe("1 / span 2");
});
test("md container derives the 8-column layout; sm renders one column in array order with counters in a 2-column group", async () => {
  setContainerWidth(700); expect(await screen.findByTestId("dashboard-grid")).toHaveAttribute("data-breakpoint", "md");
  setContainerWidth(390); const grid = screen.getByTestId("dashboard-grid"); expect(grid).toHaveAttribute("data-breakpoint", "sm");
  expect(Array.from(grid.querySelectorAll("[data-type]")).map((e) => e.getAttribute("data-type"))).toEqual(DEFAULT_TYPES_IN_ORDER);
  expect(screen.getByTestId("counter-group")).toHaveClass("grid-cols-2");
});
test("exactly one batch request per dashboard view, none per widget", async () => { expect(api.data).toHaveBeenCalledTimes(1); expect(api.widgetData).not.toHaveBeenCalled(); });
test("a pending widget is fetched on its own and the rest stay rendered", async () => {
  api.data.mockResolvedValue({ widgets: { a: ok(), b: { status: "pending" } } }); api.widgetData.mockResolvedValue(ok());
  render(...); expect(await screen.findByTestId("widget-b-skeleton")).toBeVisible(); await waitFor(() => expect(api.widgetData).toHaveBeenCalledWith(1, "b", expect.anything()));
  expect(screen.getByTestId("widget-a")).toBeVisible();
});
test("slow provider does not block the page: error tile for one widget, 'Erneut versuchen' refetches only that one", async () => {
  api.data.mockResolvedValue({ widgets: { a: ok(), b: { status: "error", error: "Diese Daten konnten nicht geladen werden." } } });
  fireEvent.click(within(screen.getByTestId("widget-b")).getByRole("button", { name: "Erneut versuchen" }));
  await waitFor(() => expect(api.widgetData).toHaveBeenCalledTimes(1)); expect(api.data).toHaveBeenCalledTimes(1);
});
test("SSE ticket_changed refreshes only widgets listing it in refreshOn, debounced to one request", async () => {
  vi.useFakeTimers(); emitSSE({ type: "ticket_changed", ticket_id: 1, event: "x" }); emitSSE({ type: "ticket_changed", ticket_id: 2, event: "x" });
  await vi.advanceTimersByTimeAsync(2100); expect(api.data).toHaveBeenLastCalledWith(1, expect.arrayContaining(["w-list", "w-counter"]), expect.anything());
  expect(api.data.mock.calls.at(-1)![1]).not.toContain("w-recent"); expect(api.data).toHaveBeenCalledTimes(2);
});
test("interval:60 widgets refetch on their timer and pause while the tab is hidden", async () => {
  vi.useFakeTimers();
  render(<DashboardPage />); await flush();
  const calls = api.data.mock.calls.length;
  await vi.advanceTimersByTimeAsync(60_000);
  expect(api.data.mock.calls.length).toBe(calls + 1);
  expect(api.data.mock.calls.at(-1)![1]).toEqual(["w-esc"]);               // only the escalations widget lists interval:60
  Object.defineProperty(document, "hidden", { value: true, configurable: true });
  await vi.advanceTimersByTimeAsync(120_000);
  expect(api.data.mock.calls.length).toBe(calls + 1);
});
test("a widget type that is not registered renders the unavailable tile in its slot", async () => {
  mockDashboard({ widgets: [wire("g1", "org.example.gone", { lg: { x: 3, y: 1, w: 4, h: 2 } })] });
  render(<DashboardPage />);
  const tile = await screen.findByTestId("widget-g1");
  expect(within(tile).getByText("Widget nicht verfügbar")).toBeVisible();
  expect(tile.style.gridColumn).toBe("4 / span 4");
  expect(tile.style.gridRow).toBe("2 / span 2");
});
test("a stored layout wider than 12 columns is clamped, not overflowing the grid", async () => {
  mockDashboard({ widgets: [wire("g2", "core.links", { lg: { x: 10, y: 0, w: 6, h: 2 } })] });
  render(<DashboardPage />);
  expect((await screen.findByTestId("widget-g2")).style.gridColumn).toBe("7 / span 6");
});
test("plugin widgets registered in DASHBOARD_WIDGETS render in the grid with their component", async () => {
  DASHBOARD_WIDGETS.register(pluginDef({ key: "org.example.weather", component: () => <p>Wetter Bonn</p> }), "org.example");
  mockDashboard({ widgets: [wire("p1", "org.example.weather", { lg: { x: 0, y: 0, w: 4, h: 2 } })] });
  render(<DashboardPage />);
  expect(await screen.findByText("Wetter Bonn")).toBeVisible();
});
test("an admin opening a template by ?d= sees the template banner; a viewer of a locked one sees the lock hint and no Anpassen", async () => {
  mockDashboard({ kind: "template", can_edit: true, locked: false });
  renderPage({ d: 9 });
  expect(await screen.findByText(/Vorlage/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Anpassen" })).toBeVisible();
  cleanup();
  mockDashboard({ kind: "template", can_edit: false, locked: true });
  renderPage({});
  expect(await screen.findByText(/verbindlich/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Anpassen" })).toBeNull();
});
test("a dashboard that fails to load shows an error with retry while the tabs stay usable", async () => {
  api.get.mockRejectedValueOnce(new Error("boom"));
  render(<DashboardPage />);
  expect(await screen.findByText("Das Dashboard konnte nicht geladen werden.")).toBeVisible();
  api.get.mockResolvedValue(DEFAULT_DASHBOARD);
  await user.click(screen.getByRole("button", { name: "Erneut versuchen" }));
  expect(await screen.findByTestId("dashboard-grid")).toBeVisible();
  expect(screen.getAllByRole("tab").length).toBeGreaterThan(0);
});
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Container query via `ResizeObserver` (mock it in tests with a helper `setContainerWidth`); CSS: `grid-template-columns: repeat(12, minmax(0, 1fr))` (md: 8), `grid-auto-rows: 64px`, `gap: 16px`; widget `style={{ gridColumn: \`${x+1} / span ${w}\`, gridRow: \`${y+1} / span ${h}\` }}`; RTL handled by the browser's grid direction. Remove `PluginWidgetsSection` usage (see Files). `DashboardPage` keeps the greeting block (reusing `greetingKey`, `displayTimeZone`).
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` and a demo check: `cd frontend && VITE_DEMO=1 pnpm dev`, Playwright at 1280 and 390 px, light and dark: default dashboard renders like the approved `Main`/`Mobile` mockups (counters with sparkline, lists, no handles) → PASS; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** — `feat(dashboard): home page as widget dashboards — tabs, grid, batched data, live refresh`.

---

### Task 12: Edit mode — toolbar, drag and resize, keyboard, draft, save and conflict, phone controls (frontend-only)

**Needs plugin-system Task 13 committed** (via Task 11); needs Tasks 8 and 11.

**Files:**
- Create: `frontend/src/dashboard/edit/EditToolbar.tsx`, `edit/useDragResize.ts`, `edit/draft.ts`, `edit/useEditSession.ts`, `edit/PhoneControls.tsx`, `edit/ConflictDialog.tsx`, tests for each; i18n keys `dashboards.edit.*`
- Modify: `frontend/src/dashboard/DashboardGrid.tsx` (edit rendering via `edit` prop), `frontend/src/routes/agent/DashboardPage.tsx` (Anpassen toggles the session, toolbar replaces the header buttons), `frontend/src/dashboard/data.tsx` (edit-mode data via `usePreviewData`)

**Interfaces:**
- Consumes: Task 8 reducer/engine, Task 11 grid/page, `dashboardsApi.save`, `DashboardConflictError`, `ConfirmDialog` (`@/components/ui/ConfirmDialog`).
- Produces:

```ts
// draft.ts — localStorage, every access in try/catch
export interface Draft { baseVersion: number; savedAt: number; name: string; widgets: EditWidget[] }
export const draftKey = (userId: number, dashboardId: number) => `tiqora.dashboard.draft.${userId}.${dashboardId}`;
export function readDraft(userId: number, dashboardId: number, baseVersion: number): Draft | null;   // other baseVersion or malformed -> removes the entry, returns null
export function writeDraft(userId: number, dashboardId: number, draft: Draft): boolean;               // false when storage is blocked/full
export function clearDraft(userId: number, dashboardId: number): void;
// useEditSession.ts
export function useEditSession(dashboard: Dashboard, userId: number): {
  state: EditState; dispatch: (a: EditAction) => void; draftSavedAt: number | null;
  restoreAvailable: Draft | null; restore: () => void; dropDraft: () => void;
  save: () => Promise<"saved" | "conflict" | "invalid">; invalidWidgetId: string | null;
};
// useDragResize.ts
export function useDragResize(opts: { gridRef: RefObject<HTMLElement>; cols: number; items: LayoutItem[]; onCommit: (a: { kind: "move"; id: string; to: {x:number;y:number} } | { kind: "resize"; id: string; size: {w:number;h:number} }) => void }):
  { onHandlePointerDown(id: string): PointerEventHandler; onResizePointerDown(id: string): PointerEventHandler; drag: { id: string; kind: "move" | "resize"; ghost: { dx: number; dy: number }; target: Rect; preview: Map<string, Rect> } | null };
```

Behaviour: pointer capture on the handle; during the gesture the dragged widget follows the pointer with `transform: translate()` (and the lifted style of D10), `preview` is the layout after `moveItem`/`resizeItem` at the snapped cell (other widgets shift live), the dashed drop target is drawn at `target`; `pointerup` commits **one** action (one undo step), `pointercancel`/`Escape` aborts with no action; no motion transform when `prefers-reduced-motion`. Autosave: 500 ms after the last change `writeDraft`; toolbar shows "Entwurf gesichert · HH:MM" (viewer's display zone, `formatDateTime`) once a write succeeded, nothing when storage is blocked. `save`: `dashboardsApi.save(id, { name, icon, version: baseVersion, widgets: state.widgets })` with widgets in array order; success → `clearDraft`, invalidate `dashboardKeys`, leave edit mode; `DashboardConflictError` → returns `"conflict"` (dialog "Dieses Dashboard wurde inzwischen geändert" with "Neu laden" (drops the session, keeps the draft until the next successful save, reloads) and "Weiter bearbeiten"); 422 with `path` `widgets[i]…` → `invalidWidgetId = widgets[i].id`, that widget gets a danger outline and its drawer opens (Task 14).

- [ ] **Step 1: Write the failing tests**

```tsx
test("Anpassen shows the dark toolbar, the column guide, handles and the keyboard hint; view-only chrome disappears", async () => {
  fireEvent.click(screen.getByRole("button", { name: "Anpassen" }));
  const bar = screen.getByRole("toolbar", { name: "Bearbeiten" }); expect(bar).toHaveTextContent("Mein Start bearbeiten");
  expect(screen.getByTestId("dashboard-grid")).toHaveAttribute("data-editing", "true"); expect(screen.getAllByTestId(/-handle$/)).toHaveLength(11);
  expect(screen.getByText(/Pfeiltasten verschieben, Umschalt \+ Pfeiltasten ändern die Größe, Eingabe öffnet die Einstellungen/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Rückgängig" })).toBeDisabled(); expect(screen.getByRole("button", { name: "Wiederholen" })).toBeDisabled();
});
test("dragging a widget by its handle commits one move to the snapped cell and one undo step", async () => {
  mockGridRect({ left: 0, top: 0, width: 1200 }); const handle = screen.getByTestId("widget-w-list-handle");
  firePointer(handle, "pointerdown", { clientX: 10, clientY: 10 }); firePointer(window, "pointermove", { clientX: 10 + 2 * 101, clientY: 10 + 80 }); 
  expect(screen.getByTestId("drop-target")).toHaveStyle({ gridColumn: "3 / span 6", gridRow: "6 / span 5" });         // snapped preview
  expect(screen.getByTestId("widget-w-list")).toHaveStyle({ opacity: "0.96" });                                           // lifted ghost
  firePointer(window, "pointerup", {}); expect(store.past).toHaveLength(1); expect(store.widgets.find(byId("w-list"))!.layout.lg).toMatchObject({ x: 2, y: 5 });
});
test("Escape during a drag aborts without history", async () => {
  firePointer(handle, "pointerdown", { clientX: 10, clientY: 10 });
  firePointer(window, "pointermove", { clientX: 300, clientY: 300 });
  fireEvent.keyDown(window, { key: "Escape" });
  firePointer(window, "pointerup", {});
  expect(store.past).toHaveLength(0);
  expect(screen.queryByTestId("drop-target")).toBeNull();
});
test("resizing via the corner handle respects min and max and pushes the widget below", async () => {
  mockGridRect({ left: 0, top: 0, width: 1200 });
  const corner = screen.getByTestId("widget-w-counter-1-resize");          // counter: min [2,2], max [4,3]
  firePointer(corner, "pointerdown", { clientX: 300, clientY: 130 });
  firePointer(window, "pointermove", { clientX: 300 + 6 * 101, clientY: 130 + 5 * 80 });
  firePointer(window, "pointerup", {});
  expect(store.widgets[0].layout.lg).toMatchObject({ w: 4, h: 3 });          // clamped to max
  expect(store.widgets.find(byId("w-esc"))!.layout.lg.y).toBeGreaterThanOrEqual(3);   // pushed down, no overlap
});
test("keyboard: arrows move one cell, Shift+arrows resize, Enter opens the settings, with a live announcement", async () => {
  const w = screen.getByTestId("widget-w-counter-1"); w.focus();
  await user.keyboard("{ArrowRight}"); expect(store.widgets[0].layout.lg.x).toBe(1);
  await user.keyboard("{Shift>}{ArrowDown}{/Shift}"); expect(store.widgets[0].layout.lg.h).toBe(3);
  expect(screen.getByRole("status")).toHaveTextContent(/ist jetzt in Spalte 2, Zeile 1, 3 mal 3/);
  await user.keyboard("{Enter}"); expect(screen.getByRole("dialog", { name: /Einstellungen/ })).toBeVisible();
});
test("per-widget settings, duplicate and remove buttons act on the session only (nothing is saved yet)", async () => {
  fireEvent.click(screen.getByRole("button", { name: "Duplizieren", ...forWidget("w-list") })); expect(store.widgets).toHaveLength(12);
  fireEvent.click(screen.getByRole("button", { name: "Entfernen", ...forWidget("w-list") })); expect(store.widgets).toHaveLength(11);
  expect(api.save).not.toHaveBeenCalled();
});
test("undo and redo buttons and Ctrl/Cmd+Z / Shift+Ctrl/Cmd+Z mirror the reducer", async () => {
  moveFirst();
  await user.click(screen.getByRole("button", { name: "Rückgängig" }));
  expect(store.widgets[0].layout.lg.x).toBe(0);
  await user.keyboard("{Control>}{Shift>}z{/Shift}{/Control}");
  expect(store.widgets[0].layout.lg.x).toBe(1);
  await user.keyboard("{Control>}z{/Control}");
  expect(store.widgets[0].layout.lg.x).toBe(0);
});
test("the draft is written 500 ms after the last change and the toolbar shows 'Entwurf gesichert · 10:42'", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-10T10:42:00Z")); move(); await vi.advanceTimersByTimeAsync(499); expect(localStorage.getItem(KEY)).toBeNull();
  await vi.advanceTimersByTimeAsync(2); expect(JSON.parse(localStorage.getItem(KEY)!).baseVersion).toBe(3); expect(screen.getByText(/Entwurf gesichert · /)).toBeVisible();
});
test("blocked localStorage: editing works, no draft text, no crash", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.useFakeTimers(); enterEdit(); moveFirst();
  await vi.advanceTimersByTimeAsync(600);
  expect(store.widgets[0].layout.lg.x).toBe(1);                              // the edit itself worked
  expect(screen.queryByText(/Entwurf gesichert/)).toBeNull();                // no false "saved" claim
  expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
});
test("re-entering edit mode offers to restore a draft of the same base version and silently drops one of an older version", async () => {
  seedDraft({ baseVersion: 3 }); enterEdit(); fireEvent.click(await screen.findByRole("button", { name: "Entwurf von 10:42 wiederherstellen" })); expect(store.widgets).toEqual(draft.widgets);
  leaveEdit(); seedDraft({ baseVersion: 2 }); enterEdit(); expect(screen.queryByText(/Entwurf von/)).toBeNull(); expect(localStorage.getItem(KEY)).toBeNull();
});
test("Verwerfen with changes asks first, then clears the draft and restores the saved state; without changes it leaves directly", async () => {
  await user.click(screen.getByRole("button", { name: "Verwerfen" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(screen.queryByRole("toolbar")).toBeNull();                         // a clean session leaves at once
  enterEdit(); moveFirst();
  await user.click(screen.getByRole("button", { name: "Verwerfen" }));
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Änderungen verwerfen" }));
  expect(localStorage.getItem(KEY)).toBeNull();
  expect(screen.getByTestId("widget-w-counter-1").style.gridColumn).toBe("1 / span 3");
});
test("Speichern sends widgets in array order with the base version, clears the draft and returns to view mode", async () => {
  expect(api.save).toHaveBeenCalledWith(1, { name: "Mein Start", icon: null, version: 3, widgets: store.widgets.map(toWire) }); expect(localStorage.getItem(KEY)).toBeNull();
});
test("conflict keeps the session and the draft and offers reload or continue", async () => {
  api.save.mockRejectedValue(new DashboardConflictError(4)); fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByText(/inzwischen geändert/)).toBeVisible(); expect(localStorage.getItem(KEY)).not.toBeNull(); expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
});
test("a 422 with a widget path outlines that widget and opens its settings", async () => {
  api.save.mockRejectedValue(apiError(422, { message: "Der Wert muss zwischen 1 und 50 liegen.", path: "widgets[5].settings.limit" }));
  await user.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByRole("dialog", { name: /Einstellungen/ })).toBeVisible();
  expect(screen.getByTestId("widget-w-list")).toHaveClass("ring-danger");
  expect(screen.getByText("Der Wert muss zwischen 1 und 50 liegen.")).toBeVisible();
});
test("edit mode fetches widget data through the preview endpoint, not the batch", async () => { expect(api.previewData).toHaveBeenCalled(); expect(api.data).toHaveBeenCalledTimes(1); /* only the pre-edit view load */ });
test("an unknown widget type stays in the grid in edit mode and is saved back unchanged when untouched", async () => {
  mockDashboard({ widgets: [wire("g1", "org.example.gone", { lg: { x: 0, y: 0, w: 4, h: 2 } }, { a: 1 })] });
  enterEdit();
  expect(within(screen.getByTestId("widget-g1")).getByRole("button", { name: "Entfernen" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Speichern" }));
  expect(api.save.mock.calls[0][1].widgets[0]).toMatchObject({ id: "g1", type_key: "org.example.gone", settings: { a: 1 } });
});
// phone
test("on a phone-width container edit mode offers only up/down and settings per widget — no gallery, drag, duplicate or remove", async () => {
  setContainerWidth(390); enterEdit();
  const w = screen.getByTestId("widget-w-list"); expect(within(w).getByRole("button", { name: "Nach oben" })).toBeVisible(); expect(within(w).getByRole("button", { name: "Nach unten" })).toBeVisible();
  expect(within(w).getByRole("button", { name: "Einstellungen" })).toBeVisible();
  for (const name of ["Duplizieren", "Entfernen", "Widget hinzufügen"]) expect(screen.queryByRole("button", { name })).toBeNull();
  expect(screen.queryByTestId("widget-w-list-handle")).toBeNull();
  fireEvent.click(within(w).getByRole("button", { name: "Nach oben" })); expect(store.widgets.map((x) => x.id).indexOf("w-list")).toBe(prevIndex - 1);
});
test("'Nach oben' on the first and 'Nach unten' on the last widget are disabled", async () => {
  setContainerWidth(390); enterEdit();
  const all = Array.from(screen.getByTestId("dashboard-grid").querySelectorAll<HTMLElement>("[data-type]"));
  expect(within(all[0]).getByRole("button", { name: "Nach oben" })).toBeDisabled();
  expect(within(all.at(-1)!).getByRole("button", { name: "Nach unten" })).toBeDisabled();
});
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** (pointer maths via `getBoundingClientRect`, flip x when the grid is RTL; reducer from Task 8; the toolbar uses inverse tokens `bg-ink text-surface`). `usePreviewData` is used for every widget with a `dataEndpoint` while editing; query key `dashboardKeys.preview(typeKey, hash(settings))`, `staleTime` 30 s so moving a widget never refetches.
- [ ] **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; demo check with Playwright (drag with the real mouse in `VITE_DEMO=1`, 1280 px; keyboard; phone 390 px) → PASS.
- [ ] **Step 5: Commit** — `feat(dashboard): edit mode with drag, resize, keyboard, undo/redo, draft and phone controls`.

---

### Task 13: Widget gallery (frontend-only)

**Needs plugin-system Task 13 committed**; needs Task 12.

**Files:**
- Create: `frontend/src/dashboard/gallery/GalleryPanel.tsx`, `gallery/galleryModel.ts` (pure grouping/search), tests; i18n keys `dashboards.gallery.*`
- Modify: `frontend/src/routes/agent/DashboardPage.tsx` (render the panel in edit mode on `md`/`lg`), `frontend/src/dashboard/DashboardGrid.tsx` (drop zone: `onDragOver` preventDefault, `onDrop` → `add` with the snapped cell)

**Interfaces:**
- Consumes: `useDashboardWidgetDefinitions()` (core + plugin widgets), `galleryGroupOf`, edit session `dispatch`, `MAX_WIDGETS_PER_DASHBOARD = 40`.
- Produces: `galleryModel.ts`: `type GalleryGroup = { id: "tickets" | "me" | "team"; items: WidgetDefinition[] }`; `buildGallery(defs: WidgetDefinition[], query: string, t: TFunction, canUse: (def) => boolean): GalleryGroup[]` (groups in fixed order Tickets / Für mich / Team, items sorted by translated title, empty groups omitted; query matches translated title and description case-insensitively, any whitespace-separated term must match); `GalleryPanel({ widgetCount, onAdd(def, at?), onClose })` (at `widgetCount >= MAX_WIDGETS_PER_DASHBOARD` every card is disabled and the panel shows "Es sind höchstens 40 Widgets möglich."). Drag payload: MIME `application/x-tiqora-widget` = the widget key.

- [ ] **Step 1: Write the failing tests**

```tsx
test("groups appear as Tickets, Für mich, Team in that order; plugin widgets of category 'reports' land in Für mich", () => {
  const g = buildGallery([...core, pluginReports], "", t, () => true);
  expect(g.map((x) => x.id)).toEqual(["tickets", "me", "team"]); expect(g[1].items.map((d) => d.key)).toContain("org.example.chart");
});
test("search matches title and description, case-insensitive, all terms; empty groups vanish", () => {
  expect(buildGallery(core, "eskal", t, ok).flatMap((g) => g.items.map((d) => d.key))).toEqual(["core.escalations"]);
  expect(buildGallery(core, "NOTIZ team", t, ok).flatMap((g) => g.items.map((d) => d.key))).toEqual(["core.notes"]);
  expect(buildGallery(core, "zzz", t, ok)).toEqual([]);
});
test("widgets the user may not use (permission) are not offered", () => { expect(buildGallery([{ ...def, permission: "stats" }], "", t, () => false)).toEqual([]); });
test("panel renders title, search placeholder, group headings and the foot hint of the mockup", () => {
  render(<GalleryPanel …/>); expect(screen.getByRole("complementary", { name: "Widget hinzufügen" })).toBeVisible();
  expect(screen.getByPlaceholderText("z. B. Eskalation, Notiz, Queue")).toBeVisible(); expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual(["Tickets", "Für mich", "Team"]);
  expect(screen.getByText("Ins Raster ziehen oder anklicken – das Widget landet in der ersten freien Lücke.")).toBeVisible();
});
test("clicking a card or pressing Enter on it adds the widget in the first free spot", async () => {
  fireEvent.click(screen.getByRole("button", { name: /Ticketliste/ })); expect(onAdd).toHaveBeenCalledWith(expect.objectContaining({ key: "core.ticket-list" }), undefined);
  await user.tab(); /* … */ await user.keyboard("{Enter}"); expect(onAdd).toHaveBeenCalledTimes(2);
});
test("dropping a card on the grid adds the widget at the snapped cell", () => {
  const dt = new DataTransfer(); dt.setData("application/x-tiqora-widget", "core.counter");
  fireEvent.drop(screen.getByTestId("dashboard-grid"), { dataTransfer: dt, clientX: 205, clientY: 165 }); expect(store.past).toHaveLength(1); expect(store.widgets.at(-1)!.layout.lg).toMatchObject({ x: 2, y: 2 });
});
test("a drop with a foreign or unknown payload does nothing", () => {
  const foreign = new DataTransfer(); foreign.setData("text/plain", "core.counter");
  fireEvent.drop(screen.getByTestId("dashboard-grid"), { dataTransfer: foreign, clientX: 100, clientY: 100 });
  const unknown = new DataTransfer(); unknown.setData("application/x-tiqora-widget", "org.example.unknown");
  fireEvent.drop(screen.getByTestId("dashboard-grid"), { dataTransfer: unknown, clientX: 100, clientY: 100 });
  expect(store.past).toHaveLength(0);
});
test("at 40 widgets the cards are disabled and the panel says 'Es sind höchstens 40 Widgets möglich.'", () => {
  render(<GalleryPanel widgetCount={40} onAdd={onAdd} onClose={onClose} />);
  expect(screen.getByText("Es sind höchstens 40 Widgets möglich.")).toBeVisible();
  expect(screen.getByRole("button", { name: /Ticketliste/ })).toBeDisabled();
});
test("Escape closes the panel and focus returns to the toolbar", async () => {
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("complementary", { name: "Widget hinzufügen" })).toBeNull();
  expect(screen.getByRole("toolbar", { name: "Bearbeiten" })).toContainElement(document.activeElement as HTMLElement);
});
test("an empty search result says 'Nichts gefunden' and offers to clear the search", async () => {
  await user.type(screen.getByLabelText("Suchen"), "zzz");
  expect(screen.getByText("Nichts gefunden")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Suche löschen" }));
  expect(screen.getByLabelText("Suchen")).toHaveValue("");
});
test("added widgets get fresh uuid ids and the definition's default settings", () => {
  addTwice("core.ticket-list");
  const [a, b] = store.widgets.slice(-2);
  expect(a.id).toMatch(/^[0-9a-f-]{36}$/);
  expect(a.id).not.toBe(b.id);
  expect(a.settings).toEqual(settingsWithDefaults(DASHBOARD_WIDGETS.get("core.ticket-list")!, {}, 1));
});
```

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement** (cards: 34 px tinted glyph tile, name, description — tints/ink per category from existing tokens; the glyph is the mockup's per-widget glyph kept in `galleryModel.ts` `GLYPHS` map: ticket-list `≡`, counter `12`, escalations `!`, queue-overview `Q`, reminders `◷`, calendar-agenda `31`, recent-views `↺`, team-presence `●`, notes `✎`, links `↗`; plugin widgets use their `icon` name or `puzzle`). **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` + demo check (click adds into the first gap; drag to a cell) → PASS. **Step 5: Commit** — `feat(dashboard): widget gallery panel with search, groups, click and drag to add`.

---

### Task 14: Settings drawer — generated forms, bespoke editors, live preview (frontend-only)

**Needs plugin-system Task 13 committed**; needs Tasks 10 and 12.

**Files:**
- Create: `frontend/src/dashboard/settings/SettingsDrawer.tsx`, `settings/registry.ts`, `settings/editors/TicketListSettings.tsx`, `editors/LinksSettings.tsx`, `editors/NotesSettings.tsx`, `settings/linkUrl.ts` (client mirror of `safe_link_url`), tests; i18n keys `dashboards.settings.*`
- Modify: `frontend/src/routes/agent/DashboardPage.tsx`/`DashboardGrid.tsx` (open the drawer from the widget's settings button, from Enter, and from a 422 path), `frontend/src/dashboard/data.tsx` (`usePreviewData` already exists)

**Interfaces:**
- Consumes: `SchemaForm`, `usePreviewData` (Task 11), the widget component from `DASHBOARD_WIDGETS`, `settingsWithDefaults`, edit `dispatch`.
- Produces: `registry.ts`: `export interface SettingsEditorProps { value: Record<string, unknown>; onChange(next: Record<string, unknown>): void; errors: Record<string, string>; canEditSources: boolean }`, `export const CORE_SETTINGS_EDITORS: Record<string, ComponentType<SettingsEditorProps>>` (`core.ticket-list`, `core.links`, `core.notes`); all other core widgets and every plugin widget use `SchemaForm` over `def.settingsSchema` (the core schemas for `core.counter`, `core.queue-overview`, `core.escalations`, `core.reminders`, `core.recent-views`, `core.calendar-agenda`, `core.team-presence` are flat scalars + one enum — the counter's `source.preset` is exposed as the flat enum field `preset` in its schema and mapped to/from `source` by a `toSettings/fromSettings` pair in `definitions.ts`). `SettingsDrawer({ widget, def, onApply(settings, titleOverride), onCancel })`.
  Ticket-list editor (phase-1 content before Task 17): "Welche Tickets?" shows the single radio card "Gespeicherte Suche" (selected) with a select listing the five built-in views as group "Standard" (“Meine offenen”, “Meine neuen”, “Zum Übernehmen”, “Eskaliert”, “Von der KI übergeben”); the card "Eigener Filter" and the user's own saved searches are added by Task 17 (no disabled placeholder card before that); "Spalten" checkbox chips (8 columns, at least one stays checked), "Sortierung" select with the combinations `Fälligkeit, früheste zuerst` (deadline asc), `Zuletzt geändert` (changed desc), `Priorität` (priority desc), `Älteste zuerst` (age asc), `Neueste zuerst` (age desc), "Anzahl Zeilen" number 1–50, "Darstellung" radio `Kompakt` / `Bequem`.

- [ ] **Step 1: Write the failing tests**

```tsx
test("the drawer is a labelled dialog with the type name, title field, form, preview and the two buttons", () => {
  render(<SettingsDrawer widget={listWidget} def={listDef} … />); const d = screen.getByRole("dialog", { name: "Einstellungen" });
  expect(within(d).getByText("Ticketliste")).toBeVisible(); expect(within(d).getByLabelText("Titel")).toHaveValue("Meine offenen Tickets");
  expect(within(d).getByRole("region", { name: "Vorschau" })).toBeVisible(); expect(within(d).getByRole("button", { name: "Abbrechen" })).toBeVisible(); expect(within(d).getByRole("button", { name: "Übernehmen" })).toBeVisible();
});
test("ticket-list editor: columns are chips with at least one kept, sort combos map to sort+order, rows are clamped 1..50, density radio", async () => {
  await user.click(screen.getByRole("checkbox", { name: "Queue" })); await user.selectOptions(screen.getByLabelText("Sortierung"), "Zuletzt geändert");
  await user.clear(screen.getByLabelText("Anzahl Zeilen")); await user.type(screen.getByLabelText("Anzahl Zeilen"), "99"); await user.click(screen.getByRole("radio", { name: "Kompakt" }));
  await user.click(screen.getByRole("button", { name: "Übernehmen" }));
  expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ columns: ["tn", "title", "customer", "state", "due", "queue"], sort: "changed", order: "desc", limit: 50, density: "compact" }), null);
  // unchecking every column but one: the last remaining checkbox is disabled
});
test("the live preview re-renders the real widget with the draft settings, debounced 400 ms, through the preview endpoint", async () => {
  vi.useFakeTimers(); change("limit", 3); await vi.advanceTimersByTimeAsync(399); expect(api.previewData).toHaveBeenCalledTimes(1);   // only the initial one
  await vi.advanceTimersByTimeAsync(2); expect(api.previewData).toHaveBeenLastCalledWith("core.ticket-list", expect.objectContaining({ limit: 3 }), expect.anything());
  expect(within(screen.getByTestId("settings-preview")).getByTestId("widget-component")).toHaveAttribute("data-mode", "preview");
});
test("the preview is inert: no links, no focusable controls, fixed height with overflow hidden", () => {
  const box = screen.getByTestId("settings-preview");
  expect(within(box).queryAllByRole("link")).toHaveLength(0);
  expect(box.querySelectorAll("a, button, input, [tabindex]:not([tabindex='-1'])")).toHaveLength(0);
  expect(box).toHaveClass("max-h-56", "overflow-hidden");
});
test("a preview error shows the German message in the preview box and does not block 'Übernehmen'", async () => {
  api.previewData.mockResolvedValue({ status: "error", error: "Diese Daten konnten nicht geladen werden." });
  expect(await within(screen.getByTestId("settings-preview")).findByText("Diese Daten konnten nicht geladen werden.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Übernehmen" })).toBeEnabled();
});
test("Abbrechen, Escape and the close button discard the draft values; focus returns to the opener", async () => {
  const closers = [() => user.click(screen.getByRole("button", { name: "Abbrechen" })), () => user.keyboard("{Escape}"), () => user.click(screen.getByRole("button", { name: "Schließen" }))];
  for (const close of closers) {
    openDrawer();
    await user.clear(screen.getByLabelText("Titel")); await user.type(screen.getByLabelText("Titel"), "Anders");
    await close();
    expect(onApply).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  }
});
test("Übernehmen dispatches one 'settings' action (one undo step) and closes; nothing is saved to the server", async () => {
  expect(store.past).toHaveLength(1); expect(api.save).not.toHaveBeenCalled();
});
test("an empty title means 'use the default title' (titleOverride null), not an empty heading", async () => {
  await user.clear(screen.getByLabelText("Titel"));
  await user.click(screen.getByRole("button", { name: "Übernehmen" }));
  expect(onApply).toHaveBeenCalledWith(expect.anything(), null);
});
test("a plugin widget gets a SchemaForm from its settingsSchema; without a schema only the title field is shown", async () => {
  render(<SettingsDrawer def={{ ...pluginDef, settingsSchema: { type: "object", properties: { city: { type: "string", title: "Stadt" } } } }} … />); await user.type(screen.getByLabelText("Stadt"), "Bonn");
  await user.click(apply); expect(onApply).toHaveBeenCalledWith({ city: "Bonn" }, null); cleanup(); render(<SettingsDrawer def={{ ...pluginDef, settingsSchema: null }} … />); expect(screen.queryByTestId("schema-form")).toBeNull();
});
test("links editor rejects unsafe addresses, caps at 12 rows and blocks Übernehmen while invalid", async () => {
  await user.click(screen.getByRole("button", { name: "Link hinzufügen" }));
  await user.type(screen.getByLabelText("Bezeichnung 1"), "Böse"); await user.type(screen.getByLabelText("Adresse 1"), "javascript:alert(1)");
  expect(screen.getByText("Diese Adresse ist nicht erlaubt.")).toBeVisible();
  expect(screen.getByRole("button", { name: "Übernehmen" })).toBeDisabled();
  await user.clear(screen.getByLabelText("Adresse 1")); await user.type(screen.getByLabelText("Adresse 1"), "https://example.org");
  expect(screen.getByRole("button", { name: "Übernehmen" })).toBeEnabled();
  for (let i = 1; i < 12; i++) await user.click(screen.getByRole("button", { name: "Link hinzufügen" }));
  expect(screen.getByRole("button", { name: "Link hinzufügen" })).toBeDisabled();       // 12 rows max
});
test("notes editor counts characters and blocks Übernehmen over 5000", async () => {
  fireEvent.change(screen.getByLabelText("Notiz"), { target: { value: "a".repeat(5001) } });
  expect(screen.getByText("5001 / 5000")).toHaveClass("text-danger");
  expect(screen.getByRole("button", { name: "Übernehmen" })).toBeDisabled();
});
test("server-side path errors map onto fields", async () => {
  openDrawer({ errors: { "settings.limit": "Der Wert muss zwischen 1 und 50 liegen." } });
  expect(screen.getByLabelText("Anzahl Zeilen")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Der Wert muss zwischen 1 und 50 liegen.")).toBeVisible();
});
test("migrates older stored settings on open", async () => {
  const def = { ...listDef, version: 2, migrate: (o: any) => ({ ...o, limit: o.rows }) };
  openDrawer({ def, widget: { ...w, type_version: 1, settings: { rows: 12 } } });
  expect(screen.getByLabelText("Anzahl Zeilen")).toHaveValue(12);
});
test("focus is trapped inside the drawer and the page behind is inert", async () => {
  openDrawer();
  await user.tab({ shift: true });
  expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
  for (let i = 0; i < 30; i++) await user.tab();
  expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement);
  expect(screen.getByTestId("dashboard-grid").closest("[aria-hidden='true'], [inert]")).not.toBeNull();
});
```

- [ ] **Step 2: Run to verify they fail.** **Step 3: Implement** (drawer reuses the app's `Dialog`/portal conventions — check `components/ui/Dialog.tsx` and the skill `react-dialog-focus-steal-callback-dep`: do not put an `onClose` callback with changing identity into a focus effect dependency). **Step 4: Run** `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` + demo check at 1280 px (drawer next to the grid, live preview updates) → PASS. **Step 5: Commit** — `feat(dashboard): widget settings drawer with generated forms, bespoke editors and live preview`.

---

### Task 15: Dashboard management and the admin templates page (frontend-only)

**Needs plugin-system Task 13 committed** (via Task 11); needs Tasks 7 and 11.

**Files:**
- Create: `frontend/src/dashboard/tabs/NewDashboardDialog.tsx`, `tabs/TabMenu.tsx`, `frontend/src/routes/admin/DashboardTemplatesPage.tsx`, tests; i18n keys `dashboards.manage.*`, `admin.nav.dashboardTemplates`, `admin.pageDescriptions.dashboardTemplates`, `admin.dashboardTemplates.*`
- Modify: `frontend/src/dashboard/tabs/DashboardTabs.tsx` (the "+" and a per-tab menu), `frontend/src/router.tsx` (admin route `/admin/dashboard-templates`), `frontend/src/lib/adminSearch.ts` (`ADMIN_PAGES` entry slug `dashboard-templates`, group `system`, keywords `startseite`, `vorlage`, `dashboard`, `widgets`, `rolle`) and `frontend/src/lib/adminSearch.test.ts` (count/assertions that enumerate pages)

**Interfaces:**
- Consumes: `dashboardsApi.create/save/remove/order/templates.*`, `/api/v1/admin/roles` list (existing admin API; look for the roles list hook in `frontend/src/lib/adminApi*.ts` or `AssignmentEditor`), `ConfirmDialog`.
- Produces: `NewDashboardDialog({ onCreate(name, copyFrom?) , onClose })` (name required ≤ 120; optional "Kopie von …" select of visible dashboards); `TabMenu({ tab, index, count, onRename, onMove(dir), onDelete, onCopy })`. Menu items: "Umbenennen", "Nach links"/"Nach rechts" (own personal/shared tabs only → `order`), "Als eigenes Dashboard kopieren" (shared/template tabs), "Löschen" (own tabs only, `ConfirmDialog` "Dashboard „{{name}}“ löschen?"). Rename = `save` with the current widgets and the new name (version check applies). The last own dashboard cannot be deleted (menu item disabled with title "Das letzte Dashboard bleibt bestehen."). Admin page: table (Name, Rollen, Gesperrt, Widgets, Priorität), "Neue Vorlage" dialog (name, roles multi-select from the admin roles list, "Für die Rollen verbindlich (Benutzer können sie nicht ändern)" switch, priority), edit/delete, "Layout bearbeiten" → `/agent?d=<id>`.

- [ ] **Step 1: Write the failing tests**

```tsx
test("'+' opens the dialog; the name is required and trimmed; creating selects the new tab", async () => {
  await user.click(screen.getByRole("button", { name: "Neues Dashboard" })); await user.click(screen.getByRole("button", { name: "Anlegen" }));
  expect(screen.getByText("Bitte einen Namen eingeben.")).toBeVisible(); await user.type(screen.getByLabelText("Name"), "  Mein Tag  "); await user.click(screen.getByRole("button", { name: "Anlegen" }));
  expect(api.create).toHaveBeenCalledWith({ name: "Mein Tag", icon: null, copy_from: null }); expect(await screen.findByRole("tab", { name: "Mein Tag", selected: true })).toBeVisible();
});
test("'Kopie von' passes copy_from", async () => {
  await openNew();
  await user.type(screen.getByLabelText("Name"), "Kopie");
  await user.selectOptions(screen.getByLabelText("Kopie von"), "Team Support");
  await user.click(screen.getByRole("button", { name: "Anlegen" }));
  expect(api.create).toHaveBeenCalledWith({ name: "Kopie", icon: null, copy_from: 2 });
});
test("the 21st dashboard shows the server's message and keeps the dialog open", async () => {
  api.create.mockRejectedValue(apiError(422, { message: "Es sind höchstens 20 Dashboards möglich.", path: "name" }));
  await openNew();
  await user.type(screen.getByLabelText("Name"), "Zu viel"); await user.click(screen.getByRole("button", { name: "Anlegen" }));
  expect(await screen.findByText("Es sind höchstens 20 Dashboards möglich.")).toBeVisible();
  expect(screen.getByRole("dialog")).toBeVisible();
});
test("rename saves with the current widgets and the new name; a 409 keeps the old name and says why", async () => {
  await user.click(menuOf("Mein Start")); await user.click(screen.getByRole("menuitem", { name: "Umbenennen" }));
  await user.clear(screen.getByLabelText("Name")); await user.type(screen.getByLabelText("Name"), "Mein Tag"); await user.click(screen.getByRole("button", { name: "Speichern" }));
  expect(api.save).toHaveBeenCalledWith(1, expect.objectContaining({ name: "Mein Tag", version: 3, widgets: DEFAULT_DASHBOARD.widgets }));
  api.save.mockRejectedValue(new DashboardConflictError(4));
  await user.click(menuOf("Mein Tag")); await user.click(screen.getByRole("menuitem", { name: "Umbenennen" })); await user.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByText(/inzwischen geändert/)).toBeVisible();
});
test("move left/right calls order with the permuted own ids; disabled at the ends and absent for foreign tabs", async () => {
  await user.click(menuOf("Eigen B")); await user.click(screen.getByRole("menuitem", { name: "Nach links" }));
  expect(api.order).toHaveBeenCalledWith([2, 1]);
  await user.click(menuOf("Eigen B"));                                         // now the first tab
  expect(screen.getByRole("menuitem", { name: "Nach links" })).toBeDisabled();
  await user.keyboard("{Escape}"); await user.click(menuOf("Team Support"));   // shared by someone else
  expect(screen.queryByRole("menuitem", { name: "Nach links" })).toBeNull();
});
test("delete asks for confirmation, then removes and selects the neighbour; the last own dashboard cannot be deleted", async () => {
  await user.click(menuOf("Eigen B")); await user.click(screen.getByRole("menuitem", { name: "Löschen" }));
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Löschen" }));
  expect(api.remove).toHaveBeenCalledWith(2);
  expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
  await user.click(menuOf("Mein Start"));
  expect(screen.getByRole("menuitem", { name: "Löschen" })).toHaveAttribute("aria-disabled", "true");
});
test("a shared or locked tab offers copy and no rename/delete", async () => {
  await user.click(menuOf("Team Support"));
  expect(screen.queryByRole("menuitem", { name: "Umbenennen" })).toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Löschen" })).toBeNull();
  await user.click(screen.getByRole("menuitem", { name: "Als eigenes Dashboard kopieren" }));
  expect(api.create).toHaveBeenCalledWith({ name: "Team Support (Kopie)", icon: null, copy_from: 2 });
});
test("the tab menu is keyboard operable", async () => {
  menuButtonOf("Mein Start").focus();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("menu")).toBeVisible();
  await user.keyboard("{ArrowDown}");
  expect(screen.getAllByRole("menuitem")[1]).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("menu")).toBeNull();
  expect(menuButtonOf("Mein Start")).toHaveFocus();
});
// admin page
test("lists templates with roles, lock state and widget count; the empty state explains what templates are", async () => {
  api.templates.list.mockResolvedValue([{ id: 9, name: "Support", role_ids: [3], locked: true, widget_count: 11, sort: 0 }]);
  render(<DashboardTemplatesPage />);
  const row = await screen.findByRole("row", { name: /Support/ });
  expect(within(row).getByText("Agenten")).toBeVisible(); expect(within(row).getByText("Ja")).toBeVisible(); expect(within(row).getByText("11")).toBeVisible();
  cleanup(); api.templates.list.mockResolvedValue([]);
  render(<DashboardTemplatesPage />);
  expect(await screen.findByText(/Vorlagen legen fest, womit ein neues Dashboard für eine Rolle startet/)).toBeVisible();
});
test("create requires a name and a role, then posts TemplateIn", async () => {
  await user.click(screen.getByRole("button", { name: "Neue Vorlage" })); await user.click(screen.getByRole("button", { name: "Anlegen" }));
  expect(screen.getByText("Bitte einen Namen eingeben.")).toBeVisible(); expect(screen.getByText("Bitte mindestens eine Rolle wählen.")).toBeVisible();
  await user.type(screen.getByLabelText("Name"), "Support"); await user.click(screen.getByRole("checkbox", { name: "Agenten" }));
  await user.click(screen.getByRole("switch", { name: /verbindlich/ })); await user.click(screen.getByRole("button", { name: "Anlegen" }));
  expect(api.templates.create).toHaveBeenCalledWith({ name: "Support", icon: null, role_ids: [3], locked: true, sort: 0 });
});
test("delete asks first and 'Layout bearbeiten' opens the template in the editor", async () => {
  expect(screen.getByRole("link", { name: "Layout bearbeiten" })).toHaveAttribute("href", "/agent?d=9");
  await user.click(screen.getByRole("button", { name: "Löschen" }));
  expect(api.templates.remove).not.toHaveBeenCalled();
  await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Löschen" }));
  expect(api.templates.remove).toHaveBeenCalledWith(9);
});
test("ADMIN_PAGES contains the new page once, with a route, group and German keywords", () => {
  const p = ADMIN_PAGES.filter((x) => x.slug === "dashboard-templates"); expect(p).toHaveLength(1); expect(p[0].route).toBe("/admin/dashboard-templates");
});
```

- [ ] **Step 2–5:** run → fail; implement; `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` + demo check of the admin page and "+" flow → PASS; commit `feat(dashboard): dashboard management and admin templates page`.

---

### Task 16: Sharing with groups — "Teilen" (backend + frontend)

**Needs reporting-builder Tasks 2–3 committed** (`tiqora.permissions.sharing.share_groups_for` / `check_share_groups` / `ShareGroupError`, `GET /api/v1/reference/share-groups`, `reportsApi.shareGroups`); needs Task 11.

**Files:**
- Modify: `backend/src/tiqora/dashboard/store.py` (`set_share`), `backend/src/tiqora/api/v1/dashboards.py` (`PUT /dashboards/{id}/share`), `backend/tests/test_dashboard_store.py`, `backend/tests/test_dashboards_api.py`, openapi files
- Create: `frontend/src/dashboard/tabs/ShareDialog.tsx` + test; i18n `dashboards.share.*`
- Modify: `frontend/src/dashboard/tabs/DashboardTabs.tsx`/`DashboardPage.tsx` ("Teilen" opens the dialog)

**Interfaces:**
- Consumes: `check_share_groups(session, user_id, group_ids) -> tuple[int, ...]` (raises `ShareGroupError(message)` → 422 path `group_ids`), `share_groups_for`.
- Produces: `async def set_share(session, dashboard_id: int, user_id: int, group_ids: Sequence[int], *, admin: bool) -> DashboardOut` — owner only; templates → `DashboardForbidden("Vorlagen werden über die Rollen verteilt.")`; empty list → kind `personal`, rows deleted; non-empty → kind `shared`, rows replaced; bumps `version`. `ShareDialog({ dashboard, onClose })`: radio "Nur ich" / "Mit Gruppen" + checklist of `shareGroups()` with search; note "Die Mitglieder sehen nur Tickets aus Queues, die sie selbst lesen dürfen." (viewer scope, Review Focus 1).

- [ ] **Step 1: Write the failing tests**

```python
# backend (band 49890–49909)
async def test_share_makes_the_dashboard_visible_to_group_members_and_unshare_hides_it(env) -> None:
    r = await env.as_user(USER_A).put(f"/api/v1/dashboards/{d.id}/share", json={"group_ids": [GROUP_2]})
    assert r.status_code == 200 and r.json()["kind"] == "shared" and r.json()["group_ids"] == [GROUP_2]
    assert d.id in [t["id"] for t in (await env.as_user(USER_B).get("/api/v1/dashboards")).json()]
    await env.as_user(USER_A).put(f".../share", json={"group_ids": []})
    assert (await env.as_user(USER_B).get(f"/api/v1/dashboards/{d.id}")).status_code == 404
async def test_share_rejects_groups_the_owner_cannot_share_with(env) -> None:
    r = await env.as_user(USER_B).put(url_of_b_dashboard + "/share", json={"group_ids": [GROUP_1]})        # B has no ro on GROUP_1
    assert r.status_code == 422 and r.json()["detail"]["path"] == "group_ids"
async def test_only_the_owner_shares_and_templates_cannot_be_shared(env) -> None:
    d = await env.make_personal(USER_A, name="dash497 sh", share_group_ids=[GROUP_2])
    assert (await env.as_user(USER_B).put(f"/api/v1/dashboards/{d.id}/share", json={"group_ids": []})).status_code == 403   # visible via the group, not the owner
    assert (await env.as_user(USER_C).put(f"/api/v1/dashboards/{d.id}/share", json={"group_ids": []})).status_code == 404   # not visible at all
    tpl = await env.make_template(name="dash497 tpl", role_ids=[ROLE_1], locked=False)
    r = await env.admin.put(f"/api/v1/dashboards/{tpl.id}/share", json={"group_ids": [GROUP_1]})
    assert r.status_code == 403 and "Rollen" in str(r.json())
async def test_a_shared_dashboard_edit_by_a_viewer_is_403_and_copy_works(env) -> None:
    d = (await env.as_user(USER_A).post("/api/v1/dashboards/ensure-start")).json()
    await env.as_user(USER_A).put(f"/api/v1/dashboards/{d['id']}/share", json={"group_ids": [GROUP_2]})
    b = env.as_user(USER_B)
    put = await b.put(f"/api/v1/dashboards/{d['id']}", json={"name": "x", "icon": None, "version": d["version"] + 1, "widgets": []})
    assert put.status_code == 403
    cp = await b.post("/api/v1/dashboards", json={"name": "dash497 Kopie", "copy_from": d["id"]})
    assert cp.status_code == 201 and cp.json()["is_owner"] is True and cp.json()["id"] != d["id"]
```
```tsx
test("ShareDialog lists the user's share groups, preselects the current ones and saves the selection", async () => {
  api.share.mockResolvedValue({ ...d, kind: "shared", group_ids: [2] }); await user.click(screen.getByRole("radio", { name: "Mit Gruppen" })); await user.click(screen.getByRole("checkbox", { name: "Support" }));
  await user.click(screen.getByRole("button", { name: "Speichern" })); expect(api.share).toHaveBeenCalledWith(1, [2]);
});
test("'Nur ich' saves an empty list; 'Mit Gruppen' with nothing checked disables Speichern", async () => {
  await user.click(screen.getByRole("radio", { name: "Nur ich" })); await user.click(screen.getByRole("button", { name: "Speichern" }));
  expect(api.share).toHaveBeenCalledWith(1, []);
  cleanup(); render(dialog);
  await user.click(screen.getByRole("radio", { name: "Mit Gruppen" }));
  expect(screen.getByRole("button", { name: "Speichern" })).toBeDisabled();
});
test("a 422 from the server shows its message under the list", async () => {
  api.share.mockRejectedValue(apiError(422, { message: "Mit der Gruppe Support kann nicht geteilt werden.", path: "group_ids" }));
  await user.click(screen.getByRole("radio", { name: "Mit Gruppen" })); await user.click(screen.getByRole("checkbox", { name: "Support" })); await user.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByText("Mit der Gruppe Support kann nicht geteilt werden.")).toBeVisible();
});
test("the viewer-scope note is shown", () => { expect(screen.getByText(/nur Tickets aus Queues, die sie selbst lesen dürfen/)).toBeVisible(); });
```

- [ ] **Step 2–5:** run → fail; implement; backend gates + openapi regeneration, `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; commit `feat(dashboard): share dashboards with groups`.

---

### Task 17: Ticket-list and counter sources — saved search and own filter (backend + frontend)

**Needs reporting-builder Tasks 5, 6, 10 and 11 committed** (`POST /tickets/query` with `TicketService.page_from_stmt`, saved-search store `get_visible` + `SavedSearch`, `savedSearchesApi`, `FilterBuilder` + `fieldsFromTicketCatalog`); needs Tasks 6 and 14. Wave-1 names used: `compile_ticket_filter`, `ticket_query_scope`, `count_tickets`, `validate_filter`, `ticket_filter_fields`, `FilterError`.

**Files:**
- Modify: `backend/src/tiqora/dashboard/settings_models.py` (widen `TicketSource`), `backend/src/tiqora/dashboard/providers/ticket_list.py`, `providers/counter.py`, `backend/src/tiqora/dashboard/store.py` (`validate_sources` called by `save_dashboard`/`patch_widget_settings` after the pure validation), `backend/src/tiqora/dashboard/providers/__init__.py` (`ProviderForbidden(message)` carries a message shown in the tile), tests, openapi files
- Modify (frontend): `frontend/src/dashboard/settings/editors/TicketListSettings.tsx` (radio card "Eigener Filter" + saved-search picker), a new `CounterSettings.tsx` editor registered for `core.counter`, `frontend/src/dashboard/widgets/TicketListWidget.tsx` / `CounterWidget.tsx` (links, empty state action), `frontend/src/demo/dashboardsMock.ts`, i18n

**Interfaces:**
- Consumes: reporting-builder names above; D6 shapes.
- Produces: settings union additions `SavedSearchSource{kind:"saved_search", id:int}` and `FilterSource{kind:"filter", filter: dict}` (the filter is validated at save time against `ticket_filter_fields(session)` with `validate_filter`; invalid → `DashboardInvalid("…", "widgets[i].settings.source.filter")`; a `saved_search` id must be **visible to the saver** (`get_visible`), else 422 "Die gespeicherte Suche gibt es nicht." path `…source.id`). Provider behaviour: saved search → `get_visible(session, id, viewer, admin=False)`; `SavedSearchNotFound` → `ProviderForbidden("Die Suche gibt es nicht mehr oder ist für dich nicht freigegeben.")`; `criteria is None` → `ProviderForbidden("Diese Suche kann nicht mehr gelesen werden.")`; otherwise `scope = await ticket_query_scope(session, viewer, include_archived=False)`, `stmt = await compile_ticket_filter(session, criteria, scope)`, list → `TicketService(session).page_from_stmt(viewer, stmt, offset=0, limit=settings.limit, sort=settings.sort, order=settings.order)`; counter → `count_tickets(session, criteria, scope)` under `statement_timeout`; `FilterError` at run time (field deleted since) → `ProviderForbidden("Der Filter ist nicht mehr gültig.")`. Counter sample keys: `search:<id>`, `filter:<sha1(json.dumps(filter, sort_keys=True))[:16]>`.

- [ ] **Step 1: Write the failing tests**

```python
# backend (band 49910–49949), both dialects
async def test_ticket_list_by_saved_search_runs_in_viewer_scope(env) -> None:
    sid = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1), shared_groups=[GROUP_2])           # shared with B's group, but B cannot read QUEUE_1
    out = await run_widget(f, reg, USER_B, W("t", "core.ticket-list", settings={"source": {"kind": "saved_search", "id": sid}}))
    assert out.status == "ok" and out.data["items"] == [] and out.data["total"] == 0                            # never A's tickets
    mine = await run_widget(f, reg, USER_A, W("t", "core.ticket-list", settings={"source": {"kind": "saved_search", "id": sid}}))
    assert TICKETS["a_open"] in [i["id"] for i in mine.data["items"]]
async def test_deleted_or_unshared_saved_search_is_a_forbidden_tile_with_a_message(env) -> None:
    out = await run_widget(..., USER_B, W("t", "core.ticket-list", settings={"source": {"kind": "saved_search", "id": private_sid_of_A}}))
    assert out.status == "forbidden" and out.error == "Die Suche gibt es nicht mehr oder ist für dich nicht freigegeben."
async def test_broken_saved_search_criteria_is_a_message_not_a_500(env) -> None:
    sid = await env.make_saved_search(USER_A, criteria_raw='{"all": "kaputt"}')                       # stored JSON no longer validates
    out = await run_widget(factory, registry, USER_A, W("t", "core.ticket-list", settings={"source": {"kind": "saved_search", "id": sid}}))
    assert out.status == "forbidden" and out.error == "Diese Suche kann nicht mehr gelesen werden."
async def test_inline_filter_list_and_counter_agree(env) -> None:
    src = {"kind": "filter", "filter": {"all": [{"field": "owner_id", "op": "eq", "value": USER_A}, {"field": "state_type", "op": "eq", "value": "open"}]}}
    lst = await run_widget(..., USER_A, W("l", "core.ticket-list", settings={"source": src, "limit": 50})); cnt = await run_widget(..., USER_A, W("c", "core.counter", settings={"source": src}))
    assert cnt.data["value"] == lst.data["total"] == 2
async def test_field_removed_after_saving_is_reported_not_raised(env) -> None:                                  # dynamic field deleted
    assert out.status == "forbidden" and out.error == "Der Filter ist nicht mehr gültig."
async def test_save_rejects_an_invalid_inline_filter_with_a_path(env) -> None:
    r = await env.client.put(url, json=body_with(source={"kind": "filter", "filter": {"all": [{"field": "nope", "op": "eq", "value": 1}]}}))
    assert r.status_code == 422 and r.json()["detail"]["path"].startswith("widgets[0].settings.source.filter")
async def test_save_rejects_a_saved_search_the_saver_cannot_see(env) -> None:
    sid = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1), shared_groups=[])         # private to A
    r = await env.as_user(USER_B).put(env.url_of_b_dashboard, json=env.body_with(source={"kind": "saved_search", "id": sid}))
    assert r.status_code == 422 and r.json()["detail"]["path"] == "widgets[0].settings.source.id"
async def test_counter_history_key_for_searches(env) -> None:
    flt = queue_filter(QUEUE_1)
    await run_widget(factory, registry, USER_A, W("c", "core.counter", settings={"source": {"kind": "saved_search", "id": sid}}))
    await run_widget(factory, registry, USER_A, W("c", "core.counter", settings={"source": {"kind": "filter", "filter": flt}}))
    keys = {k for k, _ in await env.sample_keys(USER_A)}
    assert f"search:{sid}" in keys and any(re.fullmatch(r"filter:[0-9a-f]{16}", k) for k in keys)
async def test_presets_still_work_after_the_union_is_widened(env) -> None:
    assert validate_widget_settings("core.ticket-list", {"source": {"kind": "preset", "preset": "mine_new"}})["source"] == {"kind": "preset", "preset": "mine_new"}
    out = await run_widget(factory, registry, USER_A, W("c", "core.counter", settings={"source": {"kind": "preset", "preset": "mine_open"}}))
    async with env.session() as s:
        assert out.data["value"] == (await TicketService(s).count_dashboard_summary(USER_A))["my_open"]
```
```tsx
test("'Eigener Filter' shows the FilterBuilder with ticket fields and updates settings and preview", async () => {
  await user.click(screen.getByRole("radio", { name: "Eigener Filter" }));
  expect(screen.getByTestId("filter-builder")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Bedingung hinzufügen" })); await user.selectOptions(screen.getByLabelText("Feld"), "Queue"); await pickQueue("Support");
  await user.click(screen.getByRole("button", { name: "Übernehmen" }));
  expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ source: { kind: "filter", filter: expect.objectContaining({ version: 1 }) } }), null);
  expect(api.previewData).toHaveBeenLastCalledWith("core.ticket-list", expect.objectContaining({ source: expect.objectContaining({ kind: "filter" }) }), expect.anything());
});
test("'Gespeicherte Suche' lists Standard views and the user's saved searches, grouped, with the selected one marked", async () => {
  expect(screen.getByRole("group", { name: "Standard" })).toBeVisible(); expect(screen.getByRole("option", { name: "Meine Wiedervorlagen" })).toBeVisible();
});
test("a forbidden tile with a message shows it and, for editors, the action 'Suche wählen' that opens the drawer", async () => {
  renderWidget("core.ticket-list", savedSearch(9), { status: "forbidden", error: "Die Suche gibt es nicht mehr oder ist für dich nicht freigegeben." }, "view", { canEdit: true });
  expect(screen.getByText(/gibt es nicht mehr/)).toBeVisible(); expect(screen.getByRole("button", { name: "Suche wählen" })).toBeVisible();
});
test("'Alle 12 anzeigen' of a saved-search list links to /agent/searches/<id>; an inline filter has no link", async () => {
  renderWidget("core.ticket-list", { source: { kind: "saved_search", id: 9 }, limit: 8 }, { status: "ok", data: { items: rows(8), total: 12 } });
  expect(screen.getByText("Alle 12 anzeigen").closest("a")).toHaveAttribute("href", "/agent/searches/9");
  cleanup();
  renderWidget("core.ticket-list", { source: { kind: "filter", filter: { version: 1 } }, limit: 8 }, { status: "ok", data: { items: rows(8), total: 12 } });
  expect(screen.queryByText("Alle 12 anzeigen")).toBeNull();
});
test("the counter editor offers the same sources and the tone select", async () => {
  openDrawer({ def: counterDef });
  expect(screen.getByRole("radio", { name: "Gespeicherte Suche" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "Eigener Filter" })).toBeVisible();
  expect(screen.getByLabelText("Farbe")).toHaveValue("neutral");
});
test("a filter the builder cannot show (deeper than 2 levels) is displayed read-only and saved unchanged", async () => {
  const deep = nestedFilter(4);
  openDrawer({ settings: { source: { kind: "filter", filter: deep } } });
  expect(screen.getByText(/zu verschachtelt/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Übernehmen" }));
  expect(onApply.mock.calls[0][0].source.filter).toEqual(deep);
});
```

- [ ] **Step 2–5:** run → fail; implement (the pure `validate_widget_settings` keeps its signature; `validate_sources(session, user_id, admin, widgets)` is the async second pass); backend gates + openapi regeneration; `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; demo check; commit `feat(dashboard): saved searches and own filters as ticket-list and counter sources`.

---

### Task 18: End-to-end flows, screenshots, documentation, full verification

**Needs plugin-system Task 13 committed**; runs after Tasks 1–17.

**Files:**
- Create: `frontend/e2e/dashboard.spec.ts`, `docs/dashboards.md`
- Modify: `frontend/e2e/fixtures/mock-api.ts` (stateful mock of `/api/v1/dashboards*` backed by the demo store so save/undo flows work; the store lives at **module level** so two pages of one test share it — the conflict test needs that — and it is seeded with the default dashboard (widget ids `demo-list-1`, `demo-list-2`, … as used below) plus a second dashboard "Alt" holding one `org.example.gone` widget), `frontend/e2e/screenshots.spec.ts` (home page is the new dashboard; add `["/agent", "agent-dashboard"]` edit-mode shot only if the README embeds it — check `grep -rn "agent-dashboard" README.md docs site | head`; **do not edit README or `site/compare.html`**), `docs/architecture.md` (new section "Dashboards": tables, providers, batched data endpoint, layout engine, extension points `core.dashboard.providers` / `core.dashboard.widgets`), `docs/compatibility.md` (row "Configurable dashboard widgets" → `partial`: ticket lists with filters/columns/limits, counters, calendar, queue overview, reminders, team presence, notes; admin role templates incl. mandatory; **missing**: stats/report widgets (phase 2), customer-info widget, RSS), `docs/plugins.md` (if it exists after the plugin plan: replace "Erweiterungen section" wording by the gallery)

- [ ] **Step 1: Write the e2e specs** (`mockApi(page)`, `loginAsAgent(page)`; `data-testid`s as in Tasks 11–14)

```ts
test("view mode shows tabs, KPI counters with sparkline and no handles", async ({ page }) => {
  await page.goto("/agent"); await expect(page.getByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
  await expect(page.getByTestId("dashboard-grid")).toHaveAttribute("data-breakpoint", "lg"); await expect(page.locator('[data-testid^="widget-"][data-type="core.counter"]')).toHaveCount(4);
  await expect(page.locator("[data-testid$='-handle']")).toHaveCount(0);
});
test("add a widget from the gallery by click and by drag, then save", async ({ page }) => {
  await page.getByRole("button", { name: "Anpassen" }).click(); await page.getByRole("button", { name: /Kalender/ }).click();
  await expect(page.locator('[data-type="core.calendar-agenda"]')).toHaveCount(1);
  await page.getByRole("button", { name: /Notizen/ }).dragTo(page.getByTestId("dashboard-grid"), { targetPosition: { x: 600, y: 300 } });
  await page.getByRole("button", { name: "Speichern" }).click(); await expect(page.getByRole("toolbar", { name: "Bearbeiten" })).toHaveCount(0);
  await page.reload(); await expect(page.locator('[data-type="core.calendar-agenda"]')).toHaveCount(1);
});
test("move by mouse, resize by mouse and by keyboard, undo", async ({ page }) => {
  const w = page.getByTestId("widget-demo-list-1"); const before = await w.boundingBox();
  await w.getByTestId("widget-demo-list-1-handle").dragTo(page.getByTestId("dashboard-grid"), { targetPosition: { x: 200, y: 700 } }); expect((await w.boundingBox())!.y).not.toBe(before!.y);
  await page.getByRole("button", { name: "Rückgängig" }).click(); expect((await w.boundingBox())!.y).toBe(before!.y);
  await w.focus(); await page.keyboard.press("Shift+ArrowRight"); expect((await w.boundingBox())!.width).toBeGreaterThan(before!.width);
});
test("settings drawer changes columns and shows the live preview", async ({ page }) => {
  await page.goto("/agent"); await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByTestId("widget-demo-list-1").getByRole("button", { name: "Einstellungen" }).click();
  const dlg = page.getByRole("dialog", { name: "Einstellungen" });
  await expect(dlg.getByTestId("settings-preview").getByRole("columnheader", { name: "Kunde" })).toBeVisible();
  await dlg.getByRole("checkbox", { name: "Kunde" }).uncheck();
  await expect(dlg.getByTestId("settings-preview").getByRole("columnheader", { name: "Kunde" })).toHaveCount(0);
  await dlg.getByRole("button", { name: "Übernehmen" }).click();
  await expect(page.getByTestId("widget-demo-list-1").getByRole("columnheader", { name: "Kunde" })).toHaveCount(0);
});
test("draft survives a reload and can be restored; Verwerfen clears it", async ({ page }) => {
  await page.goto("/agent"); await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByTestId("widget-demo-list-1").getByRole("button", { name: "Entfernen" }).click();
  await expect(page.getByText(/Entwurf gesichert/)).toBeVisible();
  await page.reload(); await page.getByRole("button", { name: "Anpassen" }).click();
  await page.getByRole("button", { name: /Entwurf von .* wiederherstellen/ }).click();
  await expect(page.getByTestId("widget-demo-list-1")).toHaveCount(0);
  await page.getByRole("button", { name: "Verwerfen" }).click(); await page.getByRole("button", { name: "Änderungen verwerfen" }).click();
  await page.getByRole("button", { name: "Anpassen" }).click();
  await expect(page.getByRole("button", { name: /wiederherstellen/ })).toHaveCount(0);
});
test("two tabs: saving in the second after the first saved shows the conflict dialog and keeps the edit", async ({ browser }) => {
  const ctx = await browser.newContext(); const a = await ctx.newPage(); const b = await ctx.newPage();
  for (const p of [a, b]) { await mockApi(p); await loginAsAgent(p); await p.goto("/agent"); await p.getByRole("button", { name: "Anpassen" }).click(); }
  await a.getByTestId("widget-demo-list-1").getByRole("button", { name: "Entfernen" }).click(); await a.getByRole("button", { name: "Speichern" }).click();
  await b.getByTestId("widget-demo-list-2").getByRole("button", { name: "Entfernen" }).click(); await b.getByRole("button", { name: "Speichern" }).click();
  await expect(b.getByText(/inzwischen geändert/)).toBeVisible();
  await expect(b.getByRole("toolbar", { name: "Bearbeiten" })).toBeVisible();
});
test("a second dashboard via '+' and switching tabs", async ({ page }) => {
  await page.goto("/agent");
  await page.getByRole("button", { name: "Neues Dashboard" }).click(); await page.getByLabel("Name").fill("Mein Tag"); await page.getByRole("button", { name: "Anlegen" }).click();
  await expect(page.getByRole("tab", { name: "Mein Tag", selected: true })).toBeVisible();
  await page.getByRole("tab", { name: "Mein Start" }).click();
  await expect(page.getByTestId("dashboard-grid").locator("[data-type='core.counter']")).toHaveCount(4);
});
test("phone: single column, edit offers only up/down and settings", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/agent");
  await expect(page.getByTestId("dashboard-grid")).toHaveAttribute("data-breakpoint", "sm");
  await page.getByRole("button", { name: "Anpassen" }).click();
  const first = page.locator("[data-type='core.escalations']");
  await expect(first.getByRole("button", { name: "Nach oben" })).toBeVisible();
  await expect(first.getByRole("button", { name: "Einstellungen" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Duplizieren" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Widget hinzufügen" })).toHaveCount(0);
  const before = await first.boundingBox(); await first.getByRole("button", { name: "Nach oben" }).click();
  expect((await first.boundingBox())!.y).toBeLessThan(before!.y);
});
test("a widget of an unknown type shows the unavailable tile and survives a save", async ({ page }) => {
  await page.goto("/agent"); await page.getByRole("tab", { name: "Alt" }).click();                  // the e2e store seeds a dashboard "Alt" holding org.example.gone
  await expect(page.getByText("Widget nicht verfügbar")).toBeVisible();
  await page.getByRole("button", { name: "Anpassen" }).click(); await page.getByRole("button", { name: "Speichern" }).click();
  await page.reload(); await page.getByRole("tab", { name: "Alt" }).click();
  await expect(page.getByText("Widget nicht verfügbar")).toBeVisible();
});
for (const [theme, vp] of [["light", { width: 1280, height: 900 }], ["dark", { width: 1280, height: 900 }], ["light", { width: 390, height: 844 }], ["dark", { width: 390, height: 844 }]] as const) {
  test(`visual ${theme} ${vp.width}`, async ({ page }) => {
    await page.clock.setFixedTime(new Date("2026-10-10T07:00:00Z"));                                  // stable greeting and times
    await page.emulateMedia({ colorScheme: theme }); await page.setViewportSize(vp); await page.goto("/agent");
    await expect(page.getByTestId("dashboard-grid")).toBeVisible();
    await expect(page).toHaveScreenshot(`dashboard-view-${theme}-${vp.width}.png`, { maxDiffPixelRatio: 0.02 });
    await page.getByRole("button", { name: "Anpassen" }).click();
    await expect(page).toHaveScreenshot(`dashboard-edit-${theme}-${vp.width}.png`, { maxDiffPixelRatio: 0.02 });   // baselines: run once with --update-snapshots
  });
}
```

- [ ] **Step 2: Run** `cd frontend && pnpm build && pnpm exec playwright test e2e/dashboard.spec.ts e2e/screenshots.spec.ts` → fix until PASS (the home-page `screenshots.spec.ts` step regenerates `docs/images/agent-dashboard.png` only in CI; do not commit regenerated images locally unless the spec's own convention says so).
- [ ] **Step 3: Docs** — `docs/dashboards.md` (German-first user/admin guide: Anpassen, Widgets, Einstellungen, Teilen, Vorlagen und verbindliche Vorlagen, Berechtigungen "jeder sieht nur Tickets aus seinen Queues", Tastaturbedienung, Smartphone; admin: roles, locked; developer note: how a plugin adds a widget via `DASHBOARD_WIDGETS`, how a core data provider is registered), `docs/architecture.md`, `docs/compatibility.md` as listed. Link `docs/dashboards.md` from `docs/README.md`.
- [ ] **Step 4: Full verification** — backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (expect all green, `db_leak_baseline.txt` unchanged), lint gates; frontend: `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; openapi regenerated and committed; demo-mode Playwright walkthrough (1280/390 px, light/dark); `rtk git status` shows only intended files; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** — `docs(dashboard): user guide, architecture and compatibility; e2e flows`.

---

## Self-review notes (for the executor)

- **Spec coverage (phase 1):** data model → Task 1–3; layout engine + edit mode → Tasks 8, 12; gallery → 13; settings forms → 14 (+17 for sources); batched endpoint → 5; phase-1 widgets → 6 (providers) + 10 (components); default template = today's home page → Task 2 (`defaults.py`) + Task 3 (`ensure_start`) + Task 11 (greeting stays); role templates incl. mandatory → Tasks 3, 7, 15; phone behaviour → 11, 12; sharing → 16; plugin widgets → Task 10 registration + 13 gallery. Phase 2/3 items (report widgets, dashboard filters, drill-down, wallboard/kiosk, template-update merge, export/import, AI brief) are **not** built; hooks: `filters` column (always `'{}'`), `ProviderContext.filters`, `tiqora_dashboard.source_template_version`, `WidgetMode "wallboard"`, `refreshOn`, the `run_report_ref` seam described in the reporting plan (D12) for a later `core.report` provider.
- **Name consistency to re-check before dispatch:** `WidgetOut`/`DashboardOut`/`DashboardTabOut`/`TemplateOut` (no clash with `DashboardSummary`), `EditWidget`, `EditAction` variants, `useWidgetData` states (`loading|ok|pending|error|forbidden|none`), provider keys == core `type_key`s == `dataEndpoint` strings, preset names (`mine_open`, `mine_new`, `unowned_new`, `escalated`, `ai_escalated`), `ColumnKey` list identical in Task 2 and Task 10, sort literals `age|changed|priority|deadline` (Task 2) and the sort-combo table of Task 14, test ids `dashboard-grid`, `widget-<id>`, `widget-<id>-handle`, `drop-target`, `settings-preview`.
- **Sequencing risk:** Tasks 10–15 import `frontend/src/plugins/dashboardWidgets.ts`; if plugin Task 13 is not committed, stop and report instead of stubbing the file.
