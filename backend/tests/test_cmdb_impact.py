"""CMDB impact graph, affected objects and CI tickets (band 743500-743999, names ``cmdb4``)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    SERVICE_ID,
    SERVICE_ID_2,
    TICKET_CLOSED,
    TICKET_HIDDEN,
    TICKET_OPEN,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    client_for,
    make_ci,
    make_class,
    make_link_type,
)
from tiqora.cmdb.access import load_access
from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.impact import affected, impact_graph, tickets_for_ci
from tiqora.cmdb.links import LinkEnd, LinkKind, LinkService
from tiqora.cmdb.schemas import ImpactGraphOut
from tiqora.cmdb.service import system_access
from tiqora.cmdb.settings import CmdbSettings

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


async def _link_kind(
    s: AsyncSession,
    source: tuple[LinkKind, str | int],
    target: tuple[LinkKind, str | int],
    link_type: str = "DependsOn",
) -> None:
    await LinkService(s).add(
        system_access(USER_A),
        source=LinkEnd(source[0], str(source[1])),
        target=LinkEnd(target[0], str(target[1])),
        link_type_key=link_type,
        enforce_permissions=False,
    )
    await s.commit()


async def _link(s: AsyncSession, a: int, b: int, link_type: str = "DependsOn") -> None:
    await _link_kind(s, ("ci", a), ("ci", b), link_type)


async def _chain(
    s: AsyncSession, names: Sequence[str], *, class_key: str = "cmdb4srv"
) -> list[int]:
    """``names[i]`` DependsOn ``names[i+1]``."""
    cid = await make_class(s, key=class_key)
    ids = [await make_ci(s, cid, n) for n in names]
    for a, b in zip(ids, ids[1:], strict=False):
        await _link(s, a, b)
    return ids


def _ci_ids(g: ImpactGraphOut) -> set[int]:
    return {n.ci_id for n in g.nodes if n.ci_id is not None}


def _cap(cap: int) -> CmdbSettings:
    # below the validated floor (50): model_construct skips validation, test only
    return CmdbSettings.model_construct(
        **{**CmdbSettings().model_dump(), "impact_graph_node_cap": cap}
    )


async def test_graph_depth_and_direction(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b, c, d = await _chain(s, ["cmdb4-a", "cmdb4-b", "cmdb4-c", "cmdb4-d"])
        acc = await load_access(s, USER_A)
        both1 = await impact_graph(s, acc, b, depth=1, direction="both", settings=CmdbSettings())
        down2 = await impact_graph(s, acc, b, depth=2, direction="down", settings=CmdbSettings())
        up3 = await impact_graph(s, acc, c, depth=3, direction="up", settings=CmdbSettings())
        clamped = await impact_graph(s, acc, b, depth=99, direction="both", settings=CmdbSettings())
    assert _ci_ids(both1) == {a, b, c}
    assert {n.depth for n in both1.nodes if n.ci_id in (a, c)} == {1}
    assert both1.root == f"ci:{b}" and not both1.truncated
    assert _ci_ids(down2) == {b, c, d}
    assert {(e.source, e.target) for e in down2.edges} == {
        (f"ci:{b}", f"ci:{c}"),
        (f"ci:{c}", f"ci:{d}"),
    }
    assert all(e.link_type == "DependsOn" and e.propagates for e in down2.edges)
    assert _ci_ids(up3) == {a, b, c}
    assert _ci_ids(clamped) == {a, b, c, d}  # depth is clamped to 5, not rejected


async def test_graph_undirected_types_follow_both_ways(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b")
        await _link(s, a, b, "ConnectedTo")
        acc = await load_access(s, USER_A)
        down = await impact_graph(s, acc, b, depth=1, direction="down", settings=CmdbSettings())
    assert _ci_ids(down) == {a, b}
    assert [e.propagates for e in down.edges] == [False]


async def test_graph_unpointed_target_type_does_not_propagate(cmdb_db: CmdbWorld) -> None:
    """Znuny's unpointed ``Target`` type reaches nobody, so its edge is not drawn as propagating."""
    async with cmdb_db.factory() as s:
        await make_link_type(s, "Cmdb4FlatTgt", "target", pointed=False)
        await make_link_type(s, "Cmdb4FlatSrc", "source", pointed=False)
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b")
        c = await make_ci(s, cid, "cmdb4-c")
        await _link(s, a, b, "Cmdb4FlatTgt")
        await _link(s, a, c, "Cmdb4FlatSrc")
        acc = await load_access(s, USER_A)
        out = await impact_graph(s, acc, a, depth=1, direction="both", settings=CmdbSettings())
    by_type = {e.link_type: e.propagates for e in out.edges}
    assert by_type == {"Cmdb4FlatTgt": False, "Cmdb4FlatSrc": True}


