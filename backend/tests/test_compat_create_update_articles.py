"""TicketCreate / TicketUpdate (GenericInterface compat): article lists, TimeUnit, history and
notification flags, dynamic fields, ``ArticleID`` / ``ArticleIDs``.

DB tests on MariaDB and PostgreSQL. Seed ids live in the GI band 7630-7649
(user 7630/7632, group 7630, queue 7631, dynamic fields 7630-7633, sysconfig 7640-7641);
every row (including the tickets the operations create in queue 7631) is deleted afterwards.
"""

from __future__ import annotations

import json
import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tiqora.api.compat.operations import op_ticket_create, op_ticket_update
from tiqora.domain.auth import SessionStore
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2024, 6, 1, 12, 0, 0)
AGENT, INVALID_AGENT, GROUP, QUEUE = 7630, 7632, 7630, 7631
DF_TICKET, DF_ART, DF_DROP, DF_MULTI, DF_BARE = 7630, 7631, 7632, 7633, 7634
AGENT_SESSION, CUST_SESSION = "GI7630AGENTSESSION", "GI7630CUSTSESSION"
CUST_LOGIN = "gi7630.cust"
TIME_REQUIRED = "Ticket::Frontend::NeedAccountedTime"
TIME_ACCOUNT = "Ticket::Frontend::AccountTime"
CREATE_TITLE = "GI7630 create"

_SEEDED: list[str] = []
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
        "DELETE FROM dynamic_field_value WHERE field_id BETWEEN 7630 AND 7639",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article_data_mime WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM ticket WHERE queue_id = {QUEUE}",
        "DELETE FROM dynamic_field WHERE id BETWEEN 7630 AND 7639",
        "DELETE FROM sessions WHERE session_id LIKE 'GI7630%'",
        f"DELETE FROM customer_user WHERE login = '{CUST_LOGIN}'",
        f"DELETE FROM queue WHERE id = {QUEUE}",
        f"DELETE FROM group_user WHERE group_id = {GROUP}",
        f"DELETE FROM permission_groups WHERE id = {GROUP}",
        f"DELETE FROM users WHERE id IN ({AGENT}, {INVALID_AGENT})",
        f"DELETE FROM sysconfig_modified WHERE name IN ('{TIME_REQUIRED}', '{TIME_ACCOUNT}')",
        f"DELETE FROM sysconfig_default WHERE name IN ('{TIME_REQUIRED}', '{TIME_ACCOUNT}')",
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
        delete_rows_above(sync_url, _SNAPSHOTS[sync_url])
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cfg(conn: Any, yaml_text: str) -> bytes | str:
    return yaml_text if conn.dialect.name == "postgresql" else yaml_text.encode()


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = int(time.time())
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    if sync_url not in _SNAPSHOTS:  # once per module: the fixture re-seeds for every test
        _SNAPSHOTS[sync_url] = snapshot_max_ids(sync_url, _COUNTER_TABLES)
    with engine.begin() as conn:
        for stmt in _cleanup_statements():
            conn.execute(text(stmt))
        for stmt in (
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            f" VALUES ({AGENT}, 'gi7630.agent', 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)",
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by)"
            f" VALUES ({INVALID_AGENT}, 'gi7630.gone', 'x', 'Gi', 'Gone', 2, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({GROUP}, 'gi7630-group', 1, :t, 1, :t, 1)",
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            f" change_time, change_by) VALUES ({AGENT}, {GROUP}, 'rw', :t, 1, :t, 1)",
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            f" change_time, change_by) VALUES ({QUEUE}, 'GI7630Queue', {GROUP}, 1, 1, 1, 1, 0,"
            " 1, :t, 1, :t, 1)",
            "INSERT INTO customer_user (login, email, customer_id, pw, first_name, last_name,"
            f" valid_id, create_time, create_by, change_time, change_by) VALUES ('{CUST_LOGIN}',"
            " 'gi7630@example.org', 'GI7630', 'x', 'Gi', 'Cust', 1, :t, 1, :t, 1)",
        ):
            conn.execute(text(stmt), {"t": T0})

        for sid, extra in (
            (
                AGENT_SESSION,
                {"UserID": str(AGENT), "UserLogin": "gi7630.agent", "UserType": "User"},
            ),
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

        possible_ab = "---\nPossibleValues:\n  a: A\n  b: B\n"
        possible_num = "---\nPossibleValues:\n  1: One\n  2: Two\n"
        for fid, name, ftype, otype, cfg in (
            (DF_TICKET, "Gi7630Ticket", "Text", "Ticket", "---\nDefaultValue: ''\n"),
            (DF_ART, "Gi7630Art", "Multiselect", "Article", possible_ab),
            (DF_DROP, "Gi7630Drop", "Dropdown", "Ticket", possible_num),
            (DF_MULTI, "Gi7630Multi", "Multiselect", "Ticket", possible_ab),
            (DF_BARE, "Gi7630Bare", "Dropdown", "Ticket", "---\nDefaultValue: ''\n"),
        ):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :id, :ft, :ot, :cfg, 1, :t, 1, :t, 1)"
                ),
                {
                    "id": fid,
                    "n": name,
                    "ft": ftype,
                    "ot": otype,
                    "cfg": _cfg(conn, cfg),
                    "t": T0,
                },
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


