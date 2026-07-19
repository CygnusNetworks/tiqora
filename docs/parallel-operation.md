# Parallel operation with Znuny

Tiqora V1 is designed to run **alongside Znuny 6.5 on the same database**.
This document lists the behavioural invariants Tiqora must honour so that both
systems remain coherent.

## Ground rules

1. **Do not change Znuny table DDL** while parallel operation is active.
2. **New state lives only in `tiqora_*` tables** (Alembic chain `versions_tiqora/`).
3. **Writes to Znuny tables must be behaviourally identical** to what Znuny would
   produce (ticket numbers, history formats, escalation columns, flags).
4. **Daemon duties stay with Znuny** until a Tiqora feature flag takes them over
   (mail, escalation sweep, notifications, GenericAgent, auto-responses).
   Takeover is mutually exclusive and documented per function.
5. After cutover, an explicit **schema-ownership** mode unlocks
   `alembic/versions_owned/` for additive FKs, indexes, and orphan reports.

## Ticket number counter

Source reference (Znuny): `Kernel/System/Ticket/NumberBase.pm`.

Znuny uses a **lock-free** counter over the `ticket_number_counter` table:

1. `INSERT` a row with `counter = 0` (or equivalent seed).
2. Short settle (~50 ms) so concurrent writers observe the row.
3. **Idempotent fill-up**: update `WHERE counter = 0` to the next value.
4. Read back the allocated counter.
5. On collision, **retry** the sequence.

**Tiqora requirements:**

- Port the same algorithm (short autocommit transactions **per step**, never one
  long transaction holding locks across the whole allocation).
- Safe under concurrent Znuny writers — mixed TN concurrency tests are a Phase 2
  exit criterion.
- Number module format (DateChecksum, AutoIncrement, …) must match the
  configured Znuny `Ticket::NumberGenerator` setting.

## Ticket history formats

Source: `Kernel/System/Ticket.pm`.

Znuny stores human- and machine-parsed strings in `ticket_history` using
`%%…%%…` name formats (merge chains, first response, state changes, etc.).
Znuny parsers depend on exact shape.

**Tiqora requirements:**

- Centralise format strings in `znuny/history.py`.
- Never invent ad-hoc history names.
- Regression tests compare history rows against a golden Znuny write for the
  same logical action.

**Golden-master validated (2026-07-19)** against a real Znuny 6.5.22 container
(`tests/golden/test_history_diff.py`): the full create → state → move →
priority → owner → note → close lifecycle now produces byte-identical
normalized history rows. Four divergences were found and fixed in the process
(CustomerUpdate row on TicketCreate, SetPendingTime `%%00-00-00 00:00` reset on
every non-pending state change, TicketOwnerSet same-owner no-op without
auto-lock, and the Misc "Reset of unlock time." row on agent article
creation). Ticket-number counter interleaving, DateChecksum checksum digits,
and escalation columns (incl. zero-on-close) are golden-validated too — see
docs/testing.md.

## Escalation recompute

Source: `Ticket.pm` → `TicketEscalationIndexBuild`.

Tickets carry **four epoch-integer columns** derived from SLA, queue, and the
working-time calendar. Znuny recomputes them synchronously on relevant writes.
The daemon task `RebuildEscalationIndexOnline` is a safety net only.

**Tiqora requirements:**

- On every domain write that can affect escalations, recompute the same four
  columns with equivalent calendar math.
- Do not rely on the Znuny daemon to “fix” Tiqora writes during parallel
  operation (daemon remains a safety net, not the primary path).

## Search index flags

- Set `article.search_index_needs_rebuild = 1` after article changes.
- Znuny’s daemon builds `article_search_index` until Tiqora owns indexing.
- `ticket_index` matters only when `Ticket::IndexModule = StaticDB`; recommend
  RuntimeDB for parallel deployments.

Tiqora additionally maintains Meilisearch via its own poller/outbox path; that
index is independent of Znuny’s full-text tables.

## Follow-up detection

- Ticket number regex in subject lines.
- `a_message_id_md5` and References-header chains.
- Merge-chain walk up to depth 10.

