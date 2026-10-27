"""Incidents and problems related to a change, and creating a change from a ticket (design I13).

The links are the ordinary Znuny ticket-ticket ``link_relation`` rows of
``ItsmChangeSettings.related_link_type`` (change = source, other ticket = target), written by
``link_objects`` / ``unlink_objects`` so the ``TicketLinkAdd`` / ``TicketLinkDelete`` history of
both tickets is Znuny's. Nothing here moves a phase: a change created from a ticket gets its one
``event="phase"`` row from :func:`create_change`.

Lists leave out the change's tasks (and every other change task) and tickets the caller cannot
read; the link rows are read with one query on Znuny tables, the task ids with an IN list on the
Tiqora table (never a join between the two families).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.domain.ticket_access import CHUNK, TicketFacts, readable_tickets
from tiqora.domain.ticket_write_service import LinkConflict, link_objects, unlink_objects
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import require_change_mode
from tiqora.itsm.change.models import TiqoraItsmChangeTask
from tiqora.itsm.change.service import (
    ChangeCreateIn,
    ChangeView,
    _existing_services,
    _parse_ci_refs,
    _row,
    _ticket,
    create_change,
    record_event,
)
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.providers import get_ci_provider
from tiqora.itsm.settings import load_itsm_settings
from tiqora.itsm.summary import Role, role_of
from tiqora.znuny.sysconfig import SysConfig


@dataclass(frozen=True, slots=True)
class RelatedTicket:
    ticket_id: int
    tn: str
    title: str
    state: str
    state_type: str
    #: ITSM role of the ticket type (``incident``, ``problem``, …); ``None`` for other types
    role: Role | None
    link_type: str
    #: the change's side of the link: ``source`` (it points at the ticket) or ``target``
    direction: Literal["source", "target"]


async def _relations(
    session: AsyncSession, ticket_id: int
) -> list[tuple[int, str, Literal["source", "target"]]]:
    """Valid ticket-ticket links of one ticket: ``(other id, link type, change side)``."""
    rows = (
        await session.execute(
            text(
                "SELECT lr.source_key, lr.target_key, lt.name FROM link_relation lr"
                " JOIN link_object lo ON lo.id = lr.source_object_id AND lo.name = 'Ticket'"
                " JOIN link_state ls ON ls.id = lr.state_id AND ls.name = 'Valid'"
                " JOIN link_type lt ON lt.id = lr.type_id"
                " WHERE lr.target_object_id = lr.source_object_id"
                " AND (lr.source_key = :k OR lr.target_key = :k)"
            ),
            {"k": str(ticket_id)},
        )
    ).all()
    found: set[tuple[int, str, Literal["source", "target"]]] = set()
    for source_key, target_key, type_name in rows:
        try:
            source, target = int(source_key), int(target_key)
        except (TypeError, ValueError):
            continue
        if source == target:
            continue
        if source == ticket_id:
            found.add((target, str(type_name), "source"))
        else:
            found.add((source, str(type_name), "target"))
    return sorted(found)


async def _task_ticket_ids(session: AsyncSession, ids: list[int]) -> set[int]:
    found: set[int] = set()
    for start in range(0, len(ids), CHUNK):
        rows = await session.execute(
            select(TiqoraItsmChangeTask.ticket_id).where(
                TiqoraItsmChangeTask.ticket_id.in_(ids[start : start + CHUNK])
            )
        )
        found.update(int(r[0]) for r in rows)
    return found


async def list_related(
    session: AsyncSession, settings: Settings, *, ticket_id: int, user_id: int
) -> list[RelatedTicket]:
    """Tickets linked to a readable change, tasks and unreadable tickets left out.

    404 ``ticket_not_found`` without ``ro`` on the change, ``change_not_found`` for a ticket
    without a change record.
    """
    await require_change_mode(session, settings)
    itsm_cfg = await load_itsm_settings(session, settings)
    await _ticket(session, ticket_id, user_id=user_id)
    await _row(session, ticket_id)
    relations = await _relations(session, ticket_id)
    ids = sorted({other for other, _, _ in relations})
    tasks = await _task_ticket_ids(session, ids)
    readable = await readable_tickets(session, [i for i in ids if i not in tasks], user_id=user_id)
    return [
        _related(readable[other], link_type, side, role_of(readable[other].type_name, itsm_cfg))
        for other, link_type, side in relations
        if other in readable
    ]


def _related(
    facts: TicketFacts, link_type: str, side: Literal["source", "target"], role: Role | None
) -> RelatedTicket:
    return RelatedTicket(
        ticket_id=facts.ticket_id,
        tn=facts.tn,
        title=facts.title,
        state=facts.state,
        state_type=facts.state_type,
        role=role,
        link_type=link_type,
        direction=side,
    )


async def _check_pair(
    session: AsyncSession, *, ticket_id: int, other_ticket_id: int, user_id: int
) -> None:
    """Existence before permission: the change (``ro``, record), then ``rw`` on it, then
    ``ro`` on the other ticket, which must not be a change task."""
    await _ticket(session, ticket_id, user_id=user_id)
    await _row(session, ticket_id)
    await _ticket(session, ticket_id, user_id=user_id, write=True)
    await _ticket(session, other_ticket_id, user_id=user_id)
    if other_ticket_id == ticket_id:
        raise ChangeError("related_self", 422)
    if await _task_ticket_ids(session, [other_ticket_id]):
        raise ChangeError("related_is_task", 409)


async def link_related(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    other_ticket_id: int,
    user_id: int,
) -> list[RelatedTicket]:
    """Link another ticket to the change (``rw`` on the change, ``ro`` on the other); idempotent.

    Refused: itself (422 ``related_self``), a change task (409 ``related_is_task``), a link
    Znuny's link rules refuse (409 ``link_refused`` with the ``reason``).
    """
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    await _check_pair(
        session, ticket_id=ticket_id, other_ticket_id=other_ticket_id, user_id=user_id
    )
    await _link(
        session,
        change_id=ticket_id,
        other_id=other_ticket_id,
        link_type=cfg.related_link_type,
        user_id=user_id,
        via=None,
    )
    return await list_related(session, settings, ticket_id=ticket_id, user_id=user_id)


async def _link(
    session: AsyncSession,
    *,
    change_id: int,
    other_id: int,
    link_type: str,
    user_id: int,
    via: str | None,
) -> None:
    try:
        written = await link_objects(
            session,
            source_object="Ticket",
            source_key=str(change_id),
            target_object="Ticket",
            target_key=str(other_id),
            link_type=link_type,
            user_id=user_id,
        )
    except LinkConflict as exc:
        raise ChangeError("link_refused", 409, reason=exc.reason) from exc
    if written:
        await record_event(
            session,
            ticket_id=change_id,
            event="related_linked",
            user_id=user_id,
            detail={"ticket_id": other_id, "link_type": link_type, **({"via": via} if via else {})},
        )


async def unlink_related(
    session: AsyncSession,
    settings: Settings,
    *,
    ticket_id: int,
    other_ticket_id: int,
    user_id: int,
) -> list[RelatedTicket]:
    """Remove the link between the change and another ticket (404 ``link_not_found`` when the
    two are not linked by the related-link type)."""
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    await _check_pair(
        session, ticket_id=ticket_id, other_ticket_id=other_ticket_id, user_id=user_id
    )
    removed = await unlink_objects(
        session,
        object1="Ticket",
        key1=str(ticket_id),
        object2="Ticket",
        key2=str(other_ticket_id),
        link_type=cfg.related_link_type,
        user_id=user_id,
    )
    if not removed:
        raise ChangeError("link_not_found", 404)
    await record_event(
        session,
        ticket_id=ticket_id,
        event="related_unlinked",
        user_id=user_id,
        detail={"ticket_id": other_ticket_id, "link_type": cfg.related_link_type},
    )
    return await list_related(session, settings, ticket_id=ticket_id, user_id=user_id)


async def create_from_ticket(
    session: AsyncSession,
    settings: Settings,
    sysconfig: SysConfig,
    *,
    source_ticket_id: int,
    data: ChangeCreateIn,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[ChangeView, int | None]:
    """A new change from a ticket the caller may read (``ro``; 404 otherwise, before anything
    else), linked to it with the related-link type.

    What the caller left out is taken from the source: its service (when still valid), the CIs
    linked to it (those the provider shows the caller) and its customer user; the first note
    names the source. Everything else is :func:`create_change`'s, including the ``create``
    permission on the queue, the ``created`` and ``phase`` events and the returned job id.
    """
    await require_change_mode(session, settings)
    cfg = await load_change_settings(session, settings)
    source = await _ticket(session, source_ticket_id, user_id=user_id)

    service_id = data.service_id
    if (
        service_id is None
        and source.service_id is not None
        and source.service_id in await _existing_services(session, [source.service_id])
    ):
        service_id = source.service_id
    ci_refs = list(data.ci_refs)
    provider = get_ci_provider("tiqora", sysconfig)
    if provider is not None:
        for link in (await provider.ticket_links(session, [source.ticket_id], user_id=user_id)).get(
            source.ticket_id, []
        ):
            if link.ci.ref.source == "tiqora":
                ci_refs.append(str(link.ci.ref))
    ci_refs = [str(ref) for ref in _parse_ci_refs(ci_refs)][:50]
    customer = data.customer_user_id or await _customer_user(session, source.ticket_id)
    prefilled = data.model_copy(
        update={
            "from_ticket_id": None,
            "service_id": service_id,
            "ci_refs": ci_refs,
            "customer_user_id": customer,
            "body": data.body or f"Erstellt aus Ticket {source.tn}: {source.title}",
        }
    )
    view, tasks_job_id = await create_change(
        session,
        settings,
        sysconfig,
        data=prefilled,
        user_id=user_id,
        session_factory=session_factory,
    )
    await _link(
        session,
        change_id=view.record.ticket_id,
        other_id=source.ticket_id,
        link_type=cfg.related_link_type,
        user_id=user_id,
        via="create_from",
    )
    return view, tasks_job_id


async def _customer_user(session: AsyncSession, ticket_id: int) -> str | None:
    row = (
        await session.execute(
            text("SELECT customer_user_id FROM ticket WHERE id = :t"), {"t": ticket_id}
        )
    ).first()
    value = None if row is None or row[0] is None else str(row[0]).strip()
    return value or None


__all__ = [
    "RelatedTicket",
    "create_from_ticket",
    "link_related",
    "list_related",
    "unlink_related",
]
