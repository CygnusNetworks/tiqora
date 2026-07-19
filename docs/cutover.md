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

- [ ] All Phase 4b daemon takeovers are live and have been running cleanly in
      production for a representative period (see
      `docs/parallel-operation.md` → "Taking over ..." sections):
      `daemon.postmaster.enabled`, `daemon.escalation.enabled`,
      `daemon.notifications.enabled`, `daemon.generic_agent.enabled` — all
      `1` in `tiqora_settings`.
- [ ] Golden-master and compat test suites are green on the current release
      (`cd backend && uv run pytest -m db`).
- [ ] A full, verified, restorable database backup/dump exists and its
      restore procedure has been tested recently. **Every rollback stage
      beyond "freeze Znuny web" assumes this dump is available and current.**
- [ ] Prometheus/Grafana dashboards for the golden signals below are set up
      and someone is watching them during the cutover window.
- [ ] A maintenance window is scheduled and stakeholders notified.

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
   `daemon.notifications.enabled`, `daemon.generic_agent.enabled` all `1`.
2. Confirm the Tiqora worker process is running and its daemon-specific
   Prometheus counters are advancing (`tiqora_postmaster_fetched_total`,
   `tiqora_escalation_tickets_swept_total`,
   `tiqora_notifications_sent_total`, `tiqora_generic_agent_jobs_run_total`).
3. Stop the Znuny daemon:
   ```sh
   su -c "bin/otrs.Daemon.pl stop" -s /bin/bash otrs
   # or: systemctl stop znuny-daemon (deployment-dependent unit wrapping the same script)
   ```
4. Confirm it stayed stopped (`bin/znuny.Console.pl Maint::Daemon::Summary`
   for per-task status, or `ps`/`systemctl status` for the process itself)
   for at least one full poll interval of the slowest takeover
   (`escalation`, default 300s — see `TIQORA_ESCALATION_INTERVAL`).

**Verify**: no new rows appear in Znuny's own daemon PID/lock files; Tiqora's
per-daemon counters keep advancing with the Znuny daemon down.

**Rollback**: `su -c "bin/otrs.Daemon.pl start" -s /bin/bash otrs` restarts the Znuny
daemon. Because every takeover flag is a mutually-exclusive switch (Tiqora
checks the flag before acting, Znuny's own daemon tasks are independent of
those flags), having *both* running briefly during rollback is not
catastrophic but should be avoided — flip the `daemon.*.enabled` flags back
to `0` in `tiqora_settings` first, then restart the Znuny daemon, then
Tiqora resumes deferring to Znuny.

---

## Stage 3 — Repoint nginx GenericInterface locations to Tiqora compat

**Goal**: external integrators (and the TiqoraSync addon, if still installed)
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
   integrators use (`TicketCreate`, `TicketSearch`, `TicketUpdate`,
   `TicketGet`, `SessionCreate` at minimum).

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
| `tiqora_poller_history_lag` / `tiqora_poller_article_lag` | Znuny-write poller keeping up (should now trend to ~0 permanently, since Znuny is no longer writing) | non-zero and growing |
| `tiqora_webhook_deliveries_total{status="failure"}` | Outbox → webhook fan-out health | any sustained increase |
| `tiqora_escalation_errors_total` | Escalation sweep errors | any increase |
| `tiqora_notifications_errors_total` | Notification engine errors | any increase |
| `tiqora_generic_agent_errors_total` | GenericAgent executor errors | any increase |
| `tiqora_postmaster_errors_total` | Mail fetch/dispatch errors | any increase |
| `tiqora_index_documents_total` | Meilisearch indexing keeping up | flatlines unexpectedly |

### Manual checklist

- [ ] Spot-check tickets created/updated during the window in both the
      Tiqora UI and (read-only) the Znuny UI — they must match.
- [ ] Confirm outbound mail (`SMTP`) is flowing via
      `tiqora_postmaster_*` metrics/logs, not Znuny's mail queue.
- [ ] Confirm no operator has re-enabled the Znuny daemon out-of-band.

If anything looks wrong, stop here — do **not** proceed to Stage 5. Roll
back Stages 1–3 (in reverse order) and investigate.

---

## Stage 5 — Enable schema ownership

**Goal**: unlock the `alembic/versions_owned` migration chain (additive
indexes, orphan reporting) now that Znuny is confirmed shut down and will
stay shut down.

This is the **first stage with a real point of no return**: once owned
migrations are applied, rolling back requires a database restore, not just a
config revert (see Rollback below).

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
with `alembic downgrade 20260719_0005` (they are pure additive DDL with a
matching `downgrade()`) — this returns the schema to the pre-ownership
state. However:

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

- [ ] **Uninstall the TiqoraSync Znuny addon** (`packages/znuny-addon`) from
      the now-retired Znuny instance, if it was ever installed for
      cache-invalidation purposes — it has no further role once Znuny is
      shut down. (`bin/znuny.Console.pl Admin::Package::Uninstall
      TiqoraSync`, if the Znuny instance is still reachable for
      housekeeping.)
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
- [ ] Remove the maintenance-mode banner / 503 override from Stage 1.

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
