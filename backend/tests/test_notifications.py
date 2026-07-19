"""DB-backed tests for the notification engine (Phase 4b subtask 2)."""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.domain.settings_store import KEY_NOTIFICATIONS_ENABLED, set_setting
from tiqora.worker.notifications import KEY_NOTIFICATIONS_WATERMARK, run_notifications_tick


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    ddl = [
        """CREATE TABLE IF NOT EXISTS tiqora_event_outbox (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            event_type VARCHAR(100) NOT NULL,
            ticket_id BIGINT NOT NULL,
            payload TEXT,
            created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed TINYINT(1) NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS tiqora_settings (
            `key` VARCHAR(200) PRIMARY KEY,
            value TEXT
        )""",
    ]
    for stmt in ddl:
        with contextlib.suppress(Exception):
            await session.execute(text(stmt))
    await session.commit()


async def _insert_queue(session: AsyncSession, name: str) -> int:
    """Insert a dedicated queue (reusing queue-1's FK rows) so tests can scope
    notification matching to their own ticket via a QueueID filter and avoid
    cross-test pollution in the session-scoped testcontainer."""
    await session.execute(
        text(
            "INSERT INTO queue (name, group_id, unlock_timeout, system_address_id,"
            " salutation_id, signature_id, follow_up_id, follow_up_lock, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:name, 1, 0, 1, 1, 1, 1, 0, 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"name": name},
    )
    row = (
        await session.execute(text("SELECT id FROM queue WHERE name = :name"), {"name": name})
    ).first()
    assert row is not None
    return int(row[0])


async def _insert_ticket(
    session: AsyncSession,
    tn: str,
    *,
    owner_id: int = 1,
    customer_user_id: str | None = None,
    queue_id: int = 1,
) -> int:
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, customer_user_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, title, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tn, :qid, 1, :oid, :oid, 3, 1, :cuid, 0, 0, 0, 0, 0, 0, 0,"
            " 'Test Ticket Title', current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn, "qid": queue_id, "oid": owner_id, "cuid": customer_user_id},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


async def _insert_notification_event(
    session: AsyncSession,
    name: str,
    *,
    items: dict[str, list[str]],
    subject: str,
    body: str,
    content_type: str = "text/plain",
    language: str = "en",
) -> int:
    await session.execute(
        text(
            "INSERT INTO notification_event (name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:name, 1, current_timestamp, 1,"
            " current_timestamp, 1)"
        ),
        {"name": name},
    )
    row = (
        await session.execute(
            text("SELECT id FROM notification_event WHERE name = :name"), {"name": name}
        )
    ).first()
    assert row is not None
    nid = int(row[0])

    for key, values in items.items():
        for value in values:
            await session.execute(
                text(
                    "INSERT INTO notification_event_item (notification_id, event_key, event_value)"
                    " VALUES (:nid, :k, :v)"
                ),
                {"nid": nid, "k": key, "v": value},
            )

    await session.execute(
        text(
            "INSERT INTO notification_event_message"
            " (notification_id, subject, text, content_type, language)"
            " VALUES (:nid, :subj, :body, :ct, :lang)"
        ),
        {"nid": nid, "subj": subject, "body": body, "ct": content_type, "lang": language},
    )
    return nid


async def _set_user_prefs(
    session: AsyncSession, user_id: int, email: str, language: str = "en"
) -> None:
    for key, value in (("UserEmail", email), ("UserLanguage", language)):
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:uid, :k, :v)"
            ),
            {"uid": user_id, "k": key, "v": value},
        )


async def _insert_customer_user(session: AsyncSession, login: str, email: str) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:login, :email, 'cust', 'Test', 'Customer', 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"login": login, "email": email},
    )


async def _insert_article(
    session: AsyncSession, ticket_id: int, *, sender_type: str, is_visible: int
) -> int:
    sender_row = (
        await session.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n"), {"n": sender_type}
        )
    ).first()
    assert sender_row is not None
    channel_row = (
        await session.execute(text("SELECT id FROM communication_channel WHERE name = 'Email'"))
    ).first()
    channel_id = int(channel_row[0]) if channel_row is not None else 1
    await session.execute(
        text(
            "INSERT INTO article (ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, create_time, create_by, change_time, change_by)"
            " VALUES (:tid, :st, :ch, :vis, current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tid": ticket_id, "st": int(sender_row[0]), "ch": channel_id, "vis": is_visible},
    )
    row = (
        await session.execute(
            text("SELECT id FROM article WHERE ticket_id = :tid ORDER BY id DESC LIMIT 1"),
            {"tid": ticket_id},
        )
    ).first()
    assert row is not None
    return int(row[0])


