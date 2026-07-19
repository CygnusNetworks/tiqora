# TiqoraSync — install / verify / uninstall

TiqoraSync is a small Znuny OPM addon that keeps Znuny's in-process ticket
cache in sync while Tiqora writes tickets directly to the shared database.
See the top-level [`docs/parallel-operation.md`](../../../../docs/parallel-operation.md)
for the full background.

## What it installs

- `Kernel/System/TiqoraSync.pm` — the module the daemon cron task calls.
- `Kernel/Config/Files/XML/TiqoraSync.xml` — SysConfig registration of the
  daemon cron task `Daemon::SchedulerCronTaskManager::Task###TiqoraSync`.

## Build the installable package

`TiqoraSync.sopm` is the package *source* (it references the two files above
by their relative `Location`, it does not embed their content). Znuny's own
package tooling turns a `.sopm` into an installable `.opm` by embedding the
referenced files:

```sh
# run from inside the Znuny checkout, with this addon's source tree
# available on disk, e.g. bind-mounted or copied to
# /opt/znuny/TiqoraSync-src/
bin/znuny.Console.pl Dev::Package::Build \
    /opt/znuny/TiqoraSync-src/TiqoraSync.sopm \
    /opt/znuny/var/packages/
```

This produces `/opt/znuny/var/packages/TiqoraSync-1.0.0.opm`.

If your Znuny version accepts `.sopm` files directly for installation (the
`.sopm`/`.opm` XML format is otherwise identical, the `.sopm` just lacks
embedded file content — some Znuny/OTRS versions read files straight off
disk relative to the package's own location when installing a `.sopm`),
you can skip the build step and install `TiqoraSync.sopm` directly. If in
doubt, build the `.opm` first as shown above and install that.

## Install

```sh
bin/znuny.Console.pl Admin::Package::Install /opt/znuny/var/packages/TiqoraSync-1.0.0.opm
```

## Verify

1. Confirm the package is registered:

   ```sh
   bin/znuny.Console.pl Admin::Package::List
   ```

   `TiqoraSync 1.0.0` should be listed.

2. Confirm the daemon cron task is registered and enabled:

   Admin UI -> System Configuration -> search for
   `Daemon::SchedulerCronTaskManager::Task###TiqoraSync`, or:

   ```sh
   bin/znuny.Console.pl Admin::Config::Read \
       --setting-name "Daemon::SchedulerCronTaskManager::Task###TiqoraSync"
   ```

3. Restart (or wait for) the Znuny daemon to pick up the new SysConfig, then
   watch the daemon log for the task firing every minute (see the "Note on
   scheduling granularity" in `TiqoraSync.xml` — Znuny's cron task manager
   only supports minute resolution, so the effective interval is 60s, not
   the originally targeted 30s):

   ```sh
   tail -f /opt/znuny/var/log/Daemon/SchedulerTaskWorker.log
   ```

4. End-to-end check: with both `tiqora_cache_invalidation` and
   `tiqora_settings` present in the shared database (i.e. Tiqora has run its
   Alembic migrations), manually insert a row:

   ```sql
   INSERT INTO tiqora_cache_invalidation (ticket_id) VALUES (123);
   ```

   Within about a minute, confirm the watermark advanced:

   ```sql
   SELECT * FROM tiqora_settings WHERE `key` = 'tiqorasync.watermark';
   ```

   The stored `value` should be greater than or equal to the `id` of the row
   you inserted. If the hand-off tables do not exist yet, TiqoraSync logs a
   `debug`-level message and returns cleanly on every run — it does not
   error, retry loudly, or block the daemon.

## Uninstall

```sh
bin/znuny.Console.pl Admin::Package::Uninstall TiqoraSync
```

This removes the `Kernel/System/TiqoraSync.pm` module and the SysConfig
cron task registration. It does not touch the `tiqora_cache_invalidation`
or `tiqora_settings` tables, which are owned and migrated by Tiqora.
