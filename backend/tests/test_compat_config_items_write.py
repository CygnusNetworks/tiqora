"""GI ``ConfigItemCreate`` and ``ConfigItemUpdate`` on the Tiqora CMDB (Task 23; both dialects).

Rows come from the CMDB world of ``_cmdb_helpers`` (class ``cmdb4srv`` in group ``GROUP_RW``);
the three agents ``itim56.wr.rw`` (``rw`` on that group), ``itim56.wr.ro`` (``ro`` only) and
``itim56.wr.none`` (no group) live in the plan's band 56490-56529. Error codes, texts and the
answer keys are the ones of ``ConfigItemCreate.pm`` / ``ConfigItemUpdate.pm`` of
ITSMConfigurationManagement 6.5.4 and 7.3.5 (the two versions only differ in whitespace).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import GROUP_RW, CmdbWorld, _attr, make_class, set_mode
from tiqora.api.compat.config_items import (
    MAX_ATTACHMENT_BYTES,
    op_config_item_create,
    op_config_item_get,
    op_config_item_update,
)
from tiqora.cmdb.seed import DEPLOYMENT_STATE
from tiqora.domain.auth import SessionStore
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

PW = "itim56-wr-secret"
RW_ID, RO_ID, NONE_ID = 56490, 56491, 56492
RW, RO, NONE = "itim56.wr.rw", "itim56.wr.ro", "itim56.wr.none"
WHEN = datetime(2026, 10, 1, 8, 0, 0)
CUSTOMER = "cmdb4-cust-a@example.org"  # seeded by the CMDB world


@pytest.fixture(scope="module", autouse=True)
def write_agents(cmdb_db: CmdbWorld) -> Iterator[None]:
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
                        " VALUES (:id, :l, :pw, 'Wr', 'Agent', 1, :t, 1, :t, 1)"
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


def _auth(login: str = RW) -> dict[str, str]:
    return {"UserLogin": login, "Password": PW}


async def _call(op: Any, world: CmdbWorld, data: dict[str, Any]) -> dict[str, Any]:
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    async with world.factory() as session:
        return await op(data, session, store)  # type: ignore[no-any-return]


async def _create(
    world: CmdbWorld, item: Any, *, login: str = RW, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {**_auth(login), **(extra or {})}
    if item is not None:
        data["ConfigItem"] = item
    return await _call(op_config_item_create, world, data)


async def _update(
    world: CmdbWorld,
    ci_id: Any,
    item: Any,
    *,
    login: str = RW,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {**_auth(login), **(extra or {})}
    if ci_id is not None:
        data["ConfigItemID"] = ci_id
    if item is not None:
        data["ConfigItem"] = item
    return await _call(op_config_item_update, world, data)


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Class": "cmdb4srv",
        "Name": "itim56-wr-ci",
        "DeplState": "Production",
        "InciState": "Operational",
        "CIXMLData": {"Vendor": "itim56 Hersteller"},
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


async def _prepare(world: CmdbWorld, attrs: list[dict[str, Any]] | None = None) -> None:
    set_mode(world, "tiqora")
    async with world.factory() as s:
        await make_class(s, attributes=attrs)


def _error(result: dict[str, Any]) -> tuple[str, str]:
    return result["Error"]["ErrorCode"], result["Error"]["ErrorMessage"]


def _versions(world: CmdbWorld, ci_id: int) -> list[Any]:
    return world.sql(
        "SELECT id, name, attributes FROM tiqora_cmdb_ci_version WHERE ci_id = :c ORDER BY id",
        c=ci_id,
    )


def _history(world: CmdbWorld, ci_id: int) -> list[Any]:
    return world.sql(
        "SELECT event, created_by FROM tiqora_cmdb_ci_history WHERE ci_id = :c ORDER BY id",
        c=ci_id,
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --------------------------------------------------------------------------- create


async def test_create_writes_ci_with_one_version(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    result = await _create(
        cmdb_db,
        _item(
            Name="  itim56 spaced  ",
            CIXMLData={
                "Vendor": " itim56 Hersteller ",
                "CPU": "8",
                "NIC": [
                    {"NIC": "eth0", "IPAddress": ["192.0.2.5", "192.0.2.6"]},
                    {"NIC": "eth1"},
                ],
                "Purchase": "2026-09-30",
            },
        ),
    )
    assert set(result) == {"ConfigItemID", "Number"}, result
    ci_id = result["ConfigItemID"]
    assert isinstance(ci_id, int)
    rows = cmdb_db.sql("SELECT number, name FROM tiqora_cmdb_ci WHERE id = :c", c=ci_id)
    assert rows[0][0] == result["Number"] and rows[0][1] == "itim56 spaced"
    versions = _versions(cmdb_db, ci_id)
    assert len(versions) == 1
    attrs = json.loads(versions[0][2])
    assert attrs["Vendor"] == [{"v": "itim56 Hersteller"}]
    assert attrs["CPU"] == [{"v": 8}]
    assert attrs["Purchase"] == [{"v": "2026-09-30"}]
    assert attrs["NIC"] == [
        {"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.5"}, {"v": "192.0.2.6"}]}},
        {"v": "eth1", "s": {}},
    ]
    events = _history(cmdb_db, ci_id)
    assert [e[0] for e in events][:2] == ["ConfigItemCreate", "VersionCreate"]
    assert {e[1] for e in events} == {RW_ID}


async def test_create_without_cixmldata_is_allowed(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    result = await _create(cmdb_db, _item(CIXMLData=None))
    assert "ConfigItemID" in result, result
    assert json.loads(_versions(cmdb_db, result["ConfigItemID"])[0][2]) == {}


async def test_create_catalog_name_becomes_catalog_id(cmdb_db: CmdbWorld) -> None:
    attrs = [_attr("Stage", "Stage", "catalog", options={"catalog_class": DEPLOYMENT_STATE})]
    await _prepare(cmdb_db, attrs)
    result = await _create(cmdb_db, _item(CIXMLData={"Stage": "Production"}))
    assert "ConfigItemID" in result, result
    stored = json.loads(_versions(cmdb_db, result["ConfigItemID"])[0][2])
    prod = cmdb_db.sql(
        "SELECT id FROM tiqora_cmdb_catalog WHERE catalog_class = :c AND name = 'Production'",
        c=DEPLOYMENT_STATE,
    )[0][0]
    assert stored == {"Stage": [{"v": prod}]}
    # the answer of ConfigItemGet gives the name back
    got = await _call(
        op_config_item_get, cmdb_db, {**_auth(), "ConfigItemID": result["ConfigItemID"]}
    )
    assert got["ConfigItem"][0]["CIXMLData"] == {"Stage": "Production"}


async def test_create_unknown_catalog_name_is_refused(cmdb_db: CmdbWorld) -> None:
    attrs = [_attr("Stage", "Stage", "catalog", options={"catalog_class": DEPLOYMENT_STATE})]
    await _prepare(cmdb_db, attrs)
    result = await _create(cmdb_db, _item(CIXMLData={"Stage": "No such stage"}))
    assert _error(result) == (
        "ConfigItemCreate.InvalidParameter",
        "ConfigItemCreate: ConfigItem->CIXMLData->Stage parameter value is not a valid for "
        f"General Catalog '{DEPLOYMENT_STATE}'!",
    )


async def test_create_customer_must_exist(cmdb_db: CmdbWorld) -> None:
    attrs = [_attr("Owner", "Owner", "customer_user")]
    await _prepare(cmdb_db, attrs)
    ok = await _create(cmdb_db, _item(CIXMLData={"Owner": CUSTOMER}))
    assert "ConfigItemID" in ok, ok
    assert json.loads(_versions(cmdb_db, ok["ConfigItemID"])[0][2]) == {"Owner": [{"v": CUSTOMER}]}
    bad = await _create(cmdb_db, _item(CIXMLData={"Owner": "nobody@example.org"}))
    assert _error(bad) == (
        "ConfigItemCreate.InvalidParameter",
        "ConfigItemCreate: ConfigItem->CIXMLData->Owner parameter value is not a valid customer!",
    )


@pytest.mark.parametrize("missing", ["Class", "Name", "DeplState", "InciState"])
async def test_create_missing_header_parameter(cmdb_db: CmdbWorld, missing: str) -> None:
    await _prepare(cmdb_db)
    result = await _create(cmdb_db, _item(**{missing: None}))
    assert _error(result) == (
        "ConfigItemCreate.MissingParameter",
        f"ConfigItemCreate: ConfigItem->{missing} parameter is missing!",
    )


async def test_create_missing_or_empty_config_item(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    expected = (
        "ConfigItemCreate.MissingParameter",
        "ConfigItemCreate: ConfigItem parameter is missing or not valid!",
    )
    assert _error(await _create(cmdb_db, None)) == expected
    assert _error(await _create(cmdb_db, {})) == expected
    assert _error(await _create(cmdb_db, "text")) == expected


async def test_create_cixmldata_must_be_a_hash(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    for bad in ("text", [], {}):
        result = await _create(cmdb_db, _item(CIXMLData=bad))
        assert _error(result) == (
            "ConfigItemCreate.MissingParameter",
            "ConfigItemCreate: ConfigItem->CIXMLData is missing or invalid!",
        ), bad


async def test_create_unknown_class_and_states(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    for field, value in (("Class", "Nope"), ("DeplState", "Nope"), ("InciState", "Nope")):
        result = await _create(cmdb_db, _item(**{field: value}))
        assert _error(result) == (
            "ConfigItemCreate.InvalidParameter",
            f"ConfigItemCreate: ConfigItem->{field} parameter is invalid!",
        ), field
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0


async def test_create_cixmldata_over_count_max(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    nics = [{"NIC": f"eth{i}"} for i in range(4)]  # NIC allows three
    result = await _create(cmdb_db, _item(CIXMLData={"NIC": nics}))
    assert _error(result) == (
        "ConfigItemCreate.InvalidParameter",
        "ConfigItemCreate: ConfigItem->CIXMLData->NIC parameter repetitions is higher than "
        "the maxium value!",
    )
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0


async def test_create_required_attribute_missing(cmdb_db: CmdbWorld) -> None:
    attrs = [{**_attr("Vendor", "Vendor", "text"), "required": True, "count_min": 1}]
    await _prepare(cmdb_db, attrs)
    result = await _create(cmdb_db, _item(CIXMLData={"Model": "x"}))
    assert _error(result) == (
        "ConfigItemCreate.MissingParameter",
        "ConfigItemCreate: ConfigItem->CIXMLData->Vendor parameter is missing!",
    )


async def test_create_requires_rw_on_the_class_group(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    for login in (RO, NONE):
        result = await _create(cmdb_db, _item(), login=login)
        assert _error(result) == (
            "ConfigItemCreate.AccessDenied",
            "ConfigItemCreate: Can not create configuration items!",
        ), login
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0


async def test_create_auth_failures(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    bad = await _call(
        op_config_item_create,
        cmdb_db,
        {"UserLogin": RW, "Password": "wrong", "ConfigItem": _item()},
    )
    assert bad["Error"]["ErrorCode"] == "ConfigItemCreate.AuthFail"
    anonymous = await _call(op_config_item_create, cmdb_db, {"ConfigItem": _item()})
    assert anonymous["Error"]["ErrorCode"] == "ConfigItemCreate.AuthFail"


async def test_create_in_mode_a_is_not_available(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    set_mode(cmdb_db, "znuny_readthrough")
    result = await _create(cmdb_db, _item())
    assert _error(result) == (
        "ConfigItemCreate.NotAvailable",
        "Die CMDB wird in Znuny geführt (Modus A)",
    )
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0
    set_mode(cmdb_db, "off")
    assert (await _create(cmdb_db, _item()))["Error"]["ErrorCode"] == "Operation.NotImplemented"


async def test_create_number_is_generated_not_taken_from_the_request(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    first = await _create(cmdb_db, _item(Number="111"))
    second = await _create(cmdb_db, _item(Number="111"))
    assert first["Number"] != "111" and second["Number"] != first["Number"]


# --------------------------------------------------------------------------- create: attachments


async def test_create_attachments_are_decoded_and_keep_their_content_type(
    cmdb_db: CmdbWorld,
) -> None:
    await _prepare(cmdb_db)
    payload = bytes(range(256)) * 3
    result = await _create(
        cmdb_db,
        _item(
            Attachment=[
                {"Content": _b64(payload), "ContentType": "Application/PDF", "Filename": "a.pdf"},
                {"Content": _b64(b"hello"), "ContentType": "text/plain", "Filename": "b.txt"},
            ]
        ),
    )
    ci_id = result["ConfigItemID"]
    rows = cmdb_db.sql(
        "SELECT filename, content_type, size_bytes FROM tiqora_cmdb_attachment"
        " WHERE ci_id = :c ORDER BY filename",
        c=ci_id,
    )
    assert [tuple(r) for r in rows] == [
        ("a.pdf", "application/pdf", len(payload)),
        ("b.txt", "text/plain", 5),
    ]
    got = await _call(
        op_config_item_get,
        cmdb_db,
        {**_auth(), "ConfigItemID": ci_id, "Attachments": 1},
    )
    files = {a["Filename"]: a for a in got["ConfigItem"][0]["Attachment"]}
    assert base64.b64decode(files["a.pdf"]["Content"]) == payload
    assert files["b.txt"]["ContentType"] == "text/plain"
    assert "AttachmentAddPost" in [e[0] for e in _history(cmdb_db, ci_id)]


async def test_create_single_attachment_hash(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    result = await _create(
        cmdb_db,
        _item(Attachment={"Content": _b64(b"x"), "ContentType": "text/plain", "Filename": "x.txt"}),
    )
    assert (
        cmdb_db.sql(
            "SELECT COUNT(*) FROM tiqora_cmdb_attachment WHERE ci_id = :c", c=result["ConfigItemID"]
        )[0][0]
        == 1
    )


async def test_create_attachment_over_50_mb_is_refused_and_nothing_is_written(
    cmdb_db: CmdbWorld,
) -> None:
    await _prepare(cmdb_db)
    big = base64.b64encode(b"\0" * (MAX_ATTACHMENT_BYTES + 1)).decode("ascii")
    result = await _create(
        cmdb_db,
        _item(Attachment=[{"Content": big, "ContentType": "text/plain", "Filename": "big.bin"}]),
    )
    assert _error(result) == (
        "ConfigItemCreate.InvalidParameter",
        "ConfigItemCreate: Attachment->Content is too large (at most 50 MB)!",
    )
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0


async def test_attachment_limit_is_inclusive(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary with a small limit (a 50 MB row does not fit MariaDB's default packet)."""
    from tiqora.api.compat import config_items

    await _prepare(cmdb_db)
    monkeypatch.setattr(config_items, "MAX_ATTACHMENT_BYTES", 1000)

    def att(size: int) -> dict[str, str]:
        return {
            "Content": _b64(b"\0" * size),
            "ContentType": "text/plain",
            "Filename": f"f{size}.bin",
        }

    ok = await _create(cmdb_db, _item(Attachment=[att(1000)]))
    assert "ConfigItemID" in ok, ok
    big = await _create(cmdb_db, _item(Attachment=[att(1001)]))
    assert big["Error"]["ErrorCode"] == "ConfigItemCreate.InvalidParameter"
    assert "too large" in big["Error"]["ErrorMessage"]
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 1


