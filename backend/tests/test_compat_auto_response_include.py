"""TicketCreate / TicketUpdate (GenericInterface compat): auto responses and ``IncludeTicketData``.

DB tests on MariaDB and PostgreSQL. Seed ids live in the GI band 7650-7669 (agent 7650, group
7650, queue 7651, auto responses 7650 "auto reply" / 7651 "auto follow up"); every row, including
the tickets the operations create in queue 7651, is deleted afterwards. The outbound relay is a
monkeypatched ``CapturingMailSender``. The operations only queue their mails (``tiqora_job``
kind ``gi_mail``); ``_call`` runs the queued jobs afterwards like the worker unless
``drain=False``.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import aiosmtplib
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._gi_mail_jobs import (
    delete_gi_mail_jobs,
    drain_gi_mail_jobs,
    gi_mail_jobs,
    make_due,
    max_job_id,
)
from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tiqora.api.compat.operations import op_ticket_create, op_ticket_update
from tiqora.api.compat.webservices import OperationConfig, parse_webservice
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.domain import mail_outbound
from tiqora.domain.auth import SessionStore
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2024, 6, 1, 12, 0, 0)
AGENT, GROUP, QUEUE = 7650, 7650, 7651
AR_REPLY, AR_FOLLOW_UP = 7650, 7651
_OTHER_QUEUE = 7653  # a queue without auto responses (created by its test)
SESSION = "GI7650AGENTSESSION"
CUSTOMER_MAIL = "kim7650@example.org"
CUSTOMER_FROM = f"Kim Kunde <{CUSTOMER_MAIL}>"
CUST_SESSION = "GI7650CUSTSESSION"
CUST_LOGIN = "gi7650.cust"
CUST_MAIL = "cust7650@example.org"
SYSADDR, SYSADDR_MAIL = 7652, "gi7650-system@example.org"
DAEMON_MAIL = "MAILER-DAEMON@example.org"
TITLE = "GI7650 ticket"

_SEEDED: list[str] = []
# tiqora_job: this module's gi_mail jobs are the ones above the watermark taken at first seed
_JOB_WATERMARK: dict[str, int] = {}
_COUNTER_TABLES = ("tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_number_counter")
_SNAPSHOTS: dict[str, dict[str, int]] = {}
_IN_QUEUE = f"(SELECT id FROM ticket WHERE queue_id = {QUEUE})"
_IN_QUEUE_ARTICLES = f"(SELECT id FROM article WHERE ticket_id IN {_IN_QUEUE})"


def _cleanup_statements() -> tuple[str, ...]:
    return (
        f"DELETE FROM time_accounting WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM ticket_history WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM tiqora_mail_log WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM article_data_mime_send_error WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article_data_mime WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM ticket WHERE queue_id = {QUEUE}",
        "DELETE FROM ticket_loop_protection WHERE sent_to IN"
        f" ('{CUSTOMER_MAIL}', '{DAEMON_MAIL}', '{CUST_MAIL}', 'someone@example.org')",
        f"DELETE FROM system_address WHERE id = {SYSADDR}",
        f"DELETE FROM customer_user WHERE login = '{CUST_LOGIN}'",
        f"DELETE FROM queue_auto_response WHERE queue_id = {QUEUE}",
        f"DELETE FROM auto_response WHERE id IN ({AR_REPLY}, {AR_FOLLOW_UP})",
        "DELETE FROM sessions WHERE session_id LIKE 'GI7650%'",
        f"DELETE FROM queue WHERE id IN ({QUEUE}, {_OTHER_QUEUE})",
        f"DELETE FROM group_user WHERE group_id = {GROUP}",
        f"DELETE FROM permission_groups WHERE id = {GROUP}",
        f"DELETE FROM users WHERE id = {AGENT}",
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _cleanup_statements():
                conn.execute(text(stmt))
            delete_gi_mail_jobs(conn, above_id=_JOB_WATERMARK[sync_url])
        engine.dispose()
        delete_rows_above(sync_url, _SNAPSHOTS[sync_url])
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = int(time.time())
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    if sync_url not in _SNAPSHOTS:  # once per module: the fixture re-seeds for every test
        _SNAPSHOTS[sync_url] = snapshot_max_ids(sync_url, _COUNTER_TABLES)
        _JOB_WATERMARK[sync_url] = max_job_id(sync_url)
    with engine.begin() as conn:
        for stmt in _cleanup_statements():
            conn.execute(text(stmt))
        delete_gi_mail_jobs(conn, above_id=_JOB_WATERMARK[sync_url])
        for stmt in (
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            f" VALUES ({AGENT}, 'gi7650.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({GROUP}, 'gi7650-group', 1, :t, 1, :t, 1)",
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            f" change_time, change_by) VALUES ({AGENT}, {GROUP}, 'rw', :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({QUEUE}, 'GI7650Queue', {GROUP}, 1, 1, 1, 1, 0,"
            " 1, :t, 1, :t, 1)",
            # type_id 1 = "auto reply", 3 = "auto follow up" (initial_insert)
            "INSERT INTO auto_response (id, type_id, system_address_id, name, text0, text1,"
            " content_type, comments, valid_id, create_by, create_time, change_by, change_time)"
            f" VALUES ({AR_REPLY}, 1, 1, 'gi7650 reply', 'Eingang: <OTRS_CUSTOMER_SUBJECT>',"
            " 'Danke, Ticket <OTRS_TICKET_TicketNumber> ist angelegt.', 'text/plain', 'test',"
            " 1, 1, :t, 1, :t)",
            "INSERT INTO auto_response (id, type_id, system_address_id, name, text0, text1,"
            " content_type, comments, valid_id, create_by, create_time, change_by, change_time)"
            f" VALUES ({AR_FOLLOW_UP}, 3, 1, 'gi7650 follow up',"
            " 'Nachfrage: <OTRS_CUSTOMER_SUBJECT>',"
            " 'Wir haben Ihre Nachricht erhalten.', 'text/plain', 'test', 1, 1, :t, 1, :t)",
            "INSERT INTO system_address (id, value0, value1, queue_id, valid_id, create_time,"
            f" create_by, change_time, change_by) VALUES ({SYSADDR}, '{SYSADDR_MAIL}', 'Sys', 1,"
            " 1, :t, 1, :t, 1)",
            "INSERT INTO customer_user (login, email, customer_id, pw, first_name, last_name,"
            f" valid_id, create_time, create_by, change_time, change_by) VALUES ('{CUST_LOGIN}',"
            f" '{CUST_MAIL}', 'GI7650', 'x', 'Kim', 'Kunde', 1, :t, 1, :t, 1)",
            "INSERT INTO queue_auto_response (queue_id, auto_response_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({QUEUE}, {AR_REPLY}, :t, 1, :t, 1)",
            "INSERT INTO queue_auto_response (queue_id, auto_response_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({QUEUE}, {AR_FOLLOW_UP}, :t, 1, :t, 1)",
        ):
            conn.execute(text(stmt), {"t": T0})
        for sid, extra in (
            (SESSION, {"UserID": str(AGENT), "UserLogin": "gi7650.agent", "UserType": "User"}),
            (CUST_SESSION, {"UserLogin": CUST_LOGIN, "UserType": "Customer"}),
        ):
            for key, val in {
                **extra,
                "UserLastRequest": str(now),
                "UserSessionStart": str(now),
            }.items():
                conn.execute(
                    text(
                        "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                        " VALUES (:sid, :k, :v, 0)"
                    ),
                    {"sid": sid, "k": key, "v": val},
                )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch) -> CapturingMailSender:
    """An enabled outbound relay whose sender records the mails."""
    sender = CapturingMailSender()

    async def _enabled(session: Any, settings: Any = None) -> Any:
        return mail_outbound.ResolvedOutboundSmtp(
            enabled=True,
            host="smtp.example.org",
            port=25,
            security="none",
            auth_type="none",
            auth_user="",
            auth_password="",
            from_default="",
            timeout_seconds=5,
            source="db",
        )

    async def _build(session: Any, **kwargs: Any) -> CapturingMailSender:
        return sender

    monkeypatch.setattr(mail_outbound, "resolve_outbound_smtp", _enabled)
    monkeypatch.setattr(mail_outbound, "build_outbound_sender", _build)
    return sender


@pytest.fixture
def no_relay(monkeypatch: pytest.MonkeyPatch) -> CapturingMailSender:
    """A disabled relay; the sender must never be asked for."""
    sender = CapturingMailSender()

    async def _disabled(session: Any, settings: Any = None) -> Any:
        return mail_outbound.ResolvedOutboundSmtp(
            enabled=False,
            host="",
            port=25,
            security="none",
            auth_type="none",
            auth_user="",
            auth_password="",
            from_default="",
            timeout_seconds=5,
            source="none",
        )

    async def _build(session: Any, **kwargs: Any) -> CapturingMailSender:
        raise AssertionError("no sender may be built without an outbound relay")

    monkeypatch.setattr(mail_outbound, "resolve_outbound_smtp", _disabled)
    monkeypatch.setattr(mail_outbound, "build_outbound_sender", _build)
    return sender


class _FakeRedis:
    async def get(self, key: str) -> str | None:
        return None


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


def _query(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return list(result.fetchall()) if result.returns_rows else []
    finally:
        engine.dispose()


async def _call(
    sync_url: str,
    operation: str,
    params: dict[str, Any],
    *,
    operation_config: OperationConfig | None = None,
    customer: bool = False,
    drain: bool = True,
) -> dict[str, Any]:
    """Run the operation; then (``drain``) the mail jobs it queued, like the worker."""
    result = await _operation(
        sync_url, operation, params, operation_config=operation_config, customer=customer
    )
    if drain:
        await drain_gi_mail_jobs(sync_url)
    return result


async def _operation(
    sync_url: str,
    operation: str,
    params: dict[str, Any],
    *,
    operation_config: OperationConfig | None = None,
    customer: bool = False,
) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    func = op_ticket_create if operation == "create" else op_ticket_update
    payload = {"SessionID": CUST_SESSION if customer else SESSION, **params}
    try:
        async with factory() as session:

            async def _no_row(name: str) -> None:
                return None

            return await func(  # type: ignore[operator, no-any-return]
                payload,
                session,
                factory,
                store,
                SysConfig(fetch=_no_row),
                operation_config=operation_config,
            )
    finally:
        await engine.dispose()


def _operation_config(operation_type: str, *, include: str | None) -> OperationConfig:
    """The operation config of a webservice 7655 whose operation "Make" has the given flag."""
    operation: dict[str, Any] = {"Type": operation_type}
    if include is not None:
        operation["IncludeTicketData"] = include
    ws = parse_webservice(
        7655, "Gi7655", {"Provider": {"Operation": {"Make": operation}}}, huge_xml=False
    )
    return ws.operations["Make"]


def _ticket(title: str = TITLE) -> dict[str, Any]:
    return {"Title": title, "QueueID": QUEUE, "StateID": 1, "PriorityID": 3}


def _customer_article(subject: str = "Hilfe", **extra: Any) -> dict[str, Any]:
    return {
        "Subject": subject,
        "Body": f"{subject} body",
        "SenderType": "customer",
        "From": CUSTOMER_FROM,
        **extra,
    }


def _history_names(sync_url: str, ticket_id: int) -> list[str]:
    rows = _query(
        sync_url,
        "SELECT t.name FROM ticket_history h"
        " JOIN ticket_history_type t ON t.id = h.history_type_id"
        " WHERE h.ticket_id = :t ORDER BY h.id",
        t=ticket_id,
    )
    return [str(r[0]) for r in rows]


def _article_count(sync_url: str, ticket_id: int) -> int:
    return int(
        _query(sync_url, "SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=ticket_id)[0][0]
    )


def _article_ids(sync_url: str, ticket_id: int) -> list[int]:
    rows = _query(sync_url, "SELECT id FROM article WHERE ticket_id = :t ORDER BY id", t=ticket_id)
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Auto responses (D16)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_create_sends_auto_reply_when_queue_has_one(
    sync_url: str, relay: CapturingMailSender
) -> None:
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Drucker")}
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]

    assert len(relay.sent) == 1
    mail = relay.sent[0]
    assert CUSTOMER_MAIL in str(mail["To"])
    assert "Drucker" in str(mail["Subject"])
    assert result["TicketNumber"] in mail.get_content()
    # the customer's article plus the stored auto reply
    assert _article_count(sync_url, ticket_id) == 2
    assert _history_names(sync_url, ticket_id).count("SendAutoReply") == 1
    # ArticleID is still the request's article, not the auto reply
    assert result["ArticleID"] == _article_ids(sync_url, ticket_id)[0]


@pytest.mark.db
async def test_no_relay_no_auto_response(sync_url: str, no_relay: CapturingMailSender) -> None:
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Ohne Relay")}
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]

    assert no_relay.sent == []
    assert _article_count(sync_url, ticket_id) == 1
    assert "SendAutoReply" not in _history_names(sync_url, ticket_id)


@pytest.mark.db
async def test_article_send_never_triggers_auto_response(
    sync_url: str, relay: CapturingMailSender
) -> None:
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket(),
            "Article": {
                "Subject": "Raus",
                "Body": "Hallo",
                "From": "Agent <agent@example.org>",
                "To": CUSTOMER_MAIL,
                "ArticleSend": 1,
                "AutoResponseType": "auto reply",
            },
        },
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]

    # exactly the ArticleSend mail went out, no auto reply on top of it
    assert [m["To"] for m in relay.sent] == [CUSTOMER_MAIL]
    assert _article_count(sync_url, ticket_id) == 1
    assert "SendAutoReply" not in _history_names(sync_url, ticket_id)


@pytest.mark.db
async def test_update_sends_auto_follow_up(sync_url: str, relay: CapturingMailSender) -> None:
    created = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Erstkontakt")}
    )
    assert "Error" not in created, created
    ticket_id = created["TicketID"]
    relay.sent.clear()  # the auto reply of the create is not under test here

    result = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _customer_article("Nachtrag")},
    )
    assert "Error" not in result, result

    assert len(relay.sent) == 1
    assert CUSTOMER_MAIL in str(relay.sent[0]["To"])
    assert "Nachfrage" in str(relay.sent[0]["Subject"])
    assert _history_names(sync_url, ticket_id).count("SendAutoFollowUp") == 1
    # the customer's follow-up is the only article id the answer names
    assert result["ArticleID"] == _article_ids(sync_url, ticket_id)[2]


@pytest.mark.db
async def test_empty_auto_response_type_falls_back_to_default(
    sync_url: str, relay: CapturingMailSender
) -> None:
    # Znuny: `if (!$Article->{AutoResponseType})` -> the SysConfig default, so "" still sends
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": _customer_article("Leer", AutoResponseType="")},
    )
    assert "Error" not in result, result

    assert len(relay.sent) == 1
    assert _history_names(sync_url, result["TicketID"]).count("SendAutoReply") == 1


# ---------------------------------------------------------------------------
# IncludeTicketData
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_include_ticket_data_via_webservice(
    sync_url: str, no_relay: CapturingMailSender
) -> None:
    from tests.test_compat_ticket_get import TICKET_KEYS

    config = _operation_config("Ticket::TicketCreate", include="1")
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": [_customer_article("Eins"), _customer_article("Zwei")]},
        operation_config=config,
    )
    assert "Error" not in result, result
    ids = _article_ids(sync_url, result["TicketID"])
    assert result["ArticleID"] == ids

    ticket = result["Ticket"]
    # no Service / SLA keys on a ticket without them (Znuny, ruling T8)
    assert (set(TICKET_KEYS) - {"Service", "SLA"}) <= set(ticket)
    assert ticket["TicketID"] == result["TicketID"]
    assert ticket["Title"] == TITLE
    assert [a["ArticleID"] for a in ticket["Article"]] == ids
    assert [a["Subject"] for a in ticket["Article"]] == ["Eins", "Zwei"]
    assert [a["ArticleNumber"] for a in ticket["Article"]] == [1, 2]
    # attachments are only echoed when the request carried a top-level Attachment
    assert all("Attachment" not in a for a in ticket["Article"])


@pytest.mark.db
async def test_include_ticket_data_lists_only_new_articles_on_update(
    sync_url: str, no_relay: CapturingMailSender
) -> None:
    create_config = _operation_config("Ticket::TicketCreate", include="1")
    update_config = _operation_config("Ticket::TicketUpdate", include="1")
    created = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": _customer_article("Alt")},
        operation_config=create_config,
    )
    ticket_id = created["TicketID"]
    old_ids = _article_ids(sync_url, ticket_id)

    one = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _customer_article("Neu")},
        operation_config=update_config,
    )
    assert "Error" not in one, one
    new_id = one["ArticleID"]
    assert new_id not in old_ids
    # one article: a hash under "Article"
    article = one["Ticket"]["Article"]
    assert article["ArticleID"] == new_id
    assert article["ArticleNumber"] == 2
    assert "Articles" not in one["Ticket"]

    two = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": [_customer_article("A"), _customer_article("B")]},
        operation_config=update_config,
    )
    assert "Error" not in two, two
    assert [a["Subject"] for a in two["Ticket"]["Articles"]] == ["A", "B"]
    assert [a["ArticleID"] for a in two["Ticket"]["Articles"]] == two["ArticleIDs"]
    assert "Article" not in two["Ticket"]

    none = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Ticket": {"Title": "GI7650 renamed"}},
        operation_config=update_config,
    )
    assert "Error" not in none, none
    assert none["Ticket"]["Title"] == "GI7650 renamed"
    assert "Article" not in none["Ticket"] and "Articles" not in none["Ticket"]
    assert "ArticleID" not in none


@pytest.mark.db
async def test_include_ticket_data_on_update_carries_attachments(
    sync_url: str, no_relay: CapturingMailSender
) -> None:
    content = base64.b64encode(b"hallo").decode()
    created = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Mit Anhang")}
    )
    result = await _call(
        sync_url,
        "update",
        {
            "TicketID": created["TicketID"],
            "Article": _customer_article(
                "Datei",
                Attachment={"Filename": "a.txt", "ContentType": "text/plain", "Content": content},
            ),
        },
        operation_config=_operation_config("Ticket::TicketUpdate", include="1"),
    )
    assert "Error" not in result, result
    attachments = result["Ticket"]["Article"]["Attachment"]
    assert [(a["Filename"], a["Content"]) for a in attachments] == [("a.txt", content)]


@pytest.mark.db
async def test_canonical_route_never_includes_ticket_data(
    sync_url: str, no_relay: CapturingMailSender
) -> None:
    # no webservice at all, and a webservice operation without the flag
    for config in (None, _operation_config("Ticket::TicketCreate", include=None)):
        result = await _call(
            sync_url,
            "create",
            {"Ticket": _ticket(), "Article": _customer_article("Kanonisch")},
            operation_config=config,
        )
        assert "Error" not in result, result
        assert "Ticket" not in result
        assert sorted(result) == ["ArticleID", "TicketID", "TicketNumber"]


# ---------------------------------------------------------------------------
# Mails are sent after the commit (ArticleSend and auto responses)
# ---------------------------------------------------------------------------


def _permanent() -> Exception:
    return aiosmtplib.SMTPResponseException(550, "5.1.1 mailbox unavailable")


def _temporary() -> Exception:
    return OSError("connection refused")


class _ScriptedSender:
    """Records mails; the n-th send (1-based) listed in ``fail_on`` raises ``error()`` - by
    default a permanent 5xx reply (recorded at once, like Znuny's ``MailQueue``)."""

    def __init__(
        self, fail_on: tuple[int, ...] = (), error: Any = _permanent, block: bool = False
    ) -> None:
        self.sent: list[Any] = []
        self.attempts = 0
        self.fail_on = fail_on
        self.error = error
        self.block = block  # hang inside SMTP (an interrupted send)
        self.last_smtp_code: int | None = 250
        self.last_smtp_detail: str | None = "250 OK"

    async def send(self, message: Any, *, envelope_from: str | None = None) -> None:
        self.attempts += 1
        if self.block:
            await asyncio.Event().wait()
        if self.attempts in self.fail_on:
            raise self.error()
        self.sent.append(message)


def _use_relay(monkeypatch: pytest.MonkeyPatch, sender: Any) -> None:
    async def _enabled(session: Any, settings: Any = None) -> Any:
        return mail_outbound.ResolvedOutboundSmtp(
            enabled=True,
            host="smtp.example.org",
            port=25,
            security="none",
            auth_type="none",
            auth_user="",
            auth_password="",
            from_default="",
            timeout_seconds=5,
            source="db",
        )

    async def _build(session: Any, **kwargs: Any) -> Any:
        return sender

    monkeypatch.setattr(mail_outbound, "resolve_outbound_smtp", _enabled)
    monkeypatch.setattr(mail_outbound, "build_outbound_sender", _build)


def _jobs(sync_url: str) -> list[dict[str, Any]]:
    """This module's gi_mail jobs."""
    return gi_mail_jobs(sync_url, above_id=_JOB_WATERMARK[sync_url])


def _send_article(subject: str) -> dict[str, Any]:
    return {
        "Subject": subject,
        "Body": f"{subject} body",
        "From": "Agent <agent@example.org>",
        "To": CUSTOMER_MAIL,
        "ArticleSend": 1,
    }


def _mail_log(sync_url: str) -> list[tuple[str, int | None]]:
    rows = _query(
        sync_url,
        "SELECT status, article_id FROM tiqora_mail_log WHERE ticket_id IN "
        f"{_IN_QUEUE} ORDER BY id",
    )
    return [(str(r[0]), None if r[1] is None else int(r[1])) for r in rows]


def _send_errors(sync_url: str) -> list[tuple[int, str | None, str]]:
    rows = _query(
        sync_url,
        "SELECT article_id, message_id, log_message FROM article_data_mime_send_error"
        f" WHERE article_id IN {_IN_QUEUE_ARTICLES} ORDER BY article_id",
    )
    return [(int(r[0]), None if r[1] is None else str(r[1]), str(r[2] or "")) for r in rows]


@pytest.mark.db
async def test_request_answers_before_any_mail_and_the_worker_sends(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender()
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": [_send_article("Eins"), _customer_article("Zwei")]},
        drain=False,
    )
    assert "Error" not in result, result
    # nothing was sent inside the request: the mails wait as jobs, committed with the ticket
    assert sender.attempts == 0 and sender.sent == []
    assert _mail_log(sync_url) == []
    jobs = _jobs(sync_url)
    assert [j["status"] for j in jobs] == ["queued", "queued"]
    send_id, customer_id = result["ArticleID"]
    assert [
        (j["payload"]["kind"], j["payload"]["article_id"], j["payload"]["auto_response_type"])
        for j in jobs
    ] == [("article_send", send_id, ""), ("auto_response", customer_id, "auto reply")]
    assert all(j["payload"]["ticket_id"] == result["TicketID"] for j in jobs)

    done = await drain_gi_mail_jobs(sync_url)
    assert [d["status"] for d in done] == ["succeeded", "succeeded"]
    assert [str(m["Subject"]).rsplit(" ", 1)[-1] for m in sender.sent][0] == "Eins"
    assert len(sender.sent) == 2  # the ArticleSend mail and the auto reply
    assert ("sent", send_id) in _mail_log(sync_url)
    assert _history_names(sync_url, result["TicketID"]).count("SendAutoReply") == 1


@pytest.mark.db
async def test_send_failure_is_recorded_like_the_mail_queue(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender(fail_on=(1,))
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}, drain=False
    )
    # the answer no longer depends on the relay (Znuny: the mail sits in the MailQueue)
    assert "Error" not in result, result
    done = await drain_gi_mail_jobs(sync_url)
    assert [d["status"] for d in done] == ["failed"]
    article_id = result["ArticleID"]
    message_id = _query(
        sync_url, "SELECT a_message_id FROM article_data_mime WHERE article_id = :a", a=article_id
    )[0][0]
    # MailQueue _SetArticleTransmissionError -> ArticleCreateTransmissionError
    errors = _send_errors(sync_url)
    assert [(a, m) for a, m, _ in errors] == [(article_id, message_id)]
    assert "5.1.1 mailbox unavailable" in errors[0][2]
    assert _mail_log(sync_url) == [("failed", article_id)]
    assert _article_count(sync_url, result["TicketID"]) == 1  # the article stays


