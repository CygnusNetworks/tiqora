"""Change availability gate, manager/legacy rules and the status route (both dialects).

Seed band 946200-946299: users 946201-946206, groups 946210-946212.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import itsm_url, make_factory, plain_url, store
from tests._itsm_setup_helpers import CHANGE_FIELDS, TYPES, remove_rows, signal_floor
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import (
    can_see_legacy,
    change_availability,
    is_change_manager,
    require_change_mode,
)
from tiqora.itsm.change.models import TiqoraItsmLegacyChange
from tiqora.itsm.change.settings import ItsmChangeSettings
from tiqora.itsm.settings import ItsmSettings
from tiqora.itsm.setup import apply_setup, setup_group_complete
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache

pytestmark = pytest.mark.db

URL_FIXTURES = ["mariadb_znuny_url", "postgres_znuny_url"]
ADMIN_ID = 1
U_MANAGER, U_PLAIN, U_LEGACY, U_RO_MANAGER = 946201, 946202, 946203, 946204
G_MANAGER, G_LEGACY = 946210, 946211
SETTINGS = Settings(environment="test")
_T = "2026-10-01 00:00:00"


def _sql(url: str, statements: list[tuple[str, dict[str, Any]]]) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for stmt, params in statements:
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


def _seed_world(url: str, *, legacy_group: bool = True) -> None:
    rows: list[tuple[str, dict[str, Any]]] = []
    for uid, login in (
        (U_MANAGER, "chg94.manager"),
        (U_PLAIN, "chg94.plain"),
        (U_LEGACY, "chg94.legacy"),
        (U_RO_MANAGER, "chg94.romanager"),
    ):
        rows.append(
            (
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :l, 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)",
                {"id": uid, "l": login, "t": _T},
            )
        )
    groups = [(G_MANAGER, "chg94-managers")]
    if legacy_group:
        groups.append((G_LEGACY, "chg94-legacy"))
    for gid, name in groups:
        rows.append(
            (
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)",
                {"id": gid, "n": name, "t": _T},
            )
        )
    grants = [(U_MANAGER, G_MANAGER, "rw"), (U_RO_MANAGER, G_MANAGER, "ro")]
    if legacy_group:
        grants.append((U_LEGACY, G_LEGACY, "ro"))
    for uid, gid, key in grants:
        rows.append(
            (
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)",
                {"u": uid, "g": gid, "k": key, "t": _T},
            )
        )
    _sql(url, rows)


def _drop_world(url: str) -> None:
    _sql(
        url,
        [
            ("DELETE FROM group_user WHERE user_id BETWEEN 946200 AND 946299", {}),
            ("DELETE FROM permission_groups WHERE id BETWEEN 946200 AND 946299", {}),
            ("DELETE FROM users WHERE id BETWEEN 946200 AND 946299", {}),
            ("DELETE FROM tiqora_itsm_legacy_change WHERE change_number LIKE 'CHG94%'", {}),
        ],
    )


@pytest.fixture
def world(request: pytest.FixtureRequest) -> Callable[..., str]:
    """``world(url_fixture, mode=..., ...)`` -> a clean plain Znuny URL with the seeded users."""

    def build(url_fixture: str, *, mode: str | None = None, legacy_group: bool = True) -> str:
        url = plain_url(request, url_fixture)
        floor = signal_floor(url)
        request.addfinalizer(
            lambda: remove_rows(url, types=TYPES, fields=CHANGE_FIELDS, signal_floor=floor)
        )
        request.addfinalizer(lambda: _drop_world(url))
        _seed_world(url, legacy_group=legacy_group)
        store(url, "itsm.mode", mode)
        clear_detect_cache()
        return url

    return build


async def _avail(url: str, user_id: int, change_cfg: ItsmChangeSettings | None = None) -> Any:
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            return await change_availability(session, SETTINGS, user_id=user_id)
    finally:
        await engine.dispose()


async def _complete_setup(url: str) -> None:
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            await apply_setup(session, ItsmSettings(), groups={"change"}, user_id=1)
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_status_by_mode(world: Callable[..., str], url_fixture: str) -> None:
    url = world(url_fixture, mode="off")
    off = await _avail(url, ADMIN_ID)
    assert (off.mode, off.available, off.reason, off.legacy) == ("off", False, "itsm_off", "none")

    store(url, "itsm.mode", "tiqora")
    clear_detect_cache()
    missing = await _avail(url, ADMIN_ID)
    assert (missing.available, missing.reason, missing.setup_complete_change) == (
        False,
        "setup_missing",
        False,
    )

    await _complete_setup(url)
    ok = await _avail(url, ADMIN_ID)
    assert (ok.mode, ok.available, ok.reason, ok.setup_complete_change) == (
        "tiqora",
        True,
        "ok",
        True,
    )
    assert ok.legacy == "none"  # no archive rows


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_status_archive_rows_make_legacy_archive(
    world: Callable[..., str], url_fixture: str
) -> None:
    url = world(url_fixture, mode="tiqora")
    when = datetime(2026, 10, 1)
    _sql(
        url,
        [
            (
                "INSERT INTO tiqora_itsm_legacy_change (znuny_change_id, change_number, title,"
                " znuny_created_at, znuny_changed_at, workorder_count, data, fingerprint,"
                " imported_at) VALUES (946201, 'CHG94-1', 'Router-Tausch', :t, :t, 0, '{}',"
                " 'f', :t)",
                {"t": when},
            )
        ],
    )
    assert (await _avail(url, ADMIN_ID)).legacy == "archive"
    assert TiqoraItsmLegacyChange.__tablename__ == "tiqora_itsm_legacy_change"


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_status_mode_a_live_legacy(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    """Znuny ITSM database: Mode A with the change tables -> the legacy view is "live"."""
    url = itsm_url(request, dialect, profile)
    clear_detect_cache()
    out = await _avail(url, ADMIN_ID)
    assert out.mode == "znuny_readthrough"
    assert (out.available, out.reason, out.legacy) == (False, "mode_a", "live")
    assert out.can_see_legacy is True and out.can_manage is True  # root is admin


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_require_change_mode_codes(world: Callable[..., str], url_fixture: str) -> None:
    url = world(url_fixture, mode="off")
    engine, factory = make_factory(url)

    async def refused() -> ChangeError:
        async with factory() as session:
            with pytest.raises(ChangeError) as err:
                await require_change_mode(session, SETTINGS)
        return err.value

    try:
        off = await refused()
        assert (off.error_code, off.status_code) == ("change_unavailable", 404)
        assert off.detail == {"code": "change_unavailable"}

        store(url, "itsm.mode", "znuny_readthrough")
        clear_detect_cache()
        mode_a = await refused()
        assert (mode_a.error_code, mode_a.status_code) == ("change_mode_a", 409)

        store(url, "itsm.mode", "tiqora")
        clear_detect_cache()
        missing = await refused()
        assert (missing.error_code, missing.status_code) == ("change_setup_missing", 409)

        await _complete_setup(url)
        async with factory() as session:
            await require_change_mode(session, SETTINGS)  # passes
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
@pytest.mark.parametrize(
    ("key", "reason"),
    [("itsm_change.enabled", "change_off"), ("approvals.enabled", "approvals_off")],
)
async def test_switch_beats_mode_setup_and_archive(
    world: Callable[..., str], url_fixture: str, key: str, reason: str
) -> None:
    """Off in every mode, with or without setup; legacy "none" although the archive stays."""
    url = world(url_fixture, mode="tiqora")
    when = datetime(2026, 10, 1)
    _sql(
        url,
        [
            (
                "INSERT INTO tiqora_itsm_legacy_change (znuny_change_id, change_number, title,"
                " znuny_created_at, znuny_changed_at, workorder_count, data, fingerprint,"
                " imported_at) VALUES (946202, 'CHG94-2', 'Router-Tausch', :t, :t, 0, '{}',"
                " 'f', :t)",
                {"t": when},
            )
        ],
    )
    await _complete_setup(url)
    assert (await _avail(url, ADMIN_ID)).reason == "ok"
    store(url, key, "0")
    try:
        for mode in ("tiqora", "znuny_readthrough", "off"):
            store(url, "itsm.mode", mode)
            clear_detect_cache()
            got = await _avail(url, ADMIN_ID)
            assert (got.mode, got.available, got.reason, got.legacy, got.can_see_legacy) == (
                mode,
                False,
                reason,
                "none",
                False,
            )
            engine, factory = make_factory(url)
            try:
                async with factory() as session:
                    with pytest.raises(ChangeError) as err:
                        await require_change_mode(session, SETTINGS)
                assert (err.value.error_code, err.value.status_code) == ("change_unavailable", 404)
            finally:
                await engine.dispose()
    finally:
        store(url, key, None)
    store(url, "itsm.mode", "tiqora")
    clear_detect_cache()
    back = await _avail(url, ADMIN_ID)
    assert (back.available, back.reason, back.legacy) == (True, "ok", "archive")


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_change_switch_wins_over_the_approvals_switch(
    world: Callable[..., str], url_fixture: str
) -> None:
    url = world(url_fixture, mode="tiqora")
    store(url, "itsm_change.enabled", "0")
    store(url, "approvals.enabled", "0")
    try:
        assert (await _avail(url, ADMIN_ID)).reason == "change_off"
    finally:
        store(url, "itsm_change.enabled", None)
        store(url, "approvals.enabled", None)


def test_change_error_detail_may_be_called_code() -> None:
    err = ChangeError("approval_request_failed", 409, code="no_approvers", round=2)
    assert err.status_code == 409 and err.error_code == "approval_request_failed"
    # the answer carries the primary code; the engine's own code stays available as ``extra``
    assert err.detail == {"code": "approval_request_failed", "round": 2}
    assert err.extra == {"code": "no_approvers", "round": 2}


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_manager_rule(world: Callable[..., str], url_fixture: str) -> None:
    url = world(url_fixture, mode="tiqora")
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            group = ItsmChangeSettings(manager_group="chg94-managers")
            assert await is_change_manager(session, ADMIN_ID, group) is True
            assert await is_change_manager(session, U_MANAGER, group) is True
            assert await is_change_manager(session, U_RO_MANAGER, group) is False  # ro is not rw
            assert await is_change_manager(session, U_PLAIN, group) is False
            # empty group setting: admins only
            empty = ItsmChangeSettings(manager_group="")
            assert await is_change_manager(session, ADMIN_ID, empty) is True
            assert await is_change_manager(session, U_MANAGER, empty) is False
            # a group that does not exist grants nothing
            gone = ItsmChangeSettings(manager_group="chg94-nonexistent")
            assert await is_change_manager(session, U_MANAGER, gone) is False
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_legacy_visibility_rule(world: Callable[..., str], url_fixture: str) -> None:
    url = world(url_fixture, mode="tiqora")
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            cfg = ItsmChangeSettings(
                legacy_change_group="chg94-legacy", manager_group="chg94-managers"
            )
            assert await can_see_legacy(session, ADMIN_ID, cfg) is True
            assert await can_see_legacy(session, U_LEGACY, cfg) is True  # ro in the group
            assert await can_see_legacy(session, U_PLAIN, cfg) is False
            # the group exists: managers are not let in through the back door
            assert await can_see_legacy(session, U_MANAGER, cfg) is False

            # group missing (removed from Znuny, or never set): admins and change managers
            gone = ItsmChangeSettings(
                legacy_change_group="chg94-nonexistent", manager_group="chg94-managers"
            )
            assert await can_see_legacy(session, ADMIN_ID, gone) is True
            assert await can_see_legacy(session, U_MANAGER, gone) is True
            assert await can_see_legacy(session, U_LEGACY, gone) is False
            assert await can_see_legacy(session, U_PLAIN, gone) is False
            unset = ItsmChangeSettings(legacy_change_group="", manager_group="")
            assert await can_see_legacy(session, U_MANAGER, unset) is False
            assert await can_see_legacy(session, ADMIN_ID, unset) is True
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_setup_group_complete_needs_every_item(
    world: Callable[..., str], url_fixture: str
) -> None:
    url = world(url_fixture, mode="tiqora")
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            assert await setup_group_complete(session, ItsmSettings(), "change") is False
            await apply_setup(session, ItsmSettings(), groups={"change"}, user_id=1)
            await session.commit()
        async with factory() as session:
            assert await setup_group_complete(session, ItsmSettings(), "change") is True
            # the other group is not part of "change"
            assert await setup_group_complete(session, ItsmSettings(), "incident_problem") is False
            await session.execute(text("DELETE FROM dynamic_field WHERE name = 'ITSMChangePhase'"))
            await session.commit()
        async with factory() as session:
            assert await setup_group_complete(session, ItsmSettings(), "change") is False
    finally:
        await engine.dispose()


async def _client(url: str, user_id: int) -> AsyncIterator[AsyncClient]:
    engine, factory = make_factory(url)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=user_id, login="chg94.user", first_name="Erika", last_name="Beispiel",
        auth_method="session",
    )  # fmt: skip
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest.mark.parametrize("url_fixture", URL_FIXTURES)
async def test_status_route(world: Callable[..., str], url_fixture: str) -> None:
    url = world(url_fixture, mode="tiqora")
    await _complete_setup(url)
    async for client in _client(url, U_MANAGER):
        res = await client.get("/api/v1/itsm/changes/status")
        assert res.status_code == 200
        assert res.json() == {
            "mode": "tiqora",
            "available": True,
            "reason": "ok",
            "legacy": "none",
            "can_manage": False,  # manager_group is not set: admins only
            "can_see_legacy": False,
            "setup_complete_change": True,
        }
    async for client in _client(url, ADMIN_ID):
        body = (await client.get("/api/v1/itsm/changes/status")).json()
        assert body["can_manage"] is True and body["can_see_legacy"] is True


def test_every_change_prefix_is_area_tickets() -> None:
    from tiqora.domain.api_key_scopes import path_to_area

    for prefix in (
        "changes",
        "change-tasks",
        "change-calendar",
        "change-templates",
        "cabs",
        "cab-meetings",
        "freeze-windows",
        "legacy-changes",
    ):
        assert path_to_area(f"/api/v1/itsm/{prefix}") == "tickets", prefix
        assert path_to_area(f"/api/v1/itsm/{prefix}/1") == "tickets", prefix
    assert path_to_area("/api/v1/itsm/changes/status") == "tickets"
    assert path_to_area("/api/v1/itsm/status") == "cmdb"  # untouched
    assert path_to_area("/api/v1/admin/itsm/change/settings") == "admin"
