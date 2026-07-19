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

Real Znuny DDL has **no foreign key constraints**. Integrity is application-side.
Tiqora must:

- enforce referential integrity in domain code,
- tolerate and report orphans,
- only introduce real FKs after schema-ownership mode (Phase 5).

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

## Schema ownership (Phase 5)

When parallel operation ends:

1. Set a DB marker + config flag for ownership mode.
2. Enable `alembic/versions_owned/`.
3. Apply additive migrations only (FKs, helpful indexes, orphan reports).
4. Run the cutover runbook and keep a rollback probe ready.