@pytest.mark.db
async def test_article_send_failure_keeps_the_data_and_sends_the_rest(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender(fail_on=(2,))
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket(),
            "Article": [_send_article("Eins"), _send_article("Zwei"), _send_article("Drei")],
        },
    )
    assert "Error" not in result, result
    # ticket and all articles persist; the 1st and 3rd mail went out; every outcome is logged
    tickets = _query(sync_url, "SELECT id FROM ticket WHERE title = :t", t=TITLE)
    assert len(tickets) == 1
    ids = _article_ids(sync_url, int(tickets[0][0]))
    assert len(ids) == 3 and result["ArticleID"] == ids
    assert [str(m["Subject"]).rsplit(" ", 1)[-1] for m in sender.sent] == ["Eins", "Drei"]
    assert _mail_log(sync_url) == [("sent", ids[0]), ("failed", ids[1]), ("sent", ids[2])]
    assert [a for a, _, _ in _send_errors(sync_url)] == [ids[1]]
    assert [j["status"] for j in _jobs(sync_url)] == ["succeeded", "failed", "succeeded"]


@pytest.mark.db
async def test_every_failing_article_send_is_logged(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender(fail_on=(1, 2))
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": [_send_article("Eins"), _send_article("Zwei")]},
    )
    assert "Error" not in result, result
    ids = _article_ids(
        sync_url, int(_query(sync_url, "SELECT id FROM ticket WHERE title = :t", t=TITLE)[0][0])
    )
    assert _mail_log(sync_url) == [("failed", ids[0]), ("failed", ids[1])]
    assert [a for a, _, _ in _send_errors(sync_url)] == ids


