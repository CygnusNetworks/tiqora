"""Phone/CTI call logging: a thin wrapper over add_article with the right
sender/history type (PhoneCallCustomer / PhoneCallAgent), for CTI
integrations (Asterisk AMI/AGI, generic click-to-log) to log a call as an
article on a ticket — creating one via caller-number resolution/follow-up
detection when no ``ticket_id`` is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import (
    channel_setting,
    resolve_customer_by_phone,
    resolve_ticket_for_inbound,
)
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

CHANNEL_NAME = "phone"


@dataclass(frozen=True, slots=True)
class PhoneNoteResult:
    ticket_id: int
    article_id: int
    created: bool


async def log_phone_call(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    direction: str,  # "inbound" | "outbound"
    caller_number: str,
    note: str,
    ticket_id: int | None,
    user_id: int,
    subject: str | None = None,
) -> PhoneNoteResult:
    if direction not in ("inbound", "outbound"):
        raise ValueError("direction must be 'inbound' or 'outbound'")

    created = False
    target_ticket_id = ticket_id
    if target_ticket_id is None:
        customer_no, customer_user_id = await resolve_customer_by_phone(session, caller_number)
        if customer_user_id is None:
            default_cu = await channel_setting(session, CHANNEL_NAME, "default_customer_user")
            customer_user_id = default_cu
            customer_no = default_cu
        title = (
            f"Phone call from {caller_number}"
            if direction == "inbound"
            else f"Phone call to {caller_number}"
        )
        target_ticket_id, created = await resolve_ticket_for_inbound(
            session,
            session_factory,
            sysconfig,
            channel=CHANNEL_NAME,
            body_text=note,
            customer_no=customer_no,
            customer_user_id=customer_user_id,
            title=title,
            user_id=user_id,
        )

    sender_type = "customer" if direction == "inbound" else "agent"
    article = ArticleIn(
        sender_type=sender_type,
        is_visible_for_customer=True,
        subject=subject or (f"Phone call ({direction})"),
        body=note,
        content_type="text/plain; charset=utf-8",
        from_address=caller_number if direction == "inbound" else None,
        to_address=caller_number if direction == "outbound" else None,
        channel=CHANNEL_NAME,
    )
    article_id = await add_article(
        session, ticket_id=target_ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    return PhoneNoteResult(ticket_id=target_ticket_id, article_id=article_id, created=created)