@pytest.mark.parametrize("missing", ["Content", "ContentType", "Filename"])
async def test_create_attachment_missing_field(cmdb_db: CmdbWorld, missing: str) -> None:
    await _prepare(cmdb_db)
    att = {"Content": _b64(b"x"), "ContentType": "text/plain", "Filename": "x.txt"}
    del att[missing]
    result = await _create(cmdb_db, _item(Attachment=[att]))
    assert _error(result) == (
        "ConfigItemCreate.MissingParameter",
        f"ConfigItemCreate: Attachment->{missing} parameter is missing!",
    )
    assert cmdb_db.sql("SELECT COUNT(*) FROM tiqora_cmdb_ci")[0][0] == 0


async def test_create_attachment_invalid_shape_and_content_type(cmdb_db: CmdbWorld) -> None:
    await _prepare(cmdb_db)
    for bad in ("text", [], ["text"], [{}]):
        result = await _create(cmdb_db, _item(Attachment=bad))
        assert _error(result) == (
            "ConfigItemCreate.InvalidParameter",
            "ConfigItemCreate: ConfigItem->Attachment parameter is invalid!",
        ), bad
    for content_type in ("nonsense", "text/plain; charset=no-such-charset"):
        result = await _create(
            cmdb_db,
            _item(
                Attachment=[
                    {"Content": _b64(b"x"), "ContentType": content_type, "Filename": "x.txt"}
                ]
            ),
        )
        assert _error(result) == (
            "ConfigItemCreate.InvalidParameter",
            "ConfigItemCreate: Attachment->ContentType is invalid!",
        ), content_type


