# Wave 3 — Znuny Stats Import, PDF Reports, Scheduled Delivery (Reporting phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents take over their Znuny statistics (from `xml_storage` or from a Znuny export file) as Tiqora saved reports or saved searches with a complete list of everything that could not be taken over; every report can be downloaded as a server-rendered PDF (WeasyPrint, server-side SVG charts) through a background job; and reports or bundles of reports are delivered on a schedule (daily/weekly/monthly/yearly, own time zone) as PDF, XLSX or CSV mail attachments to agents, groups, roles and — when an admin allows it — external addresses, with a run history, a "Jetzt senden" button and visible failures.

**Architecture:** Backend: a read-only Znuny stats package `tiqora.reporting.znuny_stats` (XMLHash rebuild from `xml_storage` rows without eval, Znuny export-file parser via `defusedxml`, pure mappers per stats object onto the wave-1/2 `ReportDefinition` or the wave-2 saved-search `Filter`, preview/apply API that stores through the existing `create_saved_report` / `upsert_imported` services); server-side texts and formatting for unattended runs (`reporting/texts.py` + committed label bundle extracted from the frontend locales); a pure SVG chart renderer (`reporting/svg.py`) and a WeasyPrint PDF writer (`reporting/pdf.py`, network-free `url_fetcher`); `pdf` as a third format of the existing `core.report_export` job; schedules (`tiqora_report_schedule*`), runs and run files (`tiqora_report_run*`), a pure recurrence calculator, the delivery job `core.report_delivery` (renders, stores, mails through `build_outbound_sender`), a worker loop `report_scheduler` (60 s) and a daily purge loop. Frontend: "Als PDF" in the export menu, a Znuny-stats import dialog on the reports overview, and schedule pages under `/agent/reports/schedules` with run history.

**Tech Stack:** FastAPI, SQLAlchemy 2 async (ORM + Core), Alembic two-chain, Pydantic v2, `defusedxml` (already locked), WeasyPrint (new; Pango/HarfBuzz native libs), `pypdf` (tests), `openpyxl` (existing XLSX writer), `zoneinfo`, React 19 + TanStack Router/Query, Tailwind tokens, Vitest + Testing Library, MSW demo mode, Playwright (tag-only e2e).

**Spec:** `docs/superpowers/specs/2026-10-08-reporting-analytics-design.md` — phase 3 row of "Phases" (§3 "Znuny import from `xml_storage`", §5 "PDF: server-side with WeasyPrint … `tiqora/reporting/svg.py`", §9 "Scheduled delivery", "API / UI surface" for `/report-schedules` and `/reports/import/znuny`, "Tests" for import fixtures and PDF text extraction). Master roadmap: `docs/superpowers/specs/2026-10-08-competitive-parity-roadmap-design.md` (table "Waves", wave 3 "Reporting 3"). Builds on `docs/superpowers/plans/2026-10-08-wave1-reporting-foundation.md` and `docs/superpowers/plans/2026-10-10-wave2-reporting-builder.md` (both implemented: `reporting/saved.py`, `reporting/run_service.py`, `reporting/jobs.py`, `reporting/export.py`, `saved_searches/*`).

## Open questions for the product owner

Every item below is decided conservatively in this plan and marked "Assumption:" where it is used. Each can be changed later without a schema change unless noted.

