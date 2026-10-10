"""Importing plugins: daemon-thread import with timeout, staging, atomic commit, ordering."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Sequence
from importlib.metadata import entry_points
from typing import Any

from tiqora.extensions.registry import Registry
from tiqora.plugins.context import PluginContext, PluginHttp, PluginTickets
from tiqora.plugins.discovery import ENTRY_POINT_GROUP, DiscoveredPlugin
from tiqora.plugins.registrar import PluginRegistrar, RegistrationRejected


class PluginLoadTimeout(TimeoutError):
    """Importing or registering a plugin took longer than its budget."""


async def run_in_daemon_thread[T](
    fn: Callable[[], T],
    *,
    timeout: float,  # noqa: ASYNC109  (signature fixed by the design)
) -> T:
    """Run ``fn`` in a daemon thread; abandon the thread after ``timeout`` seconds.

    A timed-out import keeps running in the background (its module-level side effects can
    still happen) but never blocks interpreter exit.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[T] = loop.create_future()

    def deliver(result: T | None, exc: BaseException | None) -> None:
        if fut.done():
            return
        if exc is not None:
            fut.set_exception(exc)
        else:
            fut.set_result(result)  # type: ignore[arg-type]

    def target() -> None:
        try:
            result = fn()
        except BaseException as exc:
            outcome: tuple[T | None, BaseException | None] = (None, exc)
        else:
            outcome = (result, None)
        # RuntimeError: the event loop is already closed (abandoned import finished late)
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, *outcome)

    name = getattr(fn, "__name__", "plugin")
    threading.Thread(target=target, name=f"tiqora-plugin-load-{name}", daemon=True).start()
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout)
    except TimeoutError:
        # Retrieve a late exception so asyncio does not log "never retrieved".
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        raise PluginLoadTimeout(f"Laden dauerte länger als {timeout:g} Sekunden") from None


def import_and_register(plugin: DiscoveredPlugin, context: PluginContext) -> PluginRegistrar:
    """Load the entry point and call its ``register``; nothing touches the real registry."""
    if plugin.manifest is None:
        raise RegistrationRejected("Erweiterung hat kein gültiges Manifest")
    matches = [
        ep
        for ep in entry_points(group=ENTRY_POINT_GROUP, name=plugin.plugin_id)
        if ep.value == plugin.entry
    ]
    if not matches:
        raise RegistrationRejected("Einstiegspunkt der Erweiterung nicht gefunden")
    obj: Any = matches[0].load()
    register = getattr(obj, "register", None)
    if not callable(register):
        raise RegistrationRejected("Einstiegspunkt liefert kein Erweiterungsobjekt mit register()")
    registrar = PluginRegistrar(plugin.manifest, context)
    register(registrar)
    return registrar


def commit_registrar(registry: Registry, registrar: PluginRegistrar) -> list[tuple[str, str]]:
    """Add every staged item with the plugin as owner, or none of them."""
    for point, item in registrar.staged:
        key = point.item_key(item)
        if registry.get(point, key) is not None:
            raise RegistrationRejected(
                f"{point.key}: „{key}“ ist bereits vergeben und wird nicht überschrieben"
            )
    committed: list[tuple[str, str]] = []
    for point, item in registrar.staged:
        registry.add(point, item, owner=registrar.plugin_id)
        committed.append((point.key, point.item_key(item)))
    return committed


def make_plugin_context(plugin: DiscoveredPlugin) -> PluginContext:
    if plugin.manifest is None:
        raise RegistrationRejected("Erweiterung hat kein gültiges Manifest")
    return PluginContext(
        plugin_id=plugin.plugin_id,
        version=plugin.manifest.version,
        http=PluginHttp(plugin.manifest.capabilities.outbound_hosts),
        tickets=PluginTickets(),
    )


def dependency_order(
    plugins: Sequence[DiscoveredPlugin],
) -> tuple[list[DiscoveredPlugin], dict[str, str]]:
    """Topological order by ``manifest.requires``; cycles are reported per plugin id.

    Requirements on plugins that are not in ``plugins`` are ignored here (version and
    presence checks belong to the caller). Plugins that wait on a cycle are reported too.
    """
    by_id = {p.plugin_id: p for p in plugins}
    deps = {
        pid: [d for d in (p.manifest.requires if p.manifest else {}) if d in by_id]
        for pid, p in by_id.items()
    }
    ordered: list[DiscoveredPlugin] = []
    placed: set[str] = set()
    remaining = [pid for pid in by_id]
    while remaining:
        ready = [pid for pid in remaining if all(d in placed for d in deps[pid])]
        if not ready:
            break
        for pid in ready:
            ordered.append(by_id[pid])
            placed.add(pid)
        remaining = [pid for pid in remaining if pid not in placed]
    problems = {
        pid: "Abhängigkeiten zwischen Erweiterungen sind zyklisch: " + ", ".join(sorted(remaining))
        for pid in remaining
    }
    # Plugins stuck behind a cycle are never in ``ordered``; the caller marks them failed.
    return ordered, problems
