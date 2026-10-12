"""``ticket_deep_get`` / ``prepare_request`` against real Znuny tables (both dialects).

Seed ids live in the GI band 51080-51159; every row is deleted again after the module.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Coroutine, Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._gi_fixtures import requester_config
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.gi.config import InvokerConfig, parse_requester
from tiqora.gi.deep_get import ticket_deep_get
from tiqora.gi.invoker_generic import prepare_request
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

T0 = datetime(2026, 1, 1, 10, 0, 0)
USER, USER_OTHER = 51080, 51081
QUEUE, SERVICE, SERVICE_PARENT, SLA = 51080, 51080, 51081, 51080
TICKET, TICKET_ONE, TICKET_NONE = 51080, 51082, 51083
ART_MAIL, ART_NOTE, ART_ONE = 51080, 51081, 51082
DF_TICKET, DF_ART = 51080, 51081
LOGIN = "gi51080@example.org"
COMPANY = "GI51080"
PLAIN, HTML, PDF = b"plain body", b"<p>html body</p>", b"%PDF-1.4 fake"
SYSCONFIG_BASE = 51080

_SEEDED: list[str] = []


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    for stmt in (
        "DELETE FROM sysconfig_modified WHERE id BETWEEN 51080 AND 51089",
        "DELETE FROM sysconfig_default WHERE id BETWEEN 51080 AND 51089",
        "DELETE FROM time_accounting WHERE ticket_id BETWEEN 51080 AND 51099",
        "DELETE FROM dynamic_field_value WHERE field_id BETWEEN 51080 AND 51089",
        "DELETE FROM dynamic_field WHERE id BETWEEN 51080 AND 51089",
        "DELETE FROM article_data_mime_attachment WHERE article_id BETWEEN 51080 AND 51099",
        "DELETE FROM article_data_mime WHERE article_id BETWEEN 51080 AND 51099",
        "DELETE FROM article WHERE id BETWEEN 51080 AND 51099",
        "DELETE FROM ticket WHERE id BETWEEN 51080 AND 51099",
        "DELETE FROM customer_preferences WHERE user_id = 'gi51080@example.org'",
        "DELETE FROM customer_user WHERE login = 'gi51080@example.org'",
        "DELETE FROM customer_company WHERE customer_id = 'GI51080'",
        "DELETE FROM queue_preferences WHERE queue_id = 51080",
        "DELETE FROM queue WHERE id = 51080",
        "DELETE FROM permission_groups WHERE id = 51080",
        "DELETE FROM sla_preferences WHERE sla_id = 51080",
        "DELETE FROM service_sla WHERE sla_id = 51080",
        "DELETE FROM sla WHERE id = 51080",
        "DELETE FROM service_preferences WHERE service_id IN (51080, 51081)",
        "DELETE FROM service WHERE id IN (51080, 51081)",
        "DELETE FROM user_preferences WHERE user_id IN (51080, 51081)",
        "DELETE FROM users WHERE id IN (51080, 51081)",
    ):
        conn.execute(text(stmt))


def _article(conn: Any, aid: int, tid: int, *, sender: int, channel: int, body: str) -> None:
    conn.execute(
        text(
            "INSERT INTO article (id, ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tid, :s, :ch, 1, :t, 51080, :t, 51080)"
        ),
        {"id": aid, "tid": tid, "s": sender, "ch": channel, "t": T0},
    )
    conn.execute(
        text(
            "INSERT INTO article_data_mime (article_id, a_from, a_to, a_cc, a_subject,"
            " a_message_id, a_content_type, a_body, incoming_time, create_time, create_by,"
            " change_time, change_by) VALUES (:id, :f, 'support@example.org',"
            " 'Cc <cc@example.org>', :subj, :mid, 'text/plain; charset=\"utf-8\"', :body,"
            " 1767261600, :t, 51080, :t, 51080)"
        ),
        {
            "id": aid,
            "f": '"Max Muster" <max@example.org>',
            "subj": f"Subject {aid}",
            "mid": f"<{aid}@example.org>",
            "body": body,
            "t": T0,
        },
    )


def _attachment(conn: Any, aid: int, filename: str, ctype: str, content: bytes, disp: str) -> None:
    conn.execute(
        text(
            "INSERT INTO article_data_mime_attachment (article_id, filename, content_size,"
            " content_type, content_id, content_alternative, disposition, content, create_time,"
            " create_by, change_time, change_by) VALUES (:a, :fn, :sz, :ct, NULL, NULL, :d, :c,"
            " :t, 51080, :t, 51080)"
        ),
        {
            "a": aid,
            "fn": filename,
            "sz": str(len(content)),
            "ct": ctype,
            "d": disp,
            "c": content,
            "t": T0,
        },
    )


def _ticket(conn: Any, tid: int, tn: str, *, customer: bool, service: bool) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " sla_id, user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            " customer_id, customer_user_id, timeout, until_time, escalation_time,"
            " escalation_update_time, escalation_response_time, escalation_solution_time,"
            " archive_flag, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tn, 'GI DeepGet', :q, 1, 1, :svc, :sla, :u, 1, 3, 1, :cid, :cu,"
            " 0, 0, 0, 0, 0, 0, 0, :t, :u, :t, :u)"
        ),
        {
            "id": tid,
            "tn": tn,
            "q": QUEUE,
            "svc": SERVICE if service else None,
            "sla": SLA if service else None,
            "u": USER,
            "cid": COMPANY if customer else "",
            "cu": LOGIN if customer else "",
            "t": T0,
        },
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _cleanup(conn)
        for uid, login, first, last in (
            (USER, "gi51080.agent", "Gi", "Agent"),
            (USER_OTHER, "gi51081.agent", "Other", "Agent"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'secret-hash', :f, :la, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "la": last, "t": T0},
            )
        for key, value in (
            ("UserEmail", "agent@example.org"),
            ("UserLastLogin", "1767261600"),
            ("UserGoogleAuthenticatorSecretKey", "TOPSECRET"),
        ):
            conn.execute(
                text(
                    "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                    " VALUES (:u, :k, :v)"
                ),
                {
                    "u": USER,
                    "k": key,
                    "v": value.encode() if conn.dialect.name != "postgresql" else value,
                },
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (51080, 'gi51080-group', 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, comments, calendar_name,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (51080, 'GI51080Queue', 51080, 1, 1, 1, 1, 0, 1, 'queue note', NULL,"
                " :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        for key, value in (("ParentQueueID", "7"), ("QueueToken", "hidden")):
            conn.execute(
                text(
                    "INSERT INTO queue_preferences (queue_id, preferences_key, preferences_value)"
                    " VALUES (51080, :k, :v)"
                ),
                {"k": key, "v": value},
            )
        for sid, name in ((SERVICE_PARENT, "GI51080Parent"), (SERVICE, "GI51080Parent::Child")):
            conn.execute(
                text(
                    "INSERT INTO service (id, name, valid_id, comments, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, 1, NULL, :t, 1, :t, 1)"
                ),
                {"id": sid, "n": name, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (51080, 'GI51080SLA', 10, 20, 30, 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        conn.execute(text("INSERT INTO service_sla (service_id, sla_id) VALUES (51080, 51080)"))
        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, street, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES ('GI51080', 'Example Corp', 'Main St 1', 1, :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, pw, title, first_name,"
                " last_name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:l, :l, 'GI51080', 'customer-hash', 'Dr.', 'Max', 'Muster', 1, :t, 1,"
                " :t, 1)"
            ),
            {"l": LOGIN, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO customer_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:l, 'UserLanguage', 'de')"
            ),
            {"l": LOGIN},
        )

        _ticket(conn, TICKET, "5108001", customer=True, service=True)
        _ticket(conn, TICKET_ONE, "5108002", customer=False, service=False)
        _ticket(conn, TICKET_NONE, "5108003", customer=False, service=False)
        _article(conn, ART_MAIL, TICKET, sender=3, channel=1, body="Hello\nworld")
        _article(conn, ART_NOTE, TICKET, sender=1, channel=3, body="internal")
        _article(conn, ART_ONE, TICKET_ONE, sender=3, channel=1, body="only one")
        _attachment(conn, ART_MAIL, "file-1", "text/plain; charset=utf-8", PLAIN, "inline")
        _attachment(conn, ART_MAIL, "file-2", "text/html; charset=utf-8", HTML, "inline")
        _attachment(conn, ART_MAIL, "report.pdf", "application/pdf", PDF, "attachment")
        _attachment(conn, ART_ONE, "single.pdf", "application/pdf", PDF, "attachment")
        conn.execute(
            text(
                "INSERT INTO time_accounting (ticket_id, article_id, time_unit, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (51080, 51080, 1.5, :t, 51080, :t, 51080)"
            ),
            {"t": T0},
        )
        cfg = "---\nDefaultValue: ''\n"
        for fid, name, otype in ((DF_TICKET, "GiTag", "Ticket"), (DF_ART, "GiArtTag", "Article")):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :id, 'Text', :ot, :cfg, 1, :t, 1, :t, 1)"
                ),
                {
                    "id": fid,
                    "n": name,
                    "ot": otype,
                    "cfg": cfg if conn.dialect.name == "postgresql" else cfg.encode(),
                    "t": T0,
                },
            )
        for fid, oid, val in ((DF_TICKET, TICKET, "a"), (DF_ART, ART_MAIL, "art-val")):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                    " VALUES (:f, :o, :v)"
                ),
                {"f": fid, "o": oid, "v": val},
            )
        set_sysconfig_rows(
            conn,
            SYSCONFIG_BASE,
            {
                "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::OmittedFields": {
                    "SendTicket": "Articles->Body; QueueData->Comment ; Article->Attachment",
                },
                "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::Base64EncodedFields": {
                    "SendTicket": "Title; Articles->Subject",
                },
                # Znuny keeps one setting per preference group (``Name###Group``)
                "PreferencesGroups###RefreshTime": {
                    "PrefKey": "UserRefreshTime",
                    "DataSelected": "0",
                },
                "PreferencesGroups###Mail": {"PrefKey": "UserEmail", "DataSelected": "dflt@x.test"},
                "CustomerPreferencesGroups###ShowTickets": {
                    "PrefKey": "UserShowTickets",
                    "DataSelected": "25",
                },
            },
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _cleanup(conn)
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


async def _with_session(
    sync_url: str,
    fn: Callable[[AsyncSession, SysConfig], Coroutine[Any, Any, Any]],
    *,
    statements: list[str] | None = None,
) -> Any:
    engine = create_async_engine(_async_url(sync_url))
    if statements is not None:

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _capture(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
            statements.append(statement)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await fn(session, SysConfig(session))
    finally:
        await engine.dispose()


def _all_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value] + [k for v in value.values() for k in _all_keys(v)]
    if isinstance(value, list):
        return [k for v in value for k in _all_keys(v)]
    return []


def _invoker(**extra: Any) -> InvokerConfig:
    config = {"Type": "Ticket::Generic", "Events": [{"Event": "TicketCreate"}], **extra}
    parsed = parse_requester(requester_config(invokers={"SendTicket": config}), huge_xml=False)
    return parsed.invokers["SendTicket"]


async def test_deep_get_shape(sync_url: str) -> None:
    statements: list[str] = []

    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        return await ticket_deep_get(
            session, TICKET, article_id=None, all_attachments=False, sysconfig=sc
        )

    d = await _with_session(sync_url, run, statements=statements)
    assert d is not None
    assert d["TicketID"] == TICKET and d["DynamicField_GiTag"] == "a" and d["TimeUnit"] == 1.5
    assert d["Title"] == "GI DeepGet" and d["Queue"] == "GI51080Queue"
    assert len(d["Articles"]) == 2 and d["Article"] == {}
    assert all("TimeUnit" not in a for a in d["Articles"])  # operation-only key, not ArticleGet
    for part in (
        "CustomerUser",
        "CustomerCompany",
        "QueueData",
        "TypeData",
        "PriorityData",
        "ServiceData",
        "SLAData",
        "OwnerData",
        "ResponsibleData",
        "CreateByData",
    ):
        assert isinstance(d[part], dict) and d[part], part
    flat = json.dumps(d)
    assert "UserPw" not in flat and "Password" not in flat
    assert "customer-hash" not in flat and "secret-hash" not in flat and "TOPSECRET" not in flat
    assert "hidden" not in flat
    assert not [k for k in _all_keys(d) if "::" in k]
    # Znuny-shaped related objects
    assert d["QueueData"]["Name"] == "GI51080Queue" and d["QueueData"]["Calendar"] == ""
    assert d["QueueData"]["ParentQueueID"] == "7" and d["QueueData"]["Email"]
    assert (
        d["ServiceData"]["NameShort"] == "Child" and d["ServiceData"]["ParentID"] == SERVICE_PARENT
    )
    assert d["SLAData"]["ServiceIDs"] == [SERVICE] and d["SLAData"]["SolutionTime"] == 30
    assert d["TypeData"]["ID"] == 1 and d["PriorityData"]["ID"] == 3
    assert d["OwnerData"]["UserLogin"] == "gi51080.agent"
    assert d["OwnerData"]["UserFullname"] == "Gi Agent"
    assert d["OwnerData"]["UserEmail"] == "agent@example.org"
    assert d["OwnerData"]["UserLastLoginTimestamp"] == "2026-01-01 10:00:00"
    assert d["CreateByData"]["UserID"] == USER
    assert d["ResponsibleData"]["UserLogin"] == "root@localhost"
    # nothing reads a Tiqora table
    assert not [s for s in statements if "tiqora_" in s]


async def test_customer_user_merges_company_and_preferences(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        return await ticket_deep_get(
            session, TICKET, article_id=None, all_attachments=False, sysconfig=sc
        )

    d = await _with_session(sync_url, run)
    assert d is not None
    cu, cc = d["CustomerUser"], d["CustomerCompany"]
    assert cu["UserLogin"] == LOGIN and cu["UserID"] == LOGIN and cu["UserCustomerID"] == COMPANY
    assert cu["UserFullname"] == "Dr. Max Muster"
    assert cu["UserMailString"] == f'"Max Muster" <{LOGIN}>'
    assert cu["UserLanguage"] == "de"
    assert cu["CustomerCompanyName"] == "Example Corp" and cu["Source"] == "CustomerUser"
    assert cc["CustomerID"] == COMPANY and cc["CustomerCompanyStreet"] == "Main St 1"
    assert "UserPassword" not in cu


async def test_preference_group_defaults_are_applied(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        return await ticket_deep_get(
            session, TICKET, article_id=None, all_attachments=False, sysconfig=sc
        )

    d = await _with_session(sync_url, run)
    assert d is not None
    for part in ("OwnerData", "ResponsibleData"):
        assert d[part]["UserRefreshTime"] == "0"
    # a stored preference is never overwritten by DataSelected
    assert d["OwnerData"]["UserEmail"] == "agent@example.org"
    assert d["CustomerUser"]["UserShowTickets"] == "25"
    assert "UserRefreshTime" not in d["CustomerUser"]


async def test_ticket_without_related_objects_has_empty_parts(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        return await ticket_deep_get(
            session, TICKET_NONE, article_id=None, all_attachments=False, sysconfig=sc
        )

    d = await _with_session(sync_url, run)
    assert d is not None
    assert d["Articles"] == [] and d["Article"] == {}
    for part in ("CustomerUser", "CustomerCompany", "ServiceData", "SLAData"):
        assert d[part] == {}, part
    assert d["QueueData"] and d["OwnerData"]


async def test_queue_calendar_zero_is_empty_like_perl(sync_url: str) -> None:
    """Znuny QueueGet does `Calendar => $Data[20] || ''`: a stored "0" is falsy and becomes ''
    (found by the requester golden test, QueueData->Calendar '' vs '0')."""

    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        return await ticket_deep_get(
            session, TICKET, article_id=None, all_attachments=False, sysconfig=sc
        )

    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE queue SET calendar_name = '0' WHERE id = 51080"))
        d = await _with_session(sync_url, run)
        assert d is not None and d["QueueData"]["Calendar"] == ""
        with engine.begin() as conn:
            conn.execute(text("UPDATE queue SET calendar_name = '2' WHERE id = 51080"))
        d = await _with_session(sync_url, run)
        assert d is not None and d["QueueData"]["Calendar"] == "2"
    finally:
        with engine.begin() as conn:
            conn.execute(text("UPDATE queue SET calendar_name = NULL WHERE id = 51080"))
        engine.dispose()


async def test_unknown_ticket_is_none(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> Any:
        return await ticket_deep_get(
            session, 51099, article_id=None, all_attachments=False, sysconfig=sc
        )

    assert await _with_session(sync_url, run) is None


async def test_single_article_becomes_article_with_attachments(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        # a wrong article id is ignored when the ticket has exactly one article
        return await ticket_deep_get(
            session, TICKET_ONE, article_id=999, all_attachments=False, sysconfig=sc
        )

    d = await _with_session(sync_url, run)
    assert d is not None and len(d["Articles"]) == 1
    article = d["Article"]
    assert article["ArticleID"] == ART_ONE and article["Body"] == "only one"
    (attachment,) = article["Attachment"]
    assert attachment["Filename"] == "single.pdf" and attachment["FilesizeRaw"] == len(PDF)
    assert base64.b64decode(attachment["Content"]) == PDF and "\n" not in attachment["Content"]
    assert "Attachment" not in d["Articles"][0]  # all_attachments is off


async def test_article_id_selects_article(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> list[dict[str, Any] | None]:
        return [
            await ticket_deep_get(
                session, TICKET, article_id=aid, all_attachments=False, sysconfig=sc
            )
            for aid in (ART_NOTE, ART_MAIL, 999)
        ]

    note, mail, unknown = await _with_session(sync_url, run)
    assert note["Article"]["ArticleID"] == ART_NOTE and "Attachment" not in note["Article"]
    assert note["Article"]["DynamicField_GiArtTag"] is None
    assert mail["Article"]["ArticleID"] == ART_MAIL
    assert mail["Article"]["DynamicField_GiArtTag"] == "art-val"
    assert sorted(a["Filename"] for a in mail["Article"]["Attachment"]) == [
        "file-1",
        "file-2",
        "report.pdf",
    ]
    assert unknown["Article"] == {}
    # RealNames => 1
    assert mail["Article"]["FromRealname"] == "Max Muster"
    assert mail["Article"]["ToRealname"] == "support@example.org"
    assert mail["Article"]["CcRealname"] == "Cc"
    assert "SubjectRealname" not in mail["Article"]


async def test_all_attachments_base64_including_plain_and_html(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any] | None:
        return await ticket_deep_get(
            session, TICKET, article_id=None, all_attachments=True, sysconfig=sc
        )

    d = await _with_session(sync_url, run)
    assert d is not None
    mail = next(a for a in d["Articles"] if a["ArticleID"] == ART_MAIL)
    note = next(a for a in d["Articles"] if a["ArticleID"] == ART_NOTE)
    by_name = {a["Filename"]: a for a in mail["Attachment"]}
    assert set(by_name) == {"file-1", "file-2", "report.pdf"}
    assert base64.b64decode(by_name["file-1"]["Content"]) == PLAIN
    assert base64.b64decode(by_name["file-2"]["Content"]) == HTML
    assert base64.b64decode(by_name["report.pdf"]["Content"]) == PDF
    assert [a["FileID"] for a in mail["Attachment"]] == sorted(
        (a["FileID"] for a in mail["Attachment"]), key=str
    )
    assert "Attachment" not in note


async def test_prepare_request_omitted_and_base64_fields(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> tuple[dict[str, Any], dict[str, Any]]:
        data = {"TicketID": TICKET, "ArticleID": ART_MAIL, "Event": "TicketCreate"}
        listed = await prepare_request(
            session, invoker_name="SendTicket", invoker=_invoker(), data=data, sysconfig=sc
        )
        other = await prepare_request(
            session, invoker_name="OtherInvoker", invoker=_invoker(), data=data, sysconfig=sc
        )
        assert data == {"TicketID": TICKET, "ArticleID": ART_MAIL, "Event": "TicketCreate"}
        return listed, other

    listed, other = await _with_session(sync_url, run)
    ticket = listed["Ticket"]
    # OmittedFields "Articles->Body; QueueData->Comment ; Article->Attachment"
    assert all("Body" not in a for a in ticket["Articles"])
    assert "Comment" not in ticket["QueueData"] and "Name" in ticket["QueueData"]
    assert ticket["Article"] and "Attachment" not in ticket["Article"]
    # Base64EncodedFields "Title; Articles->Subject" (MIME base64 with a trailing newline)
    assert ticket["Title"] == base64.encodebytes(b"GI DeepGet").decode()
    assert [a["Subject"] for a in ticket["Articles"]] == [
        base64.encodebytes(f"Subject {i}".encode()).decode() for i in (ART_MAIL, ART_NOTE)
    ]
    assert listed["Event"] == {"TicketID": TICKET, "ArticleID": ART_MAIL, "Event": "TicketCreate"}
    # other invoker names are untouched
    assert other["Ticket"]["Title"] == "GI DeepGet"
    assert all("Body" in a for a in other["Ticket"]["Articles"])
    assert "Comment" in other["Ticket"]["QueueData"]


async def test_prepare_request_applies_the_lists_to_the_event_too(sync_url: str) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> dict[str, Any]:
        data = {"TicketID": TICKET, "Title": "event title", "Articles": [{"Body": "x", "K": 1}]}
        return await prepare_request(
            session, invoker_name="SendTicket", invoker=_invoker(), data=data, sysconfig=sc
        )

    result = await _with_session(sync_url, run)
    assert result["Event"]["Title"] == base64.encodebytes(b"event title").decode()
    assert result["Event"]["Articles"] == [{"K": 1}]


async def test_prepare_request_without_ticket_id_and_with_invoker_attachments(
    sync_url: str,
) -> None:
    async def run(session: AsyncSession, sc: SysConfig) -> tuple[dict[str, Any], dict[str, Any]]:
        empty = await prepare_request(
            session,
            invoker_name="NoList",
            invoker=_invoker(),
            data={"Event": "X"},
            sysconfig=sc,
        )
        full = await prepare_request(
            session,
            invoker_name="NoList",
            invoker=_invoker(GetAllArticleAttachments="1"),
            data={"TicketID": TICKET},
            sysconfig=sc,
        )
        return empty, full

    empty, full = await _with_session(sync_url, run)
    assert empty == {"Ticket": {}, "Event": {"Event": "X"}}
    mail = next(a for a in full["Ticket"]["Articles"] if a["ArticleID"] == ART_MAIL)
    assert len(mail["Attachment"]) == 3


async def test_prepare_request_until_time_parts(sync_url: str) -> None:
    until = int(datetime.now(UTC).timestamp()) + 7200
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE ticket SET until_time = :u WHERE id = 51082"), {"u": until})
    engine.dispose()

    async def run(session: AsyncSession, sc: SysConfig) -> tuple[dict[str, Any], dict[str, Any]]:
        with_until = await prepare_request(
            session,
            invoker_name="NoList",
            invoker=_invoker(),
            data={"TicketID": TICKET_ONE},
            sysconfig=sc,
        )
        without = await prepare_request(
            session,
            invoker_name="NoList",
            invoker=_invoker(),
            data={"TicketID": TICKET},
            sysconfig=sc,
        )
        return with_until, without

    try:
        with_until, without = await _with_session(sync_url, run)
    finally:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            conn.execute(text("UPDATE ticket SET until_time = 0 WHERE id = 51082"))
        engine.dispose()
    ticket = with_until["Ticket"]
    assert 7100 < ticket["UntilTime"] <= 7200
    parts = ticket["UntilTimeDateTimeParts"]
    assert parts["TimeZone"] == "UTC"
    assert datetime(
        parts["Year"],
        parts["Month"],
        parts["Day"],
        parts["Hour"],
        parts["Minute"],
        parts["Second"],
        tzinfo=UTC,
    ).timestamp() == pytest.approx(until, abs=5)
    assert "UntilTimeDateTimeParts" not in without["Ticket"]