These rules must match Znuny so that inbound mail processed by either side
threads correctly.

## Foreign keys and orphans

Znuny’s **base** schema (`schema.*.sql`) declares tables without foreign keys.
The installer then applies **`schema-post.*.sql` after `initial_insert`**, which
**does** add FK constraints (including the circular `users`↔`valid` pair that
only works in that order). Fresh Znuny installs therefore **have** FK
constraints.

Older upgraded installs may still miss some constraints. Tiqora must:

- write in FK-safe order (seed/reference rows before dependents; reuse
  `users` id 1 / `valid` id 1 from initial_insert when testing),
- enforce referential integrity in domain code,
- tolerate and report orphans on legacy databases that lack constraints,
- only own/alter FKs after schema-ownership mode (Phase 5).

## Znuny cache invalidation

Direct SQL writes by Tiqora are invisible to Znuny’s in-process cache until TTL
expiry or explicit invalidation.

**Implemented:** the Perl OPM addon `TiqoraSync`
([packages/znuny-addon/TiqoraSync/](../packages/znuny-addon/TiqoraSync/)) is
installed into the co-running Znuny instance
(`Admin::Package::Install`, see its `install/README.md`). A daemon cron task
(`Daemon::SchedulerCronTaskManager::Task###TiqoraSync`, every minute — Znuny's
scheduler supports 5-field cron only, so worst-case staleness is ~60 s) invokes
`Kernel::System::TiqoraSync::Run()`, which:

1. reads the watermark from `tiqora_settings` key `tiqorasync.watermark`,
2. selects up to 500 rows from `tiqora_cache_invalidation` above the watermark
   (rows are written by Tiqora in the same transaction as each ticket write),
