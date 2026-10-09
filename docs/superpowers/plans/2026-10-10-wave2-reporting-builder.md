# Wave 2 — Reporting Builder, Saved Reports, Saved Searches (Reporting phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents build their own reports in a three-pane builder (source + Zeilen/Spalten/Werte/Filter/Zeitraum, live preview, Darstellung), save them as `tiqora_report` rows with Znuny-parity `stats` group rights and private/group/all sharing, run them by id everywhere a built-in runs (viewer, export, jobs, and in-process for the coming dashboard widgets); agents save ticket searches (`tiqora_saved_search`) with sharing, sidebar entries and live counts, and import their Znuny search profiles read-only with a report of everything that could not be taken over.

**Architecture:** Backend: new modules `tiqora.reporting.rights` (view/create/share rule), `tiqora.reporting.saved` (saved-report store, visibility, report-key resolution), `tiqora.reporting.run_service` (the inline-or-job run path, moved out of the route, plus `RunOverrides` incl. dashboard filters), `tiqora.permissions.sharing` (groups a user may share with), and a new package `tiqora.saved_searches` (store, API models, Znuny search-profile mapper + read-only reader). Saved searches use the wave-0 filter language over the **live ticket filter catalog** (`tiqora.filters.tickets`) and a new `POST /api/v1/tickets/query` that lists tickets by a `Filter`. Frontend: a shared `FilterBuilder`, a pure builder state module, the builder page `/agent/reports/new` + `/agent/reports/$reportKey/edit`, saved reports in the reports overview and viewer, saved searches in the sidebar (`/agent/searches/*`), and a Znuny import dialog.

