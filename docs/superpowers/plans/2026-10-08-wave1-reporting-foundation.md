# Wave 1 — Reporting Foundation (Reporting phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Historically correct, permission-scoped reporting on top of incrementally built fact tables: fact/event/snapshot tables filled from `ticket_history` (Znuny and Tiqora writes) with backfill, working-minute SLA numbers from the escalation calendars, a semantic layer + query compiler with dialect-aware date buckets, a read-only built-in report catalogue (25 reports incl. time accounting by customer/queue/agent/type), async runs with polling and a Redis result cache, XLSX/CSV export as background jobs, the old `/api/v1/stats/*` endpoints as thin wrappers, and a new `/agent/reports` UI built from the hand-written SVG charts.

**Architecture:** New package `tiqora.reporting` (facts, events, snapshots, backfill, semantic layer, compiler, engine, catalogue, export). The worker gets one interval loop `report_facts` (history id watermark + gap list + dirty-ticket queue, per-ticket full recompute, hourly daily-snapshot fill, orphan sweep). Long work (backfill, big report runs, exports) runs through the wave-0 job system (`tiqora.jobs`). Report definitions are versioned JSON (`ReportDefinition`, `schema_version: 1`) so phase 2 (builder, saved reports, saved searches), phase 3 (Znuny stats import, schedules, PDF) and phase 4 (NL questions, AI analytics) store and produce the same JSON. Frontend: `/agent/reports` (overview + catalogue) and `/agent/reports/$reportKey` (viewer) replace `/agent/stats`.

**Tech Stack:** FastAPI, SQLAlchemy 2 async + Core (`@compiles` constructs), Alembic two-chain, Pydantic v2, openpyxl (write-only), redis.asyncio, asyncio worker loops, React 19 + TanStack Router/Query, Tailwind tokens, hand-written SVG charts, Vitest + Testing Library, MSW demo mode.

