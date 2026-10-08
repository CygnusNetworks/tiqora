---
name: tiqora-naive-utc-datetimes-display-2h-early
description: |
  Tiqora/aurix: every timestamp in the UI (ticket create/change time, article
  time, history) shows ~2h too early in summer (CEST) / 1h in winter (CET).
  Use when: (1) a mail/reply that arrived at HH:MM is displayed at HH:MM minus
  the UTC offset (e.g. real 18:57 → shown 16:58, real 10:06 → shown 08:06),
  (2) a ticket "appears in the UI much later than its shown create_time"
  (bot/agent-created tickets), (3) users report "timezone wrong / not set" in
  Tiqora. Root cause: Znuny stores UTC (OTRSTimeZone=UTC) but Tiqora serializes
  DB datetimes as NAIVE ISO (no Z/offset) and the frontend new Date() parses
  naive strings as browser-LOCAL. Fix at the backend serialization boundary.
author: Claude Code
version: 1.2.0
date: 2026-07-27
---

# Tiqora naive UTC datetimes display ~2h early

## Problem
All timestamps in the Tiqora UI are shown shifted **earlier** by the local UTC
offset: −2h under CEST (summer), −1h under CET (winter). A DFN-CERT mail that
truly arrived 10:06 shows as 08:06; a reply that arrived 18:57 shows as 16:58.
A bot/agent-created ticket also seems to "appear in the UI long after its own
create_time" — because its shown time is the offset-shifted value, not when it
actually became visible.

## Context / Trigger Conditions
- Reported timestamps are off by exactly the UTC offset (2h summer / 1h winter),
  always **too early**, never too late.
- Affects **every** timestamp (ticket create/change, article, history), not one
  place → it is a serialization/display bug, not per-record data corruption.
- Backend is aurix/tiqora in parallel operation over a Znuny 6.5 MariaDB.

## Root Cause (three-layer chain, all required)
1. **DB stores UTC.** Znuny SysConfig `OTRSTimeZone = UTC` (default, usually
   unmodified — check `sysconfig_default`/`sysconfig_modified`). Znuny writes
   `ticket.create_time`, `article.create_time`, `ticket_history.create_time`
   etc. as UTC regardless of the MariaDB session `time_zone` (which may be
   `SYSTEM`/local — irrelevant, Znuny does not use it).
2. **Backend serializes NAIVE.** `backend/src/tiqora/domain/schemas.py` declares
   `create_time: datetime` (etc.) with **no** `tzinfo`, **no** `field_serializer`,
   **no** `json_encoders`. aiomysql returns naive `datetime`; Pydantic v2 emits
   ISO **without** offset: `"2026-07-27T08:06:18"`. Note: `SysConfig.otrs_time_zone()`
   exists but is only used in `znuny/escalation.py` and `znuny/ticket_number.py`,
   **not** on the ticket/article read path.
3. **Frontend reads naive as LOCAL.** `frontend/src/lib/format.ts` `formatDateTime`
   does `new Date(value)`. Per ECMAScript, a date-time string **without** a
   timezone offset is parsed in the **browser's local** timezone. So the UTC
   value `08:06` is rendered as `08:06` local instead of the correct `10:06` CEST.

## Second bug (write side) — coupled, fix together
`ticket_write_service.create_ticket`/`add_article` INSERT/UPDATE use SQL
**`current_timestamp`**, which resolves to the DB **session** timezone.
`db/engine.py` sets **no** session tz, and MariaDB defaults to `SYSTEM`
(container-local, e.g. CEST). So rows **written by Tiqora** land in **local
time**, 1–2h ahead of Znuny's UTC rows and of the `datetime.now(UTC)` audit
rows (e.g. `ArticleFlag`). Verified on prod: without pinning, `current_timestamp`
= 11:08 while `utc_timestamp()` = 09:08; with `SET time_zone='+00:00'` both = 09:08.

Consequence of the coupling: today Tiqora-written rows display *correctly by
accident* (written local, read as local). Applying the read fix alone flips them
to +2h *late*. So apply **both** fixes together, and note existing Tiqora-written
rows are already stored in local time (a targeted backfill may be needed —
Znuny/bot rows are UTC and must NOT be touched).

**Write fix:** pin the session to UTC in `db/engine.py` via `connect_args`:
`{"init_command": "SET time_zone = '+00:00'"}` for `mysql+aiomysql`,
`{"server_settings": {"timezone": "UTC"}}` for `postgresql+asyncpg`. aiomysql
accepts `init_command`. This makes every `current_timestamp` write UTC.

## Solution (read side)
Fix once at the **backend serialization boundary** so the API emits UTC-aware
ISO (`…+00:00` / `Z`). Because `OTRSTimeZone` is guaranteed UTC, attach
`tzinfo=UTC` to naive datetimes centrally — e.g. a Pydantic `field_serializer`
for `datetime` on the shared base model in `domain/schemas.py`, or a model-level
serializer. Then the frontend's `new Date()` + `Intl.DateTimeFormat` render
correctly in the browser/user timezone with **no frontend change**, and it fixes
every timestamp app-wide at once.

