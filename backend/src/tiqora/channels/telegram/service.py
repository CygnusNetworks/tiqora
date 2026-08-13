"""Telegram inbound update processing, wired to ticket_write_service.

``process_update`` handles a single Telegram Bot API update (one ``message``)
and is shared by both transports Task 3 adds (long-poll daemon, webhook
route) — neither of those exist yet, this module only turns an update dict
into a ticket/article.

Ticket resolution deliberately does *not* reuse
:func:`tiqora.channels.common.resolve_ticket_for_inbound` — Telegram chat_ids
are not looked up against ``customer_user`` the way phone numbers are, so an
unmapped contact (the common case: a Telegram user who never linked a portal
account) always resolves to the same ``default_customer_user``. Blindly
reusing the generic "most recent open ticket for this customer_user" fallback
would then merge different Telegram users' messages into one ticket. Instead
we key off the chat via an ``a_from`` marker embedded in every outbound
article's From address (``<chat_id>@telegram.invalid>``), and only fall back
to the customer_user-based lookup once a contact has a *real* mapped login.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import channel_setting, ensure_channel_row
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.db.tiqora.models import TiqoraTelegramContact
from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, add_article, create_ticket
from tiqora.znuny.followup import detect_followup
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

CHANNEL_NAME = "telegram"
COMM_CHANNEL_NAME = "Telegram"
COMM_CHANNEL_MODULE = "Tiqora::CommunicationChannel::Telegram"

# Telegram media message keys that carry a downloadable attachment, in the
# order they're checked (a message has at most one of these).
_MEDIA_PLACEHOLDERS: dict[str, str] = {
    "photo": "[Foto]",
    "voice": "[Sprachnachricht]",
    "video": "[Video]",
    "sticker": "[Sticker]",
}


async def _lookup_id(session: AsyncSession, table: str, name_col: str, value: str) -> int | None:
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE {name_col} = :v LIMIT 1"), {"v": value}
        )
    ).first()
    return int(row[0]) if row is not None else None


def _pick_media(message: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return ``(kind, file_id, filename, content_type_hint)`` for the first
    supported attachment on *message*, or ``None``."""
    photo = message.get("photo")
    if photo:
        largest = max(photo, key=lambda p: p.get("file_size") or 0)
        return "photo", str(largest["file_id"]), "photo.jpg", "image/jpeg"
    document = message.get("document")
    if document:
        name = str(document.get("file_name") or "document")
        ct = str(document.get("mime_type") or "application/octet-stream")
        return "document", str(document["file_id"]), name, ct
    voice = message.get("voice")
    if voice:
        ct = str(voice.get("mime_type") or "audio/ogg")
        return "voice", str(voice["file_id"]), "voice.ogg", ct
    video = message.get("video")
    if video:
        name = str(video.get("file_name") or "video.mp4")
        ct = str(video.get("mime_type") or "video/mp4")
        return "video", str(video["file_id"]), name, ct
    sticker = message.get("sticker")
    if sticker:
        ext = "webm" if sticker.get("is_video") else "webp"
        ct = "video/webm" if sticker.get("is_video") else "image/webp"
        return "sticker", str(sticker["file_id"]), f"sticker.{ext}", ct
    return None


def _extract_body(message: dict[str, Any]) -> tuple[str, tuple[str, str, str, str] | None]:
    """Return ``(body_text, media)``. *body_text* is the message/caption text,
    or a placeholder like ``"[Foto]"``/``"[Dokument: name]"`` when the message
    carries only media."""
    text_value = message.get("text") or message.get("caption")
    media = _pick_media(message)
    if text_value:
        return str(text_value), media
    if media is not None:
        kind, _file_id, filename, _ct = media
        if kind == "document":
            return f"[Dokument: {filename}]", media
        return _MEDIA_PLACEHOLDERS[kind], media
    return "", None


