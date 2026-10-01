"""Read-only ticket, article, attachment, and history access."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import yaml
from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from tiqora.ai.handoff import ai_escalated_ticket_ids as _ai_escalated_ticket_ids
from tiqora.ai.models import TiqoraAiArticleOrigin, TiqoraAiTicketState
from tiqora.channels.email.parser import get_email_address, split_address_line
from tiqora.crypto.attachment_kind import (
    SNIFF_HEAD_BYTES,
    classify_attachment,
    needs_sniff,
)
from tiqora.db.legacy.article import (
    Article,
    ArticleDataMime,
    ArticleDataMimeAttachment,
    ArticleDataMimePlain,
    ArticleSenderType,
    CommunicationChannel,
)
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import (
    Queue,
    QueueStandardTemplate,
    Service,
    Sla,
    StandardTemplate,
    SystemAddress,
)
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketHistory,
    TicketHistoryType,
    TicketLockType,
    TicketPriority,
    TicketState,
    TicketStateType,
    TicketType,
    TicketWatcher,
)
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraTelegramContact, TiqoraTelegramMessage
from tiqora.domain.article_html import RenderedArticleBody, render_article_body
from tiqora.domain.history_render import render_history_entry
from tiqora.domain.queue_service import OPEN_STATE_TYPES, age_seconds
from tiqora.domain.quoting import (
    build_reply_subject,
    build_ticket_subject,
    html_to_plaintext,
    quote_plaintext_body,
)
from tiqora.domain.schemas import (
    ArticleListItem,
    ArticleSecurity,
    AttachmentMetaOut,
    DynamicFieldValueOut,
    HistoryEntry,
    PaginatedTickets,
    ReplyDraftOut,
    TemplateOut,
    TicketDetail,
    TicketListItem,
    TicketPermissions,
)
from tiqora.domain.subject_hook import load_subject_config
from tiqora.permissions.engine import PERMISSION_KEYS, PermissionEngine
from tiqora.storage.backend import AttachmentContent, AttachmentMeta, DbMimeStorage

if TYPE_CHECKING:
    from tiqora.crypto.article_view import DecryptedView
    from tiqora.crypto.mime_walk import SecurityResult

logger = logging.getLogger(__name__)

#: Body Znuny's EmailParser stores when a mail has no text part (S/MIME, PGP/MIME).
_NO_TEXT_BODY = "- no text message => see attachment -"

#: Named ``state_type`` query-param views resolved to one-or-more
#: ``ticket_state_type.name`` values. Mirrors Znuny's
#: ``Ticket::ViewableStateType`` sysconfig (new + open + pending reminder +
#: pending auto) so the default queue "Offen" view — previously a literal
#: equality match against the single state type named ``open`` — no longer
#: hides freshly-arrived ``new`` tickets. ``"new"`` is also exposed as its
#: own view for the dedicated "Neu" tab. The inbox segments add ``"todo"``
#: (new + open: everything that still needs an agent's action, i.e. the
#: viewable set minus pending) and ``"open_only"`` (literally the ``open``
#: state type, without ``new``). ``"open"`` keeps its viewable-state meaning
#: for backwards compatibility. Any ``state_type`` value not in this map
#: (e.g. ``"closed"``, or a raw ``ticket_state_type.name``) still falls back
#: to a literal single-name match.
VIEW_STATE_TYPES: dict[str, frozenset[str]] = {
    "open": OPEN_STATE_TYPES,
    "new": frozenset({"new"}),
    "pending": frozenset({"pending reminder", "pending auto"}),
    "todo": frozenset({"new", "open"}),
    "open_only": frozenset({"open"}),
}

#: ``ticket.user_id`` of Znuny's root/admin account. Znuny leaves freshly
#: arrived tickets owned by it until an agent takes ownership, so "owned by
#: root" is how "unassigned" is expressed.
UNASSIGNED_OWNER_ID = 1


#: The ticket list's "channel". Znuny has no per-ticket channel, so a ticket
#: that has a message on one of these conversational channels is listed as
#: that channel (its origin: a Telegram ticket may later get an e-mail reply);
#: everything else is "email". "Chat" is Znuny's seeded chat channel, the
#: slot a web chat uses.
LIST_CHANNELS: dict[str, str] = {"Telegram": "telegram", "Chat": "webchat"}
#: A ticket opened by a phone call (its FIRST article is on Znuny's ``Phone``
#: channel) is listed as "phone" -- unless a chat channel above applies.
#: Unlike chats, a later phone call does not change an e-mail ticket's channel.
PHONE_CHANNEL = "Phone"
LIST_CHANNEL_KEYS: tuple[str, ...] = ("email", "telegram", "webchat", "phone")


def _has_article_on(ticket_id_col: Any, channel_names: Sequence[str]) -> ColumnElement[bool]:
    return (
        select(Article.id)
        .join(CommunicationChannel, CommunicationChannel.id == Article.communication_channel_id)
        .where(Article.ticket_id == ticket_id_col, CommunicationChannel.name.in_(channel_names))
        .exists()
    )


def _first_article_on_phone(ticket_id_col: Any) -> ColumnElement[bool]:
    first = aliased(Article)
    # Correlated to the article of the EXISTS below, not to ``ticket_id_col``:
    # auto-correlation only reaches the immediately enclosing SELECT, so a
    # reference to the outer ticket would pull ``ticket`` into this FROM.
    first_id = (
        select(func.min(first.id)).where(first.ticket_id == Article.ticket_id).scalar_subquery()
    )
    return (
        select(Article.id)
        .join(CommunicationChannel, CommunicationChannel.id == Article.communication_channel_id)
        .where(
            Article.ticket_id == ticket_id_col,
            Article.id == first_id,
            CommunicationChannel.name == PHONE_CHANNEL,
        )
        .exists()
    )


def _channel_condition(ticket_id_col: Any, key: str) -> ColumnElement[bool]:
    """``key`` from :data:`LIST_CHANNEL_KEYS` as a condition on a ticket id column."""
    no_chat = ~_has_article_on(ticket_id_col, list(LIST_CHANNELS))
    if key == "email":
        return and_(no_chat, ~_first_article_on_phone(ticket_id_col))
    if key == "phone":
        return and_(no_chat, _first_article_on_phone(ticket_id_col))
    return _has_article_on(ticket_id_col, [n for n, k in LIST_CHANNELS.items() if k == key])


def _escalation_due_before(deadline: int, cols: Sequence[Any] | None = None) -> ColumnElement[bool]:
    """Any ``escalation_*`` epoch set (``> 0``) and earlier than ``deadline``.

    With ``deadline=now`` this is "already escalated"; a later deadline also
    matches tickets that escalate within the window (overdue ones included).
    ``cols`` swaps in the four columns of a subquery over ``ticket``.
    """
    if cols is None:
        cols = (
            Ticket.escalation_time,
            Ticket.escalation_response_time,
            Ticket.escalation_update_time,
            Ticket.escalation_solution_time,
        )
    return or_(*(and_(col > 0, col < deadline) for col in cols))


#: Last activity of a ticket: its newest article's ``create_time``, or the
#: ticket's own ``create_time`` when it has no article yet. A correlated
#: scalar subquery (served by the ``article.ticket_id`` index) rather than a
#: join, so the permission-filtered ``Ticket`` select stays one row per
#: ticket. ``ticket.change_time`` is no substitute: adding an article does
#: not bump it.
_LAST_ACTIVITY = func.coalesce(
    select(func.max(Article.create_time))
    .where(Article.ticket_id == Ticket.id)
    .correlate(Ticket)
    .scalar_subquery(),
    Ticket.create_time,
)

#: Nearest SLA deadline that is set (any ``escalation_*`` epoch ``> 0``);
#: tickets without one sort last in ascending order. ``LEAST`` over CASEs
#: rather than over NULLs because MariaDB's LEAST returns NULL as soon as one
#: argument is NULL, while PostgreSQL skips them.
_NO_DEADLINE = 2**31 - 1
_NEAREST_DEADLINE = func.least(
    *(
        case((col > 0, col), else_=_NO_DEADLINE)
        for col in (
            Ticket.escalation_time,
            Ticket.escalation_response_time,
            Ticket.escalation_update_time,
            Ticket.escalation_solution_time,
        )
    )
)


def _addresses_of(value: str | None) -> set[str]:
    """Lowercased bare email addresses in an address header field."""
    return {
        addr
        for addr in (get_email_address(e).lower() for e in split_address_line(value or ""))
        if addr
    }


def _is_body_part_attachment(a: AttachmentMeta) -> bool:
    """Znuny stores the mail body's MIME alternatives as pseudo-attachments
    (``file-1`` text/plain, ``file-2`` / ``file-1.html`` text/html, plus rows
    flagged via ``content_alternative``). They duplicate the article body and
    are hidden in Znuny's zoom — hide them here too."""
    if a.content_alternative:
        return True
    name = (a.filename or "").lower()
    ctype = (a.content_type or "").lower()
    # ``file-1`` is text/plain for plain mails but text/html for HTML-only
    # mails (no multipart/alternative) — both are body duplicates.
    return name in ("file-1", "file-2", "file-1.html") and (
        ctype.startswith("text/plain") or ctype.startswith("text/html")
    )


