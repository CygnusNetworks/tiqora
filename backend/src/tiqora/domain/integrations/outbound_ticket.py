"""Ticket with one outgoing customer e-mail, for external callers (netadmin).

Backs ``POST /api/v1/integrations/outbound-ticket``: netadmin sends login
credentials to a new contact as a ticket in a configured queue. The mail
contains a temporary password, so before the article exists AI automation is
paused on the ticket (no extra internal note) and the article raises no agent
notification. Everything runs in one transaction and delivery is
send-then-store: when SMTP fails, nothing stays stored.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.handoff import set_ai_paused
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import Ticket, TicketPriority, TicketState
from tiqora.domain import mail_outbound
from tiqora.domain.schemas import OutboundTicketIn, OutboundTicketOut
from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, TicketWriteService
from tiqora.znuny.sysconfig import SysConfig

STATE_NEW = "new"
STATE_CLOSED = "closed successful"
PRIORITY_NORMAL = "3 normal"


class QueueNotFound(LookupError):
    """No valid queue with the requested name."""


class OutboundMailDisabled(RuntimeError):
    """Outbound mail is switched off; the mail would be stored but never sent."""


async def _named_id(
    session: AsyncSession, model: type[TicketState] | type[TicketPriority], name: str
) -> int:
    found = (await session.execute(select(model.id).where(model.name == name))).scalar_one_or_none()
    if found is None:
        raise LookupError(f"{model.__tablename__} {name!r} missing")
    return int(found)


async def create_outbound_ticket(
    session: AsyncSession,
    sysconfig: SysConfig,
    write_service: TicketWriteService,
    user_id: int,
    req: OutboundTicketIn,
) -> OutboundTicketOut:
    """Create the ticket and send ``req.body`` to ``req.to_address``.

    Raises :class:`QueueNotFound`, :class:`OutboundMailDisabled`, the write
    service's access errors and ``OutboundMailError`` (nothing stored then).
    ``sysconfig`` is the one the write service was built with.
    """
    _ = sysconfig
    queue_id = (
        await session.execute(select(Queue.id).where(Queue.name == req.queue, Queue.valid_id == 1))
    ).scalar_one_or_none()
    if queue_id is None:
        raise QueueNotFound(req.queue)
    # Before any write: with outbound mail off deliver_agent_email_reply would
    # store the article without sending it and the caller would believe it went out.
    if not (await mail_outbound.resolve_outbound_smtp(session)).enabled:
        raise OutboundMailDisabled
    new_id = await _named_id(session, TicketState, STATE_NEW)
    closed_id = await _named_id(session, TicketState, STATE_CLOSED)
    priority_id = await _named_id(session, TicketPriority, PRIORITY_NORMAL)

    # The caller's session starts a transaction implicitly on the selects above.
    if session.in_transaction():
        await session.rollback()
    async with session.begin():
        tid = await write_service.create_ticket(
            user_id,
            TicketIn(
                title=req.subject,
                queue_id=int(queue_id),
                state_id=new_id,
                priority_id=priority_id,
                owner_id=user_id,
                lock_id=1,
                customer_user_id=req.customer_user_id,
            ),
        )
        await set_ai_paused(session, tid, user_id)
        if req.close:
            await write_service.validate_state_change(user_id, tid, closed_id, run_hooks=True)
        article_id = await write_service.add_article(
            user_id,
            tid,
            ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject=req.subject,
                body=req.body,
                content_type="text/plain; charset=utf-8",
                to_address=req.to_address,
                channel="email",
                no_agent_notify=True,
            ),
        )
        if req.close:
            await write_service.change_state(user_id, tid, closed_id, hooks_checked=True)
        tn = (await session.execute(select(Ticket.tn).where(Ticket.id == tid))).scalar_one()
    return OutboundTicketOut(ticket_id=tid, ticket_number=str(tn), article_id=article_id)
