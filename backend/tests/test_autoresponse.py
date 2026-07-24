"""Tests for channels/email/autoresponse.py — Znuny-parity auto-response dispatch.

Covers loop-protection (``X-OTRS-Loop`` header + day-counter), closed-ticket
suppression, and normal auto-reply/-reject/-followup dispatch, with a mocked
SMTP sender. Follows the seeding conventions of test_agent_email_outbound.py
(explicit PKs in a private id band, cleaned up idempotently per test).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.autoresponse import send_auto_response
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

# auto_response_type ids seeded by initial_insert.*.sql (fixed, not test-owned):
# 1=auto reply, 2=auto reject, 3=auto follow up, 4=auto reply/new ticket, 5=auto remove.

# ticket_state ids seeded by initial_insert.*.sql: 1=new (type new), 4=open (type open),
# 2/3=closed successful/unsuccessful (type closed).


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


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _seed(
    sync_url: str,
    *,
    ns: int,
    auto_response_type_id: int,
    state_id: int = 4,
    body_tpl: str = "Hi <OTRS_CUSTOMER_SUBJECT[5]>, we got ticket <OTRS_TICKET_TicketNumber>.",
    subject_tpl: str = "Re: <OTRS_TICKET_Title>",
) -> dict[str, Any]:
    """Seed queue + auto_response + queue_auto_response + one ticket.

    ``ns`` keeps ids in a private 92xx band so this file's tests do not
    collide with each other or with test_agent_email_outbound.py's 91xx band.
    """
    group_id = 9230 + ns
    queue_id = 9200 + ns
    ticket_id = 9270 + ns
    sa_id = 9200 + ns
    ar_id = 9200 + ns
    tn = f"20240601920{ns:03d}"
    queue_name = f"AutoRespQueue92{ns}"

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM ticket WHERE id = :id"), {"id": ticket_id})
        conn.execute(text("DELETE FROM queue_auto_response WHERE queue_id = :id"), {"id": queue_id})
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
        conn.execute(text("DELETE FROM auto_response WHERE id = :id"), {"id": ar_id})
        conn.execute(text("DELETE FROM system_address WHERE id = :id"), {"id": sa_id})
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": group_id})
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"autoresp-grp-92{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO system_address (id, value0, value1, comments, valid_id, queue_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :addr, 'Support Bot', 'test', 1, 1, 1, :t, 1, :t)"
            ),
            {"id": sa_id, "addr": f"autoresp{ns}@example.com", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, :sa, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": queue_id, "name": queue_name, "gid": group_id, "sa": sa_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO auto_response (id, type_id, system_address_id, name, text0, text1,"
                " content_type, comments, valid_id, create_by, create_time, change_by,"
                " change_time)"
                " VALUES (:id, :tid, :sa, :name, :subj, :body, 'text/plain', 'test', 1, 1, :t,"
                " 1, :t)"
            ),
            {
                "id": ar_id,
                "tid": auto_response_type_id,
                "sa": sa_id,
                "name": f"autoresp-{ns}",
                "subj": subject_tpl,
                "body": body_tpl,
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO queue_auto_response (queue_id, auto_response_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, :arid, :t, 1, :t, 1)"
            ),
            {"qid": queue_id, "arid": ar_id, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, :title, :qid, 1, 1,"
                " 1, 1, 3, :sid, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": ticket_id,
                "tn": tn,
                "title": f"Autoresponse ticket 92{ns}",
                "qid": queue_id,
                "sid": state_id,
                "cid": f"CUST92{ns}",
                "cuid": f"cust92{ns}@example.com",
                "t": NOW,
            },
        )
    engine.dispose()
    return {
        "queue": queue_id,
        "ticket": ticket_id,
        "tn": tn,
        "customer_email": f"cust92{ns}@example.com",
        "support_email": f"autoresp{ns}@example.com",
        "ns": ns,
    }


async def _history_names(
    factory: async_sessionmaker[AsyncSession], ticket_id: int
) -> list[tuple[str, str]]:
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tht.name, th.name FROM ticket_history th"
                    " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                    " WHERE th.ticket_id = :tid ORDER BY th.id"
                ),
                {"tid": ticket_id},
            )
        ).all()
    return [(str(r[0]), str(r[1])) for r in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_auto_reply_dispatch_sends_mail_and_writes_article(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=1, auto_response_type_id=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reply",
            recipient_from_header=ids["customer_email"],
            orig_subject="Help me please",
            orig_body="I need assistance.",
            orig_message_id="<cust-orig@example.com>",
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is not None and article_id > 0
    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg["To"] == ids["customer_email"]
    assert ids["support_email"] in (msg["From"] or "")
    assert ids["tn"] in (msg.get_content() or "")

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a.is_visible_for_customer, m.a_to, m.a_in_reply_to"
                    " FROM article a JOIN article_data_mime m ON m.article_id = a.id"
                    " WHERE a.id = :aid"
                ),
                {"aid": article_id},
            )
        ).first()
        assert row is not None
        assert int(row[0]) == 1
        assert row[1] == ids["customer_email"]
        assert row[2] == "<cust-orig@example.com>"

    history = await _history_names(factory, ids["ticket"])
    assert ("SendAutoReply", "%%" + ids["customer_email"]) not in history

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_auto_reject_dispatch(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=2, auto_response_type_id=2)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reject",
            recipient_from_header=ids["customer_email"],
            orig_subject="Follow-up rejected",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is not None
    assert len(sender.sent) == 1

    async with factory() as session:
        hist_type = (
            await session.execute(
                text(
                    "SELECT tht.name FROM article a"
                    " JOIN ticket_history th ON th.article_id = a.id"
                    " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                    " WHERE a.id = :aid"
                ),
                {"aid": article_id},
            )
        ).scalar()
        assert hist_type == "SendAutoReject"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_auto_follow_up_dispatch(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=3, auto_response_type_id=3)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto follow up",
            recipient_from_header=ids["customer_email"],
            orig_subject="More info",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is not None
    assert len(sender.sent) == 1

    async with factory() as session:
        hist_type = (
            await session.execute(
                text(
                    "SELECT tht.name FROM article a"
                    " JOIN ticket_history th ON th.article_id = a.id"
                    " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                    " WHERE a.id = :aid"
                ),
                {"aid": article_id},
            )
        ).scalar()
        assert hist_type == "SendAutoFollowUp"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_x_otrs_loop_header_suppresses_response(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Inbound mail carrying X-OTRS-Loop (not 'false'/'no') must not get a reply."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=4, auto_response_type_id=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reply",
            recipient_from_header=ids["customer_email"],
            orig_subject="Auto-generated bounce",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop="yes",
            user_id=1,
        )

    assert article_id is None
    assert sender.sent == []
    history = await _history_names(factory, ids["ticket"])
    assert any(
        htype == "Misc" and "doesn't want an auto-response" in name for htype, name in history
    )
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_x_otrs_loop_false_does_not_suppress(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """X-OTRS-Loop: false / no is the documented opt-out from the loop suppression."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=5, auto_response_type_id=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reply",
            recipient_from_header=ids["customer_email"],
            orig_subject="normal mail",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop="false",
            user_id=1,
        )

    assert article_id is not None
    assert len(sender.sent) == 1
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_closed_ticket_suppresses_auto_reply(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """auto reply on a closed/removed ticket must be suppressed (Znuny early-return)."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=6, auto_response_type_id=1, state_id=2)  # closed successful
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reply",
            recipient_from_header=ids["customer_email"],
            orig_subject="re-opened by mistake?",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is None
    assert sender.sent == []
    history = await _history_names(factory, ids["ticket"])
    assert any(htype == "Misc" and "state-type 'closed'" in name for htype, name in history)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_closed_ticket_does_not_suppress_follow_up(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The closed-state early-return is specific to 'auto reply', not other types."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=7, auto_response_type_id=3, state_id=2)  # closed successful
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto follow up",
            recipient_from_header=ids["customer_email"],
            orig_subject="more info on closed ticket",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is not None
    assert len(sender.sent) == 1
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_day_counter_loop_protection_suppresses_after_max(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """ticket_loop_protection day-counter blocks once max_emails_per_address is exceeded."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=8, auto_response_type_id=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async def _fetch(name: str) -> Any:
        if name == "PostmasterMaxEmailsPerAddress":
            return {ids["customer_email"]: 1}
        return None

    sysconfig = SysConfig(fetch=_fetch)

    # First send: allowed, records loop-protection.
    sender1 = CapturingMailSender()
    async with factory() as session, session.begin():
        article_id_1 = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender1,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reply",
            recipient_from_header=ids["customer_email"],
            orig_subject="first",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )
    assert article_id_1 is not None
    assert len(sender1.sent) == 1

    # Second send to the same address: max_emails_per_address=1 already hit.
    sender2 = CapturingMailSender()
    async with factory() as session, session.begin():
        article_id_2 = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender2,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reply",
            recipient_from_header=ids["customer_email"],
            orig_subject="second",
            orig_body="body",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )
    assert article_id_2 is None
    assert sender2.sent == []
    history = await _history_names(factory, ids["ticket"])
    assert any(htype == "LoopProtection" for htype, _ in history)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_no_auto_response_configured_returns_none(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """No queue_auto_response row for the requested type → no-op, no crash."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=9, auto_response_type_id=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            auto_response_type="auto reject",  # only "auto reply" (type 1) is configured
            recipient_from_header=ids["customer_email"],
            orig_subject="s",
            orig_body="b",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is None
    assert sender.sent == []
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_missing_ticket_returns_none(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sender = CapturingMailSender()
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        article_id = await send_auto_response(
            session,
            factory,
            sysconfig,
            sender,
            ticket_id=999_999_999,
            queue_id=1,
            auto_response_type="auto reply",
            recipient_from_header="someone@example.com",
            orig_subject="s",
            orig_body="b",
            orig_message_id=None,
            orig_x_otrs_loop=None,
            user_id=1,
        )

    assert article_id is None
    assert sender.sent == []
    await engine.dispose()