# --------------------------------------------------------------------------- update


async def _seed(world: CmdbWorld, **over: Any) -> dict[str, Any]:
    await _prepare(world)
    result = await _create(world, _item(**over))
    assert "ConfigItemID" in result, result
    return result


async def test_update_creates_exactly_one_new_version(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    ci_id = created["ConfigItemID"]
    result = await _update(
        cmdb_db,
        ci_id,
        _item(Name="itim56 renamed", DeplState="Planned", CIXMLData={"Model": "M1"}),
    )
    assert result == {"ConfigItemID": ci_id, "Number": created["Number"]}
    versions = _versions(cmdb_db, ci_id)
    assert len(versions) == 2
    # not ReplaceExistingData: the Vendor of the first version is merged in
    assert json.loads(versions[1][2]) == {
        "Vendor": [{"v": "itim56 Hersteller"}],
        "Model": [{"v": "M1"}],
    }
    row = cmdb_db.sql(
        "SELECT name, version_count, changed_by FROM tiqora_cmdb_ci WHERE id = :c", c=ci_id
    )[0]
    assert tuple(row) == ("itim56 renamed", 2, RW_ID)
    assert "VersionCreate" in [e[0] for e in _history(cmdb_db, ci_id)]


async def test_update_unchanged_payload_adds_no_version_but_answers_success(
    cmdb_db: CmdbWorld,
) -> None:
    created = await _seed(cmdb_db)
    ci_id = created["ConfigItemID"]
    result = await _update(cmdb_db, ci_id, _item(CIXMLData={"Vendor": "itim56 Hersteller"}))
    assert result == {"ConfigItemID": ci_id, "Number": created["Number"]}
    assert len(_versions(cmdb_db, ci_id)) == 1


async def test_update_merges_top_level_keys_unless_replace_existing_data(
    cmdb_db: CmdbWorld,
) -> None:
    created = await _seed(cmdb_db, CIXMLData={"Vendor": "V1", "Model": "M1", "CPU": "2"})
    ci_id = created["ConfigItemID"]
    await _update(cmdb_db, ci_id, _item(CIXMLData={"Model": "M2"}))
    assert json.loads(_versions(cmdb_db, ci_id)[-1][2]) == {
        "Vendor": [{"v": "V1"}],
        "Model": [{"v": "M2"}],
        "CPU": [{"v": 2}],
    }
    await _update(
        cmdb_db, ci_id, _item(CIXMLData={"Model": "M3"}), extra={"ReplaceExistingData": 1}
    )
    assert json.loads(_versions(cmdb_db, ci_id)[-1][2]) == {"Model": [{"v": "M3"}]}
    assert len(_versions(cmdb_db, ci_id)) == 3


async def test_update_replace_existing_data_without_cixmldata_keeps_the_data(
    cmdb_db: CmdbWorld,
) -> None:
    created = await _seed(cmdb_db, CIXMLData={"Vendor": "V1"})
    ci_id = created["ConfigItemID"]
    result = await _update(
        cmdb_db, ci_id, _item(Name="renamed", CIXMLData=None), extra={"ReplaceExistingData": 1}
    )
    assert result["ConfigItemID"] == ci_id
    assert json.loads(_versions(cmdb_db, ci_id)[-1][2]) == {"Vendor": [{"v": "V1"}]}


async def test_update_empty_cixmldata_is_invalid(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db, CIXMLData=None)
    result = await _update(cmdb_db, created["ConfigItemID"], _item(CIXMLData=None))
    assert _error(result) == (
        "ConfigItemUpdate.InvalidParameter",
        "ConfigItemUpdate: ConfigItem->CIXMLData is empty or invalid!",
    )
    bad = await _update(cmdb_db, created["ConfigItemID"], _item(CIXMLData="text"))
    assert _error(bad) == (
        "ConfigItemUpdate.MissingParameter",
        "ConfigItemUpdate: ConfigItem->CIXMLData is missing or invalid!",
    )


async def test_update_parameter_checks(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    ci_id = created["ConfigItemID"]
    assert _error(await _update(cmdb_db, ci_id, None)) == (
        "ConfigItemUpdate.MissingParameter",
        "ConfigItemUpdate: ConfigItem parameter is missing or not valid!",
    )
    for bad_id in (None, "abc", "0", "-3", "1.5"):
        assert _error(await _update(cmdb_db, bad_id, _item())) == (
            "ConfigItemUpdate.MissingParameter",
            "ConfigItemUpdate: ConfigItemID parameter is missing or not valid!",
        ), bad_id
    assert _error(await _update(cmdb_db, ci_id + 9999, _item())) == (
        "ConfigItemUpdate.InvalidParameter",
        "ConfigItemUpdate: ConfigItemID is invalid!",
    )
    for field in ("Class", "Name", "DeplState", "InciState"):
        assert _error(await _update(cmdb_db, ci_id, _item(**{field: None}))) == (
            "ConfigItemUpdate.MissingParameter",
            f"ConfigItemUpdate: ConfigItem->{field} parameter is missing!",
        ), field
    for field in ("Class", "DeplState", "InciState"):
        assert _error(await _update(cmdb_db, ci_id, _item(**{field: "Nope"}))) == (
            "ConfigItemUpdate.InvalidParameter",
            f"ConfigItemUpdate: ConfigItem->{field} parameter is invalid!",
        ), field
    assert len(_versions(cmdb_db, ci_id)) == 1


async def test_update_cixmldata_violating_count_max(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    nics = [{"NIC": f"eth{i}"} for i in range(4)]
    result = await _update(cmdb_db, created["ConfigItemID"], _item(CIXMLData={"NIC": nics}))
    assert _error(result) == (
        "ConfigItemUpdate.InvalidParameter",
        "ConfigItemUpdate: ConfigItem->CIXMLData->NIC parameter repetitions is higher than "
        "the maxium value!",
    )


async def test_update_class_must_be_the_class_of_the_ci(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    async with cmdb_db.factory() as s:
        await make_class(s, key="cmdb4other")
    result = await _update(cmdb_db, created["ConfigItemID"], _item(Class="cmdb4other"))
    assert _error(result) == (
        "ConfigItemUpdate.InvalidParameter",
        "ConfigItemUpdate: ConfigItem->Class parameter is invalid!",
    )
    assert len(_versions(cmdb_db, created["ConfigItemID"])) == 1


async def test_update_requires_rw_on_the_class_group(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    ci_id = created["ConfigItemID"]
    for login in (RO, NONE):
        result = await _update(cmdb_db, ci_id, _item(Name="denied"), login=login)
        assert _error(result) == (
            "ConfigItemUpdate.AccessDenied",
            "ConfigItemUpdate: Can not update configuration items!",
        ), login
    assert len(_versions(cmdb_db, ci_id)) == 1


async def test_update_in_mode_a_is_not_available(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    set_mode(cmdb_db, "znuny_readthrough")
    result = await _update(cmdb_db, created["ConfigItemID"], _item(Name="x"))
    assert _error(result) == (
        "ConfigItemUpdate.NotAvailable",
        "Die CMDB wird in Znuny geführt (Modus A)",
    )
    assert len(_versions(cmdb_db, created["ConfigItemID"])) == 1


async def test_update_adds_attachments_and_replace_existing_data_drops_the_old_ones(
    cmdb_db: CmdbWorld,
) -> None:
    created = await _seed(
        cmdb_db,
        Attachment=[{"Content": _b64(b"one"), "ContentType": "text/plain", "Filename": "one.txt"}],
    )
    ci_id = created["ConfigItemID"]

    def names() -> list[str]:
        rows = cmdb_db.sql(
            "SELECT filename FROM tiqora_cmdb_attachment WHERE ci_id = :c ORDER BY filename",
            c=ci_id,
        )
        return [r[0] for r in rows]

    await _update(
        cmdb_db,
        ci_id,
        _item(
            Attachment={"Content": _b64(b"two"), "ContentType": "text/plain", "Filename": "two.txt"}
        ),
    )
    assert names() == ["one.txt", "two.txt"]  # added, the unchanged data created no version
    assert len(_versions(cmdb_db, ci_id)) == 1
    await _update(
        cmdb_db,
        ci_id,
        _item(
            Attachment=[
                {"Content": _b64(b"three"), "ContentType": "text/plain", "Filename": "three.txt"}
            ]
        ),
        extra={"ReplaceExistingData": 1},
    )
    assert names() == ["three.txt"]


async def test_update_attachment_over_50_mb_changes_nothing(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    ci_id = created["ConfigItemID"]
    big = base64.b64encode(b"\0" * (MAX_ATTACHMENT_BYTES + 1)).decode("ascii")
    result = await _update(
        cmdb_db,
        ci_id,
        _item(
            Name="would-rename",
            Attachment=[{"Content": big, "ContentType": "text/plain", "Filename": "big.bin"}],
        ),
    )
    assert _error(result) == (
        "ConfigItemUpdate.InvalidParameter",
        "ConfigItemUpdate: Attachment->Content is too large (at most 50 MB)!",
    )
    assert len(_versions(cmdb_db, ci_id)) == 1
    name = cmdb_db.sql("SELECT name FROM tiqora_cmdb_ci WHERE id = :c", c=ci_id)[0][0]
    assert name == "itim56-wr-ci"


async def test_update_missing_attachment_field_uses_the_update_text(cmdb_db: CmdbWorld) -> None:
    created = await _seed(cmdb_db)
    result = await _update(
        cmdb_db,
        created["ConfigItemID"],
        _item(Attachment=[{"ContentType": "text/plain", "Filename": "x.txt"}]),
    )
    # ConfigItemUpdate.pm has two spaces before "parameter"
    assert _error(result) == (
        "ConfigItemUpdate.MissingParameter",
        "ConfigItemUpdate: Attachment->Content  parameter is missing!",
    )


# --------------------------------------------------------------------------- routes


async def test_rest_routes_create_and_update(cmdb_db: CmdbWorld) -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db, get_session_store
    from tiqora.config import Settings

    await _prepare(cmdb_db)
    factory = cmdb_db.factory

    async def _db() -> Any:
        async with factory() as session:
            yield session

    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_store] = lambda: store
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/znuny-compat/ConfigItem", json={**_auth(), "ConfigItem": _item()}
        )
        assert created.status_code == 200, created.text
        ci_id = created.json()["ConfigItemID"]
        updated = await client.patch(
            f"/znuny-compat/ConfigItem/{ci_id}",
            json={**_auth(), "ConfigItem": _item(CIXMLData={"Model": "M9"})},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["ConfigItemID"] == ci_id
        denied = await client.post(
            "/znuny-compat/ConfigItem", json={**_auth(RO), "ConfigItem": _item()}
        )
        assert denied.status_code == 403
        assert denied.json()["Error"]["ErrorCode"] == "ConfigItemCreate.AccessDenied"
    assert len(_versions(cmdb_db, ci_id)) == 2
