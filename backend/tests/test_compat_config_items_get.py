"""GI ``ConfigItemGet`` and the ``ConfigItem*`` routing (Task 21; both dialects).

Rows come from the CMDB world of ``_cmdb_helpers`` (class group ``GROUP_RO``); the two agents
``itim56.gi.ro`` (``ro`` on that group) and ``itim56.gi.none`` (no group) and the webservice row
live in the plan's band 56430-56459. The expected error codes, texts and keys are the ones of
``backend/tests/fixtures/itsm/gi_shapes.md`` (ITSMConfigurationManagement 6.5.4 / 7.3.5).
"""

from __future__ import annotations

import base64
import dataclasses
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RO,
    USER_A,
    CmdbWorld,
    _dep,
    _inc,
    make_class,
    set_mode,
)
from tests._itsm_import_fixtures import T0, sample_catalog, sample_source
from tiqora.api.app import create_app
from tiqora.api.compat.config_items import op_config_item_get
from tiqora.api.deps import get_db, get_session_store
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.seed import DEPLOYMENT_STATE
from tiqora.config import Settings
from tiqora.domain.auth import SessionStore
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.facades import CmdbServiceWriter
from tiqora.itsm.importer.ports import CiView
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

PW = "itim56-gi-secret"
RO_ID, NONE_ID = 56430, 56431
RO, NONE = "itim56.gi.ro", "itim56.gi.none"
WS_ID, WS_NAME = 56450, "itim56GI"
NUMBER = "2026101256431"
WHEN = datetime(2026, 10, 1, 8, 0, 0)


@pytest.fixture(scope="module", autouse=True)
def gi_agents(cmdb_db: CmdbWorld) -> Iterator[None]:
    """The two GI agents (real password hashes); removed again after the module."""
    cleanup = (
        f"DELETE FROM gi_webservice_config WHERE id = {WS_ID}",
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
                        " VALUES (:id, :l, :pw, 'Gi', 'Agent', 1, :t, 1, :t, 1)"
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
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def expire(self, key: str, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def _client(world: CmdbWorld) -> AsyncClient:
    factory = world.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _op_get(world: CmdbWorld, **data: Any) -> dict[str, Any]:
    """``op_config_item_get`` called directly (bodies a path-based route cannot carry)."""
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    async with world.factory() as session:
        return await op_config_item_get({**_auth(), **data}, session, store)


def _auth(login: str = RO, password: str = PW) -> dict[str, str]:
    return {"UserLogin": login, "Password": password}


async def _seed_ci(
    world: CmdbWorld, *, number: str = NUMBER, name: str = "itim56-gi-ci", extra: bool = False
) -> int:
    """A Computer-like CI of class group ``GROUP_RO`` with a fixed number."""
    async with world.factory() as s:
        class_id = await make_class(s, group_id=GROUP_RO)
        cls = await ClassService(s).get(class_id)
        assert cls.current_definition_id is not None
        writer = CmdbServiceWriter(s)
        dep, inc = await _dep(s, "production"), await _inc(s, "operational")
        ci_id = await writer.create_imported_ci(
            class_id=class_id,
            number=number,
            name=name,
            created_by=USER_A,
            created_at=WHEN,
            deployment_state_id=dep,
            incident_state_id=inc,
        )
        attributes: dict[str, Any] = {"Vendor": [{"v": "itim56 Hersteller"}]}
        if extra:
            attributes["NIC"] = [
                {"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.5"}, {"v": "192.0.2.6"}]}}
            ]
        await writer.add_imported_version(
            ci_id,
            definition_id=cls.current_definition_id,
            name=name,
            deployment_state_id=dep,
            incident_state_id=inc,
            attributes=attributes,
            created_by=USER_A,
            created_at=WHEN,
        )
        await s.commit()
        return ci_id


