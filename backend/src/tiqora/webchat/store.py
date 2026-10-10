"""Chat configs (typed values) and the chat session / message rows.

Message order: ``tiqora_chat_session.last_seq`` is the per-session counter. ``allocate_seq``
increments it with an UPDATE, whose row lock serialises concurrent writers of the same
session until their commit on both dialects — so ``seq`` is gapless and unique as long as
the increment and the message INSERT commit or roll back together (``insert_message`` does
both; callers wrap it in the same SAVEPOINT as the article it belongs to).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

import structlog
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key

from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatMessage, TiqoraChatSession
from tiqora.webchat.models import ChatConfigValues, default_prechat_fields

logger = structlog.get_logger(__name__)

#: Session statuses in which visitor and agent can still write.
LIVE_STATUSES: Final = frozenset({"waiting", "active"})

#: Columns holding JSON text; every other value column is a plain scalar.
_JSON_COLUMNS: Final = (
    "allowed_origins",
    "topics",
    "prechat_fields",
    "upload_policy",
    "theme",
    "rate_limits",
)
_SCALAR_COLUMNS: Final = (
    "greeting",
    "privacy_url",
    "offline_mode",
    "business_calendar",
    "wait_timeout_seconds",
    "default_customer_user",
    "show_agent_names",
    "retention_days",
)
#: A topic that always validates, so one column can be checked on its own.
_PROBE_TOPICS: Final = [{"key": "probe", "label": "probe", "queue_id": 1}]


async def load_config(db: AsyncSession, config_id: int) -> TiqoraChatConfig | None:
    return (
        await db.execute(select(TiqoraChatConfig).where(TiqoraChatConfig.id == config_id))
    ).scalar_one_or_none()


async def load_config_by_key(db: AsyncSession, public_key: str) -> TiqoraChatConfig | None:
    """The config for *public_key*, only while it is valid (``valid`` true)."""
    return (
        await db.execute(
            select(TiqoraChatConfig).where(
                TiqoraChatConfig.public_key == public_key,
                TiqoraChatConfig.valid.is_(True),
            )
        )
    ).scalar_one_or_none()


def _default_for(column: str) -> Any:
    if column == "topics":
        return []
    if column == "prechat_fields":
        return default_prechat_fields()
    field = ChatConfigValues.model_fields[column]
    return field.get_default(call_default_factory=True)


def _column_value(row: TiqoraChatConfig, column: str) -> Any:
    """Validate one column in isolation; invalid -> its default (and a warning)."""
    raw = getattr(row, column)
    try:
        value = json.loads(raw) if column in _JSON_COLUMNS else raw
        probe = ChatConfigValues.model_validate({"topics": _PROBE_TOPICS, column: value})
    except (ValueError, TypeError, ValidationError):
        logger.warning("webchat_config_column_invalid", config_id=row.id, column=column)
        return _default_for(column)
    return getattr(probe, column)


def config_values(row: TiqoraChatConfig) -> ChatConfigValues:
    """Typed values of *row*; each column falls back to its default on its own.

    ``topics`` ``min_length=1`` only binds admin input: a broken topics column
    yields ``[]`` (the config then counts as offline) via ``model_construct``.
    """
    parts: dict[str, Any] = {
        column: _column_value(row, column) for column in (*_JSON_COLUMNS, *_SCALAR_COLUMNS)
    }
    try:
        return ChatConfigValues.model_validate(parts)
    except ValidationError:
        if parts["topics"]:
            logger.warning("webchat_config_column_invalid", config_id=row.id, column="topics")
            parts["topics"] = []
        return ChatConfigValues.model_construct(**parts)


# -- sessions and messages ---------------------------------------------------------------------


async def get_chat_session(
    db: AsyncSession, session_id: str, *, for_update: bool = False
) -> TiqoraChatSession | None:
    """The session row; ``for_update`` takes its row lock (R-LOCK-ORDER step 2).

    Always re-reads the row into an instance the identity map may already hold, so a caller
    that waited for the lock sees the status another transaction just committed.
    """
    stmt = select(TiqoraChatSession).where(TiqoraChatSession.id == session_id)
    if for_update:
        stmt = stmt.with_for_update()
    stmt = stmt.execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def allocate_seq(db: AsyncSession, session_id: str) -> int:
    """Next message ``seq`` of the session (``last_seq + 1``), reserved in this transaction.

    ``UPDATE … SET last_seq = last_seq + 1`` locks the row until commit, so concurrent
    writers queue up behind it on MariaDB and PostgreSQL (gapless, no duplicates). The
    loaded instance, if any, is updated without marking it dirty.
    """
    result = await db.execute(
        update(TiqoraChatSession)
        .where(TiqoraChatSession.id == session_id)
        .values(last_seq=TiqoraChatSession.last_seq + 1)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 1) == 0:
        raise LookupError(f"chat session {session_id} not found")
    seq = int(
        (
            await db.execute(
                select(TiqoraChatSession.last_seq).where(TiqoraChatSession.id == session_id)
            )
        ).scalar_one()
    )
    loaded = db.identity_map.get(identity_key(TiqoraChatSession, session_id))
    if isinstance(loaded, TiqoraChatSession):
        set_committed_value(loaded, "last_seq", seq)
    return seq


async def insert_message(
    db: AsyncSession,
    *,
    chat: TiqoraChatSession,
    sender: str,
    body: str,
    user_id: int | None = None,
    article_id: int | None = None,
    attachments: Sequence[dict[str, Any]] = (),
    meta: dict[str, Any] | None = None,
    client_id: str | None = None,
    now: datetime,
) -> TiqoraChatMessage:
    """Allocate the next ``seq`` and INSERT one transcript line (flushed, so ``id`` is set).

    A duplicate ``client_id`` raises ``IntegrityError`` from the flush; run this inside a
    SAVEPOINT together with the article when the caller wants to recover from that.
    """
    seq = await allocate_seq(db, chat.id)
    message = TiqoraChatMessage(
        session_id=chat.id,
        ticket_id=int(chat.ticket_id),
        seq=seq,
        sender=sender,
        user_id=user_id,
        body=body,
        meta=json.dumps(meta) if meta is not None else None,
        attachments=json.dumps(list(attachments)) if attachments else None,
        article_id=article_id,
        client_id=client_id,
        created=now,
    )
    db.add(message)
    await db.flush([message])
    return message


async def find_by_client_id(
    db: AsyncSession, session_id: str, client_id: str, *, locking: bool = False
) -> TiqoraChatMessage | None:
    """The message a retried POST already stored.

    ``locking`` reads the newest committed row (``FOR SHARE`` / ``LOCK IN SHARE MODE``)
    instead of the transaction's snapshot — needed after a duplicate-key error on MariaDB,
    whose REPEATABLE READ snapshot would not show the other transaction's row.
    """
    stmt = select(TiqoraChatMessage).where(
        TiqoraChatMessage.session_id == session_id, TiqoraChatMessage.client_id == client_id
    )
    if locking:
        stmt = stmt.with_for_update(read=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def load_messages_since(
    db: AsyncSession, session_id: str, after_seq: int, limit: int = 200
) -> list[TiqoraChatMessage]:
    """Messages with ``seq > after_seq`` in order, at most *limit* (1..200)."""
    limit = max(1, min(int(limit), 200))
    rows = await db.execute(
        select(TiqoraChatMessage)
        .where(TiqoraChatMessage.session_id == session_id, TiqoraChatMessage.seq > after_seq)
        .order_by(TiqoraChatMessage.seq)
        .limit(limit)
    )
    return list(rows.scalars().all())


async def live_session_for_ticket(db: AsyncSession, ticket_id: int) -> TiqoraChatSession | None:
    """The newest session of the ticket that is still ``waiting`` or ``active``."""
    return (
        await db.execute(
            select(TiqoraChatSession)
            .where(
                TiqoraChatSession.ticket_id == ticket_id,
                TiqoraChatSession.status.in_(sorted(LIVE_STATUSES)),
            )
            .order_by(TiqoraChatSession.created.desc(), TiqoraChatSession.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
