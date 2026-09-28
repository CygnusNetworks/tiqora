"""Business logic for the Telegram chat-composer edit/retract/info/typing
endpoints (Task 6).

Kept out of :mod:`tiqora.channels.telegram.messages` (which deliberately
avoids ``tiqora.domain.*`` imports -- see its module docstring) and out of
:mod:`tiqora.channels.telegram.outbound` (which only ever sends *new*
messages): these operations mutate an already-sent message, or just read
chat state, so they live in their own module. Imports ``tiqora.domain.*``
freely -- unlike ``messages.py`` there is no cycle risk here, and
``outbound.py`` already sets that precedent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.telegram import outbound
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.messages import (
    buttons_from_json,
    get_by_article,
    keyboard_for,
    list_for_ticket,
)
from tiqora.db.tiqora.models import TiqoraTelegramMessage
from tiqora.znuny.history import TYPE_MISC, history_add

logger = structlog.get_logger(__name__)


class TelegramMessageNotFound(Exception):
    """No Telegram map row for this article on this ticket (route -> 404)."""


class TelegramActionConflict(Exception):
    """The article exists but the edit/retract can't be applied (route -> 409)."""


@dataclass(frozen=True, slots=True)
class ChatContact:
    """``tiqora_telegram_contact`` fields the chat-info endpoint exposes."""

    chat_id: int
    username: str | None
    display_name: str | None
    customer_user_login: str | None
    consent_time: datetime | None


@dataclass(frozen=True, slots=True)
class ChatInfo:
    contact: ChatContact
    ai_escalated_at: datetime | None
    messages: list[TiqoraTelegramMessage]


async def get_chat_info(session: AsyncSession, ticket_id: int) -> ChatInfo:
    """Resolve the ticket's Telegram chat, contact identity, and per-article
    message metadata.

    Raises :class:`~tiqora.channels.telegram.outbound.TelegramDeliveryError`
    (the route maps this to 404) when the ticket has no Telegram chat at all
    -- same resolution :func:`resolve_chat_id` uses everywhere else.
    """
    chat_id = await outbound.resolve_chat_id(session, ticket_id)
    contact_row = (
        (
            await session.execute(
                text(
                    "SELECT chat_id, username, display_name, customer_user_login, consent_time"
                    " FROM tiqora_telegram_contact WHERE chat_id = :chat LIMIT 1"
                ),
                {"chat": chat_id},
            )
        )
        .mappings()
        .first()
    )
    if contact_row is not None:
        contact = ChatContact(
            chat_id=int(contact_row["chat_id"]),
            username=contact_row["username"],
            display_name=contact_row["display_name"],
            customer_user_login=contact_row["customer_user_login"],
            consent_time=contact_row["consent_time"],
        )
    else:
        # resolve_chat_id's fallback (parsing a_from off the newest inbound
        # article) can find a chat_id with no tiqora_telegram_contact row yet.
        contact = ChatContact(
            chat_id=chat_id,
            username=None,
            display_name=None,
            customer_user_login=None,
            consent_time=None,
        )
    ai_row = (
        await session.execute(
            text("SELECT ai_escalated_at FROM tiqora_ai_ticket_state WHERE ticket_id = :tid"),
            {"tid": ticket_id},
        )
    ).first()
    messages = await list_for_ticket(session, ticket_id)
    return ChatInfo(
        contact=contact,
        ai_escalated_at=ai_row[0] if ai_row is not None else None,
        messages=messages,
    )


async def send_typing(
    session: AsyncSession, ticket_id: int, gateway: TelegramGateway | None = None
) -> None:
    """Send a best-effort ``typing`` chat action for the ticket's Telegram chat.

    The Telegram call itself never raises -- :meth:`TelegramGateway.send_chat_action`
    logs and swallows failures. Only chat/gateway resolution (no Telegram chat,
    no ``bot_token`` configured) can turn this into an error for the caller.
    """
    chat_id = await outbound.resolve_chat_id(session, ticket_id)
    gw = gateway if gateway is not None else await outbound.build_gateway(session)
    await gw.send_chat_action(chat_id, "typing")


async def _editable_message(
    session: AsyncSession, ticket_id: int, article_id: int
) -> TiqoraTelegramMessage:
    """The map row for an article an agent may edit/retract, or raise.

    ``TelegramMessageNotFound`` when the article has no map row at all, or its
    row belongs to a different ticket (the id came from the client -- treated
    like "not found", not "forbidden"). ``TelegramActionConflict`` when the
    article exists but isn't an agent-sent, still-live Telegram message: a
    customer's inbound message, one Telegram never returned a message_id for,
    or one already retracted.
    """
    row = await get_by_article(session, article_id)
    if row is None or row.ticket_id != ticket_id:
        raise TelegramMessageNotFound(
            f"article {article_id} has no Telegram message on ticket {ticket_id}"
        )
    if row.direction != "out" or row.message_id is None:
        raise TelegramActionConflict(f"article {article_id} is not an agent-sent Telegram message")
    if row.retracted_at is not None:
        raise TelegramActionConflict(f"article {article_id} was already retracted")
    return row


