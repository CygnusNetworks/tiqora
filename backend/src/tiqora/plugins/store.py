"""Database side of the plugin system: installed-plugin rows, per-role runtime rows, hooks."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginHook, TiqoraPluginRuntime
from tiqora.plugins.discovery import DiscoveredPlugin
from tiqora.plugins.state import QUARANTINE_AFTER, PluginRole, RuntimeState

log = structlog.get_logger(__name__)

#: pause between the attempts of :func:`prepare_boot` (tests monkeypatch this)
BOOT_RETRY_DELAY_SECONDS: float = 2.0

MAX_ID_LEN: Final = 190
MAX_ERROR_LEN: Final = 2000


@dataclass(frozen=True, slots=True)
class BootInfo:
    enabled: bool
    boot_attempts: int


def storable(plugin_id: str) -> bool:
    """Ids longer than the column cannot be stored (they never pass discovery anyway)."""
    return 0 < len(plugin_id) <= MAX_ID_LEN


async def sync_discovered(
    session: AsyncSession,
    plugins: Sequence[DiscoveredPlugin],
    *,
    auto_enable: bool,
    now: datetime,
) -> dict[str, TiqoraPlugin]:
    """Insert plugins seen for the first time, refresh name/version; never touch ``enabled``.

    A concurrent boot of another process may insert the same row; the loser rolls back
    and re-reads (second pass finds the row and only updates).
    """
    wanted = [p for p in plugins if storable(p.plugin_id)]
    for attempt in (1, 2):
        ids = [p.plugin_id for p in wanted]
        rows: dict[str, TiqoraPlugin] = {}
        if ids:
            found = await session.execute(select(TiqoraPlugin).where(TiqoraPlugin.id.in_(ids)))
            rows = {r.id: r for r in found.scalars()}
        for p in wanted:
            name = ((p.manifest.name if p.manifest else p.dist_name) or p.plugin_id)[:200]
            version = (p.manifest.version if p.manifest else p.dist_version)[:64]
            row = rows.get(p.plugin_id)
            if row is None:
                row = TiqoraPlugin(
                    id=p.plugin_id,
                    name=name,
                    version=version,
                    enabled=auto_enable,
                    source="image",
                    installed_at=now,
                    updated_at=now,
                )
                session.add(row)
                rows[p.plugin_id] = row
            elif row.name != name or row.version != version:
                row.name = name
                row.version = version
                row.updated_at = now
        try:
            await session.flush()
        except IntegrityError:
            if attempt == 2:
                raise
            await session.rollback()
            continue
        return rows
    raise AssertionError("unreachable")  # pragma: no cover


async def list_plugin_rows(session: AsyncSession) -> dict[str, TiqoraPlugin]:
    result = await session.execute(select(TiqoraPlugin))
    return {r.id: r for r in result.scalars()}


async def list_runtime_rows(
    session: AsyncSession, plugin_ids: Sequence[str] | None = None
) -> list[TiqoraPluginRuntime]:
    stmt = select(TiqoraPluginRuntime).order_by(
        TiqoraPluginRuntime.plugin_id, TiqoraPluginRuntime.role
    )
    if plugin_ids is not None:
        if not plugin_ids:
            return []
        stmt = stmt.where(TiqoraPluginRuntime.plugin_id.in_(list(plugin_ids)))
    return list((await session.execute(stmt)).scalars())


async def _prepare_once(
    factory: async_sessionmaker[AsyncSession],
    plugins: Sequence[DiscoveredPlugin],
    role: PluginRole,
    auto_enable: bool,
    now: datetime,
) -> dict[str, BootInfo]:
    if not plugins:
        return {}
    async with factory() as session:
        rows = await sync_discovered(session, plugins, auto_enable=auto_enable, now=now)
        info: dict[str, BootInfo] = {}
        if rows:
            runtime = await session.execute(
                select(TiqoraPluginRuntime).where(
                    TiqoraPluginRuntime.role == role,
                    TiqoraPluginRuntime.plugin_id.in_(list(rows)),
                )
            )
            attempts = {r.plugin_id: r.boot_attempts for r in runtime.scalars()}
            info = {
                pid: BootInfo(enabled=row.enabled, boot_attempts=attempts.get(pid, 0))
                for pid, row in rows.items()
            }
        await session.commit()
        return info


async def prepare_boot(
    factory: async_sessionmaker[AsyncSession],
    plugins: Sequence[DiscoveredPlugin],
    *,
    role: PluginRole,
    auto_enable: bool,
    attempts: int = 3,
    delay_seconds: float | None = None,
) -> dict[str, BootInfo]:
    """Sync discovered plugins and read this role's crash counters, in one session.

    Retried on any error (R-BOOT-ANYERR): a worker may start before the api has
    migrated, so even a missing table can be transient. The last error is raised.
    """
    from tiqora.jobs.service import utc_now

    delay = BOOT_RETRY_DELAY_SECONDS if delay_seconds is None else delay_seconds
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await _prepare_once(factory, plugins, role, auto_enable, utc_now())
        except Exception as exc:
            if attempt >= attempts:
                raise
            log.warning(
                "plugins_state_retry",
                role=role,
                attempt=attempt,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def mark_loading(
    factory: async_sessionmaker[AsyncSession],
    plugin_id: str,
    role: PluginRole,
    version: str,
    now: datetime,
) -> int:
    """Count a boot attempt (committed before any plugin code runs); returns the new count."""
    key = (TiqoraPluginRuntime.plugin_id == plugin_id) & (TiqoraPluginRuntime.role == role)
    for attempt in (1, 2):
        async with factory() as session:
            # atomic increment: concurrent boots of the same role cannot lose a count
            updated = await session.execute(
                update(TiqoraPluginRuntime)
                .where(key)
                .values(
                    state=RuntimeState.LOADING.value,
                    version=version[:64],
                    boot_attempts=TiqoraPluginRuntime.boot_attempts + 1,
                    updated_at=now,
                )
            )
            if updated.rowcount == 0:  # type: ignore[attr-defined]
                session.add(
                    TiqoraPluginRuntime(
                        plugin_id=plugin_id,
                        role=role,
                        state=RuntimeState.LOADING.value,
                        version=version[:64],
                        boot_attempts=1,
                        updated_at=now,
                    )
                )
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                if attempt == 2:
                    raise
                continue
            count = (
                await session.execute(select(TiqoraPluginRuntime.boot_attempts).where(key))
            ).scalar_one()
            await session.commit()
            return int(count)
    raise AssertionError("unreachable")  # pragma: no cover


async def mark_result(
    factory: async_sessionmaker[AsyncSession],
    plugin_id: str,
    role: PluginRole,
    *,
    state: RuntimeState,
    version: str | None,
    error: str | None,
    now: datetime,
    keep_attempts: bool = False,
) -> None:
    """Record the outcome; the crash counter resets unless quarantined or ``keep_attempts``.

    ``keep_attempts`` is for plugins that were not imported in this boot (disabled, budget
    used up, missing dependency): only a load that actually ran may reset the counter.
    """
    clipped = error[:MAX_ERROR_LEN] if error is not None else None
    for attempt in (1, 2):
        async with factory() as session:
            row = await session.get(TiqoraPluginRuntime, (plugin_id, role))
            quarantined = state is RuntimeState.QUARANTINED
            keep = quarantined or keep_attempts
            if row is None:
                session.add(
                    TiqoraPluginRuntime(
                        plugin_id=plugin_id,
                        role=role,
                        state=state.value,
                        version=version[:64] if version is not None else None,
                        error=clipped,
                        boot_attempts=QUARANTINE_AFTER if quarantined else 0,
                        updated_at=now,
                    )
                )
            else:
                row.state = state.value
                row.version = version[:64] if version is not None else None
                row.error = clipped
                if not keep:
                    row.boot_attempts = 0
                row.updated_at = now
            try:
                await session.commit()
            except IntegrityError:
                if attempt == 2:
                    raise
                continue
            return


async def set_enabled(
    session: AsyncSession,
    plugin_id: str,
    enabled: bool,
    *,
    by: int | None,
    now: datetime,
) -> TiqoraPlugin | None:
    """Switch a plugin on or off (flush only). ``None`` when the plugin is unknown.

    Switching off -> on clears the cursor of the plugin's event hooks, so they restart at
    the current end of the history instead of replaying what happened while off
    (R-REENABLE-BACKLOG). A plain retry after FAILED/QUARANTINED keeps the cursors.
    """
    row = await session.get(TiqoraPlugin, plugin_id)
    if row is None:
        return None
    if row.enabled == enabled:
        return row
    was_enabled = row.enabled
    row.enabled = enabled
    row.changed_by = by
    row.updated_at = now
    if enabled and not was_enabled:
        await session.execute(
            update(TiqoraPluginHook)
            .where(TiqoraPluginHook.plugin_id == plugin_id, TiqoraPluginHook.kind == "event")
            .values(cursor=None)
        )
    await session.flush()
    return row


async def reset_plugin(session: AsyncSession, plugin_id: str, *, now: datetime) -> None:
    """ "Try again": crash counters to 0, QUARANTINED -> DISABLED, circuit breakers closed."""
    await session.execute(
        update(TiqoraPluginRuntime)
        .where(
            TiqoraPluginRuntime.plugin_id == plugin_id,
            TiqoraPluginRuntime.state == RuntimeState.QUARANTINED.value,
        )
        .values(state=RuntimeState.DISABLED.value, updated_at=now)
    )
    await session.execute(
        update(TiqoraPluginRuntime)
        .where(TiqoraPluginRuntime.plugin_id == plugin_id)
        .values(boot_attempts=0)
    )
    await session.execute(
        update(TiqoraPluginHook)
        .where(TiqoraPluginHook.plugin_id == plugin_id)
        .values(tripped_at=None, consecutive_failures=0)
    )
    await session.flush()


# --- hooks (event subscribers and scheduled tasks) -------------------------------------------
#
# The helpers below write with explicit UPDATEs keyed by ``hook_key`` and mirror the change on
# the passed object, so a hook loaded in one session can be booked in another (the dispatcher
# commits every subscriber in its own session). They flush only; the caller commits.


async def ensure_hook(
    session: AsyncSession,
    *,
    hook_key: str,
    plugin_id: str,
    kind: str,
    initial_cursor: int | None,
    now: datetime,
) -> TiqoraPluginHook:
    """The hook row, created at ``initial_cursor`` when missing.

    A NULL cursor (plugin switched off -> on, R-REENABLE-BACKLOG) is set to
    ``initial_cursor`` as well, so a re-enabled subscriber starts at the current end of the
    history. An existing cursor is never moved.
    """
    hook = await session.get(TiqoraPluginHook, hook_key)
    if hook is None:
        hook = TiqoraPluginHook(
            hook_key=hook_key,
            plugin_id=plugin_id,
            kind=kind,
            cursor=initial_cursor,
            consecutive_failures=0,
            total_failures=0,
            created_at=now,
        )
        try:
            # savepoint: a concurrent insert must not roll back the caller's other work
            async with session.begin_nested():
                session.add(hook)
            return hook
        except IntegrityError:
            hook = (
                await session.execute(
                    select(TiqoraPluginHook).where(TiqoraPluginHook.hook_key == hook_key)
                )
            ).scalar_one()
    if hook.cursor is None and initial_cursor is not None:
        hook.cursor = initial_cursor
        await session.flush()
    return hook


async def load_hooks(
    session: AsyncSession, *, plugin_id: str | None = None, kind: str | None = None
) -> dict[str, TiqoraPluginHook]:
    stmt = select(TiqoraPluginHook).order_by(TiqoraPluginHook.hook_key)
    if plugin_id is not None:
        stmt = stmt.where(TiqoraPluginHook.plugin_id == plugin_id)
    if kind is not None:
        stmt = stmt.where(TiqoraPluginHook.kind == kind)
    return {h.hook_key: h for h in (await session.execute(stmt)).scalars()}


async def record_hook_success(
    session: AsyncSession, hook: TiqoraPluginHook, *, cursor: int | None, now: datetime
) -> None:
    """A handled event or a finished task run: failures reset, ``cursor`` stored when given."""
    values: dict[str, object] = {"consecutive_failures": 0, "last_ok_at": now}
    if cursor is not None:
        values["cursor"] = cursor
    await session.execute(
        update(TiqoraPluginHook).where(TiqoraPluginHook.hook_key == hook.hook_key).values(values)
    )
    await session.flush()
    hook.consecutive_failures = 0
    hook.last_ok_at = now
    if cursor is not None:
        hook.cursor = cursor


async def advance_hook_cursor(
    session: AsyncSession, hook: TiqoraPluginHook, *, cursor: int
) -> None:
    """Move the cursor forward past events the subscriber does not want (nothing else changes).

    Never moves it back, and leaves a NULL cursor alone (the plugin was switched off and on
    meanwhile; the next tick restarts it at the end of the history).
    """
    await session.execute(
        update(TiqoraPluginHook)
        .where(
            TiqoraPluginHook.hook_key == hook.hook_key,
            TiqoraPluginHook.cursor.is_not(None),
            TiqoraPluginHook.cursor < cursor,
        )
        .values(cursor=cursor)
    )
    await session.flush()
    if hook.cursor is not None and hook.cursor < cursor:
        hook.cursor = cursor


async def record_hook_failure(
    session: AsyncSession,
    hook: TiqoraPluginHook,
    *,
    error: str,
    now: datetime,
    trip_after: int,
    cursor: int | None = None,
) -> bool:
    """Count a failure; opens the circuit breaker at ``trip_after`` consecutive failures.

    Returns True when this failure tripped it. ``cursor`` (optional) also moves the cursor
    forward past events handled before the failing one in the same run.
    """
    key = TiqoraPluginHook.hook_key == hook.hook_key
    await session.execute(
        update(TiqoraPluginHook)
        .where(key)
        .values(
            consecutive_failures=TiqoraPluginHook.consecutive_failures + 1,
            total_failures=TiqoraPluginHook.total_failures + 1,
            last_error=error[:MAX_ERROR_LEN],
            last_error_at=now,
        )
    )
    if cursor is not None:
        await advance_hook_cursor(session, hook, cursor=cursor)
    consecutive, total, tripped_at = (
        await session.execute(
            select(
                TiqoraPluginHook.consecutive_failures,
                TiqoraPluginHook.total_failures,
                TiqoraPluginHook.tripped_at,
            ).where(key)
        )
    ).one()
    tripped_now = tripped_at is None and consecutive >= trip_after
    if tripped_now:
        await session.execute(update(TiqoraPluginHook).where(key).values(tripped_at=now))
        tripped_at = now
    await session.flush()
    hook.consecutive_failures = int(consecutive)
    hook.total_failures = int(total)
    hook.last_error = error[:MAX_ERROR_LEN]
    hook.last_error_at = now
    hook.tripped_at = tripped_at
    return tripped_now