def _query(sync_url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return list(result.fetchall()) if result.returns_rows else []
    finally:
        engine.dispose()


def _set_sysconfig(sync_url: str, name: str, value: object, row_id: int) -> None:
    """One system-wide sysconfig_default row (YAML effective value)."""
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM sysconfig_modified WHERE name = :n"), {"n": name})
            conn.execute(text("DELETE FROM sysconfig_default WHERE name = :n"), {"n": name})
            conn.execute(
                text(
                    "INSERT INTO sysconfig_default (id, name, description, navigation,"
                    " is_invisible, is_readonly, is_required, is_valid, has_configlevel,"
                    " user_modification_possible, user_modification_active, xml_content_raw,"
                    " xml_content_parsed, xml_filename, effective_value, is_dirty,"
                    " exclusive_lock_guid, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :n, 'Core', 0, 0, 0, 1, 0, 0, 0, :n, :n, 'Ticket.xml',"
                    " :eff, 0, '0', current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": row_id, "n": name, "eff": yaml.safe_dump(value)},
            )
    finally:
        engine.dispose()


def _clear_sysconfig(sync_url: str) -> None:
    for stmt in _cleanup_statements()[-2:]:
        _query(sync_url, stmt)


async def _call(
    sync_url: str,
    operation: str,
    params: dict[str, Any],
    *,
    customer: bool = False,
    db_sysconfig: bool = False,
) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    func = op_ticket_create if operation == "create" else op_ticket_update
    payload = {"SessionID": CUST_SESSION if customer else AGENT_SESSION, **params}
    try:
        async with factory() as session:
            # SysConfig reads the DB after the test has written its rows (its cache is per instance)
            if db_sysconfig:
                sysconfig = SysConfig(session)
            else:

                async def _no_row(name: str) -> None:
                    return None

                sysconfig = SysConfig(fetch=_no_row)
            return await func(payload, session, factory, store, sysconfig)  # type: ignore[operator, no-any-return]
    finally:
        await engine.dispose()


def _ticket(title: str = CREATE_TITLE, **extra: Any) -> dict[str, Any]:
    return {
        "Title": title,
        "QueueID": QUEUE,
        "StateID": 1,
        "PriorityID": 3,
        **extra,
    }


def _article(subject: str, **extra: Any) -> dict[str, Any]:
    return {"Subject": subject, "Body": f"{subject} body", **extra}


def _ticket_count(sync_url: str, title: str) -> int:
    return int(_query(sync_url, "SELECT COUNT(*) FROM ticket WHERE title = :t", t=title)[0][0])


def _article_ids(sync_url: str, ticket_id: int) -> list[int]:
    rows = _query(sync_url, "SELECT id FROM article WHERE ticket_id = :t ORDER BY id", t=ticket_id)
    return [int(r[0]) for r in rows]


def _history(sync_url: str, article_id: int) -> list[tuple[str, str]]:
    rows = _query(
        sync_url,
        "SELECT t.name, h.name FROM ticket_history h"
        " JOIN ticket_history_type t ON t.id = h.history_type_id"
        " WHERE h.article_id = :a ORDER BY h.id",
        a=article_id,
    )
    return [(str(r[0]), str(r[1])) for r in rows]


def _outbox_events(sync_url: str, ticket_id: int) -> list[str]:
    rows = _query(
        sync_url,
        "SELECT event_type FROM tiqora_event_outbox WHERE ticket_id = :t ORDER BY id",
        t=ticket_id,
    )
    return [str(r[0]) for r in rows]


def _df_values(sync_url: str, field_id: int, object_id: int) -> list[str]:
    rows = _query(
        sync_url,
        "SELECT value_text FROM dynamic_field_value WHERE field_id = :f AND object_id = :o"
        " ORDER BY value_text",
        f=field_id,
        o=object_id,
    )
    return [str(r[0]) for r in rows]


async def _new_ticket(sync_url: str, title: str, **extra: Any) -> tuple[int, int]:
    """A ticket with one article; returns (ticket id, article id)."""
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket(title, **extra), "Article": _article("first")},
    )
    assert "Error" not in result, result
    ticket_id = int(result["TicketID"])
    return ticket_id, int(result["ArticleID"])


