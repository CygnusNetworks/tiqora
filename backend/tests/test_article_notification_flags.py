"""ArticleCreate notification flags (Znuny MIMEBase::ArticleCreate) + worker recipients.

Seed ids use the 7600-7612 band (users 7600-7604, queue 7611, ticket 7612).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.outbound_reply import prepare_outgoing_agent_email
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.config import get_settings
from tiqora.domain.settings_store import KEY_NOTIFICATION_SENDER_EMAIL, set_setting
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.worker.notifications import process_event
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2026, 2, 1, 12, 0, 0)
ACTOR = 7600
EXCLUDED = 7601
MUTED = 7602
AGENT3 = 7603
INVALID = 7604
QUEUE_ID = 7611
TICKET_ID = 7612
USER_IDS = (ACTOR, EXCLUDED, MUTED, AGENT3, INVALID)
NOTIFY_NAME = "w1flags-notify"
NOTIFY_SUBJECT = "W1FLAGS-RECIPIENTS"


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


_NOTIF_IDS = f"(SELECT id FROM notification_event WHERE name = '{NOTIFY_NAME}')"


def _cleanup(conn: Any) -> None:
    key_col = "`key`" if conn.dialect.name == "mysql" else '"key"'
    conn.execute(text(f"DELETE FROM tiqora_settings WHERE {key_col} = 'notification.sender_email'"))
    ids = ", ".join(map(str, USER_IDS))
    for sql in (
        f"DELETE FROM ticket_history WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {TICKET_ID}",
        "DELETE FROM article_data_mime WHERE article_id IN"
        f" (SELECT id FROM article WHERE ticket_id = {TICKET_ID})",
        f"DELETE FROM article WHERE ticket_id = {TICKET_ID}",
        f"DELETE FROM ticket WHERE id = {TICKET_ID}",
        f"DELETE FROM queue WHERE id = {QUEUE_ID}",
        f"DELETE FROM notification_event_item WHERE notification_id IN {_NOTIF_IDS}",
        f"DELETE FROM notification_event_message WHERE notification_id IN {_NOTIF_IDS}",
        f"DELETE FROM notification_event WHERE name = '{NOTIFY_NAME}'",
        f"DELETE FROM user_preferences WHERE user_id IN ({ids})",
        f"DELETE FROM users WHERE id IN ({ids})",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        users = (
            (ACTOR, "w1f.actor", "Act", "Or", 1, "actor@example.org"),
            (EXCLUDED, "w1f.excluded", "Ex", "Cluded", 1, "excluded@example.org"),
            (MUTED, "w1f.muted", "Mu", "Ted", 1, "muted@example.org"),
            (AGENT3, "w1f.agent3", "Agent", "Three", 1, "agent3@example.org"),
            (INVALID, "w1f.invalid", "In", "Valid", 2, "invalid@example.org"),
        )
        for uid, login, first, last, valid, email in users:
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:i, :l, 'x', :f, :ln, :v, :t, 1, :t, 1)"
                ),
                {"i": uid, "l": login, "f": first, "ln": last, "v": valid, "t": NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                    " VALUES (:u, 'UserEmail', :e)"
                ),
                {"u": uid, "e": email},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'W1FlagsQueue', 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, '20260201760001', 'Flags ticket', :q, 1, 1, :o, 1, 3, 4,"
                " 'CUST1', 'alice@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": TICKET_ID, "q": QUEUE_ID, "o": ACTOR, "t": NOW},
        )
    engine.dispose()


def _query(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        rows = result.fetchall() if result.returns_rows else []
    engine.dispose()
    return rows


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield sync_url, factory
    finally:
        await engine.dispose()
        e = create_engine(sync_url)
        with e.begin() as conn:
            _cleanup(conn)
        e.dispose()


async def _add(factory: async_sessionmaker[AsyncSession], **flags: Any) -> int:
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=False,
        subject="Flag note",
        body="body",
        channel="note",
        **flags,
    )
    async with factory() as session, session.begin():
        return await add_article(
            session,
            ticket_id=TICKET_ID,
            article=article,
            user_id=ACTOR,
            sysconfig=_sysconfig(),
        )


def _events(url: str) -> list[tuple[str, dict[str, Any]]]:
    rows = _query(
        url,
        "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id",
        t=TICKET_ID,
    )
    return [(str(r[0]), json.loads(r[1])) for r in rows]


async def test_no_agent_notify_suppresses_legacy_event(env: Any) -> None:
    url, factory = env
    await _add(factory)
    assert [e for e, _ in _events(url)] == ["NotificationAddNote", "ArticleCreate"]
    _query(url, "DELETE FROM tiqora_event_outbox WHERE ticket_id = :t", t=TICKET_ID)
    await _add(factory, no_agent_notify=True)
    assert [e for e, _ in _events(url)] == ["ArticleCreate"]


async def test_exclude_lists_extend_skip_user_ids(env: Any) -> None:
    url, factory = env
    await _add(
        factory,
        exclude_notification_user_ids=(EXCLUDED,),
        exclude_mute_notification_user_ids=(MUTED,),
    )
    [payload] = [p for e, p in _events(url) if e == "NotificationAddNote"]
    assert set(payload["skip_user_ids"]) == {ACTOR, EXCLUDED, MUTED}
    assert "recipient_user_ids" not in payload


async def test_force_ids_become_recipients_and_to_line(env: Any) -> None:
    url, factory = env
    article_id = await _add(factory, force_notification_user_ids=(AGENT3,))
    [payload] = [p for e, p in _events(url) if e == "NotificationAddNote"]
    assert payload["recipient_user_ids"] == [AGENT3]
    [(a_to,)] = _query(
        url, "SELECT a_to FROM article_data_mime WHERE article_id = :a", a=article_id
    )
    assert a_to.endswith('"Agent Three" <agent3@example.org>')
    assert a_to == '"Agent Three" <agent3@example.org>'

    # id 1, excluded ids and invalid users are never added to the To line; an
    # existing To keeps its separator.
    article_id = await _add(
        factory,
        to_address="x@example.org",
        force_notification_user_ids=(1, MUTED, INVALID, AGENT3),
        exclude_notification_user_ids=(MUTED,),
    )
    [(a_to,)] = _query(
        url, "SELECT a_to FROM article_data_mime WHERE article_id = :a", a=article_id
    )
    assert a_to == 'x@example.org, "Agent Three" <agent3@example.org>'


async def test_history_comment_override(env: Any) -> None:
    url, factory = env
    article_id = await _add(factory, history_comment_override="%%GenericInterface Note")
    other_id = await _add(factory)
    q = "SELECT name FROM ticket_history WHERE article_id = :a"
    assert _query(url, q, a=article_id) == [("%%GenericInterface Note",)]
    assert _query(url, q, a=other_id) == [("%% Flag note",)]


async def _insert_recipientless_notification(url: str) -> None:
    _query(
        url,
        "INSERT INTO notification_event (name, valid_id, create_time, create_by,"
        " change_time, change_by) VALUES (:n, 1, :t, 1, :t, 1)",
        n=NOTIFY_NAME,
        t=NOW,
    )
    nid = _query(url, "SELECT id FROM notification_event WHERE name = :n", n=NOTIFY_NAME)[0][0]
    # No Recipients / RecipientAgents at all: only pre-calculated ids can match.
    for key, value in (
        ("Events", "NotificationAddNote"),
        ("Transports", "Email"),
        ("QueueID", str(QUEUE_ID)),
    ):
        _query(
            url,
            "INSERT INTO notification_event_item (notification_id, event_key, event_value)"
            " VALUES (:n, :k, :v)",
            n=nid,
            k=key,
            v=value,
        )
    _query(
        url,
        "INSERT INTO notification_event_message (notification_id, subject, text,"
        " content_type, language) VALUES (:n, :s, 'body', 'text/plain', 'en')",
        n=nid,
        s=NOTIFY_SUBJECT,
    )


async def _run(factory: async_sessionmaker[AsyncSession], payload: dict[str, Any]) -> list[str]:
    sender = CapturingMailSender()
    async with factory() as session, session.begin():
        await process_event(
            session,
            SysConfig(session),
            sender,
            event_type="NotificationAddNote",
            ticket_id=TICKET_ID,
            payload=payload,
            user_id=ACTOR,
            settings=get_settings(),
        )
    return sorted(str(m["To"]) for m in sender.sent if NOTIFY_SUBJECT in str(m["Subject"]))


async def test_worker_adds_precalculated_recipients(env: Any) -> None:
    url, factory = env
    await _insert_recipientless_notification(url)
    async with factory() as session:
        await set_setting(session, KEY_NOTIFICATION_SENDER_EMAIL, "support@example.org")
    assert await _run(factory, {}) == []
    assert await _run(factory, {"recipient_user_ids": [AGENT3]}) == ["agent3@example.org"]
    # Still subject to the skip list, validity and the root-agent rule.
    assert await _run(factory, {"recipient_user_ids": [AGENT3], "skip_user_ids": [AGENT3]}) == []
    assert await _run(factory, {"recipient_user_ids": [1, INVALID]}) == []


async def test_signature_skipped_when_append_signature_false(env: Any) -> None:
    _url, factory = env

    async def _prepared(**flags: Any) -> str:
        async with factory() as session:
            prepared = await prepare_outgoing_agent_email(
                session,
                _sysconfig(),
                ticket_id=TICKET_ID,
                queue_id=QUEUE_ID,
                user_id=ACTOR,
                article=ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: x",
                    body="Thanks for writing in.",
                    channel="email",
                    to_address="alice@example.org",
                    **flags,
                ),
            )
        return prepared.body

    with_sig = await _prepared()
    without_sig = await _prepared(append_signature=False)
    assert without_sig == "Thanks for writing in."
    assert with_sig.startswith("Thanks for writing in.")
    assert len(with_sig) > len(without_sig)


async def test_signature_directly_after_body_for_generic_interface(env: Any) -> None:
    """GI ArticleSend: ``Body . $Signature`` -- no "-- " separator like the composer's."""
    _url, factory = env

    async def _prepared(**flags: Any) -> str:
        async with factory() as session:
            prepared = await prepare_outgoing_agent_email(
                session,
                _sysconfig(),
                ticket_id=TICKET_ID,
                queue_id=QUEUE_ID,
                user_id=ACTOR,
                article=ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: x",
                    body="Thanks for writing in.",
                    channel="email",
                    to_address="alice@example.org",
                    **flags,
                ),
            )
        return prepared.body

    composer = await _prepared()
    direct = await _prepared(signature_directly_after_body=True)
    assert "\n\n-- \n" in composer
    assert direct.startswith("Thanks for writing in.")
    assert "\n\n-- \n" not in direct
    assert len(direct) > len("Thanks for writing in.")
    # the same signature text, appended as is (the composer strips a leading "-- " line)
    assert composer.split("\n\n-- \n", 1)[1].strip() in direct