async def test_graph_rejects_unknown_direction(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")
        acc = await load_access(s, USER_A)
        with pytest.raises(CmdbError):
            await impact_graph(s, acc, a, direction="sideways", settings=CmdbSettings())


async def test_graph_cap_sets_truncated(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        hub = await make_ci(s, cid, "cmdb4-hub")
        for i in range(60):
            await _link(s, hub, await make_ci(s, cid, f"cmdb4-leaf-{i}"), "ConnectedTo")
        acc = await load_access(s, USER_A)
        g = await impact_graph(s, acc, hub, depth=1, settings=_cap(10))
        full = await impact_graph(s, acc, hub, depth=1, settings=CmdbSettings())
    assert len(g.nodes) == 10 and g.truncated
    ids = {n.id for n in g.nodes}
    assert all(e.source in ids and e.target in ids for e in g.edges)  # no edge to a dropped node
    assert len(full.nodes) == 61 and not full.truncated


async def test_graph_has_service_nodes_with_state(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        app = await make_ci(s, cid, "cmdb4-app", incident="incident")
        await _link_kind(s, ("service", SERVICE_ID), ("ci", app))
        acc = await load_access(s, USER_A)
        g = await impact_graph(s, acc, app, depth=1, settings=CmdbSettings())
    svc = next(n for n in g.nodes if n.kind == "service")
    assert svc.id == f"service:{SERVICE_ID}" and svc.name == "cmdb4-service"
    assert svc.ci_id is None and not svc.hidden and svc.depth == 1
    assert svc.incident_functionality == "incident"
    assert [(e.source, e.target) for e in g.edges] == [(f"service:{SERVICE_ID}", f"ci:{app}")]


async def test_impact_graph_never_names_a_hidden_ci(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        hid_cls = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        a = await make_ci(s, cid, "cmdb4-a")
        h = await make_ci(s, hid_cls, "cmdb4-secret", user_id=USER_ADMIN)
        x = await make_ci(s, cid, "cmdb4-behind-secret")
        await _link(s, a, h)
        await _link(s, h, x)
        g = await impact_graph(s, await load_access(s, USER_A), a, depth=3, settings=CmdbSettings())
        admin = await impact_graph(
            s, await load_access(s, USER_ADMIN), a, depth=3, settings=CmdbSettings()
        )
    hidden = [n for n in g.nodes if n.hidden]
    assert len(hidden) == 1 and hidden[0].name is None and hidden[0].number is None
    assert hidden[0].class_key is None and hidden[0].incident_functionality is None
    assert hidden[0].ci_id is None and hidden[0].id == f"ci:{h}" and hidden[0].depth == 1
    assert all(n.ci_id != x for n in g.nodes)  # never expanded past the hidden node
    assert "cmdb4-secret" not in g.model_dump_json()
    assert "cmdb4-behind-secret" not in g.model_dump_json()
    assert _ci_ids(admin) == {a, h, x}  # an admin sees the whole chain


async def test_graph_unreadable_root_is_not_found(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        hid_cls = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        h = await make_ci(s, hid_cls, "cmdb4-secret", user_id=USER_ADMIN)
        acc = await load_access(s, USER_A)
        with pytest.raises(CmdbNotFound):
            await impact_graph(s, acc, h, settings=CmdbSettings())
        with pytest.raises(CmdbNotFound):
            await impact_graph(s, acc, 749999, settings=CmdbSettings())


async def test_affected_lists_services_customers_and_open_tickets(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        app, db = await _chain(s, ["cmdb4-app", "cmdb4-db"])  # app DependsOn db
        await _link_kind(s, ("service", SERVICE_ID), ("ci", app))
        await _link_kind(s, ("ci", app), ("customer_company", "cmdb4-co-a"), "RelevantTo")
        for t in (TICKET_OPEN, TICKET_HIDDEN, TICKET_CLOSED):
            await _link_kind(s, ("ci", app), ("ticket", t), "RelevantTo")
        out = await affected(s, await load_access(s, USER_A), db, settings=CmdbSettings())
        none = await affected(s, await load_access(s, USER_A), app, settings=CmdbSettings())
    assert out.ci_count == 2 and [x.id for x in out.services] == [str(SERVICE_ID)]
    assert out.services[0].label == "cmdb4-service" and out.service_count == 1
    assert [x.id for x in out.customers] == ["cmdb4-co-a"] and out.customer_count == 1
    assert [t.ticket_id for t in out.open_tickets] == [TICKET_OPEN]  # hidden queue, closed
    assert out.open_ticket_count == 1 and not out.truncated
    assert out.open_tickets[0].open and out.open_tickets[0].state_type == "open"
    # DependsOn propagates both ways (survey: {DependsOn: Both}): an incident at app also reaches
    # db, which has no links of its own
    assert none.ci_count == 2 and [t.ticket_id for t in none.open_tickets] == [TICKET_OPEN]


async def test_affected_service_only_when_type_propagates_toward_it(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        app = await make_ci(s, cid, "cmdb4-app")
        await _link_kind(s, ("service", SERVICE_ID), ("ci", app), "DependsOn")  # propagates
        await _link_kind(s, ("service", SERVICE_ID_2), ("ci", app), "ConnectedTo")  # does not
        out = await affected(s, await load_access(s, USER_A), app, settings=CmdbSettings())
    assert [x.id for x in out.services] == [str(SERVICE_ID)] and out.service_count == 1


async def test_affected_service_follows_znuny_rule_for_unpointed_types(cmdb_db: CmdbWorld) -> None:
    """Ruling CMDB-PM-I2: the service end reads the type reversed (``counts_for_service``); an
    unpointed type matches only ``Source``, so ``target`` reaches the service, ``source`` not --
    also for a link stored with the service as source (a type made unpointed later, or imported)."""
    async with cmdb_db.factory() as s:
        await make_link_type(s, "Cmdb4PeerSrc", "source")
        await make_link_type(s, "Cmdb4PeerTgt", "target")
        cid = await make_class(s)
        app = await make_ci(s, cid, "cmdb4-app")
        await _link_kind(s, ("service", SERVICE_ID), ("ci", app), "Cmdb4PeerSrc")
        await _link_kind(s, ("service", SERVICE_ID_2), ("ci", app), "Cmdb4PeerTgt")
    cmdb_db.sql(
        "UPDATE tiqora_itsm_link_type SET pointed = :p"
        " WHERE name IN ('Cmdb4PeerSrc', 'Cmdb4PeerTgt')",
        p=False,
    )
    async with cmdb_db.factory() as s:
        out = await affected(s, await load_access(s, USER_A), app, settings=CmdbSettings())
    assert [x.id for x in out.services] == [str(SERVICE_ID_2)]


async def test_affected_cap_marks_truncated(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ids = await _chain(s, [f"cmdb4-c{i}" for i in range(6)])
        small = CmdbSettings.model_construct(
            **{**CmdbSettings().model_dump(), "propagation_node_cap": 3}
        )
        out = await affected(s, await load_access(s, USER_A), ids[-1], settings=small)
    assert out.truncated and out.ci_count == 3


async def test_affected_leaves_hidden_cis_out(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        hid_cls = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        db = await make_ci(s, cid, "cmdb4-db")
        secret = await make_ci(s, hid_cls, "cmdb4-secret", user_id=USER_ADMIN)
        app = await make_ci(s, cid, "cmdb4-app")
        await _link(s, secret, db)  # secret DependsOn db
        await _link(s, app, secret)  # app DependsOn secret: the incident passes through
        await _link_kind(s, ("service", SERVICE_ID), ("ci", secret))
        out_a = await affected(s, await load_access(s, USER_A), db, settings=CmdbSettings())
        out_admin = await affected(s, await load_access(s, USER_ADMIN), db, settings=CmdbSettings())
    assert out_a.ci_count == 2 and out_a.services == []
    assert out_admin.ci_count == 3 and [x.id for x in out_admin.services] == [str(SERVICE_ID)]


async def test_tickets_for_ci_open_only_and_queue_permission(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci = await make_ci(s, cid, "cmdb4-ci")
        for t in (TICKET_OPEN, TICKET_HIDDEN, TICKET_CLOSED):
            await _link_kind(s, ("ticket", t), ("ci", ci), "RelevantTo")
        acc = await load_access(s, USER_A)
        every = await tickets_for_ci(s, acc, ci, open_only=False)
        only_open = await tickets_for_ci(s, acc, ci, open_only=True)
        admin = await tickets_for_ci(s, await load_access(s, USER_ADMIN), ci, open_only=False)
    assert [t.ticket_id for t in every] == [TICKET_CLOSED, TICKET_OPEN]  # newest first
    assert [t.open for t in every] == [False, True]
    assert [t.ticket_id for t in only_open] == [TICKET_OPEN]
    row = only_open[0]
    assert row.number == f"2026101074{TICKET_OPEN}" and row.link_type == "RelevantTo"
    assert row.title == f"cmdb4 ticket {TICKET_OPEN}" and row.state_name == "open"
    assert row.queue_name == f"cmdb4-queue-{row.queue_id}"
    assert admin == []  # CMDB admin is not a queue member: queue groups decide, as for tickets


async def test_tickets_for_ci_class_ro_but_no_queue_access(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        ro_cls = await make_class(s, key="cmdb4ro", group_id=GROUP_RO)
        rw_cls = await make_class(s)
        visible = await make_ci(s, ro_cls, "cmdb4-visible")
        secret = await make_ci(s, rw_cls, "cmdb4-rw-only")
        await _link_kind(s, ("ticket", TICKET_OPEN), ("ci", visible), "RelevantTo")
        acc = await load_access(s, USER_B)  # ro on the class, nothing on the ticket's queue
        assert await tickets_for_ci(s, acc, visible, open_only=False) == []
        with pytest.raises(CmdbNotFound):  # no class access at all
            await tickets_for_ci(s, acc, secret, open_only=False)


async def test_routes_answer_404_for_unreadable_ci(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        hid_cls = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        h = await make_ci(s, hid_cls, "cmdb4-secret", user_id=USER_ADMIN)
    async with client_for(cmdb_db, USER_A) as c:
        unknown = await c.get("/api/v1/cmdb/cis/749999/impact")
        for tail in ("impact", "affected", "tickets", "links"):
            r = await c.get(f"/api/v1/cmdb/cis/{h}/{tail}")
            assert r.status_code == 404, tail
            assert r.json() == unknown.json()
        assert (await c.get(f"/api/v1/cmdb/cis/{h}/impact?depth=9")).status_code == 422
    async with client_for(cmdb_db, USER_ADMIN) as c:
        assert (await c.get(f"/api/v1/cmdb/cis/{h}/impact")).status_code == 200


async def test_impact_and_affected_routes(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        a, b, c3 = await _chain(s, ["cmdb4-a", "cmdb4-b", "cmdb4-c"])
        await _link_kind(s, ("service", SERVICE_ID), ("ci", a))
    async with client_for(cmdb_db, USER_A) as c:
        r = await c.get(f"/api/v1/cmdb/cis/{b}/impact", params={"depth": 1, "direction": "down"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["root"] == f"ci:{b}" and body["truncated"] is False
        assert {n["id"] for n in body["nodes"]} == {f"ci:{b}", f"ci:{c3}"}
        assert body["edges"] == [
            {
                "source": f"ci:{b}",
                "target": f"ci:{c3}",
                "link_type": "DependsOn",
                "propagates": True,
            }
        ]
        bad = await c.get(f"/api/v1/cmdb/cis/{b}/impact", params={"direction": "sideways"})
        assert bad.status_code == 422
        aff = await c.get(f"/api/v1/cmdb/cis/{c3}/affected")
        assert aff.status_code == 200, aff.text
        assert aff.json()["ci_count"] == 3
        assert [x["label"] for x in aff.json()["services"]] == ["cmdb4-service"]
        links = await c.get(f"/api/v1/cmdb/cis/{b}/links")
        assert links.status_code == 200 and len(links.json()) == 2


async def test_ci_tickets_route_open_only(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        ci = await make_ci(s, cid, "cmdb4-ci")
        for t in (TICKET_OPEN, TICKET_CLOSED, TICKET_HIDDEN):
            await _link_kind(s, ("ticket", t), ("ci", ci), "RelevantTo")
    async with client_for(cmdb_db, USER_A) as c:
        every = await c.get(f"/api/v1/cmdb/cis/{ci}/tickets")
        only = await c.get(f"/api/v1/cmdb/cis/{ci}/tickets", params={"open_only": "true"})
    assert [t["ticket_id"] for t in every.json()] == [TICKET_CLOSED, TICKET_OPEN]
    assert [t["ticket_id"] for t in only.json()] == [TICKET_OPEN]
    assert only.json()[0]["open"] is True and only.json()[0]["queue_name"].startswith("cmdb4-queue")