**Tech Stack:** FastAPI, SQLAlchemy 2 async (ORM + Core `Table` for Znuny's key-less `search_profile`), Alembic two-chain, Pydantic v2, PyYAML (`safe_load`, aliases rejected), React 19 + TanStack Router/Query, Tailwind tokens, Vitest + Testing Library, MSW demo mode, Playwright (tag-only e2e).

**Spec:** `docs/superpowers/specs/2026-10-08-reporting-analytics-design.md` — phase 2 row of "Phases" (§3 saved definitions + permissions, §6 builder UI, §8 saved searches, "API / UI surface" for reports CRUD and `/saved-searches`). Builds on `docs/superpowers/plans/2026-10-08-wave1-reporting-foundation.md` (fully implemented; its Design R0–R14 as amended by `.superpowers/sdd/2026-10-08-wave1-reporting-foundation/rulings.md` and the final-fix wave) and on wave 0 (`docs/superpowers/plans/2026-10-08-wave0-foundations.md`: jobs, filter language, `JobDialog`). Master: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md`. Consumed later by the dashboards plan (`docs/superpowers/specs/2026-10-08-dashboard-widgets-design.md`, phase 1 ticket-list widget "saved search", phase 2 "report chart/table (any saved report)") — see Design D12.

## Global Constraints

- Work in the worktree `<repo>` on the local branch `roadmap-wave1`; never touch `<repo>`; never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data (example.org, .test). Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows and never get added to `backend/tests/db_leak_baseline.txt`.
- Seed ids only inside this plan's band **48000–48399** (sub-bands per task below; confirm free with `grep -rn "48[0-3][0-9][0-9]" backend/tests` before use, skill `pytest-session-container-id-band-collisions`). Logins/names contain `rep48`. Znuny `search_profile` has no id column: test rows use logins `TicketSearch::rep48.<x>` and are deleted by `login LIKE 'TicketSearch::rep48%'`.
- DB tests that touch SQL run on BOTH dialects (`@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])`, pattern `backend/tests/test_reports_api.py`; skeleton in D13) and create the new tables with `TiqoraBase.metadata.create_all`.
- Migrations: Alembic two-chain setup; new migration in `backend/alembic/versions_tiqora`; `down_revision` = the CURRENT primary head at implementation time (`ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — never hardcode beyond "current head"); follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and `version == …` in `backend/tests/test_migrate_cli.py`. ORM in `backend/src/tiqora/db/tiqora/models.py` must produce the migration's DDL (`backend/tests/test_tiqora_model_ddl.py`). Tables only `tiqora_*`; never change Znuny DDL; writes to Znuny tables must be behaviourally identical to Znuny (history rows, change_time, TiqoraSync cache invalidation) — **this plan writes no Znuny table at all**.
- MySQL/MariaDB AND PostgreSQL must both work. Prod MariaDB mixes collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list (integer joins/IN-lists on `permission_groups.id`, `users.id` are fine).
- Znuny SysConfig/YAML checkbox values are Perl-truthy strings ('0' is false): always read them with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`.
- Long-running endpoints (> a few seconds possible) use the wave-0 job system (prod nginx 90 s timeout). Job results that contain scoped data are `result_private` (report jobs already are).
- Timestamps the application writes into the new `tiqora_*` columns (`created_at`, `changed_at`) are naive UTC from `tiqora.jobs.service.utc_now()`; API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- After any API model/route change: regenerate both openapi files and schema.d.ts with `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build` per skill `tiqora-openapi-regen-or-ci-wipes-schema`; check `head -c 40 packages/api-client/openapi.json` starts `{"components"` and the diff stat is tens/hundreds of lines; never hand-edit schema.d.ts. A Pydantic response field WITH a default becomes REQUIRED in the generated TS type — fixtures, demo mocks and `frontend/e2e/fixtures/mock-api.ts` must carry it in the same commit (run `cd frontend && pnpm lint` to find them).
- Frontend: `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`; never run prettier/biome on frontend/. New i18n keys in en.json + de.json, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). Frontend tasks that add i18n keys run sequentially, never in parallel (wave-1 ruling R-I18NSEQ). New screens get demo-mode mocks (VITE_DEMO=1) and are checked in demo mode with Playwright at 1280 px and 390 px, light + dark (delete `.playwright-mcp` before committing). Tailwind colour tokens only via `themeColor()`. API wrappers are hand-written `frontend/src/lib/*Api.ts` using `api.request<T>(method, path, {query, body, signal})` with `Schemas["…"]` types.
- UI copy German first, plain words, named by what admins/agents recognise ("Bericht erstellen", "Zeilen", "Spalten", "Werte", "Filter", "Zeitraum", "Gespeicherte Suchen", "Suchvorlagen aus Znuny übernehmen"); no system jargon in UI text ("Definition", "Quelle" is fine as "Datenbasis", never "Source", "Semantic layer", "Filter-JSON").
- Znuny reference source (6.5 core) at `<repo>/znuny-6.5.22/` (read-only) for behaviour checks; Znuny 7.3 golden peer image exists for golden tests.
- Charts reuse the wave-1 components and dataviz rules unchanged (no new chart types in this plan).

## Review Focus

1. **A viewer who may not see a saved report** — a colleague's private report, a report shared with a group the viewer has left, a deleted report — addressed by id through `GET /reports/{key}`, `POST /reports/run`, `POST /reports/export`, `GET /reports/{key}/file`, or through a job queued before the report was unshared → 404 (410 for a deleted one the viewer could see), the job ends failed "nicht mehr gültig"; never the definition or a result. Pinned in Task 3 (`test_invisible_report_is_404_on_every_route`) and Task 4 (`test_job_fails_after_report_unshared`).
2. **A shared report or saved search runs with the viewer's permissions** — a colleague shares a search/report over queue Q the viewer cannot read → the viewer gets 0 rows / count 0, never the owner's numbers (also from the result cache). Pinned in Task 4 (`test_shared_report_runs_in_viewer_scope`) and Task 6 (`test_shared_search_lists_only_viewer_queues`).
3. **Znuny search profiles with things Tiqora cannot express** — full-text/article fields, future-relative times ("Next"/"After"), ticket numbers with search operators, an unknown dynamic field or state type id, a date dynamic field stored with Format/Start swapped → each such key appears in the import report with a German reason, the mappable rest is imported correctly, nothing is silently dropped, and `search_profile` is never written. Pinned in Task 7 (`test_every_unmappable_key_is_reported`, `test_dynamic_date_accepts_swapped_format_and_start`) and Task 8 (`test_import_never_writes_search_profile`).
4. **A stored definition or criteria that no longer validates** — dynamic field deleted, plugin source removed, field renamed by a later version → the list still loads (a saved report whose JSON no longer parses is marked broken), opening/running/querying answers 422 with a German message, the sidebar count is `null`; never a 500. Pinned in Task 3 (`test_broken_definition_lists_and_runs_422`) and Task 6 (`test_deleted_dynamic_field_marks_search_broken`).
5. **Two tabs edit the same saved report/search** (or owner and admin at once) → the second save gets 409 with a German "inzwischen geändert" message and nothing is overwritten. Pinned in Task 3 (`test_update_with_stale_version_is_409`) and Task 6 (`test_search_update_with_stale_version_is_409`).

Also pinned (not top five): a YAML report file with anchors/aliases (alias bomb) is rejected (Task 3); a filter nested deeper than the builder can show is displayed read-only, never mangled on save (Task 11); dashboard filter keys a source cannot apply are reported back, never silently ignored (Task 4); customer-user erasure rewrites the erased login inside saved criteria (Task 9).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Wave-0 and wave-1 names are used exactly as they exist in code today: `Filter`, `Condition`, `AllOf`, `AnyOf`, `Not`, `validate_filter`, `iter_conditions`, `FilterFieldSpec`, `FilterError`, `OPS_BY_TYPE`, `TicketQueryScope`, `ticket_query_scope`, `compile_ticket_filter`, `count_tickets`, `ticket_filter_fields`, `TicketListParams`, `filter_from_list_params`, `enqueue_job`, `utc_now`, `JobAccepted`, `JobFailed`, `lock_user_job_quota`, `count_active_jobs`, `ReportDefinition` (`schema_version: 1`), `ReportParameter`, `RunParams` (incl. internal `extra_filter`), `validate_definition`, `definition_hash`, `DefinitionError`, `ReportCompileError`, `REPORT_SOURCES`, `ReportSource`, `BUILTIN_REPORTS`, `plan_report`, `estimate_rows`, `execute_and_store`, `run_report`, `QueryBudget`, `ReportCache`, `read_cache_generation`, `ReportQueryTimeout`, `has_report_access`, `require_report_access`, `STATS_GROUP_NAME`, `load_reporting_settings`, `ReportingSettings`, `prepare_run`, `RequestError`, `REPORT_RUN_JOB`, `REPORT_EXPORT_JOB`, `ReportRunPayload`, `MAX_ACTIVE_REPORT_JOBS_PER_USER`, `EVENTS_INLINE_MAX_DAYS`, `get_report_cache`, `export_slug`, `UserMe.can_view_reports`, frontend `reportsApi`, `useReportRun`, `ReportView`, `ReportFilterBar`, `ReportsAccessGate`, `JobDialog`, `TicketTable`.

### D1. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_saved_reports_searches.py` | five tables of D2 |
| modify `backend/src/tiqora/db/tiqora/models.py` | `TiqoraReport`, `TiqoraReportGroup`, `TiqoraSavedSearch`, `TiqoraSavedSearchGroup`, `TiqoraSavedSearchPref` |
| `backend/src/tiqora/permissions/sharing.py` | `ShareGroup`, `share_groups_for`, `check_share_groups`, `ShareGroupError` |
| `backend/src/tiqora/reporting/rights.py` | `ReportRights`, `report_rights` |
| modify `backend/src/tiqora/reporting/access.py` | `stats_group_id` helper shared with `rights.py` |
| `backend/src/tiqora/reporting/saved.py` | saved-report store, visibility, `resolve_report_ref` |
| `backend/src/tiqora/reporting/report_file.py` | `ReportFileOut`, `ReportFileImportIn`, `dump_report_file`, `parse_report_file` |
| `backend/src/tiqora/reporting/run_service.py` | `RunOverrides`, `RunRefOutcome`, `prepare_ref`, `prepare_run` (moved), `RequestError` (moved), `run_report_ref`, `enqueue_report_ref`, `TooManyReportJobs`, `common_filter`, `COMMON_FILTER_KEYS` |
| modify `backend/src/tiqora/reporting/jobs.py` | re-export `prepare_run`/`RequestError`; `ReportRunPayload.extra_filter` |
| modify `backend/src/tiqora/reporting/semantic.py`, `sources/*.py` | `ReportSource.common_fields` |
| modify `backend/src/tiqora/reporting/schemas.py` | `ReportOut` (replaces `BuiltinReportOut`), `ReportOwnerOut`, `SavedReportIn`, `SavedReportUpdateIn`; `SourceDimensionOut.ops/enum_values`; `ReportSourceOut.common_fields` |
| modify `backend/src/tiqora/api/v1/reports.py` | catalogue with saved reports, CRUD, file export/import, run via `run_service` |
| modify `backend/src/tiqora/api/v1/reference.py` | `GET /reference/share-groups` |
| modify `backend/src/tiqora/domain/schemas.py` | `UserMe.can_create_reports`, `UserMe.can_share_reports`, `TicketSortKey` |
| modify `backend/src/tiqora/api/v1/auth.py` | fill the two flags |
| modify `backend/src/tiqora/domain/ticket_service.py` | public `page_from_stmt` (extracted tail of `list_tickets`) |
| modify `backend/src/tiqora/api/v1/tickets.py` | `POST /tickets/query` (registered before `/{ticket_id}`) |
| `backend/src/tiqora/saved_searches/__init__.py` | docstring only |
| `backend/src/tiqora/saved_searches/store.py` | saved-search store |
| `backend/src/tiqora/saved_searches/schemas.py` | API models |
| `backend/src/tiqora/saved_searches/znuny_import.py` | pure mapper Znuny profile → `Filter` |
| `backend/src/tiqora/saved_searches/znuny_reader.py` | read-only DB reader + lookups |
| `backend/src/tiqora/api/v1/saved_searches.py` | `/api/v1/saved-searches*` |
| modify `backend/src/tiqora/db/legacy/misc.py` | `SEARCH_PROFILE` Core `Table` (own `MetaData`, never created) |
| `backend/src/tiqora/gdpr/saved_criteria.py` | `replace_filter_values`, `scrub_saved_criteria` |
| modify `backend/src/tiqora/gdpr/erasure.py` | call `scrub_saved_criteria` |
| modify `backend/src/tiqora/api/v1/__init__.py` | register `saved_searches.router` |

Frontend (new unless marked):

| Path | Responsibility |
|---|---|
| modify `frontend/src/lib/reportsApi.ts` | `ReportItem`, saved CRUD, `fileUrl`, `importFile`, `reportTitle` |
| `frontend/src/lib/savedSearchesApi.ts` | saved searches, counts, Znuny import, `queryTickets`, `shareGroups` |
| `frontend/src/components/filters/filterModel.ts` | pure: `FilterFieldDef`, ops per type, editable tree ⇄ `Filter`, validation |
| `frontend/src/components/filters/filterFields.ts` | catalog adapters (`fieldsFromTicketCatalog`, `fieldsFromSource`) |
| `frontend/src/components/filters/useFilterOptions.ts` | options for id fields from `/reference/*` |
| `frontend/src/components/filters/FilterBuilder.tsx`, `FilterValueInput.tsx` | the shared filter editor |
| `frontend/src/components/reports/builder/builderState.ts` | pure reducer, validation, definition ⇄ state |
| `frontend/src/components/reports/builder/BuilderFieldsPanel.tsx`, `BuilderPreview.tsx`, `BuilderOptionsPanel.tsx`, `BuilderParametersEditor.tsx`, `SaveReportDialog.tsx`, `ShareFields.tsx` | builder UI parts |
| `frontend/src/routes/agent/ReportBuilderPage.tsx` | `/agent/reports/new`, `/agent/reports/$reportKey/edit` |
| `frontend/src/components/reports/ReportParameterControls.tsx` | generic runtime parameter controls in the viewer |
| modify `frontend/src/routes/agent/ReportsPage.tsx`, `ReportViewPage.tsx`, `reportViewSearch.ts`, `components/reports/reportFilter.ts` | saved reports in list + viewer |
| `frontend/src/routes/agent/SavedSearchPage.tsx` | `/agent/searches/$searchId` (ticket list) |
| `frontend/src/routes/agent/SavedSearchEditorPage.tsx` | `/agent/searches/new`, `/agent/searches/$searchId/edit` |
| `frontend/src/components/searches/SavedSearchNav.tsx` | sidebar group with counts |
| `frontend/src/components/searches/SaveViewDialog.tsx` | "Ansicht speichern" from the ticket list |
| `frontend/src/components/searches/ZnunyImportDialog.tsx` | import preview + apply |
| modify `frontend/src/router.tsx`, `components/layout/AgentShell.tsx`, `routes/agent/QueuesPage.tsx` | routes, sidebar group, save button |
| `frontend/src/demo/savedMock.ts` (+ modify `demo/mockData.ts`, `demo/reportsMock.ts`, `demo/reportsCatalogue.ts`) | demo data |
| modify `frontend/e2e/fixtures/mock-api.ts`; new `frontend/e2e/report-builder.spec.ts`, `frontend/e2e/saved-searches.spec.ts` | e2e |

### D2. Tables (one migration, Task 1)

```
tiqora_report                                     -- saved report definitions
  id               BIGINT PK autoincrement
  name             VARCHAR(200) NOT NULL
  description      TEXT NULL
  owner_user_id    INT NOT NULL                   -- Znuny users.id (no FK)
  visibility       VARCHAR(10) NOT NULL default 'private'   -- private | groups | all
  definition       TEXT NOT NULL (MEDIUMTEXT on mysql/mariadb; MEDIUM_TEXT_VARIANT)  -- ReportDefinition JSON
  schema_version   SMALLINT NOT NULL              -- definition.schema_version when saved
  version          INT NOT NULL default 1         -- optimistic lock, +1 per update
  valid            BOOL NOT NULL default true     -- false = deleted (kept: links/widgets say "gelöscht")
  copied_from      VARCHAR(120) NULL              -- "builtin.<name>" or "<id>"
  created_at       DATETIME NOT NULL
  created_by       INT NOT NULL
  changed_at       DATETIME NOT NULL
  changed_by       INT NOT NULL
  INDEX ix_tiqora_report_owner (owner_user_id, valid)
  INDEX ix_tiqora_report_visibility (visibility, valid)

tiqora_report_group                               -- groups a "groups" report is shared with
  report_id   BIGINT NOT NULL  FK fk_tiqora_report_group_report -> tiqora_report.id ON DELETE CASCADE
  group_id    INT NOT NULL                        -- Znuny permission_groups.id (no FK)
  PRIMARY KEY (report_id, group_id)
  INDEX ix_tiqora_report_group_group (group_id)

tiqora_saved_search
  id               BIGINT PK autoincrement
  owner_user_id    INT NOT NULL
  name             VARCHAR(200) NOT NULL
  criteria         TEXT NOT NULL (MEDIUM_TEXT_VARIANT)   -- wave-0 Filter JSON over the ticket filter catalog
  include_archived BOOL NOT NULL default false     -- honoured for admins only, like GET /tickets
  sort_by          VARCHAR(20) NOT NULL default 'age'    -- TicketSortKey
  sort_order       VARCHAR(4) NOT NULL default 'desc'
  source           VARCHAR(10) NOT NULL default 'tiqora' -- tiqora | znuny
  source_name      VARCHAR(200) NULL               -- Znuny profile name (re-import key)
  version          INT NOT NULL default 1
  created_at       DATETIME NOT NULL
  changed_at       DATETIME NOT NULL
  INDEX ix_tiqora_saved_search_owner (owner_user_id)
  UNIQUE uq_tiqora_saved_search_source (owner_user_id, source, source_name)   -- NULL source_name never collides

tiqora_saved_search_group
  saved_search_id  BIGINT NOT NULL FK fk_tiqora_saved_search_group_search -> tiqora_saved_search.id ON DELETE CASCADE
  group_id         INT NOT NULL
  PRIMARY KEY (saved_search_id, group_id)
  INDEX ix_tiqora_saved_search_group_group (group_id)

tiqora_saved_search_pref                          -- per viewer: sidebar entry, count badge, order
  saved_search_id  BIGINT NOT NULL FK fk_tiqora_saved_search_pref_search -> tiqora_saved_search.id ON DELETE CASCADE
  user_id          INT NOT NULL
  show_in_sidebar  BOOL NOT NULL default true
  show_count       BOOL NOT NULL default false
  position         INT NOT NULL default 0
  PRIMARY KEY (saved_search_id, user_id)
  INDEX ix_tiqora_saved_search_pref_user (user_id, position)
```

Booleans `server_default=false()` / `true()`; FK columns of the junction tables `autoincrement=False`. No row is seeded by the migration.

### D3. Rights (Task 2)

```python
# backend/src/tiqora/reporting/access.py (addition; has_report_access uses it)
async def stats_group_id(session: AsyncSession) -> int | None
    # id of the VALID permission_groups row named STATS_GROUP_NAME

# backend/src/tiqora/reporting/rights.py
@dataclass(frozen=True, slots=True)
class ReportRights:
    view: bool      # has_report_access (unchanged wave-1 rule)
    create: bool    # save PRIVATE reports (builder "Speichern")
    share: bool     # visibility "groups" / "all"
    admin: bool     # PermissionEngine.is_admin
async def report_rights(session: AsyncSession, user_id: int, reporting: ReportingSettings) -> ReportRights
    # view   = has_report_access(...)
    # admin  = PermissionEngine(session).is_admin(user_id)
    # rw     = stats group exists and its id in groups_for_permission(user_id, "rw")
    # create = view and (reporting.access == "all_agents" or admin or rw)
    # share  = view and (admin or rw)
```

`/auth/me` (`UserMe`): `can_create_reports: bool = False`, `can_share_reports: bool = False`, filled from `report_rights` with the same guard as `can_view_reports` (exception → False + rollback).

Edit/delete a saved report: `(report.owner_user_id == user_id and rights.create) or rights.admin`. Setting `visibility != "private"` (on create or update, also keeping a non-private visibility on update by someone without share right is allowed — only *changing to* or *changing the groups of* a shared visibility needs `share`).

### D4. Saved-report store (Task 2)

```python
# backend/src/tiqora/permissions/sharing.py
@dataclass(frozen=True, slots=True)
class ShareGroup:
    id: int
    name: str
class ShareGroupError(ValueError):              # German message, path "group_ids"
    def __init__(self, message: str) -> None
async def share_groups_for(session: AsyncSession, user_id: int) -> list[ShareGroup]
    # admin: every valid permission_groups row; else the valid groups in groups_for_permission(user_id, "ro"); sorted by name
async def check_share_groups(session: AsyncSession, user_id: int, group_ids: Sequence[int]) -> tuple[int, ...]
    # deduplicated sorted tuple; any id not in share_groups_for -> ShareGroupError("Mit der Gruppe … kann nicht geteilt werden.")

# backend/src/tiqora/reporting/saved.py
Visibility = Literal["private", "groups", "all"]
SAVED_KEY_RE: Final = re.compile(r"^[1-9][0-9]{0,17}$")     # report_key of a saved report = its decimal id
MAX_SAVED_REPORTS_PER_USER: Final = 500
class SavedReportIn(BaseModel):              # extra="forbid"
    name: str = Field(min_length=1, max_length=200)          # stripped; blank after strip -> 422
    description: str = Field("", max_length=2000)
    definition: ReportDefinition
    visibility: Visibility = "private"
    group_ids: list[int] = Field(default_factory=list, max_length=50)
    copied_from: str | None = Field(None, max_length=120)
class SavedReportUpdateIn(SavedReportIn):
    version: int = Field(ge=1)
@dataclass(frozen=True, slots=True)
class SavedReport:
    id: int; name: str; description: str; owner_user_id: int; visibility: Visibility
    group_ids: tuple[int, ...]; definition: ReportDefinition | None; definition_error: str | None
    version: int; valid: bool; copied_from: str | None; created_at: datetime; changed_at: datetime
class ReportNotFound(LookupError): ...           # unknown id, or not visible to the viewer
class ReportGone(LookupError):                   # deleted (valid = false) and was visible
    def __init__(self, name: str) -> None
class ReportForbidden(PermissionError):          # German message
    def __init__(self, message: str) -> None
class ReportVersionConflict(Exception):
    def __init__(self, current_version: int) -> None
def load_definition(raw: str) -> tuple[ReportDefinition | None, str | None]
    # ReportDefinition.model_validate_json; ValidationError -> (None, "Der gespeicherte Bericht kann nicht mehr gelesen werden.")
    # (the single place where a later schema_version upgrade goes)
def dump_definition(defn: ReportDefinition) -> str      # json.dumps(model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True)
async def visible_saved_reports(session: AsyncSession, user_id: int, *, admin: bool,
                                include_others_private: bool = False) -> list[SavedReport]
    # valid only; owner OR visibility "all" OR (visibility "groups" AND a group row IN groups_for_permission(user_id,"ro"))
    # admin + include_others_private -> every valid row; ordered by name, id
async def get_saved_report(session: AsyncSession, report_id: int, user_id: int, *, admin: bool) -> SavedReport
    # visible as above, or admin (any). Not found / not visible -> ReportNotFound; visible but valid = false -> ReportGone(name)
def can_edit_report(report: SavedReport, user_id: int, rights: ReportRights) -> bool
async def create_saved_report(session: AsyncSession, user_id: int, rights: ReportRights, body: SavedReportIn,
                              *, registry: Registry) -> SavedReport
    # rights.create else ReportForbidden; validate_definition(body.definition, registry) (DefinitionError -> caller 422,
    # path prefixed "definition."); visibility rules (D3; "groups" needs >= 1 group -> ShareGroupError;
    # "private"/"all" store no group rows); check_share_groups; per-user limit MAX_SAVED_REPORTS_PER_USER -> ReportForbidden;
    # timestamps utc_now(); version 1; flush (caller commits)
async def update_saved_report(session: AsyncSession, report_id: int, user_id: int, rights: ReportRights,
                              body: SavedReportUpdateIn, *, registry: Registry) -> SavedReport
    # get_saved_report (admin = rights.admin) -> can_edit_report else ReportForbidden;
    # body.version != row.version -> ReportVersionConflict(row.version); replace group rows; version + 1
    # (UPDATE ... WHERE id = :id AND version = :v, rowcount 0 -> ReportVersionConflict: two concurrent saves)
async def delete_saved_report(session: AsyncSession, report_id: int, user_id: int, rights: ReportRights) -> None
    # valid = false, changed_at/by; group rows kept (a later admin restore is not part of this plan)
@dataclass(frozen=True, slots=True)
class ResolvedReport:
    key: str                          # "builtin.<name>" or str(id)
    definition: ReportDefinition
    builtin: BuiltinReport | None
    saved: SavedReport | None
async def resolve_report_ref(session: AsyncSession, report_key: str, user_id: int, *, admin: bool,
                             registry: Registry | None = None) -> ResolvedReport
    # "builtin.*" -> BUILTIN_REPORTS (missing -> ReportNotFound); SAVED_KEY_RE -> get_saved_report
    # (definition None -> DefinitionError(definition_error, path="definition")); anything else -> ReportNotFound
```

### D5. Reports HTTP API (Tasks 3–4)

```python
# backend/src/tiqora/reporting/schemas.py (BuiltinReportOut is replaced by ReportOut everywhere)
class ReportOwnerOut(BaseModel):
    user_id: int
    name: str                                  # "First Last" (login when empty), via resolve_labels(session, "user", ...)
class ReportOut(BaseModel):
    key: str                                   # "builtin.<name>" | "<id>"
    kind: Literal["builtin", "saved"]
    id: int | None = None
    category: str | None = None                # built-ins
    title_key: str | None = None               # built-ins (i18n)
    description_key: str | None = None
    name: str | None = None                    # saved (literal)
    description: str | None = None
    definition: ReportDefinition | None        # None only for a saved report whose stored definition no longer loads
    definition_error: str | None = None
    overview: bool = False
    read_only: bool                            # built-in, or saved without edit right for this viewer
    owner: ReportOwnerOut | None = None
    visibility: Literal["private", "groups", "all"] | None = None
    group_ids: list[int] = Field(default_factory=list)
    version: int | None = None
    copied_from: str | None = None
    changed_at: UtcDateTime | None = None
class ReportCatalogueOut(BaseModel):          # unchanged shape, items are ReportOut now
    reports: list[ReportOut]                   # built-ins first (catalogue order), then saved by name
    facts: FactsStatusOut
class SourceDimensionOut(BaseModel):          # two new fields
    ...; ops: list[str] = Field(default_factory=list)          # sorted allowed filter ops; [] = not filterable
    enum_values: list[str] = Field(default_factory=list)
class ReportSourceOut(BaseModel):
    ...; common_fields: dict[str, str] = Field(default_factory=dict)   # D6 common filter key -> field key

# backend/src/tiqora/reporting/report_file.py
class ReportFileOut(BaseModel):               # the export/import file (JSON or YAML)
    format: Literal["tiqora-report"] = "tiqora-report"
    format_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    definition: ReportDefinition
class ReportFileImportIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
def dump_report_file(out: ReportFileOut, fmt: Literal["json", "yaml"]) -> bytes
    # json: indent 2, ensure_ascii False; yaml: yaml.safe_dump(..., allow_unicode=True, sort_keys=False)
def parse_report_file(content: str) -> ReportFileOut
    # JSON first; else YAML: any yaml.AliasEvent in yaml.parse(content, Loader=yaml.SafeLoader) ->
    # DefinitionError("Verweise (Anker) sind in Berichtsdateien nicht erlaubt.", path="content");
    # yaml.safe_load; not a mapping / wrong format -> DefinitionError(..., path="content"); pydantic errors ->
    # DefinitionError(first error msg, path=dotted loc)
```

Routes (`api/v1/reports.py`; every route `CurrentUser` + view access; registration order matters — static paths before `/{report_key}`):

```
GET    /api/v1/reports?all=false            -> ReportCatalogueOut   (all=true honoured for admins: others' private too;
                                               parameter `all_: bool = Query(False, alias="all")` — ruff A002)
POST   /api/v1/reports                      -> 201 ReportOut        (SavedReportIn; rights.create)
POST   /api/v1/reports/run                  -> 200 | 202 ReportRunOut   (report_key may be "<id>"; D6)
POST   /api/v1/reports/export               -> 202 JobAccepted          (report_key may be "<id>")
POST   /api/v1/reports/import-file          -> ReportFileOut        (ReportFileImportIn; parse + validate_definition; saves nothing)
GET    /api/v1/reports/{report_key}/file?format=json|yaml -> attachment "<export_slug(name)>.json|.yaml"
                                               (built-ins: name = the report key)
GET    /api/v1/reports/{report_key}         -> ReportOut            (404 unknown/invisible, 410 deleted)
PUT    /api/v1/reports/{report_id}          -> ReportOut            (int path; SavedReportUpdateIn; 403/404/409/410)
DELETE /api/v1/reports/{report_id}          -> 204
GET    /api/v1/reference/share-groups       -> list[ShareGroupOut{id, name}]   (any agent; used by reports, searches, dashboards)
```

Error bodies: `{"detail": {"message": <German>, "path": <optional>}}` like wave 1. 404 `"Bericht nicht gefunden."`; 410 `"Der Bericht „<name>“ wurde gelöscht."`; 409 `{"message": "Der Bericht wurde inzwischen geändert. Bitte neu laden.", "current_version": n}`; 403 from `ReportForbidden.message` (`"Für das Speichern von Berichten fehlt die Berechtigung (Gruppe „stats“, Schreibrecht)."`, `"Zum Teilen von Berichten fehlt die Berechtigung (Gruppe „stats“, Schreibrecht)."`, `"Nur der Ersteller oder ein Admin kann diesen Bericht ändern."`). API-key scopes: `/api/v1/reports*` already maps to `stats`; `/api/v1/reference/share-groups` falls under the existing `reference` area — verify in `api_key_scopes` and add a test line if it does not.

### D6. Running by key; dashboard seam (Task 4)

```python
# backend/src/tiqora/reporting/run_service.py
CommonFilterKey = Literal["queue", "state_type", "priority", "type", "service", "sla", "owner",
                          "customer", "customer_user", "channel"]
COMMON_FILTER_KEYS: Final[tuple[CommonFilterKey, ...]]           # in that order

class RequestError(Exception): ...            # moved verbatim from jobs.py (message, path, status_code)
class TooManyReportJobs(Exception): ...       # 429; message = the wave-1 German text

@dataclass(frozen=True, slots=True)
class RunOverrides:
    """What a caller may change when running a report (HTTP body, dashboard widget, job)."""
    time_range: TimeRange | None = None
    grain: Grain | None = None
    time_zone: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    dimension_choices: Mapping[int, str] = field(default_factory=dict)
    compare_previous: bool = False
    no_cache: bool = False
    #: narrowing filter in the SOURCE's field keys (ANDed like the /stats wrappers' extra_filter);
    #: in-process only (dashboards, jobs) - ReportRunIn deliberately has no such field
    extra_filter: Filter | None = None
    #: dashboard-level filters by common key; mapped through ReportSource.common_fields
    common: Mapping[str, Sequence[str | int]] = field(default_factory=dict)
    @classmethod
    def from_run_in(cls, body: ReportRunIn) -> RunOverrides   # non-int dimension_choices key -> RequestError (wave-1 text)

@dataclass(frozen=True, slots=True)
class PreparedRun:
    definition: ReportDefinition
    params: RunParams                          # extra_filter = overrides.extra_filter AND common filter
    report_key: str | None
    saved: SavedReport | None
    ignored_common: tuple[str, ...]            # common keys the source cannot apply

def common_filter(source: ReportSource, values: Mapping[str, Sequence[str | int]]) -> tuple[Filter | None, tuple[str, ...]]
    # every key with a non-empty value list: mapped -> Condition(field=source.common_fields[key], op="in", value=list(values));
    # not in COMMON_FILTER_KEYS or not mapped by the source -> ignored (sorted); several -> AllOf; none -> None

async def prepare_ref(session: AsyncSession, user_id: int, *, report_key: str | None,
                      definition: ReportDefinition | None, overrides: RunOverrides) -> PreparedRun
    # exactly one of report_key/definition (wave-1 RequestError text, path "report_key");
    # report_key -> resolve_report_ref(admin = PermissionEngine.is_admin): ReportNotFound -> RequestError(
    # "Bericht nicht gefunden.", path="report_key", 404); ReportGone -> RequestError(410 text, 410);
    # zone: overrides.time_zone -> definition.time_zone -> resolve_user_time_zone (wave-1 order);
    # source = REPORT_SOURCES[definition.source] (unknown -> DefinitionError path "source");
    # extra_filter = AllOf of (overrides.extra_filter.where, common_filter(...).where) when both set
async def prepare_run(session: AsyncSession, user_id: int, body: ReportRunIn,
                      *, extra_filter: Filter | None = None) -> tuple[ReportDefinition, RunParams, str | None]
    # wave-1 signature kept (+ keyword extra_filter for jobs): prepare_ref(..., RunOverrides.from_run_in(body) with extra_filter)

@dataclass(frozen=True, slots=True)
class RunRefOutcome:
    result: ReportResultOut | None
    deferred: Literal["forced", "events_window", "estimate"] | None   # set -> caller enqueues (or shows "pending")
    extra_filter: Filter | None                                       # effective narrowing filter a job must carry
    ignored_common: tuple[str, ...]

async def run_report_ref(session: AsyncSession, *, user_id: int, report_key: str | None,
                         definition: ReportDefinition | None, overrides: RunOverrides,
                         reporting: ReportingSettings, cache: ReportCache | None, budget: QueryBudget,
                         force_job: bool = False) -> RunRefOutcome
    # the wave-1 route body, unchanged in behaviour: prepare_ref -> plan_report -> cache.get (unless no_cache)
    # -> deferral checks (force_job; ticket_events window > EVENTS_INLINE_MAX_DAYS; estimate_rows > inline_row_threshold)
    # -> execute_and_store. Raises RequestError, DefinitionError, FilterError, ReportQueryTimeout.

async def enqueue_report_ref(session: AsyncSession, *, user_id: int, run: ReportRunIn,
                             extra_filter: Filter | None) -> JobAccepted
    # session.rollback(); lock_user_job_quota; cap over core.report_run + core.report_export (include_cancelling=False)
    # >= MAX_ACTIVE_REPORT_JOBS_PER_USER -> TooManyReportJobs; enqueue_job(kind="core.report_run",
    # payload=ReportRunPayload(user_id, run=run, extra_filter=extra_filter), created_by=user_id); commit
```

`MAX_ACTIVE_REPORT_JOBS_PER_USER` and `EVENTS_INLINE_MAX_DAYS` move to `run_service.py`; `api/v1/reports.py` re-imports them for readers, but `run_service` uses its own module attributes, `estimate_rows`, `execute_and_store`, `enqueue_job` and `count_active_jobs` imported into `run_service`'s namespace — tests patch them there (Task 4 re-points the wave-1 patches). `jobs.py` keeps `from tiqora.reporting.run_service import RequestError, prepare_run` (re-export). `ReportRunPayload` gains `extra_filter: Filter | None = None` (older queued payloads stay valid).

`ReportSource.common_fields: Mapping[str, str]` (frozen dataclass field, `field(default_factory=dict)`), set by the core sources:

| source | queue | state_type | priority | type | service | sla | owner | customer | customer_user | channel |
|---|---|---|---|---|---|---|---|---|---|---|
| `tickets` | queue | state_type | priority | type | service | sla | owner | customer | customer_user | channel |
| `open_tickets` | queue | state_type | priority | type | service | sla | owner | customer | customer_user | — |
| `ticket_events` | ticket_queue | — | ticket_priority | ticket_type | — | — | — | customer | — | channel |
| `accounted_time` | queue | — | — | type | service | — | — | customer | customer_user | — |
| `daily_snapshot` | queue | state_type | priority | — | — | — | — | — | — | — |

Every mapped field must resolve through `source.filter_spec(field)` (Task 4 test).

### D7. Tickets by filter (Task 5)

```python
# backend/src/tiqora/domain/schemas.py
TicketSortKey = Literal["age", "created", "changed", "tn", "title", "priority", "activity", "deadline"]
# == keys of TicketService._SORT_COLUMNS (test)

# backend/src/tiqora/domain/ticket_service.py
async def page_from_stmt(self, user_id: int, stmt: Select[tuple[Ticket]] | None, *, offset: int, limit: int,
                         sort: str, order: str) -> PaginatedTickets
    # the tail of list_tickets (count, _order_by, page, enrichment) moved here; list_tickets calls it

# backend/src/tiqora/api/v1/tickets.py
class TicketQueryIn(BaseModel):              # extra="forbid"
    filter: Filter
    include_archived: bool = False          # admins only (others: silently False, like GET /tickets)
    sort: TicketSortKey = "age"
    order: Literal["asc", "desc"] = "desc"
    offset: int = Field(0, ge=0, le=100_000)
    limit: int = Field(50, ge=1, le=200)
POST /api/v1/tickets/query -> PaginatedTickets      # FilterError -> 422 {"message","path"}; registered before /{ticket_id}
```

The route builds `ticket_query_scope(session, user.id, include_archived=granted)`, `compile_ticket_filter(session, body.filter, scope)` and `TicketService(session).page_from_stmt(...)` (no import of `filters.tickets` from `ticket_service.py` — that would be circular).

### D8. Saved searches (Task 6)

```python
# backend/src/tiqora/saved_searches/store.py
MAX_SAVED_SEARCHES_PER_USER: Final = 200
MAX_SIDEBAR_COUNTS: Final = 15
COUNT_TIMEOUT_SECONDS: Final = 5
@dataclass(frozen=True, slots=True)
class SavedSearch:
    id: int; owner_user_id: int; name: str
    criteria: Filter | None; criteria_error: str | None        # None + error when stored JSON no longer parses/validates
    include_archived: bool; sort_by: str; sort_order: str
    source: Literal["tiqora", "znuny"]; source_name: str | None
    group_ids: tuple[int, ...]; version: int; changed_at: datetime
@dataclass(frozen=True, slots=True)
class SavedSearchPrefs:
    show_in_sidebar: bool; show_count: bool; position: int
class SavedSearchNotFound(LookupError): ...
class SavedSearchForbidden(PermissionError): ...           # German message
class SavedSearchConflict(Exception):
    def __init__(self, current_version: int) -> None
async def validate_criteria(session: AsyncSession, flt: Filter) -> None
    # validate_filter(flt, {f.key: f for f in await ticket_filter_fields(session)}.get) -> FilterError
async def list_visible(session: AsyncSession, user_id: int) -> list[tuple[SavedSearch, SavedSearchPrefs]]
    # own + shared with a group in groups_for_permission(user_id, "ro"); ordered by prefs.position, name.
    # Default prefs without a row: owner (True, False, 0); others (False, False, 0)
async def get_visible(session: AsyncSession, search_id: int, user_id: int, *, admin: bool) -> tuple[SavedSearch, SavedSearchPrefs]
async def create_search(session: AsyncSession, user_id: int, data: SavedSearchData) -> SavedSearch
async def update_search(session: AsyncSession, search_id: int, user_id: int, data: SavedSearchData, *,
                        version: int, admin: bool) -> SavedSearch      # owner or admin; version check like D4
async def set_prefs(session: AsyncSession, search_id: int, user_id: int, prefs: SavedSearchPrefs) -> None   # upsert (delete+insert)
async def delete_search(session: AsyncSession, search_id: int, user_id: int, *, admin: bool) -> None        # hard delete (CASCADE)
async def upsert_imported(session: AsyncSession, user_id: int, *, source_name: str, criteria: Filter,
                          include_archived: bool) -> tuple[int, Literal["created", "updated"]]
    # keyed by (owner, "znuny", source_name); name = source_name on create; update replaces criteria, version + 1
@dataclass(frozen=True, slots=True)
class SavedSearchData:
    name: str; criteria: Filter; include_archived: bool; sort_by: str; sort_order: str; group_ids: tuple[int, ...]

# backend/src/tiqora/saved_searches/schemas.py
class SavedSearchIn(BaseModel):              # extra="forbid"
    name: str = Field(min_length=1, max_length=200)
    criteria: Filter | None = None
    list_params: TicketListParams | None = None     # exactly one of criteria / list_params ("Ansicht speichern")
    include_archived: bool = False
    sort_by: TicketSortKey = "age"
    sort_order: Literal["asc", "desc"] = "desc"
    group_ids: list[int] = Field(default_factory=list, max_length=50)
    show_in_sidebar: bool = True             # the caller's own prefs, written on create
    show_count: bool = False
class SavedSearchUpdateIn(SavedSearchIn):
    version: int = Field(ge=1)
class SavedSearchPrefsIn(BaseModel):
    show_in_sidebar: bool; show_count: bool; position: int | None = Field(None, ge=0, le=10_000)
class SavedSearchOut(BaseModel):
    id: int; name: str; criteria: Filter | None; criteria_error: str | None
    include_archived: bool; sort_by: TicketSortKey; sort_order: Literal["asc", "desc"]
    owner: ReportOwnerOut; is_owner: bool; can_edit: bool
    group_ids: list[int]; show_in_sidebar: bool; show_count: bool; position: int
    source: Literal["tiqora", "znuny"]; source_name: str | None
    version: int; changed_at: UtcDateTime
class SavedSearchCountsOut(BaseModel):
    counts: dict[str, int | None]            # str(id) -> count; None = could not be counted (invalid criteria, timeout)
```

Routes (`api/v1/saved_searches.py`, `router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])`, `CurrentUser`; static paths before `/{search_id}`):

```
GET    /api/v1/saved-searches                  -> list[SavedSearchOut]
POST   /api/v1/saved-searches                  -> 201 SavedSearchOut
GET    /api/v1/saved-searches/counts           -> SavedSearchCountsOut   (sidebar entries with show_count; first MAX_SIDEBAR_COUNTS by position)
GET    /api/v1/saved-searches/import/znuny     -> ZnunyImportPreviewOut  (D9)
POST   /api/v1/saved-searches/import/znuny     -> ZnunyImportResultOut
GET    /api/v1/saved-searches/{search_id}      -> SavedSearchOut   (404 invisible)
PUT    /api/v1/saved-searches/{search_id}      -> SavedSearchOut   (owner/admin; 409 stale version)
PUT    /api/v1/saved-searches/{search_id}/prefs -> SavedSearchOut  (any viewer)
DELETE /api/v1/saved-searches/{search_id}      -> 204              (owner/admin)
```

Counts: each `count_tickets` runs inside `tiqora.reporting.sql.statement_timeout(session, COUNT_TIMEOUT_SECONDS)`; `FilterError`, a pydantic error, or a timeout (`ReportQueryTimeout` / `is_timeout_error`) → `None` for that id, then `await session.rollback()` before the next count. The scope is the viewer's (`ticket_query_scope(user.id, include_archived=admin and search.include_archived)`). API-key scope area for `/api/v1/saved-searches`: `tickets` (add to the area map, test).

### D9. Znuny search-profile import (Tasks 7–8)

Read side (`db/legacy/misc.py`): `SEARCH_PROFILE = Table("search_profile", MetaData(), Column("login", String(200)), Column("profile_name", String(200)), Column("profile_type", String(30)), Column("profile_key", Text), Column("profile_value", Text))` — own `MetaData`, never part of `LegacyBase.metadata`, never created or written.

```python
# backend/src/tiqora/saved_searches/znuny_reader.py
ProfileValues = dict[str, str | list[str]]
async def load_znuny_profiles(session: AsyncSession, user_login: str) -> dict[str, ProfileValues]
    # SELECT profile_name, profile_type, profile_key, profile_value FROM search_profile
    # WHERE LOWER(login) = LOWER('TicketSearch::' || user_login)   (Znuny SearchProfileGet: LOWER on case-sensitive DBs)
    # rows grouped by name; profile_type 'ARRAY' -> list (row order), else str; NULL value -> ""; name 'last-search' skipped
async def load_lookups(session: AsyncSession, user_id: int) -> ZnunyLookups

# backend/src/tiqora/saved_searches/znuny_import.py (pure)
@dataclass(frozen=True, slots=True)
class ZnunyLookups:
    state_type_names: Mapping[int, str]      # ticket_state_type.id -> name
    queue_names: Mapping[int, str]           # queue.id -> full name ("Parent::Child"), valid queues
    lock_names: Mapping[int, str]            # ticket_lock_type.id -> name (unlock | lock | tmp_lock)
    dynamic_fields: Mapping[str, str]        # valid ticket dynamic field name -> field_type
    time_zone: str                           # the importing agent's zone (TimeSlot dates are local)
@dataclass(frozen=True, slots=True)
class UnmappedItem:
    key: str; value: str; reason: str        # value shortened to 200 chars; reason German
@dataclass(frozen=True, slots=True)
class MappedProfile:
    name: str
    criteria: Filter                          # where None when nothing mapped
    include_archived: bool
    unmapped: tuple[UnmappedItem, ...]
def map_search_profile(name: str, values: Mapping[str, str | list[str]], lookups: ZnunyLookups) -> MappedProfile
```

Mapping (conditions ANDed in key order; a key with an empty value / `""` / `[]` is skipped silently — Znuny stores every form field):

| Znuny key(s) | Tiqora condition | not mappable → reason |
|---|---|---|
| `QueueIDs` (+ `UseSubQueues` = "1": also every queue whose name starts with `<parent name>::`) | `queue_id in` | unknown queue ids are kept as ids (scope filters them) |
| `StateIDs` / `PriorityIDs` / `TypeIDs` / `ServiceIDs` / `SLAIDs` / `OwnerIDs` / `ResponsibleIDs` | `state_id` / `priority_id` / `type_id` / `service_id` / `sla_id` / `owner_id` / `responsible_id` `in` | non-integer → "Kein gültiger Wert." |
| `StateTypeIDs` | `state_type in` (names via lookup) | unknown id → "Statustyp unbekannt." |
| `WatchUserIDs` | `watched_by in` | |
| `LockIDs` | names: {lock/tmp_lock only} → `locked is_true`; {unlock only} → `locked is_false`; both kinds → nothing | unknown id → reason |
| `TicketNumber` (str or list) | pattern rule P on `tn` (list → `any`) | operators → "Suchoperatoren werden nicht übernommen." |
| `Title` | each whitespace-separated word → `title contains word` (all); Znuny `ExtendedSearchCondition` adds `*…*` | operators → same reason |
| `CustomerIDRaw` / `CustomerUserLoginRaw` | `customer_id` / `customer_user_id` `eq` (list → `in`) | |
| `CustomerID` / `CustomerUserLogin` (only when the Raw key is absent/empty) | pattern rule P | operators → reason |
| `SearchInArchive` | `AllTickets` → include_archived; `ArchivedTickets` → include_archived + `archived is_true`; `NotArchivedTickets` → nothing | |
| `TimeSearchType` = `TimePoint` + `TicketCreateTimePoint`/`…PointStart`/`…PointFormat` | `created`: `Last` → `within_last`, `Before` → `older_than`; amount/unit per rule T | `Next`/`After` → "Zeitpunkte in der Zukunft werden nicht übernommen." |
| `TimeSearchType` = `TimeSlot` + `TicketCreateTimeStart{Year,Month,Day}` / `…Stop…` | `created between` {from: start 00:00:00 local → UTC ISO, to: stop 23:59:59 local → UTC ISO} | invalid date → "Kein gültiges Datum." |
| `ChangeTimeSearchType` + `TicketChange…` | `changed`, same rules | |
| `PendingTimeSearchType` + `TicketPending…` | `pending_until`: TimeSlot → `between`; TimePoint `Last`/`Before` → as above | `Next`/`After` → future reason |
| `CloseTimeSearchType`, `LastChangeTimeSearchType`, `EscalationTimeSearchType`, `ArticleTimeSearchType` (+ their sub-keys) | — | "Dieses Zeitfeld gibt es in der Tiqora-Suche nicht." |
| `Search_DynamicField_<Name>` Text/TextArea | pattern rule P on `df.<Name>` | |
| … Dropdown / Multiselect | `df.<Name> in values` | |
| … Checkbox | values {"1"} → `is_true`; {"-1"} → `is_false`; both → nothing | |
| … Date / DateTime: `<prefix>TimePoint` = "1" + `TimePointFormat`/`TimePointStart`/`TimePointValue` | `within_last`/`older_than` on `df.<Name>`; whichever of Format/Start is in {Before, Last, Next, After} is the direction, the other the unit (Znuny stores them both ways) | future → reason |
| … Date / DateTime: `<prefix>TimeSlot` = "1" + `TimeSlotStart{Year,Month,Day,Hour,Minute,Second}` / `…Stop…` | `between` (local → UTC) | |
| unknown/invalid dynamic field name | — | "Dynamisches Feld unbekannt." |
| `Fulltext`, `From`, `To`, `Cc`, `Subject`, `Body`, `AttachmentName`, any other `Article*`/`MIMEBase_*` field | — | "Volltextsuche wird nicht in gespeicherte Suchen übernommen – bitte die Suche verwenden." |
| `CreatedQueueIDs`, `CreatedUserIDs`, `CreatedTypeIDs`, `CreatedPriorityIDs`, `CreatedStateIDs`, `Agent`, `CustomerUserID`, `States`, `Queues`, `Priorities`, `Types`, `Services`, `SLAs`, `Locks` (name lists) | — | "Wird von Tiqora nicht unterstützt." |
| UI-only keys: `ShownAttributes`, `ResultForm`, `Profile`, `SaveProfile`, `SelectTemplate`, `EmptySearch`, `TakeLastSearch`, `SearchTemplate`, `Subaction`, `Action`, `ProfileName` | ignored silently (not criteria) | |
| every other key | — | "Wird von Tiqora nicht unterstützt." |

Dynamic-field keys: a key `Search_DynamicField_<rest>` belongs to the field whose name is the longest prefix of `<rest>` among `lookups.dynamic_fields` (date fields carry suffixes like `TimePointFormat`); no match → one unmapped item per distinct key with "Dynamisches Feld unbekannt.". Conditions are emitted in the row order of the table above (within a row in the order the keys are listed); a single condition is not wrapped in `all`.

Pattern rule P (Znuny `TicketSearch` `QueryCondition`, `*` = `%`): no `*` → `eq`; exactly one trailing `*` → `starts_with`; leading and trailing `*` only → `contains`; value of only `*` → nothing; anything else with `*`, or containing `||`, `&&`, `!`, `(`, `)`, `"`, or ` + ` / ` - ` → unmapped (operators reason). Rule T (AgentTicketSearch.pm 597–613): `minute` → minute, `hour` → hour, `day` → day, `week` → week, `month` → day × 30, `year` → day × 365; amount must be an int ≥ 1 else "Kein gültiger Wert.".

```python
# backend/src/tiqora/saved_searches/schemas.py (import part)
class UnmappedOut(BaseModel):
    key: str; value: str; reason: str
class ZnunyProfilePreviewOut(BaseModel):
    name: str; criteria: Filter; include_archived: bool; unmapped: list[UnmappedOut]
    existing_id: int | None                  # already imported -> apply updates it
    importable: bool                         # False when nothing mapped
class ZnunyImportPreviewOut(BaseModel):
    profiles: list[ZnunyProfilePreviewOut]   # sorted by name
class ZnunyImportIn(BaseModel):
    names: list[Annotated[str, Field(max_length=200)]] = Field(min_length=1, max_length=100)
class ZnunyImportSkippedOut(BaseModel):
    name: str; reason: str
class ZnunyImportResultOut(BaseModel):
    created: list[int]; updated: list[int]; skipped: list[ZnunyImportSkippedOut]
```

Apply: profiles not found → skipped "Suchvorlage nicht gefunden."; not importable → skipped "Keine Bedingung übernehmbar."; mapped criteria that fail `validate_criteria` (e.g. DF type changed) → skipped with the FilterError message. Imported searches are private (no groups), `show_in_sidebar` True for the importer.

### D10. GDPR (Task 9)

```python
# backend/src/tiqora/gdpr/saved_criteria.py
def replace_filter_values(where: dict[str, Any] | None, fields: frozenset[str],
                          mapping: Mapping[str, str]) -> tuple[dict[str, Any] | None, bool]
    # walks the filter JSON (all/any/not/condition); for a condition whose field is in `fields`, replaces every
    # string value (scalar, list item) whose lower() is a key of {k.lower(): v} with the mapped value; returns (new, changed)
async def scrub_saved_criteria(session: AsyncSession, mapping: Mapping[str, str]) -> int
    # tiqora_saved_search.criteria: fields {"customer_user_id"};
    # tiqora_report.definition: filter fields {"customer_user"} plus parameters[] with field "customer_user"
    # (allowed_values, default); rows whose JSON does not parse are skipped; changed rows get version + 1 and
    # changed_at; returns the number of changed rows (no commit)
```

`run_erasure` calls it once with `{old_login: new_login}` for anonymise mode and `{old_login: "geloescht-" + sha256(old_login)[:12]}` for delete mode, in the same transaction as the ticket rewrite. Rollback does not restore saved criteria (documented).

### D11. Frontend

```ts
// frontend/src/lib/reportsApi.ts (additions; BuiltinReport stays as an alias)
export type ReportItem = Schemas["ReportOut"];
export type BuiltinReport = ReportItem;             // kept for existing imports
export type SavedReportIn = Schemas["SavedReportIn"];
export type SavedReportUpdateIn = Schemas["SavedReportUpdateIn"];
export type ReportFile = Schemas["ReportFileOut"];
export type ShareGroup = Schemas["ShareGroupOut"];
reportsApi.catalogue(signal?, opts?: { all?: boolean })
reportsApi.create(body: SavedReportIn): Promise<ReportItem>                 // POST /api/v1/reports
reportsApi.update(id: number, body: SavedReportUpdateIn): Promise<ReportItem>   // PUT /api/v1/reports/{id}
reportsApi.remove(id: number): Promise<void>                               // DELETE
reportsApi.fileUrl(key: string, format: "json" | "yaml"): string           // GET .../{key}/file?format=
reportsApi.importFile(content: string): Promise<ReportFile>                // POST /api/v1/reports/import-file
reportsApi.shareGroups(signal?): Promise<ShareGroup[]>                     // GET /api/v1/reference/share-groups
export function reportTitle(report: ReportItem, t: TFunction): string      // saved -> name; built-in -> t(title_key)
export function reportDescription(report: ReportItem, t: TFunction): string
export function isSavedKey(key: string): boolean                           // /^[1-9][0-9]{0,17}$/

// frontend/src/lib/savedSearchesApi.ts
export type SavedSearch = Schemas["SavedSearchOut"];
export type SavedSearchIn = Schemas["SavedSearchIn"];
export type SavedSearchUpdateIn = Schemas["SavedSearchUpdateIn"];
export type ZnunyImportPreview = Schemas["ZnunyImportPreviewOut"];
export type ZnunyImportResult = Schemas["ZnunyImportResultOut"];
export type TicketQueryIn = Schemas["TicketQueryIn"];
export type TicketFilter = Schemas["Filter"];
export type FilterFieldOut = Schemas["FilterFieldOut"];
export const savedSearchesApi = {
  list(signal?), get(id, signal?), create(body), update(id, body), setPrefs(id, body: Schemas["SavedSearchPrefsIn"]),
  remove(id), counts(signal?), znunyPreview(signal?), znunyImport(names: string[]),
  queryTickets(body: Partial<TicketQueryIn> & { filter: TicketFilter }, signal?): Promise<PaginatedTickets>,   // POST /api/v1/tickets/query
  countTickets(filter: TicketFilter, signal?): Promise<{ count: number }>,                                   // POST /api/v1/ticket-filters/count
  ticketFilterFields(signal?): Promise<FilterFieldOut[]>,                                                    // GET /api/v1/ticket-filters/fields
};

// frontend/src/components/filters/filterModel.ts
export type FilterFieldType = "id" | "string" | "int" | "datetime" | "bool" | "enum";
export type FilterFieldDef = {
  key: string; label: string; type: FilterFieldType; ops: string[];
  enumValues: { value: string; label: string }[];
  optionsSource: string | null;            // "queue" | "state" | "priority" | "type" | "service" | "sla" | "user" | "df:<Name>" | null
};
export const OPS_BY_TYPE: Record<FilterFieldType, readonly string[]>;   // copy of backend OPS_BY_TYPE (test pins equality with a JSON fixture)
export type EditCondition = { kind: "condition"; id: string; field: string; op: string; value: unknown };
export type EditGroup = { kind: "group"; id: string; mode: "all" | "any"; negate: boolean; children: (EditCondition | EditGroup)[] };
export const MAX_UI_DEPTH = 2;            // root group + one nested group
export function fromFilter(filter: TicketFilter | null | undefined): { root: EditGroup; editable: boolean };
  // editable false when the filter is deeper than MAX_UI_DEPTH or uses "not" on a single condition the UI cannot show
export function toFilter(root: EditGroup): TicketFilter;     // empty root -> {version: 1}; single child unwrapped is NOT done (round trip stable)
export function emptyValueFor(op: string, type: FilterFieldType): unknown;
export function conditionError(c: EditCondition, field: FilterFieldDef | undefined): string | null;   // i18n key or null
export function filterErrors(root: EditGroup, fields: FilterFieldDef[]): Map<string, string>;          // condition id -> i18n key

// frontend/src/components/filters/FilterBuilder.tsx
export function FilterBuilder(props: {
  fields: FilterFieldDef[]; value: TicketFilter | null; onChange: (next: TicketFilter) => void;
  testId?: string /* default "filter-builder" */; disabled?: boolean;
}): JSX.Element;
  // root mode toggle "Alle Bedingungen müssen zutreffen" / "Mindestens eine muss zutreffen";
  // rows: field select, operator select (only field.ops), FilterValueInput, remove; "Bedingung hinzufügen",
  // "Gruppe hinzufügen" (root only); non-editable filter -> read-only summary + "Filter zurücksetzen"

