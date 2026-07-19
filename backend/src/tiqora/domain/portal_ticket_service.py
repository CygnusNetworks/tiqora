"""Portal (customer) ticket read/write orchestration.

Deliberately does **not** use ``tiqora.permissions.engine.PermissionEngine``
(the agent group/role ACL engine) — portal visibility is a much simpler,
parallel scoping rule (Phase 3a subtask 1):

- A customer always sees tickets where ``ticket.customer_user_id`` equals
  their own login (Znuny stores the customer *login string* on the ticket,
  not a numeric FK — see ``tiqora.db.legacy.ticket.Ticket.customer_user_id``).
- If the ``portal.company_tickets_enabled`` tiqora_settings flag is "1", a
  customer additionally sees tickets whose ``customer_id`` matches their own
  ``CustomerUser.customer_id`` or any ``customer_user_customer`` mapping row
  for their login (Znuny's "CustomerGroupSupport" style company-wide ticket
  visibility).

Writes reuse the shared invariant bundle in ``ticket_write_service`` — this
module never inserts ticket/article rows itself, it only resolves the
queue/state/priority ids a customer request implies and enforces the
follow-up reopen/reject semantics (Kernel/System/PostMaster/FollowUp.pm).
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.legacy.article import Article, ArticleDataMime, ArticleSenderType
from tiqora.db.legacy.customer import CustomerUserCustomer
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketLockType,
    TicketPriority,
    TicketState,
    TicketStateType,
)
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.queue_service import age_seconds
from tiqora.domain.schemas import (
    ArticleListItem,
    AttachmentMetaOut,
    PaginatedTickets,
    TicketDetail,
    TicketListItem,
)
from tiqora.domain.settings_store import get_setting
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    add_article,
    change_state,
    create_ticket,
)
from tiqora.storage.backend import AttachmentContent, DbMimeStorage
from tiqora.znuny.sysconfig import SysConfig

# tiqora_settings keys (see docs/compatibility.md uncertainties for Phase 3a)
SETTING_DEFAULT_QUEUE = "portal.default_queue_id"
SETTING_COMPANY_TICKETS = "portal.company_tickets_enabled"
SETTING_FOLLOWUP_REOPEN_STATE = "portal.followup_reopen_state"

# Fallbacks used when the tiqora_settings row is absent.
# Queue id 2 = 'Raw' ("All default incoming tickets") in Znuny's seed data —
# a reasonable default inbound queue for portal-created tickets.
DEFAULT_QUEUE_ID = 2
DEFAULT_FOLLOWUP_REOPEN_STATE = "open"
# System/"root@localhost" user id used by postmaster-style automated inserts
# elsewhere in this codebase (see ticket_write_service defaults). Portal
# writes are not attributable to any Znuny `users` row, so we reuse it as
# create_by/owner for portal-originated tickets/articles.
PORTAL_SYSTEM_USER_ID = 1

# Queue.follow_up_id (see `follow_up_possible` seed table)
FOLLOWUP_POSSIBLE = 1
FOLLOWUP_REJECT = 2
FOLLOWUP_NEW_TICKET = 3


class PortalTicketNotFound(Exception):
    """Ticket/article/attachment id does not exist."""


class PortalTicketAccessDenied(Exception):
    """Ticket exists but is outside the customer's visibility scope."""


class PortalFollowUpRejected(Exception):
    """Queue.follow_up_id == reject: the ticket does not accept follow-ups."""


class PortalInvalidInput(Exception):
    """Caller passed an invalid combination of parameters (e.g. bad queue)."""