3. deletes the `Cache::GetTicket<ID>` entries (Cache type `Ticket`, mirroring
   Znuny's `_TicketCacheClear`) for each affected ticket plus a coarse
   `CleanUp(Type => 'Ticket')` for list/count caches,
4. advances the watermark.

The module is defensive: if the `tiqora_*` tables do not exist yet (Znuny
started before Tiqora's migrations), it logs at debug level and returns
cleanly — it never dies inside the daemon.

**Fallback:** lowered Znuny cache TTLs remain a documented option for
environments where installing the addon is not possible.

Proving the path: an admin creates a queue in Tiqora and it appears in Znuny
without restart (Phase 3 exit criterion).

## Detecting Znuny writes

Tiqora poller (Phase 1+):

| Watermark | Source | Use |
|---|---|---|
| `ticket_history.id` | append-only | Meilisearch + UI invalidation |
| `article.id` | append-only | Article index / SSE |
| Nightly `ticket.change_time` | reconcile | Catch missed updates |

SSE is implemented: the outbox drain and the Znuny-write poller publish
ticket-change notifications on the Redis pub/sub channel `tiqora:events`;
`GET /api/v1/events/stream` forwards them to authenticated agents, feeding
TanStack Query invalidation. Agent presence (viewing/composing) uses 30 s
Redis TTL keys (`tiqora:presence:<ticket_id>:<user_id>`) surfaced on the
ticket zoom.

## Feature-flag daemon takeover (Phase 4)

Each of the following moves to Tiqora independently:

| Function | Znuny owner until flag | Tiqora worker job |
|---|---|---|
| Postmaster / inbound mail | Znuny daemon | `worker` postmaster pipeline |
| Escalation sweep | RebuildEscalationIndexOnline | escalation job |
| Notifications | NotificationEvent | notification engine |
| GenericAgent | GenericAgent | GA executor |
| Auto-responses | AutoResponse | auto-response job |

Flags must be **mutually exclusive** with the Znuny side for each function to
avoid double-send or double-escalation.

## Taking over mail processing (Phase 4a)

Tiqora's postmaster pipeline (`tiqora.worker.postmaster`, `tiqora.channels.email.*`)
is a behavioural port of `Kernel/System/PostMaster.pm` and friends: mail
fetch (IMAP/IMAPS/POP3/POP3S), `postmaster_filter` application, X-OTRS
pseudo-header handling for trusted accounts, follow-up detection (subject TN
regex, then References/In-Reply-To), new-ticket / follow-up / reject
dispatch, loop protection, and auto-responses.

**It is OFF by default** and **mutually exclusive** with Znuny's own mail
fetch. Both sides polling the same mailbox will double-process (and, for
POP3/IMAP delete-after-fetch semantics, race to delete) messages.

### Enabling the takeover

1. Disable Znuny's own scheduler task so it stops fetching mail. Either:
   - Console: `bin/otrs.Console.pl Admin::Config::Update --setting-name "Daemon::SchedulerCronTaskManager::Task###MailAccountFetch" --value '{}'`
     (or set it invalid via SysConfig UI → *Daemon → SchedulerCronTaskManager*
     → `MailAccountFetch`), or
   - Stop/disable the Znuny daemon process entirely if nothing else on it is
     needed yet.
2. Set the Tiqora feature flag (a `tiqora_settings` row, not an env var —
   toggle at runtime without restarting the worker):
   ```sql
   INSERT INTO tiqora_settings (key, value) VALUES ('daemon.postmaster.enabled', '1')
     ON CONFLICT (key) DO UPDATE SET value = '1';  -- Postgres
   -- MySQL/MariaDB: INSERT ... ON DUPLICATE KEY UPDATE value = '1';
   ```
3. Tiqora's worker process polls this flag every tick (default 60s,
   `TIQORA_POSTMASTER_INTERVAL`) — no restart required to flip it.
4. Verify: send a test mail, confirm exactly one ticket is created (not
   duplicated by Znuny) and that Znuny's `SchedulerFutureTaskList` /
   `SchedulerTaskLog` show no further `MailAccountFetch` executions.

### Rollback

1. Set `daemon.postmaster.enabled` back to `0` (or delete the row — default
   is OFF).
2. Re-enable the Znuny `MailAccountFetch` cron task (reverse step 1 above).
3. Any mail left in mailboxes when Tiqora had `leave_on_server=1` set will be
   picked up by Znuny's fetch on the next cycle; verify no duplicates from
   messages Tiqora already processed but did not delete.

### Safety flags

- `daemon.postmaster.enabled` (`tiqora_settings`, default unset = OFF) — the
  takeover switch.
- `daemon.postmaster.leave_on_server` (`tiqora_settings`, default unset =
  OFF) — Znuny always deletes fetched mail; Tiqora replicates that by
  default. Set to `1` only for testing against a mailbox nothing else reads,
  never against a mailbox Znuny's daemon also polls.

## Uncertainties (Phase 4a — postmaster)

Documented simplifications vs. exact Znuny behaviour, in descending order of
likely impact:

- **Mail fetch transport**: blocking stdlib `imaplib`/`poplib` wrapped in
  `asyncio.to_thread`, not an async IMAP client or `Mail::IMAPClient`
  feature-for-feature port. OAuth2 mail account authentication
  (`authentication_type = oauth2_token`) is **not implemented** — only
  `password` auth. StartTLS is not offered for plain IMAP/POP3 (only the
  implicit-TLS `IMAPS`/`POP3S` variants); Znuny supports both SSL and StartTLS
  options per account.
- **HTML→text conversion**: Znuny's `PostmasterAutoHTML2Text` uses a proper
  HTML-to-text converter; Tiqora's fallback (`channels/email/parser.py`) is a
  regex tag-strip. Formatting fidelity (lists, tables, links) is lost.
- **Customer resolution**: Znuny's `CustomerSearch(PostMasterSearch => ...)`
  can scan multiple configured customer_user fields; Tiqora only matches
  `login`/`email` case-insensitively against the sender address.
- **CheckFollowUpModule chain**: only `Subject` and `References` are
  implemented (Znuny's own default-active chain). `Body`, `Attachments`,
  `RawEmail`, and `ExternalTicketNumberRecognition` follow-up checks are
  **not implemented** (they are registered but `Valid="0"` in a stock Znuny
  install too, so this matches an out-of-the-box deployment, not a
  customised one).
- **X-OTRS field coverage**: `Queue`, `State`, `Priority`, `Type`, `Owner`,
  `OwnerID`, `Responsible`, `ResponsibleID`, `SenderType`, `CustomerNo`,
  `CustomerUser`, `Title`, `Ignore`, `IsVisibleForCustomer`,
  `FollowUp-State`, `Loop` are honoured. **Not implemented**: `Service`,
  `SLA`, `State-PendingTime`, `FollowUp-Lock`, `DynamicField-*`, and the
  legacy `TicketKey`/`TicketValue`/`TicketTime` free-text/free-time
  back-compat headers.
- **`<OTRS_...>` placeholder tags**: only `TICKET_*`, `QUEUE`,
  `CUSTOMER_SUBJECT`, `CUSTOMER_EMAIL[n]`, `CONFIG_*` are expanded
  (`channels/email/placeholder.py`). `OTRS_AGENT_*`, `OTRS_CUSTOMER_BODY`,
  `OTRS_CUSTOMER_DATA_*`, and DynamicField tags are left verbatim in the
  rendered text.
- **Follow-up reject**: on `follow_up_id = 'reject'` for a closed ticket,
  Tiqora sends the `auto reject` response but — unlike a full port of
  `Kernel::System::PostMaster::Reject` — does **not** attach the rejected
  mail as an article anywhere, so the content is not retained for audit past
  the structured log line.
- **Linking new-ticket-on-closed to the original ticket**: Znuny's
  `NewTicket->Run(LinkToTicketID => ...)` creates a `LinkObject` Normal link
  between the closed ticket and the new one; Tiqora does not create this
  link yet.
- **Attachment size / `PostMasterReconnectMessage` reconnect batching**: not
  implemented — Tiqora fetches an account's whole mailbox in one pass rather
  than reconnecting every N messages.

## Taking over escalation index rebuild (Phase 4b)

Tiqora's escalation sweep (`tiqora.worker.escalation`, math in
`tiqora.znuny.escalation`) is a behavioural port of
`Kernel::System::Console::Command::Maint::Ticket::EscalationIndexRebuild`
(scheduled via `Daemon::SchedulerCronTaskManager::Task###EscalationCheck`)
plus the `TriggerEscalationStartEvents` semantics: it batches open
(non-`merge`/`close`/`remove` state-type) tickets, recomputes the four
`ticket.escalation_*` columns, and fires
`Escalation{ResponseTime,UpdateTime,SolutionTime}{Start,Stop,NotifyBefore}`
`ticket_history` rows + `tiqora_event_outbox` events on state transitions.

**It is OFF by default** and **mutually exclusive** with Znuny's own
`EscalationCheck` scheduler task. Both sides recomputing the same columns is
harmless *by itself* (the math is deterministic), but both sides firing
Start/Stop/NotifyBefore events independently will double the ticket_history
rows and duplicate any downstream notification.

### Enabling the takeover

1. Disable Znuny's `EscalationCheck` daemon task (SysConfig UI → *Daemon →
   SchedulerCronTaskManager* → `EscalationCheck`, or set it invalid via
   `Admin::Config::Update`).