1. **Znuny TicketList stats become saved searches, not reports** (Assumption A1, D6): Tiqora reports have at most 3 dimensions and no "ticket list" source; the saved-search list is the closest thing. Column choice and `Limit` are not taken over (listed). Should a "ticket list" report source be added later instead?
2. **External e-mail recipients are off by default** (A2, D2/D8): new setting "Externe Empfänger für Berichte erlauben" defaults to false; even when on, only users with the share right may add them.
3. **PDFs and scheduled mails exist in German and English only** (A3, D9): other UI languages fall back to English; no RTL layout in the PDF.
4. **Recurrence is presets only** (A4, D10): daily (optionally Mon–Fri), weekly (weekdays), monthly (day 1–31, clamped to the month's last day), yearly — no free cron expression.
5. **One mail per delivery with all recipients in Bcc** (A5, D12): `To:` is the sender address; recipients do not see each other. Alternative: one mail per recipient.
6. **Recipients other than the owner need the share right** (A6, D8): with only the create right, an agent can schedule reports to himself only.
7. **Auto-pause after 3 consecutive failed scheduled runs** (A7, D12) with a mail to the owner.
8. **After downtime only one catch-up run** per schedule (A8, D13), never one per missed occurrence.
9. **Stats import runs inline, not as a background job** (A9, D7): bounded to 500 stats per preview and one statement timeout; revisit if a site has more.
10. **Imported stats are private saved reports/searches of the importing agent** (A10, D7); re-importing updates the importer's own copy; sharing is a manual step afterwards.
11. **PDF font is DejaVu Sans from the Debian package** (A11, D9), not the UI font Inter.
12. **Dynamic-field elements of Znuny stats are not taken over** (A12, D6): Tiqora report sources have no dynamic-field dimensions yet; they are listed per stat.
13. **Static stats: only `StateAction` is taken over, approximated** (A13, D6) as "Ereignisse pro Tag und Art" over the last month; every other static module is listed as not taken over.
14. **`ShowAsDashboardWidget` is not taken over** (A14, D6): the dashboards plan owns widgets; the import report says so.
15. **A schedule whose report became unavailable fails as a whole** (A15, D12): nothing is sent with a missing part; the owner gets a mail.
16. **Znuny export files (`.xml`) can be imported too** (A16, D7), not only `xml_storage` — cheap, and lets sites import from a Znuny that Tiqora is not connected to.
17. **Run files are kept 90 days in a Tiqora table, downloadable by owner and admins only** (A17, Deviation 1).
18. **Interactive PDF export uses the browser's translated labels; only unattended runs use the server text bundle** (A18, Deviation 6).

## Global Constraints

- Work in the wave-3 worktree and local branch the orchestrator names; never touch `<repo>` (main) and never commit to main; never push (a push to origin main deploys production); no branches/PRs on GitHub. Never `--no-verify` (PII hooks). Only made-up example data: domains `example.org` / `.test`, names like "Erika Beispiel". Use `rtk git`; check `rtk git log -1 --oneline` after committing (the pre-commit hook can block silently). Stage explicit paths only and run `rtk git diff --cached --name-only` before every commit (other agents share the index). Never amend/reset/rebase/stash; never restore files you did not change. Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- zsh runs with `noclobber`: use `>|` or the Write tool to overwrite files.
- **Writes only to `tiqora_*` tables**, except through existing Znuny write services (this plan writes no Znuny table at all; `xml_storage`, `search_profile`, `users`, `user_preferences`, `group_user`, `role_user` are read only). Tests may seed Znuny tables and must clean them up.
- **Never JOIN `tiqora_*` tables with Znuny tables** (prod MariaDB mixes collations → error 1267): load keys from one table, then query the other with an IN-list. Integer IN-lists on `users.id`, `permission_groups.id`, `roles.id` are fine; string joins never.
- **No `db.begin()` / `session.begin()` after the session autobegan** (skill `tiqora-route-read-blocks-session-begin`): routes and services use the request session and `commit()`; worker code opens its own `async with factory() as session` and commits explicitly.
- **No synchronous endpoint may run longer than a few seconds** (prod nginx `proxy_read_timeout 90s` → 504): PDF rendering, report runs for delivery and anything with unbounded size run as jobs (`tiqora.jobs`), the frontend polls (`JobDialog` / `useJob`). Inline endpoints in this plan (stats preview/apply, schedule CRUD, recurrence preview) are bounded by explicit caps stated in their task.
- **Migrations:** Alembic two-chain; new migration in `backend/alembic/versions_tiqora` with the **next free number at implementation time** after the current primary head (at plan time the head on main is `20261010_0072` after the web-chat merge — do not hardcode it; `ls backend/alembic/versions_tiqora | tail -3` and `cd backend && uv run alembic heads`; never reuse a gap such as `0069`); `down_revision` = that current head; follow skill `alembic-two-chain-new-head-forks-graph`; update `TIQORA_HEAD` in `backend/tests/test_migration_gate.py` and `version == …` in `backend/tests/test_migrate_cli.py`. ORM in `backend/src/tiqora/db/tiqora/models.py` must produce the migration's DDL (`backend/tests/test_tiqora_model_ddl.py`).
- **Both dialects:** every DB test runs on MariaDB AND PostgreSQL (`@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])`, pattern `backend/tests/test_reports_api.py`; module skeleton = wave-2 plan D13) and creates new tables with `TiqoraBase.metadata.create_all`.
- Backend test command: `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q <paths>` (never plain `uv run pytest`). Lint gates before every backend commit: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src/tiqora`. New DB test modules clean up their rows and are never added to `backend/tests/db_leak_baseline.txt`.
- Seed ids only in this plan's band **52000–52399** (sub-bands per task below); confirm free first with `grep -rnE "\b52[0-3][0-9]{2}\b" backend/tests` (skill `pytest-session-container-id-band-collisions`). Logins/names contain `rep52`. `xml_storage` test rows use `xml_type = 'Stats'` and `xml_key` = a band number as string and are deleted by explicit key list.
- Znuny SysConfig/YAML checkbox values are Perl-truthy strings: read with `SysConfig.get_bool` / `tiqora.util.perl.perl_truthy`, never `bool(...)`. Znuny stats XML `Fixed`, `SumRow`, `SumCol`, `Valid`, `ShowAsDashboardWidget` are Perl-truthy too.
- Timestamps written into new `tiqora_*` columns are naive UTC from `tiqora.jobs.service.utc_now()`; API datetimes use `tiqora.domain.schemas.UtcDateTime` (skill `tiqora-naive-utc-datetimes-display-2h-early`). Local wall-clock times (schedule time of day) are converted with `zoneinfo` only (skill `python-same-tzinfo-ignores-dst`).
- After any API model/route change: regenerate both openapi files and schema.d.ts: `cd backend && uv run tiqora openapi -o ../packages/api-client/openapi.json && uv run tiqora openapi -o ../docs/api/openapi.json && cd ../packages/api-client && pnpm build` (skill `tiqora-openapi-regen-or-ci-wipes-schema`); check `head -c 40 packages/api-client/openapi.json` starts with `{"components"`; never hand-edit schema.d.ts. A Pydantic response field WITH a default becomes REQUIRED in the generated TS type — demo mocks, test fixtures and `frontend/e2e/fixtures/mock-api.ts` carry it in the same commit (`cd frontend && pnpm lint` finds them).
- Frontend: `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs` (`pnpm lint` = eslint + tsc); **never run prettier/biome on frontend/**. New i18n keys in `en.json` + `de.json`, then `node frontend/scripts/propagate-i18n-keys.mjs` (skill `tiqora-i18n-key-propagation`; never `scaffold-locale.mjs` on existing locales). Frontend tasks that add i18n keys run sequentially, never in parallel. New screens get demo-mode mocks (`VITE_DEMO=1`) and are checked in demo mode with Playwright at 1280 px and 390 px, light + dark (delete `.playwright-mcp` before committing). Tailwind colours only via `themeColor()` tokens. API wrappers are hand-written `frontend/src/lib/*Api.ts` using `api.request<T>(method, path, {query, body, signal})` with `Schemas["…"]` types.
- **UI copy and every new German message (API errors, job messages, mail texts, PDF texts) use the polite "Sie" form**, plain words named by what agents/admins recognise ("Zeitplan", "Empfänger", "Jetzt senden", "Statistiken aus Znuny übernehmen", "Als PDF"). Do not copy the du-form of older wave-1 messages (e.g. "Bitte warte …"). No system jargon in UI text ("Job", "Payload", "XMLHash", "Definition").
- Znuny reference source (6.5 core) at `<repo>/znuny-6.5.22/` (read-only): `Kernel/System/Stats.pm` (`StatsGet`, `Export`, `Import`, `_GenerateDynamicStats`), `Kernel/System/XML.pm` (`XMLHashGet`, `_XMLHash2D`), `Kernel/System/Stats/Dynamic/*.pm` (`GetObjectAttributes`, `ExportWrapper`, `ImportWrapper`, `_KindsOfReporting`), `Kernel/System/Stats/Static/StateAction.pm`, sample files `scripts/test/sample/Stats/*.xml`. Golden peers under `tests/golden/` (manual, not in CI).
- Charts in PDF follow the dataviz rules of the frontend charts (`frontend/src/components/agent/stats/*`): light-theme series colours, mono numerals, "Weitere" muted, at most 6 series colours.

## Review Focus

1. **The schedule owner's rights change after the schedule was saved** — owner loses report access, a queue, is set invalid, or the saved report in the schedule is deleted/unshared → the next run computes with the owner's **current** scope (never a cached older scope), or fails as a whole with a German reason in the run history and a mail to the owner; nothing is sent to the recipients. Pinned in Task 11 (`test_run_uses_owner_scope_at_run_time`, `test_owner_without_access_fails_without_mail`, `test_deleted_report_fails_run_and_mails_owner`).
2. **Due times around DST and month ends, and worker downtime** — 02:30 on the spring-forward day, 02:30 on the fall-back day, the 31st in February, a worker that was down across 5 occurrences → each occurrence is computed in the schedule's zone, exactly one catch-up run happens, `next_run_at` is strictly in the future afterwards. Pinned in Task 9 (`test_spring_forward_gap_shifts_forward`, `test_fall_back_runs_once`, `test_monthly_31_clamps_to_last_day`) and Task 12 (`test_downtime_runs_one_catch_up`).
3. **Two scheduler ticks (two workers, or a slow tick overlapping the next) see the same due schedule** → exactly one run row and one delivery job per occurrence. Pinned in Task 12 (`test_concurrent_ticks_enqueue_once`).
4. **Hostile text reaches the PDF** — a report name, queue name, customer name or mail subject containing `<script>`, `<img src="http://evil.test/x">`, `<link href="file:///etc/passwd">`, `{{owner}}` or a 5000-character word → shown as literal text, no network or file access during rendering, no template injection, layout does not break. Pinned in Task 8 (`test_pdf_escapes_and_fetches_nothing`) and Task 11 (`test_subject_placeholders_are_not_recursive`).
5. **A Znuny stat Tiqora cannot fully express** — dynamic-field restriction, escalation time, `HalfYear` scale, upcoming periods, min/max measures, a static module, an export file whose queue names do not exist here, a `xml_content_value` with quotes/backslashes/newlines → every unmappable part appears in the import report with element, value and a German reason; the mappable rest is imported and runs; nothing is silently dropped; `xml_storage` is never written. Pinned in Task 2 (`test_values_with_quotes_backslashes_newlines`), Task 3 (`test_every_dropped_element_is_listed`), Task 4 (`test_kinds_of_reporting_partial`), Task 5 (`test_import_never_writes_znuny_tables`, `test_file_import_unknown_names_listed`).

Also pinned (not top five): a stat restricted to a Znuny group the agent is not in is not listed for import (Task 5); an `xml_storage` row set with a broken content key is reported per stat, the others still import (Task 2); the run history never stores recipient e-mail addresses (Task 11); an erased customer's e-mail address is removed from schedule recipients (Task 13); recipients without an e-mail address are named in the run detail, not silently skipped (Task 11); a PDF with more rows than the PDF limit says so on the page (Task 8).

---

## Design (fixed)

Do not rename anything in this section without updating this document in the same commit. Wave-0/1/2 names are used exactly as they exist in code today: `ReportDefinition`, `DimensionRef`, `MeasureRef`, `ReportParameter`, `Totals`, `Visualization`, `RelativeTimeRange`, `AbsoluteTimeRange`, `TimeRange`, `Grain`, `validate_definition`, `DefinitionError`, `REPORT_SOURCES`, `BUILTIN_REPORTS`, `run_report`, `QueryBudget`, `ReportQueryTimeout`, `ReportResultOut`, `ResultColumnOut`, `ReportExportIn`, `ReportExportPayload`, `REPORT_EXPORT_KIND`, `run_export_job`, `export_slug`, `report_sheet`, `write_report_csv`, `write_xlsx_workbook`, `XlsxSheet`, `OTHER_KEY`, `has_report_access`, `require_report_access`, `report_rights`, `ReportRights`, `load_reporting_settings`, `ReportingSettings`, `REPORTING_SECTION`, `resolve_report_ref`, `ResolvedReport`, `ReportNotFound`, `ReportGone`, `create_saved_report`, `update_saved_report`, `get_saved_report`, `SavedReportIn`, `SavedReportUpdateIn`, `prepare_ref`, `RunOverrides`, `RequestError`, `check_share_groups`, `share_groups_for`, `ShareGroupError`, `ReportOwnerOut`, `map_search_profile`, `ZnunyLookups`, `MappedProfile`, `UnmappedItem`, `load_lookups`, `upsert_imported`, `ProfileValues`, `TicketSortKey`, `enqueue_job`, `utc_now`, `JobHandler`, `JobContext`, `JobFailed`, `JobRetry`, `JobCancelled`, `JOB_HANDLERS`, `JobAccepted`, `build_outbound_sender`, `build_message`, `is_permanent_smtp_failure`, `write_mail_log`, `bulk_get_preferences`, `PermissionEngine` (`is_admin`, `is_valid_user`, `users_for_group_permission`, `groups_for_permission`), `resolve_user_time_zone`, `valid_time_zone`, `DaemonService`, `DAEMON_SERVICES`, `_interval_loop`, `_daily_loop`, `scrub_saved_criteria`, frontend `reportsApi`, `ReportExportButton`, `exportLabels`, `JobDialog`, `ReportsAccessGate`.

### D1. File map

Backend (new unless marked):

| Path | Responsibility |
|---|---|
| `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_report_schedules.py` | five tables of D2 |
| modify `backend/src/tiqora/db/tiqora/models.py` | `TiqoraReportSchedule`, `TiqoraReportScheduleItem`, `TiqoraReportScheduleRecipient`, `TiqoraReportRun`, `TiqoraReportRunFile` |
| modify `backend/src/tiqora/reporting/settings.py` | new `ReportingSettings` fields (D2) |
| `backend/src/tiqora/reporting/znuny_stats/__init__.py` | docstring only |
| `backend/src/tiqora/reporting/znuny_stats/xml_hash.py` | pure: content-key tokenizer, XMLHash rebuild, export-file parse |
| `backend/src/tiqora/reporting/znuny_stats/model.py` | `StatElement`, `ZnunyStat`, `stat_from_xml_hash` |
| `backend/src/tiqora/reporting/znuny_stats/reader.py` | read-only DB access: `xml_storage` stats, lookups |
| `backend/src/tiqora/reporting/znuny_stats/mapping_common.py` | `ImportIssue`, `StatLookups`, element/time/total/visualisation mapping |
| `backend/src/tiqora/reporting/znuny_stats/mapper.py` | `StatMapping`, `MappedSearch`, `map_stat` dispatcher + `Dynamic::Ticket` + `Static::StateAction` |
| `backend/src/tiqora/reporting/znuny_stats/map_more.py` | `Dynamic::TicketAccountedTime`, `Dynamic::TicketSolutionResponseTime`, `Dynamic::TicketList` |
| `backend/src/tiqora/reporting/znuny_stats/service.py` | preview + apply (stores via saved reports / saved searches) |
| `backend/src/tiqora/reporting/znuny_stats/schemas.py` | API models of D7 |
| modify `backend/src/tiqora/api/v1/reports.py` | `/reports/import/znuny*` routes (static paths before `/{report_key}`) |
| `backend/src/tiqora/reporting/texts.py` | `ReportTexts`, `load_texts`, `server_export_labels`, number/date formatting de/en; `python -m tiqora.reporting.texts --sync` |
| `backend/src/tiqora/reporting/texts_data/reports.de.json`, `reports.en.json`, `__init__.py` | committed label bundle (the `reports` namespace of the frontend locales) |
| `backend/src/tiqora/reporting/svg.py` | pure SVG charts: bar, stacked_bar, line, area, donut, heatmap, kpi |
| `backend/src/tiqora/reporting/pdf.py` | `ReportSection`, `render_report_html`, `write_report_pdf` (WeasyPrint, no network) |
| modify `backend/src/tiqora/reporting/schemas.py` | `ReportExportIn.format` gains `"pdf"`, new `locale` |
| modify `backend/src/tiqora/reporting/jobs.py` | PDF branch of `run_export_job` |
| `backend/src/tiqora/reporting/recurrence.py` | pure: `Recurrence`, `next_occurrence`, `upcoming` |
| `backend/src/tiqora/reporting/schedules.py` | schedule store, validation, recipient resolution |
| `backend/src/tiqora/reporting/schedule_schemas.py` | API models of D11 |
| `backend/src/tiqora/api/v1/report_schedules.py` | `/api/v1/report-schedules*`, `/api/v1/report-runs/*` |
| `backend/src/tiqora/reporting/delivery.py` | `core.report_delivery` job: run, render, store, mail; failure handling |
| `backend/src/tiqora/reporting/scheduler.py` | `run_report_scheduler_tick`, `purge_report_runs` |
| `backend/src/tiqora/worker/report_scheduler.py` | worker wrappers (enabled flag) |
| modify `backend/src/tiqora/worker/__main__.py`, `worker/services.py`, `domain/settings_store.py`, `config.py` | loops, daemon catalog entries, setting keys, interval default |
| modify `backend/src/tiqora/jobs/registry.py` or the place the core handlers are registered (grep `REPORT_EXPORT_JOB`) | register `REPORT_DELIVERY_JOB` |
| modify `backend/src/tiqora/domain/api_key_scopes.py` | `/api/v1/report-schedules`, `/api/v1/report-runs` → `stats` |
| modify `backend/src/tiqora/api/v1/__init__.py` | register `report_schedules.router` |
| modify `backend/src/tiqora/gdpr/saved_criteria.py`, `gdpr/erasure.py`, `domain/user_delete.py` | D14 |
| modify `backend/pyproject.toml`, root `uv.lock`, `Dockerfile`, `.github/workflows/ci.yml` | WeasyPrint + native libs (Task 8) |
| `backend/tests/fixtures/znuny_stats/*.xml` | made-up stats in Znuny export format (Task 2) |
| `tests/golden/test_stats_import.py` | golden check against a real Znuny peer (manual) |

Frontend (new unless marked):

| Path | Responsibility |
|---|---|
| modify `frontend/src/lib/reportsApi.ts` | `znunyStatsPreview`, `znunyStatsFilePreview`, `znunyStatsApply`; export format `pdf` + `locale` |
| `frontend/src/lib/reportSchedulesApi.ts` | schedules CRUD, run now, runs, run file URL, recurrence preview |
| `frontend/src/demo/schedulesMock.ts` (+ modify `demo/mockData.ts`, `demo/reportsMock.ts`) | demo data |
| modify `frontend/src/components/reports/ReportExportButton.tsx` | "Als PDF" |
| `frontend/src/components/reports/ZnunyStatsImportDialog.tsx` | preview list, issues, file upload, apply |
| `frontend/src/components/reports/schedules/scheduleForm.ts` | pure: form state ⇄ API, validation |
| `frontend/src/components/reports/schedules/RecurrenceFields.tsx`, `RecipientsFields.tsx`, `ScheduleReportsPicker.tsx`, `ScheduleRunsTable.tsx` | editor parts |
| `frontend/src/routes/agent/ReportSchedulesPage.tsx` | `/agent/reports/schedules` |
| `frontend/src/routes/agent/ReportScheduleEditorPage.tsx` | `/agent/reports/schedules/new`, `/agent/reports/schedules/$scheduleId` |
| modify `frontend/src/router.tsx`, `routes/agent/ReportsPage.tsx`, `routes/agent/ReportViewPage.tsx` | routes, overview buttons, "Zeitplan anlegen" |
| modify `frontend/e2e/fixtures/mock-api.ts`; new `frontend/e2e/report-schedules.spec.ts` | e2e |

### D2. Tables (one migration, Task 1)

```
tiqora_report_schedule
  id                BIGINT PK autoincrement
  owner_user_id     INT NOT NULL                       -- Znuny users.id (no FK); runs with this user's permissions
  name              VARCHAR(200) NOT NULL
  format            VARCHAR(4) NOT NULL default 'pdf'  -- pdf | xlsx | csv
  recurrence        TEXT NOT NULL                      -- Recurrence JSON (D10)
  time_zone         VARCHAR(64) NOT NULL               -- IANA zone the recurrence is evaluated in
  language          VARCHAR(5) NOT NULL default 'de'   -- de | en (A3)
  subject           VARCHAR(250) NOT NULL              -- '' = default subject of the language
  body              TEXT NOT NULL (MEDIUM_TEXT_VARIANT)  -- '' = default body
  only_if_nonempty  BOOL NOT NULL default false
  active            BOOL NOT NULL default true
  paused_reason     VARCHAR(500) NULL                  -- German; set by auto-pause (A7)
  next_run_at       DATETIME NULL                      -- naive UTC; NULL while inactive
  last_run_at       DATETIME NULL
  last_status       VARCHAR(10) NULL                   -- succeeded | partial | failed | skipped
  failure_count     INT NOT NULL default 0             -- consecutive failed scheduled runs
  version           INT NOT NULL default 1             -- optimistic lock
  created_at        DATETIME NOT NULL
  changed_at        DATETIME NOT NULL
  INDEX ix_tiqora_report_schedule_due (active, next_run_at)
  INDEX ix_tiqora_report_schedule_owner (owner_user_id)

tiqora_report_schedule_item                          -- the reports of a bundle, in order
  schedule_id  BIGINT NOT NULL FK fk_tiqora_report_schedule_item_schedule -> tiqora_report_schedule.id ON DELETE CASCADE
  position     SMALLINT NOT NULL                     -- 0..9
  report_key   VARCHAR(120) NOT NULL                 -- "builtin.<name>" | "<saved id>"
  overrides    TEXT NULL                             -- ScheduleItemOverrides JSON (D11)
  PRIMARY KEY (schedule_id, position)
  INDEX ix_tiqora_report_schedule_item_key (report_key)

tiqora_report_schedule_recipient
  id           BIGINT PK autoincrement
  schedule_id  BIGINT NOT NULL FK fk_tiqora_report_schedule_recipient_schedule -> tiqora_report_schedule.id ON DELETE CASCADE
  kind         VARCHAR(5) NOT NULL                   -- user | group | role | email
  ref_id       INT NULL                              -- users.id / permission_groups.id / roles.id (kind != email)
  email        VARCHAR(250) NULL                     -- kind = email; stored lower-case
  INDEX ix_tiqora_report_schedule_recipient_schedule (schedule_id)
  INDEX ix_tiqora_report_schedule_recipient_email (email)

tiqora_report_run                                    -- one row per delivery (bundle), Deviation 2
  id              BIGINT PK autoincrement
  schedule_id     BIGINT NULL                        -- no FK: runs outlive a deleted schedule until retention
  schedule_name   VARCHAR(200) NOT NULL              -- name at run time
  owner_user_id   INT NOT NULL
  run_kind        VARCHAR(10) NOT NULL               -- schedule | manual
  owner_only      BOOL NOT NULL default false        -- manual "nur an mich"
  due_at          DATETIME NULL                      -- the occurrence (schedule runs)
  job_id          BIGINT NULL
  status          VARCHAR(10) NOT NULL               -- queued | running | succeeded | partial | failed | skipped
  report_keys     TEXT NOT NULL                      -- JSON list at run time
  row_count       INT NULL
  recipient_count INT NULL
  detail          TEXT NULL                          -- German lines; NEVER e-mail addresses
  error           VARCHAR(1000) NULL                 -- German
  created_at      DATETIME NOT NULL
  started_at      DATETIME NULL
  finished_at     DATETIME NULL
  INDEX ix_tiqora_report_run_schedule (schedule_id, id)
  INDEX ix_tiqora_report_run_finished (finished_at)
  INDEX ix_tiqora_report_run_owner (owner_user_id)

tiqora_report_run_file
  id            BIGINT PK autoincrement
  run_id        BIGINT NOT NULL FK fk_tiqora_report_run_file_run -> tiqora_report_run.id ON DELETE CASCADE
  name          VARCHAR(255) NOT NULL
  content_type  VARCHAR(100) NOT NULL
  size_bytes    BIGINT NOT NULL
  data          JOB_FILE_BLOB NOT NULL (deferred)     -- LONGBLOB on mysql/mariadb, bytea on postgres
  created_at    DATETIME NOT NULL
  INDEX ix_tiqora_report_run_file_run (run_id)
```

Booleans `server_default=false()` / `true()`; junction FK column `schedule_id` of `tiqora_report_schedule_item` `autoincrement=False`. Column names avoid SQL keywords (`run_kind`, not `trigger`). No row is seeded by the migration.

New `ReportingSettings` fields (admin page renders them through the generic settings section; i18n keys `admin.settings.reporting.fields.<name>.label/.help` — check the existing key shape in `de.json` under `admin.settings.reporting` and copy it):

```python
schedule_external_recipients: bool = False                    # A2 "Externe Empfänger erlauben"
schedule_max_recipients: int = Field(50, ge=1, le=500)        # resolved addresses per delivery
run_retention_days: int = Field(90, ge=7, le=730)             # tiqora_report_run + files
pdf_row_limit: int = Field(1000, ge=100, le=5000)             # rows per report in a PDF
delivery_max_attachment_mb: int = Field(15, ge=1, le=50)      # all attachments of one mail
```

### D3. Znuny XMLHash (Task 2)

Znuny stores a stat in `xml_storage` as one row per leaf: `xml_type = 'Stats'`, `xml_key = <StatID>`, `xml_content_key` like `[1]{'otrs_stats'}[1]{'UseAsXvalue'}[1]{'SelectedValues'}[2]{'Content'}`, `xml_content_value` = the text (NULL = ''). `XMLHashGet` eval's these as Perl; Tiqora parses them with a tokenizer (never eval). Inside the content-key, `'` and `\` in hash keys are escaped as `\'` and `\\` (verified: `_XMLHash2D` does not escape keys, only `XMLHashGet` escapes values; the tokenizer still accepts the escapes).

```python
# backend/src/tiqora/reporting/znuny_stats/xml_hash.py
MAX_STAT_ROWS: Final = 20_000                 # leaves per stat; more -> XmlHashError
MAX_DEPTH: Final = 12
MAX_FILE_BYTES: Final = 1_000_000                # ruling 33: base64 ~1.37 MB behind the proxy
class XmlHashError(ValueError):               # German message for the import report
    def __init__(self, message: str) -> None
Path_ = list[int | str]
def parse_content_key(key: str) -> Path_
    # "[1]{'otrs_stats'}[1]{'Title'}[1]{'Content'}" -> [1, "otrs_stats", 1, "Title", 1, "Content"]
    # grammar: ( "[" digits "]" | "{'" ( [^'\\] | "\\'" | "\\\\" )* "'}" )+ ; anything else -> XmlHashError
def build_xml_hash(rows: Iterable[tuple[str, str | None]]) -> list[Any]
    # Znuny @XMLHash shape: list index 0 = None; hash keys -> dict; [n] -> list padded with None.
    # Value None -> "". A path that conflicts (list vs dict at the same place) -> XmlHashError.
def xml_file_to_hash(content: bytes) -> list[Any]
    # defusedxml.ElementTree.fromstring (no DTD/entities); size > MAX_FILE_BYTES -> XmlHashError;
    # same shape as Znuny XMLParse2XMLHash: element -> {"Content": text, **attributes}, repeated tags -> list.
    # Root must be <otrs_stats> else XmlHashError("Die Datei ist keine Znuny-Statistik.")
```

```python
# backend/src/tiqora/reporting/znuny_stats/model.py
ElementUse = Literal["x", "series", "restriction"]
TIME_KEYS: Final = ("TimeStart", "TimeStop", "TimeRelativeUnit", "TimeRelativeCount",
                    "TimeRelativeUpcomingCount", "TimeScaleCount")
@dataclass(frozen=True, slots=True)
class StatElement:
    use: ElementUse
    element: str                       # "QueueIDs", "CreateTime", "KindsOfReporting", "DynamicField_X", ...
    fixed: bool                        # Perl-truthy "Fixed"; False = changeable at runtime
    selected: tuple[str, ...]          # SelectedValues contents in order ('' skipped)
    time: Mapping[str, str]            # only the TIME_KEYS present
@dataclass(frozen=True, slots=True)
class ZnunyStat:
    origin: Literal["db", "file"]
    stat_id: int | None                # xml_key for db; None for a file
    stat_number: str | None
    title: str
    description: str
    stat_type: str                     # "dynamic" | "static" (anything else kept, mapped to "none")
    object_module: str                 # "Kernel::System::Stats::Dynamic::Ticket"
    static_file: str | None            # static stats: "StateAction"
    valid: bool
    permission: tuple[str, ...]        # group ids (db) or group names (file), raw
    formats: tuple[str, ...]           # "Print", "CSV", "Excel", "D3::BarChart", ...
    sum_row: bool
    sum_col: bool
    time_zone: str | None
    show_as_dashboard_widget: bool
    elements: tuple[StatElement, ...]
    @property
    def import_key(self) -> str        # "znuny.stat.<StatID>" (db) | "znuny.file.<sha1(title|object_module)[:12]>"
def stat_from_xml_hash(xml_hash: list[Any], *, origin: Literal["db", "file"], stat_id: int | None) -> ZnunyStat
    # reads xml_hash[1]["otrs_stats"][1] like StatsGet: string keys from [1]["Content"],
    # list keys (Permission, Format) from every [n]["Content"], UseAsXvalue/UseAsValueSeries/
    # UseAsRestriction lists -> StatElement (attributes "Element", "Fixed", TIME_KEYS; SelectedValues[n]["Content"]).
    # elements are ordered by use (x, series, restriction), then by their index [n] - never by document order
    # (xml_storage rows carry none), so db and file origin give the same order.
    # Missing otrs_stats / Title / ObjectModule -> XmlHashError.
```

### D4. Reader (Task 2)

```python
# backend/src/tiqora/reporting/znuny_stats/reader.py  (read-only; no JOIN with tiqora_* tables)
MAX_PREVIEW_STATS: Final = 500
@dataclass(frozen=True, slots=True)
class StatLoadError:
    stat_id: int
    message: str                                  # German
async def list_stat_ids(session: AsyncSession) -> list[int]
    # SELECT DISTINCT xml_key FROM xml_storage WHERE xml_type = 'Stats'; non-numeric keys skipped; sorted; capped
async def load_stats(session: AsyncSession, stat_ids: Sequence[int], *, row_budget: int = MAX_TOTAL_ROWS) -> tuple[list[ZnunyStat], list[StatLoadError], bool]  # third value: truncated (row budget hit); leaves counted per chunk first
async def list_stat_ids(session: AsyncSession, *, permission_group_ids: Collection[int] | None = None) -> list[int]  # non-admins: Permission leaves filter BEFORE the 500 cap (F10); the inline statement timeout (QueryBudget.inline) is applied by the Task 5 caller
    # one SELECT xml_key, xml_content_key, xml_content_value ... WHERE xml_type='Stats' AND xml_key IN (:keys as str)
    # (chunked by 200); per stat build_xml_hash + stat_from_xml_hash; XmlHashError -> StatLoadError, others continue
async def load_stat_lookups(session: AsyncSession, user_id: int) -> StatLookups     # D5
```

### D5. Mapping contracts (Tasks 3–4)

```python
# backend/src/tiqora/reporting/znuny_stats/mapping_common.py
IssueSeverity = Literal["dropped", "approximated"]
@dataclass(frozen=True, slots=True)
class ImportIssue:
    element: str           # ONE rule: an axis/restriction element -> "<Use>.<Element>" with Use in UseAsXvalue |
                           # UseAsValueSeries | UseAsRestriction ("UseAsRestriction.LockIDs", "UseAsXvalue.KindsOfReporting",
                           # "UseAsXvalue.TicketAttributes", "UseAsRestriction.Limit"); a stat-level key -> the key itself
                           # ("Object", "Format", "SumRow", "Valid", "TimeZone", "ShowAsDashboardWidget")
    value: str             # the offending value (a selected value, a kind name, the module name), shortened to 200
    reason: str            # German, Sie-form
    severity: IssueSeverity
@dataclass(frozen=True, slots=True)
class StatLookups:
    state_type_names: Mapping[int, str]          # ticket_state_type.id -> name
    queue_ids_by_name: Mapping[str, int]         # casefolded full queue name -> id (file import)
    state_ids_by_name: Mapping[str, int]         # casefolded state name -> id
    priority_ids_by_name: Mapping[str, int]
    user_ids_by_login: Mapping[str, int]         # casefolded login -> id
    group_ids_by_name: Mapping[str, int]         # casefolded group name -> id (file Permission)
    default_time_zone: str                       # the importing agent's zone
    search: ZnunyLookups                         # wave-2 lookups for map_search_profile (TicketList)
@dataclass(frozen=True, slots=True)
class FieldMap:
    field: str                    # Tiqora source field key
    kind: Literal["id", "string", "state_type", "time"]
ELEMENT_FIELDS_TICKETS: Final[Mapping[str, FieldMap]]        # table D6.1
def resolve_values(el: StatElement, fmap: FieldMap, stat: ZnunyStat, lookups: StatLookups,
                   issues: list[ImportIssue]) -> list[int | str]
    # db: ints for "id" (non-int -> dropped issue); file + element in FILE_NAME_ELEMENTS -> name/login lookup
    # (unknown name -> dropped issue "„X“ gibt es in diesem System nicht."); state_type: id -> name, must be in STATE_TYPES;
    # string: values containing "*" -> dropped issue "Platzhalter (*) werden nicht übernommen."
FILE_NAME_ELEMENTS: Final[Mapping[str, Literal["queue", "state", "priority", "user"]]]
    # the elements Znuny's ExportWrapper converts to names - read Kernel/System/Stats/Dynamic/*.pm ExportWrapper and
    # copy the exact list; for Ticket.pm: QueueIDs, CreatedQueueIDs -> queue; StateIDs, CreatedStateIDs -> state;
    # PriorityIDs, CreatedPriorityIDs -> priority; OwnerIDs, CreatedUserIDs, ResponsibleIDs -> user
def map_time_range(el: StatElement, issues: list[ImportIssue]) -> TimeRange | None          # table D6.3
def map_grain(el: StatElement, issues: list[ImportIssue]) -> Grain | None                    # table D6.3
def map_visualization(stat: ZnunyStat, n_dims: int) -> Visualization                         # table D6.4
def map_totals(stat: ZnunyStat, n_dims: int) -> Totals                                       # table D6.4
def common_issues(stat: ZnunyStat) -> list[ImportIssue]     # Valid=0, ShowAsDashboardWidget, Cache: D6.4

# backend/src/tiqora/reporting/znuny_stats/mapper.py
StatTarget = Literal["report", "saved_search", "none"]
@dataclass(frozen=True, slots=True)
class MappedSearch:
    criteria: Filter
    include_archived: bool
    sort_by: TicketSortKey
    sort_order: Literal["asc", "desc"]
@dataclass(frozen=True, slots=True)
class StatMapping:
    stat: ZnunyStat
    target: StatTarget
    definition: ReportDefinition | None          # target == "report"; always passes validate_definition
    search: MappedSearch | None                  # target == "saved_search"
    issues: tuple[ImportIssue, ...]              # every part not taken over 1:1; ordered by element
def map_stat(stat: ZnunyStat, lookups: StatLookups, *, is_admin: bool) -> StatMapping
    # dispatch on object_module (dynamic) / static_file (static); unknown -> target "none" with issue
    # ("Object", module, "Diese Art von Statistik kann Tiqora nicht übernehmen.", "dropped").
    # A mapped definition that fails validate_definition -> target "none" + issue with the DefinitionError message
    # (never an exception out of map_stat).
```

### D6. Mapping tables (Tasks 3–4)

**D6.1 Elements → `tickets` source** (Dynamic::Ticket and Dynamic::TicketSolutionResponseTime):

| Znuny element | Tiqora field | kind | notes |
|---|---|---|---|
| `QueueIDs` | `queue` | id | |
| `CreatedQueueIDs` | `queue_at_create` | id | |
| `StateIDs` | `state` | id | |
| `StateTypeIDs` | `state_type` | state_type | id → name via lookup; name not in `STATE_TYPES` → dropped |
| `PriorityIDs` | `priority` | id | |
| `TypeIDs` | `type` | id | |
| `ServiceIDs` | `service` | id | |
| `SLAIDs` | `sla` | id | |
| `OwnerIDs` | `owner` | id | |
| `ResponsibleIDs` | `responsible` | id | |
| `CustomerID`, `CustomerIDRaw` | `customer` | string | `*` → dropped |
| `CustomerUserLogin`, `CustomerUserLoginRaw`, `CustomerUserID` | `customer_user` | string | `*` → dropped |
| `CreateTime` | `created` | time | |
| `CloseTime`, `CloseTime2` | `closed` | time | `CloseTime2` is the solution-time objects' period (ruling 5) |
| `SearchInArchive` | — | — | `NotArchivedTickets`/empty → nothing; `ArchivedTickets`/`AllTickets` → dropped "Archivierte Tickets werden in Berichten nicht mitgezählt." |
| `ChangeTime`, `LastChangeTime`, `PendingUntilTime`, `EscalationTime`, `EscalationResponseTime`, `EscalationUpdateTime`, `EscalationSolutionTime` | — | — | dropped "Dieses Zeitfeld gibt es in den Berichtsdaten nicht." |
| `LockIDs`, `Title`, `CreatedStateIDs`, `CreatedPriorityIDs`, `CreatedUserIDs`, `MIMEBase_*` | — | — | dropped "Wird in Berichten nicht unterstützt." |
| `DynamicField_*` | — | — | dropped "Dynamische Felder stehen in Berichten noch nicht zur Verfügung." (A12) |
| anything else | — | — | dropped "Unbekanntes Element." |

**D6.2 Axes, restrictions, runtime parameters** (all dynamic matrix objects):

- `UseAsXvalue` → `dimensions[0]`; each `UseAsValueSeries` element (Znuny allows up to 2) → `dimensions[1]`, `[2]` (max 3 dimensions total; a fourth → dropped). Time element on an axis → date dimension with `grain` from `map_grain` (unmappable grain → the axis is dropped, issue).
- Selected values of a non-time axis or restriction → condition `{"field": f, "op": "in", "value": values}` ANDed into `definition.filter` (`AllOf` when several), when `Fixed` is true.
- `Fixed` false (Znuny "changeable at runtime") on a non-time element → `ReportParameter(key=f"p{n}_{f}"[:40] lower-case, field=f, op="in", default=values or None, allowed_values=None, required=False)` instead of a fixed condition; on a time element → nothing extra (the viewer can always change the time range), no issue.
- Axis values that Znuny would show with 0 (selected values without tickets) → one approximated issue per stat "Zeilen ohne Tickets werden nicht angezeigt." when an axis has selected values.
- The first time element with a range sets `time_field` + `time_range`; a second time element with its own range → dropped "Nur ein Zeitraum pro Bericht."
- `TimeZone` valid (`valid_time_zone`) → `definition.time_zone`; invalid → approximated "Zeitzone ungültig – es gilt die Zeitzone des Betrachters."

**D6.3 Time** (`map_time_range`, `map_grain`):

| Znuny | Tiqora | else |
|---|---|---|
| `TimeStart` + `TimeStop` (`YYYY-MM-DD HH:MM:SS`) | `AbsoluteTimeRange(start=date(TimeStart), end=date(TimeStop))` | times other than `00:00:00`/`23:59:59` → approximated "Uhrzeiten werden auf ganze Tage gerundet." ; unparsable → dropped |
| `TimeRelativeUnit` `Year`/`Quarter`/`Month`/`Week`/`Day` + `TimeRelativeCount` n | `RelativeTimeRange(unit=year/quarter/month/week/day, count=n)` | |
| `HalfYear` | `RelativeTimeRange(unit="month", count=6·n)` | approximated "Halbjahre werden als 6 Monate übernommen." |
| `Hour`/`Minute`/`Second` unit | — | dropped "Zeiträume in Stunden/Minuten gibt es in Berichten nicht." |
| `TimeRelativeUpcomingCount` 0 / missing | `include_current=False` | |
| `TimeRelativeUpcomingCount` 1 | `include_current=True` | |
| `TimeRelativeUpcomingCount` > 1 | `include_current=True` | approximated "Zukünftige Zeiträume entfallen." |
| scale (`SelectedValues[0]` of a time axis) `Day`/`Week`/`Month`/`Quarter`/`Year` with `TimeScaleCount` 1 or missing | grain day/week/month/quarter/year | `TimeScaleCount` > 1 → approximated "Zusammenfassung über mehrere Einheiten entfällt." |
| scale `Second`/`Minute`/`Hour`/`HalfYear` | — | dropped "Diese Zeiteinteilung gibt es in Berichten nicht." |

**D6.4 Stat-level attributes:**

| Znuny | Tiqora |
|---|---|
| `SumCol` true | `totals.grand = True` (+ `totals.columns = True` when 2+ dimensions) |
| `SumRow` true | `totals.rows = True` when 2+ dimensions; 1 dimension → nothing (the value column is its own sum), no issue |
| `Format` contains `D3::BarChart` | `bar` (`series_dimension=1` when 2+ dims) |
| `Format` contains `D3::LineChart` | `line` (same series rule) |
| `Format` contains `D3::StackedAreaChart` | `area` |
| no chart format | `pivot` with 2+ dims, `table` otherwise; `Print`/`CSV`/`Excel` silently fine (Tiqora always offers them) |
| `Valid` false | approximated "In Znuny ungültig – als normaler Bericht übernommen." |
| `ShowAsDashboardWidget` true | dropped "Bitte den Bericht im Dashboard als Widget hinzufügen." (A14) |
| `Cache` true | nothing (Tiqora caches results itself) |
| `Title` / `Description` | saved report `name` (stripped, ≤200) / `description` (≤2000) |

**D6.5 Objects:**

| Object module / static file | Target | Source + measures | Element table |
|---|---|---|---|
| `Kernel::System::Stats::Dynamic::Ticket` | report | `tickets`, `[tickets]` | D6.1 |
| `Kernel::System::Stats::Dynamic::TicketSolutionResponseTime` | report | `tickets`, measures from `KindsOfReporting`: `SolutionAverageAllOver`→`solution_clock_avg`, `NumberOfTicketsAllOver`→`closed_tickets`, `SolutionWorkingTimeAverage`→`solution_avg`, `ResponseWorkingTimeAverage`→`first_response_avg`; `*MinTime*`/`*MaxTime*`/`*MinWorkingTime`/`*MaxWorkingTime` → dropped "Minimum und Maximum gibt es nicht; Tiqora bietet Median (P50) und 90-%-Wert (P90)."; `SolutionAverage`/`ResponseAverage`/`NumberOfTickets` (escalation-affected) → dropped "Hängt in Znuny von der Eskalationskonfiguration ab; bitte die Werte ohne diesen Zusatz verwenden." | D6.1 |
| `Kernel::System::Stats::Dynamic::TicketAccountedTime` | report | `accounted_time`, `KindsOfReporting`: `TotalTime`→`time_units`, `NumberOfTickets`→`tickets`; every other kind → dropped "Kennzahl gibt es in Tiqora nicht." | `AccountedByAgent`→`agent` (id), `QueueIDs`→`queue`, `TypeIDs`→`type`, `ServiceIDs`→`service`, `CustomerID(Raw)`→`customer`, `CustomerUserLogin(Raw)`/`CustomerUserID`→`customer_user`, `ArticleAccountedTime`→`booked` (time); all other elements → dropped "Wird bei der Zeiterfassung nicht unterstützt." |
| `Kernel::System::Stats::Dynamic::TicketList` | saved search (A1) | — | D6.6 |
| static `StateAction` | report (A13) | `ticket_events`, dims `[{key:"occurred",grain:"day"},{key:"kind"}]`, measures `[events]`, `time_range` relative month 1, viz `pivot`, totals rows+columns+grand; issue approximated "Znuny zählt jeden Historientyp; Tiqora zählt die Ereignisarten der Berichtsdaten (Erstellt, Status, Queue, Antwort …). Der Monat ist zur Laufzeit wählbar." | — |
| any other static file / object | none | — | issue dropped "Diese Art von Statistik kann Tiqora nicht übernehmen." |

`KindsOfReporting`: in Znuny it is an axis; in Tiqora it becomes `measures` (selected kinds in order). When it is the X axis, the first value series becomes `dimensions[0]`. No mappable kind → target `none`.

**D6.6 TicketList → saved search** (adapter `stat_to_profile_values(stat, lookups) -> tuple[ProfileValues, list[ImportIssue]]`, then wave-2 `map_search_profile(stat.title, values, lookups.search, is_admin=is_admin)`; its `UnmappedItem`s become `ImportIssue(element=f"UseAsRestriction.{key}", value, reason, "dropped")`):

- Restriction elements keep their Znuny search-parameter name (`QueueIDs`, `StateIDs`, `StateTypeIDs`, `PriorityIDs`, `TypeIDs`, `ServiceIDs`, `SLAIDs`, `OwnerIDs`, `ResponsibleIDs`, `LockIDs`, `CustomerID`, `CustomerIDRaw`, `CustomerUserLogin`, `CustomerUserLoginRaw`, `Title`, `SearchInArchive`, `Created*IDs`); the mapper reports what it cannot map. File origin: name elements are converted to ids first (`resolve_values`), unknown names → dropped issue.
- `MIMEBase_From|To|Cc|Subject|Body` → renamed `From|To|Cc|Subject|Body` (the mapper reports them as full-text).
- `DynamicField_<Name>` → dropped (A12) — do not pass them on (stats DF values have a different shape than search-profile DF keys).
- Time restrictions → search keys: `CreateTime`→ type key `TimeSearchType`, prefix `TicketCreate`; `ChangeTime`→`ChangeTimeSearchType`/`TicketChange`; `PendingUntilTime`→`PendingTimeSearchType`/`TicketPending`; `CloseTime`→`CloseTimeSearchType`/`TicketClose`; `LastChangeTime`→`LastChangeTimeSearchType`/`TicketLastChange`; `EscalationTime`→`EscalationTimeSearchType`/`TicketEscalation` (the last three are reported by the mapper as fields without counterpart). Absolute → `"TimeSlot"` + `<prefix>TimeStartYear|Month|Day` and `<prefix>TimeStopYear|Month|Day`. Relative → `"TimePoint"` + `<prefix>TimePoint` = amount, `<prefix>TimePointStart` = `"Last"`, `<prefix>TimePointFormat` = unit, with `Year`→`year`·n, `HalfYear`→`month`·6n, `Quarter`→`month`·3n, `Month`→`month`·n, `Week`→`week`·n, `Day`→`day`·n, `Hour`→`hour`·n, `Minute`→`minute`·n; `Second` → dropped; `TimeRelativeUpcomingCount` ≥ 1 → approximated "Als gleitender Zeitraum der letzten n … übernommen." (n>1 also "Zukünftige Zeiträume entfallen.").
- `UseAsXvalue` `TicketAttributes` → approximated "Die Ticketliste zeigt ihre festen Spalten; die Spaltenauswahl wird nicht übernommen."
- `OrderBy` → `sort_by` via `Age`→`age`, `Created`→`created`, `Changed`→`changed`, `TicketNumber`→`tn`, `Title`→`title`, `Priority`→`priority`; else default `age` + approximated "Sortierung nach X gibt es nicht; es wird nach Alter sortiert."; `SortSequence` `Up`→`asc`, `Down`→`desc` (default `desc`).
- `Limit` → approximated "Die Liste zeigt alle Treffer seitenweise."; `StateIDsHistoric`, `StateTypeIDsHistoric`, `HistoricTimeRange` → dropped "Historische Status werden in Suchen nicht unterstützt."
- `MappedSearch(criteria=mapped.criteria, include_archived=mapped.include_archived, sort_by, sort_order)`; a search whose `criteria.where` is None is still imported (matches all visible tickets) with an approximated issue "Ohne Einschränkung – zeigt alle Tickets, die Sie sehen dürfen."

### D7. Import service and API (Task 5)

```python
# backend/src/tiqora/reporting/znuny_stats/schemas.py
class ImportIssueOut(BaseModel):
    element: str; value: str; reason: str; severity: Literal["dropped", "approximated"]
class StatPreviewOut(BaseModel):
    import_key: str                              # ZnunyStat.import_key
    stat_id: int | None
    stat_number: str | None
    title: str
    description: str
    object_label: str                            # German: "Ticket-Matrix", "Ticketliste", "Zeiterfassung",
                                                 # "Lösungs- und Reaktionszeit", "Statisch: StateAction", else module
    target: Literal["report", "saved_search", "none"]
    issues: list[ImportIssueOut]
    definition: ReportDefinition | None          # for "Vorschau öffnen" in the builder
    already_imported: Literal["report", "saved_search"] | None
    existing_id: int | None                      # the importer's existing copy
class StatLoadErrorOut(BaseModel):
    stat_id: int; message: str
class ZnunyStatsPreviewOut(BaseModel):
    stats: list[StatPreviewOut]                  # by stat_number, then title
    errors: list[StatLoadErrorOut]
    truncated: bool                              # more than MAX_PREVIEW_STATS stats exist
class ZnunyStatsFileIn(BaseModel):
    content_base64: str = Field(min_length=1, max_length=2_800_000)   # <= MAX_FILE_BYTES decoded
class ZnunyStatsApplyIn(BaseModel):              # extra="forbid"
    stat_ids: list[int] = Field(default_factory=list, max_length=200)     # from xml_storage
    file_content_base64: str | None = Field(None, max_length=2_800_000)   # or one file
class ImportedStatOut(BaseModel):
    import_key: str; title: str
    target: Literal["report", "saved_search"]
    id: int
    action: Literal["created", "updated"]
    issues: list[ImportIssueOut]
class ZnunyStatsApplyOut(BaseModel):
    imported: list[ImportedStatOut]
    skipped: list[StatPreviewOut]                # target "none", invisible or unknown ids (issue says why)

# backend/src/tiqora/reporting/znuny_stats/service.py
async def visible_stat_ids(session: AsyncSession, user_id: int, *, admin: bool, stats: Sequence[ZnunyStat]) -> set[int]
    # admin: all; else stats whose Permission group ids intersect groups_for_permission(user_id, "ro")
    # (Znuny: the stat's Permission groups decide who may run it); a stat without Permission -> admins only
async def preview_db(session: AsyncSession, user_id: int) -> ZnunyStatsPreviewOut
async def preview_file(session: AsyncSession, user_id: int, content: bytes) -> ZnunyStatsPreviewOut
async def apply_import(session: AsyncSession, user_id: int, rights: ReportRights, body: ZnunyStatsApplyIn,
                       *, registry: Registry) -> ZnunyStatsApplyOut
    # rights.create else ReportForbidden; exactly one of stat_ids / file -> RequestError(422);
    # report target: existing = the importer's valid tiqora_report with copied_from == import_key ->
    #   update_saved_report(... SavedReportUpdateIn(name, description, definition, visibility="private", version=existing.version))
    #   else create_saved_report(... SavedReportIn(..., visibility="private", copied_from=import_key))  (A10)
    # saved-search target: upsert_imported(session, user_id, source_name=import_key, criteria, include_archived)
    #   then ONE UPDATE on that row: name = stat.title (stripped, <= 200; upsert_imported would otherwise name it
    #   after source_name "znuny.stat.<id>"), sort_by, sort_order (same transaction)
    # one commit at the end; any error -> rollback, nothing half-imported
```

Routes in `api/v1/reports.py` (register BEFORE `/{report_key}`; `CurrentUser`; view access required for every route):

```
GET  /api/v1/reports/import/znuny            -> ZnunyStatsPreviewOut   (rights.view)
POST /api/v1/reports/import/znuny/file       -> ZnunyStatsPreviewOut   (ZnunyStatsFileIn; rights.view; saves nothing)
POST /api/v1/reports/import/znuny/apply      -> ZnunyStatsApplyOut     (rights.create; 403 German otherwise)
```

Bounded inline (A9): at most `MAX_PREVIEW_STATS` stats, one `xml_storage` query per 200 keys, mapping is pure. `already_imported` is computed with two IN-list queries on `tiqora_report.copied_from` / `tiqora_saved_search.source_name` for the importer (no join with Znuny tables).

### D8. Rights for schedules (Task 10)

- List/read/create/edit/delete own schedules: `rights.create` (wave-2 `ReportRights`; view alone is not enough).
- Admin (`rights.admin`): sees all (`?all=true`), may edit, pause and delete any schedule; the schedule keeps running with its **owner's** permissions.
- Recipients other than exactly the owner (other users, any group, any role, any e-mail) need `rights.share` (A6) → 403 "Für das Versenden an andere fehlt die Berechtigung (Gruppe „stats“, Schreibrecht)."
- `emails` need `reporting.schedule_external_recipients` (A2) → 422 path `recipients.emails` "Externe Empfänger sind nicht freigeschaltet. Bitte wenden Sie sich an einen Administrator."
- `group_ids` must pass `check_share_groups(session, owner_id, ...)`; `role_ids` must be valid roles; `user_ids` valid agents. At least one recipient entry.
- Every report key must resolve for the owner (`resolve_report_ref(session, key, owner_id, admin=is_admin(owner))`) at save time and at run time.
- Limit `MAX_SCHEDULES_PER_USER = 50` (valid rows of the owner) → 422 path `name`.

### D9. Texts, formatting, PDF (Tasks 6–8)

```python
# backend/src/tiqora/reporting/texts.py
TextLocale = Literal["de", "en"]
def text_locale(raw: str | None) -> TextLocale          # "de", "de_DE", "de-AT" -> "de"; anything else -> "en" (A3)
@dataclass(frozen=True, slots=True)
class ReportTexts:
    locale: TextLocale
    def t(self, key: str, default: str | None = None, **params: str | int) -> str
        # key with "reports." prefix looked up in the bundle (nested JSON, dotted path); {{name}} params
        # substituted once (no recursion); missing -> default or the key; en bundle as fallback for de
    def fixed(self, key: str, **params: str | int) -> str   # backend-only texts in FIXED_TEXTS
def load_texts(locale: str | None) -> ReportTexts        # cached per locale; reads texts_data/reports.<loc>.json via importlib.resources
FIXED_TEXTS: Final[Mapping[TextLocale, Mapping[str, str]]]
    # keys: "pdf.generated" ("Erstellt am {date} um {time}" / "Generated on …"), "pdf.period" ("Zeitraum: {start} – {end}"),
    # "pdf.truncated" ("Es werden nur die ersten {n} Zeilen gezeigt. Die vollständige Liste erhalten Sie als Excel-Datei."),
    # "pdf.empty" ("Keine Daten im gewählten Zeitraum."), "pdf.page" ("Seite {page} von {pages}"),
    # "pdf.owner" ("Erstellt mit den Berechtigungen von {owner}."), "mail.default_subject" ("Bericht: {{schedule}} ({{period}})"),
    # "mail.default_body" (Sie-form, 3 lines), "mail.footer" ("Dieser Bericht wurde mit den Berechtigungen von {owner} erstellt. Zeitplan: {link}"),
    # "mail.failed_subject" ("Zeitplan „{name}“: Bericht konnte nicht erstellt werden"), "mail.failed_body", "mail.paused_body",
    # "month.1".."month.12", "weekday.1".."weekday.7", "quarter" ("Q{q} {year}")
    # (the week label is NOT duplicated here: it is the bundle key "reports.weekLabel", "KW {{week}}" / "Wk {{week}}")
def format_number(value: float, locale: TextLocale, digits: tuple[int, int]) -> str     # de "1.234,5", en "1,234.5"
def format_measure(value: Cell, col_type: ColumnType, locale: TextLocale) -> str
    # mirror frontend/src/components/reports/reportFormat.ts formatMeasure/formatMinutes/dayUnit exactly ("—" for None)
def format_date_bucket(value: str, grain: Grain | None, texts: ReportTexts) -> str
    # mirror reportFormat.ts dateBucketLabel: week -> texts.t("reports.weekLabel", week=iso week), month -> "Oktober 2026",
    # quarter -> "Q4 2026", year -> "2026", day -> de "11.10.2026" / en "10/11/2026" (en-US short like Intl default)
@dataclass(frozen=True, slots=True)
class ExportLabels:
    column_labels: dict[str, str]
    value_labels: dict[str, dict[str, str]]
    totals_label: str
def server_export_labels(result: ReportResultOut, texts: ReportTexts) -> ExportLabels
    # mirror frontend exportLabels/cellLabel: result.labels first, OTHER_KEY -> t("reports.other"),
    # i18n_values -> t(f"{i18n_values}.{v}", default=v), hour -> t("reports.hourLabel", hour="07"),
    # weekday -> t(f"reports.values.weekday.{v}"); date columns are NOT put into value_labels (XLSX keeps real dates)
```

Bundle: `backend/src/tiqora/reporting/texts_data/reports.de.json` / `.en.json` = `{"reports": <the "reports" object of frontend/src/i18n/locales/<loc>.json>}`, written by `cd backend && uv run python -m tiqora.reporting.texts --sync` (reads `../frontend/src/i18n/locales`), compared by a test. Committed, never gitignored; `texts_data/__init__.py` makes it a package so the wheel includes it (skill `hatch-wheel-gitignore-drops-data-files`).

```python
# backend/src/tiqora/reporting/svg.py   (pure; returns an SVG string; every text escaped with html.escape)
SERIES_COLORS: Final = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7")   # tokens.css light --color-series-1..6
MUTED_COLOR: Final = "#66708a"                                                              # tokens.css light --color-muted
MAX_CATEGORIES: Final = 60
@dataclass(frozen=True, slots=True)
class ChartSeries:
    key: str; label: str; values: tuple[float | None, ...]
@dataclass(frozen=True, slots=True)
class ChartData:
    categories: tuple[str, ...]          # labels, already formatted
    series: tuple[ChartSeries, ...]      # "__other__" series drawn in MUTED_COLOR, never a slot
    value_format: Callable[[float], str]
def chart_data(result: ReportResultOut, viz: Visualization, labels: ExportLabels, texts: ReportTexts) -> ChartData | None
    # mirror reportFormat.ts toSeries (categories = non-series dimension, series = series_dimension values or measures);
    # None when the visualization is table/pivot or there is nothing to plot
def render_chart(kind: VisualizationType, data: ChartData, *, width: int = 680, height: int = 260) -> str | None
    # bar, stacked_bar, line, area, donut, heatmap, kpi; table/pivot -> None. > MAX_CATEGORIES categories: first ones + note.
    # Series colours by slot like assignSeriesSlots (stable order: enum order or ascending key); > 6 series -> rest muted "Weitere".
    # Text: font-family "DejaVu Sans Mono" for numbers, "DejaVu Sans" for labels; viewBox set; no external refs.

# backend/src/tiqora/reporting/pdf.py
@dataclass(frozen=True, slots=True)
class ReportSection:
    title: str
    result: ReportResultOut
    visualization: Visualization
    labels: ExportLabels
    info_lines: tuple[str, ...]          # "Zeitraum: …", parameter values
    row_limit: int
@dataclass(frozen=True, slots=True)
class PdfMeta:
    title: str                           # document title (schedule name or report title)
    generated_at_local: datetime         # in the report time zone, for the footer
    owner_line: str | None               # "Erstellt mit den Berechtigungen von …" (scheduled only)
def render_report_html(sections: Sequence[ReportSection], meta: PdfMeta, texts: ReportTexts) -> str
    # html.escape for every dynamic string; inline CSS only (A4 portrait, 15 mm margins, @page footer with
    # counter(page)/counter(pages)); per section: h2 title, info lines, chart SVG (render_chart) inline, table of the
    # first row_limit rows (format_measure / labels), totals row, truncated note (FIXED "pdf.truncated") when
    # result.truncated or len(rows) > row_limit; empty result -> "pdf.empty"; long words wrap (overflow-wrap:anywhere)
def write_report_pdf(sections: Sequence[ReportSection], meta: PdfMeta, texts: ReportTexts) -> bytes
    # weasyprint.HTML(string=html, base_url=None, url_fetcher=_refuse_fetch).write_pdf()
def _refuse_fetch(url: str, *args: object, **kwargs: object) -> dict[str, Any]
    # always raises ValueError(f"blocked: {url[:80]}") - no http(s), file, data URL is ever loaded
class PdfUnavailable(RuntimeError): ...   # weasyprint import/OSError (native libs missing) -> German message
```

Interactive export (Task 8): `ReportExportIn.format: Literal["xlsx", "csv", "pdf"]`, new `locale: str | None = Field(None, max_length=10)` (number/date formatting and fixed texts of the PDF; labels still come from `column_labels`/`value_labels` sent by the browser, A18). `run_export_job` with `format == "pdf"`: `row_cap = reporting.pdf_row_limit` (not `export_row_limit`), one `ReportSection`, `PdfMeta(title=body.title, generated_at_local=now in the report zone, owner_line=None)`, file `<slug>.pdf`, content type `application/pdf`; `PdfUnavailable` → `JobFailed("PDF-Erstellung ist auf diesem Server nicht verfügbar. Bitte wenden Sie sich an einen Administrator.")`.

PDF font: `fonts-dejavu-core` (A11). PDF layout is LTR (A3).

### D10. Recurrence (Task 9)

```python
# backend/src/tiqora/reporting/recurrence.py  (pure)
class Recurrence(BaseModel):                 # extra="forbid"
    kind: Literal["daily", "weekly", "monthly", "yearly"]
    time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")      # local wall time HH:MM
    workdays_only: bool = False              # daily only: Monday-Friday
    weekdays: list[int] = Field(default_factory=list, max_length=7)    # weekly only: ISO 1=Mon..7=Sun, >=1 entry, unique
    day_of_month: int | None = Field(None, ge=1, le=31)                # monthly + yearly; > month length -> last day
    month: int | None = Field(None, ge=1, le=12)                       # yearly only
    # model_validator: fields not belonging to the kind must be empty/None/False; required ones present;
    # errors are German ("Bitte mindestens einen Wochentag wählen.")
MAX_SEARCH_DAYS: Final = 800
def next_occurrence(rec: Recurrence, time_zone: str, after_utc: datetime) -> datetime
    # smallest occurrence STRICTLY after after_utc (naive UTC in and out). Each candidate local date d gets
    # local = datetime.combine(d, time).replace(tzinfo=ZoneInfo(tz), fold=0) -> astimezone(UTC):
    #   nonexistent wall time (spring-forward gap) resolves with the pre-transition offset, i.e. 02:30 -> 03:30 local;
    #   ambiguous wall time (fall-back) takes the first instant (fold=0) - one run, not two.
    # Unknown zone -> ValueError (callers validated it). No occurrence within MAX_SEARCH_DAYS -> ValueError.
def upcoming(rec: Recurrence, time_zone: str, after_utc: datetime, count: int = 5) -> list[datetime]
def describe(rec: Recurrence, texts: ReportTexts) -> str      # "Jeden Montag und Freitag um 07:00" (UI list + mail)
```

### D11. Schedule API (Task 10)

```python
# backend/src/tiqora/reporting/schedule_schemas.py
class ScheduleItemOverrides(BaseModel):     # extra="forbid"
    time_range: TimeRange | None = None
    grain: Grain | None = None
    parameters: dict[Annotated[str, Field(max_length=40)], ParameterValue] = Field(default_factory=dict, max_length=10)
class ScheduleItemIn(BaseModel):            # extra="forbid"
    report_key: str = Field(min_length=1, max_length=120)
    overrides: ScheduleItemOverrides = Field(default_factory=ScheduleItemOverrides)
class ScheduleRecipientsIn(BaseModel):      # extra="forbid"
    user_ids: list[int] = Field(default_factory=list, max_length=50)
    group_ids: list[int] = Field(default_factory=list, max_length=20)
    role_ids: list[int] = Field(default_factory=list, max_length=20)
    emails: list[Annotated[str, Field(max_length=250)]] = Field(default_factory=list, max_length=50)
    # validator: emails stripped + lower-cased, must match a single addr-spec (email.utils.parseaddr round trip,
    # exactly one "@", no spaces, no "," ";" "<" ">") else 422 "Keine gültige E-Mail-Adresse: …"; duplicates removed
class ReportScheduleIn(BaseModel):          # extra="forbid"
    name: str = Field(min_length=1, max_length=200)
    items: list[ScheduleItemIn] = Field(min_length=1, max_length=10)
    format: Literal["pdf", "xlsx", "csv"] = "pdf"
    recurrence: Recurrence
    time_zone: str | None = Field(None, max_length=64)          # None -> owner's zone (resolve_user_time_zone)
    language: Literal["de", "en"] = "de"
    subject: str = Field("", max_length=250)
    body: str = Field("", max_length=10_000)
    only_if_nonempty: bool = False
    active: bool = True
    recipients: ScheduleRecipientsIn
class ReportScheduleUpdateIn(ReportScheduleIn):
    version: int = Field(ge=1)
class ScheduleItemOut(BaseModel):
    report_key: str
    title: str | None                        # saved report name
    title_key: str | None                    # built-in i18n key
    available: bool                          # resolves for the owner right now
    problem: str | None                      # German, when not available
    overrides: ScheduleItemOverrides
class ScheduleRecipientOut(BaseModel):
    kind: Literal["user", "group", "role", "email"]
    id: int | None
    email: str | None
    label: str                               # "Erika Beispiel", group/role name, the address
    problem: str | None                      # "Keine E-Mail-Adresse hinterlegt.", "Ungültig.", "Externe Empfänger sind ausgeschaltet."
class ReportScheduleOut(BaseModel):
    id: int; name: str
    owner: ReportOwnerOut; is_owner: bool; can_edit: bool
    items: list[ScheduleItemOut]
    format: Literal["pdf", "xlsx", "csv"]
    recurrence: Recurrence; recurrence_text: str
    time_zone: str; language: Literal["de", "en"]
    subject: str; body: str; only_if_nonempty: bool
    active: bool; paused_reason: str | None
    next_run_at: UtcDateTime | None; last_run_at: UtcDateTime | None
    last_status: Literal["succeeded", "partial", "failed", "skipped"] | None
    failure_count: int
    recipients: list[ScheduleRecipientOut]
    version: int; changed_at: UtcDateTime
class ReportRunFileOut(BaseModel):
    id: int; name: str; content_type: str; size_bytes: int
class ReportRunOut(BaseModel):
    id: int; schedule_id: int | None; schedule_name: str
    run_kind: Literal["schedule", "manual"]; owner_only: bool
    status: Literal["queued", "running", "succeeded", "partial", "failed", "skipped"]
    due_at: UtcDateTime | None; created_at: UtcDateTime
    started_at: UtcDateTime | None; finished_at: UtcDateTime | None
    row_count: int | None; recipient_count: int | None
    detail: str | None; error: str | None
    job_id: int | None
    files: list[ReportRunFileOut]
class ScheduleRunIn(BaseModel):              # extra="forbid"
    owner_only: bool = True                  # "Testversand an mich"
class ScheduleRunAccepted(BaseModel):
    run_id: int
    job: JobAccepted
class RecurrencePreviewIn(BaseModel):
    recurrence: Recurrence
    time_zone: str | None = Field(None, max_length=64)
class RecurrencePreviewOut(BaseModel):
    next_runs: list[UtcDateTime]             # 5
    text: str
```

```python
# backend/src/tiqora/reporting/schedules.py
MAX_SCHEDULES_PER_USER: Final = 50
FAILURES_BEFORE_PAUSE: Final = 3
class ScheduleNotFound(LookupError): ...
class ScheduleForbidden(PermissionError):
    def __init__(self, message: str) -> None
class ScheduleConflict(Exception):
    def __init__(self, current_version: int) -> None
class ScheduleInvalid(ValueError):
    def __init__(self, message: str, *, path: str) -> None
@dataclass(frozen=True, slots=True)
class ScheduleRecipients:
    user_ids: tuple[int, ...]; group_ids: tuple[int, ...]; role_ids: tuple[int, ...]; emails: tuple[str, ...]
@dataclass(frozen=True, slots=True)
class ScheduleItem:
    report_key: str; overrides: ScheduleItemOverrides
@dataclass(frozen=True, slots=True)
class ReportSchedule:
    id: int; owner_user_id: int; name: str; format: str
    recurrence: Recurrence | None; recurrence_error: str | None
    time_zone: str; language: str; subject: str; body: str; only_if_nonempty: bool
    active: bool; paused_reason: str | None
    next_run_at: datetime | None; last_run_at: datetime | None; last_status: str | None; failure_count: int
    items: tuple[ScheduleItem, ...]; recipients: ScheduleRecipients
    version: int; created_at: datetime; changed_at: datetime
async def list_schedules(session: AsyncSession, user_id: int, *, admin: bool, include_all: bool = False) -> list[ReportSchedule]
async def get_schedule(session: AsyncSession, schedule_id: int, user_id: int, *, admin: bool) -> ReportSchedule
    # owner or admin, else ScheduleNotFound (no existence leak)
async def load_schedule(session: AsyncSession, schedule_id: int) -> ReportSchedule | None    # worker use, no rights
async def create_schedule(session: AsyncSession, user_id: int, rights: ReportRights, body: ReportScheduleIn,
                          *, reporting: ReportingSettings, now: datetime) -> ReportSchedule
async def update_schedule(session: AsyncSession, schedule_id: int, user_id: int, rights: ReportRights,
                          body: ReportScheduleUpdateIn, *, reporting: ReportingSettings, now: datetime) -> ReportSchedule
    # owner (with rights.create) or admin; version check via UPDATE ... WHERE id AND version (rowcount 0 -> ScheduleConflict);
    # recipient/share rules evaluated against the OWNER (an admin editing keeps the owner's share right as the rule)
    # next_run_at recomputed from now when recurrence/time_zone/active changed; active -> failure_count 0, paused_reason NULL
async def delete_schedule(session: AsyncSession, schedule_id: int, user_id: int, *, admin: bool) -> None   # hard delete; runs kept
async def check_items(session: AsyncSession, owner_id: int, items: Sequence[ScheduleItemIn]) -> None
    # resolve_report_ref for the owner (admin flag of the owner): ReportNotFound/ReportGone/DefinitionError ->
    # ScheduleInvalid(path=f"items.{i}.report_key"); override parameter keys must exist in definition.parameters
    # (path items.{i}.overrides.parameters); duplicate keys allowed (same report, other overrides)
async def check_recipients(session: AsyncSession, owner_id: int, owner_rights: ReportRights,
                           recipients: ScheduleRecipientsIn, reporting: ReportingSettings) -> ScheduleRecipients    # D8
@dataclass(frozen=True, slots=True)
class ResolvedRecipients:
    addresses: tuple[str, ...]               # deduplicated case-insensitively, sorted, capped at schedule_max_recipients
    problems: tuple[str, ...]                # German lines WITHOUT addresses: "Erika Beispiel: keine E-Mail-Adresse", "Gruppe „x“: ungültig", "3 Empfänger über dem Limit"
async def resolve_recipients(session: AsyncSession, schedule: ReportSchedule, reporting: ReportingSettings,
                             *, owner_only: bool) -> ResolvedRecipients
    # owner_only -> just the owner. users: PermissionEngine.is_valid_user; groups: users_for_group_permission(g, "ro");
    # roles: role_user rows of valid roles (own query, int IN-list) -> users.valid_id = 1;
    # addresses via bulk_get_preferences(session, ids, "UserEmail"); emails only when schedule_external_recipients
async def recipient_views(session: AsyncSession, schedule: ReportSchedule, reporting: ReportingSettings) -> list[ScheduleRecipientOut]
```

Routes (`api/v1/report_schedules.py`; `router = APIRouter(tags=["report-schedules"])`, paths written in full; `CurrentUser`; `require_report_access` first; static paths before `/{schedule_id}`):

```
GET    /api/v1/report-schedules?all=false                 -> list[ReportScheduleOut]   (rights.create; all=true admins only)
POST   /api/v1/report-schedules                           -> 201 ReportScheduleOut
POST   /api/v1/report-schedules/preview-recurrence        -> RecurrencePreviewOut      (bounded, pure)
GET    /api/v1/report-schedules/{schedule_id}             -> ReportScheduleOut         (404 owner/admin only)
PUT    /api/v1/report-schedules/{schedule_id}             -> ReportScheduleOut         (403/404/409/422)
DELETE /api/v1/report-schedules/{schedule_id}             -> 204
POST   /api/v1/report-schedules/{schedule_id}/run         -> 202 ScheduleRunAccepted   (ScheduleRunIn; owner_only=false needs
                                                             the same rights as the recipients; one active manual run per
                                                             schedule: dedupe_key "report_schedule:<id>:manual" -> 409
                                                             "Ein Versand läuft bereits.")
GET    /api/v1/report-schedules/{schedule_id}/runs?limit=20  -> list[ReportRunOut]    (newest first, limit <= 100)
GET    /api/v1/report-runs/{run_id}/files/{file_id}       -> attachment (owner of the run or admin; Content-Disposition
                                                             via the existing job-file download helper's quoting)
```

Error bodies `{"detail": {"message": <German>, "path": <optional>}}`; 409 `{"message": "Der Zeitplan wurde inzwischen geändert. Bitte laden Sie die Seite neu.", "current_version": n}`. `api_key_scopes._PATH_PREFIX_TO_AREA` gains `("/api/v1/report-schedules", "stats")` and `("/api/v1/report-runs", "stats")` — `/api/v1/reports` does NOT prefix-match `/api/v1/report-…`.

### D12. Delivery job (Task 11)

```python
# backend/src/tiqora/reporting/delivery.py
REPORT_DELIVERY_KIND: Final = "core.report_delivery"
CHECKPOINT_SENDING: Final = "sending"
MAX_ATTEMPTS: Final = 3                       # temporary SMTP errors, like gi_mail
class ReportDeliveryPayload(BaseModel):
    run_id: int
async def run_delivery_job(ctx: JobContext, payload: ReportDeliveryPayload) -> dict[str, Any]
async def on_delivery_interrupted(ctx: JobContext, payload: ReportDeliveryPayload) -> None
REPORT_DELIVERY_JOB = JobHandler(kind=REPORT_DELIVERY_KIND, payload_model=ReportDeliveryPayload,
    run=run_delivery_job, title_key="jobs.kinds.core.report_delivery", timeout_seconds=1800,
    lane=DEFAULT_LANE,                        # NOT the one-slot mail lane: rendering must not hold up article mails
    max_attempts=MAX_ATTEMPTS, requeue_if_interrupted=True, on_interrupted=on_delivery_interrupted,
    result_private=True)
def apply_placeholders(template: str, values: Mapping[str, str]) -> str
    # replaces {{schedule}}, {{period}}, {{date}}, {{reports}}, {{owner}} in ONE pass (re.sub with a dict lookup);
    # substituted values are never scanned again; unknown {{x}} stay as written
async def resolve_report_sender(session: AsyncSession) -> str | None
    # tiqora setting "notification.sender_email" -> SysConfig NotificationSenderEmail when it is a usable address
    # (reuse the "usable address" check of worker/notification_templates.py; extract it to a small public helper there
    # if it is private) -> None (job fails "Kein Absender für Berichte eingerichtet …")
```

Run steps (all German messages Sie-form; every DB step in its own short session from `ctx.session_factory`):

1. Load the run (missing → return `{"skipped": "run_missing"}`); set `status="running"`, `started_at`.
2. Load the schedule (`load_schedule`); deleted → fail "Der Zeitplan wurde gelöscht."; for `run_kind="schedule"` and `active` false → status `skipped`, detail "Zeitplan pausiert.".
3. Owner checks: `PermissionEngine.is_valid_user(owner)` and `has_report_access(owner)` (current settings) → else fail "Der Ersteller hat keinen Zugriff mehr auf Berichte." (Review Focus 1).
4. Files already stored for this run (a retry after a temporary SMTP error) → skip to step 8.
5. Per item: `prepare_ref(session, owner, report_key=item.report_key, definition=None, overrides=RunOverrides(time_range=…, grain=…, parameters=…, time_zone=schedule.time_zone, no_cache=True))` → `run_report(…, user_id=owner, report_key=key, reporting=…, row_cap=pdf_row_limit if format=="pdf" else export_row_limit, use_cache=False, budget=None)` inside `_until_cancelled` (reuse from `reporting/jobs.py`; make it importable). `RequestError`/`DefinitionError`/`FilterError` → fail the whole run "Bericht „X“ ist nicht mehr verfügbar: …" (A15); `ReportQueryTimeout` → fail with the wave-1 timeout text in Sie-form.
6. `only_if_nonempty` and every result has 0 rows → `status="skipped"`, detail "Keine Daten – nichts versendet.", schedule `last_status="skipped"`, `failure_count=0`; return.
7. Render with `texts = load_texts(schedule.language)`, labels `server_export_labels`: `pdf` → one PDF (one section per item, `PdfMeta(title=schedule.name, generated_at_local, owner_line=texts.fixed("pdf.owner", owner=<owner name>))`), file `<export_slug(name)>-<YYYY-MM-DD>.pdf`; `xlsx` → one workbook, one sheet per item (`report_sheet` + `write_xlsx_workbook`, sheet names = report titles); `csv` → one file per item (`write_report_csv`), more than one → one `.zip` (`zipfile`, deflated) named like the PDF. Total size > `delivery_max_attachment_mb` → fail "Der Anhang ist größer als … MB. Bitte wählen Sie weniger Berichte oder einen kürzeren Zeitraum." (file still stored for the owner). Store files in `tiqora_report_run_file`; set `row_count` (sum).
8. `ResolvedRecipients` (`owner_only` from the run). No address → fail "Keiner der Empfänger hat eine E-Mail-Adresse." with problems in `detail`.
9. Sender (`resolve_report_sender`), subject/body (`apply_placeholders`; empty → language defaults), body + fixed footer (`mail.footer`, owner name, link `<public_base_url>/agent/reports/schedules/<id>` or without link when `public_base_url` is empty). `build_message(from_addr=sender, to_addrs=sender, cc_addrs=None, bcc_addrs=", ".join(addresses), subject=…, body=…, content_type="text/plain", in_reply_to=None, loop_hint=True, attachments=[(name, ctype, data), …])` (A5).
10. `await ctx.checkpoint(CHECKPOINT_SENDING)`; `sender = await build_outbound_sender(session, settings=ctx.settings)`; `await sender.send(message, envelope_from="")`. Temporary error (not `is_permanent_smtp_failure`) and `not ctx.last_attempt` → `JobRetry(…, delay_seconds=ctx.attempt * 120)` (run stays `running`, files kept); permanent or last attempt → fail "Die E-Mail konnte nicht versendet werden: <smtp detail>".
11. Success: `write_mail_log(direction="out", status="sent", from_addr=sender, to_addr=f"{n} Empfänger (Bcc)", subject=…, detail=f"report_run {run_id}")`; run `status = "partial" if problems else "succeeded"`, `recipient_count`, `detail` = problems joined, `finished_at`; schedule `last_run_at`, `last_status`, `failure_count = 0`.

Failure path `_fail_run(session, run, schedule, message, *, detail=None)`: run `failed` + `error` + `finished_at`; for `run_kind="schedule"` schedule `failure_count += 1`, `last_status="failed"`, `last_run_at`; when `failure_count >= FAILURES_BEFORE_PAUSE`: `active=False`, `next_run_at=None`, `paused_reason="Nach 3 Fehlversuchen angehalten: <message>"` (A7). Then a best-effort plain mail to the owner's `UserEmail` (subject `mail.failed_subject`, body `mail.failed_body` or `mail.paused_body`), no attachment, errors logged and ignored; then `raise JobFailed(message)`. `on_delivery_interrupted`: run `failed`, error "Der Versand wurde unterbrochen; die E-Mail ist möglicherweise nicht angekommen." (only reached after the checkpoint — before it, the runner requeues).

### D13. Scheduler loop and purge (Task 12)

```python
# backend/src/tiqora/reporting/scheduler.py
TICK_LIMIT: Final = 50
async def run_report_scheduler_tick(*, settings: Settings, session_factory: async_sessionmaker[AsyncSession],
                                    now: datetime | None = None, registry: Registry | None = None) -> dict[str, int]
    # SELECT id, next_run_at FROM tiqora_report_schedule WHERE active AND next_run_at <= now ORDER BY next_run_at LIMIT 50
    # per row, own session + commit:
    #   schedule = load_schedule(...); recurrence None (stored JSON broken) or zone invalid ->
    #     UPDATE active=false, next_run_at=NULL, paused_reason="Zeitplan ungültig: …" (guarded by next_run_at = :due)
    #   nxt = next_occurrence(rec, tz, max(now, due))           # strictly after now: ONE catch-up (A8)
    #   UPDATE ... SET next_run_at=:nxt WHERE id=:id AND next_run_at=:due AND active  -> rowcount != 1: rollback, skip
    #   INSERT tiqora_report_run(status="queued", run_kind="schedule", due_at=due, owner, schedule_name, report_keys)
    #   enqueue_job(kind=REPORT_DELIVERY_KIND, payload=ReportDeliveryPayload(run_id), created_by=owner,
    #               dedupe_key=f"report_schedule:{id}:{due:%Y%m%dT%H%M}")
    #   run.job_id = job.id; commit
    # returns {"due": n, "enqueued": k, "paused": p}
async def purge_report_runs(session: AsyncSession, *, older_than: datetime) -> int
    # runs with finished_at < older_than, plus runs still queued/running whose created_at < older_than (stuck);
    # delete their files first (IN-list chunks of 500), then the runs; returns the number of runs
```

Worker (`worker/report_scheduler.py`): `run_report_scheduler_worker_tick(settings)` (enabled key `KEY_REPORT_SCHEDULER_ENABLED`, default True → `{"skipped": 1}` when off) and `run_report_runs_purge_tick()` (`KEY_REPORT_RUNS_PURGE_ENABLED`, default True; cutoff = `utc_now() - run_retention_days`). `worker/__main__.py`: `_interval_loop("report_scheduler", …, settings.report_scheduler_interval_seconds, KEY_REPORT_SCHEDULER_INTERVAL_SECONDS, stop)` and `_daily_loop("report_runs_purge", …, "03:45", stop)`. `config.py`: `report_scheduler_interval_seconds: int = Field(60, ge=15, le=3600, …)` following the `report_facts_interval_seconds` pattern. `worker/services.py`: two `DaemonService` entries (`report_scheduler` interval, `report_runs_purge` daily `03:45`), toggleable, default enabled.

### D14. GDPR and user removal (Task 13)

- Customer erasure (`gdpr/erasure.py` → `scrub_saved_criteria`): additionally (a) rewrites erased logins / company ids inside `tiqora_report_schedule_item.overrides.parameters` for parameters whose report field is `customer_user` / `customer` (resolve the report definition of the item; a built-in's parameter fields are known from `BUILTIN_REPORTS`), backed up through the same `BackupFn` (add `"tiqora_report_schedule_item"` to `SAVED_CRITERIA_TABLES` only if `rollback_job` restores by primary key generically — check; otherwise document "not restored"), and (b) deletes `tiqora_report_schedule_recipient` rows whose `email` equals an erased customer user's e-mail address (case-insensitive; addresses loaded from `customer_user.email` for the erased logins BEFORE anonymisation, or taken from the variable `erasure.py` already holds — find it), bumping the schedule's `version`/`changed_at`. Recipient deletion is not restored by a rollback (documented in `docs/gdpr.md`).
- Agent removal (`domain/user_delete.py` `delete_user_rows`): schedules owned by the user are deleted (with items/recipients via CASCADE), recipient rows `kind='user' AND ref_id = user` are deleted, `tiqora_report_run.owner_user_id` rows of that user are deleted with their files. Add the new tables to whatever `blocking_references` / FK scan the module uses so the deletion is not refused (no FK exists, so it is a plain cleanup — read the module first).
- The run history never contains e-mail addresses (D12 step 8/11), so nothing to scrub there; `tiqora_mail_log` rows written by deliveries contain no addresses either ("n Empfänger (Bcc)").

### D15. Frontend (Tasks 14–17)

- `reportsApi` additions: `znunyStatsPreview(signal?)` → `GET /reports/import/znuny`; `znunyStatsFilePreview(file: File)` → reads as base64 (`FileReader`), `POST /reports/import/znuny/file`; `znunyStatsApply(body)`; `ReportExportIn["format"]` includes `"pdf"`; `locale` sent = `i18n.language`.
- `reportSchedulesApi`: `list(all?)`, `get(id)`, `create(body)`, `update(id, body)`, `remove(id)`, `runNow(id, ownerOnly)`, `runs(id, limit)`, `fileUrl(runId, fileId)`, `previewRecurrence(body)`; types `Schemas["ReportScheduleOut"]` etc.
- Routes (in `router.tsx`, next to the existing `/reports*` routes; static before `$reportKey`): `/agent/reports/schedules` → `ReportSchedulesPage`; `/agent/reports/schedules/new?report=<key>` and `/agent/reports/schedules/$scheduleId` → `ReportScheduleEditorPage`. All wrapped in `ReportsAccessGate`; pages need `user.can_create_reports` (else the gate's "keine Berechtigung" body).
- `ReportsPage` header: buttons "Zeitpläne" (link) and "Statistiken aus Znuny übernehmen" (opens `ZnunyStatsImportDialog`; shown when `can_view_reports`; apply disabled without `can_create_reports` with a hint).
- `ReportViewPage`: "Zeitplan anlegen" next to the export buttons (when `can_create_reports`) → `/agent/reports/schedules/new?report=<key>`.
- `ReportExportButton`: third button "Als PDF" (`data-testid="report-export-pdf"`), same job dialog.
- `ZnunyStatsImportDialog`: tabs "Aus Znuny" (list from preview) / "Aus Datei" (file input `.xml`, max 2 MB, then preview); per stat: checkbox, title, object label, target badge ("Bericht" / "Gespeicherte Suche" / "Nicht übernehmbar"), "bereits übernommen" badge, expandable issue list (dropped = "Nicht übernommen", approximated = "Angepasst", each with element, value, reason); "Übernehmen (n)" → apply → result list with links (`/agent/reports/<id>` or `/agent/searches/<id>`). Load errors listed at the bottom. Empty state "In Znuny sind keine Statistiken gespeichert, die Sie sehen dürfen."
- `scheduleForm.ts` (pure): `ScheduleFormState`, `emptyForm(defaults)`, `fromSchedule(out)`, `toRequest(state)`, `validateForm(state, canShare, externalAllowed) -> Record<string, string>` (i18n keys), `defaultSubjectHint(language)`.
- `RecurrenceFields`: kind select (Täglich / Wöchentlich / Monatlich / Jährlich), time input, weekday toggles, day of month (1–31, hint "In kürzeren Monaten am letzten Tag"), month select, "nur Montag bis Freitag", time-zone select (existing time-zone option source used by the profile page — grep it), live "Nächste Termine" list from `previewRecurrence` (debounced 400 ms).
- `RecipientsFields`: agent multi-select (existing agent picker), group multi-select (`/reference/share-groups`), role multi-select (existing admin roles reference if agent-readable; else omit roles in the UI and say so in the commit — backend still supports them), e-mail chips, shown only when `UserMe.report_external_recipients` is true (added in Task 10, filled from `ReportingSettings.schedule_external_recipients`); stored e-mails of a schedule whose setting was switched off later still show, with the server's `problem` text. Without share right: only "an mich" (owner) and a hint "Zum Versand an andere fehlt die Berechtigung."
- `ScheduleReportsPicker`: ordered list (max 10) of reports from the catalogue (built-ins + visible saved), up/down/remove, per item an optional "Zeitraum für den Versand" (time-range preset select reusing the viewer's presets) .
- `ScheduleRunsTable`: status chip, due/finished time (local), rows, recipients, detail/error, file links; "Jetzt senden" split button ("Testversand an mich" / "An alle Empfänger senden"), polls the run list every 5 s while a run is queued/running (TanStack Query `refetchInterval`).
- `ReportSchedulesPage`: table name / reports / Rhythmus (`recurrence_text`) / next run / last status / active toggle (PUT with version) / owner (admin "alle anzeigen" switch); empty state with "Zeitplan anlegen".
- i18n namespace `reports.schedules.*`, `reports.znunyImport.*`, `reports.exportPdf`, `jobs.kinds.core.report_delivery`, daemon service names/descriptions for `report_scheduler` and `report_runs_purge` (same place as the existing `report_facts` entry), admin reporting settings field labels (D2).

### D16. Test module skeleton

As wave-2 plan D13: module docstring naming band and seeds; `pytestmark = pytest.mark.db`; `LO, HI`; `_CLEANUP` (new `tiqora_report_run_file` → `tiqora_report_run` → schedule recipients/items → schedules by `owner_user_id BETWEEN :lo AND :hi`; saved reports/searches of band owners; `xml_storage` rows by key list; then Znuny seeds `group_user`, `role_user`, `user_preferences`, `permission_groups`, `roles`, `queue`, `users`, tickets); `_run_sql(sync_url, statements)` with `TiqoraBase.metadata.create_all`; module autouse cleanup on every URL; `env` fixture parametrised over `mariadb_znuny_url`/`postgres_znuny_url` with `create_app(Settings(environment="test"))`, `dependency_overrides[get_current_user]` (switchable via a holder) and `[get_db]`, `httpx.AsyncClient(transport=ASGITransport(app=app))`; per-test `_RESET` before and after. Seed agents `rep52.<role>` with `UserEmail` = `rep52.<role>@example.org`.

### Deviations from spec

1. **Run files are stored in `tiqora_report_run_file`, not through `StorageBackend`** (spec §9). `StorageBackend` is the read-only article-attachment protocol; job files (`tiqora_job_file`) are purged after `jobs.retention_days` (14) and are creator-only. Own table with `run_retention_days` (90) and owner-or-admin download (A17).
2. **One `tiqora_report_run` row per delivery**, holding the list of report keys, instead of one per report (spec: `report_id` per run). A delivery succeeds or fails as a whole (A15); per-report row counts go into `detail`.
3. **No `zip` format.** Formats are `pdf | xlsx | csv`; a CSV bundle of more than one report is zipped automatically.
4. **Recurrence presets only, no cron** (A4); `tiqora/util/cron.py` stays GenericAgent's (it is naive UTC).
5. **Znuny `Dynamic::TicketList` becomes a saved search** (A1); spec maps it to a definition.
6. **Two label paths.** Interactive exports (XLSX/CSV/PDF) keep the browser-translated labels of wave 1 (`exportLabels`); unattended runs translate on the server from a committed copy of the frontend `reports.*` texts, German and English only (A3, A18).
7. **Import is inline, bounded** (A9), not a job; the spec's "async" rule is kept by the caps in D7.
8. **Failure notification is a mail to the owner** plus the run history and the Daemons page tick status; Tiqora has no in-app notice for agents yet.
9. **HTML for the PDF is built with `html.escape`, no template engine** (Jinja2 is only a transitive dependency; nothing new to sandbox).
10. **The ITSM stats** (CI/change, first-level solution rate) are not part of this import: the ITSM objects do not exist in Tiqora yet (wave 4).
11. **Not in this plan:** NL questions and MCP tools (phase 4), report widgets / "show as dashboard widget" (dashboards plan, A14), XLSX streaming beyond the existing writer, dynamic-field report dimensions (A12), a ticket-list report source.

---

## Tasks

Order: backend Tasks 1→13. Tasks 2–4 (Znuny parsing/mapping) do not depend on Task 1 (they touch no new table) and can run in parallel with Tasks 6, 7, 9 (all pure); Task 3 needs Task 2, Task 4 needs Task 3. Task 5 needs 2–4. Task 8 needs 6–7. Task 10 needs 1 and 9. Task 11 needs 6–8 and 10. Task 12 needs 11. Task 13 needs 10. Frontend Tasks 14→17 run sequentially (shared locale files) after the openapi regeneration of Tasks 5, 8, 10. Task 18 closes.

Sizing: Tasks 11 and 16 are the largest (about 5–6 h each). If the orchestrator budgets strictly 1–4 h per subagent, split them at these seams (each half has its own tests and commit): **11a** delivery job + failure path (`delivery.py`, handler registration; tests drive the job through `create_run` + `enqueue_delivery` directly: retry, permanent failure/auto-pause, owner scope, owner without access, deleted report, only-if-nonempty, attachment size, interrupted, placeholders) and **11b** the three routes (`/run` with dedupe 409, `/runs`, run-file download with owner/admin check; route tests + openapi regeneration); **16a** `scheduleForm.ts` + `RecurrenceFields` + `RecipientsFields` + `ScheduleReportsPicker` with their tests, and **16b** the two pages, `ScheduleRunsTable`, routes and the `ReportViewPage` button.

Seed sub-bands: T1 52000–52019, T2 52020–52039, T5 52040–52099, T8 52100–52119, T10 52120–52199, T11 52200–52259, T12 52260–52299, T13 52300–52319; 52320–52399 reserved (fix rounds).

---

### Task 1: Tables, ORM, migration, reporting settings (backend)

**Files:**
- Create: `backend/alembic/versions_tiqora/<YYYYMMDD>_<NNNN>_report_schedules.py` (next free number after the current head; `down_revision` = current head — check, never copy an id from this plan)
- Modify: `backend/src/tiqora/db/tiqora/models.py` (five classes of D2), `backend/src/tiqora/reporting/settings.py` (D2 fields), `backend/tests/test_migration_gate.py` (`TIQORA_HEAD`), `backend/tests/test_migrate_cli.py`, `backend/tests/test_tiqora_model_ddl.py` (if it lists tables), `frontend/src/i18n/locales/en.json` + `de.json` + propagated locales (admin settings field labels only)
- Test: `backend/tests/test_report_schedule_tables.py` (both dialects, band 52000–52019)

**Interfaces:**
- Produces: `TiqoraReportSchedule`, `TiqoraReportScheduleItem`, `TiqoraReportScheduleRecipient`, `TiqoraReportRun`, `TiqoraReportRunFile` (columns exactly D2); `ReportingSettings.schedule_external_recipients`, `.schedule_max_recipients`, `.run_retention_days`, `.pdf_row_limit`, `.delivery_max_attachment_mb`.

- [ ] **Step 1: Write the failing test**

```python
"""DDL, defaults and cascades of the report schedule/run tables (both dialects).

Band 52000-52019: owner user ids only (owner_user_id has no FK; no Znuny rows seeded).
"""
from __future__ import annotations

from collections.abc import Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect, text

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.reporting.settings import ReportingSettings

pytestmark = pytest.mark.db
LO, HI = 52000, 52019
T0 = datetime(2026, 10, 11, 7, 0)
_URLS: set[str] = set()
_CLEANUP = (
    "DELETE FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi",
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
def test_columns_and_indexes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    insp = inspect(engine)
    assert {c["name"] for c in insp.get_columns("tiqora_report_schedule")} == {
        "id", "owner_user_id", "name", "format", "recurrence", "time_zone", "language", "subject",
        "body", "only_if_nonempty", "active", "paused_reason", "next_run_at", "last_run_at",
        "last_status", "failure_count", "version", "created_at", "changed_at"}
    assert insp.get_pk_constraint("tiqora_report_schedule_item")["constrained_columns"] == [
        "schedule_id", "position"]
    assert {c["name"] for c in insp.get_columns("tiqora_report_run")} >= {
        "schedule_id", "schedule_name", "run_kind", "owner_only", "due_at", "job_id", "status",
        "report_keys", "row_count", "recipient_count", "detail", "error"}
    assert {i["name"] for i in insp.get_indexes("tiqora_report_schedule")} >= {
        "ix_tiqora_report_schedule_due", "ix_tiqora_report_schedule_owner"}
    engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
def test_defaults_and_cascade(url_fixture: str, request: pytest.FixtureRequest) -> None:
    engine = _engine(request.getfixturevalue(url_fixture))
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tiqora_report_schedule (owner_user_id, name, recurrence, time_zone, subject, body,"
            " created_at, changed_at) VALUES (52001, 'rep52 s', '{}', 'Europe/Berlin', '', '', :t, :t)"),
            {"t": T0})
        sid = conn.execute(text(
            "SELECT id FROM tiqora_report_schedule WHERE owner_user_id = 52001")).scalar_one()
        row = conn.execute(text(
            "SELECT format, language, active, only_if_nonempty, failure_count, version"
            " FROM tiqora_report_schedule WHERE id = :i"), {"i": sid}).one()
        assert (row.format, row.language, bool(row.active), bool(row.only_if_nonempty),
                row.failure_count, row.version) == ("pdf", "de", True, False, 0, 1)
        conn.execute(text("INSERT INTO tiqora_report_schedule_item (schedule_id, position, report_key)"
                          " VALUES (:s, 0, 'builtin.volume')"), {"s": sid})
        conn.execute(text("INSERT INTO tiqora_report_schedule_recipient (schedule_id, kind, ref_id)"
                          " VALUES (:s, 'user', 52001)"), {"s": sid})
        conn.execute(text("DELETE FROM tiqora_report_schedule WHERE id = :s"), {"s": sid})
        for table in ("tiqora_report_schedule_item", "tiqora_report_schedule_recipient"):
            assert conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE schedule_id = :s"),
                                {"s": sid}).scalar_one() == 0
        conn.execute(text(
            "INSERT INTO tiqora_report_run (schedule_name, owner_user_id, run_kind, status, report_keys,"
            " created_at) VALUES ('rep52 s', 52002, 'manual', 'queued', '[]', :t)"), {"t": T0})
        rid = conn.execute(text("SELECT id FROM tiqora_report_run WHERE owner_user_id = 52002")).scalar_one()
        conn.execute(text(
            "INSERT INTO tiqora_report_run_file (run_id, name, content_type, size_bytes, data, created_at)"
            " VALUES (:r, 'a.pdf', 'application/pdf', 3, :d, :t)"), {"r": rid, "d": b"%PD", "t": T0})
        conn.execute(text("DELETE FROM tiqora_report_run WHERE id = :r"), {"r": rid})
        assert conn.execute(text("SELECT COUNT(*) FROM tiqora_report_run_file WHERE run_id = :r"),
                            {"r": rid}).scalar_one() == 0
    engine.dispose()


def test_reporting_settings_defaults() -> None:
    s = ReportingSettings()
    assert (s.schedule_external_recipients, s.schedule_max_recipients, s.run_retention_days,
            s.pdf_row_limit, s.delivery_max_attachment_mb) == (False, 50, 90, 1000, 15)
```

- [ ] **Step 2: Run → FAIL** `cd backend && TIQORA_STRICT_DB_LEAKS=1 uv run python -m pytest -q tests/test_report_schedule_tables.py` (tables/fields missing). Also `grep -rn "tiqora_report" backend/tests backend/src | grep -E "LIKE|startswith|%"` — any prefix match on `tiqora_report` would now include the new tables; switch hits to explicit table names.
- [ ] **Step 3: Implement** ORM classes (`MEDIUM_TEXT_VARIANT` for `body`, `JOB_FILE_BLOB` + `deferred` for `data`, FK names and `ondelete="CASCADE"` as D2, indexes in `__table_args__`), the migration (`op.create_table` ×5 in dependency order; downgrade in reverse), the settings fields, `TIQORA_HEAD`/`test_migrate_cli.py`. Admin settings labels: add `admin.settings.reporting.fields.<name>` keys (copy the existing key shape) in en/de, run `node frontend/scripts/propagate-i18n-keys.mjs`.
- [ ] **Step 4: Run → PASS** the new module plus `tests/test_tiqora_model_ddl.py tests/test_migration_gate.py tests/test_migrate_cli.py tests/test_reporting_settings*.py` (whatever exists for the section); `uv run alembic heads` shows one primary head (+ the gated chain). Lint gates; `cd frontend && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Commit** `feat(reporting): tables for report schedules and delivery runs`.

---

### Task 2: Znuny stats parsing — XMLHash, export files, reader (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/znuny_stats/__init__.py`, `xml_hash.py`, `model.py`, `reader.py` (reader lookups part: `load_stat_lookups` comes in Task 3 — create the function stub only when Task 3 needs it; here `list_stat_ids`, `load_stats`)
- Create: `backend/tests/fixtures/znuny_stats/` — copy `scripts/test/sample/Stats/Stats.TicketOverview.de.xml` and `Stats.TestTicketList.en.xml` from the Znuny source (they are Znuny's own test samples; re-encode the ISO-8859-1 one keeping its declaration), plus made-up files: `matrix_time_axis.xml` (Dynamic::Ticket, X = CreateTime scale Month relative 12 months upcoming 1, series QueueIDs Fixed=0, restriction StateTypeIDs, SumCol=1, Format D3::LineChart, TimeZone Europe/Berlin), `accounted_time.xml` (X = KindsOfReporting TotalTime+NumberOfTickets+TicketMinTime, series AccountedByAgent, restriction ArticleAccountedTime absolute 2026-09-01 00:00:00–2026-09-30 23:59:59), `solution_response.xml` (X = KindsOfReporting with four mappable + ResponseMinTime + SolutionAverage, series PriorityIDs), `static_state_action.xml` (StatType static, File StateAction), `unsupported.xml` (DynamicField_Kundenwert restriction, EscalationTime restriction, scale HalfYear, upcoming 3, ShowAsDashboardWidget 1, Valid 0). Titles/descriptions made up ("rep52 Tickets pro Monat"), no real names.
- Test: `backend/tests/test_znuny_stats_parse.py` (pure + one DB test both dialects, band 52020–52039)

**Interfaces:**
- Produces: D3 (`XmlHashError`, `parse_content_key`, `build_xml_hash`, `xml_file_to_hash`, `MAX_STAT_ROWS`, `MAX_FILE_BYTES`, `StatElement`, `ZnunyStat`, `stat_from_xml_hash`, `TIME_KEYS`), D4 (`MAX_PREVIEW_STATS`, `StatLoadError`, `list_stat_ids`, `load_stats`).

- [ ] **Step 1: Read the Znuny source** (`Kernel/System/XML.pm` `XMLHashGet` and `_XMLHash2D`, `Kernel/System/Stats.pm` `StatsGet` lines 186–400 and `Import` 1357+) and note in the module docstring how keys/values are escaped and which keys `StatsGet` reads. Adjust D3 only if the source contradicts it (update this plan in the same commit).
- [ ] **Step 2: Write the failing tests**

```python
FIX = Path(__file__).parent / "fixtures" / "znuny_stats"

def test_parse_content_key() -> None:
    assert parse_content_key("[1]{'otrs_stats'}[1]{'Title'}[1]{'Content'}") == [
        1, "otrs_stats", 1, "Title", 1, "Content"]
    assert parse_content_key("[1]{'a\\'b'}[2]{'c\\\\d'}") == [1, "a'b", 2, "c\\d"]
    for bad in ("", "[x]", "{'a'}[1", "[1]{a}", "[1]{'a'}junk", "[-1]"):
        with pytest.raises(XmlHashError):
            parse_content_key(bad)

def test_values_with_quotes_backslashes_newlines() -> None:
    rows = [
        ("[1]{'otrs_stats'}[1]{'Title'}[1]{'Content'}", "It's a \\ \"test\"\nline 2"),
        ("[1]{'otrs_stats'}[1]{'ObjectModule'}[1]{'Content'}", "Kernel::System::Stats::Dynamic::Ticket"),
        ("[1]{'otrs_stats'}[1]{'StatType'}[1]{'Content'}", "dynamic"),
        ("[1]{'otrs_stats'}[1]{'Description'}[1]{'Content'}", None),
    ]
    stat = stat_from_xml_hash(build_xml_hash(rows), origin="db", stat_id=52020)
    assert stat.title == "It's a \\ \"test\"\nline 2"
    assert stat.description == ""
    assert stat.import_key == "znuny.stat.52020"

def test_elements_from_db_rows() -> None:
    base = "[1]{'otrs_stats'}[1]"
    rows = [
        (f"{base}{{'Title'}}[1]{{'Content'}}", "rep52 Matrix"),
        (f"{base}{{'ObjectModule'}}[1]{{'Content'}}", "Kernel::System::Stats::Dynamic::Ticket"),
        (f"{base}{{'StatType'}}[1]{{'Content'}}", "dynamic"),
        (f"{base}{{'Permission'}}[1]{{'Content'}}", "3"),
        (f"{base}{{'Permission'}}[2]{{'Content'}}", "7"),
        (f"{base}{{'UseAsXvalue'}}[1]{{'Element'}}", "CreateTime"),
        (f"{base}{{'UseAsXvalue'}}[1]{{'Fixed'}}", "1"),
        (f"{base}{{'UseAsXvalue'}}[1]{{'TimeRelativeUnit'}}", "Month"),
        (f"{base}{{'UseAsXvalue'}}[1]{{'TimeRelativeCount'}}", "12"),
        (f"{base}{{'UseAsXvalue'}}[1]{{'SelectedValues'}}[1]{{'Content'}}", "Month"),
        (f"{base}{{'UseAsRestriction'}}[1]{{'Element'}}", "QueueIDs"),
        (f"{base}{{'UseAsRestriction'}}[1]{{'Fixed'}}", "0"),
        (f"{base}{{'UseAsRestriction'}}[1]{{'SelectedValues'}}[1]{{'Content'}}", "4"),
        (f"{base}{{'UseAsRestriction'}}[1]{{'SelectedValues'}}[2]{{'Content'}}", "5"),
    ]
    stat = stat_from_xml_hash(build_xml_hash(rows), origin="db", stat_id=52021)
    assert stat.permission == ("3", "7")
    x, r = stat.elements
    assert (x.use, x.element, x.fixed, x.selected) == ("x", "CreateTime", True, ("Month",))
    assert dict(x.time) == {"TimeRelativeUnit": "Month", "TimeRelativeCount": "12"}
    assert (r.use, r.element, r.fixed, r.selected) == ("restriction", "QueueIDs", False, ("4", "5"))

def test_conflicting_paths_raise() -> None:
    with pytest.raises(XmlHashError):
        build_xml_hash([("[1]{'a'}[1]{'b'}", "x"), ("[1]{'a'}{'b'}", "y")])

def test_export_files_parse() -> None:
    overview = stat_from_xml_hash(xml_file_to_hash((FIX / "Stats.TicketOverview.de.xml").read_bytes()),
                                  origin="file", stat_id=None)
    assert overview.title.startswith("Überblick")
    assert overview.sum_row and overview.sum_col and overview.permission == ("stats",)
    assert [(e.use, e.element) for e in overview.elements] == [("x", "StateIDs"), ("series", "QueueIDs")]
    lst = stat_from_xml_hash(xml_file_to_hash((FIX / "Stats.TestTicketList.en.xml").read_bytes()),
                             origin="file", stat_id=None)
    assert lst.object_module.endswith("::TicketList") and lst.import_key.startswith("znuny.file.")

def test_file_rejects_entities_and_wrong_root() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><otrs_stats><Title>&a;</Title></otrs_stats>'
    with pytest.raises(XmlHashError):
        xml_file_to_hash(bomb)
    with pytest.raises(XmlHashError):
        xml_file_to_hash(b"<foo/>")
    with pytest.raises(XmlHashError):
        xml_file_to_hash(b"<otrs_stats>" + b"x" * (MAX_FILE_BYTES + 1) + b"</otrs_stats>")


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_stats_from_xml_storage(url_fixture: str, request: pytest.FixtureRequest) -> None:
    # seed two stats (keys '52022', '52023') + one with a broken key ('52024') + a non-numeric key 'rep52x';
    # list_stat_ids -> [.., 52022, 52023, 52024, ..] (non-numeric skipped);
    # load_stats([52022, 52023, 52024]) -> two ZnunyStat, one StatLoadError(52024, German message)
    ...
```

(The DB test seeds `xml_storage` with `INSERT INTO xml_storage (xml_type, xml_key, xml_content_key, xml_content_value)` on both dialects, deletes by `xml_key IN ('52022','52023','52024','rep52x')` before and after; write the body fully.)

- [ ] **Step 3: Run → FAIL** (`ModuleNotFoundError`).
- [ ] **Step 4: Implement** D3/D4. Tokenizer: a hand-written scanner (index loop), not a regex with backtracking. `xml_file_to_hash`: `defusedxml.ElementTree.fromstring(content, forbid_dtd=True)`; map `defusedxml` exceptions and `ParseError` to `XmlHashError("Die Datei ist keine gültige Znuny-Statistik (XML).")`. Encoding: let the XML declaration decide (ISO-8859-1 sample must work). `load_stats`: string keys in the IN-list (`str(i)`), chunks of 200, one SELECT per chunk ordered by `xml_key, xml_content_key`.
- [ ] **Step 5: Run → PASS**; lint gates.
- [ ] **Step 6: Commit** `feat(reporting): read Znuny statistics from xml_storage and export files`.

---

### Task 3: Znuny stats mapping — common rules, ticket matrix, StateAction (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/znuny_stats/mapping_common.py`, `mapper.py`
- Modify: `backend/src/tiqora/reporting/znuny_stats/reader.py` (`load_stat_lookups`, DB part tested in Task 5)
- Test: `backend/tests/test_znuny_stats_map_matrix.py` (pure; builds `StatLookups` by hand)

**Interfaces:**
- Consumes: Task 2 `ZnunyStat`, `StatElement`; `ReportDefinition` & co.; `validate_definition(defn, registry)`; `STATE_TYPES` (`tiqora.reporting.sources._common`); `ZnunyLookups`.
- Produces: D5 (`ImportIssue`, `IssueSeverity`, `StatLookups`, `FieldMap`, `ELEMENT_FIELDS_TICKETS`, `FILE_NAME_ELEMENTS`, `resolve_values`, `map_time_range`, `map_grain`, `map_visualization`, `map_totals`, `common_issues`, `StatTarget`, `MappedSearch`, `StatMapping`, `map_stat`); `load_stat_lookups(session, user_id) -> StatLookups`. Task 4 registers its object mappers through a module-level dict `OBJECT_MAPPERS: dict[str, Callable[[ZnunyStat, StatLookups, bool], StatMapping]]` in `mapper.py` (keys = object module names; static files under `"static:<File>"`).

- [ ] **Step 1: Read** `Kernel/System/Stats/Dynamic/Ticket.pm` (`GetObjectAttributes`, `ExportWrapper` lines 858–918) and `Kernel/System/Stats.pm` `_GenerateDynamicStats` (time handling ~2290–2420) and confirm D6.1–D6.4; copy the exact `ExportWrapper` element list into `FILE_NAME_ELEMENTS`.
- [ ] **Step 2: Write the failing tests**

```python
LOOKUPS = StatLookups(
    state_type_names={1: "new", 2: "open", 3: "closed", 9: "merged"},
    queue_ids_by_name={"raw": 2, "junk": 3, "support::level 1": 12},
    state_ids_by_name={"open": 4, "closed successful": 2},
    priority_ids_by_name={"3 normal": 3},
    user_ids_by_login={"rep52.agent": 52005},
    group_ids_by_name={"stats": 9, "users": 1},
    default_time_zone="Europe/Berlin",
    search=ZnunyLookups(state_type_names={}, queue_names={}, lock_names={}, dynamic_fields={}, time_zone="Europe/Berlin"),
)

def _stat(*elements: StatElement, **kw: Any) -> ZnunyStat:
    base = dict(origin="db", stat_id=52030, stat_number="10030", title="rep52 Matrix", description="",
                stat_type="dynamic", object_module="Kernel::System::Stats::Dynamic::Ticket",
                static_file=None, valid=True, permission=("9",), formats=("Print",), sum_row=False,
                sum_col=False, time_zone=None, show_as_dashboard_widget=False, elements=elements)
    base.update(kw)
    return ZnunyStat(**base)

def el(use, element, *selected, fixed=True, **time) -> StatElement:
    return StatElement(use=use, element=element, fixed=fixed, selected=tuple(selected), time=time)

def test_overview_matrix() -> None:   # Znuny's own sample: series QueueIDs, X StateIDs, sums
    m = map_stat(_stat(el("series", "QueueIDs"), el("x", "StateIDs"), sum_row=True, sum_col=True),
                 LOOKUPS, is_admin=False)
    assert m.target == "report"
    d = m.definition
    assert d.source == "tickets" and [x.key for x in d.dimensions] == ["state", "queue"]
    assert [x.key for x in d.measures] == ["tickets"]
    assert d.totals == Totals(grand=True, rows=True, columns=True)
    assert d.visualization.type == "pivot"

def test_time_axis_relative_and_runtime_parameter() -> None:
    m = map_stat(_stat(
        el("x", "CreateTime", "Month", TimeRelativeUnit="Month", TimeRelativeCount="12",
           TimeRelativeUpcomingCount="1"),
        el("restriction", "QueueIDs", "4", "5", fixed=False),
        el("restriction", "StateTypeIDs", "1", "2"),
        formats=("D3::LineChart",), time_zone="Europe/Berlin"), LOOKUPS, is_admin=False)
    d = m.definition
    assert d.dimensions[0].key == "created" and d.dimensions[0].grain == "month"
    assert d.time_field == "created"
    assert d.time_range == RelativeTimeRange(unit="month", count=12, include_current=True)
    assert d.time_zone == "Europe/Berlin" and d.visualization.type == "line"
    assert d.parameters[0].field == "queue" and d.parameters[0].default == [4, 5]
    assert d.filter.model_dump()["where"] == {"field": "state_type", "op": "in", "value": ["new", "open"]}

def test_absolute_range_rounding_is_reported() -> None:
    m = map_stat(_stat(el("x", "QueueIDs", "4"),
                       el("restriction", "CreateTime", TimeStart="2026-09-01 08:00:00",
                          TimeStop="2026-09-30 23:59:59")), LOOKUPS, is_admin=False)
    assert m.definition.time_range == AbsoluteTimeRange(start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert any(i.severity == "approximated" and "gerundet" in i.reason for i in m.issues)

def test_every_dropped_element_is_listed() -> None:
    m = map_stat(_stat(
        el("x", "QueueIDs", "4"),
        el("restriction", "DynamicField_Kundenwert", "A"),
        el("restriction", "EscalationTime", TimeRelativeUnit="Day", TimeRelativeCount="3"),
        el("restriction", "LockIDs", "2"),
        el("restriction", "CustomerID", "rep52*"),
        el("restriction", "SearchInArchive", "AllTickets"),
        el("series", "CreateTime", "HalfYear", TimeRelativeUnit="Year", TimeRelativeCount="1"),
        show_as_dashboard_widget=True, valid=False), LOOKUPS, is_admin=False)
    dropped = {i.element for i in m.issues if i.severity == "dropped"}
    assert dropped >= {"UseAsRestriction.DynamicField_Kundenwert", "UseAsRestriction.EscalationTime",
                       "UseAsRestriction.LockIDs", "UseAsRestriction.CustomerID",
                       "UseAsRestriction.SearchInArchive", "UseAsValueSeries.CreateTime",
                       "ShowAsDashboardWidget"}
    assert any(i.element == "Valid" for i in m.issues)
    assert m.target == "report" and [x.key for x in m.definition.dimensions] == ["queue"]
    assert all(i.reason.endswith(".") for i in m.issues)               # full German sentences

def test_relative_units() -> None:
    issues: list[ImportIssue] = []
    assert map_time_range(el("restriction", "CreateTime", TimeRelativeUnit="HalfYear", TimeRelativeCount="2"), issues) == \
        RelativeTimeRange(unit="month", count=12, include_current=False)
    assert map_time_range(el("restriction", "CreateTime", TimeRelativeUnit="Hour", TimeRelativeCount="5"), issues) is None
    assert map_time_range(el("restriction", "CreateTime", TimeRelativeUnit="Week", TimeRelativeCount="4",
                             TimeRelativeUpcomingCount="3"), issues) == \
        RelativeTimeRange(unit="week", count=4, include_current=True)
    assert [i.severity for i in issues] == ["approximated", "dropped", "approximated"]

def test_file_origin_names_to_ids() -> None:
    m = map_stat(_stat(el("x", "QueueIDs", "Junk", "Support::Level 1", "Nope"), origin="file", stat_id=None),
                 LOOKUPS, is_admin=False)
    assert m.definition.filter.model_dump()["where"]["value"] == [3, 12]
    assert any(i.value == "Nope" and "gibt es in diesem System nicht" in i.reason for i in m.issues)

def test_state_action_static() -> None:
    m = map_stat(_stat(stat_type="static", object_module="Kernel::System::Stats::Static::StateAction",
                       static_file="StateAction"), LOOKUPS, is_admin=False)
    d = m.definition
    assert d.source == "ticket_events" and [x.key for x in d.dimensions] == ["occurred", "kind"]
    assert d.time_range == RelativeTimeRange(unit="month", count=1)
    assert [i.severity for i in m.issues] == ["approximated"]

def test_unknown_object_is_none() -> None:
    m = map_stat(_stat(object_module="Kernel::System::Stats::Dynamic::ITSMChangeManagement"), LOOKUPS, is_admin=False)
    assert m.target == "none" and m.definition is None and m.issues[0].element == "Object"

def test_fixtures_map_and_validate() -> None:
    for name in ("Stats.TicketOverview.de.xml", "matrix_time_axis.xml", "static_state_action.xml", "unsupported.xml"):
        stat = stat_from_xml_hash(xml_file_to_hash((FIX / name).read_bytes()), origin="file", stat_id=None)
        m = map_stat(stat, LOOKUPS, is_admin=False)
        if m.definition is not None:
            validate_definition(m.definition, get_registry())   # never raises
```

- [ ] **Step 3: Run → FAIL**.
- [ ] **Step 4: Implement** D5/D6.1–D6.5 for `Dynamic::Ticket` and `static:StateAction`; `map_stat` catches `DefinitionError` from a final `validate_definition` and converts it to target `none` + issue. Parameter keys: `f"p{index}_{field}"` truncated to 40 and matching `^[a-z][a-z0-9_]{0,39}$`. Issues sorted by `(element, value)`. `load_stat_lookups`: one query per Znuny table (`queue` with full names as Znuny stores them, `ticket_state`, `ticket_priority`, `users` valid, `permission_groups` valid, `ticket_state_type`) + `resolve_user_time_zone` + wave-2 `load_lookups` for `search`.
- [ ] **Step 5: Run → PASS**; lint gates.
- [ ] **Step 6: Commit** `feat(reporting): map Znuny ticket matrix and StateAction statistics to reports`.

---

### Task 4: Znuny stats mapping — accounted time, solution/response time, ticket list (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/znuny_stats/map_more.py`
- Modify: `backend/src/tiqora/reporting/znuny_stats/mapper.py` (register in `OBJECT_MAPPERS`)
- Test: `backend/tests/test_znuny_stats_map_more.py`

**Interfaces:**
- Consumes: Task 3 helpers; wave-2 `map_search_profile`, `ProfileValues`, `TicketSortKey`.
- Produces: `map_accounted_time`, `map_solution_response`, `map_ticket_list`, `stat_to_profile_values(stat, lookups) -> tuple[ProfileValues, list[ImportIssue]]` (D6.5/D6.6).

- [ ] **Step 1: Read** `TicketAccountedTime.pm` and `TicketSolutionResponseTime.pm` (`GetObjectAttributes`, `_KindsOfReporting`, `ExportWrapper`) and `TicketList.pm` (`_TicketAttributes`, `OrderBy`, `SortSequence`, `Limit`, historic elements); confirm the tables of D6.5/D6.6.
- [ ] **Step 2: Write the failing tests**

```python
def test_kinds_of_reporting_partial() -> None:
    m = map_stat(_stat(el("x", "KindsOfReporting", "TotalTime", "NumberOfTickets", "TicketMinTime"),
                       el("series", "AccountedByAgent", "52005"),
                       el("restriction", "ArticleAccountedTime", TimeStart="2026-09-01 00:00:00",
                          TimeStop="2026-09-30 23:59:59"),
                       el("restriction", "StateIDs", "4"),
                       object_module="Kernel::System::Stats::Dynamic::TicketAccountedTime"),
                 LOOKUPS, is_admin=False)
    d = m.definition
    assert d.source == "accounted_time"
    assert [x.key for x in d.measures] == ["time_units", "tickets"]
    assert [x.key for x in d.dimensions] == ["agent"] and d.time_field == "booked"
    assert {(i.element, i.value) for i in m.issues if i.severity == "dropped"} >= {
        ("UseAsXvalue.KindsOfReporting", "TicketMinTime"), ("UseAsRestriction.StateIDs", "4")}

def test_kinds_none_mappable_is_none() -> None:
    m = map_stat(_stat(el("x", "KindsOfReporting", "ArticleAverage"),
                       object_module="Kernel::System::Stats::Dynamic::TicketAccountedTime"), LOOKUPS, is_admin=False)
    assert m.target == "none"

def test_solution_response_measures() -> None:
    m = map_stat(_stat(el("x", "KindsOfReporting", "SolutionAverageAllOver", "NumberOfTicketsAllOver",
                          "SolutionWorkingTimeAverage", "ResponseWorkingTimeAverage", "ResponseMinTime",
                          "SolutionAverage"),
                       el("series", "PriorityIDs", "3"),
                       object_module="Kernel::System::Stats::Dynamic::TicketSolutionResponseTime"),
                 LOOKUPS, is_admin=False)
    assert [x.key for x in m.definition.measures] == [
        "solution_clock_avg", "closed_tickets", "solution_avg", "first_response_avg"]
    assert [x.key for x in m.definition.dimensions] == ["priority"]
    reasons = {i.value: i.reason for i in m.issues}
    assert "Median" in reasons["ResponseMinTime"] and "Eskalation" in reasons["SolutionAverage"]

def test_ticket_list_becomes_saved_search() -> None:
    stat = _stat(el("restriction", "QueueIDs", "3"),
                 el("restriction", "CreateTime", TimeRelativeUnit="Month", TimeRelativeCount="2"),
                 el("restriction", "MIMEBase_Subject", "Rechnung"),
                 el("restriction", "DynamicField_Kundenwert", "A"),
                 el("x", "TicketAttributes", "TicketNumber", "Title"),
                 el("restriction", "OrderBy", "Created"), el("restriction", "SortSequence", "Up"),
                 el("restriction", "Limit", "100"),
                 object_module="Kernel::System::Stats::Dynamic::TicketList")
    values, issues = stat_to_profile_values(stat, LOOKUPS)
    assert values["QueueIDs"] == ["3"]
    assert (values["TimeSearchType"], values["TicketCreateTimePoint"], values["TicketCreateTimePointStart"],
            values["TicketCreateTimePointFormat"]) == ("TimePoint", "2", "Last", "month")
    assert values["Subject"] == "Rechnung" and "DynamicField_Kundenwert" not in values
    m = map_stat(stat, LOOKUPS, is_admin=False)
    assert m.target == "saved_search" and m.definition is None
    assert (m.search.sort_by, m.search.sort_order) == ("created", "asc")
    elements = {i.element for i in m.issues}
    assert {"UseAsRestriction.Subject", "UseAsRestriction.DynamicField_Kundenwert",
            "UseAsXvalue.TicketAttributes", "UseAsRestriction.Limit"} <= elements

def test_ticket_list_absolute_slot_and_halfyear() -> None:
    stat = _stat(el("restriction", "ChangeTime", TimeStart="2026-01-01 00:00:00", TimeStop="2026-06-30 23:59:59"),
                 el("restriction", "PendingUntilTime", TimeRelativeUnit="HalfYear", TimeRelativeCount="1"),
                 object_module="Kernel::System::Stats::Dynamic::TicketList")
    values, _ = stat_to_profile_values(stat, LOOKUPS)
    assert values["ChangeTimeSearchType"] == "TimeSlot"
    assert (values["TicketChangeTimeStartYear"], values["TicketChangeTimeStopMonth"]) == ("2026", "06")
    assert (values["TicketPendingTimePoint"], values["TicketPendingTimePointFormat"]) == ("6", "month")

def test_sample_ticket_list_file() -> None:   # Znuny's own sample: QueueIDs "Junk" by name, Fixed=0
    stat = stat_from_xml_hash(xml_file_to_hash((FIX / "Stats.TestTicketList.en.xml").read_bytes()),
                              origin="file", stat_id=None)
    m = map_stat(stat, LOOKUPS, is_admin=False)
    assert m.target == "saved_search"
    assert m.search.criteria.model_dump()["where"] == {"field": "queue_id", "op": "in", "value": [3]}
```

(`queue_id` is the live ticket filter field the wave-2 mapper emits for `QueueIDs` — check `znuny_import.py` and use what it really emits.)

- [ ] **Step 3: Run → FAIL**.
- [ ] **Step 4: Implement** D6.5/D6.6. The TicketList adapter converts file-origin names with `resolve_values` before building `ProfileValues` (ids as strings, like Znuny profiles store them). `map_search_profile` `UnmappedItem` → `ImportIssue(f"UseAsRestriction.{item.key}", item.value, item.reason, "dropped")`.
- [ ] **Step 5: Run → PASS** this module and Task 3's; lint gates.
- [ ] **Step 6: Commit** `feat(reporting): map Znuny accounted-time, solution-time and ticket-list statistics`.

---

### Task 5: Znuny stats import — preview and apply API (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/znuny_stats/service.py`, `schemas.py`; `tests/golden/test_stats_import.py`
- Modify: `backend/src/tiqora/api/v1/reports.py` (three routes before `/{report_key}`), `packages/api-client/openapi.json`, `docs/api/openapi.json`, `packages/api-client/src/schema.d.ts` (generated)
- Test: `backend/tests/test_znuny_stats_import_api.py` (both dialects, band 52040–52099)

**Interfaces:**
- Consumes: Tasks 2–4; wave-2 `report_rights`, `create_saved_report`, `update_saved_report`, `upsert_imported`, `TiqoraReport.copied_from`, `TiqoraSavedSearch.source_name`, `PermissionEngine.groups_for_permission`.
- Produces: D7 (`StatPreviewOut`, `ZnunyStatsPreviewOut`, `ZnunyStatsFileIn`, `ZnunyStatsApplyIn`, `ZnunyStatsApplyOut`, `ImportedStatOut`, `ImportIssueOut`, `visible_stat_ids`, `preview_db`, `preview_file`, `apply_import`) and the three routes.

Seeds: agents 52040 `rep52.importer` (ro on group 52060), 52041 `rep52.viewonly` (with `reporting.access = stats_group`: ro on `stats`, no rw — set per test), 52042 `rep52.outsider`; group 52060 `rep52-group-52060`; queue 52070 `rep52-queue-52070` in group 52060; stats in `xml_storage` keys `52080` (matrix, Permission 52060), `52081` (TicketList, Permission 52060), `52082` (matrix, Permission = an unrelated group id 52061), `52083` (broken content key).

- [ ] **Step 1: Write the failing tests** (D16 skeleton)

```python
async def test_preview_lists_only_visible_stats(env) -> None:
    env.user = user(52040)
    out = (await env.client.get("/api/v1/reports/import/znuny")).json()
    keys = {s["import_key"] for s in out["stats"]}
    assert {"znuny.stat.52080", "znuny.stat.52081"} <= keys and "znuny.stat.52082" not in keys
    assert any(e["stat_id"] == 52083 for e in out["errors"])
    s80 = next(s for s in out["stats"] if s["stat_id"] == 52080)
    assert s80["target"] == "report" and s80["definition"]["source"] == "tickets"
    assert s80["already_imported"] is None

async def test_admin_sees_all(env) -> None:
    env.user = user(1)
    keys = {s["import_key"] for s in (await env.client.get("/api/v1/reports/import/znuny")).json()["stats"]}
    assert "znuny.stat.52082" in keys

async def test_apply_creates_then_updates(env) -> None:
    env.user = user(52040)
    r1 = (await env.client.post("/api/v1/reports/import/znuny/apply", json={"stat_ids": [52080, 52081]})).json()
    by_key = {i["import_key"]: i for i in r1["imported"]}
    assert by_key["znuny.stat.52080"]["target"] == "report" and by_key["znuny.stat.52080"]["action"] == "created"
    assert by_key["znuny.stat.52081"]["target"] == "saved_search"
    rep = (await env.client.get(f"/api/v1/reports/{by_key['znuny.stat.52080']['id']}")).json()
    assert rep["visibility"] == "private" and rep["copied_from"] == "znuny.stat.52080"
    search = (await env.client.get(f"/api/v1/saved-searches/{by_key['znuny.stat.52081']['id']}")).json()
    assert search["name"] == "rep52 Ticketliste" and search["source"] == "znuny"   # the stat's Title, not the import key
    r2 = (await env.client.post("/api/v1/reports/import/znuny/apply", json={"stat_ids": [52080]})).json()
    assert r2["imported"][0]["action"] == "updated" and r2["imported"][0]["id"] == by_key["znuny.stat.52080"]["id"]
    prev = (await env.client.get("/api/v1/reports/import/znuny")).json()
    assert next(s for s in prev["stats"] if s["stat_id"] == 52080)["already_imported"] == "report"

async def test_apply_needs_create_right(env) -> None:
    env.user = user(52041)
    await env.set_access("stats_group")
    resp = await env.client.post("/api/v1/reports/import/znuny/apply", json={"stat_ids": [52080]})
    assert resp.status_code == 403 and "Berechtigung" in resp.json()["detail"]["message"]

async def test_apply_ignores_invisible_ids(env) -> None:
    env.user = user(52040)
    out = (await env.client.post("/api/v1/reports/import/znuny/apply", json={"stat_ids": [52082, 999999]})).json()
    assert out["imported"] == [] and len(out["skipped"]) == 2

async def test_file_import_unknown_names_listed(env) -> None:
    env.user = user(52040)
    content = (FIX / "Stats.TestTicketList.en.xml").read_bytes()          # QueueIDs "Junk"
    body = {"content_base64": base64.b64encode(content).decode()}
    prev = (await env.client.post("/api/v1/reports/import/znuny/file", json=body)).json()
    stat = prev["stats"][0]
    assert stat["target"] == "saved_search" and stat["stat_id"] is None
    # "Junk" exists in the test DB (Znuny seed queue 3) -> mapped; then a variant with "rep52-missing" -> issue
    missing = content.replace(b"<SelectedValues>Junk</SelectedValues>", b"<SelectedValues>rep52-missing</SelectedValues>")
    prev2 = (await env.client.post("/api/v1/reports/import/znuny/file",
                                   json={"content_base64": base64.b64encode(missing).decode()})).json()
    assert any(i["value"] == "rep52-missing" for i in prev2["stats"][0]["issues"])

async def test_file_errors_are_422(env) -> None:
    env.user = user(52040)
    for raw in (b"not xml", b"<foo/>"):
        resp = await env.client.post("/api/v1/reports/import/znuny/file",
                                     json={"content_base64": base64.b64encode(raw).decode()})
        assert resp.status_code == 422 and "Znuny-Statistik" in resp.json()["detail"]["message"]

async def test_import_never_writes_znuny_tables(env) -> None:
    before = await env.count_rows(("xml_storage", "search_profile"))
    env.user = user(52040)
    await env.client.post("/api/v1/reports/import/znuny/apply", json={"stat_ids": [52080, 52081]})
    assert await env.count_rows(("xml_storage", "search_profile")) == before

async def test_route_order_and_scope(env) -> None:
    # "/reports/import/znuny" must not be swallowed by "/reports/{report_key}"
    env.user = user(52040)
    assert (await env.client.get("/api/v1/reports/import/znuny")).status_code == 200
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D7. File body: base64 decode with `validate=True` (error → 422 "Die Datei konnte nicht gelesen werden."), size check before parsing. `XmlHashError` → 422 `{"message": exc.message, "path": "content_base64"}`. Apply: build previews first (same code path), then store; saved-search `name` (stat title) and sort via one UPDATE on the row `upsert_imported` returned (same session). Seed stat 52081 with `Title` "rep52 Ticketliste". Use `session.commit()` once; never `session.begin()`.
- [ ] **Step 4: Golden test** `tests/golden/test_stats_import.py`: with a running peer (`GOLDEN_PEER=znuny-6.5 just golden-up && just golden-seed`), import the fixture XML files through the peer's `Kernel::System::Stats->Import` (perl one-liner via `docker exec`, pattern of the other golden tests), then call Tiqora's `load_stats` + `map_stat` on the shared DB and assert the same targets/dimensions as the pure tests (ids now from `xml_storage`). Marked like the other golden tests (manual). Run it once if the peer is available; otherwise note "golden not run" in the commit body.
- [ ] **Step 5: Run → PASS**; regenerate openapi (both files) + `pnpm build`; `cd frontend && pnpm lint` (no new required fields expected); backend lint gates.
- [ ] **Step 6: Commit** `feat(reporting): import Znuny statistics as saved reports and searches`.

---

### Task 6: Server texts and formatting for unattended runs (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/texts.py`, `backend/src/tiqora/reporting/texts_data/__init__.py`, `texts_data/reports.de.json`, `texts_data/reports.en.json` (generated by `--sync`)
- Test: `backend/tests/test_report_texts.py`

**Interfaces:**
- Consumes: `ReportResultOut`, `ColumnType`, `Cell`, `OTHER_KEY`.
- Produces: D9 texts part (`TextLocale`, `text_locale`, `ReportTexts`, `load_texts`, `FIXED_TEXTS`, `format_number`, `format_measure`, `format_date_bucket`, `ExportLabels`, `server_export_labels`).

- [ ] **Step 1: Write the failing tests**

```python
REPO = Path(__file__).resolve().parents[2]

def test_bundle_in_sync_with_frontend() -> None:
    for loc in ("de", "en"):
        frontend = json.loads((REPO / "frontend/src/i18n/locales" / f"{loc}.json").read_text("utf-8"))
        bundled = json.loads(resources.files("tiqora.reporting.texts_data").joinpath(f"reports.{loc}.json")
                             .read_text("utf-8"))
        assert bundled == {"reports": frontend["reports"]}, "run: uv run python -m tiqora.reporting.texts --sync"

def test_text_locale() -> None:
    assert [text_locale(x) for x in ("de", "de_DE", "de-AT", "en", "fr", None, "")] == [
        "de", "de", "de", "en", "en", "en", "en"]

def test_lookup_params_and_fallback() -> None:
    de = load_texts("de")
    assert de.t("reports.totals") == "Summe"
    assert de.t("reports.hourLabel", hour="07") == "07 Uhr"
    assert de.t("reports.nope.missing", default="x") == "x"
    assert de.t("reports.hourLabel", hour="{{hour}}") .count("{{hour}}") == 1      # no recursion

@pytest.mark.parametrize(("value", "col_type", "de", "en"), [
    (1234.5, "number", "1.234,5", "1,234.5"),
    (0.1234, "percent", "12,3 %", "12.3 %"),
    (45, "minutes", "45 min", "45 min"),
    (59.96, "minutes", "1 h", "1 h"),
    (1439.9, "minutes", "1 T", "1 d"),
    (2.5, "units", "2,50", "2.50"),
    (1234, "count", "1.234", "1,234"),
    (None, "count", "—", "—"),
])
def test_format_measure_matches_frontend(value, col_type, de, en) -> None:
    assert format_measure(value, col_type, "de") == de
    assert format_measure(value, col_type, "en") == en

def test_date_buckets() -> None:
    de, en = load_texts("de"), load_texts("en")
    assert format_date_bucket("2026-10-05", "week", de) == "KW 41"            # bundle "reports.weekLabel"
    assert format_date_bucket("2026-10-05", "week", en) == "Wk 41"
    assert format_date_bucket("2026-10-01", "month", de) == "Oktober 2026"
    assert format_date_bucket("2026-10-01", "month", en) == "October 2026"
    assert format_date_bucket("2026-10-01", "quarter", de) == "Q4 2026"
    assert format_date_bucket("2026-10-11", "day", de) == "11.10.2026"
    assert format_date_bucket("2026-10-11", "day", en) == "10/11/2026"
    assert format_date_bucket("garbage", "day", de) == "garbage"

def test_server_export_labels() -> None:
    result = make_result(   # helper building a ReportResultOut: dims state_type (i18n_values), queue (labels), measure tickets
        columns=[col("state_type", "dimension", "enum", i18n_values="reports.values.state_type"),
                 col("queue", "dimension", "id"), col("tickets", "measure", "count")],
        rows=[["open", 4, 3], ["__other__", 5, 1]], labels={"queue": {"4": "rep52 Queue"}})
    labels = server_export_labels(result, load_texts("de"))
    assert labels.column_labels["tickets"] == load_texts("de").t("reports.measures.tickets")
    assert labels.value_labels["state_type"]["open"] == load_texts("de").t("reports.values.state_type.open")
    assert labels.value_labels["state_type"]["__other__"] == "Weitere"
    assert labels.value_labels["queue"]["4"] == "rep52 Queue"
    assert labels.totals_label == "Summe"
```

(Expected strings for `format_measure` — copy them from `frontend/src/components/reports/reportFormat.test.ts` where the same inputs exist; the table above must match what the frontend really prints. Fix the expectation, not the frontend, if they differ. `reports.other` must be "Weitere" in de.json — check.)

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D9 texts part. `--sync` entry point: `if __name__ == "__main__":` with `argparse` (`--sync`, `--check`), writes JSON with `ensure_ascii=False, indent=2, sort_keys=True` + trailing newline. Number formatting by hand (`f"{value:,.{d}f}"` then swap separators for de; minimum/maximum fraction digits like `Intl.NumberFormat`: trim trailing zeros down to the minimum). Generate the two JSON files and commit them.
- [ ] **Step 4: Run → PASS**; build the wheel once and check the JSON is inside: `cd backend && uv build --wheel -o /tmp/claude-wheel && unzip -l /tmp/claude-wheel/*.whl | grep texts_data` (use the scratchpad directory instead of /tmp if available); lint gates.
- [ ] **Step 5: Commit** `feat(reporting): server-side report texts and number formats for unattended runs`.

---

### Task 7: Server-side SVG charts (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/svg.py`
- Test: `backend/tests/test_report_svg.py`

**Interfaces:**
- Consumes: Task 6 `ExportLabels`, `ReportTexts`, `format_measure`, `format_date_bucket`; `Visualization`, `VisualizationType`.
- Produces: D9 svg part (`SERIES_COLORS`, `MUTED_COLOR`, `MAX_CATEGORIES`, `ChartSeries`, `ChartData`, `chart_data`, `render_chart`).

- [ ] **Step 1: Read** `frontend/src/components/agent/stats/BarChart.tsx`, `StackedBarChart.tsx`, `LineChart.tsx`, `DonutChart.tsx`, `HeatmapChart.tsx`, `KpiTile.tsx`, `chartColors.ts` and `reportFormat.ts` `toSeries` — the server charts copy their geometry choices (axis at zero, gridlines, label rotation rules, donut hole ratio) in simplified form; no interactivity.
- [ ] **Step 2: Write the failing tests**

```python
REPO = Path(__file__).resolve().parents[2]

def test_series_colors_match_tokens() -> None:
    css = (REPO / "frontend/src/themes/tokens.css").read_text("utf-8")
    light = css.split('[data-theme="dark"]')[0]
    for i, color in enumerate(SERIES_COLORS, start=1):
        assert f"--color-series-{i}: {color};" in light
    assert f"--color-muted: {MUTED_COLOR};" in light

def _data(n_series: int = 2, n_cat: int = 3) -> ChartData:
    return ChartData(categories=tuple(f"K{i}" for i in range(n_cat)),
                     series=tuple(ChartSeries(key=str(s), label=f"S{s}", values=tuple(float(s + c) for c in range(n_cat)))
                                  for s in range(n_series)),
                     value_format=lambda v: f"{v:.0f}")

@pytest.mark.parametrize("kind", ["bar", "stacked_bar", "line", "area", "donut", "heatmap", "kpi"])
def test_every_kind_renders_valid_svg(kind) -> None:
    svg = render_chart(kind, _data())
    root = ET.fromstring(svg)                       # well-formed
    assert root.tag.endswith("svg") and root.get("viewBox")
    assert "href" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")

def test_table_and_pivot_have_no_chart() -> None:
    assert render_chart("table", _data()) is None and render_chart("pivot", _data()) is None

def test_labels_are_escaped() -> None:
    data = ChartData(categories=("<script>alert(1)</script>",), series=(ChartSeries("a", "A & <b>", (1.0,)),),
                     value_format=str)
    svg = render_chart("bar", data)
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "A &amp; &lt;b&gt;" in svg

def test_more_than_six_series_fold_to_muted() -> None:
    svg = render_chart("line", _data(n_series=8))
    assert svg.count(MUTED_COLOR) >= 2 and all(c in svg for c in SERIES_COLORS)

def test_other_series_is_muted_not_a_slot() -> None:
    data = ChartData(categories=("a",), series=(ChartSeries("__other__", "Weitere", (2.0,)), ChartSeries("1", "Q1", (1.0,))),
                     value_format=str)
    svg = render_chart("bar", data)
    assert MUTED_COLOR in svg and SERIES_COLORS[0] in svg

def test_too_many_categories_note() -> None:
    svg = render_chart("bar", _data(n_cat=MAX_CATEGORIES + 5))
    assert "+5" in svg

def test_none_values_and_negative_values() -> None:
    data = ChartData(categories=("a", "b"), series=(ChartSeries("1", "Q", (None, -3.0)),), value_format=str)
    for kind in ("bar", "line", "area"):
        ET.fromstring(render_chart(kind, data))

def test_chart_data_from_result() -> None:
    # 2 dims (created by month, queue) + measure; viz line with series_dimension=1 ->
    # categories = formatted months, series = queues with labels
    ...
```

(Write `test_chart_data_from_result` fully with a `ReportResultOut` built like Task 6's helper.)

- [ ] **Step 3: Run → FAIL**.
- [ ] **Step 4: Implement** D9 svg part. All numbers formatted through `data.value_format`; coordinates rounded to 1 decimal; one `<title>` per mark is not needed (static PDF). Heatmap: categories × series grid with a sequential ramp of `SERIES_COLORS[0]` (opacity steps), KPI: big number per series (first value) with label.
- [ ] **Step 5: Run → PASS**; lint gates.
- [ ] **Step 6: Commit** `feat(reporting): server-side SVG charts for PDF reports`.

---

### Task 8: PDF writer, WeasyPrint dependency, "pdf" export format (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/pdf.py`
- Modify: `backend/pyproject.toml` (dependency `weasyprint>=63`; mypy override `ignore_missing_imports` next to the `openpyxl` entry if WeasyPrint ships no types), root `uv.lock` (`uv lock`), `Dockerfile` (runtime stage `apt-get install` block: add `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1 fonts-dejavu-core`), `.github/workflows/ci.yml` (job "Backend tests", step "Install crypto tooling …": add `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 fonts-dejavu-core`; env `TIQORA_REQUIRE_PDF: "1"` on both Pytest steps; lint job unchanged unless mypy needs the package — it is installed by `uv sync` there), `backend/src/tiqora/reporting/schemas.py` (`ReportExportIn.format` + `locale`), `backend/src/tiqora/reporting/jobs.py` (PDF branch), `packages/api-client/openapi.json`, `docs/api/openapi.json`, `schema.d.ts`
- Test: `backend/tests/test_report_pdf.py` (pure), `backend/tests/test_reports_export_pdf.py` (job, both dialects, band 52100–52119)

**Interfaces:**
- Consumes: Tasks 6–7; `run_export_job`, `ReportExportIn`, `report_sheet` labels.
- Produces: D9 pdf part (`ReportSection`, `PdfMeta`, `render_report_html`, `write_report_pdf`, `PdfUnavailable`), `ReportExportIn.format == "pdf"`, `ReportExportIn.locale`, `PDF_CONTENT_TYPE = "application/pdf"` in `reporting/jobs.py`.

- [ ] **Step 1: Add the dependency** (`uv add --project backend "weasyprint>=63"` or edit + `uv lock` at the repo root per the workspace layout; keep other pins unchanged — check `git diff uv.lock` touches only WeasyPrint and its new deps `pydyf`, `tinycss2`, `cssselect2`, `tinyhtml5`/`html5lib`, `pyphen`, `fonttools`, `Pillow` as already locked). Local macOS: `brew install pango` if the import fails.
- [ ] **Step 2: Write the failing tests**

```python
pdf_required = os.environ.get("TIQORA_REQUIRE_PDF") == "1"
try:
    import weasyprint  # noqa: F401
    HAVE_PDF = True
except (ImportError, OSError):
    HAVE_PDF = False
needs_pdf = pytest.mark.skipif(not HAVE_PDF and not pdf_required, reason="WeasyPrint native libs missing")

def _section(rows=None, *, title="rep52 Volumen", truncated=False, viz="bar") -> ReportSection:
    result = make_result(columns=[col("queue", "dimension", "id"), col("tickets", "measure", "count")],
                         rows=rows if rows is not None else [[4, 12], [5, 3]],
                         labels={"queue": {"4": "rep52 Queue A", "5": "rep52 Queue B"}}, truncated=truncated)
    texts = load_texts("de")
    return ReportSection(title=title, result=result, visualization=Visualization(type=viz),
                         labels=server_export_labels(result, texts), info_lines=("Zeitraum: 01.09.2026 – 30.09.2026",),
                         row_limit=1000)

META = PdfMeta(title="rep52 Zeitplan", generated_at_local=datetime(2026, 10, 11, 7, 0), owner_line=None)

def test_html_escapes_everything() -> None:
    evil = '<img src="http://evil.test/x"><link href="file:///etc/passwd">{{owner}}'
    html_out = render_report_html([_section(title=evil)], META, load_texts("de"))
    assert "<img" not in html_out and "<link href=\"file" not in html_out and "&lt;img" in html_out
    assert "{{owner}}" in html_out               # literal, never substituted

def test_html_truncation_and_empty_notes() -> None:
    texts = load_texts("de")
    many = _section(rows=[[i, 1] for i in range(1500)])
    out = render_report_html([many], META, texts)
    assert "nur die ersten 1.000 Zeilen" in out
    assert texts.fixed("pdf.empty") in render_report_html([_section(rows=[])], META, texts)

@needs_pdf
def test_pdf_text_extraction() -> None:
    data = write_report_pdf([_section(), _section(title="rep52 Zweiter Bericht", viz="table")], META, load_texts("de"))
    assert data.startswith(b"%PDF")
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    assert "rep52 Volumen" in text and "rep52 Zweiter Bericht" in text and "rep52 Queue A" in text
    assert "Summe" in text or "12" in text

@needs_pdf
def test_pdf_escapes_and_fetches_nothing(monkeypatch) -> None:
    calls: list[str] = []
    real = pdf_module._refuse_fetch
    def spy(url, *a, **k):
        calls.append(url)
        return real(url, *a, **k)
    monkeypatch.setattr(pdf_module, "_refuse_fetch", spy)
    evil = '<img src="http://evil.test/x"><link rel="stylesheet" href="file:///etc/passwd">' + "W" * 5000
    data = write_report_pdf([_section(title=evil)], META, load_texts("de"))
    text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages)
    assert "<img" in text           # shown as literal text
    assert calls == []              # escaped markup never asked the fetcher for anything

def test_refuse_fetch_blocks_all() -> None:
    for url in ("http://evil.test/", "https://evil.test/", "file:///etc/passwd", "data:text/css,x"):
        with pytest.raises(ValueError):
            pdf_module._refuse_fetch(url)
```

Job test (`test_reports_export_pdf.py`, D16 skeleton, seeds one agent 52100 with `ro` on a queue 52110 and two fact rows): `POST /api/v1/reports/export` with `{"report_key": "builtin.open_now", "format": "pdf", "title": "rep52 Offen", "locale": "de", "column_labels": {...}}` → 202; run the job with the runner helper the wave-1 export tests use (`run_job_now` or equivalent — grep `run_export_job` in `backend/tests`); the job file is `rep52-offen.pdf`, `application/pdf`, starts with `%PDF` (`@needs_pdf`); with `weasyprint` import patched to fail → job `failed` with the German "PDF-Erstellung ist auf diesem Server nicht verfügbar" message; `format: "docx"` → 422.

- [ ] **Step 3: Run → FAIL**.
- [ ] **Step 4: Implement** D9 pdf part and the export branch. Import WeasyPrint lazily inside `write_report_pdf` (`try: from weasyprint import HTML except (ImportError, OSError) as exc: raise PdfUnavailable(...) from exc`) so the API process never needs the libs at import time. `url_fetcher=_refuse_fetch` and `base_url=None`; CSS inline in `<style>`; charts inline `<svg>` (WeasyPrint renders inline SVG without fetching). `@page { size: A4; margin: 15mm; @bottom-right { content: "<page text>" } }` with the German/English page text built from `FIXED_TEXTS` (CSS-escaped). `run_export_job`: `row_cap = reporting.pdf_row_limit if body.format == "pdf" else reporting.export_row_limit`; labels from the body (`column_labels`/`value_labels`/`totals_label`) wrapped in `ExportLabels`; `texts = load_texts(body.locale)`.
- [ ] **Step 5: Run → PASS** both modules plus the wave-1 export tests (`grep -l run_export_job backend/tests`); regenerate openapi (both) + `pnpm build`; `cd frontend && pnpm lint`. Build the runtime image locally once and smoke-test: `docker build --target runtime -t tiqora-pdf-check . && docker run --rm tiqora-pdf-check python -c "import weasyprint; weasyprint.HTML(string='<p>Grüße</p>').write_pdf()"` (skip with a note in the commit body when Docker is unavailable). Record the image size delta in the commit body.
- [ ] **Step 6: Commit** `feat(reporting): PDF reports with server-side charts (WeasyPrint)`.

---

### Task 9: Recurrence calculator (backend, pure)

**Files:**
- Create: `backend/src/tiqora/reporting/recurrence.py`
- Test: `backend/tests/test_report_recurrence.py`

**Interfaces:**
- Consumes: Task 6 `ReportTexts` (for `describe`).
- Produces: D10 (`Recurrence`, `MAX_SEARCH_DAYS`, `next_occurrence`, `upcoming`, `describe`).

- [ ] **Step 1: Write the failing tests**

```python
BER = "Europe/Berlin"
def utc(*a: int) -> datetime:
    return datetime(*a)          # naive UTC

def test_daily_basic_and_strictly_after() -> None:
    r = Recurrence(kind="daily", time="07:00")
    assert next_occurrence(r, BER, utc(2026, 10, 11, 4, 59)) == utc(2026, 10, 11, 5, 0)     # CEST +2
    assert next_occurrence(r, BER, utc(2026, 10, 11, 5, 0)) == utc(2026, 10, 12, 5, 0)      # strictly after

def test_workdays_only() -> None:
    r = Recurrence(kind="daily", time="07:00", workdays_only=True)
    assert next_occurrence(r, BER, utc(2026, 10, 9, 6, 0)) == utc(2026, 10, 12, 5, 0)       # Fri after 07:00 -> Mon

def test_spring_forward_gap_shifts_forward() -> None:
    r = Recurrence(kind="daily", time="02:30")
    # 2027-03-28: 02:00 CET -> 03:00 CEST; 02:30 does not exist -> 03:30 CEST = 01:30 UTC
    assert next_occurrence(r, BER, utc(2027, 3, 27, 12, 0)) == utc(2027, 3, 28, 1, 30)

def test_fall_back_runs_once() -> None:
    r = Recurrence(kind="daily", time="02:30")
    # 2026-10-25: 03:00 CEST -> 02:00 CET; 02:30 happens twice -> first one (00:30 UTC), next day 01:30 UTC
    first = next_occurrence(r, BER, utc(2026, 10, 24, 12, 0))
    assert first == utc(2026, 10, 25, 0, 30)
    assert next_occurrence(r, BER, first) == utc(2026, 10, 26, 1, 30)

def test_weekly_weekdays() -> None:
    r = Recurrence(kind="weekly", time="08:15", weekdays=[1, 5])       # Mon, Fri
    assert upcoming(r, BER, utc(2026, 10, 11, 0, 0), 3) == [
        utc(2026, 10, 12, 6, 15), utc(2026, 10, 16, 6, 15), utc(2026, 10, 19, 6, 15)]

def test_monthly_31_clamps_to_last_day() -> None:
    r = Recurrence(kind="monthly", time="06:00", day_of_month=31)
    assert upcoming(r, BER, utc(2027, 1, 31, 12, 0), 3) == [
        utc(2027, 2, 28, 5, 0), utc(2027, 3, 31, 4, 0), utc(2027, 4, 30, 4, 0)]

def test_yearly_feb_29() -> None:
    r = Recurrence(kind="yearly", time="06:00", month=2, day_of_month=29)
    assert next_occurrence(r, BER, utc(2027, 1, 1)) == utc(2027, 2, 28, 5, 0)
    assert next_occurrence(r, BER, utc(2027, 3, 1)) == utc(2028, 2, 29, 5, 0)

def test_other_zones() -> None:
    r = Recurrence(kind="daily", time="00:00")
    assert next_occurrence(r, "America/New_York", utc(2026, 10, 11, 3, 0)) == utc(2026, 10, 11, 4, 0)
    assert next_occurrence(r, "UTC", utc(2026, 10, 11, 0, 0)) == utc(2026, 10, 12, 0, 0)

@pytest.mark.parametrize("bad", [
    {"kind": "weekly", "time": "07:00"},                                   # no weekday
    {"kind": "weekly", "time": "07:00", "weekdays": [0]},
    {"kind": "weekly", "time": "07:00", "weekdays": [1, 1]},
    {"kind": "daily", "time": "24:00"},
    {"kind": "daily", "time": "7:00"},
    {"kind": "monthly", "time": "07:00"},
    {"kind": "daily", "time": "07:00", "day_of_month": 3},
    {"kind": "yearly", "time": "07:00", "day_of_month": 3},
])
def test_validation_german(bad) -> None:
    with pytest.raises(ValidationError) as exc:
        Recurrence.model_validate(bad)
    assert exc.value.errors()[0]["msg"]

def test_unknown_zone_raises() -> None:
    with pytest.raises(ValueError):
        next_occurrence(Recurrence(kind="daily", time="07:00"), "Mars/Base", utc(2026, 1, 1))

def test_describe() -> None:
    assert describe(Recurrence(kind="weekly", time="07:00", weekdays=[1, 5]), load_texts("de")) == \
        "Jeden Montag und Freitag um 07:00"
    assert describe(Recurrence(kind="monthly", time="06:00", day_of_month=31), load_texts("de")) == \
        "Monatlich am 31. um 06:00 (in kürzeren Monaten am letzten Tag)"
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D10 (iterate local dates from the local date of `after_utc` forward; candidate → UTC; return the first `> after_utc`). Add `describe` texts to `FIXED_TEXTS` (de + en): "Täglich um {time}", "Montag bis Freitag um {time}", "Jeden {days} um {time}", "Monatlich am {day}. um {time}", the clamp note, "Jährlich am {day}. {month} um {time}"; weekday names `weekday.1..7`.
- [ ] **Step 4: Run → PASS**; lint gates.
- [ ] **Step 5: Commit** `feat(reporting): DST-safe recurrence for report schedules`.

---

### Task 10: Schedule store and API (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/schedules.py`, `schedule_schemas.py`, `backend/src/tiqora/api/v1/report_schedules.py`
- Modify: `backend/src/tiqora/api/v1/__init__.py`, `backend/src/tiqora/domain/api_key_scopes.py` (+ its test file: add the two prefixes to the expected table), `backend/src/tiqora/domain/schemas.py` (`UserMe.report_external_recipients: bool = False`), `backend/src/tiqora/api/v1/auth.py` (fill it from `ReportingSettings` with the same guard as `can_view_reports`), openapi files, `schema.d.ts`, frontend `UserMe` fixtures `pnpm lint` reports
- Test: `backend/tests/test_report_schedules_api.py` (both dialects, band 52120–52199)

**Interfaces:**
- Consumes: Task 1 tables; Task 9 `Recurrence`, `next_occurrence`, `upcoming`, `describe`; wave-2 `report_rights`, `resolve_report_ref`, `check_share_groups`, `ReportOwnerOut` + the owner-name resolver used by `ReportOut` (`resolve_labels(session, "user", …)`), `ParameterValue`.
- Produces: D8, D11 (all names), routes; `load_schedule`, `resolve_recipients`, `ResolvedRecipients` for Tasks 11–12.

Seeds: agents 52120 `rep52.owner` (create only: `reporting.access = all_agents`, no `stats` rw), 52121 `rep52.sharer` (`rw` on `stats`, `ro` on group 52150), 52122 `rep52.colleague` (`ro` on 52150, `UserEmail` set), 52123 `rep52.noemail` (`ro` on 52150, no `UserEmail`), 52124 `rep52.invalid` (valid_id 2); group 52150; group 52151 `rep52-group-52151` (no band agent is a member); role 52160 with member 52122; a saved report of 52121 (private) created through the API in the test.

- [ ] **Step 1: Write the failing tests**

```python
BASE = {"name": "rep52 Wochenbericht", "items": [{"report_key": "builtin.volume"}], "format": "pdf",
        "recurrence": {"kind": "weekly", "time": "07:00", "weekdays": [1]}, "time_zone": "Europe/Berlin",
        "recipients": {"user_ids": []}}

def body(**kw): return {**BASE, **kw}

async def test_create_self_only_without_share_right(env) -> None:
    env.user = user(52120)
    resp = await env.client.post("/api/v1/report-schedules", json=body(recipients={"user_ids": [52120]}))
    assert resp.status_code == 201
    out = resp.json()
    assert out["next_run_at"] is not None and out["recurrence_text"].startswith("Jeden Montag")
    assert out["recipients"][0]["label"] and out["recipients"][0]["problem"] is None
    resp = await env.client.post("/api/v1/report-schedules", json=body(recipients={"user_ids": [52122]}))
    assert resp.status_code == 403 and "Berechtigung" in resp.json()["detail"]["message"]

async def test_groups_roles_emails_rules(env, reporting_setting) -> None:
    env.user = user(52121)
    ok = await env.client.post("/api/v1/report-schedules",
                               json=body(recipients={"group_ids": [52150], "role_ids": [52160]}))
    assert ok.status_code == 201
    bad_group = await env.client.post("/api/v1/report-schedules", json=body(recipients={"group_ids": [52151]}))
    assert bad_group.status_code == 422                     # the owner is not a member of 52151 -> not a share group
    ext = await env.client.post("/api/v1/report-schedules", json=body(recipients={"emails": ["boss@example.org"]}))
    assert ext.status_code == 422 and ext.json()["detail"]["path"] == "recipients.emails"
    await reporting_setting(schedule_external_recipients=True)
    ext2 = await env.client.post("/api/v1/report-schedules",
                                 json=body(recipients={"emails": [" Boss@Example.org ", "boss@example.org"]}))
    assert [r["email"] for r in ext2.json()["recipients"]] == ["boss@example.org"]
    for bad in ("no-at", "a@b@c", "x <y@example.org>", "a@example.org, b@example.org"):
        r = await env.client.post("/api/v1/report-schedules", json=body(recipients={"emails": [bad]}))
        assert r.status_code == 422

async def test_items_must_resolve_for_owner(env) -> None:
    env.user = user(52121)
    saved = (await env.client.post("/api/v1/reports", json={"name": "rep52 privat", "definition": TICKETS_DEF})).json()
    env.user = user(52120)
    resp = await env.client.post("/api/v1/report-schedules",
                                 json=body(items=[{"report_key": saved["key"]}], recipients={"user_ids": [52120]}))
    assert resp.status_code == 422 and resp.json()["detail"]["path"] == "items.0.report_key"
    resp = await env.client.post("/api/v1/report-schedules",
                                 json=body(items=[{"report_key": "builtin.volume",
                                                   "overrides": {"parameters": {"nope": [1]}}}],
                                           recipients={"user_ids": [52120]}))
    assert resp.status_code == 422 and resp.json()["detail"]["path"].startswith("items.0.overrides")

async def test_visibility_owner_admin_only(env) -> None:
    env.user = user(52120)
    sid = (await env.client.post("/api/v1/report-schedules", json=body(recipients={"user_ids": [52120]}))).json()["id"]
    env.user = user(52121)
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}")).status_code == 404
    assert sid not in [s["id"] for s in (await env.client.get("/api/v1/report-schedules")).json()]
    env.user = user(1)
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}")).status_code == 200
    assert sid in [s["id"] for s in (await env.client.get("/api/v1/report-schedules?all=true")).json()]

async def test_update_version_conflict_and_next_run(env) -> None:
    env.user = user(52120)
    s = (await env.client.post("/api/v1/report-schedules", json=body(recipients={"user_ids": [52120]}))).json()
    upd = body(recipients={"user_ids": [52120]}, recurrence={"kind": "daily", "time": "06:00"}, version=1)
    s2 = (await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)).json()
    assert s2["version"] == 2 and s2["next_run_at"] != s["next_run_at"]
    stale = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2
    paused = (await env.client.put(f"/api/v1/report-schedules/{s['id']}",
                                   json={**upd, "active": False, "version": 2})).json()
    assert paused["active"] is False and paused["next_run_at"] is None

async def test_recipient_problems_are_shown(env) -> None:
    env.user = user(52121)
    s = (await env.client.post("/api/v1/report-schedules",
                               json=body(recipients={"user_ids": [52123, 52124]}))).json()
    problems = {r["id"]: r["problem"] for r in s["recipients"]}
    assert "E-Mail" in problems[52123] and problems[52124]

async def test_preview_recurrence(env) -> None:
    env.user = user(52120)
    out = (await env.client.post("/api/v1/report-schedules/preview-recurrence",
                                 json={"recurrence": {"kind": "daily", "time": "07:00"}, "time_zone": "Europe/Berlin"})).json()
    assert len(out["next_runs"]) == 5 and out["text"] == "Täglich um 07:00"
    bad = await env.client.post("/api/v1/report-schedules/preview-recurrence",
                                json={"recurrence": {"kind": "daily", "time": "07:00"}, "time_zone": "Mars/Base"})
    assert bad.status_code == 422

async def test_limit_and_delete(env) -> None: ...   # 50 schedules -> 51st 422 path "name"; DELETE -> 204, GET -> 404

async def test_me_flag_and_api_key_scope(env, reporting_setting) -> None:
    env.user = user(52120)
    assert (await env.client.get("/api/v1/auth/me")).json()["report_external_recipients"] is False
    assert area_for_path("/api/v1/report-schedules/5") == "stats"      # the function api_key_scopes exposes
    assert area_for_path("/api/v1/report-runs/5/files/1") == "stats"
```

(Use the real helper name of `api_key_scopes` for `area_for_path`; write `test_limit_and_delete` fully.)

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D8/D11. `time_zone` None → `resolve_user_time_zone(session, SysConfig(session), owner)`; invalid zone → 422 path `time_zone`. `next_run_at = next_occurrence(rec, tz, utc_now())` when `active`, else None. Recipient rows written as one row per entry (`kind`, `ref_id` / `email`). `ScheduleItemOut.available/problem` via `resolve_report_ref` for the owner (never the viewer — an admin sees what the owner can run). The `/run`, `/runs` and run-file routes are NOT part of this task; Task 11 adds them together with the delivery job and their tests.
- [ ] **Step 4: Run → PASS**; regenerate openapi + `pnpm build`; `cd frontend && pnpm lint && pnpm test --run` (fixture fields for `UserMe.report_external_recipients`); backend lint gates; rerun `tests/test_reports_api.py` and the api-key-scope tests.
- [ ] **Step 5: Commit** `feat(reporting): report schedules with recipients and DST-safe next run`.

---

### Task 11: Delivery job, "Jetzt senden", run history and file download (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/delivery.py`
- Modify: the module that registers core job handlers (grep where `REPORT_EXPORT_JOB` is added to `JOB_HANDLERS`) — register `REPORT_DELIVERY_JOB`; `backend/src/tiqora/reporting/jobs.py` (make `_until_cancelled` importable as `until_cancelled`, keep the old name as alias), `backend/src/tiqora/worker/notification_templates.py` (public helper for "usable sender address" if it is private today), `backend/src/tiqora/api/v1/report_schedules.py` (`POST /{id}/run`, `GET /{id}/runs`, `GET /report-runs/{run_id}/files/{file_id}`), openapi files, `schema.d.ts`
- Test: `backend/tests/test_report_delivery.py` (both dialects, band 52200–52259)

**Interfaces:**
- Consumes: Tasks 6–10; `prepare_ref`, `RunOverrides`, `run_report`, `report_sheet`, `write_xlsx_workbook`, `write_report_csv`, `export_slug`, `build_outbound_sender`, `build_message`, `is_permanent_smtp_failure`, `write_mail_log`, `bulk_get_preferences`, `CapturingMailSender`/`FailingMailSender` (tests).
- Produces: D12 (`REPORT_DELIVERY_KIND`, `REPORT_DELIVERY_JOB`, `ReportDeliveryPayload`, `run_delivery_job`, `on_delivery_interrupted`, `apply_placeholders`, `resolve_report_sender`, `CHECKPOINT_SENDING`, `MAX_ATTEMPTS`), `create_run(session, schedule, *, run_kind, owner_only, due_at, now) -> TiqoraReportRun` and `enqueue_delivery(session, run, *, dedupe_key) -> TiqoraJob` in `delivery.py` (used by the route and Task 12).

Seeds: owner 52200 `rep52.owner` (ro on queue 52230 via group 52220, `UserEmail` `rep52.owner@example.org`, `rw` on `stats`), recipient 52201 (`UserEmail` `rep52.colleague@example.org`), recipient 52202 (no e-mail), queue 52230 + 52231 (52231 not visible to 52200), fact rows for tickets in both queues (as the wave-1 report tests seed `tiqora_report_ticket_fact`), sender setting `notification.sender_email = reports@example.org`. Mail sender patched: `monkeypatch.setattr(delivery, "build_outbound_sender", fake)` returning a `CapturingMailSender`.

- [ ] **Step 1: Write the failing tests**

```python
async def _schedule(env, **kw) -> int:
    env.user = user(52200)
    payload = {**BASE, "items": [{"report_key": "builtin.open_now"}],
               "recipients": {"user_ids": [52200, 52201, 52202]}, **kw}
    return (await env.client.post("/api/v1/report-schedules", json=payload)).json()["id"]

async def test_run_now_owner_only_sends_pdf(env, mail) -> None:
    sid = await _schedule(env)
    acc = (await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})).json()
    await env.run_job(acc["job"]["job_id"])
    msg = mail.sent[-1]
    assert msg["Bcc"] == "rep52.owner@example.org" and msg["To"] == "reports@example.org"
    att = [p for p in msg.iter_attachments()]
    assert len(att) == 1 and att[0].get_content_type() == "application/pdf"     # needs_pdf
    runs = (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).json()
    assert runs[0]["status"] == "succeeded" and runs[0]["recipient_count"] == 1 and runs[0]["files"]
    file = runs[0]["files"][0]
    dl = await env.client.get(f"/api/v1/report-runs/{runs[0]['id']}/files/{file['id']}")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")

async def test_all_recipients_partial_without_addresses_in_history(env, mail) -> None:
    sid = await _schedule(env, format="xlsx")
    acc = (await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": False})).json()
    await env.run_job(acc["job"]["job_id"])
    msg = mail.sent[-1]
    assert set(msg["Bcc"].split(", ")) == {"rep52.owner@example.org", "rep52.colleague@example.org"}
    run = (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).json()[0]
    assert run["status"] == "partial" and "keine E-Mail-Adresse" in run["detail"]
    assert "@" not in (run["detail"] or "") and "@" not in (run["error"] or "")

async def test_run_uses_owner_scope_at_run_time(env, mail) -> None:
    sid = await _schedule(env, format="csv")
    await env.grant_queue(52200, 52231)                   # owner gains queue 52231 AFTER saving
    acc = (await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})).json()
    await env.run_job(acc["job"]["job_id"])
    csv_text = mail.sent[-1].get_payload()[-1].get_content()   # adapt to the message structure
    assert "rep52-queue-52231" in csv_text
    await env.revoke_queue(52200, 52231)
    acc = (await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})).json()
    await env.run_job(acc["job"]["job_id"])
    assert "rep52-queue-52231" not in mail.sent[-1].get_payload()[-1].get_content()

async def test_owner_without_access_fails_without_mail(env, mail, reporting_setting) -> None:
    sid = await _schedule(env)
    await reporting_setting(access="stats_group")
    await env.revoke_stats(52200)
    acc = (await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": False})).json()
    sent_before = len(mail.sent)
    await env.run_job(acc["job"]["job_id"], expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "keinen Zugriff" in run.error
    assert all("rep52.colleague" not in (m["Bcc"] or "") for m in mail.sent[sent_before:])

async def test_deleted_report_fails_run_and_mails_owner(env, mail) -> None:
    env.user = user(52200)
    saved = (await env.client.post("/api/v1/reports", json={"name": "rep52 r", "definition": TICKETS_DEF})).json()
    sid = await _schedule(env, items=[{"report_key": "builtin.open_now"}, {"report_key": saved["key"]}])
    await env.client.delete(f"/api/v1/reports/{saved['id']}")
    acc = (await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": False})).json()
    await env.run_job(acc["job"]["job_id"], expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "nicht mehr verfügbar" in run.error
    last = mail.sent[-1]
    assert last["To"] == "rep52.owner@example.org" and not list(last.iter_attachments())

async def test_only_if_nonempty_skips(env, mail) -> None: ...      # report over an empty queue -> status skipped, no mail

async def test_temporary_smtp_error_retries_without_rerender(env, monkeypatch) -> None:
    sid = await _schedule(env, format="csv")
    sends: list[EmailMessage] = []
    class FlakySender:
        async def send(self, message, *, envelope_from=None):
            sends.append(message)
            if len(sends) == 1:
                raise aiosmtplib.SMTPResponseException(451, "4.3.0 try again")
    async def fake_build(*a, **k):
        return FlakySender()
    monkeypatch.setattr(delivery, "build_outbound_sender", fake_build)
    renders: list[int] = []
    real_csv = delivery.write_report_csv
    monkeypatch.setattr(delivery, "write_report_csv", lambda *a, **k: renders.append(1) or real_csv(*a, **k))
    async with env.factory() as s:
        sched = await load_schedule(s, sid)
        run = await create_run(s, sched, run_kind="schedule", owner_only=True, due_at=None, now=utc_now())
        job = await enqueue_delivery(s, run, dedupe_key=f"rep52-retry-{run.id}")
        await s.commit()
    await env.run_job(job.id, expect="queued")            # JobRetry -> back in the queue with due_at
    run_row = (await env.load_runs(sid))[0]
    assert run_row.status == "running" and len(await env.load_run_files(run_row.id)) == 1
    await env.run_job(job.id, ignore_due=True)             # second claim (attempt 2)
    run_row = (await env.load_runs(sid))[0]
    assert run_row.status == "succeeded" and len(sends) == 2
    assert len(renders) == 1                               # files reused, not rendered again
    assert len(await env.load_run_files(run_row.id)) == 1

async def test_permanent_smtp_error_fails_counts_and_pauses(env, monkeypatch, mail) -> None:
    sid = await _schedule(env, format="csv")
    owner_mails: list[EmailMessage] = []
    class Rejecting:
        async def send(self, message, *, envelope_from=None):
            if message["Bcc"]:
                raise aiosmtplib.SMTPResponseException(550, "5.1.1 rejected")
            owner_mails.append(message)                    # the failure mail to the owner (no Bcc)
    async def fake_build(*a, **k):
        return Rejecting()
    monkeypatch.setattr(delivery, "build_outbound_sender", fake_build)
    for attempt in range(1, FAILURES_BEFORE_PAUSE + 1):
        async with env.factory() as s:
            sched = await load_schedule(s, sid)
            run = await create_run(s, sched, run_kind="schedule", owner_only=False, due_at=utc_now(), now=utc_now())
            job = await enqueue_delivery(s, run, dedupe_key=f"rep52-perm-{run.id}")
            await s.commit()
        await env.run_job(job.id, expect="failed")
        sched_row = await env.load_schedule(sid)
        assert sched_row.failure_count == attempt and sched_row.last_status == "failed"
    assert sched_row.active is False and sched_row.next_run_at is None
    assert sched_row.paused_reason.startswith("Nach 3 Fehlversuchen angehalten")
    assert len(owner_mails) == FAILURES_BEFORE_PAUSE and owner_mails[-1]["To"] == "rep52.owner@example.org"
    run_row = (await env.load_runs(sid))[0]
    assert "nicht versendet" in run_row.error and "@" not in run_row.error

async def test_attachment_too_large(env, reporting_setting, mail) -> None: ...   # delivery_max_attachment_mb=1 + big csv -> failed, file kept

async def test_manual_run_dedupe_409(env) -> None:
    sid = await _schedule(env)
    await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})
    second = await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})
    assert second.status_code == 409

async def test_file_download_owner_or_admin_only(env) -> None: ...   # colleague 52201 -> 404, admin -> 200

def test_subject_placeholders_are_not_recursive() -> None:
    out = apply_placeholders("{{schedule}} – {{period}} {{unknown}}",
                             {"schedule": "{{period}}", "period": "Sep 2026"})
    assert out == "{{period}} – Sep 2026 {{unknown}}"

async def test_interrupted_after_checkpoint_marks_failed(env) -> None:
    # call on_delivery_interrupted directly with a run in "running" -> status failed, German "möglicherweise"
    ...
```

(Write every `...` body fully; `env.run_job` = the runner helper wave-1 job tests use; `mail` fixture patches `delivery.build_outbound_sender` with a `CapturingMailSender`. Tests that inspect PDF bytes carry the `needs_pdf` marker of Task 8.)

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D12 and the three routes. Route `POST /report-schedules/{id}/run`: `get_schedule` (owner/admin), `owner_only=False` → the owner's share rule must still hold (re-run `check_recipients` against the stored recipients; 403 otherwise); `create_run(run_kind="manual")` + `enqueue_delivery(dedupe_key=f"report_schedule:{id}:manual")`; when `enqueue_job` returns an existing active job (dedupe) → rollback the new run row and answer 409 "Ein Versand läuft bereits." File download: `select` with `undefer(data)`; `Content-Disposition` built like the job-file route (grep it in `api/v1/jobs.py` and reuse its helper). Owner name for texts via the same resolver as `ReportOwnerOut`.
- [ ] **Step 4: Run → PASS**; regenerate openapi + `pnpm build`; `cd frontend && pnpm lint`; backend lint gates.
- [ ] **Step 5: Commit** `feat(reporting): deliver scheduled reports by mail with run history`.

---

### Task 12: Scheduler loop, run purge, daemon catalogue (backend)

**Files:**
- Create: `backend/src/tiqora/reporting/scheduler.py`, `backend/src/tiqora/worker/report_scheduler.py`
- Modify: `backend/src/tiqora/worker/__main__.py`, `backend/src/tiqora/worker/services.py`, `backend/src/tiqora/domain/settings_store.py` (`KEY_REPORT_SCHEDULER_ENABLED`, `KEY_REPORT_SCHEDULER_INTERVAL_SECONDS`, `KEY_REPORT_RUNS_PURGE_ENABLED` — follow the `KEY_REPORT_FACTS_*` naming), `backend/src/tiqora/config.py` (`report_scheduler_interval_seconds`), the daemon-catalog tests (grep `report_facts` in `backend/tests/test_*daemon*`), frontend locales (daemon service name/description for `report_scheduler`, `report_runs_purge` — copy the `report_facts` entry's place) + propagate
- Test: `backend/tests/test_report_scheduler.py` (both dialects, band 52260–52299)

**Interfaces:**
- Consumes: Task 11 `create_run`, `enqueue_delivery`, `ReportDeliveryPayload`, `REPORT_DELIVERY_KIND`; Task 10 `load_schedule`; Task 9 `next_occurrence`.
- Produces: D13 (`TICK_LIMIT`, `run_report_scheduler_tick`, `purge_report_runs`, worker wrappers, daemon entries).

- [ ] **Step 1: Write the failing tests**

```python
NOW = datetime(2026, 10, 12, 5, 0, 30)     # Monday 07:00:30 Berlin

async def test_due_schedule_enqueues_and_advances(env) -> None:
    sid = await env.make_schedule(owner=52260, recurrence={"kind": "daily", "time": "07:00"},
                                  next_run_at=datetime(2026, 10, 12, 5, 0))
    out = await run_report_scheduler_tick(settings=env.settings, session_factory=env.factory, now=NOW)
    assert out["enqueued"] == 1
    sched = await env.load_schedule(sid)
    assert sched.next_run_at == datetime(2026, 10, 13, 5, 0)
    runs = await env.load_runs(sid)
    assert len(runs) == 1 and runs[0].run_kind == "schedule" and runs[0].due_at == datetime(2026, 10, 12, 5, 0)
    job = await env.load_job(runs[0].job_id)
    assert job.kind == "core.report_delivery" and job.dedupe_key == f"report_schedule:{sid}:20261012T0500"
    assert job.created_by == 52260

async def test_downtime_runs_one_catch_up(env) -> None:
    sid = await env.make_schedule(owner=52260, recurrence={"kind": "daily", "time": "07:00"},
                                  next_run_at=datetime(2026, 10, 7, 5, 0))       # 5 occurrences missed
    await run_report_scheduler_tick(settings=env.settings, session_factory=env.factory, now=NOW)
    assert len(await env.load_runs(sid)) == 1
    assert (await env.load_schedule(sid)).next_run_at == datetime(2026, 10, 13, 5, 0)
    await run_report_scheduler_tick(settings=env.settings, session_factory=env.factory, now=NOW)
    assert len(await env.load_runs(sid)) == 1

async def test_concurrent_ticks_enqueue_once(env) -> None:
    sid = await env.make_schedule(owner=52260, recurrence={"kind": "daily", "time": "07:00"},
                                  next_run_at=datetime(2026, 10, 12, 5, 0))
    await asyncio.gather(*(run_report_scheduler_tick(settings=env.settings, session_factory=env.factory, now=NOW)
                           for _ in range(3)))
    assert len(await env.load_runs(sid)) == 1
    assert await env.count_jobs(kind="core.report_delivery", created_by=52260) == 1

async def test_inactive_and_future_are_ignored(env) -> None: ...

async def test_broken_recurrence_pauses(env) -> None:
    sid = await env.make_schedule(owner=52260, recurrence_raw='{"kind": "hourly"}',
                                  next_run_at=datetime(2026, 10, 12, 5, 0))
    out = await run_report_scheduler_tick(settings=env.settings, session_factory=env.factory, now=NOW)
    s = await env.load_schedule(sid)
    assert out["paused"] == 1 and s.active is False and "ungültig" in s.paused_reason

async def test_purge_report_runs(env) -> None:
    # finished 100 days ago -> deleted with files; finished 10 days ago -> kept; queued 100 days ago -> deleted
    ...

async def test_worker_wrappers_respect_enabled_flag(env) -> None: ...   # KEY_REPORT_SCHEDULER_ENABLED "0" -> {"skipped": 1}

def test_daemon_catalogue_entries() -> None:
    slugs = {s.slug: s for s in DAEMON_SERVICES}
    assert slugs["report_scheduler"].schedule_kind == "interval" and slugs["report_scheduler"].toggleable
    assert slugs["report_runs_purge"].daily_at == "03:45"
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D13. The compare-and-set UPDATE must compare `next_run_at` with the exact value read (naive UTC DATETIME, no microseconds — store `next_run_at` truncated to whole seconds everywhere: in Task 10 when computing, here when comparing). Each schedule in its own session; an exception for one schedule is logged and counted (`errors`) and does not stop the tick. Wire both loops into `worker/__main__.py` and the daemon catalogue; check the admin Daemons page test (`frontend/src/routes/admin/DaemonsPage.test.tsx`) still passes with the new catalogue rows (backend list is data-driven; frontend only needs the i18n keys).
- [ ] **Step 4: Run → PASS**; also `backend/tests/test_worker*.py` and the daemon-catalogue tests; lint gates; `cd frontend && node scripts/check-i18n-keys.mjs && pnpm test --run src/routes/admin/DaemonsPage.test.tsx`.
- [ ] **Step 5: Commit** `feat(reporting): scheduler loop and run retention for report schedules`.

---

### Task 13: GDPR erasure and agent removal for schedules (backend)

**Files:**
- Modify: `backend/src/tiqora/gdpr/saved_criteria.py`, `backend/src/tiqora/gdpr/erasure.py`, `backend/src/tiqora/domain/user_delete.py`, `docs/gdpr.md` (one paragraph; full docs in Task 18)
- Test: `backend/tests/test_report_schedules_gdpr.py` (both dialects, band 52300–52319)

**Interfaces:**
- Consumes: Task 1 tables; existing `scrub_saved_criteria(session, login_to_new, company_to_new, *, backup)`, `delete_user_rows(session, user_id)`.
- Produces: D14; `scrub_schedule_items(session, login_to_new, company_to_new, *, backup) -> int` and `remove_erased_recipient_emails(session, emails: Collection[str]) -> int` in `gdpr/saved_criteria.py`, both called from the erasure flow next to `scrub_saved_criteria`.

- [ ] **Step 1: Read** `gdpr/erasure.py` around `scrub_saved_criteria` (line ~2033) and the backup/rollback mechanism (`rollback_job`), and `domain/user_delete.py` (`blocking_references`, `delete_user_rows`). Decide whether schedule items can be restored by the existing column-wise rollback; write the decision into D14 (this plan) in the same commit.
- [ ] **Step 2: Write the failing tests**

```python
CUSTOMER_PARAM_DEF = {"source": "tickets", "dimensions": [{"key": "queue"}], "measures": [{"key": "tickets"}],
                      "parameters": [{"key": "p_kunde", "field": "customer_user", "op": "in"}]}

async def test_erasure_rewrites_schedule_item_parameters(env) -> None:
    report_id = await env.insert_saved_report(owner=52300, definition=CUSTOMER_PARAM_DEF)
    sid = await env.insert_schedule(owner=52300, items=[
        (str(report_id), {"parameters": {"p_kunde": ["rep52.kunde", "rep52.other"]}}),
        ("builtin.volume", None)])
    backups: list[tuple[str, dict, dict]] = []
    async def backup(table, pk, original):
        backups.append((table, pk, original))
    async with env.factory() as s:
        changed = await scrub_schedule_items(s, {"rep52.kunde": "erased-52300"}, None, backup=backup)
        await s.commit()
    assert changed == 1
    items = await env.load_items(sid)
    assert json.loads(items[0].overrides)["parameters"]["p_kunde"] == ["erased-52300", "rep52.other"]
    assert items[1].overrides is None
    assert (await env.load_schedule(sid)).version == 2
    assert backups and backups[0][0] == "tiqora_report_schedule_item"

async def test_erasure_ignores_non_customer_parameters(env) -> None:
    definition = {**CUSTOMER_PARAM_DEF, "parameters": [{"key": "p_queue", "field": "queue", "op": "in"}]}
    report_id = await env.insert_saved_report(owner=52300, definition=definition)
    sid = await env.insert_schedule(owner=52300, items=[(str(report_id), {"parameters": {"p_queue": ["rep52.kunde"]}})])
    async with env.factory() as s:
        assert await scrub_schedule_items(s, {"rep52.kunde": "erased-52300"}, None, backup=None) == 0
        await s.commit()
    assert json.loads((await env.load_items(sid))[0].overrides)["parameters"]["p_queue"] == ["rep52.kunde"]

async def test_erasure_removes_recipient_email(env) -> None:
    sid = await env.insert_schedule(owner=52300, emails=["kunde52@example.org", "other52@example.org"])
    async with env.factory() as s:
        removed = await remove_erased_recipient_emails(s, ["Kunde52@Example.org"])
        await s.commit()
    assert removed == 1
    assert [r.email for r in await env.load_recipients(sid)] == ["other52@example.org"]
    assert (await env.load_schedule(sid)).version == 2

async def test_full_erasure_flow_calls_both(env) -> None:
    # customer user rep52.kunde (email kunde52@example.org) with one ticket; run the erasure job the way
    # tests/test_gdpr_saved_criteria.py runs it -> recipient row gone, schedule item parameter rewritten
    await env.seed_customer_user("rep52.kunde", email="kunde52@example.org", ticket_id=52310)
    report_id = await env.insert_saved_report(owner=52300, definition=CUSTOMER_PARAM_DEF)
    sid = await env.insert_schedule(owner=52300, emails=["kunde52@example.org"],
                                    items=[(str(report_id), {"parameters": {"p_kunde": ["rep52.kunde"]}})])
    await env.run_erasure(login="rep52.kunde")
    assert await env.load_recipients(sid) == []
    assert json.loads((await env.load_items(sid))[0].overrides)["parameters"]["p_kunde"] != ["rep52.kunde"]

async def test_agent_delete_removes_schedules_and_runs(env) -> None:
    own = await env.insert_schedule(owner=52301, user_ids=[52301])
    other = await env.insert_schedule(owner=52300, user_ids=[52300, 52301])
    run_id = await env.insert_run(owner=52301, schedule_id=own, file=b"%PDF-1.7")
    async with env.factory() as s:
        assert [r for r in await blocking_references(s, 52301) if r.table.startswith("tiqora_report")] == []
        await delete_user_rows(s, 52301)
        await s.commit()
    assert await env.load_schedule(own) is None
    assert await env.load_run(run_id) is None and await env.count_run_files(run_id) == 0
    assert [r.ref_id for r in await env.load_recipients(other)] == [52300]
    assert (await env.load_schedule(other)) is not None
```

(`env.insert_*`/`load_*` are small helpers in the module writing/reading the new tables with Core statements; `env.seed_customer_user` and `env.run_erasure` reuse the seeding and job helpers of `backend/tests/test_gdpr_saved_criteria.py` — import or copy them, do not invent a second erasure path. Seed agents 52300 and 52301 in `users`.)

- [ ] **Step 3: Run → FAIL**.
- [ ] **Step 4: Implement** D14 with `FOR UPDATE` reads like `scrub_saved_criteria`; e-mail comparison lower-case; no JOIN between `customer_user` and `tiqora_*` (load the e-mail list first).
- [ ] **Step 5: Run → PASS** plus the existing GDPR and user-delete tests; lint gates.
- [ ] **Step 6: Commit** `feat(gdpr): report schedules follow customer erasure and agent removal`.

---

### Task 14: Frontend API layer, demo data, i18n base (frontend)

**Files:**
- Modify: `frontend/src/lib/reportsApi.ts`, `frontend/src/lib/reportsApi.test.ts`, `frontend/src/demo/mockData.ts`, `frontend/src/demo/reportsMock.ts`, `frontend/src/i18n/locales/en.json`, `de.json` (+ propagate)
- Create: `frontend/src/lib/reportSchedulesApi.ts`, `frontend/src/lib/reportSchedulesApi.test.ts`, `frontend/src/demo/schedulesMock.ts`, `frontend/src/demo/schedulesMock.test.ts`

**Interfaces:**
- Consumes: regenerated `Schemas` from Tasks 5, 8, 10, 11.
- Produces: D15 API functions (`reportsApi.znunyStatsPreview`, `.znunyStatsFilePreview`, `.znunyStatsApply`; `reportSchedulesApi.list/get/create/update/remove/runNow/runs/fileUrl/previewRecurrence`), types `ReportSchedule = Schemas["ReportScheduleOut"]`, `ReportScheduleIn`, `ReportRun`, `ZnunyStatsPreview`, `StatPreview`, `ImportIssue`; demo handlers for every new route (MSW pattern of `reportsMock.ts`) with 3 schedules (active weekly PDF, paused with `paused_reason`, monthly XLSX bundle), 4 runs (succeeded, partial, failed, running), 5 Znuny stats (report, saved search, none, already imported, approximated issues); i18n namespaces `reports.schedules.*`, `reports.znunyImport.*`, `reports.exportPdf`, `jobs.kinds.core.report_delivery`.

- [ ] **Step 1: Write the failing tests** (Vitest)

```ts
describe("reportSchedulesApi", () => {
  it("lists with all=true only when asked", async () => {
    const calls = mockApi();
    await reportSchedulesApi.list();
    await reportSchedulesApi.list(true);
    expect(calls.map((c) => [c.method, c.path, c.query])).toEqual([
      ["GET", "/report-schedules", {}],
      ["GET", "/report-schedules", { all: true }],
    ]);
  });
  it("runNow posts owner_only", async () => {
    const calls = mockApi({ run_id: 7, job: { job_id: 9, status: "queued" } });
    await reportSchedulesApi.runNow(3, false);
    expect(calls[0]).toMatchObject({ method: "POST", path: "/report-schedules/3/run", body: { owner_only: false } });
  });
  it("builds the run file URL", () => {
    expect(reportSchedulesApi.fileUrl(5, 8)).toBe("/api/v1/report-runs/5/files/8");
  });
});

describe("reportsApi Znuny import", () => {
  it("sends the file as base64", async () => {
    const calls = mockApi({ stats: [], errors: [], truncated: false });
    await reportsApi.znunyStatsFilePreview(new File(["<otrs_stats/>"], "s.xml", { type: "text/xml" }));
    expect(calls[0].body).toEqual({ content_base64: btoa("<otrs_stats/>") });
  });
  it("rejects files over 2 MB before upload", async () => {
    await expect(reportsApi.znunyStatsFilePreview(new File([new Uint8Array(2_000_001)], "big.xml")))
      .rejects.toThrow();
  });
});
```

(`mockApi` = the helper `reportsApi.test.ts` already uses — reuse, do not invent.)

- [ ] **Step 2: Run → FAIL** `cd frontend && pnpm test --run src/lib/reportSchedulesApi.test.ts src/lib/reportsApi.test.ts`.
- [ ] **Step 3: Implement** the API modules, demo data and handlers, i18n keys (German Sie-form: "Zeitpläne", "Zeitplan anlegen", "Jetzt senden", "Testversand an mich", "An alle Empfänger senden", "Nächste Termine", "Empfänger", "Externe E-Mail-Adressen", "Nur senden, wenn Daten vorhanden sind", "Angehalten", "Statistiken aus Znuny übernehmen", "Nicht übernommen", "Angepasst", "Bereits übernommen", "Als PDF", …); `propagate-i18n-keys.mjs`.
- [ ] **Step 4: Run → PASS**; `pnpm lint`; `node scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Commit** `feat(reports-ui): API layer and demo data for schedules and Znuny import`.

---

### Task 15: "Als PDF" and the Znuny stats import dialog (frontend)

**Files:**
- Modify: `frontend/src/components/reports/ReportExportButton.tsx` (+ test), `frontend/src/routes/agent/ReportsPage.tsx` (+ test), locales (+ propagate)
- Create: `frontend/src/components/reports/ZnunyStatsImportDialog.tsx`, `ZnunyStatsImportDialog.test.tsx`

**Interfaces:**
- Consumes: Task 14 API/types; `JobDialog`; `exportLabels`; `useAuth()` user flags (`can_view_reports`, `can_create_reports`).
- Produces: `ZnunyStatsImportDialog({open, onClose, canCreate})`; `data-testid`s `report-export-pdf`, `reports-znuny-import`, `znuny-import-dialog`, `znuny-import-row-<import_key>`, `znuny-import-apply`, `znuny-import-file`, `reports-schedules-link`.

- [ ] **Step 1: Write the failing tests**

```tsx
it("starts a PDF export with the UI language", async () => {
  const exportSpy = vi.spyOn(reportsApi, "export").mockResolvedValue({ job_id: 4, status: "queued" });
  renderWithProviders(<ReportExportButton runIn={RUN_IN} result={RESULT} title="rep52 Bericht" infoLines={[]} />);
  await userEvent.click(screen.getByTestId("report-export-pdf"));
  expect(exportSpy).toHaveBeenCalledWith(expect.objectContaining({ format: "pdf", locale: "de" }));
});

it("lists stats with target badges and issues, applies the selection", async () => {
  vi.spyOn(reportsApi, "znunyStatsPreview").mockResolvedValue(PREVIEW);   // 1 report, 1 search, 1 none
  const apply = vi.spyOn(reportsApi, "znunyStatsApply").mockResolvedValue(APPLIED);
  renderWithProviders(<ZnunyStatsImportDialog open onClose={() => {}} canCreate />);
  expect(await screen.findByText("Bericht")).toBeInTheDocument();
  expect(screen.getByText("Gespeicherte Suche")).toBeInTheDocument();
  const none = screen.getByTestId("znuny-import-row-znuny.stat.3");
  expect(within(none).getByRole("checkbox")).toBeDisabled();
  await userEvent.click(within(screen.getByTestId("znuny-import-row-znuny.stat.1")).getByRole("button", { name: /Details/ }));
  expect(screen.getByText(/Dynamische Felder stehen in Berichten noch nicht zur Verfügung/)).toBeInTheDocument();
  await userEvent.click(within(screen.getByTestId("znuny-import-row-znuny.stat.1")).getByRole("checkbox"));
  await userEvent.click(screen.getByTestId("znuny-import-apply"));
  expect(apply).toHaveBeenCalledWith({ stat_ids: [1] });
  expect(await screen.findByRole("link", { name: /rep52 Tickets pro Monat/ })).toHaveAttribute("href", "/agent/reports/41");
});

it("without create right apply is disabled with a hint", async () => { ... });
it("file tab previews an uploaded file and shows load errors", async () => { ... });
it("empty state", async () => { ... });
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D15 (dialog with the existing dialog component used by `JobDialog`/import dialogs; issue list grouped "Nicht übernommen" / "Angepasst"; already-imported rows preselected off with badge "Bereits übernommen – wird aktualisiert"; result view with links). ReportsPage header buttons. Demo mode check with Playwright (1280/390, light/dark) on `/agent/reports` → dialog open; delete `.playwright-mcp`.
- [ ] **Step 4: Run → PASS**; `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Commit** `feat(reports-ui): PDF export and Znuny statistics import dialog`.

---

### Task 16: Schedule pages — list, editor, run history (frontend)

**Files:**
- Create: `frontend/src/components/reports/schedules/scheduleForm.ts` (+ `.test.ts`), `RecurrenceFields.tsx`, `RecipientsFields.tsx`, `ScheduleReportsPicker.tsx`, `ScheduleRunsTable.tsx` (+ tests for form/recurrence/runs), `frontend/src/routes/agent/ReportSchedulesPage.tsx` (+ test), `ReportScheduleEditorPage.tsx` (+ test)
- Modify: `frontend/src/router.tsx`, `frontend/src/routes/agent/ReportViewPage.tsx` ("Zeitplan anlegen"), locales (+ propagate)

**Interfaces:**
- Consumes: Task 14 API; existing pickers (agent picker, share groups `/reference/share-groups`, time-zone options, report catalogue `reportsApi.catalogue()`), `ReportsAccessGate`, `useAuth`.
- Produces: routes `/agent/reports/schedules`, `/agent/reports/schedules/new`, `/agent/reports/schedules/$scheduleId`; `scheduleForm` (`ScheduleFormState`, `emptyForm`, `fromSchedule`, `toRequest`, `validateForm`).

- [ ] **Step 1: Write the failing tests**

```ts
describe("scheduleForm", () => {
  it("round-trips a schedule", () => {
    expect(toRequest(fromSchedule(SCHEDULE_OUT))).toEqual(SCHEDULE_IN_WITH_VERSION);
  });
  it("defaults recipients to the owner and weekly Monday 07:00", () => {
    const f = emptyForm({ ownerId: 52120, reportKey: "builtin.volume", timeZone: "Europe/Berlin" });
    expect(f.recipients.user_ids).toEqual([52120]);
    expect(f.recurrence).toEqual({ kind: "weekly", time: "07:00", weekdays: [1], workdays_only: false, day_of_month: null, month: null });
    expect(f.items).toEqual([{ report_key: "builtin.volume", overrides: {} }]);
  });
  it("validates", () => {
    const f = { ...emptyForm(DEFAULTS), name: " ", items: [] };
    expect(validateForm(f, { canShare: false, externalAllowed: false })).toMatchObject({
      name: "reports.schedules.errors.name", items: "reports.schedules.errors.items" });
    const g = { ...emptyForm(DEFAULTS), recipients: { ...emptyForm(DEFAULTS).recipients, group_ids: [3] } };
    expect(validateForm(g, { canShare: false, externalAllowed: false }).recipients).toBe("reports.schedules.errors.shareRight");
  });
  it("strips fields that do not belong to the recurrence kind", () => {
    const f = { ...emptyForm(DEFAULTS), recurrence: { kind: "daily", time: "06:00", weekdays: [1, 2], workdays_only: true, day_of_month: 3, month: 2 } };
    expect(toRequest(f).recurrence).toEqual({ kind: "daily", time: "06:00", workdays_only: true });
  });
});
```

Component tests: editor saves (POST then navigate to `/agent/reports/schedules/<id>`), 409 shows "Der Zeitplan wurde inzwischen geändert …" with reload button, recurrence preview list renders 5 local dates, e-mail input hidden when `report_external_recipients` false, recipients limited to "an mich" without share right with hint; runs table shows status chips, file links (`reportSchedulesApi.fileUrl`), polling while a run is `queued`/`running` (fake timers), "Jetzt senden" menu calls `runNow(id, true|false)` and 409 shows "Ein Versand läuft bereits."; list page toggles `active` via PUT with version, shows `paused_reason`, admin "Alle anzeigen" switch; ReportViewPage shows "Zeitplan anlegen" only with `can_create_reports` and links with `?report=<key>`.

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement** D15. Routes before `$reportKey` routes in `router.tsx`. All times shown in the browser's local zone with the schedule zone as suffix when different ("07:00 (Europe/Berlin)"). Demo-mode Playwright check of the three screens at 1280/390, light/dark; delete `.playwright-mcp`.
- [ ] **Step 4: Run → PASS**; `pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 5: Commit** `feat(reports-ui): report schedules with recipients, recurrence and run history`.

---

### Task 17: End-to-end flows and e2e mocks (frontend)

**Files:**
- Modify: `frontend/e2e/fixtures/mock-api.ts` (schedules, runs, Znuny import, PDF export job), `frontend/e2e/report-builder.spec.ts` only if the export menu change breaks a selector
- Create: `frontend/e2e/report-schedules.spec.ts`

**Interfaces:**
- Consumes: Tasks 14–16 test ids and routes.

- [ ] **Step 1: Write the e2e spec** (tag-only like the other specs — check how existing specs are tagged and copy):
  1. Reports overview → "Statistiken aus Znuny übernehmen" → select one report stat → "Übernehmen" → result link opens the saved report.
  2. Report viewer → "Als PDF" → job dialog shows done with a download link.
  3. Report viewer → "Zeitplan anlegen" → fill name, weekly Mon+Fri 07:00, recipients "an mich" → save → editor shows "Nächste Termine" and an empty run history → "Jetzt senden" → "Testversand an mich" → a run row appears "Läuft" then "Gesendet" (mock flips status on the second poll).
  4. Schedules list → pause toggle → row shows "Angehalten".
- [ ] **Step 2: Run** `cd frontend && pnpm exec playwright test e2e/report-schedules.spec.ts` (with the project's e2e command/tag) → PASS; rerun the other report specs.
- [ ] **Step 3:** `pnpm lint`.
- [ ] **Step 4: Commit** `test(reports-ui): e2e for Znuny import, PDF export and schedules`.

---

### Task 18: Docs and full verification

**Files:**
- Modify: `docs/compatibility.md` (row "Stats framework": add Znuny stats import (db + file), PDF, scheduled delivery → status per the doc's scale; new sub-section under "Reporting differences to Znuny stats" — "Importing Znuny statistics" with the full D6 mapping tables, StateAction approximation, TicketList → saved search, what is never taken over; "Scheduled reports vs. the Statistic Reports add-on": bundles, recipients, Bcc, presets, owner permissions), `docs/architecture.md` (section "Stats / reporting": `znuny_stats` package, texts bundle, svg/pdf, schedules/delivery/scheduler, run files; 15–25 lines with module paths), `docs/deployment.md` (WeasyPrint native libs in the image + image size delta; `fonts-dejavu-core`; new daemon services and their settings; sender address requirement `notification.sender_email` / `NotificationSenderEmail`; `TIQORA_PUBLIC_BASE_URL` for the link in mails; external recipients setting; run retention), `docs/parallel-operation.md` (`xml_storage` read only; stats changed in Znuny afterwards are re-imported on demand; Znuny's own `Maint::Stats::Generate` cron keeps working independently), `docs/gdpr.md` (schedule recipients/overrides on erasure; rollback does not restore removed recipients; run files hold report data for `run_retention_days`; run history and mail log hold no recipient addresses), `docs/i18n.md` (server text bundle: `uv run python -m tiqora.reporting.texts --sync` after changing `reports.*` keys)
- No code changes except fixes the sweep finds.

- [ ] **Step 1: Write the docs** (English, like the existing docs; German UI terms with their English meaning). Do NOT touch `site/compare.html` or README (the compare page is updated per release by the orchestrator).
- [ ] **Step 2: Full backend suite** `cd backend && TIQORA_STRICT_DB_LEAKS=1 TIQORA_REQUIRE_PDF=1 uv run python -m pytest -q` → all green; `rtk git diff --stat backend/tests/db_leak_baseline.txt` empty; lint gates.
- [ ] **Step 3: Frontend** `cd frontend && pnpm lint && pnpm test --run && node scripts/check-i18n-keys.mjs`.
- [ ] **Step 4: Freshness checks** regenerate openapi (both) + `pnpm build` → no diff; `cd backend && uv run python -m tiqora.reporting.texts --check` → in sync.
- [ ] **Step 5: Alembic graph** `cd backend && uv run alembic heads` shows the expected heads; `uv run python -m pytest -q tests/test_migration_gate.py tests/test_migrate_cli.py`.
- [ ] **Step 6: Commit** `docs(reporting): Znuny stats import, PDF reports and scheduled delivery`.

---

## Self-review notes (for the executor)

- Spec coverage (phase 3 row): Znuny stats import from `xml_storage` with the four dynamic objects + `StateAction`, unmappable elements listed, read-only → Tasks 2–5 (+ file import, A16); import/export as file → wave 2 (Tiqora files) + Task 5 (Znuny files); WeasyPrint PDF with server SVG charts matching the frontend tokens → Tasks 6–8; Debian libs, image size, osv → Task 8 + Task 18 docs; scheduled generation + mail delivery with bundles, recipients (users/groups/roles/external by setting), subject/body, `only_if_nonempty`, `next_run_at`/`last_run_at`/`last_status`, run table with retention and stored files → Tasks 1, 9–12; "scheduled reports run with the owner's permissions and say so in the mail" → Task 11 (footer + PDF owner line); "failures surface on the Daemons page and notify the owner" → Task 12 (tick status) + Task 11 (owner mail); tests "fixtures exported from a real Znuny 6.5 and 7.3 peer" → Task 2 fixtures (Znuny's own samples + made-up files) + Task 5 golden test against the 6.5 peer (7.3 when its peer is up); "PDF text extraction with pypdf" → Task 8; Playwright "schedule creation" → Task 17.
- Shared files other wave-3 plans may also touch: the Alembic primary head (+ `test_migration_gate.py`, `test_migrate_cli.py`), `db/tiqora/models.py`, `api/v1/__init__.py`, `api/v1/reports.py`, `domain/schemas.py` (`UserMe`), `api/v1/auth.py`, `domain/api_key_scopes.py`, `worker/__main__.py`, `worker/services.py`, `domain/settings_store.py`, `config.py`, `gdpr/erasure.py`, `domain/user_delete.py`, `Dockerfile`, `.github/workflows/ci.yml`, `pyproject.toml`/`uv.lock`, `router.tsx`, `demo/mockData.ts`, `e2e/fixtures/mock-api.ts`, `en.json`/`de.json` (+ propagated locales). Conflicts are additive; re-run propagate, `uv lock` and the openapi regeneration instead of hand-merging generated files.
- Test bodies marked `...` (Task 2 `test_load_stats_from_xml_storage`, Task 7 `test_chart_data_from_result`, Task 10 `test_limit_and_delete`, Task 11 `test_only_if_nonempty_skips`, `test_attachment_too_large`, `test_file_download_owner_or_admin_only`, `test_interrupted_after_checkpoint_marks_failed`, Task 12 `test_inactive_and_future_are_ignored`, `test_purge_report_runs`, `test_worker_wrappers_respect_enabled_flag`, Task 15 three component tests) are specified by their comment line and MUST be written out in full by the implementer before Step "Run → FAIL"; a task review rejects a commit that still contains `...` in a test. Task 16's component tests are specified in prose with the exact behaviours to assert.
- Type consistency: `ImportIssue` (backend) ↔ `ImportIssueOut` (API) ↔ `ImportIssue` (frontend type alias of `Schemas["ImportIssueOut"]`); `ReportSchedule` (store dataclass) vs `ReportScheduleOut` (API) vs frontend `ReportSchedule = Schemas["ReportScheduleOut"]` — do not mix them up in imports; `run_kind`, never `trigger`.
