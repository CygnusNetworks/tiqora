"""Impact graph, affected objects and the tickets linked to a CI (D8, D11, Task 13).

Everything here is read-only. Visibility follows the class groups: a CI of a class the caller may
not read is a *hidden* node (``id``, ``kind``, ``hidden``, ``depth`` only), is never expanded and
is left out of every count and list of :func:`affected`; the traversal of the propagation
itself runs through such CIs because the incident really passes through them.

All lookups of Znuny rows (``ticket``, ``service``, ``queue``, ...) are separate IN-list queries;
there is no JOIN between ``tiqora_*`` and Znuny tables (collation, error 1267).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Final

from sqlalchemy import and_, bindparam, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from tiqora.cmdb.access import CmdbAccess, readable_class_ids, require_class_access
from tiqora.cmdb.errors import CmdbError, CmdbNotFound
from tiqora.cmdb.links import LinkService, LinkTypeService, Propagating
from tiqora.cmdb.propagation import MAX_DEPTH, _Graph, service_incident_states
from tiqora.cmdb.schemas import (
    AffectedOut,
    CiTicketOut,
    CmdbNamedRefOut,
    ImpactEdgeOut,
    ImpactGraphOut,
    ImpactNodeOut,
)
from tiqora.cmdb.settings import CmdbSettings
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbClass,
    TiqoraItsmLink,
)
from tiqora.domain.queue_service import OPEN_STATE_TYPES
from tiqora.itsm.service_state import counts_for_service
from tiqora.permissions.engine import PermissionEngine

DEPTH_MIN: Final = 1
DEPTH_MAX: Final = 5
LIST_CAP: Final = 50
TICKET_LIST_CAP: Final = 500
_CHUNK: Final = 1000
DIRECTIONS: Final = ("both", "up", "down")


def _chunks(items: Sequence[int] | Sequence[str]) -> Iterable[list[int] | list[str]]:
    for i in range(0, len(items), _CHUNK):
        yield list(items[i : i + _CHUNK])  # type: ignore[misc]


def _in(sql: str) -> TextClause:
    return text(sql).bindparams(bindparam("ids", expanding=True))


async def _ci_rows(
    session: AsyncSession, ids: Iterable[int]
) -> dict[int, tuple[str, str, int, int]]:
    """CI id -> (number, name, class id, current incident state id)."""
    out: dict[int, tuple[str, str, int, int]] = {}
    ordered = sorted(set(ids))
    for chunk in _chunks(ordered):
        for i, number, name, class_id, cur in (
            await session.execute(
                select(
                    TiqoraCmdbCi.id,
                    TiqoraCmdbCi.number,
                    TiqoraCmdbCi.name,
                    TiqoraCmdbCi.class_id,
                    TiqoraCmdbCi.cur_incident_state_id,
                ).where(TiqoraCmdbCi.id.in_([int(c) for c in chunk]))
            )
        ).all():
            out[int(i)] = (str(number), str(name), int(class_id), int(cur))
    return out


async def _readable(session: AsyncSession, access: CmdbAccess) -> frozenset[int]:
    return frozenset(await readable_class_ids(session, access))


async def _require_ci(
    session: AsyncSession, access: CmdbAccess, ci_id: int
) -> tuple[str, str, int, int]:
    """The CI's row, or ``CmdbNotFound`` when it is unknown or its class is not readable."""
    row = (await _ci_rows(session, [ci_id])).get(ci_id)
    if row is None:
        raise CmdbNotFound(f"ci {ci_id}")
    await require_class_access(session, access, row[2], "ro")
    return row


async def _service_names(session: AsyncSession, ids: Iterable[int]) -> dict[int, str]:
    ordered = sorted(set(ids))
    out: dict[int, str] = {}
    for chunk in _chunks(ordered):
        rows = await session.execute(
            _in("SELECT id, name FROM service WHERE id IN :ids"),
            {"ids": chunk},
        )
        out.update({int(i): str(n) for i, n in rows.all()})
    return out


# --- impact graph ---------------------------------------------------------------------------


