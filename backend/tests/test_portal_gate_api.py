"""The /api/portal mount is invisible (404) while the portal is switched off."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tiqora.api.deps import get_db
from tiqora.config import Settings

# One route per portal sub-router, so a newly added sub-router that bypasses
# the gate would show up here.
PORTAL_ROUTES = [
    "/api/portal/auth/me",
    "/api/portal/tickets",
    "/api/portal/tickets/1/attachments/2",
    "/api/portal/kb/search",
    "/api/portal/process/",
]


class _Result:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> str | None:
        return self._value


class _FakeSession:
    def __init__(self, stored: str | None) -> None:
        self.stored = stored

    async def execute(self, stmt: Any) -> _Result:
        del stmt
        return _Result(self.stored)


def _build_app(*, stored: str | None, env_enabled: bool = True) -> Any:
    from tiqora.api.app import create_app

    app = create_app(Settings(environment="test", portal_enabled=env_enabled))
    session = _FakeSession(stored)

    async def _override_db() -> Any:
        yield session

    app.dependency_overrides[get_db] = _override_db
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PORTAL_ROUTES)
async def test_portal_routes_404_when_switched_off_in_the_database(path: str) -> None:
    app = _build_app(stored="0")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PORTAL_ROUTES)
async def test_portal_routes_404_when_the_deployment_hard_disables_the_portal(path: str) -> None:
    app = _build_app(stored="1", env_enabled=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_an_enabled_portal_answers_401_not_404_without_a_session_cookie() -> None:
    """Proves the gate is open — the route exists and enforces auth as usual."""
    app = _build_app(stored=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/portal/auth/me")).status_code == 401
