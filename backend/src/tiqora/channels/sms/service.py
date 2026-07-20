"""SMS inbound processing + outbound sending, wired to ticket_write_service."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import (
    channel_setting,
    ensure_channel_row,
    resolve_customer_by_phone,
    resolve_ticket_for_inbound,
    verify_shared_secret,
)
from tiqora.channels.sms.gateway import GenericHttpSmsGateway, SmsGateway
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

CHANNEL_NAME = "sms"
COMM_CHANNEL_NAME = "SMS"
COMM_CHANNEL_MODULE = "Tiqora::CommunicationChannel::SMS"


@dataclass(frozen=True, slots=True)
class InboundSmsResult:
    ticket_id: int
    article_id: int
    created: bool
    customer_user_id: str | None


class SmsInboundAuthError(Exception):
    """Shared-secret check failed for an inbound SMS webhook call."""


async def verify_inbound_secret(session: AsyncSession, provided: str | None) -> None:
    expected = await channel_setting(session, CHANNEL_NAME, "inbound_shared_secret")
    if not verify_shared_secret(expected, provided):
        raise SmsInboundAuthError("invalid or missing shared secret")


async def process_inbound_sms(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    from_number: str,
    to_number: str | None,
    body: str,
    user_id: int,
) -> InboundSmsResult:
    """Create/append a ticket for an inbound SMS. Caller commits the session."""
    await ensure_channel_row(session, COMM_CHANNEL_NAME, COMM_CHANNEL_MODULE)

    customer_no, customer_user_id = await resolve_customer_by_phone(session, from_number)
    if customer_user_id is None:
        default_cu = await channel_setting(session, CHANNEL_NAME, "default_customer_user")
        customer_user_id = default_cu
        customer_no = default_cu

    title = f"SMS from {from_number}"
    ticket_id, created = await resolve_ticket_for_inbound(
        session,
        session_factory,
        sysconfig,
        channel=CHANNEL_NAME,
        body_text=body,
        customer_no=customer_no,
        customer_user_id=customer_user_id,
        title=title,
        user_id=user_id,
    )

    article = ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=title if created else "SMS reply",
        body=body,
        content_type="text/plain; charset=utf-8",
        from_address=from_number,
        to_address=to_number,
        channel=CHANNEL_NAME,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    return InboundSmsResult(
        ticket_id=ticket_id,
        article_id=article_id,
        created=created,
        customer_user_id=customer_user_id,
    )


async def build_gateway(session: AsyncSession) -> SmsGateway | None:
    webhook_url = await channel_setting(session, CHANNEL_NAME, "outbound_webhook_url")
    if not webhook_url:
        return None
    secret = await channel_setting(session, CHANNEL_NAME, "outbound_shared_secret")
    return GenericHttpSmsGateway(webhook_url, shared_secret=secret)


async def send_outbound_sms(
    session: AsyncSession,
    sysconfig: SysConfig,
    gateway: SmsGateway,
    *,
    ticket_id: int,
    to_number: str,
    body: str,
    user_id: int,
) -> int:
    """Record an agent-authored outbound SMS article, then deliver via *gateway*."""
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="SMS reply",
        body=body,
        content_type="text/plain; charset=utf-8",
        to_address=to_number,
        channel=CHANNEL_NAME,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    await gateway.send(to=to_number, body=body)
    return article_id
