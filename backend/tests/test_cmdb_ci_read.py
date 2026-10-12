"""CI read path: versions, rendered values, diff, history (both dialects, band 742000-742999)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    SERVER_ATTRS,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    _attr,
    _dep,
    _inc,
    make_ci,
    make_class,
    parse_attrs,
)
from tiqora.cmdb.access import load_access
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.diff import diff_versions
from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.render import NO_ACCESS, RenderedAttr, flatten, render_values
from tiqora.cmdb.schemas import RenderedAttrOut
from tiqora.cmdb.seed import DEPLOYMENT_STATE
from tiqora.cmdb.service import CiService, system_access
from tiqora.db.tiqora.models import TiqoraCmdbCatalog

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

CUSTOMER_A = "cmdb4-cust-a@example.org"


async def _render_current(s: Any, user_id: int, ci_id: int) -> list[RenderedAttr]:
    acc = await load_access(s, user_id)
    rec = await CiService(s).get(acc, ci_id)
    assert rec.current_version_id is not None
    v = await CiService(s).get_version(acc, ci_id, rec.current_version_id)
    return await render_values(s, v.definition_attrs, v.attributes, access=acc)


async def _bump(s: Any, ci_id: int, n: int) -> list[int]:
    """``n`` further versions (one name change each); returns every version id, oldest first."""
    svc = CiService(s)
    acc = system_access(USER_A)
    rec = await svc.get(acc, ci_id)
    ids = [int(rec.current_version_id or 0)]
    for i in range(n):
        rec = await svc.get(acc, ci_id)
        res = await svc.update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            name=f"{rec.name}-{i}",
            enforce_permissions=False,
        )
        ids.append(int(res.ci.current_version_id or 0))
    await s.commit()
    return ids


async def test_old_version_renders_with_its_definition(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci_id = await make_ci(s, cid, "cmdb4-a", attributes={"CPU": [{"v": 4}]})
        first = (await CiService(s).get(system_access(USER_A), ci_id)).current_version_id
        assert first is not None
        await ClassService(s).add_definition(
            cid,
            [a for a in parse_attrs(SERVER_ATTRS) if a.key != "CPU"],
            comment=None,
            user_id=USER_ADMIN,
        )
        await s.commit()
        acc = await load_access(s, USER_A)
        v = await CiService(s).get_version(acc, ci_id, first)
        assert v.definition_version == 1
        rendered = await render_values(s, v.definition_attrs, v.attributes, access=acc)
        assert [(r.label, r.display) for r in rendered] == [("CPU cores", "4")]


async def test_render_resolves_references_without_leaking_hidden_ci(
    cmdb_db: CmdbWorld,
) -> None:
    async with cmdb_db.factory() as s:
        hid_cls = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        hidden = await make_ci(s, hid_cls, "cmdb4-secret", user_id=USER_ADMIN)
        visible = await make_ci(s, await make_class(s), "cmdb4-visible")
        ci_id = await make_ci(
            s,
            await make_class(s, key="cmdb4main"),
            "cmdb4-a",
            attributes={"Location": [{"v": hidden}], "Owner": [{"v": CUSTOMER_A}]},
            user_id=USER_ADMIN,
        )
        other = await make_ci(
            s,
            await make_class(s, key="cmdb4main2"),
            "cmdb4-b",
            attributes={"Location": [{"v": visible}]},
        )
        out = {r.key: r for r in await _render_current(s, USER_A, ci_id)}
        assert out["Location"].display == NO_ACCESS
        assert out["Location"].ref is None
        assert "cmdb4-secret" not in (out["Location"].display or "")
        assert out["Owner"].display == f"Cora Customer <{CUSTOMER_A}>"
        assert out["Owner"].ref is not None
        assert (out["Owner"].ref.kind, out["Owner"].ref.key) == ("customer_user", CUSTOMER_A)
        # a readable CI: "number – name" and a link
        loc = {r.key: r for r in await _render_current(s, USER_A, other)}["Location"]
        assert loc.display is not None and loc.display.endswith(" – cmdb4-visible")
        assert loc.ref is not None and (loc.ref.kind, loc.ref.key) == ("ci", str(visible))
        # the admin sees the hidden one
        adm = {r.key: r for r in await _render_current(s, USER_ADMIN, ci_id)}
        assert adm["Location"].display is not None
        assert adm["Location"].display.endswith(" – cmdb4-secret")


async def test_render_types_nesting_and_positions(cmdb_db: CmdbWorld) -> None:
    attrs = [
        _attr("Dep", "Deployment", "catalog", options={"catalog_class": DEPLOYMENT_STATE}),
        _attr("Admin", "Admin", "agent"),
        _attr("Price", "Price", "money", options={"currency": "EUR"}),
        _attr("Seen", "Seen", "datetime"),
        _attr(
            "NIC",
            "NIC",
            "text",
            count_max=3,
            sub=[_attr("IP", "IP", "ip", count_max=4)],
        ),
        _attr("Spec", "Spec", "group", sub=[_attr("Disk", "Disk", "text")]),
    ]
    async with cmdb_db.factory() as s:
        dep = await _dep(s, "production")
        dep_name = (
            await s.execute(select(TiqoraCmdbCatalog.name).where(TiqoraCmdbCatalog.id == dep))
        ).scalar_one()
        values = {
            "Dep": [{"v": dep}],
            "Admin": [{"v": USER_A}],
            "Price": [{"v": "12.5"}],
            "Seen": [{"v": "2026-10-01T08:00:00"}],
            "NIC": [
                {"v": "eth0", "s": {"IP": [{"v": "192.0.2.1"}, {"v": "192.0.2.2"}]}},
                {"v": "eth1"},
            ],
            "Spec": [{"v": None, "s": {"Disk": [{"v": "500G"}]}}],
        }
        rendered = await render_values(s, parse_attrs(attrs), values, access=None)
        top = {r.path: r for r in rendered}
        assert top["Dep[1]"].display == dep_name
        assert top["Admin[1]"].display == "Anna Alpha"
        assert top["Admin[1]"].ref is not None and top["Admin[1]"].ref.kind == "agent"
        assert top["Price[1]"].display == "12.5 EUR"
        assert top["Seen[1]"].display == "2026-10-01T08:00:00Z"
        assert [r.path for r in rendered] == [
            "Dep[1]",
            "Admin[1]",
            "Price[1]",
            "Seen[1]",
            "NIC[1]",
            "NIC[2]",
            "Spec[1]",
        ]
        assert [c.path for c in top["NIC[1]"].children] == ["NIC[1].IP[1]", "NIC[1].IP[2]"]
        # a group entry renders its children only
        assert top["Spec[1]"].display is None
        assert [(c.label, c.display) for c in top["Spec[1]"].children] == [("Disk", "500G")]
        assert [n.path for n in flatten(rendered)][-1] == "Spec[1].Disk[1]"
        # the DTO reads straight from the dataclass tree
        out = RenderedAttrOut.model_validate(top["NIC[1]"])
        assert out.children[1].display == "192.0.2.2"


async def test_render_links_only_http_urls(cmdb_db: CmdbWorld) -> None:
    attrs = parse_attrs([_attr("Web", "Web", "url", count_max=3)])
    async with cmdb_db.factory() as s:
        values = {
            "Web": [
                {"v": "https://example.org/x"},
                {"v": "javascript:alert(1)"},
                {"v": "data:text/html,x"},
            ]
        }
        rendered = await render_values(s, attrs, values, access=None)
        assert [r.display for r in rendered] == [
            "https://example.org/x",
            "javascript:alert(1)",
            "data:text/html,x",
        ]
        assert rendered[0].ref is not None
        assert (rendered[0].ref.kind, rendered[0].ref.key) == ("url", "https://example.org/x")
        assert rendered[1].ref is None
        assert rendered[2].ref is None


async def test_render_portal_only_filters(cmdb_db: CmdbWorld) -> None:
    attrs = parse_attrs(
        [
            _attr("Name", "Name", "text", portal=True),
            _attr("Secret", "Secret", "text"),
            _attr("Admin", "Admin", "agent", portal=True),
            _attr("Q", "Queue", "queue", portal=True),
            _attr("Peer", "Peer", "ci_ref", portal=True, count_max=2),
            _attr(
                "Grp",
                "Group",
                "group",
                portal=True,
                sub=[
                    _attr("Pub", "Pub", "text", portal=True),
                    _attr("Priv", "Priv", "text"),
                ],
            ),
        ]
    )
    async with cmdb_db.factory() as s:
        shown_cls = await make_class(s, key="cmdb4portal", portal_visible=True)
        hidden_cls = await make_class(s, key="cmdb4noportal")
        shown = await make_ci(s, shown_cls, "cmdb4-shown")
        hidden = await make_ci(s, hidden_cls, "cmdb4-internal")
        values = {
            "Name": [{"v": "n"}],
            "Secret": [{"v": "s"}],
            "Admin": [{"v": USER_A}],
            "Q": [{"v": 1}],
            "Peer": [{"v": shown}, {"v": hidden}],
            "Grp": [{"v": None, "s": {"Pub": [{"v": "p"}], "Priv": [{"v": "x"}]}}],
        }
        rendered = await render_values(s, attrs, values, access=None, portal_only=True)
        assert [r.key for r in rendered] == ["Name", "Peer", "Grp"]
        peer = next(r for r in rendered if r.key == "Peer")
        assert peer.display is not None and peer.display.endswith(" – cmdb4-shown")
        grp = next(r for r in rendered if r.key == "Grp")
        assert [(c.key, c.display) for c in grp.children] == [("Pub", "p")]
        assert all(r.portal for r in rendered)


async def test_versions_keyset_paging(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        ids = await _bump(s, ci_id, 6)
        assert len(ids) == 7 and ids == sorted(ids)
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        p1 = await svc.list_versions(acc, ci_id, limit=3)
        assert [v.id for v in p1.items] == [ids[6], ids[5], ids[4]]
        assert p1.has_more is True and p1.archived_count == 0
        p2 = await svc.list_versions(acc, ci_id, before_id=p1.items[-1].id, limit=3)
        assert [v.id for v in p2.items] == [ids[3], ids[2], ids[1]]
        assert p2.has_more is True
        p3 = await svc.list_versions(acc, ci_id, before_id=p2.items[-1].id, limit=3)
        assert [v.id for v in p3.items] == [ids[0]]
        assert p3.has_more is False
        top = p1.items[0]
        assert top.name == "cmdb4-a-0-1-2-3-4-5"
        assert (top.deployment_state, top.incident_state) != ("", "")
        assert top.created_by == USER_A and top.created_by_name == "Anna Alpha"
        assert top.definition_version == 1 and top.archived is False
        # limit is clamped to 1..100
        big = await svc.list_versions(acc, ci_id, limit=10_000)
        assert len(big.items) == 7 and big.has_more is False
        one = await svc.list_versions(acc, ci_id, limit=0)
        assert len(one.items) == 1 and one.has_more is True


async def test_versions_merge_archive_rows(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a", attributes={"CPU": [{"v": 2}]})
        ids = await _bump(s, ci_id, 3)
    # move the two oldest versions into the archive table (what the archive job does)
    cmdb_db.sql(
        "INSERT INTO tiqora_cmdb_ci_version_archive (id, ci_id, definition_id, name,"
        " deployment_state_id, incident_state_id, attributes, change_note, created_by,"
        " created_at, archived_at) SELECT id, ci_id, definition_id, name, deployment_state_id,"
        " incident_state_id, attributes, change_note, created_by, created_at, :now"
        " FROM tiqora_cmdb_ci_version WHERE id IN (:a, :b)",
        a=ids[0],
        b=ids[1],
        now=datetime(2026, 10, 2),
    )
    cmdb_db.sql("DELETE FROM tiqora_cmdb_ci_version WHERE id IN (:a, :b)", a=ids[0], b=ids[1])
    async with cmdb_db.factory() as s:
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        hot = await svc.list_versions(acc, ci_id)
        assert [v.id for v in hot.items] == [ids[3], ids[2]]
        assert hot.archived_count == 2 and hot.has_more is False
        merged = await svc.list_versions(acc, ci_id, include_archived=True)
        assert [(v.id, v.archived) for v in merged.items] == [
            (ids[3], False),
            (ids[2], False),
            (ids[1], True),
            (ids[0], True),
        ]
        page = await svc.list_versions(acc, ci_id, include_archived=True, limit=3)
        assert [v.id for v in page.items] == [ids[3], ids[2], ids[1]] and page.has_more is True
        old = await svc.get_version(acc, ci_id, ids[0])
        assert old.archived is True and old.attributes == {"CPU": [{"v": 2}]}
        assert old.definition_version == 1 and old.definition_attrs[0].key == "Vendor"
        # the diff reads archived versions too
        rows = await diff_versions(s, acc, ci_id, ids[0], ids[3])
        assert [r.path for r in rows] == ["name"]


async def test_version_of_another_ci_or_hidden_class_is_not_found(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cls = await make_class(s)
        a = await make_ci(s, cls, "cmdb4-a")
        b = await make_ci(s, cls, "cmdb4-b")
        vb = (await CiService(s).get(system_access(USER_A), b)).current_version_id
        assert vb is not None
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        with pytest.raises(CmdbNotFound):
            await svc.get_version(acc, a, vb)
        with pytest.raises(CmdbNotFound):
            await svc.get_version(acc, a, 999_999_999)
        with pytest.raises(CmdbNotFound):
            await svc.list_versions(acc, 999_999_999)
        # user B has ro only on GROUP_RO: the class (GROUP_RW) is invisible to B
        acc_b = await load_access(s, USER_B)
        with pytest.raises(CmdbNotFound):
            await svc.list_versions(acc_b, a)
        with pytest.raises(CmdbNotFound):
            await svc.get_version(acc_b, a, vb)
        with pytest.raises(CmdbNotFound):
            await svc.list_history(acc_b, a)
        with pytest.raises(CmdbNotFound):
            await diff_versions(s, acc_b, a, vb, vb)


async def test_diff_lists_name_state_and_nested_changes(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(
            s,
            await make_class(s),
            "cmdb4-a",
            attributes={"NIC": [{"v": "eth0", "s": {"IPAddress": [{"v": "192.0.2.1"}]}}]},
        )
        acc = await load_access(s, USER_A)
        rec = await CiService(s).get(acc, ci_id)
        v1 = rec.current_version_id
        assert v1 is not None
        res = await CiService(s).update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            name="cmdb4-b",
            attributes={
                "NIC": [
                    {
                        "v": "eth0",
                        "s": {"IPAddress": [{"v": "192.0.2.1"}, {"v": "192.0.2.2"}]},
                    }
                ]
            },
        )
        await s.commit()
        assert res.ci.current_version_id is not None
        rows = await diff_versions(s, acc, ci_id, v1, res.ci.current_version_id)
        assert [(r.path, r.kind, r.old, r.new) for r in rows] == [
            ("name", "changed", "cmdb4-a", "cmdb4-b"),
            ("NIC[1].IPAddress[2]", "added", None, "192.0.2.2"),
        ]
        assert rows[1].label == "IP address"
        # same version against itself: nothing
        assert await diff_versions(s, acc, ci_id, v1, v1) == []
        # reverse direction turns "added" into "removed"
        back = await diff_versions(s, acc, ci_id, res.ci.current_version_id, v1)
        assert [(r.path, r.kind, r.old, r.new) for r in back] == [
            ("name", "changed", "cmdb4-b", "cmdb4-a"),
            ("NIC[1].IPAddress[2]", "removed", "192.0.2.2", None),
        ]


async def test_diff_states_and_attribute_removed_from_definition(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci_id = await make_ci(s, cid, "cmdb4-a", attributes={"CPU": [{"v": 4}]})
        acc = await load_access(s, USER_A)
        rec = await CiService(s).get(acc, ci_id)
        v1 = rec.current_version_id
        assert v1 is not None
        await ClassService(s).add_definition(
            cid,
            [a for a in parse_attrs(SERVER_ATTRS) if a.key != "CPU"],
            comment=None,
            user_id=USER_ADMIN,
        )
        await s.commit()
        res = await CiService(s).update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            attributes={"Vendor": [{"v": "Acme"}]},
            drop_unknown=True,
            deployment_state_id=await _dep(s, "retired"),
            incident_state_id=await _inc(s, "incident"),
        )
        await s.commit()
        assert res.ci.current_version_id is not None
        rows = await diff_versions(s, acc, ci_id, v1, res.ci.current_version_id)
        by_path = {r.path: r for r in rows}
        assert [r.path for r in rows[:2]] == ["deployment_state", "incident_state"]
        assert by_path["deployment_state"].kind == "changed"
        assert by_path["deployment_state"].old != by_path["deployment_state"].new
        assert by_path["incident_state"].kind == "changed"
        assert (by_path["Vendor[1]"].kind, by_path["Vendor[1]"].new) == ("added", "Acme")
        # CPU only exists in version 1's definition: still rendered, as removed
        assert (by_path["CPU[1]"].kind, by_path["CPU[1]"].old) == ("removed", "4")
        assert by_path["CPU[1]"].label == "CPU cores"


async def test_history_paging_and_events(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a", attributes={"CPU": [{"v": 2}]})
        acc = await load_access(s, USER_A)
        svc = CiService(s)
        rec = await svc.get(acc, ci_id)
        await svc.update(
            acc,
            ci_id,
            expected_lock_version=rec.lock_version,
            name="cmdb4-b",
            attributes={"CPU": [{"v": 8}]},
        )
        await s.commit()
        page = await svc.list_history(acc, ci_id, limit=100)
        events = [e.event for e in page.items]
        assert page.has_more is False
        # newest first: the update's events come before the create events
        assert events[-2:] == ["VersionCreate", "ConfigItemCreate"]
        assert "NameUpdate" in events and "ValueUpdate" in events
        name_event = next(e for e in page.items if e.event == "NameUpdate")
        assert name_event.detail == "cmdb4-a → cmdb4-b"
        assert name_event.created_by == USER_A and name_event.created_by_name == "Anna Alpha"
        assert name_event.version_id is not None
        ids = [e.id for e in page.items]
        assert ids == sorted(ids, reverse=True)
        # keyset paging walks the same list in pages of two
        seen: list[int] = []
        before: int | None = None
        while True:
            part = await svc.list_history(acc, ci_id, before_id=before, limit=2)
            seen.extend(e.id for e in part.items)
            assert len(part.items) <= 2
            if not part.has_more:
                break
            before = part.items[-1].id
        assert seen == ids
        assert len(ids) >= 5


async def test_history_of_deleted_ci_is_not_found(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ci_id = await make_ci(s, await make_class(s), "cmdb4-a")
        acc = await load_access(s, USER_ADMIN)
        await CiService(s).delete(acc, ci_id)
        await s.commit()
        with pytest.raises(CmdbNotFound):
            await CiService(s).list_history(acc, ci_id)
        leftover = cmdb_db.sql(
            "SELECT COUNT(*) FROM tiqora_cmdb_ci_history WHERE ci_id = :i", i=ci_id
        )
        assert leftover[0][0] == 0
