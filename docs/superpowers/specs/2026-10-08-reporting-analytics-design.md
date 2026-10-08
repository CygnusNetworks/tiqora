# Reporting & analytics — design

> Part of the [competitive parity roadmap](2026-10-08-competitive-parity-roadmap-design.md).
> Status: proposed 2026-10-08. Decisions below are fixed unless marked "open".

## Goal

Replace "Partial — fixed reports + CSV; no configurable stats" in the
comparison with the strongest reporting of any self-hosted helpdesk:
everything Znuny's stats framework and the paid "Statistic Reports" add-on
do, plus a report builder, dashboards, scheduled delivery, SLA/process/AI
analytics and natural-language questions — without Elasticsearch.

## Current state (2026-10-08)

- `tiqora/stats/service.py` + `api/v1/stats.py`: five fixed reports
  (`ticket_volume`, `backlog_trend`, `open_snapshot`, `sla_stats`,
  `agent_workload`), JSON + `;`-CSV with BOM. Computed live per request with
  ORM selects on `ticket`, `ticket_history`, `article`; `open_snapshot` and
  `sla_stats` load whole `Ticket` rows; bucketing in Python. Scoped to `ro`
  queues; any agent may call them.
- `/stats` page with hand-written SVG charts (`components/agent/stats/*`) —
  no chart library in `frontend/package.json`.
- Agent home `/` (`DashboardPage.tsx`) has fixed sections; per-user state only
  in `localStorage`. No widget system.
- Time accounting: `GET /api/v1/tickets/time-accounting` (list + total), page
  groups by day client-side; no grouping by customer/queue/agent, no export.
- No saved searches (`search_profile` unused), no Znuny stats import
  (`xml_storage` unused), no scheduled delivery, no PDF/XLSX writer
  (`openpyxl` is installed but only reads attachments).
- Worker has `_interval_loop` and `_daily_loop` (`worker/__main__.py`);
  GenericAgent already evaluates Znuny-style schedules.
- Analytics-friendly Tiqora tables: `tiqora_event_outbox` (Tiqora writes only),
  `tiqora_ai_usage`, `tiqora_ai_triage`, `tiqora_ai_draft`,
  `tiqora_ai_article_origin`, `tiqora_mail_log`.
- Production constraint: nginx `proxy_read_timeout 90s` on `/api/` → anything
  that can run long must be async + polling.

## What competitors offer (research 2026-10-08)

- **Znuny stats framework**: Dynamic Matrix (objects Ticket Accumulation,
  Ticket Accounted Time, Ticket Solution/Response Time; X axis, Y axis,
  restrictions, runtime-changeable fields, sum row/column, timezone, result
  cache), Dynamic List (Ticket List), Static (Perl modules); output CSV,
  Excel (sheet names, frozen panes, column widths since 6.3), print/PDF,
  bar/line/pie graph; "show as dashboard widget" for matrix stats; access via
  the `stats` group; YAML import/export. ITSM adds CI/change stats, first-level
  solution rate, average solution time.
- **Scheduling in Znuny**: core = cron + `Maint::Stats::Generate`. The paid
  "Statistic Reports" add-on bundles several stats into one CSV/XLSX/PDF report,
  daily/weekly/monthly/yearly, several recipients with own subject/body.
- **Zammad**: needs Elasticsearch; fixed metrics, report profiles as filters,
  `.xlsx` ticket download capped at 6,000 rows; no builder, no scheduling.
- **Best in class** (Freshservice Analytics, Zendesk Explore, JSM): widget
  reports as dashboards, custom metrics, SLA datasets, scheduled PDF/CSV/XLS
  delivery, Zendesk announcing "agentic analytics".

## Parity checklist

- [ ] Configurable matrix reports (X, Y, filters) over tickets, accounted time, response/solution time
- [ ] Configurable ticket list reports (column choice, sort, filters)
- [ ] Runtime-changeable filters with preselected allowed values
- [ ] Sum row / column, timezone per report
- [ ] Bar / line / pie output, table output
- [ ] CSV, XLSX (formatted), PDF export
- [ ] Report as dashboard widget
- [ ] Access by group (`stats` = view, `stats_rw`-style edit right)
- [ ] Import of existing Znuny stats definitions; import/export as file
- [ ] Scheduled generation + email delivery (Znuny add-on level: bundles, recipients, subject/body)
- [ ] Time accounting by agent / customer / queue / ticket type (Znuny `TicketAccountedTime`)
- [ ] First-level solution rate, average solution time (ITSM stats)
- [ ] Saved searches (Znuny search profiles) — on the "missing" list in `docs/compatibility.md`
- [ ] Configurable dashboard — on the "missing" list