@pytest.mark.db
async def test_unbuildable_sender_is_logged_as_a_failure(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_relay(monkeypatch, _ScriptedSender())

    async def _broken(session: Any, **kwargs: Any) -> Any:
        raise RuntimeError("oauth token fetch failed")

    monkeypatch.setattr(mail_outbound, "build_outbound_sender", _broken)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}
    )
    assert "Error" not in result, result
    # no SMTP reply at all: temporary like in Znuny, so the mail is tried three times
    for _ in range(2):
        make_due(sync_url, above_id=_JOB_WATERMARK[sync_url])
        await drain_gi_mail_jobs(sync_url)
    assert [status for status, _ in _mail_log(sync_url)] == ["failed"] * 3
    assert "oauth token fetch failed" in _send_errors(sync_url)[0][2]
    assert [(j["status"], j["attempts"]) for j in _jobs(sync_url)] == [("failed", 3)]


@pytest.mark.db
async def test_article_send_mail_waits_for_the_commit(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender()
    _use_relay(monkeypatch, sender)
    seen_at_send: list[int] = []

    original_send = sender.send

    async def _send(message: Any, *, envelope_from: str | None = None) -> None:
        # a second connection must already see the committed ticket and article
        seen_at_send.append(
            int(
                _query(
                    sync_url,
                    "SELECT COUNT(*) FROM article a JOIN ticket t ON t.id = a.ticket_id"
                    " WHERE t.title = :t",
                    t=TITLE,
                )[0][0]
            )
        )
        await original_send(message, envelope_from=envelope_from)

    sender.send = _send  # type: ignore[method-assign]
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": [_send_article("Eins"), _send_article("Zwei")]},
    )
    assert "Error" not in result, result
    assert seen_at_send == [2, 2]
    assert _mail_log(sync_url) == [
        ("sent", result["ArticleID"][0]),
        ("sent", result["ArticleID"][1]),
    ]


