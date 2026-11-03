"""Agent email reply outbound SMTP + queue signature (bugs #2 / #9).

Uses UNIQUE seed ids/logins per test case (91xx range with * 10 + offset)
so the session-scoped testcontainer DB is shared safely across tests/files.
"""

from __future__ import annotations

import base64
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
from tiqora.domain.ticket_write_service import ArticleIn, TicketNotFound, TicketWriteService
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
        "tn": tn,
        "customer_email": f"alice91{ns}@example.com",
        "support_email": f"support{ns}@outbound.example",
        "orig_mid": f"<cust-orig-91{ns}@example.com>",
        "seed_article": seed_article_id,
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


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        # quoted customer mail ending in its own RFC-3676 signature
        ("Hallo\n\nOn 2026-11-03, X wrote:\n> Text\n> \n> -- \n> Max Muster", "text/plain"),
        # "--" in running text of the quote
        ("Hallo\n\nOn 2026-11-03, X wrote:\n> Ausfall 10:00--12:00 Uhr\n> Gruss", "text/plain"),
        # HTML quote with a rule
        ("<p>Hallo</p><blockquote>Text<hr>Gesendet von Gmail</blockquote>", "text/html"),
    ],
)
def test_append_signature_ignores_markers_in_quote(body: str, content_type: str) -> None:
    """A '--' or <hr> in the quoted mail must not suppress the queue signature."""
    out = append_signature(body, "--\nAlice Example\nSupport", content_type=content_type)
    assert "Alice Example" in out


def test_append_signature_quoted_previous_signature_still_appended() -> None:
    """The agent's own earlier signature inside the quote is not this reply's signature."""
    body = "Hallo\n\nOn 2026-11-03, X wrote:\n> Danke\n>\n> > -- \n> > Alice Example\n> > Support"
    out = append_signature(body, "Alice Example\nSupport", content_type="text/plain")
    assert out.endswith("\n\n-- \nAlice Example\nSupport")


def test_append_signature_html_once() -> None:
    sig = "Alice & Bob\nSupport"
    out = append_signature("<p>Hi</p>", sig, content_type="text/html")
    assert append_signature(out, sig, content_type="text/html") == out


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


def test_build_message_with_attachments_is_multipart_mixed() -> None:
    msg = build_message(
        from_addr="Support <support@example.com>",
        to_addrs="alice@example.com",
        cc_addrs=None,
        subject="Re: ticket",
        body="Anbei das Formular.",
        content_type="text/plain; charset=utf-8",
        in_reply_to=None,
        loop_hint=False,
        attachments=[
            ("Formular.pdf", "application/pdf", b"%PDF-1.4 data"),
            ("notiz.txt", "text/plain; charset=utf-8", b"Gr\xc3\xbc\xc3\x9fe"),
            ("raw.eml", "message/rfc822", b"From: x"),
            ("blob", "", b"\x00\x01"),
        ],
    )
    assert msg.get_content_type() == "multipart/mixed"
    body = msg.get_body(("plain",))
    assert body is not None and "Anbei das Formular." in body.get_content()
    parts = [
        (p.get_filename(), p.get_content_type(), p.get_payload(decode=True))
        for p in msg.iter_attachments()
    ]
    assert parts == [
        ("Formular.pdf", "application/pdf", b"%PDF-1.4 data"),
        ("notiz.txt", "text/plain", b"Gr\xc3\xbc\xc3\x9fe"),
        # Structured types cannot take raw bytes — sent as an opaque file.
        ("raw.eml", "application/octet-stream", b"From: x"),
        ("blob", "application/octet-stream", b"\x00\x01"),
    ]


# Real web.de/GMX "trinity-..." id: 107 chars, no legal fold point. On Python
# 3.12 the unstructured-header folder turned this into RFC 2047 encoded-words
# ("=?utf-8?q?=3Ctrinity-...") which no MUA matches against a Message-ID, so
# replies were not threaded with the customer's mail.
_LONG_MID = (
    "<trinity-40d5ad9f-c09b-4b90-8eae-e814de0fce7f-1785927016342"
    "@trinity-msg-rest-webde-webde-live-845c6c87f5-pv8gw>"
)


def test_threading_headers_are_not_encoded_words() -> None:
    """Long In-Reply-To/References msg-ids must be serialized raw (Znuny parity)."""
    msg = build_message(
        from_addr="Netadmin <netadmin@example.com>",
        to_addrs="kunde@example.org",
        cc_addrs=None,
        subject="[Cygnus#2026010710000025] Re: Grüße aus Musterstadt",
        body="Antwort",
        content_type="text/plain; charset=utf-8",
        in_reply_to=_LONG_MID,
        references=f"{_LONG_MID} <second-{'x' * 80}@example.com>",
        loop_hint=False,
    )
    raw = msg.as_string()
    assert "=?utf-8?q?=3C" not in raw
    # The id must appear verbatim on one line for MUA thread matching.
    assert any(line.endswith(_LONG_MID) for line in raw.splitlines())
    assert msg["In-Reply-To"] == _LONG_MID
    # Non-ASCII subjects are still encoded (charset-driven, not length-driven).
    assert "Subject: [Cygnus#2026010710000025] Re: =?utf-8?" in raw
    # RFC 5322 hard limit per line.
    assert max(len(line) for line in raw.splitlines()) <= 998


