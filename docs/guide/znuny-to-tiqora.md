# Operator playbook: migrating from Znuny to Tiqora

This is a generic, stage-by-stage playbook for taking a site from "Znuny
only" to "Tiqora only", with no downtime beyond a single planned cutover
window. It ties together three more detailed reference documents rather
than duplicating them:

- [`../parallel-operation.md`](../parallel-operation.md) — the behavioural
  invariants Tiqora maintains while both systems share a database, and the
  per-function daemon-takeover procedures (enable/verify/rollback).
- [`../cutover.md`](../cutover.md) — the detailed, checklist-driven runbook
  for the final cutover stages (freeze Znuny, stop its daemon, repoint
  GenericInterface traffic, enable schema ownership).
- [`../deploy/docker-compose.md`](../deploy/docker-compose.md) — how to
  actually deploy the `tiqora-api`/`tiqora-worker`/`tiqora-mcp` containers.

Read all three before starting a real migration. This page is the map; they
are the territory.

## The core idea

Tiqora is designed to run **on the same database as an existing Znuny 6.5
install**, adding only new `tiqora_*` tables. Znuny keeps owning its own
schema (`ticket`, `article`, `queue`, `sessions`, …) throughout — Tiqora
reads and, once verified, writes to those tables using logic ported to be
behaviourally identical to Znuny's own (ticket number allocation, history
row formats, escalation column recompute — see
[`../parallel-operation.md`](../parallel-operation.md) for the details and
golden-master validation evidence). Only at the very end, after Znuny is
fully shut down, does Tiqora take **ownership** of the schema and start
applying its own additive migrations (indexes, FKs).

This lets you run both systems side by side for as long as you need to
build confidence, with a well-defined, reversible rollback at every stage
short of the final one.

```
Stage 0            Stage 1              Stage 2              Stage 3                Stage 4
Backup      ──▶  Deploy Tiqora   ──▶  Install         ──▶  Take over daemon  ──▶  Cutover
                 read-only            TiqoraSync            duties one by one       (docs/cutover.md)
                 alongside Znuny      (cache coherence)     (docs/parallel-
                                      + enable writes        operation.md)
```

---

## Stage 0 — Prerequisites and backup

Before touching anything:

- [ ] A recent, **verified-restorable** database dump exists. Every later
      rollback in this playbook that isn't a pure config revert assumes this
      dump is current and its restore procedure has actually been tested
      (not just "we ran `mysqldump`").
