# Complete daemon takeover implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the remaining background duties needed to retire a Znuny application, and execute the authorized production cutover after verification.

**Architecture:** Add independently gated unlock-timeout and pending-check workers using existing ticket mutation, calendar, notification outbox, and worker status infrastructure. Fix empty GenericAgent criteria at the source. Preserve shared database and external import processes during application retirement.

**Tech Stack:** Python, async SQLAlchemy, MariaDB/PostgreSQL, pytest, Docker Compose.

**Spec:** User request in this conversation: implement all findings from the production audit, excluding API and URL changes; cutover documentation must contain only generally applicable guidance.

## Global Constraints

- No API or URL changes.
- `docs/cutover.md` contains only generally applicable guidance, no customer-specific hostnames, imports, URLs or counts.
- Legacy worker duties are OFF by default and must be mutually exclusive with Znuny counterparts.
- Preserve Znuny-compatible schema, history, events, configured calendars, and customer data.
- Do not send external test messages; test mail against a controlled sink.
- Do not remove the shared database, its volumes, or independent customer import.

## Task 1: Missing workers and GenericAgent correctness

**Files:** Add `backend/src/tiqora/worker/unlock_timeout.py`, `backend/src/tiqora/worker/pending_check.py`, and behavioral tests. Modify worker registration, settings, admin service catalog, frontend service labels where necessary, and `backend/src/tiqora/worker/generic_agent.py`.

**Interfaces:** Follow `run_escalation_tick(*, settings=None, session_factory=None)` and existing loop/status catalog patterns. Consume existing `SysConfig`, calendar working-time helpers, ticket mutation services, and notification outbox. Produce flags `daemon.unlock_timeout.enabled` and `daemon.pending_check.enabled` with configurable intervals.

- [ ] Reproduce empty GenericAgent Title/CustomerID values with a failing regression using a real query and nonempty ticket data. Also retain active nonempty filters.
- [ ] Ignore truly empty optional search values so they do not become `LIKE ''`; preserve whitespace and wildcard semantics unless Znuny says otherwise.
- [ ] Read full Znuny `Maint/Ticket/UnlockTimeout.pm` and `PendingCheck.pm` plus called state/calendar semantics. Write failing behavioral tests for timeout based on working time, SLA calendar precedence, disabled flags, ineligible state/lock, and repeat-run idempotence.
- [ ] Implement unlock timeout using configured unlock state types and viewable locks, queue timeout minutes, ticket timeout epoch, existing ticket calendar resolution and unlock mutation. Recheck eligibility under row lock before writes; preserve transactional history/outbox.
- [ ] Write failing tests for due pending auto transitions, closed-state unlock, future pending exclusion, configured mappings, due reminder event during business time, cadence/deduplication and disabled flags.
- [ ] Implement pending checker using existing state mutation, `NotificationPendingReminder` outbox event and business calendar. Ensure notifications consume this event correctly without fabricating unrelated history types. Prevent repeat reminders within the intended cadence and preserve retryability.
- [ ] Register worker settings, loops and admin services, and provide translated labels for new services using existing locale conventions.
- [ ] Run focused tests including MariaDB/PostgreSQL as supported by existing testcontainers fixtures, lint changed files, and commit only Task 1 code/tests.

## Task 2: Generic operator documentation and production preflight

**Files:** `docs/cutover.md`, `docs/parallel-operation.md` and deployment instructions only where needed.

- [ ] Extend generic checklist with pending/unlock takeovers, both GenericAgent scheduling paths, mail queue/spool drain, conditional calendar and addon tasks, persistent data and independent import preservation, restore verification, daemon autorestart prevention, and primary-mode AI side effects.
- [ ] Make API routing an applicable-only stage; respect deployment exclusions without site-specific text.
- [ ] Inspect production backup facilities, image rollout, service dependencies, all remaining scheduler tasks and external writers. Record site-specific execution evidence outside generic docs.
- [ ] Obtain verified restorable backup before application cutover. Test restore into an isolated database instance and preserve backup securely.

## Task 3: Deploy and finish authorized cutover

- [ ] Independently review Task 1 against upstream behavior and tests; fix findings before deployment.
- [ ] Run applicable backend, frontend and compatibility checks; build an identifiable deployment image and preserve rollback image/configuration.
- [ ] Deploy tested code, then disable Znuny pending/unlock and GenericAgent scheduling before enabling Tiqora equivalents. Verify successful ticks and no errors.
- [ ] Verify mail behavior in an isolated fixture/sink and empty production legacy queue/spool. Preserve database/import infrastructure and stop only Znuny application services, with restart policy/configuration preventing automatic revival.
- [ ] Set primary operation mode only with explicit consideration of already enabled AI automation; verify health and runtime state after change.
- [ ] Run ownership preflight after Znuny is stopped; enable both gates and owned migrations only after verified backup and checked external-writer compatibility. Monitor relevant health and restore rollback if checks fail.
- [ ] Report actual delivered changes, tests, production state, backup location, and any unresolved external limitation without claiming unchecked outcomes.