def test_build_references_chain_extends_parent_chain() -> None:
    from tiqora.channels.email.outbound_reply import build_references_chain

    # Znuny: parent's References + parent Message-ID.
    assert build_references_chain("<a@x> <b@x>", "<c@x>") == "<a@x> <b@x> <c@x>"
    # Bare ids get bracketed, duplicates dropped.
    assert build_references_chain("a@x", "<a@x>") == "<a@x>"
    assert build_references_chain(None, "<c@x>") == "<c@x>"
    assert build_references_chain(None, None) is None
    # Capped, keeping the thread anchor plus the recent tail.
    chain = " ".join(f"<id{i}@x>" for i in range(30))
    capped = build_references_chain(chain, "<new@x>")
    assert capped is not None
    ids = capped.split()
    assert len(ids) == 20
    assert ids[0] == "<id0@x>"
    assert ids[-1] == "<new@x>"


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
        # Attachment bytes go into the TEXT column raw, which only MySQL/MariaDB
        # takes (Znuny stores base64 on PostgreSQL; not ported here yet).
        files = (
            [("Formular.pdf", "application/pdf", b"%PDF-1.4 data")]
            if url_fixture == "mariadb_znuny_url"
            else []
        )

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
                    attachments=files,
                ),
            )
            assert article_id > 0

        assert len(sender.sent) == 1
        msg = sender.sent[0]
        assert msg["To"] == ids["customer_email"]
        assert msg["Cc"] == "cc91@example.com"
        sent_files = [
            (p.get_filename(), p.get_payload(decode=True)) for p in msg.iter_attachments()
        ]
        assert sent_files == [(name, content) for name, _ct, content in files]
        # Default Ticket::Hook=Ticket#, SubjectFormat=Left → tag left of subject.
        expected_subject = f"[Ticket#{ids['tn']}] Re: Outbound ticket 911"
        assert msg["Subject"] == expected_subject
        assert f"[Ticket#{ids['tn']}]" in (msg["Subject"] or "")
        body_part = msg.get_body(("plain",))
        assert body_part is not None
        body = body_part.get_content()
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
            stored = (
                (
                    await session.execute(
                        text(
                            "SELECT filename FROM article_data_mime_attachment"
                            " WHERE article_id = :aid"
                        ),
                        {"aid": article_id},
                    )
                )
                .scalars()
                .all()
            )
            assert list(stored) == [name for name, _ct, _content in files]

        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_forward_carries_original_and_attachments(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forwarding an article sends it along (Znuny AgentTicketForward).

    Regression: the forward used to go out as the agent's note only, subject
    a bare ``Fwd:``, without the original text or its attachments.
    """
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=8)
        csv = b"ID;NAME\nu00001;Example\n"
        engine_sync = create_engine(sync_url)
        postgres = engine_sync.dialect.name == "postgresql"
        with engine_sync.begin() as conn:
            conn.execute(
                text("UPDATE article_data_mime SET a_subject = :s WHERE article_id = :aid"),
                {"s": f"[Ticket#{ids['tn']}] Re: Re: Help please", "aid": ids["seed_article"]},
            )
            # A real attachment plus Znuny's file-1 body copy, which must not be re-sent.
            for filename, ctype, content in (
                ("Teilnehmer.csv", "text/csv", csv),
                ("file-1", "text/plain; charset=utf-8", b"I need help"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO article_data_mime_attachment"
                        " (article_id, filename, content_size, content_type, content,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:aid, :fn, :sz, :ct, :c, :t, 1, :t, 1)"
                    ),
                    {
                        "aid": ids["seed_article"],
                        "fn": filename,
                        "sz": str(len(content)),
                        "ct": ctype,
                        "c": base64.b64encode(content).decode("ascii") if postgres else content,
                        "t": NOW,
                    },
                )
        engine_sync.dispose()
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sender = CapturingMailSender()

        async with factory() as session, session.begin():
            svc = TicketWriteService(session, factory, _make_sysconfig(), mail_sender=sender)
            article_id = await svc.forward_article(
                ids["agent"],
                ids["ticket"],
                article_id=ids["seed_article"],
                subject=None,
                body="FYI, see below.",
                to_address="colleague918@example.com",
            )

        assert len(sender.sent) == 1
        msg = sender.sent[0]
        assert msg["To"] == "colleague918@example.com"
        assert msg["Subject"] == f"[Ticket#{ids['tn']}] Fwd: Help please"
        body_part = msg.get_body(("plain",))
        assert body_part is not None
        body = body_part.get_content()
        assert body.index("FYI, see below.") < body.index("---- Forwarded message from")
        assert f"From: {ids['customer_email']}" in body
        assert "I need help" in body
        assert "---- End forwarded message ---" in body
        sent_files = [
            (p.get_filename(), p.get_payload(decode=True)) for p in msg.iter_attachments()
        ]
        assert sent_files == [("Teilnehmer.csv", csv)]

        async with factory() as session:
            stored = (
                (
                    await session.execute(
                        text(
                            "SELECT filename FROM article_data_mime_attachment"
                            " WHERE article_id = :aid"
                        ),
                        {"aid": article_id},
                    )
                )
                .scalars()
                .all()
            )
            assert list(stored) == ["Teilnehmer.csv"]

            # A nonexistent article id is a not-found, not a note-only forward.
            svc = TicketWriteService(session, factory, _make_sysconfig(), mail_sender=sender)
            with pytest.raises(TicketNotFound):
                await svc.forward_article(
                    ids["agent"],
                    ids["ticket"],
                    article_id=article_id + 100000,
                    subject=None,
                    body="",
                    to_address="colleague918@example.com",
                )
        await engine.dispose()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_bounce_redirects_original_mail_to_target_only(
    url_fixture: str,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounce = Znuny Email::Bounce: original mail + Resent-*, RCPT TO the target alone.

    Regression: bounce stored an agent article and never sent anything. The
    envelope check matters most — the original To/Cc headers stay on the
    mail, and header-based delivery would mail the original recipients again.
    """
    monkeypatch.setenv("TIQORA_SMTP_ENABLED", "1")
    get_settings.cache_clear()
    try:
        sync_url: str = request.getfixturevalue(url_fixture)
        ids = _seed(sync_url, ns=9)
        target = "other919@example.com"
        engine_sync = create_engine(sync_url)
        postgres = engine_sync.dialect.name == "postgresql"
        pdf = b"%PDF-1.4 data"
        with engine_sync.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO article_data_mime_attachment"
                    " (article_id, filename, content_size, content_type, content,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, 'Formular.pdf', :sz, 'application/pdf', :c, :t, 1, :t, 1)"
                ),
                {
                    "aid": ids["seed_article"],
                    "sz": str(len(pdf)),
                    "c": base64.b64encode(pdf).decode("ascii") if postgres else pdf,
                    "t": NOW,
                },
            )
        engine_sync.dispose()
        engine = create_async_engine(_to_async_url(sync_url))
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        sender = CapturingMailSender()

        async def bounce() -> int:
            async with factory() as session, session.begin():
                svc = TicketWriteService(session, factory, _make_sysconfig(), mail_sender=sender)
                return await svc.bounce_article(
                    ids["agent"], ids["ticket"], ids["seed_article"], to_address=target
                )

        # 1) No stored raw mail: rebuilt from the article, original headers kept.
        assert await bounce() == ids["seed_article"]
        msg = sender.sent[0]
        assert sender.envelope_recipients[0] == [target]
        assert sender.envelope_senders[0] == ids["support_email"]
        assert msg["Resent-To"] == target
        assert msg["Resent-From"] == ids["support_email"]
        assert msg["Resent-Message-ID"] and msg["Resent-Date"]
        assert msg["From"] == ids["customer_email"]
        assert msg["To"] == ids["support_email"]
        assert msg["Subject"] == "Help please"
        assert msg["Message-ID"] == ids["orig_mid"]
        body_part = msg.get_body(("plain",))
        assert body_part is not None and "I need help" in body_part.get_content()
        sent_files = [
            (p.get_filename(), p.get_payload(decode=True)) for p in msg.iter_attachments()
        ]
        assert sent_files == [("Formular.pdf", pdf)]

        # 2) Stored raw mail: sent byte for byte behind the Resent-* lines.
        raw = (
            f"From: {ids['customer_email']}\r\nTo: {ids['support_email']}\r\n"
            f"Subject: Help please\r\nMessage-ID: {ids['orig_mid']}\r\n\r\nI need help\r\n"
        )
        engine_sync = create_engine(sync_url)
        with engine_sync.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO article_data_mime_plain"
                    " (article_id, body, create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, :b, :t, 1, :t, 1)"
                ),
                {"aid": ids["seed_article"], "b": raw if postgres else raw.encode(), "t": NOW},
            )
        engine_sync.dispose()
        await bounce()
        msg = sender.sent[1]
        assert sender.envelope_recipients[1] == [target]
        wire = msg.tiqora_raw  # type: ignore[attr-defined]
        assert wire.startswith(b"Resent-Message-ID: ")
        assert f"Resent-To: {target}\r\n".encode() in wire
        assert wire.endswith(raw.encode())

        async with factory() as session:
            history = (
                await session.execute(
                    text(
                        "SELECT h.name, h.article_id FROM ticket_history h"
                        " JOIN ticket_history_type t ON t.id = h.history_type_id"
                        " WHERE h.ticket_id = :tid AND t.name = 'Bounce'"
                    ),
                    {"tid": ids["ticket"]},
                )
            ).all()
            assert [tuple(r) for r in history] == [(f"%%{target}", ids["seed_article"])] * 2
            articles = (
                await session.execute(
                    text("SELECT COUNT(*) FROM article WHERE ticket_id = :tid"),
                    {"tid": ids["ticket"]},
                )
            ).scalar()
            assert articles == 1, "a bounce stores no article (Znuny)"
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
