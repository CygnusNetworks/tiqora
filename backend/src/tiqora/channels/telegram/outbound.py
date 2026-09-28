"""Outbound agent Telegram replies (TicketZoom compose / ArticleCreate channel=telegram).

Wires :class:`~tiqora.channels.telegram.gateway.TelegramGateway` into the agent
reply path, mirroring :func:`tiqora.channels.email.outbound_reply.deliver_agent_email_reply`'s
send-then-store semantics: attachments and ``sendMessage`` first, then
:func:`add_article` plus the ``tiqora_telegram_message`` map row — a failed
send leaves no article row (and already-sent parts are deleted again) so the
agent can retry without a false "sent" customer-visible note.

Telegram has no drafts/queues/signatures, so unlike the email path there is no
separate prepare step: chat_id resolution, plaintext extraction, send, and
store all happen in :func:`deliver_agent_telegram_reply`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.common import channel_enabled, channel_setting
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.messages import get_by_article, keyboard_for, record_message
from tiqora.channels.telegram.service import CHANNEL_NAME
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TelegramSendOptions,
    add_article,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# Local-part of the synthetic Telegram address embedded in a_from by both the
# inbound pipeline (service.py) and the store step below: "<chat_id>@telegram.invalid".
_CHAT_ID_RE = re.compile(r"<(-?\d+)@telegram\.invalid>")

# Sent via sendPhoto (compressed inline preview); anything else, and photos
# over Telegram's 10 MB photo limit, go out as documents (limit 50 MB).
_PHOTO_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_PHOTO_MAX_BYTES = 10 * 1024 * 1024

# Stored body of an attachment-only reply (the article needs some body text).
_ATTACHMENT_ONLY_BODY = "[Anhang]"

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


async def resolve_chat_id(session: AsyncSession, ticket_id: int) -> int:
    """Resolve the Telegram chat_id to reply into.

    (1) the ticket's mapped contact (``tiqora_telegram_contact.customer_user_login``
    == ``ticket.customer_user_id``), (2) fallback: the most recent inbound
    Telegram article on the ticket, chat_id parsed from its ``a_from`` local-part.

    Public (not ``_``-prefixed) so other AI-runtime callers (e.g. the
    typing-indicator task in :mod:`tiqora.ai.runtime`) can resolve the same
    chat_id without duplicating this lookup.
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


async def build_gateway(session: AsyncSession) -> TelegramGateway:
    """Construct a :class:`TelegramGateway` from the channel's configured
    ``bot_token`` — shared by :func:`deliver_agent_telegram_reply` and the
    AI-runtime typing-indicator task (:mod:`tiqora.ai.runtime`), which both
    need a gateway when none is injected (production; tests inject a fake)."""
    bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
    if not bot_token:
        raise TelegramDeliveryError("Telegram channel has no bot_token configured")
    return TelegramGateway(bot_token=bot_token)


async def _resolve_quote(
    session: AsyncSession, *, ticket_id: int, chat_id: int, reply_to_article_id: int
) -> int | None:
    """Telegram message id to quote for *reply_to_article_id*, or ``None``.

    The article must belong to this ticket (``InvalidInput`` otherwise — the
    id comes from the client). An article without a map row (a customer
    message from before the map table existed) or one mapped into a different
    chat is not an error: the reply simply goes out unquoted.
    """
    owner = (
        await session.execute(
            text("SELECT ticket_id FROM article WHERE id = :aid"), {"aid": reply_to_article_id}
        )
    ).first()
    if owner is None or int(owner[0]) != ticket_id:
        raise InvalidInput(
            f"reply_to_article_id {reply_to_article_id} is not an article of ticket {ticket_id}"
        )
    row = await get_by_article(session, reply_to_article_id)
    if row is None or row.message_id is None or row.chat_id != chat_id:
        return None
    return int(row.message_id)


async def _ticket_title(session: AsyncSession, ticket_id: int) -> str:
    row = (
        await session.execute(text("SELECT title FROM ticket WHERE id = :tid"), {"tid": ticket_id})
    ).first()
    return str(row[0]) if row is not None and row[0] else ""


def _is_photo(content_type: str, content: bytes) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime in _PHOTO_TYPES and len(content) <= _PHOTO_MAX_BYTES


def _message_id(result: dict[str, Any]) -> int | None:
    raw = result.get("message_id")
    return int(raw) if raw is not None else None


