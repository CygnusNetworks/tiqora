# Cutover runbook: Znuny → Tiqora

This document is the step-by-step procedure for taking a site from **parallel
operation** (Tiqora and Znuny both reading/writing the shared database,
Tiqora writes verified byte-identical to Znuny's own — see
[`parallel-operation.md`](parallel-operation.md)) to **Tiqora-only
operation**.

It does not perform any cutover itself — it is the operator's checklist.
Every stage lists its own rollback. Read the whole document before starting;
do not begin a stage you are not prepared to roll back.

**Audience**: the operator running the cutover, with shell access to both the
Znuny and Tiqora hosts, the shared database, and the nginx frontend.

---

## 0. Preconditions

Before starting *any* stage below:

- [ ] All daemon takeovers are live and have been running cleanly in
      production for a representative period (see
      `docs/parallel-operation.md` → "Taking over ..." sections):
      `daemon.postmaster.enabled`, `daemon.escalation.enabled`,
      `daemon.notifications.enabled`, `daemon.generic_agent.enabled`,
      `daemon.unlock_timeout.enabled`, `daemon.pending_check.enabled` — all
      `1` in `tiqora_settings`.
- [ ] Relevant DB integration and compatibility tests pass on the deployed
      revision (`cd backend && uv run pytest -m db`). Run the separate
      golden-master suite with its documented prerequisites when validating
      peer parity; `-m db` alone is not a golden-master run.
- [ ] A full, verified, restorable database backup/dump exists and its
      restore procedure has been tested recently. **Every rollback stage
      beyond "freeze Znuny web" assumes this dump is available and current.**
- [ ] Restore the dump into an isolated database, verify critical row counts
      and attachment data, and retain the dump, checksum, deployment config,
      encryption keys and previous image reference securely. A successful
      backup job alone does not demonstrate a working restore. Never start
      mail fetchers or outbound workers against the restored production data.
- [ ] Prometheus/Grafana dashboards for the golden signals below are set up
      and someone is watching them during the cutover window.
- [ ] A maintenance window is scheduled and stakeholders notified.
- [ ] Security-hardening backlog reviewed (see next section) — accepted or
      resolved before going production-primary.

### Inventory every remaining responsibility

Run the peer's `Maint::Daemon::Summary` and inspect OS cron, supervisor,
container restart policies, event handlers and installed addons. A healthy
Tiqora container and enabled flags do not prove that every legacy task has
been replaced. Classify each remaining task as **taken over**, **no longer
needed**, or **must remain as an independent service**.

| Responsibility | Required evidence before stopping the peer |
|---|---|
| Unlock timeout | Tiqora respects configured unlock states, queue timeout and queue/SLA working calendar; a due locked ticket unlocks once. |
| Pending checks | Due automatic states transition to their configured destination; due reminders use working hours and do not duplicate within their cadence. |
| GenericAgent | Compare a read-only match result for each active job, including empty optional fields and archive/time filters. Scheduled jobs run from a dedicated daemon module, not from the GenericAgent event handler, so disabling that handler does not stop them. The module is usually a *required* setting and therefore cannot be invalidated (see the warning below) — plan the GenericAgent handover for the moment the peer daemon stops, and keep both sides' job definitions identical until then, since a job table shared by both is executed by whichever side is running. |
| Notification rules | List each valid rule's `Events` and `Recipients` values and confirm every one is emitted and resolvable. Rules carried over from older releases are typically bound to the legacy `Notification*` events, not to `Ticket*`/`Article*` ones — a rule bound to an event nobody emits stays silent while the takeover reports healthy ticks. |
| Outbound mail and spool | Drain the peer's `mail_queue` and filesystem spool while its sender still runs. Tiqora's SMTP sender does not drain the legacy queue. Verify direct replies, autoresponses, notifications and SMTP failure handling with controlled test recipients or a sink. |
| Calendar, S/MIME, processes and addons | Check actual configured use, including scheduled ticket creation, certificate renewal and custom jobs. An empty current workload is not implementation parity; retain or replace required functions before stopping their executor. |
| Shared data and external writers | Preserve the database, attachment storage, keys and any independent imports or integrations. Verify external writers against the owned schema before enabling migrations. |
| Peer-only housekeeping | Cache, dashboard and search-index maintenance can retire with the peer if nothing else consumes their output. |