@pytest.mark.db
async def test_no_mail_when_validation_fails(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender()
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket(),
            "Article": [_send_article("Gut"), {**_send_article("Schlecht"), "HistoryType": "Nope"}],
        },
    )
    assert result["Error"]["ErrorMessage"].endswith("HistoryType parameter is invalid!")
    assert sender.sent == [] and sender.attempts == 0
    assert _query(sync_url, "SELECT id FROM ticket WHERE title = :t", t=TITLE) == []
    assert _jobs(sync_url) == []  # no job without its ticket


@pytest.mark.db
async def test_failing_auto_response_never_fails_the_call(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender(fail_on=(1,))
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Defekt")}
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]
    assert sender.attempts == 1 and sender.sent == []
    assert _article_count(sync_url, ticket_id) == 1  # no partial auto reply row
    assert "SendAutoReply" not in _history_names(sync_url, ticket_id)
    assert (
        _query(sync_url, "SELECT 1 FROM ticket_loop_protection WHERE sent_to = :a", a=CUSTOMER_MAIL)
        == []
    )


@pytest.mark.db
async def test_raising_add_article_in_auto_response_leaves_no_partial_rows(
    sync_url: str, relay: CapturingMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.channels.email import autoresponse

    async def _boom(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("database went away")

    monkeypatch.setattr(autoresponse, "add_article", _boom)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Boom")}
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]
    assert _article_count(sync_url, ticket_id) == 1
    assert "SendAutoReply" not in _history_names(sync_url, ticket_id)
    assert (
        _query(sync_url, "SELECT 1 FROM ticket_loop_protection WHERE sent_to = :a", a=CUSTOMER_MAIL)
        == []
    )


@pytest.mark.db
async def test_queue_without_auto_response_sends_nothing(
    sync_url: str, relay: CapturingMailSender
) -> None:
    _query(sync_url, f"DELETE FROM queue_auto_response WHERE queue_id = {QUEUE}")
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Leer")}
    )
    assert "Error" not in result, result
    assert relay.sent == []
    assert _article_count(sync_url, result["TicketID"]) == 1


