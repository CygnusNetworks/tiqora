"""GI ``ConfigItemDelete`` on the Tiqora CMDB (Task 24; both dialects).

Answer keys, error codes and the partial-failure behaviour are the ones of
``ConfigItemDelete.pm`` of ITSMConfigurationManagement 6.5.4 and 7.3.5 (identical): every id is
deleted in its own step, a missing right or a failing delete stops the loop, and the ids deleted
before stay deleted. Rows come from the CMDB world of ``_cmdb_helpers`` (class ``cmdb4srv`` in
``GROUP_RW``); the three agents live in the plan's band 56530-56549.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import GROUP_RO, GROUP_RW, CmdbWorld, make_ci, make_class, set_mode
from tiqora.api.compat.config_items import op_config_item_delete
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.service import system_access
from tiqora.domain.auth import SessionStore
from tiqora.itsm.importer import facades
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

PW = "itim56-del-secret"
RW_ID, RO_ID, NONE_ID = 56530, 56531, 56532
RW, RO, NONE = "itim56.del.rw", "itim56.del.ro", "itim56.del.none"
WHEN = datetime(2026, 10, 1, 8, 0, 0)
MAP_RUN = 56530  # run_id of the importer map rows this module writes


@pytest.fixture(scope="module", autouse=True)
def delete_agents(cmdb_db: CmdbWorld) -> Iterator[None]:
    cleanup = (
        f"DELETE FROM group_user WHERE user_id IN ({RW_ID}, {RO_ID}, {NONE_ID})",
        f"DELETE FROM users WHERE id IN ({RW_ID}, {RO_ID}, {NONE_ID})",
    )
    engine = create_engine(cmdb_db.sync_url)
    try:
        with engine.begin() as conn:
            for stmt in cleanup:
                conn.execute(text(stmt))
            for uid, login in ((RW_ID, RW), (RO_ID, RO), (NONE_ID, NONE)):
                conn.execute(
                    text(
                        "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :l, :pw, 'Del', 'Agent', 1, :t, 1, :t, 1)"
                    ),
                    {"id": uid, "l": login, "pw": hash_password(PW), "t": WHEN},
                )
            for uid, perm in ((RW_ID, "rw"), (RO_ID, "ro")):
                conn.execute(
                    text(
                        "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                        " create_by, change_time, change_by) VALUES (:u, :g, :p, :t, 1, :t, 1)"
                    ),
                    {"u": uid, "g": GROUP_RW, "p": perm, "t": WHEN},
                )
    finally:
        engine.dispose()
    try:
        yield
    finally:
        engine = create_engine(cmdb_db.sync_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM tiqora_itsm_import_map WHERE run_id = :r"), {"r": MAP_RUN}
                )
                for stmt in cleanup:
                    conn.execute(text(stmt))
        finally:
            engine.dispose()


class _FakeRedis:
    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        pass

    async def get(self, key: str) -> str | None:
        return None

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


async def _delete(world: CmdbWorld, ci_ids: Any, *, login: str | None = RW) -> dict[str, Any]:
    data: dict[str, Any] = {} if login is None else {"UserLogin": login, "Password": PW}
    if ci_ids is not None:
        data["ConfigItemID"] = ci_ids
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    async with world.factory() as session:
        return await op_config_item_delete(data, session, store)  # type: ignore[no-any-return]


def _error(result: dict[str, Any]) -> tuple[str, str]:
    return result["Error"]["ErrorCode"], result["Error"]["ErrorMessage"]


async def _prepare(world: CmdbWorld, names: tuple[str, ...] = ("a", "b", "c")) -> list[int]:
    """Mode ``tiqora``, class ``cmdb4srv`` (``GROUP_RW``) and one CI per name."""
    set_mode(world, "tiqora")
    ids: list[int] = []
    async with world.factory() as s:
        class_id = await make_class(s)
        for name in names:
            ids.append(await make_ci(s, class_id, f"itim56-del-{name}"))
    return ids


async def _exists(world: CmdbWorld, ci_id: int) -> bool:
    async with world.factory() as s:
        return await facades.get_ports(s).reader.get_ci(ci_id) is not None


def _count(world: CmdbWorld, sql: str, **params: Any) -> int:
    return int(world.sql(sql, **params)[0][0])


async def test_delete_two_cis_removes_them_their_links_and_keeps_the_importer_map(
    cmdb_db: CmdbWorld,
) -> None:
    a, b, c = await _prepare(cmdb_db)
    async with cmdb_db.factory() as s:
        links = LinkService(s)
        for source, target in ((a, b), (a, c), (b, c)):
            await links.add(
                system_access(RW_ID),
                source=LinkEnd("ci", str(source)),
                target=LinkEnd("ci", str(target)),
                link_type_key="RelevantTo",
                enforce_permissions=False,
            )
        await s.commit()
    cmdb_db.sql(
        "INSERT INTO tiqora_itsm_import_map (source_kind, source_id, target_id, fingerprint,"
        " run_id, updated_at) VALUES ('ci', 'znuny-56530', :t, 'f', :r, :w)",
        t=a,
        r=MAP_RUN,
        w=WHEN,
    )
    assert _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_itsm_link") == 3

    result = await _delete(cmdb_db, f"{a},{b}")

    assert result == {"ConfigItemID": [str(a), str(b)]}
    assert not await _exists(cmdb_db, a)
    assert not await _exists(cmdb_db, b)
    assert await _exists(cmdb_db, c)
    assert _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_cmdb_ci") == 1
    assert _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_itsm_link") == 0  # cmdb: links go along
    # the importer's map is not touched: a re-import would recreate the item (documented)
    assert (
        _count(cmdb_db, "SELECT COUNT(*) FROM tiqora_itsm_import_map WHERE run_id = :r", r=MAP_RUN)
        == 1
    )


async def test_delete_accepts_a_list_and_answers_the_ids_as_given(cmdb_db: CmdbWorld) -> None:
    a, b, c = await _prepare(cmdb_db)
    result = await _delete(cmdb_db, [a, str(b)])
    assert result == {"ConfigItemID": [a, str(b)]}
    assert [await _exists(cmdb_db, i) for i in (a, b, c)] == [False, False, True]
    single = await _delete(cmdb_db, c)  # a bare number is a one-item list
    assert single == {"ConfigItemID": [c]}
    assert not await _exists(cmdb_db, c)


async def test_delete_stops_at_a_missing_right_and_keeps_what_was_deleted(
    cmdb_db: CmdbWorld,
) -> None:
    a, b = await _prepare(cmdb_db, ("a", "b"))
    async with cmdb_db.factory() as s:
        other = await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        foreign = await make_ci(s, other, "itim56-del-foreign")

    result = await _delete(cmdb_db, [a, foreign, b])

    assert _error(result) == (
        "ConfigItemDelete.AccessDenied",
        "ConfigItemDelete: Can not delete configuration item!",
    )
    assert not await _exists(cmdb_db, a)  # deleted before the refusal: stays deleted
    assert await _exists(cmdb_db, foreign)
    assert await _exists(cmdb_db, b)  # after the refusal: never reached


async def test_delete_unknown_id_looks_like_a_missing_right(cmdb_db: CmdbWorld) -> None:
    (a,) = await _prepare(cmdb_db, ("a",))
    for unknown in (a + 1000, "abc", "-3"):
        result = await _delete(cmdb_db, [unknown])
        assert _error(result) == (
            "ConfigItemDelete.AccessDenied",
            "ConfigItemDelete: Can not delete configuration item!",
        )
    assert await _exists(cmdb_db, a)


async def test_delete_requires_rw_on_the_class_group(cmdb_db: CmdbWorld) -> None:
    (a,) = await _prepare(cmdb_db, ("a",))
    for login in (RO, NONE):
        result = await _delete(cmdb_db, a, login=login)
        assert _error(result) == (
            "ConfigItemDelete.AccessDenied",
            "ConfigItemDelete: Can not delete configuration item!",
        ), login
    assert await _exists(cmdb_db, a)


async def test_delete_in_mode_a_is_not_available_and_off_is_not_implemented(
    cmdb_db: CmdbWorld,
) -> None:
    (a,) = await _prepare(cmdb_db, ("a",))
    set_mode(cmdb_db, "znuny_readthrough")
    result = await _delete(cmdb_db, a)
    assert _error(result) == (
        "ConfigItemDelete.NotAvailable",
        "Die CMDB wird in Znuny geführt (Modus A)",
    )
    set_mode(cmdb_db, "off")
    assert (await _delete(cmdb_db, a))["Error"]["ErrorCode"] == "Operation.NotImplemented"
    set_mode(cmdb_db, "tiqora")
    assert await _exists(cmdb_db, a)


async def test_delete_parameter_checks_and_auth(cmdb_db: CmdbWorld) -> None:
    (a,) = await _prepare(cmdb_db, ("a",))
    assert _error(await _delete(cmdb_db, None)) == (
        "ConfigItemDelete.MissingParameter",
        "ConfigItemDelete: ConfigItemID parameter is missing!",
    )
    assert _error(await _delete(cmdb_db, "0"))[0] == "ConfigItemDelete.MissingParameter"
    assert _error(await _delete(cmdb_db, {"x": 1})) == (
        "ConfigItemDelete.WrongStructure",
        "ConfigItemDelete: Structure for ConfigItemID is not correct!",
    )
    assert _error(await _delete(cmdb_db, [1] * 501))[0] == "ConfigItemDelete.InvalidParameter"
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    async with cmdb_db.factory() as s:
        wrong = await op_config_item_delete(
            {"UserLogin": RW, "Password": "wrong", "ConfigItemID": a}, s, store
        )
    assert wrong["Error"]["ErrorCode"] == "ConfigItemDelete.AuthFail"
    anonymous = await _delete(cmdb_db, a, login=None)
    assert anonymous["Error"]["ErrorCode"] == "ConfigItemDelete.AuthFail"


async def test_rest_route_delete(cmdb_db: CmdbWorld) -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings

    a, b = await _prepare(cmdb_db, ("a", "b"))
    factory = cmdb_db.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    auth = {"UserLogin": RW, "Password": PW}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.request("DELETE", f"/znuny-compat/ConfigItem/{a}", json=auth)
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"ConfigItemID": [str(a)]}
        denied = await client.request(
            "DELETE", f"/znuny-compat/ConfigItem/{b}", json={"UserLogin": RO, "Password": PW}
        )
        assert denied.status_code == 403
        assert denied.json()["Error"]["ErrorCode"] == "ConfigItemDelete.AccessDenied"
    assert not await _exists(cmdb_db, a)
    assert await _exists(cmdb_db, b)
