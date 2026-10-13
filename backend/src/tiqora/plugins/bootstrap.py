"""Process start: build the registry with core items, then load enabled plugins.

A broken plugin, a broken plugin state or an unreachable database must never keep
Tiqora from starting: every failure below ``register_core`` ends in a frozen, core-only
or partially loaded registry and a log line.
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable, Sequence

import structlog
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.extensions.core import get_registry, install_registry, register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.plugins import store
from tiqora.plugins.compat import check_compat, release_core_version
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import DiscoveredPlugin, discovered_plugins
from tiqora.plugins.loader import (
    PluginLoadTimeout,
    commit_registrar,
    dependency_order,
    import_and_register,
    run_in_daemon_thread,
)
from tiqora.plugins.migrations import MIGRATION_PENDING_MESSAGE, migrations_dir
from tiqora.plugins.registrar import RegistrationRejected
from tiqora.plugins.runtime import (
    RuntimeActivation,
    activate_runtime_plugins,
    gc_runtime_dirs_at_boot,
)
from tiqora.plugins.state import (
    QUARANTINE_AFTER,
    PluginLoadResult,
    PluginRole,
    RuntimeState,
    set_loaded_plugins,
)
from tiqora.plugins.sync_hooks import ensure_sync_hook_rows

log = structlog.get_logger(__name__)

BUDGET_EXHAUSTED_MESSAGE = "Start abgebrochen: Zeitbudget für Erweiterungen aufgebraucht"
STATE_NOT_SAVED_MESSAGE = "Zustand konnte nicht gespeichert werden"


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, RegistrationRejected | PluginLoadTimeout):
        return str(exc)[:2000]  # already a German sentence
    return f"{type(exc).__name__}: {exc}"[:2000]


def _core_only() -> Registry:
    registry = Registry()
    register_core(registry)
    return registry


def _finish(registry: Registry) -> Registry:
    registry.freeze()
    install_registry(registry)
    return registry


#: set by the last ``init_registry``: the core-only fallback was caused by missing plugin tables
_tables_missing = False
BOOT_FAILED_MESSAGE = "Start abgebrochen: Erweiterungen konnten nicht geladen werden"
RETRY_INTERVAL_SECONDS: float = 60.0
RETRY_MAX_SECONDS: float = 1800.0


def is_missing_table_error(exc: BaseException) -> bool:
    """True for "table doesn't exist" on MariaDB (1146) and PostgreSQL (42P01 / UndefinedTable)."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        cur = pending.pop()
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if cur.args and cur.args[0] == 1146:
            return True
        if getattr(cur, "sqlstate", None) == "42P01" or getattr(cur, "pgcode", None) == "42P01":
            return True
        if type(cur).__name__ in {"UndefinedTableError", "UndefinedTable"}:
            return True
        for nxt in (getattr(cur, "orig", None), cur.__cause__, cur.__context__):
            if isinstance(nxt, BaseException):
                pending.append(nxt)
    return False


def plugin_tables_missing() -> bool:
    return _tables_missing


async def init_registry(
    *,
    role: PluginRole,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    _late: bool = False,
) -> Registry:
    """Build + freeze the process registry (core, then enabled plugins) and install it.

    ``_late`` is the retry after a core-only boot (R-BOOT-RETRY): a success swaps the
    content of the existing, still core-only registry in place; a failure changes nothing.
    """
    global _tables_missing
    registry = _core_only()  # a broken core registration still fails the boot
    if settings.plugins_safe_mode:
        log.warning("plugins_safe_mode", role=role)
        set_loaded_plugins([])
        _tables_missing = False
        return _finish(registry)
    active_written: list[str] = []
    try:
        results = await _load_plugins(
            registry, role, settings, session_factory, active_written, status_only=_late
        )
    except Exception as exc:  # R-BOOT-ANYERR: our own plugin path must not block startup
        log.error(
            "plugins_boot_failed",
            role=role,
            error=f"{type(exc).__name__}: {exc}"[:500],
            exc_info=not _late,
        )
        _tables_missing = is_missing_table_error(exc)
        await _correct_active_rows(session_factory, role, active_written)
        if _late:
            return get_registry()
        set_loaded_plugins([])
        return _finish(_core_only())  # drop anything a half-finished load committed
    _tables_missing = False
    await _ensure_sync_hook_rows(registry, session_factory)
    if _late:
        current = get_registry()
        if all(r.owner == "core" for r in current.registrations()):
            current.replace_contents(registry)
            set_loaded_plugins(results)
            return current
        log.warning("plugins_boot_retry_skipped", role=role, reason="registry already in use")
        return current
    set_loaded_plugins(results)
    return _finish(registry)


