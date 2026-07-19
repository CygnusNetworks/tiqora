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

**Strategy:**

1. **Preferred:** small Perl OPM addon `TiqoraSync` (~150 lines): a daemon cron
   reads `tiqora_cache_invalidation` and clears affected ticket caches.
2. **Fallback:** document lowered Znuny cache TTLs for co-existence environments.

Proving the path: an admin creates a queue in Tiqora and it appears in Znuny
without restart (Phase 3 exit criterion).

## Detecting Znuny writes

Tiqora poller (Phase 1+):

| Watermark | Source | Use |
|---|---|---|
| `ticket_history.id` | append-only | Meilisearch + UI invalidation |
| `article.id` | append-only | Article index / SSE |
| Nightly `ticket.change_time` | reconcile | Catch missed updates |

SSE feeds TanStack Query invalidation and optional agent presence (Redis TTL
keys) once real-time work lands (Phase 2/3).

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

## Schema ownership (Phase 5)

When parallel operation ends:

1. Set a DB marker + config flag for ownership mode.
2. Enable `alembic/versions_owned/`.
3. Apply additive migrations only (FKs, helpful indexes, orphan reports).
4. Run the cutover runbook and keep a rollback probe ready.
