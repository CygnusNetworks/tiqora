"""Post-commit notifications of chat writes (visitor stream wake-ups, agent-bus events).

A wake published before the commit would make a visitor stream re-read the DB and find
nothing, and an event for rolled-back work would ring agents for a chat that does not
exist. So the session services only *queue* notifications on the ``AsyncSession``:

- ``queue_wakeup`` / ``queue_agent_event`` add to ``db.info`` (pending, this transaction);
- when the outermost transaction COMMITS, the pending items become ready;
- when it ends any other way (rollback, close), the pending items are dropped;
- the caller publishes the ready items with ``flush_chat_notifications(db, redis)`` after
  ``await db.commit()``. Never call it on an exception path that skipped the commit — it
  would publish nothing then anyway, but the call has no purpose there.

Agent events carry ids only (``tiqora.events.pubsub.publish_chat_event``); visitor data
never reaches Redis.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from tiqora.events.pubsub import publish_chat_event
from tiqora.webchat.hub import publish_chat_wake

logger = structlog.get_logger(__name__)

WAKEUPS_KEY: Final = "tiqora.webchat.wakeups"
AGENT_EVENTS_KEY: Final = "tiqora.webchat.agent_events"
_READY_WAKEUPS_KEY: Final = "tiqora.webchat.ready_wakeups"
_READY_EVENTS_KEY: Final = "tiqora.webchat.ready_agent_events"
_HOOKED_KEY: Final = "tiqora.webchat.notify_hooked"


def _on_commit(session: Session) -> None:
    # Fires for SAVEPOINT releases too; only the outermost commit makes items ready.
    if session.in_nested_transaction():
        return
    wakeups = session.info.pop(WAKEUPS_KEY, None)
    if wakeups:
        ready = session.info.setdefault(_READY_WAKEUPS_KEY, [])
        ready.extend(w for w in wakeups if w not in ready)
    events = session.info.pop(AGENT_EVENTS_KEY, None)
    if events:
        session.info.setdefault(_READY_EVENTS_KEY, []).extend(events)


def _on_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    # Outermost transaction over: after a commit the pending lists are already empty;
    # after a rollback/close they belong to work that never happened.
    if transaction.nested or transaction.parent is not None:
        return
    dropped_w = session.info.pop(WAKEUPS_KEY, None)
    dropped_e = session.info.pop(AGENT_EVENTS_KEY, None)
    if dropped_w or dropped_e:
        logger.info(
            "webchat_notifications_dropped",
            wakeups=len(dropped_w or ()),
            agent_events=len(dropped_e or ()),
        )


def _hook(db: AsyncSession) -> None:
    sync = db.sync_session
    if sync.info.get(_HOOKED_KEY):
        return
    event.listen(sync, "after_commit", _on_commit)
    event.listen(sync, "after_transaction_end", _on_transaction_end)
    sync.info[_HOOKED_KEY] = True


def queue_wakeup(db: AsyncSession, session_id: str) -> None:
    """Wake the visitor streams of *session_id* after this transaction commits."""
    _hook(db)
    pending: list[str] = db.info.setdefault(WAKEUPS_KEY, [])
    if session_id not in pending:
        pending.append(session_id)


def queue_agent_event(db: AsyncSession, payload: dict[str, Any]) -> None:
    """Publish an agent-bus chat event after this transaction commits.

    *payload*: ``{"type", "session_id", "ticket_id", "queue_id"[, "user_id"][, "topic_ref"]}``
    — ``queue_id`` must be the ticket's current queue (R-QUEUE-SOT).
    """
    _hook(db)
    db.info.setdefault(AGENT_EVENTS_KEY, []).append(dict(payload))


async def flush_chat_notifications(db: AsyncSession, client: Any) -> None:
    """Publish what committed transactions queued; never raises (best-effort like pub/sub)."""
    wakeups: list[str] = db.info.pop(_READY_WAKEUPS_KEY, None) or []
    events: list[dict[str, Any]] = db.info.pop(_READY_EVENTS_KEY, None) or []
    for session_id in wakeups:
        try:
            await publish_chat_wake(client, session_id)
        except Exception:  # noqa: BLE001 - streams re-read the DB on every heartbeat
            logger.warning("webchat_wake_publish_failed")
    for payload in events:
        try:
            user_id = payload.get("user_id")
            await publish_chat_event(
                client,
                str(payload["type"]),
                session_id=str(payload["session_id"]),
                ticket_id=int(payload["ticket_id"]),
                queue_id=int(payload["queue_id"]),
                user_id=int(user_id) if user_id is not None else None,
                topic_ref=payload.get("topic_ref"),
            )
        except Exception:  # noqa: BLE001 - a malformed payload must not break the request
            logger.warning("webchat_agent_event_publish_failed", event_type=payload.get("type"))