@pytest.mark.db
async def test_closed_ticket_gets_no_auto_reply(sync_url: str, relay: CapturingMailSender) -> None:
    ticket = {**_ticket(), "StateID": 2}
    result = await _call(sync_url, "create", {"Ticket": ticket, "Article": _customer_article("Zu")})
    assert "Error" not in result, result
    assert relay.sent == []
    names = _query(
        sync_url,
        "SELECT h.name FROM ticket_history h JOIN ticket_history_type t ON t.id = h.history_type_id"
        " WHERE h.ticket_id = :t AND t.name = 'Misc'",
        t=result["TicketID"],
    )
    assert [r[0] for r in names] == ["Sent no auto response because ticket is state-type 'closed'!"]


@pytest.mark.db
async def test_no_auto_response_to_daemons_and_system_addresses(
    sync_url: str, relay: CapturingMailSender
) -> None:
    daemon = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket(),
            "Article": _customer_article("Bounce", From=f"Mail Delivery <{DAEMON_MAIL}>"),
        },
    )
    own = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket(),
            "Article": _customer_article("Eigen", From=f"System <{SYSADDR_MAIL.upper()}>"),
        },
    )
    assert "Error" not in daemon and "Error" not in own
    assert relay.sent == []
    misc = _query(
        sync_url,
        "SELECT h.name FROM ticket_history h JOIN ticket_history_type t ON t.id = h.history_type_id"
        " WHERE h.ticket_id = :t AND t.name = 'Misc'",
        t=daemon["TicketID"],
    )
    assert [r[0] for r in misc] == [
        f"Sent no auto response to '{DAEMON_MAIL}', SendNoAutoResponseRegExp matched."
    ]
    assert _article_count(sync_url, daemon["TicketID"]) == 1
    assert _article_count(sync_url, own["TicketID"]) == 1


