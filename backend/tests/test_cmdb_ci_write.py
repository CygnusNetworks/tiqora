"""CI write path: numbering, versions, locking, history, references, index (both dialects)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RO,
    SERVER_ATTRS,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    _dep,
    _inc,
    make_ci,
    make_class,
    parse_attrs,
)
from tiqora.cmdb import service as service_mod
from tiqora.cmdb.access import load_access
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.numbering import bump_counter_for_number
from tiqora.cmdb.service import (
    CiConflict,
    CiService,
    InvalidValues,
    UnknownAttributes,
    system_access,
)
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbCiHistory,
    TiqoraCmdbCiVersion,
    TiqoraCmdbClass,
    TiqoraItsmLink,
)

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


async def _history_events(s: Any, ci_id: int) -> list[str]:
    rows = await s.execute(
        select(TiqoraCmdbCiHistory.event)
        .where(TiqoraCmdbCiHistory.ci_id == ci_id)
        .order_by(TiqoraCmdbCiHistory.id)
    )
    return [str(r) for r in rows.scalars().all()]


async def _history(s: Any, ci_id: int) -> list[tuple[str, str]]:
    rows = await s.execute(
        select(TiqoraCmdbCiHistory.event, TiqoraCmdbCiHistory.detail)
        .where(TiqoraCmdbCiHistory.ci_id == ci_id)
        .order_by(TiqoraCmdbCiHistory.id)
    )
    return [(str(e), str(d)) for e, d in rows.all()]


async def _index_values(s: Any, ci_id: int, path: str) -> list[str]:
    rows = await s.execute(
        select(TiqoraCmdbCiAttr.value_text)
        .where(TiqoraCmdbCiAttr.ci_id == ci_id, TiqoraCmdbCiAttr.attr_path == path)
        .order_by(TiqoraCmdbCiAttr.position)
    )
    return [str(r) for r in rows.scalars().all()]


async def _index_paths(s: Any, ci_id: int) -> set[str]:
    rows = await s.execute(
        select(TiqoraCmdbCiAttr.attr_path).where(TiqoraCmdbCiAttr.ci_id == ci_id)
    )
    return {str(r) for r in rows.scalars().all()}


async def _count(s: Any, model: Any, **where: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for k, v in where.items():
        stmt = stmt.where(getattr(model, k) == v)
    return int((await s.execute(stmt)).scalar_one())


async def test_create_assigns_number_and_first_version(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        acc = await load_access(s, USER_A)
        rec = await CiService(s).create(
            acc,
            class_id=cid,
            name="cmdb4-web-01",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={"Vendor": [{"v": "Example"}]},
        )
        rec2 = await CiService(s).create(
            acc,
            class_id=cid,
            name="cmdb4-web-02",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={},
        )
        await s.commit()
        assert (rec.number, rec2.number) == ("CMDB4SRV-000001", "CMDB4SRV-000002")
        assert rec.version_count == 1 and rec.cur_incident_state_id == rec.incident_state_id
        assert rec.attributes == {"Vendor": [{"v": "Example"}]}
        assert rec.created_by == USER_A and rec.lock_version == 1
        assert await _history_events(s, rec.id) == ["ConfigItemCreate", "VersionCreate"]
        assert await _index_values(s, rec.id, "Vendor") == ["Example"]


async def test_number_skips_existing_imported_number(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        imported = await make_ci(s, cid, "cmdb4-imported")
        await s.execute(
            text("UPDATE tiqora_cmdb_ci SET number = 'CMDB4SRV-000002' WHERE id = :i"),
            {"i": imported},
        )
        await s.commit()
        acc = await load_access(s, USER_A)
        rec = await CiService(s).create(
            acc,
            class_id=cid,
            name="cmdb4-b",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={},
        )
        await s.commit()
        # counter is at 1 after the first CI; 000002 is taken by the imported number
        assert rec.number == "CMDB4SRV-000003"


async def test_bump_counter_for_number(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        await bump_counter_for_number(s, cid, "CMDB4SRV-000120")
        await bump_counter_for_number(s, cid, "OTHER-000999")  # foreign prefix: ignored
        await bump_counter_for_number(s, cid, "CMDB4SRV-000007")  # lower: never lowers
        await s.commit()
        counter = (
            await s.execute(select(TiqoraCmdbClass.number_counter).where(TiqoraCmdbClass.id == cid))
        ).scalar_one()
        assert counter == 120
        rec = await CiService(s).create(
            system_access(USER_A),
            class_id=cid,
            name="cmdb4-n",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={},
        )
        assert rec.number == "CMDB4SRV-000121"


async def test_unchanged_save_writes_no_version(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(
            s, await make_class(s), "cmdb4-a", attributes={"Vendor": [{"v": "X"}]}
        )
        await s.commit()
        acc = await load_access(s, USER_A)
        rec = await CiService(s).get(acc, ci_id)
        res = await CiService(s).update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            attributes={"Vendor": [{"v": "X"}]},
            name="cmdb4-a",
        )
        await s.commit()
        assert res.changed is False
        assert res.ci.version_count == 1 and res.ci.lock_version == rec.lock_version
        assert await _history_events(s, ci_id) == ["ConfigItemCreate", "VersionCreate"]


async def test_changed_save_writes_version_history_and_index(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(
            s,
            await make_class(s),
            "cmdb4-a",
            attributes={"Vendor": [{"v": "X"}], "CPU": [{"v": 4}]},
        )
        acc = await load_access(s, USER_A)
        rec = await CiService(s).get(acc, ci_id)
        res = await CiService(s).update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            name="cmdb4-b",
            deployment_state_id=await _dep(s, "maintenance"),
            attributes={"Vendor": [{"v": "Y"}], "CPU": [{"v": 4}]},
            change_note="rename",
        )
        await s.commit()
        assert res.changed is True
        assert (res.ci.version_count, res.ci.lock_version) == (2, rec.lock_version + 1)
        assert res.ci.current_version_id != rec.current_version_id
        assert res.ci.name == "cmdb4-b" and res.ci.attributes["Vendor"] == [{"v": "Y"}]
        assert await _index_values(s, ci_id, "Vendor") == ["Y"]
        events = await _history(s, ci_id)
        assert [e for e, _ in events] == [
            "ConfigItemCreate",
            "VersionCreate",
            "VersionCreate",
            "NameUpdate",
            "DeploymentStateUpdate",
            "ValueUpdate",
        ]
        assert events[-1][1] == "Vendor: X → Y"
        assert events[3][1] == "cmdb4-a → cmdb4-b"
        note = (
            await s.execute(
                select(TiqoraCmdbCiVersion.change_note).where(
                    TiqoraCmdbCiVersion.id == res.ci.current_version_id
                )
            )
        ).scalar_one()
        assert note == "rename"


async def test_value_history_is_capped(cmdb_db: CmdbWorld) -> None:
    attrs = [_attr(f"K{i:02d}", "text") for i in range(25)]
    async with cmdb_db.factory() as s:
        cid = await make_class(s, attributes=attrs)
        ci_id = await make_ci(s, cid, "cmdb4-a")
        acc = await load_access(s, USER_A)
        rec = await CiService(s).get(acc, ci_id)
        await CiService(s).update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            attributes={f"K{i:02d}": [{"v": "x"}] for i in range(25)},
        )
        await s.commit()
        values = [d for e, d in await _history(s, ci_id) if e == "ValueUpdate"]
        assert len(values) == 21 and values[-1] == "… (5 weitere)"


async def test_stale_lock_version_is_409_and_writes_nothing(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        acc = await load_access(s, USER_A)
        v = (await CiService(s).get(acc, ci_id)).lock_version
        await CiService(s).update(acc, ci_id, expected_lock_version=v, name="cmdb4-b")
        await s.commit()
        with pytest.raises(CiConflict):
            await CiService(s).update(
                acc,
                ci_id,
                expected_lock_version=v,
                name="cmdb4-c",
                attributes={"Vendor": [{"v": "Y"}]},
            )
        await s.rollback()
        rec = await CiService(s).get(acc, ci_id)
        assert rec.name == "cmdb4-b" and rec.version_count == 2
        assert await _index_values(s, ci_id, "Vendor") == []
        assert (await _history_events(s, ci_id)).count("VersionCreate") == 2


async def test_lock_guard_in_the_update_statement(cmdb_db: CmdbWorld) -> None:
    """A concurrent writer between the read and the write is caught by the UPDATE guard."""
    async with cmdb_db.factory() as s1, cmdb_db.factory() as s2:
        ci_id = await make_ci(s1, await make_class(s1), "cmdb4-a")
        acc = await load_access(s1, USER_A)
        v = (await CiService(s1).get(acc, ci_id)).lock_version
        await s1.commit()
        await CiService(s2).update(acc, ci_id, expected_lock_version=v, name="cmdb4-b")
        await s2.commit()
        # s1 read nothing yet in this transaction; pass the stale number explicitly
        with pytest.raises(CiConflict):
            await CiService(s1).update(acc, ci_id, expected_lock_version=v, name="cmdb4-c")


async def test_save_with_unknown_keys_needs_drop_unknown(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci_id = await make_ci(
            s, cid, "cmdb4-a", attributes={"CPU": [{"v": 4}], "Vendor": [{"v": "X"}]}
        )
        await ClassService(s).add_definition(
            cid,
            [a for a in parse_attrs(SERVER_ATTRS) if a.key != "CPU"],
            comment=None,
            user_id=USER_ADMIN,
        )
        await s.commit()
        acc = await load_access(s, USER_A)
        v = (await CiService(s).get(acc, ci_id)).lock_version
        with pytest.raises(UnknownAttributes) as e:
            await CiService(s).update(
                acc, ci_id, expected_lock_version=v, attributes={"Vendor": [{"v": "Y"}]}
            )
        assert e.value.keys == ["CPU"]
        await s.rollback()
        res = await CiService(s).update(
            acc,
            ci_id,
            expected_lock_version=v,
            attributes={"Vendor": [{"v": "Y"}]},
            drop_unknown=True,
        )
        await s.commit()
        assert "CPU" not in res.ci.attributes and await _index_paths(s, ci_id) == {"Vendor"}


async def _legacy_ci(s: Any) -> tuple[int, int]:
    cid = await make_class(s)
    ci_id = await make_ci(s, cid, "cmdb4-a", attributes={"CPU": [{"v": 4}], "Vendor": [{"v": "X"}]})
    await ClassService(s).add_definition(
        cid,
        [a for a in parse_attrs(SERVER_ATTRS) if a.key != "CPU"],
        comment=None,
        user_id=USER_ADMIN,
    )
    await s.commit()
    return cid, ci_id


async def test_patch_and_state_only_updates_carry_unknown_keys_forward(
    cmdb_db: CmdbWorld,
) -> None:
    async with cmdb_db.factory() as s:
        _, ci_id = await _legacy_ci(s)
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        v = (await svc.get(acc, ci_id)).lock_version
        res = await svc.update(
            acc, ci_id, expected_lock_version=v, attributes_patch={"Model": [{"v": "M1"}]}
        )
        assert res.changed and res.ci.attributes["CPU"] == [{"v": 4}]
        assert res.ci.attributes["Vendor"] == [{"v": "X"}]
        assert res.ci.attributes["Model"] == [{"v": "M1"}]
        res2 = await svc.update(
            acc,
            ci_id,
            expected_lock_version=res.ci.lock_version,
            deployment_state_id=await _dep(s, "maintenance"),
        )
        await s.commit()
        assert res2.changed and res2.ci.attributes["CPU"] == [{"v": 4}]
        assert res2.ci.version_count == 3


async def test_noninteractive_full_save_carries_unknown_keys_forward(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        _, ci_id = await _legacy_ci(s)
        svc = CiService(s)
        sys = system_access(USER_B)
        res = await svc.update(
            sys,
            ci_id,
            expected_lock_version=None,
            attributes={"Vendor": [{"v": "Z"}]},
            enforce_permissions=False,
        )
        await s.commit()
        assert res.ci.attributes["CPU"] == [{"v": 4}] and res.ci.attributes["Vendor"] == [
            {"v": "Z"}
        ]


async def test_patch_with_unknown_key_is_rejected_and_empty_patch_removes_key(
    cmdb_db: CmdbWorld,
) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(
            s, await make_class(s), "cmdb4-a", attributes={"Vendor": [{"v": "X"}]}
        )
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        with pytest.raises(InvalidValues) as e:
            await svc.update(
                acc, ci_id, expected_lock_version=None, attributes_patch={"Nope": [{"v": 1}]}
            )
        assert [i.code for i in e.value.issues] == ["unknown_key"]
        res = await svc.update(
            acc, ci_id, expected_lock_version=None, attributes_patch={"Vendor": []}
        )
        assert res.changed and "Vendor" not in res.ci.attributes
        with pytest.raises(ValueError):
            await svc.update(
                acc, ci_id, expected_lock_version=None, attributes={}, attributes_patch={}
            )


def _attr(key: str, type_: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": key,
        "name": key,
        "type": type_,
        "searchable": False,
        "count_max": 1,
        "count_default": 1,
    }
    base.update(kw)
    return base


async def test_invalid_values_and_references_are_rejected(cmdb_db: CmdbWorld) -> None:
    attrs = [
        _attr("Location", "ci_ref", options={"class_keys": ["cmdb4loc"]}),
        _attr("Owner", "customer_user"),
        _attr("Cost", "money", options={"currency": "EUR"}),
        _attr("Agent", "agent"),
    ]
    async with cmdb_db.factory() as s:
        other = await make_class(s, key="cmdb4other")
        loc = await make_class(s, key="cmdb4loc")
        cid = await make_class(s, attributes=attrs)
        other_ci = await make_ci(s, other, "cmdb4-other")
        loc_ci = await make_ci(s, loc, "cmdb4-loc")
        acc = await load_access(s, USER_A)
        with pytest.raises(InvalidValues) as e:
            await CiService(s).create(
                acc,
                class_id=cid,
                name="cmdb4-a",
                deployment_state_id=await _dep(s, "production"),
                incident_state_id=await _inc(s, "operational"),
                attributes={
                    "Location": [{"v": other_ci}],
                    "Owner": [{"v": "nobody@example.org"}],
                    "Cost": [{"v": "abc"}],
                    "Agent": [{"v": 2_000_000_000}],
                },
            )
        # value format errors stop before the reference lookups
        assert {i.path: i.code for i in e.value.issues} == {"Cost[1]": "bad_type"}
        with pytest.raises(InvalidValues) as e2:
            await CiService(s).create(
                acc,
                class_id=cid,
                name="cmdb4-a",
                deployment_state_id=await _dep(s, "production"),
                incident_state_id=await _inc(s, "operational"),
                attributes={
                    "Location": [{"v": other_ci}],
                    "Owner": [{"v": "nobody@example.org"}],
                    "Agent": [{"v": 2_000_000_000}],
                },
            )
        by_path = {i.path: i.code for i in e2.value.issues}
        assert by_path == {
            "Location[1]": "wrong_class",
            "Owner[1]": "unknown_reference",
            "Agent[1]": "unknown_reference",
        }
        # nothing was written for the rejected creates
        assert await _count(s, TiqoraCmdbCi, class_id=cid) == 0
        ok = await CiService(s).create(
            acc,
            class_id=cid,
            name="cmdb4-a",
            deployment_state_id=await _dep(s, "production"),
            incident_state_id=await _inc(s, "operational"),
            attributes={"Location": [{"v": loc_ci}]},
        )
        assert ok.attributes == {"Location": [{"v": loc_ci}]}


async def test_valid_references_are_accepted(cmdb_db: CmdbWorld) -> None:
    attrs = [
        _attr("Location", "ci_ref", options={"class_keys": ["cmdb4other"]}),
        _attr("Owner", "customer_user"),
        _attr("Agent", "agent"),
        _attr("Cat", "catalog", options={"catalog_class": "ITSM::ConfigItem::YesNo"}),
    ]
    async with cmdb_db.factory() as s:
        other = await make_class(s, key="cmdb4other")
        cid = await make_class(s, attributes=attrs)
        other_ci = await make_ci(s, other, "cmdb4-other")
        dep = await _dep(s, "production")
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        rec = await svc.create(
            acc,
            class_id=cid,
            name="cmdb4-a",
            deployment_state_id=dep,
            incident_state_id=await _inc(s, "operational"),
            attributes={
                "Location": [{"v": other_ci}],
                "Owner": [{"v": _cmdb_helpers.CUSTOMER_A}],
                "Agent": [{"v": USER_A}],
            },
        )
        assert rec.attributes["Location"] == [{"v": other_ci}]
        # a catalog item of another catalog class is rejected
        with pytest.raises(InvalidValues) as e:
            await svc.update(
                acc, rec.id, expected_lock_version=None, attributes_patch={"Cat": [{"v": dep}]}
            )
        assert [(i.path, i.code) for i in e.value.issues] == [("Cat[1]", "wrong_class")]


async def test_vanished_reference_does_not_block_unrelated_edits(cmdb_db: CmdbWorld) -> None:
    attrs = [_attr("Location", "ci_ref"), _attr("Note", "text")]
    async with cmdb_db.factory() as s:
        cid = await make_class(s, attributes=attrs)
        gone = await make_ci(s, cid, "cmdb4-gone")
        ci_id = await make_ci(s, cid, "cmdb4-a", attributes={"Location": [{"v": gone}]})
        await CiService(s).delete(system_access(USER_ADMIN), gone, enforce_permissions=False)
        res = await CiService(s).update(
            system_access(USER_ADMIN),
            ci_id,
            expected_lock_version=None,
            attributes={"Location": [{"v": gone}], "Note": [{"v": "x"}]},
        )
        assert res.changed is True


async def test_ro_user_cannot_create_and_unreadable_ci_is_not_found(
    cmdb_db: CmdbWorld,
) -> None:
    async with cmdb_db.factory() as s:
        rw_class = await make_class(s)
        ro_class = await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        rw_ci = await make_ci(s, rw_class, "cmdb4-rw")
        ro_ci = await make_ci(s, ro_class, "cmdb4-ro")
        acc = await load_access(s, USER_B)
        svc = CiService(s)
        dep, inc = await _dep(s, "production"), await _inc(s, "operational")
        with pytest.raises(CmdbNotFound):
            await svc.create(
                acc,
                class_id=rw_class,
                name="x",
                deployment_state_id=dep,
                incident_state_id=inc,
                attributes={},
            )
        with pytest.raises(CmdbForbidden):
            await svc.create(
                acc,
                class_id=ro_class,
                name="x",
                deployment_state_id=dep,
                incident_state_id=inc,
                attributes={},
            )
        with pytest.raises(CmdbNotFound):
            await svc.get(acc, rw_ci)
        assert (await svc.get(acc, ro_ci)).name == "cmdb4-ro"
        with pytest.raises(CmdbForbidden):
            await svc.update(acc, ro_ci, expected_lock_version=None, name="y")
        with pytest.raises(CmdbForbidden):
            await svc.delete(acc, ro_ci)
        with pytest.raises(CmdbNotFound):
            await svc.delete(acc, rw_ci)
        with pytest.raises(CmdbNotFound):
            await svc.get(acc, 999999999)
        by_number = await svc.get_by_number(acc, (await svc.get(acc, ro_ci)).number)
        assert by_number.id == ro_ci
        with pytest.raises(CmdbNotFound):
            await svc.get_by_number(acc, "NOPE-1")


async def test_bad_name_and_states_are_rejected(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        dep, inc = await _dep(s, "production"), await _inc(s, "operational")
        for kwargs, field in (
            ({"name": "  "}, "name"),
            ({"name": "x" * 251}, "name"),
            ({"name": "x", "deployment_state_id": inc}, "deployment_state_id"),
            ({"name": "x", "incident_state_id": 999999}, "incident_state_id"),
        ):
            args: dict[str, Any] = {
                "class_id": cid,
                "deployment_state_id": dep,
                "incident_state_id": inc,
                "attributes": {},
            }
            args.update(kwargs)
            with pytest.raises(InvalidValues) as e:
                await svc.create(acc, **args)
            assert e.value.issues[0].path == field


async def test_delete_removes_everything(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci_id = await make_ci(s, cid, "cmdb4-a", attributes={"Vendor": [{"v": "X"}]})
        keep = await make_ci(s, cid, "cmdb4-keep", attributes={"Vendor": [{"v": "K"}]})
        await s.execute(
            text(
                "INSERT INTO tiqora_cmdb_ci_version_archive (id, ci_id, definition_id, name,"
                " deployment_state_id, incident_state_id, attributes, created_by, created_at,"
                " archived_at) SELECT id + 100000, ci_id, definition_id, name,"
                " deployment_state_id, incident_state_id, attributes, created_by, created_at,"
                " created_at FROM tiqora_cmdb_ci_version WHERE ci_id = :i"
            ),
            {"i": ci_id},
        )
        for src, tgt in ((ci_id, keep), (keep, ci_id)):
            await s.execute(
                text(
                    "INSERT INTO tiqora_itsm_link (source_kind, source_key, target_kind,"
                    " target_key, link_type, created_by, created_at)"
                    " VALUES ('ci', :s, 'ci', :t, 'DependsOn', 1, :now)"
                ),
                {"s": str(src), "t": str(tgt), "now": datetime(2026, 10, 1)},
            )
        await s.commit()
        assert await _count(s, TiqoraItsmLink) == 2
        await CiService(s).delete(await load_access(s, USER_A), ci_id)
        await s.commit()
        assert await _count(s, TiqoraCmdbCi, id=ci_id) == 0
        assert await _count(s, TiqoraCmdbCiVersion, ci_id=ci_id) == 0
        assert await _count(s, TiqoraCmdbCiAttr, ci_id=ci_id) == 0
        assert await _count(s, TiqoraCmdbCiHistory, ci_id=ci_id) == 0
        assert await _count(s, TiqoraItsmLink) == 0
        archived = (
            await s.execute(text("SELECT count(*) FROM tiqora_cmdb_ci_version_archive"))
        ).scalar_one()
        assert archived == 0
        # the other CI is untouched
        assert await _count(s, TiqoraCmdbCiVersion, ci_id=keep) == 1
        assert await _index_values(s, keep, "Vendor") == ["K"]


async def test_enforce_permissions_false_for_system_access(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        res = await CiService(s).update(
            system_access(USER_B),
            ci_id,
            expected_lock_version=None,
            name="cmdb4-b",
            enforce_permissions=False,
        )
        await s.commit()
        assert res.changed and res.ci.changed_by == USER_B
        created_by = (
            await s.execute(
                select(TiqoraCmdbCiVersion.created_by).where(
                    TiqoraCmdbCiVersion.id == res.ci.current_version_id
                )
            )
        ).scalar_one()
        assert created_by == USER_B
        # the same call with permissions enforced is refused (USER_B cannot even read GROUP_RW)
        with pytest.raises(CmdbNotFound):
            await CiService(s).update(
                await load_access(s, USER_B), ci_id, expected_lock_version=None, name="z"
            )


async def test_recompute_hook_is_called_for_incident_changes_and_delete_neighbours(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[int]] = []

    async def hook(_session: Any, ids: list[int]) -> None:
        calls.append(list(ids))

    monkeypatch.setattr(service_mod, "_recompute_hook", hook)
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")  # operational: no call
        b = await make_ci(s, cid, "cmdb4-b", incident="incident")  # incident: call
        assert calls == [[b]]
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        await svc.update(acc, a, expected_lock_version=None, name="cmdb4-a2")  # name only
        assert calls == [[b]]
        await svc.update(
            acc, a, expected_lock_version=None, incident_state_id=await _inc(s, "warning")
        )
        assert calls == [[b], [a]]
        await svc.update(
            acc,
            a,
            expected_lock_version=None,
            incident_state_id=await _inc(s, "incident"),
            recompute=False,
        )
        assert calls == [[b], [a]]
        await s.execute(
            text(
                "INSERT INTO tiqora_itsm_link (source_kind, source_key, target_kind, target_key,"
                " link_type, created_by, created_at)"
                " VALUES ('ci', :s, 'ci', :t, 'DependsOn', 1, :now)"
            ),
            {"s": str(a), "t": str(b), "now": datetime(2026, 10, 1)},
        )
        await svc.delete(acc, a)
        assert calls[-1] == [b]
        await s.commit()


async def test_create_in_invalid_class_is_rejected(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        await s.execute(text("UPDATE tiqora_cmdb_class SET valid = :v"), {"v": False})
        await s.commit()
        with pytest.raises(InvalidValues) as e:
            await CiService(s).create(
                await load_access(s, USER_A),
                class_id=cid,
                name="x",
                deployment_state_id=await _dep(s, "production"),
                incident_state_id=await _inc(s, "operational"),
                attributes={},
            )
        assert e.value.issues[0].code == "class_invalid"

