"""Agent email reply outbound SMTP + queue signature (bugs #2 / #9).

Uses UNIQUE seed ids/logins per test case (91xx range with * 10 + offset)
so the session-scoped testcontainer DB is shared safely across tests/files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.outbound_reply import (
    OutboundMailError,
    append_signature,
    generate_message_id,
)
from tiqora.channels.email.smtp import CapturingMailSender, FailingMailSender, build_message
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraMailOutbound  # noqa: F401 — register for create_all
from tiqora.domain.ticket_write_service import ArticleIn, TicketWriteService
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
        return None

    return SysConfig(fetch=_fetch)


def _seed(sync_url: str, *, ns: int) -> dict[str, Any]:
    """Seed one isolated agent/queue/ticket set.

    ``ns`` is a small integer (1..9) so ids stay in the 91xx SMALLINT-safe band
    and do not collide with other test files or sibling cases.
    """
    # Explicit PKs only for tables we fully control (users/queue/ticket).
    # Seed articles omit `id` so AUTO_INCREMENT is not advanced past our
    # planned ids (inserting a high explicit article id would make the next
    # add_article() claim the following id and collide with a later seed).
    agent_id = 9100 + ns
    group_id = 9130 + ns
    queue_id = 9100 + ns
    ticket_id = 9170 + ns
    sig_id = 9100 + ns
    sa_id = 9100 + ns
    login = f"agent.outbound.91{ns}"
    queue_name = f"OutboundQueue91{ns}"
    sig_name = f"outbound-sig-91{ns}"
    tn = f"20240601910{ns:03d}"

    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Isolate from admin mail tests sharing the session-scoped container.
        conn.execute(text("DELETE FROM tiqora_mail_outbound"))
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
            {"id": group_id, "name": f"outbound-grp-91{ns}", "t": NOW},
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
                " VALUES (:id, :name, :txt, 'text/plain; charset=utf-8', 'test', 1, 1, :t, 1, :t)"
            ),
            {
                "id": sig_id,
                "name": sig_name,
                "txt": (
                    "\nYour Ticket-Team\n\n <OTRS_AGENT_UserFirstname> <OTRS_AGENT_UserLastname>\n"
                ),
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO system_address (id, value0, value1, comments, valid_id, queue_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :addr, 'Outbound Support', 'test', 1, 1, 1, :t, 1, :t)"
            ),
            {"id": sa_id, "addr": f"support{ns}@outbound.example", "t": NOW},
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
                "title": f"Outbound ticket 91{ns}",
                "qid": queue_id,
                "uid": agent_id,
                "cid": f"CUST91{ns}",
                "cuid": f"alice91{ns}@example.com",
                "t": NOW,
            },
        )
        cust_st = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = 'customer' LIMIT 1")
        ).scalar()
        email_ch = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Email' LIMIT 1")
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " insert_fingerprint, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :st, :ch, 1, 0, :fp, :t, 1, :t, 1)"
            ),
            {
                "tid": ticket_id,
                "st": cust_st,
                "ch": email_ch,
                "fp": f"fp-cust-91{ns}",
                "t": NOW,
            },
        )
        seed_article_id = conn.execute(
            text("SELECT id FROM article WHERE insert_fingerprint = :fp LIMIT 1"),
            {"fp": f"fp-cust-91{ns}"},
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_from, a_to, a_subject,"
                " a_message_id, a_content_type, a_body, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :frm, :to, 'Help please', :mid,"
                " 'text/plain; charset=utf-8', 'I need help', 0, :t, 1, :t, 1)"
            ),
            {
                "aid": seed_article_id,
                "frm": f"alice91{ns}@example.com",
                "to": f"support{ns}@outbound.example",
                "mid": f"<cust-orig-91{ns}@example.com>",
                "t": NOW,
            },
        )
    engine.dispose()
    return {
        "agent": agent_id,
        "queue": queue_id,
        "ticket": ticket_id,
        "customer_email": f"alice91{ns}@example.com",
        "support_email": f"support{ns}@outbound.example",
        "orig_mid": f"<cust-orig-91{ns}@example.com>",
        "ns": ns,
    }


# ---------------------------------------------------------------------------
# Unit (no DB)
# ---------------------------------------------------------------------------


def test_append_signature_plaintext_once() -> None:
    body = "Hello customer,"
    sig = "Best regards\nSupport"
    out = append_signature(body, sig, content_type="text/plain; charset=utf-8")
    assert "Hello customer," in out
    assert "Best regards" in out
    assert out.count("Best regards") == 1
    out2 = append_signature(out, sig, content_type="text/plain")
    assert out2 == out


def test_append_signature_strips_leading_delimiter() -> None:
    """Stored signatures often start with -- / -- ; only one RFC-3676 delim remains."""
    out = append_signature(
        "Hello customer,",
        "--\nAlice Example\nSupport",
        content_type="text/plain; charset=utf-8",
    )
    assert out == "Hello customer,\n\n-- \nAlice Example\nSupport"
    assert out.count("--") == 1

    out_sp = append_signature(
        "Hi",
        "-- \nBob\nTeam",
        content_type="text/plain",
    )
    assert out_sp == "Hi\n\n-- \nBob\nTeam"
    assert out_sp.count("--") == 1

    out_html = append_signature(
        "<p>Hi</p>",
        "--\nTeam Support",
        content_type="text/html",
    )
    assert out_html.count("--") == 1
    assert "Team Support" in out_html


def test_append_signature_html() -> None:
    out = append_signature("<p>Hi</p>", "Team Support", content_type="text/html")
    assert "<p>Hi</p>" in out
    assert "Team Support" in out
    assert "<br" in out.lower() or "<pre" in out.lower()


def test_generate_message_id_bracketed() -> None:
    mid = generate_message_id(domain="example.test")
    assert mid.startswith("<") and mid.endswith(">")
    assert "example.test" in mid


def test_build_message_agent_headers() -> None:
    msg = build_message(
        from_addr="Support <support@example.com>",
        to_addrs="alice@example.com",
        cc_addrs="bob@example.com",
        bcc_addrs="secret@example.com",
        subject="Re: ticket",
        body="body with signature",
        content_type="text/plain",
        in_reply_to="<orig@example.com>",
        references="<orig@example.com>",
        reply_to="noreply@example.com",
        message_id="<agent-1@example.com>",
        loop_hint=False,
    )
    assert msg["Message-ID"] == "<agent-1@example.com>"
    assert msg["In-Reply-To"] == "<orig@example.com>"
    assert msg["References"] == "<orig@example.com>"
    assert msg["Reply-To"] == "noreply@example.com"
    assert msg["Bcc"] == "secret@example.com"
    assert "X-OTRS-Loop" not in msg


# ---------------------------------------------------------------------------
# DB integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_email_reply_sends_and_stores(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=1)
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = CapturingMailSender()

        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
            article_id = await svc.add_article(
                ids["agent"],
                ids["ticket"],
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=False,  # forced True for email agent
                    subject="Re: Outbound ticket 911",
                    body="Thanks for your report — we are looking into it.",
                    channel="email",
                    to_address=ids["customer_email"],
                    cc="cc91@example.com",
                    in_reply_to=ids["orig_mid"],
                    references=ids["orig_mid"],
                ),
            )
            assert article_id > 0

        assert len(sender.sent) == 1
        msg = sender.sent[0]
        assert msg["To"] == ids["customer_email"]
        assert msg["Cc"] == "cc91@example.com"
        assert msg["Subject"] == "Re: Outbound ticket 911"
        body = msg.get_content()
        assert "Thanks for your report" in body
        assert "Your Ticket-Team" in body
        assert "Ada" in body and "Lovelace" in body
        assert msg["In-Reply-To"] == ids["orig_mid"]
        assert msg["Message-ID"] and msg["Message-ID"].startswith("<")
        assert "X-OTRS-Loop" not in msg
        assert ids["support_email"] in (msg["From"] or "")

        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT a.is_visible_for_customer, m.a_message_id, m.a_in_reply_to,"
                        " m.a_body, m.a_from, m.a_to, m.a_message_id_md5"
                        " FROM article a"
                        " JOIN article_data_mime m ON m.article_id = a.id"
                        " WHERE a.id = :aid"
                    ),
                    {"aid": article_id},
                )
            ).first()
            assert row is not None
            assert int(row[0]) == 1, "agent email must be customer-visible"
            assert row[1] == msg["Message-ID"], "stored Message-ID must match sent mail"
            assert row[2] == ids["orig_mid"]
            assert "Your Ticket-Team" in (row[3] or "")
            assert "Ada" in (row[3] or "")
            assert ids["support_email"] in (row[4] or "")
            assert row[5] == ids["customer_email"]
            assert row[6] is not None, "a_message_id_md5 required for Znuny follow-up"

        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_email_smtp_failure_raises_no_silent_success(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=2)
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = FailingMailSender("connection refused by test")

        async with factory() as session:
            before = int(
                (
                    await session.execute(
                        text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                        {"tid": ids["ticket"]},
                    )
                ).scalar_one()
            )

        with pytest.raises(OutboundMailError, match="SMTP send failed"):
            async with factory() as session, session.begin():
                svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
                await svc.add_article(
                    ids["agent"],
                    ids["ticket"],
                    ArticleIn(
                        sender_type="agent",
                        is_visible_for_customer=True,
                        subject="Re: will fail",
                        body="this must not be stored",
                        channel="email",
                        to_address=ids["customer_email"],
                    ),
                )

        assert sender.attempts == 1

        async with factory() as session:
            after = int(
                (
                    await session.execute(
                        text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                        {"tid": ids["ticket"]},
                    )
                ).scalar_one()
            )
            assert after == before, "send-then-store: no article after SMTP failure"

        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_email_smtp_disabled_stores_without_send(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default / TIQORA_SMTP_ENABLED=0: store the reply, do not SMTP, do not 502."""
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "0")
    get_settings.cache_clear()
    try:
        assert get_settings().smtp_enabled is False
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=4)
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = CapturingMailSender()

        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
            article_id = await svc.add_article(
                ids["agent"],
                ids["ticket"],
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: offline store",
                    body="Agent typed this with no relay configured.",
                    channel="email",
                    to_address=ids["customer_email"],
                    in_reply_to=ids["orig_mid"],
                ),
            )
            assert article_id > 0

        assert sender.sent == [], "smtp disabled must not attempt delivery"

        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT a.is_visible_for_customer, m.a_body, m.a_message_id,"
                        " m.a_in_reply_to, m.a_to"
                        " FROM article a"
                        " JOIN article_data_mime m ON m.article_id = a.id"
                        " WHERE a.id = :aid"
                    ),
                    {"aid": article_id},
                )
            ).first()
            assert row is not None
            assert int(row[0]) == 1
            body = row[1] or ""
            assert "Agent typed this with no relay configured." in body
            assert "Your Ticket-Team" in body, "signature still appended when not dispatched"
            assert "Ada" in body
            assert row[2] and str(row[2]).startswith("<")
            assert row[3] == ids["orig_mid"]
            assert row[4] == ids["customer_email"]

        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_internal_note_does_not_send_mail(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=3)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()
    sender = CapturingMailSender()

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
        aid = await svc.add_article(
            ids["agent"],
            ids["ticket"],
            ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="Internal note",
                body="not for customer",
                channel="note",
            ),
        )
        assert aid > 0

    assert sender.sent == []
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_email_uses_db_outbound_when_enabled(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB ``tiqora_mail_outbound.enabled`` dispatches even when env SMTP is off."""
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "0")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=5)
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = CapturingMailSender()

        async with factory() as session:
            from tiqora.config import get_settings as _gs
            from tiqora.domain.mail_outbound import upsert_mail_outbound

            await upsert_mail_outbound(
                session,
                settings=_gs(),
                change_by=ids["agent"],
                enabled=True,
                host="smtp.db-test.example",
                port=587,
                security="starttls",
                auth_type="password",
                auth_user="relay@example.com",
                auth_password="db-secret",
                from_default="Help <help@example.com>",
            )

        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
            article_id = await svc.add_article(
                ids["agent"],
                ids["ticket"],
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: via DB SMTP",
                    body="Dispatched from DB config.",
                    channel="email",
                    to_address=ids["customer_email"],
                ),
            )
            assert article_id > 0

        assert len(sender.sent) == 1
        assert "Dispatched from DB config." in sender.sent[0].get_content()
        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_email_db_disabled_falls_back_to_env(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB row present but disabled → env TIQORA_SMTP_ENABLED still gates send."""
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=6)
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = CapturingMailSender()

        async with factory() as session:
            from tiqora.config import get_settings as _gs
            from tiqora.domain.mail_outbound import upsert_mail_outbound

            await upsert_mail_outbound(
                session,
                settings=_gs(),
                change_by=ids["agent"],
                enabled=False,
                host="smtp.disabled.example",
                port=25,
            )

        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
            article_id = await svc.add_article(
                ids["agent"],
                ids["ticket"],
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: env fallback",
                    body="Env still works when DB disabled.",
                    channel="email",
                    to_address=ids["customer_email"],
                ),
            )
            assert article_id > 0

        assert len(sender.sent) == 1
        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_agent_email_db_disabled_and_env_off_stores_only(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "0")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=7)
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sysconfig = _make_sysconfig()
        sender = CapturingMailSender()

        async with factory() as session:
            from tiqora.config import get_settings as _gs
            from tiqora.domain.mail_outbound import upsert_mail_outbound

            await upsert_mail_outbound(
                session,
                settings=_gs(),
                change_by=ids["agent"],
                enabled=False,
                host="smtp.off.example",
            )

        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, sysconfig, mail_sender=sender)
            article_id = await svc.add_article(
                ids["agent"],
                ids["ticket"],
                ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=True,
                    subject="Re: store only",
                    body="No relay at all.",
                    channel="email",
                    to_address=ids["customer_email"],
                ),
            )
            assert article_id > 0

        assert sender.sent == []
        await engine.dispose()
    finally:
        get_settings.cache_clear()