# ---------------------------------------------------------------------------
# TicketCreate
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_create_with_two_articles(sync_url: str) -> None:
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 two articles"),
            "Article": [_article("a", TimeUnit="1,5"), _article("b")],
        },
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]
    ids = _article_ids(sync_url, ticket_id)
    assert len(ids) == 2
    assert result["ArticleID"] == ids  # a list for several articles, ascending
    units = _query(
        sync_url,
        "SELECT article_id, time_unit FROM time_accounting WHERE ticket_id = :t",
        t=ticket_id,
    )
    assert [(int(r[0]), float(r[1])) for r in units] == [(ids[0], 1.5)]
    assert ("TimeAccounting", "%%1.5%%1.5") in _history(sync_url, ids[0])
    assert all(h[0] != "TimeAccounting" for h in _history(sync_url, ids[1]))


@pytest.mark.db
async def test_create_single_article_answers_a_bare_article_id(sync_url: str) -> None:
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket("GI7630 one article"), "Article": _article("only")},
    )
    ids = _article_ids(sync_url, result["TicketID"])
    assert len(ids) == 1
    assert result["ArticleID"] == ids[0]
    assert isinstance(result["ArticleID"], int)


@pytest.mark.db
async def test_create_defaults_from_sysconfig(sync_url: str) -> None:
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket("GI7630 defaults"), "Article": _article("defaults")},
    )
    article_id = result["ArticleID"]
    assert _history(sync_url, article_id) == [("NewTicket", "%%GenericInterface Create")]
    visible = _query(
        sync_url, "SELECT is_visible_for_customer FROM article WHERE id = :a", a=article_id
    )
    assert int(visible[0][0]) == 1


@pytest.mark.db
async def test_create_explicit_history_and_visibility_win(sync_url: str) -> None:
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 explicit"),
            "Article": _article(
                "x", HistoryType="AddNote", HistoryComment="%%mine", IsVisibleForCustomer=0
            ),
        },
    )
    article_id = result["ArticleID"]
    assert _history(sync_url, article_id) == [("AddNote", "%%mine")]
    visible = _query(
        sync_url, "SELECT is_visible_for_customer FROM article WHERE id = :a", a=article_id
    )
    assert int(visible[0][0]) == 0


@pytest.mark.db
async def test_article_and_ticket_dynamic_fields_split(sync_url: str) -> None:
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 df split"),
            "Article": [_article("a"), _article("b")],
            "DynamicField": [
                {"Name": "Gi7630Ticket", "Value": "x"},
                {"Name": "Gi7630Art", "Value": ["a", "b"]},
                {"Name": "Gi7630Drop", "Value": "1"},
                {"Name": "Gi7630Multi", "Value": ["a", "b"]},
            ],
        },
    )
    assert "Error" not in result, result
    ticket_id = result["TicketID"]
    first, second = result["ArticleID"]
    assert _df_values(sync_url, DF_TICKET, ticket_id) == ["x"]
    assert _df_values(sync_url, DF_DROP, ticket_id) == ["1"]
    # a list value is stored as several rows, never as the text "['a', 'b']"
    assert _df_values(sync_url, DF_MULTI, ticket_id) == ["a", "b"]
    assert _df_values(sync_url, DF_ART, first) == ["a", "b"]
    assert _df_values(sync_url, DF_ART, second) == ["a", "b"]
    assert _df_values(sync_url, DF_ART, ticket_id) == []  # not misfiled on the ticket


