"""DB tests for tiqora_mail_log (outbound + inbound hooks + admin list).

Uses unique seed ids in the 93xx range so the session-scoped testcontainer
DB is shared safely across tests/files.
"""

from __future__ import annotations

from datetime import datetime
from email.message import EmailMessage
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import mail_log as admin_mail_log
from tiqora.channels.email.outbound_reply import OutboundMailError, deliver_agent_email_reply
from tiqora.channels.email.pipeline import process_message
from tiqora.channels.email.smtp import CapturingMailSender, FailingMailSender
from tiqora.config import get_settings
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraMailLog
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.mail_log import write_mail_log
from tiqora.domain.ticket_write_service import ArticleIn
from tiqora.znuny.password import hash_password
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


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
        defaults = {
            "PostmasterDefaultQueue": "Raw",
            "PostmasterDefaultState": "new",
            "PostmasterDefaultPriority": "3 normal",
            "PostmasterUserID": 1,
            "CheckEmailAddresses": 0,
        }
        return defaults.get(name)

    return SysConfig(fetch=_fetch)


def _ensure_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_mail_log"))
        conn.execute(text("DELETE FROM tiqora_mail_outbound"))
    engine.dispose()


def _seed_outbound(sync_url: str, *, ns: int) -> dict[str, Any]:
    agent_id = 9300 + ns
    group_id = 9330 + ns
    queue_id = 9300 + ns
    ticket_id = 9370 + ns
    sig_id = 9300 + ns
    sa_id = 9300 + ns
    login = f"agent.maillog.93{ns}"
    queue_name = f"MailLogQueue93{ns}"
    tn = f"20240601930{ns:03d}"

    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_mail_log"))
        conn.execute(text("DELETE FROM tiqora_mail_outbound"))
        # Idempotent cleanup of our ns block (shared session-scoped DB).
        conn.execute(text("DELETE FROM ticket WHERE id = :id"), {"id": ticket_id})
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
        conn.execute(
            text("DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid"),
            {"uid": agent_id, "gid": group_id},
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": group_id})
        conn.execute(text("DELETE FROM signature WHERE id = :id"), {"id": sig_id})
        conn.execute(text("DELETE FROM system_address WHERE id = :id"), {"id": sa_id})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": agent_id})
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :pw, 'Ada', 'Lovelace', 1, :t, 1, :t, 1)"
            ),
            {"id": agent_id, "login": login, "pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"maillog-grp-93{ns}", "t": NOW},
        )
        for key in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": agent_id, "gid": group_id, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO signature (id, name, text, content_type, comments, valid_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :name, 'Sig', 'text/plain; charset=utf-8', 't', 1, 1, :t, 1, :t)"
            ),
            {"id": sig_id, "name": f"maillog-sig-93{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO system_address (id, value0, value1, comments, valid_id, queue_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :addr, 'ML Support', 't', 1, 1, 1, :t, 1, :t)"
            ),
            {"id": sa_id, "addr": f"support{ns}@maillog.example", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, :sa, 1, :sig, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {
                "id": queue_id,
                "name": queue_name,
                "gid": group_id,
                "sa": sa_id,
                "sig": sig_id,
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, :title, :qid, 1, 1,"
                " :uid, 1, 3, 4, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": ticket_id,
                "tn": tn,
                "title": f"MailLog ticket 93{ns}",
                "qid": queue_id,
                "uid": agent_id,
                "cid": f"CUST93{ns}",
                "cuid": f"alice93{ns}@example.com",
                "t": NOW,
            },
        )
    engine.dispose()
    return {
        "agent": agent_id,
        "queue": queue_id,
        "ticket": ticket_id,
        "customer_email": f"alice93{ns}@example.com",
        "support_email": f"support{ns}@maillog.example",
        "queue_name": queue_name,
        "ns": ns,
    }


def _root_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


