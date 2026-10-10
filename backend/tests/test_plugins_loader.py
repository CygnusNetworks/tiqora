from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from tests.plugins.helpers import Installer
from tiqora.channels.registry import CHANNELS
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler
from tiqora.plugins.discovery import DiscoveredPlugin, discover_plugins
from tiqora.plugins.loader import (
    PluginLoadTimeout,
    commit_registrar,
    dependency_order,
    import_and_register,
    make_plugin_context,
    run_in_daemon_thread,
)
from tiqora.plugins.manifest import PluginManifest
from tiqora.plugins.registrar import RegistrationRejected
from tiqora.settings_sections.section import SETTINGS_SECTIONS


class EchoPayload(BaseModel):
    text: str


async def run_echo_job(ctx: Any, payload: EchoPayload) -> None:
    return None


def _discovered(plugin_id: str, requires: dict[str, str] | None = None) -> DiscoveredPlugin:
    manifest = PluginManifest.model_validate(
        {
            "manifest_version": 1,
            "id": plugin_id,
            "short_id": "pp",
            "name": "Test",
            "version": "1.0.0",
            "publisher": "Example GmbH",
            "entry": "x:plugin",
            "sdk": ">=0.1,<0.2",
            "requires": requires or {},
        }
    )
    return DiscoveredPlugin(
        plugin_id, "x:plugin", "x", "1.0.0", Path("."), manifest, None, (), None
    )


def test_import_and_register_fixture(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = import_and_register(p, make_plugin_context(p))
    points = sorted({point.key for point, _ in reg.staged})
    assert points == [
        "core.events.subscribers",
        "core.http.routes",
        "core.job_handlers",
        "core.mcp.tools",
        "core.settings_sections",
        "core.worker.scheduled_tasks",
    ]
    assert len(reg.staged) == 8


def test_commit_registers_with_owner(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    registry = Registry()
    register_core(registry)
    committed = commit_registrar(registry, import_and_register(p, make_plugin_context(p)))
    assert ("core.mcp.tools", "hello_echo") in committed
    assert len(committed) == 8
    owners = {r.owner for r in registry.registrations() if (r.point_key, r.key) in set(committed)}
    assert owners == {"org.example.hello"}
    assert registry.get(CHANNELS, "telegram") is not None  # core untouched


def test_staged_items_carry_the_plugin_context(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    ctx = make_plugin_context(p)
    reg = import_and_register(p, ctx)
    assert ctx.http.host_allowed("api.example.org") and ctx.version == "1.0.0"
    with_ctx = [i for point, i in reg.staged if hasattr(i, "context")]
    assert len(with_ctx) == 6 and all(i.context is ctx for i in with_ctx)


def test_register_raises_leaves_no_items(fixture_plugins: Installer) -> None:
    fixture_plugins("tq_fixture_broken", plugin_id="org.example.broken")
    (p,) = discover_plugins()
    registry = Registry()
    register_core(registry)
    before = len(registry.registrations())
    with pytest.raises(RuntimeError, match="register kaputt"):
        import_and_register(p, make_plugin_context(p))
    assert len(registry.registrations()) == before
    assert registry.get(JOB_HANDLERS, "org.example.broken.one") is None


def test_undeclared_point_fails_registration(fixture_plugins: Installer) -> None:
    fixture_plugins("tq_fixture_undeclared", plugin_id="org.example.undeclared")
    (p,) = discover_plugins()
    with pytest.raises(RegistrationRejected, match="core.mcp.tools"):
        import_and_register(p, make_plugin_context(p))


def test_entry_without_register_rejected(fixture_plugins: Installer) -> None:
    fixture_plugins(entry="tq_fixture_hello.plugin:router")
    (p,) = discover_plugins()
    with pytest.raises(RegistrationRejected, match="register"):
        import_and_register(p, make_plugin_context(p))


def test_duplicate_with_core_rejected_core_kept(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    registry = Registry()
    register_core(registry)
    squatter = JobHandler(
        kind="org.example.hello.echo", payload_model=EchoPayload, run=run_echo_job, title_key="t"
    )
    registry.add(JOB_HANDLERS, squatter)  # owner "core"
    with pytest.raises(RegistrationRejected, match="org.example.hello.echo"):
        commit_registrar(registry, import_and_register(p, make_plugin_context(p)))
    assert (
        registry.get(SETTINGS_SECTIONS, "plugins.org.example.hello") is None
    )  # nothing half-added
    assert registry.get(JOB_HANDLERS, "org.example.hello.echo") is squatter


async def test_run_in_daemon_thread_returns_and_raises() -> None:
    assert await run_in_daemon_thread(lambda: 42, timeout=1) == 42

    def boom() -> None:
        raise ValueError("x")

    with pytest.raises(ValueError, match="x"):
        await run_in_daemon_thread(boom, timeout=1)


async def test_import_timeout_reports_failure(
    fixture_plugins: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TQ_SLOW_SECONDS", "2")
    fixture_plugins("tq_fixture_slow", plugin_id="org.example.slow")
    (p,) = discover_plugins()
    started = time.monotonic()
    with pytest.raises(PluginLoadTimeout, match="0.2"):
        await run_in_daemon_thread(
            lambda: import_and_register(p, make_plugin_context(p)), timeout=0.2
        )
    assert time.monotonic() - started < 1.0
    loaders = [t for t in threading.enumerate() if t.name.startswith("tiqora-plugin-load")]
    assert loaders and all(t.daemon for t in loaders)
    for t in loaders:  # let the abandoned import finish so it does not outlive the test
        t.join(3)


def test_dependency_order_and_cycle() -> None:
    a = _discovered("org.example.a", requires={"org.example.b": ">=1.0"})
    b = _discovered("org.example.b")
    ordered, problems = dependency_order([a, b])
    assert [p.plugin_id for p in ordered] == ["org.example.b", "org.example.a"]
    assert problems == {}
    c = _discovered("org.example.c", requires={"org.example.d": ">=0"})
    d = _discovered("org.example.d", requires={"org.example.c": ">=0"})
    ordered, problems = dependency_order([c, d])
    assert ordered == [] and set(problems) == {"org.example.c", "org.example.d"}


def test_dependency_order_ignores_missing_and_keeps_independent() -> None:
    e = _discovered("org.example.e", requires={"org.example.absent": ">=0"})
    c = _discovered("org.example.c", requires={"org.example.d": ">=0"})
    d = _discovered("org.example.d", requires={"org.example.c": ">=0"})
    ordered, problems = dependency_order([c, e, d])
    assert [p.plugin_id for p in ordered] == ["org.example.e"]
    assert set(problems) == {"org.example.c", "org.example.d"}


def _any(_: Any) -> None:
    return None