@pytest.mark.db
async def test_missing_from_is_the_customer_user_like_znuny(
    sync_url: str, relay: CapturingMailSender
) -> None:
    known = await _call(
        sync_url,
        "create",
        {
            "Ticket": {**_ticket(), "CustomerUser": CUST_LOGIN},
            "Article": {"Subject": "Ohne Absender", "Body": "Hallo", "SenderType": "customer"},
        },
    )
    assert "Error" not in known, known
    assert len(relay.sent) == 1 and CUST_MAIL in str(relay.sent[0]["To"])
    from_header = _query(
        sync_url,
        "SELECT a_from FROM article_data_mime WHERE article_id = :a",
        a=known["ArticleID"],
    )[0][0]
    assert from_header == f'"Kim Kunde" <{CUST_MAIL}>'

    # an unknown customer user is used as given (Znuny: it should be an address)
    unknown = await _call(
        sync_url,
        "create",
        {
            "Ticket": {**_ticket(), "CustomerUser": "someone@example.org"},
            "Article": {"Subject": "Roh", "Body": "Hallo", "SenderType": "customer"},
        },
    )
    raw_from = _query(
        sync_url,
        "SELECT a_from FROM article_data_mime WHERE article_id = :a",
        a=unknown["ArticleID"],
    )[0][0]
    assert raw_from == "someone@example.org"


@pytest.mark.db
async def test_customer_follow_up_gets_the_auto_follow_up_and_ticket_data(
    sync_url: str, relay: CapturingMailSender
) -> None:
    created = await _call(
        sync_url,
        "create",
        {
            "Ticket": {**_ticket(), "CustomerUser": CUST_LOGIN},
            "Article": _customer_article("Start"),
        },
    )
    ticket_id = created["TicketID"]
    relay.sent.clear()
    result = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": {"Subject": "Nachfrage", "Body": "Noch da?"}},
        customer=True,
        operation_config=_operation_config("Ticket::TicketUpdate", include="1"),
    )
    assert "Error" not in result, result
    assert len(relay.sent) == 1
    assert CUST_MAIL in str(relay.sent[0]["To"])
    assert "Nachfrage" in str(relay.sent[0]["Subject"])
    assert _history_names(sync_url, ticket_id).count("SendAutoFollowUp") == 1
    assert result["Ticket"]["Article"]["ArticleID"] == result["ArticleID"]


