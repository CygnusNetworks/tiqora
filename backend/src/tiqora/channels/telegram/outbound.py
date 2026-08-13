"""Outbound agent Telegram replies (TicketZoom compose / ArticleCreate channel=telegram).

Wires :class:`~tiqora.channels.telegram.gateway.TelegramGateway` into the agent
reply path, mirroring :func:`tiqora.channels.email.outbound_reply.deliver_agent_email_reply`'s
send-then-store semantics: ``sendMessage`` first, then :func:`add_article` — a
failed send leaves no article row so the agent can retry without a false
"sent" customer-visible note.

Telegram has no drafts/queues/signatures, so unlike the email path there is no
separate prepare step: chat_id resolution, plaintext extraction, send, and
store all happen in :func:`deliver_agent_telegram_reply`.
"""

from __future__ import annotations

import re
from dataclasses import replace

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.common import channel_enabled, channel_setting
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.service import CHANNEL_NAME
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# Local-part of the synthetic Telegram address embedded in a_from by both the
# inbound pipeline (service.py) and the store step below: "<chat_id>@telegram.invalid".
_CHAT_ID_RE = re.compile(r"<(-?\d+)@telegram\.invalid>")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")


class TelegramDeliveryError(Exception):
    """Outgoing agent Telegram reply could not be delivered (route -> 409)."""


def _is_html(content_type: str | None) -> bool:
    return "html" in (content_type or "").lower()


def _html_to_text(html: str) -> str:
    """Minimal tag-strip for the Telegram Bot API sendMessage call (no parse_mode)."""
    plain = _TAG_RE.sub(" ", html)
    plain = (
        plain.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    )
    plain = _WS_RE.sub(" ", plain)
    return "\n".join(line.strip() for line in plain.splitlines() if line.strip())


def _plaintext_body(article: ArticleIn) -> str:
    body = article.body or ""
    if _is_html(article.content_type):
        return _html_to_text(body)
    return body


async def _resolve_chat_id(session: AsyncSession, ticket_id: int) -> int:
    """Resolve the Telegram chat_id to reply into.

    (1) the ticket's mapped contact (``tiqora_telegram_contact.customer_user_login``
    == ``ticket.customer_user_id``), (2) fallback: the most recent inbound
    Telegram article on the ticket, chat_id parsed from its ``a_from`` local-part.
    """
    ticket_row = (
        await session.execute(
            text("SELECT customer_user_id FROM ticket WHERE id = :tid"),
            {"tid": ticket_id},
        )
    ).first()
    customer_user_id = str(ticket_row[0]) if ticket_row is not None and ticket_row[0] else None

    if customer_user_id:
        contact_row = (
            await session.execute(
                text(
                    "SELECT chat_id FROM tiqora_telegram_contact"
                    " WHERE customer_user_login = :login LIMIT 1"
                ),
                {"login": customer_user_id},
            )
        ).first()
        if contact_row is not None and contact_row[0] is not None:
            return int(contact_row[0])

    from_row = (
        await session.execute(
            text(
                "SELECT m.a_from FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                " WHERE a.ticket_id = :tid AND st.name = 'customer' AND cc.name = 'Telegram'"
                " ORDER BY a.id DESC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if from_row is not None and from_row[0]:
        match = _CHAT_ID_RE.search(str(from_row[0]))
        if match:
            return int(match.group(1))

    raise TelegramDeliveryError(f"Cannot resolve Telegram chat_id for ticket {ticket_id}")


async def _build_gateway(session: AsyncSession) -> TelegramGateway:
    bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
    if not bot_token:
        raise TelegramDeliveryError("Telegram channel has no bot_token configured")
    return TelegramGateway(bot_token=bot_token)


async def deliver_agent_telegram_reply(
    session: AsyncSession,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    user_id: int,
    article: ArticleIn,
    gateway: TelegramGateway | None = None,
) -> int:
    """Send-then-store an agent's Telegram reply. Returns the new article id.

    Raises :class:`TelegramDeliveryError` when the channel is disabled, has no
    bot_token, the chat_id can't be resolved, or the Telegram send fails — in
    every case no article row is created.
    """
    if not await channel_enabled(session, CHANNEL_NAME):
        raise TelegramDeliveryError("Telegram channel is disabled")

    gw = gateway if gateway is not None else await _build_gateway(session)
    chat_id = await _resolve_chat_id(session, ticket_id)
    body = _plaintext_body(article)

    try:
        await gw.send_message(chat_id, body)
    except TelegramApiError as exc:
        logger.warning(
            "agent_telegram_send_failed", ticket_id=ticket_id, chat_id=chat_id, error=str(exc)
        )
        raise TelegramDeliveryError(f"Telegram send failed: {exc}") from exc

    prepared = replace(
        article,
        body=body,
        channel=CHANNEL_NAME,
        to_address=f"{chat_id}@telegram.invalid",
        is_visible_for_customer=True,
    )
    article_id = await add_article(
        session,
        ticket_id=ticket_id,
        article=prepared,
        user_id=user_id,
        sysconfig=sysconfig,
    )
    logger.info(
        "agent_telegram_reply_sent", ticket_id=ticket_id, article_id=article_id, chat_id=chat_id
    )
    return article_id


__all__ = ["TelegramDeliveryError", "deliver_agent_telegram_reply"]