class PortalTicketService:
    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        sysconfig: SysConfig,
    ) -> None:
        self._session = session
        self._factory = session_factory
        self._sysconfig = sysconfig
        self._storage = DbMimeStorage(session)

    # -- scope -------------------------------------------------------

    async def _company_tickets_enabled(self) -> bool:
        val = await get_setting(self._session, SETTING_COMPANY_TICKETS)
        return val == "1"

    async def _scope_filter(self, customer: AuthenticatedCustomer) -> ColumnElement[bool]:
        conditions: list[ColumnElement[bool]] = [Ticket.customer_user_id == customer.login]
        if await self._company_tickets_enabled():
            cids: set[str] = {customer.customer_id}
            rows = await self._session.execute(
                select(CustomerUserCustomer.customer_id).where(
                    CustomerUserCustomer.user_id == customer.login
                )
            )
            cids.update(rows.scalars().all())
            cids.discard(None)
            if cids:
                conditions.append(Ticket.customer_id.in_(cids))
        return or_(*conditions)

    async def _get_owned_ticket(self, customer: AuthenticatedCustomer, ticket_id: int) -> Ticket:
        t = (
            await self._session.execute(select(Ticket).where(Ticket.id == ticket_id))
        ).scalar_one_or_none()
        if t is None:
            raise PortalTicketNotFound(ticket_id)
        scope = await self._scope_filter(customer)
        owned = (
            await self._session.execute(select(Ticket.id).where(Ticket.id == ticket_id, scope))
        ).scalar_one_or_none()
        if owned is None:
            raise PortalTicketAccessDenied(ticket_id)
        return t

    # -- lookup maps ---------------------------------------------------

    async def _lookup_maps(self) -> dict[str, dict[int, str]]:
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
        return {
            "state": states,
            "state_type": state_types_by_state,
            "priority": priorities,
            "lock": locks,
            "queue": queues,
        }

    def _to_list_item(self, t: Ticket, maps: dict[str, dict[int, str]]) -> TicketListItem:
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
            owner_login=None,
            owner_name=None,
            customer_id=t.customer_id,
            customer_user_id=t.customer_user_id,
            create_time=t.create_time,
            change_time=t.change_time,
            age_seconds=age_seconds(t.create_time),
            escalation_time=t.escalation_time,
            escalation_response_time=t.escalation_response_time,
            escalation_update_time=t.escalation_update_time,
            escalation_solution_time=t.escalation_solution_time,
            until_time=t.until_time,
        )

    async def _state_id_by_name(self, name: str) -> int | None:
        row = (
            await self._session.execute(select(TicketState.id).where(TicketState.name == name))
        ).first()
        return int(row[0]) if row else None

    async def _priority_id_by_name(self, name: str) -> int | None:
        row = (
            await self._session.execute(
                select(TicketPriority.id).where(TicketPriority.name == name)
            )
        ).first()
        return int(row[0]) if row else None

    async def _lock_id_by_name(self, name: str) -> int | None:
        row = (
            await self._session.execute(
                select(TicketLockType.id).where(TicketLockType.name == name)
            )
        ).first()
        return int(row[0]) if row else None

    # -- reads -----------------------------------------------------------

    async def list_tickets(
        self,
        customer: AuthenticatedCustomer,
        *,
        state_id: int | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> PaginatedTickets:
        scope = await self._scope_filter(customer)
        stmt = select(Ticket).where(scope, Ticket.archive_flag == 0)
        count_stmt = select(func.count()).select_from(Ticket).where(scope, Ticket.archive_flag == 0)
        if state_id is not None:
            stmt = stmt.where(Ticket.ticket_state_id == state_id)
            count_stmt = count_stmt.where(Ticket.ticket_state_id == state_id)
        stmt = stmt.order_by(Ticket.create_time.desc())

        total = int((await self._session.execute(count_stmt)).scalar_one())
        result = await self._session.execute(stmt.offset(offset).limit(min(limit, 200)))
        tickets = list(result.scalars().all())
        maps = await self._lookup_maps()
        items = [self._to_list_item(t, maps) for t in tickets]
        return PaginatedTickets(items=items, total=total, offset=offset, limit=limit)

    async def get_ticket(self, customer: AuthenticatedCustomer, ticket_id: int) -> TicketDetail:
        t = await self._get_owned_ticket(customer, ticket_id)
        maps = await self._lookup_maps()
        base = self._to_list_item(t, maps)
        return TicketDetail(**base.model_dump())

    async def list_visible_articles(
        self, customer: AuthenticatedCustomer, ticket_id: int
    ) -> list[ArticleListItem]:
        await self._get_owned_ticket(customer, ticket_id)
        sender_types = {
            r.id: r.name for r in (await self._session.execute(select(ArticleSenderType))).scalars()
        }
        articles = (
            (
                await self._session.execute(
                    select(Article)
                    .where(Article.ticket_id == ticket_id, Article.is_visible_for_customer == 1)
                    .order_by(Article.id)
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
        out: list[ArticleListItem] = []
        for a in articles:
            m = mime_by_aid.get(a.id)
            out.append(
                ArticleListItem(
                    id=a.id,
                    ticket_id=a.ticket_id,
                    sender_type=sender_types.get(a.article_sender_type_id),
                    sender_type_id=a.article_sender_type_id,
                    communication_channel_id=a.communication_channel_id,
                    is_visible_for_customer=bool(a.is_visible_for_customer),
                    create_time=a.create_time,
                    create_by=a.create_by,
                    subject=m.a_subject if m else None,
                    from_address=m.a_from if m else None,
                    to_address=m.a_to if m else None,
                    content_type=m.a_content_type if m else None,
                    incoming_time=m.incoming_time if m else None,
                )
            )
        return out

    async def list_attachments(
        self, customer: AuthenticatedCustomer, ticket_id: int, article_id: int
    ) -> list[AttachmentMetaOut]:
        await self._get_owned_ticket(customer, ticket_id)
        art = (
            await self._session.execute(
                select(Article.id).where(
                    Article.id == article_id,
                    Article.ticket_id == ticket_id,
                    Article.is_visible_for_customer == 1,
                )
            )
        ).scalar_one_or_none()
        if art is None:
            raise PortalTicketNotFound(article_id)
        atts = await self._storage.list_attachments(article_id)
        return [
            AttachmentMetaOut(
                id=a.id,
                article_id=a.article_id,
                filename=a.filename,
                content_type=a.content_type,
                content_size=a.content_size,
                content_id=a.content_id,
                disposition=a.disposition,
            )
            for a in atts
        ]

    async def get_attachment(
        self, customer: AuthenticatedCustomer, ticket_id: int, attachment_id: int
    ) -> AttachmentContent:
        """Download an attachment by id, scoped to owned ticket + customer-visible article."""
        await self._get_owned_ticket(customer, ticket_id)
        content = await self._storage.get_attachment(attachment_id)
        if content is None:
            raise PortalTicketNotFound(attachment_id)
        art = (
            await self._session.execute(
                select(Article.id).where(
                    Article.id == content.meta.article_id,
                    Article.ticket_id == ticket_id,
                    Article.is_visible_for_customer == 1,
                )
            )
        ).scalar_one_or_none()
        if art is None:
            raise PortalTicketNotFound(attachment_id)
        return content

    # -- writes ------------------------------------------------------------

    async def _resolve_queue(self, queue_id: int | None) -> Queue:
        qid = queue_id
        if qid is None:
            raw = await get_setting(self._session, SETTING_DEFAULT_QUEUE)
            qid = int(raw) if raw else DEFAULT_QUEUE_ID
        queue = (
            await self._session.execute(select(Queue).where(Queue.id == qid, Queue.valid_id == 1))
        ).scalar_one_or_none()
        if queue is None:
            raise PortalInvalidInput(f"queue {qid} does not exist or is invalid")
        return queue

    async def create_ticket(
        self, customer: AuthenticatedCustomer, *, title: str, body: str, queue_id: int | None = None
    ) -> int:
        queue = await self._resolve_queue(queue_id)
        state_id = await self._state_id_by_name("new")
        priority_id = await self._priority_id_by_name("3 normal")
        lock_id = await self._lock_id_by_name("unlock")
        if state_id is None or priority_id is None or lock_id is None:
            raise PortalInvalidInput("Znuny seed data (state/priority/lock) missing")
        params = TicketIn(
            title=title,
            queue_id=queue.id,
            state_id=state_id,
            priority_id=priority_id,
            owner_id=PORTAL_SYSTEM_USER_ID,
            lock_id=lock_id,
            customer_id=customer.customer_id,
            customer_user_id=customer.login,
            article=ArticleIn(
                sender_type="customer",
                is_visible_for_customer=True,
                subject=title,
                body=body,
                channel="note",
            ),
        )
        return await create_ticket(
            self._session,
            self._factory,
            self._sysconfig,
            params=params,
            user_id=PORTAL_SYSTEM_USER_ID,
        )

    async def reply(
        self,
        customer: AuthenticatedCustomer,
        ticket_id: int,
        *,
        body: str,
        subject: str | None = None,
        attachments: list[tuple[str, str, bytes]] | None = None,
    ) -> tuple[int, bool]:
        """Add a customer article; reopen/reject per FollowUp.pm semantics.

        Returns ``(article_id, reopened)``.
        """
        t = await self._get_owned_ticket(customer, ticket_id)
        queue = (
            await self._session.execute(select(Queue).where(Queue.id == t.queue_id))
        ).scalar_one_or_none()

        state_type = (
            await self._session.execute(
                select(TicketStateType.name)
                .join(TicketState, TicketState.type_id == TicketStateType.id)
                .where(TicketState.id == t.ticket_state_id)
            )
        ).scalar_one_or_none()
        is_closed = bool(state_type) and (state_type or "").lower().startswith("close")

        reopened = False
        if is_closed:
            if queue is not None and queue.follow_up_id == FOLLOWUP_REJECT:
                raise PortalFollowUpRejected(ticket_id)
            # follow_up_id == FOLLOWUP_NEW_TICKET ("new ticket") would, in
            # Znuny, spawn a brand-new ticket instead of reopening this one.
            # Phase 3a subtask 1 does not implement that split-ticket flow
            # (POST /tickets/{id}/reply always replies to the *same* ticket);
            # treat it the same as FOLLOWUP_POSSIBLE (reopen) and document
            # this as a known deviation.
            reopen_name = (
                await get_setting(self._session, SETTING_FOLLOWUP_REOPEN_STATE)
                or DEFAULT_FOLLOWUP_REOPEN_STATE
            )
            reopen_state_id = await self._state_id_by_name(reopen_name)
            if reopen_state_id is None:
                reopen_state_id = await self._state_id_by_name(DEFAULT_FOLLOWUP_REOPEN_STATE)
            if reopen_state_id is not None:
                await change_state(
                    self._session,
                    ticket_id=ticket_id,
                    new_state_id=reopen_state_id,
                    user_id=PORTAL_SYSTEM_USER_ID,
                    sysconfig=self._sysconfig,
                )
                reopened = True

        article_id = await add_article(
            self._session,
            ticket_id=ticket_id,
            article=ArticleIn(
                sender_type="customer",
                is_visible_for_customer=True,
                subject=subject or f"Re: {t.title or t.tn}",
                body=body,
                channel="note",
                attachments=attachments or [],
            ),
            user_id=PORTAL_SYSTEM_USER_ID,
            sysconfig=self._sysconfig,
        )
        return article_id, reopened