@pytest.mark.db
async def test_unknown_or_invalid_dynamic_field_is_rejected_before_any_write(
    sync_url: str,
) -> None:
    cases = [
        (
            [{"Name": "NoSuchField", "Value": "x"}],
            "TicketCreate.InvalidParameter",
            "TicketCreate: DynamicField->Name parameter is invalid!",
        ),
        (
            [{"Name": "Gi7630Drop", "Value": "9"}],
            "TicketCreate.InvalidParameter",
            "TicketCreate: DynamicField->Value parameter is invalid!",
        ),
        (
            [{"Name": "Gi7630Multi", "Value": ["a", "zzz"]}],
            "TicketCreate.InvalidParameter",
            "TicketCreate: DynamicField->Value parameter is invalid!",
        ),
        (
            [{"Name": "Gi7630Ticket"}],
            "TicketCreate.MissingParameter",
            "TicketCreate: DynamicField->Value  parameter is missing!",
        ),
        (
            [{"Value": "x"}],
            "TicketCreate.MissingParameter",
            "TicketCreate: DynamicField->Name  parameter is missing!",
        ),
        (
            ["junk"],
            "TicketCreate.InvalidParameter",
            "TicketCreate: Ticket->DynamicField parameter is invalid!",
        ),
    ]
    for items, code, message in cases:
        title = f"GI7630 reject {message}"
        result = await _call(
            sync_url,
            "create",
            {"Ticket": _ticket(title), "Article": _article("x"), "DynamicField": items},
        )
        assert result == {"Error": {"ErrorCode": code, "ErrorMessage": message}}
        assert _ticket_count(sync_url, title) == 0


@pytest.mark.db
async def test_invalid_history_type_and_time_unit_errors(sync_url: str) -> None:
    cases = [
        (
            _article("x", HistoryType="Nope"),
            "TicketCreate.InvalidParameter",
            "TicketCreate: Article->HistoryType parameter is invalid!",
        ),
        (
            _article("x", TimeUnit="abc"),
            "TicketCreate.InvalidParameter",
            "TicketCreate: Article->TimeUnit parameter is invalid!",
        ),
        (
            _article("x", TimeUnit="-1"),
            "TicketCreate.InvalidParameter",
            "TicketCreate: Article->TimeUnit parameter is invalid!",
        ),
        (
            _article("x", AutoResponseType="no such type"),
            "TicketCreate.InvalidParameter",
            "TicketCreate: Article->AutoResponseType parameter is invalid!",
        ),
        (
            _article("x", NoAgentNotify="2"),
            "TicketCreate.InvalidParameter",
            "TicketCreate: Article->NoAgent parameter is invalid!",
        ),
        (
            _article("x", ArticleSend=1),
            "TicketCreate.InvalidParameter",
            "TicketCreate: Article->To parameter must be a valid email address"
            " when Article->ArticleSend is set!",
        ),
    ]
    for article, code, message in cases:
        title = f"GI7630 invalid {message}"
        result = await _call(sync_url, "create", {"Ticket": _ticket(title), "Article": article})
        assert result == {"Error": {"ErrorCode": code, "ErrorMessage": message}}
        assert _ticket_count(sync_url, title) == 0
    # the second of two articles is checked as well, and nothing of the first is written
    title = "GI7630 second article invalid"
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket(title),
            "Article": [_article("ok"), _article("bad", HistoryType="Nope")],
        },
    )
    assert result["Error"]["ErrorMessage"] == (
        "TicketCreate: Article->HistoryType parameter is invalid!"
    )
    assert _ticket_count(sync_url, title) == 0


