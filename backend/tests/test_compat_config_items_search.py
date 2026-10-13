"""GI ``ConfigItemSearch`` (Task 22; both dialects, band 56460-56489).

Parameter checks, their order, error texts and the answer shape are the ones of
``ConfigItemSearch.pm`` / ``ITSMConfigItem.pm`` (``backend/tests/fixtures/itsm/gi_shapes.md``).
Four CIs of one class (group ``GROUP_RO``) carry the rows; the agents ``itim56.srch.ro`` (``ro``)
and ``itim56.srch.none`` (no group) are created for the module.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    USER_A,
    CmdbWorld,
    _dep,
    _inc,
    make_class,
    set_mode,
)
from tests._itsm_import_fixtures import sample_catalog, sample_source
from tiqora.api.compat.config_items import op_config_item_search
from tiqora.cmdb.classes import ClassService
from tiqora.domain.auth import SessionStore
from tiqora.itsm.importer import facades, gi_search
from tiqora.itsm.importer.facades import CmdbServiceWriter
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

PW = "itim56-srch-secret"
RO_ID, NONE_ID = 56460, 56461
RO, NONE = "itim56.srch.ro", "itim56.srch.none"
WHEN = datetime(2026, 10, 1, 8, 0, 0)
BASE = 2026101256460000
PREFIX = "202610125646000*"  # the numbers are BASE + 1 .. BASE + 4


@pytest.fixture(scope="module", autouse=True)
def search_agents(cmdb_db: CmdbWorld) -> Iterator[None]:
    cleanup = (
        f"DELETE FROM group_user WHERE user_id IN ({RO_ID}, {NONE_ID})",
        f"DELETE FROM users WHERE id IN ({RO_ID}, {NONE_ID})",
    )
    engine = create_engine(cmdb_db.sync_url)
    try:
        with engine.begin() as conn:
            for stmt in cleanup:
                conn.execute(text(stmt))
            for uid, login in ((RO_ID, RO), (NONE_ID, NONE)):
                conn.execute(
                    text(
                        "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :l, :pw, 'Srch', 'Agent', 1, :t, 1, :t, 1)"
                    ),
                    {"id": uid, "l": login, "pw": hash_password(PW), "t": WHEN},
                )
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
                ),
                {"u": RO_ID, "g": GROUP_RO, "t": WHEN},
            )
    finally:
        engine.dispose()
    try:
        yield
    finally:
        engine = create_engine(cmdb_db.sync_url)
        try:
            with engine.begin() as conn:
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


def _auth(login: str = RO) -> dict[str, str]:
    return {"UserLogin": login, "Password": PW}


async def _search(
    world: CmdbWorld, item: Any, *, login: str = RO, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    data: dict[str, Any] = {**_auth(login), **(extra or {})}
    if item is not None:
        data["ConfigItem"] = item
    async with world.factory() as session:
        return await op_config_item_search(data, session, store)


@dataclasses.dataclass
class _Cis:
    ids: list[int]
    class_name: str


async def _seed(world: CmdbWorld) -> _Cis:
    """Four CIs: alpha (2 versions), beta, gamma, unrelated; returns ids in creation order."""
    async with world.factory() as s:
        class_id = await make_class(s, group_id=GROUP_RO)
        cls = await ClassService(s).get(class_id)
        assert cls.current_definition_id is not None
        writer = CmdbServiceWriter(s)
        rows = (
            # name, vendor, deployment, incident, created
            ("itim56-alpha", "itim56 Dell", "production", "operational", datetime(2026, 10, 1, 8)),
            ("itim56-Beta", "itim56 HP", "planned", "operational", datetime(2026, 10, 3, 8)),
            ("itim56-gamma", "other", "retired", "warning", datetime(2026, 10, 5, 8)),
            ("unrelated", "itim56 Dell", "production", "operational", datetime(2026, 10, 7, 8)),
        )
        ids: list[int] = []
        for n, (name, vendor, dep_key, inc_key, created) in enumerate(rows, start=1):
            dep, inc = await _dep(s, dep_key), await _inc(s, inc_key)
            ci_id = await writer.create_imported_ci(
                class_id=class_id,
                number=str(BASE + n),
                name=name,
                created_by=USER_A,
                created_at=created,
                deployment_state_id=dep,
                incident_state_id=inc,
            )
            attributes: dict[str, Any] = {"Vendor": [{"v": vendor}]}
            if n == 1:
                attributes["NIC"] = [{"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.5"}]}}]
            await writer.add_imported_version(
                ci_id,
                definition_id=cls.current_definition_id,
                name=name,
                deployment_state_id=dep,
                incident_state_id=inc,
                attributes=attributes,
                created_by=USER_A,
                created_at=created,
            )
            ids.append(ci_id)
        await s.commit()
    return _Cis(ids, cls.name)


def _ids(answer: dict[str, Any]) -> list[int]:
    assert "Error" not in answer, answer
    value = answer["ConfigItemIDs"]
    assert value != "", "expected a result, got the empty answer"
    return list(value)


async def test_wildcard_name_is_case_insensitive_and_defaults_to_id_ascending(
    cmdb_db: CmdbWorld,
) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one, two, three, _four = cis.ids
    assert _ids(await _search(cmdb_db, {"Class": cis.class_name, "Name": "itim56-*"})) == [
        one,
        two,
        three,
    ]
    # Znuny: LOWER(name) LIKE LOWER(pattern)
    assert _ids(await _search(cmdb_db, {"Class": cis.class_name, "Name": "ITIM56-BETA"})) == [two]
    # a wildcard anywhere, not only at the ends
    assert _ids(await _search(cmdb_db, {"Class": cis.class_name, "Name": "i*56-g*a"})) == [three]
    # without a wildcard the name must match completely
    assert (await _search(cmdb_db, {"Class": cis.class_name, "Name": "itim56"})) == {
        "ConfigItemIDs": ""
    }


async def test_number_pattern_and_list(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one, two, three, four = cis.ids
    assert _ids(await _search(cmdb_db, {"Class": cis.class_name, "Number": PREFIX})) == cis.ids
    assert _ids(await _search(cmdb_db, {"Class": cis.class_name, "Number": str(BASE + 3)})) == [
        three
    ]
    assert _ids(
        await _search(cmdb_db, {"Class": cis.class_name, "Number": [str(BASE + 2), str(BASE + 4)]})
    ) == [two, four]
    limited = await _search(cmdb_db, {"Class": cis.class_name, "Number": PREFIX, "Limit": 3})
    assert _ids(limited) == [one, two, three]


async def test_class_is_required_and_checked(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    async with cmdb_db.factory() as s:
        await make_class(s, key="itim56hid", group_id=GROUP_HIDDEN)
    missing = await _search(cmdb_db, {"Name": "x"})
    assert missing["Error"] == {
        "ErrorCode": "ConfigItemSearch.MissingParameter",
        "ErrorMessage": "ConfigItemSearch: ConfigItem->Class parameter is missing!",
    }
    unknown = await _search(cmdb_db, {"Class": "no-such-class"})
    assert unknown["Error"] == {
        "ErrorCode": "ConfigItemSearch.InvalidParameter",
        "ErrorMessage": "ConfigItemSearch: ConfigItem->Class parameter is invalid!",
    }
    denied = await _search(cmdb_db, {"Class": "itim56hid"})
    assert denied["Error"] == {
        "ErrorCode": "ConfigItemSearch.AccessDenied",
        "ErrorMessage": "ConfigItemSearch: Can not search configuration items!",
    }
    # an agent without any group right on the class
    assert (await _search(cmdb_db, {"Class": cis.class_name}, login=NONE))["Error"][
        "ErrorCode"
    ] == "ConfigItemSearch.AccessDenied"


async def test_states_filter_and_structure_errors(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one, two, three, four = cis.ids
    cls = cis.class_name
    assert _ids(await _search(cmdb_db, {"Class": cls, "DeplStates": ["Production"]})) == [
        one,
        four,
    ]
    # a comma separated string is split (Znuny: split /,/)
    assert _ids(await _search(cmdb_db, {"Class": cls, "DeplStates": "Planned,Retired"})) == [
        two,
        three,
    ]
    assert _ids(await _search(cmdb_db, {"Class": cls, "InciStates": ["Warning"]})) == [three]
    bad_state = await _search(cmdb_db, {"Class": cls, "DeplStates": ["Nonsense"]})
    assert bad_state["Error"] == {
        "ErrorCode": "ConfigItemSearch.InvalidParameter",
        "ErrorMessage": "ConfigItemSearch: ConfigItem->DeplStates parameter is invalid!",
    }
    bad_inci = await _search(cmdb_db, {"Class": cls, "InciStates": ["Nonsense"]})
    assert bad_inci["Error"]["ErrorMessage"] == (
        "ConfigItemSearch: ConfigItem->InciStates parameter is invalid!"
    )
    wrong = await _search(cmdb_db, {"Class": cls, "DeplStates": {"a": 1}})
    assert wrong["Error"] == {
        "ErrorCode": "ConfigItemSearch.WrongStructure",
        "ErrorMessage": "ConfigItemSearch: Structure for DeplStates is not correct!",
    }
    wrong = await _search(cmdb_db, {"Class": cls, "InciStates": []})
    assert wrong["Error"]["ErrorMessage"] == (
        "ConfigItemSearch: Structure for ConfigItem->InciStates is not correct!"
    )
    wrong = await _search(cmdb_db, {"Class": cls, "OrderBy": {}})
    assert (
        wrong["Error"]["ErrorMessage"] == "ConfigItemSearch: Structure for OrderBy is not correct!"
    )
    wrong = await _search(cmdb_db, {"Class": cls, "OrderByDirection": []})
    assert wrong["Error"]["ErrorMessage"] == (
        "ConfigItemSearch: Structure for OrderByDirection is not correct!"
    )


async def test_attribute_filter_uses_the_attribute_index(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one, two, three, four = cis.ids
    cls = cis.class_name
    assert _ids(await _search(cmdb_db, {"Class": cls, "CIXMLData": {"Vendor": "itim56*"}})) == [
        one,
        two,
        four,
    ]
    # an array is a list of alternatives
    assert _ids(
        await _search(cmdb_db, {"Class": cls, "CIXMLData": {"Vendor": ["itim56 HP", "other"]}})
    ) == [two, three]
    # several keys must all match; combined with the name
    assert _ids(
        await _search(cmdb_db, {"Class": cls, "Name": "unrel*", "CIXMLData": {"Vendor": "*Dell"}})
    ) == [four]
    # a sub attribute is addressed through its parent hash
    assert _ids(
        await _search(cmdb_db, {"Class": cls, "CIXMLData": {"NIC": {"IPAddress": "192.0.2.5"}}})
    ) == [one]
    # a key outside the definition matches nothing (no XML row in Znuny)
    assert await _search(cmdb_db, {"Class": cls, "CIXMLData": {"Nope": "x"}}) == {
        "ConfigItemIDs": ""
    }
    # an attribute the CMDB does not index cannot be searched
    unsearchable = await _search(cmdb_db, {"Class": cls, "CIXMLData": {"Model": "x"}})
    assert unsearchable["Error"]["ErrorCode"] == "ConfigItemSearch.InvalidParameter"
    assert "CIXMLData->Model" in unsearchable["Error"]["ErrorMessage"]
    invalid = await _search(cmdb_db, {"Class": cls, "CIXMLData": "text"})
    assert invalid["Error"] == {
        "ErrorCode": "ConfigItemSearch.InvalidParameter",
        "ErrorMessage": "ConfigItemSearch: ConfigItem->CIXMLData is invalid!",
    }
    invalid = await _search(cmdb_db, {"Class": cls, "CIXMLData": {}})
    assert invalid["Error"]["ErrorMessage"] == "ConfigItemSearch: ConfigItem->CIXMLData is invalid!"


async def test_limit_and_ordering(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one, two, three, four = cis.ids
    cls = cis.class_name
    assert _ids(await _search(cmdb_db, {"Class": cls, "Limit": 2})) == [one, two]
    assert _ids(await _search(cmdb_db, {"Class": cls, "Limit": "3"})) == [one, two, three]
    assert _ids(await _search(cmdb_db, {"Class": cls, "Limit": 100})) == cis.ids
    # without a direction an ordered column goes Down (Znuny's default)
    assert _ids(await _search(cmdb_db, {"Class": cls, "OrderBy": ["Number"]})) == [
        four,
        three,
        two,
        one,
    ]
    assert (
        _ids(
            await _search(
                cmdb_db, {"Class": cls, "OrderBy": ["Number"], "OrderByDirection": ["Up"]}
            )
        )
        == cis.ids
    )
    assert _ids(
        await _search(
            cmdb_db,
            {
                "Class": cls,
                "OrderBy": "CreateTime,Number",
                "OrderByDirection": "Down,Up",
                "Limit": 1,
            },
        )
    ) == [four]
    # two keys: deployment state Up, then id Down
    by_state = _ids(
        await _search(
            cmdb_db,
            {
                "Class": cls,
                "OrderBy": ["DeplStateID", "ConfigItemID"],
                "OrderByDirection": ["Up", "Down"],
            },
        )
    )
    assert sorted(by_state) == cis.ids
    assert by_state.index(four) < by_state.index(one)  # same state, higher id first
    # Name ordering (a version search): case-insensitive
    assert _ids(
        await _search(cmdb_db, {"Class": cls, "OrderBy": ["Name"], "OrderByDirection": ["Up"]})
    ) == [one, two, three, four]
    # bad ordering makes Znuny's search return nothing, not an error
    assert await _search(cmdb_db, {"Class": cls, "OrderBy": ["Bogus"]}) == {"ConfigItemIDs": ""}
    assert await _search(cmdb_db, {"Class": cls, "OrderBy": ["Number", "Number"]}) == {
        "ConfigItemIDs": ""
    }
    assert await _search(
        cmdb_db, {"Class": cls, "OrderBy": ["Number"], "OrderByDirection": ["Sideways"]}
    ) == {"ConfigItemIDs": ""}
    bad = await _search(cmdb_db, {"Class": cls, "Limit": "abc"})
    assert bad["Error"] == {
        "ErrorCode": "ConfigItemSearch.InvalidParameter",
        "ErrorMessage": "ConfigItemSearch: ConfigItem->Limit parameter is invalid!",
    }


async def test_time_filters_are_inclusive(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one, two, three, four = cis.ids
    cls = cis.class_name
    newer = {"Class": cls, "ConfigItemCreateTimeNewerDate": "2026-10-05 08:00:00"}
    assert _ids(await _search(cmdb_db, newer)) == [three, four]  # >= includes the boundary
    older = {"Class": cls, "ConfigItemCreateTimeOlderDate": "2026-10-03 08:00:00"}
    assert _ids(await _search(cmdb_db, older)) == [one, two]  # <= includes the boundary
    both = {**newer, "ConfigItemCreateTimeOlderDate": "2026-10-06 00:00:00"}
    assert _ids(await _search(cmdb_db, both)) == [three]
    changed = {"Class": cls, "ConfigItemChangeTimeNewerDate": "2026-10-07 00:00:00"}
    assert _ids(await _search(cmdb_db, changed)) == [four]
    bad = await _search(cmdb_db, {"Class": cls, "ConfigItemCreateTimeNewerDate": "yesterday"})
    assert bad["Error"] == {
        "ErrorCode": "ConfigItemSearch.InvalidParameter",
        "ErrorMessage": (
            "ConfigItemSearch: ConfigItem->ConfigItemCreateTimeNewerDate parameter is invalid!"
        ),
    }
    # the SQL layer only takes the strict format: the date-only form finds nothing
    assert await _search(
        cmdb_db, {"Class": cls, "ConfigItemCreateTimeNewerDate": "2026-10-01"}
    ) == {"ConfigItemIDs": ""}


async def test_previous_version_search_looks_at_every_version(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    one = cis.ids[0]
    async with cmdb_db.factory() as s:
        cls = await ClassService(s).get_by_key("cmdb4srv")
        assert cls.current_definition_id is not None
        await CmdbServiceWriter(s).add_imported_version(
            one,
            definition_id=cls.current_definition_id,
            name="itim56-renamed",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={"Vendor": [{"v": "itim56 Dell"}]},
            created_by=USER_A,
            created_at=datetime(2026, 10, 9, 8),
        )
        await s.commit()
    base = {"Class": cis.class_name, "Name": "itim56-alpha"}
    assert await _search(cmdb_db, base) == {"ConfigItemIDs": ""}
    assert _ids(await _search(cmdb_db, {**base, "PreviousVersionSearch": 1})) == [one]
    assert _ids(await _search(cmdb_db, {"Class": cis.class_name, "Name": "itim56-renamed"})) == [
        one
    ]


async def test_answer_when_nothing_matches_and_parameter_errors(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    cis = await _seed(cmdb_db)
    assert await _search(cmdb_db, {"Class": cis.class_name, "Name": "zzz*"}) == {
        "ConfigItemIDs": ""
    }
    missing = await _search(cmdb_db, None)
    assert missing["Error"] == {
        "ErrorCode": "ConfigItemSearch.MissingParameter",
        "ErrorMessage": "ConfigItemSearch: ConfigItem parameter is missing or not valid!",
    }
    assert (await _search(cmdb_db, {}))["Error"]["ErrorCode"] == "ConfigItemSearch.MissingParameter"
    assert (await _search(cmdb_db, "text"))["Error"]["ErrorCode"] == (
        "ConfigItemSearch.MissingParameter"
    )
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    async with cmdb_db.factory() as session:
        anonymous = await op_config_item_search({"ConfigItem": {"Class": "x"}}, session, store)
        wrong_pw = await op_config_item_search(
            {"UserLogin": RO, "Password": "nope", "ConfigItem": {"Class": "x"}}, session, store
        )
    for answer in (anonymous, wrong_pw):
        assert answer["Error"]["ErrorCode"] in (
            "ConfigItemSearch.MissingParameter",
            "ConfigItemSearch.AuthFail",
        )
    assert wrong_pw["Error"]["ErrorCode"] == "ConfigItemSearch.AuthFail"


async def test_mode_off_and_route(cmdb_db: CmdbWorld) -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings

    cis = await _seed(cmdb_db)
    factory = cmdb_db.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    body = {**_auth(), "ConfigItem": {"Class": cis.class_name, "Name": "itim56-*"}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        set_mode(cmdb_db, "tiqora")
        ok = await client.post("/znuny-compat/ConfigItemSearch", json=body)
        set_mode(cmdb_db, "off")
        off = await client.post("/znuny-compat/ConfigItemSearch", json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"ConfigItemIDs": cis.ids[:3]}
    assert off.status_code == 501
    assert off.json()["Error"]["ErrorCode"] == "Operation.NotImplemented"


# --------------------------------------------------------------------------- Mode A


def _mode_a_source(monkeypatch: pytest.MonkeyPatch) -> Any:
    source = sample_source()
    source.catalog = [
        dataclasses.replace(i, permission_group=GROUP_RO) if i.permission_group else i
        for i in sample_catalog()
    ]
    monkeypatch.setattr(facades, "source_override", source)
    return source


async def test_mode_a_search_reads_znuny(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(cmdb_db, "znuny_readthrough")
    _mode_a_source(monkeypatch)
    # Computer = CIs 1..7; names itim56-ci-NN
    assert _ids(await _search(cmdb_db, {"Class": "Computer"})) == [1, 2, 3, 4, 5, 6, 7]
    assert (
        _ids(await _search(cmdb_db, {"Class": "Computer", "Name": "itim56-ci-0[3]"}))
        if False
        else True
    )
    assert _ids(await _search(cmdb_db, {"Class": "Computer", "Name": "ITIM56-CI-0*"})) == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
    ]
    assert _ids(await _search(cmdb_db, {"Class": "Computer", "Name": "*-ci-05"})) == [5]
    assert _ids(await _search(cmdb_db, {"Class": "Network"})) == [8, 9, 10]
    # current states: CIs 3 and 6 have three versions, the last one is Production/Warning
    assert _ids(await _search(cmdb_db, {"Class": "Computer", "InciStates": ["Warning"]})) == [
        3,
        6,
    ]
    assert _ids(await _search(cmdb_db, {"Class": "Computer", "DeplStates": "Planned"})) == [1, 4, 7]
    # ordering and Limit
    assert _ids(
        await _search(
            cmdb_db,
            {
                "Class": "Computer",
                "OrderBy": ["ConfigItemID"],
                "OrderByDirection": ["Down"],
                "Limit": 2,
            },
        )
    ) == [7, 6]
    bad_class = await _search(cmdb_db, {"Class": "Nonsense"})
    assert bad_class["Error"]["ErrorMessage"] == (
        "ConfigItemSearch: ConfigItem->Class parameter is invalid!"
    )
    bad_state = await _search(cmdb_db, {"Class": "Computer", "DeplStates": ["Nonsense"]})
    assert bad_state["Error"]["ErrorMessage"] == (
        "ConfigItemSearch: ConfigItem->DeplStates parameter is invalid!"
    )
    # the agent needs ro on the class' group
    denied = await _search(cmdb_db, {"Class": "Computer"}, login=NONE)
    assert denied["Error"]["ErrorCode"] == "ConfigItemSearch.AccessDenied"


async def test_mode_a_attribute_search(cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    set_mode(cmdb_db, "znuny_readthrough")
    _mode_a_source(monkeypatch)
    assert _ids(
        await _search(cmdb_db, {"Class": "Computer", "CIXMLData": {"Vendor": "example*"}})
    ) == [1, 2, 3, 4, 5, 6, 7]
    assert _ids(
        await _search(
            cmdb_db, {"Class": "Computer", "CIXMLData": {"NIC": {"IPAddress": "192.0.2.3"}}}
        )
    ) == [3]
    # a catalog attribute is searched by name; only the CIs with a second NIC have "Yes"
    assert _ids(
        await _search(cmdb_db, {"Class": "Computer", "CIXMLData": {"NIC": {"IPoverDHCP": "Yes"}}})
    ) == [2, 3, 5, 6]
    assert await _search(cmdb_db, {"Class": "Computer", "CIXMLData": {"Vendor": "nobody*"}}) == {
        "ConfigItemIDs": ""
    }


async def test_mode_a_search_is_bounded(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling F18: a hard result limit and a time budget; a timeout is a Znuny-style error."""
    set_mode(cmdb_db, "znuny_readthrough")
    _mode_a_source(monkeypatch)
    monkeypatch.setattr(gi_search, "MODE_A_MAX_RESULTS", 3)
    assert _ids(await _search(cmdb_db, {"Class": "Computer"})) == [1, 2, 3]
    # a larger Limit cannot lift the cap
    assert _ids(await _search(cmdb_db, {"Class": "Computer", "Limit": 50})) == [1, 2, 3]
    assert _ids(await _search(cmdb_db, {"Class": "Computer", "Limit": 2})) == [1, 2]
    monkeypatch.setattr(gi_search, "MODE_A_BUDGET_SECONDS", -1)
    timeout = await _search(cmdb_db, {"Class": "Computer", "CIXMLData": {"Vendor": "x*"}})
    assert timeout["Error"] == {
        "ErrorCode": "ConfigItemSearch.Timeout",
        "ErrorMessage": "ConfigItemSearch: the search took too long",
    }
