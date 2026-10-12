"""Seed helpers for the web-chat AI first line tests (sync connections, both dialects).

``seed_bot_queue`` writes the queue AI policy a bot topic needs; ``cleanup_bot_queue`` removes
it again together with the usage rows the budget tests add. Made-up ids only; callers pick
them from their own band.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection, delete, insert

from tiqora.ai.models import FEATURE_AUTO_REPLY, TiqoraAiQueuePolicy, TiqoraAiUsage
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import KEY_AI_AUTO_REPLY_PAUSED


def seed_bot_queue(
    conn: Connection,
    *,
    queue_id: int,
    service_user_id: int | None,
    auto_reply: bool = True,
    budget_tokens_day: int | None = None,
    autonomy: str = "full",
    max_replies_per_hour: int | None = None,
) -> None:
    """The AI policy of *queue_id*: auto-reply switch, service user, autonomy, daily budget."""
    conn.execute(delete(TiqoraAiQueuePolicy).where(TiqoraAiQueuePolicy.queue_id == queue_id))
    conn.execute(
        insert(TiqoraAiQueuePolicy).values(
            queue_id=queue_id,
            enabled_auto_reply=auto_reply,
            autonomy=autonomy,
            service_user_id=service_user_id,
            budget_tokens_day=budget_tokens_day,
            max_replies_per_hour=max_replies_per_hour,
            create_by=1,
            change_by=1,
        )
    )


def seed_ai_usage(conn: Connection, *, queue_id: int, tokens: int, success: bool = True) -> None:
    """One auto-reply usage row of *tokens* tokens, written now."""
    conn.execute(
        insert(TiqoraAiUsage).values(
            ts=datetime.now(UTC).replace(tzinfo=None),
            queue_id=queue_id,
            feature=FEATURE_AUTO_REPLY,
            prompt_tokens=tokens,
            completion_tokens=0,
            success=success,
        )
    )


def set_auto_reply_paused_sync(conn: Connection, paused: bool) -> None:
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == KEY_AI_AUTO_REPLY_PAUSED))
    if paused:
        conn.execute(insert(TiqoraSettings).values(key=KEY_AI_AUTO_REPLY_PAUSED, value="true"))


def cleanup_bot_queue(conn: Connection, *, queue_ids: list[int]) -> None:
    for queue_id in queue_ids:
        conn.execute(delete(TiqoraAiUsage).where(TiqoraAiUsage.queue_id == queue_id))
        conn.execute(delete(TiqoraAiQueuePolicy).where(TiqoraAiQueuePolicy.queue_id == queue_id))
    set_auto_reply_paused_sync(conn, False)
