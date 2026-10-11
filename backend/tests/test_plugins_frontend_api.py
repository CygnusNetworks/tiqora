"""Frontend plugin list and plugin asset serving (``/api/v1/plugins``; no DB)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests.plugins.helpers import Installer
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_redis
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import discover_plugins
from tiqora.plugins.loader import commit_registrar, import_and_register
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins

AGENT = AuthenticatedUser(
    id=7, login="agent@example.org", first_name="Ann", last_name="Agent", auth_method="session"
)
PLUGIN_ID = "org.example.hello"
ASSETS = f"/api/v1/plugins/{PLUGIN_ID}/assets"


class _FakeSession:
    async def rollback(self) -> None:
        return None


class _FakeRedis:
    async def set(self, *a: Any, **k: Any) -> None:
        return None


async def _no_db() -> Any:
    yield _FakeSession()


@pytest.fixture(autouse=True)
def _reset_plugin_state() -> Iterator[None]:
    yield
    set_registry_for_tests(None)
    set_loaded_plugins([])


def _set_admin(monkeypatch: pytest.MonkeyPatch, is_admin: bool) -> None:
    async def fake(self: PermissionEngine, user_id: int) -> bool:
        return is_admin

    monkeypatch.setattr(PermissionEngine, "is_admin", fake)


def _make_app(
    fixture_plugins: Installer,
    *,
    safe_mode: bool = False,
    state: RuntimeState = RuntimeState.ACTIVE,
    authenticated: bool = True,
) -> Any:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    if not safe_mode:  # safe mode never imports plugin code
        commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    reg.freeze()
    set_registry_for_tests(reg)
    set_loaded_plugins([] if safe_mode else [PluginLoadResult(PLUGIN_ID, "1.0.0", state, None, ())])
    app = create_app(Settings(environment="test", plugins_safe_mode=safe_mode))
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_redis] = lambda: _FakeRedis()
    if authenticated:
        app.dependency_overrides[get_current_user] = lambda: AGENT
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_frontend_list_for_agent_hides_admin_only(
    fixture_plugins: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_admin(monkeypatch, False)
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get("/api/v1/plugins/frontend")
    assert r.status_code == 200
    (p,) = r.json()
    assert p["id"] == PLUGIN_ID
    assert p["asset_base"] == "/api/v1/plugins/org.example.hello/assets/1.0.0/"
    assert p["entry"] == "web/index.js" and p["styles"] == ["web/hello.css"]
    assert p["locales"] == ["de", "en"]
    keys = {(c["slot"], c["key"]) for c in p["contributions"]}
    assert ("agent.route", "admin") not in keys and ("agent.nav", "admin") not in keys
    assert ("agent.route", "overview") in keys and ("agent.nav", "overview") in keys
    assert ("ticket.sidebar", "info") in keys and ("dashboard.widget", "weather") in keys
    widget = next(c for c in p["contributions"] if c["slot"] == "dashboard.widget")
    assert widget["size"]["default"] == [4, 2]  # R-TS-REQUIRED-DEFAULTS: defaults are serialized
    assert widget["version"] == 1 and widget["permission"] is None


async def test_frontend_list_for_admin_includes_admin_only(
    fixture_plugins: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_admin(monkeypatch, True)
    async with _client(_make_app(fixture_plugins)) as c:
        (p,) = (await c.get("/api/v1/plugins/frontend")).json()
    keys = {(c["slot"], c["key"]) for c in p["contributions"]}
    assert ("agent.route", "admin") in keys and ("agent.nav", "admin") in keys


async def test_frontend_list_requires_login(fixture_plugins: Installer) -> None:
    app = _make_app(fixture_plugins, authenticated=False)
    async with _client(app) as c:
        assert (await c.get("/api/v1/plugins/frontend")).status_code == 401


async def test_frontend_list_empty_in_safe_mode_or_inactive(
    fixture_plugins: Installer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_admin(monkeypatch, True)
    async with _client(_make_app(fixture_plugins, safe_mode=True)) as c:
        safe = await c.get("/api/v1/plugins/frontend")
    assert safe.status_code == 200 and safe.json() == []
    async with _client(_make_app(fixture_plugins, state=RuntimeState.FAILED)) as c:
        failed = await c.get("/api/v1/plugins/frontend")
    assert failed.status_code == 200 and failed.json() == []


async def test_asset_served_with_headers(fixture_plugins: Installer) -> None:
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get(f"{ASSETS}/1.0.0/web/index.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers.get("content-security-policy") != "sandbox"  # only SVG is sandboxed
    assert b"definePlugin" in r.content


async def test_css_media_type(fixture_plugins: Installer) -> None:
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get(f"{ASSETS}/1.0.0/web/hello.css")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")


async def test_svg_is_sandboxed(fixture_plugins: Installer) -> None:
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get(f"{ASSETS}/1.0.0/web/logo.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["content-security-policy"] == "sandbox"
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path",
    [
        "2.0.0/web/index.js",  # not the loaded version
        "1.0.0/web/%2e%2e/tiqora-plugin.toml",  # traversal (decoded by the server)
        "1.0.0/web/../tiqora-plugin.toml",
        "1.0.0/tiqora-plugin.toml",
        "1.0.0/plugin.py",
        "1.0.0/web/missing.js",
        "1.0.0/i18n/de.txt",
    ],
)
async def test_asset_404s(fixture_plugins: Installer, path: str) -> None:
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get(f"{ASSETS}/{path}")
    assert r.status_code == 404
    assert r.json()["detail"] == "Datei nicht gefunden"  # from the handler, not a route miss


async def test_asset_404_for_inactive_plugin_and_unknown_plugin(
    fixture_plugins: Installer,
) -> None:
    async with _client(_make_app(fixture_plugins, state=RuntimeState.FAILED)) as c:
        assert (await c.get(f"{ASSETS}/1.0.0/web/index.js")).status_code == 404
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get("/api/v1/plugins/org.example.nope/assets/1.0.0/web/index.js")
    assert r.status_code == 404


async def test_asset_requires_login(fixture_plugins: Installer) -> None:
    app = _make_app(fixture_plugins, authenticated=False)
    async with _client(app) as c:
        assert (await c.get(f"{ASSETS}/1.0.0/web/index.js")).status_code == 401


async def test_i18n_asset(fixture_plugins: Installer) -> None:
    async with _client(_make_app(fixture_plugins)) as c:
        r = await c.get(f"{ASSETS}/1.0.0/i18n/de.json")
    assert r.status_code == 200 and r.json()["panel"]["title"] == "Hallo"


async def test_symlink_out_of_the_package_is_404(fixture_plugins: Installer, tmp_path: Any) -> None:
    app = _make_app(fixture_plugins)
    (plugin,) = discover_plugins()
    assert plugin.package_dir is not None
    outside = plugin.package_dir.parent / "outside-secret.json"
    outside.write_text('{"secret": true}')
    (plugin.package_dir / "web" / "leak.json").symlink_to(outside)
    (plugin.package_dir / "i18n" / "fr.json").symlink_to(outside)
    async with _client(app) as c:
        for rel in ("web/leak.json", "i18n/fr.json"):
            r = await c.get(f"{ASSETS}/1.0.0/{rel}")
            assert r.status_code == 404, rel
            assert b"secret" not in r.content
