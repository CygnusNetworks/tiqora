"""Runtime activation of installed packages in every process (D5, Task 11), both dialects.

Band 62060-62079: package hashes start ``62060``; the plugin used is the fixture
``org.example.tables`` (cleaned by ``_plugins_state_helpers``).
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._plugins_state_helpers import (  # noqa: F401
    T0,
    db_url_fixture,
    factory_fixture,
    runtime_rows,
    settings_fixture,
)
from tests.plugins.helpers import Installer
from tests.plugins.package_builder import build_tqplugin, build_wheel
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginPackage
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import set_registry_for_tests
from tiqora.plugins import packages_store
from tiqora.plugins.bootstrap import init_registry
from tiqora.plugins.discovery import clear_discovery_cache, discover_plugins
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.package import read_tqplugin
from tiqora.plugins.runtime import (
    MISSING_PACKAGE_TEXT,
    NEXT_START_TEXT,
    activate_runtime_plugins,
    image_plugin_ids,
    materialize,
    reset_dir_status_cache,
    version_dir,
)
from tiqora.plugins.signing import TrustLevel
from tiqora.plugins.state import RuntimeState, loaded_plugins, set_loaded_plugins

pytestmark = pytest.mark.db

PID = "org.example.tables"
PKG = "tq_fixture_tables"
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(sys, "path", list(sys.path))  # activation appends to sys.path
    reset_dir_status_cache()
    clear_discovery_cache()
    yield
    for name in [m for m in sys.modules if m == PKG or m.startswith(PKG + ".")]:
        del sys.modules[name]
    set_registry_for_tests(None)
    set_loaded_plugins([])
    reset_dir_status_cache()
    clear_discovery_cache()


@pytest.fixture(name="rt_settings")
def rt_settings_fixture(settings: Settings, tmp_path: Path) -> Settings:
    (tmp_path / "plugins").mkdir()
    return settings.model_copy(update={"plugins_dir": str(tmp_path / "plugins")})


def _package(tmp_path: Path, *, version: str = "1.0.0") -> tuple[bytes, str]:
    wheel = build_wheel(tmp_path / f"w{version}", PKG, plugin_id=PID, version=version)
    data = build_tqplugin(tmp_path / f"w{version}", wheel).read_bytes()
    return data, read_tqplugin(data, max_bytes=20 * 1024 * 1024).sha256


def _report(sha: str, version: str) -> PackageReport:
    return PackageReport(
        sha256=sha,
        size_bytes=3,
        plugin_id=PID,
        short_id="tables",
        name="Tabellen",
        version=version,
        publisher="Example GmbH",
        publisher_id="org.example",
        description="",
        license=None,
        homepage=None,
        support=None,
        categories=[],
        changelog=None,
        trust=TrustLevel.UNSIGNED,
        key_id=None,
        signer=None,
        capabilities=[],
        problems=[],
        warnings=[],
        installed_version=None,
        action="install",
        installable=True,
    )


async def _install_row(
    factory: async_sessionmaker[AsyncSession],
    sha: str,
    *,
    version: str = "1.0.0",
    data: bytes | None = None,
    source: str = "upload",
) -> None:
    async with factory() as s:
        if data is not None:
            await packages_store.store_package(
                s,
                data=data,
                report=_report(sha, version),
                manifest_text="m",
                source="upload",
                repository_id=None,
                uploaded_by=None,
                now=T0,
            )
        existing = await s.get(TiqoraPlugin, PID)
        if existing is None:
            s.add(
                TiqoraPlugin(
                    id=PID,
                    name="Tabellen",
                    version=version,
                    enabled=True,
                    source=source,
                    package_sha256=sha,
                    installed_at=T0,
                    updated_at=T0,
                )
            )
        else:
            existing.version, existing.package_sha256 = version, sha
        await s.commit()


@pytest.fixture(autouse=True)
async def _clean_packages(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[None]:
    async def wipe() -> None:
        async with factory() as s:
            await s.execute(delete(TiqoraPluginPackage).where(TiqoraPluginPackage.plugin_id == PID))
            await s.commit()

    await wipe()
    yield
    await wipe()


async def _row(factory: async_sessionmaker[AsyncSession]) -> TiqoraPlugin:
    async with factory() as s:
        return (await s.execute(select(TiqoraPlugin).where(TiqoraPlugin.id == PID))).scalar_one()


async def test_activation_materialises_from_db_and_discovers(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    data, sha = _package(tmp_path)
    await _install_row(factory, sha, data=data)
    act = await activate_runtime_plugins(rt_settings, factory)
    site = version_dir(rt_settings, PID, "1.0.0", sha) / "site"
    assert act.problems == {} and act.site_dirs == {PID: site}
    assert (site.parent / "COMPLETE").read_text() == sha
    assert str(site) in sys.path
    found = {d.plugin_id: d for d in discover_plugins()}
    assert PID in found and found[PID].problem is None
    assert PKG not in sys.modules  # paths only, no plugin code


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the directory mode")
async def test_activation_uses_existing_complete_dir_when_read_only(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    data, sha = _package(tmp_path)
    site = materialize(rt_settings, PID, "1.0.0", sha, data)
    await _install_row(factory, sha)  # no blob row: only the directory can serve it
    root = rt_settings.plugins_dir
    os.chmod(root, 0o555)
    try:
        reset_dir_status_cache()
        act = await activate_runtime_plugins(rt_settings, factory)
    finally:
        os.chmod(root, 0o755)
    assert act.problems == {} and act.site_dirs == {PID: site}
    assert str(site) in sys.path


async def test_read_only_without_directory_reports_it(
    factory: async_sessionmaker[AsyncSession],
    rt_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, sha = _package(tmp_path)
    await _install_row(factory, sha, data=data)
    off = rt_settings.model_copy(update={"plugins_runtime_install": False})
    act = await activate_runtime_plugins(off, factory)
    assert act.site_dirs == {}
    assert "abgeschaltet" in act.problems[PID]  # the real reason, not "schreibgeschützt"
    assert "schreibgeschützt" not in act.problems[PID]


async def test_materialize_false_reports_next_start(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    data, sha = _package(tmp_path)
    await _install_row(factory, sha, data=data)
    act = await activate_runtime_plugins(rt_settings, factory, materialize=False)
    assert act.problems == {PID: NEXT_START_TEXT} and act.site_dirs == {}
    assert not version_dir(rt_settings, PID, "1.0.0", sha).exists()
    assert not any(str(rt_settings.plugins_dir) in p for p in sys.path)


async def test_missing_blob_marks_invalid_core_boots(
    factory: async_sessionmaker[AsyncSession],
    rt_settings: Settings,
    tmp_path: Path,
    fixture_plugins: Installer,
) -> None:
    fixture_plugins()  # phase-1 image plugin org.example.hello
    _, sha = _package(tmp_path)
    await _install_row(factory, sha)  # row, but neither package row nor directory
    reg = await init_registry(role="api", settings=rt_settings, session_factory=factory)
    assert reg.frozen
    results = loaded_plugins()
    assert results["org.example.hello"].state is RuntimeState.ACTIVE
    assert results[PID].state is RuntimeState.INVALID
    assert results[PID].error == MISSING_PACKAGE_TEXT
    assert [r.state for r in await runtime_rows(factory, PID, "api")] == ["invalid"]
    assert (await _row(factory)).name == "Tabellen"  # the synthetic entry kept the stored name


async def test_image_wins_over_runtime_package(
    factory: async_sessionmaker[AsyncSession],
    rt_settings: Settings,
    tmp_path: Path,
    fixture_plugins: Installer,
) -> None:
    fixture_plugins(PKG, plugin_id=PID)  # the same plugin baked into the image
    data, sha = _package(tmp_path)
    await _install_row(factory, sha, data=data)
    assert image_plugin_ids(rt_settings) == {PID}
    act = await activate_runtime_plugins(rt_settings, factory)
    assert act.site_dirs == {} and act.problems == {}
    row = await _row(factory)
    assert row.source == "image" and row.package_sha256 is None and row.repository_id is None
    assert not version_dir(rt_settings, PID, "1.0.0", sha).exists()
    entries = [d for d in discover_plugins() if d.plugin_id == PID]
    assert len(entries) == 1 and entries[0].problem is None  # no "mehrfach installiert"


async def test_runtime_package_is_not_an_image_plugin(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    data, sha = _package(tmp_path)
    await _install_row(factory, sha, data=data)
    await activate_runtime_plugins(rt_settings, factory)
    clear_discovery_cache()
    assert PID in {d.plugin_id for d in discover_plugins()}
    assert PID not in image_plugin_ids(rt_settings)  # its dist lives below plugins_dir


async def test_activation_twice_keeps_one_dir_and_status_only_never_writes(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    data1, sha1 = _package(tmp_path, version="1.0.0")
    data2, sha2 = _package(tmp_path, version="1.1.0")
    await _install_row(factory, sha1, data=data1)
    first = await activate_runtime_plugins(rt_settings, factory)
    again = await activate_runtime_plugins(rt_settings, factory, status_only=True)
    assert again.site_dirs == {} and again.problems == {}  # already on sys.path: untouched
    # an update lands while the process runs: still one site dir for the id
    await _install_row(factory, sha2, version="1.1.0", data=data2)
    update = await activate_runtime_plugins(rt_settings, factory, status_only=True)
    assert update.site_dirs == {}
    root = str(Path(rt_settings.plugins_dir) / PID)
    assert [p for p in sys.path if p.startswith(root)] == [str(first.site_dirs[PID])]
    assert not version_dir(rt_settings, PID, "1.1.0", sha2).exists()


async def test_status_only_does_not_flip_source(
    factory: async_sessionmaker[AsyncSession],
    rt_settings: Settings,
    tmp_path: Path,
    fixture_plugins: Installer,
) -> None:
    fixture_plugins(PKG, plugin_id=PID)
    data, sha = _package(tmp_path)
    await _install_row(factory, sha, data=data)
    await activate_runtime_plugins(rt_settings, factory, status_only=True)
    row = await _row(factory)
    assert row.source == "upload" and row.package_sha256 == sha


@pytest.mark.parametrize("code", [1146, None])
async def test_activation_without_tables_is_empty(
    factory: async_sessionmaker[AsyncSession],
    rt_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    code: int | None,
) -> None:
    class Gone(Exception):
        sqlstate = "42P01"

    orig: Exception = Exception(code, "no such table") if code else Gone("relation missing")

    async def boom(session: AsyncSession) -> list[TiqoraPlugin]:
        raise OperationalError("SELECT 1", {}, orig)

    monkeypatch.setattr(packages_store, "installed_runtime_rows", boom)
    act = await activate_runtime_plugins(rt_settings, factory)
    assert act.site_dirs == {} and act.problems == {}


async def test_other_database_errors_propagate(
    factory: async_sessionmaker[AsyncSession],
    rt_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(session: AsyncSession) -> list[TiqoraPlugin]:
        raise OperationalError("SELECT 1", {}, Exception(2006, "gone away"))

    monkeypatch.setattr(packages_store, "installed_runtime_rows", boom)
    with pytest.raises(OperationalError):
        await activate_runtime_plugins(rt_settings, factory)


async def test_safe_mode_lists_runtime_plugins_without_import(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    data, sha = _package(tmp_path)
    materialize(rt_settings, PID, "1.0.0", sha, data)  # the list never unpacks
    await _install_row(factory, sha)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(rt_settings.model_copy(update={"plugins_safe_mode": True}))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ADMIN
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert body["safe_mode"] is True
    entry = next(p for p in body["plugins"] if p["id"] == PID)
    assert entry["status"] == "unknown"
    assert entry["status_message"] == "Abgesicherter Modus: nicht geladen"
    assert entry["version"] == "1.0.0" and entry["source"] == "upload"
    assert PKG not in sys.modules


async def test_admin_list_shows_activation_problem_as_invalid(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    _, sha = _package(tmp_path)
    await _install_row(factory, sha)  # nothing materialised, list never unpacks

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(rt_settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ADMIN
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/plugins")
    entry = next(p for p in resp.json()["plugins"] if p["id"] == PID)
    assert entry["status"] == "invalid" and entry["status_message"] == NEXT_START_TEXT


async def test_admin_list_after_update_without_restart(
    factory: async_sessionmaker[AsyncSession], rt_settings: Settings, tmp_path: Path
) -> None:
    """F1: the install job leaves a COMPLETE 1.1.0 dir next to the active 1.0.0 one."""
    data1, sha1 = _package(tmp_path, version="1.0.0")
    data2, sha2 = _package(tmp_path, version="1.1.0")
    await _install_row(factory, sha1, data=data1)
    first = await activate_runtime_plugins(rt_settings, factory)  # boot
    # D9 step 2 + row update, as the install job does, while the process keeps running
    new_site = materialize(rt_settings, PID, "1.1.0", sha2, data2)
    assert new_site.parent.joinpath("COMPLETE").exists()
    await _install_row(factory, sha2, version="1.1.0", data=data2)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(rt_settings)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: ADMIN
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/plugins")
    assert resp.status_code == 200
    entries = [p for p in resp.json()["plugins"] if p["id"] == PID]
    assert len(entries) == 1
    entry = entries[0]
    assert "mehrfach installiert" not in (entry["status_message"] or "")
    assert entry["status"] != "invalid"
    assert entry["source"] == "upload"
    root = str(Path(rt_settings.plugins_dir) / PID)
    assert [p for p in sys.path if p.startswith(root)] == [str(first.site_dirs[PID])]
    row = await _row(factory)
    assert row.source == "upload" and row.package_sha256 == sha2 and row.version == "1.1.0"