Do **not** patch by appending `"Z"` in the frontend: fragile, breaks the moment
the backend sends an aware datetime, and ignores `OTRSTimeZone` should it ever
be non-UTC.

Optional later refinement: honor the agent's Znuny personal timezone
(`PreferencesGroups###TimeZone`) instead of browser-local.

## Verification
- Unit test: feed a naive DB-style `datetime(2026,7,27,8,6,18)` through the
  schema and assert the serialized JSON ends with `+00:00` or `Z`.
- E2E: a ticket whose Znuny UI shows 10:06 must now also show 10:06 in Tiqora
  (Europe/Berlin browser), not 08:06.
- Confirm DB is UTC before assuming this fix: `OTRSTimeZone` effective value.

## How to inspect prod data (read-only)
No DB container in the prod compose; the DB is external (`mariadb`, db `otrs`).
Query from inside the app container which already holds `DATABASE_URL`:
```
ssh root@<docker-host-fqdn> \
  'cd /home/docker/tiqora && docker compose exec -T tiqora-api python3 -' < script.py
```
`script.py` parses `os.environ["DATABASE_URL"]` and uses `aiomysql`. Znuny 6.5:
MIME fields (`a_subject`, `a_from`, `a_message_id`, `a_in_reply_to`,
`a_references`) live in **`article_data_mime`** (joined on `article_id`), NOT in
`article` — `article` only has ids/`create_time`/sender/channel/visibility.

## Backfill of existing local-written rows (hard-won gotchas)
When correcting rows Tiqora wrote in local time back to UTC:
- **Articles are exact and safe:** `article.create_time := FROM_UNIXTIME(article_data_mime.incoming_time)`
  under a UTC session — `incoming_time` is an absolute epoch, so this is DST-correct
  with no guessing. Do NOT blanket `-2 HOUR`: the dataset spans winter (CET, −1h)
  and summer (CEST, −2h).
- **`ticket`/`ticket_history` have no `incoming_time` anchor.** Using "first article
  by id" as the UTC anchor is UNRELIABLE: a ticket's earliest article may be a note
  added days later, producing a huge bogus offset. Bound the detected offset to
  plausible tz shifts (≈3600s or ≈7200s only) and REJECT anything larger — a
  hundred-hour "offset" means the anchor is wrong, not that the row is local.
- **Beware false positives:** postmaster-created (`create_by=1`) tickets are Znuny/UTC
  and must be left alone; they get flagged only by the bad-anchor trap above.
- **A "no local rows remain" guard is NOT sufficient** to auto-commit: subtracting
  *any* offset trivially satisfies it, so a wrong correction still passes. Preview the
  actual before/after values and sanity-check the per-row offset magnitude BEFORE
  committing; prefer a human-reviewed preview over an in-script auto-commit for prod.
- Real 2026 scope seen in prod: 17 article rows + 6 genuine tickets (all July/summer);
  the whole pre-2024 "local" population is OTRS-era legacy, out of scope.

## Notes
- Offset sign is a quick diagnostic: times too *early* ⇒ UTC shown as local
  (this bug). Times too *late* would mean the opposite (local shown as UTC).
- Same class of bug can hide behind "why did ticket X appear so late" reports —
  reconcile the *shown* create_time against the true wall-clock event; a
  constant offset equal to the UTC offset points straight here.
- **The central fix (`UtcDateTime` in `domain/schemas.py`) only covers models that
  use it.** Route modules define their own response models with bare `datetime`
  (e.g. the time-accounting report in `api/v1/tickets.py` still showed UTC on
  2026-10-07). To find stragglers, walk `tiqora.api.v1` with the
  `_mentions_datetime`/`_has_utc_serializer` helpers from
  `tests/test_timezone_serialization.py` (skip `*Request`/`*Params`/`*In` request
  models). Fixed package-wide on 2026-10-07; `test_every_v1_response_model_serializes_utc`
  now enforces it, and `as_naive_utc()` in `domain/schemas.py` normalises filter bounds.
- Date-range filters have the same issue in reverse: naive local day bounds
  (`YYYY-MM-DDT00:00:00`) compared against UTC columns. Send
  `new Date(local).toISOString()` and normalise aware→naive UTC in the backend.
- Since 2026-10-07 the display zone is per agent, Znuny-style: frontend
  `lib/timeZone.ts` (`displayTimeZone()` = `me.time_zone` ?? browser), server text
  via `domain/timezones.resolve_user_time_zone` (UserTimeZone → UserDefaultTimeZone
  → OTRSTimeZone). New date code must use these, not `new Date().getDate()` /
  bare `Intl.DateTimeFormat` / naive strftime. Stats/search take `tz`.
- SLA calendars: DB timestamps are OTRSTimeZone; only the working-hours walk uses
  `TimeZone::Calendar<N>`. Prod runs Calendar1 = Europe/Berlin on all queues.
- See also: `tiqora-openapi-regen-or-ci-wipes-schema` (if you touch schemas.py,
  regenerate `packages/api-client/openapi.json` or CI's frontend build goes red).