## Beyond parity ("besser als")

1. **Historically correct numbers**: backlog, time-in-state, queue transfers
   and SLA compliance computed from `ticket_history` as events, not from the
   ticket's *current* state (Znuny's accumulation stats answer "what is the
   ticket now", which makes last month's numbers drift).
2. **Business-hours-aware SLA analytics**: response/solution time in working
   minutes using the same Znuny calendars the escalation code uses; P50/P90,
   breach by queue/service/SLA/customer; "near miss" (< 10 % left).
3. **AI and automation impact**: auto-reply resolution rate, triage accuracy
   (triaged queue vs. final queue), draft acceptance/edit distance, cost per
   ticket and per resolved ticket (`tiqora_ai_usage`), handoff reasons.
4. **Ask in plain words**: "Wie viele Telegram-Tickets hatte Queue 5 im
   September, nach Woche?" → the LLM returns a *report definition* (JSON
   validated against the semantic layer), never SQL; the user sees and can
   save the definition. Same as an MCP tool (`run_report`, `list_measures`).
5. **Trending topics**: weekly clustering of new-ticket subjects/summaries
   (existing summaries + embeddings once hybrid RAG lands) → "rising issues"
   widget; volume anomaly alerts (z-score on hourly volume per queue) as an
   agent notification.
6. **Wallboard / TV mode** with live numbers over the existing SSE stream.
7. **Process analytics** (with the process work): tickets per activity, dwell
   time per activity, actual paths taken (process mining from history).
8. **No Elasticsearch, no row caps**: XLSX streaming up to the configured limit.

## Fixed decisions

### 1. Two data paths: fact tables for history, compiled queries for live lists

- **Fact tables** (new, `tiqora_report_*`, filled incrementally by the worker):
  - `tiqora_report_ticket_fact` — one row per ticket: created, first
    response at, first response working minutes, closed at (last close),
    solution working minutes, reopen count, queue at create / current,
    state type, priority, type, service, SLA, owner, responsible,
    customer_id, customer_user, first-article channel, inbound/outbound
    article counts, agent touches, accounted time sum, escalation breach
    flags (response/update/solution), near-miss flags, AI flags (triaged,
    triage accepted, auto-replied, draft used, handed off), process id,
    changed watermark.
  - `tiqora_report_ticket_event` — one row per relevant history event
    (state change, queue move, owner change, priority change, close, reopen,
    process activity change) with `from`/`to` and duration since the previous
    event of the same kind → time-in-state, ping-pong, dwell time.
  - `tiqora_report_daily_snapshot` — per day × queue × state type × priority:
    open count, age buckets (0–1d, 1–3d, 3–7d, 7–30d, >30d). Taken by a
    `_daily_loop` at 00:05 in the configured report timezone; backfilled
    from events for history.
- Source of truth is `ticket_history` (written by **both** Znuny and Tiqora),
  read with an id watermark like the existing Znuny-write poller — not
  `tiqora_event_outbox`, which misses Znuny-side writes.
- Backfill: `tiqora report backfill [--since]` CLI, chunked, resumable,
  idempotent; also offered as an admin button (async job).
- Working-minute calculation reuses the escalation calendar code (same
  calendar per queue/SLA as escalation) so reported SLA numbers match what
  escalation did.
- **Compiled queries** for "now" data (ticket lists, open snapshot,
  current workload): the query compiler (below) runs against `ticket` directly
  with indexes Znuny already has.