// frontend/src/components/reports/builder/builderState.ts
export type BuilderState = {
  name: string; description: string; source: string | null;
  rows: DimensionSlot[]; columns: DimensionSlot[];   // rows = dimensions[0..], columns = series dimension (max 1)
  measures: string[]; filter: TicketFilter | null; parameters: ReportParameter[];
  timeField: string | null; timeRange: ReportDefinition["time_range"] | null; timeZone: string | null;
  visualization: ReportDefinition["visualization"]; sort: ReportDefinition["sort"]; limit: number | null;
  totals: ReportDefinition["totals"];
};
export type DimensionSlot = { key: string; grain: Grain | null; choices: string[]; topN: number | null };
export type BuilderAction =
  | { type: "setSource"; source: string }                      // resets fields that the new source lacks
  | { type: "addDimension"; target: "rows" | "columns"; key: string }
  | { type: "removeDimension"; target: "rows" | "columns"; index: number }
  | { type: "moveDimension"; target: "rows" | "columns"; index: number; delta: -1 | 1 }
  | { type: "updateDimension"; target: "rows" | "columns"; index: number; patch: Partial<DimensionSlot> }
  | { type: "addMeasure"; key: string } | { type: "removeMeasure"; index: number } | { type: "moveMeasure"; index: number; delta: -1 | 1 }
  | { type: "setFilter"; filter: TicketFilter | null }
  | { type: "setTime"; timeField?: string | null; timeRange?: BuilderState["timeRange"]; timeZone?: string | null }
  | { type: "setVisualization"; patch: Partial<ReportDefinition["visualization"]> }
  | { type: "setSort"; sort: ReportDefinition["sort"] } | { type: "setLimit"; limit: number | null }
  | { type: "setTotals"; patch: Partial<ReportDefinition["totals"]> }
  | { type: "setParameters"; parameters: ReportParameter[] }
  | { type: "setMeta"; name?: string; description?: string }
  | { type: "load"; state: BuilderState };
