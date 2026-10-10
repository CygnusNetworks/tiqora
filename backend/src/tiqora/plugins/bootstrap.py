"""Process start: build the registry with core items, then load enabled plugins.

A broken plugin, a broken plugin state or an unreachable database must never keep
Tiqora from starting: every failure below ``register_core`` ends in a frozen, core-only
or partially loaded registry and a log line.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Sequence

import structlog
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.extensions.core import install_registry, register_core
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
from tiqora.plugins.registrar import RegistrationRejected
from tiqora.plugins.state import (
    QUARANTINE_AFTER,
    PluginLoadResult,
    PluginRole,
    RuntimeState,
    set_loaded_plugins,
)

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


async def init_registry(
    *,
    role: PluginRole,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> Registry:
    """Build + freeze the process registry (core, then enabled plugins) and install it."""
    registry = _core_only()  # a broken core registration still fails the boot
    if settings.plugins_safe_mode:
        log.warning("plugins_safe_mode", role=role)
        set_loaded_plugins([])
        return _finish(registry)
    try:
        results = await _load_plugins(registry, role, settings, session_factory)
    except Exception as exc:  # R-BOOT-ANYERR: our own plugin path must not block startup
        log.error(
            "plugins_boot_failed",
            role=role,
            error=f"{type(exc).__name__}: {exc}"[:500],
            exc_info=True,
        )
        registry = _core_only()  # drop anything a half-finished load committed
        set_loaded_plugins([])
        return _finish(registry)
    set_loaded_plugins(results)
    return _finish(registry)


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


async def _load_plugins(
    registry: Registry,
    role: PluginRole,
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
) -> list[PluginLoadResult]:
    plugins: Sequence[DiscoveredPlugin] = discovered_plugins()
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
            )
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
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
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
            await record(p, RuntimeState.FAILED, _error_text(exc))
            continue
        await record(p, RuntimeState.ACTIVE, None, tuple(committed))
    return list(results.values())