def _is_inline_attachment(a: AttachmentMeta) -> bool:
    """cid:-referenced or disposition=inline **image** parts of the HTML body
    (signature logos etc.). A ``content_id``/``inline`` disposition alone is
    not enough — mail clients also attach non-image documents (PDFs, Office
    files) with a Content-ID or ``inline`` disposition, and those must still
    show up as regular attachments rather than being folded into the
    collapsed "inline images" section."""
    is_marked_inline = bool(a.content_id) or (a.disposition or "").lower() == "inline"
    if not is_marked_inline:
        return False
    ctype = (a.content_type or "").split(";", 1)[0].strip().lower()
    return ctype.startswith("image/")


class TicketAccessDenied(Exception):
    """User lacks ro permission on the ticket's queue group."""


class TicketNotFound(Exception):
    """Ticket id does not exist."""


class TicketService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)
        self._storage = DbMimeStorage(session)

    async def _assert_ticket_ro(self, user_id: int, ticket_id: int) -> Ticket:
        result = await self._session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise TicketNotFound(ticket_id)
        if not await self._perms.check(user_id, ticket.queue_id, "ro"):
            raise TicketAccessDenied(ticket_id)
        return ticket

    async def _lookup_maps(self) -> dict[str, Any]:
        states = {
            r.id: r.name for r in (await self._session.execute(select(TicketState))).scalars()
        }
        state_types_by_state: dict[int, str] = {}
        st_rows = await self._session.execute(
            select(TicketState.id, TicketStateType.name).join(
                TicketStateType, TicketStateType.id == TicketState.type_id
            )
        )
        for sid, stname in st_rows.all():
            state_types_by_state[sid] = stname
        priorities = {
            r.id: r.name for r in (await self._session.execute(select(TicketPriority))).scalars()
        }
        locks = {
            r.id: r.name for r in (await self._session.execute(select(TicketLockType))).scalars()
        }
        queues = {r.id: r.name for r in (await self._session.execute(select(Queue))).scalars()}
        users: dict[int, tuple[str, str]] = {
            r.id: (r.login, f"{r.first_name} {r.last_name}".strip())
            for r in (await self._session.execute(select(Users))).scalars()
        }
        return {
            "state": states,
            "state_type": state_types_by_state,
            "priority": priorities,
            "lock": locks,
            "queue": queues,
            "user": users,
        }

    def _to_list_item(
        self,
        t: Ticket,
        maps: dict[str, Any],
        first_from_by_ticket: dict[int, str] | None = None,
        attachment_count_by_ticket: dict[int, int] | None = None,
        ai_summary_ticket_ids: set[int] | None = None,
        customer_email_by_login: dict[str, str] | None = None,
        ai_escalated_ticket_ids: set[int] | None = None,
        ai_reply_source_by_ticket: dict[int, str] | None = None,
        last_article_by_ticket: dict[int, tuple[datetime, str | None]] | None = None,
        channel_by_ticket: dict[int, str] | None = None,
        chat_contact_by_ticket: dict[int, tuple[str | None, str | None]] | None = None,
    ) -> TicketListItem:
        owner = maps["user"].get(t.user_id)
        last_article = (last_article_by_ticket or {}).get(t.id)
        chat_contact = (chat_contact_by_ticket or {}).get(t.id)
        return TicketListItem(
            id=t.id,
            tn=t.tn,
            title=t.title,
            queue_id=t.queue_id,
            queue_name=maps["queue"].get(t.queue_id),
            state_id=t.ticket_state_id,
            state=maps["state"].get(t.ticket_state_id),
            state_type=maps["state_type"].get(t.ticket_state_id),
            priority_id=t.ticket_priority_id,
            priority=maps["priority"].get(t.ticket_priority_id),
            lock_id=t.ticket_lock_id,
            lock=maps["lock"].get(t.ticket_lock_id),
            owner_id=t.user_id,
            owner_login=owner[0] if owner else None,
            owner_name=owner[1] if owner else None,
            customer_id=t.customer_id,
            customer_user_id=t.customer_user_id,
            archive_flag=t.archive_flag,
            customer_email=(
                (customer_email_by_login or {}).get(t.customer_user_id)
                if t.customer_user_id
                else None
            ),
            first_from=(first_from_by_ticket or {}).get(t.id),
            channel=(channel_by_ticket or {}).get(t.id, "email"),
            chat_display_name=chat_contact[0] if chat_contact else None,
            chat_username=chat_contact[1] if chat_contact else None,
            attachment_count=(attachment_count_by_ticket or {}).get(t.id, 0),
            has_ai_summary=t.id in (ai_summary_ticket_ids or set()),
            ai_escalated=t.id in (ai_escalated_ticket_ids or set()),
            ai_reply_source=(ai_reply_source_by_ticket or {}).get(t.id),
            last_article_time=last_article[0] if last_article else None,
            last_sender_type=last_article[1] if last_article else None,
            create_time=t.create_time,
            change_time=t.change_time,
            age_seconds=age_seconds(t.create_time),
            escalation_time=t.escalation_time,
            escalation_response_time=t.escalation_response_time,
            escalation_update_time=t.escalation_update_time,
            escalation_solution_time=t.escalation_solution_time,
            until_time=t.until_time,
        )

    _SORT_COLUMNS: dict[str, Any] = {
        "age": Ticket.create_time,
        "created": Ticket.create_time,
        "changed": Ticket.change_time,
        "tn": Ticket.tn,
        "title": Ticket.title,
        "priority": Ticket.ticket_priority_id,
        "activity": _LAST_ACTIVITY,
        "deadline": _NEAREST_DEADLINE,
    }

    async def _filtered_ticket_stmt(
        self,
        user_id: int,
        *,
        queue_id: int | None,
        state_id: int | None,
        state_type: str | None,
        owner_id: int | None,
        customer_id: str | None = None,
        responsible_id: int | None = None,
        service_id: int | None = None,
        locked: bool | None = None,
        watcher_user_id: int | None = None,
        escalated: bool | None = None,
        ai_escalated: bool | None = None,
        unassigned: bool | None = None,
        escalating_within: int | None = None,
        channel: Sequence[str] | None = None,
        include_archived: bool = False,
    ) -> Select[tuple[Ticket]] | None:
        """Build the permission-filtered, unordered ``Ticket`` select.

        Shared by :meth:`list_tickets` (paginated UI) and
        :meth:`iter_tickets_for_export` (unbounded CSV export) so both apply
        identical ``ro`` permission scoping and query filters. Returns
        ``None`` when the result set is guaranteed empty (no permission, no
        allowed queues, or a ``state_type`` with no matching states) — every
        caller treats ``None`` as "zero rows".
        """
        allowed_groups = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return None

        q_ids_result = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
        )
        allowed_queues = set(q_ids_result.scalars().all())
        if not allowed_queues:
            return None

        if queue_id is not None:
            if queue_id not in allowed_queues:
                return None
            filter_queues = {queue_id}
        else:
            filter_queues = allowed_queues

        stmt = select(Ticket).where(Ticket.queue_id.in_(filter_queues))
        if not include_archived:
            stmt = stmt.where(Ticket.archive_flag == 0)

        if state_id is not None:
            stmt = stmt.where(Ticket.ticket_state_id == state_id)
        if owner_id is not None:
            stmt = stmt.where(Ticket.user_id == owner_id)
        if customer_id is not None:
            stmt = stmt.where(Ticket.customer_id == customer_id)
        if responsible_id is not None:
            stmt = stmt.where(Ticket.responsible_user_id == responsible_id)
        if service_id is not None:
            stmt = stmt.where(Ticket.service_id == service_id)
        if locked is not None:
            lock_rows = await self._session.execute(select(TicketLockType.id, TicketLockType.name))
            lock_map = {name: lid for lid, name in lock_rows.all()}
            locked_ids = {lid for name, lid in lock_map.items() if name in {"lock", "tmp_lock"}}
            unlock_id = lock_map.get("unlock")
            if locked:
                if not locked_ids:
                    return None
                stmt = stmt.where(Ticket.ticket_lock_id.in_(locked_ids))
            else:
                if unlock_id is None:
                    return None
                stmt = stmt.where(Ticket.ticket_lock_id == unlock_id)
        if watcher_user_id is not None:
            watched_ids = (
                (
                    await self._session.execute(
                        select(TicketWatcher.ticket_id).where(
                            TicketWatcher.user_id == watcher_user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not watched_ids:
                return None
            stmt = stmt.where(Ticket.id.in_(set(watched_ids)))
        if unassigned is not None:
            stmt = stmt.where(
                Ticket.user_id == UNASSIGNED_OWNER_ID
                if unassigned
                else Ticket.user_id != UNASSIGNED_OWNER_ID
            )
        if escalated:
            stmt = stmt.where(_escalation_due_before(int(time.time())))
        if escalating_within is not None:
            stmt = stmt.where(_escalation_due_before(int(time.time()) + escalating_within))
        if ai_escalated:
            try:
                ai_ticket_ids = (
                    (
                        await self._session.execute(
                            select(TiqoraAiTicketState.ticket_id).where(
                                TiqoraAiTicketState.ai_escalated_at.is_not(None)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            except DBAPIError:
                logger.debug(
                    "tiqora_ai_ticket_state query failed (table missing?) — treating as no"
                    " AI handoffs",
                    exc_info=True,
                )
                await self._session.rollback()
                ai_ticket_ids = []
            if not ai_ticket_ids:
                return None
            stmt = stmt.where(Ticket.id.in_(set(ai_ticket_ids)))
        if channel:
            stmt = stmt.where(or_(*(_channel_condition(Ticket.id, c) for c in channel)))
        if state_type is not None:
            type_names = VIEW_STATE_TYPES.get(state_type, {state_type})
            state_ids = (
                (
                    await self._session.execute(
                        select(TicketState.id)
                        .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                        .where(TicketStateType.name.in_(type_names))
                    )
                )
                .scalars()
                .all()
            )
            if not state_ids:
                return None
            stmt = stmt.where(Ticket.ticket_state_id.in_(state_ids))

        return stmt

    def _order_by(
        self, stmt: Select[tuple[Ticket]], sort: str, order: str
    ) -> Select[tuple[Ticket]]:
        sort_col = self._SORT_COLUMNS.get(sort, Ticket.create_time)
        # ``Ticket.id`` breaks ties. None of the sort columns is unique -- two
        # tickets created in the same second sort arbitrarily, and since the
        # listing is paginated with OFFSET/LIMIT an arbitrary order lets a row
        # be skipped on one page and repeated on the next.
        if order.lower() == "asc":
            return stmt.order_by(sort_col.asc(), Ticket.id.asc())
        return stmt.order_by(sort_col.desc(), Ticket.id.desc())

    async def list_tickets(
        self,
        user_id: int,
        *,
        queue_id: int | None = None,
        state_id: int | None = None,
        state_type: str | None = None,
        owner_id: int | None = None,
        customer_id: str | None = None,
        responsible_id: int | None = None,
        service_id: int | None = None,
        locked: bool | None = None,
        watcher_user_id: int | None = None,
        escalated: bool | None = None,
        ai_escalated: bool | None = None,
        unassigned: bool | None = None,
        escalating_within: int | None = None,
        channel: Sequence[str] | None = None,
        offset: int = 0,
        limit: int = 50,
        sort: str = "age",
        order: str = "desc",
        include_archived: bool = False,
    ) -> PaginatedTickets:
        stmt = await self._filtered_ticket_stmt(
            user_id,
            queue_id=queue_id,
            state_id=state_id,
            state_type=state_type,
            owner_id=owner_id,
            customer_id=customer_id,
            responsible_id=responsible_id,
            service_id=service_id,
            locked=locked,
            watcher_user_id=watcher_user_id,
            escalated=escalated,
            ai_escalated=ai_escalated,
            unassigned=unassigned,
            escalating_within=escalating_within,
            channel=channel,
            include_archived=include_archived,
        )
        if stmt is None:
            return PaginatedTickets(items=[], total=0, offset=offset, limit=limit)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(count_stmt)).scalar_one())

        ordered = self._order_by(stmt, sort, order)
        result = await self._session.execute(ordered.offset(offset).limit(min(limit, 200)))
        tickets = list(result.scalars().all())
        maps = await self._lookup_maps()
        ticket_ids = [t.id for t in tickets]
        first_from_by_ticket = await self._first_article_from_by_ticket(ticket_ids)
        attachment_count_by_ticket = await self._attachment_counts_by_ticket(ticket_ids)
        ai_summary_ticket_ids = await self._ai_summary_ticket_ids(ticket_ids)
        ai_escalated_ids = await _ai_escalated_ticket_ids(self._session, ticket_ids)
        ai_reply_source_by_ticket = await self._ai_reply_source_by_ticket(ticket_ids)
        last_article_by_ticket = await self._last_article_by_ticket(ticket_ids)
        channel_by_ticket = await self._channel_by_ticket(ticket_ids)
        chat_contact_by_ticket = await self._telegram_contact_by_ticket(ticket_ids)
        customer_email_by_login = await self._customer_emails_by_login(
            [t.customer_user_id for t in tickets if t.customer_user_id]
        )
        items = [
            self._to_list_item(
                t,
                maps,
                first_from_by_ticket,
                attachment_count_by_ticket,
                ai_summary_ticket_ids,
                customer_email_by_login,
                ai_escalated_ids,
                ai_reply_source_by_ticket,
                last_article_by_ticket,
                channel_by_ticket,
                chat_contact_by_ticket,
            )
            for t in tickets
        ]
        return PaginatedTickets(items=items, total=total, offset=offset, limit=limit)

    async def _channel_by_ticket(self, ticket_ids: list[int]) -> dict[int, str]:
        """List channel key per ticket (see :data:`LIST_CHANNELS`); tickets
        without a conversational message are absent (= "email"). With messages
        on several conversational channels, the earliest one wins. Tickets
        without one whose first article is a phone call are "phone"."""
        if not ticket_ids:
            return {}
        rows = await self._session.execute(
            select(Article.ticket_id, CommunicationChannel.name)
            .join(CommunicationChannel, CommunicationChannel.id == Article.communication_channel_id)
            .where(
                Article.ticket_id.in_(ticket_ids),
                CommunicationChannel.name.in_(list(LIST_CHANNELS)),
            )
            .order_by(Article.id)
        )
        out: dict[int, str] = {}
        for ticket_id, name in rows.all():
            out.setdefault(ticket_id, LIST_CHANNELS[name])
        rest = [t for t in ticket_ids if t not in out]
        if rest:
            first_ids = (
                select(func.min(Article.id))
                .where(Article.ticket_id.in_(rest))
                .group_by(Article.ticket_id)
                .scalar_subquery()
            )
            phone_rows = await self._session.execute(
                select(Article.ticket_id)
                .join(
                    CommunicationChannel,
                    CommunicationChannel.id == Article.communication_channel_id,
                )
                .where(Article.id.in_(first_ids), CommunicationChannel.name == PHONE_CHANNEL)
            )
            for (ticket_id,) in phone_rows.all():
                out[ticket_id] = "phone"
        return out

    async def _telegram_contact_by_ticket(
        self, ticket_ids: list[int]
    ) -> dict[int, tuple[str | None, str | None]]:
        """``(display_name, username)`` of the Telegram chat behind each ticket
        (newest message's chat), for the list's sender line. Only for chats
        not linked to a customer user: those tickets belong to the channel's
        shared guest customer, which says nothing about who wrote; a linked
        chat's ticket shows its real customer as usual."""
        if not ticket_ids:
            return {}
        try:
            rows = await self._session.execute(
                select(
                    TiqoraTelegramMessage.ticket_id,
                    TiqoraTelegramContact.display_name,
                    TiqoraTelegramContact.username,
                    TiqoraTelegramContact.customer_user_login,
                )
                .join(
                    TiqoraTelegramContact,
                    TiqoraTelegramContact.chat_id == TiqoraTelegramMessage.chat_id,
                )
                .where(TiqoraTelegramMessage.ticket_id.in_(ticket_ids))
                .order_by(TiqoraTelegramMessage.article_id.desc())
            )
        except DBAPIError:
            logger.debug("tiqora_telegram_* query failed (table missing?)", exc_info=True)
            await self._session.rollback()
            return {}
        out: dict[int, tuple[str | None, str | None]] = {}
        seen: set[int] = set()
        for ticket_id, display_name, username, linked_login in rows.all():
            if ticket_id in seen:
                continue
            seen.add(ticket_id)
            if not linked_login and (display_name or username):
                out[ticket_id] = (display_name, username)
        return out

    async def _first_article_from_by_ticket(self, ticket_ids: list[int]) -> dict[int, str]:
        """Raw ``From`` header of each ticket's first article, keyed by ticket id.

        A single extra query for the page's ticket ids: the first article per
        ticket (``min(article.id)``) joined to its MIME row's ``a_from``.
        Kept out of the main ``Ticket`` select so the permission-filtered
        list/count/export query isn't burdened with a join most callers don't
        need. Tickets without an article (or without a MIME row) are simply
        absent from the returned dict.
        """
        if not ticket_ids:
            return {}
        first_article_ids = (
            select(func.min(Article.id).label("article_id"))
            .where(Article.ticket_id.in_(ticket_ids))
            .group_by(Article.ticket_id)
            .subquery()
        )
        rows = await self._session.execute(
            select(Article.ticket_id, ArticleDataMime.a_from)
            .join(first_article_ids, Article.id == first_article_ids.c.article_id)
            .join(ArticleDataMime, ArticleDataMime.article_id == Article.id)
        )
        return {ticket_id: a_from for ticket_id, a_from in rows.all() if a_from}

    async def _last_article_by_ticket(
        self, ticket_ids: list[int]
    ) -> dict[int, tuple[datetime, str | None]]:
        """``(create_time, sender type name)`` of each ticket's newest article.

        One query for the page's ticket ids (mirrors
        :meth:`_first_article_from_by_ticket`): the articles carrying their
        ticket's ``max(create_time)``, joined to ``article_sender_type``.
        Several articles can share that second — the highest ``article.id``
        among them wins, so "newest" is ``max(create_time, id)``. Internal
        notes count as activity too. Tickets without an article are absent.
        """
        if not ticket_ids:
            return {}
        newest = (
            select(
                Article.ticket_id.label("ticket_id"),
                func.max(Article.create_time).label("max_time"),
            )
            .where(Article.ticket_id.in_(ticket_ids))
            .group_by(Article.ticket_id)
            .subquery()
        )
        rows = await self._session.execute(
            select(Article.ticket_id, Article.id, Article.create_time, ArticleSenderType.name)
            .join(
                newest,
                and_(
                    Article.ticket_id == newest.c.ticket_id,
                    Article.create_time == newest.c.max_time,
                ),
            )
            .outerjoin(ArticleSenderType, ArticleSenderType.id == Article.article_sender_type_id)
            .order_by(Article.ticket_id, Article.id)
        )
        # Ordered by article id, so the last row per ticket wins the tie.
        return {
            int(ticket_id): (create_time, sender)
            for ticket_id, _, create_time, sender in rows.all()
        }

    async def _customer_emails_by_login(self, logins: list[str]) -> dict[str, str]:
        """``customer_user.email`` for a set of ``customer_user.login`` values.

        ``ticket.customer_user_id`` stores the login, which is not always an
        e-mail address itself — a separate lookup against ``customer_user``
        is needed to show the real address next to the customer number.
        Scoped to the page's logins (mirrors ``_first_article_from_by_ticket``)
        rather than loading the whole (potentially huge) customer table.
        """
        if not logins:
            return {}
        rows = await self._session.execute(
            select(CustomerUser.login, CustomerUser.email).where(
                CustomerUser.login.in_(set(logins))
            )
        )
        return {login: email for login, email in rows.all() if email}

    async def _attachment_counts_by_ticket(self, ticket_ids: list[int]) -> dict[int, int]:
        """Count of "real" attachments per ticket, one bulk query for the page.

        SQL approximation of :func:`_is_body_part_attachment` /
        :func:`_is_inline_attachment`: excludes rows with a ``content_id``,
        rows with ``disposition='inline'``, and the ``file-1``/``file-2``/
        ``file-1.html`` body-part pseudo-attachments. A slight mismatch
        against the Python-side filters (e.g. non-image inline-tagged
        documents count as real here in both) is acceptable — this powers a
        list-view badge, not the ticket zoom's attachment panel.
        """
        if not ticket_ids:
            return {}
        rows = await self._session.execute(
            select(Article.ticket_id, func.count(ArticleDataMimeAttachment.id))
            .select_from(ArticleDataMimeAttachment)
            .join(Article, Article.id == ArticleDataMimeAttachment.article_id)
            .where(
                Article.ticket_id.in_(ticket_ids),
                or_(
                    ArticleDataMimeAttachment.content_id.is_(None),
                    ArticleDataMimeAttachment.content_id == "",
                ),
                func.lower(func.coalesce(ArticleDataMimeAttachment.disposition, "")) != "inline",
                ~and_(
                    ArticleDataMimeAttachment.filename.in_(("file-1", "file-2", "file-1.html")),
                    or_(
                        ArticleDataMimeAttachment.content_type.like("text/plain%"),
                        ArticleDataMimeAttachment.content_type.like("text/html%"),
                    ),
                ),
            )
            .group_by(Article.ticket_id)
        )
        return {ticket_id: int(count) for ticket_id, count in rows.all()}

    async def _ai_summary_ticket_ids(self, ticket_ids: list[int]) -> set[int]:
        """Ids (of the page's tickets) that have an AI summary, one bulk query.

        The ``tiqora_ai_ticket_state`` table may not exist in Znuny-only
        fixtures/deployments (Tiqora tables are created lazily by the ai
        worker) — treat a missing-table error as "no summaries" rather than
        failing the whole ticket list.
        """
        if not ticket_ids:
            return set()
        try:
            rows = await self._session.execute(
                select(TiqoraAiTicketState.ticket_id).where(
                    TiqoraAiTicketState.ticket_id.in_(ticket_ids),
                    TiqoraAiTicketState.summary_body.is_not(None),
                )
            )
            return set(rows.scalars().all())
        except DBAPIError:
            logger.debug(
                "tiqora_ai_ticket_state query failed (table missing?) — treating as no summaries",
                exc_info=True,
            )
            await self._session.rollback()
            return set()

    async def _ai_reply_source_by_ticket(self, ticket_ids: list[int]) -> dict[int, str]:
        """Per ticket, how its most recent AI-written article got there.

        ``"auto"`` means the agent sent it itself; ``"manual_accept"`` means a
        human accepted an AI draft. A ticket can have both over its life, so
        the newest article wins — the list answers "how was this last handled",
        which is what an agent scanning a queue is asking.

        Origins are keyed by article, hence the join. Same missing-table
        tolerance as the other AI lookups: a Znuny-only deployment must still
        get its ticket list.
        """
        if not ticket_ids:
            return {}
        try:
            rows = await self._session.execute(
                select(Article.ticket_id, TiqoraAiArticleOrigin.source, Article.id)
                .join(TiqoraAiArticleOrigin, TiqoraAiArticleOrigin.article_id == Article.id)
                .where(Article.ticket_id.in_(ticket_ids))
                .order_by(Article.ticket_id, Article.id)
            )
        except DBAPIError:
            logger.debug(
                "tiqora_ai_article_origin query failed (table missing?)"
                " — treating as no AI replies",
                exc_info=True,
            )
            await self._session.rollback()
            return {}
        # Ordered by article id, so the last row per ticket is the newest.
        return {int(ticket_id): str(source) for ticket_id, source, _ in rows.all()}

    async def iter_tickets_for_export(
        self,
        user_id: int,
        *,
        queue_id: int | None = None,
        state_id: int | None = None,
        state_type: str | None = None,
        owner_id: int | None = None,
        customer_id: str | None = None,
        responsible_id: int | None = None,
        service_id: int | None = None,
        locked: bool | None = None,
        watcher_user_id: int | None = None,
        escalated: bool | None = None,
        ai_escalated: bool | None = None,
        unassigned: bool | None = None,
        escalating_within: int | None = None,
        channel: Sequence[str] | None = None,
        sort: str = "age",
        order: str = "desc",
        batch_size: int = 500,
        include_archived: bool = False,
    ) -> AsyncGenerator[TicketListItem, None]:
        """Yield every matching ticket (no page cap), same filters as ``list_tickets``.

        Streams server-side via ``AsyncSession.stream`` with ``yield_per`` so
        exporting a large queue never buffers the whole result set in memory.
        """
        stmt = await self._filtered_ticket_stmt(
            user_id,
            queue_id=queue_id,
            state_id=state_id,
            state_type=state_type,
            owner_id=owner_id,
            customer_id=customer_id,
            responsible_id=responsible_id,
            service_id=service_id,
            locked=locked,
            watcher_user_id=watcher_user_id,
            escalated=escalated,
            ai_escalated=ai_escalated,
            unassigned=unassigned,
            escalating_within=escalating_within,
            channel=channel,
            include_archived=include_archived,
        )
        if stmt is None:
            return

        ordered = self._order_by(stmt, sort, order).execution_options(yield_per=batch_size)
        maps = await self._lookup_maps()
        result = await self._session.stream(ordered)
        async for ticket in result.scalars():
            yield self._to_list_item(ticket, maps)

    #: State types hidden from the lightweight agent ticket picker (link/merge).
    _SEARCH_EXCLUDED_STATE_TYPES: frozenset[str] = frozenset({"merged", "removed"})

    async def search_tickets(
        self,
        user_id: int,
        *,
        q: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Permission-scoped ticket picker search by number (``tn``) or title.

        Only tickets in queues where the agent has at least ``ro`` are returned.
        Merged/removed tickets are excluded. Limit is capped at 50 (default 20).
        Empty ``q`` yields an empty list (no full dump).
        """
        term = (q or "").strip()
        if not term:
            return []
        limit = max(1, min(int(limit), 50))

        allowed_groups = await self._perms.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return []

        q_ids_result = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(allowed_groups), Queue.valid_id == 1)
        )
        allowed_queues = set(q_ids_result.scalars().all())
        if not allowed_queues:
            return []

        like = f"%{term}%"
        stmt = (
            select(
                Ticket.id,
                Ticket.tn,
                Ticket.title,
                Queue.name,
                TicketState.name,
                TicketStateType.name,
            )
            .join(Queue, Queue.id == Ticket.queue_id)
            .join(TicketState, TicketState.id == Ticket.ticket_state_id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(
                Ticket.queue_id.in_(allowed_queues),
                Ticket.archive_flag == 0,
                TicketStateType.name.notin_(self._SEARCH_EXCLUDED_STATE_TYPES),
                or_(Ticket.tn.ilike(like), Ticket.title.ilike(like)),
            )
            .order_by(Ticket.change_time.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "ticket_id": int(r[0]),
                "tn": r[1] or "",
                "title": r[2] or "",
                "queue": r[3],
                "state": r[4],
                "state_type": r[5],
            }
            for r in rows
        ]

    async def count_owned(self, user_id: int) -> dict[str, int]:
        """Open/new ticket counts for tickets owned by ``user_id``.

        Powers the "My tickets" sidebar badges. Reuses the same permission
        scoping and ``state_type`` view resolution as the ticket list, so the
        numbers agree with what the agent sees after clicking through. Two
        cheap ``COUNT(*)`` queries — no rows or lookup maps are materialised.
        ``open`` uses the viewable-state view (new + open + pending), ``new``
        counts only freshly-arrived tickets.
        """
        counts: dict[str, int] = {"open": 0, "new": 0}
        for view in counts:
            stmt = await self._filtered_ticket_stmt(
                user_id,
                queue_id=None,
                state_id=None,
                state_type=view,
                owner_id=user_id,
            )
            if stmt is None:
                continue
            count_stmt = select(func.count()).select_from(stmt.subquery())
            counts[view] = int((await self._session.execute(count_stmt)).scalar_one())
        return counts

    #: ``states`` keys of :meth:`facet_counts`, each a ``state_type`` view.
    _FACET_STATE_VIEWS: tuple[str, ...] = ("todo", "new", "open_only", "pending", "closed")

    async def facet_counts(
        self,
        user_id: int,
        *,
        queue_id: int | None = None,
        state_id: int | None = None,
        state_type: str | None = None,
        owner_id: int | None = None,
        customer_id: str | None = None,
        responsible_id: int | None = None,
        service_id: int | None = None,
        locked: bool | None = None,
        watcher_user_id: int | None = None,
        escalated: bool | None = None,
        ai_escalated: bool | None = None,
        unassigned: bool | None = None,
        escalating_within: int | None = None,
        channel: Sequence[str] | None = None,
        include_archived: bool = False,
    ) -> dict[str, dict[str, int]]:
        """Segment and chip counts for the inbox, in three ``COUNT`` queries.

        - ``states``: every filter applied *except* ``state_type``/``state_id``,
          counted per view (``todo``/``new``/``open_only``/``pending`` from
          :data:`VIEW_STATE_TYPES`, ``closed`` as the literal state type, as
          the list's fallback matches it) plus ``all`` (no state filter).
          Grouped by ``ticket_state_id`` and folded onto the views in Python.
        - ``flags``: scope filters *and* the state filter applied, but the
          three flag filters (``escalated``/``locked``/``unassigned``)
          ignored — each count is that base plus only its own flag, so a
          chip shows what clicking it would yield. One conditional-sum query.

        - ``channels``: every filter applied except ``channel`` — the count
          per :data:`LIST_CHANNEL_KEYS` value.

        All go through :meth:`_filtered_ticket_stmt`, so the numbers agree
        with the list the segments and chips link to.
        """
        scope: dict[str, Any] = {
            "queue_id": queue_id,
            "owner_id": owner_id,
            "customer_id": customer_id,
            "responsible_id": responsible_id,
            "service_id": service_id,
            "watcher_user_id": watcher_user_id,
            "ai_escalated": ai_escalated,
            "escalating_within": escalating_within,
            "include_archived": include_archived,
        }
        states = dict.fromkeys((*self._FACET_STATE_VIEWS, "all"), 0)
        flags = {"escalated": 0, "locked": 0, "unassigned": 0}
        channels = dict.fromkeys(LIST_CHANNEL_KEYS, 0)

        # Channels first, before ``channel`` joins the scope: every filter
        # applied except the channel filter itself, like the flag chips.
        channel_base = await self._filtered_ticket_stmt(
            user_id,
            state_id=state_id,
            state_type=state_type,
            escalated=escalated,
            locked=locked,
            unassigned=unassigned,
            **scope,
        )
        if channel_base is not None:
            sub = channel_base.subquery()
            row = (
                await self._session.execute(
                    # select_from: without it the EXISTS would carry ``sub`` in
                    # its own FROM and count over the whole scope, not per row.
                    select(
                        *(
                            func.coalesce(
                                func.sum(case((_channel_condition(sub.c.id, k), 1), else_=0)), 0
                            )
                            for k in LIST_CHANNEL_KEYS
                        )
                    ).select_from(sub)
                )
            ).one()
            channels = {k: int(v) for k, v in zip(LIST_CHANNEL_KEYS, row, strict=True)}
        scope["channel"] = channel

        state_base = await self._filtered_ticket_stmt(
            user_id,
            state_id=None,
            state_type=None,
            escalated=escalated,
            locked=locked,
            unassigned=unassigned,
            **scope,
        )
        if state_base is not None:
            sub = state_base.subquery()
            per_state = {
                int(sid): int(n)
                for sid, n in (
                    await self._session.execute(
                        select(sub.c.ticket_state_id, func.count()).group_by(sub.c.ticket_state_id)
                    )
                ).all()
            }
            type_by_state = {
                int(sid): str(name)
                for sid, name in (
                    await self._session.execute(
                        select(TicketState.id, TicketStateType.name).join(
                            TicketStateType, TicketStateType.id == TicketState.type_id
                        )
                    )
                ).all()
            }
            for sid, n in per_state.items():
                states["all"] += n
                type_name = type_by_state.get(sid)
                for view in self._FACET_STATE_VIEWS:
                    if type_name in VIEW_STATE_TYPES.get(view, {view}):
                        states[view] += n

        flag_base = await self._filtered_ticket_stmt(
            user_id, state_id=state_id, state_type=state_type, **scope
        )
        if flag_base is not None:
            lock_ids = (
                (
                    await self._session.execute(
                        select(TicketLockType.id).where(
                            TicketLockType.name.in_({"lock", "tmp_lock"})
                        )
                    )
                )
                .scalars()
                .all()
            )
            sub = flag_base.subquery()
            esc = _escalation_due_before(
                int(time.time()),
                (
                    sub.c.escalation_time,
                    sub.c.escalation_response_time,
                    sub.c.escalation_update_time,
                    sub.c.escalation_solution_time,
                ),
            )
            row = (
                await self._session.execute(
                    select(
                        func.coalesce(func.sum(case((esc, 1), else_=0)), 0),
                        func.coalesce(
                            func.sum(case((sub.c.ticket_lock_id.in_(lock_ids), 1), else_=0)), 0
                        ),
                        func.coalesce(
                            func.sum(case((sub.c.user_id == UNASSIGNED_OWNER_ID, 1), else_=0)),
                            0,
                        ),
                    )
                )
            ).one()
            flags = {"escalated": int(row[0]), "locked": int(row[1]), "unassigned": int(row[2])}

        return {"states": states, "flags": flags, "channels": channels}

    async def count_dashboard_summary(self, user_id: int) -> dict[str, int]:
        """KPI-tile counts for the agent dashboard.

        Reuses the same ``ro`` permission scoping and ``state_type`` view
        resolution as the ticket list (:meth:`_filtered_ticket_stmt`) so each
        tile agrees with the filtered list it links to. Five cheap
        ``COUNT(*)`` queries — no rows or lookup maps are materialised.

        - ``my_open`` / ``my_new``: viewable-open / new tickets owned by the
          agent (same numbers as the "My tickets" sidebar badges).
        - ``unowned_new``: new tickets still owned by root (``owner_id=1``) in
          queues the agent can see — the unclaimed queue to pick up from.
        - ``escalated``: viewable-open tickets whose nearest escalation
          deadline has already passed (any ``escalation_*`` epoch in
          ``(0, now)``).
        - ``ai_escalated``: viewable-open tickets the AI handed off to a
          human (``tiqora_ai_ticket_state.ai_escalated_at`` set) that a human
          has not yet taken back over.
        """
        summary = {"my_open": 0, "my_new": 0, "unowned_new": 0, "escalated": 0, "ai_escalated": 0}

        async def _count(stmt: Select[tuple[Ticket]] | None) -> int:
            if stmt is None:
                return 0
            count_stmt = select(func.count()).select_from(stmt.subquery())
            return int((await self._session.execute(count_stmt)).scalar_one())

        summary["my_open"] = await _count(
            await self._filtered_ticket_stmt(
                user_id, queue_id=None, state_id=None, state_type="open", owner_id=user_id
            )
        )
        summary["my_new"] = await _count(
            await self._filtered_ticket_stmt(
                user_id, queue_id=None, state_id=None, state_type="new", owner_id=user_id
            )
        )
        # "unowned" = still assigned to root (owner_id=1); this is how Znuny
        # leaves freshly-arrived tickets until an agent takes ownership.
        summary["unowned_new"] = await _count(
            await self._filtered_ticket_stmt(
                user_id, queue_id=None, state_id=None, state_type="new", owner_id=1
            )
        )

        esc_stmt = await self._filtered_ticket_stmt(
            user_id, queue_id=None, state_id=None, state_type="open", owner_id=None
        )
        if esc_stmt is not None:
            now = int(time.time())
            esc_stmt = esc_stmt.where(
                or_(
                    and_(Ticket.escalation_time > 0, Ticket.escalation_time < now),
                    and_(
                        Ticket.escalation_response_time > 0, Ticket.escalation_response_time < now
                    ),
                    and_(Ticket.escalation_update_time > 0, Ticket.escalation_update_time < now),
                    and_(
                        Ticket.escalation_solution_time > 0, Ticket.escalation_solution_time < now
                    ),
                )
            )
            summary["escalated"] = await _count(esc_stmt)

        summary["ai_escalated"] = await _count(
            await self._filtered_ticket_stmt(
                user_id,
                queue_id=None,
                state_id=None,
                state_type="open",
                owner_id=None,
                ai_escalated=True,
            )
        )
        return summary

    async def get_ticket(self, user_id: int, ticket_id: int) -> TicketDetail:
        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        maps = await self._lookup_maps()
        customer_email_by_login = await self._customer_emails_by_login(
            [ticket.customer_user_id] if ticket.customer_user_id else []
        )
        base = self._to_list_item(ticket, maps, customer_email_by_login=customer_email_by_login)
        dfs = await self._load_dynamic_fields(ticket.id)
        is_watched = (
            await self._session.execute(
                select(TicketWatcher.user_id).where(
                    TicketWatcher.ticket_id == ticket_id,
                    TicketWatcher.user_id == user_id,
                )
            )
        ).first() is not None
        # One queue_permissions() call + group lookup; rw implies every key.
        permissions = await self._ticket_permissions(user_id, ticket.queue_id)
        can_write = permissions.rw
        type_name = None
        if ticket.type_id is not None:
            type_name = (
                await self._session.execute(
                    select(TicketType.name).where(TicketType.id == ticket.type_id)
                )
            ).scalar_one_or_none()
        service_name = None
        if ticket.service_id is not None:
            service_name = (
                await self._session.execute(
                    select(Service.name).where(Service.id == ticket.service_id)
                )
            ).scalar_one_or_none()
        sla_name = None
        if ticket.sla_id is not None:
            sla_name = (
                await self._session.execute(select(Sla.name).where(Sla.id == ticket.sla_id))
            ).scalar_one_or_none()
        return TicketDetail(
            **base.model_dump(),
            type_id=ticket.type_id,
            type_name=type_name,
            service_id=ticket.service_id,
            service_name=service_name,
            sla_id=ticket.sla_id,
            sla_name=sla_name,
            responsible_user_id=ticket.responsible_user_id,
            create_by=ticket.create_by,
            change_by=ticket.change_by,
            dynamic_fields=dfs,
            is_watched=is_watched,
            can_write=can_write,
            permissions=permissions,
        )

    async def _ticket_permissions(self, user_id: int, queue_id: int) -> TicketPermissions:
        """Effective per-key flags for *user_id* on the group owning *queue_id*."""
        group_id = (
            await self._session.execute(select(Queue.group_id).where(Queue.id == queue_id))
        ).scalar_one_or_none()
        if group_id is None:
            return TicketPermissions()
        by_group = await self._perms.queue_permissions(user_id)
        keys = by_group.get(group_id, set())
        has_rw = "rw" in keys

        def _has(key: str) -> bool:
            return has_rw or key in keys

        return TicketPermissions(
            **{key: _has(key) for key in sorted(PERMISSION_KEYS)},
        )

    async def _load_dynamic_fields(self, ticket_id: int) -> list[DynamicFieldValueOut]:
        fields = (
            (
                await self._session.execute(
                    select(DynamicField).where(
                        DynamicField.object_type == "Ticket",
                        DynamicField.valid_id == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not fields:
            return []
        field_by_id = {f.id: f for f in fields}
        values = (
            (
                await self._session.execute(
                    select(DynamicFieldValue).where(
                        DynamicFieldValue.object_id == ticket_id,
                        DynamicFieldValue.field_id.in_(field_by_id.keys()),
                    )
                )
            )
            .scalars()
            .all()
        )
        grouped: dict[int, list[Any]] = {fid: [] for fid in field_by_id}
        for v in values:
            val: Any
            if v.value_text is not None:
                val = v.value_text
            elif v.value_int is not None:
                val = v.value_int
            elif v.value_date is not None:
                val = v.value_date.isoformat()
            else:
                val = None
            grouped.setdefault(v.field_id, []).append(val)

        out: list[DynamicFieldValueOut] = []
        for fid, field in sorted(field_by_id.items(), key=lambda x: x[1].field_order):
            vals = [v for v in grouped.get(fid, []) if v is not None and str(v) != ""]
            # Hide dynamic fields with no value for this ticket — the ticket
            # zoom omits empty fields (and hides the panel entirely when none
            # have a value). Fields with at least one non-empty value are kept.
            if not vals:
                continue
            # Optionally resolve label overrides from YAML config blob
            label = field.label
            if field.config:
                try:
                    raw_cfg: Any = field.config
                    if isinstance(raw_cfg, bytes):
                        raw_cfg = raw_cfg.decode("utf-8", errors="replace")
                    cfg = yaml.safe_load(raw_cfg)
                    if isinstance(cfg, dict) and cfg.get("Label"):
                        label = str(cfg["Label"])
                except (yaml.YAMLError, TypeError, UnicodeError):
                    pass
            out.append(
                DynamicFieldValueOut(
                    name=field.name,
                    label=label,
                    field_type=field.field_type,
                    values=vals,
                )
            )
        return out

    async def list_articles(self, user_id: int, ticket_id: int) -> list[ArticleListItem]:
        await self._assert_ticket_ro(user_id, ticket_id)
        sender_types = {
            r.id: r.name for r in (await self._session.execute(select(ArticleSenderType))).scalars()
        }
        channel_rows = await self._session.execute(select(CommunicationChannel))
        channel_names = {r.id: r.name for r in channel_rows.scalars()}
        articles = (
            (
                await self._session.execute(
                    select(Article).where(Article.ticket_id == ticket_id).order_by(Article.id)
                )
            )
            .scalars()
            .all()
        )
        if not articles:
            return []
        article_ids = [a.id for a in articles]
        mime_rows = (
            (
                await self._session.execute(
                    select(ArticleDataMime).where(ArticleDataMime.article_id.in_(article_ids))
                )
            )
            .scalars()
            .all()
        )
        mime_by_aid = {m.article_id: m for m in mime_rows}
        origin_ids = set(
            (
                await self._session.execute(
                    select(TiqoraAiArticleOrigin.article_id).where(
                        TiqoraAiArticleOrigin.article_id.in_(article_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

        from tiqora.crypto.article_security import load_security

        security_by_aid = await load_security(self._session, article_ids)

        out: list[ArticleListItem] = []
        for a in articles:
            m = mime_by_aid.get(a.id)
            sec = security_by_aid.get(a.id)
            out.append(
                ArticleListItem(
                    id=a.id,
                    ticket_id=a.ticket_id,
                    sender_type=sender_types.get(a.article_sender_type_id),
                    sender_type_id=a.article_sender_type_id,
                    communication_channel_id=a.communication_channel_id,
                    communication_channel_name=channel_names.get(a.communication_channel_id),
                    is_visible_for_customer=bool(a.is_visible_for_customer),
                    create_time=a.create_time,
                    create_by=a.create_by,
                    subject=m.a_subject if m else None,
                    from_address=m.a_from if m else None,
                    to_address=m.a_to if m else None,
                    content_type=m.a_content_type if m else None,
                    incoming_time=m.incoming_time if m else None,
                    ai_origin=a.id in origin_ids,
                    security=ArticleSecurity.from_result(sec) if sec else None,
                )
            )
        return out

    async def get_article_body(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> RenderedArticleBody:
        rendered, _security = await self.get_article_body_with_security(
            user_id, ticket_id, article_id
        )
        return rendered

    async def get_article_body_with_security(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> tuple[RenderedArticleBody, SecurityResult | None]:
        """Rendered body plus the article's PGP/S-MIME security summary.

        Legacy Znuny articles that are still encrypted are decrypted on view
        (read-only, see :mod:`tiqora.crypto.article_view`).
        """
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        mime = (
            await self._session.execute(
                select(ArticleDataMime).where(ArticleDataMime.article_id == article_id)
            )
        ).scalar_one_or_none()
        body = mime.a_body if mime else None
        ct = mime.a_content_type if mime else "text/plain"
        security, view = await self._crypto_view(user_id, article_id, body)
        if view is not None and view.body is not None:
            body, ct = view.body, view.content_type
        rendered = render_article_body(
            body=body,
            content_type=ct,
            ticket_id=ticket_id,
            article_id=article_id,
        )
        return rendered, security

    async def _crypto_view(
        self,
        user_id: int,
        article_id: int,
        body: str | None,
        attachments: list[AttachmentMeta] | None = None,
    ) -> tuple[SecurityResult | None, DecryptedView | None]:
        """Security flags, and the decrypted view of a still-encrypted legacy article."""
        from tiqora.crypto.article_security import load_security
        from tiqora.crypto.article_view import article_crypto_view, might_need_view

        stripped = (body or "").strip()
        if attachments is None and (
            not stripped or stripped == _NO_TEXT_BODY or "-----BEGIN PGP" in stripped
        ):
            attachments = await self._storage.list_attachments(article_id)
        if attachments is None or not might_need_view(body, attachments):
            return (await load_security(self._session, [article_id])).get(article_id), None
        return await article_crypto_view(
            self._session,
            article_id=article_id,
            body=body,
            attachments=attachments,
            user_id=user_id,
        )

    async def get_article_plain_body(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> RenderedArticleBody:
        """Return ``article_data_mime_plain.body`` when present, else MIME body.

        Znuny keeps a plaintext copy of the article in
        ``article_data_mime_plain`` for search/index; agents may fetch it
        explicitly (parity with article_data_mime_plain consumers).
        """
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        plain = (
            await self._session.execute(
                select(ArticleDataMimePlain).where(ArticleDataMimePlain.article_id == article_id)
            )
        ).scalar_one_or_none()
        if plain is not None and plain.body is not None:
            raw = plain.body
            if isinstance(raw, bytes):
                text_body = raw.decode("utf-8", errors="replace")
            else:
                text_body = str(raw)
            return RenderedArticleBody(
                content_type="text/plain; charset=utf-8",
                is_html=False,
                body=text_body,
            )
        return await self.get_article_body(user_id, ticket_id, article_id)

    async def get_article_ai_origin(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> TiqoraAiArticleOrigin | None:
        """Return the AI-origin row for an article, or ``None`` if the article
        was not auto-sent/accepted by the AI agent (caller maps that to 404).
        """
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        return (
            await self._session.execute(
                select(TiqoraAiArticleOrigin).where(TiqoraAiArticleOrigin.article_id == article_id)
            )
        ).scalar_one_or_none()

    async def list_attachments(
        self, user_id: int, ticket_id: int, article_id: int
    ) -> list[AttachmentMetaOut]:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        atts = await self._storage.list_attachments(article_id)
        body = await self._article_body_text(article_id)
        _security, view = await self._crypto_view(user_id, article_id, body, atts)
        virtual: dict[int, bytes] = {}
        if view is not None and view.body is not None:
            atts = view.attachment_meta(article_id)
            virtual = {
                meta.id: parsed.content for meta, parsed in zip(atts, view.attachments, strict=True)
            }
        out: list[AttachmentMetaOut] = []
        for a in atts:
            if _is_body_part_attachment(a):
                continue
            head: bytes | None = None
            if needs_sniff(a.filename, a.content_type, a.content_size):
                if a.id in virtual:
                    head = virtual[a.id][:SNIFF_HEAD_BYTES]
                else:
                    content = await self._storage.get_attachment(a.id)
                    head = content.content[:SNIFF_HEAD_BYTES] if content else None
            out.append(
                AttachmentMetaOut(
                    id=a.id,
                    article_id=a.article_id,
                    filename=a.filename,
                    content_type=a.content_type,
                    content_size=a.content_size,
                    content_id=a.content_id,
                    disposition=a.disposition,
                    inline=_is_inline_attachment(a),
                    crypto_kind=classify_attachment(a.filename, a.content_type, head),
                )
            )
        return out

    async def get_attachment(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        attachment_id: int,
    ) -> AttachmentContent:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        if attachment_id < 0:
            # Virtual attachment of a decrypted-on-view legacy article.
            view_att = await self._decrypted_attachments(user_id, article_id)
            index = -attachment_id - 1
            if index >= len(view_att):
                raise TicketNotFound(attachment_id)
            return view_att[index]
        content = await self._storage.get_attachment(attachment_id)
        if content is None or content.meta.article_id != article_id:
            raise TicketNotFound(attachment_id)
        return content

    async def _article_body_text(self, article_id: int) -> str | None:
        return (
            await self._session.execute(
                select(ArticleDataMime.a_body).where(ArticleDataMime.article_id == article_id)
            )
        ).scalar_one_or_none()

    async def _decrypted_attachments(
        self, user_id: int, article_id: int
    ) -> list[AttachmentContent]:
        atts = await self._storage.list_attachments(article_id)
        body = await self._article_body_text(article_id)
        _security, view = await self._crypto_view(user_id, article_id, body, atts)
        if view is None or view.body is None:
            return []
        return [
            AttachmentContent(meta=meta, content=parsed.content)
            for meta, parsed in zip(view.attachment_meta(article_id), view.attachments, strict=True)
        ]

    async def get_attachment_by_cid(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        content_id: str,
    ) -> AttachmentContent:
        await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        content = await self._storage.get_by_content_id(article_id, content_id)
        if content is None:
            wanted = content_id.strip("<>")
            for virtual in await self._decrypted_attachments(user_id, article_id):
                if (virtual.meta.content_id or "").strip("<>") == wanted:
                    return virtual
            raise TicketNotFound(content_id)
        return content

    async def list_history(
        self, user_id: int, ticket_id: int, *, order: str = "desc"
    ) -> list[HistoryEntry]:
        await self._assert_ticket_ro(user_id, ticket_id)
        types = {
            r.id: r.name for r in (await self._session.execute(select(TicketHistoryType))).scalars()
        }
        # login-by-user-id map so the renderer can resolve numeric ids in the
        # %% payload (e.g. OwnerUpdate) and each row can show who acted.
        logins: dict[int, str] = {
            r.id: r.login for r in (await self._session.execute(select(Users))).scalars()
        }
        order_col = TicketHistory.id.asc() if order.lower() == "asc" else TicketHistory.id.desc()
        rows = (
            (
                await self._session.execute(
                    select(TicketHistory)
                    .where(TicketHistory.ticket_id == ticket_id)
                    .order_by(order_col)
                )
            )
            .scalars()
            .all()
        )

        def _resolve(uid: int | str | None) -> str | None:
            if uid is None:
                return None
            try:
                return logins.get(int(uid))
            except (TypeError, ValueError):
                return None

        return [
            HistoryEntry(
                id=h.id,
                ticket_id=h.ticket_id,
                name=h.name,
                rendered=render_history_entry(
                    history_type=types.get(h.history_type_id),
                    name=h.name,
                    resolve_user=_resolve,
                ),
                history_type_id=h.history_type_id,
                history_type=types.get(h.history_type_id),
                article_id=h.article_id,
                owner_id=h.owner_id,
                create_time=h.create_time,
                create_by=h.create_by,
                create_by_login=logins.get(h.create_by),
            )
            for h in rows
        ]

    async def _system_addresses(self) -> set[str]:
        """Lowercased addresses of all configured system (queue) addresses.

        Znuny strips these from reply recipients so an answer never loops back
        into the helpdesk itself.
        """
        rows = (await self._session.execute(select(SystemAddress.value0))).scalars().all()
        return {str(v).strip().lower() for v in rows if v and str(v).strip()}

    async def get_reply_draft(
        self, user_id: int, ticket_id: int, article_id: int, *, reply_all: bool = False
    ) -> ReplyDraftOut:
        """Build a prefilled reply draft (Re: subject, To/Cc, quoted body).

        Ports Znuny's reply behaviour (TicketSubjectBuild + TemplateGenerator
        quoting): the answer area is empty and placed ABOVE the quoted
        original. Quoting is plaintext-only (HTML bodies are down-converted);
        see ``tiqora.domain.quoting``.

        Also loads the queue signature (expanded placeholders) for a read-only
        composer preview. The signature is **not** part of ``body`` — the send
        pipeline appends it via ``prepare_outgoing_agent_email``.
        """
        from tiqora.channels.email.outbound_reply import (
            _queue_outbound_meta,
            build_references_chain,
        )
        from tiqora.channels.email.placeholder import expand_placeholders
        from tiqora.znuny.sysconfig import SysConfig

        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        art = (
            await self._session.execute(
                select(Article).where(Article.id == article_id, Article.ticket_id == ticket_id)
            )
        ).scalar_one_or_none()
        if art is None:
            raise TicketNotFound(article_id)
        mime = (
            await self._session.execute(
                select(ArticleDataMime).where(ArticleDataMime.article_id == article_id)
            )
        ).scalar_one_or_none()

        subject = build_reply_subject(mime.a_subject if mime else None)
        # Show the same hooked subject the send path will produce so the
        # composer preview matches the outbound mail (idempotent strip-then-add
        # in prepare_outgoing_agent_email still protects against mismatch).
        sysconfig = SysConfig(self._session)
        hook_cfg = await load_subject_config(self._session, sysconfig)
        if hook_cfg.enabled and ticket.tn:
            subject = build_ticket_subject(
                subject,
                hook=hook_cfg.hook,
                divider=hook_cfg.divider,
                tn=str(ticket.tn),
                subject_format=hook_cfg.subject_format,
                add_re=False,
                add_fwd=False,
            )
        # Znuny (AgentTicketCompose) picks the reply target by sender type: an
        # incoming customer article is answered to its From, an outgoing
        # agent/system article to whoever it was addressed to. Without that
        # distinction, replying inside a ticket the agent created himself would
        # address the agent instead of the customer.
        sender_type = (
            await self._session.execute(
                select(ArticleSenderType.name).where(
                    ArticleSenderType.id == art.article_sender_type_id
                )
            )
        ).scalar_one_or_none()
        outgoing = (sender_type or "").strip().lower() in {"agent", "system"}
        from_addr = (mime.a_from if mime else None) or None
        art_to = (mime.a_to if mime else None) or None
        art_cc = (mime.a_cc if mime else None) or None
        to_addr = (art_to if outgoing else from_addr) or None
        cc: str | None = None
        if reply_all:
            # Reply-all: everyone else on the original as Cc — minus whoever is
            # already in To, minus the article's own sender on an outgoing
            # article, and minus our own system addresses so a reply never
            # loops back into the queue it came from.
            skip = _addresses_of(to_addr)
            if outgoing:
                skip |= _addresses_of(from_addr)
            skip |= await self._system_addresses()
            extras: list[str] = []
            for field in (art_cc,) if outgoing else (art_to, art_cc):
                for entry in split_address_line(field or ""):
                    addr = get_email_address(entry).lower()
                    if not addr or addr in skip:
                        continue
                    skip.add(addr)
                    extras.append(entry)
            cc = ", ".join(extras) or None

        # Telegram reads as a chat, not an email — no "On <date>, X wrote:"
        # quote of the original message (Task: Telegram-Chat-UX). Subject
        # handling above is unaffected; only the quoted body is skipped.
        based_on_channel_name = (
            await self._session.execute(
                select(CommunicationChannel.name).where(
                    CommunicationChannel.id == art.communication_channel_id
                )
            )
        ).scalar_one_or_none()
        if (based_on_channel_name or "").strip() == "Telegram":
            body = ""
        else:
            raw_body = (mime.a_body if mime else None) or ""
            ct = (mime.a_content_type if mime else "text/plain") or "text/plain"
            is_html = ct.split(";", 1)[0].strip().lower() in {
                "text/html",
                "application/xhtml+xml",
            }
            plain = html_to_plaintext(raw_body) if is_html else raw_body
            quoted = quote_plaintext_body(plain, from_address=from_addr, sent_at=art.create_time)
            # Empty answer area above the quote (two newlines), then the quote.
            body = f"\n\n{quoted}\n"

        signature = ""
        signature_is_html = False
        queue_id = int(ticket.queue_id) if ticket.queue_id else 0
        if queue_id:
            _from, queue_name, sig_text, sig_ct = await _queue_outbound_meta(
                self._session, queue_id
            )
            if sig_text and str(sig_text).strip():
                expanded = await expand_placeholders(
                    self._session,
                    sysconfig,
                    str(sig_text),
                    ticket_id=ticket_id,
                    user_id=user_id,
                    queue_name=queue_name or "",
                    customer_subject=subject or "",
                    customer_email_lines=[],
                )
                signature = expanded
                signature_is_html = "html" in (sig_ct or "").lower()

        return ReplyDraftOut(
            to_address=to_addr,
            cc=cc,
            subject=subject,
            body=body,
            is_html=False,
            in_reply_to=(mime.a_message_id if mime else None),
            references=build_references_chain(
                mime.a_references if mime else None,
                mime.a_message_id if mime else None,
            ),
            signature=signature,
            signature_is_html=signature_is_html,
        )

    async def list_templates(
        self,
        user_id: int,
        ticket_id: int,
        template_type: Literal["Answer", "Chat"] = "Answer",
    ) -> list[TemplateOut]:
        """Response templates for a ticket's queue (default template_type='Answer').

        Znuny join: ``queue_standard_template`` → ``standard_template`` on the
        ticket's current ``queue_id``, valid templates of the given type only.
        ``template_type='Chat'`` selects the Telegram chat snippets instead of
        the e-mail Answer templates.

        ``<OTRS_...>`` placeholders are expanded server-side against the ticket
        context (and the acting agent) so the frontend can insert the returned
        text verbatim.
        """
        from tiqora.channels.email.placeholder import expand_placeholders
        from tiqora.znuny.sysconfig import SysConfig

        ticket = await self._assert_ticket_ro(user_id, ticket_id)
        rows = (
            (
                await self._session.execute(
                    select(StandardTemplate)
                    .join(
                        QueueStandardTemplate,
                        QueueStandardTemplate.standard_template_id == StandardTemplate.id,
                    )
                    .where(
                        QueueStandardTemplate.queue_id == ticket.queue_id,
                        StandardTemplate.template_type == template_type,
                        StandardTemplate.valid_id == 1,
                    )
                    .order_by(StandardTemplate.name)
                )
            )
            .scalars()
            .all()
        )
        sysconfig = SysConfig(self._session)
        out: list[TemplateOut] = []
        for r in rows:
            raw = r.text or ""
            expanded = await expand_placeholders(
                self._session,
                sysconfig,
                raw,
                ticket_id=ticket_id,
                user_id=user_id,
            )
            out.append(
                TemplateOut(
                    id=r.id,
                    name=r.name,
                    text=expanded,
                    content_type=r.content_type,
                    template_type=r.template_type,
                )
            )
        return out
