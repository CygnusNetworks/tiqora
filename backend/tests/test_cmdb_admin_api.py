"""Admin API (catalog, classes, definitions, templates, seeding) and the agent class API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    GROUP_RW,
    SERVER_ATTRS,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    client_for,
    make_class,
)
from tiqora.db.tiqora.models import TiqoraCmdbCatalog

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

FIXTURES = Path(__file__).parent / "fixtures" / "cmdb"
ADMIN = "/api/v1/admin/cmdb"
AGENT = "/api/v1/cmdb"


def _class_body(key: str = "cmdb4srv", group_id: int = GROUP_RW, **extra: Any) -> dict[str, Any]:
    return {"class_key": key, "name": "Server", "group_id": group_id, **extra}


async def test_admin_creates_class_and_definition(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(f"{ADMIN}/classes", json=_class_body())
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        assert r.json()["number_prefix"] == "CMDB4SRV-"
        assert r.json()["group_name"] == "cmdb4-rw"
        r = await c.post(
            f"{ADMIN}/classes/{cid}/definitions", json={"attributes": SERVER_ATTRS, "comment": "v1"}
        )
        assert r.status_code == 201, r.text
        assert r.json()["version"] == 1
        assert r.json()["attributes"][0]["key"] == "Vendor"
        got = (await c.get(f"{ADMIN}/classes/{cid}")).json()
        assert got["current_definition_version"] == 1
        assert got["definition"]["comment"] == "v1"
        listing = (await c.get(f"{ADMIN}/classes")).json()
        assert [x["class_key"] for x in listing] == ["cmdb4srv"]
        assert listing[0]["definition"] is None


async def test_non_admin_gets_403(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_A) as c:
        assert (await c.get(f"{ADMIN}/classes")).status_code == 403
        assert (await c.post(f"{ADMIN}/seed-defaults")).status_code == 403
        assert (await c.get(f"{ADMIN}/status")).status_code == 403


async def test_class_key_taken_unknown_group_and_immutable_key(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        first = await c.post(f"{ADMIN}/classes", json=_class_body())
        assert first.status_code == 201
        dup = await c.post(f"{ADMIN}/classes", json=_class_body())
        assert dup.status_code == 409 and dup.json()["detail"]["code"] == "class_key_taken"
        bad_group = await c.post(f"{ADMIN}/classes", json=_class_body("cmdb4other", 999999999))
        assert bad_group.status_code == 422
        assert bad_group.json()["detail"]["code"] == "unknown_group"
        bad_key = await c.post(f"{ADMIN}/classes", json=_class_body("Bad Key"))
        assert bad_key.status_code == 422
        cid = first.json()["id"]
        renamed = await c.put(
            f"{ADMIN}/classes/{cid}",
            json={**_class_body("cmdb4renamed"), "expected_lock_version": 1},
        )
        assert renamed.status_code == 422
        assert renamed.json()["detail"]["code"] == "invalid_class_key"


async def test_class_update_and_stale_lock_version(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        cid = (await c.post(f"{ADMIN}/classes", json=_class_body())).json()["id"]
        ok = await c.put(
            f"{ADMIN}/classes/{cid}",
            json={**_class_body(name="Renamed"), "expected_lock_version": 1, "valid": False},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["name"] == "Renamed" and ok.json()["lock_version"] == 2
        assert ok.json()["valid"] is False
        stale = await c.put(
            f"{ADMIN}/classes/{cid}", json={**_class_body(), "expected_lock_version": 1}
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            "code": "conflict",
            "message": "Inzwischen geändert. Bitte neu laden.",
        }
        assert (await c.get(f"{ADMIN}/classes/987654321")).status_code == 404


async def test_invalid_definition_is_422_with_issues(cmdb_db: CmdbWorld) -> None:
    bad = [{**SERVER_ATTRS[0], "key": "1bad"}]
    async with client_for(cmdb_db, USER_ADMIN) as c:
        cid = (await c.post(f"{ADMIN}/classes", json=_class_body())).json()["id"]
        r = await c.post(f"{ADMIN}/classes/{cid}/definitions", json={"attributes": bad})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "invalid_definition"
        assert detail["issues"][0]["code"] == "bad_key"
        assert detail["issues"][0]["path"] == "1bad"
        assert (await c.get(f"{ADMIN}/classes/{cid}/definitions")).json() == []
        v = await c.post(f"{ADMIN}/definitions/validate", json={"attributes": bad})
        assert v.status_code == 200 and v.json()[0]["code"] == "bad_key"
        ok = await c.post(f"{ADMIN}/definitions/validate", json={"attributes": SERVER_ATTRS})
        assert ok.json() == []
        unknown = await c.post(
            f"{ADMIN}/definitions/validate", json={"class_id": 987654321, "attributes": []}
        )
        assert unknown.status_code == 404


async def test_definition_import_perl_preview(cmdb_db: CmdbWorld) -> None:
    text = FIXTURES.joinpath("znuny65_computer_definition.pl").read_text()
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(f"{ADMIN}/definition-import", json={"format": "znuny_perl", "text": text})
        body = r.json()
        assert r.status_code == 200, r.text
        assert body["attributes"][0]["key"] == "Vendor"
        assert "ITSM::ConfigItem::Computer::Type" in body["missing_catalog_classes"]
        # a preview writes nothing
        assert (await c.get(f"{ADMIN}/classes")).json() == []


async def test_definition_import_yaml_and_json(cmdb_db: CmdbWorld) -> None:
    yml = FIXTURES.joinpath("znuny73_computer_definition.yml").read_text()
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(f"{ADMIN}/definition-import", json={"format": "znuny_yaml", "text": yml})
        assert r.status_code == 200, r.text
        assert len(r.json()["attributes"]) >= 1
        import json as _json

        j = await c.post(
            f"{ADMIN}/definition-import",
            json={"format": "tiqora_json", "text": _json.dumps(SERVER_ATTRS)},
        )
        assert j.status_code == 200
        assert [a["key"] for a in j.json()["attributes"]][:2] == ["Vendor", "Model"]
        assert j.json()["missing_catalog_classes"] == []
        not_list = await c.post(
            f"{ADMIN}/definition-import", json={"format": "tiqora_json", "text": "{}"}
        )
        assert not_list.status_code == 422 and not_list.json()["detail"]["code"] == "parse_error"


async def test_definition_import_rejects_perl_code(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(
            f"{ADMIN}/definition-import",
            json={"format": "znuny_perl", "text": "[ { Key => system('id') } ]"},
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "parse_error"
    assert isinstance(detail["position"], int)


async def test_definition_diff_route(cmdb_db: CmdbWorld) -> None:
    v2 = [*SERVER_ATTRS, {**SERVER_ATTRS[0], "key": "Rack", "name": "Rack"}]
    async with client_for(cmdb_db, USER_ADMIN) as c:
        cid = (await c.post(f"{ADMIN}/classes", json=_class_body())).json()["id"]
        for attrs in (SERVER_ATTRS, v2):
            r = await c.post(f"{ADMIN}/classes/{cid}/definitions", json={"attributes": attrs})
            assert r.status_code == 201, r.text
        r = await c.get(
            f"{ADMIN}/classes/{cid}/definitions/diff", params={"from_version": 1, "to_version": 2}
        )
        assert r.status_code == 200, r.text
        assert r.json() == [{"path": "Rack", "kind": "added", "detail": "text"}]
        missing = await c.get(
            f"{ADMIN}/classes/{cid}/definitions/diff", params={"from_version": 1, "to_version": 9}
        )
        assert missing.status_code == 404
        assert len((await c.get(f"{ADMIN}/classes/{cid}/definitions")).json()) == 2


async def test_apply_template_route(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        templates = (await c.get(f"{ADMIN}/class-templates")).json()
        keys = [t["key"] for t in templates]
        assert "computer" in keys
        assert all(t["attribute_count"] > 0 for t in templates)
        r = await c.post(f"{ADMIN}/class-templates/computer/apply", json={"group_id": GROUP_RW})
        assert r.status_code == 201, r.text
        assert r.json()["class_key"] == "computer"
        assert r.json()["current_definition_version"] == 1
        again = await c.post(f"{ADMIN}/class-templates/computer/apply", json={"group_id": GROUP_RW})
        assert again.status_code == 409
        none = await c.post(f"{ADMIN}/class-templates/nope/apply", json={"group_id": GROUP_RW})
        assert none.status_code == 404


async def test_catalog_admin_crud(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        classes = (await c.get(f"{ADMIN}/catalog-classes")).json()
        assert "ITSM::ConfigItem::DeploymentState" in classes
        body = {
            "catalog_class": "cmdb4::Color",
            "item_key": "red",
            "name": "Red",
            "functionality": None,
            "position": 10,
        }
        r = await c.post(f"{ADMIN}/catalog", json=body)
        assert r.status_code == 201, r.text
        item_id = r.json()["id"]
        dup = await c.post(f"{ADMIN}/catalog", json=body)
        assert dup.status_code == 409 and dup.json()["detail"]["code"] == "catalog_item_exists"
        upd = await c.put(
            f"{ADMIN}/catalog/{item_id}", json={**body, "name": "Rot", "valid": False}
        )
        assert upd.status_code == 200 and upd.json()["name"] == "Rot"
        assert upd.json()["valid"] is False
        immutable = await c.put(f"{ADMIN}/catalog/{item_id}", json={**body, "item_key": "blue"})
        assert immutable.status_code == 422
        assert immutable.json()["detail"]["code"] == "immutable_key"
        admin_items = (
            await c.get(f"{ADMIN}/catalog", params={"catalog_class": "cmdb4::Color"})
        ).json()
        assert [i["item_key"] for i in admin_items] == ["red"]
        agent_items = (
            await c.get(f"{AGENT}/catalog", params={"catalog_class": "cmdb4::Color"})
        ).json()
        assert agent_items == []  # invalid items are not offered to agents
        assert (await c.put(f"{ADMIN}/catalog/987654321", json=body)).status_code == 404
    async with cmdb_db.factory() as s:
        rows = (
            (
                await s.execute(
                    select(TiqoraCmdbCatalog.item_key).where(
                        TiqoraCmdbCatalog.catalog_class == "cmdb4::Color"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert list(rows) == ["red"]


async def test_seed_defaults_is_idempotent_and_reports_views(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_ADMIN) as c:
        before = (await c.get(f"{ADMIN}/status")).json()
        assert before["catalog_seeded"] is True and before["mode"] == "tiqora"
        r = await c.post(f"{ADMIN}/seed-defaults")
        assert r.status_code == 200, r.text
        assert r.json()["catalog_seeded"] is True
        assert r.json()["views_present"] is True
        again = await c.post(f"{ADMIN}/seed-defaults")
        assert again.json() == r.json()


async def test_seed_defaults_without_view_privilege_says_so(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tiqora.api.v1.admin.cmdb.create_cmdb_views", lambda conn: False)
    async with client_for(cmdb_db, USER_ADMIN) as c:
        r = await c.post(f"{ADMIN}/seed-defaults")
    assert r.status_code == 200, r.text
    assert r.json()["views_present"] is False
    assert r.json()["catalog_seeded"] is True


async def test_admin_status_counts(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        await make_class(s, key="cmdb4srv")
    async with client_for(cmdb_db, USER_ADMIN) as c:
        body = (await c.get(f"{ADMIN}/status")).json()
    assert body["class_count"] == 1
    assert body["ci_count"] == 0 and body["archived_version_count"] == 0


async def test_agent_sees_only_readable_classes(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        await make_class(s, key="cmdb4rw", group_id=GROUP_RW)
        await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.get(f"{AGENT}/classes")
        by_key = {x["class_key"]: x for x in r.json()}
        status_body = (await c.get(f"{AGENT}/status")).json()
    assert set(by_key) == {"cmdb4rw", "cmdb4ro"}
    assert by_key["cmdb4rw"]["can_write"] is True and by_key["cmdb4ro"]["can_write"] is False
    assert by_key["cmdb4rw"]["definition"]["version"] == 1
    assert by_key["cmdb4rw"]["group_name"] == "cmdb4-rw"
    assert status_body["readable_class_count"] == 2
    assert status_body["mode"] == "tiqora" and status_body["can_admin"] is False
    assert status_body["views_present"] is True
    async with client_for(cmdb_db, USER_B) as c:
        keys = [x["class_key"] for x in (await c.get(f"{AGENT}/classes")).json()]
    assert keys == ["cmdb4ro"]


async def test_agent_definition_by_version(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        rw = await make_class(s, key="cmdb4rw", group_id=GROUP_RW)
        hidden = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
    async with client_for(cmdb_db, USER_A) as c:
        ok = await c.get(f"{AGENT}/classes/{rw}/definitions/1")
        assert ok.status_code == 200 and ok.json()["version"] == 1
        assert ok.json()["attributes"][0]["key"] == "Vendor"
        assert (await c.get(f"{AGENT}/classes/{rw}/definitions/2")).status_code == 404
        # an unreadable class answers exactly like an unknown one
        denied = await c.get(f"{AGENT}/classes/{hidden}/definitions/1")
        unknown = await c.get(f"{AGENT}/classes/987654321/definitions/1")
        assert denied.status_code == unknown.status_code == 404
        assert denied.json() == unknown.json() == {"detail": "Nicht gefunden"}


async def test_agent_catalog_lists_valid_items(cmdb_db: CmdbWorld) -> None:
    async with client_for(cmdb_db, USER_B) as c:
        r = await c.get(f"{AGENT}/catalog", params={"catalog_class": "ITSM::Core::IncidentState"})
        assert r.status_code == 200
        keys = [i["item_key"] for i in r.json()]
        assert keys == ["operational", "warning", "incident"]
        assert (await c.get(f"{AGENT}/catalog")).status_code == 422


async def test_incident_state_changes_recompute_and_keep_a_warning_state(
    cmdb_db: CmdbWorld,
) -> None:
    from tiqora.db.tiqora.models import TiqoraJob

    def jobs() -> int:
        sql = "SELECT COUNT(*) FROM tiqora_job WHERE kind = 'core.cmdb_propagation'"
        return int(cmdb_db.sql(sql)[0][0])

    async with client_for(cmdb_db, USER_ADMIN) as c:
        items = (
            await c.get(f"{ADMIN}/catalog", params={"catalog_class": "ITSM::Core::IncidentState"})
        ).json()
        by_key = {i["item_key"]: i for i in items}
        warning, incident = by_key["warning"], by_key["incident"]

        def body(item: dict[str, Any], **over: Any) -> dict[str, Any]:
            keys = ("catalog_class", "item_key", "name", "functionality", "position", "valid")
            return {**{k: item[k] for k in keys}, **over}

        try:
            # the last valid warning state can be neither invalidated nor repurposed
            for over in ({"valid": False}, {"functionality": "incident"}):
                r = await c.put(f"{ADMIN}/catalog/{warning['id']}", json=body(warning, **over))
                assert r.status_code == 409, r.text
                assert r.json()["detail"]["code"] == "last_warning_state"
            # a rename changes no derived state: no recompute
            r = await c.put(
                f"{ADMIN}/catalog/{incident['id']}", json=body(incident, name="Störung")
            )
            assert r.status_code == 200 and jobs() == 0
            # a functionality change does: the full recompute is queued
            r = await c.put(
                f"{ADMIN}/catalog/{incident['id']}",
                json=body(incident, functionality="warning"),
            )
            assert r.status_code == 200 and jobs() == 1
            # with a second warning state the first may go
            r = await c.put(f"{ADMIN}/catalog/{warning['id']}", json=body(warning, valid=False))
            assert r.status_code == 200, r.text
        finally:
            for item in (warning, incident):
                await c.put(f"{ADMIN}/catalog/{item['id']}", json=body(item))
    async with cmdb_db.factory() as s:
        queued = (
            await s.execute(select(TiqoraJob).where(TiqoraJob.kind == "core.cmdb_propagation"))
        ).scalars()
        assert {j.dedupe_key for j in queued} == {"all"}
