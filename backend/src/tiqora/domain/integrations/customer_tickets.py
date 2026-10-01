"""Read-only ticket history of one customer account, for external consumers.

Backs ``GET /api/v1/integrations/customer-tickets`` (netadmin shows it in its
per-account history). The account id is the customer login *without* the
site's contract suffix: ``z50test`` covers ``z50test``, ``z50test#1``,
``z50test#3`` — the separator is the customer-link ``login_suffix_separator``
(:func:`tiqora.domain.customer_link.login_suffix_separators`).
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import datetime
from email.utils import parseaddr

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.context import _is_generic_name, display_name_tokens
from tiqora.ai.models import TiqoraAiQueuePolicy, TiqoraAiTicketState
from tiqora.db.legacy.article import Article, ArticleDataMime, CommunicationChannel
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue, SystemAddress
from tiqora.db.legacy.ticket import Ticket, TicketState, TicketStateType
from tiqora.domain.customer_link import login_suffix_separators
from tiqora.domain.integrations.subject_safety import is_title_pii_free
from tiqora.domain.schemas import CustomerTicketItem, CustomerTicketsOut
from tiqora.permissions.engine import PermissionEngine

logger = structlog.get_logger(__name__)

LOGIN_MAX_LENGTH = 150
EMAIL_CHANNEL = "Email"


class InvalidLogin(ValueError):
    """The ``login`` query parameter is not a bare account id."""


def validate_login(login: str, separators: list[str]) -> None:
    """Raise :class:`InvalidLogin` unless ``login`` is 1..150 characters
    without whitespace, control/format characters or a suffix separator."""
    if not 1 <= len(login) <= LOGIN_MAX_LENGTH:
        raise InvalidLogin(f"login must be 1..{LOGIN_MAX_LENGTH} characters")
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in login):
        raise InvalidLogin("login must not contain whitespace or control characters")
    if any(sep in login for sep in separators):
        raise InvalidLogin("login must be the bare account id, without a contract suffix")


async def list_customer_tickets(
    session: AsyncSession, user_id: int, login: str, *, limit: int = 100
) -> CustomerTicketsOut:
    """Tickets of ``login`` (and its suffixed variants), newest first,
    archived ones included. Tickets in queues where ``user_id`` lacks ``ro``
    are silently left out.

    Raises :class:`InvalidLogin` for a malformed ``login``.
    """
    separators = await login_suffix_separators(session)
    validate_login(login, separators)

    allowed_groups = await PermissionEngine(session).groups_for_permission(user_id, "ro")
    if not allowed_groups:
        return CustomerTicketsOut(login=login, tickets=[])

    # autoescape: "%", "_" and the escape char in login/separator match
    # literally (rendered as LIKE … ESCAPE '/', bound as a parameter).
    login_match = or_(
        Ticket.customer_user_id == login,
        *(Ticket.customer_user_id.startswith(login + sep, autoescape=True) for sep in separators),
    )
    rows = (
        await session.execute(
            select(
                Ticket.id,
                Ticket.tn,
                Ticket.title,
                Ticket.queue_id,
                Ticket.customer_user_id,
                Ticket.create_time,
                Ticket.change_time,
                Queue.name,
                TicketState.name,
                TicketStateType.name,
            )
            .join(Queue, Queue.id == Ticket.queue_id)
            .join(TicketState, TicketState.id == Ticket.ticket_state_id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(Queue.group_id.in_(allowed_groups), login_match)
            .order_by(Ticket.create_time.desc(), Ticket.id.desc())
            .limit(limit)
        )
    ).all()
    if not rows:
        return CustomerTicketsOut(login=login, tickets=[])

    ticket_ids = [r[0] for r in rows]
    customer_user_ids = sorted({r[4] for r in rows if r[4]})
    email_stats = await _email_stats_by_ticket(session, ticket_ids)
    summaries = await _summaries_by_ticket(session, ticket_ids)
    customer_names = await _customer_names(session, customer_user_ids)
    from_names = await _from_names_by_ticket(session, ticket_ids)
    ner_queue_ids = await _ner_queue_ids(session, {r[3] for r in rows})
    identifiers = [login, *customer_user_ids]

    person_names = None
    if ner_queue_ids:
        from tiqora.ai.ner import extract_person_names

        person_names = extract_person_names

    tickets = []
    for (
        ticket_id,
        tn,
        title,
        queue_id,
        customer_user_id,
        create_time,
        change_time,
        queue_name,
        state_name,
        state_type_name,
    ) in rows:
        count, first_time, last_time = email_stats.get(ticket_id, (0, None, None))
        summary, summary_created_at = summaries.get(ticket_id, (None, None))
        tickets.append(
            CustomerTicketItem(
                ticket_id=ticket_id,
                ticket_number=tn,
                title=title,
                title_pii_free=is_title_pii_free(
                    title,
                    known_names=[*customer_names, *from_names.get(ticket_id, ())],
                    identifiers=identifiers,
                    person_names=person_names if queue_id in ner_queue_ids else None,
                ),
                queue=queue_name,
                state=state_name,
                state_type=state_type_name,
                customer_user_id=customer_user_id,
                created=create_time,
                changed=change_time,
                first_article_time=first_time,
                last_article_time=last_time,
                email_count=count,
                summary=summary,
                summary_created_at=summary_created_at,
            )
        )
    return CustomerTicketsOut(login=login, tickets=tickets)


async def _email_stats_by_ticket(
    session: AsyncSession, ticket_ids: list[int]
) -> dict[int, tuple[int, datetime | None, datetime | None]]:
    """``(count, min create_time, max create_time)`` of each ticket's
    customer-visible ``Email`` articles; tickets without one are absent."""
    rows = await session.execute(
        select(
            Article.ticket_id,
            func.count(Article.id),
            func.min(Article.create_time),
            func.max(Article.create_time),
        )
        .join(CommunicationChannel, CommunicationChannel.id == Article.communication_channel_id)
        .where(
            Article.ticket_id.in_(ticket_ids),
            CommunicationChannel.name == EMAIL_CHANNEL,
            Article.is_visible_for_customer == 1,
        )
        .group_by(Article.ticket_id)
    )
    return {tid: (int(count), first, last) for tid, count, first, last in rows.all()}


async def _summaries_by_ticket(
    session: AsyncSession, ticket_ids: list[int]
) -> dict[int, tuple[str, datetime | None]]:
    """Stored AI summary per ticket. ``tiqora_ai_ticket_state`` may be missing
    on Znuny-only deployments — treated as "no summaries" (same guard as
    ``TicketService._ai_summary_ticket_ids``)."""
    try:
        rows = await session.execute(
            select(
                TiqoraAiTicketState.ticket_id,
                TiqoraAiTicketState.summary_body,
                TiqoraAiTicketState.summary_created_at,
            ).where(
                TiqoraAiTicketState.ticket_id.in_(ticket_ids),
                TiqoraAiTicketState.summary_body.is_not(None),
            )
        )
    except DBAPIError:
        logger.debug("tiqora_ai_ticket_state query failed (table missing?)", exc_info=True)
        await session.rollback()
        return {}
    return {tid: (body, created_at) for tid, body, created_at in rows.all()}


async def _ner_queue_ids(session: AsyncSession, queue_ids: set[int]) -> set[int]:
    """Queues whose AI policy has PII masking with NER on — the same switch
    the AI runtime uses. Missing AI tables mean no NER."""
    try:
        rows = await session.execute(
            select(TiqoraAiQueuePolicy.queue_id).where(
                TiqoraAiQueuePolicy.queue_id.in_(queue_ids),
                TiqoraAiQueuePolicy.pii_masking.is_(True),
                TiqoraAiQueuePolicy.pii_ner_enabled.is_(True),
            )
        )
    except DBAPIError:
        logger.debug("tiqora_ai_queue_policy query failed (table missing?)", exc_info=True)
        await session.rollback()
        return set()
    return set(rows.scalars().all())


async def _customer_names(session: AsyncSession, logins: list[str]) -> list[str]:
    """First, last and full names of the matched ``customer_user`` rows."""
    if not logins:
        return []
    rows = await session.execute(
        select(CustomerUser.first_name, CustomerUser.last_name).where(
            CustomerUser.login.in_(logins)
        )
    )
    names: list[str] = []
    for first, last in rows.all():
        names.extend(n for n in (first, last) if n)
        if first and last:
            names.append(f"{first} {last}")
    return [n for n in names if not _is_generic_name(n)]


async def _from_names_by_ticket(
    session: AsyncSession, ticket_ids: list[int]
) -> dict[int, list[str]]:
    """Display-name tokens from each ticket's article ``From`` headers, minus
    our own system addresses (organisation names) and generic role words —
    the same candidates ``tiqora.ai.context.collect_known_names`` builds."""
    own = {
        str(v).strip().lower()
        for v in (await session.execute(select(SystemAddress.value0))).scalars().all()
        if v
    }
    rows = await session.execute(
        select(Article.ticket_id, ArticleDataMime.a_from)
        .join(ArticleDataMime, ArticleDataMime.article_id == Article.id)
        .where(Article.ticket_id.in_(ticket_ids), ArticleDataMime.a_from.is_not(None))
    )
    out: dict[int, list[str]] = defaultdict(list)
    for ticket_id, a_from in rows.all():
        address = parseaddr(a_from or "")[1].strip().lower()
        if address and address in own:
            continue
        out[ticket_id].extend(n for n in display_name_tokens(a_from) if not _is_generic_name(n))
    return dict(out)


__all__ = [
    "InvalidLogin",
    "LOGIN_MAX_LENGTH",
    "list_customer_tickets",
    "validate_login",
]