export const MAX_DIMENSIONS = 3; export const MAX_MEASURES = 10; export const MAX_PARAMETERS = 10;
export function initialState(): BuilderState;
export function builderReducer(state: BuilderState, action: BuilderAction): BuilderState;
export function toDefinition(state: BuilderState): ReportDefinition | null;   // null while invalid
export function fromDefinition(defn: ReportDefinition, meta?: { name?: string; description?: string }): BuilderState;
export function validate(state: BuilderState, source: ReportSource | undefined): BuilderIssue[];
export type BuilderIssue = { path: string; key: string /* i18n key under reports.builder.issues */ };
export function allowedVisualizations(state: BuilderState, source: ReportSource | undefined): VisualizationType[];
```

Visualization rules in `allowedVisualizations` (mirrors what `ReportView` can draw): `table` always; `pivot` needs exactly 2 dimensions; `bar`/`line`/`area` need ≥ 1 dimension; `stacked_bar` needs 2 dimensions or ≥ 2 measures; `donut` exactly 1 dimension and 1 plotted measure; `heatmap` exactly 2 dimensions and 1 measure; `kpi` 0 dimensions. Setting columns sets `visualization.series_dimension` to the index of the column dimension in `dimensions` (rows first, then the column).

Routes (TanStack; static segments win over `$reportKey`):

```
/agent/reports/new                    ReportBuilderPage  search { from?: string /* report key to copy */ }
/agent/reports/$reportKey/edit        ReportBuilderPage  (saved keys only; a built-in key redirects to /new?from=)
/agent/reports/$reportKey             ReportViewPage     (built-in or saved; search adds params?: Record<string, string>)
/agent/searches/new                   SavedSearchEditorPage
/agent/searches/$searchId/edit        SavedSearchEditorPage
/agent/searches/$searchId             SavedSearchPage    search { offset?: number; sort?: SortKey; order?: "asc" | "desc" }
```

Guards: builder routes need `user.can_create_reports` (else `ReportsAccessGate`-style message "Zum Erstellen von Berichten fehlt die Berechtigung."); share controls are disabled without `can_share_reports`.

i18n (German first; English in en.json):
- `reports.builder.{newTitle,editTitle,dataBasis,rows,columns,values,filter,timeRange,timeField,timeZone,visualization,chartType,series,totalsGrand,totalsRows,totalsColumns,sort,sortNone,limit,topN,grain,choices,parameters,addParameter,parameterField,parameterOp,parameterAllowed,parameterDefault,parameterRequired,preview,previewEmpty,previewInvalid,previewTooLarge,save,saveCopy,saving,saved,cancel,discardConfirm,loadFile,loadFileError,unsaved,addDimension,addMeasure,moveUp,moveDown,remove,noRight}` and `reports.builder.issues.{noSource,noMeasure,tooManyDimensions,tooManyMeasures,vizNeedsTwoDimensions,vizNeedsOneDimension,vizNeedsNoDimension,vizNeedsOneMeasure,nameRequired,groupsRequired,parameterFieldUnknown,duplicateParameter}` and `reports.builder.viz.{table,pivot,bar,stacked_bar,line,area,donut,heatmap,kpi}`
- `reports.saved.{mine,shared,newReport,edit,copy,delete,deleteConfirm,deleted,broken,exportJson,exportYaml,owner,changed,allAdmin,conflict,notFound}` and `reports.share.{title,private,groups,all,groupsLabel,noGroups,needShareRight}`
- `filters.{all,any,not,addCondition,addGroup,remove,field,operator,value,empty,tooComplex,reset,from,to,amount,unit,units.{minute,hour,day,week},yes,no,chooseValues,invalid.{required,number,date,list}}` and `filters.ops.<op>` for the 19 ops (`eq` "ist", `neq` "ist nicht", `in` "ist eines von", `not_in` "ist keines von", `contains` "enthält", `starts_with` "beginnt mit", `is_empty` "ist leer", `is_not_empty` "ist nicht leer", `lt` "kleiner als", `lte` "höchstens", `gt` "größer als", `gte` "mindestens", `between` "zwischen", `before` "vor", `after` "nach", `within_last` "in den letzten", `older_than` "älter als", `is_true` "ja", `is_false` "nein")
- `savedSearches.{title,new,saveView,saveViewHint,name,sort,order,includeArchived,showInSidebar,showCount,share,edit,delete,deleteConfirm,empty,sharedBy,conflict,notFound,broken,countUnavailable,preview,matches,save,cancel,importZnuny}` and `savedSearches.import.{title,intro,none,unmapped,willUpdate,notImportable,apply,result,created,updated,skipped}`
- `admin.settings.reporting.fields.access.help` reworded (who may view, save, share)

### D12. Interface for the dashboards plan (consumes this plan)

- **Report widgets reference a report by key**: `"builtin.<name>"` or the saved id as a decimal string (`ReportOut.key`). The widget stores the key, never a copy of the definition.
- **Server-side (data provider)**: `run_report_ref(session, user_id=viewer_or_kiosk_owner, report_key=key, definition=None, overrides=RunOverrides(time_range=…, grain=…, parameters=…, dimension_choices=…, common={"queue": [..], "customer": [..]}, extra_filter=None), reporting=…, cache=ReportCache(...), budget=QueryBudget.inline(reporting) or a per-provider budget)` → `RunRefOutcome`. `outcome.deferred` set → the widget is "pending"; the provider may enqueue with `enqueue_report_ref(session, user_id=…, run=ReportRunIn(report_key=key, …), extra_filter=outcome.extra_filter)` and hand the job id to the frontend (`useJob`). `outcome.ignored_common` lists dashboard filters this report's source cannot apply → the widget shows "Filter X gilt hier nicht".
- **Dashboard filters** are common keys (`COMMON_FILTER_KEYS`), mapped per source by `ReportSource.common_fields` (also published in `GET /reporting/sources` → `common_fields`); they only narrow (ANDed next to the scope, cached under their own key). Time range/grain overrides use the existing `ReportRunIn` semantics.
- **Errors a widget must handle**: `RequestError` 404 (unknown/invisible → "Bericht nicht verfügbar"), 410 (deleted → "Bericht „X“ wurde gelöscht"), `DefinitionError`/`FilterError` 422 (broken definition), `ReportQueryTimeout`, `TooManyReportJobs` (429).
- **Saved searches for ticket-list widgets**: `get_visible(session, search_id, user_id, admin=…)` + `compile_ticket_filter` + `TicketService.page_from_stmt` (or HTTP `GET /saved-searches/{id}` + `POST /tickets/query`); counts via `count_tickets`.
- **Sharing**: `tiqora.permissions.sharing.share_groups_for` / `check_share_groups` and `GET /api/v1/reference/share-groups` are reusable for `tiqora_dashboard.shared_group_ids`.
- HTTP `ReportRunIn` intentionally gains no filter-override field; a dashboard's own data endpoint carries dashboard filters.

### D13. DB/HTTP test module skeleton (used by Tasks 2–6, 8, 9)

Copy the structure of `backend/tests/test_reports_api.py`: module docstring naming the band and seeds; `pytestmark = pytest.mark.db`; `LO, HI` band constants; `_CLEANUP` tuple of DELETEs scoped to the band (new `tiqora_*` rows by `id BETWEEN :lo AND :hi` or by `owner_user_id BETWEEN :lo AND :hi`; junction/pref rows first; then Znuny seed rows: `group_user`, `queue`, `permission_groups`, `users`, tickets); `_run_sql(sync_url, statements)` with `TiqoraBase.metadata.create_all`; module-scoped autouse fixture that runs `_CLEANUP` on every URL used; `env` fixture parametrised over `mariadb_znuny_url`/`postgres_znuny_url`, `create_async_engine(_to_async_url(url))`, `create_app(Settings(environment="test"))` with `dependency_overrides[get_current_user]` (switchable user via a holder) and `[get_db]`; `httpx.AsyncClient(transport=ASGITransport(app=app))`; a per-test `_RESET` (run before and after each test, like wave 1's) deletes the saved rows of band owners and the band users' report jobs, so tests never see each other's saved reports/searches. Seed agents with logins `rep48.<role>`; seed groups `rep48-group-<id>`; queues `rep48-queue-<id>`; tickets `rep48 ticket <id>`. Saved rows created through the API get ids from the autoincrement — clean them by `owner_user_id BETWEEN :lo AND :hi` (all test users are band users; the Znuny admin `root@localhost` (id 1) is used as viewer only and never owns rows, except where a test says so and deletes `owner_user_id = 1 AND name LIKE 'rep48%'`).

### Deviations from spec

1. **Edit right.** Spec §3: view = `stats` ro, create/edit = `stats` rw. Wave 1 ruled `reporting.access = all_agents` by default (R-ACCESS; stock Znuny grants `stats` to admins only). Strict rw-for-create would leave nobody but admins able to save a report. Rule here (D3): with `all_agents` every agent may save **private** reports; **sharing** (groups/all) always needs `stats` rw or admin; with `stats_group` view needs `stats` ro and every save needs `stats` rw. Editing a saved report = its owner (with the create right) or an admin, not every `stats` rw holder (Znuny has no owner concept for stats; Tiqora does).
2. **Saved-search criteria use the live ticket filter catalog** (`tiqora.filters.tickets`: `queue_id`, `state_type`, `created`, `df.<Name>`, …), not the fact-table `tickets` report source the spec names. Same JSON filter language and operators ("one filter language"), different field catalog: saved searches list live tickets (title, ticket number, lock, watcher, dynamic fields), which the fact table does not hold.
3. **Visibility of saved searches** is per group only (spec: `shared_group_ids`); **sidebar flags are per viewer** (`tiqora_saved_search_pref`) instead of columns on the search, so a shared search can be pinned by each colleague; `sort` is split into ticket sort (`sort_by`/`sort_order`) and sidebar order (`position`).
4. **Full-text is not part of saved searches.** The search page's full-text query runs on Meilisearch and has no counterpart in the filter language; Znuny profiles' `Fulltext`/article fields are listed as not taken over. Saving the search page itself ("Suche speichern") is not offered in this plan.
5. **Builder preview is not sampled.** Spec §6: "live preview (debounced, sample-limited)". The preview runs the real report through `POST /reports/run` (debounced 700 ms, `definition.limit` capped at 200 for the preview body, normal cache); big previews become jobs that the existing `useReportRun` cancels when superseded. Sampling would show numbers that differ from the saved report.
6. **No `/reports/{id}/run` path.** Wave 1 deviation 3 stands: saved reports run through `POST /reports/run` with `report_key = "<id>"` (wave-1 R9: "phase 2 saved reports get numeric ids on the same paths").
7. **Report file import/export (JSON/YAML) is included** (spec §3 lists it; phases table is silent) because it is small once reports are stored; import opens the builder prefilled and saves nothing by itself. Znuny **stats** import stays in phase 3.
8. **Deleted reports are soft-deleted** (`valid = false`) so links and future dashboard widgets can say "gelöscht" (410) instead of a bare 404. Saved searches are hard-deleted (nothing references them yet besides the sidebar).
9. **Not in this plan:** MCP tools (`list_saved_searches`, `run_report` — phase 4 with NL questions), bulk actions on saved-search results, saving the full-text search page, restoring a deleted report, scheduled delivery (phase 3), dashboard widgets (dashboards plan).

---

## Tasks

Order: backend Tasks 1→9 are sequential (Task 7 is pure and may run in parallel with Tasks 5–6). Frontend Tasks 10→16 need the regenerated `openapi.json` from Tasks 2–6 and 8; they run sequentially (i18n, R-I18NSEQ); Task 11 and 12 are independent of each other but share locale files → sequential. Task 17 (e2e) needs 13–16. Task 18 closes. Backend-only: 1–9 (Tasks 2–4 also touch frontend fixtures/mocks only to keep `tsc` green after the schema change). Frontend-only: 10–17.

Seed sub-bands: T1 48000–48019, T2 48020–48059, T3 48060–48119, T4 48120–48169, T5 48170–48199, T6 48200–48249, T8 48250–48289, T9 48290–48319; 48320–48399 reserved (fix rounds).

---

### Task 1: Tables, ORM, migration (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_saved_reports_searches.py` (revision = next number after the current head; `down_revision` = current head — check, do not copy an id from here)
- Modify: `backend/src/tiqora/db/tiqora/models.py` (five ORM classes of D2), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py` (`version ==`), `backend/tests/test_tiqora_model_ddl.py` (if it lists tables explicitly)
- Test: `backend/tests/test_saved_tables.py` (both dialects, band 48000–48019)

**Interfaces:**
- Produces: `TiqoraReport`, `TiqoraReportGroup`, `TiqoraSavedSearch`, `TiqoraSavedSearchGroup`, `TiqoraSavedSearchPref` (column names exactly as D2).

- [ ] **Step 1: Write the failing test**

```python
"""DDL and constraints of the saved-report / saved-search tables (both dialects).

Band 48000-48019: owner user ids only (no Znuny rows are seeded; owner_user_id has no FK).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db
LO, HI = 48000, 48019
T0 = datetime(2026, 10, 10, 8, 0)
_URLS: set[str] = set()
_CLEANUP = (
    "DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
)


def _engine(url: str):
    engine = create_engine(url)
    TiqoraBase.metadata.create_all(engine)
    _URLS.add(url)
    return engine


@pytest.fixture(scope="module", autouse=True)
def _cleanup() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        engine = create_engine(url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt), {"lo": LO, "hi": HI})
        engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_tables_columns_and_indexes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("tiqora_report")}
    assert cols == {"id", "name", "description", "owner_user_id", "visibility", "definition",
                    "schema_version", "version", "valid", "copied_from", "created_at", "created_by",
                    "changed_at", "changed_by"}
    assert {c["name"] for c in insp.get_columns("tiqora_saved_search")} == {
        "id", "owner_user_id", "name", "criteria", "include_archived", "sort_by", "sort_order",
        "source", "source_name", "version", "created_at", "changed_at"}
    assert {c["name"] for c in insp.get_columns("tiqora_saved_search_pref")} == {
        "saved_search_id", "user_id", "show_in_sidebar", "show_count", "position"}
    assert insp.get_pk_constraint("tiqora_report_group")["constrained_columns"] == ["report_id", "group_id"]
    assert {i["name"] for i in insp.get_indexes("tiqora_report")} >= {
        "ix_tiqora_report_owner", "ix_tiqora_report_visibility"}
    uniques = {u["name"] for u in insp.get_unique_constraints("tiqora_saved_search")} | {
        i["name"] for i in insp.get_indexes("tiqora_saved_search") if i.get("unique")}
    assert "uq_tiqora_saved_search_source" in uniques
    engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_defaults_cascade_and_source_unique(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tiqora_report (name, owner_user_id, definition, schema_version,"
            " created_at, created_by, changed_at, changed_by)"
            " VALUES ('rep48 r', 48001, '{}', 1, :t, 48001, :t, 48001)"), {"t": T0})
        rid = conn.execute(text("SELECT id FROM tiqora_report WHERE owner_user_id = 48001")).scalar_one()
        row = conn.execute(text("SELECT visibility, version, valid FROM tiqora_report WHERE id = :i"),
                           {"i": rid}).one()
        assert (row.visibility, row.version, bool(row.valid)) == ("private", 1, True)
        conn.execute(text("INSERT INTO tiqora_report_group (report_id, group_id) VALUES (:r, 7)"), {"r": rid})
        conn.execute(text("DELETE FROM tiqora_report WHERE id = :r"), {"r": rid})
        assert conn.execute(text("SELECT COUNT(*) FROM tiqora_report_group WHERE report_id = :r"),
                            {"r": rid}).scalar_one() == 0
        for _ in range(2):  # NULL source_name never collides
            conn.execute(text(
                "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria, created_at, changed_at)"
                " VALUES (48002, 'rep48 s', '{}', :t, :t)"), {"t": T0})
        s = conn.execute(text("SELECT sort_by, sort_order, source, include_archived FROM tiqora_saved_search"
                              " WHERE owner_user_id = 48002")).first()
        assert (s.sort_by, s.sort_order, s.source, bool(s.include_archived)) == ("age", "desc", "tiqora", False)
        conn.execute(text(
            "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria, source, source_name,"
            " created_at, changed_at) VALUES (48003, 'rep48 z', '{}', 'znuny', 'P1', :t, :t)"), {"t": T0})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tiqora_saved_search (owner_user_id, name, criteria, source, source_name,"
            " created_at, changed_at) VALUES (48003, 'rep48 z2', '{}', 'znuny', 'P1', :t, :t)"), {"t": T0})
    engine.dispose()
```

Also add a guard like `test_auth_config_user_id_does_not_generate_ids` for `TiqoraReportGroup.report_id`, `TiqoraSavedSearchPref.saved_search_id` (`autoincrement is False`), and append the new classes to `test_tiqora_model_ddl.py`'s compared set if it is explicit.

- [ ] **Step 2: Run → FAIL** `cd backend && uv run python -m pytest -q tests/test_saved_tables.py` (tables missing). Also check that nothing selects report tables by prefix (the new `tiqora_report`/`tiqora_report_group` share it): `grep -rn "tiqora_report" backend/tests backend/src | grep -E "LIKE|startswith|%"` — switch any hit to explicit table names (at plan time there was none; `test_reporting_migration_db.py` uses an explicit set).
- [ ] **Step 3: Implement** the ORM classes (D2; `MEDIUM_TEXT_VARIANT` for `definition`/`criteria`; FKs named as in D2 with `ondelete="CASCADE"`; `__table_args__` indexes/unique) and the migration (`op.create_table` ×5 in dependency order; downgrade drops in reverse). Update `TIQORA_HEAD` and `test_migrate_cli.py`.
- [ ] **Step 4: Run → PASS** the new module plus `tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py`; `uv run alembic heads` shows one primary head (+ gated chain). Lint gates.
- [ ] **Step 5: Commit** `feat(reporting): tables for saved reports and saved searches`.

---

### Task 2: Rights, sharing groups, saved-report store, `/auth/me` flags (backend)

**Files:**
- Create: `backend/src/tiqora/permissions/sharing.py`, `backend/src/tiqora/reporting/rights.py`, `backend/src/tiqora/reporting/saved.py`
- Modify: `backend/src/tiqora/reporting/access.py` (`stats_group_id`), `backend/src/tiqora/domain/schemas.py` (`UserMe.can_create_reports`, `can_share_reports`), `backend/src/tiqora/api/v1/auth.py`, `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client/src/schema.d.ts` (generated), frontend `UserMe` fixtures that `pnpm lint` reports (expected: `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, `auth/AuthContext.test.tsx`, `components/layout/AgentShell.test.tsx`, `components/agent/AccountMenu.test.tsx`, `routes/agent/*Report*.test.tsx`, `routes/agent/TimeAccountingReportPage.test.tsx`, `routes/agent/NewTicketPage.test.tsx`, `e2e/admin-access.spec.ts` — add both flags `true` in the demo/e2e user, `false` where a test is about a non-report agent)
- Test: `backend/tests/test_report_rights_saved.py` (both dialects, band 48020–48059)

**Interfaces:**
- Consumes: D2 tables; `has_report_access`, `PermissionEngine.groups_for_permission/is_admin`, `validate_definition`, `utc_now`.
- Produces: D3 + D4 exactly (`ReportRights`, `report_rights`, `ShareGroup`, `share_groups_for`, `check_share_groups`, `ShareGroupError`, `SavedReportIn`, `SavedReportUpdateIn`, `SavedReport`, `ReportNotFound`, `ReportGone`, `ReportForbidden`, `ReportVersionConflict`, `load_definition`, `dump_definition`, `visible_saved_reports`, `get_saved_report`, `can_edit_report`, `create_saved_report`, `update_saved_report`, `delete_saved_report`, `ResolvedReport`, `resolve_report_ref`, `SAVED_KEY_RE`, `MAX_SAVED_REPORTS_PER_USER`).

Seeds (D13 skeleton): users 48020 `rep48.plain` (no stats), 48021 `rep48.statsro` (`ro` on Znuny's seeded `stats` group, looked up by name), 48022 `rep48.statsrw` (`rw` on `stats`), 48023 `rep48.member` (`ro` on group 48030), 48024 `rep48.outsider`; groups 48030 `rep48-group-48030`, 48031 `rep48-group-48031`; user 48022 also `ro` on 48030 and 48031. Znuny admin = user 1.

- [ ] **Step 1: Write the failing tests**

```python
TICKETS_DEF = {"source": "tickets", "dimensions": [{"key": "queue"}], "measures": [{"key": "tickets"}]}

async def _rights(env, user_id: int, access: str = "all_agents") -> ReportRights:
    await env.set_access(access)
    async with env.factory() as s:
        return await report_rights(s, user_id, await load_reporting_settings(s, Settings(environment="test")))

async def test_rights_all_agents(env) -> None:
    assert await _rights(env, 48020) == ReportRights(view=True, create=True, share=False, admin=False)
    assert await _rights(env, 48022) == ReportRights(view=True, create=True, share=True, admin=False)
    assert await _rights(env, 1) == ReportRights(view=True, create=True, share=True, admin=True)

async def test_rights_stats_group(env) -> None:
    assert await _rights(env, 48020, "stats_group") == ReportRights(False, False, False, False)
    assert await _rights(env, 48021, "stats_group") == ReportRights(True, False, False, False)
    assert await _rights(env, 48022, "stats_group") == ReportRights(True, True, True, False)

async def test_me_exposes_create_and_share(env) -> None:
    env.user = user(48020)
    me = (await env.client.get("/api/v1/auth/me")).json()
    assert (me["can_view_reports"], me["can_create_reports"], me["can_share_reports"]) == (True, True, False)
    env.user = user(48022)
    me = (await env.client.get("/api/v1/auth/me")).json()
    assert me["can_share_reports"] is True

async def test_share_groups_member_vs_admin(env) -> None:
    async with env.factory() as s:
        mine = [g.id for g in await share_groups_for(s, 48023)]
        assert 48030 in mine and 48031 not in mine
        assert {48030, 48031} <= {g.id for g in await share_groups_for(s, 1)}
        with pytest.raises(ShareGroupError):
            await check_share_groups(s, 48023, [48030, 48031])
        assert await check_share_groups(s, 48023, [48030, 48030]) == (48030,)

async def test_visibility_private_groups_all(env) -> None:
    async with env.factory() as s:
        rw = await _rights_in(s, 48022)
        priv = await create_saved_report(s, 48022, rw, SavedReportIn(name="rep48 priv", definition=TICKETS_DEF), registry=get_registry())
        grp = await create_saved_report(s, 48022, rw, SavedReportIn(name="rep48 grp", definition=TICKETS_DEF,
                                        visibility="groups", group_ids=[48030]), registry=get_registry())
        allv = await create_saved_report(s, 48022, rw, SavedReportIn(name="rep48 all", definition=TICKETS_DEF,
                                         visibility="all"), registry=get_registry())
        await s.commit()
        member = {r.id for r in await visible_saved_reports(s, 48023, admin=False)}
        outsider = {r.id for r in await visible_saved_reports(s, 48024, admin=False)}
        admin_default = {r.id for r in await visible_saved_reports(s, 1, admin=True)}
        admin_all = {r.id for r in await visible_saved_reports(s, 1, admin=True, include_others_private=True)}
    assert member >= {grp.id, allv.id} and priv.id not in member
    assert allv.id in outsider and grp.id not in outsider and priv.id not in outsider
    assert priv.id not in admin_default and priv.id in admin_all

async def test_get_invisible_is_not_found_and_deleted_is_gone(env) -> None:
    async with env.factory() as s:
        rw = await _rights_in(s, 48022)
        priv = await create_saved_report(s, 48022, rw, SavedReportIn(name="rep48 p2", definition=TICKETS_DEF), registry=get_registry())
        allv = await create_saved_report(s, 48022, rw, SavedReportIn(name="rep48 a2", definition=TICKETS_DEF, visibility="all"), registry=get_registry())
        await delete_saved_report(s, allv.id, 48022, rw)
        await s.commit()
        with pytest.raises(ReportNotFound):
            await get_saved_report(s, priv.id, 48024, admin=False)
        with pytest.raises(ReportGone) as gone:
            await get_saved_report(s, allv.id, 48024, admin=False)
        assert gone.value.name == "rep48 a2"
        assert (await get_saved_report(s, priv.id, 1, admin=True)).name == "rep48 p2"

async def test_create_rules(env) -> None:
    async with env.factory() as s:
        plain = await _rights_in(s, 48020)          # create, no share
        with pytest.raises(ReportForbidden):
            await create_saved_report(s, 48020, plain, SavedReportIn(name="rep48 x", definition=TICKETS_DEF, visibility="all"), registry=get_registry())
        with pytest.raises(ShareGroupError):
            rw = await _rights_in(s, 48022)
            await create_saved_report(s, 48022, rw, SavedReportIn(name="rep48 y", definition=TICKETS_DEF, visibility="groups"), registry=get_registry())
        with pytest.raises(DefinitionError) as bad:
            await create_saved_report(s, 48020, plain, SavedReportIn(name="rep48 z",
                definition={"source": "tickets", "measures": [{"key": "nope"}]}), registry=get_registry())
        assert bad.value.path.startswith("definition.measures")
        r = await create_saved_report(s, 48020, plain, SavedReportIn(name="  rep48 trimmed  ", definition=TICKETS_DEF,
                                      group_ids=[48030]), registry=get_registry())
        assert (r.name, r.visibility, r.group_ids, r.version) == ("rep48 trimmed", "private", (), 1)

async def test_update_owner_admin_conflict(env) -> None:
    async with env.factory() as s:
        plain = await _rights_in(s, 48020)
        r = await create_saved_report(s, 48020, plain, SavedReportIn(name="rep48 u", definition=TICKETS_DEF), registry=get_registry())
        await s.commit()
        upd = SavedReportUpdateIn(name="rep48 u2", definition=TICKETS_DEF, version=1)
        with pytest.raises(ReportForbidden):
            await update_saved_report(s, r.id, 48023, await _rights_in(s, 48023), upd, registry=get_registry())
        r2 = await update_saved_report(s, r.id, 48020, plain, upd, registry=get_registry())
        assert (r2.name, r2.version) == ("rep48 u2", 2)
        with pytest.raises(ReportVersionConflict) as conflict:
            await update_saved_report(s, r.id, 1, await _rights_in(s, 1), upd, registry=get_registry())   # stale version 1
        assert conflict.value.current_version == 2

async def test_load_definition_reports_broken_json(env) -> None:
    defn, err = load_definition('{"schema_version": 2, "source": "tickets"}')
    assert defn is None and "nicht mehr gelesen" in (err or "")
    defn, err = load_definition(dump_definition(ReportDefinition.model_validate(TICKETS_DEF)))
    assert err is None and defn is not None and defn.source == "tickets"

async def test_resolve_report_ref(env) -> None:
    async with env.factory() as s:
        builtin = await resolve_report_ref(s, "builtin.ticket_kpis", 48024, admin=False)
        assert builtin.builtin is not None and builtin.saved is None
        for bad in ("builtin.nope", "0", "12abc", "-3"):
            with pytest.raises(ReportNotFound):
                await resolve_report_ref(s, bad, 48024, admin=False)
```

(`_rights_in(s, uid)` = `report_rights(s, uid, await load_reporting_settings(s, Settings(environment="test")))`; `user(uid)` builds an `AuthenticatedUser` with the seeded login.)

- [ ] **Step 2: Run → FAIL** (modules missing).
- [ ] **Step 3: Implement** D3/D4. `has_report_access` keeps its behaviour but uses `stats_group_id`. In `auth.py` compute `report_rights` once and set the three flags (keep `can_view_reports` = `rights.view`; guard as today). `create_saved_report`: strip name; blank → `DefinitionError("Bitte einen Namen angeben.", path="name")`; re-raise `validate_definition` errors with `path="definition." + exc.path`; count `owner_user_id = user AND valid` ≥ limit → `ReportForbidden("Es sind höchstens 500 Berichte pro Person möglich.")`. `update_saved_report` uses `UPDATE tiqora_report SET … , version = version + 1 WHERE id = :id AND version = :v` and checks `rowcount`.
- [ ] **Step 4: Run → PASS**; regenerate openapi (both files) + `pnpm build` in `packages/api-client`; `cd frontend && pnpm lint && pnpm test --run` (fixtures updated). Backend lint gates; run `tests/test_reports_api.py` too (access unchanged).
- [ ] **Step 5: Commit** `feat(reporting): report rights, share groups and saved-report store`.

---

### Task 3: Reports API — catalogue with saved reports, CRUD, report files, share groups (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/report_file.py`
- Modify: `backend/src/tiqora/reporting/schemas.py` (`ReportOut`, `ReportOwnerOut`; remove `BuiltinReportOut`), `backend/src/tiqora/api/v1/reports.py`, `backend/src/tiqora/api/v1/reference.py` (`ShareGroupOut`, `GET /share-groups`), openapi files + schema.d.ts, `frontend/src/lib/reportsApi.ts` (`ReportItem` + `BuiltinReport` alias only), `frontend/src/demo/reportsCatalogue.ts` / `reportsMock.ts` (built-in helper adds `kind: "builtin"`, `id: null`, `read_only: true`, `group_ids: []`, the other new fields `null`), `frontend/e2e/fixtures/mock-api.ts` (same), any frontend test fixture `pnpm lint` reports, and the null-guards `definition` now needs (`ReportOut.definition` is nullable): `frontend/src/routes/agent/ReportsPage.tsx` (skip cards whose definition is `null`), `frontend/src/routes/agent/ReportViewPage.tsx` (a `null` definition shows `definition_error` instead of running), `frontend/src/components/reports/reportFilter.ts` / `reportViewSearch.ts` if their signatures take the definition — Task 14 adds the real broken-report UI
- Test: `backend/tests/test_saved_reports_api.py` (both dialects, band 48060–48119), `backend/tests/test_report_file.py` (pure)

**Interfaces:**
- Consumes: Task 2 store; `resolve_labels(session, "user", ids)` for owner names; `export_slug`.
- Produces: D5 routes and models; `dump_report_file`, `parse_report_file`, `ReportFileOut`, `ReportFileImportIn`.

Seeds: users 48060 `rep48.owner` (first/last name "Rhea"/"Owner"; `rw` on `stats`, `ro` on group 48070), 48061 `rep48.viewer` (`ro` on 48070), 48062 `rep48.stranger` (no groups), group 48070.

- [ ] **Step 1: Write the failing tests**

```python
# test_report_file.py (pure)
def test_json_round_trip() -> None:
    out = ReportFileOut(name="rep48 Datei", definition=ReportDefinition.model_validate(TICKETS_DEF))
    assert parse_report_file(dump_report_file(out, "json").decode()) == out

def test_yaml_round_trip_keeps_umlauts() -> None:
    out = ReportFileOut(name="Größe je Queue", definition=ReportDefinition.model_validate(TICKETS_DEF))
    text = dump_report_file(out, "yaml").decode()
    assert "Größe je Queue" in text
    assert parse_report_file(text) == out

def test_yaml_aliases_are_rejected() -> None:
    bomb = "a: &a [x, x]\nb: &b [*a, *a]\nc: [*b, *b]\n"
    with pytest.raises(DefinitionError) as err:
        parse_report_file(bomb)
    assert err.value.path == "content" and "Anker" in err.value.message

@pytest.mark.parametrize("content", ["[1, 2]", "format: other\nformat_version: 1\nname: x\ndefinition: {}", "{"])
def test_wrong_files_are_definition_errors(content: str) -> None:
    with pytest.raises(DefinitionError):
        parse_report_file(content)

def test_pydantic_error_carries_dotted_path() -> None:
    bad = json.dumps({"format": "tiqora-report", "format_version": 1, "name": "x",
                      "definition": {"source": "tickets", "measures": []}})
    with pytest.raises(DefinitionError) as err:
        parse_report_file(bad)
    assert err.value.path == "definition.measures"
```

```python
# test_saved_reports_api.py (D13 skeleton; env.as_user(uid) switches the current user)
async def _create(env, body: dict) -> dict:
    resp = await env.client.post("/api/v1/reports", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()

async def test_create_list_get_saved(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 Queues", "definition": TICKETS_DEF})
    assert made["kind"] == "saved" and made["key"] == str(made["id"]) and made["read_only"] is False
    assert made["owner"] == {"user_id": 48060, "name": "Rhea Owner"} and made["visibility"] == "private"
    cat = (await env.client.get("/api/v1/reports")).json()["reports"]
    assert [r["key"] for r in cat[:25]] == BUILTIN_KEYS            # built-ins first, order unchanged
    assert cat[25]["key"] == made["key"]
    got = (await env.client.get(f"/api/v1/reports/{made['key']}")).json()
    assert got["definition"]["source"] == "tickets" and got["version"] == 1

async def test_invisible_report_is_404_on_every_route(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 privat", "definition": TICKETS_DEF})
    env.as_user(48062)
    key = made["key"]
    assert (await env.client.get(f"/api/v1/reports/{key}")).status_code == 404
    assert (await env.client.get(f"/api/v1/reports/{key}/file?format=json")).status_code == 404
    run = await env.client.post("/api/v1/reports/run", json={"report_key": key})
    assert run.status_code == 404 and run.json()["detail"]["message"] == "Bericht nicht gefunden."
    assert (await env.client.post("/api/v1/reports/export", json={"report_key": key})).status_code == 404
    assert (await env.client.put(f"/api/v1/reports/{made['id']}", json={
        "name": "x", "definition": TICKETS_DEF, "version": 1})).status_code == 404
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 404
    assert key not in {r["key"] for r in (await env.client.get("/api/v1/reports")).json()["reports"]}

async def test_group_share_and_delete_gives_410(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 Team", "definition": TICKETS_DEF, "visibility": "groups", "group_ids": [48070]})
    env.as_user(48061)
    seen = (await env.client.get(f"/api/v1/reports/{made['key']}")).json()
    assert seen["read_only"] is True and seen["group_ids"] == [48070]
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 403
    env.as_user(48060)
    assert (await env.client.delete(f"/api/v1/reports/{made['id']}")).status_code == 204
    env.as_user(48061)
    gone = await env.client.get(f"/api/v1/reports/{made['key']}")
    assert gone.status_code == 410 and "rep48 Team" in gone.json()["detail"]["message"]
    run = await env.client.post("/api/v1/reports/run", json={"report_key": made["key"]})
    assert run.status_code == 410

async def test_update_with_stale_version_is_409(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 v", "definition": TICKETS_DEF})
    body = {"name": "rep48 v2", "definition": TICKETS_DEF, "version": 1}
    first = await env.client.put(f"/api/v1/reports/{made['id']}", json=body)
    assert first.status_code == 200 and first.json()["version"] == 2
    second = await env.client.put(f"/api/v1/reports/{made['id']}", json=body)
    assert second.status_code == 409
    assert second.json()["detail"] == {"message": "Der Bericht wurde inzwischen geändert. Bitte neu laden.", "current_version": 2}
    assert (await env.client.get(f"/api/v1/reports/{made['key']}")).json()["name"] == "rep48 v2"

async def test_share_needs_share_right(env) -> None:
    env.as_user(48061)                                      # all_agents: may create, may not share
    resp = await env.client.post("/api/v1/reports", json={"name": "rep48 s", "definition": TICKETS_DEF, "visibility": "all"})
    assert resp.status_code == 403 and "Teilen" in resp.json()["detail"]["message"]
    resp = await env.client.post("/api/v1/reports", json={"name": "rep48 g", "definition": TICKETS_DEF,
                                                          "visibility": "groups", "group_ids": [48070]})
    assert resp.status_code == 403

async def test_invalid_definition_is_422_with_path(env) -> None:
    env.as_user(48060)
    resp = await env.client.post("/api/v1/reports", json={"name": "rep48 bad",
        "definition": {"source": "tickets", "measures": [{"key": "tickets"}], "dimensions": [{"key": "nope"}]}})
    assert resp.status_code == 422 and resp.json()["detail"]["path"].startswith("definition.dimensions")

async def test_broken_definition_lists_and_runs_422(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 kaputt", "definition": TICKETS_DEF})
    _run_sql(env.sync_url, ("UPDATE tiqora_report SET definition = '{\"schema_version\": 2}' WHERE id = %d" % made["id"],))
    item = next(r for r in (await env.client.get("/api/v1/reports")).json()["reports"] if r["key"] == made["key"])
    assert item["definition"] is None and "nicht mehr gelesen" in item["definition_error"]
    run = await env.client.post("/api/v1/reports/run", json={"report_key": made["key"]})
    assert run.status_code == 422 and run.json()["detail"]["path"] == "definition"

async def test_admin_all_lists_others_private(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 nur ich", "definition": TICKETS_DEF})
    env.as_user(1)
    keys = {r["key"] for r in (await env.client.get("/api/v1/reports")).json()["reports"]}
    keys_all = {r["key"] for r in (await env.client.get("/api/v1/reports?all=true")).json()["reports"]}
    assert made["key"] not in keys and made["key"] in keys_all
    env.as_user(48061)
    keys_all = {r["key"] for r in (await env.client.get("/api/v1/reports?all=true")).json()["reports"]}
    assert made["key"] not in keys_all                     # all=true ignored for non-admins

async def test_file_download_and_import(env) -> None:
    env.as_user(48060)
    made = await _create(env, {"name": "rep48 Größe", "definition": TICKETS_DEF})
    resp = await env.client.get(f"/api/v1/reports/{made['key']}/file?format=yaml")
    assert resp.status_code == 200
    assert 'filename="rep48-groesse.yaml"' in resp.headers["content-disposition"]
    parsed = await env.client.post("/api/v1/reports/import-file", json={"content": resp.text})
    assert parsed.status_code == 200 and parsed.json()["name"] == "rep48 Größe"
    builtin = await env.client.get("/api/v1/reports/builtin.ticket_kpis/file?format=json")
    assert builtin.status_code == 200 and builtin.json()["name"] == "builtin.ticket_kpis"
    bad = await env.client.post("/api/v1/reports/import-file", json={"content": json.dumps({
        "format": "tiqora-report", "format_version": 1, "name": "x",
        "definition": {"source": "nope", "measures": [{"key": "tickets"}]}})})
    assert bad.status_code == 422 and bad.json()["detail"]["path"].startswith("definition.source")

async def test_share_groups_endpoint(env) -> None:
    env.as_user(48061)
    groups = (await env.client.get("/api/v1/reference/share-groups")).json()
    assert {"id": 48070, "name": "rep48-group-48070"} in groups
    assert all(g["id"] != 48071 for g in groups)
```

Also update `tests/test_reports_api.py`'s catalogue test only where it asserted `BuiltinReportOut`-specific shape (`kind == "builtin"`, `read_only is True`); its count of 25 stays (no saved rows there).

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `_builtin_out` → `ReportOut(kind="builtin", read_only=True, …)`; `_saved_out(report, rights, user_id, owner_names)`; catalogue loads `report_rights` once, owner names for all saved rows in one `resolve_labels` call. Map exceptions in one helper: `ReportNotFound` → 404, `ReportGone` → 410, `ReportForbidden` → 403, `ReportVersionConflict` → 409 with `current_version`, `ShareGroupError` → 422 path `group_ids`, `DefinitionError` → 422. `PUT`/`DELETE` paths are typed `report_id: int`. `GET /{report_key}/file`: `Response(content=dump_report_file(...), media_type="application/json" | "application/yaml", headers={"Content-Disposition": f'attachment; filename="{export_slug(name)}.{ext}"'})`. `POST /import-file` also runs `validate_definition` (errors path-prefixed `definition.`). Check the API-key area of `/api/v1/reference/share-groups` (`api_key_scopes`) and add an assertion to `tests/test_api_key_scopes.py`.
- [ ] **Step 4: Run → PASS** (new modules + `tests/test_reports_api.py`); regenerate openapi + `pnpm build`; `cd frontend && pnpm lint && pnpm test --run`. Lint gates.
- [ ] **Step 5: Commit** `feat(reporting): saved reports API, report files and share groups`.

---

### Task 4: Run saved reports by key; run service and dashboard seam (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/run_service.py`
- Modify: `backend/src/tiqora/reporting/jobs.py` (re-export, `ReportRunPayload.extra_filter`, `_checked` passes it), `backend/src/tiqora/api/v1/reports.py` (route uses `run_report_ref`/`enqueue_report_ref`; constants re-imported), `backend/src/tiqora/reporting/semantic.py` (`common_fields`), `backend/src/tiqora/reporting/sources/{tickets,open_tickets,ticket_events,accounted_time,daily_snapshot}.py` (D6 table), `backend/src/tiqora/reporting/schemas.py` (`SourceDimensionOut.ops/enum_values`, `ReportSourceOut.common_fields`), `_source_out` in `api/v1/reports.py`, openapi + schema.d.ts, `frontend/src/demo/reportsMock.ts` `demoSources()` + `e2e/fixtures/mock-api.ts` (new required fields)
- Modify (tests): `backend/tests/test_reports_api.py` — every `monkeypatch.setattr(reports_api, …)` whose target moves into `run_service` (today: `estimate_rows` ×4, `execute_and_store` ×2, `enqueue_job`, `count_active_jobs`; `grep -n "setattr(reports_api" backend/tests/test_reports_api.py`) is re-pointed to `tiqora.reporting.run_service` (a re-exported name does not follow a patch); assertions unchanged
- Test: `backend/tests/test_report_run_service.py` (both dialects, band 48120–48169), additions to `backend/tests/test_reporting_definition.py` (pure; it already imports `core_report_sources`)

**Interfaces:**
- Consumes: Task 2 `resolve_report_ref`; wave-1 `plan_report`, `estimate_rows`, `execute_and_store`, `QueryBudget`, `ReportCache`, `enqueue_job`, `lock_user_job_quota`, `count_active_jobs`.
- Produces: D6 exactly (`RunOverrides`, `PreparedRun`, `RunRefOutcome`, `prepare_ref`, `prepare_run`, `RequestError`, `run_report_ref`, `enqueue_report_ref`, `TooManyReportJobs`, `common_filter`, `COMMON_FILTER_KEYS`, `CommonFilterKey`), `ReportSource.common_fields`.

Seeds: users 48120 `rep48.runowner` (`rw` on `stats`, `ro` on group 48130 = queue 48130), 48121 `rep48.runviewer` (`ro` on group 48131 = queue 48131 only), group/queue 48130 and 48131; tickets 48140 (queue 48130, June 2024), 48141 (queue 48131, June 2024), 48142 (queue 48130, customer `rep48-cust-b`); facts built with `rebuild_ticket_facts` like `test_reports_api.py`.

- [ ] **Step 1: Write the failing tests**

```python
# pure (sources)
@pytest.mark.parametrize("source", core_report_sources(), ids=lambda s: s.key)
def test_common_fields_resolve_to_filterable_fields(source: ReportSource) -> None:
    assert set(source.common_fields) <= set(COMMON_FILTER_KEYS)
    for key, field in source.common_fields.items():
        assert source.filter_spec(field) is not None, (source.key, key, field)

def test_common_fields_table() -> None:
    by_key = {s.key: s for s in core_report_sources()}
    assert by_key["ticket_events"].common_fields["queue"] == "ticket_queue"
    assert "channel" not in by_key["open_tickets"].common_fields
    assert set(by_key["daily_snapshot"].common_fields) == {"queue", "state_type", "priority"}

def test_common_filter_maps_and_reports_ignored() -> None:
    src = {s.key: s for s in core_report_sources()}["accounted_time"]
    flt, ignored = common_filter(src, {"queue": [5], "owner": [7], "channel": [], "bogus": ["x"]})
    assert ignored == ("bogus", "owner")
    assert flt is not None and flt.model_dump(by_alias=True, exclude_none=True)["where"] == {
        "field": "queue", "op": "in", "value": [5]}
    assert common_filter(src, {}) == (None, ())

```

DB/HTTP tests in `test_report_run_service.py`:

```python
async def test_sources_out_carry_ops_enum_values_common_fields(env) -> None:
    env.as_user(48120)
    tickets = next(s for s in (await env.client.get("/api/v1/reporting/sources")).json() if s["key"] == "tickets")
    channel = next(d for d in tickets["dimensions"] if d["key"] == "channel")
    assert channel["ops"] == ["eq", "in", "neq", "not_in"] and "email" in channel["enum_values"]
    created_hour = next(d for d in tickets["dimensions"] if d["key"] == "created_hour")
    assert created_hour["ops"] == []
    assert tickets["common_fields"]["queue"] == "queue"

async def _saved(env, owner: int, **extra) -> str:
    env.as_user(owner)
    body = {"name": "rep48 run", "definition": {"source": "tickets", "dimensions": [{"key": "queue"}],
            "measures": [{"key": "tickets"}], "time_range": JUNE}, **extra}
    resp = await env.client.post("/api/v1/reports", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["key"]

async def test_run_saved_report_by_key(env) -> None:
    key = await _saved(env, 48120)
    resp = await env.client.post("/api/v1/reports/run", json={"report_key": key})
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["report_key"] == key
    assert sorted(r[0] for r in result["rows"]) == [48130]           # the owner's scope: queue 48130 only
    assert sum(r[1] for r in result["rows"]) == 2

async def test_shared_report_runs_in_viewer_scope(env) -> None:
    key = await _saved(env, 48120, visibility="all")
    first = await env.client.post("/api/v1/reports/run", json={"report_key": key})   # owner fills the cache
    assert first.status_code == 200
    env.as_user(48121)
    resp = await env.client.post("/api/v1/reports/run", json={"report_key": key})
    rows = resp.json()["result"]["rows"]
    assert [r[0] for r in rows] == [48131] and rows[0][1] == 1      # viewer's own queue, never the owner's numbers
    assert resp.json()["result"]["cached"] is False

async def test_job_fails_after_report_unshared(env) -> None:
    key = await _saved(env, 48120, visibility="all")
    env.as_user(48121)
    resp = await env.client.post("/api/v1/reports/run", json={"report_key": key, "force_job": True})
    assert resp.status_code == 202
    job_id = resp.json()["job"]["job_id"]
    env.as_user(48120)
    upd = await env.client.put(f"/api/v1/reports/{key}", json={"name": "rep48 run", "version": 1,
        "definition": {"source": "tickets", "dimensions": [{"key": "queue"}], "measures": [{"key": "tickets"}]}})
    assert upd.status_code == 200                                    # visibility back to private
    await env.run_jobs()
    env.as_user(48121)
    job = await env.job(job_id)
    assert job["status"] == "failed" and "nicht mehr gültig" in job["error"]

async def test_run_report_ref_common_filters_and_extra_filter(env) -> None:
    key = await _saved(env, 48120)
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s, user_id=48120, report_key=key, definition=None,
            overrides=RunOverrides(common={"customer": ["rep48-cust-b"], "sla": []}),
            reporting=reporting, cache=None, budget=QueryBudget.inline(reporting))
    assert out.deferred is None and out.ignored_common == ()
    assert out.result is not None and [r[1] for r in out.result.rows] == [1]      # only ticket 48142
    assert out.extra_filter is not None

async def test_ignored_common_keys_are_reported(env) -> None:
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s, user_id=48120, report_key="builtin.backlog_trend", definition=None,
            overrides=RunOverrides(common={"customer": ["x"], "queue": [48130]}),
            reporting=reporting, cache=None, budget=QueryBudget.inline(reporting))
    assert out.ignored_common == ("customer",)

async def test_extra_filter_never_widens_scope(env) -> None:
    # an "any" extra filter naming the foreign queue still yields only the viewer's queue
    widen = Filter.model_validate({"where": {"any": [{"field": "queue", "op": "in", "value": [48130]},
                                                      {"field": "queue", "op": "in", "value": [48131]}]}})
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(
            s, user_id=48121, report_key=None,
            definition=ReportDefinition.model_validate({"source": "tickets", "dimensions": [{"key": "queue"}],
                                                        "measures": [{"key": "tickets"}], "time_range": JUNE}),
            overrides=RunOverrides(extra_filter=widen), reporting=reporting, cache=None,
            budget=QueryBudget.inline(reporting))
    assert [r[0] for r in out.result.rows] == [48131]

async def test_deferred_and_enqueue_carry_extra_filter(env) -> None:
    key = await _saved(env, 48120)
    async with env.factory() as s:
        reporting = await load_reporting_settings(s, Settings(environment="test"))
        out = await run_report_ref(s, user_id=48120, report_key=key, definition=None,
                                   overrides=RunOverrides(common={"queue": [48130]}), reporting=reporting,
                                   cache=None, budget=QueryBudget.inline(reporting), force_job=True)
        assert out.result is None and out.deferred == "forced"
        accepted = await enqueue_report_ref(s, user_id=48120, run=ReportRunIn(report_key=key),
                                            extra_filter=out.extra_filter)
    await env.run_jobs()
    env.as_user(48120)
    job = await env.job(accepted.job_id)
    assert job["status"] == "succeeded"
    payload = _job_payload(env, accepted.job_id)                       # SELECT payload FROM tiqora_job
    assert payload["extra_filter"]["where"]["field"] == "queue"

async def test_enqueue_cap_raises(env) -> None:
    async with env.factory() as s:
        for _ in range(MAX_ACTIVE_REPORT_JOBS_PER_USER):
            await enqueue_report_ref(s, user_id=48120, run=ReportRunIn(report_key="builtin.ticket_kpis"), extra_filter=None)
        with pytest.raises(TooManyReportJobs):
            await enqueue_report_ref(s, user_id=48120, run=ReportRunIn(report_key="builtin.ticket_kpis"), extra_filter=None)

async def test_old_payload_without_extra_filter_still_runs(env) -> None:
    # a job row written by wave 1 (no extra_filter key) is accepted by the new payload model
    assert ReportRunPayload.model_validate({"user_id": 48120, "run": {"report_key": "builtin.ticket_kpis"}}).extra_filter is None
```

Cleanup adds `tiqora_job`/`tiqora_job_file` rows of `core.report_run` created by band users (`created_by BETWEEN :lo AND :hi`).

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** D6. Move `RequestError` + `prepare_run` into `run_service.py` (behaviour identical for built-ins/inline definitions); `prepare_ref` resolves keys via `resolve_report_ref` (admin flag from `PermissionEngine.is_admin`). Move the route's body (plan → cache → deferral → execute) into `run_report_ref`, and `_check_job_limit` + enqueue into `enqueue_report_ref`; the route maps `RequestError` (status from the error), `TooManyReportJobs` → 429 (unchanged message), timeouts → 504 as today. The export route calls `prepare_run` exactly as before (key resolution comes for free). `common_fields` per D6 table; `_source_out` fills `ops` (`sorted(spec.allowed_ops())` from `src.filter_spec(d.key)`, `[]` when None) and `enum_values`.
- [ ] **Step 4: Run → PASS** — new module, the semantic tests and the full wave-1 reporting set (`uv run python -m pytest -q tests/test_reports_api.py tests/test_reporting_*.py tests/test_stats*.py`) unchanged. Regenerate openapi + `pnpm build`; frontend `pnpm lint && pnpm test --run`. Lint gates.
- [ ] **Step 5: Commit** `feat(reporting): run saved reports by key; run service with dashboard filters`.

---

### Task 5: List tickets by filter — `POST /tickets/query` (backend)

**Files:**
- Modify: `backend/src/tiqora/domain/ticket_service.py` (`page_from_stmt`; `list_tickets` uses it), `backend/src/tiqora/domain/schemas.py` (`TicketSortKey`), `backend/src/tiqora/api/v1/tickets.py` (`TicketQueryIn`, route before `/{ticket_id}`), openapi + schema.d.ts
- Test: `backend/tests/test_tickets_query_api.py` (both dialects, band 48170–48199)

**Interfaces:**
- Consumes: `compile_ticket_filter`, `ticket_query_scope`, `PermissionEngine.is_admin`.
- Produces: `TicketService.page_from_stmt(user_id, stmt, *, offset, limit, sort, order) -> PaginatedTickets`, `TicketSortKey`, `POST /api/v1/tickets/query`.

Seeds: user 48170 `rep48.query` (`ro` on group/queue 48171), foreign group/queue 48172; tickets 48180 (`rep48 alpha`, queue 48171, created 2026-01-02), 48181 (`rep48 beta`, queue 48171, created 2026-01-03, archived `archive_flag = 1`), 48182 (`rep48 gamma`, queue 48172).

- [ ] **Step 1: Write the failing tests**

```python
def test_sort_keys_match_service() -> None:
    assert set(get_args(TicketSortKey)) == set(TicketService._SORT_COLUMNS)

async def test_query_filters_sorts_and_pages(env) -> None:
    env.as_user(48170)
    body = {"filter": {"where": {"field": "title", "op": "starts_with", "value": "rep48"}},
            "sort": "created", "order": "asc", "limit": 1}
    page = (await env.client.post("/api/v1/tickets/query", json=body)).json()
    assert page["total"] == 1 and [t["ticket_id"] for t in page["items"]] == [48180]   # archived + foreign excluded

async def test_query_equals_list_tickets_for_same_params(env) -> None:
    env.as_user(48170)
    listed = (await env.client.get("/api/v1/tickets", params={"queue_id": 48171, "sort": "tn"})).json()
    queried = (await env.client.post("/api/v1/tickets/query", json={
        "filter": {"where": {"field": "queue_id", "op": "eq", "value": 48171}}, "sort": "tn"})).json()
    assert queried == listed                                         # same enrichment, same page shape

async def test_include_archived_admin_only(env) -> None:
    env.as_user(48170)
    body = {"filter": {"where": {"field": "queue_id", "op": "eq", "value": 48171}}, "include_archived": True}
    assert (await env.client.post("/api/v1/tickets/query", json=body)).json()["total"] == 1
    _grant_admin(env.sync_url, 48170)                                 # rw on group "admin" for this test only
    assert (await env.client.post("/api/v1/tickets/query", json=body)).json()["total"] == 2

async def test_invalid_filter_is_422_with_path(env) -> None:
    env.as_user(48170)
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {"where": {"field": "nope", "op": "eq", "value": 1}}})
    assert resp.status_code == 422 and resp.json()["detail"]["path"] == "where.field"
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}, "sort": "bogus"})
    assert resp.status_code == 422

async def test_query_route_is_not_shadowed_by_ticket_id(env) -> None:
    env.as_user(48170)
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": {}})
    assert resp.status_code == 200 and resp.json()["total"] == 1
```

(`_grant_admin` inserts a `group_user` row for 48170 on the group named `admin` with `rw`; cleanup deletes `group_user WHERE user_id BETWEEN :lo AND :hi`.)

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — move lines "count → order → page → enrichment → `PaginatedTickets`" of `list_tickets` into `page_from_stmt` (stmt `None` → empty page); `list_tickets` calls it (existing ticket list tests are the regression gate). Route per D7.
- [ ] **Step 4: Run → PASS** (new module + `tests/test_filters_ticket_params.py tests/test_filters_tickets.py` + the ticket list tests `uv run python -m pytest -q -k "list_tickets or tickets_api"`); openapi regen + `pnpm build`. Lint gates.
- [ ] **Step 5: Commit** `feat(tickets): list tickets by a saved filter (POST /tickets/query)`.

---

### Task 6: Saved searches — store, API, counts, "Ansicht speichern" (backend)

**Files:**
- Create: `backend/src/tiqora/saved_searches/__init__.py`, `store.py`, `schemas.py` (non-import models of D8), `backend/src/tiqora/api/v1/saved_searches.py`
- Modify: `backend/src/tiqora/api/v1/__init__.py` (router), the API-key area map (`/api/v1/saved-searches` → `tickets`), openapi + schema.d.ts
- Test: `backend/tests/test_saved_searches_api.py` (both dialects, band 48200–48249)

**Interfaces:**
- Consumes: Task 5 `page_from_stmt` (not needed by the API itself), `count_tickets`, `ticket_filter_fields`, `filter_from_list_params`, `check_share_groups`, `statement_timeout`, `is_timeout_error`, `ReportOwnerOut` (reused for owners).
- Produces: D8 store functions, models and routes (`SavedSearch`, `SavedSearchPrefs`, `SavedSearchData`, `list_visible`, `get_visible`, `create_search`, `update_search`, `set_prefs`, `delete_search`, `upsert_imported`, `validate_criteria`, `MAX_SIDEBAR_COUNTS`, `COUNT_TIMEOUT_SECONDS`).

Seeds: users 48200 `rep48.searcher` (`ro` on group/queue 48210 and group 48212), 48201 `rep48.colleague` (`ro` on group/queue 48211 and group 48212), 48202 `rep48.other`; queues 48210, 48211; tickets 48220/48221 (queue 48210, `customer_user_id = rep48.cust@example.org`), 48222 (queue 48211); dynamic field 48230 `Rep48Area` (Dropdown, ticket, valid) with a value on 48220.

- [ ] **Step 1: Write the failing tests**

```python
Q210 = {"where": {"field": "queue_id", "op": "in", "value": [48210, 48211]}}

async def test_create_list_get_and_prefs(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 Meine", "criteria": Q210, "show_count": True})).json()
    assert made["is_owner"] and made["can_edit"] and made["show_in_sidebar"] and made["show_count"]
    assert made["sort_by"] == "age" and made["source"] == "tiqora" and made["version"] == 1
    listed = (await env.client.get("/api/v1/saved-searches")).json()
    assert [s["id"] for s in listed] == [made["id"]]

async def test_create_from_list_params(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={
        "name": "rep48 Ansicht", "list_params": {"queue_id": 48210, "state_type": "todo", "unassigned": True}})).json()
    where = made["criteria"]["where"]["all"]
    assert {"field": "queue_id", "op": "eq", "value": 48210} in where
    assert {"field": "unassigned", "op": "is_true"} in where

async def test_exactly_one_of_criteria_or_list_params(env) -> None:
    env.as_user(48200)
    both = await env.client.post("/api/v1/saved-searches", json={"name": "rep48 x", "criteria": Q210, "list_params": {}})
    none = await env.client.post("/api/v1/saved-searches", json={"name": "rep48 y"})
    assert both.status_code == 422 and none.status_code == 422

async def test_invalid_criteria_is_422_with_path(env) -> None:
    env.as_user(48200)
    resp = await env.client.post("/api/v1/saved-searches", json={"name": "rep48 z",
        "criteria": {"where": {"field": "queue_id", "op": "contains", "value": "x"}}})
    assert resp.status_code == 422 and resp.json()["detail"]["path"] == "criteria.where.op"

async def test_shared_search_lists_only_viewer_queues(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 Team", "criteria": Q210,
                                                                  "group_ids": [48212]})).json()
    env.as_user(48201)
    seen = next(s for s in (await env.client.get("/api/v1/saved-searches")).json() if s["id"] == made["id"])
    assert seen["is_owner"] is False and seen["can_edit"] is False and seen["show_in_sidebar"] is False
    page = (await env.client.post("/api/v1/tickets/query", json={"filter": seen["criteria"]})).json()
    assert [t["ticket_id"] for t in page["items"]] == [48222]        # the colleague's own queue only
    await env.client.put(f"/api/v1/saved-searches/{made['id']}/prefs", json={"show_in_sidebar": True, "show_count": True})
    counts = (await env.client.get("/api/v1/saved-searches/counts")).json()["counts"]
    assert counts[str(made["id"])] == 1
    env.as_user(48202)
    assert (await env.client.get(f"/api/v1/saved-searches/{made['id']}")).status_code == 404

async def test_only_owner_or_admin_edits_and_deletes(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 E", "criteria": Q210, "group_ids": [48212]})).json()
    env.as_user(48201)
    upd = {"name": "rep48 E2", "criteria": Q210, "version": 1}
    assert (await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=upd)).status_code == 403
    assert (await env.client.delete(f"/api/v1/saved-searches/{made['id']}")).status_code == 403
    env.as_user(1)
    assert (await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=upd)).status_code == 200

async def test_search_update_with_stale_version_is_409(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 V", "criteria": Q210})).json()
    body = {"name": "rep48 V2", "criteria": Q210, "version": 1}
    assert (await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=body)).status_code == 200
    stale = await env.client.put(f"/api/v1/saved-searches/{made['id']}", json=body)
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2

async def test_deleted_dynamic_field_marks_search_broken(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 DF", "show_count": True,
        "criteria": {"where": {"field": "df.Rep48Area", "op": "in", "value": ["Nord"]}}})).json()
    _run_sql(env.sync_url, ("UPDATE dynamic_field SET valid_id = 2 WHERE id = 48230",))
    item = next(s for s in (await env.client.get("/api/v1/saved-searches")).json() if s["id"] == made["id"])
    assert item["criteria"] is not None                               # stored JSON still parses
    counts = (await env.client.get("/api/v1/saved-searches/counts")).json()["counts"]
    assert counts[str(made["id"])] is None                            # cannot be counted, no 500
    resp = await env.client.post("/api/v1/tickets/query", json={"filter": item["criteria"]})
    assert resp.status_code == 422

async def test_counts_only_sidebar_with_count_and_capped(env, monkeypatch) -> None:
    monkeypatch.setattr(store, "MAX_SIDEBAR_COUNTS", 2)
    env.as_user(48200)
    ids = [(await env.client.post("/api/v1/saved-searches", json={"name": f"rep48 C{i}", "criteria": Q210,
            "show_count": i != 1})).json()["id"] for i in range(4)]
    counts = (await env.client.get("/api/v1/saved-searches/counts")).json()["counts"]
    assert set(counts) == {str(ids[0]), str(ids[2])}                   # first two by position with show_count

async def test_count_timeout_gives_null(env, monkeypatch) -> None:
    async def _slow(*_a, **_k):
        raise ReportQueryTimeout()
    monkeypatch.setattr(saved_searches_api, "count_tickets", _slow)
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 T", "criteria": Q210, "show_count": True})).json()
    assert (await env.client.get("/api/v1/saved-searches/counts")).json()["counts"][str(made["id"])] is None

async def test_delete_cascades_prefs_and_groups(env) -> None:
    env.as_user(48200)
    made = (await env.client.post("/api/v1/saved-searches", json={"name": "rep48 D", "criteria": Q210, "group_ids": [48212]})).json()
    assert (await env.client.delete(f"/api/v1/saved-searches/{made['id']}")).status_code == 204
    left = _scalar(env.sync_url, "SELECT COUNT(*) FROM tiqora_saved_search_pref WHERE saved_search_id = %d" % made["id"])
    assert left == 0
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** D8. The counting loop lives in `api/v1/saved_searches.py` (`_sidebar_counts`), which imports `count_tickets` from `tiqora.filters.tickets` into its own namespace and calls it by that name, and reads the cap as `store.MAX_SIDEBAR_COUNTS` (module attribute, at call time) — the tests patch exactly these two names. Criteria stored with `Filter.model_dump(by_alias=True, exclude_none=True)`; loading uses `Filter.model_validate` (error → `criteria=None`, `criteria_error="Die gespeicherte Suche kann nicht mehr gelesen werden."`). `list_params` → `filter_from_list_params`; its `include_archived` copies into the row. `FilterError` from `validate_criteria` → 422 with `path = "criteria." + exc.path`. Owner names via `resolve_labels(session, "user", ...)`. Position on create = max(position of the user's prefs) + 1. Per-user limit → 422 "Es sind höchstens 200 gespeicherte Suchen pro Person möglich.".
- [ ] **Step 4: Run → PASS**; openapi regen + `pnpm build`. Lint gates.
- [ ] **Step 5: Commit** `feat(searches): saved ticket searches with sharing, sidebar prefs and counts`.

---

### Task 7: Znuny search-profile mapping (backend, pure)

**Files:**
- Create: `backend/src/tiqora/saved_searches/znuny_import.py`
- Test: `backend/tests/test_znuny_search_profile_mapping.py` (pure, no DB, no ids)

**Interfaces:**
- Produces: `ZnunyLookups`, `UnmappedItem`, `MappedProfile`, `map_search_profile`, constants `IGNORED_KEYS`, `FULLTEXT_REASON`, `UNSUPPORTED_REASON`, `OPERATOR_REASON`, `FUTURE_REASON`, `NO_TIME_FIELD_REASON`, `UNKNOWN_DF_REASON`.

Fixture lookups used by every test:

```python
LOOKUPS = ZnunyLookups(
    state_type_names={1: "new", 2: "open", 3: "closed"},
    queue_names={10: "Support", 11: "Support::Level 2", 12: "Support::Level 2::Netz", 13: "Sales"},
    lock_names={1: "unlock", 2: "lock", 3: "tmp_lock"},
    dynamic_fields={"Area": "Dropdown", "Note": "Text", "Vip": "Checkbox", "Due": "Date"},
    time_zone="Europe/Berlin",
)

def where(m: MappedProfile) -> Any:
    return m.criteria.model_dump(by_alias=True, exclude_none=True).get("where")
```

- [ ] **Step 1: Write the failing tests**

```python
def test_id_lists_and_state_types() -> None:
    m = map_search_profile("P", {"QueueIDs": ["13"], "StateTypeIDs": ["1", "2"], "OwnerIDs": ["5"],
                                  "PriorityIDs": ["3"], "WatchUserIDs": ["7"]}, LOOKUPS)
    assert where(m) == {"all": [                          # order = rows of the D9 mapping table
        {"field": "queue_id", "op": "in", "value": [13]},
        {"field": "priority_id", "op": "in", "value": [3]},
        {"field": "owner_id", "op": "in", "value": [5]},
        {"field": "state_type", "op": "in", "value": ["new", "open"]},
        {"field": "watched_by", "op": "in", "value": [7]}]}
    assert m.unmapped == ()

def test_sub_queues_expand_by_name() -> None:
    m = map_search_profile("P", {"QueueIDs": ["11"], "UseSubQueues": "1"}, LOOKUPS)
    assert where(m) == {"field": "queue_id", "op": "in", "value": [11, 12]}

def test_lock_ids() -> None:
    assert where(map_search_profile("P", {"LockIDs": ["2", "3"]}, LOOKUPS)) == {"field": "locked", "op": "is_true"}
    assert where(map_search_profile("P", {"LockIDs": ["1"]}, LOOKUPS)) == {"field": "locked", "op": "is_false"}
    assert where(map_search_profile("P", {"LockIDs": ["1", "2"]}, LOOKUPS)) is None

@pytest.mark.parametrize(("value", "cond"), [
    ("2026101012345", {"field": "tn", "op": "eq", "value": "2026101012345"}),
    ("2026*", {"field": "tn", "op": "starts_with", "value": "2026"}),
    ("*1234*", {"field": "tn", "op": "contains", "value": "1234"}),
])
def test_ticket_number_pattern_rule(value: str, cond: dict) -> None:
    assert where(map_search_profile("P", {"TicketNumber": value}, LOOKUPS)) == cond

def test_title_words_are_all_contains() -> None:
    assert where(map_search_profile("P", {"Title": "Drucker  Etage"}, LOOKUPS)) == {"all": [
        {"field": "title", "op": "contains", "value": "Drucker"},
        {"field": "title", "op": "contains", "value": "Etage"}]}

def test_customer_raw_wins_over_pattern_key() -> None:
    m = map_search_profile("P", {"CustomerIDRaw": "ACME*", "CustomerID": "other*"}, LOOKUPS)
    assert where(m) == {"field": "customer_id", "op": "eq", "value": "ACME*"} and m.unmapped == ()

def test_archive_flags() -> None:
    m = map_search_profile("P", {"SearchInArchive": "ArchivedTickets"}, LOOKUPS)
    assert m.include_archived and where(m) == {"field": "archived", "op": "is_true"}
    m = map_search_profile("P", {"SearchInArchive": "AllTickets"}, LOOKUPS)
    assert m.include_archived and where(m) is None

def test_time_point_last_and_before_with_month_as_30_days() -> None:
    m = map_search_profile("P", {"TimeSearchType": "TimePoint", "TicketCreateTimePoint": "2",
                                  "TicketCreateTimePointStart": "Last", "TicketCreateTimePointFormat": "month"}, LOOKUPS)
    assert where(m) == {"field": "created", "op": "within_last", "value": {"amount": 60, "unit": "day"}}
    m = map_search_profile("P", {"ChangeTimeSearchType": "TimePoint", "TicketChangeTimePoint": "1",
                                  "TicketChangeTimePointStart": "Before", "TicketChangeTimePointFormat": "year"}, LOOKUPS)
    assert where(m) == {"field": "changed", "op": "older_than", "value": {"amount": 365, "unit": "day"}}

def test_time_slot_is_local_day_bounds_in_utc() -> None:
    m = map_search_profile("P", {"TimeSearchType": "TimeSlot",
        "TicketCreateTimeStartYear": "2026", "TicketCreateTimeStartMonth": "7", "TicketCreateTimeStartDay": "1",
        "TicketCreateTimeStopYear": "2026", "TicketCreateTimeStopMonth": "07", "TicketCreateTimeStopDay": "31"}, LOOKUPS)
    assert where(m) == {"field": "created", "op": "between",
                        "value": {"from": "2026-06-30T22:00:00", "to": "2026-07-31T21:59:59"}}   # CEST = UTC+2

def test_future_time_point_is_unmapped() -> None:
    m = map_search_profile("P", {"PendingTimeSearchType": "TimePoint", "TicketPendingTimePoint": "1",
                                  "TicketPendingTimePointStart": "Next", "TicketPendingTimePointFormat": "day"}, LOOKUPS)
    assert where(m) is None
    assert [(u.key, u.reason) for u in m.unmapped] == [("TicketPendingTimePoint", FUTURE_REASON)]

def test_dynamic_fields_by_type() -> None:
    m = map_search_profile("P", {"Search_DynamicField_Area": ["Nord", "Süd"], "Search_DynamicField_Note": "*VIP*",
                                  "Search_DynamicField_Vip": ["1"]}, LOOKUPS)
    assert where(m) == {"all": [
        {"field": "df.Area", "op": "in", "value": ["Nord", "Süd"]},
        {"field": "df.Note", "op": "contains", "value": "VIP"},
        {"field": "df.Vip", "op": "is_true"}]}
    assert where(map_search_profile("P", {"Search_DynamicField_Vip": ["1", "-1"]}, LOOKUPS)) is None

def test_dynamic_date_accepts_swapped_format_and_start() -> None:
    a = map_search_profile("P", {"Search_DynamicField_DueTimePoint": "1", "Search_DynamicField_DueTimePointFormat": "week",
                                  "Search_DynamicField_DueTimePointStart": "Before", "Search_DynamicField_DueTimePointValue": "2"}, LOOKUPS)
    b = map_search_profile("P", {"Search_DynamicField_DueTimePoint": "1", "Search_DynamicField_DueTimePointFormat": "Before",
                                  "Search_DynamicField_DueTimePointStart": "week", "Search_DynamicField_DueTimePointValue": "2"}, LOOKUPS)
    expected = {"field": "df.Due", "op": "older_than", "value": {"amount": 2, "unit": "week"}}
    assert where(a) == expected and where(b) == expected

def test_every_unmappable_key_is_reported() -> None:
    m = map_search_profile("P", {
        "Fulltext": "drucker", "Subject": "x", "From": "a@example.org", "CreatedQueueIDs": ["1"],
        "TicketNumber": "123 || 456", "Title": "a && b", "StateTypeIDs": ["99"],
        "Search_DynamicField_Gone": "x", "CloseTimeSearchType": "TimePoint", "TicketCloseTimePoint": "1",
        "TicketCloseTimePointStart": "Last", "TicketCloseTimePointFormat": "day", "Frobnicate": "1",
        "ShownAttributes": ["LabelTitle"], "ResultForm": "Normal"}, LOOKUPS)
    reasons = {u.key: u.reason for u in m.unmapped}
    assert reasons == {
        "Fulltext": FULLTEXT_REASON, "Subject": FULLTEXT_REASON, "From": FULLTEXT_REASON,
        "CreatedQueueIDs": UNSUPPORTED_REASON, "TicketNumber": OPERATOR_REASON, "Title": OPERATOR_REASON,
        "StateTypeIDs": "Statustyp unbekannt.", "Search_DynamicField_Gone": UNKNOWN_DF_REASON,
        "CloseTimeSearchType": NO_TIME_FIELD_REASON, "Frobnicate": UNSUPPORTED_REASON}
    assert where(m) is None                                   # UI-only keys ignored, nothing invented

def test_empty_values_are_skipped_silently() -> None:
    m = map_search_profile("P", {"Title": "", "QueueIDs": [], "TicketNumber": "*", "Fulltext": ""}, LOOKUPS)
    assert where(m) is None and m.unmapped == ()

def test_result_passes_wave0_validation() -> None:
    m = map_search_profile("P", {"QueueIDs": ["13"], "TimeSearchType": "TimePoint", "TicketCreateTimePoint": "3",
                                  "TicketCreateTimePointStart": "Last", "TicketCreateTimePointFormat": "week"}, LOOKUPS)
    validate_filter(m.criteria, TICKET_FILTER_FIELDS.get)     # static catalog: raises on any invalid field/op/value
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** per D9 (one dispatcher over the mapping table; helpers `_pattern(field, value)`, `_time_point(field, amount, start, unit)`, `_time_slot(field, prefix, values, zone)` using `zoneinfo` and `datetime.combine` → `.astimezone(UTC).replace(tzinfo=None).isoformat()`; sub-key sets of each time type and each DF date are consumed so they never appear as unsupported; values shortened to 200 chars in `UnmappedItem`). Conditions in the order of the mapping table, single condition unwrapped (as the tests expect).
- [ ] **Step 4: Run → PASS.** Lint gates.
- [ ] **Step 5: Commit** `feat(searches): map Znuny search profiles to Tiqora filters`.

---

### Task 8: Znuny import — read-only reader and preview/apply API (backend)

**Files:**
- Create: `backend/src/tiqora/saved_searches/znuny_reader.py`
- Modify: `backend/src/tiqora/db/legacy/misc.py` (`SEARCH_PROFILE`), `backend/src/tiqora/saved_searches/schemas.py` (import models), `backend/src/tiqora/api/v1/saved_searches.py` (two routes), openapi + schema.d.ts
- Test: `backend/tests/test_znuny_search_import_api.py` (both dialects, band 48250–48289; profile rows by login)

**Interfaces:**
- Consumes: Task 7 `map_search_profile`; Task 6 `upsert_imported`, `validate_criteria`; `resolve_user_time_zone`.
- Produces: `load_znuny_profiles`, `load_lookups`, `SEARCH_PROFILE`, D9 API models and routes.

Seeds: user 48250 login `rep48.Import` (mixed case on purpose), `ro` on group/queue 48260; `search_profile` rows with login `TicketSearch::rep48.Import`: profile `Offen Support` = `QueueIDs` ARRAY ["48260"], `StateTypeIDs` ARRAY [id of `open`], `ShownAttributes` ARRAY ["LabelTitle"]; profile `Volltext` = `Fulltext` SCALAR "drucker"; profile `last-search` = `Title` SCALAR "x"; profile `Gemischt` = `Title` "Drucker", `Subject` "y"; and with login `TicketSearch::REP48.IMPORT` (case test): profile `Gross` = `TicketNumber` SCALAR "1*". Cleanup: `DELETE FROM search_profile WHERE login LIKE 'TicketSearch::rep48%' OR login LIKE 'TicketSearch::REP48%'`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_reader_groups_arrays_and_skips_last_search(env) -> None:
    async with env.factory() as s:
        profiles = await load_znuny_profiles(s, "rep48.Import")
    assert set(profiles) == {"Offen Support", "Volltext", "Gemischt", "Gross"}
    assert profiles["Offen Support"]["QueueIDs"] == ["48260"]
    assert profiles["Volltext"]["Fulltext"] == "drucker"

async def test_reader_matches_login_case_insensitively(env) -> None:
    async with env.factory() as s:
        profiles = await load_znuny_profiles(s, "rep48.import")
    assert profiles["Gross"] == {"TicketNumber": "1*"}

async def test_preview_lists_mapped_and_unmapped(env) -> None:
    env.as_user(48250)
    prev = (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]
    by = {p["name"]: p for p in prev}
    assert by["Offen Support"]["importable"] is True and by["Offen Support"]["unmapped"] == []
    assert by["Volltext"]["importable"] is False
    assert by["Volltext"]["unmapped"][0]["key"] == "Fulltext"
    assert [u["key"] for u in by["Gemischt"]["unmapped"]] == ["Subject"]
    assert all(p["existing_id"] is None for p in prev)

async def test_apply_creates_then_updates_never_duplicates(env) -> None:
    env.as_user(48250)
    first = (await env.client.post("/api/v1/saved-searches/import/znuny",
                                   json={"names": ["Offen Support", "Volltext", "Gibt es nicht"]})).json()
    assert len(first["created"]) == 1 and first["updated"] == []
    assert {s["name"]: s["reason"] for s in first["skipped"]} == {
        "Volltext": "Keine Bedingung übernehmbar.", "Gibt es nicht": "Suchvorlage nicht gefunden."}
    second = (await env.client.post("/api/v1/saved-searches/import/znuny", json={"names": ["Offen Support"]})).json()
    assert second["updated"] == first["created"] and second["created"] == []
    mine = [s for s in (await env.client.get("/api/v1/saved-searches")).json() if s["source"] == "znuny"]
    assert len(mine) == 1 and mine[0]["source_name"] == "Offen Support" and mine[0]["version"] == 2
    page = (await env.client.post("/api/v1/tickets/query", json={"filter": mine[0]["criteria"]})).json()
    assert page["total"] >= 0                                         # imported criteria run (valid filter)

async def test_import_never_writes_search_profile(env) -> None:
    before = _rows(env.sync_url, "SELECT login, profile_name, profile_type, profile_key, profile_value"
                   " FROM search_profile WHERE login LIKE 'TicketSearch::rep48%%' ORDER BY 1, 2, 4, 5")
    env.as_user(48250)
    await env.client.get("/api/v1/saved-searches/import/znuny")
    await env.client.post("/api/v1/saved-searches/import/znuny", json={"names": ["Offen Support", "Gemischt"]})
    after = _rows(env.sync_url, "SELECT login, profile_name, profile_type, profile_key, profile_value"
                  " FROM search_profile WHERE login LIKE 'TicketSearch::rep48%%' ORDER BY 1, 2, 4, 5")
    assert after == before

async def test_other_users_profiles_are_invisible(env) -> None:
    _insert_profile(env.sync_url, "TicketSearch::rep48.someoneelse", "Fremd", "Title", "SCALAR", "x")
    env.as_user(48250)
    names = {p["name"] for p in (await env.client.get("/api/v1/saved-searches/import/znuny")).json()["profiles"]}
    assert "Fremd" not in names
```

Also a static guard: `test_search_profile_table_is_not_in_legacy_metadata` asserts `"search_profile" not in LegacyBase.metadata.tables` and `SEARCH_PROFILE.metadata is not LegacyBase.metadata`.

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** D9 reader (Core select on `SEARCH_PROFILE`, `func.lower(login) == func.lower(literal)` bound), lookups (`ticket_state_type`, valid `queue` names, `ticket_lock_type`, valid ticket dynamic fields `object_type = 'Ticket'`, `resolve_user_time_zone`), and routes (preview runs `map_search_profile` per profile and `validate_criteria`; a failing validation turns the profile non-importable with the FilterError message as an extra `UnmappedOut(key="criteria", value="", reason=message)`; apply per D9, one commit at the end).
- [ ] **Step 4: Run → PASS**; openapi regen + `pnpm build`. Lint gates.
- [ ] **Step 5: Commit** `feat(searches): import Znuny search profiles (read-only) with an import report`.

---

### Task 9: GDPR — erased customer logins inside saved criteria (backend)

**Files:**
- Create: `backend/src/tiqora/gdpr/saved_criteria.py`
- Modify: `backend/src/tiqora/gdpr/erasure.py` (`run_erasure` collects `{old_login: new_value}` where it rewrites/deletes tickets and calls `scrub_saved_criteria` in the same transaction)
- Test: `backend/tests/test_gdpr_saved_criteria.py` (pure part + both dialects, band 48290–48319)

**Interfaces:**
- Consumes: D2 tables; `load_definition`, `dump_definition` (Task 2).
- Produces: `replace_filter_values`, `scrub_saved_criteria` (D10).

- [ ] **Step 1: Write the failing tests**

```python
def test_replace_filter_values_walks_tree_case_insensitively() -> None:
    where = {"any": [
        {"field": "customer_user_id", "op": "in", "value": ["Max@Example.org", "keep@example.org"]},
        {"not": {"field": "customer_user_id", "op": "eq", "value": "max@example.org"}},
        {"field": "title", "op": "contains", "value": "max@example.org"}]}
    new, changed = replace_filter_values(where, frozenset({"customer_user_id"}), {"max@example.org": "anon-1"})
    assert changed is True
    assert new == {"any": [
        {"field": "customer_user_id", "op": "in", "value": ["anon-1", "keep@example.org"]},
        {"not": {"field": "customer_user_id", "op": "eq", "value": "anon-1"}},
        {"field": "title", "op": "contains", "value": "max@example.org"}]}     # other fields untouched
    assert replace_filter_values(None, frozenset({"customer_user_id"}), {"a": "b"}) == (None, False)

async def test_scrub_rewrites_searches_and_report_filters_and_parameters(env) -> None:
    _seed_rows(env.sync_url)   # search 48290 (owner 48291) with customer_user_id eq; report 48292 with filter customer_user
                               # in [..] and a parameter {field: customer_user, allowed_values: [...], default: [...]};
                               # one search with unparsable criteria '{' (must be skipped)
    async with env.factory() as s:
        n = await scrub_saved_criteria(s, {"max@example.org": "anon-1"})
        await s.commit()
    assert n == 2
    crit = json.loads(_scalar(env.sync_url, "SELECT criteria FROM tiqora_saved_search WHERE owner_user_id = 48291 AND name = 'rep48 g1'"))
    assert crit["where"]["value"] == "anon-1"
    defn = json.loads(_scalar(env.sync_url, "SELECT definition FROM tiqora_report WHERE owner_user_id = 48291"))
    assert defn["filter"]["where"]["value"] == ["anon-1"]
    assert defn["parameters"][0]["allowed_values"] == ["anon-1", "other@example.org"]
    assert defn["parameters"][0]["default"] == ["anon-1"]
    assert _scalar(env.sync_url, "SELECT version FROM tiqora_report WHERE owner_user_id = 48291") == 2

async def test_erasure_anonymise_calls_scrub(env, monkeypatch) -> None:
    seen: list[dict[str, str]] = []
    async def _spy(session, mapping):
        seen.append(dict(mapping))
        return 0
    monkeypatch.setattr(erasure, "scrub_saved_criteria", _spy)
    await _run_small_anonymise(env, login="rep48.erase@example.org")   # existing erasure test helper pattern (test_gdpr_*)
    assert len(seen) == 1 and list(seen[0]) == ["rep48.erase@example.org"]
    assert seen[0]["rep48.erase@example.org"] != "rep48.erase@example.org"
```

(`_run_small_anonymise` reuses the seeding and `run_erasure` call of `backend/tests/test_gdpr_erasure.py` (one customer user with login `rep48.erase@example.org`, one ticket), with band ids 48300–48319 and the module's own cleanup.)

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** D10; in `run_erasure` build the mapping from the same login rewrite the tickets get (anonymise: new login; delete: `"geloescht-" + sha256(login.encode()).hexdigest()[:12]`) and call `await scrub_saved_criteria(session, mapping)` once before the commit; log the number of rows changed.
- [ ] **Step 4: Run → PASS** + the existing GDPR suites (`uv run python -m pytest -q tests/test_gdpr*.py`). Lint gates.
- [ ] **Step 5: Commit** `feat(gdpr): scrub erased customer logins from saved searches and reports`.

---

### Task 10: Frontend API layer and demo data (frontend)

**Files:**
- Modify: `frontend/src/lib/reportsApi.ts` (D11 additions)
- Create: `frontend/src/lib/savedSearchesApi.ts`, `frontend/src/lib/savedSearchesApi.test.ts`, `frontend/src/lib/reportsApi.test.ts` (if absent; else extend), `frontend/src/demo/savedMock.ts`, `frontend/src/demo/savedMock.test.ts`
- Modify: `frontend/src/demo/mockData.ts` (`resolveData` branches for every new GET; POST/PUT/DELETE answered from an in-memory store in `savedMock.ts`), `frontend/src/demo/handlers.ts` (YAML/JSON file download answer for `/reports/*/file`)
- No i18n keys in this task.

**Interfaces:**
- Consumes: generated `Schemas` from Tasks 2–8.
- Produces: D11 `reportsApi` additions, `reportTitle`, `reportDescription`, `isSavedKey`, `savedSearchesApi`; demo: `demoSavedReports()`, `demoSavedSearches()`, `demoZnunyPreview()`, `resolveSavedRequest(method, path, body)`.

- [ ] **Step 1: Write the failing tests**

```ts
// reportsApi.test.ts
import { vi } from "vitest";
import { api } from "./api";
import { isSavedKey, reportTitle, reportsApi } from "./reportsApi";

it("calls the saved-report routes", async () => {
  const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
  await reportsApi.create({ name: "x", definition: DEF } as never);
  await reportsApi.update(12, { name: "x", definition: DEF, version: 3 } as never);
  await reportsApi.remove(12);
  await reportsApi.importFile("{}");
  await reportsApi.catalogue(undefined, { all: true });
  expect(spy.mock.calls.map((c) => [c[0], c[1]])).toEqual([
    ["POST", "/api/v1/reports"], ["PUT", "/api/v1/reports/12"], ["DELETE", "/api/v1/reports/12"],
    ["POST", "/api/v1/reports/import-file"], ["GET", "/api/v1/reports"]]);
  expect(spy.mock.calls[4][2]).toMatchObject({ query: { all: true } });
});

it("builds file urls and recognises saved keys", () => {
  expect(reportsApi.fileUrl("builtin.volume", "yaml")).toBe("/api/v1/reports/builtin.volume/file?format=yaml");
  expect(isSavedKey("12")).toBe(true);
  expect(isSavedKey("builtin.volume")).toBe(false);
  expect(isSavedKey("012")).toBe(false);
});

it("titles saved reports by name and built-ins by key", () => {
  const t = ((k: string) => `T:${k}`) as never;
  expect(reportTitle({ kind: "saved", name: "Meins" } as never, t)).toBe("Meins");
  expect(reportTitle({ kind: "builtin", title_key: "reports.builtin.volume.title" } as never, t)).toBe("T:reports.builtin.volume.title");
});

// savedSearchesApi.test.ts
it("calls the saved-search, ticket-query and import routes", async () => {
  const spy = vi.spyOn(api, "request").mockResolvedValue({} as never);
  await savedSearchesApi.list(); await savedSearchesApi.counts();
  await savedSearchesApi.setPrefs(5, { show_in_sidebar: true, show_count: false });
  await savedSearchesApi.queryTickets({ filter: { version: 1 } });
  await savedSearchesApi.znunyImport(["A"]);
  expect(spy.mock.calls.map((c) => [c[0], c[1]])).toEqual([
    ["GET", "/api/v1/saved-searches"], ["GET", "/api/v1/saved-searches/counts"],
    ["PUT", "/api/v1/saved-searches/5/prefs"], ["POST", "/api/v1/tickets/query"],
    ["POST", "/api/v1/saved-searches/import/znuny"]]);
  expect(spy.mock.calls[4][2]).toMatchObject({ body: { names: ["A"] } });
});

// savedMock.test.ts
it("demo store round-trips a saved report and a saved search", () => {
  const made = resolveSavedRequest("POST", "/api/v1/reports", { name: "Demo", definition: DEF, visibility: "private", group_ids: [] });
  expect(made).toMatchObject({ kind: "saved", name: "Demo", version: 1 });
  const listed = resolveSavedRequest("GET", "/api/v1/reports", undefined) as { reports: { key: string }[] };
  expect(listed.reports.some((r) => r.key === String((made as { id: number }).id))).toBe(true);
  const search = resolveSavedRequest("POST", "/api/v1/saved-searches", { name: "S", criteria: { version: 1 } });
  expect(resolveSavedRequest("GET", "/api/v1/saved-searches/counts", undefined)).toMatchObject({ counts: expect.any(Object) });
  expect(search).toMatchObject({ name: "S", is_owner: true });
});
```

- [ ] **Step 2: Run → FAIL** `cd frontend && pnpm test --run src/lib/reportsApi.test.ts src/lib/savedSearchesApi.test.ts src/demo/savedMock.test.ts`.
- [ ] **Step 3: Implement** the wrappers (D11) and the demo store (two demo saved reports — one private "Meine offenen Tickets je Queue", one shared "Team-SLA je Kunde" owned by another demo agent; three demo saved searches incl. one shared with a count; a Znuny preview with one fully mappable profile and one with a full-text item). Demo catalogue = built-ins + demo saved reports. Demo runs of saved keys reuse `resolveReportRun` with the saved definition.
- [ ] **Step 4: Run → PASS**; `pnpm lint`.
- [ ] **Step 5: Commit** `feat(frontend): API wrappers and demo data for saved reports and searches`.

---

### Task 11: Shared FilterBuilder (frontend)

**Files:**
- Create: `frontend/src/components/filters/filterModel.ts`, `filterModel.test.ts`, `filterFields.ts`, `filterFields.test.ts`, `useFilterOptions.ts`, `FilterValueInput.tsx`, `FilterBuilder.tsx`, `FilterBuilder.test.tsx`, `frontend/src/components/filters/opsByType.fixture.json` (copy of backend `OPS_BY_TYPE`, sorted lists)
- Modify: `en.json`, `de.json` (`filters.*`, D11) + propagate
- Test: the three test files

**Interfaces:**
- Consumes: `savedSearchesApi.ticketFilterFields` (`FilterFieldOut`), `ReportSource` (`SourceDimensionOut.ops/enum_values/options_source/labels/i18n_values`, `filter_fields`), reference endpoints `/api/v1/reference/{queues,states,priorities,types,services,slas,agents}` (`{id, name}`; agents `{id, full_name}`), `/api/v1/reference/dynamic-fields` (Dropdown/Multiselect `possible_values`), `MultiSelectInput`, `SelectMenu`, `Button`.
- Produces: D11 `filterModel` exports, `FilterBuilder`, `fieldsFromTicketCatalog(fields: FilterFieldOut[], t): FilterFieldDef[]`, `fieldsFromSource(source: ReportSource, t): FilterFieldDef[]`, `useFilterOptions(optionsSource: string | null): { options: {value: string; label: string}[]; isLoading: boolean }`.

`optionsSource` mapping: ticket catalog `options_source` (`queue`, `state`, `priority`, `type`, `service`, `sla`) as is; ticket catalog `owner_id`/`responsible_id`/`watched_by` → `"user"`; ticket catalog `df.<Name>` with Dropdown/Multiselect → `"df:<Name>"`; report source dimensions with `options_source` as is, dimensions with `labels === "user"` → `"user"`; everything else `null` (free input).

- [ ] **Step 1: Write the failing tests**

```ts
// filterModel.test.ts
import opsFixture from "./opsByType.fixture.json";
it("mirrors backend OPS_BY_TYPE", () => {
  for (const [type, ops] of Object.entries(opsFixture)) expect([...OPS_BY_TYPE[type as FilterFieldType]].sort()).toEqual(ops);
});
it("round-trips a two-level filter", () => {
  const f = { version: 1, where: { all: [
    { field: "queue_id", op: "in", value: [1, 2] },
    { any: [{ field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] }] } };
  const { root, editable } = fromFilter(f as never);
  expect(editable).toBe(true);
  expect(toFilter(root)).toEqual(f);
});
it("negated groups round-trip as not", () => {
  const f = { version: 1, where: { not: { any: [{ field: "queue_id", op: "eq", value: 3 }] } } };
  const { root } = fromFilter(f as never);
  expect(root.negate).toBe(true);
  expect(toFilter(root)).toEqual(f);
});
it("marks filters deeper than the UI as not editable", () => {
  const deep = { version: 1, where: { all: [{ any: [{ all: [{ field: "tn", op: "eq", value: "1" }] }] }] } };
  expect(fromFilter(deep as never).editable).toBe(false);
});
it("an empty root gives a filter without where", () => {
  expect(toFilter(fromFilter(null).root)).toEqual({ version: 1 });
});
it("validates values per op", () => {
  const field: FilterFieldDef = { key: "created", label: "Erstellt", type: "datetime", ops: ["within_last", "between"], enumValues: [], optionsSource: null };
  expect(conditionError({ kind: "condition", id: "a", field: "created", op: "within_last", value: { amount: 0, unit: "day" } }, field)).toBe("filters.invalid.number");
  expect(conditionError({ kind: "condition", id: "a", field: "created", op: "between", value: { from: "2026-01-02", to: "" } }, field)).toBe("filters.invalid.date");
  expect(conditionError({ kind: "condition", id: "a", field: "created", op: "within_last", value: { amount: 3, unit: "day" } }, field)).toBeNull();
  expect(emptyValueFor("in", "id")).toEqual([]);
  expect(emptyValueFor("is_true", "bool")).toBeUndefined();
});

// filterFields.test.ts
it("maps ticket catalog fields with options sources", () => {
  const defs = fieldsFromTicketCatalog([
    { key: "owner_id", type: "id", ops: ["eq", "in"], label_key: "ticketFilter.fields.owner_id", label: null, enum_values: [], options_source: null },
    { key: "df.Area", type: "string", ops: ["eq", "in"], label_key: null, label: "Bereich", enum_values: [], options_source: "DynamicField_Area" },
  ] as never, ((k: string) => k) as never);
  expect(defs.map((d) => [d.key, d.optionsSource, d.label])).toEqual([
    ["owner_id", "user", "ticketFilter.fields.owner_id"], ["df.Area", "df:Area", "Bereich"]]);
});
it("maps report source dimensions and filter-only fields, skipping unfilterable ones", () => {
  const defs = fieldsFromSource(SOURCE_FIXTURE as never, ((k: string) => k) as never);
  expect(defs.map((d) => d.key)).toEqual(["queue", "owner", "channel", "created", "reopened"]);  // created_hour (ops []) skipped
  expect(defs.find((d) => d.key === "channel")?.enumValues[0]).toEqual({ value: "email", label: "reports.values.channel.email" });
});

// FilterBuilder.test.tsx
it("adds a condition, limits operators to the field and emits a filter", async () => {
  const onChange = vi.fn();
  render(<FilterBuilder fields={FIELDS} value={null} onChange={onChange} />);
  await user.click(screen.getByTestId("filter-builder-add-condition"));
  await user.click(screen.getByTestId("filter-builder-row-0-field"));
  await user.click(screen.getByRole("option", { name: "Titel" }));
  const ops = within(screen.getByTestId("filter-builder-row-0-op")).queryAllByRole("option");
  expect(ops.map((o) => o.textContent)).not.toContain("zwischen");
  await user.type(screen.getByTestId("filter-builder-row-0-value"), "VPN");
  expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "title", op: "eq", value: "VPN" }] } });
});
it("switching the field resets operator and value", async () => {
  const onChange = vi.fn();
  render(<FilterBuilder fields={FIELDS} value={{ version: 1, where: { all: [{ field: "queue_id", op: "in", value: [1] }] } } as never} onChange={onChange} />);
  await user.click(screen.getByTestId("filter-builder-row-0-field"));
  await user.click(screen.getByRole("option", { name: "Titel" }));
  expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { all: [{ field: "title", op: "eq", value: "" }] } });
});
it("shows a too-complex filter read-only and can reset it", async () => {
  const onChange = vi.fn();
  render(<FilterBuilder fields={FIELDS} value={DEEP as never} onChange={onChange} />);
  expect(screen.getByTestId("filter-builder-too-complex")).toHaveTextContent("zu komplex");
  expect(screen.queryByTestId("filter-builder-add-condition")).toBeNull();
  await user.click(screen.getByTestId("filter-builder-reset"));
  expect(onChange).toHaveBeenLastCalledWith({ version: 1 });
});
it("toggles all/any on the root", async () => {
  const onChange = vi.fn();
  render(<FilterBuilder fields={FIELDS} value={{ version: 1, where: { all: [
    { field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] } } as never} onChange={onChange} />);
  await user.click(screen.getByTestId("filter-builder-mode-any"));
  expect(onChange).toHaveBeenLastCalledWith({ version: 1, where: { any: [
    { field: "title", op: "contains", value: "VPN" }, { field: "locked", op: "is_true" }] } });
});
```


- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `FilterValueInput` per type/op: id/enum with options → `MultiSelectInput` for `in`/`not_in`, `SelectMenu` for `eq`/`neq`; string → text input (lists for `in`/`not_in` via `TagInput`); int → number input (`between` two inputs); datetime → date inputs (`before`/`after`/`between`, value ISO `YYYY-MM-DD`) or amount + unit (`within_last`/`older_than`, units minute/hour/day/week); bool ops → no input. Every row testid `${testId}-row-<i>-{field,op,value,remove}`; nested group rows `${testId}-group-<g>-row-<i>-…`. `useFilterOptions` caches per source with react-query (`["filter-options", source]`, staleTime 5 min). Add i18n keys (`filters.*`) en/de, propagate, check.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; `node scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Commit** `feat(frontend): shared filter builder for reports and saved searches`.

---

### Task 12: Builder state — pure reducer, validation, definition mapping (frontend)

**Files:**
- Create: `frontend/src/components/reports/builder/builderState.ts`, `builderState.test.ts`
- Modify: `en.json`, `de.json` (`reports.builder.issues.*`, `reports.builder.viz.*`) + propagate

**Interfaces:**
- Consumes: `ReportDefinition`, `ReportSource` types.
- Produces: D11 builder-state exports exactly.

- [ ] **Step 1: Write the failing tests**

```ts
const SRC = SOURCE_FIXTURE as ReportSource;     // tickets-like: queue, owner, created (time field), channel; measures tickets, closed_tickets
it("starts empty and invalid", () => {
  const s = initialState();
  expect(toDefinition(s)).toBeNull();
  expect(validate(s, undefined).map((i) => i.key)).toEqual(["noSource", "noMeasure"]);
});
it("builds a definition with rows, a column as series and measures", () => {
  let s = initialState();
  for (const a of [
    { type: "setSource", source: "tickets" }, { type: "addDimension", target: "rows", key: "created" },
    { type: "addDimension", target: "columns", key: "channel" }, { type: "addMeasure", key: "tickets" },
    { type: "setVisualization", patch: { type: "stacked_bar" } }] as BuilderAction[]) s = builderReducer(s, a);
  expect(toDefinition(s)).toEqual({
    schema_version: 1, source: "tickets",
    dimensions: [{ key: "created", grain: "day" }, { key: "channel" }],
    measures: [{ key: "tickets" }],
    visualization: { type: "stacked_bar", series_dimension: 1 },
    sort: [], totals: { grand: false, rows: false, columns: false } });
});
it("round-trips every built-in definition of the demo catalogue", () => {
  for (const r of DEMO_REPORT_CATALOGUE) {
    const back = toDefinition(fromDefinition(r.definition!));
    expect(back).toEqual(normalise(r.definition!));     // normalise = drop null/undefined/empty defaults like the backend dump
  }
});
it("changing the source drops fields the new source lacks", () => {
  let s = fromDefinition({ source: "tickets", dimensions: [{ key: "channel" }], measures: [{ key: "tickets" }] } as never);
  s = builderReducer(s, { type: "setSource", source: "accounted_time" });
  expect(s.rows).toEqual([]); expect(s.measures).toEqual([]); expect(s.filter).toBeNull(); expect(s.parameters).toEqual([]);
});
it("limits dimensions to 3 in total and measures to 10", () => {
  let s = builderReducer(initialState(), { type: "setSource", source: "tickets" });
  for (const k of ["queue", "owner", "channel", "created"]) s = builderReducer(s, { type: "addDimension", target: "rows", key: k });
  expect(s.rows.length + s.columns.length).toBe(3);
});
it("allows only visualizations the shape supports", () => {
  let s = fromDefinition({ source: "tickets", dimensions: [{ key: "queue" }], measures: [{ key: "tickets" }, { key: "closed_tickets" }] } as never);
  expect(allowedVisualizations(s, SRC)).toEqual(["table", "bar", "stacked_bar", "line", "area"]);
  s = builderReducer(s, { type: "removeMeasure", index: 1 });
  expect(allowedVisualizations(s, SRC)).toContain("donut");
  s = builderReducer(s, { type: "removeDimension", target: "rows", index: 0 });
  expect(allowedVisualizations(s, SRC)).toEqual(["table", "kpi"]);
});
it("an illegal visualization after an edit is reported, not silently changed", () => {
  let s = fromDefinition({ source: "tickets", dimensions: [{ key: "queue" }, { key: "channel" }], measures: [{ key: "tickets" }],
                          visualization: { type: "heatmap" } } as never);
  s = builderReducer(s, { type: "removeDimension", target: "rows", index: 1 });
  expect(s.visualization.type).toBe("heatmap");
  expect(validate(s, SRC).map((i) => i.key)).toContain("vizNeedsTwoDimensions");
});
it("moves dimensions and keeps the series index pointing at the column", () => {
  let s = fromDefinition({ source: "tickets", dimensions: [{ key: "queue" }, { key: "owner" }, { key: "channel" }],
    measures: [{ key: "tickets" }], visualization: { type: "stacked_bar", series_dimension: 2 } } as never);
  expect(s.columns.map((c) => c.key)).toEqual(["channel"]);
  s = builderReducer(s, { type: "moveDimension", target: "rows", index: 1, delta: -1 });
  expect(toDefinition(s)?.dimensions.map((d) => d.key)).toEqual(["owner", "queue", "channel"]);
  expect(toDefinition(s)?.visualization.series_dimension).toBe(2);
});
it("parameters must name filterable fields and unique keys", () => {
  let s = fromDefinition({ source: "tickets", measures: [{ key: "tickets" }] } as never);
  s = builderReducer(s, { type: "setParameters", parameters: [
    { key: "queues", field: "queue", op: "in" }, { key: "queues", field: "nope", op: "in" }] as never });
  expect(validate(s, SRC).map((i) => i.key)).toEqual(["duplicateParameter", "parameterFieldUnknown"]);
});
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** D11 rules: `fromDefinition` puts the dimension at `visualization.series_dimension` into `columns` (when set), all others into `rows` in order; `toDefinition` emits rows then the column, sets `series_dimension` to the column's index (or keeps `null`), drops grain on non-date dimensions, omits empty `choices`/`top_n`/`filter`/`parameters`/`time_*`/`limit`, and returns `null` while `validate` has errors other than viz advisories. Issue keys as in D11 i18n list; add en/de texts + propagate.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; i18n check.
- [ ] **Step 5: Commit** `feat(frontend): report builder state, validation and definition mapping`.

---

### Task 13: Report builder page (frontend)

**Files:**
- Create: `frontend/src/routes/agent/ReportBuilderPage.tsx`, `ReportBuilderPage.test.tsx`, `frontend/src/components/reports/builder/BuilderFieldsPanel.tsx`, `BuilderPreview.tsx`, `BuilderOptionsPanel.tsx`, `BuilderParametersEditor.tsx`, `SaveReportDialog.tsx`, `ShareFields.tsx`
- Modify: `frontend/src/router.tsx` (two routes, D11), `en.json`/`de.json` (`reports.builder.*` remaining keys, `reports.share.*`) + propagate, demo mocks if a builder call is unanswered

**Interfaces:**
- Consumes: Tasks 10–12; `useReportRun` (preview), `ReportView`, `FilterBuilder`, `useAuth().user.can_create_reports/can_share_reports`, `reportsApi.sources/get/create/update/importFile/shareGroups`.
- Produces: routes `/agent/reports/new` (search `{from?: string}`) and `/agent/reports/$reportKey/edit`; `ShareFields({ value: {visibility, group_ids}, onChange, canShare, testId })` (reused by Task 15).

Layout (desktop ≥ 1024 px three columns; below that stacked: Datenbasis/Felder → Vorschau → Darstellung): **left** `BuilderFieldsPanel` — "Datenbasis" select (source label keys), "Zeilen" list (add from dimension menu, per item: grain select for date dimensions, "Nur die größten … zeigen" top-N input, "Beim Aufruf umschaltbar" choices multi-select, up/down/remove buttons), "Spalten" (max 1), "Werte" list (measures, up/down/remove), "Filter" (`FilterBuilder` with `fieldsFromSource`), "Zeitraum" (time field select from `time_field` dimensions, the existing range presets of `ReportFilterBar` + "eigener Zeitraum", time zone select default "Zeitzone der Betrachtenden"), "Beim Aufruf änderbar" (`BuilderParametersEditor`: field, op, allowed values via `FilterValueInput`, default, required). **centre** `BuilderPreview` — debounced 700 ms `useReportRun({ definition: {...defn, limit: Math.min(defn.limit ?? 200, 200)} })`, shows `ReportView`, the run state, issues list when invalid ("Bitte Datenbasis wählen", …). **right** `BuilderOptionsPanel` — Darstellung (only `allowedVisualizations`), "Reihen aus" when applicable, plotted measures, totals checkboxes, sort (dimension/measure + direction), limit. Toolbar: name input, "Speichern" (create or update; disabled while invalid or without right), "Als Kopie speichern" (edit mode), "Aus Datei laden" (file input → `importFile` → `load`), "Abbrechen" (confirm when unsaved changes). Save dialog: name, description, `ShareFields` (visibility radio "Nur ich" / "Gruppen" / "Alle mit Berichtszugriff"; group multi-select from `shareGroups`; disabled with hint "Zum Teilen fehlt die Berechtigung (Gruppe „stats“, Schreibrecht)." when `!can_share_reports`). After save → navigate to `/agent/reports/<key>`; 409 → message `reports.saved.conflict` with "Neu laden". `?from=builtin.x` or `?from=<id>` loads that definition with name "Kopie von …" and `copied_from`. Edit of a built-in key redirects to `/new?from=<key>`; edit without edit right shows the read-only notice with "Kopie bearbeiten".

- [ ] **Step 1: Write the failing tests** (`ReportBuilderPage.test.tsx`, MSW/handler mocks as in `ReportViewPage.test.tsx`)

```tsx
it("builds a report and saves it privately", async () => {
  const create = vi.spyOn(reportsApi, "create").mockResolvedValue({ key: "41", kind: "saved", id: 41 } as never);
  renderAt("/agent/reports/new", { user: { can_create_reports: true, can_share_reports: false } });
  await user.click(await screen.findByTestId("builder-source"));
  await user.click(screen.getByRole("option", { name: "Tickets" }));
  await user.click(screen.getByTestId("builder-rows-add"));
  await user.click(screen.getByRole("menuitem", { name: "Queue" }));
  await user.click(screen.getByTestId("builder-values-add"));
  await user.click(screen.getByRole("menuitem", { name: "Tickets" }));
  await waitFor(() => expect(screen.getByTestId("builder-preview-view")).toBeInTheDocument());
  await user.type(screen.getByTestId("builder-name"), "Meine Queues");
  await user.click(screen.getByTestId("builder-save"));
  await user.click(screen.getByTestId("save-report-dialog-confirm"));
  expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: "Meine Queues", visibility: "private",
    definition: expect.objectContaining({ source: "tickets", dimensions: [{ key: "queue" }], measures: [{ key: "tickets" }] }) }));
  expect(await screen.findByTestId("report-view-page")).toBeInTheDocument();     // navigated to /agent/reports/41
});
it("disables sharing without the share right", async () => {
  renderAt("/agent/reports/new?from=builtin.volume", { user: { can_create_reports: true, can_share_reports: false } });
  await user.click(await screen.findByTestId("builder-save"));
  expect(screen.getByTestId("share-fields-groups")).toBeDisabled();
  expect(screen.getByTestId("share-fields-hint")).toHaveTextContent("Berechtigung");
});
it("copies a built-in with a 'Kopie von' name and copied_from", async () => {
  renderAt("/agent/reports/new?from=builtin.volume", { user: { can_create_reports: true, can_share_reports: true } });
  expect(await screen.findByTestId("builder-name")).toHaveValue("Kopie von Ticketaufkommen");
});
it("shows validation issues instead of a preview", async () => {
  renderAt("/agent/reports/new", { user: { can_create_reports: true, can_share_reports: true } });
  expect(await screen.findByTestId("builder-preview-issues")).toHaveTextContent("Datenbasis");
  expect(screen.getByTestId("builder-save")).toBeDisabled();
});
it("edit with a stale version shows the conflict message", async () => {
  vi.spyOn(reportsApi, "update").mockRejectedValue(new ApiError(409, { detail: { message: "Der Bericht wurde inzwischen geändert. Bitte neu laden.", current_version: 3 } }));
  renderAt("/agent/reports/41/edit", { user: { can_create_reports: true, can_share_reports: true } });
  await user.click(await screen.findByTestId("builder-save"));
  await user.click(screen.getByTestId("save-report-dialog-confirm"));
  expect(await screen.findByRole("alert")).toHaveTextContent("inzwischen geändert");
});
it("without the create right shows the notice, no builder", async () => {
  renderAt("/agent/reports/new", { user: { can_create_reports: false, can_share_reports: false } });
  expect(await screen.findByTestId("builder-no-right")).toHaveTextContent("Berechtigung");
});
it("debounces the preview", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const run = vi.spyOn(reportsApi, "run").mockResolvedValue({ result: RESULT, job: null } as never);
  renderAt("/agent/reports/new?from=builtin.volume_by_queue", { user: { can_create_reports: true, can_share_reports: true } });
  await screen.findByTestId("builder-values-add");
  await vi.advanceTimersByTimeAsync(800);                     // initial preview of the copied definition
  run.mockClear();
  const fast = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await fast.click(screen.getByTestId("builder-values-remove-1"));
  await fast.click(screen.getByTestId("builder-totals-grand"));
  await vi.advanceTimersByTimeAsync(699);
  expect(run).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(run).toHaveBeenCalledTimes(1);
  expect(run.mock.calls[0][0].definition).toMatchObject({ measures: [{ key: "tickets" }], totals: { grand: true } });
  vi.useRealTimers();
});
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the components; labels from `reports.dimensions.*` / `reports.measures.*` / `reports.sources.*` keys (add `reports.sources.<key>` for the five sources if missing: "Tickets", "Ticketereignisse", "Tagesstände", "Zeitbuchungen", "Offene Tickets (jetzt)"). i18n en/de + propagate.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; i18n check. Demo mode check: `VITE_DEMO=1 pnpm dev`, Playwright at 1280 px and 390 px, light + dark: build "Tickets je Kanal und Woche" as stacked bar, save, open; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** `feat(reports): report builder with live preview, sharing and file import`.

---

### Task 14: Saved reports in the overview and viewer; runtime parameters (frontend)

**Files:**
- Create: `frontend/src/components/reports/ReportParameterControls.tsx`, `ReportParameterControls.test.tsx`
- Modify: `frontend/src/routes/agent/ReportsPage.tsx` (+ test), `ReportViewPage.tsx` (+ test), `reportViewSearch.ts` (+ test if present), `frontend/src/components/reports/reportFilter.ts` (`toRunIn` carries generic parameters), `en.json`/`de.json` (`reports.saved.*`, `admin.settings.reporting.fields.access.help`) + propagate

**Interfaces:**
- Consumes: Tasks 10–13; `reportTitle`, `reportDescription`, `isSavedKey`.
- Produces: `ReportParameterControls({ definition, source, values, onChange, testId })` where `values: Record<string, (string|number)[] | boolean | null>`; viewer search param `params` (object; values normalised by `validateReportViewSearch`, tanstack parses JSON search params — numbers stay numbers, skill `tanstack-router-search-params-json-parsed`).

Changes: **ReportsPage** — section "Meine Berichte" (owner = me) and "Geteilte Berichte" (others) above the built-in catalogue, each card: title, description, owner + "geteilt mit …" badge, "Öffnen", menu (Bearbeiten [edit right], Kopieren [create right], Als JSON/YAML herunterladen, Löschen [edit right, confirm]); button "Neuer Bericht" (create right); admins get a toggle "Alle Berichte" (`catalogue({all: true})`); broken items show `definition_error` and only "Löschen". Built-in cards get "Kopieren" (create right). **ReportViewPage** — title via `reportTitle`; 410 → message "Dieser Bericht wurde gelöscht." with link back; 404 → not found message; saved: header actions Bearbeiten/Kopieren/Herunterladen; `ReportParameterControls` renders every parameter except the existing `queues` one (already in `ReportFilterBar`): id/enum → multi-select from `useFilterOptions`/enum values limited to `allowed_values` when set, `eq` → single select, bool ops → switch; values go into `toRunIn(...).parameters[key]`; required parameters without a value block the run with "Bitte <Label> wählen".

- [ ] **Step 1: Write the failing tests**

```tsx
// ReportsPage.test.tsx (additions)
it("lists my and shared saved reports above the catalogue", async () => {
  mockCatalogue([...BUILTINS, saved({ id: 41, name: "Meins", owner: { user_id: 7, name: "Ich" } }),
                 saved({ id: 42, name: "Vom Team", owner: { user_id: 9, name: "Kim" }, visibility: "groups", read_only: true })]);
  renderAs({ id: 7, can_create_reports: true });
  expect(await screen.findByTestId("reports-saved-mine")).toHaveTextContent("Meins");
  expect(screen.getByTestId("reports-saved-shared")).toHaveTextContent("Vom Team");
  expect(within(screen.getByTestId("reports-saved-card-42")).queryByTestId("reports-saved-card-42-edit")).toBeNull();
});
it("deletes after confirmation and refreshes", async () => {
  const remove = vi.spyOn(reportsApi, "remove").mockResolvedValue(undefined);
  const catalogue = mockCatalogue([...BUILTINS, saved({ id: 41, name: "Meins", owner: { user_id: 7, name: "Ich" } })]);
  renderAs({ id: 7, can_create_reports: true });
  await user.click(await screen.findByTestId("reports-saved-card-41-menu"));
  await user.click(screen.getByTestId("reports-saved-card-41-delete"));
  expect(remove).not.toHaveBeenCalled();                       // asks first
  await user.click(screen.getByTestId("confirm-dialog-confirm"));
  expect(remove).toHaveBeenCalledWith(41);
  await waitFor(() => expect(catalogue).toHaveBeenCalledTimes(2));
});
it("hides 'Neuer Bericht' and 'Kopieren' without the create right", async () => {
  renderAs({ id: 7, can_create_reports: false });
  expect(screen.queryByTestId("reports-new")).toBeNull();
});

// ReportViewPage.test.tsx (additions)
it("runs a saved report by its id with parameter values from the address", async () => {
  const run = vi.spyOn(reportsApi, "run");
  mockGet("41", saved({ id: 41, name: "Meins", definition: { ...DEF, parameters: [{ key: "channel", field: "channel", op: "in" }] } }));
  renderAt('/agent/reports/41?params={"channel":["email"]}');
  await waitFor(() => expect(run).toHaveBeenCalledWith(expect.objectContaining({ report_key: "41", parameters: { channel: ["email"] } }), expect.anything()));
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Meins");
});
it("shows the deleted message on 410", async () => {
  mockGetError("41", 410, "Der Bericht „Meins“ wurde gelöscht.");
  renderAt("/agent/reports/41");
  expect(await screen.findByTestId("report-view-gone")).toHaveTextContent("gelöscht");
});

// ReportParameterControls.test.tsx
it("limits choices to allowed_values and blocks missing required values", async () => {
  const onChange = vi.fn();
  render(<ReportParameterControls definition={{ ...DEF, parameters: [{ key: "ch", field: "channel", op: "in", allowed_values: ["email", "phone"], required: true }] } as never}
          source={SOURCE_FIXTURE as never} values={{}} onChange={onChange} testId="p" />);
  await user.click(screen.getByTestId("p-ch"));
  expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["E-Mail", "Telefon"]);
  expect(screen.getByTestId("p-ch-required")).toHaveTextContent("Bitte");
});
```


- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**; reword `admin.settings.reporting.fields.access.help` (de: "„Alle Agenten“: alle sehen Berichte und können eigene speichern; Teilen braucht Schreibrecht in der Gruppe „stats“. „Gruppe stats“: Lesen braucht Leserecht, Speichern und Teilen Schreibrecht in „stats“."). i18n + propagate.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; i18n check; demo check (overview with saved cards, viewer of a saved report with a parameter) at 1280/390, light+dark; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** `feat(reports): saved reports in overview and viewer with runtime parameters`.

---

### Task 15: Saved searches UI — sidebar, list page, editor, "Ansicht speichern" (frontend)

**Files:**
- Create: `frontend/src/components/searches/SavedSearchNav.tsx` (+ test), `SaveViewDialog.tsx` (+ test), `frontend/src/routes/agent/SavedSearchPage.tsx` (+ test), `SavedSearchEditorPage.tsx` (+ test)
- Modify: `frontend/src/components/layout/AgentShell.tsx` (NavGroup `searches` between "workspace" and the queue section), `AgentShell.test.tsx`, `frontend/src/routes/agent/QueuesPage.tsx` (+ test: "Ansicht speichern" button in the toolbar), `frontend/src/router.tsx` (three routes), `en.json`/`de.json` (`savedSearches.*`) + propagate

**Interfaces:**
- Consumes: `savedSearchesApi`, `FilterBuilder` + `fieldsFromTicketCatalog`, `ShareFields` (Task 13), `TicketTable`.
- Produces: routes D11; `SavedSearchNav({ onNavigate })`; `SaveViewDialog({ open, onClose, listParams })`.

Behaviour: **Sidebar** — group "Gespeicherte Suchen" (collapsible like the others, `sidebar-group-searches-toggle`), "+" action → `/agent/searches/new`; entries = searches with `show_in_sidebar`, ordered by position; count badge from `counts()` (refetch every 60 s and on window focus; `null` → no badge, tooltip "Anzahl gerade nicht verfügbar"); testids `sidebar-search-<id>`, `sidebar-search-<id>-count`; link "Alle gespeicherten Suchen…" opens a popover list with pin toggles (`setPrefs`). **SavedSearchPage** — header with name, "geteilt von <owner>" for shared ones, "Bearbeiten" (can_edit), "In Seitenleiste" toggle, "Löschen" (can_edit, confirm); `TicketTable` fed by `queryTickets({filter: criteria, sort, order, offset, limit: 50, include_archived})` with sort/paging in the URL; broken criteria → message + "Bearbeiten"; 404 → "Diese Suche gibt es nicht (mehr) oder sie ist nicht mit dir geteilt.". **Editor** — name, `FilterBuilder` over `ticketFilterFields()`, live count ("<n> Tickets passen", debounced 500 ms via `countTickets`) and the first 10 matches (`queryTickets` limit 10), sort select (TicketSortKey labels), "Archivierte Tickets einbeziehen" (admins only), `ShareFields` in group-only mode (no "Alle"), sidebar + count switches; save → `/agent/searches/<id>`; 409 → conflict message. **QueuesPage** — "Ansicht speichern" opens `SaveViewDialog` (name, sidebar, count) posting `{ name, list_params: <the page's current FilterParams + include_archived>, sort_by, sort_order }`.

- [ ] **Step 1: Write the failing tests**

```tsx
// SavedSearchNav.test.tsx
it("shows pinned searches with counts and hides unavailable counts", async () => {
  mockList([s({ id: 1, name: "Meine VIP", show_in_sidebar: true, show_count: true, position: 0 }),
            s({ id: 2, name: "Team", show_in_sidebar: true, show_count: true, position: 1 }),
            s({ id: 3, name: "Versteckt", show_in_sidebar: false })]);
  mockCounts({ "1": 4, "2": null });
  render(<SavedSearchNav onNavigate={() => {}} />, { wrapper });
  expect(await screen.findByTestId("sidebar-search-1")).toHaveTextContent("Meine VIP");
  expect(screen.getByTestId("sidebar-search-1-count")).toHaveTextContent("4");
  expect(screen.queryByTestId("sidebar-search-2-count")).toBeNull();
  expect(screen.queryByTestId("sidebar-search-3")).toBeNull();
});

// SavedSearchPage.test.tsx
it("lists tickets of the saved criteria with paging and sort in the address", async () => {
  const query = vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
  mockGet(5, s({ id: 5, criteria: CRIT, sort_by: "changed", sort_order: "asc" }));
  renderAt("/agent/searches/5?offset=50");
  await waitFor(() => expect(query).toHaveBeenCalledWith(expect.objectContaining({ filter: CRIT, sort: "changed", order: "asc", offset: 50, limit: 50 }), expect.anything()));
  expect(await screen.findByTestId("ticket-table")).toBeInTheDocument();
});
it("shows the not-found message on 404", async () => {
  vi.spyOn(savedSearchesApi, "get").mockRejectedValue(new ApiError(404, { detail: { message: "x" } }));
  renderAt("/agent/searches/5");
  expect(await screen.findByTestId("saved-search-not-found")).toHaveTextContent("nicht mit dir geteilt");
});
it("hides edit and delete for shared searches", async () => {
  mockGet(6, s({ id: 6, criteria: CRIT, is_owner: false, can_edit: false, owner: { user_id: 9, name: "Kim" } }));
  vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
  renderAt("/agent/searches/6");
  expect(await screen.findByTestId("saved-search-shared-by")).toHaveTextContent("Kim");
  expect(screen.queryByTestId("saved-search-edit")).toBeNull();
  expect(screen.queryByTestId("saved-search-delete")).toBeNull();
});

// SavedSearchEditorPage.test.tsx
it("creates a search with a live count", async () => {
  vi.spyOn(savedSearchesApi, "countTickets").mockResolvedValue({ count: 12 });
  const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 9 }) as never);
  vi.spyOn(savedSearchesApi, "queryTickets").mockResolvedValue(PAGE as never);
  renderAt("/agent/searches/new");
  await user.click(await screen.findByTestId("filter-builder-add-condition"));
  await user.click(screen.getByTestId("filter-builder-row-0-field"));
  await user.click(screen.getByRole("option", { name: "Queue" }));
  await user.click(screen.getByTestId("filter-builder-row-0-op"));
  await user.click(screen.getByRole("option", { name: "ist eines von" }));
  await user.click(screen.getByTestId("filter-builder-row-0-value"));
  await user.click(screen.getByRole("option", { name: "Postmaster" }));      // reference queue id 1 in the fixture
  await user.type(screen.getByTestId("saved-search-editor-name"), "Neu");
  expect(await screen.findByTestId("saved-search-editor-count")).toHaveTextContent("12");
  await user.click(screen.getByTestId("saved-search-editor-save"));
  expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: "Neu", criteria: { version: 1, where: { all: [{ field: "queue_id", op: "in", value: [1] }] } } }));
});

// QueuesPage.test.tsx (addition)
it("saves the current view with its list params", async () => {
  const create = vi.spyOn(savedSearchesApi, "create").mockResolvedValue(s({ id: 3 }) as never);
  renderQueues("/agent/queues?queue_id=4&state_type=pending&unassigned=true");
  await user.click(await screen.findByTestId("queues-save-view"));
  await user.type(screen.getByTestId("save-view-name"), "Wartend ohne Bearbeiter");
  await user.click(screen.getByTestId("save-view-confirm"));
  expect(create).toHaveBeenCalledWith(expect.objectContaining({
    name: "Wartend ohne Bearbeiter", list_params: expect.objectContaining({ queue_id: 4, state_type: "pending", unassigned: true }) }));
});

// AgentShell.test.tsx (addition)
it("renders the saved-search group between workspace and queues", async () => {
  renderShell();
  const order = (await screen.findAllByTestId(/^sidebar-group-.+-toggle$/)).map((el) => el.getAttribute("data-testid"));
  expect(order.slice(0, 4)).toEqual([
    "sidebar-group-workspace-toggle", "sidebar-group-searches-toggle",
    "sidebar-group-queues-toggle", "sidebar-group-knowledge-toggle"]);
});
```

(If the queue section's toggle testid differs from `sidebar-group-queues-toggle`, use the existing one — check `QueueNavSection` in `AgentShell.tsx`.)

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**; i18n en/de + propagate.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; i18n check; demo check (sidebar with counts, saved search page, editor, save view from the queue list) at 1280/390, light+dark; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** `feat(searches): saved searches in the sidebar, list page, editor and save view`.

---

### Task 16: Znuny import dialog (frontend)

**Files:**
- Create: `frontend/src/components/searches/ZnunyImportDialog.tsx`, `ZnunyImportDialog.test.tsx`
- Modify: `SavedSearchNav.tsx` (popover entry "Suchvorlagen aus Znuny übernehmen"), `SavedSearchEditorPage.tsx` is untouched; `en.json`/`de.json` (`savedSearches.import.*`) + propagate

**Interfaces:**
- Consumes: `savedSearchesApi.znunyPreview/znunyImport`.
- Produces: `ZnunyImportDialog({ open, onClose })`.

Behaviour: on open loads the preview; empty → "In Znuny sind für dich keine Suchvorlagen gespeichert."; each profile row: checkbox (preselected when importable), name, "wird aktualisiert" badge when `existing_id`, a summary of the mapped conditions (count), and an expandable list "Nicht übernommen" with key, value and the server's German reason; non-importable rows disabled with "Keine Bedingung übernehmbar"; "Übernehmen (<n>)" → `znunyImport(names)` → result summary (angelegt / aktualisiert / übersprungen with reasons) and invalidates `["saved-searches"]` queries.

- [ ] **Step 1: Write the failing tests**

```tsx
it("preselects importable profiles and lists what is not taken over", async () => {
  mockPreview({ profiles: [
    p({ name: "Offen Support", importable: true, unmapped: [] }),
    p({ name: "Gemischt", importable: true, unmapped: [{ key: "Subject", value: "y", reason: "Volltextsuche wird nicht …" }], existing_id: 4 }),
    p({ name: "Volltext", importable: false, unmapped: [{ key: "Fulltext", value: "drucker", reason: "Volltextsuche wird nicht …" }] })] });
  render(<ZnunyImportDialog open onClose={() => {}} />, { wrapper });
  expect(await screen.findByTestId("znuny-import-row-Offen Support-check")).toBeChecked();
  expect(screen.getByTestId("znuny-import-row-Volltext-check")).toBeDisabled();
  expect(screen.getByTestId("znuny-import-row-Gemischt-update")).toHaveTextContent("wird aktualisiert");
  await user.click(screen.getByTestId("znuny-import-row-Gemischt-unmapped-toggle"));
  expect(screen.getByTestId("znuny-import-row-Gemischt-unmapped")).toHaveTextContent("Subject");
  expect(screen.getByTestId("znuny-import-apply")).toHaveTextContent("2");
});
it("applies the selection and shows the result", async () => {
  mockPreview(PREVIEW_THREE);                                       // the three profiles of the first test
  const apply = vi.spyOn(savedSearchesApi, "znunyImport").mockResolvedValue({ created: [7], updated: [], skipped: [] });
  render(<ZnunyImportDialog open onClose={() => {}} />, { wrapper });
  await user.click(await screen.findByTestId("znuny-import-row-Gemischt-check"));   // untick
  await user.click(screen.getByTestId("znuny-import-apply"));
  expect(apply).toHaveBeenCalledWith(["Offen Support"]);
  expect(await screen.findByTestId("znuny-import-result")).toHaveTextContent("1 angelegt");
});
it("says so when Znuny has no profiles", async () => {
  mockPreview({ profiles: [] });
  render(<ZnunyImportDialog open onClose={() => {}} />, { wrapper });
  expect(await screen.findByTestId("znuny-import-empty")).toHaveTextContent("keine Suchvorlagen");
  expect(screen.queryByTestId("znuny-import-apply")).toBeNull();
});
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement**; i18n en/de + propagate.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; i18n check; demo check of the dialog at 1280/390, light+dark; delete `.playwright-mcp`.
- [ ] **Step 5: Commit** `feat(searches): take over Znuny search profiles from the sidebar`.

---

### Task 17: End-to-end flows and e2e mocks (frontend)

**Files:**
- Modify: `frontend/e2e/fixtures/mock-api.ts` (saved reports CRUD + run by id, sources with `ops/enum_values/common_fields`, share groups, saved searches + counts + query + Znuny preview/import; stateful in-memory per test like the existing job mocks)
- Create: `frontend/e2e/report-builder.spec.ts`, `frontend/e2e/saved-searches.spec.ts`
- Modify: `frontend/e2e/stats.spec.ts` only if the reports overview testids it uses moved

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the specs**

```ts
// report-builder.spec.ts
test("build, save and reopen a report", async ({ page }) => {
  await page.goto("/agent/reports");
  await page.getByTestId("reports-new").click();
  await page.getByTestId("builder-source").click();
  await page.getByRole("option", { name: "Tickets" }).click();
  await page.getByTestId("builder-rows-add").click();
  await page.getByRole("menuitem", { name: "Kanal" }).click();
  await page.getByTestId("builder-values-add").click();
  await page.getByRole("menuitem", { name: "Tickets" }).click();
  await expect(page.getByTestId("builder-preview-view")).toBeVisible();
  await page.getByTestId("builder-name").fill("Tickets je Kanal");
  await page.getByTestId("builder-save").click();
  await page.getByTestId("save-report-dialog-confirm").click();
  await expect(page).toHaveURL(/\/agent\/reports\/\d+$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Tickets je Kanal");
  await page.goto("/agent/reports");
  await expect(page.getByTestId("reports-saved-mine")).toContainText("Tickets je Kanal");
});
test("copy a built-in into the builder", async ({ page }) => {
  await page.goto("/agent/reports");
  await page.getByTestId("reports-card-builtin.volume-copy").click();
  await expect(page.getByTestId("builder-name")).toHaveValue(/^Kopie von /);
});

// saved-searches.spec.ts
test("save the current queue view and open it from the sidebar", async ({ page }) => {
  await page.goto("/agent/queues?state_type=pending");
  await page.getByTestId("queues-save-view").click();
  await page.getByTestId("save-view-name").fill("Wartend");
  await page.getByTestId("save-view-confirm").click();
  const entry = page.getByTestId(/^sidebar-search-\d+$/).filter({ hasText: "Wartend" });
  await expect(entry).toBeVisible();
  await entry.click();
  await expect(page.getByTestId("ticket-table")).toBeVisible();
});
test("import Znuny search profiles", async ({ page }) => {
  await page.goto("/agent/queues");
  await page.getByTestId("sidebar-searches-more").click();
  await page.getByTestId("sidebar-searches-import-znuny").click();
  await expect(page.getByTestId("znuny-import-row-Volltext-check")).toBeDisabled();
  await page.getByTestId("znuny-import-apply").click();
  await expect(page.getByTestId("znuny-import-result")).toContainText("angelegt");
});
```

- [ ] **Step 2: Run** `cd frontend && pnpm exec playwright test e2e/report-builder.spec.ts e2e/saved-searches.spec.ts e2e/stats.spec.ts` (e2e normally runs on tags only — run it here locally) → fix mocks until green.
- [ ] **Step 3:** `pnpm lint && pnpm test --run`.
- [ ] **Step 4: Commit** `test(e2e): report builder and saved searches flows`.

---

### Task 18: Docs and full verification

**Files:**
- Modify: `docs/compatibility.md` (row "Saved searches": missing → "yes — saved ticket searches with sharing by group, sidebar entries with counts, import of Znuny search profiles (read-only; full-text and future-relative times are not taken over, see list)"; row "Stats framework": builder + saved reports + `stats` rights + JSON/YAML files, Znuny stats import/scheduling still phase 3; section "Reporting differences to Znuny stats": rights rule (Deviation 1), owner-only editing, soft delete, full mapping table of D9 as a sub-section "Search profiles"), `docs/architecture.md` (section "Stats / reporting": saved reports, rights, `run_service` + dashboard seam, saved searches package, `POST /tickets/query`; 10–20 lines with module paths), `docs/parallel-operation.md` (`search_profile` read only; Tiqora never writes it; profiles changed in Znuny afterwards are re-imported on demand), `docs/gdpr.md` (saved criteria hold customer logins; erasure rewrites them, rollback does not restore them), `docs/deployment.md` (no new service; nothing to configure; note the `reporting.access` rule for saving/sharing)
- No code changes except fixes the sweep finds.

- [ ] **Step 1: Write the docs** (English, like the existing docs; German UI terms with their English meaning). Do NOT touch `site/compare.html` or README.
- [ ] **Step 2: Full backend suite:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → all green; `rtk git diff --stat backend/tests/db_leak_baseline.txt` empty; lint gates.
- [ ] **Step 3: Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 4: OpenAPI freshness:** regenerate both openapi files + `pnpm build`; `rtk git status` shows no diff afterwards (else commit the missed regeneration here).
- [ ] **Step 5: Alembic graph:** `cd backend && uv run alembic heads` shows exactly the expected heads; `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py`.
- [ ] **Step 6: Commit** `docs(reporting): builder, saved reports, saved searches and Znuny profile import`.

---

## Self-review notes (for the executor)

- Spec coverage (phase 2 row): report builder UI → Tasks 11–13 (left/centre/right panes, "Zeilen/Spalten/Werte/Filter/Zeitraum", live preview); saved reports in `tiqora_report` (`id, name, description, owner_user_id, visibility, group_ids (junction), definition, version, valid, created/changed`) → Tasks 1–4; `stats` group permissions (view ro / save rw, admins all) → Task 2 with Deviation 1; runtime-changeable filters with allowed values (parity checklist) → builder parameters (Task 13) + viewer controls (Task 14); import/export as file → Task 3 (+ builder load, Task 13); saved searches (`owner, name, criteria, shared groups, sidebar, count, sort`) → Tasks 1, 5, 6, 15; Znuny search-profile import read-only with unmapped list → Tasks 7, 8, 16; "every saved report can be placed on a dashboard" → D12 seam (Task 4); Playwright builder flow → Task 17.
- Shared files other wave-2 plans may also touch: the Alembic primary head (+ `test_migration_gate.py`, `test_migrate_cli.py`), `db/tiqora/models.py`, `db/legacy/misc.py`, `api/v1/__init__.py`, `domain/schemas.py` (`UserMe`), `api/v1/auth.py`, `api/v1/reference.py` (share-groups — dashboards should reuse, not duplicate), `domain/ticket_service.py`, `api/v1/tickets.py`, `gdpr/erasure.py`, `reporting/semantic.py` + sources, `reporting/jobs.py`, `api/v1/reports.py`, `router.tsx`, `AgentShell.tsx` (new NavGroup), `QueuesPage.tsx`, `demo/mockData.ts`, `demo/reportsMock.ts`, `e2e/fixtures/mock-api.ts`, `en.json`/`de.json` (+ 47 propagated locales). Conflicts there are additive; re-run propagate and the openapi regeneration instead of hand-merging.