2. Set `daemon.escalation.enabled = 1` in `tiqora_settings` (see the SQL
   pattern in "Taking over mail processing" above).
3. The worker polls this flag every tick (default 300s,
   `TIQORA_ESCALATION_INTERVAL`).
4. Verify: a ticket crossing its escalation time gets exactly one
   `EscalationResponseTimeStart`-type history row, not one from each side.

### Rollback

1. Set `daemon.escalation.enabled` back to `0`.
2. Re-enable Znuny's `EscalationCheck` task. Znuny's own rebuild will
   re-converge the columns on its next run (the math is deterministic and
   idempotent either way).

### Safety flags

- `daemon.escalation.enabled` (`tiqora_settings`, default unset = OFF).
- `daemon.escalation.batch_size` (`tiqora_settings`, default 500) — tickets
  swept per tick.
- `daemon.escalation.notify_before_seconds` (`tiqora_settings`, default
  86400) — window before a destination time in which a one-shot
  `*NotifyBefore` event fires; a simplified, fixed-window substitute for
  Znuny's per-SLA/queue notify-before percentage (see Uncertainties below).

## Taking over event notifications (Phase 4b)

Tiqora's notification engine (`tiqora.worker.notifications`) is a
behavioural port of `Kernel::System::Ticket::Event::NotificationEvent` (+ its
`::Transport::Email` backend): it reads `notification_event` /
`notification_event_item` / `notification_event_message`, matches them
against `tiqora_event_outbox` rows via a monotonic per-event watermark
(loop-safe — each outbox row is consumed exactly once, ever), resolves
recipients (`AgentOwner`/`AgentResponsible`/`RecipientAgents`/
`RecipientGroups`/`Customer`), evaluates ticket-attribute and `ArticleFilter`
matching, renders the subject/body with the shared `<OTRS_...>` placeholder
module (`tiqora.channels.email.placeholder`, reused from the Phase 4a
postmaster auto-response path rather than duplicated), and sends via SMTP.