async def _retract(gw: TelegramGateway, chat_id: int, message_ids: list[int]) -> None:
    """Best-effort delete of already-sent parts of a reply that is being
    abandoned. A refused delete is logged, never raised — it must not mask
    the failure that triggered the retraction."""
    for message_id in message_ids:
        try:
            await gw.delete_message(chat_id, message_id)
        except TelegramApiError as exc:
            logger.warning(
                "agent_telegram_retract_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
            )


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

    Order: attachments first (photo or document each), then the text — the
    quote goes on the first message sent, the keyboard on the text (or, with
    an empty body, on the last attachment, which then stands for the whole
    reply in the map table). Everything is sent before the insert.

    Raises :class:`TelegramDeliveryError` when the channel is disabled, has no
    bot_token, the chat_id can't be resolved, or any Telegram send fails — in
    every case no article row is created, and parts already sent are deleted
    again (best effort) so a retry does not show the customer a duplicate.
    ``InvalidInput`` when the quoted article is not on this ticket.
    """
    if not await channel_enabled(session, CHANNEL_NAME):
        raise TelegramDeliveryError("Telegram channel is disabled")

    gw = gateway if gateway is not None else await build_gateway(session)
    chat_id = await resolve_chat_id(session, ticket_id)
    options = article.telegram or TelegramSendOptions()
    buttons = list(options.buttons)
    body = _plaintext_body(article)

    quote_message_id: int | None = None
    if options.reply_to_article_id is not None:
        quote_message_id = await _resolve_quote(
            session,
            ticket_id=ticket_id,
            chat_id=chat_id,
            reply_to_article_id=options.reply_to_article_id,
        )

    # Telegram has no subject, but Znuny needs one on every article. Resolved
    # before sending so no DB read sits between the sends and the insert.
    subject = (
        article.subject if article.subject.strip() else await _ticket_title(session, ticket_id)
    )

    keyboard = keyboard_for(buttons) if buttons else None
    # Without attachments the text is always sent, even when empty, so an
    # empty reply fails at Telegram exactly as it did before attachments.
    send_text = bool(body.strip()) or not article.attachments

    sent_ids: list[int] = []
    attachment_ids: list[int] = []
    text_message_id: int | None = None
    try:
        for index, (filename, content_type, content) in enumerate(article.attachments):
            quote = quote_message_id if index == 0 else None
            carries_keyboard = not send_text and index == len(article.attachments) - 1
            markup = keyboard if carries_keyboard else None
            if _is_photo(content_type, content):
                result = await gw.send_photo(
                    chat_id, content, filename, reply_to_message_id=quote, reply_markup=markup
                )
            else:
                result = await gw.send_document(
                    chat_id,
                    content,
                    filename,
                    content_type,
                    reply_to_message_id=quote,
                    reply_markup=markup,
                )
            message_id = _message_id(result)
            if message_id is not None:
                sent_ids.append(message_id)
                attachment_ids.append(message_id)
        if send_text:
            result = await gw.send_message(
                chat_id,
                body,
                reply_markup=keyboard,
                reply_to_message_id=None if article.attachments else quote_message_id,
            )
            text_message_id = _message_id(result)
            if text_message_id is not None:
                sent_ids.append(text_message_id)
    except TelegramApiError as exc:
        logger.warning(
            "agent_telegram_send_failed", ticket_id=ticket_id, chat_id=chat_id, error=str(exc)
        )
        await _retract(gw, chat_id, sent_ids)
        raise TelegramDeliveryError(f"Telegram send failed: {exc}") from exc

    if send_text:
        row_message_id = text_message_id
        extra_ids = attachment_ids
    else:
        # The last attachment carries the keyboard and stands for the reply.
        row_message_id = attachment_ids[-1] if attachment_ids else None
        extra_ids = attachment_ids[:-1]

    prepared = replace(
        article,
        subject=subject,
        body=body if send_text else _ATTACHMENT_ONLY_BODY,
        channel=CHANNEL_NAME,
        to_address=f"{chat_id}@telegram.invalid",
        is_visible_for_customer=True,
    )
    try:
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=prepared,
            user_id=user_id,
            sysconfig=sysconfig,
        )
        await record_message(
            session,
            article_id=article_id,
            ticket_id=ticket_id,
            chat_id=chat_id,
            message_id=row_message_id,
            direction="out",
            extra_message_ids=extra_ids or None,
            reply_to_article_id=options.reply_to_article_id,
            buttons=buttons or None,
        )
    except Exception:
        # Sent but not stored: take the messages back so the customer does
        # not hold a reply the ticket never shows (and a retry won't repeat it).
        logger.exception("agent_telegram_store_failed", ticket_id=ticket_id, chat_id=chat_id)
        await _retract(gw, chat_id, sent_ids)
        raise
    logger.info(
        "agent_telegram_reply_sent", ticket_id=ticket_id, article_id=article_id, chat_id=chat_id
    )
    return article_id


__all__ = ["TelegramDeliveryError", "deliver_agent_telegram_reply", "resolve_chat_id"]
