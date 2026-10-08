"""Znuny-shaped TicketGet / TicketHistoryGet (GenericInterface compat).

Pure ``attachment_index`` / ``znuny_ts`` tests plus DB tests on MariaDB and PostgreSQL.
Seed ids live in the GI band 8950-8969; every row is deleted again after the module.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.compat.operations import op_ticket_get, op_ticket_history_get
from tiqora.api.compat.ticket_get import AttachmentRow, attachment_index, znuny_num, znuny_ts
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import SessionStore

T0 = datetime(2024, 6, 1, 12, 0, 0)
GOOD_TS = r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _row(
    fid: int,
    name: str,
    ct: str,
    disp: str = "attachment",
    cid: str = "",
    alt: str = "",
    size: int = 10,
) -> AttachmentRow:
    return AttachmentRow(
        file_id=fid,
        filename=name,
        content_type=ct,
        content_id=cid,
        content_alternative=alt,
        disposition=disp,
        content_size=size,
        content=None,
    )


def _ids(rows: list[AttachmentRow]) -> list[int]:
    return [r.file_id for r in rows]


def test_znuny_ts_format_and_none() -> None:
    assert znuny_ts(datetime(2024, 6, 1, 12, 0, 5, 123456)) == "2024-06-01 12:00:05"
    assert znuny_ts(None) == ""


def test_znuny_num_follows_perl_numification() -> None:
    assert znuny_num(Decimal("3.00")) == 3
    assert isinstance(znuny_num(Decimal("3.00")), int)
    assert znuny_num(Decimal("1.50")) == 1.5
    assert znuny_num(None) == 0


def test_plain_and_html_body_parts_detected() -> None:
    rows = [
        _row(1, "file-1", "text/plain", "inline"),
        _row(2, "file-2", "text/html", "inline"),
        _row(3, "report.pdf", "application/pdf"),
    ]
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=True,
            exclude_inline=False,
            html_body=None,
        )
    ) == [3]
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=False,
            exclude_inline=False,
            html_body=None,
        )
    ) == [2, 3]
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=False,
            exclude_html_body=False,
            exclude_inline=False,
            html_body=None,
        )
    ) == [1, 2, 3]


def test_file_1_html_variant_is_the_html_body() -> None:
    rows = [_row(1, "file-1.html", "text/html; charset=utf-8", "inline"), _row(2, "a.pdf", "x/y")]
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=True,
            exclude_inline=False,
            html_body=None,
        )
    ) == [2]


def test_fallback_plain_text_without_inline_disposition() -> None:
    rows = [_row(1, "file-1", "text/plain", "attachment"), _row(2, "a.txt", "text/plain")]
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=True,
            exclude_inline=False,
            html_body=None,
        )
    ) == [2]


def test_fallback_is_skipped_when_an_html_body_exists() -> None:
    # An inline html part was found, so the plain-text fallback pass never runs.
    rows = [_row(1, "file-1", "text/plain", "attachment"), _row(2, "file-2", "text/html", "inline")]
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=True,
            exclude_inline=False,
            html_body=None,
        )
    ) == [1]


def test_inline_images_only_when_referenced() -> None:
    rows = [
        _row(1, "file-1", "text/plain", "inline"),
        _row(2, "file-2", "text/html", "inline"),
        _row(3, "used.png", "image/png", "inline", cid="<img1@x>"),
        _row(4, "unused.png", "image/png", "inline", cid="<img2@x>"),
        _row(5, "nocid.png", "image/png", "inline"),
    ]
    html = '<p>hi</p><img alt="x" src="cid:img1@x">'
    kept = attachment_index(
        rows,
        exclude_plain_text=True,
        exclude_html_body=True,
        exclude_inline=True,
        html_body=html,
    )
    assert _ids(kept) == [4, 5]
    # Without the html body content nothing can be recognised as referenced.
    assert _ids(
        attachment_index(
            rows,
            exclude_plain_text=True,
            exclude_html_body=True,
            exclude_inline=True,
            html_body=None,
        )
    ) == [3, 4, 5]


# ---------------------------------------------------------------------------
# DB seed
# ---------------------------------------------------------------------------

AGENT_ID, GROUP_ID, QUEUE_ID, SERVICE_ID, SLA_ID = 8950, 8950, 8951, 8950, 8950
TICKET, TICKET_NO_SVC, TICKET_FR = 8950, 8955, 8956
ART_MAIL, ART_NOTE, ART_AGENT = 8950, 8951, 8953
DF_TEXT, DF_ARTICLE, DF_MULTI, DF_EMPTY = 8950, 8951, 8952, 8953
AGENT_SESSION, CUST_SESSION = "GI8950AGENTSESSION", "GI8950CUSTSESSION"
CUST_LOGIN = "gi8950.cust"
PLAIN, HTML, PDF = b"plain body", b"<p>html body</p>", b"%PDF-1.4 fake"

TICKET_KEYS = {
    "Age",
    "ArchiveFlag",
    "ChangeBy",
    "Changed",
    "CreateBy",
    "Created",
    "CustomerID",
    "CustomerUserID",
    "EscalationResponseTime",
    "EscalationSolutionTime",
    "EscalationTime",
    "EscalationUpdateTime",
    "GroupID",
    "Lock",
    "LockID",
    "Owner",
    "OwnerID",
    "Priority",
    "PriorityID",
    "Queue",
    "QueueID",
    "RealTillTimeNotUsed",
    "Responsible",
    "ResponsibleID",
    "SLA",
    "SLAID",
    "Service",
    "ServiceID",
    "State",
    "StateID",
    "StateType",
    "TicketID",
    "TicketNumber",
    "Title",
    "Type",
    "TypeID",
    "UnlockTimeout",
    "UntilTime",
    "TimeUnit",
}
ATTACHMENT_KEYS = {
    "Content",
    "ContentAlternative",
    "ContentID",
    "ContentType",
    "Disposition",
    "FileID",
    "Filename",
    "FilesizeRaw",
}
ARTICLE_KEYS = {
    "ArticleID",
    "ArticleNumber",
    "Bcc",
    "Body",
    "Cc",
    "ChangeBy",
    "ChangeTime",
    "Charset",
    "CommunicationChannel",
    "CommunicationChannelID",
    "ContentCharset",
    "ContentType",
    "CreateBy",
    "CreateTime",
    "From",
    "IncomingTime",
    "InReplyTo",
    "IsVisibleForCustomer",
    "MessageID",
    "MimeType",
    "References",
    "ReplyTo",
    "SenderType",
    "SenderTypeID",
    "Subject",
    "TicketID",
    "To",
    "TimeUnit",
}

_SEEDED: list[str] = []


def _cleanup_statements() -> tuple[str, ...]:
    return (
        "DELETE FROM time_accounting WHERE ticket_id BETWEEN 8950 AND 8969",
        "DELETE FROM ticket_history WHERE ticket_id BETWEEN 8950 AND 8969",
        "DELETE FROM dynamic_field_value WHERE field_id BETWEEN 8950 AND 8959",
        "DELETE FROM dynamic_field WHERE id BETWEEN 8950 AND 8959",
        "DELETE FROM article_data_mime_attachment WHERE article_id BETWEEN 8950 AND 8969",
        "DELETE FROM article_data_mime WHERE article_id BETWEEN 8950 AND 8969",
        "DELETE FROM article WHERE id BETWEEN 8950 AND 8969",
        "DELETE FROM ticket WHERE id BETWEEN 8950 AND 8969",
        "DELETE FROM sessions WHERE session_id LIKE 'GI8950%'",
        "DELETE FROM customer_user WHERE login = 'gi8950.cust'",
        "DELETE FROM queue WHERE id = 8951",
        "DELETE FROM group_user WHERE group_id = 8950",
        "DELETE FROM permission_groups WHERE id = 8950",
        "DELETE FROM sla WHERE id = 8950",
        "DELETE FROM service WHERE id = 8950",
        "DELETE FROM users WHERE id = 8950",
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _cleanup_statements():
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _ticket_insert(conn: Any, tid: int, tn: str, service: int | None, sla: int | None) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " sla_id, user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            " customer_id, customer_user_id, timeout, until_time, escalation_time,"
            " escalation_update_time, escalation_response_time, escalation_solution_time,"
            " archive_flag, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tn, 'GI TicketGet', 8951, 1, 1, :svc, :sla, 8950, 1, 3, 1,"
            " 'GI8950', 'gi8950.cust', 0, 0, 0, 0, 0, 0, 0, :t, 8950, :t, 8950)"
        ),
        {"id": tid, "tn": tn, "svc": service, "sla": sla, "t": T0},
    )


def _article_insert(
    conn: Any,
    aid: int,
    tid: int,
    *,
    sender: int,
    channel: int,
    visible: int,
    body: str,
    ctype: str = 'text/plain; charset="utf-8"',
    created: datetime = T0,
) -> None:
    conn.execute(
        text(
            "INSERT INTO article (id, ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tid, :s, :ch, :v, :t, 8950, :t, 8950)"
        ),
        {"id": aid, "tid": tid, "s": sender, "ch": channel, "v": visible, "t": created},
    )
    conn.execute(
        text(
            "INSERT INTO article_data_mime (article_id, a_from, a_to, a_subject, a_message_id,"
            " a_content_type, a_body, incoming_time, create_time, create_by, change_time,"
            " change_by) VALUES (:id, :f, :to, :subj, :mid, :ct, :body, 1717243200, :t, 8950,"
            " :t, 8950)"
        ),
        {
            "id": aid,
            "f": "Max Muster <max@example.org>\r\n",
            "to": "support@example.org",
            "subj": f"Subject {aid}",
            "mid": f"<{aid}@example.org>",
            "ct": ctype,
            "body": body,
            "t": created,
        },
    )


def _attachment_insert(
    conn: Any,
    aid: int,
    filename: str,
    ctype: str,
    content: bytes,
    disposition: str,
) -> None:
    conn.execute(
        text(
            "INSERT INTO article_data_mime_attachment (article_id, filename, content_size,"
            " content_type, content_id, content_alternative, disposition, content, create_time,"
            " create_by, change_time, change_by) VALUES (:a, :fn, :sz, :ct, NULL, NULL, :d, :c,"
            " :t, 8950, :t, 8950)"
        ),
        {
            "a": aid,
            "fn": filename,
            "sz": str(len(content)),
            "ct": ctype,
            "d": disposition,
            "c": content,
            "t": T0,
        },
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = int(time.time())
    with engine.begin() as conn:
        # customer scope reads tiqora_settings (portal.company_tickets_enabled)
        TiqoraSettings.__table__.create(conn, checkfirst=True)  # type: ignore[attr-defined]
        for stmt in _cleanup_statements():
            conn.execute(text(stmt))
        for stmt, params in (
            (
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (8950, 'gi8950.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)",
                {"t": T0},
            ),
            (
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (8950, 'gi8950-group', 1, :t, 1, :t, 1)",
                {"t": T0},
            ),
            (
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (8950, 8950, 'ro', :t, 1, :t, 1)",
                {"t": T0},
            ),
            (
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (8951, 'GI8950Queue', 8950, 1, 1, 1, 1, 0, 1,"
                " :t, 1, :t, 1)",
                {"t": T0},
            ),
            (
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (8950, 'GI8950Service', 1, :t, 1, :t, 1)",
                {"t": T0},
            ),
            (
                "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (8950, 'GI8950SLA', 0, 0, 0, 1, :t, 1, :t, 1)",
                {"t": T0},
            ),
            (
                "INSERT INTO customer_user (login, email, customer_id, pw, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('gi8950.cust', 'gi8950@example.org', 'GI8950', 'x', 'Gi', 'Cust', 1,"
                " :t, 1, :t, 1)",
                {"t": T0},
            ),
        ):
            conn.execute(text(stmt), params)

        for sid, extra in (
            (AGENT_SESSION, {"UserID": "8950", "UserLogin": "gi8950.agent", "UserType": "User"}),
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

        _ticket_insert(conn, TICKET, "8950001", SERVICE_ID, SLA_ID)
        _ticket_insert(conn, TICKET_NO_SVC, "8950002", None, None)
        _ticket_insert(conn, TICKET_FR, "8950003", None, None)

        _article_insert(conn, ART_MAIL, TICKET, sender=3, channel=1, visible=1, body="Hello\nworld")
        _article_insert(
            conn,
            ART_NOTE,
            TICKET,
            sender=1,
            channel=3,
            visible=0,
            body="internal",
            created=datetime(2024, 6, 1, 12, 5, 0),
        )
        _article_insert(conn, ART_AGENT, TICKET_FR, sender=1, channel=1, visible=1, body="answer")
        _attachment_insert(conn, ART_MAIL, "file-1", "text/plain; charset=utf-8", PLAIN, "inline")
        _attachment_insert(conn, ART_MAIL, "file-2", "text/html; charset=utf-8", HTML, "inline")
        _attachment_insert(conn, ART_MAIL, "report.pdf", "application/pdf", PDF, "attachment")

        for aid, unit in ((ART_MAIL, "1.50"), (ART_MAIL, "1.50")):
            conn.execute(
                text(
                    "INSERT INTO time_accounting (ticket_id, article_id, time_unit, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (8950, :a, :u, :t, 8950, :t, 8950)"
                ),
                {"a": aid, "u": Decimal(unit), "t": T0},
            )

        for fid, name, ftype, otype in (
            (DF_TEXT, "Gi8950Text", "Text", "Ticket"),
            (DF_MULTI, "Gi8950Multi", "Multiselect", "Ticket"),
            (DF_EMPTY, "Gi8950Empty", "Text", "Ticket"),
            (DF_ARTICLE, "Gi8950Art", "Text", "Article"),
        ):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :id, :ft, :ot, :cfg, 1, :t, 1, :t, 1)"
                ),
                {"id": fid, "n": name, "ft": ftype, "ot": otype, "cfg": b"---", "t": T0},
            )
        for fid, oid, val in (
            (DF_TEXT, TICKET, "hello"),
            (DF_MULTI, TICKET, "a"),
            (DF_MULTI, TICKET, "b"),
            (DF_ARTICLE, ART_MAIL, "art-val"),
        ):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                    " VALUES (:f, :o, :v)"
                ),
                {"f": fid, "o": oid, "v": val},
            )

        # history for TicketHistoryGet / Extended (Lock + first-lock stamp)
        for name, htype, when in (
            ("%%NewTicket", "NewTicket", datetime(2024, 6, 1, 12, 0, 5)),
            ("%%lock", "Lock", datetime(2024, 6, 1, 12, 0, 7)),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket_history (name, history_type_id, ticket_id, type_id,"
                    " queue_id, owner_id, priority_id, state_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:n, (SELECT id FROM ticket_history_type"
                    " WHERE name = :ht), 8950, 1, 8951, 8950, 3, 1, :w, 8950, :w, 8950)"
                ),
                {"n": name, "ht": htype, "w": when},
            )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


class _FakeRedis:
    async def get(self, key: str) -> str | None:
        return None


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


async def _get(sync_url: str, params: dict[str, Any], *, customer: bool = False) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            return await op_ticket_get(
                {"SessionID": CUST_SESSION if customer else AGENT_SESSION, **params},
                session,
                store,
            )
    finally:
        await engine.dispose()


def _by_id(articles: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(a["ArticleID"]): a for a in articles}


@pytest.mark.db
async def test_ticket_keys_and_formats(sync_url: str) -> None:
    result = await _get(sync_url, {"TicketID": TICKET})
    assert "Error" not in result, result
    ticket = result["Ticket"][0]
    assert set(ticket) == TICKET_KEYS
    assert ticket["TicketID"] == TICKET
    assert ticket["TicketNumber"] == "8950001"
    assert re.match(GOOD_TS, ticket["Created"])
    assert ticket["Created"] == "2024-06-01 12:00:00"
    assert ticket["Changed"] == "2024-06-01 12:00:00"
    assert ticket["TimeUnit"] == 3
    assert isinstance(ticket["TimeUnit"], int)
    assert ticket["ArchiveFlag"] == "n"
    assert ticket["Queue"] == "GI8950Queue"
    assert ticket["GroupID"] == GROUP_ID
    assert ticket["Owner"] == "gi8950.agent"
    assert ticket["Responsible"] == "root@localhost"
    assert ticket["ResponsibleID"] == 1
    assert ticket["Service"] == "GI8950Service" and ticket["SLA"] == "GI8950SLA"
    assert ticket["StateType"] == "new" and ticket["Lock"] == "unlock"
    assert ticket["CreateBy"] == AGENT_ID and ticket["ChangeBy"] == AGENT_ID
    assert ticket["UntilTime"] == 0 and ticket["RealTillTimeNotUsed"] == 0
    assert ticket["Age"] > 0
    assert "Article" not in ticket and "DynamicField" not in ticket
    assert "CreateTime" not in ticket and "ChangeTime" not in ticket


@pytest.mark.db
async def test_service_and_sla_keys_omitted_when_empty(sync_url: str) -> None:
    result = await _get(sync_url, {"TicketID": TICKET_NO_SVC})
    ticket = result["Ticket"][0]
    assert set(ticket) == TICKET_KEYS - {"Service", "SLA"}
    assert ticket["ServiceID"] == "" and ticket["SLAID"] == ""
    assert ticket["TimeUnit"] == 0


@pytest.mark.db
async def test_ticket_dynamic_fields_follow_znuny_value_get(sync_url: str) -> None:
    result = await _get(sync_url, {"TicketID": TICKET, "DynamicFields": 1})
    fields = result["Ticket"][0]["DynamicField"]
    # only fields with stored values, sorted by name; Multiselect is always a list
    assert fields == [
        {"Name": "Gi8950Multi", "Value": ["a", "b"]},
        {"Name": "Gi8950Text", "Value": "hello"},
    ]


@pytest.mark.db
async def test_articles_keys_attachments_and_html_flag(sync_url: str) -> None:
    params: dict[str, Any] = {"TicketID": TICKET, "AllArticles": 1, "Attachments": 1}
    articles = (await _get(sync_url, params))["Ticket"][0]["Article"]
    assert [a["ArticleID"] for a in articles] == [ART_MAIL, ART_NOTE]
    first, second = articles
    assert set(first) == ARTICLE_KEYS | {"Attachment"}
    # Znuny skips the Attachment key when the (filtered) index is empty
    assert set(second) == ARTICLE_KEYS
    assert [a["Filename"] for a in first["Attachment"]] == ["report.pdf"]
    att = first["Attachment"][0]
    assert set(att) == ATTACHMENT_KEYS
    assert att["FileID"] == 3 and att["Disposition"] == "attachment"
    assert att["ContentType"] == "application/pdf"
    assert att["FilesizeRaw"] == len(PDF)
    assert base64.b64decode(att["Content"]) == PDF
    assert att["ContentID"] == "" and att["ContentAlternative"] == ""

    assert first["ArticleNumber"] == 1 and second["ArticleNumber"] == 2
    assert first["TimeUnit"] == 3 and second["TimeUnit"] == 0
    assert first["SenderType"] == "customer" and first["SenderTypeID"] == 3
    assert first["CommunicationChannel"] == "Email" and first["CommunicationChannelID"] == 1
    assert first["IsVisibleForCustomer"] == 1 and second["IsVisibleForCustomer"] == 0
    assert first["Charset"] == "utf-8" and first["ContentCharset"] == "utf-8"
    assert first["MimeType"] == "text/plain"
    assert first["From"] == "Max Muster <max@example.org>"  # CR/LF stripped like ArticleGet
    assert first["Body"] == "Hello\nworld"
    assert first["CreateTime"] == "2024-06-01 12:00:00"
    assert second["CreateTime"] == "2024-06-01 12:05:00"
    assert first["IncomingTime"] == 1717243200
    assert "FromRealname" not in first  # GenericInterface never passes RealNames

    with_html = (await _get(sync_url, {**params, "HTMLBodyAsAttachment": 1}))["Ticket"][0][
        "Article"
    ][0]
    assert [a["Filename"] for a in with_html["Attachment"]] == ["file-2", "report.pdf"]
    assert [a["FileID"] for a in with_html["Attachment"]] == [2, 3]

    no_content = (await _get(sync_url, {**params, "GetAttachmentContents": 0}))["Ticket"][0][
        "Article"
    ][0]
    assert no_content["Attachment"][0]["Content"] == ""
    assert no_content["Attachment"][0]["ContentAlternative"] == ""
    assert no_content["Attachment"][0]["FilesizeRaw"] == len(PDF)

    plain = (await _get(sync_url, {"TicketID": TICKET, "AllArticles": 1}))["Ticket"][0]["Article"]
    assert "Attachment" not in plain[0]


@pytest.mark.db
async def test_article_dynamic_fields_only_with_flag(sync_url: str) -> None:
    base = {"TicketID": TICKET, "AllArticles": 1}
    off = (await _get(sync_url, base))["Ticket"][0]["Article"]
    assert all("DynamicField" not in a for a in off)

    on = _by_id((await _get(sync_url, {**base, "DynamicFields": 1}))["Ticket"][0]["Article"])
    # every valid Article-object field is listed (null when unset); ticket fields never leak in
    assert on[ART_MAIL]["DynamicField"] == [{"Name": "Gi8950Art", "Value": "art-val"}]
    assert on[ART_NOTE]["DynamicField"] == [{"Name": "Gi8950Art", "Value": None}]


@pytest.mark.db
async def test_customer_sees_only_visible_articles(sync_url: str) -> None:
    params = {"TicketID": TICKET, "AllArticles": 1}
    customer = (await _get(sync_url, params, customer=True))["Ticket"][0]["Article"]
    assert [a["ArticleID"] for a in customer] == [ART_MAIL]
    assert customer[0]["ArticleNumber"] == 1
    agent = (await _get(sync_url, params))["Ticket"][0]["Article"]
    assert [a["ArticleID"] for a in agent] == [ART_MAIL, ART_NOTE]


@pytest.mark.db
async def test_sender_type_filter_order_and_limit(sync_url: str) -> None:
    base: dict[str, Any] = {"TicketID": TICKET, "AllArticles": 1}

    def ids(result: dict[str, Any]) -> list[int]:
        return [a["ArticleID"] for a in result["Ticket"][0]["Article"]]

    # grouped per requested sender type, in the order the types were given
    assert ids(await _get(sync_url, {**base, "ArticleSenderType": ["agent", "customer"]})) == [
        ART_NOTE,
        ART_MAIL,
    ]
    assert ids(await _get(sync_url, {**base, "ArticleSenderType": "agent"})) == [ART_NOTE]
    # unknown sender type -> no articles, so the Article key is absent like Znuny
    assert (
        "Article"
        not in (await _get(sync_url, {**base, "ArticleSenderType": ["nope"]}))["Ticket"][0]
    )
    # ArticleOrder DESC only reverses when a limit applies (Znuny quirk)
    assert ids(await _get(sync_url, {**base, "ArticleOrder": "DESC"})) == [ART_MAIL, ART_NOTE]
    limited = await _get(
        sync_url,
        {
            **base,
            "ArticleSenderType": ["customer", "agent"],
            "ArticleLimit": 1,
            "ArticleOrder": "DESC",
        },
    )
    assert ids(limited) == [ART_NOTE]
    asc = await _get(sync_url, {**base, "ArticleLimit": 1})
    assert ids(asc) == [ART_MAIL]
    # a limit above the article count is clamped
    assert ids(await _get(sync_url, {**base, "ArticleLimit": 50})) == [ART_MAIL, ART_NOTE]


@pytest.mark.db
async def test_extended_adds_first_lock_and_first_response(sync_url: str) -> None:
    locked = (await _get(sync_url, {"TicketID": TICKET, "Extended": 1}))["Ticket"][0]
    assert set(locked) == TICKET_KEYS | {"FirstLock"}
    assert locked["FirstLock"] == "2024-06-01 12:00:07"

    answered = (await _get(sync_url, {"TicketID": TICKET_FR, "Extended": 1}))["Ticket"][0]
    assert answered["FirstResponse"] == "2024-06-01 12:00:00"
    assert "FirstLock" not in answered and "Closed" not in answered


@pytest.mark.db
async def test_history_get_timestamp_format(sync_url: str) -> None:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            session_: AsyncSession = session
            result = await op_ticket_history_get(
                {"SessionID": AGENT_SESSION, "TicketID": TICKET}, session_, store
            )
    finally:
        await engine.dispose()
    lines = result["TicketHistory"][0]["History"]
    assert [line["CreateTime"] for line in lines] == [
        "2024-06-01 12:00:05",
        "2024-06-01 12:00:07",
    ]