**It is OFF by default** and **mutually exclusive** with Znuny's own event
handler chain (which calls `NotificationEvent` synchronously on every
ticket/article write). Both sides active means every matching notification
is sent twice.

### Enabling the takeover

1. Disable Znuny's `NotificationEvent` ticket event handler
   (`Ticket::EventModulePost###900-NotificationEvent` → invalid in
   SysConfig), or accept that Tiqora only needs to win the race for writes
   it itself performs (interactive Znuny GUI edits still trigger Znuny's own
   handler) — **recommended**: disable it, since Tiqora's outbox sees writes
   from *both* Znuny and Tiqora, so leaving Znuny's handler on double-sends
   for every write regardless of origin.
2. Set `daemon.notifications.enabled = 1` in `tiqora_settings`.
3. The worker polls this flag every tick (default 60s,
   `TIQORA_NOTIFICATIONS_INTERVAL`).
4. Verify: a matching event produces exactly one email/article per
   recipient, with a `SendAgentNotification`/`SendCustomerNotification`
   history row.

### Rollback

1. Set `daemon.notifications.enabled` back to `0`.
2. Re-enable Znuny's `NotificationEvent` event handler.
3. The watermark (`daemon.notifications.outbox_watermark` in
   `tiqora_settings`) is left in place; re-enabling later resumes from where
   it left off rather than replaying old events.

### Safety flags

- `daemon.notifications.enabled` (`tiqora_settings`, default unset = OFF).

## Taking over GenericAgent (Phase 4b)

Tiqora's GenericAgent executor (`tiqora.worker.generic_agent`) is a
behavioural port of a pragmatic subset of `Kernel::System::GenericAgent`
(`JobGet`/`JobRun`/`_JobRunTicket`): it reads `generic_agent_jobs`, matches
tickets over a supported criteria subset (`StateIDs`/`QueueIDs`/
`PriorityIDs`/`OwnerIDs`/`LockIDs`/`TypeIDs`, `Title`/`CustomerID` LIKE, a
`Ticket*Time*Older/NewerMinutes` range subset), and applies `New*` actions
through `domain.ticket_write_service` (so every Znuny invariant — history,
ticket_index, escalation recompute, cache invalidation, outbox event — fires
exactly as for an interactive edit).

**It is OFF by default** and **mutually exclusive** with Znuny's own
`GenericAgent` scheduler task. Both sides running the same job doubles every
action (double note, double state-flap history, etc.).

### Enabling the takeover

1. Disable Znuny's `GenericAgent` daemon task (SysConfig UI → *Daemon →
   SchedulerCronTaskManager* → `GenericAgent`).
2. Set `daemon.generic_agent.enabled = 1` in `tiqora_settings`.
3. The worker polls this flag every tick (default 60s,
   `TIQORA_GENERIC_AGENT_INTERVAL`) and evaluates each valid job's
   `ScheduleDays`/`Hours`/`Minutes` against the current time every tick.
