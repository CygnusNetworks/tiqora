"""Admin plugin API (``/api/v1/admin/plugins``) on both dialects.

Plugin rows of ``org.example.hello`` (and ``org.example.plg49*``) are removed by the
``factory`` fixture of ``_plugins_state_helpers``.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._plugin_points import PLUGIN_ID as POINTS_ID
from tests._plugin_points import boot_points_plugin
from tests._plugins_state_helpers import (  # noqa: F401
    T0,
    db_url_fixture,
    factory_fixture,
    seed_plugin_row,
    session_fixture,
)
from tests.plugins.helpers import Installer
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginHook, TiqoraPluginPackage
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins import store
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import discover_plugins
from tiqora.plugins.inspect import CapabilityLine, PackageReport
from tiqora.plugins.loader import commit_registrar, import_and_register
from tiqora.plugins.migrations import MIGRATION_PENDING_MESSAGE
from tiqora.plugins.repositories import UpdateInfo
from tiqora.plugins.runtime import reset_dir_status_cache
from tiqora.plugins.signing import TrustLevel
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins
from tiqora.plugins.sync_hooks import SYNC_HOOKS, ensure_sync_hook_rows

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/plugins"
PLUGIN_ID = "org.example.hello"
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
AGENT = AuthenticatedUser(
    id=7, login="agent@example.org", first_name="Ann", last_name="Agent", auth_method="session"
)


@pytest.fixture(autouse=True)
def _reset_plugin_state() -> Iterator[None]:
    yield
    set_registry_for_tests(None)
    set_loaded_plugins([])


def _app(
    factory: async_sessionmaker[AsyncSession],
    *,
    safe_mode: bool = False,
    admin: bool = True,
    **settings_kw: Any,
) -> Any:
    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test", plugins_safe_mode=safe_mode, **settings_kw))
    app.dependency_overrides[get_db] = _db
    if admin:
        app.dependency_overrides[get_admin_user] = lambda: ROOT
    else:
        app.dependency_overrides[get_current_user] = lambda: AGENT
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(name="admin_client")
async def admin_client_fixture(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async with _client(_app(factory)) as c:
        yield c


@pytest.fixture(name="admin_client_safe_mode")
async def admin_client_safe_mode_fixture(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    async with _client(_app(factory, safe_mode=True)) as c:
        yield c


@pytest.fixture(name="agent_client")
async def agent_client_fixture(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    async def not_admin(self: PermissionEngine, user_id: int) -> bool:
        return False

    monkeypatch.setattr(PermissionEngine, "is_admin", not_admin)
    async with _client(_app(factory, admin=False)) as c:
        yield c


@pytest.fixture(name="seeded_runtime")
async def seeded_runtime_fixture(
    fixture_plugins: Installer, factory: async_sessionmaker[AsyncSession]
) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    reg.freeze()
    set_registry_for_tests(reg)
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.ACTIVE, None, ())])
    async with factory() as s:
        await store.sync_discovered(s, [p], auto_enable=True, now=T0)
        await s.commit()
    await store.mark_result(
        factory, PLUGIN_ID, "api", state=RuntimeState.ACTIVE, version="1.0.0", error=None, now=T0
    )


async def _plugin_row(factory: async_sessionmaker[AsyncSession], plugin_id: str) -> TiqoraPlugin:
    async with factory() as s:
        return (
            await s.execute(select(TiqoraPlugin).where(TiqoraPlugin.id == plugin_id))
        ).scalar_one()


async def _seed_tripped_hook(factory: async_sessionmaker[AsyncSession], key: str) -> None:
    kind, plugin_id, _ = key.split(":", 2)
    async with factory() as s:
        hook = await store.ensure_hook(
            s, hook_key=key, plugin_id=plugin_id, kind=kind, initial_cursor=5, now=T0
        )
        await store.record_hook_failure(s, hook, error="kaputt", now=T0, trip_after=1)
        await s.commit()


def _by_id(body: dict[str, Any], plugin_id: str) -> dict[str, Any]:
    (p,) = [x for x in body["plugins"] if x["id"] == plugin_id]
    return p  # type: ignore[no-any-return]


async def test_list_active_plugin(
    fixture_plugins: Installer, admin_client: AsyncClient, seeded_runtime: None
) -> None:
    body = (await admin_client.get(BASE)).json()
    assert body["safe_mode"] is False
    p = _by_id(body, PLUGIN_ID)
    assert p["status"] == "active" and p["enabled"] is True and p["publisher"] == "Example GmbH"
    assert p["status_message"] is None and p["version"] == "1.0.0"
    assert p["name"] == "Hallo-Erweiterung" and p["categories"] == ["tickets"]
    assert {"kind": "outbound_host", "value": "api.example.org"} in p["capabilities"]
    assert {"kind": "frontend_slot", "value": "ticket.sidebar"} in p["capabilities"]
    # R-PUBLIC-ROUTES: the unauthenticated-interface capability is visible
    assert {"kind": "extension_point", "value": "core.http.public_routes"} in p["capabilities"]
    assert {"point_key": "core.mcp.tools", "key": "hello_echo"} in p["contributions"]
    assert p["settings_section"] == "plugins.org.example.hello"
    assert {r["role"] for r in p["runtime"]} == {"api"}
    assert p["installed_at"] is not None


async def test_hooks_listed_with_schedule(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_tripped_hook(factory, "task:org.example.hello:tick")
    await _seed_tripped_hook(factory, "event:org.example.hello:flaky")
    hooks = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)["hooks"]
    assert [h["key"] for h in hooks] == [
        "event:org.example.hello:flaky",
        "task:org.example.hello:tick",
    ]
    flaky, tick = hooks
    assert flaky["kind"] == "event" and flaky["name"] == "flaky" and flaky["schedule"] is None
    assert tick["kind"] == "task" and tick["name"] == "tick" and tick["schedule"] == "jede Minute"
    assert tick["tripped"] is True and tick["last_error"] == "kaputt"


async def test_sync_hooks_listed_with_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    admin_client: AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    registry = boot_points_plugin(tmp_path, monkeypatch)
    (found,) = [d for d in discover_plugins() if d.plugin_id == POINTS_ID]
    async with factory() as s:
        await store.sync_discovered(s, [found], auto_enable=True, now=T0)
        await s.commit()
    await ensure_sync_hook_rows(registry, factory)
    try:
        hooks = _by_id((await admin_client.get(BASE)).json(), POINTS_ID)["hooks"]
        assert {h["kind"] for h in hooks} == {"sync"}
        by_op = {h["schedule"]: h for h in hooks}
        assert sorted(by_op) == sorted(h.operation for h in registry.items(SYNC_HOOKS))
        create = by_op["ticket.create"]
        assert create["key"] == f"sync:{POINTS_ID}:ticket_create"
        assert create["name"] == "ticket_create" and create["tripped"] is False
    finally:
        async with factory() as s:
            await s.execute(delete(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == POINTS_ID))
            await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id == POINTS_ID))
            await s.commit()


async def test_runtime_roles_sorted_and_worker_failure_named(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await store.mark_result(
        factory, PLUGIN_ID, "mcp", state=RuntimeState.ACTIVE, version="1.0.0", error=None, now=T0
    )
    await store.mark_result(
        factory,
        PLUGIN_ID,
        "worker",
        state=RuntimeState.FAILED,
        version="1.0.0",
        error="RuntimeError: kaputt",
        now=T0,
    )
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert [r["role"] for r in p["runtime"]] == ["api", "worker", "mcp"]
    assert p["status"] == "failed"  # api active, worker failed
    assert p["status_message"] == "Hintergrunddienst: RuntimeError: kaputt"


async def test_ai_worker_failure_named_and_sorted_last(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await store.mark_result(
        factory,
        PLUGIN_ID,
        "ai-worker",
        state=RuntimeState.FAILED,
        version="1.0.0",
        error="RuntimeError: kaputt",
        now=T0,
    )
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert [r["role"] for r in p["runtime"]] == ["api", "ai-worker"]
    assert p["status"] == "failed"
    assert p["status_message"] == "KI-Hintergrunddienst: RuntimeError: kaputt"


async def test_no_ai_worker_row_is_not_a_problem(
    fixture_plugins: Installer, admin_client: AsyncClient, seeded_runtime: None
) -> None:
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert p["status"] == "active"
    assert "ai-worker" not in {r["role"] for r in p["runtime"]}


async def test_quarantined_worker_message(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await store.mark_result(
        factory,
        PLUGIN_ID,
        "worker",
        state=RuntimeState.QUARANTINED,
        version=None,
        error=None,
        now=T0,
    )
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert p["status"] == "quarantined"
    assert p["status_message"].startswith("Hintergrunddienst: Beim Start wiederholt abgestürzt")


async def test_never_started_is_unknown(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    async with factory() as s:
        await store.sync_discovered(s, [p], auto_enable=True, now=T0)
        await s.commit()
    got = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert got["status"] == "unknown" and (
        got["status_message"] == "Weboberfläche und Schnittstelle: Noch nicht gestartet."
    )
    assert got["runtime"] == []


async def test_plugin_gone_from_image_is_invalid(
    admin_client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    await seed_plugin_row(factory, "org.example.plg49gone", enabled=True)
    got = _by_id((await admin_client.get(BASE)).json(), "org.example.plg49gone")
    assert got["status"] == "invalid"
    assert got["status_message"] == "Nicht mehr im Image enthalten"
    assert got["capabilities"] == [] and got["contributions"] == []


async def test_disable_needs_restart(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    r = await admin_client.put(f"{BASE}/{PLUGIN_ID}/enabled", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["status"] == "restart_required"
    assert r.json()["status_message"] == (
        "Änderung wird nach einem Neustart von Tiqora wirksam. Sie können Tiqora unten neu starten."
    )
    row = await _plugin_row(factory, PLUGIN_ID)
    assert row.enabled is False and row.changed_by == 1


async def test_enable_discovered_plugin_without_row(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    fixture_plugins()
    r = await admin_client.put(f"{BASE}/{PLUGIN_ID}/enabled", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert (await _plugin_row(factory, PLUGIN_ID)).enabled is True


async def test_reset_clears_quarantine_and_hooks(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    fixture_plugins()
    await store.mark_result(
        factory,
        PLUGIN_ID,
        "worker",
        state=RuntimeState.QUARANTINED,
        version="1.0.0",
        error=None,
        now=T0,
    )
    await _seed_tripped_hook(factory, "event:org.example.hello:flaky")
    r = await admin_client.post(f"{BASE}/{PLUGIN_ID}/reset")
    assert r.status_code == 200
    worker = [x for x in r.json()["runtime"] if x["role"] == "worker"][0]
    assert worker["state"] == "disabled" and worker["boot_attempts"] == 0
    (hook,) = [h for h in r.json()["hooks"] if h["key"] == "event:org.example.hello:flaky"]
    assert hook["tripped"] is False and hook["consecutive_failures"] == 0


async def test_safe_mode_lists_without_import(
    fixture_plugins: Installer, admin_client_safe_mode: AsyncClient
) -> None:
    fixture_plugins()
    body = (await admin_client_safe_mode.get(BASE)).json()
    assert body["safe_mode"] is True and [p["id"] for p in body["plugins"]] == [PLUGIN_ID]
    assert body["plugins"][0]["contributions"] == []
    assert "tq_fixture_hello" not in sys.modules


async def test_role_labels_follow_ui_wording(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    for role, label in (
        ("api", "Weboberfläche und Schnittstelle"),
        ("mcp", "KI-Schnittstelle (MCP)"),
    ):
        await store.mark_result(
            factory,
            PLUGIN_ID,
            role,
            state=RuntimeState.FAILED,
            version="1.0.0",
            error="Fehler",
            now=T0,
        )
        p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
        assert p["status_message"] == f"{label}: Fehler"
        await store.mark_result(
            factory, PLUGIN_ID, role, state=RuntimeState.ACTIVE, version="1.0.0", error=None, now=T0
        )


async def test_safe_mode_reports_enabled_plugins_as_unknown(
    fixture_plugins: Installer, admin_client_safe_mode: AsyncClient, seeded_runtime: None
) -> None:
    # the rows still say "active" from the last normal boot
    body = (await admin_client_safe_mode.get(BASE)).json()
    p = _by_id(body, PLUGIN_ID)
    assert body["safe_mode"] is True and p["enabled"] is True
    assert p["status"] == "unknown"
    assert p["status_message"] == "Abgesicherter Modus: nicht geladen"
    assert [r["state"] for r in p["runtime"]] == ["active"]  # last known state stays visible


async def test_unknown_plugin_404(admin_client: AsyncClient) -> None:
    put = await admin_client.put(f"{BASE}/org.example.nope/enabled", json={"enabled": True})
    assert put.status_code == 404
    assert (await admin_client.post(f"{BASE}/org.example.nope/reset")).status_code == 404


async def test_non_admin_403(agent_client: AsyncClient) -> None:
    assert (await agent_client.get(BASE)).status_code == 403
    put = await agent_client.put(f"{BASE}/{PLUGIN_ID}/enabled", json={"enabled": True})
    assert put.status_code == 403
    assert (await agent_client.post(f"{BASE}/{PLUGIN_ID}/reset")).status_code == 403


# ---------------------------------------------------------------- Task 20: install state fields


def _report(sha: str, plugin_id: str, *tables: str) -> PackageReport:
    return PackageReport(
        sha256=sha,
        size_bytes=3,
        plugin_id=plugin_id,
        short_id="tables",
        name="Tabellen",
        version="1.0.0",
        publisher="Example GmbH",
        publisher_id="org.example",
        description="",
        license=None,
        homepage=None,
        support=None,
        categories=[],
        changelog=None,
        trust=TrustLevel.VERIFIED_PUBLISHER,
        key_id="k1",
        signer="Example GmbH",
        capabilities=[CapabilityLine(kind="table", value=t) for t in tables],
        problems=[],
        warnings=[],
        installed_version=None,
        action="install",
        installable=True,
    )


async def _make_runtime(
    factory: async_sessionmaker[AsyncSession], plugin_id: str, *tables: str
) -> None:
    """Turn the seeded row of ``plugin_id`` into an uploaded, verified runtime plugin."""
    sha = "ab" * 32
    async with factory() as s:
        s.add(
            TiqoraPluginPackage(
                sha256=sha,
                plugin_id=plugin_id,
                version="1.0.0",
                size_bytes=3,
                data=b"abc",
                manifest="m",
                report=_report(sha, plugin_id, *tables).model_dump_json(),
                signature_status="verified_publisher",
                key_id="k1",
                source="upload",
                repository_id=None,
                uploaded_by=1,
                uploaded_at=T0,
                installed_at=T0,
            )
        )
        row = (
            await s.execute(select(TiqoraPlugin).where(TiqoraPlugin.id == plugin_id))
        ).scalar_one()
        row.source = "upload"
        row.package_sha256 = sha
        row.signature_status = "verified_publisher"
        row.signer = "Example GmbH"
        await s.commit()


@pytest.fixture(autouse=True)
async def _drop_packages(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    yield
    async with factory() as s:
        await s.execute(delete(TiqoraPluginPackage).where(TiqoraPluginPackage.sha256 == "ab" * 32))
        await s.commit()


async def test_list_runtime_install_flags(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    reset_dir_status_cache()
    async with _client(_app(factory, plugins_dir=str(tmp_path))) as c:
        body = (await c.get(BASE)).json()
    assert body["runtime_install"] is True and body["runtime_install_reason"] is None
    assert body["allow_unsigned"] is False and body["max_package_mb"] == 15
    assert body["coordinated_restart"] is True
    reset_dir_status_cache()
    missing = tmp_path / "gibt-es-nicht"
    async with _client(
        _app(
            factory,
            plugins_dir=str(missing),
            plugins_allow_unsigned=True,
            plugins_max_package_mb=7,
            plugins_coordinated_restart=False,
        )
    ) as c:
        body = (await c.get(BASE)).json()
    reset_dir_status_cache()
    assert body["runtime_install"] is False
    assert str(missing) in body["runtime_install_reason"]
    assert body["allow_unsigned"] is True and body["max_package_mb"] == 7
    assert body["coordinated_restart"] is False


async def test_runtime_plugin_fields(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    image = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert image["source"] == "image" and image["signature_status"] is None
    assert image["signer"] is None and image["can_uninstall"] is False
    assert image["migration_error"] is None and image["update"] is None
    await _make_runtime(factory, PLUGIN_ID)
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert p["source"] == "upload" and p["signature_status"] == "verified_publisher"
    assert p["signer"] == "Example GmbH" and p["can_uninstall"] is True


async def test_image_row_gone_from_image_can_be_uninstalled(
    admin_client: AsyncClient, factory: async_sessionmaker[AsyncSession]
) -> None:
    await seed_plugin_row(factory, "org.example.plg49gone", enabled=True)
    p = _by_id((await admin_client.get(BASE)).json(), "org.example.plg49gone")
    assert p["status"] == "invalid" and p["can_uninstall"] is True
    assert p["signature_status"] is None


async def test_migration_error_surfaces_as_failed(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        row = (
            await s.execute(select(TiqoraPlugin).where(TiqoraPlugin.id == PLUGIN_ID))
        ).scalar_one()
        row.migration_error = "Spalte fehlt"
        await s.commit()
    await store.mark_result(
        factory,
        PLUGIN_ID,
        "api",
        state=RuntimeState.FAILED,
        version="1.0.0",
        error=f"{MIGRATION_PENDING_MESSAGE}: Spalte fehlt",
        now=T0,
    )
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert p["status"] == "failed" and p["migration_error"] == "Spalte fehlt"
    assert MIGRATION_PENDING_MESSAGE in p["status_message"]


async def test_update_field(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(session: AsyncSession, *, settings: Settings) -> dict[str, UpdateInfo]:
        return {PLUGIN_ID: UpdateInfo("1.2.0", 5, "Beispiel-Quelle", False)}

    monkeypatch.setattr("tiqora.api.v1.admin.plugins.available_updates", fake)
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    assert p["update"] == {
        "version": "1.2.0",
        "repository_id": 5,
        "repository_name": "Beispiel-Quelle",
        "image_plugin": False,
    }


async def test_update_failure_does_not_break_the_list(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(session: AsyncSession, *, settings: Settings) -> dict[str, UpdateInfo]:
        raise RuntimeError("index kaputt")

    monkeypatch.setattr("tiqora.api.v1.admin.plugins.available_updates", boom)
    r = await admin_client.get(BASE)
    assert r.status_code == 200
    assert _by_id(r.json(), PLUGIN_ID)["update"] is None


async def test_table_capabilities_listed(
    fixture_plugins: Installer,
    admin_client: AsyncClient,
    seeded_runtime: None,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _make_runtime(factory, PLUGIN_ID, "tqp_tables_note", "tqp_tables_tag")
    p = _by_id((await admin_client.get(BASE)).json(), PLUGIN_ID)
    tables = [c["value"] for c in p["capabilities"] if c["kind"] == "table"]
    assert tables == ["tqp_tables_note", "tqp_tables_tag"]


async def test_restart_required_message_mentions_restart_button(
    fixture_plugins: Installer, admin_client: AsyncClient, seeded_runtime: None
) -> None:
    r = await admin_client.put(f"{BASE}/{PLUGIN_ID}/enabled", json={"enabled": False})
    assert r.json()["status"] == "restart_required"
    assert r.json()["status_message"] == (
        "Änderung wird nach einem Neustart von Tiqora wirksam. Sie können Tiqora unten neu starten."
    )
