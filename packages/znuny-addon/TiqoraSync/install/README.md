# TiqoraSync — install / verify / uninstall

TiqoraSync is a small OTRS/Znuny OPM addon that keeps the peer's in-process
ticket cache in sync while Tiqora writes tickets directly to the shared
database. See the top-level
[`docs/parallel-operation.md`](../../../../docs/parallel-operation.md) for
background.

**Supported peer frameworks (SOPM):** `6.0.x` … `6.5.x` and `7.0.x` … `7.3.x`.
APIs used (`Kernel::System::DB`, `Cache`, `Log`, daemon cron SysConfig) are
stable across this range.

## What it installs

- `Kernel/System/TiqoraSync.pm` — the module the daemon cron task calls.
- `Kernel/Config/Files/XML/TiqoraSync.xml` — SysConfig registration of the
  daemon cron task `Daemon::SchedulerCronTaskManager::Task###TiqoraSync`.

## Install paths

| Peer | Typical home | Console |
|------|--------------|---------|
| OTRS 6.0.x | `/opt/otrs` | `bin/otrs.Console.pl` |
| Znuny 6.x / 7.x | `/opt/znuny` | `bin/znuny.Console.pl` |

Below, `$OTRS` is that home and `$CONSOLE` is the matching Console.pl. Run as
the application user (`otrs` / `znuny`).

## Build the installable package

`TiqoraSync.sopm` is the package *source* (it references the two files above
by their relative `Location`, it does not embed their content). Znuny's /
OTRS's package tooling turns a `.sopm` into an installable `.opm` by embedding
the referenced files:

```sh
# e.g. bind-mount this tree to $OTRS/TiqoraSync-src/
$CONSOLE Dev::Package::Build \
    $OTRS/TiqoraSync-src/TiqoraSync.sopm \
    $OTRS/var/packages/
```

This produces `$OTRS/var/packages/TiqoraSync-1.1.1.opm` (version from the SOPM).

If your peer accepts `.sopm` files directly for installation, you can skip the
build step. If in doubt, build the `.opm` first.

If the Package Manager rejects multi-`<Framework>` packages on a very old
OTRS 6.0 build, ship a one-line SOPM fork with only `<Framework>6.0.x</Framework>`
(same code files) — the runtime module does not branch on version.

## Install

```sh
$CONSOLE Admin::Package::Install $OTRS/var/packages/TiqoraSync-1.1.1.opm
```

## Verify

1. Confirm the package is registered:

   ```sh
   $CONSOLE Admin::Package::List
   ```

   `TiqoraSync 1.1.1` should be listed.

2. Confirm the daemon cron task is registered and enabled:

   Admin UI → System Configuration → search for
   `Daemon::SchedulerCronTaskManager::Task###TiqoraSync`, or:

   ```sh
   $CONSOLE Admin::Config::Read \
       --setting-name "Daemon::SchedulerCronTaskManager::Task###TiqoraSync"
   ```

3. Restart (or wait for) the peer daemon, then watch the daemon log for the
   task firing every minute (Znuny's cron task manager is minute-resolution):

   ```sh
   tail -f $OTRS/var/log/Daemon/SchedulerTaskWorker.log
   ```

4. End-to-end check: with both `tiqora_cache_invalidation` and
   `tiqora_settings` present (Tiqora migrations applied), insert:

   ```sql
   INSERT INTO tiqora_cache_invalidation (ticket_id) VALUES (123);
   ```

   Within about a minute:

   ```sql
   SELECT * FROM tiqora_settings WHERE `key` = 'tiqorasync.watermark';
   ```

   The stored `value` should be ≥ the inserted row's `id`. If the hand-off
   tables do not exist yet, TiqoraSync logs at `debug` and returns cleanly.

## Uninstall

```sh
$CONSOLE Admin::Package::Uninstall TiqoraSync
```

This removes the module and SysConfig task; it does not drop `tiqora_*` tables.