**Spec:** `docs/superpowers/specs/2026-10-08-reporting-analytics-design.md` (phase 1 row of "Phases"; §1, §2, §3 built-ins only, §4, §5 CSV/XLSX, §6 chart types, "Built-in report catalogue" minus AI impact and mail health), master `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (principles, definition of done). Builds on `docs/superpowers/plans/2026-10-08-wave0-foundations.md` (jobs, filter language, extension registry, settings sections, `JobDialog`, `SchemaForm`) — read its Design section D1–D8 first; nothing here re-plans it.

## Global Constraints

- Commit directly to `main`; never push (a push deploys production). Never `--no-verify` (PII hooks). Only made-up example data (example.org etc.). Use `rtk git …` per repo CLAUDE.md; check `rtk git log -1 --oneline` after committing because the pre-commit hook can block a chained commit. Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` (needs Docker; never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows (pattern: `_seed_statements` + module-scoped autouse fixture, see `backend/tests/test_ai_refine.py`); never add entries to `backend/tests/db_leak_baseline.txt`.
- DB tests that touch SQL run on BOTH dialects: `@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])` + `request.getfixturevalue(url_fixture)` (pattern: `backend/tests/test_admin_api.py`, incl. its `_to_async_url`), and `TiqoraBase.metadata.create_all` for the new tables. Seed-id band for this plan: **47000–47999** (sub-bands per task are named in the task); confirm free with `grep -rn "47[0-9][0-9][0-9]" backend/tests` before use (skill `pytest-session-container-id-band-collisions`). Logins/names contain `rep47`.
- Migrations: Alembic two-chain setup; the new migration goes into `backend/alembic/versions_tiqora` with `down_revision` = the CURRENT primary head at implementation time (`ls backend/alembic/versions_tiqora | tail -1` and `cd backend && uv run alembic heads` — wave 0 adds one, other wave-1 plans may add more; never trust an id written here); follow skill `alembic-two-chain-new-head-forks-graph`; update `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`) and `backend/tests/test_migrate_cli.py` (`version == …`). Tables only `tiqora_*`; never change Znuny tables' DDL; writes to Znuny tables must be behaviourally identical to Znuny (source: `znuny-6.5.22/`).
- ORM models in `backend/src/tiqora/db/tiqora/models.py` must produce the same DDL as the migration (`backend/tests/test_tiqora_model_ddl.py`).
- MySQL/MariaDB AND PostgreSQL must both work. Prod MariaDB mixes collations: never JOIN `tiqora_*` tables with Znuny tables on string columns — load keys, then IN-list. (Integer joins such as `tiqora_report_ticket_event.ticket_id = tiqora_report_ticket_fact.ticket_id` or `time_accounting.ticket_id = ticket.id` are fine.)
- Timestamps the application writes into `tiqora_report_*` bookkeeping columns (`built_at`, `updated_at`, `last_tick_at`, `backfill_*_at`) are naive UTC from `tiqora.jobs.service.utc_now()`; never compare against `func.now()`/`current_timestamp` (skill `db-session-timezone-vs-app-written-rows`). Fact datetimes copied from Znuny columns stay as stored (Znuny storage zone `OTRSTimeZone`, UTC on supported setups — same assumption as the rest of Tiqora). API datetimes use `tiqora.domain.schemas.UtcDateTime`.
- Long-running endpoints (> a few seconds possible) use the wave-0 job system (prod nginx 90 s timeout): backfill, report runs above the cost threshold, every export.
- After any API model/route change: regenerate `packages/api-client/openapi.json` (skill `tiqora-openapi-regen-or-ci-wipes-schema`); never hand-edit `schema.d.ts`. Commands: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build`, then `head -c 40 packages/api-client/openapi.json` (must start `{"components"`) and `rtk git diff --stat packages/api-client/openapi.json` (tens/hundreds of lines, not five figures). A Pydantic response field WITH a default becomes required in the generated TS type — fixtures and demo mocks must include it.
- Frontend: `cd frontend && pnpm lint && pnpm test --run`; never run prettier/biome on `frontend/`. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales); `node frontend/scripts/check-i18n-keys.mjs` must pass. New screens get demo-mode mocks (`frontend/src/demo/mockData.ts` `resolveData`, binary responses in `frontend/src/demo/handlers.ts`; `VITE_DEMO=1`). Colour tokens only via `frontend/src/themes/tokens.css` + `themeColor()` in `frontend/tailwind.config.js`. API wrappers are hand-written `frontend/src/lib/*Api.ts` files using `api.request<T>(method, path, {query, body, signal})` with `Schemas["…"]` types (pattern: `frontend/src/lib/phoneApi.ts`); messages from `ApiError` via `apiErrorMessage` (`frontend/src/lib/avatarApi.ts`).
- Charts follow the dataviz skill (skill `dataviz`): form first, one axis per chart, fixed categorical slot order (never cycled; > 6 series fold into "Weitere"), a legend for ≥ 2 series, hover tooltip on every plotted mark, a table view for every chart (the light palette has slots below 3:1 contrast → table view is the required relief), text in ink/muted tokens never in series colours, status colours never used as series colours, dark mode with its own validated steps.
- UI copy German first, plain words, named by what agents/admins recognise ("Berichte", "Zeitraum", "Bearbeitungszeit", "Erstreaktion", "Berichtsdaten neu aufbauen"); no system jargon in UI text ("Fakten", "Watermark", "Job", "Semantic layer" only in code/docs).

## Review Focus

1. **A `ticket_history` row commits late with an id below rows already processed (autoincrement gap from a concurrent transaction)** → the ticket's facts are still rebuilt on a later tick, not silently skipped forever. Pinned in Task 9 (`test_gap_row_committed_later_is_processed`).
2. **Tickets with unusual histories** — no history rows at all (seeded/imported), created directly in a closed state, merged, reopened twice, moved A→B→A, pending in between, or calendars without working hours → a fact row is built, nothing crashes, the numbers follow the fixed rules. Pinned in Tasks 3–5 (`test_no_history_rows_builds_from_ticket_row`, `test_reopen_twice_counts_and_last_close`, `test_queue_pingpong`, `test_no_working_hours_gives_empty_working_minutes`).
3. **A report window that crosses a DST change in Europe/Berlin, bucketed by day/week/hour** → each event lands in the local calendar bucket it happened in on both MariaDB and PostgreSQL (no ±1 h shift). Pinned in Task 10 (`test_local_day_buckets_across_dst_both_dialects`).
4. **A viewer without access to a queue — through a runtime parameter naming that queue, an inline definition with an `any`/`not` filter, or a cached result computed for a colleague** → never sees its tickets or counts. Pinned in Tasks 12–13 (`test_parameter_cannot_widen_scope`, `test_inline_any_not_cannot_widen_scope`, `test_cache_key_differs_per_scope`).
5. **Backfill interrupted (worker restart), started twice, or running while the incremental loop rebuilds the same tickets** → it resumes from its cursor, produces no duplicate events, and ends with the same facts as a clean run. Pinned in Tasks 8–9 (`test_backfill_resume_and_idempotent` in Task 8, `test_backfill_and_tick_concurrently` in Task 9).

Also pinned (not top five): fact tables still empty on day one → reports return `facts.ready = false` and the UI shows a banner while the automatically started backfill runs (Tasks 9, 16, 20); a ticket deleted by Znuny or by GDPR erasure disappears from the facts (Task 9).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Names from the wave-0 plan (`Registry`, `ExtensionPoint`, `get_registry`, `register_core`, `JobHandler`, `JOB_HANDLERS`, `JobContext`, `JobFailed`, `enqueue_job`, `utc_now`, `JobAccepted`, `Filter`, `Condition`, `AllOf`, `AnyOf`, `Not`, `validate_filter`, `iter_conditions`, `FilterFieldSpec`, `FilterError`, `OPS_BY_TYPE`, `relative_delta`, `parse_datetime_value`, `TicketQueryScope`, `ticket_query_scope`, `SettingsSection`, `SETTINGS_SECTIONS`, `load_section_values`, `useJob`, `JobDialog`, `JobProgress`, `MultiSelectInput`, `jobsApi`) are used exactly as wave 0 defines them.

### R0. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/src/tiqora/reporting/__init__.py` | package docstring only (no import side effects) |
| `backend/src/tiqora/reporting/settings.py` | `ReportingSettings`, `REPORTING_SECTION` |
| `backend/src/tiqora/reporting/working_time.py` | `WorkingCalendar`, `EscalationTargets`, `CalendarResolver`, `to_epoch` (re-export of `escalation.to_epoch`), `from_epoch`, `working_minutes`, `due_at` |
| `backend/src/tiqora/reporting/history_events.py` | pure: `TicketRow`, `HistoryRow`, `ArticleRow`, `EventRow`, `derive_events` |
| `backend/src/tiqora/reporting/facts.py` | pure: `AiFlags`, `FactInputs`, `FactRow`, `compute_fact`, `CHANNEL_KEYS`, `FACT_VERSION` |
| `backend/src/tiqora/reporting/facts_store.py` | DB: `load_fact_inputs`, `rebuild_ticket_facts`, `rebuild_and_commit`, `delete_ticket_facts`, `fact_write_lock` |
| `backend/src/tiqora/reporting/dirty.py` | `mark_tickets_dirty`, `take_dirty_ticket_ids` |
| `backend/src/tiqora/reporting/state.py` | `STATE_NAME`, `load_state`, `facts_status` |
| `backend/src/tiqora/reporting/incremental.py` | `run_report_facts_tick` + constants |
| `backend/src/tiqora/reporting/snapshots.py` | `Segment`, `ticket_timeline`, `day_end_instants`, `compute_snapshot_counts`, `write_snapshots`, `snapshot_days` |
| `backend/src/tiqora/reporting/backfill.py` | `ReportBackfillPayload`, `run_backfill`, `REPORT_BACKFILL_JOB` |
| `backend/src/tiqora/filters/predicates.py` | public predicate helpers extracted from `filters/tickets.py` (`like_escape`, `column_predicate`, `positive_predicate`, `fit_int_condition`, `int_bounds`, ...); `filters/tickets.py` imports them |
| `backend/src/tiqora/reporting/filter_sql.py` | `compile_conditions` on the shared wave-0 predicate helpers extracted from `filters/tickets.py` |
| `backend/src/tiqora/reporting/sql.py` | `shift_seconds`, `date_bucket`, `date_part`, `TzSegment`, `tz_segments`, `to_local`, `statement_timeout`, `ReportQueryTimeout` |
| `backend/src/tiqora/reporting/definition.py` | `ReportDefinition` and parts, `TimeWindow`, `resolve_time_window`, `DefinitionError`, `validate_definition`, `definition_hash` |
| `backend/src/tiqora/reporting/semantic.py` | `Dimension`, `Measure`, `FilterOnlyField`, `ReportSource`, `CompileContext`, `REPORT_SOURCES` |
| `backend/src/tiqora/reporting/sources/__init__.py` | `core_report_sources()` |
| `backend/src/tiqora/reporting/sources/tickets.py`, `ticket_events.py`, `daily_snapshot.py`, `accounted_time.py`, `open_tickets.py` | one `ReportSource` each |
| `backend/src/tiqora/reporting/compiler.py` | `RunParams`, `CompiledReport`, `MeasurePlan`, `compile_report` |
| `backend/src/tiqora/reporting/labels.py` | `resolve_labels` |
| `backend/src/tiqora/reporting/cache.py` | `CacheBackend`, `ReportCache`, `cache_backend_from_settings` |
| `backend/src/tiqora/reporting/engine.py` | `run_report`, `raw_measure_values`, `estimate_rows` |
| `backend/src/tiqora/reporting/access.py` | `require_report_access` |
| `backend/src/tiqora/reporting/catalogue.py` | `BuiltinReport`, `BUILTIN_REPORTS`, `core_builtin_reports()` |
| `backend/src/tiqora/reporting/export.py` | `XlsxSheet`, `write_xlsx_workbook`, `report_sheet`, `write_report_csv` |
| `backend/src/tiqora/reporting/jobs.py` | `ReportRunPayload`, `ReportExportPayload`, `REPORT_RUN_JOB`, `REPORT_EXPORT_JOB` |
| `backend/src/tiqora/reporting/schemas.py` | API models (R9) |
| `backend/src/tiqora/api/v1/reports.py` | `/api/v1/reports*`, `/api/v1/reporting/*` |
| `backend/src/tiqora/api/v1/admin/reporting.py` | `/api/v1/admin/reporting/*` |
| `backend/src/tiqora/cli/report.py` | `tiqora report backfill|status` |
| `backend/src/tiqora/worker/report_facts.py` | `run_report_facts_worker_tick` (enabled switch + settings) |
| modify `backend/src/tiqora/znuny/escalation.py` | public `escalation_preferences`, `calendar_config`, `to_epoch`; new `working_seconds_between` |
| modify `backend/src/tiqora/stats/service.py` | `StatsService` methods delegate to built-ins |
| modify `backend/src/tiqora/gdpr/erasure.py` | delete facts + mark tickets dirty on erasure/rollback |
| modify shared: `db/tiqora/models.py`, `extensions/core.py`, `config.py`, `domain/settings_store.py`, `worker/__main__.py`, `worker/services.py`, `api/v1/__init__.py`, `api/v1/admin/__init__.py`, `main.py` | registrations only (listed per task) |

Frontend (new unless marked):

| Path | Responsibility |
|---|---|
| `frontend/src/lib/reportsApi.ts` | wrappers + types |
| `frontend/src/components/reports/useReportRun.ts` | run hook (inline result or job polling) |
| `frontend/src/components/reports/reportFormat.ts` | cell/label formatting, series extraction |
| `frontend/src/components/reports/ReportView.tsx` | visualization dispatcher + table toggle |
| `frontend/src/components/reports/ReportTable.tsx`, `PivotTable.tsx` | flat table with totals; pivot with sticky headers |
| `frontend/src/components/reports/ReportFilterBar.tsx` | time range presets/custom, grain, dimension choice, parameters |
| `frontend/src/components/reports/FactsBanner.tsx` | "Berichtsdaten werden aufgebaut" banner |
| `frontend/src/components/reports/ReportExportButton.tsx` | XLSX/CSV export via `JobDialog` |
| `frontend/src/components/agent/stats/chartColors.ts` | series slot tokens + stable assignment |
| `frontend/src/components/agent/stats/ChartTooltip.tsx`, `ChartLegend.tsx` | shared hover tooltip + legend |
| modify `frontend/src/components/agent/stats/BarChart.tsx`, `LineChart.tsx`, `StatTile.tsx` | dataviz rules, tooltip, legend, area mode |
| `frontend/src/components/agent/stats/StackedBarChart.tsx`, `DonutChart.tsx`, `HeatmapChart.tsx`, `Sparkline.tsx`, `KpiTile.tsx` | new chart types |
| `frontend/src/routes/agent/ReportsPage.tsx` | `/agent/reports` overview + catalogue |
| `frontend/src/routes/agent/ReportViewPage.tsx` | `/agent/reports/$reportKey` |
| `frontend/src/routes/admin/ReportingAdminPage.tsx` | `/admin/reporting` |
| modify `frontend/src/themes/tokens.css`, `frontend/tailwind.config.js` | `--color-series-1..6`, `--color-seq-1..7` |
| modify `frontend/src/router.tsx`, `components/layout/AgentShell.tsx`, `routes/admin/AdminHomePage.tsx`, `routes/agent/TimeAccountingReportPage.tsx` | routes, nav link, admin tile, link to time reports |
| delete `frontend/src/routes/agent/StatsPage.tsx` (+ its test if any) | replaced by `ReportsPage` (Task 20) |

### R1. Tables (one migration, Task 1)

```
tiqora_report_ticket_fact                         -- one row per ticket, full recompute per touched ticket
  ticket_id                       BIGINT PK (no autoincrement)
  tn                              VARCHAR(50)  NOT NULL
  created_at                      DATETIME NOT NULL
  created_by                      INT NOT NULL
  queue_id                        INT NOT NULL           -- current queue (permission scope)
  queue_id_at_create              INT NOT NULL
  state_id                        SMALLINT NOT NULL
  state_type                      VARCHAR(30) NOT NULL   -- ticket_state_type.name, e.g. "open", "closed", "pending reminder", "merged"
  priority_id                     SMALLINT NOT NULL
  type_id                         SMALLINT NULL
  service_id                      INT NULL
  sla_id                          INT NULL
  owner_id                        INT NOT NULL
  responsible_id                  INT NULL
  customer_id                     VARCHAR(150) NULL
  customer_user                   VARCHAR(250) NULL
  channel                         VARCHAR(30) NOT NULL   -- CHANNEL_KEYS value of the first article, "email" without articles
  archived                        BOOL NOT NULL default false
  merged                          BOOL NOT NULL default false
  first_response_at               DATETIME NULL
  first_response_minutes          INT NULL               -- wall clock
  first_response_working_minutes  INT NULL               -- NULL when the calendar has no working hours
  first_response_target_minutes   INT NULL               -- queue/SLA first_response_time; NULL when 0
  first_response_due_at           DATETIME NULL
  first_response_breached         BOOL NULL              -- NULL = not decided yet (open, no response)
  first_closed_at                 DATETIME NULL
  closed_at                       DATETIME NULL          -- LAST close (Znuny _TicketGetClosed = MAX)
  solution_minutes                INT NULL
  solution_working_minutes        INT NULL
  solution_target_minutes         INT NULL
  solution_due_at                 DATETIME NULL
  solution_breached               BOOL NULL
  update_target_minutes           INT NULL
  update_breach_count             INT NOT NULL default 0
  calendar_name                   VARCHAR(100) NULL
  close_count                     INT NOT NULL default 0
  reopen_count                    INT NOT NULL default 0
  queue_move_count                INT NOT NULL default 0
  queue_pingpong                  BOOL NOT NULL default false
  owner_change_count              INT NOT NULL default 0
  article_in_count                INT NOT NULL default 0   -- sender customer
  article_out_count               INT NOT NULL default 0   -- sender agent, visible for customer
  note_count                      INT NOT NULL default 0   -- sender agent, internal
  agent_touch_count               INT NOT NULL default 0   -- distinct agent authors
  accounted_time                  NUMERIC(12,2) NOT NULL default 0
  ai_triaged                      BOOL NULL
  ai_triage_queue_applied         BOOL NULL
  ai_auto_replied                 BOOL NULL
  ai_draft_used                   BOOL NULL
  ai_handed_off                   BOOL NULL
  process_entity_id               VARCHAR(50) NULL
  last_event_at                   DATETIME NOT NULL
  last_history_id                 BIGINT NOT NULL default 0
  fact_version                    SMALLINT NOT NULL
  built_at                        DATETIME NOT NULL      -- app-written naive UTC
  INDEX ix_tiqora_report_fact_queue_created (queue_id, created_at)
  INDEX ix_tiqora_report_fact_created (created_at)
  INDEX ix_tiqora_report_fact_closed (closed_at)
  INDEX ix_tiqora_report_fact_customer_created (customer_id, created_at)
  INDEX ix_tiqora_report_fact_owner (owner_id)
  INDEX ix_tiqora_report_fact_state_type (state_type)
  INDEX ix_tiqora_report_fact_last_event (last_event_at)
  INDEX ix_tiqora_report_fact_version (fact_version)

tiqora_report_ticket_event                        -- deleted + re-inserted with the fact (no unique key needed)
  id                     BIGINT PK autoincrement
  ticket_id              BIGINT NOT NULL
  history_id             BIGINT NULL
  article_id             BIGINT NULL
  kind                   VARCHAR(30) NOT NULL    -- EVENT_KINDS
  occurred_at            DATETIME NOT NULL
  actor_user_id          INT NULL
  from_id                INT NULL
  to_id                  INT NULL
  from_value             VARCHAR(100) NULL
  to_value               VARCHAR(100) NULL
  from_duration_seconds  BIGINT NULL             -- time spent in the "from" value
  queue_id               INT NOT NULL            -- the ticket's queue at that moment
  INDEX ix_tiqora_report_event_ticket (ticket_id)
  INDEX ix_tiqora_report_event_kind_time (kind, occurred_at)
  INDEX ix_tiqora_report_event_actor_time (actor_user_id, occurred_at)
  INDEX ix_tiqora_report_event_queue_time (queue_id, occurred_at)

tiqora_report_daily_snapshot                      -- end-of-day stock in the reporting time zone
  snapshot_date   DATE NOT NULL
  queue_id        INT NOT NULL
  state_type      VARCHAR(30) NOT NULL
  priority_id     SMALLINT NOT NULL
  open_count      INT NOT NULL
  age_0_1d        INT NOT NULL
  age_1_3d        INT NOT NULL
  age_3_7d        INT NOT NULL
  age_7_30d       INT NOT NULL
  age_30d_plus    INT NOT NULL
  built_at        DATETIME NOT NULL
  PRIMARY KEY (snapshot_date, queue_id, state_type, priority_id)
  INDEX ix_tiqora_report_snapshot_queue_date (queue_id, snapshot_date)

tiqora_report_state                               -- single row name = "facts"
  name                        VARCHAR(50) PK
  last_history_id             BIGINT NOT NULL default 0
  gap_ids                     TEXT NULL (MEDIUMTEXT on mysql/mariadb)   -- JSON [[history_id, first_seen_epoch], ...], up to MAX_GAPS
  last_tick_at                DATETIME NULL
  last_orphan_sweep_at        DATETIME NULL
  backfill_status             VARCHAR(20) NOT NULL default 'pending'   -- pending | running | done
  backfill_job_id             BIGINT NULL
  backfill_start_ticket_id    BIGINT NULL
  backfill_cursor_ticket_id   BIGINT NULL
  backfill_target_ticket_id   BIGINT NULL
  backfill_since              DATE NULL
  backfill_started_at         DATETIME NULL
  backfill_run_token          VARCHAR(36) NULL
  backfill_finished_at        DATETIME NULL
  last_snapshot_date          DATE NULL
  snapshot_time_zone          VARCHAR(64) NULL
  updated_at                  DATETIME NOT NULL

tiqora_report_dirty_ticket                        -- tickets whose facts must be rebuilt without a new history row
  ticket_id   BIGINT PK
  queued_at   DATETIME NOT NULL
```

ORM classes in `models.py`: `TiqoraReportTicketFact`, `TiqoraReportTicketEvent`, `TiqoraReportDailySnapshot`, `TiqoraReportState`, `TiqoraReportDirtyTicket`. Booleans `server_default=false()`; `accounted_time` `Numeric(12, 2)`; `gap_ids` uses wave 0's `JOB_RESULT_TEXT` variant (`Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")`) because 5000 gaps exceed MySQL `TEXT`'s 64 KB.

### R2. Settings section (Task 1)

```python
# backend/src/tiqora/reporting/settings.py
class ReportingSettings(BaseModel):
    time_zone: str = Field("", max_length=64)          # "" = Znuny default zone (UserDefaultTimeZone/OTRSTimeZone)
    access: Literal["all_agents", "stats_group"] = "all_agents"
    near_miss_percent: int = Field(10, ge=1, le=50)
    cache_seconds: int = Field(300, ge=0, le=3600)
    inline_row_threshold: int = Field(200_000, ge=1_000, le=10_000_000)
    max_result_rows: int = Field(5_000, ge=100, le=10_000)
    export_row_limit: int = Field(100_000, ge=1_000, le=1_000_000)
    query_timeout_seconds: int = Field(60, ge=5, le=600)
REPORTING_SECTION = SettingsSection(key="reporting", model=ReportingSettings,
    title_key="admin.settings.reporting.title", i18n_prefix="admin.settings.reporting")
async def load_reporting_settings(session: AsyncSession, settings: Settings) -> ReportingSettings
    # load_section_values(session, REPORTING_SECTION, secret_key=<exactly what wave-0 callers pass>) — the section
    # has no secrets; every reporting module loads its settings through this one function
async def reporting_time_zone(session: AsyncSession, values: ReportingSettings) -> str
    # valid_time_zone(values.time_zone) or await default_time_zone(SysConfig(session))
```

Daemon switch keys in `tiqora.domain.settings_store`: `KEY_REPORT_FACTS_ENABLED = "daemon.report_facts.enabled"` (default True), `KEY_REPORT_FACTS_INTERVAL_SECONDS = "daemon.report_facts.interval_seconds"`. Config: `report_facts_interval_seconds: int = Field(default=60, validation_alias="TIQORA_REPORT_FACTS_INTERVAL")`.

### R3. Working time (Task 2)

```python
# backend/src/tiqora/znuny/escalation.py (additions; the old underscored names stay as aliases)
async def escalation_preferences(session, sla_id: int | None, queue_id: int) -> tuple[int, int, int, str | None]
async def calendar_config(sysconfig: SysConfig, calendar: str | None) -> tuple[WorkingHours, VacationDays, VacationDaysOneTime, str]
def to_epoch(value: Any, tz_name: str) -> int
def working_seconds_between(start_epoch: int, stop_epoch: int, working_hours: WorkingHours,
                            vacation_days: VacationDays, vacation_days_once: VacationDaysOneTime,
                            tz_name: str) -> int
    # port of Kernel/System/DateTime.pm::Delta(ForWorkingTime => 1): seconds of working time in [start, stop);
    # stop <= start -> 0; no working hours configured -> 0 (Znuny); midnight whole-day fast path like
    # destination_time_epoch; inverse property: working_seconds_between(s, destination_time_epoch(s, m, ...)) == m * 60

# backend/src/tiqora/reporting/working_time.py
@dataclass(frozen=True, slots=True)
class WorkingCalendar:
    name: str | None
    working_hours: WorkingHours
    vacation_days: VacationDays
    vacation_days_once: VacationDaysOneTime
    time_zone: str
    @property
    def has_working_hours(self) -> bool

@dataclass(frozen=True, slots=True)
class EscalationTargets:
    first_response_minutes: int    # 0 = none
    update_minutes: int
    solution_minutes: int
    calendar_name: str | None

class CalendarResolver:            # one per builder run; caches everything
    def __init__(self, session: AsyncSession, sysconfig: SysConfig) -> None
    async def storage_time_zone(self) -> str                       # sysconfig.otrs_time_zone() or "UTC"
    async def targets(self, sla_id: int | None, queue_id: int) -> EscalationTargets
    async def calendar(self, name: str | None) -> WorkingCalendar

def to_epoch(value: datetime, storage_tz: str) -> int             # naive DB value in storage tz -> epoch
def from_epoch(epoch: int, storage_tz: str) -> datetime           # epoch -> naive value in storage tz
def working_minutes(start: datetime, stop: datetime, calendar: WorkingCalendar, storage_tz: str) -> int | None
    # None when not calendar.has_working_hours; else int(working_seconds_between(...) / 60) (Znuny int())
def due_at(start: datetime, minutes: int, calendar: WorkingCalendar, storage_tz: str) -> datetime
    # has_working_hours: from_epoch(destination_time_epoch(...)); else start + timedelta(minutes=minutes)
```

### R4. History → events (Task 3, pure)

```python
# backend/src/tiqora/reporting/history_events.py
EVENT_KINDS: Final = ("create", "state", "close", "reopen", "queue", "owner", "responsible", "priority",
                      "type", "service", "sla", "merge", "process_activity", "reply", "note", "customer_message")
CLOSED_STATE_TYPE: Final = "closed"
PROCESS_ACTIVITY_FIELD: Final = "ProcessManagementActivityID"   # = tiqora.process.ticket_state.ACTIVITY_ID_DF_NAME

@dataclass(frozen=True, slots=True)
class TicketRow:
    id: int; tn: str; create_time: datetime; create_by: int; queue_id: int; state_id: int
    priority_id: int; type_id: int | None; service_id: int | None; sla_id: int | None
    user_id: int; responsible_user_id: int | None; customer_id: str | None
    customer_user_id: str | None; archive_flag: int

@dataclass(frozen=True, slots=True)
class HistoryRow:
    id: int; history_type: str; name: str; article_id: int | None; queue_id: int; state_id: int
    priority_id: int; owner_id: int; type_id: int; create_time: datetime; create_by: int

@dataclass(frozen=True, slots=True)
class ArticleRow:
    id: int; sender_type: str; visible: bool; channel: str; create_time: datetime; create_by: int

@dataclass(frozen=True, slots=True)
class EventRow:
    ticket_id: int; kind: str; occurred_at: datetime; queue_id: int
    history_id: int | None = None; article_id: int | None = None; actor_user_id: int | None = None
    from_id: int | None = None; to_id: int | None = None
    from_value: str | None = None; to_value: str | None = None; from_duration_seconds: int | None = None

def derive_events(ticket: TicketRow, history: Sequence[HistoryRow], articles: Sequence[ArticleRow],
                  state_types: Mapping[int, str]) -> list[EventRow]
```

Fixed rules:

1. History is sorted by `id`. Znuny/Tiqora write the history row AFTER updating the ticket (verified: `ticket_write_service.py` UPDATE then `add_move`/`add_state_update`/`add_owner_update`/`add_priority_update`), so every row's `queue_id/state_id/owner_id/priority_id/type_id` are the values AFTER that action.
2. **Baseline** = first history row (no history → a synthetic baseline from the ticket row at `ticket.create_time`, actor `ticket.create_by`). Emit `create` (occurred_at = `ticket.create_time`, actor = `ticket.create_by`, `to_id` = baseline queue, `to_value` = baseline state type, `queue_id` = baseline queue). A baseline in a closed-type state also emits `close` at the same instant.
3. **Column diff** for each later row r against the previous snapshot p: `queue_id`→`queue`, `state_id`→`state`, `owner_id`→`owner`, `priority_id`→`priority`, `type_id`→`type`; `from_id = p.x`, `to_id = r.x`, `history_id = r.id`, `actor = r.create_by`, `occurred_at = r.create_time`, `queue_id = r.queue_id`. For `state` also `from_value/to_value` = state type names. A `state` event into type `closed` from a non-closed type adds `close`; from `closed` to non-closed adds `reopen` (same history id). Diffing is used instead of parsing `%%` names because it is identical for both writers and also catches changes made without their own history type.
4. **Name parsing** only where no snapshot column exists: `ServiceUpdate` / `SLAUpdate` (`%%New%%NewID%%Old%%OldID`; empty id → `None`) → `service` / `sla`; `ResponsibleUpdate` (`%%NewUser%%NewUserID`) → `responsible` with `from_id` = previous responsible event's `to_id` (else `None`); `Merged` → `merge` (`to_value` = name with leading `%%` removed, cut to 100 chars); `TicketDynamicFieldUpdate` whose first value is `PROCESS_ACTIVITY_FIELD` (`%%FieldName%%X%%Value%%V%%OldValue%%OV`) → `process_activity` (`from_value` = OV or `None`, `to_value` = V). Unparseable names are skipped, never raise.
5. `from_duration_seconds` for `queue, state, owner, priority, type, service, sla, responsible, process_activity` = seconds since the previous event of the same kind, or since `ticket.create_time` for the first one; `None` for the rest.
6. **Article events** (articles sorted by `(create_time, id)`): sender `agent` + visible → `reply`; `agent` + not visible → `note`; `customer` → `customer_message`; `system` → none. `actor` = `create_by`, `article_id` set, `history_id` `None`, `queue_id` = queue of the last history row with `create_time <= article.create_time` (else the baseline queue).
7. Output is sorted by `(occurred_at, history_id or 0, article_id or 0, EVENT_KINDS.index(kind))`.

### R5. Facts (Tasks 4–5)

```python
# backend/src/tiqora/reporting/facts.py (pure)
FACT_VERSION: Final = 1
CHANNEL_KEYS: Final[dict[str, str]] = {"Email": "email", "Phone": "phone", "Internal": "internal",
    "Telegram": "telegram", "Chat": "webchat", "SMS": "sms", "WhatsApp": "whatsapp"}
    # unknown channel name -> name.lower()[:30]

@dataclass(frozen=True, slots=True)
class AiFlags:
    triaged: bool | None = None; triage_queue_applied: bool | None = None; auto_replied: bool | None = None
    draft_used: bool | None = None; handed_off: bool | None = None

@dataclass(frozen=True, slots=True)
class FactInputs:
    ticket: TicketRow; history: Sequence[HistoryRow]; articles: Sequence[ArticleRow]
    events: Sequence[EventRow]; state_types: Mapping[int, str]; accounted_time: Decimal
    targets: EscalationTargets; calendar: WorkingCalendar; storage_tz: str; ai: AiFlags
    process_entity_id: str | None; now: datetime          # naive UTC (built_at)

@dataclass(frozen=True, slots=True)
class FactRow:   # one attribute per tiqora_report_ticket_fact column, same names
    ...
    def as_insert_dict(self) -> dict[str, Any]

def compute_fact(inp: FactInputs) -> FactRow
```

Fixed rules: `first_response_at` = earliest (`create_time`, `id`) agent+visible article (Znuny `_TicketGetFirstResponse`); wall minutes = `int((b - a).total_seconds() // 60)`; working minutes via `working_minutes`. `close_count`/`reopen_count` count events; `first_closed_at`/`closed_at` = first/last `close` event time. Solution minutes: created → `closed_at` when `close_count > 0`. Targets from `CalendarResolver.targets(ticket.sla_id, ticket.queue_id)` (current SLA else queue, like Znuny `TicketEscalationPreferences`); target 0 → `*_target_minutes`, `*_due_at`, `*_breached` all `NULL`. `first_response_due_at = due_at(created, target)`; `first_response_breached` = `working (or wall when no working hours) > target` when responded; when not responded and `close_count > 0`: `first_closed_at > due`; else `NULL`. `solution_breached` = `solution working (or wall) > target` when `close_count > 0`, else `NULL`. `update_breach_count` (only when update target > 0): walk articles in order, a customer article starts waiting if not already waiting, the next agent+visible article ends it; a waiting period longer than the target (working, or wall without working hours) counts 1; a still-open waiting period does not count. `queue_pingpong` = the queue sequence (create queue, then each `queue` event's `to_id`) revisits a queue after leaving it. `agent_touch_count` = distinct `create_by` of agent articles. `channel` = `CHANNEL_KEYS` of the first article's channel, `"email"` without articles. `last_event_at` = max of created, history and article times; `last_history_id` = max history id or 0.

```python
# backend/src/tiqora/reporting/facts_store.py
FACT_CHUNK_SIZE: Final = 200
def fact_write_lock() -> asyncio.Lock
    # one lock per running event loop (weakref.WeakKeyDictionary[AbstractEventLoop, asyncio.Lock]); serialises fact
    # writes of the tick loop and the backfill job in the worker process. Not a module-level Lock: under pytest-asyncio
    # each test has its own loop and a contended module-level Lock raises "bound to a different event loop".
@dataclass(frozen=True, slots=True)
class RebuildResult:
    built: int
    deleted: int                                 # fact rows removed for tickets that no longer exist
async def load_fact_inputs(session: AsyncSession, ticket_ids: Sequence[int], *, resolver: CalendarResolver,
                           now: datetime) -> dict[int, FactInputs]
    # 1 query each: ticket rows; state id -> type name; history rows (+ type names) ORDER BY id;
    # article meta (id, ticket_id, sender type name, is_visible_for_customer, channel name, create_time, create_by);
    # SUM(time_unit) GROUP BY ticket_id; AI flags (tiqora_ai_triage, tiqora_ai_article_origin via article ids,
    # tiqora_ai_draft status 'accepted', tiqora_ai_ticket_state.ai_escalated_at IS NOT NULL);
    # process id via dynamic_field_value of field PROCESS_ID_DF_NAME (tiqora.process.ticket_state). Integer joins only.
async def delete_ticket_facts(session: AsyncSession, ticket_ids: Sequence[int]) -> None   # events + facts; flush only
async def rebuild_ticket_facts(session: AsyncSession, ticket_ids: Iterable[int], *,
                               sysconfig: SysConfig | None = None, now: datetime | None = None) -> RebuildResult
    # chunks of FACT_CHUNK_SIZE: delete_ticket_facts(chunk) then insert facts + events of the tickets that exist;
    # flush only, NEVER commits
async def rebuild_and_commit(factory: async_sessionmaker[AsyncSession], ticket_ids: Iterable[int], *,
                             now: datetime | None = None, attempts: int = 3) -> RebuildResult
    # per chunk: async with fact_write_lock(), own session, rebuild_ticket_facts, commit;
    # IntegrityError / OperationalError (deadlock, lock wait) -> rollback, retry the chunk up to `attempts`, then raise
```

### R6. Incremental loop, dirty queue, snapshots, backfill (Tasks 7–9)

```python
# backend/src/tiqora/reporting/dirty.py
async def mark_tickets_dirty(session: AsyncSession, ticket_ids: Iterable[int]) -> None   # delete-then-insert; flush only
async def take_dirty_ticket_ids(session: AsyncSession, limit: int) -> list[int]          # oldest first; does not delete
async def clear_dirty(session: AsyncSession, ticket_ids: Sequence[int]) -> None           # flush only

# backend/src/tiqora/reporting/state.py
STATE_NAME: Final = "facts"
async def load_state(session: AsyncSession, *, for_update: bool = False) -> TiqoraReportState   # creates the row if missing
async def facts_status(session: AsyncSession) -> FactsStatusOut                              # see R9

# backend/src/tiqora/reporting/incremental.py
HISTORY_BATCH: Final = 5000
MAX_BATCHES_PER_TICK: Final = 10
GAP_TTL_SECONDS: Final = 600
MAX_GAPS: Final = 5000
DIRTY_BATCH: Final = 1000
ORPHAN_SWEEP_EVERY_SECONDS: Final = 3600
ORPHAN_PAGE: Final = 5000
VERSION_UPGRADE_BATCH: Final = 500
SNAPSHOT_MAX_DAYS_PER_TICK: Final = 31
async def run_report_facts_tick(*, settings: Settings | None = None,
                                session_factory: async_sessionmaker[AsyncSession] | None = None,
                                now: datetime | None = None, wall_clock: Callable[[], float] = time.time,
                                registry: Registry | None = None) -> dict[str, int]
```

Tick order (each step commits on its own; every step is idempotent, so a crash in between only repeats work):

1. `load_state`. If `last_history_id == 0`: set it to `MAX(ticket_history.id)` (0 if empty), commit (older history belongs to the backfill).
2. If `backfill_status == "pending"` and `backfill_job_id is None`: `enqueue_job(session, kind="core.report_backfill", payload=ReportBackfillPayload(), created_by=None, dedupe_key="report_backfill")`, store the job id, commit.
3. History: up to `MAX_BATCHES_PER_TICK` times read `id, ticket_id` with `id > last_history_id ORDER BY id LIMIT HISTORY_BATCH`, plus rows whose id is in the gap list. Gap bookkeeping: every id between the old watermark and the batch maximum that was not returned joins the gap list with `first_seen = wall_clock()`; gap ids that now returned a row leave it; gaps older than `GAP_TTL_SECONDS` are dropped (rolled-back inserts); the list is capped at `MAX_GAPS` (oldest dropped). Rebuild touched tickets with `rebuild_and_commit`, then save `last_history_id` + `gap_ids` + `last_tick_at`, commit. (A time-based "only rows older than 60 s" filter was rejected: `ticket_history.create_time` is written with the DB session's `current_timestamp`, whose zone is not reliably UTC on prod.)
4. Dirty queue: `take_dirty_ticket_ids(DIRTY_BATCH)` → `rebuild_and_commit` → `clear_dirty`, commit.
5. Fact version upgrade (only when `backfill_status == "done"`): up to `VERSION_UPGRADE_BATCH` fact ticket ids with `fact_version < FACT_VERSION` → rebuild.
6. Orphan sweep at most every `ORPHAN_SWEEP_EVERY_SECONDS`: page fact ticket ids by `ORPHAN_PAGE`, load which exist in `ticket` (IN-list), `delete_ticket_facts` for the rest.
7. Snapshots (only when `backfill_status == "done"`): zone = `reporting_time_zone`; `yesterday` = local date of `now` − 1 day; days from `last_snapshot_date + 1` (or `yesterday` if `NULL`) to `yesterday`, at most `SNAPSHOT_MAX_DAYS_PER_TICK`; `snapshot_days(...)`; save `last_snapshot_date`, `snapshot_time_zone`.
Returns `{"history_rows", "tickets", "gaps", "dirty", "upgraded", "orphans", "snapshot_days", "watermark"}`.

The daily snapshot therefore runs inside the hourly-or-faster `report_facts` interval loop instead of a `_daily_loop` at 00:05 (see Deviations).

```python
# backend/src/tiqora/reporting/snapshots.py
AGE_BUCKETS: Final = (("age_0_1d", 1), ("age_1_3d", 3), ("age_3_7d", 7), ("age_7_30d", 30), ("age_30d_plus", None))
OPEN_STATE_TYPES                                    # imported from tiqora.domain.queue_service
@dataclass(frozen=True, slots=True)
class Segment:
    start: datetime; end: datetime | None; queue_id: int; state_type: str; priority_id: int
SnapshotKey = tuple[date, int, str, int]            # (day, queue_id, state_type, priority_id)
def ticket_timeline(created_at: datetime, events: Sequence[EventRow]) -> list[Segment]
    # from create/queue/state/priority events; contiguous, last segment end None
def day_end_instants(days: Sequence[date], zone: str) -> dict[date, datetime]
    # naive UTC instant of local midnight AFTER each day (DST-correct via zoneinfo)
def compute_snapshot_counts(tickets: Iterable[tuple[datetime, Sequence[EventRow]]],
                            ends: Mapping[date, datetime]) -> dict[SnapshotKey, list[int]]
    # value list = [open_count, age_0_1d, age_1_3d, age_3_7d, age_7_30d, age_30d_plus];
    # a ticket counts for day d when created_at < ends[d] and its segment at ends[d] has an OPEN_STATE_TYPES state;
    # age = ends[d] - created_at
async def write_snapshots(session: AsyncSession, days: Sequence[date], counts: Mapping[SnapshotKey, list[int]],
                          *, now: datetime) -> int     # DELETE rows of those days, INSERT counts; flush only
async def snapshot_days(factory: async_sessionmaker[AsyncSession], days: Sequence[date], *, zone: str,
                        now: datetime | None = None) -> int
    # candidates: facts with created_at < end(last day) AND (state_type IN OPEN_STATE_TYPES OR last_event_at >= end(first day − 1));
    # events (create/queue/state/priority) loaded per 500 candidates; one commit per call
```

```python
# backend/src/tiqora/reporting/backfill.py
class ReportBackfillPayload(BaseModel):
    since: date | None = None        # only tickets created on/after this local date (reporting tz); snapshots from there
    restart: bool = False            # ignore the stored cursor
    snapshots: bool = True
ProgressFn = Callable[[int, int | None, str | None], Awaitable[None]]
CancelFn = Callable[[], Awaitable[None]]
async def run_backfill(factory: async_sessionmaker[AsyncSession], settings: Settings,
                       payload: ReportBackfillPayload, *, progress: ProgressFn, check_cancelled: CancelFn,
                       sleep_seconds: float = 0.05, now: datetime | None = None) -> dict[str, int]
    # 1. state: last_history_id == 0 -> set to MAX(ticket_history.id) first (CLI path without a running worker)
    # 2. restart or cursor NULL or status != "running": start = first ticket id (created >= since if given) - 1,
    #    target = MAX(ticket.id), status "running", started_at; commit
    # 3. loop ticket ids in (cursor, target] ORDER BY id LIMIT FACT_CHUNK_SIZE -> rebuild_and_commit -> save cursor
    #    (commit) -> progress(done, total, "Tickets werden ausgewertet …") -> check_cancelled -> sleep
    # 4. snapshots (payload.snapshots): days from (since or local date of MIN(fact.created_at)) to yesterday,
    #    in 31-day windows via snapshot_days -> progress(..., "Tagesstände werden berechnet …")
    # 5. status "done", finished_at, last_snapshot_date = yesterday, snapshot_time_zone = zone; commit
    # returns {"tickets": n, "snapshot_days": d}
REPORT_BACKFILL_JOB = JobHandler(kind="core.report_backfill", payload_model=ReportBackfillPayload,
    run=run_backfill_job, title_key="jobs.kinds.core.report_backfill", timeout_seconds=86_400)
```

CLI (`backend/src/tiqora/cli/report.py`, `add_report_subparser`, dispatched in `main.py` like `gdpr`): `tiqora report backfill [--since YYYY-MM-DD] [--restart] [--no-snapshots]` (prints progress lines), `tiqora report status` (prints `facts_status` as JSON).

### R7. SQL constructs (Task 10)

```python
# backend/src/tiqora/reporting/sql.py
Grain = Literal["day", "week", "month", "quarter", "year"]
DatePart = Literal["hour", "isodow"]
class shift_seconds(FunctionElement[datetime]):   # (expr, seconds: int) ; type DateTime(); inherit_cache = False
class date_bucket(FunctionElement[date]):         # (expr, grain) ; type Date(); inherit_cache = False
class date_part(FunctionElement[int]):            # (expr, part) ; type Integer(); inherit_cache = False
```

| construct | MySQL/MariaDB (`@compiles` for `"mysql"` AND `"mariadb"`) | PostgreSQL |
|---|---|---|
| `shift_seconds(x, n)` | `DATE_ADD(x, INTERVAL n SECOND)` (n = 0 → `x`) | `(x + INTERVAL 'n seconds')` |
| `date_bucket(x, "day")` | `DATE(x)` | `CAST(date_trunc('day', x) AS DATE)` |
| `"week"` (ISO, Monday) | `DATE(DATE_SUB(x, INTERVAL WEEKDAY(x) DAY))` | `CAST(date_trunc('week', x) AS DATE)` |
| `"month"` | `DATE(DATE_SUB(x, INTERVAL DAYOFMONTH(x) - 1 DAY))` | `CAST(date_trunc('month', x) AS DATE)` |
| `"quarter"` | `DATE_ADD(MAKEDATE(YEAR(x), 1), INTERVAL QUARTER(x) - 1 QUARTER)` | `CAST(date_trunc('quarter', x) AS DATE)` |
| `"year"` | `MAKEDATE(YEAR(x), 1)` | `CAST(date_trunc('year', x) AS DATE)` |
| `date_part(x, "hour")` | `HOUR(x)` | `CAST(EXTRACT(HOUR FROM x) AS INTEGER)` |
| `date_part(x, "isodow")` | `(WEEKDAY(x) + 1)` | `CAST(EXTRACT(ISODOW FROM x) AS INTEGER)` |

Any other dialect: `CompileError("<construct>: unsupported dialect <name>")`. No literal `%` anywhere (pyformat drivers would need `%%`).

Time zones never use `CONVERT_TZ`/`AT TIME ZONE` (MariaDB's named-zone tables are usually empty): the compiler shifts naive UTC columns by the zone's UTC offset, piecewise across DST transitions:

```python
@dataclass(frozen=True, slots=True)
class TzSegment:
    until_utc: datetime | None    # exclusive upper bound; None = open end
    offset_seconds: int
def tz_segments(zone: str, start_utc: datetime, end_utc: datetime, *, margin_days: int = 400) -> tuple[TzSegment, ...]
    # offsets of `zone` over [start - margin, end + margin]; transitions found by daily probing + bisection to the second
def to_local(expr: ColumnElement[datetime], segments: Sequence[TzSegment]) -> ColumnElement[datetime]
    # one segment -> shift_seconds(expr, off); else CASE WHEN expr < until_1 THEN shift(expr, off_1) ... ELSE shift(expr, off_n) END
class ReportQueryTimeout(Exception): ...
@asynccontextmanager
async def statement_timeout(session: AsyncSession, seconds: int) -> AsyncIterator[None]
    # postgresql: SET LOCAL statement_timeout = <ms>
    # mariadb (dialect.is_mariadb): SET SESSION max_statement_time = <s>; finally SET SESSION max_statement_time = 0
    # mysql: SET SESSION max_execution_time = <ms>; finally SET SESSION max_execution_time = 0
def is_timeout_error(exc: BaseException) -> bool   # MariaDB 1969, MySQL 3024, PostgreSQL SQLSTATE 57014
```

### R8. Definitions, semantic layer, compiler, engine (Tasks 11–13)

```python
# backend/src/tiqora/reporting/definition.py
SCHEMA_VERSION: Final = 1
VisualizationType = Literal["table", "pivot", "bar", "stacked_bar", "line", "area", "donut", "heatmap", "kpi"]
class RelativeTimeRange(BaseModel):          # extra="forbid"
    kind: Literal["relative"] = "relative"
    unit: Literal["day", "week", "month", "quarter", "year"]
    count: int = Field(ge=1, le=3660)
    include_current: bool = False
class AbsoluteTimeRange(BaseModel):          # extra="forbid"; end >= start; span <= 3660 days
    kind: Literal["absolute"] = "absolute"
    start: date                              # local dates in the report time zone, both inclusive
    end: date
TimeRange = Annotated[RelativeTimeRange | AbsoluteTimeRange, Field(discriminator="kind")]
class DimensionRef(BaseModel):               # extra="forbid"
    key: str
    grain: Grain | None = None               # date dimensions only; default "day"
    choices: list[str] = Field(default_factory=list, max_length=10)   # runtime alternatives (same source)
    top_n: int | None = Field(None, ge=1, le=100)                     # rest folded into "__other__"
class MeasureRef(BaseModel):                 # extra="forbid"
    key: str
class ReportParameter(BaseModel):            # Znuny "changeable at runtime"; extra="forbid"
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    field: str                               # dimension key or filter-only field key of the source
    op: Literal["in", "eq", "is_true", "is_false"] = "in"
    label_key: str | None = None
    allowed_values: list[str | int] | None = None   # None = anything the viewer may see
    default: list[str | int] | None = None
    required: bool = False
class SortSpec(BaseModel):
    key: str                                 # dimension or measure key
    direction: Literal["asc", "desc"] = "desc"
class Totals(BaseModel):
    grand: bool = False                      # one total row
    rows: bool = False                       # pivot: total per first-dimension value
    columns: bool = False                    # pivot: total per second-dimension value
class Visualization(BaseModel):
    type: VisualizationType = "table"
    series_dimension: int | None = None      # index of the dimension that forms the series
    chart_measures: list[int] | None = None  # measure indices plotted (one unit per chart); None = all
class ReportDefinition(BaseModel):           # extra="forbid"
    schema_version: Literal[1] = 1
    source: str
    dimensions: list[DimensionRef] = Field(default_factory=list, max_length=3)
    measures: list[MeasureRef] = Field(min_length=1, max_length=10)
    filter: Filter | None = None             # wave-0 filter language; field keys = source dimension/filter fields
    parameters: list[ReportParameter] = Field(default_factory=list, max_length=10)
    time_field: str | None = None            # None -> source.default_time_field
    time_range: TimeRange | None = None      # None -> no window (sources without time field) or last 30 days
    time_zone: str | None = None             # None -> the viewer's zone
    visualization: Visualization = Field(default_factory=Visualization)
    sort: list[SortSpec] = Field(default_factory=list, max_length=3)
    limit: int | None = Field(None, ge=1, le=10_000)
    totals: Totals = Field(default_factory=Totals)

@dataclass(frozen=True, slots=True)
class TimeWindow:
    start_utc: datetime; end_utc: datetime       # naive UTC, [start, end)
    start_date: date; end_date: date             # local, inclusive
    time_zone: str
def resolve_time_window(tr: TimeRange, zone: str, now_utc: datetime) -> TimeWindow
    # relative, include_current False: the `count` complete units before the current one;
    # include_current True: the current unit plus count - 1 before it (end = end of current unit);
    # weeks start Monday; quarter = Jan/Apr/Jul/Oct
class DefinitionError(ValueError):
    def __init__(self, message: str, *, path: str) -> None
    message: str; path: str
def validate_definition(defn: ReportDefinition, registry: Registry) -> ReportSource
    # unknown source/dimension/measure/parameter field/sort key, grain on non-date dim, duplicate keys,
    # visualization indices out of range, filter fields (validate_filter with the source's FilterFieldSpecs),
    # time_field not a date dimension of the source, time_zone not a valid zone -> DefinitionError
def definition_hash(defn: ReportDefinition) -> str   # sha256 of json.dumps(model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True)
```

```python
# backend/src/tiqora/reporting/semantic.py
DimensionKind = Literal["id", "string", "enum", "date", "local_date", "hour", "weekday"]
LabelSource = Literal["queue", "state", "priority", "type", "service", "sla", "user",
                      "customer_company", "customer_user", "ticket", "none"]
MeasureAgg = Literal["count", "count_if", "count_distinct", "sum", "avg", "min", "max", "p50", "p90", "rate"]
MeasureUnit = Literal["count", "number", "minutes", "hours", "days", "units", "percent"]

@dataclass(frozen=True, slots=True)
class CompileContext:
    now: datetime                    # naive UTC
    time_zone: str
    tz: tuple[TzSegment, ...]        # for to_local
    near_miss_percent: int
    window: TimeWindow | None

ExprFn = Callable[[CompileContext], ColumnElement[Any]]
BoolFn = Callable[[CompileContext], ColumnElement[bool]]

@dataclass(frozen=True, slots=True)
class Dimension:
    key: str
    label_key: str                   # "reports.dimensions.<key>"
    kind: DimensionKind
    expr: ExprFn                     # raw column (naive UTC datetime for "date"/"hour"/"weekday"; DATE for "local_date")
    labels: LabelSource = "none"
    i18n_values: str | None = None   # "reports.values.state_type" etc. for enums
    filter_type: FieldType | None = None     # wave-0 filter type; None = not filterable; enums use "enum"
    enum_values: tuple[str, ...] = ()        # allowed values when filter_type == "enum"
    options_source: str | None = None        # wave-0 POST /reference/field-options key ("queue", "state", ...)
    stock: bool = False              # daily snapshot date: values are stocks, not flows

@dataclass(frozen=True, slots=True)
class FilterOnlyField:
    key: str; label_key: str; filter_type: FieldType; expr: ExprFn

@dataclass(frozen=True, slots=True)
class Measure:
    key: str
    label_key: str                   # "reports.measures.<key>"
    agg: MeasureAgg
    expr: ExprFn | None = None       # value for sum/avg/min/max/p50/p90/count_distinct
    condition: BoolFn | None = None  # count_if; rate numerator
    denominator: BoolFn | None = None   # rate
    unit: MeasureUnit = "count"
    stock: bool = False

@dataclass(frozen=True, slots=True)
class ReportSource:
    key: str
    label_key: str
    from_clause: Callable[[CompileContext], FromClause]
    scope_column: ExprFn                               # queue id column checked against the viewer's queues
    dimensions: tuple[Dimension, ...]
    measures: tuple[Measure, ...]
    filter_fields: tuple[FilterOnlyField, ...] = ()
    base_where: Callable[[CompileContext], list[ColumnElement[bool]]] = lambda ctx: []
    time_fields: tuple[str, ...] = ()                  # date / local_date dimension keys
    default_time_field: str | None = None
    requires_facts: bool = True
    archive_exclusion: BoolFn | None = None            # drops archived tickets unless scope.include_archived (tickets, ticket_events)
    def dimension(self, key: str) -> Dimension | None
    def measure(self, key: str) -> Measure | None
    def filter_spec(self, key: str) -> FilterFieldSpec | None   # dimensions with filter_type + filter_fields

REPORT_SOURCES: ExtensionPoint[ReportSource]   # key "core.reporting.sources", item key .key
```

Sources (Task 11; exact keys — labels via `labels`, enum values translated in the frontend):

- `tickets` — FROM `tiqora_report_ticket_fact f`; scope `f.queue_id`. Dimensions: `queue`(id, queue, filter id, options `queue`), `queue_at_create`(id, queue), `state`(id, state, options `state`), `state_type`(enum `reports.values.state_type`, filter enum), `priority`(id, priority, options `priority`), `type`(id, type, options `type`), `service`(id, service, options `service`), `sla`(id, sla, options `sla`), `owner`(id, user), `responsible`(id, user), `customer`(string `f.customer_id`, customer_company), `customer_user`(string, customer_user), `channel`(enum `reports.values.channel`), `created`(date `f.created_at`), `closed`(date `f.closed_at`), `first_response`(date `f.first_response_at`), `created_hour`(hour of `f.created_at`), `created_weekday`(weekday of `f.created_at`), `process`(string `f.process_entity_id`). Filter-only: `archived`, `merged`, `reopened` (`reopen_count > 0`), `has_first_response`, `ai_triaged`, `ai_auto_replied`, `ai_handed_off` (bool). Time fields `created` (default), `closed`, `first_response`. Measures: `tickets` count; `closed_tickets` count_if `state_type = 'closed'`; `open_tickets` count_if `state_type IN OPEN_STATE_TYPES`; `reopened_tickets` count_if `reopen_count > 0`; `reopen_rate` rate(`reopen_count > 0` / `close_count > 0`); `fcr_rate` rate(`close_count > 0 AND reopen_count = 0 AND article_out_count <= 1` / `close_count > 0`); `first_response_avg|p50|p90` (`first_response_working_minutes`, minutes); `first_response_clock_avg|p50` (`first_response_minutes`); `solution_avg|p50|p90` (`solution_working_minutes`); `solution_clock_avg|p50` (`solution_minutes`); `response_breaches` count_if RESP_BREACH; `response_breach_rate` rate(RESP_BREACH / `first_response_target_minutes > 0`); `solution_breaches`, `solution_breach_rate` (same with solution); `update_breaches` sum(`update_breach_count`); `update_breached_tickets` count_if `update_breach_count > 0`; `response_near_misses` count_if (`first_response_breached = false AND first_response_target_minutes > 0 AND first_response_working_minutes * 100 >= first_response_target_minutes * (100 - near_miss_percent)`); `solution_near_misses` likewise; `sla_met_rate` rate(has any target AND NOT RESP_BREACH AND NOT SOL_BREACH AND `update_breach_count = 0` / has any target); `accounted_time` sum (units); `agent_touches_avg` avg(`agent_touch_count`, number); `queue_moves_avg` avg(`queue_move_count`, number); `pingpong_tickets` count_if `queue_pingpong`. RESP_BREACH = `first_response_breached = true OR (first_response_breached IS NULL AND first_response_due_at IS NOT NULL AND first_response_due_at < :now)`; SOL_BREACH likewise with `solution_*`.
- `ticket_events` — FROM `tiqora_report_ticket_event e JOIN tiqora_report_ticket_fact f ON f.ticket_id = e.ticket_id`; scope `f.queue_id` (current queue, like ticket visibility). Dimensions: `kind`(enum `reports.values.event_kind`, filter enum), `occurred`(date), `occurred_hour`, `occurred_weekday`, `actor`(id `e.actor_user_id`, user), `queue`(id `e.queue_id`, queue), `from_queue`/`to_queue`(id `e.from_id`/`e.to_id`, queue), `from_state`/`to_state`(id, state), `from_state_type`/`to_state_type`(enum `e.from_value`/`e.to_value` for kinds create/state/close/reopen, NULL for others, `reports.values.state_type`; `from_queue`/`to_queue` only for kinds queue/create, `from_state`/`to_state` only for state/close/reopen), `ticket_queue`(id `f.queue_id`, queue), `ticket_state`(id `f.state_id`, state), `ticket_priority`(id `f.priority_id`, priority), `ticket_type`(id `f.type_id`, type), `channel`(enum `f.channel`), `customer`(string `f.customer_id`, customer_company). Time field `occurred`. Measures: `events` count; `tickets` count_distinct(`e.ticket_id`); `dwell_avg_hours|dwell_p50_hours|dwell_p90_hours` (`e.from_duration_seconds / 3600.0`, hours).
- `daily_snapshot` — FROM `tiqora_report_daily_snapshot s`; scope `s.queue_id`. Dimensions: `snapshot`(local_date `s.snapshot_date`, stock), `queue`, `state_type`, `priority`. Time field `snapshot`. Measures (all sum, stock): `open_tickets`(`open_count`), `age_0_1d`, `age_1_3d`, `age_3_7d`, `age_7_30d`, `age_30d_plus`.
- `accounted_time` — FROM Znuny `time_accounting ta JOIN ticket t ON t.id = ta.ticket_id` (integer join, both Znuny); scope `t.queue_id`; `requires_facts = False`. Dimensions: `agent`(id `ta.create_by`, user), `customer`(string `t.customer_id`, customer_company), `customer_user`(string `t.customer_user_id`, customer_user), `queue`(id `t.queue_id`), `type`(id `t.type_id`), `service`(id `t.service_id`), `ticket`(id `t.id`, ticket), `booked`(date `ta.create_time`), `booked_weekday`. Time field `booked`. Measures: `time_units` sum(`ta.time_unit`, units); `bookings` count; `tickets` count_distinct(`ta.ticket_id`).
- `open_tickets` (live, "now") — FROM Znuny `ticket t JOIN ticket_state st ON st.id = t.ticket_state_id JOIN ticket_state_type stt ON stt.id = st.type_id`; scope `t.queue_id`; base_where `stt.name IN OPEN_STATE_TYPES AND t.archive_flag = 0`; `requires_facts = False`. Dimensions: `queue`, `state`, `state_type`(`stt.name`), `priority`, `type`, `service`, `sla`, `owner`(`t.user_id`), `responsible`, `customer`, `customer_user`, `created`(date `t.create_time`). Time field `created` (no default — window only when the definition sets one). Measures: `tickets` count; `age_0_1d … age_30d_plus` count_if on `t.create_time` against `ctx.now` (bucket edges 1, 3, 7, 30 days); `escalated` count_if (`t.escalation_time > 0 AND t.escalation_time < :now_epoch`); `unassigned` count_if `t.user_id = 1`.

```python
# backend/src/tiqora/reporting/compiler.py
@dataclass(frozen=True, slots=True)
class RunParams:
    time_range: TimeRange | None = None
    grain: Grain | None = None                  # replaces the grain of the first date dimension
    time_zone: str = "UTC"                      # resolved by the caller (request > definition > viewer)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    dimension_choices: Mapping[int, str] = field(default_factory=dict)
    window_override: TimeWindow | None = None   # internal: /stats wrappers pass exact UTC bounds
    extra_filter: Filter | None = None          # internal: /stats wrappers' state/priority/type/customer filters
    last_snapshot_date: date | None = None      # internal: engine passes tiqora_report_state.last_snapshot_date

@dataclass(frozen=True, slots=True)
class MeasurePlan:
    key: str; agg: MeasureAgg; unit: MeasureUnit
    columns: tuple[str, ...]                    # select labels: ("m0",) or ("m0_num", "m0_den"); () for percentiles

@dataclass(frozen=True, slots=True)
class CompiledReport:
    definition: ReportDefinition                # effective (choices/grain/params applied)
    source: ReportSource
    stmt: Select[Any] | None                    # GROUP BY dims; None = empty scope -> empty result, no query
    raw_stmt: Select[Any] | None                # dims + percentile values ("v_<key>"); only when p50/p90 used
    dims: tuple[str, ...]                       # select labels "d0".."d2"
    measures: tuple[MeasurePlan, ...]
    window: TimeWindow | None
    stock_dates: tuple[date, ...] | None        # daily_snapshot period-end dates used
    include_archived: bool = False              # scope.include_archived took effect (cache keys must include it)

HIDDEN_QUEUE_ID: Final = -1                     # value of queue dimensions outside the viewer's scope ("Andere Queue")

class ReportCompileError(DefinitionError): ...   # runtime parameter/choice errors (422); not SQLAlchemy's CompileError

def compile_report(defn: ReportDefinition, params: RunParams, scope: TicketQueryScope, *,
                   now: datetime, near_miss_percent: int, registry: Registry,
                   group_by: Sequence[int] | None = None) -> CompiledReport
    # group_by: dimension indices to keep (totals queries use [] / [0] / [1])
```

Compiler rules: (1) `validate_definition`; apply `dimension_choices` (must be in `choices`), `grain` (first date dimension), parameter values (`ReportParameter`: value must be in `allowed_values` when set; `in` takes a list ≤ 1000; `eq` a scalar; bool ops ignore the value; required+missing → `CompileError`; missing optional → `default` or skipped). (2) Permission: `scope.queue_ids` empty → `stmt = None`; otherwise `source.scope_column(ctx).in_(sorted(scope.queue_ids))` is ANDed OUTSIDE every user/parameter condition, so no `any`/`not` can widen it; `source.archive_exclusion` is ANDed unless `scope.include_archived` (the caller grants that to admins only, like `GET /tickets`); queue-labelled dimensions that are not the scope column (`queue_at_create`, event `queue`/`from_queue`/`to_queue`) map ids outside the scope to `HIDDEN_QUEUE_ID` in SELECT and in filters; the effective definition records applied parameter values as each parameter's `default` (`required` cleared) so its hash differs per value. (3) Window: `params.window_override` or `resolve_time_window(time_range or RelativeTimeRange(unit="day", count=30, include_current=True), zone, now)`; applied to `time_field` (`expr >= start_utc AND expr < end_utc`; `local_date` fields compare `start_date <= x <= end_date`); sources with no `default_time_field` and no `time_range` get no window. (4) Date dimensions: `date_bucket(to_local(expr, ctx.tz), grain)`; hour/weekday: `date_part(to_local(expr, ctx.tz), …)`; `local_date`: `date_bucket(expr, grain)` without shift. (5) Stock dimensions/measures (`daily_snapshot`): if the snapshot dimension is absent or its grain is not `day`, restrict `snapshot_date IN stock_dates` = for every bucket in the window the last day of the bucket clipped to `end_date` and to `params.last_snapshot_date` when set; absent dimension → only the last such date. (6) Filters: `defn.filter` and `params.extra_filter` compiled by `filter_sql.compile_conditions` (R8a). (7) Measures → select columns per `MeasurePlan`: count `COUNT(*)`; count_if `SUM(CASE WHEN cond THEN 1 ELSE 0 END)`; count_distinct `COUNT(DISTINCT expr)`; sum/avg/min/max; rate → two SUM(CASE) columns; p50/p90 → nothing in `stmt`, `raw_stmt` gets the value column. (8) Sort/limit are applied in the engine (after percentiles/rates exist).

```python
# backend/src/tiqora/reporting/filter_sql.py   (R8a)
def compile_conditions(flt: Filter, resolve: Callable[[str], tuple[FilterFieldSpec, ColumnElement[Any]] | None],
                       *, now: datetime) -> ColumnElement[bool] | None
    # same JSON + validation as wave 0 (validate_filter, iter_conditions, relative_delta, parse_datetime_value);
    # ops: eq neq in not_in contains starts_with is_empty is_not_empty lt lte gt gte between before after
    # within_last older_than is_true is_false; neq/not_in include NULL (col IS NULL OR col <> v) like wave 0;
    # contains/starts_with escape % and _ and use LIKE ... ESCAPE '\'; raises FilterError
```

```python
# backend/src/tiqora/reporting/engine.py
OTHER_KEY: Final = "__other__"
PERCENTILE_SAMPLE_CAP: Final = 200_000
INLINE_BUDGET_SECONDS: Final = 75          # R-INLINEBUDGET: one deadline for all queries of a request
@dataclass
class QueryBudget:                         # seconds per statement
    per_query_seconds: int = 60; deadline: float | None = None; clock: Callable[[], float] = time.monotonic
    @classmethod
    def inline(cls, reporting, seconds=INLINE_BUDGET_SECONDS, *, clock=time.monotonic) -> QueryBudget
    @classmethod
    def job(cls, reporting) -> QueryBudget  # query_timeout_seconds per statement, no shared deadline
    def statement_seconds(self) -> int      # inline: min(per_query, remaining); remaining < 1 -> ReportQueryTimeout
async def estimate_rows(session: AsyncSession, compiled: CompiledReport, *,
                        budget: QueryBudget | None = None) -> int
    # SELECT COUNT(*) FROM <same FROM/WHERE as stmt>; 0 when stmt is None
async def run_report(session: AsyncSession, *, defn: ReportDefinition, params: RunParams, user_id: int,
                     report_key: str | None, reporting: ReportingSettings,
                     cache: ReportCache | None = None, now: datetime | None = None,
                     row_cap: int | None = None, compare_previous: bool = False,
                     registry: Registry | None = None, use_cache: bool = True,
                     include_archived: bool = False, budget: QueryBudget | None = None) -> ReportResultOut
    # scope = ticket_query_scope(session, user_id, include_archived=include_archived and is_admin); compile; cache
    # lookup (key from R8b; a hit gets the caller's report_key and the current facts status); execute every
    # statement inside statement_timeout(budget.statement_seconds()) and asyncio.wait_for(seconds + 5) ->
    # ReportQueryTimeout (budget None = QueryBudget.job; inline callers pass ONE QueryBudget.inline per request,
    # shared with estimate_rows; label lookups count against it too); a client-side timeout/cancellation
    # invalidates the connection before the timeout reset runs (R-STMTRESET T10 amendment);
    # percentiles from raw_stmt (linear interpolation like PERCENTILE_CONT; > PERCENTILE_SAMPLE_CAP rows -> sampled
    # first rows, result.truncated = True); rates num/den (den 0 -> None); top_n folding (additive measures summed
    # into "__other__", others None); time-series bucket fill (zero rows for empty buckets of the FIRST date
    # dimension when it is the only dimension or the series dimension is another one); sort (definition.sort, else
    # first date dimension asc, else first measure desc); limit; row_cap (default reporting.max_result_rows) ->
    # truncated; totals via extra compiled queries with group_by [] / [0] / [1]; compare_previous -> the same
    # totals for the window shifted back by its own length ("previous"; whole local days shift by their day
    # count, exact UTC bounds by end - start); labels via resolve_labels; facts status; store in cache.
    # row_count = rows after `limit`, before the row cap. Folded "__other__" rows: count/count_if/sum summed,
    # rates from summed num/den, percentiles from the merged samples, everything else None; top-n ranks by the
    # first measure when it adds up, else by the row count. Zero-bucket fill skips stock (snapshot) dimensions.
@dataclass(frozen=True, slots=True)
class RunPlan:
    compiled: CompiledReport
    cache_key: str
    # + definition (as given), params (incl. the state row's last_snapshot_date), scope, facts, row_cap,
    #   compare_previous, now, near_miss_percent, registry -- what execute_plan and the totals queries need
async def plan_report(session: AsyncSession, *, defn: ReportDefinition, params: RunParams, user_id: int,
                      reporting: ReportingSettings, row_cap: int | None = None, compare_previous: bool = False,
                      now: datetime | None = None, registry: Registry | None = None,
                      include_archived: bool = False) -> RunPlan
    # what run_report does first (scope, last_snapshot_date, compile, cache key); the API uses it for the cache
    # lookup and estimate_rows before deciding inline vs. job
async def raw_measure_values(session: AsyncSession, *, defn: ReportDefinition, measure_key: str,
                             params: RunParams, user_id: int, reporting: ReportingSettings,
                             now: datetime | None = None, registry: Registry | None = None,
                             include_archived: bool = False, budget: QueryBudget | None = None) -> list[float]
    # the percentile value list of one measure (used by the /stats SLA wrapper), capped at PERCENTILE_SAMPLE_CAP

# backend/src/tiqora/reporting/labels.py
async def resolve_labels(session: AsyncSession, source: LabelSource, keys: Iterable[str | int]) -> dict[str, str]
    # queue -> queue.name; state -> ticket_state.name; priority -> ticket_priority.name; type -> ticket_type.name;
    # service -> service.name; sla -> sla.name; user -> "First Last" (login when empty);
    # customer_company -> customer_company.name (keys loaded as IN-list, string compare on one table);
    # customer_user -> "First Last" from customer_user by login; ticket -> "<tn> <title>"; none -> {}.
    # Result keys are str(key); missing keys map to str(key). Queue HIDDEN_QUEUE_ID (-1) -> "Andere Queue",
    # never looked up.
```

R8b — cache:

```python
# backend/src/tiqora/reporting/cache.py
CACHE_PREFIX: Final = "tiqora:report:v1:"
class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ex: int) -> Any: ...
class ReportCache:
    def __init__(self, backend: CacheBackend | None, ttl_seconds: int) -> None
    @staticmethod
    def key_for(*, definition_hash: str, queue_ids: Iterable[int], window: TimeWindow | None, time_zone: str,
                row_cap: int, compare_previous: bool, include_archived: bool, extra_filter: Filter | None,
                last_snapshot_date: date | None, near_miss_percent: int) -> str
        # CACHE_PREFIX + sha256(json of: hash, sorted queue ids, window start/end iso, zone, row_cap, compare_previous,
        # CompiledReport.include_archived, sha256 of extra_filter, last_snapshot_date iso, near_miss_percent);
        # every argument is required (no caller can forget a scope input)
    async def get(self, key: str) -> ReportResultOut | None       # backend errors -> None + log warning
    async def put(self, key: str, result: ReportResultOut) -> None  # ttl 0 or backend None -> no-op; errors logged
def cache_backend_from_settings(settings: Settings) -> CacheBackend   # redis.asyncio.from_url(settings.redis_url, decode_responses=True)
```

The definition hash covers the EFFECTIVE definition (choices, grain and parameter values applied), so different runtime values never share an entry; the queue-id set makes the cache per permission scope.

### R9. API (Tasks 16, 8)

```python
# backend/src/tiqora/reporting/schemas.py
Cell = str | int | float | None
ColumnType = Literal["id", "string", "enum", "date", "hour", "weekday", "count", "number", "minutes",
                     "hours", "days", "units", "percent"]
class ResultColumnOut(BaseModel):
    key: str; role: Literal["dimension", "measure"]; type: ColumnType; label_key: str
    grain: Grain | None = None; i18n_values: str | None = None
class ReportTotalsOut(BaseModel):
    grand: list[Cell] | None = None                       # aligned with columns; dimension cells None
    rows: dict[str, list[Cell]] = Field(default_factory=dict)      # first-dimension value -> row
    columns: dict[str, list[Cell]] = Field(default_factory=dict)   # second-dimension value -> row
class TimeWindowOut(BaseModel):
    start: UtcDateTime; end: UtcDateTime; start_date: date; end_date: date; time_zone: str
class FactsStatusOut(BaseModel):
    ready: bool; as_of: UtcDateTime | None
    backfill_status: Literal["pending", "running", "done"]
    backfill_job_id: int | None; backfill_progress: float | None   # 0..1
class ReportResultOut(BaseModel):
    report_key: str | None; columns: list[ResultColumnOut]; rows: list[list[Cell]]
    labels: dict[str, dict[str, str]]                     # column key -> str(value) -> label
    totals: ReportTotalsOut | None; previous: ReportTotalsOut | None
    row_count: int; truncated: bool; time_window: TimeWindowOut | None
    generated_at: UtcDateTime; cached: bool; facts: FactsStatusOut
class ReportRunIn(BaseModel):
    report_key: str | None = None
    definition: ReportDefinition | None = None            # exactly one of report_key / definition (422 otherwise)
    time_range: TimeRange | None = None
    grain: Grain | None = None
    time_zone: str | None = None
    parameters: dict[str, list[str | int] | str | int | bool | None] = Field(default_factory=dict)
    dimension_choices: dict[str, str] = Field(default_factory=dict)   # "0" -> dimension key
    compare_previous: bool = False
    no_cache: bool = False
    force_job: bool = False
class ReportRunOut(BaseModel):
    result: ReportResultOut | None = None
    job: JobAccepted | None = None
class ReportExportIn(ReportRunIn):
    format: Literal["xlsx", "csv"] = "xlsx"
    title: str = Field("Bericht", min_length=1, max_length=120)
    column_labels: dict[str, str] = Field(default_factory=dict)          # frontend-translated headers
    value_labels: dict[str, dict[str, str]] = Field(default_factory=dict)   # frontend-translated enum values
    totals_label: str = Field("Summe", max_length=40)
    info_lines: list[str] = Field(default_factory=list, max_length=20)   # "Zeitraum: …" etc. for the info sheet
class BuiltinReportOut(BaseModel):
    key: str; category: str; title_key: str; description_key: str
    definition: ReportDefinition; overview: bool; read_only: bool = True
class ReportCatalogueOut(BaseModel):
    reports: list[BuiltinReportOut]; facts: FactsStatusOut
class SourceDimensionOut(BaseModel):
    key: str; label_key: str; kind: str; labels: str; i18n_values: str | None
    filter_type: str | None; options_source: str | None; time_field: bool
class SourceMeasureOut(BaseModel):
    key: str; label_key: str; agg: str; unit: str
class ReportSourceOut(BaseModel):
    key: str; label_key: str; dimensions: list[SourceDimensionOut]; measures: list[SourceMeasureOut]
    filter_fields: list[SourceDimensionOut]; default_time_field: str | None
class ReportingStatusOut(FactsStatusOut):
    last_history_id: int; history_lag: int; gap_count: int; dirty_count: int
    last_snapshot_date: date | None; snapshot_time_zone: str | None
    fact_rows: int; event_rows: int
class ReportBackfillIn(BaseModel):
    since: date | None = None; restart: bool = True; snapshots: bool = True
```

Routes (`backend/src/tiqora/api/v1/reports.py`: `router = APIRouter(prefix="/reports", tags=["reports"])`, `sources_router = APIRouter(prefix="/reporting", tags=["reports"])`; all `CurrentUser` + `require_report_access`):

```
GET  /api/v1/reports                -> ReportCatalogueOut
GET  /api/v1/reports/{report_key}   -> BuiltinReportOut                     (404 unknown)
POST /api/v1/reports/run            -> 200 ReportRunOut{result} | 202 ReportRunOut{job}
POST /api/v1/reports/export         -> 202 JobAccepted
GET  /api/v1/reporting/sources      -> list[ReportSourceOut]
GET  /api/v1/reporting/status       -> FactsStatusOut
```

`POST /reports/run`: resolve definition (`report_key` → `BUILTIN_REPORTS`; inline `definition` allowed — phase 2's builder preview uses it; scope is injected by the compiler, so this is safe); zone = `body.time_zone` → `definition.time_zone` → `resolve_user_time_zone`; `plan_report`; cache hit (unless `no_cache`) → 200; `force_job` OR `estimate_rows > reporting.inline_row_threshold` OR (source `ticket_events` AND window longer than 366 days) → `enqueue_job("core.report_run", ReportRunPayload(user_id, run=body), created_by=user.id)`, commit, 202; else `run_report` → 200. Errors: `DefinitionError`/`CompileError`/`FilterError` → 422 `{"message", "path"}`; `ReportQueryTimeout` → 504 `{"message": "Der Bericht hat zu lange gebraucht. Bitte den Zeitraum verkleinern oder ihn als Export starten."}`. Report keys are strings (`builtin.<name>`); phase 2 saved reports get numeric ids on the same paths.

Admin (`backend/src/tiqora/api/v1/admin/reporting.py`, `prefix="/reporting"`, `tags=["admin:reporting"]`, `AdminUser`):

```
GET  /api/v1/admin/reporting/status    -> ReportingStatusOut
POST /api/v1/admin/reporting/backfill  -> 202 JobAccepted   (body ReportBackfillIn; dedupe_key "report_backfill"; sets state.backfill_job_id)
```

Access (`tiqora/reporting/access.py`): `async def require_report_access(session, user_id: int, reporting: ReportingSettings) -> None` (user id + loaded settings: the jobs only have a user id; `has_report_access` returns the bool) — `access == "all_agents"` → ok; `"stats_group"` → admin, or `ro` (incl. `rw`) on the valid group named `stats` (`PermissionEngine.groups_for_permission` + `PermissionGroups` lookup by name) → else `HTTPException(403, {"message": "Für Berichte fehlt die Berechtigung (Gruppe „stats“)."})`. The `/api/v1/stats/*` wrappers apply the same check.

Jobs (`tiqora/reporting/jobs.py`):

```python
class ReportRunPayload(BaseModel):
    user_id: int; run: ReportRunIn
class ReportExportPayload(BaseModel):
    user_id: int; export: ReportExportIn
REPORT_RUN_JOB = JobHandler(kind="core.report_run", payload_model=ReportRunPayload, run=run_report_job,
    title_key="jobs.kinds.core.report_run", timeout_seconds=1800)        # returns ReportResultOut.model_dump(mode="json")
REPORT_EXPORT_JOB = JobHandler(kind="core.report_export", payload_model=ReportExportPayload, run=run_export_job,
    title_key="jobs.kinds.core.report_export", timeout_seconds=3600)     # file "<slug(title)>.xlsx|.csv"; returns {"rows": n}
```

Both re-evaluate the scope at run time (`ticket_query_scope` of `payload.user_id`) and re-check `require_report_access` semantics (a user who lost access gets `JobFailed("Für Berichte fehlt die Berechtigung.")`). The run job writes the cache entry, so a repeated request is answered inline.

As built (Task 16):
- `ReportRunIn` has `extra="forbid"`, so the internal `RunParams` fields `window_override`, `extra_filter` and `last_snapshot_date` are rejected with 422. `ReportExportIn` inherits this.
- `include_archived` is not a request field, so runs never include archived tickets.
- The shared `prepare_run(session, user_id, body)` lives in `tiqora/reporting/jobs.py` and is used by the API and both jobs. It raises `RequestError(message, path, status_code)`; the API turns that into 422, or 404 for an unknown `report_key`.
- Time zone: the request's zone, then the definition's, then `resolve_user_time_zone`. `dimension_choices` keys must be ints; anything else gives 422 with `path="dimension_choices.<key>"`.
- Cache: the `get_report_cache` dependency returns a `CacheBackend` (the app's Redis client), and the route wraps it as `ReportCache(backend, reporting.cache_seconds)`. The jobs build theirs from `cache_backend_from_settings` (as imported into `reporting.jobs`), with the same key builder.
- Inline run: one `QueryBudget.inline` is shared by `estimate_rows` and `run_report`. The session is rolled back before `enqueue_job`, so on PostgreSQL the estimate's `SET LOCAL statement_timeout` never applies to the job insert.
- The run job uses job mode and fails with "… als Export starten" when its result exceeds `MAX_RESULT_CHARS`.
- `POST /reports/export` validates the request through `plan_report` (scope and compile only), so an invalid request gets 422 right away. No report query runs in the request.
- `ReportSourceOut.filter_fields` maps a filter-only field as: `kind` = `filter_type`, `labels` "none", `i18n_values`/`options_source` None, `time_field` false.
- The wave-0 filter models (`Filter`, `AllOf`, `AnyOf`, `Not`) set `json_schema_mode_override="validation"`. Without it, the recursive models get split into `-Input`/`-Output` OpenAPI schemas as soon as `BuiltinReportOut.definition` uses them in a response, and that renames `Filter` and `ReportDefinition` in `schema.d.ts`.

### R10. Export (Task 15)

```python
# backend/src/tiqora/reporting/export.py
@dataclass(frozen=True, slots=True)
class XlsxColumn:
    header: str; type: ColumnType; width: float | None = None
@dataclass(frozen=True, slots=True)
class XlsxSheet:
    name: str                                       # sanitised: []:*?/\ removed, ≤ 31 chars, unique per workbook
    columns: Sequence[XlsxColumn]
    rows: Iterable[Sequence[Cell | date]]
    totals: Sequence[Cell] | None = None
    totals_label: str = "Summe"
    info_lines: Sequence[str] = ()                  # written to an "Info" sheet when non-empty
def write_xlsx_workbook(sheets: Sequence[XlsxSheet]) -> bytes
    # openpyxl Workbook(write_only=True); header row bold with surface fill; freeze_panes "A2"; column widths
    # (explicit or min(60, max(10, longest text + 2)) over the first 500 rows); number formats:
    # count/id "0", number/hours/days/units "0.00", minutes "0", percent "0.0%", date "yyyy-mm-dd";
    # totals row bold; date cells as real dates; one sheet per XlsxSheet (phase 3 bundles reuse this)
def report_sheet(result: ReportResultOut, *, title: str, column_labels: Mapping[str, str],
                 value_labels: Mapping[str, Mapping[str, str]], totals_label: str,
                 info_lines: Sequence[str]) -> XlsxSheet
    # dimension cells -> value_labels[col][v] or result.labels[col][v] or v; "__other__" -> value_labels or "Weitere"
def write_report_csv(result: ReportResultOut, *, column_labels: Mapping[str, str],
                     value_labels: Mapping[str, Mapping[str, str]], totals_label: str) -> bytes
    # BOM + ";" delimiter like api/v1/stats.py; numbers machine-readable with "." decimal point, rates as fractions 0..1
```

### R11. Built-in catalogue v1 (Task 14)

```python
# backend/src/tiqora/reporting/catalogue.py
ReportCategory = Literal["overview", "volume", "backlog", "sla", "quality", "agents", "time", "customers", "patterns"]
@dataclass(frozen=True, slots=True)
class BuiltinReport:
    key: str                     # "builtin.<name>"
    category: ReportCategory
    definition: ReportDefinition
    overview: bool = False       # used by the overview section of /agent/reports
    @property
    def title_key(self) -> str          # f"reports.builtin.{name}.title"
    @property
    def description_key(self) -> str    # f"reports.builtin.{name}.description"
BUILTIN_REPORTS: ExtensionPoint[BuiltinReport]   # key "core.reporting.builtin_reports", item key .key
def core_builtin_reports() -> list[BuiltinReport]
```

| key (`builtin.` +) | category | source | dimensions (grain / choices / top_n) | measures | viz | time range | notes |
|---|---|---|---|---|---|---|---|
| `ticket_kpis` | overview | tickets | — | tickets, closed_tickets, response_breach_rate, first_response_p50, solution_p50 | kpi | day 30 incl. current | overview |
| `volume` | volume | ticket_events | occurred(week), kind | events | line, series_dimension 1 | week 12 | filter kind in [create, close]; overview |
| `backlog_trend` | backlog | daily_snapshot | snapshot(day) | open_tickets | line | day 30 incl. current | overview |
| `open_now` | backlog | open_tickets | queue (choices state, priority, owner, type, service; top 15) | tickets | bar | — | overview |
| `agent_activity` | agents | ticket_events | actor (top 25), kind | events | pivot, totals rows+grand | day 30 incl. current | filter kind in [reply, note, close]; overview |
| `volume_by_channel` | volume | tickets | created(week), channel | tickets | stacked_bar, series 1 | week 12 | |
| `channel_mix` | volume | tickets | channel | tickets | donut | day 30 incl. current | |
| `volume_by_queue` | volume | tickets | queue (choices type, service, priority; top 15) | tickets, closed_tickets | bar, chart_measures [0] | day 30 incl. current | |
| `backlog_by_state` | backlog | daily_snapshot | snapshot(day), state_type | open_tickets | stacked_bar, series 1 | day 30 incl. current | |
| `backlog_aging` | backlog | open_tickets | queue (top 15) | age_0_1d, age_1_3d, age_3_7d, age_7_30d, age_30d_plus | stacked_bar (measures as series) | — | |
| `sla_compliance` | sla | tickets | queue (choices service, sla, customer, type, priority) | tickets, sla_met_rate, response_breach_rate, solution_breach_rate, response_near_misses, solution_near_misses, update_breaches | table, totals grand | month 1 | |
| `sla_times` | sla | tickets | created(week) | first_response_p50, first_response_p90, solution_p50, solution_p90 | line | week 12 | |
| `sla_by_customer` | sla | tickets | customer (top 20) | tickets, sla_met_rate, response_breaches, solution_breaches, first_response_p50, solution_p50 | table | month 3 | |
| `first_contact_resolution` | quality | tickets | queue (choices type, channel, service) | closed_tickets, fcr_rate, reopen_rate | table, totals grand | month 1 | time_field closed |
| `reopen_trend` | quality | tickets | closed(week) | reopen_rate, reopened_tickets, closed_tickets | line, chart_measures [0] | week 12 | time_field closed |
| `queue_transfers` | quality | ticket_events | from_queue, to_queue | events, tickets | pivot, totals rows+columns+grand | day 30 incl. current | filter kind eq queue |
| `pingpong` | quality | tickets | queue (top 15) | tickets, pingpong_tickets, queue_moves_avg | table | day 30 incl. current | |
| `time_in_state` | quality | ticket_events | from_state | events, dwell_p50_hours, dwell_p90_hours, dwell_avg_hours | bar, chart_measures [1] | day 30 incl. current | filter kind eq state |
| `time_by_customer` | time | accounted_time | customer (top 25) | time_units, bookings, tickets | bar, chart_measures [0], totals grand | month 1 | |
| `time_by_queue` | time | accounted_time | queue (top 25) | time_units, bookings, tickets | bar, chart_measures [0], totals grand | month 1 | |
| `time_by_agent` | time | accounted_time | agent (top 25) | time_units, bookings, tickets | bar, chart_measures [0], totals grand | month 1 | |
| `time_by_type` | time | accounted_time | type | time_units, bookings, tickets | bar, chart_measures [0], totals grand | month 1 | |
| `time_by_agent_month` | time | accounted_time | agent, booked(month) | time_units | pivot, totals rows+columns+grand | month 12 incl. current | Znuny `TicketAccountedTime` parity |
| `top_customers` | customers | tickets | customer (top 20) | tickets, closed_tickets, solution_p50, accounted_time | bar, chart_measures [0] | month 3 | |
| `created_heatmap` | patterns | tickets | created_weekday, created_hour | tickets | heatmap | week 12 | |

"day 30 incl. current" = `RelativeTimeRange(unit="day", count=30, include_current=True)`; "week 12" = `unit="week", count=12` (complete weeks); "month 1" = last complete month. Every built-in with a `queue`-scoped source gets the parameter `ReportParameter(key="queues", field="queue" (accounted_time: "queue"; ticket_events: "ticket_queue"), op="in", label_key="reports.parameters.queues")`. All built-ins carry `time_zone=None` (viewer's zone).

### R12. `/api/v1/stats/*` wrappers (Task 17)

`api/v1/stats.py` keeps every route, query parameter and response model unchanged. `StatsService` keeps its public methods and dataclasses; bodies delegate to `run_report` with `RunParams(window_override=TimeWindow from StatsFilters (exact UTC bounds, end exclusive = date_to + 1 µs), time_zone=filters.time_zone, extra_filter=state/priority/type/customer conditions, parameters={"queues": [queue_id]} when set)`. The `extra_filter` field names depend on the source: `tickets` and `open_tickets` → `state`, `priority`, `type`, `customer`; `ticket_events` → `ticket_state`, `ticket_priority`, `ticket_type`, `customer` (all current ticket values, as the old code filtered on the current `ticket` row). Every wrapper accepts every `StatsFilters` field.

| method | built-in / query | mapping |
|---|---|---|
| `ticket_volume` | `builtin.volume`, grain override | rows (bucket, kind, events) → `VolumePoint(bucket, created, closed)`; only buckets with data (old behaviour) |
| `backlog_trend` | `builtin.volume` rows (same call as `ticket_volume`) → running `created − closed` per bucket, floored at 0 (the old semantics, now with correct closes; `daily_snapshot` cannot filter by state/type/customer). The new UI uses the snapshot-based `builtin.backlog_trend`. | `BacklogPoint(bucket, open_count)` |
| `open_snapshot` | `builtin.open_now`, `dimension_choices={"0": dimension}` (`queue`/`state`/`priority`/`owner`) | `DimensionCount(id, label, count)`, count desc |
| `sla_stats` | `builtin.ticket_kpis`-like inline definition on `tickets` (measures tickets, response_breaches, update_breaches → count_if `update_breach_count > 0` via filter, solution_breaches) + `open_tickets` `escalated` with filter `created` in window + `raw_measure_values` for `first_response_clock_p50` / `solution_clock_p50` | `SlaStats`; `escalated` = currently escalated open tickets created in the window (as before); breach counts now come from history (no longer from the zeroed `escalation_*` columns of closed tickets) |
| `agent_workload` | `builtin.open_now` with owner choice + inline `tickets` definition dims [owner] measure closed_tickets, time_field `closed` | `AgentWorkloadItem` (login/name via the existing `Users` query, invalid users excluded as today) |

### R13. Frontend

```ts
// frontend/src/lib/reportsApi.ts
export type ReportDefinition = Schemas["ReportDefinition"];
export type ReportResult = Schemas["ReportResultOut"];
export type ReportRunIn = Schemas["ReportRunIn"];
export type ReportRunOut = Schemas["ReportRunOut"];
export type ReportExportIn = Schemas["ReportExportIn"];
export type BuiltinReport = Schemas["BuiltinReportOut"];
export type ReportCatalogue = Schemas["ReportCatalogueOut"];
export type FactsStatus = Schemas["FactsStatusOut"];
export type ReportingStatus = Schemas["ReportingStatusOut"];
export type ReportCell = string | number | null;
export const reportsApi = {
  catalogue(signal?: AbortSignal): Promise<ReportCatalogue>,           // GET /api/v1/reports
  get(key: string, signal?: AbortSignal): Promise<BuiltinReport>,     // GET /api/v1/reports/{key}
  run(body: ReportRunIn, signal?: AbortSignal): Promise<ReportRunOut>, // POST /api/v1/reports/run (200 and 202 share the body)
  export(body: ReportExportIn): Promise<JobAccepted>,                 // POST /api/v1/reports/export
  factsStatus(signal?: AbortSignal): Promise<FactsStatus>,            // GET /api/v1/reporting/status
  adminStatus(signal?: AbortSignal): Promise<ReportingStatus>,        // GET /api/v1/admin/reporting/status
  adminBackfill(body: Schemas["ReportBackfillIn"]): Promise<JobAccepted>,
};

// frontend/src/components/reports/useReportRun.ts
export function useReportRun(body: ReportRunIn | null, opts?: { enabled?: boolean }): {
  result: ReportResult | undefined; jobId: number | null; isLoading: boolean; error: unknown; refetch: () => void };
  // react-query key ["reports", "run", body]; when the POST answers {job}, polls useJob(job.job_id) and returns
  // job.result (as ReportResult) once succeeded; job failed -> error = new Error(job.error)

// frontend/src/components/reports/reportFormat.ts
export function cellLabel(result: ReportResult, columnKey: string, value: ReportCell, t: TFunction): string;
  // labels map -> i18n_values (`${i18n_values}.${value}`) -> "__other__" t("reports.other") -> date/hour/weekday formatting -> String(value)
export function formatMeasure(value: ReportCell, type: ReportResult["columns"][number]["type"], locale: string): string;
  // percent -> "12,5 %"; minutes -> formatDuration (< 60 "45 min", < 1440 "3,5 h", else "2,1 T"); units -> 2 decimals; null -> "—"
export type SeriesData = { key: string; label: string; values: (number | null)[] };
export function toSeries(result: ReportResult, viz: ReportDefinition["visualization"], t: TFunction):
  { categories: { key: string; label: string }[]; series: SeriesData[] };
  // categories = distinct values of the non-series dimension (or first dimension), series = values of
  // series_dimension, or one series per plotted measure when series_dimension is null
export function exportLabels(result: ReportResult, t: TFunction): Pick<ReportExportIn, "column_labels" | "value_labels" | "totals_label">;

// frontend/src/components/agent/stats/chartColors.ts
export const SERIES_SLOTS = 6;
export function seriesColor(slot: number): string;            // `var(--color-series-${slot + 1})`, slot >= 6 -> "var(--color-muted)" ("Weitere")
export function assignSeriesSlots(keys: readonly string[], fixedOrder?: readonly string[]): Map<string, number>;
  // fixedOrder (enum values, e.g. channel order) wins -> filtering never repaints enum series; else keys sorted
  // ascending (numeric ids numerically) -> colour follows the key order, never the value rank
export function sequentialColor(t: number): string;           // 0..1 -> var(--color-seq-1..7)

// chart components (all: testId prop, emptyLabel, role="img" + aria-label, hover tooltip via ChartTooltip)
LineChart({ labels, series: LineSeries[], area?: boolean, height?, emptyLabel?, testId?, formatValue? })   // existing props kept
BarChart({ data: BarDatum[], color?, height?, emptyLabel?, testId?, formatValue? })                      // existing props kept
StackedBarChart({ categories: {key,label}[], series: {key,label,color,values:(number|null)[]}[], height?, emptyLabel?, testId?, formatValue? })
DonutChart({ data: {key,label,value,color}[], centerLabel?: string, size?, emptyLabel?, testId?, formatValue? })
HeatmapChart({ rows: {key,label}[], columns: {key,label}[], values: (number|null)[][], emptyLabel?, testId?, formatValue? })
Sparkline({ values: number[], color?, width?, height?, testId? })
KpiTile({ label, value: string, delta?: { value: number; direction: "up" | "down" | "flat"; goodWhen: "up" | "down" }, sparkline?: number[], hint?, testId? })
ChartTooltip({ x, y, title, rows: {label,value,color?}[] })      // absolutely positioned in a relative container
ChartLegend({ items: {key,label,color}[], testId? })

// frontend/src/components/reports/ReportView.tsx
export function ReportView(props: { report: { key: string | null; definition: ReportDefinition };
  result: ReportResult; testId?: string /* default "report-view" */; compact?: boolean }): JSX.Element;
  // dispatch on definition.visualization.type; "Tabelle anzeigen"/"Diagramm anzeigen" toggle
  // (testid `${testId}-toggle-table`); table/pivot always available
