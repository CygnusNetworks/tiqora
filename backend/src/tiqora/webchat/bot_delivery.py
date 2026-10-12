"""Delivering the AI's answer into a web chat (C7), and the chat tone of the AI.

The runtime (``tiqora.ai.runtime``) calls :func:`deliver_bot_chat_reply` for an auto-reply whose
customer article came from the web chat. It never reaches SMTP: the visitor's ``From`` may be a
real address or ``…@chat.invalid``.

Commit-first rule (R-LOCK-ORDER, extended): the AI tools may have changed ticket fields earlier
in the run's transaction. The caller commits those writes BEFORE calling
:func:`deliver_bot_chat_reply`, which then takes the order session row (``FOR UPDATE``) → ticket
writes (``add_article``) → ``insert_message``. A visitor line posted meanwhile holds the session
row and wants the ticket row; without the commit the two would wait for each other.

``BotChatTakenOver`` means the chat no longer belongs to the bot (an agent accepted it, it ended,
or there is no chat for the ticket): nothing was written, and the caller keeps the answer as a
draft instead of talking over a human.

Imports are one-way: this module uses :mod:`tiqora.webchat.sessions`; ``sessions`` never imports
it (``post_bot_intro`` lives there and is re-exported here).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraChatSession
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.jobs.service import utc_now
from tiqora.webchat.models import DEFAULT_BOT_NAME
from tiqora.webchat.notify import queue_agent_event, queue_wakeup
from tiqora.webchat.sessions import (
    current_queue_id,
    post_bot_intro,
    visitor_from_address,
)
from tiqora.webchat.store import config_values, get_chat_session, insert_message, load_config
from tiqora.webchat.text import chat_body_from_article
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

BOT_CHAT_CONTENT_TYPE: Final = "text/plain; charset=utf-8"

#: System-prompt addendum of a chat-sourced run when the config has no ``bot_tone_prompt``.
DEFAULT_WEBCHAT_TONE_PROMPT: Final = (
    "You are answering a visitor in a live web chat on the website, not writing an e-mail. "
    "Write short, plain-text chat messages: no greeting line, no signature, no subject, "
    "no Markdown headings or tables. Keep each answer to a few sentences and ask at most one "
    "question at a time. Address the visitor politely in the language they write in. "
    "When you cannot solve the problem, escalate to a human with a reason that summarises "
    "the visitor's problem and what was already tried, so the colleague can continue "
    "without asking again."
)


class BotChatTakenOver(Exception):
    """The ticket's chat is not in ``bot`` status any more (or does not exist)."""


async def _latest_chat_id(db: AsyncSession, ticket_id: int) -> str | None:
    return (
        await db.execute(
            select(TiqoraChatSession.id)
            .where(TiqoraChatSession.ticket_id == ticket_id)
            .order_by(TiqoraChatSession.created.desc(), TiqoraChatSession.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def webchat_tone_prompt(db: AsyncSession, *, ticket_id: int) -> str:
    """The config's ``bot_tone_prompt`` of the ticket's chat, else the default."""
    chat_id = await _latest_chat_id(db, ticket_id)
    if chat_id is not None:
        chat = await get_chat_session(db, chat_id)
        cfg = await load_config(db, int(chat.config_id)) if chat is not None else None
        if cfg is not None:
            configured = (config_values(cfg).bot_tone_prompt or "").strip()
            if configured:
                return configured
    return DEFAULT_WEBCHAT_TONE_PROMPT


async def deliver_bot_chat_reply(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    service_user_id: int,
    subject: str,
    body: str,
    now: datetime | None = None,
) -> int:
    """Store the AI's answer as an agent article AND post it as a ``bot`` transcript line.

    Returns the article id. Runs inside the caller's transaction (the caller commits, then
    publishes with ``flush_chat_notifications``). Refusals before any write:
    ``BotChatTakenOver`` (no chat, or status ≠ ``bot`` under the row lock) and
    ``tiqora.webchat.text.MessageRejected`` (empty / too long after cleaning).
    """
    text_body = chat_body_from_article(body, "text/plain")
    chat_id = await _latest_chat_id(db, ticket_id)
    if chat_id is None:
        raise BotChatTakenOver(f"no chat for ticket {ticket_id}")
    locked = await get_chat_session(db, chat_id, for_update=True)
    if locked is None or locked.purged_at is not None or locked.status != "bot":
        raise BotChatTakenOver(f"chat of ticket {ticket_id} is not with the bot")
    queue_id = await current_queue_id(db, locked)
    cfg = await load_config(db, int(locked.config_id))
    bot_name = config_values(cfg).bot_name if cfg is not None else DEFAULT_BOT_NAME
    article_id = await add_article(
        db,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=True,
            subject=subject,
            body=text_body,
            content_type=BOT_CHAT_CONTENT_TYPE,
            to_address=visitor_from_address(
                locked.visitor_name, locked.visitor_email, locked.visitor_id
            ),
            channel="webchat",
        ),
        user_id=service_user_id,
        sysconfig=sysconfig,
    )
    await insert_message(
        db,
        chat=locked,
        sender="bot",
        body=text_body,
        user_id=service_user_id,
        article_id=article_id,
        meta={"agent_name": bot_name},
        now=now or utc_now(),
    )
    queue_wakeup(db, locked.id)
    queue_agent_event(
        db,
        {
            "type": "chat_message",
            "session_id": locked.id,
            "ticket_id": int(locked.ticket_id),
            "queue_id": int(queue_id),
        },
    )
    logger.info("webchat_bot_reply", session_id=locked.id, article_id=article_id)
    return article_id


__all__ = [
    "BOT_CHAT_CONTENT_TYPE",
    "DEFAULT_WEBCHAT_TONE_PROMPT",
    "BotChatTakenOver",
    "deliver_bot_chat_reply",
    "post_bot_intro",
    "webchat_tone_prompt",
]