async def _service_links(
    session: AsyncSession, ci_ids: Sequence[int]
) -> list[tuple[int, int, str, bool]]:
    """Links between a CI of ``ci_ids`` and a service: ``(ci, service, type, ci_is_source)``."""
    out: set[tuple[int, int, str, bool]] = set()
    for chunk in _chunks([str(i) for i in sorted(ci_ids)]):
        keys = [str(c) for c in chunk]
        rows = await session.execute(
            select(
                TiqoraItsmLink.source_kind,
                TiqoraItsmLink.source_key,
                TiqoraItsmLink.target_key,
                TiqoraItsmLink.link_type,
            ).where(
                or_(
                    and_(
                        TiqoraItsmLink.source_kind == "ci",
                        TiqoraItsmLink.source_key.in_(keys),
                        TiqoraItsmLink.target_kind == "service",
                    ),
                    and_(
                        TiqoraItsmLink.target_kind == "ci",
                        TiqoraItsmLink.target_key.in_(keys),
                        TiqoraItsmLink.source_kind == "service",
                    ),
                )
            )
        )
        for skind, skey, tkey, name in rows.all():
            ci_is_source = skind == "ci"
            ci_key, svc_key = (skey, tkey) if ci_is_source else (tkey, skey)
            if str(ci_key).isdigit() and str(svc_key).isdigit():
                out.add((int(ci_key), int(svc_key), str(name), ci_is_source))
    return sorted(out)


async def impact_graph(
    session: AsyncSession,
    access: CmdbAccess,
    ci_id: int,
    *,
    depth: int = 2,
    direction: str = "both",
    settings: CmdbSettings,
) -> ImpactGraphOut:
    """Breadth-first neighbourhood of a CI over all CI to CI links plus linked services.

    ``down`` follows links from source to target (what the CI depends on, for the default types),
    ``up`` the reverse; undirected types count in both. Stops at ``settings.impact_graph_node_cap``
    nodes (``truncated``).
    """
    if direction not in DIRECTIONS:
        raise CmdbError(f"unknown direction {direction!r}")
    depth = max(DEPTH_MIN, min(DEPTH_MAX, depth))
    await _require_ci(session, access, ci_id)
    readable = await _readable(session, access)
    cap = int(settings.impact_graph_node_cap)
    types = {t.name: t for t in await LinkTypeService(session).list_types(include_invalid=True)}
    link_svc = LinkService(session)

    ci_depth: dict[int, int] = {ci_id: 0}
    hidden_cis: set[int] = set()
    svc_depth: dict[int, int] = {}
    edges: set[tuple[str, str, str]] = set()
    truncated = False
    frontier = [ci_id]

    def follows(name: str, from_source: bool) -> bool:
        pointed = bool(types[name].pointed) if name in types else True
        if not pointed or direction == "both":
            return True
        return from_source if direction == "down" else not from_source

    for layer in range(1, depth + 1):
        if not frontier:
            break
        fset = set(frontier)
        found_ci: set[int] = set()
        found_svc: set[int] = set()
        for src, tgt, name in await link_svc.ci_neighbours(frontier, propagating_only=False):
            if src in fset and follows(name, True):
                found_ci.add(tgt)
                edges.add((f"ci:{src}", f"ci:{tgt}", name))
            if tgt in fset and follows(name, False):
                found_ci.add(src)
                edges.add((f"ci:{src}", f"ci:{tgt}", name))
        for ci, svc, name, ci_is_source in await _service_links(session, frontier):
            if follows(name, ci_is_source):
                found_svc.add(svc)
                pair = (
                    (f"ci:{ci}", f"service:{svc}")
                    if ci_is_source
                    else (
                        f"service:{svc}",
                        f"ci:{ci}",
                    )
                )
                edges.add((pair[0], pair[1], name))
        new_ci = sorted(found_ci - set(ci_depth))
        new_svc = sorted(found_svc - set(svc_depth))
        info = await _ci_rows(session, new_ci)
        next_frontier: list[int] = []
        for n in new_ci:
            if n not in info:  # dangling link to a deleted CI
                continue
            if len(ci_depth) + len(svc_depth) >= cap:
                truncated = True
                break
            ci_depth[n] = layer
            if info[n][2] in readable:
                next_frontier.append(n)
            else:
                hidden_cis.add(n)
        for s in new_svc:
            if len(ci_depth) + len(svc_depth) >= cap:
                truncated = True
                break
            svc_depth[s] = layer
        frontier = next_frontier

    info = await _ci_rows(session, ci_depth)
    visible = [i for i in ci_depth if i in info and i not in hidden_cis]
    class_keys = await _class_keys(session, {info[i][2] for i in visible})
    functionality = await _functionality(session, {info[i][3] for i in visible})
    svc_names = await _service_names(session, svc_depth)
    svc_states = await service_incident_states(session, sorted(svc_depth))

    nodes: list[ImpactNodeOut] = []
    for i, d in sorted(ci_depth.items(), key=lambda kv: (kv[1], kv[0])):
        if i in hidden_cis:
            nodes.append(ImpactNodeOut(id=f"ci:{i}", kind="ci", hidden=True, depth=d))
        else:
            number, name, class_id, cur = info[i]
            nodes.append(
                ImpactNodeOut(
                    id=f"ci:{i}",
                    kind="ci",
                    ci_id=i,
                    number=number,
                    name=name,
                    class_key=class_keys.get(class_id),
                    incident_functionality=functionality.get(cur),
                    hidden=False,
                    depth=d,
                )
            )
    for s, d in sorted(svc_depth.items(), key=lambda kv: (kv[1], kv[0])):
        state = svc_states[s].state
        nodes.append(
            ImpactNodeOut(
                id=f"service:{s}",
                kind="service",
                name=svc_names.get(s, str(s)),
                incident_functionality=state.functionality if state is not None else None,
                hidden=False,
                depth=d,
            )
        )
    ids = {n.id for n in nodes}
    propagating = await LinkTypeService(session).propagating()
    out_edges = [
        ImpactEdgeOut(source=s, target=t, link_type=name, propagates=name in propagating)
        for s, t, name in sorted(edges)
        if s in ids and t in ids
    ]
    return ImpactGraphOut(nodes=nodes, edges=out_edges, truncated=truncated, root=f"ci:{ci_id}")


