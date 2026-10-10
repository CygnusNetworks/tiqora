"""Plugin bootstrap (both dialects): safe mode, quarantine, budget, failure containment.

Every test ends with a core-only registry and an empty load result (autouse fixture), so
the process-wide state never leaks into other modules. Plugin ids owned by this module
are ``org.example.plg49*`` plus the fixture plugins; ``_plugins_state_helpers`` deletes them.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import (  # noqa: F401
    T0,
    db_url_fixture,
    factory_fixture,
    runtime_rows,
    seed_plugin_row,
    settings_fixture,
)
from tests.plugins.helpers import Installer
from tiqora.channels.registry import CHANNELS
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions import get_registry, set_registry_for_tests
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.plugins import bootstrap as bootstrap_mod
from tiqora.plugins import store
from tiqora.plugins.bootstrap import BUDGET_EXHAUSTED_MESSAGE, init_registry
from tiqora.plugins.state import (
    PluginStatus,
    RuntimeState,
    is_active,
    loaded_plugins,
    set_loaded_plugins,
    summarize_status,
)
from tiqora.plugins.store import mark_loading
from tiqora.settings_sections.section import SETTINGS_SECTIONS

pytestmark = pytest.mark.db


@pytest.fixture(autouse=True)
def _reset_process_state() -> Iterator[None]:
    yield
    set_registry_for_tests(None)
    set_loaded_plugins([])


async def test_safe_mode_imports_nothing(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    reg = await init_registry(
        role="api",
        settings=settings.model_copy(update={"plugins_safe_mode": True}),
        session_factory=factory,
    )
    assert "tq_fixture_hello" not in sys.modules
    assert {r.owner for r in reg.registrations()} == {"core"}
    assert loaded_plugins() == {} and reg.frozen and get_registry() is reg
    assert await runtime_rows(factory, "org.example.hello") == []  # no DB access either


async def test_safe_mode_does_not_even_discover(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    def boom() -> object:
        raise AssertionError("discovery must not run in safe mode")

    monkeypatch.setattr(bootstrap_mod, "discovered_plugins", boom)
    await init_registry(
        role="api",
        settings=settings.model_copy(update={"plugins_safe_mode": True}),
        session_factory=factory,
    )


async def test_loads_enabled_plugin(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    assert reg.get(SETTINGS_SECTIONS, "plugins.org.example.hello") is not None
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.ACTIVE
    assert loaded_plugins()["org.example.hello"].contributions
    assert is_active("org.example.hello") and reg.frozen and get_registry() is reg
    (row,) = await runtime_rows(factory, "org.example.hello", role="api")
    assert (row.state, row.boot_attempts, row.version) == ("active", 0, "1.0.0")
    assert {r.owner for r in reg.registrations()} == {"core", "org.example.hello"}


async def test_no_plugins_installed_core_only(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    set_registry_for_tests(None)
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    assert reg.get(CHANNELS, "telegram") is not None and loaded_plugins() == {}


async def test_disabled_plugin_not_imported(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    await seed_plugin_row(factory, "org.example.hello", enabled=False)
    await init_registry(role="worker", settings=settings, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.DISABLED
    (row,) = await runtime_rows(factory, "org.example.hello", role="worker")
    assert row.state == "disabled"


async def test_env_disabled_not_imported(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    s = settings.model_copy(update={"plugins_disabled": " org.example.hello , other"})
    assert s.plugins_disabled_ids == {"org.example.hello", "other"}
    await init_registry(role="api", settings=s, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.DISABLED


async def test_auto_enable_off_leaves_new_plugins_disabled(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    s = settings.model_copy(update={"plugins_auto_enable": False})
    await init_registry(role="api", settings=s, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.DISABLED


async def test_crash_loop_quarantines_after_three_boots(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    for _ in range(3):  # three boots that died between mark_loading and mark_result
        await mark_loading(factory, "org.example.hello", "api", "1.0.0", T0)
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert "tq_fixture_hello" not in sys.modules
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.QUARANTINED
    (row,) = await runtime_rows(factory, "org.example.hello", role="api")
    assert row.state == "quarantined" and row.boot_attempts == 3


async def test_quarantine_is_per_role(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    for _ in range(3):
        await mark_loading(factory, "org.example.hello", "worker", "1.0.0", T0)
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.ACTIVE


async def test_two_crashes_still_loads(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    for _ in range(2):
        await mark_loading(factory, "org.example.hello", "api", "1.0.0", T0)
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.ACTIVE
    (row,) = await runtime_rows(factory, "org.example.hello", role="api")
    assert row.boot_attempts == 0


async def test_failed_register_marks_failed_core_ok(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins("tq_fixture_broken", plugin_id="org.example.broken")
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    result = loaded_plugins()["org.example.broken"]
    assert result.state is RuntimeState.FAILED
    assert "RuntimeError: register kaputt" in (result.error or "")
    assert reg.get(JOB_HANDLERS, "org.example.broken.one") is None
    assert reg.get(CHANNELS, "telegram") is not None
    (row,) = await runtime_rows(factory, "org.example.broken", role="api")
    assert row.state == "failed" and row.boot_attempts == 0
    assert "RuntimeError: register kaputt" in (row.error or "")
    assert "Traceback" not in (row.error or "")


async def test_slow_import_fails_with_timeout(
    fixture_plugins: Installer,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TQ_SLOW_SECONDS", "3")
    fixture_plugins("tq_fixture_slow", plugin_id="org.example.slow")
    await init_registry(
        role="api",
        settings=settings.model_copy(update={"plugins_import_timeout_seconds": 1}),
        session_factory=factory,
    )
    assert loaded_plugins()["org.example.slow"].state is RuntimeState.FAILED
    assert "länger als 1 Sekunden" in (loaded_plugins()["org.example.slow"].error or "")


async def test_boot_budget_fails_the_remaining_plugins(
    fixture_plugins: Installer,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-BOOT-BUDGET: two slow plugins, 1 s budget -> the second is not even imported."""
    monkeypatch.setenv("TQ_SLOW_SECONDS", "3")
    fixture_plugins("tq_fixture_slow", plugin_id="org.example.slow")
    fixture_plugins(
        "tq_fixture_slow",
        plugin_id="org.example.plg49slow2",
        dist_name="tq-fixture-slow-two",
        manifest_overrides={"id": "org.example.plg49slow2", "short_id": "slowtwo"},
    )
    s = settings.model_copy(
        update={"plugins_boot_budget_seconds": 1, "plugins_import_timeout_seconds": 20}
    )
    await init_registry(role="api", settings=s, session_factory=factory)
    results = loaded_plugins()
    # plugins load in id order: plg49slow2 first, and it eats the whole budget
    first = results["org.example.plg49slow2"]
    assert first.state is RuntimeState.FAILED and "länger als" in (first.error or "")
    second = results["org.example.slow"]
    assert second.state is RuntimeState.FAILED
    assert second.error == BUDGET_EXHAUSTED_MESSAGE
    assert (
        BUDGET_EXHAUSTED_MESSAGE == "Start abgebrochen: Zeitbudget für Erweiterungen aufgebraucht"
    )
    (row,) = await runtime_rows(factory, "org.example.slow", role="api")
    assert row.state == "failed" and row.boot_attempts == 0  # never counted as a boot