```

Routes: `/agent/reports` (`ReportsPage`), `/agent/reports/$reportKey` (`ReportViewPage`, search params `{from?: "YYYY-MM-DD", to?: "YYYY-MM-DD", preset?: string, grain?: Grain, choice?: string, queues?: string /* comma ids */}`), `/agent/stats` → `redirect({ to: "/agent/reports" })`, `/admin/reporting` (`ReportingAdminPage`). AgentShell nav item "Berichte" (`agent-nav-stats` testid kept) points to `/agent/reports`.

Colour tokens (`themes/tokens.css`, validated in the session that wrote this plan with the dataviz validator against Tiqora's surfaces `#ffffff` light / `#151821` dark — all hard gates PASS; light has a contrast WARN for slots 3–5 → table view + visible labels are required):

| token | light | dark |
|---|---|---|
| `--color-series-1` | `#2a78d6` | `#3987e5` |
| `--color-series-2` | `#eb6834` | `#d95926` |
| `--color-series-3` | `#1baf7a` | `#199e70` |
| `--color-series-4` | `#eda100` | `#c98500` |
| `--color-series-5` | `#e87ba4` | `#d55181` |
| `--color-series-6` | `#4a3aa7` | `#9085e9` |
| `--color-seq-1 … --color-seq-7` (heatmap, both modes) | `#cde2fb #9ec5f4 #6da7ec #3987e5 #256abf #184f95 #0d366b` | same |