async def _ensure_sync_hook_rows(
    registry: Registry, factory: async_sessionmaker[AsyncSession]
) -> None:
    """Best effort: a ``sync`` state row per registered hook (every role, idempotent)."""
    try:
        await ensure_sync_hook_rows(registry, factory)
    except Exception as exc:  # noqa: BLE001 - the rows only feed the admin page
        log.warning("plugin_sync_hook_rows_failed", error=f"{type(exc).__name__}: {exc}"[:300])


async def _correct_active_rows(
    factory: async_sessionmaker[AsyncSession], role: PluginRole, plugin_ids: list[str]
) -> None:
    """After a core-only fallback no row of this role may claim a plugin runs here.

    Covers the rows this failed attempt wrote as active (``plugin_ids``) and rows an earlier
    boot left active. Best effort: with the database unreachable nothing can be corrected.
    """
    try:
        await store.fail_active_rows(factory, role, error=BOOT_FAILED_MESSAGE, now=utc_now())
    except Exception as exc:
        if is_missing_table_error(exc):
            return  # nothing stored yet (worker/mcp before the api migrated)
        log.warning(
            "plugin_state_write_failed",
            plugins=plugin_ids,
            role=role,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


async def retry_plugin_boot(
    *,
    role: PluginRole,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    interval: float | None = None,
    max_seconds: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Worker/mcp/ai-worker that booted before the api migrated: retry until the tables exist.

    Every ``interval`` seconds (60) for up to ``max_seconds`` (30 min). The api never
    retries (it runs the migration). Returns True once plugins were loaded.
    """
    if role == "api" or not _tables_missing:
        return False
    step = RETRY_INTERVAL_SECONDS if interval is None else interval
    limit = RETRY_MAX_SECONDS if max_seconds is None else max_seconds
    started = clock()
    attempt = 0
    while clock() - started < limit:
        await sleep(step)
        attempt += 1
        log.info("plugins_boot_retry", role=role, attempt=attempt)
        await init_registry(
            role=role, settings=settings, session_factory=session_factory, _late=True
        )
        if not _tables_missing:
            return True
    log.warning("plugins_boot_retry_gave_up", role=role, attempts=attempt)
    return False


def _requirement_problem(
    plugin: DiscoveredPlugin,
    by_id: dict[str, DiscoveredPlugin],
    results: dict[str, PluginLoadResult],
) -> str | None:
    assert plugin.manifest is not None
    for dep, spec in plugin.manifest.requires.items():
        found = by_id.get(dep)
        if found is None or found.manifest is None:
            return f"Benötigte Erweiterung {dep} ist nicht installiert"
        done = results.get(dep)
        if done is None or done.state is not RuntimeState.ACTIVE:
            return f"Benötigte Erweiterung {dep} ist nicht aktiv"
        try:
            if not SpecifierSet(spec).contains(Version(found.manifest.version), prereleases=True):
                return (
                    f"Benötigte Erweiterung {dep} hat Version {found.manifest.version}, "
                    f"gebraucht wird {spec}"
                )
        except (InvalidSpecifier, InvalidVersion):
            return f"Version der benötigten Erweiterung {dep} ist nicht prüfbar"
    return None


async def _activation_problem_entries(
    factory: async_sessionmaker[AsyncSession],
    activation: RuntimeActivation,
    discovered: Sequence[DiscoveredPlugin],
) -> list[DiscoveredPlugin]:
    """Synthetic INVALID entries for runtime packages that could not be put on ``sys.path``."""
    known = {p.plugin_id for p in discovered}
    wanted = {pid: msg for pid, msg in activation.problems.items() if pid not in known}
    if not wanted:
        return []
    async with factory() as session:
        rows = await store.list_plugin_rows(session)
    out: list[DiscoveredPlugin] = []
    for pid, message in sorted(wanted.items()):
        row = rows.get(pid)
        out.append(
            DiscoveredPlugin(
                pid,
                "",
                row.name if row is not None else "",  # keeps the stored name on sync
                row.version if row is not None else "",
                None,
                None,
                None,
                (),
                message,
            )
        )
    return out


async def _load_plugins(
    registry: Registry,
    role: PluginRole,
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    active_written: list[str],
    *,
    status_only: bool = False,
) -> list[PluginLoadResult]:
    # runtime packages first: their site dirs must be on sys.path before discovery
    activation = await activate_runtime_plugins(
        settings, factory, materialize=True, status_only=status_only
    )
    if not status_only:  # F8/D9: only a booting process collects old runtime dirs
        await gc_runtime_dirs_at_boot(settings, factory)
    plugins: Sequence[DiscoveredPlugin] = discovered_plugins()
    plugins = [*plugins, *await _activation_problem_entries(factory, activation, plugins)]
    if not plugins:
        return []
    try:
        boot = await store.prepare_boot(
            factory, plugins, role=role, auto_enable=settings.plugins_auto_enable
        )
    except Exception:
        log.error("plugins_state_unavailable", role=role)
        raise

    results: dict[str, PluginLoadResult] = {}
    by_id = {p.plugin_id: p for p in plugins}

    async def record(
        plugin: DiscoveredPlugin,
        state: RuntimeState,
        error: str | None,
        contributions: tuple[tuple[str, str], ...] = (),
        imported: bool = False,
    ) -> None:
        version = plugin.manifest.version if plugin.manifest else plugin.dist_version
        results[plugin.plugin_id] = PluginLoadResult(
            plugin.plugin_id, version, state, error, contributions
        )
        if state is RuntimeState.ACTIVE:
            log.info("plugin_loaded", plugin=plugin.plugin_id, role=role, state=state.value)
        elif state is RuntimeState.FAILED:
            log.warning(
                "plugin_load_failed",
                plugin=plugin.plugin_id,
                role=role,
                state=state.value,
                error=error,
            )
        else:
            log.info(
                "plugin_skipped",
                plugin=plugin.plugin_id,
                role=role,
                state=state.value,
                reason=error,
            )
        if not store.storable(plugin.plugin_id):
            return
        try:  # best effort: the state row only feeds the admin page
            await store.mark_result(
                factory,
                plugin.plugin_id,
                role,
                state=state,
                version=version,
                error=error,
                now=utc_now(),
                # only a load that really ran may reset the crash counter
                keep_attempts=not (state is RuntimeState.ACTIVE or imported),
            )
            if state is RuntimeState.ACTIVE:
                active_written.append(plugin.plugin_id)
        except Exception as exc:
            log.warning(
                "plugin_state_write_failed",
                plugin=plugin.plugin_id,
                role=role,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

    for p in plugins:
        if p.problem is not None:
            await record(p, RuntimeState.INVALID, p.problem)
    valid = [p for p in plugins if p.problem is None and p.manifest is not None]
    ordered, cycles = dependency_order(valid)
    for pid, message in cycles.items():
        await record(by_id[pid], RuntimeState.FAILED, message)

    started = time.monotonic()
    budget = float(settings.plugins_boot_budget_seconds)
    disabled_ids = settings.plugins_disabled_ids
    core_version = release_core_version()
    for p in ordered:
        assert p.manifest is not None
        compat = check_compat(p.manifest, core=core_version)
        if compat is not None:
            await record(p, RuntimeState.INCOMPATIBLE, compat)
            continue
        info = boot.get(p.plugin_id)
        if p.plugin_id in disabled_ids or info is None or not info.enabled:
            await record(p, RuntimeState.DISABLED, None)
            continue
        if info.boot_attempts >= QUARANTINE_AFTER:
            await record(
                p,
                RuntimeState.QUARANTINED,
                f"Beim Start {info.boot_attempts}-mal abgestürzt; "
                "bitte prüfen und erneut versuchen",
            )
            continue
        problem = _requirement_problem(p, by_id, results)
        if problem is not None:
            await record(p, RuntimeState.FAILED, problem)
            continue
        if migrations_dir(p) is not None and (
            info.migrated_version != p.manifest.version or info.migration_error
        ):
            detail = info.migration_error or "noch nicht ausgeführt"
            await record(p, RuntimeState.FAILED, f"{MIGRATION_PENDING_MESSAGE}: {detail}"[:2000])
            continue
        remaining = budget - (time.monotonic() - started)
        if remaining < 1:  # too little left for a meaningful import attempt
            await record(p, RuntimeState.FAILED, BUDGET_EXHAUSTED_MESSAGE)
            continue
        try:
            await store.mark_loading(factory, p.plugin_id, role, p.manifest.version, utc_now())
        except Exception as exc:
            log.warning(
                "plugin_state_write_failed",
                plugin=p.plugin_id,
                role=role,
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
            await record(p, RuntimeState.FAILED, STATE_NOT_SAVED_MESSAGE)
            continue
        try:
            context = make_plugin_context(p)
            timeout = min(settings.plugins_import_timeout_seconds, remaining)
            registrar = await run_in_daemon_thread(
                functools.partial(import_and_register, p, context),
                timeout=timeout,
                label=p.plugin_id,
            )
            committed = commit_registrar(registry, registrar)
        except Exception as exc:
            if not isinstance(exc, RegistrationRejected | PluginLoadTimeout):
                log.warning("plugin_load_traceback", plugin=p.plugin_id, role=role, exc_info=True)
            await record(p, RuntimeState.FAILED, _error_text(exc), imported=True)
            continue
        await record(p, RuntimeState.ACTIVE, None, tuple(committed))
    return list(results.values())