async def _class_keys(session: AsyncSession, class_ids: set[int]) -> dict[int, str]:
    if not class_ids:
        return {}
    rows = await session.execute(
        select(TiqoraCmdbClass.id, TiqoraCmdbClass.class_key).where(
            TiqoraCmdbClass.id.in_(sorted(class_ids))
        )
    )
    return {int(i): str(k) for i, k in rows.all()}


async def _functionality(session: AsyncSession, state_ids: set[int]) -> dict[int, str | None]:
    if not state_ids:
        return {}
    rows = await session.execute(
        select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.functionality).where(
            TiqoraCmdbCatalog.id.in_(sorted(state_ids))
        )
    )
    return {int(i): (str(f) if f is not None else None) for i, f in rows.all()}


# --- tickets --------------------------------------------------------------------------------


async def _ticket_links(session: AsyncSession, ci_ids: Sequence[int]) -> dict[int, str]:
    """Ticket id -> link type name (the alphabetically first) for tickets linked to the CIs."""
    out: dict[int, str] = {}
    for chunk in _chunks([str(i) for i in sorted(ci_ids)]):
        keys = [str(c) for c in chunk]
        rows = await session.execute(
            select(
                TiqoraItsmLink.source_kind,
                TiqoraItsmLink.source_key,
                TiqoraItsmLink.target_key,
                TiqoraItsmLink.link_type,
            ).where(
                or_(
                    and_(
                        TiqoraItsmLink.source_kind == "ticket",
                        TiqoraItsmLink.target_kind == "ci",
                        TiqoraItsmLink.target_key.in_(keys),
                    ),
                    and_(
                        TiqoraItsmLink.target_kind == "ticket",
                        TiqoraItsmLink.source_kind == "ci",
                        TiqoraItsmLink.source_key.in_(keys),
                    ),
                )
            )
        )
        for skind, skey, tkey, name in rows.all():
            tid = skey if skind == "ticket" else tkey
            if str(tid).isdigit():
                t = int(str(tid))
                if t not in out or str(name) < out[t]:
                    out[t] = str(name)
    return out