- Alternative recorded: everything live on Znuny tables (today's approach).
  Rejected: wrong history semantics and does not scale; Python bucketing over
  whole rows is already slow.
- Alternative recorded: external OLAP (DuckDB/ClickHouse). Rejected for now —
  one more service to run; the fact tables are small (one row per ticket).

### 2. Semantic layer + query compiler

- `tiqora/reporting/semantic.py`: a registry of **sources** (`tickets`,
  `ticket_events`, `daily_snapshot`, `accounted_time`, `articles`,
  `ai_usage`, later `config_items`, `changes`, `chat_sessions`), each with
  typed **dimensions** (queue, state, owner, customer, created date with
  grain, service, SLA, dynamic field X, …) and **measures** (count,
  count_distinct, sum, avg, p50, p90, rate = ratio of two counts).
- `tiqora/reporting/compiler.py`: definition → SQLAlchemy Core. Date
  bucketing via a `date_bucket()` construct with `@compiles` per dialect
  (MySQL/MariaDB `DATE_FORMAT`/`STR_TO_DATE`, PostgreSQL `date_trunc`) —
  tested on both. Percentiles: computed in Python over the bucketed rows
  (MariaDB lacks `PERCENTILE_CONT` as aggregate), capped.
- **Permission is part of the compiler, not of the caller**: every query on a
  ticket-derived source gets the user's `ro` queue set injected; a report
  shared with others runs with the *viewer's* permissions (scheduled reports
  run with the owner's permissions and say so in the mail).
- Dynamic fields as dimensions/filters (Text, Dropdown, Multiselect, Date,
  Checkbox) via `dynamic_field_value` joins, limited to fields marked
  "reportable" (new flag in a `tiqora_*` side table, default on for
  Dropdown/Checkbox/Date).

### 3. Report definitions are data

- `tiqora_report` — `id, name, description, owner_user_id, visibility
  (private|groups|all), group_ids, definition JSON, version, valid,
  created/changed`. Definition: `source, dimensions[], measures[], filters[]
  (with "changeable at runtime" + allowed values), time range (absolute or
  relative: "letzter Monat", "letzte 12 Wochen"), timezone, visualization
  (table|pivot|bar|stacked_bar|line|area|donut|heatmap|kpi), sort, limit,
  totals (row/col)`. JSON Schema-validated; versioned (`schema_version`).
- Built-in reports ship as read-only definitions (seeded by code, not by
  migration) and can be copied.
- Permissions: view = member of Znuny group `stats` (ro), create/edit =
  `stats` rw (Znuny parity, nothing new to administer). Admin can see all.
- Import/export: Tiqora JSON/YAML file; **Znuny import** from `xml_storage`
  (`Type = 'Stats'`): map `Dynamic::Ticket` (matrix), `Dynamic::TicketList`,
  `Dynamic::TicketAccountedTime`, `Dynamic::TicketSolutionResponseTime` to
  definitions; static stats (`StateAction`) map to a built-in; every
  unmappable element is listed in an import report, nothing is silently
  dropped. Read-only on Znuny tables.

### 4. Execution: async jobs with polling

- `POST /api/v1/reports/{id}/run` → `202 {run_id}` when the estimated cost is
  above a threshold, otherwise inline; `GET /api/v1/report-runs/{id}` polls.
  Results cached in Redis by (definition hash, viewer permission hash, time
  window) for 5 minutes.
- Generic job table `tiqora_job` (kind, payload, status, progress, result
  ref, created_by, timestamps) executed by the worker — shared with the
  backfill, GDPR previews and other long operations (see the master roadmap,
  cross-cutting "async jobs").
- Query timeout per dialect (`MAX_EXECUTION_TIME` / `statement_timeout`),
  row limit per output format.

### 5. Output formats

- CSV (exists, keep `;` + BOM), XLSX via `openpyxl` (write-only streaming
  mode; header formatting, frozen header row, column widths, number formats,
  one sheet per report in a bundle), JSON for API.
- **PDF: server-side with WeasyPrint** (HTML template + SVG charts). Needed
  because scheduled mail must work without a browser. Charts for PDF come
  from a small server-side SVG renderer (`tiqora/reporting/svg.py`: bar,
  stacked bar, line, donut, table) whose visual tokens match the frontend
  components. Cost: Pango/Cairo libraries in the runtime image (≈ 40–60 MB);
  install only in the main image stage, document in `docs/deployment.md`.
  Alternative recorded: ReportLab (no system libs, but layout/i18n/RTL is far
  weaker — Tiqora ships RTL locales); headless Chromium (300 MB+). The
  in-browser "print to PDF" stays for interactive use.

### 6. Visualization in the frontend

- Keep hand-written SVG components (current approach, theme tokens, no
  dependency) and add: stacked bar, area, donut, heatmap (weekday × hour),
  KPI tile with sparkline and delta, pivot table with sticky headers.
  Follow the dataviz rules already used (mono numerals, accessible colours,
  light + dark). Revisit a library only if more than ~8 chart types are needed.
- Report builder UI: left = source + dimensions/measures picker, centre =
  live preview (debounced, sample-limited), right = visualization options.
  German labels by what users know ("Zeilen", "Spalten", "Werte", "Filter",
  "Zeitraum").

### 7. Dashboards

Own design document: [dashboards with modular widgets](2026-10-08-dashboard-widgets-design.md).
Reporting provides the data providers for report, KPI, SLA and heatmap
widgets; every saved report can be placed on a dashboard.

### 8. Saved searches

- `tiqora_saved_search` (`owner, name, criteria JSON, shared_group_ids,
  show_in_sidebar, show_count, sort`). Criteria use the same filter model as
  the semantic layer `tickets` source → one filter language for search,
  reports, dashboard widgets and bulk actions.
- Znuny `search_profile` rows are **imported** (read-only) into Tiqora on
  demand per user ("Suchvorlagen aus Znuny übernehmen"); criteria that do not
  map are listed. Writing back into `search_profile` is out of scope.

### 9. Scheduled delivery

- `tiqora_report_schedule` (`owner, name, report_ids[] (bundle), format
  (pdf|xlsx|csv|zip), recurrence (daily/weekly/monthly/yearly + time +
  timezone, or cron-like), recipients (users, groups, roles, external
  emails — external only if allowed by a setting), subject, body template,
  only_if_nonempty, next_run_at, last_run_at, last_status`).
- `tiqora_report_run` (`schedule_id | null, report_id, started, finished,
  status, row_count, file storage ref, error`) with retention (default 90
  days), files stored through the existing `StorageBackend`.
- Worker loop `report_scheduler` (interval 60 s): due schedules → jobs →
  mail via `tiqora_mail_outbound`/`channels/email/smtp.py`; failures surface on
  the Daemons page and notify the owner.

## API / UI surface

- `/api/v1/reporting/sources` (semantic catalog), `/api/v1/reports` (CRUD,
  run, export), `/api/v1/report-runs/{id}`, `/api/v1/report-schedules`,
  `/api/v1/saved-searches`,
  `/api/v1/reports/import/znuny` (preview + apply), `/api/v1/reports/ask`
  (NL → definition, AI policy + PII masking as everywhere).
- The five current `/api/v1/stats/*` endpoints stay as thin wrappers over
  built-in definitions for one release, then deprecate.
- MCP: `list_report_sources`, `run_report`, `list_saved_searches`.
- Frontend: `/reports` (list, builder, viewer), `/reports/schedules`,
  saved searches in the ticket sidebar. Dashboards and wallboard: see the
  dashboard design.

## Built-in report catalogue (first release)

Volume (created/closed/backlog, by channel/queue/type), backlog aging,
SLA compliance (by queue/service/SLA/customer, P50/P90 FRT and solution,
breaches, near misses), first-contact / first-level solution rate, reopen
rate, queue transfers and ping-pong, agent activity (replies, touches,
closed, accounted time), time accounting by customer/queue/agent/type
(Znuny `TicketAccountedTime` parity), customer/company top-N, hour × weekday
heatmap, channel mix incl. Telegram/WhatsApp/SMS/chat, AI impact (see above),
mail delivery health (`tiqora_mail_log`).

## Parallel operation

- Read-only on Znuny tables (`ticket`, `ticket_history`, `xml_storage`,
  `search_profile`, `time_accounting`, `dynamic_field_value`).
- Fact tables are additive `tiqora_*`; the backfill reads history in chunks
  with sleeps so it does not compete with Znuny's daemon.
- History event types written by Znuny vs. Tiqora are identical (ground
  rule 3), so the event parser needs no source distinction.

## Tests

- Compiler: golden SQL snapshots per dialect + execution against MariaDB and
  PostgreSQL in CI; permission injection tests (a report cannot see a queue
  the viewer cannot read, also for shared and scheduled reports).
- Fact builder: synthetic histories (reopen, queue ping-pong, pending,
  merges) → expected facts; working-minute tests reuse escalation fixtures.
- Znuny stats import: fixtures exported from a real Znuny 6.5 and 7.3 peer
  (each dynamic object type) → expected definitions.
- XLSX/PDF: structure tests (sheet names, header, row count), PDF text
  extraction with `pypdf` (already a dependency).
- Playwright: builder flow, dashboard edit, schedule creation.

## Phases

| Phase | Content | Size |
|---|---|---|
| 1 | Async job table + worker; fact tables + history parser + backfill; semantic layer + compiler (tickets, events, accounted time); built-in catalogue v1; XLSX export; time-accounting reports | 3–4 weeks |
| 2 | Report builder UI, saved reports with `stats` group permissions, saved searches + Znuny search-profile import (dashboards: see dashboard design) | 2–3 weeks |
| 3 | Znuny stats import, scheduled delivery, WeasyPrint PDF + server SVG charts | 2–3 weeks |
| 4 | NL questions + MCP tools, AI impact reports, trending topics + anomaly alerts, process analytics (needs process work) | 2 weeks |

## Risks / open

- History parsing must handle every history type both systems write
  (including merges and moves through `TicketMerged`); start from the list the
  history renderer (`domain/history_render.py`) already knows.
- Working-minute SLA numbers may differ from Znuny's escalation columns for
  tickets whose calendar changed mid-life; document as intended (history is
  evaluated with the calendar valid at the time — verify feasibility).
- WeasyPrint adds native libraries to the image — check the Debian base,
  image size budget and the osv-scanner allowlist.
- **Open:** whether a separate read replica is needed for very large sites;
  the compiler can take a second engine URL (`TIQORA_REPORT_DATABASE_URL`).
