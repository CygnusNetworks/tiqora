"""Plugin HTTP routes under /api/plugins/<id>/ (no DB)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

from tests.plugins.helpers import Installer
from tiqora.api.app import create_app
from tiqora.api.deps import get_auth_service, get_current_user, get_db, get_redis
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import get_registry, register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import discover_plugins
from tiqora.plugins.loader import commit_registrar, import_and_register
from tiqora.plugins.routes import mount_plugin_routes
from tiqora.plugins.state import PluginLoadResult, RuntimeState, set_loaded_plugins

ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
BASE = "/api/plugins/org.example.hello"
PLUGIN_ID = "org.example.hello"


@pytest.fixture(autouse=True)
def _reset_plugin_state() -> Iterator[None]:
    yield
    set_registry_for_tests(None)
    set_loaded_plugins([])
    mod = sys.modules.get("tq_fixture_hello.plugin")
    if mod is not None:
        mod.ECHO_CALLS.clear()


def _echo_calls() -> list[str]:
    return sys.modules["tq_fixture_hello.plugin"].ECHO_CALLS  # type: ignore[no-any-return]


def _app_with_hello(
    fixture_plugins: Installer, *, state: RuntimeState = RuntimeState.ACTIVE
) -> Any:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    reg.freeze()
    set_registry_for_tests(reg)
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", state, None, ())])
    app = create_app(Settings(environment="test"))
    mount_plugin_routes(app, reg)
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_authenticated_get_reaches_plugin(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r = await c.get(f"{BASE}/hello")
    assert r.status_code == 200 and r.json() == {"hello": "Root"}


async def test_anonymous_rejected_unless_public(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_redis] = lambda: _FakeRedis()
    async with _client(app) as c:
        anon = await c.get(f"{BASE}/hello")
        anon_post = await c.post(f"{BASE}/echo", json={"text": "x"})
        pub = await c.get(f"{BASE}/ping")
    assert anon.status_code == 401 and anon_post.status_code == 401
    assert _echo_calls() == []
    assert pub.status_code == 200 and pub.json() == {"pong": True, "app": "Tiqora"}


async def test_cross_site_post_rejected(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    cookie = Settings(environment="test").session_cookie_name
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/echo",
            json={"text": "x"},
            cookies={cookie: "abc"},
            headers={"Origin": "https://evil.example.com"},
        )
    assert r.status_code == 403 and _echo_calls() == []


async def test_same_origin_post_allowed(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    cookie = Settings(environment="test").session_cookie_name
    async with _client(app) as c:
        r = await c.post(
            f"{BASE}/echo",
            json={"text": "x"},
            cookies={cookie: "abc"},
            headers={"Origin": "http://test"},
        )
    assert r.status_code == 200 and _echo_calls() == ["x"]


@pytest.mark.parametrize("state", [RuntimeState.FAILED, RuntimeState.DISABLED])
async def test_unknown_or_unloaded_plugin_404(
    fixture_plugins: Installer, state: RuntimeState
) -> None:
    app = _app_with_hello(fixture_plugins, state=state)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r1 = await c.get(f"{BASE}/hello")
        r1p = await c.get(f"{BASE}/ping")
        r2 = await c.get("/api/plugins/org.example.nope/x")
    assert r1.status_code == 404 and r1p.status_code == 404 and r2.status_code == 404
    assert r2.json() == {"detail": "Erweiterung nicht gefunden"}
    assert r1.json() == {"detail": "Erweiterung nicht gefunden"}


async def test_mount_is_idempotent_and_reports_ids(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    registry = get_registry()
    assert mount_plugin_routes(app, registry) == []
    mounts = [r for r in app.state.plugin_router.routes if isinstance(r, Mount)]
    assert [m.path for m in mounts] == ["/" + PLUGIN_ID]


async def test_plugin_routes_not_in_core_openapi(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    assert not [p for p in app.openapi()["paths"] if p.startswith("/api/plugins")]


async def test_subapp_openapi_only_outside_production(fixture_plugins: Installer) -> None:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r = await c.get(f"{BASE}/openapi.json")
    assert r.status_code == 200 and "/hello" in r.json()["paths"]


async def test_no_subapp_openapi_in_production(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    reg.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.ACTIVE, None, ())])
    app = create_app(Settings(environment="test"))
    app.state.settings = app.state.settings.model_copy(update={"environment": "production"})
    assert app.state.settings.is_production
    mount_plugin_routes(app, reg)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    async with _client(app) as c:
        r = await c.get(f"{BASE}/openapi.json")
    assert r.status_code == 404


# --- API-key scopes (R-SCOPE-PLUGINS) -----------------------------------------------


class _FakeRedis:
    async def set(self, *a: Any, **k: Any) -> None:
        return None


class _FakeSession:
    async def rollback(self) -> None:
        return None


async def _no_db() -> Any:
    yield _FakeSession()


class _KeyAuth:
    def __init__(self, scopes: frozenset[str] | None) -> None:
        self._scopes = scopes

    async def resolve_session(self, token: str) -> None:
        return None

    async def resolve_api_key(self, raw: str) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=1,
            login="root@localhost",
            first_name="Root",
            last_name="Admin",
            auth_method="api_key",
            api_key_scopes=self._scopes,
        )


async def _key_request(
    fixture_plugins: Installer, scopes: frozenset[str] | None, method: str
) -> int:
    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_auth_service] = lambda: _KeyAuth(scopes)
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_redis] = lambda: _FakeRedis()
    headers = {"Authorization": "Bearer tiqora_plg49"}
    async with _client(app) as c:
        if method == "GET":
            r = await c.get(f"{BASE}/hello", headers=headers)
        else:
            r = await c.post(f"{BASE}/echo", json={"text": "k"}, headers=headers)
    return r.status_code


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_key_without_plugins_area_is_forbidden(
    fixture_plugins: Installer, method: str
) -> None:
    assert await _key_request(fixture_plugins, frozenset({"kb:ro"}), method) == 403
    assert _echo_calls() == []


async def test_plugins_ro_key_reads_but_cannot_write(fixture_plugins: Installer) -> None:
    ro = frozenset({"plugins:ro"})
    assert await _key_request(fixture_plugins, ro, "GET") == 200
    assert await _key_request(fixture_plugins, ro, "POST") == 403
    assert _echo_calls() == []


async def test_plugins_rw_and_unrestricted_keys_can_do_both(fixture_plugins: Installer) -> None:
    for scopes in (frozenset({"plugins:rw"}), None):
        assert await _key_request(fixture_plugins, scopes, "GET") == 200
        assert await _key_request(fixture_plugins, scopes, "POST") == 200
    assert _echo_calls() == ["k", "k"]


@pytest.mark.parametrize("method,legacy", [("GET", "read"), ("POST", "write")])
async def test_legacy_read_write_keys_get_no_plugin_access(
    fixture_plugins: Installer, method: str, legacy: str
) -> None:
    assert await _key_request(fixture_plugins, frozenset({legacy}), method) == 403
    assert _echo_calls() == []


async def test_mount_refuses_unauthenticated_route_in_authenticated_router(
    fixture_plugins: Installer,
) -> None:
    from fastapi import APIRouter

    from tiqora.plugins.routes import PLUGIN_ROUTES, PluginRoute

    async def raw(request: Any) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse({"leak": True})

    fixture_plugins()
    router = APIRouter()
    router.add_route("/raw", raw)
    reg = Registry()
    register_core(reg)
    reg.add(PLUGIN_ROUTES, PluginRoute(f"{PLUGIN_ID}:0", PLUGIN_ID, router, False), owner=PLUGIN_ID)
    reg.freeze()
    set_loaded_plugins([PluginLoadResult(PLUGIN_ID, "1.0.0", RuntimeState.ACTIVE, None, ())])
    app = create_app(Settings(environment="test"))
    assert mount_plugin_routes(app, reg) == []
    async with _client(app) as c:
        assert (await c.get(f"{BASE}/raw")).status_code == 404
        assert (await c.post(f"{BASE}/raw")).status_code == 404


async def test_routes_added_to_plugin_router_after_mount_are_not_served(
    fixture_plugins: Installer,
) -> None:
    from starlette.responses import JSONResponse
    from starlette.routing import Mount as StarletteMount

    app = _app_with_hello(fixture_plugins)
    app.dependency_overrides[get_current_user] = lambda: ROOT
    mod = sys.modules["tq_fixture_hello.plugin"]

    async def raw(request: Any) -> Any:
        return JSONResponse({"leak": True})

    async def later() -> dict[str, bool]:
        return {"later": True}

    mod.router.add_route("/raw", raw)
    mod.router.routes.append(StarletteMount("/m", app=JSONResponse({"leak": True})))
    mod.router.add_api_route("/later", later, methods=["GET"])
    mod.public_router.add_api_route("/later-public", later, methods=["GET"])
    async with _client(app) as c:
        assert (await c.get(f"{BASE}/raw")).status_code == 404
        assert (await c.get(f"{BASE}/m/x")).status_code == 404
        assert (await c.get(f"{BASE}/later")).status_code == 404
        assert (await c.get(f"{BASE}/later-public")).status_code == 404
        assert (await c.get(f"{BASE}/hello")).status_code == 200
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_redis] = lambda: _FakeRedis()
    async with _client(app) as c:
        assert (await c.get(f"{BASE}/hello")).status_code == 401