@pytest.mark.db
async def test_time_unit_required_by_sysconfig(sync_url: str) -> None:
    _set_sysconfig(sync_url, TIME_ACCOUNT, 1, 7640)
    _set_sysconfig(sync_url, TIME_REQUIRED, 1, 7641)
    try:
        title = "GI7630 time required"
        missing = await _call(
            sync_url,
            "create",
            {"Ticket": _ticket(title), "Article": _article("no unit")},
            db_sysconfig=True,
        )
        assert missing == {
            "Error": {
                "ErrorCode": "TicketCreate.MissingParameter",
                "ErrorMessage": "TicketCreate: Article->TimeUnit is required by sysconfig option!",
            }
        }
        assert _ticket_count(sync_url, title) == 0

        # "0" is a string with data: no error, but a Perl-false unit books nothing
        zero = await _call(
            sync_url,
            "create",
            {"Ticket": _ticket(title), "Article": _article("zero", TimeUnit="0")},
            db_sysconfig=True,
        )
        assert "Error" not in zero, zero
        assert not _query(
            sync_url, "SELECT 1 FROM time_accounting WHERE ticket_id = :t", t=zero["TicketID"]
        )

        booked = await _call(
            sync_url,
            "create",
            {"Ticket": _ticket(title), "Article": _article("two", TimeUnit="2")},
            db_sysconfig=True,
        )
        assert "Error" not in booked, booked
        rows = _query(
            sync_url,
            "SELECT time_unit FROM time_accounting WHERE ticket_id = :t",
            t=booked["TicketID"],
        )
        assert [float(r[0]) for r in rows] == [2.0]
    finally:
        _clear_sysconfig(sync_url)


@pytest.mark.db
async def test_no_agent_notify_default_with_owner(sync_url: str) -> None:
    mail = {"HistoryType": "EmailCustomer", "SenderType": "customer"}
    with_owner = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 notify owner", OwnerID=AGENT),
            "Article": _article("owned", **mail),
        },
    )
    assert "Error" not in with_owner, with_owner
    events = _outbox_events(sync_url, with_owner["TicketID"])
    assert "ArticleCreate" in events
    assert not [e for e in events if e.startswith("Notification") and "NewTicket" in e]

    without_owner = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket("GI7630 notify no owner"), "Article": _article("free", **mail)},
    )
    assert "NotificationNewTicket" in _outbox_events(sync_url, without_owner["TicketID"])

    # an explicit NoAgentNotify=1 silences even an ownerless ticket
    silent = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 notify silent"),
            "Article": _article("silent", NoAgentNotify=1, **mail),
        },
    )
    assert "NotificationNewTicket" not in _outbox_events(sync_url, silent["TicketID"])


@pytest.mark.db
async def test_force_and_exclude_user_ids_are_validated_but_not_forwarded(sync_url: str) -> None:
    mail = {"HistoryType": "EmailCustomer", "SenderType": "customer", "To": "queue@example.org"}
    ok = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 force ok"),
            # a scalar is accepted like Znuny (wrapped into a list)
            "Article": _article(
                "x",
                ForceNotificationToUserID=AGENT,
                ExcludeNotificationToUserID=[1],
                ExcludeMuteNotificationToUserID=["1"],
                **mail,
            ),
        },
    )
    assert "Error" not in ok, ok
    rows = _query(
        sync_url,
        "SELECT payload FROM tiqora_event_outbox WHERE ticket_id = :t"
        " AND event_type = 'NotificationNewTicket'",
        t=ok["TicketID"],
    )
    assert len(rows) == 1
    # Znuny's GI never passes them to ArticleCreate: no forced recipients, and only the
    # acting agent (self-notify rule) is on the skip list -- not the excluded root user 1
    payload = json.loads(rows[0][0])
    assert "recipient_user_ids" not in payload
    assert payload["skip_user_ids"] == [AGENT]
    to_line = _query(
        sync_url,
        "SELECT a_to FROM article_data_mime WHERE article_id = :a",
        a=ok["ArticleID"],
    )
    assert to_line[0][0] == "queue@example.org"

    bad_user = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 force bad"),
            "Article": _article("x", ForceNotificationToUserID=[AGENT, INVALID_AGENT]),
        },
    )
    assert bad_user == {
        "Error": {
            "ErrorCode": "TicketCreate.InvalidParameter",
            "ErrorMessage": (
                f"TicketCreate: Article->ForceNotificationToUserID UserID={INVALID_AGENT}"
                " parameter is invalid!"
            ),
        }
    }
    as_hash = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 force hash"),
            "Article": _article("x", ExcludeNotificationToUserID={"a": 1}),
        },
    )
    assert as_hash["Error"]["ErrorMessage"] == (
        "TicketCreate: Article->ExcludeNotificationToUserID parameter is invalid!"
    )