def _raw_email(*, subject: str, from_addr: str, to_addr: str, body: str = "Hello") -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject.replace(' ', '-').lower()}@example.com>"
    msg.set_content(body)
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# write_mail_log best-effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_mail_log_failure_does_not_propagate(mariadb_znuny_url: str) -> None:
    """Broken bind / missing table must not raise out of write_mail_log."""
    # No tables created — insert will fail; call must still return.
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            # Drop if present then call — or use a closed session.
            await session.close()
            await write_mail_log(
                session,
                direction="out",
                status="sent",
                from_addr="a@b.c",
                to_addr="d@e.f",
                subject="x",
            )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Outbound hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_send_success_writes_sent_row(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        ids = _seed_outbound(mariadb_znuny_url, ns=1)
        engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = CapturingMailSender()

        async with factory() as session, session.begin():
            article_id = await deliver_agent_email_reply(
                session,
                sysconfig,
                sender,
                ticket_id=ids["ticket"],
                queue_id=ids["queue"],
                user_id=ids["agent"],
                article=ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: MailLog ticket 931",
                    body="Thanks.",
                    channel="email",
                    to_address=ids["customer_email"],
                ),
                dispatch=True,
            )
            assert article_id > 0

        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TiqoraMailLog)
                        .where(TiqoraMailLog.ticket_id == ids["ticket"])
                        .order_by(TiqoraMailLog.id.desc())
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) >= 1
            row = rows[0]
            assert row.direction == "out"
            assert row.status == "sent"
            assert row.to_addr == ids["customer_email"]
            assert row.article_id == article_id
            assert row.smtp_code == 250
            assert row.detail and "250" in row.detail
            assert row.duration_ms is not None

        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_outbound_send_failure_writes_failed_row(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        ids = _seed_outbound(mariadb_znuny_url, ns=2)
        engine = create_async_engine(_to_async_url(mariadb_znuny_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = FailingMailSender("connection refused by test")

        async with factory() as session, session.begin():
            with pytest.raises(OutboundMailError):
                await deliver_agent_email_reply(
                    session,
                    sysconfig,
                    sender,
                    ticket_id=ids["ticket"],
                    queue_id=ids["queue"],
                    user_id=ids["agent"],
                    article=ArticleIn(
                        sender_type="agent",
                        is_visible_for_customer=True,
                        subject="Re: will fail",
                        body="nope",
                        channel="email",
                        to_address=ids["customer_email"],
                    ),
                    dispatch=True,
                )

        # Failure log must survive the outer transaction rollback.
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TiqoraMailLog).where(
                            TiqoraMailLog.ticket_id == ids["ticket"],
                            TiqoraMailLog.status == "failed",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].direction == "out"
            assert "connection refused" in (rows[0].detail or "")
            assert rows[0].article_id is None

        await engine.dispose()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Inbound hooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_accepted_writes_received_row(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()
    account = MailAccount(
        id=1,
        login="postmaster@example.com",
        pw="x",
        host="localhost",
        account_type="IMAP",
        queue_id=1,
        trusted=0,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )
    raw = _raw_email(
        subject="New mail log inbound 931",
        from_addr="customer@example.com",
        to_addr="support@example.com",
        body="Please help",
    )
    try:
        async with factory() as session, session.begin():
            result = await process_message(
                session,
                factory,
                sysconfig,
                raw=raw,
                account=account,
                user_id=1,
            )
            assert result.outcome == "new_ticket"
            assert result.ticket_id is not None

        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TiqoraMailLog).where(
                            TiqoraMailLog.direction == "in",
                            TiqoraMailLog.status == "received",
                            TiqoraMailLog.subject == "New mail log inbound 931",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].ticket_id == result.ticket_id
            assert "customer@example.com" in rows[0].from_addr
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inbound_filtered_writes_filtered_row(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()
    account = MailAccount(
        id=1,
        login="postmaster@example.com",
        pw="x",
        host="localhost",
        account_type="IMAP",
        queue_id=1,
        trusted=0,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )
    # Craft a message that will be ignored via postmaster filter setting X-OTRS-Ignore.
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                " VALUES ('maillog-ignore-93', 0, 'Match', 'Subject', 'MAILLOG-DROP-93', 0)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                " VALUES ('maillog-ignore-93', 0, 'Set', 'X-OTRS-Ignore', 'yes', 0)"
            )
        )
        await session.commit()

    raw = _raw_email(
        subject="MAILLOG-DROP-93 please ignore",
        from_addr="spam@example.com",
        to_addr="support@example.com",
    )
    try:
        async with factory() as session, session.begin():
            result = await process_message(
                session,
                factory,
                sysconfig,
                raw=raw,
                account=account,
                user_id=1,
            )
            assert result.outcome == "ignored"

        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TiqoraMailLog).where(
                            TiqoraMailLog.direction == "in",
                            TiqoraMailLog.status == "filtered",
                            TiqoraMailLog.subject.like("%MAILLOG-DROP-93%"),
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert "Ignore" in (rows[0].detail or "") or rows[0].detail
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Admin list filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_filters_direction_status_q(mariadb_znuny_url: str) -> None:
    _ensure_tables(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            await write_mail_log(
                session,
                direction="out",
                status="sent",
                from_addr="a@example.com",
                to_addr="b@example.com",
                subject="Alpha unique subject 93",
            )
            await write_mail_log(
                session,
                direction="in",
                status="filtered",
                from_addr="c@example.com",
                to_addr="d@example.com",
                subject="Beta other subject 93",
            )
            await write_mail_log(
                session,
                direction="out",
                status="failed",
                from_addr="e@example.com",
                to_addr="f@example.com",
                subject="Gamma fail 93",
            )

        async with factory() as session:
            out_only = await admin_mail_log.list_mail_log(
                _root_user(),
                session,
                admin_mail_log.MailLogListParams(
                    page=1, page_size=50, valid="all", direction="out"
                ),
            )
            assert all(i.direction == "out" for i in out_only.items)
            assert out_only.total >= 2

            failed = await admin_mail_log.list_mail_log(
                _root_user(),
                session,
                admin_mail_log.MailLogListParams(
                    page=1, page_size=50, valid="all", status="failed"
                ),
            )
            assert all(i.status == "failed" for i in failed.items)
            assert any("Gamma" in i.subject for i in failed.items)

            q = await admin_mail_log.list_mail_log(
                _root_user(),
                session,
                admin_mail_log.MailLogListParams(
                    page=1, page_size=50, valid="all", q="Alpha unique"
                ),
            )
            assert q.total >= 1
            assert any("Alpha unique" in i.subject for i in q.items)

            detail = await admin_mail_log.get_mail_log(q.items[0].id, _root_user(), session)
            assert detail.id == q.items[0].id
            assert detail.subject == q.items[0].subject
    finally:
        await engine.dispose()