### R14. i18n namespaces (German first; English in `en.json`)

- `reports.{title,subtitle,overview,catalogue,allReports,back,empty,loading,error,other,showTable,showChart,export,exportXlsx,exportCsv,exportTitle,truncated,cached,totals,previousPeriod,timeRange,grain,groupBy,custom,from,to,apply,timeZone,factsBuilding,factsBuildingProgress,factsAsOf,timeout}`
- `reports.categories.{overview,volume,backlog,sla,quality,agents,time,customers,patterns}`
- `reports.builtin.<name>.{title,description}` for all 25 built-ins (R11)
- `reports.dimensions.<key>` and `reports.measures.<key>` for every dimension/measure key of the five sources; `reports.parameters.queues`
- `reports.values.state_type.{new,open,closed,pending reminder,pending auto,removed,merged}`, `reports.values.channel.{email,phone,internal,telegram,webchat,sms,whatsapp}`, `reports.values.event_kind.<kind>` (16), `reports.values.weekday.{1..7}`
- `reports.presets.*` reuse the existing `stats.presets.*` texts under the new namespace; `reports.grains.{day,week,month,quarter,year}`
- `jobs.kinds.core.{report_run,report_export,report_backfill}` = "Bericht" / "Berichtsexport" / "Berichtsdaten aufbauen"
- `admin.reporting.{title,subtitle,ready,building,pending,asOf,lag,gaps,dirty,lastSnapshot,factRows,eventRows,rebuild,rebuildSince,rebuildConfirm,settings}`
- `admin.settings.reporting.{title,fields.<field>.label,fields.<field>.help,fields.access.options.{all_agents,stats_group}}`
- `admin.daemons.services.report_facts.{name,description}` = "Berichtsdaten" / "Wertet neue Ticketänderungen für die Berichte aus und schreibt die Tagesstände."
- `admin.home.reporting.{title,description}` (tile)

### Deviations from spec