@pytest.mark.db
async def test_invalid_customer_user_still_yields_a_full_name_from(
    sync_url: str, relay: CapturingMailSender
) -> None:
    # UserFullname comes from CustomerUserDataGet, which does not filter on validity
    _query(sync_url, "UPDATE customer_user SET valid_id = 2 WHERE login = :l", l=CUST_LOGIN)
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": {**_ticket(), "CustomerUser": CUST_LOGIN},
            "Article": {"Subject": "Alt", "Body": "Hallo", "SenderType": "customer"},
        },
    )
    assert "Error" not in result, result
    from_header = _query(
        sync_url,
        "SELECT a_from FROM article_data_mime WHERE article_id = :a",
        a=result["ArticleID"],
    )[0][0]
    assert from_header == f'"Kim Kunde" <{CUST_MAIL}>'


# ---------------------------------------------------------------------------
# GI mail delivery like Znuny's MailQueue: retries, interruption, queue snapshot
# ---------------------------------------------------------------------------


def _due_in_minutes(job: dict[str, Any]) -> float:
    from tiqora.jobs.service import utc_now

    assert job["due_at"] is not None
    return float((job["due_at"] - utc_now()).total_seconds()) / 60


def _log_details(sync_url: str) -> list[str]:
    rows = _query(
        sync_url,
        f"SELECT detail FROM tiqora_mail_log WHERE ticket_id IN {_IN_QUEUE} ORDER BY id",
    )
    return [str(r[0] or "") for r in rows]


@pytest.mark.db
@pytest.mark.parametrize(
    "error",
    [
        _temporary,
        lambda: aiosmtplib.SMTPResponseException(451, "4.3.0 try again later"),
        lambda: aiosmtplib.SMTPRecipientsRefused(
            [aiosmtplib.SMTPRecipientRefused(450, "4.2.0 mailbox busy", CUSTOMER_MAIL)]
        ),
    ],
    ids=["connection", "4xx-reply", "4xx-recipient"],
)
async def test_temporary_send_error_is_retried_without_a_transmission_error(
    sync_url: str, monkeypatch: pytest.MonkeyPatch, error: Any
) -> None:
    sender = _ScriptedSender(fail_on=(1,), error=error)
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}
    )
    assert "Error" not in result, result
    article_id = result["ArticleID"]
    (job,) = _jobs(sync_url)
    # MailQueue::_SendError for a temporary error: attempts 1, due in 1 * 2 minutes
    assert (job["status"], job["attempts"], job["checkpoint"]) == ("queued", 1, None)
    assert 1.5 < _due_in_minutes(job) <= 2.0
    assert _send_errors(sync_url) == []
    assert _mail_log(sync_url) == [("failed", article_id)]
    assert "temporary error, attempt 1 of 3, next attempt in 2 min" in _log_details(sync_url)[0]
    # not due yet: draining again sends nothing
    assert await drain_gi_mail_jobs(sync_url) == []
    make_due(sync_url, above_id=_JOB_WATERMARK[sync_url])
    done = await drain_gi_mail_jobs(sync_url)
    assert [d["status"] for d in done] == ["succeeded"]
    assert len(sender.sent) == 1 and sender.attempts == 2
    assert _mail_log(sync_url) == [("failed", article_id), ("sent", article_id)]
    assert _send_errors(sync_url) == []
    assert [(j["status"], j["attempts"]) for j in _jobs(sync_url)] == [("succeeded", 2)]


@pytest.mark.db
async def test_third_temporary_failure_is_final_like_item_max_attempts(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender(fail_on=(1, 2, 3, 4), error=_temporary)
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}
    )
    article_id = result["ArticleID"]
    make_due(sync_url, above_id=_JOB_WATERMARK[sync_url])
    await drain_gi_mail_jobs(sync_url)
    (job,) = _jobs(sync_url)
    assert (job["status"], job["attempts"]) == ("queued", 2)
    assert 3.5 < _due_in_minutes(job) <= 4.0  # attempts * IncrementAttemptDelayInMinutes
    assert _send_errors(sync_url) == []
    make_due(sync_url, above_id=_JOB_WATERMARK[sync_url])
    done = await drain_gi_mail_jobs(sync_url)
    assert [d["status"] for d in done] == ["failed"]
    assert sender.attempts == 3 and sender.sent == []
    assert _mail_log(sync_url) == [("failed", article_id)] * 3
    errors = _send_errors(sync_url)
    assert [a for a, _, _ in errors] == [article_id]
    assert "connection refused" in errors[0][2]
    # a failed job is never picked up again
    make_due(sync_url, above_id=_JOB_WATERMARK[sync_url])
    assert await drain_gi_mail_jobs(sync_url) == []


@pytest.mark.db
async def test_temporary_auto_response_error_is_retried(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender(fail_on=(1,), error=_temporary)
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _customer_article("Wieder")}
    )
    ticket_id = result["TicketID"]
    (job,) = _jobs(sync_url)
    assert (job["status"], job["attempts"]) == ("queued", 1)
    assert _article_count(sync_url, ticket_id) == 1  # nothing of the failed attempt stays
    assert _mail_log(sync_url) == [("failed", None)]
    make_due(sync_url, above_id=_JOB_WATERMARK[sync_url])
    assert [d["status"] for d in await drain_gi_mail_jobs(sync_url)] == ["succeeded"]
    assert len(sender.sent) == 1
    assert _history_names(sync_url, ticket_id).count("SendAutoReply") == 1