@pytest.mark.db
async def test_closed_ticket_is_never_created_locked(sync_url: str) -> None:
    result = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket("GI7630 closed", StateID=2, LockID=2), "Article": _article("c")},
    )
    assert "Error" not in result, result
    lock = _query(sync_url, "SELECT ticket_lock_id FROM ticket WHERE id = :t", t=result["TicketID"])
    assert int(lock[0][0]) == 1


# ---------------------------------------------------------------------------
# TicketUpdate
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_update_note_visible_by_default_and_article_ids(sync_url: str) -> None:
    ticket_id, _ = await _new_ticket(sync_url, "GI7630 update note")
    one = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _article("note")},
    )
    assert "Error" not in one, one
    assert isinstance(one["ArticleID"], int)
    assert "ArticleIDs" not in one
    visible = _query(
        sync_url,
        "SELECT is_visible_for_customer FROM article WHERE id = :a",
        a=one["ArticleID"],
    )
    assert int(visible[0][0]) == 1  # SysConfig default of TicketUpdate###IsVisibleForCustomer
    assert _history(sync_url, one["ArticleID"]) == [("AddNote", "%%GenericInterface Note")]
    assert "NotificationAddNote" in _outbox_events(sync_url, ticket_id)

    two = await _call(
        sync_url,
        "update",
        {
            "TicketID": ticket_id,
            "Article": [_article("n1", IsVisibleForCustomer=0), _article("n2", NoAgentNotify=1)],
        },
    )
    assert "ArticleID" not in two
    assert two["ArticleIDs"] == _article_ids(sync_url, ticket_id)[-2:]

    none = await _call(sync_url, "update", {"TicketID": ticket_id, "Ticket": {"Title": "GI7630 t"}})
    assert "ArticleID" not in none and "ArticleIDs" not in none


@pytest.mark.db
async def test_update_time_unit_and_validation(sync_url: str) -> None:
    ticket_id, _ = await _new_ticket(sync_url, "GI7630 update time")
    booked = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _article("t", TimeUnit="0,25")},
    )
    rows = _query(
        sync_url,
        "SELECT article_id, time_unit FROM time_accounting WHERE ticket_id = :t",
        t=ticket_id,
    )
    assert [(int(r[0]), float(r[1])) for r in rows] == [(booked["ArticleID"], 0.25)]
    bad = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _article("t", TimeUnit="x")},
    )
    assert bad == {
        "Error": {
            "ErrorCode": "TicketUpdate.InvalidParameter",
            "ErrorMessage": "TicketUpdate: Article->TimeUnit parameter is invalid!",
        }
    }
    bad_history = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _article("t", HistoryType="Nope")},
    )
    assert bad_history["Error"]["ErrorMessage"] == (
        "TicketUpdate: Article->HistoryType parameter is invalid!"
    )
    assert len(_article_ids(sync_url, ticket_id)) == 2  # the first article + the booked one


@pytest.mark.db
async def test_update_dynamic_fields(sync_url: str) -> None:
    ticket_id, _ = await _new_ticket(sync_url, "GI7630 update df")
    result = await _call(
        sync_url,
        "update",
        {
            "TicketID": ticket_id,
            "Article": [_article("a"), _article("b")],
            "DynamicField": [
                {"Name": "Gi7630Multi", "Value": ["a", "b"]},
                {"Name": "Gi7630Art", "Value": ["b"]},
            ],
        },
    )
    assert "Error" not in result, result
    first, second = result["ArticleIDs"]
    assert _df_values(sync_url, DF_MULTI, ticket_id) == ["a", "b"]
    # TicketUpdate sets the request's Article fields on the first article only
    assert _df_values(sync_url, DF_ART, first) == ["b"]
    assert _df_values(sync_url, DF_ART, second) == []

    # an Article field needs an article; an unknown field is an error, not ignored
    no_article = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "DynamicField": {"Name": "Gi7630Art", "Value": "a"}},
    )
    assert no_article == {
        "Error": {
            "ErrorCode": "TicketUpdate.MissingParameter",
            "ErrorMessage": (
                "TicketUpdate: To create an article DynamicField an article is required!"
            ),
        }
    }
    unknown = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "DynamicField": {"Name": "NoSuchField", "Value": "a"}},
    )
    assert (
        unknown["Error"]["ErrorMessage"] == "TicketUpdate: DynamicField->Name parameter is invalid!"
    )
    invalid = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "DynamicField": {"Name": "Gi7630Drop", "Value": "7"}},
    )
    assert invalid["Error"]["ErrorMessage"] == (
        "TicketUpdate: DynamicField->Value parameter is invalid!"
    )
    missing = await _call(
        sync_url, "update", {"TicketID": ticket_id, "DynamicField": {"Name": "Gi7630Ticket"}}
    )
    assert (
        missing["Error"]["ErrorMessage"]
        == "TicketUpdate: DynamicField->Value parameter is missing!"
    )