async def _upsert_contact(session: AsyncSession, message: dict[str, Any]) -> TiqoraTelegramContact:
    """Insert or refresh the ``tiqora_telegram_contact`` row for this chat.

    ``customer_user_login`` is a manual/admin-set mapping and is never
    touched here.
    """
    chat = message.get("chat") or {}
    frm = message.get("from") or {}
    chat_id = int(chat["id"])
    telegram_user_id = frm.get("id")
    username = frm.get("username")
    first_name = str(frm.get("first_name") or "").strip()
    last_name = str(frm.get("last_name") or "").strip()
    display_name = " ".join(part for part in (first_name, last_name) if part) or None

    row = (
        await session.execute(
            select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == chat_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = TiqoraTelegramContact(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            username=username,
            display_name=display_name,
        )
        session.add(row)
    else:
        if telegram_user_id is not None:
            row.telegram_user_id = telegram_user_id
        row.username = username
        row.display_name = display_name
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.flush()
    return row


async def _resolve_ticket(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    chat_id: int,
    body_text: str,
    customer_no: str | None,
    customer_user_id: str | None,
    is_mapped_customer: bool,
    title: str,
    user_id: int,
) -> tuple[int, bool]:
    """Return ``(ticket_id, created)``.

    Order: (a) follow-up tag in the text, (b) most recent non-closed ticket
    that already has an article from this chat (per-chat continuity — see
    module docstring), (c) only for a *really* mapped contact, most recent
    open ticket for that customer_user (generic fallback), (d) create new.
    """
    from tiqora.domain.subject_hook import load_subject_config

    subject_cfg = await load_subject_config(session, sysconfig)
    followup = await detect_followup(
        session,
        sysconfig,
        subject=body_text,
        references=[],
        hook=subject_cfg.hook,
        hook_divider=subject_cfg.divider,
    )
    if followup is not None:
        _tn, ticket_id = followup
        return ticket_id, False

    chat_pattern = f"%<{chat_id}@telegram.invalid>%"
    row = (
        await session.execute(
            text(
                "SELECT t.id FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " JOIN article a ON a.ticket_id = t.id"
                " JOIN article_data_mime adm ON adm.article_id = a.id"
                " WHERE adm.a_from LIKE :pat AND tst.name NOT IN ('closed', 'removed')"
                " ORDER BY t.id DESC LIMIT 1"
            ),
            {"pat": chat_pattern},
        )
    ).first()
    if row is not None:
        return int(row[0]), False

    if is_mapped_customer and customer_user_id:
        row = (
            await session.execute(
                text(
                    "SELECT t.id FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE t.customer_user_id = :cu AND tst.name NOT IN ('closed', 'removed')"
                    " ORDER BY t.id DESC LIMIT 1"
                ),
                {"cu": customer_user_id},
            )
        ).first()
        if row is not None:
            return int(row[0]), False

    resolved_queue = await channel_setting(session, CHANNEL_NAME, "queue_name")
    resolved_queue = resolved_queue or await sysconfig.postmaster_default_queue()
    state_name = await sysconfig.postmaster_default_state()
    priority_name = await sysconfig.postmaster_default_priority()

    queue_id = await _lookup_id(session, "queue", "name", resolved_queue) or 1
    state_id = await _lookup_id(session, "ticket_state", "name", state_name) or 1
    priority_id = await _lookup_id(session, "ticket_priority", "name", priority_name) or 3

    params = TicketIn(
        title=title[:255],
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        owner_id=user_id,
        customer_id=customer_no,
        customer_user_id=customer_user_id,
    )
    ticket_id = await create_ticket(
        session, session_factory, sysconfig, params=params, user_id=user_id
    )
    return ticket_id, True


async def process_update(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    gateway: TelegramGateway | None,
    update: dict[str, Any],
    user_id: int = 1,
) -> dict[str, Any]:
    """Process one Telegram update. Caller commits the session.

    Used by both the long-poll daemon and the webhook route (Task 3); this
    function itself is transport-agnostic.
    """
    message = update.get("message")
    if not isinstance(message, dict):
        # edited_message, callback_query, channel_post, ... — not handled yet.
        return {"skipped": "unsupported"}

    frm = message.get("from") or {}
    if frm.get("is_bot"):
        # Loop protection: never react to another bot's message (including
        # our own echoes, if this bot is ever added to a group with itself).
        return {"skipped": "bot"}

    await ensure_channel_row(session, COMM_CHANNEL_NAME, COMM_CHANNEL_MODULE)

    contact = await _upsert_contact(session, message)

    default_customer = await channel_setting(session, CHANNEL_NAME, "default_customer_user")
    is_mapped_customer = bool(contact.customer_user_login)
    customer_user_id = contact.customer_user_login or default_customer
    customer_no = customer_user_id
    if not customer_user_id:
        logger.info("telegram_inbound_no_customer", chat_id=contact.chat_id)
        return {"skipped": "no_customer"}

    body_text, media = _extract_body(message)

    display_name = contact.display_name or (
        f"@{contact.username}" if contact.username else str(contact.chat_id)
    )
    from_address = f"{display_name} <{contact.chat_id}@telegram.invalid>"
    title = body_text[:60] if body_text else "Telegram-Nachricht"

    ticket_id, created = await _resolve_ticket(
        session,
        session_factory,
        sysconfig,
        chat_id=contact.chat_id,
        body_text=body_text,
        customer_no=customer_no,
        customer_user_id=customer_user_id,
        is_mapped_customer=is_mapped_customer,
        title=title,
        user_id=user_id,
    )

    attachments: list[tuple[str, str, bytes]] = []
    if media is not None:
        kind, file_id, filename, content_type_hint = media
        if gateway is None:
            body_text = f"{body_text}\n[Anhang konnte nicht heruntergeladen werden]"
        else:
            try:
                content, mime_guess = await gateway.download_file(file_id)
                attachments.append((filename, mime_guess or content_type_hint, content))
            except TelegramApiError as exc:
                # A download failure must not cost the customer their message —
                # keep the placeholder text, drop the attachment, note it.
                logger.warning(
                    "telegram_media_download_failed",
                    chat_id=contact.chat_id,
                    kind=kind,
                    error=str(exc),
                )
                body_text = f"{body_text}\n[Anhang konnte nicht heruntergeladen werden]"

    article = ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=title,
        body=body_text,
        content_type="text/plain; charset=utf-8",
        from_address=from_address,
        channel=CHANNEL_NAME,
        attachments=attachments,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )

    return {"ticket_id": ticket_id, "article_id": article_id, "created_ticket": created}


async def build_gateway(session: AsyncSession) -> TelegramGateway | None:
    bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
    if not bot_token:
        return None
    return TelegramGateway(bot_token=bot_token)
