"""Shared seed/cleanup helpers for the web-chat DB tests (sync connections, both dialects).

All ids come from the plan's band 48400-48799 and seeded logins contain ``wc48``.
Autoincrement rows the services create are removed with
``tests._row_cleanup.snapshot_marks`` / ``delete_above_marks`` over ``ZNUNY_MARK_TABLES``
(``MARK_CLEANUP_FIRST`` covers ``article_flag``, which has no ``id`` column).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, delete, insert, text

from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraSettings
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.models import ChatConfigValues, ChatTopic

NOW = datetime(2026, 10, 10, 12, 0, 0)

# Child rows first: this is the delete order of ``delete_above_marks``.
ZNUNY_MARK_TABLES: tuple[str, ...] = (
    "tiqora_chat_message",
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "dynamic_field_value",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket",
    "ticket_number_counter",
    "communication_channel",
)

#: ``article_flag`` has no ``id`` column; pass as ``delete_above_marks(..., first=...)`` with
#: ``{"m": marks["article"]}``.
MARK_CLEANUP_FIRST_SQL: str = "DELETE FROM article_flag WHERE article_id > :m"


def mark_cleanup_first(marks: dict[str, int]) -> list[tuple[str, dict[str, int]]]:
    return [(MARK_CLEANUP_FIRST_SQL, {"m": marks["article"]})]


def seed_queue_agent(
    conn: Connection, *, queue_id: int, group_id: int, user_id: int, login: str, rw: bool = True
) -> None:
    """Group + queue (system address 1, salutation 1, signature 1, follow_up_id 1) + agent."""
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:gid, :name, 1, :t, 1, :t, 1)"
        ),
        {"gid": group_id, "name": f"wc48-grp-{group_id}", "t": NOW},
    )
    conn.execute(
        text(
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:qid, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
        ),
        {"qid": queue_id, "name": f"wc48-queue-{queue_id}", "gid": group_id, "t": NOW},
    )
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:uid, :login, 'x', 'Chat', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"uid": user_id, "login": login, "t": NOW},
    )
    for key in ("ro", "rw") if rw else ("ro",):
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
            ),
            {"uid": user_id, "gid": group_id, "k": key, "t": NOW},
        )


def insert_config(
    conn: Connection,
    *,
    config_id: int,
    queue_id: int,
    public_key: str,
    origins: list[str],
    values_override: dict[str, Any] | None = None,
    valid: bool = True,
    topics: list[ChatTopic] | None = None,
) -> None:
    values = ChatConfigValues(
        topics=topics or [ChatTopic(key="support", label="Support", queue_id=queue_id)],
        allowed_origins=origins,
        **(values_override or {}),
    )
    dumped = values.model_dump(mode="json")
    conn.execute(
        insert(TiqoraChatConfig).values(
            id=config_id,
            name=f"wc48-config-{config_id}",
            public_key=public_key,
            valid=valid,
            allowed_origins=json.dumps(dumped["allowed_origins"]),
            topics=json.dumps(dumped["topics"]),
            greeting=dumped["greeting"],
            privacy_url=dumped["privacy_url"],
            offline_mode=dumped["offline_mode"],
            business_calendar=dumped["business_calendar"],
            wait_timeout_seconds=dumped["wait_timeout_seconds"],
            prechat_fields=json.dumps(dumped["prechat_fields"]),
            upload_policy=json.dumps(dumped["upload_policy"]),
            theme=json.dumps(dumped["theme"]),
            rate_limits=json.dumps(dumped["rate_limits"]),
            default_customer_user=dumped["default_customer_user"],
            show_agent_names=dumped["show_agent_names"],
            retention_days=dumped["retention_days"],
            bot_name=dumped["bot_name"],
            bot_tone_prompt=dumped["bot_tone_prompt"],
            show_on_portal=dumped["show_on_portal"],
            identity_create_customers=dumped["identity_create_customers"],
            created=NOW,
            created_by=1,
            changed=NOW,
            changed_by=1,
        )
    )


def cleanup_band(
    conn: Connection,
    *,
    config_ids: range,
    queue_ids: Iterable[int],
    group_ids: Iterable[int],
    user_ids: Iterable[int],
) -> None:
    """Remove everything a module seeded in its band; safe to call before and after.

    Also drops the process-wide public config cache, so no cached config or channel switch
    leaks from one module into the next.
    """
    clear_config_cache()
    lo, hi = config_ids.start, config_ids.stop
    users, groups, queues = list(user_ids), list(group_ids), list(queue_ids)
    band = {"lo": lo, "hi": hi}
    conn.execute(
        text(
            "DELETE FROM tiqora_chat_message WHERE session_id IN"
            " (SELECT id FROM tiqora_chat_session WHERE config_id >= :lo AND config_id < :hi)"
        ),
        band,
    )
    conn.execute(
        text("DELETE FROM tiqora_chat_session WHERE config_id >= :lo AND config_id < :hi"), band
    )
    conn.execute(text("DELETE FROM tiqora_chat_config WHERE id >= :lo AND id < :hi"), band)
    for sql, ids in (
        ("DELETE FROM tiqora_chat_agent_state WHERE user_id = :i", users),
        ("DELETE FROM group_user WHERE user_id = :i", users),
        ("DELETE FROM group_user WHERE group_id = :i", groups),
        ("DELETE FROM queue WHERE id = :i", queues),
        ("DELETE FROM permission_groups WHERE id = :i", groups),
        ("DELETE FROM users WHERE id = :i", users),
    ):
        for i in ids:
            conn.execute(text(sql), {"i": i})
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == "channel.webchat.enabled"))


def enable_webchat(conn: Connection) -> None:
    """Switch the channel on (``channel.webchat.enabled`` = "1")."""
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key == "channel.webchat.enabled"))
    conn.execute(insert(TiqoraSettings).values(key="channel.webchat.enabled", value="1"))