@pytest.mark.db
async def test_update_validates_everything_before_writing(sync_url: str) -> None:
    ticket_id, _ = await _new_ticket(sync_url, "GI7630 update atomic")
    before = _article_ids(sync_url, ticket_id)
    result = await _call(
        sync_url,
        "update",
        {
            "TicketID": ticket_id,
            "Ticket": {"Title": "GI7630 should not change"},
            "Article": _article("x"),
            "DynamicField": {"Name": "NoSuchField", "Value": "a"},
        },
    )
    assert "Error" in result
    assert _article_ids(sync_url, ticket_id) == before
    title = _query(sync_url, "SELECT title FROM ticket WHERE id = :t", t=ticket_id)[0][0]
    assert title == "GI7630 update atomic"


@pytest.mark.db
async def test_customer_follow_up_uses_article_list(sync_url: str) -> None:
    ticket_id, _ = await _new_ticket(sync_url, "GI7630 customer", CustomerUser=CUST_LOGIN)
    one = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": _article("question")},
        customer=True,
    )
    assert "Error" not in one, one
    article_id = one["ArticleID"]
    sender = _query(
        sync_url,
        "SELECT st.name, a.is_visible_for_customer FROM article a"
        " JOIN article_sender_type st ON st.id = a.article_sender_type_id WHERE a.id = :a",
        a=article_id,
    )
    assert (str(sender[0][0]), int(sender[0][1])) == ("customer", 1)
    assert _history(sync_url, article_id) == [("AddNote", "%%GenericInterface Note")]

    many = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Article": [_article("q1"), _article("q2")]},
        customer=True,
    )
    assert len(many["ArticleIDs"]) == 2

    # still no field changes and no article-less call for customers
    fields = await _call(
        sync_url,
        "update",
        {"TicketID": ticket_id, "Ticket": {"Title": "x"}, "Article": _article("q")},
        customer=True,
    )
    assert fields["Error"]["ErrorCode"] == "TicketUpdate.AccessDenied"
    empty = await _call(sync_url, "update", {"TicketID": ticket_id}, customer=True)
    assert empty["Error"]["ErrorCode"] == "TicketUpdate.MissingParameter"


@pytest.mark.db
async def test_update_article_own_dynamic_field_lands_on_that_article(sync_url: str) -> None:
    ticket_id, _ = await _new_ticket(sync_url, "GI7630 article df")
    result = await _call(
        sync_url,
        "update",
        {
            "TicketID": ticket_id,
            "Article": [
                _article("a", DynamicField={"Name": "Gi7630Art", "Value": ["a"]}),
                _article("b", DynamicField=[{"Name": "Gi7630Art", "Value": ["a", "b"]}]),
            ],
        },
    )
    assert "Error" not in result, result
    first, second = result["ArticleIDs"]
    assert _df_values(sync_url, DF_ART, first) == ["a"]
    assert _df_values(sync_url, DF_ART, second) == ["a", "b"]

    bad = await _call(
        sync_url,
        "update",
        {
            "TicketID": ticket_id,
            "Article": _article("c", DynamicField={"Name": "Gi7630Drop", "Value": "9"}),
        },
    )
    assert bad["Error"]["ErrorMessage"] == "TicketUpdate: DynamicField->Value parameter is invalid!"
    assert len(_article_ids(sync_url, ticket_id)) == 3


