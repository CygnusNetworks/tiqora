"""The mode gate answers on every CMDB route, before the body is validated (both dialects)."""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI

from tests import _cmdb_helpers
from tests._cmdb_helpers import USER_A, USER_ADMIN, CmdbWorld, client_for, set_mode
from tiqora.api.app import create_app
from tiqora.config import Settings

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

GATED_PREFIXES = ("/api/v1/cmdb", "/api/v1/admin/cmdb", "/api/portal/config-items")


def _cmdb_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) under the gated prefixes, read from the OpenAPI document.

    FastAPI 0.141 keeps included routers lazy (``app.routes`` holds ``_IncludedRouter`` objects
    without paths), so the schema is the stable place to enumerate the effective routes.
    """
    paths: dict[str, dict[str, object]] = app.openapi()["paths"]
    return [
        (method.upper(), path)
        for path, ops in paths.items()
        if path.startswith(GATED_PREFIXES) and not path.endswith("/status")
        for method in ops
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def test_route_walk_finds_the_cmdb_routes() -> None:
    routes = _cmdb_routes(create_app(Settings(environment="test")))
    assert ("GET", "/api/v1/cmdb/classes") in routes
    assert ("POST", "/api/v1/admin/cmdb/seed-defaults") in routes
    assert len(routes) >= 18, routes


@pytest.mark.parametrize("mode,expected", [("off", 404), ("znuny_readthrough", 409)])
async def test_mode_gate_on_every_route(cmdb_db: CmdbWorld, mode: str, expected: int) -> None:
    set_mode(cmdb_db, mode)
    routes = _cmdb_routes(create_app(Settings(environment="test")))
    async with client_for(cmdb_db, USER_ADMIN) as c:
        for method, path in routes:  # path params are filled with 1
            r = await c.request(method, re.sub(r"\{[^}]+\}", "1", path))
            want = 404 if path.startswith("/api/portal/") else expected
            assert r.status_code == want, (method, path, r.status_code)
        if mode == "znuny_readthrough":
            body = (await c.get("/api/v1/cmdb/classes")).json()
            assert body["detail"]["code"] == "cmdb_in_znuny"
        else:
            assert (await c.get("/api/v1/cmdb/classes")).json() == {"detail": "Nicht gefunden"}


async def test_status_route_always_answers(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "znuny_readthrough")
    async with client_for(cmdb_db, USER_A) as c:
        body = (await c.get("/api/v1/cmdb/status")).json()
    assert body["mode"] == "znuny_readthrough"
    assert body["readable_class_count"] == 0
    assert body["can_admin"] is False