async def _reap_as_interrupted(sync_url: str, *, checkpoint: str | None) -> int:
    """Make this module's queued gi_mail job look like the claim of a worker that died, then
    let another worker's reaper settle it. Returns the reaped count."""
    from datetime import timedelta

    from sqlalchemy import update

    from tiqora.config import get_settings
    from tiqora.db.tiqora.models import TiqoraJob
    from tiqora.extensions import get_registry
    from tiqora.jobs.runner import JobRunner
    from tiqora.jobs.service import utc_now

    (job,) = _jobs(sync_url)
    old = utc_now() - timedelta(minutes=10)
    engine = create_async_engine(_async_url(sync_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                update(TiqoraJob)
                .where(TiqoraJob.id == job["id"])
                .values(
                    status="running",
                    worker_id="dead:1",
                    started_at=old,
                    heartbeat_at=old,
                    attempts=1,
                    checkpoint=checkpoint,
                )
            )
            await session.commit()
        runner = JobRunner(
            session_factory=factory, settings=get_settings(), registry=get_registry()
        )
        return await runner._reap()
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_mail_interrupted_before_sending_is_requeued_and_sent_once(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    sender = _ScriptedSender()
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}, drain=False
    )
    assert await _reap_as_interrupted(sync_url, checkpoint=None) >= 1
    (job,) = _jobs(sync_url)
    assert (job["status"], job["attempts"]) == ("queued", 1)
    assert _send_errors(sync_url) == [] and _mail_log(sync_url) == []
    assert [d["status"] for d in await drain_gi_mail_jobs(sync_url)] == ["succeeded"]
    assert len(sender.sent) == 1
    assert _mail_log(sync_url) == [("sent", result["ArticleID"])]


@pytest.mark.db
async def test_mail_interrupted_while_sending_is_recorded_and_never_resent(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.jobs.handlers.gi_mail import INTERRUPTED_SEND_ERROR

    sender = _ScriptedSender()
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}, drain=False
    )
    article_id = result["ArticleID"]
    assert await _reap_as_interrupted(sync_url, checkpoint="sending") >= 1
    (job,) = _jobs(sync_url)
    assert job["status"] == "failed"
    # recorded like a final MailQueue failure: transmission error + failed mail-log row
    errors = _send_errors(sync_url)
    assert [(a, e) for a, _, e in errors] == [(article_id, INTERRUPTED_SEND_ERROR)]
    assert _mail_log(sync_url) == [("failed", article_id)]
    assert await drain_gi_mail_jobs(sync_url) == []
    assert sender.attempts == 0


@pytest.mark.db
async def test_worker_shutdown_during_smtp_records_the_mail(
    sync_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real handler sets its checkpoint before SMTP; a shutdown that cuts the send off
    leaves a recorded failure, not an article that looks sent."""
    from tiqora.config import get_settings
    from tiqora.extensions import get_registry
    from tiqora.jobs.handlers.gi_mail import INTERRUPTED_SEND_ERROR
    from tiqora.jobs.runner import JobRunner

    sender = _ScriptedSender(block=True)
    _use_relay(monkeypatch, sender)
    result = await _call(
        sync_url, "create", {"Ticket": _ticket(), "Article": _send_article("Eins")}, drain=False
    )
    (job,) = _jobs(sync_url)
    engine = create_async_engine(_async_url(sync_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        runner = JobRunner(
            session_factory=factory, settings=get_settings(), registry=get_registry()
        )
        attempt = await runner._claim(job["id"])
        task = asyncio.create_task(
            runner._execute(job["id"], "gi_mail", _payload_json(job), None, attempt=attempt)
        )
        runner._active[job["id"]] = task
        for _ in range(200):
            if sender.attempts:
                break
            await asyncio.sleep(0.05)
        assert sender.attempts == 1
        await runner.shutdown(grace_seconds=0.1)
    finally:
        await engine.dispose()
    (job,) = _jobs(sync_url)
    assert (job["status"], job["checkpoint"]) == ("failed", "sending")
    assert [(a, e) for a, _, e in _send_errors(sync_url)] == [
        (result["ArticleID"], INTERRUPTED_SEND_ERROR)
    ]
    assert _mail_log(sync_url) == [("failed", result["ArticleID"])]


def _payload_json(job: dict[str, Any]) -> str:
    import json

    return json.dumps(job["payload"])


@pytest.mark.db
async def test_auto_response_queue_is_decided_when_the_article_is_stored(
    sync_url: str, relay: CapturingMailSender
) -> None:
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(), "Article": _customer_article("Vorher")},
        drain=False,
    )
    ticket_id = result["TicketID"]
    (job,) = _jobs(sync_url)
    assert job["payload"]["queue_id"] == QUEUE
    # the ticket moves (e.g. triage) before the worker runs: Znuny already decided the auto
    # response in ArticleCreate, so the old queue's auto reply goes out
    _query(
        sync_url,
        "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
        " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
        f" change_time, change_by) VALUES ({_OTHER_QUEUE}, 'GI7653Queue', {GROUP}, 1, 1, 1,"
        " 1, 0, 1, :t, 1, :t, 1)",
        t=T0,
    )
    try:
        _query(sync_url, f"UPDATE ticket SET queue_id = {_OTHER_QUEUE} WHERE id = {ticket_id}")
        assert [d["status"] for d in await drain_gi_mail_jobs(sync_url)] == ["succeeded"]
    finally:
        # back, so the module cleanup finds it (the queue row goes with the cleanup)
        _query(sync_url, f"UPDATE ticket SET queue_id = {QUEUE} WHERE id = {ticket_id}")
    assert len(relay.sent) == 1
    assert _history_names(sync_url, ticket_id).count("SendAutoReply") == 1


def test_gi_mail_runs_in_its_own_serial_lane_with_mail_queue_attempts() -> None:
    from tiqora.jobs.handlers.gi_mail import GI_MAIL_JOB
    from tiqora.jobs.registry import LANE_SLOTS, MAIL_LANE

    assert GI_MAIL_JOB.lane == MAIL_LANE and LANE_SLOTS[MAIL_LANE] == 1
    assert GI_MAIL_JOB.max_attempts == 3  # MailQueue###ItemMaxAttempts default
    assert GI_MAIL_JOB.requeue_if_interrupted and GI_MAIL_JOB.on_interrupted is not None