> **A configuration tool reporting success is not proof of a change.** Some
> settings are flagged *required*, and the CLI that invalidates settings
> accepts the request, prints its usual success line, and writes nothing —
> the deployed configuration keeps the old value. After disabling anything,
> re-read the *deployed* configuration (not the tool's output, and not the
> settings table's defaults) and confirm the entry is actually gone. Duties
> that cannot be disabled individually have to be sequenced with stopping the
> peer's scheduler as a whole.

Do not run `docker compose down` on a stack containing the shared database.
Stop named application services only. Keep independent services in the
deployment definition, and use an explicit opt-in profile or remove the
retired application from automatic startup. Disable cron/supervisor and
update automation that could revive it. Retain a documented rollback path.

---

## 0b. Security hardening backlog (pre-production)

Deferred items from the full security review (2026-07). None blocks parallel
operation; review and accept or resolve before Tiqora becomes the primary,
internet-facing system.

- [ ] **Encryption-key blast radius (review M5).** One Fernet key, derived from
      `TIQORA_SECRET_KEY`, protects LLM provider API keys, MCP auth tokens **and**
      the audit log's `pii_map_enc` de-anonymization maps. A `TIQORA_SECRET_KEY`
      leak retroactively exposes all of them for the audit-retention window.
      *Follow-up:* move secrets to an external KMS or introduce per-purpose keys
      with a rotation story. Note: a code-side key-derivation change requires a
      **re-encryption migration** of existing ciphertext — plan it, don't hot-swap.
      Interim mitigations: keep `TIQORA_SECRET_KEY` in a secrets manager (not env
      files/backups), and lower `ai_audit_retention_days`.
- [ ] **AI ACL limits are soft (review M6).** `limit_requests_day` /
      `limit_tokens_day` / `limit_requests_month` are checked before a run and
      recorded after, so concurrent requests can race slightly past the cap. It's
      a cost guardrail, not an authz boundary. *Follow-up (only if hard caps are
      required):* atomic reserve-and-check (DB reservation row or Redis counter)
      in `tiqora.ai.acl.check_feature_limits`.
- [ ] **PII masking covers structured identifiers + detected names only
      (review, informational).** Addresses, IBANs, order/reference numbers and
      free-text sensitive data are not masked before reaching the LLM provider
      (and the audit log). Size this residual risk with stakeholders; prefer
      EU-hosted providers and per-queue `pii_masking` where data sensitivity is
      high.

---

## Stage 1 — Freeze Znuny web

**Goal**: stop new agent/customer logins and ticket writes through the Znuny
UI, without stopping the daemon (yet) or the database.

1. Block new logins/writes at the entry point — the most reliable
   mechanism, independent of which SysConfig options your Znuny build ships:
   - Put the nginx vhost in front of Znuny's `index.pl`/`customer.pl` into a
     503 maintenance response for all paths except health checks, while
     leaving Tiqora's own frontend reachable.
   - If your Znuny installation has a package/addon providing a maintenance
     banner or login block (not part of stock Znuny 6.5), enable it too as a
     defense-in-depth measure — but do not rely on it alone.
2. Confirm no new sessions are created: `SELECT COUNT(*) FROM sessions;`
   should stop growing.
3. Announce the freeze to agents/customers (banner, status page).

**Verify**: attempt a login through the Znuny UI — it must be blocked or
show the maintenance message. Tiqora's UI remains usable (parallel
operation continues underneath).

**Rollback**: remove the nginx 503 override (and disable any maintenance
addon enabled as a defense-in-depth measure). No data was touched — this
stage is fully reversible at any time, including after moving on to Stage 2,
by reverting Stage 2's nginx change and un-freezing Znuny.

---

## Stage 2 — Verify daemon-flag takeover is complete

**Goal**: confirm Tiqora, not Znuny, is authoritative for every background
duty before the Znuny daemon is stopped.

1. Check every takeover flag is `1`:
   ```sql
   SELECT `key`, value FROM tiqora_settings WHERE `key` LIKE 'daemon.%.enabled';
   ```
   Expect `daemon.postmaster.enabled`, `daemon.escalation.enabled`,
   `daemon.notifications.enabled`, `daemon.generic_agent.enabled`,
   `daemon.unlock_timeout.enabled`, `daemon.pending_check.enabled` all `1`.
   Disable each corresponding peer executor **before** enabling its Tiqora
   flag; the flags do not control Znuny's scheduler.
