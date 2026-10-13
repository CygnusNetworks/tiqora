"""Link graph (Task 21): ``link_graph`` and ``GET /tickets/{id}/link-graph``.

Both dialects; the world (users, queues, fake provider behind Mode A) comes from
``test_itsm_summary``. Seed band 941600-941699: chain ``T - C1 - C2 - C3``, ``X`` in queue B
(nobody reads it) linked to ``T`` and ``Y`` (queue A) which only hangs on ``X``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests._itsm_fake_provider import OPERATIONAL
from tests.test_itsm_ci_api import FULL, NOBODY, QUEUE_B, RO
from tests.test_itsm_summary import T, World, world_for
from tiqora.itsm.link_graph import TicketNotReadable, link_graph

pytestmark = pytest.mark.db

C1, C2, C3, X, Y = 941610, 941611, 941612, 941613, 941614


@pytest.fixture(params=["mysql", "postgresql"])
def dialect(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
async def w(request: pytest.FixtureRequest, dialect: str) -> AsyncIterator[World]:
    async with world_for(request, dialect) as world:
        yield world


async def _chain(w: World) -> None:
    for tid in (C1, C2, C3):
        await w.new_ticket(tid, f"Kette {tid}")
    await w.new_ticket(X, "Fremdes Ticket", queue_id=QUEUE_B)
    await w.new_ticket(Y, "Hinter dem Fremden")
    await w.link(T, C1)
    await w.link(C1, C2, link_type="ParentChild")
    await w.link(C2, C3)
    await w.link(T, X)
    await w.link(X, Y)


def _keys(body: dict[str, object]) -> list[str]:
    nodes = body["nodes"]
    assert isinstance(nodes, list)
    return [n["key"] for n in nodes]


async def test_graph_depth_and_truncation(w: World) -> None:
    await _chain(w)
    expected = {1: [T, C1], 2: [T, C1, C2], 3: [T, C1, C2, C3]}
    for depth, ids in expected.items():
        got = await w.client.get(f"/api/v1/tickets/{T}/link-graph", params={"depth": depth})
        assert got.status_code == 200, got.text
        body = got.json()
        assert _keys(body) == [f"ticket:{i}" for i in ids], depth
        assert body["root"] == f"ticket:{T}" and body["truncated"] is False
        assert [n["depth"] for n in body["nodes"]] == list(range(len(ids)))
        assert len(body["edges"]) == len(ids) - 1
    default = (await w.client.get(f"/api/v1/tickets/{T}/link-graph")).json()
    assert _keys(default) == [f"ticket:{i}" for i in expected[2]]  # depth 2 by default
    assert (
        await w.client.get(f"/api/v1/tickets/{T}/link-graph", params={"depth": 4})
    ).status_code == 422
    parent_child = [e for e in default["edges"] if e["link_type"] == "ParentChild"]
    assert parent_child == [
        {
            "source": f"ticket:{C1}",
            "target": f"ticket:{C2}",
            "link_type": "ParentChild",
            "kind": "ticket_ticket",
        }
    ]

    # the node cap: a star around T, three nodes allowed
    for i in range(5):
        await w.new_ticket(941630 + i, f"Stern {i}")
        await w.link(T, 941630 + i)
    cut = await link_graph(w.session, T, user_id=FULL, depth=1, max_nodes=3)
    assert cut.truncated is True and len(cut.nodes) == 3
    keys = {n.key for n in cut.nodes}
    assert all(e.source in keys and e.target in keys for e in cut.edges)  # no dangling edge
    uncut = await link_graph(w.session, T, user_id=FULL, depth=1, max_nodes=60)
    assert uncut.truncated is False and len(uncut.nodes) == 7  # T, C1, 5 stars, no X


async def test_graph_omits_unreadable_ticket(w: World) -> None:
    await _chain(w)
    got = await w.client.get(f"/api/v1/tickets/{T}/link-graph", params={"depth": 3})
    text = got.text
    assert f"ticket:{X}" not in text and "Fremdes" not in text
    # Y is only reachable through the unreadable X: a hidden ticket is not a bridge
    assert f"ticket:{Y}" not in text and "Hinter dem Fremden" not in text
    assert all(f"ticket:{X}" not in (e["source"], e["target"]) for e in got.json()["edges"])
    # the ticket in queue B as root: not readable by anyone here
    for user in (FULL, RO, NOBODY):
        w.login(user)
        refused = await w.client.get(f"/api/v1/tickets/{X}/link-graph")
        assert refused.status_code == 404 and refused.json()["detail"] == {
            "code": "ticket_not_found"
        }
    with pytest.raises(TicketNotReadable):
        await link_graph(w.session, X, user_id=FULL)


async def test_graph_404_without_ro_on_root(w: World) -> None:
    w.login(NOBODY)
    assert (await w.client.get(f"/api/v1/tickets/{T}/link-graph")).status_code == 404
    w.login(FULL)
    assert (await w.client.get("/api/v1/tickets/941699/link-graph")).status_code == 404


async def test_graph_includes_cis_mode_a(w: World) -> None:
    await w.new_ticket(C1, "Nachbar")
    await w.link(T, C1)
    visible = w.fake.add_ci(21, "srv-app-01.example.org", OPERATIONAL)
    secret = w.fake._cis[13].ref  # CI 13 is hidden from RO
    for ticket_id, ref in ((T, visible), (C1, visible), (T, secret)):
        await w.fake.link_ticket(
            w.session, ticket_id=ticket_id, ref=ref, link_type="RelevantTo", user_id=FULL
        )
    body = (await w.client.get(f"/api/v1/tickets/{T}/link-graph")).json()
    ci_nodes = {n["key"]: n for n in body["nodes"] if n["kind"] == "ci"}
    assert set(ci_nodes) == {"znuny:21", "znuny:13"}
    assert ci_nodes["znuny:21"]["number"] and ci_nodes["znuny:21"]["class_name"] == "Computer"
    assert ci_nodes["znuny:21"]["incident_functionality"] == "operational"
    ci_edges = sorted((e["source"], e["target"]) for e in body["edges"] if e["kind"] == "ticket_ci")
    assert ci_edges == [
        (f"ticket:{T}", "znuny:13"),
        (f"ticket:{T}", "znuny:21"),
        (f"ticket:{C1}", "znuny:21"),
    ]
    # the CI hangs on two tickets: one node, two edges
    assert len([n for n in body["nodes"] if n["key"] == "znuny:21"]) == 1

    # RO may not read CI 13: no node, no edge, no trace
    w.login(RO)
    ro_body = (await w.client.get(f"/api/v1/tickets/{T}/link-graph")).json()
    assert "znuny:13" not in str(ro_body) and "srv-secret" not in str(ro_body)
    assert "znuny:21" in _keys(ro_body)


async def test_graph_without_itsm_has_tickets_only(
    request: pytest.FixtureRequest, dialect: str
) -> None:  # noqa: F811
    async with world_for(request, dialect, mode="off") as off:
        await off.new_ticket(C1, "Nachbar")
        await off.link(T, C1)
        ref = off.fake._cis[11].ref
        await off.fake.link_ticket(
            off.session, ticket_id=T, ref=ref, link_type="RelevantTo", user_id=FULL
        )
        body = (await off.client.get(f"/api/v1/tickets/{T}/link-graph")).json()
    assert _keys(body) == [f"ticket:{T}", f"ticket:{C1}"]  # no gate; no CIs while off
    assert [e["kind"] for e in body["edges"]] == ["ticket_ticket"]
