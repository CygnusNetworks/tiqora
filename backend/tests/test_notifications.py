"""DB-backed tests for the notification engine (Phase 4b subtask 2)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.domain.settings_store import KEY_NOTIFICATIONS_ENABLED, set_setting
from tiqora.worker.notifications import KEY_NOTIFICATIONS_WATERMARK, run_notifications_tick


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    """Create additive Tiqora tables (outbox, settings, cache_invalidation, …).

    Customer notifications write a ticket article, which enqueues
    ``tiqora_cache_invalidation`` — so we need the full Tiqora metadata, not
    just the outbox/settings subset. Dialect-agnostic via SQLAlchemy.
    """
    from tiqora.db.tiqora.base import TiqoraBase

    conn = await session.connection()
    await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
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
    # ``processed`` is TINYINT on MySQL and BOOLEAN on Postgres — bind a
    # boolean so both dialects accept the literal without a cast.
    await session.execute(
        text(
            "INSERT INTO tiqora_event_outbox (event_type, ticket_id, payload, created, processed)"
            " VALUES (:et, :tid, :pl, current_timestamp, :processed)"
        ),
        {"et": event_type, "tid": ticket_id, "pl": payload, "processed": False},
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
            # Owner must not be agent 1 — Znuny never notifies the root agent.
            owner_id = 910_101
            await _insert_agent(session, user_id=owner_id, login="notify.owner1")
            ticket_id = await _insert_ticket(
                session,
                "NOTIFY_1",
                owner_id=owner_id,
                customer_user_id="cust1",
                queue_id=queue_id,
            )
            await _set_user_prefs(session, owner_id, "owner@example.com")
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
            owner_id = 910_102
            await _insert_agent(session, user_id=owner_id, login="notify.owner2")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_ARTFILT", owner_id=owner_id, queue_id=queue_id
            )
            await _set_user_prefs(session, owner_id, "owner@example.com")
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
            owner_id = 910_103
            await _insert_agent(session, user_id=owner_id, login="notify.owner3")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_WM", owner_id=owner_id, queue_id=queue_id
            )
            await _set_user_prefs(session, owner_id, "owner@example.com")
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


async def _insert_agent(
    session: AsyncSession,
    *,
    user_id: int,
    login: str,
    valid_id: int = 1,
) -> None:
    """Idempotent agent seed (shared session-scoped container)."""
    await session.execute(
        text("DELETE FROM user_preferences WHERE user_id = :uid"), {"uid": user_id}
    )
    await session.execute(
        text("DELETE FROM users WHERE id = :uid OR login = :login"),
        {
            "uid": user_id,
            "login": login,
        },
    )
    await session.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:uid, :login, 'x', 'Watch', 'Agent', :vid,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"uid": user_id, "login": login, "vid": valid_id},
    )


async def _insert_watcher(session: AsyncSession, ticket_id: int, user_id: int) -> None:
    await session.execute(
        text("DELETE FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :uid"),
        {"tid": ticket_id, "uid": user_id},
    )
    await session.execute(
        text(
            "INSERT INTO ticket_watcher (ticket_id, user_id, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:tid, :uid, current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tid": ticket_id, "uid": user_id},
    )


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_watcher_recipient_resolves_watchers(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """AgentWatcher notifies ticket_watcher agents; skips invalid/no-email and non-watchers."""
    sync_url: str = request.getfixturevalue(url_fixture)
    if sync_url.startswith("mysql"):
        async_url = _mysql_async(sync_url)
    elif sync_url.startswith("postgresql+psycopg2://"):
        async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    elif sync_url.startswith("postgresql://"):
        async_url = sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        async_url = sync_url

    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()

    # Stable ids in a high range so they never collide with Znuny seed data.
    watcher_id = 910_001
    non_watcher_id = 910_002
    invalid_watcher_id = 910_003
    no_email_watcher_id = 910_004

    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)

            queue_id = await _insert_queue(session, "notify-q-watcher")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_WATCHER", owner_id=1, queue_id=queue_id
            )

            await _insert_agent(session, user_id=watcher_id, login="watcher.valid")
            await _insert_agent(session, user_id=non_watcher_id, login="watcher.non")
            await _insert_agent(
                session, user_id=invalid_watcher_id, login="watcher.invalid", valid_id=2
            )
            await _insert_agent(session, user_id=no_email_watcher_id, login="watcher.noemail")

            await _set_user_prefs(session, watcher_id, "watcher@example.com")
            await _set_user_prefs(session, non_watcher_id, "nonwatcher@example.com")
            await _set_user_prefs(session, invalid_watcher_id, "invalid@example.com")
            # no_email_watcher deliberately has no UserEmail preference

            await _insert_watcher(session, ticket_id, watcher_id)
            await _insert_watcher(session, ticket_id, invalid_watcher_id)
            await _insert_watcher(session, ticket_id, no_email_watcher_id)
            # non_watcher is intentionally NOT on the ticket

            await _insert_notification_event(
                session,
                "watcher-notify",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentWatcher"],
                    "Transports": ["Email"],
                },
                subject="Watched ticket <OTRS_TICKET_TicketNumber>",
                body="Title: <OTRS_TICKET_Title>",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["events"] == 1
        assert result["sent"] == 1

        recipients = [str(m["To"]) for m in sender.sent]
        assert recipients == ["watcher@example.com"]
        assert "nonwatcher@example.com" not in recipients
        assert "invalid@example.com" not in recipients

        async with factory() as session:
            assert await _history_count(session, ticket_id, "SendAgentNotification") == 1
    finally:
        await engine.dispose()


def _recipients_of(sender: CapturingMailSender, subject: str) -> list[str]:
    """Recipients of the mails one rule produced, identified by its subject.

    The Znuny seed ships its own notification rules for the same events, so
    tests must not assume their rule is the only one that matched.
    """
    return sorted(str(m["To"]) for m in sender.sent if str(m["Subject"]) == subject)


async def _grant_group_permission(
    session: AsyncSession, user_id: int, group_id: int, permission_key: str = "ro"
) -> None:
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (:uid, :gid, :key, current_timestamp, 1, current_timestamp, 1)"
        ),
        {"uid": user_id, "gid": group_id, "key": permission_key},
    )


async def _subscribe_queue(session: AsyncSession, user_id: int, queue_id: int) -> None:
    await session.execute(
        text("INSERT INTO personal_queues (user_id, queue_id) VALUES (:uid, :qid)"),
        {"uid": user_id, "qid": queue_id},
    )


async def _subscribe_service(session: AsyncSession, user_id: int, service_id: int) -> None:
    await session.execute(
        text("INSERT INTO personal_services (user_id, service_id) VALUES (:uid, :sid)"),
        {"uid": user_id, "sid": service_id},
    )


async def _insert_service(session: AsyncSession, name: str) -> int:
    await session.execute(
        text(
            "INSERT INTO service (name, valid_id, create_time, create_by,"
            " change_time, change_by)"
            " VALUES (:name, 1, current_timestamp, 1, current_timestamp, 1)"
        ),
        {"name": name},
    )
    row = (
        await session.execute(text("SELECT id FROM service WHERE name = :name"), {"name": name})
    ).first()
    assert row is not None
    return int(row[0])


@pytest.mark.db
async def test_my_queues_and_my_services_recipients(mariadb_znuny_url: str) -> None:
    """AgentMyQueues/AgentMyServices resolve personal_queues/personal_services.

    Ports ``Ticket.pm::GetSubscribedUserIDsByQueueID`` (queue subscribers also
    need ``ro`` on the queue's group) and ``...ByServiceID`` (valid users only).
    """
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()

    q_sub = 910_201  # subscribed to the queue, has ro
    q_sub_no_perm = 910_202  # subscribed to the queue, but no ro on its group
    s_sub = 910_203  # subscribed to the service only
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-myq")
            service_id = await _insert_service(session, "notify-svc-myq")
            group_row = (
                await session.execute(
                    text("SELECT group_id FROM queue WHERE id = :qid"), {"qid": queue_id}
                )
            ).first()
            assert group_row is not None
            group_id = int(group_row[0])

            for uid, login, mail in (
                (q_sub, "myq.sub", "myq-sub@example.com"),
                (q_sub_no_perm, "myq.noperm", "myq-noperm@example.com"),
                (s_sub, "mysvc.sub", "mysvc-sub@example.com"),
            ):
                await _insert_agent(session, user_id=uid, login=login)
                await _set_user_prefs(session, uid, mail)

            await _grant_group_permission(session, q_sub, group_id)
            await _grant_group_permission(session, s_sub, group_id)
            await _subscribe_queue(session, q_sub, queue_id)
            await _subscribe_queue(session, q_sub_no_perm, queue_id)
            await _subscribe_service(session, s_sub, service_id)

            owner_id = 910_204  # neither a queue nor a service subscriber
            await _insert_agent(session, user_id=owner_id, login="myq.owner")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_MYQ", owner_id=owner_id, queue_id=queue_id
            )
            await session.execute(
                text("UPDATE ticket SET service_id = :sid WHERE id = :tid"),
                {"sid": service_id, "tid": ticket_id},
            )
            await _insert_notification_event(
                session,
                "myqueues-notify",
                items={
                    "Events": ["NotificationMove"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentMyQueues", "AgentMyServices"],
                    "Transports": ["Email"],
                },
                subject="MYQ-SUBJECT",
                body="b",
            )
            await _insert_outbox_event(session, "NotificationMove", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        await run_notifications_tick(session_factory=factory, mail_sender=sender)
        # The Znuny seed ships its own NotificationMove rule, so scope the
        # assertion to this rule's subject.
        assert _recipients_of(sender, "MYQ-SUBJECT") == [
            "myq-sub@example.com",
            "mysvc-sub@example.com",
        ]
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_skip_recipients_and_root_agent_are_never_notified(mariadb_znuny_url: str) -> None:
    """``skip_user_ids`` (Znuny SkipRecipients) and agent 1 are filtered out."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()

    owner_id = 910_301
    responsible_id = 910_302
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-skip")
            await _insert_agent(session, user_id=owner_id, login="skip.owner")
            await _insert_agent(session, user_id=responsible_id, login="skip.responsible")
            await _set_user_prefs(session, owner_id, "skip-owner@example.com")
            await _set_user_prefs(session, responsible_id, "skip-responsible@example.com")
            await _set_user_prefs(session, 1, "root@example.com")

            ticket_id = await _insert_ticket(
                session, "NOTIFY_SKIP", owner_id=owner_id, queue_id=queue_id
            )
            await session.execute(
                text("UPDATE ticket SET responsible_user_id = :rid WHERE id = :tid"),
                {"rid": responsible_id, "tid": ticket_id},
            )
            await _insert_notification_event(
                session,
                "skip-notify",
                items={
                    "Events": ["NotificationOwnerUpdate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner", "AgentResponsible"],
                    "RecipientAgents": ["1"],
                    "Transports": ["Email"],
                },
                subject="SKIP-SUBJECT",
                body="b",
            )
            await _insert_outbox_event(
                session,
                "NotificationOwnerUpdate",
                ticket_id,
                payload=f'{{"skip_user_ids": [{owner_id}]}}',
            )
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        await run_notifications_tick(session_factory=factory, mail_sender=sender)
        # Owner is skipped (they made the change), agent 1 is never notified.
        assert _recipients_of(sender, "SKIP-SUBJECT") == ["skip-responsible@example.com"]
    finally:
        await engine.dispose()


class _FailingMailSender:
    """Sender whose every send fails, like an unreachable relay."""

    def __init__(self) -> None:
        self.attempts = 0
        self.sendmail_bcc: str | None = None

    async def send(self, message: object) -> None:
        self.attempts += 1
        raise OSError("Connect call failed ('127.0.0.1', 25)")


@pytest.mark.db
async def test_failed_sends_are_counted_not_swallowed(mariadb_znuny_url: str) -> None:
    """A tick must not report errors: 0 while every send is failing.

    Per-recipient failures were logged but never counted, so a misconfigured
    relay produced healthy-looking ticks indefinitely.
    """
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = _FailingMailSender()
    owner_id = 910_401
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-fail")
            await _insert_agent(session, user_id=owner_id, login="fail.owner")
            await _set_user_prefs(session, owner_id, "fail-owner@example.com")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_FAIL", owner_id=owner_id, queue_id=queue_id
            )
            await _insert_notification_event(
                session,
                "fail-notify",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                subject="FAIL-SUBJECT",
                body="b",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert sender.attempts == 1
        assert result["sent"] == 0
        assert result["errors"] == 1
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_stock_notification_renders_recipient_article_and_tiqora_link(
    mariadb_znuny_url: str,
) -> None:
    from tiqora.config import Settings

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            qid = await _insert_queue(session, "Notification rendering support")
            uid = 910_901
            await _insert_agent(session, user_id=uid, login="notify.render")
            await session.execute(
                text("UPDATE users SET first_name = 'Ada', last_name = 'Lovelace' WHERE id = :uid"),
                {"uid": uid},
            )
            await _set_user_prefs(session, uid, "ada@example.com", "de")
            tid = await _insert_ticket(session, "2026090810000045", owner_id=uid, queue_id=qid)
            aid = await _insert_article(session, tid, sender_type="customer", is_visible=1)
            await session.execute(
                text(
                    "INSERT INTO article_data_mime (article_id, a_from, a_subject, a_body,"
                    " a_content_type, incoming_time, create_time, create_by, change_time,"
                    " change_by)"
                    " VALUES (:aid, 'Elisa <elisa@example.com>', 'Internet kaputt',"
                    " 'Erste Zeile\nZweite Zeile\nDritte Zeile', 'text/plain', 0,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"aid": aid},
            )
            await _insert_notification_event(
                session,
                "stock-render-regression",
                items={
                    "Events": ["NotificationNewTicket"],
                    "QueueID": [str(qid)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                language="de",
                subject="Neu: <OTRS_CUSTOMER_SUBJECT[7]>",
                body=(
                    "Hallo <OTRS_NOTIFICATION_RECIPIENT_UserFirstname> "
                    "<OTRS_NOTIFICATION_RECIPIENT_UserLastname>,\n"
                    "[<OTRS_CONFIG_Ticket::Hook><OTRS_CONFIG_Ticket::HookDivider>"
                    "<OTRS_TICKET_TicketNumber>] Queue <OTRS_TICKET_Queue>\n"
                    "<OTRS_CUSTOMER_REALNAME> schrieb:\n<OTRS_CUSTOMER_BODY[2]>\n"
                    "<OTRS_CONFIG_HttpType>://<OTRS_CONFIG_FQDN>/"
                    "<OTRS_CONFIG_ScriptAlias>index.pl?Action=AgentTicketZoom;"
                    "TicketID=<OTRS_TICKET_TicketID>\n"
                    "-- <OTRS_CONFIG_NotificationSenderName>"
                ),
            )
            await _insert_outbox_event(
                session, "NotificationNewTicket", tid, payload=f'{{"article_id": {aid}}}'
            )
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")
        result = await run_notifications_tick(
            session_factory=factory,
            mail_sender=sender,
            settings=Settings(public_base_url="https://support.example.org/helpdesk/"),
        )
        assert result["sent"] == 1
        message = sender.sent[0]
        assert str(message["Subject"]) == "Neu: Interne"
        body = message.get_content()
        assert "Hallo Ada Lovelace," in body
        assert "Ticket#2026090810000045" in body
        assert "Queue Notification rendering support" in body
        assert "Elisa schrieb:\nErste Zeile\nZweite Zeile" in body
        assert "Dritte Zeile" not in body
        assert f"https://support.example.org/helpdesk/agent/tickets/{tid}" in body
        assert "-- Tiqora Notifications" in body
        assert "<OTRS_" not in body
        assert "index.pl" not in body
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_notification_recipient_placeholders_are_rendered_per_actual_agent(
    mariadb_znuny_url: str,
) -> None:
    """The recipient map is the addressed agent, not the ticket owner for every mail."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_902
    explicit_recipient_id = 910_903
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-recipient-context")
            for user_id, login, first, last, email in (
                (owner_id, "notify.recipient.owner", "Owner", "One", "owner-one@example.com"),
                (
                    explicit_recipient_id,
                    "notify.recipient.other",
                    "Recipient",
                    "Two",
                    "recipient-two@example.com",
                ),
            ):
                await _insert_agent(session, user_id=user_id, login=login)
                await session.execute(
                    text("UPDATE users SET first_name = :first, last_name = :last WHERE id = :uid"),
                    {"uid": user_id, "first": first, "last": last},
                )
                await _set_user_prefs(session, user_id, email)
            ticket_id = await _insert_ticket(
                session, "NOTIFY_RECIPIENT_CONTEXT", owner_id=owner_id, queue_id=queue_id
            )
            await _insert_notification_event(
                session,
                "recipient-context-regression",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "RecipientAgents": [str(explicit_recipient_id)],
                    "Transports": ["Email"],
                },
                subject="RECIPIENT-CONTEXT",
                body=(
                    "Hello <OTRS_NOTIFICATION_RECIPIENT_UserFullname> "
                    "(<OTRS_NOTIFICATION_RECIPIENT_UserEmail>)"
                ),
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["sent"] == 2
        rendered = {
            str(message["To"]): message.get_content()
            for message in sender.sent
            if str(message["Subject"]) == "RECIPIENT-CONTEXT"
        }
        assert rendered == {
            "owner-one@example.com": "Hello Owner One (owner-one@example.com)\n",
            "recipient-two@example.com": "Hello Recipient Two (recipient-two@example.com)\n",
        }
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_notification_uses_event_article_and_never_falls_back_to_later_article(
    mariadb_znuny_url: str,
) -> None:
    """Legacy Notification* events must quote their payload article only.

    This protects against the historical behavior where a missing article was
    replaced by the ticket's most recent customer message, potentially leaking
    an internal or unrelated later update.
    """
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_904
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-event-article")
            await _insert_agent(session, user_id=owner_id, login="notify.event.article")
            await _set_user_prefs(session, owner_id, "event-article@example.com")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_EVENT_ARTICLE", owner_id=owner_id, queue_id=queue_id
            )
            event_article_id = await _insert_article(
                session, ticket_id, sender_type="customer", is_visible=1
            )
            later_article_id = await _insert_article(
                session, ticket_id, sender_type="agent", is_visible=0
            )
            for article_id, sender_name, subject, body in (
                (event_article_id, "Event Sender", "Event subject", "event body"),
                (later_article_id, "Later Sender", "Later subject", "later body"),
            ):
                sender_address = sender_name.lower().replace(" ", ".") + "@example.com"
                await session.execute(
                    text(
                        "INSERT INTO article_data_mime "
                        "(article_id, a_from, a_subject, a_body, a_content_type, incoming_time,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:aid, :from_addr, :subject, :body, 'text/plain', 0,"
                        " current_timestamp, 1, current_timestamp, 1)"
                    ),
                    {
                        "aid": article_id,
                        "from_addr": f"{sender_name} <{sender_address}>",
                        "subject": subject,
                        "body": body,
                    },
                )
            await _insert_notification_event(
                session,
                "event-article-regression",
                items={
                    "Events": ["NotificationNewTicket"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                subject="EVENT-ARTICLE-CONTEXT",
                body=(
                    "from=<OTRS_CUSTOMER_REALNAME>; subject=<OTRS_CUSTOMER_SUBJECT>; "
                    "body=<OTRS_CUSTOMER_BODY>"
                ),
            )
            await _insert_outbox_event(
                session,
                "NotificationNewTicket",
                ticket_id,
                payload=f'{{"article_id": {event_article_id}}}',
            )
            # The row has been deleted before the worker sees it.  The later
            # article remains in the ticket and must not become a fallback.
            await _insert_outbox_event(
                session,
                "NotificationNewTicket",
                ticket_id,
                payload='{"article_id": 999999999}',
            )
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["sent"] == 2
        bodies = [
            message.get_content()
            for message in sender.sent
            if str(message["Subject"]) == "EVENT-ARTICLE-CONTEXT"
        ]
        assert bodies == [
            "from=Event Sender; subject=Event subject; body=event body\n",
            "from=; subject=; body=\n",
        ]
        assert all("Later Sender" not in body and "later body" not in body for body in bodies)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_customer_notification_does_not_expose_internal_event_article(
    mariadb_znuny_url: str,
) -> None:
    """Customer recipients must never receive body data from an internal article."""
    from tiqora.config import Settings

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-customer-article-privacy")
            await _insert_customer_user(session, "privacy.customer", "privacy@example.com")
            await session.execute(
                text(
                    "UPDATE customer_user SET first_name = 'Priya', last_name = 'Customer'"
                    " WHERE login = 'privacy.customer'"
                )
            )
            owner_id = 910_905
            await _insert_agent(session, user_id=owner_id, login="notify.customer.privacy")
            ticket_id = await _insert_ticket(
                session,
                "NOTIFY_CUSTOMER_ARTICLE_PRIVACY",
                owner_id=owner_id,
                customer_user_id="privacy.customer",
                queue_id=queue_id,
            )
            internal_article_id = await _insert_article(
                session, ticket_id, sender_type="agent", is_visible=0
            )
            await session.execute(
                text(
                    "INSERT INTO article_data_mime "
                    "(article_id, a_from, a_subject, a_body, a_content_type, incoming_time,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, 'Agent <agent@example.com>', 'Internal', 'do not disclose',"
                    " 'text/plain', 0, current_timestamp, 1, current_timestamp, 1)"
                ),
                {"aid": internal_article_id},
            )
            await _insert_notification_event(
                session,
                "customer-article-privacy-regression",
                items={
                    "Events": ["NotificationNewTicket"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["Customer"],
                    "Transports": ["Email"],
                },
                subject="CUSTOMER-ARTICLE-PRIVACY",
                body=(
                    "Hello <OTRS_NOTIFICATION_RECIPIENT_UserFullname>; "
                    "quoted=<OTRS_CUSTOMER_BODY>; sender=<OTRS_CUSTOMER_REALNAME>; "
                    "subject=<OTRS_CUSTOMER_SUBJECT>; url=<TIQORA_TICKET_URL>"
                ),
            )
            await _insert_outbox_event(
                session,
                "NotificationNewTicket",
                ticket_id,
                payload=f'{{"article_id": {internal_article_id}}}',
            )
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(
            session_factory=factory,
            mail_sender=sender,
            settings=Settings(public_base_url="https://support.example.org/helpdesk/"),
        )
        assert result["sent"] == 1
        message = next(
            message
            for message in sender.sent
            if str(message["Subject"]) == "CUSTOMER-ARTICLE-PRIVACY"
        )
        assert str(message["To"]) == "privacy@example.com"
        assert message.get_content() == (
            "Hello Priya Customer; quoted=; sender=; subject=; "
            f"url=https://support.example.org/helpdesk/portal/tickets/{ticket_id}\n"
        )
        assert "do not disclose" not in message.get_content()
        assert "Agent" not in message.get_content()
        assert "Internal" not in message.get_content()
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_untranslated_legacy_link_is_warned_about(mariadb_znuny_url: str) -> None:
    """A customized ticket URL we cannot translate now points at the Tiqora host
    with a Znuny path. The mail still goes out, but the tick says so."""
    import structlog.testing

    from tiqora.config import Settings

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_906
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-legacy-link")
            await _insert_agent(session, user_id=owner_id, login="notify.legacy.link")
            await _set_user_prefs(session, owner_id, "legacy-link@example.com")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_LEGACY_LINK", owner_id=owner_id, queue_id=queue_id
            )
            await _insert_notification_event(
                session,
                "legacy-link-warning",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                subject="LEGACY-LINK",
                # Not the stock ticket-zoom URL, so the translation cannot match it.
                body=(
                    "<OTRS_CONFIG_HttpType>://<OTRS_CONFIG_FQDN>/<OTRS_CONFIG_ScriptAlias>"
                    "index.pl?Action=AgentTicketHistory;TicketID=<OTRS_TICKET_TicketID>"
                ),
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        with structlog.testing.capture_logs() as logs:
            result = await run_notifications_tick(
                session_factory=factory,
                mail_sender=sender,
                settings=Settings(public_base_url="https://support.example.org"),
            )

        assert result["sent"] == 1
        warned = [
            entry for entry in logs if entry["event"] == "notification_legacy_link_untranslated"
        ]
        assert len(warned) == 1
        assert warned[0]["ticket_id"] == ticket_id
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_placeholder_context_is_built_once_per_recipient_kind(
    mariadb_znuny_url: str,
) -> None:
    """Only the recipient map differs per recipient — loading the ticket/queue/customer
    context per recipient turns one event into dozens of redundant query rounds."""
    from unittest.mock import AsyncMock, patch

    import tiqora.worker.notifications as notifications_module

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_907
    second_agent_id = 910_908
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-context-reuse")
            await _insert_customer_user(session, "reuse.customer", "reuse-customer@example.com")
            for user_id, login, email in (
                (owner_id, "notify.reuse.owner", "reuse-owner@example.com"),
                (second_agent_id, "notify.reuse.second", "reuse-second@example.com"),
            ):
                await _insert_agent(session, user_id=user_id, login=login)
                await _set_user_prefs(session, user_id, email)
            ticket_id = await _insert_ticket(
                session,
                "NOTIFY_CONTEXT_REUSE",
                owner_id=owner_id,
                customer_user_id="reuse.customer",
                queue_id=queue_id,
            )
            await _insert_notification_event(
                session,
                "context-reuse",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner", "Customer"],
                    "RecipientAgents": [str(second_agent_id)],
                    "Transports": ["Email"],
                },
                subject="CONTEXT-REUSE",
                body="Hallo <OTRS_NOTIFICATION_RECIPIENT_UserFullname>",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        real_loader = notifications_module.load_placeholder_context
        spy = AsyncMock(side_effect=real_loader)
        with patch.object(notifications_module, "load_placeholder_context", spy):
            result = await run_notifications_tick(session_factory=factory, mail_sender=sender)

        assert result["sent"] == 3
        # Two agents + one customer, but only an agent view and a customer view.
        assert spy.await_count == 2
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_html_notification_escapes_values_and_rewrites_encoded_link(
    mariadb_znuny_url: str,
) -> None:
    """HTML templates store their tags HTML-encoded; values substituted into them
    must be escaped and keep their line breaks."""
    from tiqora.config import Settings

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_909
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-html")
            await _insert_agent(session, user_id=owner_id, login="notify.html")
            await session.execute(
                text(
                    "UPDATE users SET first_name = 'Ada & <b>Ada</b>', last_name = 'Lovelace'"
                    " WHERE id = :uid"
                ),
                {"uid": owner_id},
            )
            await _set_user_prefs(session, owner_id, "html-agent@example.com")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_HTML", owner_id=owner_id, queue_id=queue_id
            )
            article_id = await _insert_article(
                session, ticket_id, sender_type="customer", is_visible=1
            )
            await session.execute(
                text(
                    "INSERT INTO article_data_mime (article_id, a_from, a_subject, a_body,"
                    " a_content_type, incoming_time, create_time, create_by, change_time,"
                    " change_by)"
                    " VALUES (:aid, 'Elisa <elisa@example.com>', 'HTML', 'Zeile A\nZeile B',"
                    " 'text/plain', 0, current_timestamp, 1, current_timestamp, 1)"
                ),
                {"aid": article_id},
            )
            await _insert_notification_event(
                session,
                "html-escaping",
                items={
                    "Events": ["NotificationNewTicket"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                content_type="text/html",
                subject="HTML-ESCAPING",
                body=(
                    "<p>Hallo <OTRS_NOTIFICATION_RECIPIENT_UserFirstname></p>"
                    "<p><OTRS_CUSTOMER_BODY></p>"
                    '<a href="&lt;OTRS_CONFIG_HttpType&gt;://&lt;OTRS_CONFIG_FQDN&gt;/'
                    "&lt;OTRS_CONFIG_ScriptAlias&gt;index.pl?Action=AgentTicketZoom&amp;"
                    'TicketID=&lt;OTRS_TICKET_TicketID&gt;">Ticket</a>'
                ),
            )
            await _insert_outbox_event(
                session,
                "NotificationNewTicket",
                ticket_id,
                payload=f'{{"article_id": {article_id}}}',
            )
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(
            session_factory=factory,
            mail_sender=sender,
            settings=Settings(public_base_url="https://support.example.org/helpdesk"),
        )
        assert result["sent"] == 1
        body = sender.sent[0].get_content()
        assert "<p>Hallo Ada &amp; &lt;b&gt;Ada&lt;/b&gt;</p>" in body
        assert "<p>Zeile A<br />\nZeile B</p>" in body
        assert f'href="https://support.example.org/helpdesk/agent/tickets/{ticket_id}"' in body
        assert "index.pl" not in body
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_customer_without_customer_user_row_is_greeted_by_address(
    mariadb_znuny_url: str,
) -> None:
    """A customer known only from an article has no name columns — the greeting
    must not degrade to "Hallo ,"."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_910
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-nameless-customer")
            await _insert_agent(session, user_id=owner_id, login="notify.nameless")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_NAMELESS", owner_id=owner_id, queue_id=queue_id
            )
            article_id = await _insert_article(
                session, ticket_id, sender_type="customer", is_visible=1
            )
            await session.execute(
                text(
                    "INSERT INTO article_data_mime (article_id, a_from, a_subject, a_body,"
                    " a_content_type, incoming_time, create_time, create_by, change_time,"
                    " change_by)"
                    " VALUES (:aid, 'walkin@example.com', 'Hilfe', 'Text', 'text/plain', 0,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"aid": article_id},
            )
            await _insert_notification_event(
                session,
                "nameless-customer",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["Customer"],
                    "Transports": ["Email"],
                },
                subject="NAMELESS-CUSTOMER",
                body="Hallo <OTRS_NOTIFICATION_RECIPIENT_UserFullname>,",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["sent"] == 1
        assert sender.sent[0].get_content() == "Hallo walkin@example.com,\n"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_missing_public_base_url_fails_loudly_instead_of_sending_dead_links(
    mariadb_znuny_url: str,
) -> None:
    from tiqora.config import Settings

    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_911
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-no-base-url")
            await _insert_agent(session, user_id=owner_id, login="notify.no.base.url")
            await _set_user_prefs(session, owner_id, "no-base-url@example.com")
            ticket_id = await _insert_ticket(
                session, "NOTIFY_NO_BASE_URL", owner_id=owner_id, queue_id=queue_id
            )
            await _insert_notification_event(
                session,
                "no-base-url",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner"],
                    "Transports": ["Email"],
                },
                subject="NO-BASE-URL",
                body="<TIQORA_TICKET_URL>",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(
            session_factory=factory,
            mail_sender=sender,
            settings=Settings(public_base_url="", cors_origins="*"),
        )
        assert result["sent"] == 0
        assert result["errors"] == 1
        assert sender.sent == []
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_notification_sender_is_the_queue_address_not_localhost(
    mariadb_znuny_url: str,
) -> None:
    """A hardcoded ``notifications@localhost`` sender is what relays reject; the
    queue's own system address is a mailbox this install already sends from."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sender = CapturingMailSender()
    owner_id = 910_912
    try:
        async with factory() as session:
            await _seed_tiqora_tables(session)
            await _skip_backlog(session)
            queue_id = await _insert_queue(session, "notify-q-sender-address")
            await session.execute(
                text(
                    "INSERT INTO system_address (value0, value1, comments, valid_id, queue_id,"
                    " create_by, create_time, change_by, change_time)"
                    " VALUES ('support@example.org', 'Support', '', 1, :qid, 1,"
                    " current_timestamp, 1, current_timestamp)"
                ),
                {"qid": queue_id},
            )
            address_id = (
                await session.execute(
                    text("SELECT id FROM system_address WHERE value0 = 'support@example.org'")
                )
            ).scalar_one()
            await session.execute(
                text("UPDATE queue SET system_address_id = :sid WHERE id = :qid"),
                {"sid": address_id, "qid": queue_id},
            )
            await _insert_customer_user(session, "sender.customer", "sender-cust@example.com")
            await _insert_agent(session, user_id=owner_id, login="notify.sender.address")
            await _set_user_prefs(session, owner_id, "sender-agent@example.com")
            ticket_id = await _insert_ticket(
                session,
                "NOTIFY_SENDER_ADDRESS",
                owner_id=owner_id,
                customer_user_id="sender.customer",
                queue_id=queue_id,
            )
            await _insert_notification_event(
                session,
                "sender-address",
                items={
                    "Events": ["TicketCreate"],
                    "QueueID": [str(queue_id)],
                    "Recipients": ["AgentOwner", "Customer"],
                    "Transports": ["Email"],
                },
                subject="SENDER-ADDRESS",
                body="body",
            )
            await _insert_outbox_event(session, "TicketCreate", ticket_id)
            await session.commit()
            await set_setting(session, KEY_NOTIFICATIONS_ENABLED, "1")

        result = await run_notifications_tick(session_factory=factory, mail_sender=sender)
        assert result["sent"] == 2
        assert {str(message["From"]) for message in sender.sent} == {
            "Tiqora Notifications <support@example.org>"
        }

        async with factory() as session:
            article_from = (
                await session.execute(
                    text(
                        "SELECT m.a_from FROM article a"
                        " JOIN article_data_mime m ON m.article_id = a.id"
                        " WHERE a.ticket_id = :tid ORDER BY a.id DESC LIMIT 1"
                    ),
                    {"tid": ticket_id},
                )
            ).scalar_one()
        assert article_from == "Tiqora Notifications <support@example.org>"
    finally:
        await engine.dispose()