async def _skip_backlog(session: AsyncSession) -> None:
    """Advance the notifications watermark past any outbox rows left by earlier
    tests in this session-scoped container, so each test only sees its own
    events (the container/schema is shared across tests in this file)."""
    row = (
        await session.execute(text("SELECT COALESCE(MAX(id), 0) FROM tiqora_event_outbox"))
    ).first()
    assert row is not None
    await set_setting(session, KEY_NOTIFICATIONS_WATERMARK, str(int(row[0])))


async def _insert_outbox_event(
    session: AsyncSession, event_type: str, ticket_id: int, payload: str = "{}"
) -> int:
    await session.execute(
        text(
            "INSERT INTO tiqora_event_outbox (event_type, ticket_id, payload, created, processed)"
            " VALUES (:et, :tid, :pl, current_timestamp, 0)"
        ),
        {"et": event_type, "tid": ticket_id, "pl": payload},
    )
    row = (await session.execute(text("SELECT MAX(id) FROM tiqora_event_outbox"))).first()
    assert row is not None
    return int(row[0])


async def _history_count(session: AsyncSession, ticket_id: int, history_type: str) -> int:
    row = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid AND ht.name = :htype"
            ),
            {"tid": ticket_id, "htype": history_type},
        )
    ).first()
    assert row is not None
    return int(row[0])


@pytest.mark.db
async def test_owner_and_customer_notified_with_placeholders_and_history(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-1")
            await _insert_customer_user(session, "cust1", "cust1@example.com")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_1", owner_id=1, customer_user_id="cust1", queue_id=queue_id
            )
            await _set_user_prefs(session, 1, "owner@example.com")
            await _insert_notification_event(
                session,
                "new-ticket-notify",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner", "Customer"],
                    "Transports": ["Email"],
                },
                subject="New ticket <OTRS_TICKET_TicketNumber>",
                body="Title: <OTRS_TICKET_Title>",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["events"] == 1
        assert result["sent"] == 2

        recipients = sorted(str(m["To"]) for m in sender.sent)
        assert recipients == ["cust1@example.com", "owner@example.com"]

        subjects = {str(m["Subject"]) for m in sender.sent}
        assert subjects == {"New ticket NOTIFY_1"}

        async with factory() as session:
            assert await _history_count(session, ticket_id, "SendAgentNotification") == 1
            assert await _history_count(session, ticket_id, "SendCustomerNotification") == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_article_filter_respected(mariadb_znuny_url: str) -> None:
    """ArticleIsVisibleForCustomer=1 only matches visible articles."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-2")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_ARTFILT", owner_id=1, queue_id=queue_id
            )
            await _set_user_prefs(session, 1, "owner@example.com")
            await _insert_notification_event(
                session,
                "visible-article-notify",
                items={
                    "Events": ["ArticleCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                    "ArticleIsVisibleForCustomer": ["1"],
                },
                subject="Article notice",
                body="body",
            )
            internal_article_id = await _insert_article(
                session, ticket_id, sender_type="agent", is_visible=0
            )
            await _insert_outbox_event(
                session,
                "ArticleCreate",
                ticket_id,
                payload=f'{{"article_id": {internal_article_id}}}',
            )
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["sent"] == 0  # internal article does not match the filter

        async with factory() as session:
            visible_article_id = await _insert_article(
                session, ticket_id, sender_type="agent", is_visible=1
            )
            await _insert_outbox_event(
                session,
                "ArticleCreate",
                ticket_id,
                payload=f'{{"article_id": {visible_article_id}}}',
            )
            await session.commit()

        result2 = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result2["sent"] == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_watermark_prevents_resend_on_rerun(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-3")
            ticket_id = await _insert_ticket(session, "NOTIFY_WM", owner_id=1, queue_id=queue_id)
            await _set_user_prefs(session, 1, "owner@example.com")
            await _insert_notification_event(
                session,
                "wm-notify",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                subject="s",
                body="b",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result1 = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result1["sent"] == 1

        result2 = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result2["events"] == 0
        assert len(sender.sent) == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_run_notifications_tick_disabled_by_default(mariadb_znuny_url: str) -> None:
    """Uses a dedicated engine/table set — not the shared container's persisted
    ``daemon.notifications.enabled=1`` left by earlier tests in this file — to
    verify the flag really defaults OFF."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "0")
        result = await run_notifications_tick(session_factory=factory)
        assert result == {"enabled": 0}
    finally:
        await engine.dispose()