- [ ] You have credentials to create a **dedicated, least-privilege DB user**
      for Tiqora (see Stage 1 — do not reuse Znuny's own DB user).
- [ ] You have a place to run `tiqora-api`, `tiqora-worker`, and
      `tiqora-mcp` (see [`../deploy/docker-compose.md`](../deploy/docker-compose.md)).
- [ ] A Meilisearch instance is available for the search index (bundled in
      the example Compose file).

### Taking the backup

**MariaDB / MySQL** (single-transaction avoids locking a live Znuny for the
duration of the dump):

```sh
mysqldump \
  --single-transaction \
  --routines --triggers \
  -h db.example.internal -u backup_user -p \
  znuny_production > znuny_pre_tiqora_$(date +%Y%m%d).sql

# Verify: the dump is non-trivially sized and round-trips into a scratch DB
mysql -h db.example.internal -u root -p -e "CREATE DATABASE znuny_restore_test"
mysql -h db.example.internal -u root -p znuny_restore_test < znuny_pre_tiqora_$(date +%Y%m%d).sql
```

**PostgreSQL**:

```sh
pg_dump \
  --format=custom \
  -h db.example.internal -U backup_user \
  znuny_production > znuny_pre_tiqora_$(date +%Y%m%d).dump

# Verify: restore into a scratch database
createdb znuny_restore_test
pg_restore -d znuny_restore_test znuny_pre_tiqora_$(date +%Y%m%d).dump
```

Keep this dump until well past a successful cutover (Stage 4) — it is the
only rollback path once schema-ownership migrations have been applied (see
[`../cutover.md`](../cutover.md#stage-5--enable-schema-ownership)).

---

## Stage 1 — Deploy Tiqora read-only alongside Znuny

**Goal**: get Tiqora running against the live database, writes still fully
disabled, so agents can start evaluating the UI without any risk to Znuny.

1. Create a dedicated, narrowly-scoped DB user for Tiqora (`GRANT SELECT,
   INSERT, UPDATE, DELETE ON znuny_production.* TO 'tiqora'@'%'` — Tiqora
   needs write access to its own `tiqora_*` tables plus the Znuny tables it
   will *eventually* write to once you enable writes, but nothing beyond the
   existing Znuny schema; do not grant `DROP`/`ALTER` at this stage).
2. Point `DATABASE_URL` at the **existing** Znuny database (see
   [`../deploy/docker-compose.md`](../deploy/docker-compose.md#connecting-to-an-existing-znuny-database)
   for the exact env var and connection-string forms for MariaDB vs
   PostgreSQL).
3. Run the migration command — this creates **only** `tiqora_*` tables; it
   never touches Znuny's own schema at this stage (schema ownership, which
   gates a separate additive migration chain, is off by default):
   ```sh
   tiqora migrate upgrade
   ```
4. Build the initial Meilisearch index:
   ```sh
   tiqora index rebuild
   ```
5. Start `tiqora-api` and `tiqora-worker` with `TIQORA_SCHEMA_OWNERSHIP`
   unset (or `false`) — this is the default and must stay this way for the
   whole parallel-operation period.
6. Point a subset of agents at the Tiqora UI to evaluate it. At this stage
   Tiqora is read-write-*capable* at the application layer but you should
   treat it as read-only in practice until you are confident in it — nothing
   technical stops an agent from writing through the Tiqora UI at this
   point, so communicate the evaluation boundary clearly to the pilot group.

**Rollback**: stop the Tiqora containers. Nothing in Znuny changed; the only
artifacts left behind are the (harmless, additive) `tiqora_*` tables, which
can be dropped or simply ignored.

---

## Stage 2 — Install TiqoraSync, enable writes

**Goal**: keep Znuny's in-process cache coherent once Tiqora starts writing
directly to Znuny's tables, then actually enable those writes.

1. Install the `TiqoraSync` Znuny addon (OPM package,
   `packages/znuny-addon/TiqoraSync/` in this repository) into the running
   Znuny instance:
   ```sh
   bin/znuny.Console.pl Admin::Package::Install /path/to/TiqoraSync.opm
   ```
   This adds a Znuny scheduler cron task that invalidates Znuny's ticket
   cache for rows Tiqora has written, so agents using the Znuny UI don't see
   stale data. See
   [`../parallel-operation.md`](../parallel-operation.md#znuny-cache-invalidation)
   for exactly how this works and its ~60s worst-case staleness bound. If
   installing the addon isn't possible in your environment, lowering
   Znuny's cache TTLs is a documented fallback.
2. Verify the sync path: create or edit a queue in Tiqora and confirm it
   appears in the Znuny UI without a restart.
3. Enable Tiqora writes for real (agents now actively work tickets through
   Tiqora, not just evaluate it read-only). There is no single global
   "writes on" switch — this is a policy/communication step: tell the pilot
   group Tiqora is now the primary UI for their queues.

**Rollback**: uninstall `TiqoraSync`
(`Admin::Package::Uninstall TiqoraSync`) and revert to Znuny-only for those
agents/queues. Any Tiqora writes already made remain valid Znuny-schema data
(they were written in Znuny-compatible form) — no data repair is needed,
you're only changing which UI agents use.

---

## Stage 3 — Take over daemon functions, one at a time

**Goal**: move background responsibilities (mail fetch, escalation sweep,
notifications, GenericAgent) from Znuny's daemon to Tiqora's worker, in a
controlled order, each individually verified and reversible.

Each function is gated by its own `daemon.<name>.enabled` key in
`tiqora_settings` (default OFF), and is designed to be **mutually
exclusive** with the corresponding Znuny daemon task — running both sides
active for the same function causes double-processing (duplicate tickets,
duplicate notifications, etc.), so always disable the Znuny side *before*
flipping the Tiqora flag on.

Recommended order (each stage's detailed enable/verify/rollback procedure is
in [`../parallel-operation.md`](../parallel-operation.md)):

1. **Escalation sweep** — lowest blast radius; the math is deterministic and
   idempotent, so even a brief overlap during the flag flip just means a
   value gets recomputed twice, not corrupted.
   → [`../parallel-operation.md`](../parallel-operation.md#taking-over-escalation-index-rebuild)
2. **Notifications** — verify no double-send before moving on.
   → [`../parallel-operation.md`](../parallel-operation.md#taking-over-event-notifications)
3. **GenericAgent** — verify job matches against a read-only run before
   enabling `daemon.generic_agent.allow_delete`.
   → [`../parallel-operation.md`](../parallel-operation.md#taking-over-genericagent)
4. **Postmaster (inbound mail) — last.** This is the highest-risk takeover:
   getting it wrong risks duplicate-processed or (for POP3/IMAP
   delete-after-fetch) lost mail.
   → [`../parallel-operation.md`](../parallel-operation.md#taking-over-mail-processing)

   **OAuth2-mail caveat**: Tiqora's postmaster pipeline only implements
   `password` mail-account authentication. If any mail account uses OAuth2
   token auth, it **cannot** be taken over as-is — leave that specific
   account on Znuny's daemon (or add OAuth2 support before migrating it) and
   take over only the password-authenticated accounts.

For each function: disable the Znuny scheduler task, set the Tiqora flag,
wait at least one poll interval, verify via the documented metric/behaviour,
and only then move to the next function. Do not enable two functions
simultaneously on a first migration — verify each independently.

**Rollback per function**: set the `daemon.<name>.enabled` flag back to
`0` and re-enable the corresponding Znuny scheduler task. See each
function's own "Rollback" subsection in `parallel-operation.md` for
function-specific caveats (e.g. mail left on the server, notification
watermark preservation).

---

## Stage 4 — Cutover

**Goal**: end parallel operation. Freeze Znuny, repoint any remaining
GenericInterface integrations at `/znuny-compat`, and — once confident —
enable schema ownership so Tiqora can apply its own additive migrations.

This stage is fully specified, stage-by-stage with its own rollback per
stage, in [`../cutover.md`](../cutover.md). Summary of what it covers:

1. **Freeze Znuny web** — block new logins/writes via the Znuny frontend
   (nginx maintenance response), Tiqora's UI stays up.
2. **Verify daemon-flag takeover is complete** — every `daemon.*.enabled`
   flag from Stage 3 above is `1`; stop the Znuny daemon process itself.
3. **Repoint GenericInterface integrations** — reverse-proxy rewrite from
   Znuny's `nph-genericinterface.pl` to Tiqora's `/znuny-compat` (see
   [`../api/compat.md`](../api/compat.md) for the compat layer itself).
4. **Monitor** the golden-signal metrics for the rest of the maintenance
   window before proceeding.
5. **Enable schema ownership** — the gated CLI (`tiqora ownership status` /
   `tiqora ownership enable --confirm "..."`), which runs preflight checks
   (idle `ticket_history` watermark, empty `sessions` table) before setting
   the DB marker, then the `TIQORA_SCHEMA_OWNERSHIP` env flag unlocks the
   `alembic/versions_owned/` migration chain (additive indexes/FKs only).
   **This is the point of no return** — see `cutover.md` for why rollback
   beyond this point requires restoring the Stage 0 dump, not a config
   revert.
6. **Post-cutover cleanup** — uninstall `TiqoraSync`, archive/disable the
   Znuny crontab, decommission the Znuny frontend.

Read [`../cutover.md`](../cutover.md) in full before starting Stage 4 — it
is the authoritative, checklist-driven version of this summary, including
the exact SQL/CLI commands and Prometheus metric names to watch.

---

## Boundaries, restated

- **Stages 0–3** are all safely reversible by flipping flags/config back
  and, worst case, reinstalling `TiqoraSync` or restarting the Znuny daemon.
  No destructive schema change happens until Stage 4.
- **Stage 4, steps 1–4** are still reversible (nginx reverts, daemon
  restarts). **Stage 4, step 5 (schema ownership) is not** — once owned
  migrations are applied, the only supported rollback is restoring the
  pre-cutover database dump from Stage 0.
- Do not skip ahead: enabling schema ownership before Znuny is confirmed
  fully stopped, or taking over postmaster before escalation/notifications
  are verified stable, both increase blast radius for no benefit.
