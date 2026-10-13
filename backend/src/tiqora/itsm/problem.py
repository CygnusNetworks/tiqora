"""Problem management (I11, problem part): root cause, workaround, known error, linked incidents.

A problem is a ticket of the configured problem type (``ItsmSettings.problem_type``). Root cause
and workaround live in ``tiqora_itsm_problem`` (Tiqora only, deviation D4). Incidents are linked
with ``ParentChild`` (the problem is the source) through :func:`link_objects`, so the link rows,
history and events are exactly what Znuny writes. Closing the linked incidents "with workaround"
is a bulk job (:mod:`tiqora.itsm.linked_articles`); linking is synchronous, capped at
:data:`MAX_LINK_INCIDENTS` targets (F14).

Every function checks the caller's rights itself (``ro`` to read, ``rw`` to change) and raises
:class:`ProblemError`; its ``http_status`` is what the routes answer. Functions never commit.
The ``cfg`` parameter (the loaded :class:`ItsmSettings`) is added to the I11 signatures: the
section needs the application settings to be decrypted, which the domain layer does not hold.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import bindparam, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.ticket_write_service import LinkConflict, link_objects
from tiqora.itsm.linked_articles import (
    DEFAULT_CLOSE_SUBJECT,
    LinkedArticlesPayload,
    enqueue_linked_articles,
    linked_ticket_ids,
)
from tiqora.itsm.models import TiqoraItsmProblem
from tiqora.itsm.settings import ItsmSettings
from tiqora.kb.models import STATE_REVIEW, TiqoraKbArticle, TiqoraKbCategory, TiqoraKbLink
from tiqora.kb.schemas import ArticleIn as KbArticleIn
from tiqora.kb.schemas import ArticleUpdateIn as KbArticleUpdateIn
from tiqora.kb.service import KbService
from tiqora.permissions.engine import PermissionEngine

MAX_LINK_INCIDENTS = 50
MAX_TEXT = 20000
#: Znuny state types of a finished ticket
CLOSED_STATE_TYPES = frozenset({"closed", "removed", "merged"})
KNOWN_ERROR_LANGUAGE = "de"
# Object type of ``TiqoraKbLink`` rows; a name, not a literal, so the no-cross-join guard does not
# read it as the Znuny table ``ticket``.
KB_LINK_OBJECT_TYPE = "ticket"


class ProblemError(Exception):
    """A refused problem operation; ``code`` is the stable API error code."""

    def __init__(self, code: str, http_status: int = 409, **detail: object) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.detail: dict[str, object] = {"code": code, **detail}


class ProblemIn(BaseModel):
    root_cause: str | None = Field(None, max_length=MAX_TEXT)
    workaround: str | None = Field(None, max_length=MAX_TEXT)


class LinkedIncident(BaseModel):
    ticket_id: int
    tn: str
    title: str
    type_name: str
    state: str
    state_type: str
    closed: bool


class ProblemView(BaseModel):
    ticket_id: int
    root_cause: str | None
    workaround: str | None
    known_error: bool
    known_error_article_id: int | None
    #: state of the known-error KB article (``review`` until a reviewer publishes it)
    known_error_article_state: str | None
    known_error_since: datetime | None
    #: linked incident tickets the caller may read, oldest id first
    incidents: list[LinkedIncident]
    change_time: datetime | None
    change_by: int | None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


async def _problem_ticket(
    session: AsyncSession,
    ticket_id: int,
    *,
    user_id: int,
    cfg: ItsmSettings,
    write: bool = False,
) -> tuple[str, str]:
    """``(tn, title)`` of a readable problem ticket.

    Unreadable and missing collapse to 404 (a ticket never shows whether it exists); without
    ``rw`` a ``write`` is 403; a ticket of another type is 409 ``not_a_problem``.
    """
    row = (
        await session.execute(
            text(
                "SELECT t.queue_id, t.tn, t.title, tt.name FROM ticket t"
                " LEFT JOIN ticket_type tt ON tt.id = t.type_id WHERE t.id = :t"
            ),
            {"t": ticket_id},
        )
    ).first()
    engine = PermissionEngine(session)
    if row is None or not await engine.check(user_id, int(row[0]), "ro"):
        raise ProblemError("ticket_not_found", 404)
    if write and not await engine.check(user_id, int(row[0]), "rw"):
        raise ProblemError("forbidden", 403)
    if row[3] != cfg.problem_type:
        raise ProblemError("not_a_problem")
    return str(row[1]), str(row[2])


async def _incident_tickets(
    session: AsyncSession, ticket_ids: Sequence[int], *, user_id: int, cfg: ItsmSettings
) -> list[LinkedIncident]:
    """Incident-typed tickets among ``ticket_ids`` that the user may read (others are absent)."""
    if not ticket_ids:
        return []
    stmt = text(
        "SELECT t.id, t.tn, t.title, t.queue_id, tt.name, ts.name, tst.name FROM ticket t"
        " JOIN ticket_type tt ON tt.id = t.type_id"
        " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
        " WHERE t.id IN :ids AND tt.name IN :types ORDER BY t.id"
    ).bindparams(bindparam("ids", expanding=True), bindparam("types", expanding=True))
    rows = (
        await session.execute(
            stmt,
            {
                "ids": sorted(set(ticket_ids)),
                "types": [cfg.incident_type, cfg.major_incident_type],
            },
        )
    ).all()
    engine = PermissionEngine(session)
    allowed: dict[int, bool] = {}
    out: list[LinkedIncident] = []
    for tid, tn, title, queue_id, type_name, state, state_type in rows:
        qid = int(queue_id)
        if qid not in allowed:
            allowed[qid] = await engine.check(user_id, qid, "ro")
        if allowed[qid]:
            out.append(
                LinkedIncident(
                    ticket_id=int(tid),
                    tn=str(tn),
                    title=str(title),
                    type_name=str(type_name),
                    state=str(state),
                    state_type=str(state_type),
                    closed=str(state_type) in CLOSED_STATE_TYPES,
                )
            )
    return out


async def _view(
    session: AsyncSession, ticket_id: int, *, user_id: int, cfg: ItsmSettings
) -> ProblemView:
    row = await session.get(TiqoraItsmProblem, ticket_id)
    article_state: str | None = None
    if row is not None and row.known_error_article_id is not None:
        article = await session.get(TiqoraKbArticle, row.known_error_article_id)
        article_state = None if article is None else str(article.state)
    incidents = await _incident_tickets(
        session, sorted(await linked_ticket_ids(session, ticket_id)), user_id=user_id, cfg=cfg
    )
    return ProblemView(
        ticket_id=ticket_id,
        root_cause=row.root_cause if row else None,
        workaround=row.workaround if row else None,
        known_error=bool(row.known_error) if row else False,
        known_error_article_id=row.known_error_article_id if row else None,
        known_error_article_state=article_state,
        known_error_since=row.known_error_since if row else None,
        incidents=incidents,
        change_time=row.change_time if row else None,
        change_by=row.change_by if row else None,
    )


async def _row_for_write(session: AsyncSession, ticket_id: int, user_id: int) -> TiqoraItsmProblem:
    row = await session.get(TiqoraItsmProblem, ticket_id)
    if row is not None:
        return row
    row = TiqoraItsmProblem(
        ticket_id=ticket_id,
        known_error=False,
        change_time=_now(),
        change_by=user_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:  # a concurrent first save won
        session.expunge(row)
        existing = await session.get(TiqoraItsmProblem, ticket_id)
        if existing is None:
            raise
        return existing
    return row


async def get_problem(
    session: AsyncSession, ticket_id: int, *, user_id: int, cfg: ItsmSettings
) -> ProblemView:
    """The problem record with its linked incidents. Needs ``ro`` on the ticket."""
    await _problem_ticket(session, ticket_id, user_id=user_id, cfg=cfg)
    return await _view(session, ticket_id, user_id=user_id, cfg=cfg)


async def save_problem(
    session: AsyncSession, ticket_id: int, data: ProblemIn, *, user_id: int, cfg: ItsmSettings
) -> ProblemView:
    """Store root cause and workaround (blank becomes empty). Needs ``rw``; problem tickets only."""
    await _problem_ticket(session, ticket_id, user_id=user_id, cfg=cfg, write=True)
    row = await _row_for_write(session, ticket_id, user_id)
    row.root_cause = _clean(data.root_cause)
    row.workaround = _clean(data.workaround)
    row.change_time = _now()
    row.change_by = user_id
    await session.flush()
    return await _view(session, ticket_id, user_id=user_id, cfg=cfg)


async def link_incidents(
    session: AsyncSession,
    problem_id: int,
    incident_ids: Sequence[int],
    *,
    user_id: int,
    cfg: ItsmSettings,
) -> int:
    """Link incidents to the problem (``problem_link_type``, problem = source); returns how many
    links were new.

    Needs ``rw`` on the problem and ``ro`` on every incident. At most :data:`MAX_LINK_INCIDENTS`
    ids (422). An id the caller may not read, a ticket that is no incident and a missing ticket
    all refuse the whole call with the same 404 ``incident_not_found`` (it must not tell whether
    a ticket exists). A Znuny link conflict (another link type of the same group, the incident
    already the parent) is 409 ``link_conflict``; nothing is written then.
    """
    ids = list(dict.fromkeys(i for i in incident_ids if i != problem_id))
    if not ids:
        raise ProblemError("no_incidents", 422)
    if len(ids) > MAX_LINK_INCIDENTS:
        raise ProblemError("too_many_incidents", 422)
    await _problem_ticket(session, problem_id, user_id=user_id, cfg=cfg, write=True)
    readable = {
        i.ticket_id for i in await _incident_tickets(session, ids, user_id=user_id, cfg=cfg)
    }
    if not set(ids) <= readable:
        raise ProblemError("incident_not_found", 404)
    created = 0
    try:
        async with session.begin_nested():
            for incident_id in ids:
                if await link_objects(
                    session,
                    source_object="Ticket",
                    source_key=str(problem_id),
                    target_object="Ticket",
                    target_key=str(incident_id),
                    link_type=cfg.problem_link_type,
                    user_id=user_id,
                ):
                    created += 1
    except LinkConflict as exc:
        raise ProblemError("link_conflict", reason=str(exc)) from exc
    return created


def known_error_markdown(title: str, root_cause: str | None, workaround: str | None) -> str:
    """The article body: headings in German, the texts as the agent wrote them."""
    parts = [f"# {title}"]
    parts.append("## Ursache\n\n" + (root_cause or "_Noch nicht bekannt._"))
    parts.append("## Workaround\n\n" + (workaround or "_Noch nicht bekannt._"))
    return "\n\n".join(parts) + "\n"


async def publish_known_error(
    session: AsyncSession,
    kb: KbService,
    problem_id: int,
    *,
    user_id: int,
    cfg: ItsmSettings,
) -> int:
    """Create (or update) the known-error KB article and return its id.

    The article goes into ``known_error_kb_category_id`` in state ``review`` (a reviewer
    publishes it; indexing happens there), is linked to the ticket with a ``tiqora_kb_link`` and
    the problem is marked ``known_error``. Calling it again updates the same article; a changed
    article is set back to ``review``. 409 ``known_error_category_missing`` while the category
    is not configured or no longer exists. Needs ``rw``; problem tickets only.
    """
    _tn, title = await _problem_ticket(session, problem_id, user_id=user_id, cfg=cfg, write=True)
    category_id = cfg.known_error_kb_category_id
    category = await session.get(TiqoraKbCategory, category_id) if category_id else None
    if category is None or not category.valid:
        raise ProblemError("known_error_category_missing")
    row = await _row_for_write(session, problem_id, user_id)
    content = known_error_markdown(title, row.root_cause, row.workaround)
    article = (
        await session.get(TiqoraKbArticle, row.known_error_article_id)
        if row.known_error_article_id is not None
        else None
    )
    if article is None:
        article = await kb.create_article(
            user_id,
            KbArticleIn(
                category_id=category_id,
                title=title,
                language=KNOWN_ERROR_LANGUAGE,
                state=STATE_REVIEW,
                content_md=content,
            ),
        )
    elif (
        article.title != title
        or article.content_md != content
        or article.category_id != category_id
        or article.state == "archived"
    ):
        article = await kb.update_article(
            user_id,
            article.id,
            KbArticleUpdateIn(
                category_id=category_id, title=title, content_md=content, state=STATE_REVIEW
            ),
        )
    linked = (
        await session.execute(
            select(TiqoraKbLink.id).where(
                TiqoraKbLink.article_id == article.id,
                TiqoraKbLink.object_type == KB_LINK_OBJECT_TYPE,
                TiqoraKbLink.object_id == problem_id,
            )
        )
    ).first()
    if linked is None:
        session.add(
            TiqoraKbLink(
                article_id=article.id, object_type=KB_LINK_OBJECT_TYPE, object_id=problem_id
            )
        )
    now = _now()
    row.known_error_article_id = article.id
    if not row.known_error:
        row.known_error = True
        row.known_error_since = now
    row.change_time = now
    row.change_by = user_id
    await session.flush()
    return int(article.id)


def default_close_note(title: str, workaround: str | None) -> tuple[str, str]:
    """Subject and HTML body used when the agent gives no note (F29)."""
    subject = f"{DEFAULT_CLOSE_SUBJECT}: {title}"[:250]
    body = f"<p>{html.escape(subject)}</p>"
    if workaround:
        body += "<p>Workaround:</p><p>" + html.escape(workaround).replace("\n", "<br>") + "</p>"
    return subject, body


async def enqueue_close_incidents(
    session: AsyncSession,
    problem_id: int,
    *,
    user_id: int,
    cfg: ItsmSettings,
    note_html: str | None = None,
) -> TiqoraJob:
    """Queue the job that writes a note on every open linked incident and sets the workaround
    state. Needs ``rw`` on the problem; the job checks ``rw``/``note`` per incident.

    The targets are the linked incidents the caller may read that are not yet closed (skip
    reasons in the result collapse to ``no_rw``). 409 ``workaround_state_missing`` when the
    state does not exist, 409 ``no_open_incidents`` when there is nothing to close, 422
    ``too_many_targets`` above ``linked_articles_max_tickets``.
    """
    _tn, title = await _problem_ticket(session, problem_id, user_id=user_id, cfg=cfg, write=True)
    state = (
        await session.execute(
            text("SELECT id FROM ticket_state WHERE name = :n AND valid_id = 1"),
            {"n": cfg.workaround_state},
        )
    ).first()
    if state is None:
        raise ProblemError("workaround_state_missing")
    linked = await _incident_tickets(
        session, sorted(await linked_ticket_ids(session, problem_id)), user_id=user_id, cfg=cfg
    )
    targets = [i.ticket_id for i in linked if not i.closed]
    if not targets:
        raise ProblemError("no_open_incidents")
    if len(targets) > cfg.linked_articles_max_tickets:
        raise ProblemError("too_many_targets", 422)
    row = await session.get(TiqoraItsmProblem, problem_id)
    if _clean(note_html):
        subject = f"{DEFAULT_CLOSE_SUBJECT}: {title}"[:250]
        body = note_html or ""
    else:
        subject, body = default_close_note(title, row.workaround if row else None)
    return await enqueue_linked_articles(
        session,
        LinkedArticlesPayload(
            source_ticket_id=problem_id,
            target_ticket_ids=targets,
            mode="note",
            subject=subject,
            body_html=body,
            visible_for_customer=False,
            set_state_name=cfg.workaround_state,
            origin="problem_close_incidents",
            acting_user_id=user_id,
        ),
    )


__all__ = [
    "CLOSED_STATE_TYPES",
    "MAX_LINK_INCIDENTS",
    "LinkedIncident",
    "ProblemError",
    "ProblemIn",
    "ProblemView",
    "default_close_note",
    "enqueue_close_incidents",
    "get_problem",
    "known_error_markdown",
    "link_incidents",
    "publish_known_error",
    "save_problem",
]