1. **Daily snapshot loop.** Spec: `_daily_loop` at 00:05 report time. `_daily_loop` only knows UTC `HH:MM`, and a missed run would leave a hole. Instead the `report_facts` interval loop fills every complete local day since `last_snapshot_date` (DST-correct, self-healing). Snapshots are computed from facts + events ("as of the end of day d"), not read from live `ticket` rows, so the daily value and the backfilled history come from one code path.
2. **No `CONVERT_TZ`/`DATE_FORMAT`.** Spec names `DATE_FORMAT`/`STR_TO_DATE` for MySQL. `date_bucket` uses `DATE`/`WEEKDAY`/`DAYOFMONTH`/`MAKEDATE` (no `%` literals that pyformat drivers would need doubled), and time zones are applied by piecewise UTC-offset shifting (`to_local`) because MariaDB's named-zone tables are usually not loaded.
3. **`GET /api/v1/report-runs/{id}` is not built.** Runs poll the wave-0 `GET /api/v1/jobs/{id}`; `tiqora_report_run` (history of scheduled runs + files) belongs to phase 3 with scheduling. Run endpoint is `POST /api/v1/reports/run` with `report_key` or an inline definition (needed by phase 2's builder preview) instead of `POST /reports/{id}/run`.
4. **Extra source `open_tickets`** (live on `ticket`) — the spec's "compiled queries for now data"; needed for open-now, aging and workload reports that must not depend on the fact loop.
5. **Built-ins are code, not migration rows; no `tiqora_report` table yet** (phase 2). Access to the new reports defaults to "all agents" (today's `/stats` behaviour); the Znuny-parity gate "group `stats`" is the setting `reporting.access = stats_group`. Reason: stock Znuny gives `stats` only to admins, so gating by default would remove reports from every agent on upgrade.
6. **Calendar history.** SysConfig has no history, so working minutes use the calendar valid at build time (resolves the spec's open point: "evaluate with the calendar valid at the time" is not feasible). A calendar change affects only tickets rebuilt afterwards; an admin can rebuild everything (`/admin/reporting`).
7. **Working minutes without working hours.** Znuny's `FirstResponseInMin`/`SolutionInMin` are 0 when no working hours are configured; facts store `NULL` there and breaches fall back to wall-clock minutes, so P50 values are never a misleading 0.
8. **Labels for exports come from the frontend** (`ReportExportIn.column_labels/value_labels`): there is no server-side i18n catalogue, and duplicating 49 locales server-side is not worth it in phase 1. Phase 3 scheduled exports (no browser) will need a server-side label set — recorded there.
9. **Not in phase 1:** dynamic fields as dimensions (`df.<Name>` keys reserved; needs the "reportable" side table), `articles`/`ai_usage` sources, AI impact and mail-delivery reports (phase 4), the read replica URL (still open; `run_report` takes a session, so a second engine can be passed later).
10. ~~**`TimeAccounting` history parity fix** (Task 6): Tiqora's `add_time_accounting` writes no `TimeAccounting` history row and does not touch `ticket.change_time`; Znuny's `TicketAccountTime` does both. Fixed here because the facts' `accounted_time` is refreshed through history rows.~~ Struck ([R-T6]): `add_time_accounting` on main already does both.

---
## Tasks

Order: backend Tasks 1→17 are sequential (each builds on the committed previous ones). Frontend Tasks 18→21 need the regenerated `openapi.json` from Tasks 8 and 16 (18 first; 19 is independent of 18 and may run in parallel with it; 20 needs 18+19; 21 needs 18). Task 22 closes. Backend-only: 1–17. Frontend-only: 18–21.

### Task 1: Tables, ORM, migration, reporting settings section, state row (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_reporting_facts.py` (revision = next number after the current head; `down_revision` = current head — check, do not copy an id from here)
- Modify: `backend/src/tiqora/db/tiqora/models.py` (five ORM classes of R1)
- Create: `backend/src/tiqora/reporting/__init__.py`, `backend/src/tiqora/reporting/settings.py`, `backend/src/tiqora/reporting/state.py` (only `STATE_NAME`, `load_state` in this task; `facts_status` comes in Task 8)
- Modify: `backend/src/tiqora/extensions/core.py` (`registry.add(SETTINGS_SECTIONS, REPORTING_SECTION)` + `validate_section_schema`), `backend/src/tiqora/domain/settings_store.py` (`KEY_REPORT_FACTS_ENABLED`, `KEY_REPORT_FACTS_INTERVAL_SECONDS`), `backend/tests/test_migration_gate.py`, `backend/tests/test_migrate_cli.py`, `backend/tests/test_tiqora_model_ddl.py`
- Test: `backend/tests/test_reporting_tables.py` (both dialects, band 47000–47049)

**Interfaces:**
- Consumes: wave-0 `SettingsSection`, `SETTINGS_SECTIONS`, `validate_section_schema`, `register_core`, `utc_now`.
- Produces: R1 tables + ORM (`TiqoraReportTicketFact`, `TiqoraReportTicketEvent`, `TiqoraReportDailySnapshot`, `TiqoraReportState`, `TiqoraReportDirtyTicket`), R2 (`ReportingSettings`, `REPORTING_SECTION`, `load_reporting_settings`, `reporting_time_zone`, the two `KEY_REPORT_FACTS_*` constants), `STATE_NAME`, `load_state(session, *, for_update=False) -> TiqoraReportState`.

- [ ] **Step 1: Write the failing tests.** Append to `test_tiqora_model_ddl.py`:
  ```python
  def test_report_fact_columns_ddl() -> None:
      from tiqora.db.tiqora.models import TiqoraReportTicketFact, TiqoraReportDailySnapshot
      assert "NUMERIC(12, 2)" in _column_ddl(TiqoraReportTicketFact.__table__, "accounted_time", postgresql.dialect())
      assert "DECIMAL(12, 2)" in _column_ddl(TiqoraReportTicketFact.__table__, "accounted_time", mysql.dialect())
      pk = [c.name for c in TiqoraReportDailySnapshot.__table__.primary_key.columns]
      assert pk == ["snapshot_date", "queue_id", "state_type", "priority_id"]
      from tiqora.db.tiqora.models import TiqoraReportState
      assert "MEDIUMTEXT" in _column_ddl(TiqoraReportState.__table__, "gap_ids", mysql.dialect())
  ```
  `test_reporting_tables.py`:
  ```python
  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_state_row_is_created_once(url_fixture: str, request: pytest.FixtureRequest) -> None:
      # create_all; async with factory() as s: st = await load_state(s); await s.commit()
      # st.name == "facts", st.last_history_id == 0, st.backfill_status == "pending", st.updated_at naive
      # second load_state returns the same row (count of tiqora_report_state rows == 1)
      # cleanup: DELETE FROM tiqora_report_state
  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_fact_and_snapshot_round_trip(url_fixture: str, request: pytest.FixtureRequest) -> None:
      # insert one TiqoraReportTicketFact (ticket_id 47001, accounted_time Decimal("1.25"), every NOT NULL column set)
      # and one TiqoraReportDailySnapshot (date(2024, 3, 31), queue 47001, "open", 3, counts); read back equal;
      # delete both rows in the test (module fixture deletes ticket_id/queue_id in 47000..47049 as safety net)
  def test_reporting_section_is_registered_and_valid() -> None:
      reg = Registry(); register_core(reg)
      section = reg.get(SETTINGS_SECTIONS, "reporting")
      assert section is not None and section.model is ReportingSettings
      validate_section_schema(section)
      assert ReportingSettings().access == "all_agents"
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_tables.py tests/test_tiqora_model_ddl.py` → FAIL (`ImportError`).
- [ ] **Step 3: Migration** — `op.create_table` for the five tables exactly as R1 (`sa.Numeric(12, 2)`, booleans `server_default=sa.false()`, `sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")` for `gap_ids`, indexes with the R1 names); `downgrade` drops the five tables in reverse order. Update `TIQORA_HEAD` and the `version ==` assertion. Run `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py` → PASS.
- [ ] **Step 4: Implement** ORM classes (same names/types/indexes as the migration; `fact_version` `SmallInteger`), `reporting/settings.py` (R2 verbatim; `reporting_time_zone` uses `tiqora.domain.timezones.valid_time_zone` / `default_time_zone`), `state.py`:
  ```python
  async def load_state(session: AsyncSession, *, for_update: bool = False) -> TiqoraReportState:
      stmt = select(TiqoraReportState).where(TiqoraReportState.name == STATE_NAME)
      if for_update:
          stmt = stmt.with_for_update()
      row = (await session.execute(stmt)).scalar_one_or_none()
      if row is None:
          row = TiqoraReportState(name=STATE_NAME, last_history_id=0, backfill_status="pending",
                                  updated_at=utc_now())
          session.add(row)
          try:
              await session.flush()
          except IntegrityError:          # another process created it concurrently
              await session.rollback()
              row = (await session.execute(stmt)).scalar_one()
      return row
  ```
  Register the section in `register_core`; add the two `KEY_REPORT_FACTS_*` constants next to the other `daemon.*` keys.
- [ ] **Step 5: Run to verify pass** (Step 2 command + `tests/test_settings_sections.py`), lint gates, commit:
  ```bash
  rtk git add backend/alembic/versions_tiqora backend/src/tiqora/db/tiqora/models.py backend/src/tiqora/reporting backend/src/tiqora/extensions/core.py backend/src/tiqora/domain/settings_store.py backend/tests/test_reporting_tables.py backend/tests/test_tiqora_model_ddl.py backend/tests/test_migration_gate.py backend/tests/test_migrate_cli.py
  rtk git commit -m "$(printf 'feat(reporting): fact, event, snapshot and state tables; reporting settings section\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>')"
  rtk git log -1 --oneline
  ```

### Task 2: Working time between two instants (backend)

**Files:**
- Modify: `backend/src/tiqora/znuny/escalation.py` (public `escalation_preferences`, `calendar_config`, `to_epoch` — rename the underscored functions and keep `_escalation_preferences = escalation_preferences`, `_calendar_config = calendar_config`, `_to_epoch = to_epoch` aliases; add `working_seconds_between`; extend `__all__`)
- Create: `backend/src/tiqora/reporting/working_time.py` (R3)
- Test: `backend/tests/test_working_time.py` (pure + one DB test on MariaDB for `CalendarResolver`, band 47050–47069)

**Interfaces:**
- Consumes: `destination_time_epoch`, `SysConfig`.
- Produces: R3 verbatim.

- [ ] **Step 1: Read Znuny.** `sed -n 700,900p znuny-6.5.22/Kernel/System/DateTime.pm` (the `Delta` working-time loop). Port: walk from start to stop; at local midnight with a 24-hour day take the whole day (`working hours of that day * 3600` capped by remaining seconds when the day is a working day, else skip the day); otherwise per hour: if working day and hour in working hours add `min(seconds to next hour, remaining)`; advance to the next hour.
- [ ] **Step 2: Write the failing tests** (`test_working_time.py`):
  ```python
  WH = {d: list(range(8, 17)) for d in ("Mon", "Tue", "Wed", "Thu", "Fri")}   # 08:00–17:00
  TZ = "Europe/Berlin"

  def _epoch(s: str) -> int:
      return int(datetime.fromisoformat(s).replace(tzinfo=ZoneInfo(TZ)).timestamp())

  def test_same_working_day() -> None:
      assert working_seconds_between(_epoch("2024-06-03T09:00"), _epoch("2024-06-03T11:30"), WH, {}, {}, TZ) == 9000

  def test_overnight_and_weekend_are_skipped() -> None:
      # Fri 16:00 -> Mon 09:00 = 1 h Friday + 1 h Monday
      assert working_seconds_between(_epoch("2024-06-07T16:00"), _epoch("2024-06-10T09:00"), WH, {}, {}, TZ) == 7200

  def test_vacation_days_are_skipped() -> None:
      vd = {12: {25: "Weihnachten"}}
      assert working_seconds_between(_epoch("2024-12-24T16:00"), _epoch("2024-12-26T09:00"), WH, vd, {}, TZ) == 7200

  def test_stop_before_start_and_no_hours_give_zero() -> None:
      assert working_seconds_between(10_000, 5_000, WH, {}, {}, TZ) == 0
      assert working_seconds_between(0, 86_400, {}, {}, {}, TZ) == 0

  @pytest.mark.parametrize("start", ["2024-03-29T15:10", "2024-10-25T07:00", "2024-06-05T12:34", "2024-06-08T03:00"])
  @pytest.mark.parametrize("minutes", [1, 59, 60, 61, 480, 2000])
  def test_inverse_of_destination_time(start: str, minutes: int) -> None:   # covers both DST weekends
      s = _epoch(start)
      dest = destination_time_epoch(s, minutes, WH, {}, {}, TZ)
      assert working_seconds_between(s, dest, WH, {}, {}, TZ) == minutes * 60

  def test_working_minutes_none_without_hours() -> None:
      cal = WorkingCalendar(name=None, working_hours={}, vacation_days={}, vacation_days_once={}, time_zone=TZ)
      assert working_minutes(datetime(2024, 6, 3, 7), datetime(2024, 6, 3, 9), cal, "UTC") is None
      assert due_at(datetime(2024, 6, 3, 7), 30, cal, "UTC") == datetime(2024, 6, 3, 7, 30)

  def test_working_minutes_truncates_like_znuny() -> None:
      cal = WorkingCalendar(name=None, working_hours=WH, vacation_days={}, vacation_days_once={}, time_zone=TZ)
      # storage UTC; 07:00Z = 09:00 Berlin (summer); 07:00:59 later -> 0 minutes, 59 s
      assert working_minutes(datetime(2024, 6, 3, 7, 0), datetime(2024, 6, 3, 7, 0, 59), cal, "UTC") == 0
  ```
  DB test (MariaDB only is enough — no SQL dialect logic): seed queue 47050 with `first_response_time=60, update_time=120, solution_time=480, calendar_name='1'` and SysConfig values via the existing test helper for sysconfig (find with `grep -ln "sysconfig_default\|TimeWorkingHours" backend/tests`; if no helper exists, seed `sysconfig_default` rows `TimeZone::Calendar1Name` and `TimeWorkingHours::Calendar1` the way `backend/tests/test_escalation*.py` does) and assert `await CalendarResolver(s, SysConfig(s)).targets(None, 47050) == EscalationTargets(60, 120, 480, "1")` and that `calendar("1")` is cached (second call issues no query — count with an `event.listen(engine.sync_engine, "before_cursor_execute", …)` counter).
- [ ] **Step 3: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_working_time.py` → FAIL.
- [ ] **Step 4: Implement** `working_seconds_between` (loop guard like `destination_time_epoch`), the renames + aliases, and `working_time.py`. `CalendarResolver.targets` keys its cache by `("sla", sla_id)` or `("queue", queue_id)`; `calendar` caches by name; `storage_time_zone` caches `await sysconfig.otrs_time_zone() or "UTC"`.
- [ ] **Step 5: Run to verify pass** + `uv run python -m pytest -q tests -k escalation` (existing escalation tests unchanged), lint gates, commit `feat(reporting): working time between two instants from the escalation calendars`.

### Task 3: History → events (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/history_events.py` (R4)
- Test: `backend/tests/test_reporting_history_events.py` (pure)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `EVENT_KINDS`, `CLOSED_STATE_TYPE`, `PROCESS_ACTIVITY_FIELD`, `TicketRow`, `HistoryRow`, `ArticleRow`, `EventRow`, `derive_events`.

- [ ] **Step 1: Write the failing tests.** Fixtures: state ids `1 new, 4 open, 2 closed, 6 pending reminder, 9 merged` with `STATE_TYPES = {1: "new", 4: "open", 2: "closed", 6: "pending reminder", 9: "merged"}`; helper `h(id, type, minutes, *, queue=1, state=1, owner=1, prio=3, type_id=1, name="%%", by=2)` builds a `HistoryRow` at `T0 + minutes`; `t(**overrides)` builds a `TicketRow` (id 1, created `T0 = datetime(2024, 6, 3, 8, 0)`).
  ```python
  def kinds(events): return [e.kind for e in events]

  def test_no_history_rows_builds_from_ticket_row() -> None:
      ev = derive_events(t(state_id=4, queue_id=7), [], [], STATE_TYPES)
      assert kinds(ev) == ["create"] and ev[0].to_id == 7 and ev[0].to_value == "open"

  def test_created_closed_emits_close() -> None:
      ev = derive_events(t(), [h(1, "NewTicket", 0, state=2)], [], STATE_TYPES)
      assert kinds(ev) == ["create", "close"]

  def test_reopen_twice_counts_and_last_close() -> None:
      hist = [h(1, "NewTicket", 0, state=1), h(2, "StateUpdate", 10, state=2), h(3, "StateUpdate", 20, state=4),
              h(4, "StateUpdate", 30, state=2), h(5, "StateUpdate", 40, state=4), h(6, "StateUpdate", 50, state=2)]
      ev = derive_events(t(), hist, [], STATE_TYPES)
      assert kinds(ev).count("close") == 3 and kinds(ev).count("reopen") == 2
      state_ev = [e for e in ev if e.kind == "state"]
      assert state_ev[0].from_value == "new" and state_ev[0].to_value == "closed"
      assert state_ev[0].from_duration_seconds == 600 and state_ev[1].from_duration_seconds == 600

  def test_queue_pingpong_events_and_durations() -> None:
      hist = [h(1, "NewTicket", 0, queue=1), h(2, "Move", 5, queue=2), h(3, "Move", 65, queue=1)]
      q = [e for e in derive_events(t(), hist, [], STATE_TYPES) if e.kind == "queue"]
      assert [(e.from_id, e.to_id, e.from_duration_seconds, e.queue_id) for e in q] == [(1, 2, 300, 2), (2, 1, 3600, 1)]

  def test_owner_priority_type_by_column_diff_regardless_of_history_type() -> None:
      hist = [h(1, "NewTicket", 0), h(2, "AddNote", 1, owner=5, prio=4, type_id=2)]
      assert sorted(kinds(derive_events(t(), hist, [], STATE_TYPES))) == ["create", "owner", "priority", "type"]

  def test_service_sla_responsible_from_names() -> None:
      hist = [h(1, "NewTicket", 0), h(2, "ServiceUpdate", 1, name="%%IT%%12%%%%"),
              h(3, "SLAUpdate", 2, name="%%Gold%%3%%Silver%%2"), h(4, "ResponsibleUpdate", 3, name="%%anna%%8"),
              h(5, "ResponsibleUpdate", 4, name="%%bert%%9"), h(6, "ServiceUpdate", 5, name="%%broken")]
      ev = {(e.kind, e.from_id, e.to_id) for e in derive_events(t(), hist, [], STATE_TYPES)}
      assert ("service", None, 12) in ev and ("sla", 2, 3) in ev
      assert ("responsible", None, 8) in ev and ("responsible", 8, 9) in ev

  def test_merge_and_process_activity() -> None:
      hist = [h(1, "NewTicket", 0),
              h(2, "TicketDynamicFieldUpdate", 1, name="%%FieldName%%ProcessManagementActivityID%%Value%%A2%%OldValue%%A1"),
              h(3, "TicketDynamicFieldUpdate", 2, name="%%FieldName%%Kundennummer%%Value%%x%%OldValue%%"),
              h(4, "Merged", 3, state=9, name="%%T100%%100%%T200%%200")]
      ev = derive_events(t(), hist, [], STATE_TYPES)
      pa = [e for e in ev if e.kind == "process_activity"]
      assert [(e.from_value, e.to_value) for e in pa] == [("A1", "A2")]
      assert "merge" in kinds(ev) and "close" not in kinds(ev)       # merged is not closed

  def test_article_events_and_queue_at_time() -> None:
      hist = [h(1, "NewTicket", 0, queue=1), h(2, "Move", 30, queue=2)]
      arts = [ArticleRow(10, "customer", True, "Email", T0 + timedelta(minutes=1), 99),
              ArticleRow(11, "agent", True, "Email", T0 + timedelta(minutes=40), 5),
              ArticleRow(12, "agent", False, "Internal", T0 + timedelta(minutes=41), 5),
              ArticleRow(13, "system", False, "Internal", T0 + timedelta(minutes=42), 1)]
      ev = [e for e in derive_events(t(), hist, arts, STATE_TYPES) if e.article_id]
      assert [(e.kind, e.queue_id, e.actor_user_id) for e in ev] == [
          ("customer_message", 1, 99), ("reply", 2, 5), ("note", 2, 5)]

  def test_output_is_time_ordered() -> None:
      # interleaved history and articles -> occurred_at non-decreasing
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && uv run python -m pytest -q tests/test_reporting_history_events.py` → FAIL.
- [ ] **Step 3: Implement** per R4 rules 1–7. Parsing helper `_split` like `domain/history_render._split` (copy the 3 lines; do not import a private name). Int parse: `int(v)` when `v.strip().isdigit()` else `None`.
- [ ] **Step 4: Run to verify pass**, lint gates, commit `feat(reporting): derive ticket events from ticket_history and articles`.

### Task 4: Fact computation (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/facts.py` (R5 pure part)
- Test: `backend/tests/test_reporting_facts.py` (pure)

**Interfaces:**
- Consumes: Task 2 (`WorkingCalendar`, `EscalationTargets`, `working_minutes`, `due_at`), Task 3 (rows + `derive_events`).
- Produces: `FACT_VERSION`, `CHANNEL_KEYS`, `AiFlags`, `FactInputs`, `FactRow` (attribute names = R1 column names), `FactRow.as_insert_dict()`, `compute_fact`.

- [ ] **Step 1: Write the failing tests.** Helper `inputs(history, articles, *, targets=EscalationTargets(60, 120, 480, None), calendar=WH_CAL, ticket=t(), accounted=Decimal("0"))` that also calls `derive_events`; `WH_CAL` = Mon–Fri 08–17 Europe/Berlin; `NO_HOURS` = empty working hours; storage tz `"UTC"`; `now = datetime(2024, 6, 20, 12, 0)`. `T0 = datetime(2024, 6, 3, 7, 0)` (Monday 09:00 Berlin).
  ```python
  def test_first_response_and_breach_in_working_minutes() -> None:
      arts = [art(1, "customer", True, 0), art(2, "agent", False, 30), art(3, "agent", True, 90)]
      f = compute_fact(inputs([h(1, "NewTicket", 0)], arts))
      assert f.first_response_at == T0 + timedelta(minutes=90)
      assert (f.first_response_minutes, f.first_response_working_minutes) == (90, 90)
      assert f.first_response_target_minutes == 60 and f.first_response_breached is True
      assert f.first_response_due_at == T0 + timedelta(minutes=60)

  def test_open_unanswered_ticket_leaves_breach_undecided() -> None:
      f = compute_fact(inputs([h(1, "NewTicket", 0)], [art(1, "customer", True, 0)]))
      assert f.first_response_breached is None and f.first_response_due_at is not None

  def test_closed_without_response_after_due_is_breached() -> None:
      f = compute_fact(inputs([h(1, "NewTicket", 0), h(2, "StateUpdate", 120, state=2)], []))
      assert f.first_response_breached is True and f.solution_breached is False

  def test_reopen_twice_counts_and_last_close() -> None:
      hist = [h(1, "NewTicket", 0), h(2, "StateUpdate", 10, state=2), h(3, "StateUpdate", 20, state=4),
              h(4, "StateUpdate", 30, state=2)]
      f = compute_fact(inputs(hist, []))
      assert (f.close_count, f.reopen_count) == (2, 1)
      assert f.first_closed_at == T0 + timedelta(minutes=10) and f.closed_at == T0 + timedelta(minutes=30)
      assert f.solution_minutes == 30

  def test_solution_working_minutes_skip_night() -> None:
      # created Mon 16:00 Berlin, closed Tue 09:00 Berlin -> 2 working hours
  def test_no_working_hours_gives_empty_working_minutes() -> None:
      f = compute_fact(inputs([h(1, "NewTicket", 0)], [art(3, "agent", True, 90)], calendar=NO_HOURS))
      assert f.first_response_working_minutes is None and f.first_response_minutes == 90
      assert f.first_response_breached is True        # wall clock 90 > 60
  def test_zero_targets_mean_no_sla() -> None:
      f = compute_fact(inputs([h(1, "NewTicket", 0)], [], targets=EscalationTargets(0, 0, 0, None)))
      assert (f.first_response_target_minutes, f.first_response_due_at, f.first_response_breached) == (None, None, None)
  def test_update_breach_count() -> None:
      # customer 0 min, agent reply 30 (ok), customer 60, customer 70 (same wait), agent reply 300 (240 > 120 -> 1),
      # customer 400 (open wait, not counted) -> update_breach_count == 1
  def test_queue_pingpong() -> None:
      hist = [h(1, "NewTicket", 0, queue=1), h(2, "Move", 5, queue=2), h(3, "Move", 10, queue=1)]
      f = compute_fact(inputs(hist, []))
      assert f.queue_move_count == 2 and f.queue_pingpong is True and f.queue_id_at_create == 1
      g = compute_fact(inputs([h(1, "NewTicket", 0, queue=1), h(2, "Move", 5, queue=2)], []))
      assert g.queue_pingpong is False
  def test_channel_counts_touches_accounted() -> None:
      arts = [art(1, "customer", True, 0, channel="Telegram"), art(2, "agent", True, 5, by=5),
              art(3, "agent", True, 6, by=6), art(4, "agent", False, 7, by=5)]
      f = compute_fact(inputs([h(1, "NewTicket", 0)], arts, accounted=Decimal("1.50")))
      assert f.channel == "telegram" and (f.article_in_count, f.article_out_count, f.note_count) == (1, 2, 1)
      assert f.agent_touch_count == 2 and f.accounted_time == Decimal("1.50")
  def test_merged_flag_and_unknown_channel() -> None:   # state 9 -> merged True; channel "Fax" -> "fax"; no articles -> "email"
  def test_as_insert_dict_has_every_column() -> None:
      from tiqora.db.tiqora.models import TiqoraReportTicketFact
      cols = {c.name for c in TiqoraReportTicketFact.__table__.columns}
      assert set(compute_fact(inputs([h(1, "NewTicket", 0)], [])).as_insert_dict()) == cols
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && uv run python -m pytest -q tests/test_reporting_facts.py` → FAIL.
- [ ] **Step 3: Implement** per R5 fixed rules. `fact_version = FACT_VERSION`, `built_at = inp.now`. Wall minutes floor-divide; working minutes via `working_minutes(a, b, inp.calendar, inp.storage_tz)`; when `None`, breach comparisons use wall minutes. `due_at(created, target, calendar, storage_tz)`.
- [ ] **Step 4: Run to verify pass**, lint gates, commit `feat(reporting): compute ticket fact rows (SLA in working minutes, reopen, ping-pong)`.

### Task 5: Load and write facts; dirty queue (backend, DB)

**Files:**
- Create: `backend/src/tiqora/reporting/facts_store.py`, `backend/src/tiqora/reporting/dirty.py`
- Test: `backend/tests/test_reporting_facts_store.py` (both dialects, band 47100–47199)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: R5 DB part (`FACT_CHUNK_SIZE`, `fact_write_lock`, `RebuildResult`, `load_fact_inputs`, `delete_ticket_facts`, `rebuild_ticket_facts`, `rebuild_and_commit`), R6 dirty helpers (`mark_tickets_dirty`, `take_dirty_ticket_ids`, `clear_dirty`).

- [ ] **Step 1: Write the failing tests.** Seed (module `_seed_statements(ns)` pattern; ids 47100+): one group/queue pair (queue 47101 `first_response_time=60`, `solution_time=480`), one agent 47110, tickets 47120 (history NewTicket→Move→StateUpdate closed; articles customer/agent visible; two `time_accounting` rows 0.5 + 1.0; dynamic field value `ProcessManagementProcessID` = `"Process-1"` if the field exists in the seed — else skip that assert), 47121 (no history rows at all), 47122 (merged via `Merged` history + merged state). Use the Znuny seed's history type ids (look them up by name in `ticket_history_type`, never hardcode) and the seed's `communication_channel` ids by name.
  ```python
  async def test_rebuild_builds_facts_and_events(url_fixture, request) -> None:
      # res = await rebuild_ticket_facts(s, [47120, 47121, 47122]); await s.commit()
      # res.built == 3; fact 47120: queue_id_at_create, closed_at, accounted_time == Decimal("1.50"),
      #   first_response_at set, channel "email", fact_version == FACT_VERSION
      # fact 47121 exists with created_at == ticket.create_time; events: one "create"
      # fact 47122 merged True
      # events of 47120 contain kinds create, queue, state, close, customer_message, reply
  async def test_rebuild_is_idempotent(url_fixture, request) -> None:
      # rebuild twice -> event count unchanged, fact row identical except built_at
  async def test_rebuild_deletes_facts_of_missing_ticket(url_fixture, request) -> None:
      # insert a fact + event row for ticket_id 47199 (no such ticket) -> rebuild [47199] -> RebuildResult(built=0, deleted=1), rows gone
  async def test_ai_flags(url_fixture, request) -> None:
      # tiqora_ai_triage row (ticket 47120, queue_applied True), tiqora_ai_article_origin for the agent article,
      # tiqora_ai_ticket_state.ai_escalated_at set -> ai_triaged/ai_triage_queue_applied/ai_auto_replied/ai_handed_off True;
      # ticket without AI rows -> all False (not None) once the AI tables exist
  async def test_dirty_queue_round_trip(url_fixture, request) -> None:
      # mark_tickets_dirty([47120, 47121]); mark again [47120] (no IntegrityError); take(10) == [47120, 47121] or ordered by queued_at;
      # clear_dirty -> table empty
  async def test_rebuild_and_commit_retries_once_on_integrity_error(url_fixture, request, monkeypatch) -> None:
      # monkeypatch rebuild_ticket_facts to raise IntegrityError on the first call -> result still built == 1, called twice
  async def test_fact_write_lock_is_per_loop() -> None:
      # fact_write_lock() is the same object within one loop; in a fresh loop (asyncio.run of a helper) another object
  ```
- [ ] **Step 2: Run to verify failure.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_facts_store.py` → FAIL.
- [ ] **Step 3: Implement.** `load_fact_inputs`: one query per kind listed in R5 with `.in_(ticket_ids)`; history joined with `ticket_history_type` (both Znuny, integer join); articles joined with `article_sender_type` and `communication_channel` (Znuny, integer joins); `SUM(time_accounting.time_unit)`; AI tables and `dynamic_field_value` only by integer ids (`dynamic_field.name = PROCESS_ID_DF_NAME` resolved to its id first). Build `FactInputs` with `CalendarResolver.targets(ticket.sla_id, ticket.queue_id)` and `calendar(targets.calendar_name)`. `rebuild_ticket_facts`: for each chunk → `delete_ticket_facts(chunk)` → `session.execute(insert(TiqoraReportTicketFact), [f.as_insert_dict() ...])` and `insert(TiqoraReportTicketEvent)` with `dataclasses.asdict(e)` → `flush`. Deleted count = chunk ids that had a fact row but no ticket. `rebuild_and_commit` per R5 (`asyncio.sleep(0.2 * attempt)` between retries).
- [ ] **Step 4: Run to verify pass**, lint gates, commit `feat(reporting): load ticket data and rebuild fact rows per ticket`.

### Task 6: `TimeAccounting` history like Znuny (backend)

> **Verify-only ([R-T6]).** `add_time_accounting` on main already writes `change_time`, the `%%unit%%perl_sum` history row and normalises 0.256 to 0.25. Run `tests/test_time_accounting_znuny.py` plus the existing time-accounting tests; add no new function, no new test file and no `format(float(...), ".15g")` rewrite. The steps below are superseded.

**Files:**
- Modify: `backend/src/tiqora/znuny/history.py` (`TYPE_TIME_ACCOUNTING: Final = "TimeAccounting"`, `async def add_time_accounting_history(session, *, ticket_id, article_id, time_unit: str, accounted_time: str, user_id) -> None`), `backend/src/tiqora/domain/ticket_write_service.py` (`add_time_accounting`)
- Test: `backend/tests/test_time_accounting_history.py` (both dialects, band 47200–47219); existing tests found by `grep -ln "time-accounting\|add_time_accounting" backend/tests` stay green.

**Interfaces:**
- Consumes: `history_add`.
- Produces: `add_time_accounting` behaviour identical to Znuny `TicketAccountTime` (signature unchanged).

- [ ] **Step 1: Read Znuny.** `sed -n 6195,6285p znuny-6.5.22/Kernel/System/Ticket.pm` (`TicketAccountTime`) and `grep -n "sub TicketAccountedTimeGet" -A40 znuny-6.5.22/Kernel/System/Ticket.pm`. Record exactly: TimeUnit normalisation (`,`→`.`, spaces removed, cut to 2 decimals), the `UPDATE ticket SET change_time = current_timestamp, change_by = ?` BEFORE the insert, the history name `%%<TimeUnit>%%<AccountedTime>` where AccountedTime is the ticket's sum AFTER the insert as `TicketAccountedTimeGet` returns it (Perl adds the rows one by one → `1.50 + 1.50` prints `3`, `0.50` prints `0.5`; Python: `SELECT time_unit FROM time_accounting WHERE ticket_id = :tid` after the insert, sum the values as `Decimal` in Python (not SQL `SUM`), then `format(float(total), ".15g")`), TimeUnit = the normalised input string (Python: `str(time_unit)`, `,`→`.`, spaces removed, then `re.sub(r"^(\d{1,10}\.\d\d).+?$", r"\1", value)` — cut, not rounded), and the cache clear (Tiqora: `TiqoraSync` ticket cache invalidation — use the same helper `add_note`-style writes use, find with `grep -n "invalidate_ticket_cache" backend/src/tiqora/domain/ticket_write_service.py`).
- [ ] **Step 2: Write the failing tests.**
  ```python
  async def test_time_accounting_writes_history_and_change_time(url_fixture, request) -> None:
      # ticket 47201 with change_time 2024-01-01; add_time_accounting(1.5) then (1.5)
      # -> two TimeAccounting history rows named "%%1.5%%1.5" and "%%1.5%%3" (exact strings per Step 1),
      #    article_id carried, create_by = user; ticket.change_time > 2024-01-01 and change_by = user
  async def test_time_unit_normalisation(url_fixture, request) -> None:
      # time_unit 0.256 -> time_accounting.time_unit == Decimal("0.25") (cut, not rounded) and history name "%%0.25%%0.25"
  ```
- [ ] **Step 3: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_time_accounting_history.py`
- [ ] **Step 4: Implement** (keep the article ownership check and the id read-back), run the new test plus the existing time-accounting tests → PASS. Lint gates, commit `fix(tickets): book time like Znuny (TimeAccounting history, change_time)`.

### Task 7: Daily snapshots (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/snapshots.py` (R6 snapshot part)
- Test: `backend/tests/test_reporting_snapshots.py` (pure + both dialects, band 47220–47299)

**Interfaces:**
- Consumes: Task 3 `EventRow`, Task 5 `rebuild_ticket_facts`, `OPEN_STATE_TYPES`.
- Produces: `AGE_BUCKETS`, `Segment`, `SnapshotKey`, `ticket_timeline`, `day_end_instants`, `compute_snapshot_counts`, `write_snapshots`, `snapshot_days`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  def test_day_end_instants_across_dst() -> None:
      ends = day_end_instants([date(2024, 3, 30), date(2024, 3, 31), date(2024, 10, 27)], "Europe/Berlin")
      assert ends[date(2024, 3, 30)] == datetime(2024, 3, 30, 23, 0)    # midnight CET = 23:00 UTC
      assert ends[date(2024, 3, 31)] == datetime(2024, 3, 31, 22, 0)    # midnight CEST = 22:00 UTC
      assert ends[date(2024, 10, 27)] == datetime(2024, 10, 27, 23, 0)

  def test_timeline_segments() -> None:
      # create (queue 1, open, prio 3) at T0; queue -> 2 at T0+1d; state -> pending reminder at T0+2d;
      # priority -> 4 at T0+3d; close at T0+4d -> 5 contiguous segments, last end None, last state_type "closed"

  def test_counts_open_only_and_age_buckets() -> None:
      # ticket A created 2024-06-01 08:00 UTC, open until closed 2024-06-05 10:00 UTC; ticket B created 2024-06-04 12:00, pending
      # days 06-01..06-06 in UTC zone -> (06-01, q, "open", 3) == [1, 1, 0, 0, 0, 0]; (06-04, q, "open", 3) age bucket 1_3d;
      # 06-05: A closed -> only B (state "pending reminder"); 06-06: B age 1_3d
  def test_ticket_reopened_counts_again() -> None:
      # closed 06-02, reopened 06-03 -> absent on 06-02, present on 06-03

  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_snapshot_days_writes_and_replaces(url_fixture, request) -> None:
      # seed 2 tickets with histories (queue 47221) -> rebuild_ticket_facts -> commit
      # await snapshot_days(factory, [date(2024, 6, 1), date(2024, 6, 2)], zone="Europe/Berlin")
      # rows for both days with expected counts; second call -> same rows (no duplicates, replaced)
      # cleanup deletes tiqora_report_daily_snapshot rows of queue 47221 and the facts/events of the seeded tickets
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_snapshots.py`
- [ ] **Step 3: Implement** per R6. `day_end_instants`: `datetime.combine(d + timedelta(days=1), time.min, ZoneInfo(zone)).astimezone(UTC).replace(tzinfo=None)`. `compute_snapshot_counts`: per ticket iterate segments, for each day whose end instant lies in `[seg.start, seg.end)` and whose state type is open, increment `[1, bucket...]` (age days = `(end - created_at).total_seconds() / 86400`; bucket by the `AGE_BUCKETS` upper bounds, exclusive). `snapshot_days` loads candidate fact ids (R6 rule), events of kinds `create, queue, state, priority` per 500 ids, computes, `write_snapshots` (delete by `snapshot_date IN days`, then bulk insert), commit.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): daily open-ticket snapshots from events`.

### Task 8: Backfill job, CLI, admin status and rebuild API (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/backfill.py`, `backend/src/tiqora/reporting/schemas.py` (only `FactsStatusOut`, `ReportingStatusOut`, `ReportBackfillIn` in this task), `backend/src/tiqora/cli/report.py`, `backend/src/tiqora/api/v1/admin/reporting.py`
- Modify: `backend/src/tiqora/reporting/state.py` (`facts_status`), `backend/src/tiqora/extensions/core.py` (`registry.add(JOB_HANDLERS, REPORT_BACKFILL_JOB)`), `backend/src/tiqora/api/v1/admin/__init__.py` (include router), `backend/src/tiqora/main.py` (`add_report_subparser` + `elif command == "report"` dispatch like `gdpr`)
- Test: `backend/tests/test_reporting_backfill.py` (both dialects, band 47300–47399), `backend/tests/test_admin_reporting_api.py` (HTTP, MariaDB)
- Regenerate openapi files.

**Interfaces:**
- Consumes: Tasks 1, 5, 7; wave-0 `JobHandler`, `JobContext`, `enqueue_job`, `JobAccepted`, `JobRunner` (tests).
- Produces: `ReportBackfillPayload`, `run_backfill`, `run_backfill_job`, `REPORT_BACKFILL_JOB`, `facts_status`, `FactsStatusOut`, `ReportingStatusOut`, `ReportBackfillIn`, `GET /api/v1/admin/reporting/status`, `POST /api/v1/admin/reporting/backfill`, CLI `tiqora report backfill|status`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_backfill_builds_all_tickets_and_snapshots(url_fixture, request) -> None:
      # seed 5 tickets (ids 47301..47305, created 2024-06-01..05) with histories; state row fresh
      # result = await run_backfill(factory, Settings(environment="test"), ReportBackfillPayload(since=date(2024, 6, 1)),
      #                              progress=rec, check_cancelled=noop, sleep_seconds=0, now=datetime(2024, 6, 10, 12))
      # result["tickets"] == number of tickets with id >= first seeded id and <= MAX(ticket.id)  (seed tickets of the
      #   Znuny fixture with lower ids are excluded by `since`); facts exist for 47301..47305; snapshot rows for 06-01..06-09;
      # state.backfill_status == "done", last_snapshot_date == date(2024, 6, 9), last_history_id > 0; progress recorded
  async def test_backfill_resume_and_idempotent(url_fixture, request) -> None:
      # check_cancelled raises JobCancelled after the first chunk (chunk size monkeypatched to 2) -> state "running",
      # cursor == 2nd seeded id; second run (restart False) continues -> done; event rows per ticket equal a clean
      # restart=True run (compare SELECT ticket_id, kind, occurred_at, from_id, to_id ORDER BY ...)
  async def test_backfill_job_handler_runs_through_job_runner(url_fixture, request) -> None:
      # enqueue core.report_backfill via enqueue_job (registry from register_core), commit, JobRunner.run_once + wait_idle
      # -> job succeeded, result {"tickets": n, "snapshot_days": d}
  async def test_facts_status(url_fixture, request) -> None:
      # pending -> ready False, backfill_progress None; running with start 10, cursor 15, target 20 -> 0.5; done -> ready True
  ```
  `test_admin_reporting_api.py`: admin GET status 200 with `history_lag >= 0`; non-admin 403; POST backfill → 202 `{"job_id", "status": "queued"}`, a second POST while queued returns the same `job_id` (dedupe); `state.backfill_job_id` set.
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_backfill.py tests/test_admin_reporting_api.py`
- [ ] **Step 3: Implement** per R6/R9. `run_backfill_job(ctx, payload)` = `run_backfill(ctx.session_factory, ctx.settings, payload, progress=ctx.progress, check_cancelled=ctx.check_cancelled)`. `since` → first ticket id: `SELECT MIN(id) FROM ticket WHERE create_time >= :since_utc` (local midnight of `since` in the reporting zone → naive UTC). Zone via `load_reporting_settings` + `reporting_time_zone`. Admin POST: `enqueue_job(session, kind="core.report_backfill", payload=ReportBackfillPayload(**body.model_dump()), created_by=user.id, dedupe_key="report_backfill")`, set `state.backfill_job_id`, and when `restart` set `backfill_status="pending"` only if no job is active; commit; 202. Status: `history_lag = MAX(ticket_history.id) - last_history_id`, counts by `SELECT COUNT(*)`. CLI: argparse subcommands `backfill` (`--since`, `--restart`, `--no-snapshots`) printing `"{done}/{total} {message}"` per progress call, and `status` printing `facts_status(...).model_dump_json(indent=2)`.
- [ ] **Step 4: Run → PASS**, regenerate openapi, lint gates, commit `feat(reporting): resumable backfill job, CLI and admin status`.

### Task 9: Incremental fact loop, worker service, GDPR hook (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/incremental.py`, `backend/src/tiqora/worker/report_facts.py`
- Modify: `backend/src/tiqora/config.py` (`report_facts_interval_seconds`), `backend/src/tiqora/worker/__main__.py` (`_interval_loop("report_facts", …, settings.report_facts_interval_seconds, KEY_REPORT_FACTS_INTERVAL_SECONDS, stop)`), `backend/src/tiqora/worker/services.py` (`DaemonService(slug="report_facts", enabled_key=KEY_REPORT_FACTS_ENABLED, default_enabled=True, toggleable=True, schedule_kind="interval", interval_key=KEY_REPORT_FACTS_INTERVAL_SECONDS, interval_settings_attr="report_facts_interval_seconds")`), `backend/tests/test_admin_daemons.py` (service count +1 from whatever it is now), `backend/src/tiqora/gdpr/erasure.py`, `frontend/src/i18n/locales/en.json` + `de.json` (`admin.daemons.services.report_facts.{name,description}`) + propagate
- Test: `backend/tests/test_reporting_incremental.py` (both dialects, band 47400–47499), existing GDPR erasure tests (`grep -ln "run_erasure\|rollback_job" backend/tests`)

**Interfaces:**
- Consumes: Tasks 1, 5, 7, 8.
- Produces: R6 incremental constants + `run_report_facts_tick`; `run_report_facts_worker_tick(settings) -> dict[str, int]` (returns `{"skipped": 1}` when `KEY_REPORT_FACTS_ENABLED` is false).

- [ ] **Step 1: Write the failing tests** (each test resets `tiqora_report_state` and works only on its own seeded tickets; `now` and `wall_clock` injected):
  ```python
  async def test_first_tick_sets_watermark_and_enqueues_backfill(url_fixture, request) -> None:
      # empty state -> tick -> last_history_id == MAX(ticket_history.id); one queued job kind core.report_backfill,
      # state.backfill_job_id == its id; second tick enqueues nothing new
  async def test_new_history_rows_rebuild_touched_tickets(url_fixture, request) -> None:
      # watermark at current max; insert NewTicket + Move rows for ticket 47401 -> tick -> fact for 47401, result["tickets"] == 1
  async def test_gap_row_committed_later_is_processed(url_fixture, request) -> None:
      # insert history rows with explicit ids wm+1 and wm+3 (ticket 47402), tick -> gap_ids contains wm+2;
      # insert row id wm+2 for ticket 47403 -> tick -> fact for 47403 exists, gap list empty
      # (explicit ids: MariaDB accepts them; on PostgreSQL insert with explicit id then
      #  SELECT setval(pg_get_serial_sequence('ticket_history','id'), MAX(id)) so later inserts do not collide)
  async def test_old_gaps_expire(url_fixture, request) -> None:
      # gap first_seen = wall_clock() - GAP_TTL_SECONDS - 1 -> dropped on next tick
  async def test_dirty_tickets_are_rebuilt(url_fixture, request) -> None:
      # mark_tickets_dirty([47404]) + commit -> tick -> fact exists, dirty table empty
  async def test_orphan_sweep_removes_deleted_tickets(url_fixture, request) -> None:
      # fact rows for 47405 (ticket deleted afterwards via DELETE FROM ticket...) and last_orphan_sweep_at None -> tick -> gone;
      # last_orphan_sweep_at set; immediate second tick does not sweep (result["orphans"] == 0)
  async def test_snapshots_only_after_backfill_and_fill_missing_days(url_fixture, request) -> None:
      # backfill_status pending -> no snapshot rows; set done + last_snapshot_date 2024-06-05, now 2024-06-08 10:00 UTC,
      # zone Europe/Berlin -> days 06-06 and 06-07 written, last_snapshot_date == 06-07
  async def test_backfill_and_tick_concurrently(url_fixture, request) -> None:
      # asyncio.gather(run_backfill(...restart=True...), run_report_facts_tick(...)) over the same seeded tickets
      # -> no exception; per ticket exactly the events of a fresh rebuild (no duplicates)
  def test_disabled_service_skips(monkeypatch) -> None:   # get_setting_bool -> False -> {"skipped": 1}
  ```
  GDPR: in the existing erasure DB test module add `test_erasure_removes_report_facts_and_marks_dirty`: build facts for the erased ticket first; after `run_erasure` (delete mode) the fact/event rows are gone and the ticket id is in `tiqora_report_dirty_ticket`; after `rollback_job` the id is marked dirty again.
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_incremental.py`
- [ ] **Step 3: Implement** the tick per R6 steps 1–7. Gap list JSON `[[id, first_seen], ...]`. The gap computation for a batch: `expected = set(range(old_wm + 1, batch_max + 1))`, `missing = expected - returned_ids`. Rebuild with `rebuild_and_commit(factory, sorted(ticket_ids), now=now)`. Worker wrapper reads the enabled flag with `get_setting_bool(session, KEY_REPORT_FACTS_ENABLED, True)`. GDPR: in `run_erasure` after the ticket rows are anonymised/deleted and in `rollback_job` after the restore — inside the same transaction — call `delete_ticket_facts(session, ticket_ids)` and `mark_tickets_dirty(session, ticket_ids)` for every affected ticket id (find the id set the function already computes for its ticket loop; anonymise mode included, because facts hold `customer_id`/`customer_user`).
- [ ] **Step 4: Run → PASS** (+ `tests/test_admin_daemons.py`, `tests/test_admin_system.py`, the GDPR tests; `cd frontend && pnpm vitest run src/routes/admin/DaemonsPage.test.tsx` after the i18n keys; `node frontend/scripts/propagate-i18n-keys.mjs && node frontend/scripts/check-i18n-keys.mjs`), lint gates, commit `feat(reporting): incremental fact loop with gap-safe history watermark`.

### Task 10: SQL constructs, time-zone shifting, statement timeout (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/sql.py` (R7)
- Test: `backend/tests/test_reporting_sql.py` (golden SQL pure + execution on both dialects, band 47500–47519)

**Interfaces:**
- Consumes: nothing new.
- Produces: R7 verbatim (`Grain`, `DatePart`, `shift_seconds`, `date_bucket`, `date_part`, `TzSegment`, `tz_segments`, `to_local`, `ReportQueryTimeout`, `statement_timeout`, `is_timeout_error`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  from sqlalchemy.dialects import mysql, postgresql
  col = column("created_at", DateTime())

  def sql(expr, dialect) -> str:
      return str(select(expr).compile(dialect=dialect, compile_kwargs={"literal_binds": True}))

  @pytest.mark.parametrize("grain,expected_mysql,expected_pg", [
      ("day", "DATE(created_at)", "CAST(date_trunc('day', created_at) AS DATE)"),
      ("week", "DATE(DATE_SUB(created_at, INTERVAL WEEKDAY(created_at) DAY))", "CAST(date_trunc('week', created_at) AS DATE)"),
      ("month", "DATE(DATE_SUB(created_at, INTERVAL DAYOFMONTH(created_at) - 1 DAY))", "CAST(date_trunc('month', created_at) AS DATE)"),
      ("quarter", "DATE_ADD(MAKEDATE(YEAR(created_at), 1), INTERVAL QUARTER(created_at) - 1 QUARTER)", "CAST(date_trunc('quarter', created_at) AS DATE)"),
      ("year", "MAKEDATE(YEAR(created_at), 1)", "CAST(date_trunc('year', created_at) AS DATE)"),
  ])
  def test_date_bucket_golden_sql(grain, expected_mysql, expected_pg) -> None:
      assert expected_mysql in sql(date_bucket(col, grain), mysql.dialect())
      assert expected_pg in sql(date_bucket(col, grain), postgresql.dialect())
      assert "%" not in sql(date_bucket(col, grain), mysql.dialect())

  def test_unknown_dialect_fails_loudly() -> None:
      from sqlalchemy.dialects import sqlite
      with pytest.raises(CompileError, match="date_bucket"):
          sql(date_bucket(col, "day"), sqlite.dialect())

  def test_tz_segments_berlin_2024() -> None:
      segs = tz_segments("Europe/Berlin", datetime(2024, 3, 1), datetime(2024, 11, 1), margin_days=0)
      assert [(s.until_utc, s.offset_seconds) for s in segs] == [
          (datetime(2024, 3, 31, 1, 0), 3600), (datetime(2024, 10, 27, 1, 0), 7200), (None, 3600)]
      assert tz_segments("UTC", datetime(2024, 1, 1), datetime(2025, 1, 1)) == (TzSegment(None, 0),)

  def test_to_local_single_segment_is_plain_shift() -> None:
      assert "CASE" not in sql(to_local(col, (TzSegment(None, 3600),)), mysql.dialect())
      assert "CASE" in sql(to_local(col, tz_segments("Europe/Berlin", datetime(2024, 3, 1), datetime(2024, 11, 1))), mysql.dialect())

  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_local_day_buckets_across_dst_both_dialects(url_fixture, request) -> None:
      # temp table via a tiqora-free approach: SELECT over a VALUES-like UNION ALL of literal naive UTC datetimes:
      # 2024-03-30 23:30 (= 03-31 00:30 CET) -> day 2024-03-31; 2024-03-31 21:59 (= 23:59 CEST) -> 03-31;
      # 2024-03-31 22:00 -> 04-01; 2024-10-26 22:30 (= 10-27 00:30 CEST) -> 10-27; 2024-10-27 22:59 (= 23:59 CET) -> 10-27
      # week grain: 2024-03-31 21:59 -> week 2024-03-25 (Monday); date_part hour of 2024-10-27 00:30 UTC
      #   (02:30 CEST, before the switch at 01:00 UTC) -> 2, of 2024-10-27 01:30 UTC (02:30 CET, after it) -> 2;
      #   isodow of 2024-03-31 21:59 UTC (Sunday 23:59 local) -> 7
      # expression: date_bucket(to_local(x, tz_segments("Europe/Berlin", 2024-01-01, 2025-01-01)), grain)
  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_statement_timeout(url_fixture, request) -> None:
      # async with statement_timeout(s, 1): run SELECT SLEEP(3) (mysql) / SELECT pg_sleep(3) (postgresql)
      # -> either an exception with is_timeout_error(exc) True, or (MariaDB may end SLEEP early without error) elapsed < 2.5 s;
      # afterwards the same connection runs SELECT 1 fine and (mysql) SELECT @@max_statement_time is 0 again
  ```
  For the literal-row query use `union_all(*(select(literal(dt, DateTime()).label("x")) for dt in samples)).subquery()`.
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_sql.py`
- [ ] **Step 3: Implement** R7. `FunctionElement` subclasses with `inherit_cache = False`, store `grain`/`part`/`seconds` as Python attributes set in `__init__` (validated against the Literal values; `ValueError` otherwise). `@compiles(date_bucket, "mysql")` and `@compiles(date_bucket, "mariadb")` share one function; `compiler.process(element.clauses.clauses[0], **kw)` renders the inner expression. `tz_segments`: probe the UTC offset (`ZoneInfo(zone).utcoffset(dt)`) daily from `start - margin` to `end + margin`; where two probes differ, bisect to the second. `to_local` builds `case((expr < bindparam(until), shift_seconds(expr, off)), ..., else_=shift_seconds(expr, last))`. `statement_timeout` per R7 using `session.get_bind().dialect` (`name`, `is_mariadb`).
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): dialect-aware date buckets, time-zone shifting and query timeouts`.

### Task 11: Report definitions, semantic layer, five sources (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/definition.py`, `backend/src/tiqora/reporting/semantic.py`, `backend/src/tiqora/reporting/filter_sql.py`, `backend/src/tiqora/reporting/sources/__init__.py`, `sources/tickets.py`, `sources/ticket_events.py`, `sources/daily_snapshot.py`, `sources/accounted_time.py`, `sources/open_tickets.py`
- Modify: `backend/src/tiqora/extensions/core.py` (`for s in core_report_sources(): registry.add(REPORT_SOURCES, s)`)
- Test: `backend/tests/test_reporting_definition.py` (pure), `backend/tests/test_reporting_filter_sql.py` (pure golden SQL)

**Interfaces:**
- Consumes: Task 10 (`Grain`, `TzSegment`), wave-0 `Filter`, `validate_filter`, `FilterFieldSpec`, `FilterError`, `iter_conditions`, `relative_delta`, `parse_datetime_value`, `OPS_BY_TYPE`, `MAX_IN_VALUES`.
- Produces: R8 definition + semantic parts verbatim, `compile_conditions` (R8a), `core_report_sources() -> list[ReportSource]` (order: tickets, ticket_events, daily_snapshot, accounted_time, open_tickets).

- [ ] **Step 1: Write the failing tests.**
  ```python
  NOW = datetime(2024, 6, 12, 10, 0)    # Wednesday, naive UTC

  @pytest.mark.parametrize("tr,start,end", [
      (RelativeTimeRange(unit="day", count=30, include_current=True), date(2024, 5, 14), date(2024, 6, 12)),
      (RelativeTimeRange(unit="week", count=12), date(2024, 3, 18), date(2024, 6, 9)),
      (RelativeTimeRange(unit="month", count=1), date(2024, 5, 1), date(2024, 5, 31)),
      (RelativeTimeRange(unit="quarter", count=1, include_current=True), date(2024, 4, 1), date(2024, 6, 30)),
      (RelativeTimeRange(unit="year", count=1), date(2023, 1, 1), date(2023, 12, 31)),
      (AbsoluteTimeRange(start=date(2024, 3, 30), end=date(2024, 3, 31)), date(2024, 3, 30), date(2024, 3, 31)),
  ])
  def test_resolve_time_window(tr, start, end) -> None:
      w = resolve_time_window(tr, "Europe/Berlin", NOW)
      assert (w.start_date, w.end_date) == (start, end)
      assert w.start_utc == datetime.combine(start, time.min, ZoneInfo("Europe/Berlin")).astimezone(UTC).replace(tzinfo=None)

  def test_absolute_range_validation() -> None:
      with pytest.raises(ValidationError):
          AbsoluteTimeRange(start=date(2024, 2, 1), end=date(2024, 1, 1))

  def test_core_sources_registered_with_expected_keys() -> None:
      reg = Registry(); register_core(reg)
      assert [s.key for s in reg.items(REPORT_SOURCES)] == ["tickets", "ticket_events", "daily_snapshot", "accounted_time", "open_tickets"]

  @pytest.mark.parametrize("bad,path", [
      ({"source": "nope", "measures": [{"key": "tickets"}]}, "source"),
      ({"source": "tickets", "measures": [{"key": "nope"}]}, "measures[0].key"),
      ({"source": "tickets", "dimensions": [{"key": "queue", "grain": "week"}], "measures": [{"key": "tickets"}]}, "dimensions[0].grain"),
      ({"source": "tickets", "dimensions": [{"key": "queue", "choices": ["nope"]}], "measures": [{"key": "tickets"}]}, "dimensions[0].choices[0]"),
      ({"source": "tickets", "measures": [{"key": "tickets"}], "time_field": "queue"}, "time_field"),
      ({"source": "tickets", "measures": [{"key": "tickets"}], "time_zone": "Mars/Base"}, "time_zone"),
      ({"source": "tickets", "measures": [{"key": "tickets"}], "parameters": [{"key": "q", "field": "nope"}]}, "parameters[0].field"),
      ({"source": "tickets", "measures": [{"key": "tickets"}], "visualization": {"type": "line", "series_dimension": 2}}, "visualization.series_dimension"),
      ({"source": "tickets", "measures": [{"key": "tickets"}], "filter": {"version": 1, "where": {"field": "nope", "op": "eq", "value": 1}}}, "filter.where"),
  ])
  def test_validate_definition_errors(bad, path) -> None:
      reg = Registry(); register_core(reg)
      with pytest.raises(DefinitionError) as exc:
          validate_definition(ReportDefinition.model_validate(bad), reg)
      assert exc.value.path.startswith(path)

  def test_definition_hash_is_stable_and_order_sensitive_only_where_it_matters() -> None:
      a = ReportDefinition.model_validate({"source": "tickets", "measures": [{"key": "tickets"}]})
      b = ReportDefinition.model_validate({"measures": [{"key": "tickets"}], "source": "tickets"})
      assert definition_hash(a) == definition_hash(b) and len(definition_hash(a)) == 64

  def test_every_source_dimension_and_measure_has_label_key_pattern() -> None:
      # label_key == f"reports.dimensions.{key}" / f"reports.measures.{key}"; filter-only fields "reports.dimensions.<key>" too
  ```
  `test_reporting_filter_sql.py`: compile conditions against a toy column map on both dialects (literal binds) — `eq`, `neq` (`IS NULL OR <>`), `in`, `not_in`, `contains` (`LIKE '%a\%b%' ESCAPE '\'` for value `a%b`), `between` datetime (`>= AND <`), `within_last` (with fixed `now`), `is_true` on a bool, nested `any`/`not`; unknown field → `FilterError` with path.
- [ ] **Step 2: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_reporting_definition.py tests/test_reporting_filter_sql.py`
- [ ] **Step 3: Implement** R8 definitions/semantic + the five sources exactly as listed in R8 (table objects from the ORM classes and from `tiqora.db.legacy.*` models; `open_tickets` measures read `ctx.now`; `now_epoch = int(ctx.now.replace(tzinfo=UTC).timestamp())`). `ReportSource.filter_spec` builds `FilterFieldSpec(key, type=dimension.filter_type, label_key=dimension.label_key, options_source=dimension.options_source)`; date dimensions use type `datetime`; enums (`state_type`, `channel`, `kind`) use type `string` restricted to ops `eq neq in not_in`.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): report definition schema and semantic layer with five sources`.

### Task 12: Query compiler with permission injection (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/compiler.py`
- Test: `backend/tests/test_reporting_compiler.py` (golden SQL pure + execution on both dialects, band 47520–47599)

**Interfaces:**
- Consumes: Tasks 10, 11; wave-0 `TicketQueryScope`.
- Produces: R8 compiler part verbatim (`RunParams`, `MeasurePlan`, `CompiledReport`, `CompileError`, `compile_report`).

- [ ] **Step 1: Write the failing tests.**
  ```python
  # sql(stmt, dialect): same 2-line helper as in test_reporting_sql.py (copy it; tests do not import each other)
  SCOPE = TicketQueryScope(user_id=47520, queue_ids=frozenset({47521, 47522}))
  REG = _registry()          # Registry() + register_core
  NOW = datetime(2024, 6, 12, 10, 0)

  def comp(defn: dict, **params) -> CompiledReport:
      return compile_report(ReportDefinition.model_validate(defn), RunParams(time_zone="Europe/Berlin", **params),
                            SCOPE, now=NOW, near_miss_percent=10, registry=REG)

  def test_empty_scope_compiles_to_none() -> None:
      c = compile_report(ReportDefinition.model_validate({"source": "tickets", "measures": [{"key": "tickets"}]}),
                         RunParams(), TicketQueryScope(user_id=1, queue_ids=frozenset()), now=NOW, near_miss_percent=10, registry=REG)
      assert c.stmt is None and c.raw_stmt is None

  def test_scope_and_window_in_sql() -> None:
      s = sql(comp({"source": "tickets", "dimensions": [{"key": "created", "grain": "week"}], "measures": [{"key": "tickets"}],
                    "time_range": {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-07"}}).stmt, mysql.dialect())
      assert "queue_id IN (47521, 47522)" in s
      assert "created_at >= '2024-05-31 22:00:00'" in s and "created_at < '2024-06-07 22:00:00'" in s
      assert "WEEKDAY(" in s and "GROUP BY" in s

  def test_parameter_cannot_widen_scope() -> None:
      c = comp({"source": "tickets", "measures": [{"key": "tickets"}],
                "parameters": [{"key": "queues", "field": "queue", "op": "in"}]}, parameters={"queues": [99999]})
      s = sql(c.stmt, mysql.dialect())
      assert "queue_id IN (47521, 47522)" in s and "99999" in s          # both ANDed: result empty, never widened

  def test_inline_any_not_cannot_widen_scope() -> None:
      flt = {"version": 1, "where": {"any": [{"field": "queue", "op": "eq", "value": 99999},
                                            {"not": {"field": "queue", "op": "in", "value": [47521]}}]}}
      s = sql(comp({"source": "tickets", "measures": [{"key": "tickets"}], "filter": flt}).stmt, postgresql.dialect())
      assert s.index("queue_id IN (47521, 47522)") < s.index("99999")     # scope outside, at top level of WHERE
      # structural check: the top-level WHERE is an AND whose first clause is the scope IN-list

  def test_parameter_value_outside_allowed_values_is_rejected() -> None:
      with pytest.raises(CompileError, match="parameters.queues"):
          comp({"source": "tickets", "measures": [{"key": "tickets"}],
                "parameters": [{"key": "queues", "field": "queue", "allowed_values": [47521]}]}, parameters={"queues": [47522]})

  def test_dimension_choice_and_grain_override() -> None:
      c = comp({"source": "open_tickets", "dimensions": [{"key": "queue", "choices": ["owner"]}], "measures": [{"key": "tickets"}]},
               dimension_choices={0: "owner"})
      assert c.definition.dimensions[0].key == "owner"
      with pytest.raises(CompileError):
          comp({"source": "open_tickets", "dimensions": [{"key": "queue", "choices": ["owner"]}], "measures": [{"key": "tickets"}]},
               dimension_choices={0: "state_type"})

  def test_rate_and_percentile_plans() -> None:
      c = comp({"source": "tickets", "measures": [{"key": "reopen_rate"}, {"key": "first_response_p50"}]})
      assert [m.columns for m in c.measures] == [("m0_num", "m0_den"), ()]
      assert c.raw_stmt is not None

  def test_stock_dates_for_weekly_snapshot() -> None:
      c = comp({"source": "daily_snapshot", "dimensions": [{"key": "snapshot", "grain": "week"}], "measures": [{"key": "open_tickets"}],
                "time_range": {"kind": "absolute", "start": "2024-06-03", "end": "2024-06-12"}})
      assert c.stock_dates == (date(2024, 6, 9), date(2024, 6, 12))

  def test_open_tickets_has_no_window_without_time_range() -> None:
      assert comp({"source": "open_tickets", "measures": [{"key": "tickets"}]}).window is None

  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_every_source_executes(url_fixture, request) -> None:
      # for each source: one definition using every dimension once (in groups of <= 3) and every measure,
      # execute stmt and raw_stmt against the seeded DB -> no SQL error on either dialect
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_compiler.py`
- [ ] **Step 3: Implement** per R8 compiler rules 1–8. Build `CompileContext(now, zone, tz_segments(zone, window.start_utc, window.end_utc) if window else tz_segments(zone, now - 400 d, now), near_miss_percent, window)`. Select labels `d0..d2` for dimensions, `m<i>` / `m<i>_num` / `m<i>_den` for measures; `raw_stmt` selects `d*` + `v_<measure key>` with `WHERE value IS NOT NULL`. Parameter conditions are turned into wave-0 `Condition`s and compiled with `compile_conditions` (so their validation is identical). Where clause = `and_(scope_in, *base_where, window_conds, *stock_conds, user_filter, extra_filter, *param_conds)` with scope first.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): query compiler with permission injection and runtime parameters`.

### Task 13: Report engine — execution, post-processing, labels, cache, access (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/engine.py`, `backend/src/tiqora/reporting/labels.py`, `backend/src/tiqora/reporting/cache.py`, `backend/src/tiqora/reporting/access.py`
- Modify: `backend/src/tiqora/reporting/schemas.py` (add the result models of R9: `Cell`, `ColumnType`, `ResultColumnOut`, `ReportTotalsOut`, `TimeWindowOut`, `ReportResultOut`)
- Test: `backend/tests/test_reporting_engine.py` (both dialects, band 47600–47699), `backend/tests/test_reporting_cache.py` (pure, in-memory fake backend)

**Interfaces:**
- Consumes: Tasks 1, 5, 8 (`facts_status`), 10–12; wave-0 `ticket_query_scope`, `load_section_values`.
- Produces: `OTHER_KEY`, `PERCENTILE_SAMPLE_CAP`, `estimate_rows`, `RunPlan`, `plan_report`, `run_report`, `raw_measure_values`, `resolve_labels`, R8b cache verbatim, `require_report_access`.

- [ ] **Step 1: Write the failing tests.** Seed (both dialects): agent 47600 with `ro` on queue 47601 only; queue 47602 not readable; 6 tickets in 47601 and 2 in 47602 with histories covering create/close/reopen/move, agent replies with known minutes, `time_accounting` rows; run `rebuild_ticket_facts` for all; `now = datetime(2024, 6, 20, 12, 0)`. A `_FakeBackend` with a dict implements `CacheBackend`.
  ```python
  async def test_counts_by_queue_only_visible(url_fixture, request) -> None:
      r = await run_report(s, defn=D({"source": "tickets", "dimensions": [{"key": "queue"}], "measures": [{"key": "tickets"}],
                                      "time_range": ABS_JUNE}), params=RunParams(time_zone="UTC"), user_id=47600,
                           report_key=None, reporting=ReportingSettings(), now=NOW)
      assert r.rows == [[47601, 6]] and r.labels["queue"]["47601"] == "RepQueue47601"
      assert [c.key for c in r.columns] == ["queue", "tickets"] and r.columns[1].type == "count"
  async def test_rates_and_percentiles(url_fixture, request) -> None:
      # reopen_rate == reopened / closed (exact fraction), first_response_p50 == the median of the seeded working minutes,
      # a group with denominator 0 -> None
  async def test_time_series_is_filled_with_zero_buckets(url_fixture, request) -> None:
      # created(day) over 2024-06-01..2024-06-07 with tickets on 2 days -> 7 rows, missing days have 0
  async def test_top_n_folds_into_other(url_fixture, request) -> None:
      # dims [state] top_n 1 -> 2 rows, second key "__other__" with the summed count
  async def test_totals_grand_rows_columns(url_fixture, request) -> None:
      # accounted_time dims [agent, booked(month)] measure time_units, totals all -> grand == sum, rows per agent, columns per month
  async def test_compare_previous(url_fixture, request) -> None:
      # window 06-11..06-20 -> previous.grand counts tickets of 06-01..06-10
  async def test_sort_limit_and_row_cap(url_fixture, request) -> None:
      # sort tickets asc, limit 1 -> one row; row_cap=1 with 2 rows -> truncated True
  async def test_empty_scope_returns_empty_result_without_query(url_fixture, request) -> None:
      # user without any ro queue -> rows [] and no SELECT on tiqora_report_ticket_fact (cursor-execute counter)
  async def test_facts_status_is_attached(url_fixture, request) -> None:
      # state backfill pending -> r.facts.ready is False
  async def test_open_tickets_live_source(url_fixture, request) -> None:
      # open_tickets by state_type counts only open tickets of readable queues, ignores facts (works with empty fact table)
  async def test_timeout_maps_to_report_query_timeout(url_fixture, request, monkeypatch) -> None:
      # monkeypatch session.execute inside the engine to raise an OperationalError with code 1969 / a QueryCanceled -> ReportQueryTimeout
  async def test_require_report_access(url_fixture, request) -> None:
      # access all_agents -> ok; stats_group: agent without `stats` ro -> HTTPException 403 with detail.message; with ro -> ok; admin -> ok
  ```
  `test_reporting_cache.py`:
  ```python
  def test_cache_key_differs_per_scope() -> None:
      k1 = ReportCache.key_for(definition_hash="h", queue_ids=[1, 2], window=None, time_zone="UTC", row_cap=10, compare_previous=False)
      k2 = ReportCache.key_for(definition_hash="h", queue_ids=[1], window=None, time_zone="UTC", row_cap=10, compare_previous=False)
      assert k1 != k2 and k1.startswith(CACHE_PREFIX)
      assert k1 == ReportCache.key_for(definition_hash="h", queue_ids=[2, 1], window=None, time_zone="UTC", row_cap=10, compare_previous=False)
  async def test_round_trip_and_ttl_zero() -> None   # put/get returns equal model with cached=True; ttl 0 -> get None
  async def test_backend_errors_are_swallowed() -> None   # backend.get raises ConnectionError -> None, no exception
  ```
  Engine cache test (DB): two `run_report` calls with the same fake cache → second `cached is True` and executes no SELECT; a call for a different user with another scope does not hit it.
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_engine.py tests/test_reporting_cache.py`
- [ ] **Step 3: Implement** R8 engine/labels/cache/access. Execution: `async with statement_timeout(session, reporting.query_timeout_seconds): rows = (await asyncio.wait_for(session.execute(stmt), reporting.query_timeout_seconds + 5)).all()`; `except Exception as exc: if is_timeout_error(exc) or isinstance(exc, TimeoutError):` first `(await session.connection()).invalidate()` (a cancelled aiomysql/asyncpg call leaves the connection mid-protocol; it must not return to the pool), then `raise ReportQueryTimeout from exc`. Percentile: sort values, `p = q * (n - 1)`, interpolate. Dimension cells: date → `value.isoformat()`, ints stay ints, `None` stays `None`. Measure column types from `Measure.unit` (`percent` for rates; `count` for count/count_if/count_distinct). Labels: for each dimension column with `labels != "none"` call `resolve_labels` with the distinct non-null values (plus `OTHER_KEY` skipped). `last_snapshot_date` read from the state row and passed via `RunParams`. Previous window = `TimeWindow(start - (end - start), start, ...)` with recomputed local dates.
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): report engine with percentiles, rates, totals, labels and result cache`.

### Task 14: Built-in report catalogue v1 (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/catalogue.py` (R11)
- Modify: `backend/src/tiqora/extensions/core.py` (`for r in core_builtin_reports(): registry.add(BUILTIN_REPORTS, r)`)
- Test: `backend/tests/test_reporting_catalogue.py` (pure + both dialects, band 47700–47749)

**Interfaces:**
- Consumes: Tasks 11–13.
- Produces: `ReportCategory`, `BuiltinReport`, `BUILTIN_REPORTS`, `core_builtin_reports`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  EXPECTED = ["ticket_kpis", "volume", "backlog_trend", "open_now", "agent_activity", "volume_by_channel", "channel_mix",
              "volume_by_queue", "backlog_by_state", "backlog_aging", "sla_compliance", "sla_times", "sla_by_customer",
              "first_contact_resolution", "reopen_trend", "queue_transfers", "pingpong", "time_in_state",
              "time_by_customer", "time_by_queue", "time_by_agent", "time_by_type", "time_by_agent_month",
              "top_customers", "created_heatmap"]

  def test_catalogue_keys_and_order() -> None:
      reg = Registry(); register_core(reg)
      assert [r.key for r in reg.items(BUILTIN_REPORTS)] == [f"builtin.{k}" for k in EXPECTED]

  def test_every_builtin_validates_and_has_queue_parameter() -> None:
      reg = Registry(); register_core(reg)
      for r in reg.items(BUILTIN_REPORTS):
          validate_definition(r.definition, reg)
          assert r.definition.time_zone is None
          assert [p.key for p in r.definition.parameters] == ["queues"]
          assert r.title_key == f"reports.builtin.{r.key.removeprefix('builtin.')}.title"

  def test_overview_reports() -> None:
      reg = Registry(); register_core(reg)
      assert {r.key for r in reg.items(BUILTIN_REPORTS) if r.overview} == {
          "builtin.ticket_kpis", "builtin.volume", "builtin.backlog_trend", "builtin.open_now", "builtin.agent_activity"}

  def test_line_and_bar_charts_plot_one_unit() -> None:
      # for every builtin with type in line/bar/stacked_bar/area: the plotted measures (chart_measures or all) share one unit

  @pytest.mark.asyncio
  @pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
  async def test_every_builtin_runs(url_fixture, request) -> None:
      # small seed (2 tickets, facts rebuilt, one snapshot day, one time_accounting row); for each builtin:
      # run_report(..., params=RunParams(time_zone="Europe/Berlin")) -> no exception, columns match dims + measures
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reporting_catalogue.py`
- [ ] **Step 3: Implement** R11 with a small helper:
  ```python
  QUEUE_PARAM_FIELD = {"tickets": "queue", "ticket_events": "ticket_queue", "daily_snapshot": "queue",
                       "accounted_time": "queue", "open_tickets": "queue"}
  def _report(name: str, category: ReportCategory, source: str, *, dims: list[dict[str, Any]], measures: list[str],
              viz: dict[str, Any], time_range: dict[str, Any] | None, overview: bool = False,
              filter_where: dict[str, Any] | None = None, time_field: str | None = None,
              totals: dict[str, bool] | None = None) -> BuiltinReport:
      return BuiltinReport(key=f"builtin.{name}", category=category, overview=overview,
          definition=ReportDefinition.model_validate({
              "source": source, "dimensions": dims, "measures": [{"key": m} for m in measures],
              "filter": {"version": 1, "where": filter_where} if filter_where else None,
              "parameters": [{"key": "queues", "field": QUEUE_PARAM_FIELD[source], "op": "in",
                              "label_key": "reports.parameters.queues"}],
              "time_field": time_field, "time_range": time_range, "visualization": viz,
              "totals": totals or {}}))
  LAST_30_DAYS = {"kind": "relative", "unit": "day", "count": 30, "include_current": True}
  LAST_12_WEEKS = {"kind": "relative", "unit": "week", "count": 12}
  LAST_MONTH = {"kind": "relative", "unit": "month", "count": 1}
  LAST_3_MONTHS = {"kind": "relative", "unit": "month", "count": 3}
  LAST_12_MONTHS_INCL = {"kind": "relative", "unit": "month", "count": 12, "include_current": True}
  ```
  and one `_report(...)` call per R11 row (same order as `EXPECTED`), e.g.
  ```python
  _report("volume", "volume", "ticket_events", dims=[{"key": "occurred", "grain": "week"}, {"key": "kind"}],
          measures=["events"], viz={"type": "line", "series_dimension": 1}, time_range=LAST_12_WEEKS, overview=True,
          filter_where={"field": "kind", "op": "in", "value": ["create", "close"]}),
  _report("open_now", "backlog", "open_tickets",
          dims=[{"key": "queue", "choices": ["state", "priority", "owner", "type", "service"], "top_n": 15}],
          measures=["tickets"], viz={"type": "bar"}, time_range=None, overview=True),
  _report("time_by_agent_month", "time", "accounted_time", dims=[{"key": "agent"}, {"key": "booked", "grain": "month"}],
          measures=["time_units"], viz={"type": "pivot"}, time_range=LAST_12_MONTHS_INCL,
          totals={"grand": True, "rows": True, "columns": True}),
  ```
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): built-in report catalogue v1 (25 read-only reports)`.

### Task 15: XLSX and CSV writers (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/export.py` (R10)
- Test: `backend/tests/test_reporting_export.py` (pure; reads back with `openpyxl.load_workbook`)

**Interfaces:**
- Consumes: Task 13 `ReportResultOut`.
- Produces: `XlsxColumn`, `XlsxSheet`, `write_xlsx_workbook`, `report_sheet`, `write_report_csv`.

- [ ] **Step 1: Write the failing tests.**
  ```python
  RESULT = ReportResultOut(report_key="builtin.time_by_customer",
      columns=[ResultColumnOut(key="customer", role="dimension", type="string", label_key="reports.dimensions.customer"),
               ResultColumnOut(key="time_units", role="measure", type="units", label_key="reports.measures.time_units"),
               ResultColumnOut(key="share", role="measure", type="percent", label_key="x")],
      rows=[["ACME", 12.5, 0.625], ["__other__", 7.5, 0.375]], labels={"customer": {"ACME": "Acme GmbH"}},
      totals=ReportTotalsOut(grand=[None, 20.0, 1.0]), previous=None, row_count=2, truncated=False, time_window=None,
      generated_at=datetime(2024, 6, 20, 12, 0), cached=False,
      facts=FactsStatusOut(ready=True, as_of=None, backfill_status="done", backfill_job_id=None, backfill_progress=None))

  def test_xlsx_structure() -> None:
      data = write_xlsx_workbook([report_sheet(RESULT, title="Zeit nach Kunde: Juni/2024", column_labels={"customer": "Kunde",
          "time_units": "Zeiteinheiten", "share": "Anteil"}, value_labels={"customer": {"__other__": "Weitere"}},
          totals_label="Summe", info_lines=["Zeitraum: 01.06.2024 – 30.06.2024"])])
      wb = openpyxl.load_workbook(io.BytesIO(data))
      assert wb.sheetnames == ["Zeit nach Kunde Juni2024", "Info"]
      ws = wb["Zeit nach Kunde Juni2024"]
      assert [c.value for c in ws[1]] == ["Kunde", "Zeiteinheiten", "Anteil"] and ws["A1"].font.b
      assert ws.freeze_panes == "A2"
      assert [c.value for c in ws[2]] == ["Acme GmbH", 12.5, 0.625] and ws["C2"].number_format == "0.0%"
      assert ws["A3"].value == "Weitere" and ws["B2"].number_format == "0.00"
      assert [c.value for c in ws[4]] == ["Summe", 20.0, 1.0] and ws["A4"].font.b
      assert wb["Info"]["A1"].value == "Zeitraum: 01.06.2024 – 30.06.2024"

  def test_date_cells_are_real_dates() -> None   # a date column "2024-06-03" -> datetime cell, number_format "yyyy-mm-dd"
  def test_sheet_names_are_unique_and_short() -> None   # two sheets titled with 40 identical chars -> "<31 chars>", "<28 chars> (2)"
  def test_csv_has_bom_semicolons_and_labels() -> None:
      data = write_report_csv(RESULT, column_labels={"customer": "Kunde"}, value_labels={}, totals_label="Summe")
      assert data.startswith(b"\xef\xbb\xbf")
      lines = data.decode("utf-8-sig").splitlines()
      assert lines[0] == "Kunde;time_units;share" and lines[1] == "Acme GmbH;12.5;0.625" and lines[-1] == "Summe;20.0;1.0"
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && uv run python -m pytest -q tests/test_reporting_export.py`
- [ ] **Step 3: Implement** R10 (`Workbook(write_only=True)`, `WriteOnlyCell` for header/totals fonts and number formats, header fill `PatternFill("solid", fgColor="EDF0F7")`; `freeze_panes` and `column_dimensions[...].width` set before the first `append`; widths need the rows twice → materialise up to 500 rows for the width scan, stream the rest).
- [ ] **Step 4: Run → PASS**, lint gates, commit `feat(reporting): XLSX and CSV writers for report results`.

### Task 16: Reports API, run/export jobs (backend)

**Files:**
- Create: `backend/src/tiqora/api/v1/reports.py`, `backend/src/tiqora/reporting/jobs.py`
- Modify: `backend/src/tiqora/reporting/schemas.py` (remaining R9 models: `ReportRunIn`, `ReportRunOut`, `ReportExportIn`, `BuiltinReportOut`, `ReportCatalogueOut`, `SourceDimensionOut`, `SourceMeasureOut`, `ReportSourceOut`), `backend/src/tiqora/api/v1/__init__.py` (include `reports.router` and `reports.sources_router`), `backend/src/tiqora/extensions/core.py` (register `REPORT_RUN_JOB`, `REPORT_EXPORT_JOB`)
- Test: `backend/tests/test_reports_api.py` (HTTP via `httpx.ASGITransport`, overriding `get_current_user` and `get_db` like `tests/test_agent_ticket_zoom_apis.py::_client_for`; MariaDB + PostgreSQL; band 47750–47849)
- Regenerate openapi files.

**Interfaces:**
- Consumes: Tasks 8, 11–15; wave-0 `enqueue_job`, `JobAccepted`, `JobRunner`, `JobFailed`, `JobContext.add_file`.
- Produces: R9 routes and jobs verbatim.

- [ ] **Step 1: Write the failing tests.**
  ```python
  async def test_catalogue_lists_builtins_and_facts_status(...) -> None:
      # GET /api/v1/reports -> 25 reports, first key "builtin.ticket_kpis", body["facts"]["backfill_status"] in (...)
  async def test_get_unknown_report_404(...) -> None
  async def test_run_builtin_inline(...) -> None:
      # POST /api/v1/reports/run {"report_key": "builtin.channel_mix", "time_range": {...}} -> 200, body["job"] is None,
      # body["result"]["columns"][0]["key"] == "channel"; time_window.start ends with "Z" or "+00:00"
  async def test_run_inline_definition(...) -> None:
      # {"definition": {"source": "tickets", "measures": [{"key": "tickets"}]}} -> 200
  async def test_run_requires_exactly_one_of_key_or_definition(...) -> None   # both / neither -> 422
  async def test_invalid_definition_422_with_path(...) -> None   # unknown measure -> 422 detail {"message", "path": "measures[0].key"}
  async def test_force_job_returns_202_and_job_result_is_report(...) -> None:
      # force_job True -> 202, body["job"]["job_id"]; JobRunner.run_once + wait_idle; GET /api/v1/jobs/{id} -> succeeded,
      # result["columns"] present; a repeated POST without force_job is answered inline with result["cached"] True
  async def test_threshold_triggers_job(...) -> None   # monkeypatch tiqora.api.v1.reports.estimate_rows to return 10**7 -> 202
  async def test_export_xlsx_job_file(...) -> None:
      # POST /api/v1/reports/export {"report_key": "builtin.time_by_customer", "format": "xlsx", "title": "Zeit", ...} -> 202;
      # run job -> file "zeit.xlsx", content_type "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      # bytes load with openpyxl; result {"rows": n}
  async def test_export_csv_job_file(...) -> None   # format csv -> "zeit.csv", "text/csv; charset=utf-8", BOM
  async def test_sources_endpoint(...) -> None   # GET /api/v1/reporting/sources -> 5 sources, tickets has dimension "queue" with options_source "queue"
  async def test_access_setting_stats_group(...) -> None   # access stats_group: agent without stats -> 403 on /reports and /reports/run
  async def test_timeout_is_504(...) -> None   # monkeypatch run_report to raise ReportQueryTimeout -> 504 with detail.message
  async def test_scope_is_reevaluated_in_job(...) -> None   # enqueue run job, then remove the agent's ro, run job -> result rows []
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_reports_api.py`
- [ ] **Step 3: Implement** R9. Shared helper `async def _prepare(session, user, body) -> tuple[ReportDefinition, RunParams, str | None]` (definition lookup, zone resolution via `valid_time_zone(body.time_zone) or definition.time_zone or await resolve_user_time_zone(...)`, `dimension_choices` keys parsed to int (non-int → 422)). `POST /reports/run` returns `JSONResponse(status_code=202, content=ReportRunOut(job=...).model_dump(mode="json"))` for jobs; declare `responses={202: {"model": ReportRunOut}}`. Export slug: `re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "bericht"` (umlauts: `ä→ae ö→oe ü→ue ß→ss` first). Export job runs `run_report(..., row_cap=reporting.export_row_limit, use_cache=False)`, builds `report_sheet` / `write_report_csv`, `ctx.add_file(...)`. Register routers in `api/v1/__init__.py`.
- [ ] **Step 4: Run → PASS**, regenerate openapi (Global Constraints), lint gates, commit `feat(reporting): reports API with inline or background runs and XLSX/CSV export`.

### Task 17: `/api/v1/stats/*` as thin wrappers (backend)

**Files:**
- Modify: `backend/src/tiqora/stats/service.py` (method bodies per R12; module docstring updated), `backend/src/tiqora/api/v1/stats.py` (only: call `require_report_access` in each route; docstring)
- Modify tests: `backend/tests/test_stats_service.py`, `backend/tests/test_stats_api.py` (seed realistic histories — `NewTicket` + `StateUpdate` rows — and call `rebuild_ticket_facts` for the seeded tickets before asserting); `backend/tests/test_stats_time_zone.py` stays green unchanged
- Test: new cases in `test_stats_service.py`

**Interfaces:**
- Consumes: Tasks 13–14 (`run_report`, `raw_measure_values`, built-ins).
- Produces: unchanged `StatsService` API and `/api/v1/stats/*` responses (shape-identical; semantics per R12).

- [ ] **Step 1: Adjust/extend the tests first.** Keep every existing assertion that is still true under R12; replace assertions that relied on the old semantics with the new ones and a one-line comment why (e.g. "closes now counted from state transitions into a closed state, not from every history row on a closed ticket"). New cases:
  ```python
  async def test_volume_counts_one_close_per_transition(mariadb_znuny_url) -> None:
      # ticket closed once, then a note added (history row with closed state) -> closed == 1 (old code counted 2)
  async def test_open_snapshot_owner_dimension_labels(mariadb_znuny_url) -> None   # DimensionCount.label == "<First> <Last>"
  async def test_sla_breaches_survive_close(mariadb_znuny_url) -> None:
      # ticket answered after its first-response target and then closed (escalation_* columns zeroed) -> first_response_breached == 1
  async def test_wrappers_respect_stats_group_setting(mariadb_znuny_url) -> None   # access stats_group -> /api/v1/stats/volume 403
  async def test_every_wrapper_accepts_every_filter(mariadb_znuny_url) -> None:
      # GET each of /stats/volume, /backlog, /open-snapshot, /sla, /agent-workload (+ .csv) with date_from, date_to,
      # queue_id, state_id, priority_id, type_id, customer_id, tz all set -> 200 (no 422 from an unknown filter field)
  ```
- [ ] **Step 2: Run → FAIL.** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_stats_service.py tests/test_stats_api.py tests/test_stats_time_zone.py`
- [ ] **Step 3: Implement** R12. `StatsFilters` → `TimeWindow(start_utc=date_from or (now - timedelta(days=3660)), end_utc=(date_to + timedelta(microseconds=1)) if date_to else now, start_date/end_date = local dates in filters.time_zone, time_zone)`; `extra_filter` from `state_id/priority_id/type_id/customer_id` (`eq` conditions on `state`, `priority`, `type`, `customer`); `queue_id` → `parameters={"queues": [queue_id]}`. Delete the now-unused private helpers (`_apply_filters`, `_bucket_key` only if no other module imports them — `grep -rn "_bucket_key\|_apply_filters" backend/src backend/tests`).
- [ ] **Step 4: Run → PASS** (+ full `tests -k stats`), lint gates, commit `refactor(stats): /api/v1/stats endpoints as thin wrappers over built-in reports`.

### Task 18: Frontend API wrapper, run hook, formatting, demo mocks (frontend)

**Files:**
- Create: `frontend/src/lib/reportsApi.ts`, `frontend/src/components/reports/useReportRun.ts`, `frontend/src/components/reports/reportFormat.ts`
- Modify: `frontend/src/demo/mockData.ts` (`resolveData` entries for `GET …/reports`, `GET …/reports/builtin.*`, `POST …/reports/export` → `{job_id: 9201, status: "queued"}`, `GET …/reporting/status`, `GET …/admin/reporting/status`, `POST …/admin/reporting/backfill`, `GET …/jobs/9201` (export job, succeeded, file `bericht.xlsx`); plus a pure exported `resolveReportRun(body: ReportRunIn): ReportRunOut` that picks the mock result by `report_key`, else by `definition.source`), `frontend/src/demo/handlers.ts` (an MSW `http.post("*/api/v1/reports/run", async ({ request }) => HttpResponse.json(resolveReportRun(await request.json())))` — `resolveData` never sees request bodies — and binary `GET */api/v1/jobs/9201/files/*` with an XLSX content type and a tiny body), `frontend/src/i18n/locales/en.json` + `de.json` (all `reports.*` keys of R14 except chart-only ones added in Task 19) + propagate
- Test: `frontend/src/components/reports/useReportRun.test.tsx`, `frontend/src/components/reports/reportFormat.test.ts`

**Interfaces:**
- Consumes: openapi types from Tasks 8 and 16; wave-0 `useJob`, `jobsApi`.
- Produces: R13 `reportsApi`, `useReportRun`, `cellLabel`, `formatMeasure`, `toSeries`, `SeriesData`, `exportLabels`.

- [ ] **Step 1: Write the failing tests.**
  ```ts
  // reportFormat.test.ts
  const result = { columns: [
      { key: "created", role: "dimension", type: "date", label_key: "reports.dimensions.created", grain: "week", i18n_values: null },
      { key: "channel", role: "dimension", type: "enum", label_key: "reports.dimensions.channel", grain: null, i18n_values: "reports.values.channel" },
      { key: "tickets", role: "measure", type: "count", label_key: "reports.measures.tickets", grain: null, i18n_values: null }],
    rows: [["2024-06-03", "email", 5], ["2024-06-03", "telegram", 2], ["2024-06-10", "email", 3]],
    labels: {}, /* …remaining ReportResultOut fields… */ } as ReportResult;
  it("labels enum values via i18n and other via reports.other", () => {
    expect(cellLabel(result, "channel", "telegram", t)).toBe("Telegram");
    expect(cellLabel(result, "channel", "__other__", t)).toBe("Weitere");
  });
  it("formats measures", () => {
    expect(formatMeasure(0.125, "percent", "de")).toBe("12,5 %");
    expect(formatMeasure(45, "minutes", "de")).toBe("45 min");
    expect(formatMeasure(210, "minutes", "de")).toBe("3,5 h");
    expect(formatMeasure(null, "count", "de")).toBe("—");
  });
  it("pivots rows into series by the series dimension", () => {
    const { categories, series } = toSeries(result, { type: "stacked_bar", series_dimension: 1, chart_measures: null }, t);
    expect(categories.map((c) => c.key)).toEqual(["2024-06-03", "2024-06-10"]);
    expect(series.map((s) => [s.key, s.values])).toEqual([["email", [5, 3]], ["telegram", [2, null]]]);
  });
  it("one series per plotted measure without series dimension", () => { /* chart_measures [0] -> one series */ });
  ```
  ```tsx
  // useReportRun.test.tsx — mock reportsApi.run / jobsApi.get
  it("returns an inline result", async () => { /* run -> {result, job: null} -> hook.result set, jobId null */ });
  it("polls the job and returns its result", async () => {
    /* run -> {result: null, job: {job_id: 5, status: "queued"}}; jobsApi.get -> running, then succeeded with result
       -> hook.jobId 5, finally hook.result.columns defined */ });
  it("surfaces a failed job as error", async () => { /* status failed, error "Zu groß" -> hook.error.message "Zu groß" */ });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/reports`
- [ ] **Step 3: Implement** R13 wrapper/hook/format. Number formatting with `Intl.NumberFormat(toBcp47(locale), …)`; dates with the existing `formatCalendarDay` (`@/lib/format`) — week buckets shown as "KW 23" via `reports.weekLabel` (`"KW {{week}}"` / `"Wk {{week}}"`, ISO week number), month buckets "Juni 2024" (`Intl.DateTimeFormat` month+year), hour `"09 Uhr"`/`"09:00"` via `reports.hourLabel`, weekday via `reports.values.weekday.<n>`. Demo mocks: catalogue with all 25 keys (definitions copied from R11 in JSON form; `facts.ready: true`), and a deterministic `ReportResultOut` per built-in key (small realistic made-up numbers, every field present). Add the R14 `reports.*` keys needed here (`title`, `subtitle`, categories, `builtin.*`, `dimensions.*`, `measures.*`, `values.*`, `parameters.queues`, `presets.*`, `grains.*`, `other`, `weekLabel`, `hourLabel`, `empty`, `error`, `loading`, `timeout`, `factsBuilding`, `factsBuildingProgress`, `factsAsOf`), propagate, check.
- [ ] **Step 4: Run → PASS** (`pnpm vitest run src/components/reports`, then `pnpm lint && pnpm test --run`, i18n check), commit `feat(reports): API wrapper, run hook and result formatting`.

### Task 19: Chart tokens and chart components (frontend)

**Files:**
- Modify: `frontend/src/themes/tokens.css` (R13 tokens, light block and dark block), `frontend/tailwind.config.js` (`series: {1..6}`, `seq: {1..7}` via `themeColor()`), `frontend/src/components/agent/stats/BarChart.tsx`, `LineChart.tsx`, `StatTile.tsx` (unchanged API; `StatTile` gains optional `testId` passthrough only if missing)
- Create: `frontend/src/components/agent/stats/chartColors.ts`, `ChartTooltip.tsx`, `ChartLegend.tsx`, `StackedBarChart.tsx`, `DonutChart.tsx`, `HeatmapChart.tsx`, `Sparkline.tsx`, `KpiTile.tsx`
- Test: `frontend/src/components/agent/stats/charts.test.tsx`, `frontend/src/components/agent/stats/chartColors.test.ts`; existing `StatTile.test.tsx` stays green
- Modify: `en.json`/`de.json` (`reports.chart.{noData,legend,tooltipOther,deltaUp,deltaDown,deltaFlat,ofTotal}`) + propagate

**Interfaces:**
- Consumes: nothing from Task 18 (pure components).
- Produces: R13 chart components, `SERIES_SLOTS`, `seriesColor`, `assignSeriesSlots`, `sequentialColor`.

- [ ] **Step 1: Validate the palette** (dataviz skill; the script lives in the dataviz skill's base directory — print it with the Skill tool if needed):
  ```bash
  node <dataviz-skill-dir>/scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a,#eda100,#e87ba4,#4a3aa7" --mode light --surface "#ffffff"
  node <dataviz-skill-dir>/scripts/validate_palette.js "#3987e5,#d95926,#199e70,#c98500,#d55181,#9085e9" --mode dark --surface "#151821"
  ```
  Expected: `ALL CHECKS PASS` for both (light: contrast WARN for three slots → the table view and visible value labels in this task are the required relief). If a check FAILs, stop and report — do not invent other colours.
- [ ] **Step 2: Write the failing tests.**
  ```ts
  // chartColors.test.ts
  it("orders id-valued series by key, not by value rank", () => {
    const a = assignSeriesSlots(["7", "3", "12"]);     // input order = value rank
    expect([a.get("3"), a.get("7"), a.get("12")]).toEqual([0, 1, 2]);   // numeric ascending
  });
  it("fixed enum order wins", () => {
    expect(assignSeriesSlots(["telegram", "email"], ["email", "phone", "telegram"]).get("telegram")).toBe(2);
  });
  it("slots beyond six fall back to muted", () => { expect(seriesColor(6)).toBe("var(--color-muted)"); });
  ```
  Enum-valued series get a fixed order so filters never repaint them — channel `email, phone, telegram, webchat, sms, whatsapp, internal`; state_type `new, open, pending reminder, pending auto, closed, merged, removed`; event kind in `EVENT_KINDS` order. Id-valued series (queues, agents) are ordered by id; a filter that removes a lower id can shift later colours — accepted limitation, documented in the component's doc comment.
  ```tsx
  // charts.test.tsx
  it("line chart shows a legend for two series and a tooltip on hover", async () => {
    render(<LineChart testId="lc" labels={["KW 1", "KW 2"]} series={[
      { name: "Erstellt", color: seriesColor(0), values: [3, 5] }, { name: "Geschlossen", color: seriesColor(1), values: [2, 4] }]} />);
    expect(screen.getByTestId("lc-legend")).toHaveTextContent("Erstellt");
    fireEvent.mouseMove(screen.getByTestId("lc-hit-1"));
    expect(await screen.findByTestId("lc-tooltip")).toHaveTextContent("KW 2");
    expect(screen.getByTestId("lc-tooltip")).toHaveTextContent("5");
  });
  it("single-series line has no legend box", () => { /* one series -> no lc-legend */ });
  it("area mode fills under the line", () => { /* area -> path[data-testid=lc-area-<name>] */ });
  it("stacked bar renders segments with a surface gap and stable order", () => {
    /* 2 categories x 2 series -> 4 rects lc-seg-<cat>-<series>; stroke var(--color-surface) strokeWidth 2 */ });
  it("donut renders one arc per slice and a centre label", () => { /* dn-slice-<key>, dn-center text */ });
  it("heatmap colours by sequential ramp and shows empty cells", () => {
    /* values [[0, 5],[10, null]] -> cell 1,0 uses var(--color-seq-7), null cell has class bg-surface-subtle-ish fill var(--color-surface-subtle) */ });
  it("kpi tile shows value, delta direction and sparkline", () => {
    render(<KpiTile label="Tickets" value="120" delta={{ value: 0.12, direction: "up", goodWhen: "down" }} sparkline={[1, 3, 2]} testId="k" />);
    expect(screen.getByTestId("k-delta")).toHaveTextContent("12");
    expect(screen.getByTestId("k-delta")).toHaveClass("text-danger");   // up is bad here; arrow icon + text, never colour alone
    expect(screen.getByTestId("k-sparkline")).toBeInTheDocument();
  });
  it("bar chart keeps its old props and test ids", () => { /* BarChart testId bc -> bc-bar-0 still rendered */ });
  ```
- [ ] **Step 3: Run → FAIL.** `cd frontend && pnpm vitest run src/components/agent/stats`
- [ ] **Step 4: Implement.** Rules for every chart: `viewBox` coordinates as today; marks thin (bars ≤ 70 % of band, 4 px rounded data end via `rx` on the value end only — use a `path` for bars), lines 2 px `vectorEffect="non-scaling-stroke"`, markers ≥ 8 px on hover only; one y axis with 3–5 recessive gridlines (`var(--color-hairline)`) and mono tabular numerals (`font-mono tabular-nums`, `text-muted`); 2 px `var(--color-surface)` stroke between adjacent fills; invisible hit rectangles per category (`${testId}-hit-${i}`) wider than the marks that set the hovered index; `ChartTooltip` (title + rows with a colour swatch beside ink-coloured text) and a crosshair line for line/area; `ChartLegend` (`${testId}-legend`) for ≥ 2 series; text never in series colours. `HeatmapChart`: rows × columns grid of `rect`s, colour = `sequentialColor(value / max)`, `null` → `var(--color-surface-subtle)`, row/column labels in muted ink, tooltip per cell. `DonutChart`: arcs with 2 px surface gaps, centre label (total), legend with values and shares. `KpiTile`: extends `StatTile` layout; delta = arrow icon + signed percent text, tone `text-green` when the direction equals `goodWhen`, `text-danger` otherwise, `text-muted` for flat; `Sparkline` 2 px line, no axes. Add tokens and Tailwind colours; i18n keys; propagate.
- [ ] **Step 5: Run → PASS**, `pnpm lint && pnpm test --run`, i18n check. The visual check (dataviz step 7) happens in Task 20 Step 5, where the charts are on real pages. Commit `feat(reports): chart tokens and stacked bar, donut, heatmap, KPI charts with tooltips`.

### Task 20: Reports pages — overview, catalogue, viewer; `/stats` moves (frontend)

**Files:**
- Create: `frontend/src/components/reports/ReportView.tsx`, `ReportTable.tsx`, `PivotTable.tsx`, `ReportFilterBar.tsx`, `FactsBanner.tsx`, `ReportExportButton.tsx`, `frontend/src/routes/agent/ReportsPage.tsx`, `frontend/src/routes/agent/ReportViewPage.tsx`
- Modify: `frontend/src/router.tsx` (routes of R13; `/agent/stats` → redirect), `frontend/src/components/layout/AgentShell.tsx` (nav item `to="/agent/reports"`, testid unchanged), `frontend/src/routes/agent/TimeAccountingReportPage.tsx` (link row "Auswertungen nach Kunde, Queue, Agent, Typ" → `/agent/reports?category=time`, testid `time-accounting-reports-link`), `en.json`/`de.json` (remaining `reports.*` keys) + propagate
- Delete: `frontend/src/routes/agent/StatsPage.tsx` (and `StatsPage.test.tsx` if it exists — port its still-meaningful assertions to `ReportsPage.test.tsx`)
- Test: `frontend/src/routes/agent/ReportsPage.test.tsx`, `frontend/src/routes/agent/ReportViewPage.test.tsx`, `frontend/src/components/reports/ReportView.test.tsx`, `frontend/src/components/reports/PivotTable.test.tsx`; update `AgentShell` tests that assert the stats link href (`grep -rln "agent-nav-stats" frontend/src`)

**Interfaces:**
- Consumes: Tasks 18, 19; wave-0 `JobDialog`, `MultiSelectInput`; existing `Tabs`, `SelectMenu`, `Button`, `Spinner`, `DATE_RANGE_PRESETS`/`dateRangeForPreset`/`isPresetActive` (`@/lib/dateRanges`), `displayTimeZone`, `flattenQueues`.
- Produces: R13 `ReportView`, pages and routes.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  // ReportView.test.tsx
  it("renders the chart type from the definition and toggles to the table", async () => {
    render(<Providers><ReportView report={{ key: "builtin.volume", definition: volumeDef }} result={volumeResult} /></Providers>);
    expect(screen.getByTestId("report-view-chart")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("report-view-toggle-table"));
    expect(screen.getByTestId("report-view-table")).toHaveTextContent("KW 23");
  });
  it("kpi visualization shows one tile per measure with previous-period delta", () => {});
  it("pivot shows row and column totals", () => {});
  it("shows a truncated hint", () => { /* result.truncated -> report-view-truncated */ });
  // PivotTable.test.tsx: 2 dims -> header row = second-dim labels, first column = first-dim labels, totals row/col, sticky classes
  // ReportsPage.test.tsx
  it("shows the facts banner while data is being built", async () => { /* catalogue.facts.ready false -> reports-facts-banner */ });
  it("runs the overview reports with the selected range and queue", async () => {
    /* click stats-preset-last7Days -> reportsApi.run called for builtin.ticket_kpis with time_range absolute from/to
       and parameters {queues: [id]} after picking a queue; compare_previous true for ticket_kpis */ });
  it("lists the catalogue grouped by category and links to the viewer", async () => {
    /* card reports-card-builtin.sla_compliance has href /agent/reports/builtin.sla_compliance */ });
  it("open-now dimension tabs switch the dimension choice", async () => {
    /* click tab owner -> run called with dimension_choices {"0": "owner"} */ });
  // ReportViewPage.test.tsx
  it("loads the definition, runs it and exports XLSX via a background job", async () => {
    /* reportsApi.get -> def; run -> result; click report-export-xlsx -> reportsApi.export called with format xlsx,
       title = translated report title, column_labels for every column; JobDialog opens (job-dialog) */ });
  it("grain select only for reports with a date dimension", () => {});
  it("shows the server message on 422/504", async () => { /* run rejects ApiError 504 {message} -> report-error text */ });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/components/reports src/routes/agent/ReportsPage.test.tsx src/routes/agent/ReportViewPage.test.tsx`
- [ ] **Step 3: Implement.**
  - `ReportFilterBar` props: `{ definition: ReportDefinition; value: ReportFilterState; onChange: (v: ReportFilterState) => void; testId?: string }` with `type ReportFilterState = { preset: DateRangePreset | "custom" | null; from: string; to: string; grain: Grain | null; choice: string | null; queues: number[] }`; presets reuse `DATE_RANGE_PRESETS` chips (testids `stats-preset-<key>` kept for continuity), custom date inputs, grain select only when the effective first dimension is a date, dimension-choice tabs when `choices` is non-empty, queue `MultiSelectInput` over `api.listQueues()`. `toRunIn(reportKey, state)` builds `ReportRunIn` (`time_range` absolute from `from/to`, omitted when `preset === null` so the definition default applies; `time_zone: displayTimeZone()`; `parameters: {queues}` when non-empty).
  - `ReportView`: `type` → `line`/`area` → `LineChart` (`area` prop), `bar` → `BarChart` (one series) or `StackedBarChart` (series dimension or several measures), `stacked_bar` → `StackedBarChart`, `donut` → `DonutChart`, `heatmap` → `HeatmapChart` (rows = first dim, columns = second dim), `kpi` → `KpiTile` per measure (`previous` → delta; `goodWhen` "down" for breach/rate-of-problem measures `response_breach_rate`, `solution_breach_rate`, `reopen_rate`, `first_response_*`, `solution_*`; "up" otherwise), `pivot` → `PivotTable`, `table` → `ReportTable`. Series colours from `assignSeriesSlots` (fixed enum orders from Task 19). Below every chart the toggle "Tabelle anzeigen" (relief rule).
  - `ReportsPage` (`/agent/reports`, testid `reports-page`): heading "Berichte", `FactsBanner` (`reports-facts-banner`; admin sees `JobProgress` via `useJob(facts.backfill_job_id)`, others the percentage), overview filter bar (presets + queue), KPI row (`builtin.ticket_kpis`, `compare_previous: true`, sparkline from `builtin.volume` created series), volume + backlog charts (`builtin.volume`, `builtin.backlog_trend`), open-now bar with tabs queue/state/priority/owner (`builtin.open_now`; testid `stats-open-snapshot-*` kept where the old page had them), agent table combining `open_now`(owner) and `agent_activity`; then the catalogue grouped by `reports.categories.*` with cards (`reports-card-<key>`), optional `?category=` search param scrolls to and highlights that group.
  - `ReportViewPage` (`/agent/reports/$reportKey`, testid `report-view-page`): back link, title/description, `ReportFilterBar` bound to the route search params, `ReportView`, `ReportExportButton` (XLSX + CSV; body from `exportLabels`, `info_lines` = translated "Zeitraum: …", "Zeitzone: …", "Queues: …"; `JobDialog` with `jobsApi`), errors via `apiErrorMessage` (`report-error`).
  - Router: add both routes, `validateSearch` for the viewer (numbers/dates validated like `agentTimeAccountingRoute`), replace `agentStatsRoute` component with `beforeLoad: () => { throw redirect({ to: "/agent/reports" }); }`.
- [ ] **Step 4: Run → PASS**, `pnpm lint && pnpm test --run`, i18n propagate + check.
- [ ] **Step 5: Demo-mode check (dataviz step 7 — look at it).** `cd frontend && VITE_DEMO=1 pnpm vite --port 5179`; open `/agent/reports`, `/agent/reports/builtin.created_heatmap`, `/agent/reports/builtin.time_by_agent_month`, `/agent/reports/builtin.backlog_aging`, `/agent/reports/builtin.channel_mix` in light and dark theme at desktop and 390 px width (Playwright per memory "UI-Check via Demo-Modus"); check no label collisions, legends present, tooltips appear, tables reachable; delete `.playwright-mcp` before committing.
- [ ] **Step 6: Commit** `feat(reports): reports overview, catalogue and viewer replace the stats page`.

### Task 21: Admin page "Berichtsdaten" and settings (frontend)

**Files:**
- Create: `frontend/src/routes/admin/ReportingAdminPage.tsx`, `frontend/src/routes/admin/ReportingAdminPage.test.tsx`
- Modify: `frontend/src/router.tsx` (`/admin/reporting`), `frontend/src/routes/admin/AdminHomePage.tsx` (tile "Berichtsdaten" next to the "Dienste"/Daemons tile, testid `admin-home-reporting`), `frontend/src/demo/mockData.ts` (already has the admin endpoints from Task 18 — extend if fields changed), `en.json`/`de.json` (`admin.reporting.*`, `admin.settings.reporting.*` incl. `fields.<field>.{label,help}` for all eight `ReportingSettings` fields and `fields.access.options.{all_agents,stats_group}`, `admin.home.reporting.*`, `jobs.kinds.core.{report_run,report_export,report_backfill}`) + propagate

**Interfaces:**
- Consumes: Task 18 `reportsApi.adminStatus/adminBackfill`; wave-0 `JobDialog`, `/admin/settings/$section` page.
- Produces: `/admin/reporting`.

- [ ] **Step 1: Write the failing tests.**
  ```tsx
  it("shows status numbers", async () => { /* adminStatus -> ready, as_of, history_lag 12, last_snapshot_date -> texts */ });
  it("starts a rebuild with an optional start date and shows progress", async () => {
    /* type 2024-01-01 into admin-reporting-since, click admin-reporting-rebuild, confirm ->
       adminBackfill({since: "2024-01-01", restart: true, snapshots: true}); JobDialog visible */ });
  it("links to the settings section", () => { /* admin-reporting-settings href /admin/settings/reporting */ });
  ```
- [ ] **Step 2: Run → FAIL.** `cd frontend && pnpm vitest run src/routes/admin/ReportingAdminPage.test.tsx src/routes/admin/AdminHomePage.test.tsx`
- [ ] **Step 3: Implement** (status card with `StatTile`s; rebuild form; confirm dialog with `admin.reporting.rebuildConfirm` = "Alle Berichtsdaten werden neu berechnet. Das kann bei vielen Tickets Stunden dauern; die Berichte bleiben währenddessen nutzbar."; German texts per R14).
- [ ] **Step 4: Run → PASS**, `pnpm lint && pnpm test --run`, i18n check, commit `feat(reports): admin page to watch and rebuild report data`.

### Task 22: Docs and full verification

**Files:**
- Modify: `docs/architecture.md` (replace section "Stats / reporting" with: fact tables + loop + gap-safe watermark + dirty queue + snapshots + backfill; semantic layer/compiler/engine/cache; sources; built-ins; API; permission rule "current queue, scope ANDed outside every user condition"; time zones via offset shifting; frontend pages/charts; 15–30 lines with module paths), `docs/compatibility.md` (row "Stats framework": "partial → Configurable statistics not yet (builder in phase 2); 25 built-in reports incl. Znuny `TicketAccountedTime`-style time accounting, SLA in working minutes from the Znuny calendars, XLSX/CSV export; no Znuny stats import / scheduling yet"; add "`TimeAccounting` history row written like Znuny" where time accounting is listed), `docs/deployment.md` (worker loop `report_facts`, `TIQORA_REPORT_FACTS_INTERVAL`, first-start backfill and `tiqora report backfill|status`, Redis used as report cache), `docs/parallel-operation.md` (reporting reads `ticket_history` written by both systems; read-only on Znuny tables; only Tiqora's own `TimeAccounting` history change), `docs/gdpr.md` (fact tables hold `customer_id`/`customer_user`; erasure deletes and rebuilds them)
- No code changes except fixes found by the sweep.

- [ ] **Step 1: Write the docs** (English, like the existing docs; German UI terms only with their English meaning). Do NOT touch `site/compare.html` or README claims.
- [ ] **Step 2: Full backend suite:** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q` → all green; `rtk git diff --stat backend/tests/db_leak_baseline.txt` empty. Lint gates.
- [ ] **Step 3: Frontend:** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 4: OpenAPI freshness:** regenerate both openapi files; `rtk git status` must show no diff afterwards (else commit the missed regeneration here).
- [ ] **Step 5: Alembic graph:** `cd backend && uv run alembic heads` shows exactly the expected heads (primary + gated chain); `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py`.
- [ ] **Step 6: Commit** `docs(reporting): architecture, compatibility, deployment and GDPR notes for reporting phase 1`.

---

## Self-review notes (for the executor)

- Spec coverage (phase 1 row): async jobs → wave 0 (consumed in Tasks 8, 16); fact tables + history parser + backfill → Tasks 1, 3–5, 7–9; working minutes from the escalation calendars → Tasks 2, 4; semantic layer + compiler (tickets, events, snapshots, accounted time; plus live open tickets) with permission injection and `date_bucket` → Tasks 10–12; built-in catalogue v1 → Task 14; XLSX export → Tasks 15–16; time-accounting reports by customer/queue/agent/type → Task 14 (`time_by_*`, `time_by_agent_month`); async run + polling + Redis cache → Tasks 13, 16; `/stats/*` thin wrappers → Task 17; chart types (stacked bar, area, donut, heatmap, KPI with sparkline/delta, pivot with sticky headers) → Tasks 19–20.
- Designed to fit later phases: `ReportDefinition` (`schema_version: 1`) is what phase 2 stores in `tiqora_report` and what the builder previews through `POST /reports/run {definition}`; `REPORT_SOURCES`/`BUILTIN_REPORTS` are extension points (plugins, process/chat analytics add sources); `write_xlsx_workbook` takes several sheets (phase 3 bundles); `ReportExportIn` labels move server-side for phase 3 scheduled mails; `FACT_VERSION` lets later phases add fact columns and rebuild lazily; event kinds already include `process_activity`; AI flag columns are filled now for phase 4.
- Not in this plan (deliberate): builder UI, saved reports/searches, `tiqora_report`/`tiqora_report_schedule`/`tiqora_report_run` tables, Znuny stats import, PDF/WeasyPrint, NL questions, MCP tools, dynamic-field dimensions, `articles`/`ai_usage` sources, mail-health and AI-impact reports, dashboard widgets, read replica.
- Shared files other wave-1 plans may also touch: the Alembic primary head (+ `test_migration_gate.py`, `test_migrate_cli.py`), `db/tiqora/models.py`, `extensions/core.py`, `config.py`, `domain/settings_store.py`, `worker/__main__.py`, `worker/services.py` + `test_admin_daemons.py` count, `api/v1/__init__.py`, `api/v1/admin/__init__.py`, `main.py`, `gdpr/erasure.py`, `router.tsx`, `AgentShell.tsx`, `AdminHomePage.tsx`, `themes/tokens.css`, `tailwind.config.js`, `demo/mockData.ts`, `demo/handlers.ts`, `en.json`/`de.json`. Rebase conflicts there are additive; re-run the gates after resolving.