async def _readable_tickets(
    session: AsyncSession,
    access: CmdbAccess,
    links: dict[int, str],
    *,
    open_only: bool,
) -> list[CiTicketOut]:
    """Ticket rows (IN-list), state and queue names (IN-lists), filtered by ``ro`` on the queue.

    The open check resolves the state types first and filters the rows in Python.
    """
    if not links:
        return []
    rows: list[tuple[int, str, str, int, int]] = []
    for chunk in _chunks(sorted(links)):
        res = await session.execute(
            _in("SELECT id, tn, title, queue_id, ticket_state_id FROM ticket WHERE id IN :ids"),
            {"ids": chunk},
        )
        rows.extend((int(i), str(tn), str(t), int(q), int(s)) for i, tn, t, q, s in res.all())
    if not rows:
        return []
    state_ids = sorted({r[4] for r in rows})
    states = {
        int(i): (str(n), int(tid))
        for i, n, tid in (
            await session.execute(
                _in("SELECT id, name, type_id FROM ticket_state WHERE id IN :ids"),
                {"ids": state_ids},
            )
        ).all()
    }
    type_names = {
        int(i): str(n)
        for i, n in (
            await session.execute(
                _in("SELECT id, name FROM ticket_state_type WHERE id IN :ids"),
                {"ids": sorted({t for _, t in states.values()})},
            )
        ).all()
    }
    queue_ids = sorted({r[3] for r in rows})
    queue_names = {
        int(i): str(n)
        for i, n in (
            await session.execute(
                _in("SELECT id, name FROM queue WHERE id IN :ids"),
                {"ids": queue_ids},
            )
        ).all()
    }
    perms = PermissionEngine(session)
    allowed: dict[int, bool] = {}
    out: list[CiTicketOut] = []
    for tid, tn, title, queue_id, state_id in sorted(rows, reverse=True):
        state_name, type_id = states.get(state_id, (str(state_id), 0))
        type_name = type_names.get(type_id, "")
        is_open = type_name in OPEN_STATE_TYPES
        if open_only and not is_open:
            continue
        if queue_id not in allowed:
            allowed[queue_id] = await perms.check(access.user_id, queue_id, "ro")
        if not allowed[queue_id]:
            continue
        out.append(
            CiTicketOut(
                ticket_id=tid,
                number=tn,
                title=title,
                queue_id=queue_id,
                queue_name=queue_names.get(queue_id, str(queue_id)),
                state_name=state_name,
                state_type=type_name,
                open=is_open,
                link_type=links[tid],
            )
        )
    return out


async def tickets_for_ci(
    session: AsyncSession, access: CmdbAccess, ci_id: int, *, open_only: bool
) -> list[CiTicketOut]:
    """Tickets linked to the CI the caller may read (``ro`` on the queue), newest first.

    Unknown or unreadable CI: ``CmdbNotFound``. At most :data:`TICKET_LIST_CAP` entries.
    """
    await _require_ci(session, access, ci_id)
    links = await _ticket_links(session, [ci_id])
    return (await _readable_tickets(session, access, links, open_only=open_only))[:TICKET_LIST_CAP]


# --- affected -------------------------------------------------------------------------------


async def _reach(session: AsyncSession, ci_id: int, cap: int) -> tuple[set[int], bool]:
    """CIs that would get ``warning`` from an incident at ``ci_id`` (incl. itself), capped."""
    graph = _Graph(session, await LinkTypeService(session).propagating())
    reach = {ci_id}
    frontier = {ci_id}
    depth = 0
    while frontier:
        await graph.expand(frontier)
        nxt: set[int] = set()
        for n in frontier:
            nxt |= graph.affects.get(n, set()) - reach
        if not nxt:
            break
        depth += 1
        if depth > MAX_DEPTH:
            return reach, True
        room = cap - len(reach)
        if len(nxt) > room:
            reach |= set(sorted(nxt)[: max(room, 0)])
            return reach, True
        reach |= nxt
        frontier = nxt
    return reach, False