@pytest.mark.db
async def test_dynamic_field_name_must_match_exactly(sync_url: str) -> None:
    # MariaDB's collation would find "gi7630ticket"; Znuny's lookup is an exact hash key
    for operation, params in (
        ("create", {"Ticket": _ticket("GI7630 case"), "Article": _article("x")}),
        ("update", {"TicketID": (await _new_ticket(sync_url, "GI7630 case upd"))[0]}),
    ):
        result = await _call(
            sync_url,
            operation,
            {**params, "DynamicField": {"Name": "gi7630TICKET", "Value": "x"}},
        )
        op = "TicketCreate" if operation == "create" else "TicketUpdate"
        assert result == {
            "Error": {
                "ErrorCode": f"{op}.InvalidParameter",
                "ErrorMessage": f"{op}: DynamicField->Name parameter is invalid!",
            }
        }


@pytest.mark.db
async def test_select_field_values_follow_znuny_field_value_validate(sync_url: str) -> None:
    invalid = "TicketCreate: DynamicField->Value parameter is invalid!"
    cases: list[tuple[dict[str, Any], bool]] = [
        ({"Name": "Gi7630Multi", "Value": [""]}, False),  # "" is not a PossibleValues key
        ({"Name": "Gi7630Multi", "Value": ""}, True),  # an empty scalar passes, clears the field
        ({"Name": "Gi7630Multi", "Value": []}, True),
        ({"Name": "Gi7630Drop", "Value": ["1"]}, False),  # a list is no Dropdown value
        ({"Name": "Gi7630Drop", "Value": "1"}, True),
        ({"Name": "Gi7630Bare", "Value": "x"}, False),  # a Dropdown without PossibleValues
        ({"Name": "Gi7630Bare", "Value": ""}, False),
    ]
    for item, ok in cases:
        title = f"GI7630 select {item}"
        result = await _call(
            sync_url,
            "create",
            {"Ticket": _ticket(title), "Article": _article("x"), "DynamicField": item},
        )
        if ok:
            assert "Error" not in result, (item, result)
        else:
            assert result["Error"]["ErrorMessage"] == invalid, (item, result)
            assert _ticket_count(sync_url, title) == 0


@pytest.mark.db
async def test_owner_without_lock_locks_the_new_ticket(sync_url: str) -> None:
    def _lock(ticket_id: int) -> int:
        row = _query(sync_url, "SELECT ticket_lock_id FROM ticket WHERE id = :t", t=ticket_id)
        return int(row[0][0])

    owned = await _call(
        sync_url,
        "create",
        {"Ticket": _ticket("GI7630 lock owner", OwnerID=AGENT), "Article": _article("x")},
    )
    assert _lock(owned["TicketID"]) == 2
    explicit = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 lock explicit", OwnerID=AGENT, LockID=1),
            "Article": _article("x"),
        },
    )
    assert _lock(explicit["TicketID"]) == 1
    nobody = await _call(
        sync_url, "create", {"Ticket": _ticket("GI7630 lock none"), "Article": _article("x")}
    )
    assert _lock(nobody["TicketID"]) == 1


@pytest.mark.db
async def test_attachment_bytes_are_stored_and_read_back_on_both_dialects(sync_url: str) -> None:
    import base64

    payload = bytes(range(256)) * 3
    result = await _call(
        sync_url,
        "create",
        {
            "Ticket": _ticket("GI7630 attachment"),
            "Article": _article(
                "x",
                Attachment={
                    "Filename": "a.bin",
                    "ContentType": "application/octet-stream",
                    "Content": base64.b64encode(payload).decode(),
                },
            ),
        },
    )
    assert "Error" not in result, result
    from tiqora.api.compat.operations import op_ticket_get

    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            got = await op_ticket_get(
                {
                    "SessionID": AGENT_SESSION,
                    "TicketID": result["TicketID"],
                    "AllArticles": 1,
                    "Attachments": 1,
                },
                session,
                store,
            )
    finally:
        await engine.dispose()
    attachment = got["Ticket"][0]["Article"][0]["Attachment"][0]
    assert base64.b64decode(attachment["Content"]) == payload
    assert attachment["FilesizeRaw"] == len(payload)
