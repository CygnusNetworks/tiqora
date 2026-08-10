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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored", "env_enabled", "expected"),
    [("0", True, False), ("1", True, True), (None, True, True), ("1", False, False)],
)
async def test_auth_methods_reports_the_portal_state(
    stored: str | None, env_enabled: bool, expected: bool
) -> None:
    app = _build_app(stored=stored, env_enabled=env_enabled)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/methods")
        assert resp.status_code == 200
        assert resp.json()["portal_enabled"] is expected


class _RecordingSession(_FakeSession):
    """Fake session that also captures set_setting() upserts."""

    def __init__(self, stored: str | None) -> None:
        super().__init__(stored)
        self.added: list[Any] = []
        self.committed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_global_auth_config_reports_portal_state_and_env_lock() -> None:
    from tiqora.api.v1.admin.auth_config import get_global_auth_config

    out = await get_global_auth_config(
        admin=None,
        session=_FakeSession("0"),
        settings=Settings(environment="test", portal_enabled=True),
    )
    assert out.portal_enabled is False
    assert out.portal_locked_by_env is False

    locked = await get_global_auth_config(
        admin=None,
        session=_FakeSession("1"),
        settings=Settings(environment="test", portal_enabled=False),
    )
    assert locked.portal_enabled is False
    assert locked.portal_locked_by_env is True


@pytest.mark.asyncio
async def test_putting_portal_enabled_while_env_locks_it_conflicts() -> None:
    from fastapi import HTTPException

    from tiqora.api.v1.admin.auth_config import put_global_auth_config
    from tiqora.api.v1.admin.schemas import AuthConfigGlobalUpdate

    session = _RecordingSession("1")
    with pytest.raises(HTTPException) as exc:
        await put_global_auth_config(
            body=AuthConfigGlobalUpdate(enforce_all=False, portal_enabled=True),
            admin=None,
            session=session,
            settings=Settings(environment="test", portal_enabled=False),
        )
    assert exc.value.status_code == 409
    assert session.added == []


@pytest.mark.asyncio
async def test_putting_only_2fa_settings_succeeds_while_env_locks_the_portal() -> None:
    """portal_enabled omitted (None) means "leave unchanged" — the 409 must not fire."""
    from tiqora.api.v1.admin.auth_config import put_global_auth_config
    from tiqora.api.v1.admin.schemas import AuthConfigGlobalUpdate
    from tiqora.domain.settings_store import KEY_PORTAL_ENABLED

    # No stored row: put_global_auth_config still needs to upsert enforce_all,
    # which this fake session honors by recording an add() when there's nothing
    # to update in place.
    session = _RecordingSession(None)
    out = await put_global_auth_config(
        body=AuthConfigGlobalUpdate(enforce_all=True),
        admin=None,
        session=session,
        settings=Settings(environment="test", portal_enabled=False),
    )
    assert out.portal_enabled is False
    assert out.portal_locked_by_env is True
    assert all(getattr(obj, "key", None) != KEY_PORTAL_ENABLED for obj in session.added)
