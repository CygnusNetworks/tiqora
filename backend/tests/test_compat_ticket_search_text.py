"""TicketSearch article text filters (MIMEBase_*, Fulltext, ContentSearch) on both dialects.

Seed ids live in the band 7690-7699; every row is deleted again after the module.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.compat.operations import op_ticket_search
from tiqora.api.compat.search_filters import article_text_filters, content_like_pattern
from tiqora.domain.auth import SessionStore

SESSION = "GI7690AGENT"
ATTACH_CFG = "Ticket::Article::Backend::MIMEBase::IndexAttachmentNames"
T_INV, T_OFFER, T_OTHER = 7690, 7691, 7692
_SEEDED: list[str] = []
_SEQ = itertools.count(7690)

_CLEANUP = (
    "DELETE FROM article_data_mime_attachment WHERE article_id BETWEEN 7690 AND 7699",
    "DELETE FROM article_data_mime WHERE article_id BETWEEN 7690 AND 7699",
    "DELETE FROM article WHERE id BETWEEN 7690 AND 7699",
    "DELETE FROM ticket WHERE id BETWEEN 7690 AND 7699",
    "DELETE FROM sessions WHERE session_id LIKE 'GI7690%'",
    "DELETE FROM queue WHERE id = 7690",
    "DELETE FROM group_user WHERE group_id = 7690",
    "DELETE FROM permission_groups WHERE id = 7690",
    "DELETE FROM users WHERE id = 7690",
    f"DELETE FROM sysconfig_modified WHERE name = '{ATTACH_CFG}'",
    f"DELETE FROM sysconfig_default WHERE name = '{ATTACH_CFG}'",
)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _ticket(conn: Any, tid: int, title: str, now: datetime) -> None:
    conn.execute(
        text(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id,"
            " customer_user_id, timeout, until_time, escalation_time, escalation_update_time,"
            " escalation_response_time, escalation_solution_time, archive_flag, create_time,"
            " create_by, change_time, change_by) VALUES (:id, :tn, :title, 7690, 1, 1, 7690,"
            " 1, 3, (SELECT id FROM ticket_state WHERE name = 'new'), 'GI7690', 'gi7690.cust',"
            " 0, 0, 0, 0, 0, 0, 0, :t, 7690, :t, 7690)"
        ),
        {"id": tid, "tn": f"{tid}001", "title": title, "t": now},
    )


def _article(
    conn: Any, aid: int, tid: int, now: datetime, *, frm: str, bcc: str, subject: str, body: str
) -> None:
    conn.execute(
        text(
            "INSERT INTO article (id, ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :tid, 3, 1, 1, :t, 7690, :t, 7690)"
        ),
        {"id": aid, "tid": tid, "t": now},
    )
    conn.execute(
        text(
            "INSERT INTO article_data_mime (article_id, a_from, a_to, a_bcc, a_subject,"
            " a_message_id, a_content_type, a_body, incoming_time, create_time, create_by,"
            " change_time, change_by) VALUES (:id, :f, 'support@example.org', :bcc, :s, :mid,"
            " 'text/plain', :b, 1, :t, 7690, :t, 7690)"
        ),
        {
            "id": aid,
            "f": frm,
            "bcc": bcc,
            "s": subject,
            "mid": f"<{aid}@example.org>",
            "b": body,
            "t": now,
        },
    )


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = datetime(2024, 6, 1, 12, 0, 0)
    stamp = str(int(time.time()))
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        for stmt in (
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            " VALUES (7690, 'gi7690.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7690, 'gi7690-group', 1, :t, 1, :t, 1)",
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (7690, 7690, 'ro', :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (7690, 'Q7690', 7690, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
        ):
            conn.execute(text(stmt), {"t": now})
        for key, val in {
            "UserID": "7690",
            "UserLogin": "gi7690.agent",
            "UserType": "User",
            "UserLastRequest": stamp,
            "UserSessionStart": stamp,
        }.items():
            conn.execute(
                text(
                    "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                    " VALUES (:sid, :k, :v, 0)"
                ),
                {"sid": SESSION, "k": key, "v": val},
            )
        _ticket(conn, T_INV, "Zahlung", now)
        _ticket(conn, T_OFFER, "Projekt", now)
        _ticket(conn, T_OTHER, "Rech*nung", now)
        _article(
            conn,
            7690,
            T_INV,
            now,
            frm="Max <max@example.org>",
            bcc="audit@example.org",
            subject="Ihre Rechnung März",
            body="Bitte zahlen",
        )
        _article(
            conn,
            7691,
            T_OFFER,
            now,
            frm="Erika <erika@example.org>",
            bcc="",
            subject="Angebot",
            body="Anbei der Vertrag",
        )
        _article(
            conn,
            7692,
            T_OTHER,
            now,
            frm="Hans <hans@example.org>",
            bcc="",
            subject="Sonstiges",
            body="nichts",
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime_attachment (article_id, filename, content_size,"
                " content_type, content, disposition, create_time, create_by, change_time,"
                " change_by) VALUES (7691, 'Vertrag-2024.pdf', '3', 'application/pdf', :c,"
                " 'attachment', :t, 7690, :t, 7690)"
            ),
            {"c": b"abc", "t": now},
        )
    engine.dispose()


async def _set_sysconfig(sync_url: str, value: object) -> None:
    effective = yaml.safe_dump(value, default_flow_style=False)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM sysconfig_modified WHERE name = :n"), {"n": ATTACH_CFG})
        conn.execute(text("DELETE FROM sysconfig_default WHERE name = :n"), {"n": ATTACH_CFG})
        conn.execute(
            text(
                "INSERT INTO sysconfig_default (id, name, description, navigation, is_invisible,"
                " is_readonly, is_required, is_valid, has_configlevel,"
                " user_modification_possible, user_modification_active, xml_content_raw,"
                " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, :n, 'Core', 0, 0, 0, 1, 0, 0, 0, :n, :n, 'Ticket.xml', :eff,"
                " 0, '0', current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": 90_700 + next(_SEQ), "n": ATTACH_CFG, "eff": effective},
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


async def _search(sync_url: str, params: dict[str, Any]) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            return await op_ticket_search(
                {"SessionID": SESSION, "QueueIDs": [7690], **params}, session, store
            )
    finally:
        await engine.dispose()


async def _ids(sync_url: str, params: dict[str, Any]) -> list[int]:
    result = await _search(sync_url, params)
    assert "Error" not in result, result
    return sorted(int(i) for i in result.get("TicketID", []))


pytestmark = pytest.mark.db


def test_content_like_pattern_rules() -> None:
    assert content_like_pattern("Rechnung") == "%rechnung%"
    assert content_like_pattern("Rech*") == "%rech%"
    assert content_like_pattern("*a*b*") == "%a%b%"
    for skipped in ("", "   ", "*", "***", "*****", "%", None, ["x"]):
        assert content_like_pattern(skipped) is None


async def test_subject_is_substring_without_wildcard(sync_url: str) -> None:
    assert await _ids(sync_url, {"MIMEBase_Subject": "Rechnung"}) == [T_INV]
    assert await _ids(sync_url, {"MIMEBase_Subject": "RECHNUNG"}) == [T_INV]


async def test_explicit_wildcard_is_honoured(sync_url: str) -> None:
    assert await _ids(sync_url, {"MIMEBase_Subject": "Rech*"}) == [T_INV]
    assert await _ids(sync_url, {"MIMEBase_Subject": "Ihre*März"}) == [T_INV]
    assert await _ids(sync_url, {"MIMEBase_Subject": "Rech*zzz"}) == []


async def test_wildcard_only_values_are_skipped(sync_url: str) -> None:
    everything = [T_INV, T_OFFER, T_OTHER]
    assert await _ids(sync_url, {"MIMEBase_Subject": "*"}) == everything
    assert await _ids(sync_url, {"MIMEBase_Subject": "*********"}) == everything


async def test_bcc_filter(sync_url: str) -> None:
    assert await _ids(sync_url, {"MIMEBase_Bcc": "audit@"}) == [T_INV]
    assert await _ids(sync_url, {"MIMEBase_Bcc": "nobody@"}) == []


async def test_and_is_default_or_widens(sync_url: str) -> None:
    both = {"MIMEBase_From": "erika", "MIMEBase_Subject": "Rechnung"}
    assert await _ids(sync_url, both) == []
    assert await _ids(sync_url, {**both, "ContentSearch": "OR"}) == [T_INV, T_OFFER]
    assert await _ids(sync_url, {**both, "ContentSearch": "or"}) == [T_INV, T_OFFER]
    assert await _ids(sync_url, {**both, "ContentSearch": "AND"}) == []


async def test_attachment_name_ignored_unless_indexed(sync_url: str) -> None:
    await _set_sysconfig(sync_url, 0)
    # filter dropped -> no restriction at all
    assert await _ids(sync_url, {"MIMEBase_AttachmentName": "Vertrag"}) == [
        T_INV,
        T_OFFER,
        T_OTHER,
    ]
    await _set_sysconfig(sync_url, 1)
    assert await _ids(sync_url, {"MIMEBase_AttachmentName": "vertrag"}) == [T_OFFER]
    assert await _ids(sync_url, {"MIMEBase_AttachmentName": "*.pdf"}) == [T_OFFER]
    assert await _ids(sync_url, {"MIMEBase_AttachmentName": "nope"}) == []
    # OR with an article field must not lose articles that have no attachment
    mixed = {"MIMEBase_AttachmentName": "vertrag", "MIMEBase_Subject": "Rechnung"}
    assert await _ids(sync_url, {**mixed, "ContentSearch": "OR"}) == [T_INV, T_OFFER]


async def test_fulltext_searches_fields_and_title(sync_url: str) -> None:
    assert await _ids(sync_url, {"Fulltext": "audit@"}) == [T_INV]
    assert await _ids(sync_url, {"Fulltext": "Zahlung"}) == [T_INV]  # title
    assert await _ids(sync_url, {"Fulltext": "erika"}) == [T_OFFER]
    assert await _ids(sync_url, {"Fulltext": "Vertrag"}) == [T_OFFER]  # body


async def test_title_stays_exact_with_wildcards(sync_url: str) -> None:
    assert await _ids(sync_url, {"Title": "Zahl"}) == []
    assert await _ids(sync_url, {"Title": "Zahlung"}) == [T_INV]
    assert await _ids(sync_url, {"Title": "Zahl*"}) == [T_INV]


async def test_non_integer_in_id_filter_empties_whole_search(sync_url: str) -> None:
    assert await _search(sync_url, {"TicketID": [T_INV, "abc"]}) == {}
    # Znuny Quote(..., 'Integer') allows 1-16 digits: a 17-digit element empties the search
    assert await _search(sync_url, {"TicketID": [T_INV, 10**16]}) == {}
    assert await _ids(sync_url, {"TicketID": [T_INV, 10**15]}) == [T_INV]


async def test_underscore_is_a_literal_not_a_wildcard(sync_url: str) -> None:
    # Znuny quotes the word with Quote(..., 'Like'): '_' -> '\_'
    assert await _ids(sync_url, {"MIMEBase_Subject": "Ihre_Rechnung"}) == []
    assert await _ids(sync_url, {"MIMEBase_Subject": "Ihre Rechnung"}) == [T_INV]
    assert await _ids(sync_url, {"Fulltext": "Ihre_Rechnung"}) == []


async def test_wildcard_only_fulltext_still_forces_or(sync_url: str) -> None:
    # GI: `if ($GetParam{Fulltext}) { ContentSearch = 'OR' }` - also for '*'
    both = {"MIMEBase_From": "erika", "MIMEBase_Subject": "Rechnung"}
    assert await _ids(sync_url, both) == []
    assert await _ids(sync_url, {**both, "Fulltext": "*"}) == [T_INV, T_OFFER]
    assert await _ids(sync_url, {**both, "Fulltext": "0"}) == []


async def test_fulltext_with_mimebase_is_or_and_finds_attachment_names(sync_url: str) -> None:
    await _set_sysconfig(sync_url, 1)
    # Fulltext forces OR: the subject match and the fulltext match both count
    assert await _ids(sync_url, {"Fulltext": "Vertrag-2024", "MIMEBase_Subject": "Rechnung"}) == [
        T_INV,
        T_OFFER,
    ]
    # the attachment name is only searched while IndexAttachmentNames is on
    assert await _ids(sync_url, {"Fulltext": "Vertrag-2024"}) == [T_OFFER]
    await _set_sysconfig(sync_url, 0)
    assert await _ids(sync_url, {"Fulltext": "Vertrag-2024"}) == []


async def test_article_text_filters_appends_condition_only_when_needed() -> None:
    class _Cfg:
        async def get(self, name: str, default: Any = None) -> Any:
            return 1

    conditions: list[str] = []
    params: dict[str, Any] = {}
    joins: list[str] = []
    await article_text_filters(
        None,  # type: ignore[arg-type]
        _Cfg(),  # type: ignore[arg-type]
        {"MIMEBase_Subject": "*"},
        conditions,
        params,
        lambda sql, key: joins.append(key),
    )
    assert (conditions, params, joins) == ([], {}, [])
    await article_text_filters(
        None,  # type: ignore[arg-type]
        _Cfg(),  # type: ignore[arg-type]
        {"MIMEBase_Subject": "x", "MIMEBase_Bcc": "y"},
        conditions,
        params,
        lambda sql, key: joins.append(key),
    )
    assert joins == ["mime"]
    assert len(conditions) == 1 and " AND " in conditions[0]
    assert sorted(params.values()) == ["%x%", "%y%"]