async def test_db_unavailable_loads_no_plugins_core_boots(
    fixture_plugins: Installer, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_plugins()
    monkeypatch.setattr("tiqora.plugins.store.BOOT_RETRY_DELAY_SECONDS", 0.0)
    dead = async_sessionmaker(create_async_engine("mysql+aiomysql://u:p@127.0.0.1:1/x"))
    reg = await init_registry(role="api", settings=settings, session_factory=dead)
    assert "tq_fixture_hello" not in sys.modules
    assert reg.get(CHANNELS, "telegram") is not None and loaded_plugins() == {}
    assert reg.frozen and get_registry() is reg


async def test_missing_tables_give_a_core_only_boot(
    fixture_plugins: Installer,
    db_url: str,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-BOOT-ANYERR: a worker that boots before the api migrated must not crash."""
    fixture_plugins()
    monkeypatch.setattr("tiqora.plugins.store.BOOT_RETRY_DELAY_SECONDS", 0.0)
    engine = create_async_engine(db_url)
    tables = [t for t in TiqoraBase.metadata.sorted_tables if t.name.startswith("tiqora_plugin")]
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: TiqoraBase.metadata.drop_all(c, tables=tables))
        with pytest.raises(ProgrammingError):  # the premise: the state really is unreadable
            await store.prepare_boot(
                factory,
                list(bootstrap_mod.discovered_plugins()),
                role="api",
                auto_enable=True,
                attempts=1,
            )
        reg = await init_registry(role="worker", settings=settings, session_factory=factory)
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, tables=tables))
        await engine.dispose()
    assert "tq_fixture_hello" not in sys.modules
    assert reg.get(CHANNELS, "telegram") is not None and loaded_plugins() == {}
    assert reg.frozen and get_registry() is reg


async def test_failing_discovery_gives_a_core_only_boot(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> object:
        raise PermissionError("kein Zugriff")

    monkeypatch.setattr(bootstrap_mod, "discovered_plugins", boom)
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    assert reg.get(CHANNELS, "telegram") is not None and loaded_plugins() == {}
    assert reg.frozen and get_registry() is reg


async def test_unexpected_error_after_a_load_drops_the_partial_registry(
    fixture_plugins: Installer,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Our own code failing after plugin 1 was committed: core-only, nothing half-loaded."""
    fixture_plugins()
    fixture_plugins(
        "tq_fixture_slow",
        plugin_id="org.example.plg49zzz",
        dist_name="tq-fixture-zzz",
        manifest_overrides={"id": "org.example.plg49zzz", "short_id": "zzz"},
    )
    real = bootstrap_mod.check_compat

    def check(manifest: object, **kw: object) -> str | None:
        if getattr(manifest, "id", "") == "org.example.plg49zzz":
            raise RuntimeError("Kompatibilitätsprüfung kaputt")
        return real(manifest, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(bootstrap_mod, "check_compat", check)
    reg = await init_registry(role="api", settings=settings, session_factory=factory)
    assert "tq_fixture_hello" in sys.modules  # it did load before the failure ...
    assert reg.get(SETTINGS_SECTIONS, "plugins.org.example.hello") is None  # ... but is dropped
    assert {r.owner for r in reg.registrations()} == {"core"}
    assert loaded_plugins() == {} and reg.frozen and get_registry() is reg


async def test_failing_mark_loading_skips_the_plugin(
    fixture_plugins: Installer,
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_plugins()

    async def broken(*a: object, **kw: object) -> int:
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(store, "mark_loading", broken)
    await init_registry(role="api", settings=settings, session_factory=factory)
    result = loaded_plugins()["org.example.hello"]
    assert result.state is RuntimeState.FAILED
    assert result.error == "Zustand konnte nicht gespeichert werden"
    assert "tq_fixture_hello" not in sys.modules


async def test_invalid_plugin_is_recorded_not_imported(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins(drop=["tiqora-plugin.toml"])
    await init_registry(role="api", settings=settings, session_factory=factory)
    result = loaded_plugins()["org.example.hello"]
    assert result.state is RuntimeState.INVALID and "tiqora-plugin.toml" in (result.error or "")
    assert "tq_fixture_hello" not in sys.modules
    (row,) = await runtime_rows(factory, "org.example.hello", role="api")
    assert row.state == "invalid"


async def test_missing_required_plugin_fails(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins(manifest_overrides={"requires": {"org.example.base": ">=1.0"}})
    await init_registry(role="api", settings=settings, session_factory=factory)
    result = loaded_plugins()["org.example.hello"]
    assert result.state is RuntimeState.FAILED and "org.example.base" in (result.error or "")
    assert "tq_fixture_hello" not in sys.modules


async def test_failed_required_plugin_blocks_the_dependant(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins("tq_fixture_broken", plugin_id="org.example.broken")
    fixture_plugins(manifest_overrides={"requires": {"org.example.broken": ">=1.0"}})
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.broken"].state is RuntimeState.FAILED
    hello = loaded_plugins()["org.example.hello"]
    assert hello.state is RuntimeState.FAILED and "org.example.broken" in (hello.error or "")
    assert "tq_fixture_hello" not in sys.modules


async def test_required_plugin_version_mismatch_fails(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins()
    fixture_plugins(
        "tq_fixture_slow",
        plugin_id="org.example.plg49dep",
        dist_name="tq-fixture-dep",
        manifest_overrides={
            "id": "org.example.plg49dep",
            "short_id": "depone",
            "requires": {"org.example.hello": ">=2.0"},
        },
    )
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.ACTIVE
    dep = loaded_plugins()["org.example.plg49dep"]
    assert dep.state is RuntimeState.FAILED
    assert "org.example.hello" in (dep.error or "") and ">=2.0" in (dep.error or "")
    assert "tq_fixture_slow" not in sys.modules


async def test_incompatible_sdk_not_loaded(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    fixture_plugins(manifest_overrides={"sdk": ">=9"})
    await init_registry(role="api", settings=settings, session_factory=factory)
    assert loaded_plugins()["org.example.hello"].state is RuntimeState.INCOMPATIBLE
    assert "tq_fixture_hello" not in sys.modules


def test_startup_wiring_calls_init_registry() -> None:
    import inspect

    from tiqora.api import app as app_mod
    from tiqora.mcp_server import server as mcp_mod
    from tiqora.worker import __main__ as worker_mod

    for fn, role in (
        (app_mod.lifespan, "api"),
        (worker_mod.run_worker, "worker"),
        (mcp_mod._app_lifespan, "mcp"),
    ):
        src = inspect.getsource(fn)
        assert "init_registry(" in src and f'role="{role}"' in src
        assert "get_registry()" not in src.replace("Build + freeze", "")
    worker_src = inspect.getsource(worker_mod.run_worker)
    assert worker_src.index("init_registry(") < worker_src.index(
        "_ensure_legacy_schema_at_worker_start"
    )


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            dict(enabled=True, problem=None, api_state=RuntimeState.ACTIVE, compat_problem=None),
            "active",
        ),
        (
            dict(enabled=False, problem=None, api_state=RuntimeState.ACTIVE, compat_problem=None),
            "restart_required",
        ),
        (
            dict(enabled=True, problem=None, api_state=RuntimeState.DISABLED, compat_problem=None),
            "restart_required",
        ),
        (
            dict(enabled=False, problem=None, api_state=RuntimeState.DISABLED, compat_problem=None),
            "disabled",
        ),
        (dict(enabled=True, problem="x", api_state=None, compat_problem=None), "invalid"),
        (dict(enabled=True, problem=None, api_state=None, compat_problem="y"), "incompatible"),
        (
            dict(
                enabled=True, problem=None, api_state=RuntimeState.QUARANTINED, compat_problem=None
            ),
            "quarantined",
        ),
        (
            dict(enabled=True, problem=None, api_state=RuntimeState.FAILED, compat_problem=None),
            "failed",
        ),
        (dict(enabled=True, problem=None, api_state=None, compat_problem=None), "unknown"),
        # R-STATUS-ROLES: worker/mcp rows count, with the documented precedence
        (
            dict(
                enabled=True,
                problem=None,
                api_state=RuntimeState.ACTIVE,
                compat_problem=None,
                other_states=[RuntimeState.FAILED],
            ),
            "failed",
        ),
        (
            dict(
                enabled=True,
                problem=None,
                api_state=RuntimeState.FAILED,
                compat_problem=None,
                other_states=[RuntimeState.QUARANTINED],
            ),
            "quarantined",
        ),
        (
            dict(enabled=True, problem="x", api_state=RuntimeState.FAILED, compat_problem="y"),
            "invalid",
        ),
        (
            dict(enabled=True, problem=None, api_state=RuntimeState.FAILED, compat_problem="y"),
            "incompatible",
        ),
        (
            dict(
                enabled=True,
                problem=None,
                api_state=RuntimeState.ACTIVE,
                compat_problem=None,
                other_states=[RuntimeState.ACTIVE, RuntimeState.DISABLED],
            ),
            "active",
        ),
    ],
)
def test_summarize_status(kwargs: dict[str, object], expected: PluginStatus) -> None:
    assert summarize_status(**kwargs) == expected  # type: ignore[arg-type]
