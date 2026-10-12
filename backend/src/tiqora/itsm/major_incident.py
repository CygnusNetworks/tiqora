"""Major incidents (I11, major part): declare, status updates, timeline, banner on linked tickets.

A major incident is a ticket whose type is switched to ``ItsmSettings.major_incident_type``
(through :func:`change_type`, so Znuny's ``TypeUpdate`` history and event are written) plus a
row in ``tiqora_itsm_major_incident``. Ending a major incident only closes that row; the type
stays (Znuny has no "end"). A status update is stored in ``tiqora_itsm_major_incident_update``
(HTML, sanitised on write, F12), always lands as an internal note on the major ticket and, for
the ``note`` and ``reply`` modes, as one background job (:mod:`tiqora.itsm.linked_articles`) that
writes a note, or an e-mail reply, on every linked ticket. Customer replies are refused unless
``major_incident_customer_reply`` is on (deviation D7).

Every function checks the caller's rights itself (``ro`` to read, ``rw`` to change) and raises
:class:`MajorIncidentError`; its ``http_status`` is what the routes answer. Functions never
commit. As for problems, the loaded :class:`ItsmSettings` is passed in as ``cfg``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.article_html import sanitize_html
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketWriteService,
    change_type,
)
from tiqora.itsm.linked_articles import (
    LinkedArticlesPayload,
    enqueue_linked_articles,
    linked_ticket_ids,
)
from tiqora.itsm.models import TiqoraItsmMajorIncident, TiqoraItsmMajorIncidentUpdate
from tiqora.itsm.settings import ItsmSettings
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

UpdateMode = Literal["internal", "note", "reply"]
CHUNK = 500
MAX_SUMMARY = 500
MAX_BODY = 200_000


class MajorIncidentError(Exception):
    """A refused major-incident operation; ``code`` is the stable API error code."""

    def __init__(self, code: str, http_status: int = 409, **detail: object) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.detail: dict[str, object] = {"code": code, **detail}


class TimelineEntry(BaseModel):
    kind: Literal["declared", "update", "ended"]
    at: datetime
    by: int
    #: updates only
    update_id: int | None = None
    mode: UpdateMode | None = None
    body: str | None = None
    target_count: int = 0
    job_id: int | None = None
    #: declared only
    summary: str | None = None


class MajorIncidentView(BaseModel):
    ticket_id: int
    active: bool
    summary: str | None
    declared_at: datetime
    declared_by: int
    ended_at: datetime | None
    ended_by: int | None
    #: declared, the updates (oldest first), ended
    timeline: list[TimelineEntry]


class MajorBanner(BaseModel):
    """What a linked ticket shows: an active major incident it is linked to."""

    ticket_id: int
    tn: str
    title: str
    summary: str | None
    declared_at: datetime


def _now() -> datetime:
    """Naive UTC to the second: MariaDB DATETIME drops fractions; answers must match a re-read."""
    return utc_now().replace(microsecond=0)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def _ticket(
    session: AsyncSession, ticket_id: int, *, user_id: int, write: bool = False
) -> tuple[str, str]:
    """``(tn, title)`` of a readable ticket. Unreadable and missing are both 404; ``write``
    without ``rw`` is 403."""
    row = (
        await session.execute(
            text("SELECT queue_id, tn, title FROM ticket WHERE id = :t"), {"t": ticket_id}
        )
    ).first()
    engine = PermissionEngine(session)
    if row is None or not await engine.check(user_id, int(row[0]), "ro"):
        raise MajorIncidentError("ticket_not_found", 404)
    if write and not await engine.check(user_id, int(row[0]), "rw"):
        raise MajorIncidentError("forbidden", 403)
    return str(row[1]), str(row[2])


async def _readable_ids(
    session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
) -> dict[int, tuple[str, str]]:
    """``{id: (tn, title)}`` of the tickets among ``ticket_ids`` the user may read."""
    ids = sorted(set(ticket_ids))
    engine = PermissionEngine(session)
    allowed: dict[int, bool] = {}
    out: dict[int, tuple[str, str]] = {}
    stmt = text("SELECT id, tn, title, queue_id FROM ticket WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for start in range(0, len(ids), CHUNK):
        for tid, tn, title, queue_id in (
            await session.execute(stmt, {"ids": ids[start : start + CHUNK]})
        ).all():
            qid = int(queue_id)
            if qid not in allowed:
                allowed[qid] = await engine.check(user_id, qid, "ro")
            if allowed[qid]:
                out[int(tid)] = (str(tn), str(title))
    return out


async def _timeline(session: AsyncSession, row: TiqoraItsmMajorIncident) -> list[TimelineEntry]:
    entries = [
        TimelineEntry(kind="declared", at=row.declared_at, by=row.declared_by, summary=row.summary)
    ]
    updates = (
        (
            await session.execute(
                select(TiqoraItsmMajorIncidentUpdate)
                .where(TiqoraItsmMajorIncidentUpdate.ticket_id == row.ticket_id)
                .order_by(TiqoraItsmMajorIncidentUpdate.id)
            )
        )
        .scalars()
        .all()
    )
    for u in updates:
        entries.append(
            TimelineEntry(
                kind="update",
                at=u.created_at,
                by=u.created_by,
                update_id=int(u.id),
                mode=u.mode,  # type: ignore[arg-type]
                body=u.body,
                target_count=int(u.target_count),
                job_id=None if u.job_id is None else int(u.job_id),
            )
        )
    if row.ended_at is not None and row.ended_by is not None:
        entries.append(TimelineEntry(kind="ended", at=row.ended_at, by=row.ended_by))
    return entries


async def _view(session: AsyncSession, row: TiqoraItsmMajorIncident) -> MajorIncidentView:
    return MajorIncidentView(
        ticket_id=int(row.ticket_id),
        active=row.ended_at is None,
        summary=row.summary,
        declared_at=row.declared_at,
        declared_by=int(row.declared_by),
        ended_at=row.ended_at,
        ended_by=None if row.ended_by is None else int(row.ended_by),
        timeline=await _timeline(session, row),
    )


async def _row(session: AsyncSession, ticket_id: int) -> TiqoraItsmMajorIncident:
    row = await session.get(TiqoraItsmMajorIncident, ticket_id)
    if row is None:
        raise MajorIncidentError("no_major_incident", 404)
    return row


async def get_major_incident(
    session: AsyncSession, ticket_id: int, *, user_id: int
) -> MajorIncidentView:
    """The declaration with its timeline. Needs ``ro``; 404 ``no_major_incident`` otherwise."""
    await _ticket(session, ticket_id, user_id=user_id)
    return await _view(session, await _row(session, ticket_id))


async def timeline(session: AsyncSession, ticket_id: int, *, user_id: int) -> list[TimelineEntry]:
    """Declared, the updates, ended - in that order. Needs ``ro``."""
    await _ticket(session, ticket_id, user_id=user_id)
    return await _timeline(session, await _row(session, ticket_id))


async def declare(
    session: AsyncSession,
    ticket_id: int,
    *,
    summary: str | None,
    user_id: int,
    sysconfig: SysConfig,
    cfg: ItsmSettings,
) -> MajorIncidentView:
    """Declare the ticket a major incident. Needs ``rw``.

    Switches the ticket to ``major_incident_type`` (when it is not already; the history is the
    ``TypeUpdate`` entry :func:`change_type` writes) and stores the declaration. Declaring an
    active major incident again changes nothing and returns it. Declaring an ended one starts
    a new run (its old updates are dropped, the articles they created stay). 409
    ``major_type_missing`` while the type does not exist.
    """
    await _ticket(session, ticket_id, user_id=user_id, write=True)
    existing = await session.get(TiqoraItsmMajorIncident, ticket_id)
    if existing is not None and existing.ended_at is None:
        return await _view(session, existing)
    type_id = (
        await session.execute(
            text("SELECT id FROM ticket_type WHERE name = :n AND valid_id = 1"),
            {"n": cfg.major_incident_type},
        )
    ).scalar_one_or_none()
    if type_id is None:
        raise MajorIncidentError("major_type_missing")
    await change_type(
        session,
        ticket_id=ticket_id,
        new_type_id=int(type_id),
        user_id=user_id,
        sysconfig=sysconfig,
    )
    now = _now()
    text_summary = _clean(summary)
    if text_summary is not None:
        text_summary = text_summary[:MAX_SUMMARY]
    if existing is not None:
        await session.execute(
            delete(TiqoraItsmMajorIncidentUpdate).where(
                TiqoraItsmMajorIncidentUpdate.ticket_id == ticket_id
            )
        )
        existing.summary = text_summary
        existing.declared_at = now
        existing.declared_by = user_id
        existing.ended_at = None
        existing.ended_by = None
        row = existing
    else:
        row = TiqoraItsmMajorIncident(
            ticket_id=ticket_id,
            summary=text_summary,
            declared_at=now,
            declared_by=user_id,
        )
        try:
            async with session.begin_nested():
                session.add(row)
        except IntegrityError:  # a concurrent declaration won
            session.expunge(row)
            row = await _row(session, ticket_id)
    await session.flush()
    return await _view(session, row)


async def end(session: AsyncSession, ticket_id: int, *, user_id: int) -> MajorIncidentView:
    """End the active major incident. Needs ``rw``; the ticket keeps its type. 409
    ``not_active`` when there is nothing to end."""
    await _ticket(session, ticket_id, user_id=user_id, write=True)
    row = await _row(session, ticket_id)
    if row.ended_at is not None:
        raise MajorIncidentError("not_active")
    row.ended_at = _now()
    row.ended_by = user_id
    await session.flush()
    return await _view(session, row)


async def post_update(
    session: AsyncSession,
    writer: TicketWriteService,
    ticket_id: int,
    *,
    mode: UpdateMode,
    body_html: str,
    user_id: int,
    cfg: ItsmSettings,
) -> tuple[TiqoraItsmMajorIncidentUpdate, TiqoraJob | None]:
    """Record a status update of the active major incident. Needs ``rw``.

    ``internal``: an internal note on the major ticket only. ``note``/``reply``: that note plus
    one job that writes a note (or an e-mail reply) on every linked ticket the caller may read.
    ``reply`` is 409 ``customer_reply_disabled`` unless the setting is on; 409 ``not_active``
    when the incident is over; 422 ``empty_body`` and ``too_many_targets``. Nothing is written
    when a check fails. The body is sanitised before it is stored.
    """
    _tn, title = await _ticket(session, ticket_id, user_id=user_id, write=True)
    row = await _row(session, ticket_id)
    if row.ended_at is not None:
        raise MajorIncidentError("not_active")
    if mode == "reply" and not cfg.major_incident_customer_reply:
        raise MajorIncidentError("customer_reply_disabled")
    body = sanitize_html(body_html or "")
    if not _clean(_strip_tags(body)):
        raise MajorIncidentError("empty_body", 422)
    targets: list[int] = []
    if mode != "internal":
        linked = await linked_ticket_ids(session, ticket_id)
        targets = sorted(await _readable_ids(session, sorted(linked), user_id=user_id))
        if len(targets) > cfg.linked_articles_max_tickets:
            raise MajorIncidentError("too_many_targets", 422)
    subject = f"Major Incident: {row.summary or title}"[:250]
    await writer.add_article(
        user_id,
        ticket_id,
        ArticleIn(
            sender_type="agent",
            is_visible_for_customer=False,
            subject=subject,
            body=body,
            content_type="text/html; charset=utf-8",
            channel="note",
        ),
    )
    update = TiqoraItsmMajorIncidentUpdate(
        ticket_id=ticket_id,
        created_at=_now(),
        created_by=user_id,
        mode=mode,
        body=body,
        target_count=len(targets),
    )
    session.add(update)
    await session.flush()
    job: TiqoraJob | None = None
    if targets:
        job = await enqueue_linked_articles(
            session,
            LinkedArticlesPayload(
                source_ticket_id=ticket_id,
                target_ticket_ids=targets,
                mode="reply" if mode == "reply" else "note",
                subject=subject,
                body_html=body,
                visible_for_customer=False,
                origin="major_incident_update",
                major_update_id=int(update.id),
                acting_user_id=user_id,
            ),
        )
        update.job_id = int(job.id)
        await session.flush()
    return update, job


def _strip_tags(value: str) -> str:
    return re.sub(r"<[^>]*>", "", value)


async def active_banners(
    session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int
) -> dict[int, MajorBanner]:
    """For each ticket the active major incident it is linked to (the most recently declared
    one the user may read). Tickets without one are absent.

    Link keys come from ``link_relation`` (Znuny), the active declarations from
    ``tiqora_itsm_major_incident`` by IN list, and only major tickets the caller holds ``ro``
    on count. A major ticket shows no banner for itself.
    """
    ids = sorted({int(i) for i in ticket_ids})
    if not ids:
        return {}
    neighbours: dict[int, set[int]] = {i: set() for i in ids}
    wanted = {str(i) for i in ids}
    stmt = text(
        "SELECT lr.source_key, lr.target_key FROM link_relation lr"
        " JOIN link_object lo ON lo.id = lr.source_object_id AND lo.name = 'Ticket'"
        " JOIN link_state ls ON ls.id = lr.state_id AND ls.name = 'Valid'"
        " WHERE lr.target_object_id = lr.source_object_id"
        " AND (lr.source_key IN :keys OR lr.target_key IN :keys)"
    ).bindparams(bindparam("keys", expanding=True))
    for start in range(0, len(ids), CHUNK):
        keys = [str(i) for i in ids[start : start + CHUNK]]
        for source_key, target_key in (await session.execute(stmt, {"keys": keys})).all():
            for mine, other in ((source_key, target_key), (target_key, source_key)):
                if str(mine) in wanted:
                    try:
                        neighbours[int(mine)].add(int(other))
                    except (TypeError, ValueError):
                        continue
    candidates = sorted({o for s in neighbours.values() for o in s})
    if not candidates:
        return {}
    active: list[TiqoraItsmMajorIncident] = []
    for start in range(0, len(candidates), CHUNK):
        active.extend(
            (
                await session.execute(
                    select(TiqoraItsmMajorIncident).where(
                        TiqoraItsmMajorIncident.ticket_id.in_(candidates[start : start + CHUNK]),
                        TiqoraItsmMajorIncident.ended_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    readable = await _readable_ids(session, [int(a.ticket_id) for a in active], user_id=user_id)
    majors = {
        int(a.ticket_id): a for a in sorted(active, key=lambda a: (a.declared_at, a.ticket_id))
    }
    out: dict[int, MajorBanner] = {}
    for ticket_id, linked in neighbours.items():
        usable = [m for m in linked if m in majors and m in readable and m != ticket_id]
        if not usable:
            continue
        best = max(usable, key=lambda m: (majors[m].declared_at, m))
        tn, title = readable[best]
        out[ticket_id] = MajorBanner(
            ticket_id=best,
            tn=tn,
            title=title,
            summary=majors[best].summary,
            declared_at=majors[best].declared_at,
        )
    return out


__all__ = [
    "MajorBanner",
    "MajorIncidentError",
    "MajorIncidentView",
    "TimelineEntry",
    "active_banners",
    "declare",
    "end",
    "get_major_incident",
    "post_update",
    "timeline",
]