4. `NewDelete` jobs stay inert (matched but not acted on, logged as
   `generic_agent_delete_blocked`) until `daemon.generic_agent.allow_delete`
   is also set — enable only after confirming the job's search criteria are
   correct against a read-only run.
5. Verify: a job's matched tickets get acted on exactly once per tick, not
   once from each side.

### Rollback

1. Set `daemon.generic_agent.enabled` back to `0`.
2. Re-enable Znuny's `GenericAgent` daemon task.

### Safety flags

- `daemon.generic_agent.enabled` (`tiqora_settings`, default unset = OFF).
- `daemon.generic_agent.allow_delete` (`tiqora_settings`, default unset =
  OFF) — required in addition to the takeover flag before any job's
  `NewDelete` action actually deletes a ticket.

## Uncertainties (Phase 4b — escalation, notifications, GenericAgent)

Documented simplifications vs. exact Znuny behaviour, in descending order of
likely impact:

- **Escalation NotifyBefore window**: Znuny derives the notify-before point
  per SLA/queue from configured percentages
  (`{FirstResponse,Update,Solution}Notify`); Tiqora uses one fixed window
  (`daemon.escalation.notify_before_seconds`, default 24h) across all
  tickets regardless of SLA/queue configuration.
- **Escalation sweep ordering**: batches tickets by `change_time ASC` to
  guarantee eventual coverage under a batch-size cap; Znuny's console command
  processes an unordered full-table scan every invocation (no batching).
- **Notification transports**: only `Email` is implemented. Znuny's
  `SMS`/custom transports and its `Notification::Transport` config table are
  not consulted.
- **Notification recipient preferences**: Znuny checks each user's
  `Notification-<id>-<Transport>` preference before sending (agents can
  individually opt out); Tiqora sends to every matched recipient
  unconditionally.
- **Notification recipients**: `AgentMyQueues`/`AgentMyServices`/
  `AgentWatcher`/`AgentWritePermissions`/`AgentCreateBy` are **not
  implemented** (they require queue-subscription/ticket-watcher/permission
  joins not yet modelled in the notification engine). `RecipientGroups`
  resolves direct `group_user` members only — no role→group expansion via
  `PermissionGroupRoleGet`.
- **Notification history divergence**: Znuny writes no `ticket_history` row
  for agent email notifications (only customer notifications, via the
  article backend); Tiqora deliberately writes a `SendAgentNotification` row
  for both, for auditability. This is an intentional divergence, not a bug —
  golden-master history-row comparisons for agent notifications will differ.
- **GenericAgent time criteria**: only the `Older/NewerMinutes` variant of
  each supported `Ticket*Time*` key is implemented; Znuny's `TimeSlot`
  (absolute date range) and `TimePoint` (relative unit) UI variants, and
  dynamic-field search criteria, are **not implemented**.
- **GenericAgent recipient/notification interplay**: jobs with
  `SendNoNotification` are not distinguished — Tiqora's actions always run
  through the normal `ticket_write_service` path, so any *separately
  enabled* Tiqora notification engine will see the resulting events like any
  other write. Disable the notification engine (or scope its
  `notification_event` rows away from GenericAgent-driven states/queues) if
  a job must stay silent.
- **GenericAgent delete port**: `_delete_ticket` is a best-effort ordered
  delete of `article_data_mime`/`article`/`ticket_history`/
  `dynamic_field_value`/`ticket` rows, not a full port of
  `Ticket.pm::TicketDelete` (which also clears the search index, cache,
  links, and other tables). Links, cache, and the search index are left to
  the normal outbox/reindex path (a deleted ticket's stale search entry is
  removed on the next full reindex, not immediately).

## Schema ownership (Phase 5)

When parallel operation ends:

1. Set a DB marker + config flag for ownership mode.
2. Enable `alembic/versions_owned/`.
3. Apply additive migrations only (FKs, helpful indexes, orphan reports).
4. Run the cutover runbook and keep a rollback probe ready.