async def edit_message(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int,
    user_id: int,
    body: str,
    gateway: TelegramGateway | None = None,
) -> None:
    """Edit a previously sent agent Telegram message's text.

    Keeps the message's inline keyboard when it has one and no button has
    been answered yet; stores ``original_body`` only on the first edit of a
    message. Writes a ``Misc`` ``%%TelegramEdited%%<article_id>`` history row.
    """
    row = await _editable_message(session, ticket_id, article_id)
    assert row.message_id is not None  # _editable_message already checked this
    gw = gateway if gateway is not None else await outbound.build_gateway(session)
    buttons = buttons_from_json(row.buttons_json)
    reply_markup = keyboard_for(buttons) if buttons and row.answered_button is None else None
    try:
        await gw.edit_message_text(row.chat_id, row.message_id, body, reply_markup=reply_markup)
    except TelegramApiError as exc:
        raise TelegramActionConflict(str(exc)) from exc

    mime = (
        await session.execute(
            text("SELECT a_body FROM article_data_mime WHERE article_id = :aid"),
            {"aid": article_id},
        )
    ).first()
    previous_body = str(mime[0]) if mime is not None and mime[0] is not None else ""

    await session.execute(
        text("UPDATE article_data_mime SET a_body = :body WHERE article_id = :aid"),
        {"body": body, "aid": article_id},
    )
    # original_body is set only on the first edit: COALESCE keeps whatever a
    # prior edit already stored there.
    await session.execute(
        text(
            "UPDATE tiqora_telegram_message SET edited_at = current_timestamp,"
            " original_body = COALESCE(original_body, :orig) WHERE article_id = :aid"
        ),
        {"orig": previous_body, "aid": article_id},
    )
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_MISC,
        name=f"%%TelegramEdited%%{article_id}",
        user_id=user_id,
        article_id=article_id,
    )


def _already_gone(exc: TelegramApiError) -> bool:
    """Telegram's refusal for a part that's already been deleted (by us on an
    earlier attempt, or by the customer) -- e.g. "Bad Request: message to
    delete not found". Treated the same as a successful delete: it means the
    message is gone, which is the retract's whole goal, so a retry after a
    partial failure doesn't get stuck forever re-deleting the same part.
    """
    return "not found" in str(exc).lower()


async def retract_message(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int,
    user_id: int,
    gateway: TelegramGateway | None = None,
) -> None:
    """Delete a previously sent agent Telegram message and its attachment parts.

    Deletes ``message_id`` and each of ``extra_message_ids``, in that order.

    Before anything has been deleted, a refusal aborts the whole call: raises
    :class:`TelegramActionConflict` with Telegram's reason and leaves the
    article/map row untouched (so a genuinely-refused retract -- Telegram's
    48h window, wrong chat -- is reported instead of silently half-applied).

    Once at least one part is confirmed gone (deleted here, or already gone
    from an earlier attempt), the retract is committed either way: later
    refusals are logged and skipped rather than aborting, and the article is
    marked retracted at the end. Otherwise a message that partially succeeds
    could never be retried -- the parts already gone would keep refusing
    "not found" forever while the DB stayed unretracted.
    """
    row = await _editable_message(session, ticket_id, article_id)
    gw = gateway if gateway is not None else await outbound.build_gateway(session)
    extra_ids = json.loads(row.extra_message_ids) if row.extra_message_ids else []
    message_ids = [row.message_id, *extra_ids]

    any_gone = False
    for message_id in message_ids:
        try:
            await gw.delete_message(row.chat_id, message_id)
            any_gone = True
        except TelegramApiError as exc:
            if _already_gone(exc):
                any_gone = True
                continue
            if not any_gone:
                raise TelegramActionConflict(str(exc)) from exc
            logger.warning(
                "telegram_retract_partial_failure",
                ticket_id=ticket_id,
                article_id=article_id,
                message_id=message_id,
                error=str(exc),
            )

    await session.execute(
        text(
            "UPDATE tiqora_telegram_message SET retracted_at = current_timestamp,"
            " retracted_by = :uid WHERE article_id = :aid"
        ),
        {"uid": user_id, "aid": article_id},
    )
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_MISC,
        name=f"%%TelegramRetracted%%{article_id}",
        user_id=user_id,
        article_id=article_id,
    )


__all__ = [
    "ChatContact",
    "ChatInfo",
    "TelegramActionConflict",
    "TelegramMessageNotFound",
    "edit_message",
    "get_chat_info",
    "retract_message",
    "send_typing",
]