async def test_get_returns_znuny_shape(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _seed_ci(cmdb_db, extra=True)
    async with _client(cmdb_db) as client:
        r = await client.get(f"/znuny-compat/ConfigItem/{ci_id}", params=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["ConfigItem"], list) and len(body["ConfigItem"]) == 1
    item = body["ConfigItem"][0]
    assert item["Number"] == NUMBER
    assert item["Class"] == "cmdb4srv"
    assert item["Name"] == "itim56-gi-ci"
    assert item["ConfigItemID"] == ci_id
    assert item["CIXMLData"]["Vendor"] == "itim56 Hersteller"
    # a sub-attribute: the hash carries its own value under its own key; of several entries of
    # one sub attribute only the last survives ``InvertFormatXMLData`` (Znuny quirk, gi_cixml.py)
    assert item["CIXMLData"]["NIC"] == {"NIC": "eth0", "IPAddress": "192.0.2.6"}
    assert set(item) == {
        "VersionID",
        "ConfigItemID",
        "Name",
        "DefinitionID",
        "CreateTime",
        "CreateBy",
        "DeplState",
        "DeplStateType",
        "InciState",
        "InciStateType",
        "Class",
        "LastVersionID",
        "Number",
        "CurDeplState",
        "CurDeplStateType",
        "CurInciState",
        "CurInciStateType",
        "CIXMLData",
    }
    assert item["DeplState"] == item["CurDeplState"] == "Production"
    assert item["DeplStateType"] == "productive"
    assert item["InciState"] == item["CurInciState"] == "Operational"
    assert item["InciStateType"] == "operational"
    assert item["VersionID"] == item["LastVersionID"]
    assert item["CreateBy"] == USER_A
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", item["CreateTime"])
    assert "Attachment" not in item


async def test_get_denies_class_without_ro(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _seed_ci(cmdb_db)
    async with _client(cmdb_db) as client:
        r = await client.get(f"/znuny-compat/ConfigItem/{ci_id}", params=_auth(NONE))
    assert r.status_code == 403
    assert r.json()["Error"] == {
        "ErrorCode": "ConfigItemGet.AccessDenied",
        "ErrorMessage": "ConfigItemGet: Can not get configuration item!",
    }


async def test_get_auth_failures(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _seed_ci(cmdb_db)
    async with _client(cmdb_db) as client:
        bad = await client.get(f"/znuny-compat/ConfigItem/{ci_id}", params=_auth(RO, "wrong"))
        anonymous = await client.get(f"/znuny-compat/ConfigItem/{ci_id}")
    for r in (bad, anonymous):
        assert r.status_code == 401
        assert r.json()["Error"]["ErrorCode"] == "ConfigItemGet.AuthFail"


async def test_get_mode_off_not_implemented(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "off")
    ci_id = await _seed_ci(cmdb_db)
    async with _client(cmdb_db) as client:
        r = await client.get(f"/znuny-compat/ConfigItem/{ci_id}", params=_auth())
        # the mode is looked at after authentication: an anonymous caller learns nothing
        anonymous = await client.get(f"/znuny-compat/ConfigItem/{ci_id}")
    assert r.status_code == 501
    assert r.json()["Error"]["ErrorCode"] == "Operation.NotImplemented"
    assert anonymous.json()["Error"]["ErrorCode"] == "ConfigItemGet.AuthFail"


@dataclasses.dataclass
class _FakeModeAReader:
    """Stands in for ``facades.ModeAReader``; records what it was asked."""

    view: CiView | None
    asked: list[tuple[int, int]] = dataclasses.field(default_factory=list)

    async def get_ci(self, znuny_id: int, *, user_id: int) -> CiView | None:
        self.asked.append((znuny_id, user_id))
        return self.view if self.view is not None and self.view.id == znuny_id else None

    async def attachment_contents(
        self, znuny_id: int
    ) -> list[tuple[str, str, bytes, dict[str, str]]]:
        return []

    async def catalog_names(self) -> dict[int, str]:
        return {7: "Znuny Yes"}


def _znuny_view(ci_id: int) -> CiView:
    schema = {
        "attributes": [
            {
                "key": "Vendor",
                "name": "Vendor",
                "type": "text",
                "required": False,
                "searchable": False,
                "portal": False,
                "count_min": 0,
                "count_max": 1,
                "count_default": 1,
                "default": None,
                "help": None,
                "options": {},
                "sub": [],
            },
            {
                "key": "Flag",
                "name": "Flag",
                "type": "catalog",
                "required": False,
                "searchable": False,
                "portal": False,
                "count_min": 0,
                "count_max": 1,
                "count_default": 1,
                "default": None,
                "help": None,
                "options": {"catalog_class": "ITSM::ConfigItem::YesNo"},
                "sub": [],
            },
        ]
    }
    return CiView(
        id=ci_id,
        number="9000001",
        name="znuny-side-ci",
        class_id=3,
        class_key="3",
        class_name="Computer",
        deployment_state="Planned",
        deployment_state_id=11,
        incident_state="Operational",
        incident_state_id=12,
        version_id=77,
        definition_version=2,
        attributes={"Vendor": [{"v": "Znuny Vendor"}], "Flag": [{"v": 7}]},
        schema=schema,
        created_at=T0,
        created_by=1,
        changed_at=datetime(2026, 2, 3, 4, 5, 6),
        attachments=[],
        changed_by=4,
        definition_id=9,
        deployment_functionality="preproductive",
        incident_functionality="operational",
        cur_deployment_state="Production",
        cur_deployment_functionality="productive",
        cur_incident_state="Warning",
        cur_incident_functionality="warning",
        last_version_id=78,
    )


async def test_get_in_mode_a_reads_znuny(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(cmdb_db, "znuny_readthrough")
    fake = _FakeModeAReader(_znuny_view(4242))
    monkeypatch.setattr(facades, "mode_a_override", fake)
    async with _client(cmdb_db) as client:
        r = await client.get("/znuny-compat/ConfigItem/4242", params=_auth())
        missing = await client.get("/znuny-compat/ConfigItem/4243", params=_auth())
    assert fake.asked == [(4242, RO_ID), (4243, RO_ID)]
    item = r.json()["ConfigItem"][0]
    assert item["ConfigItemID"] == 4242 and item["Number"] == "9000001"
    assert item["Name"] == "znuny-side-ci"
    assert item["CIXMLData"] == {"Vendor": "Znuny Vendor", "Flag": "Znuny Yes"}
    # the version's states and the CI's current states are different columns in Znuny
    assert (item["DeplState"], item["DeplStateType"]) == ("Planned", "preproductive")
    assert (item["CurDeplState"], item["CurDeplStateType"]) == ("Production", "productive")
    assert (item["CurInciState"], item["CurInciStateType"]) == ("Warning", "warning")
    assert (item["VersionID"], item["LastVersionID"], item["DefinitionID"]) == (77, 78, 9)
    assert (item["CreateTime"], item["CreateBy"]) == ("2026-02-03 04:05:06", 4)
    assert missing.json()["Error"]["ErrorCode"] == "ConfigItemGet.AccessDenied"


async def test_get_in_mode_a_with_the_real_reader_on_a_fake_source(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ModeAReader`` converts Znuny XMLData, honours the class group and names catalog ids."""
    set_mode(cmdb_db, "znuny_readthrough")
    source = sample_source()
    source.catalog = [
        dataclasses.replace(i, permission_group=GROUP_RO) if i.permission_group else i
        for i in sample_catalog()
    ]
    monkeypatch.setattr(facades, "source_override", source)
    async with _client(cmdb_db) as client:
        ok = await client.get("/znuny-compat/ConfigItem/1", params=_auth())
        none = await client.get("/znuny-compat/ConfigItem/1", params=_auth(NONE))
    assert ok.status_code == 200, ok.text
    item = ok.json()["ConfigItem"][0]
    assert item["Class"] == "Computer"
    assert item["Number"].endswith("56001")
    assert item["CIXMLData"]["Vendor"] == "Example Corp"
    # GeneralCatalog sub-attribute: the Znuny id becomes the catalog name
    assert item["CIXMLData"]["NIC"]["IPoverDHCP"] == "No"
    assert item["DeplState"] == "Planned" and item["DeplStateType"] == "preproductive"
    assert none.json()["Error"]["ErrorCode"] == "ConfigItemGet.AccessDenied"


async def test_get_in_mode_a_loads_catalog_and_definitions_once_per_request(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Up to 500 ids per call: Znuny's catalog and class definitions are read once, not per id."""
    set_mode(cmdb_db, "znuny_readthrough")
    source = sample_source()
    source.catalog = [
        dataclasses.replace(i, permission_group=GROUP_RO) if i.permission_group else i
        for i in sample_catalog()
    ]
    calls = {"catalog": 0, "definitions": 0}
    catalog_items, class_definitions = source.catalog_items, source.class_definitions

    async def counted_catalog() -> Any:
        calls["catalog"] += 1
        return await catalog_items()

    async def counted_definitions() -> Any:
        calls["definitions"] += 1
        return await class_definitions()

    monkeypatch.setattr(source, "catalog_items", counted_catalog)
    monkeypatch.setattr(source, "class_definitions", counted_definitions)
    monkeypatch.setattr(facades, "source_override", source)
    async with _client(cmdb_db) as client:
        r = await client.get("/znuny-compat/ConfigItem/1,2,3", params=_auth())
    assert r.status_code == 200, r.text
    assert [i["ConfigItemID"] for i in r.json()["ConfigItem"]] == [1, 2, 3]
    assert calls == {"catalog": 1, "definitions": 1}


async def test_get_with_attachments_base64(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _seed_ci(cmdb_db)
    payload = bytes(range(256)) * 2  # more than 76 base64 characters, binary
    async with cmdb_db.factory() as s:
        await CmdbServiceWriter(s).add_imported_attachment(
            ci_id,
            filename="itim56.bin",
            content_type="application/octet-stream",
            data=payload,
            created_at=WHEN,
            created_by=USER_A,
        )
        await s.commit()
    url = f"/znuny-compat/ConfigItem/{ci_id}"
    async with _client(cmdb_db) as client:
        plain = await client.get(url, params=_auth())
        with_att = await client.get(url, params={**_auth(), "Attachments": "1"})
        zero = await client.get(url, params={**_auth(), "Attachments": "0"})
    assert "Attachment" not in plain.json()["ConfigItem"][0]
    assert "Attachment" not in zero.json()["ConfigItem"][0]  # Perl truthiness: '0' is false
    (att,) = with_att.json()["ConfigItem"][0]["Attachment"]
    assert att["Filename"] == "itim56.bin"
    assert att["ContentType"] == "application/octet-stream"
    assert att["Type"] == "attachment"
    assert att["Filesize"] == str(len(payload))
    assert att["Preferences"]["FilesizeRaw"] == str(len(payload))
    # the preferences are the rows Znuny's VirtualFS keeps: no human-readable "Filesize" in them,
    # the uploader and an empty ContentID instead (golden: test_gi_config_items.py)
    assert att["Preferences"] == {
        "ConfigItemID": str(ci_id),
        "ContentID": "",
        "ContentType": "application/octet-stream",
        "FilesizeRaw": str(len(payload)),
        "UserID": str(USER_A),
    }
    assert att["Content"].endswith("\n") and "\n" in att["Content"][:-1]  # encode_base64 lines
    assert base64.b64decode(att["Content"]) == payload


async def test_get_attachment_is_an_empty_string_when_there_is_none(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _seed_ci(cmdb_db)
    async with _client(cmdb_db) as client:
        r = await client.get(
            f"/znuny-compat/ConfigItem/{ci_id}", params={**_auth(), "Attachments": "1"}
        )
    assert r.json()["ConfigItem"][0]["Attachment"] == ""


async def test_get_multiple_ids_and_unknown_id(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    first = await _seed_ci(cmdb_db)
    async with cmdb_db.factory() as s:
        cls = await ClassService(s).get_by_key("cmdb4srv")
        assert cls.current_definition_id is not None
        writer = CmdbServiceWriter(s)
        second = await writer.create_imported_ci(
            class_id=cls.id,
            number="2026101256432",
            name="itim56-gi-ci-2",
            created_by=USER_A,
            created_at=WHEN,
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
        )
        await writer.add_imported_version(
            second,
            definition_id=cls.current_definition_id,
            name="itim56-gi-ci-2",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={},
            created_by=USER_A,
            created_at=WHEN,
        )
        await s.commit()
    base = "/znuny-compat/ConfigItem"
    async with _client(cmdb_db) as client:
        both = await client.get(f"{base}/{second},{first}", params=_auth())
        unknown = await client.get(f"{base}/{first},999999999", params=_auth())
        text_id = await client.get(f"{base}/NotExistent56431", params=_auth())
        missing = await client.get(f"{base}/0", params=_auth())
    as_list = await _op_get(cmdb_db, ConfigItemID=[first, str(second)])
    wrong_shape = await _op_get(cmdb_db, ConfigItemID={"Test1": 1})
    empty_list = await _op_get(cmdb_db, ConfigItemID=[])
    too_many = await _op_get(cmdb_db, ConfigItemID=",".join(["1"] * 501))
    assert [i["ConfigItemID"] for i in both.json()["ConfigItem"]] == [second, first]
    assert [i["Number"] for i in both.json()["ConfigItem"]] == ["2026101256432", NUMBER]
    assert [i["ConfigItemID"] for i in as_list["ConfigItem"]] == [first, second]
    # one bad id fails the whole call (nothing is omitted), unknown and forbidden look the same
    for r in (unknown, text_id):
        assert r.status_code == 403
        assert r.json()["Error"] == {
            "ErrorCode": "ConfigItemGet.AccessDenied",
            "ErrorMessage": "ConfigItemGet: Can not get configuration item!",
        }
    assert missing.json()["Error"] == {
        "ErrorCode": "ConfigItemGet.MissingParameter",
        "ErrorMessage": "ConfigItemGet: ConfigItemID parameter is missing!",
    }
    for bad in (wrong_shape, empty_list):  # a hash and Perl's (true) empty array ref
        assert bad["Error"] == {
            "ErrorCode": "ConfigItemGet.WrongStructure",
            "ErrorMessage": "ConfigItemGet: Structure for ConfigItemID is not correct!",
        }
    assert too_many["Error"]["ErrorCode"] == "ConfigItemGet.InvalidParameter"


async def test_get_catalog_attribute_is_returned_by_name(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    from tests._cmdb_helpers import _attr

    attrs = [_attr("Stage", "Stage", "catalog", options={"catalog_class": DEPLOYMENT_STATE})]
    async with cmdb_db.factory() as s:
        class_id = await make_class(s, group_id=GROUP_RO, attributes=attrs)
        cls = await ClassService(s).get(class_id)
        assert cls.current_definition_id is not None
        dep, inc = await _dep(s, "production"), await _inc(s, "operational")
        writer = CmdbServiceWriter(s)
        ci_id = await writer.create_imported_ci(
            class_id=class_id,
            number="2026101256433",
            name="itim56-gi-stage",
            created_by=USER_A,
            created_at=WHEN,
            deployment_state_id=dep,
            incident_state_id=inc,
        )
        await writer.add_imported_version(
            ci_id,
            definition_id=cls.current_definition_id,
            name="itim56-gi-stage",
            deployment_state_id=dep,
            incident_state_id=inc,
            attributes={"Stage": [{"v": dep}]},
            created_by=USER_A,
            created_at=WHEN,
        )
        await s.commit()
    async with _client(cmdb_db) as client:
        r = await client.get(f"/znuny-compat/ConfigItem/{ci_id}", params=_auth())
    assert r.json()["ConfigItem"][0]["CIXMLData"] == {"Stage": "Production"}


async def test_write_and_search_operations_answer_per_mode(cmdb_db: CmdbWorld) -> None:
    """The three write operations exist as routes and honour the mode gate."""
    routes = (
        ("POST", "/znuny-compat/ConfigItem", "ConfigItemCreate"),
        ("PATCH", "/znuny-compat/ConfigItem/1", "ConfigItemUpdate"),
        ("DELETE", "/znuny-compat/ConfigItem/1", "ConfigItemDelete"),
    )
    async with _client(cmdb_db) as client:
        for mode in ("off", "znuny_readthrough", "tiqora"):
            set_mode(cmdb_db, mode)
            for method, url, op in routes:
                r = await client.request(method, url, params=_auth())
                code = r.json()["Error"]["ErrorCode"]
                if mode == "znuny_readthrough":
                    assert code == f"{op}.NotAvailable", (mode, op)
                    assert r.json()["Error"]["ErrorMessage"] == (
                        "Die CMDB wird in Znuny geführt (Modus A)"
                    )
                elif mode == "tiqora" and op == "ConfigItemDelete":
                    # Delete exists since Task 24: CI 1 is unknown, which looks like a missing right
                    assert code == "ConfigItemDelete.AccessDenied", (mode, op)
                    assert r.status_code == 403
                elif mode == "tiqora":
                    # Create/Update exist since Task 23: no ConfigItem in the request
                    assert code == f"{op}.MissingParameter", (mode, op)
                else:
                    assert code == "Operation.NotImplemented", (mode, op)
                    assert r.status_code == 501


async def test_webservice_route_dispatches_config_item_get(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _seed_ci(cmdb_db)
    config = {
        "Provider": {
            "Transport": {
                "Type": "HTTP::REST",
                "Config": {
                    "RouteOperationMapping": {
                        "CIGet": {"Route": "/Item/:ConfigItemID", "RequestMethod": ["GET"]}
                    }
                },
            },
            "Operation": {"CIGet": {"Type": "ConfigItem::ConfigItemGet"}},
        }
    }
    raw = yaml.safe_dump(config)
    stored: bytes | str = raw if cmdb_db.dialect == "postgresql" else raw.encode()
    cmdb_db.sql(
        "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time, create_by,"
        " change_time, change_by) VALUES (:id, :n, :c, 1, :t, 1, :t, 1)",
        id=WS_ID,
        n=WS_NAME,
        c=stored,
        t=WHEN,
    )
    async with _client(cmdb_db) as client:
        r = await client.get(f"/znuny-compat/Webservice/{WS_NAME}/Item/{ci_id}", params=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["ConfigItem"][0]["Number"] == NUMBER