2. Confirm fresh successful `daemon.<slug>.status.last_ok` timestamps and
   inspect `last_result` for per-item errors. A completed tick may still
   report errors. When eligible work exists, verify corresponding counters
   and resulting ticket changes (`tiqora_postmaster_fetched_total`,
   `tiqora_escalation_tickets_swept_total`,
   `tiqora_notifications_sent_total`, `tiqora_generic_agent_jobs_run_total`).
   A zero counter is normal when no work is due; test each duty with controlled
   fixtures before cutover rather than generating unsolicited production mail.
3. Stop the Znuny daemon:
   ```sh
   su -c "bin/otrs.Daemon.pl stop" -s /bin/bash otrs
   # or: systemctl stop znuny-daemon (deployment-dependent unit wrapping the same script)
   ```
4. Confirm it stayed stopped (`bin/znuny.Console.pl Maint::Daemon::Summary`
   for per-task status, or `ps`/`systemctl status` for the process itself)
   for at least one full poll interval of the slowest enabled takeover and
   through the configured daemon restart mechanism. Check scheduled jobs
   again at their next due time, not just immediately after stopping.

**Verify**: no new rows appear in Znuny's own daemon PID/lock files; Tiqora's
per-daemon counters keep advancing with the Znuny daemon down.

**Rollback**: `su -c "bin/otrs.Daemon.pl start" -s /bin/bash otrs` restarts the Znuny
daemon. Because every takeover flag is a mutually-exclusive switch (Tiqora
checks the flag before acting, Znuny's own daemon tasks are independent of
those flags), disable the duties being returned to Znuny first, let their
in-flight Tiqora ticks finish, and only then restore the matching peer tasks
and restart its daemon. Do not disable Tiqora-only services such as the outbox
or Telegram indiscriminately. Concurrent mail fetch or notification execution
can duplicate or lose work.

---

## Stage 3 — Repoint nginx GenericInterface locations to Tiqora compat

This stage applies only if legacy clients or links must continue working.
Inventory actual consumers and test the operations they use. If no such
compatibility is required, record the stage as not applicable in the site's
execution record and leave routing changes out of the cutover.

**Goal**: external integrators
hit Tiqora's `/znuny-compat` layer instead of Znuny's
`nph-genericinterface.pl`.

Example nginx config change:

```nginx
# Before (Znuny GenericInterface, CGI/FastCGI):
# location /otrs/nph-genericinterface.pl {
#     fastcgi_pass unix:/var/run/znuny-fcgi.sock;
#     include fastcgi_params;
# }

# After (Tiqora compat layer):
location /otrs/nph-genericinterface.pl {
    rewrite ^/otrs/nph-genericinterface\.pl(.*)$ /znuny-compat$1 break;
    proxy_pass http://tiqora-backend:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Adjust the `rewrite` target to match how your integrators call the
webservice (`/Webservice/{name}/...` vs `/WebserviceID/{id}/...` — both are
supported natively by `tiqora.api.compat.router`, see
[`compatibility.md`](compatibility.md)).

1. Apply the config to a canary integrator path first if you have per-path
   routing available; otherwise apply for all GenericInterface traffic at
   once (there is no traffic-splitting layer in v1).
2. `nginx -t && systemctl reload nginx`.
3. Re-run a smoke test of each production webservice operation your
   integrators use. At minimum: `SessionCreate`, `TicketCreate`,
   `TicketSearch`, `TicketUpdate`, `TicketGet`. If clients use them,
   also: `SessionGet`/`SessionRemove`, `TicketHistoryGet`,
   `TimeAccountingGet`, `OutOfOffice` (see
   [`compatibility.md`](compatibility.md#what-is-not-emulated-and-why)
   for known partial fidelity).

**Verify**: `tiqora_http_requests_total{path="/znuny-compat/..."}` starts
incrementing; Znuny's own GI access log stops receiving traffic.

**Rollback**: revert the nginx location block to point back at
`nph-genericinterface.pl` and reload nginx. No data was touched — Znuny's
GenericInterface provider was never stopped, only bypassed.

---

## Stage 4 — Monitor

Watch for the remainder of the maintenance window (recommend >= 1 hour, or a
full business day for a cautious rollout) before proceeding to Stage 5.

### Golden signals (Prometheus)

| Metric | What it means | Alert on |
|---|---|---|
| `tiqora_http_requests_total{status=~"5.."}` | API/compat error rate | any sustained increase |
| `tiqora_http_request_duration_seconds` | Latency | p99 regression vs. baseline |
| `tiqora_poller_history_lag` / `tiqora_poller_article_lag` | Legacy-table poller keeping up, including any remaining external writers | non-zero and growing |
| `tiqora_webhook_deliveries_total{status="failure"}` | Outbox → webhook fan-out health | any sustained increase |
| `tiqora_escalation_errors_total` | Escalation sweep errors | any increase |
| `tiqora_notifications_errors_total` | Notification engine errors | any increase |
| `tiqora_generic_agent_errors_total` | GenericAgent executor errors | any increase |
| `tiqora_postmaster_errors_total` | Mail fetch/dispatch errors | any increase |
| `tiqora_index_documents_total` | Meilisearch indexing keeping up | flatlines unexpectedly |

### Manual checklist

- [ ] Spot-check tickets, history and attachments created/updated during the
      window in Tiqora against the stored data. Do not restart the retired
      peer or create new peer sessions just to run this check.
- [ ] Confirm outbound mail using SMTP delivery logs and Tiqora's outbound
      mail records; postmaster fetch counters alone do not prove delivery.
- [ ] Verify fresh successful pending-check and unlock-timeout status, due
      actions, and the next scheduled GenericAgent job.
- [ ] Confirm at least one notification per configured event family actually
      reached its recipients, using a controlled recipient rather than
      production correspondents.
- [ ] Confirm no operator has re-enabled the Znuny daemon out-of-band.
- [ ] Confirm the shared database and independent services remain healthy.

### Set the operation mode deliberately

Once Tiqora owns the required duties, set `system.operation_mode` to
`tiqora_primary` through the admin settings. This is separate from schema
ownership. Inspect AI worker, per-queue auto-reply/summary rules and the
global `ai.auto_reply.paused` switch first: changing the mode can open the
gate for autonomous AI that was already configured but inactive in parallel
mode. Record the intended behavior before switching; enabling new automated
customer replies is not a prerequisite for retiring Znuny.

For rollback, restore the previous operation mode before handing mail duties
back to the peer. Preserve intentionally paused AI settings.

If anything looks wrong, stop here — do **not** proceed to Stage 5. Roll
back Stages 1–3 (in reverse order) and investigate.

---

## Stage 5 — Enable schema ownership

**Goal**: unlock the `alembic/versions_owned` migration chain (additive
indexes, orphan reporting) now that Znuny is confirmed shut down and will
stay shut down.

This is the **first stage that changes the legacy schema**. A configuration
revert alone is insufficient once migrations run. The current index-only
migration has a downgrade; future migrations may require a database restore
(see Rollback below).

1. Confirm Znuny is fully stopped: web frontend frozen (Stage 1), daemon
   stopped (Stage 2), and — for this gate specifically — **no admin is
   logged into the Znuny back office** either (the preflight check below
   verifies this via the `sessions` table).

2. Run the CLI preflight + enable command from a Tiqora host:

   ```sh
   tiqora ownership status
   # env flag  (TIQORA_SCHEMA_OWNERSHIP): unset
   # DB marker (tiqora_settings key):     unset
   # versions_owned chain active:          no (both gates required)

   tiqora ownership enable --confirm "I have shut down Znuny"
   ```

   This runs the preflight checks (see `tiqora.domain.ownership`):
   - `ticket_history` watermark: newest `change_time` must be idle for
     `--history-watermark-minutes` (default 15).
   - `sessions` table: must be empty (Znuny does not timestamp session rows,
     so "any row present" is treated as "someone is still logged in").

   The history check sees Tiqora writes too; it cannot identify which
   application wrote a row. A busy Tiqora install can fail this conservative
   check after Znuny is stopped. Investigate the writer and arrange a quiet
   window rather than treating recent history as proof of a live peer.

   It prints a report and refuses (exit code 1) if either check fails. Do
   **not** reflexively re-run with `--force` — investigate first (a lingering
   session, a forgotten cron job still hitting Znuny, a stuck browser tab).

   `--force` exists for verified-safe cases only (e.g. you know the
   `sessions` row is a stale abandoned session, confirmed by its `data_key`/
   `data_value` content) and prints a loud warning; it does not skip the
   `--confirm` phrase check.

3. On success, the command sets the `tiqora_settings` DB marker
   (`schema.ownership = enabled`, with an ISO-8601 `enabled_at` timestamp)
   but does **not** yet activate the chain — the env flag is the second
   gate.

4. Set `TIQORA_SCHEMA_OWNERSHIP=1` in the environment of every Tiqora
   process (API, worker, MCP, and wherever `alembic upgrade` is run from)
   and restart them.

5. Run the owned migration:
   ```sh
   cd backend && uv run alembic upgrade head
   ```
   With both gates now active, `alembic/env.py` includes
   `alembic/versions_owned` in `version_locations` and applies
   `20260719_0006_owned_indexes` (three additive composite indexes — see
   `alembic/versions_owned/README.md`). This is index-only DDL: it does not
   modify any row.

6. Optionally run the orphan report for visibility (read-only, no cleanup in
   v1):
   ```sh
   tiqora ownership orphan-report
   ```

**Verify**: `tiqora ownership status` shows both gates active and
`versions_owned chain active: YES`. `alembic current` (with
`TIQORA_SCHEMA_OWNERSHIP=1` set) shows revision `20260719_0006` as the head
of the combined chain.

### Rollback — two distinct cases

**Case A: DB marker set, `TIQORA_SCHEMA_OWNERSHIP` env flag NOT yet set (or
owned migrations not yet applied).**

The gate requires *both* to be true, so the chain is still inert. Simply do
not set the env flag / do not run `alembic upgrade`. To fully revert, delete
the marker rows:

```sql
DELETE FROM tiqora_settings WHERE `key` IN ('schema.ownership', 'schema.ownership.enabled_at');
```

No schema changes occurred. This is a config-only rollback.

**Case B: owned migrations have been applied (`20260719_0006` or later is
the current head).**

The composite indexes added by `20260719_0006` **can** be dropped cleanly
using that migration's matching `downgrade()`. Determine the immediate
`down_revision` from the **deployed** revision file and downgrade to that
revision with both ownership gates still active. Do not use an old hard-coded
target: the owned migration is rebased as the normal chain advances, and an
older target could also undo application migrations and remove data. Disable
the gates only after the schema downgrade succeeds. However:

- Any **future** owned migration that is not purely additive (e.g. a
  destructive orphan cleanup, should one ever be added in a later version)
  would **not** be safely reversible by `alembic downgrade` alone.
- If schema ownership was enabled specifically because you also intend to
  resume parallel operation with Znuny (an unusual but not impossible
  rollback path), **do not** just downgrade and restart Znuny — Znuny was
  never designed to tolerate Tiqora-owned schema changes appearing and
  disappearing underneath it.

**For any rollback beyond a clean `alembic downgrade` of purely additive
DDL, the documented and only supported path is: restore the database from
the pre-cutover dump taken in Stage 0, then re-freeze Znuny and re-plan the
cutover.** Do not attempt to hand-edit the schema back to a "Znuny-compatible"
state.

---

## Stage 6 — Post-cutover tasks

Once schema ownership is enabled and the monitoring window (Stage 4-equivalent,
post-Stage-5) has passed cleanly:

- [ ] Retire the TiqoraSync executor with Znuny. It has no role after the
      peer stops. Do not restart a retired application against the owned
      database solely to uninstall an addon; an archived rollback image may
      keep the installed package.
- [ ] **Archive the Znuny crontab** (`var/cron/*`) — copy it out for
      historical reference, then disable it (`crontab -r` for the znuny
      user, or remove the cron.d drop-in). Confirm no Znuny cron job is
      still running (`grep znuny /etc/cron.d/* 2>/dev/null`).
- [ ] Decommission or archive the Znuny web frontend (container/vhost) per
      your infrastructure's standard retirement process. Keep the database
      dump from Stage 0 (and a fresh one from immediately after Stage 5)
      for the long term — do not delete either until you are confident no
      rollback will ever be needed.
- [ ] Update internal documentation/runbooks that reference the old Znuny
      URLs or admin procedures to point at Tiqora.
- [ ] Remove temporary Tiqora maintenance notices. Keep the retired peer
      inaccessible; removing an old 503 override must not reopen its UI.

---

## Summary: stage → rollback quick reference

| Stage | Action | Rollback |
|---|---|---|
| 1 | Freeze Znuny web | Un-freeze (revert SysConfig/nginx) |
| 2 | Stop Znuny daemon | Restart Znuny daemon (flip `daemon.*.enabled` off first) |
| 3 | Repoint nginx GI locations | Revert nginx location block, reload |
| 4 | Monitor | N/A — roll back 1–3 if signals are bad |
| 5 | Enable schema ownership | Marker-only: delete `tiqora_settings` rows. Migrations applied: `alembic downgrade` (additive-only) or **restore from dump** |
| 6 | Post-cutover cleanup | N/A — housekeeping only |
