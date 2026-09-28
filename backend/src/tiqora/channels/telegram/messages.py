"""Map table between Tiqora articles and Telegram messages (``tiqora_telegram_message``).

Deliberately imports nothing from ``tiqora.domain.*`` — only DB models,
SQLAlchemy and stdlib. ``tiqora.channels.telegram.outbound`` already imports
``tiqora.domain.ticket_write_service``, and a later task imports
:class:`ButtonSpec` from here into ``tiqora.domain.ticket_write_service``; a
domain import in this module would create an import cycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraTelegramMessage

ButtonAction = Literal["reply", "resolve_yes", "resolve_no"]

_BUTTON_ACTIONS: frozenset[str] = frozenset(get_args(ButtonAction))

# Prefix for inline-keyboard ``callback_data`` (Telegram limits this to 64
# bytes total); the payload is just the button's index into the row's
# ``buttons_json`` list, resolved back to a :class:`ButtonSpec` on callback.
CALLBACK_PREFIX = "tqb:"


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    """One inline-keyboard button: its label and what tapping it does."""

    label: str
    action: ButtonAction = "reply"


def keyboard_for(buttons: list[ButtonSpec]) -> dict[str, Any]:
    """Build a Telegram ``inline_keyboard`` payload, one button per row.

    ``callback_data`` is ``f"{CALLBACK_PREFIX}{i}"`` — just the button's
    index, so a later callback handler looks up the row's ``buttons_json``
    (via :func:`buttons_from_json`) and resolves the action from there.
    """
    return {
        "inline_keyboard": [
            [{"text": button.label, "callback_data": f"{CALLBACK_PREFIX}{i}"}]
            for i, button in enumerate(buttons)
        ]
    }


def buttons_to_json(buttons: list[ButtonSpec]) -> str:
    """Serialize *buttons* for storage in ``tiqora_telegram_message.buttons_json``."""
    return json.dumps([{"label": b.label, "action": b.action} for b in buttons])


def buttons_from_json(raw: str | None) -> list[ButtonSpec]:
    """Deserialize ``buttons_json``, tolerating missing/garbage input.

    Returns ``[]`` for ``None`` or invalid JSON. An unrecognised ``action``
    falls back to ``"reply"`` rather than raising, so a future action value
    added by a newer deploy never breaks an older one reading the same row.
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    buttons: list[ButtonSpec] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str):
            continue
        raw_action = item.get("action")
        action: ButtonAction = raw_action if raw_action in _BUTTON_ACTIONS else "reply"
        buttons.append(ButtonSpec(label=label, action=action))
    return buttons


async def record_message(
    session: AsyncSession,
    *,
    article_id: int,
    ticket_id: int,
    chat_id: int,
    message_id: int | None,
    direction: str,
    extra_message_ids: list[int] | None = None,
    reply_to_article_id: int | None = None,
    buttons: list[ButtonSpec] | None = None,
) -> TiqoraTelegramMessage:
    """Insert (and flush) the map row for one article <-> Telegram message."""
    row = TiqoraTelegramMessage(
        article_id=article_id,
        ticket_id=ticket_id,
        chat_id=chat_id,
        message_id=message_id,
        direction=direction,
        extra_message_ids=(
            json.dumps(extra_message_ids) if extra_message_ids is not None else None
        ),
        reply_to_article_id=reply_to_article_id,
        buttons_json=(buttons_to_json(buttons) if buttons is not None else None),
    )
    session.add(row)
    await session.flush()
    return row


async def get_by_article(session: AsyncSession, article_id: int) -> TiqoraTelegramMessage | None:
    """Look up the map row for one article, or ``None``."""
    return (
        await session.execute(
            select(TiqoraTelegramMessage).where(TiqoraTelegramMessage.article_id == article_id)
        )
    ).scalar_one_or_none()


async def get_by_message(
    session: AsyncSession, chat_id: int, message_id: int
) -> TiqoraTelegramMessage | None:
    """Look up the map row by ``(chat_id, message_id)``, or ``None``."""
    return (
        await session.execute(
            select(TiqoraTelegramMessage).where(
                TiqoraTelegramMessage.chat_id == chat_id,
                TiqoraTelegramMessage.message_id == message_id,
            )
        )
    ).scalar_one_or_none()


async def list_for_ticket(session: AsyncSession, ticket_id: int) -> list[TiqoraTelegramMessage]:
    """All map rows for a ticket, oldest first."""
    return list(
        (
            await session.execute(
                select(TiqoraTelegramMessage)
                .where(TiqoraTelegramMessage.ticket_id == ticket_id)
                .order_by(TiqoraTelegramMessage.created)
            )
        )
        .scalars()
        .all()
    )
