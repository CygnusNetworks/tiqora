"""Link graph of a ticket (I12): linked tickets and CIs around it, breadth first.

Nodes are the tickets the caller may read (``ro``) and the CIs the provider lets the caller
read; anything else is simply absent -- also as a bridge: a ticket the caller cannot read is not
walked through, so the graph never hints at its neighbours. The walk is one ``link_relation``
query and one ticket query per level (never per node), CIs are attached with one provider call
for all ticket nodes at the end. ``max_nodes`` caps the result (``truncated`` tells so); tickets
take the slots first, CIs fill what is left.

Reads only; never commits.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.ci_provider import CiUnavailable
from tiqora.itsm.mode import ItsmModeState, get_itsm_mode
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.ticket_access import readable_tickets
from tiqora.znuny.sysconfig import SysConfig

CHUNK = 500
MAX_DEPTH = 3


class TicketNotReadable(LookupError):
    """The root ticket is unknown or the caller holds no ``ro`` on it (both look the same)."""


@dataclass(frozen=True, slots=True)
class GraphNode:
    #: ``"ticket:123"`` or ``"znuny:45"`` / ``"tiqora:45"`` (the CI reference form)
    key: str
    kind: Literal["ticket", "ci"]
    #: hops from the root ticket (0 = root); CIs: the hop of the nearest ticket they hang on
    depth: int
    #: tickets: the ticket number; CIs: the CI number
    number: str
    title: str
    #: tickets: state name / state type; CIs: ``None``
    state: str | None = None
    state_type: str | None = None
    #: CIs only
    class_name: str | None = None
    incident_state: str | None = None
    incident_functionality: str | None = None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    link_type: str
    kind: Literal["ticket_ticket", "ticket_ci"]


@dataclass(slots=True)
class LinkGraph:
    root: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    truncated: bool = False


def ticket_key(ticket_id: int) -> str:
    return f"ticket:{ticket_id}"


async def _ticket_facts(
    session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
) -> dict[int, tuple[str, str, str, str]]:
    """``{id: (tn, title, state, state_type)}`` of the tickets the user may read."""
    readable = await readable_tickets(session, ticket_ids, user_id=user_id)
    return {tid: (f.tn, f.title, f.state, f.state_type) for tid, f in readable.items()}


async def _relations(
    session: AsyncSession, ticket_ids: Sequence[int]
) -> list[tuple[int, int, str]]:
    """Valid ticket-to-ticket links touching any of ``ticket_ids``: ``(source, target, type)``."""
    stmt = text(
        "SELECT lr.source_key, lr.target_key, lt.name FROM link_relation lr"
        " JOIN link_object lo ON lo.id = lr.source_object_id AND lo.name = 'Ticket'"
        " JOIN link_state ls ON ls.id = lr.state_id AND ls.name = 'Valid'"
        " JOIN link_type lt ON lt.id = lr.type_id"
        " WHERE lr.target_object_id = lr.source_object_id"
        " AND (lr.source_key IN :keys OR lr.target_key IN :keys)"
    ).bindparams(bindparam("keys", expanding=True))
    found: set[tuple[int, int, str]] = set()
    ids = sorted(set(ticket_ids))
    for start in range(0, len(ids), CHUNK):
        keys = [str(i) for i in ids[start : start + CHUNK]]
        for source_key, target_key, type_name in (
            await session.execute(stmt, {"keys": keys})
        ).all():
            try:
                found.add((int(source_key), int(target_key), str(type_name)))
            except (TypeError, ValueError):
                continue
    return sorted(found)


async def link_graph(
    session: AsyncSession,
    ticket_id: int,
    *,
    user_id: int,
    depth: int = 2,
    max_nodes: int = 60,
    mode: ItsmModeState | None = None,
) -> LinkGraph:
    """The graph around ``ticket_id`` (``depth`` clamped to 1..3, at least 2 nodes allowed).

    CIs appear when a provider serves the mode (never while ITSM is off); a provider that is
    unavailable just contributes no CI nodes. Raises :class:`TicketNotReadable` for an unknown
    or unreadable root.
    """
    depth = max(1, min(int(depth), MAX_DEPTH))
    max_nodes = max(2, int(max_nodes))
    root_facts = (await _ticket_facts(session, [ticket_id], user_id=user_id)).get(ticket_id)
    if root_facts is None:
        raise TicketNotReadable(ticket_id)

    graph = LinkGraph(root=ticket_key(ticket_id))
    nodes: dict[str, GraphNode] = {}
    ticket_depth: dict[int, int] = {ticket_id: 0}
    # readable = in the graph; unreadable ids are remembered so they are looked up once
    unreadable: set[int] = set()
    tn, title, state, state_type = root_facts
    nodes[graph.root] = GraphNode(graph.root, "ticket", 0, tn, title, state, state_type)
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    frontier = [ticket_id]

    for level in range(1, depth + 1):
        if not frontier:
            break
        relations = await _relations(session, frontier)
        fresh = sorted(
            {
                other
                for s, t, _ in relations
                for other in (s, t)
                if other not in ticket_depth and other not in unreadable
            }
        )
        facts = await _ticket_facts(session, fresh, user_id=user_id) if fresh else {}
        unreadable.update(o for o in fresh if o not in facts)
        next_frontier: list[int] = []
        for other in fresh:
            if other not in facts:
                continue
            if len(nodes) >= max_nodes:
                graph.truncated = True
                break
            ticket_depth[other] = level
            n_tn, n_title, n_state, n_type = facts[other]
            key = ticket_key(other)
            nodes[key] = GraphNode(key, "ticket", level, n_tn, n_title, n_state, n_type)
            next_frontier.append(other)
        for source, target, link_type in relations:
            if source in ticket_depth and target in ticket_depth and source != target:
                a, b = ticket_key(source), ticket_key(target)
                edges.setdefault((a, b, link_type), GraphEdge(a, b, link_type, "ticket_ticket"))
        frontier = next_frontier

    # A level-``depth`` node can also link to nodes already in the graph (a cycle): the loop
    # above only queried the frontier of each level, so close those edges with one more query.
    if len(ticket_depth) > 1 and frontier:
        for source, target, link_type in await _relations(session, frontier):
            if source in ticket_depth and target in ticket_depth and source != target:
                a, b = ticket_key(source), ticket_key(target)
                edges.setdefault((a, b, link_type), GraphEdge(a, b, link_type, "ticket_ticket"))

    await _attach_cis(
        session, graph, nodes, edges, ticket_depth, user_id=user_id, max_nodes=max_nodes, mode=mode
    )
    graph.nodes = sorted(nodes.values(), key=lambda n: (n.kind != "ticket", n.depth, n.key))
    graph.edges = sorted(edges.values(), key=lambda e: (e.kind, e.source, e.target, e.link_type))
    return graph


async def _attach_cis(
    session: AsyncSession,
    graph: LinkGraph,
    nodes: dict[str, GraphNode],
    edges: dict[tuple[str, str, str], GraphEdge],
    ticket_depth: dict[int, int],
    *,
    user_id: int,
    max_nodes: int,
    mode: ItsmModeState | None,
) -> None:
    state = mode or await get_itsm_mode(session)
    if state.mode == "off":
        return
    provider = get_ci_provider(state.mode, SysConfig(session))
    if provider is None:
        return
    try:
        links = await provider.ticket_links(session, sorted(ticket_depth), user_id=user_id)
    except CiUnavailable:
        return
    for tid in sorted(links):
        for link in links[tid]:
            key = str(link.ci.ref)
            if key not in nodes:
                if len(nodes) >= max_nodes:
                    graph.truncated = True
                    continue
                inci = link.ci.incident_state
                nodes[key] = GraphNode(
                    key,
                    "ci",
                    ticket_depth.get(tid, 0),
                    link.ci.number,
                    link.ci.name,
                    class_name=link.ci.class_name,
                    incident_state=None if inci is None else inci.name,
                    incident_functionality=None if inci is None else inci.functionality,
                )
            t_key = ticket_key(tid)
            a, b = (key, t_key) if link.direction == "target" else (t_key, key)
            edges.setdefault((a, b, link.link_type), GraphEdge(a, b, link.link_type, "ticket_ci"))


__all__ = [
    "MAX_DEPTH",
    "GraphEdge",
    "GraphNode",
    "LinkGraph",
    "TicketNotReadable",
    "link_graph",
    "ticket_key",
]