async def _affected_services(
    session: AsyncSession, ci_ids: Sequence[int], props: Mapping[str, Propagating]
) -> list[CmdbNamedRefOut]:
    found: set[int] = set()
    for _ci, svc, name, ci_is_source in await _service_links(session, ci_ids):
        ltype = props.get(name)
        # the incident travels toward the service end: the same rule as the service state (R12)
        if ltype is not None and counts_for_service(
            ltype.propagation, ltype.pointed, service_is_source=not ci_is_source
        ):
            found.add(svc)
    names = await _service_names(session, found)
    return sorted(
        (CmdbNamedRefOut(id=str(s), label=names.get(s, str(s))) for s in found),
        key=lambda r: (r.label.casefold(), r.id),
    )


async def _linked_customers(session: AsyncSession, ci_ids: Sequence[int]) -> list[CmdbNamedRefOut]:
    users: set[str] = set()
    companies: set[str] = set()
    for chunk in _chunks([str(i) for i in sorted(ci_ids)]):
        keys = [str(c) for c in chunk]
        rows = await session.execute(
            select(
                TiqoraItsmLink.source_kind,
                TiqoraItsmLink.source_key,
                TiqoraItsmLink.target_kind,
                TiqoraItsmLink.target_key,
            ).where(
                or_(
                    and_(
                        TiqoraItsmLink.source_kind == "ci",
                        TiqoraItsmLink.source_key.in_(keys),
                        TiqoraItsmLink.target_kind.in_(["customer_user", "customer_company"]),
                    ),
                    and_(
                        TiqoraItsmLink.target_kind == "ci",
                        TiqoraItsmLink.target_key.in_(keys),
                        TiqoraItsmLink.source_kind.in_(["customer_user", "customer_company"]),
                    ),
                )
            )
        )
        for skind, skey, tkind, tkey in rows.all():
            kind, key = (tkind, tkey) if skind == "ci" else (skind, skey)
            (users if kind == "customer_user" else companies).add(str(key))
    out: list[CmdbNamedRefOut] = []
    if users:
        for chunk in _chunks(sorted(users)):
            rows2 = await session.execute(
                _in("SELECT login, first_name, last_name FROM customer_user WHERE login IN :ids"),
                {"ids": chunk},
            )
            for login, first, last in rows2.all():
                name = f"{first or ''} {last or ''}".strip()
                out.append(CmdbNamedRefOut(id=str(login), label=name or str(login)))
    if companies:
        for chunk in _chunks(sorted(companies)):
            rows3 = await session.execute(
                _in("SELECT customer_id, name FROM customer_company WHERE customer_id IN :ids"),
                {"ids": chunk},
            )
            for cid, name_ in rows3.all():
                out.append(CmdbNamedRefOut(id=str(cid), label=str(name_ or cid)))
    return sorted(out, key=lambda r: (r.label.casefold(), r.id))


async def affected(
    session: AsyncSession,
    access: CmdbAccess,
    ci_id: int,
    *,
    settings: CmdbSettings,
) -> AffectedOut:
    """What an incident at this CI would reach: CIs, services, customers and open tickets.

    Only CIs of readable classes are counted and contribute services, customers and tickets.
    Every list holds at most :data:`LIST_CAP` entries with the total in ``*_count``.
    """
    await _require_ci(session, access, ci_id)
    reach, truncated = await _reach(session, ci_id, int(settings.propagation_node_cap))
    readable = await _readable(session, access)
    info = await _ci_rows(session, reach)
    visible = sorted(i for i, row in info.items() if row[2] in readable)
    props = await LinkTypeService(session).propagating()
    services = await _affected_services(session, visible, props)
    customers = await _linked_customers(session, visible)
    tickets = await _readable_tickets(
        session, access, await _ticket_links(session, visible), open_only=True
    )
    return AffectedOut(
        ci_count=len(visible),
        services=services[:LIST_CAP],
        service_count=len(services),
        customers=customers[:LIST_CAP],
        customer_count=len(customers),
        open_tickets=tickets[:LIST_CAP],
        open_ticket_count=len(tickets),
        truncated=truncated,
    )


__all__ = ["affected", "impact_graph", "tickets_for_ci"]
