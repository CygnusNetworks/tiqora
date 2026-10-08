"""GenericInterface TicketCreate / TicketGet / TicketHistoryGet / OutOfOffice: the Znuny 6.5
parity fixes of Task 15b (the golden peer found these; here they run without a peer).

DB tests on MariaDB and PostgreSQL. Seed ids live in the GI band 8900-8929: agents 8900 (acting)
and 8902 (owner to be), groups 8900 (queue 8901, agent 8900 is rw) and 8901 (queue 8902, nobody
is a member), customer users gi8900.cust / gi8900.noco. Every row (including the tickets the
operations create in queue 8901) is deleted afterwards.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_rows_above, snapshot_max_ids
from tiqora.api.compat.operations import (
    op_out_of_office,
    op_ticket_create,
    op_ticket_get,
    op_ticket_history_get,
)
from tiqora.domain.auth import SessionStore
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2024, 6, 1, 12, 0, 0)
AGENT, OWNER_TO_BE, GROUP, OTHER_GROUP = 8900, 8902, 8900, 8901
QUEUE, FOREIGN_QUEUE = 8901, 8902
SESSION = "GI8900AGENTSESSION"
CUST_LOGIN, CUST_EMAIL, CUST_COMPANY = "gi8900.cust", "gi8900.cust@example.org", "GI8900CO"
NOCO_LOGIN = "gi8900.noco"

_SEEDED: list[str] = []
_COUNTER_TABLES = ("tiqora_event_outbox", "tiqora_cache_invalidation", "ticket_number_counter")
_SNAPSHOTS: dict[str, dict[str, int]] = {}
_IN_QUEUE = f"(SELECT id FROM ticket WHERE queue_id IN ({QUEUE}, {FOREIGN_QUEUE}))"
_IN_QUEUE_ARTICLES = f"(SELECT id FROM article WHERE ticket_id IN {_IN_QUEUE})"


def _cleanup_statements() -> tuple[str, ...]:
    return (
        f"DELETE FROM ticket_history WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article_data_mime WHERE article_id IN {_IN_QUEUE_ARTICLES}",
        f"DELETE FROM article WHERE ticket_id IN {_IN_QUEUE}",
        f"DELETE FROM ticket WHERE queue_id IN ({QUEUE}, {FOREIGN_QUEUE})",
        "DELETE FROM sessions WHERE session_id LIKE 'GI8900%'",
        f"DELETE FROM customer_user WHERE login IN ('{CUST_LOGIN}', '{NOCO_LOGIN}')",
        f"DELETE FROM queue WHERE id IN ({QUEUE}, {FOREIGN_QUEUE})",
        f"DELETE FROM group_user WHERE group_id IN ({GROUP}, {OTHER_GROUP})",
        f"DELETE FROM permission_groups WHERE id IN ({GROUP}, {OTHER_GROUP})",
        f"DELETE FROM user_preferences WHERE user_id IN ({AGENT}, {OWNER_TO_BE})",
        f"DELETE FROM users WHERE id IN ({AGENT}, {OWNER_TO_BE})",
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


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = int(time.time())
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    if sync_url not in _SNAPSHOTS:  # once per module: the fixture re-seeds for every test
        _SNAPSHOTS[sync_url] = snapshot_max_ids(sync_url, _COUNTER_TABLES)
    user_sql = (
        "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
        " create_by, change_time, change_by)"
        " VALUES (:id, :login, 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)"
    )
    queue_sql = (
        "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
        " signature_id, follow_up_id, follow_up_lock, update_time, solution_time, valid_id,"
        " create_time, create_by, change_time, change_by)"
        " VALUES (:id, :name, :g, 1, 1, 1, 1, 0, :upd, :sol, 1, :t, 1, :t, 1)"
    )
    cust_sql = (
        "INSERT INTO customer_user (login, email, customer_id, pw, first_name, last_name,"
        " valid_id, create_time, create_by, change_time, change_by)"
        " VALUES (:login, :email, :cid, 'x', 'Gi', 'Cust', 1, :t, 1, :t, 1)"
    )
    with engine.begin() as conn:
        for stmt in _cleanup_statements():
            conn.execute(text(stmt))
        conn.execute(text(user_sql), {"id": AGENT, "login": "gi8900.agent", "t": T0})
        conn.execute(text(user_sql), {"id": OWNER_TO_BE, "login": "gi8900.owner", "t": T0})
        for gid, name in ((GROUP, "gi8900-group"), (OTHER_GROUP, "gi8900-other")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": name, "t": T0},
            )
        for uid in (AGENT, OWNER_TO_BE):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by)"
                    " VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
                ),
                {"u": uid, "g": GROUP, "t": T0},
            )
        conn.execute(
            text(queue_sql),
            {"id": QUEUE, "name": "GI8900Queue", "g": GROUP, "upd": 60, "sol": 120, "t": T0},
        )
        conn.execute(
            text(queue_sql),
            {"id": FOREIGN_QUEUE, "name": "GI8900Foreign", "g": OTHER_GROUP, "upd": 0, "sol": 0}
            | {"t": T0},
        )
        conn.execute(
            text(cust_sql),
            {"login": CUST_LOGIN, "email": CUST_EMAIL, "cid": CUST_COMPANY, "t": T0},
        )
        conn.execute(
            text(cust_sql), {"login": NOCO_LOGIN, "email": "noco@example.org", "cid": "", "t": T0}
        )
        for key, val in {
            "UserID": str(AGENT),
            "UserLogin": "gi8900.agent",
            "UserType": "User",
            "UserLastRequest": str(now),
            "UserSessionStart": str(now),
        }.items():
            conn.execute(
                text(
                    "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                    " VALUES (:sid, :k, :v, 0)"
                ),
                {"sid": SESSION, "k": key, "v": val},
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


async def _call(sync_url: str, func: Any, params: dict[str, Any]) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    payload = {"SessionID": SESSION, **params}
    try:
        async with factory() as session:

            async def _no_row(name: str) -> None:
                return None

            sysconfig = SysConfig(fetch=_no_row)
            if func is op_ticket_create:
                return await func(payload, session, factory, store, sysconfig)  # type: ignore[no-any-return]
            return await func(payload, session, store)  # type: ignore[no-any-return]
    finally:
        await engine.dispose()


async def _create(sync_url: str, title: str, article: dict[str, Any] | None = None, **ticket: Any):
    res = await _call(
        sync_url,
        op_ticket_create,
        {
            "Ticket": {
                "Title": title,
                "QueueID": QUEUE,
                "StateID": 1,
                "PriorityID": 3,
                **ticket,
            },
            "Article": article or {"Subject": title, "Body": f"{title} body"},
        },
    )
    assert "Error" not in res, res
    return res


def _ticket_row(sync_url: str, ticket_id: int) -> Any:
    rows = _query(
        sync_url,
        "SELECT user_id, ticket_lock_id, customer_id, customer_user_id, escalation_update_time,"
        " escalation_solution_time, escalation_time FROM ticket WHERE id = :t",
        t=ticket_id,
    )
    return rows[0]


def _history(sync_url: str, ticket_id: int) -> list[tuple[str, str]]:
    rows = _query(
        sync_url,
        "SELECT t.name, h.name FROM ticket_history h"
        " JOIN ticket_history_type t ON t.id = h.history_type_id"
        " WHERE h.ticket_id = :t ORDER BY h.id",
        t=ticket_id,
    )
    return [(str(r[0]), str(r[1])) for r in rows]


def _types(history: list[tuple[str, str]]) -> list[str]:
    # "Reset of unlock time." depends on whether the second changed between two calls
    return [t for t, n in history if (t, n) != ("Misc", "Reset of unlock time.")]


# --- 1. owner ------------------------------------------------------------------------------


@pytest.mark.db
async def test_without_owner_the_calling_agent_owns_the_unlocked_ticket(sync_url: str) -> None:
    res = await _create(sync_url, "GI8900 no owner")
    row = _ticket_row(sync_url, res["TicketID"])
    assert (int(row[0]), int(row[1])) == (AGENT, 1)  # TicketCreate.pm:1872-1880, unlocked
    history = _history(sync_url, res["TicketID"])
    assert _types(history)[0] == "NewTicket"
    assert ("OwnerUpdate", f"%%gi8900.agent%%{AGENT}") in history
    assert "Lock" not in _types(history)


@pytest.mark.db
async def test_with_owner_history_is_owner_update_then_lock(sync_url: str) -> None:
    res = await _create(sync_url, "GI8900 owner", Owner="gi8900.owner")
    row = _ticket_row(sync_url, res["TicketID"])
    assert (int(row[0]), int(row[1])) == (OWNER_TO_BE, 2)  # TicketCreate.pm:1853-1870
    types = _types(_history(sync_url, res["TicketID"]))
    assert types.index("OwnerUpdate") < types.index("Lock")
    assert types.count("OwnerUpdate") == 1
    assert types[-2:] == ["OwnerUpdate", "Lock"]  # after the article rows


@pytest.mark.db
async def test_explicit_lock_is_a_lock_set_after_create(sync_url: str) -> None:
    res = await _create(sync_url, "GI8900 lock", LockID=2)
    row = _ticket_row(sync_url, res["TicketID"])
    assert (int(row[0]), int(row[1])) == (AGENT, 2)
    types = _types(_history(sync_url, res["TicketID"]))
    assert types.index("NewTicket") < types.index("Lock") < types.index("OwnerUpdate")


# --- 2. customer id ------------------------------------------------------------------------


@pytest.mark.db
async def test_customer_id_comes_from_the_customer_user(sync_url: str) -> None:
    res = await _create(sync_url, "GI8900 cust login", CustomerUser=CUST_LOGIN)
    row = _ticket_row(sync_url, res["TicketID"])
    assert (row[2], row[3]) == (CUST_COMPANY, CUST_LOGIN)  # TicketCreate.pm:1356-1361
    assert ("CustomerUpdate", f"%%CustomerID={CUST_COMPANY};CustomerUser={CUST_LOGIN};") in (
        _history(sync_url, res["TicketID"])
    )


@pytest.mark.db
async def test_customer_user_found_by_email_and_explicit_customer_id_wins(sync_url: str) -> None:
    by_mail = await _create(sync_url, "GI8900 cust mail", CustomerUser=CUST_EMAIL.upper())
    row = _ticket_row(sync_url, by_mail["TicketID"])
    assert (row[2], row[3]) == (CUST_COMPANY, CUST_LOGIN)  # TicketCreate.pm:1327-1338

    explicit = await _create(
        sync_url, "GI8900 cust explicit", CustomerUser=CUST_LOGIN, CustomerID="OTHER-CO"
    )
    row = _ticket_row(sync_url, explicit["TicketID"])
    assert (row[2], row[3]) == ("OTHER-CO", CUST_LOGIN)


@pytest.mark.db
async def test_customer_user_without_company_and_unknown_customer_user(sync_url: str) -> None:
    noco = await _create(sync_url, "GI8900 cust noco", CustomerUser=NOCO_LOGIN)
    row = _ticket_row(sync_url, noco["TicketID"])
    assert (row[2], row[3]) == ("", NOCO_LOGIN)
    unknown = await _create(sync_url, "GI8900 cust unknown", CustomerUser="nobody@example.org")
    row = _ticket_row(sync_url, unknown["TicketID"])
    assert (row[2], row[3]) == ("", "nobody@example.org")  # kept as sent, like Znuny


# --- 3. article To -------------------------------------------------------------------------


def _article_to(sync_url: str, article_id: int) -> tuple[Any, Any, Any]:
    rows = _query(
        sync_url,
        "SELECT a_to, a_cc, a_bcc FROM article_data_mime WHERE article_id = :a",
        a=article_id,
    )
    return rows[0][0], rows[0][1], rows[0][2]


@pytest.mark.db
async def test_article_to_is_the_queue_name_without_article_send(sync_url: str) -> None:
    by_id = await _create(sync_url, "GI8900 to id")
    assert _article_to(sync_url, by_id["ArticleID"])[0] == "GI8900Queue"  # TicketCreate.pm:1557
    # the queue name of the request is used as sent; To/Cc/Bcc of the request are dropped
    res = await _call(
        sync_url,
        op_ticket_create,
        {
            "Ticket": {"Title": "GI8900 to name", "Queue": "GI8900Queue", "State": "new"}
            | {"Priority": "3 normal"},
            "Article": {
                "Subject": "s",
                "Body": "b",
                "To": "someone@example.org",
                "Cc": "cc@example.org",
            },
        },
    )
    assert "Error" not in res, res
    assert _article_to(sync_url, res["ArticleID"]) == ("GI8900Queue", None, None)


# --- 6. queue error text -------------------------------------------------------------------


@pytest.mark.db
async def test_unpermitted_queue_error_is_znunys(sync_url: str) -> None:
    res = await _call(
        sync_url,
        op_ticket_create,
        {
            "Ticket": {
                "Title": "GI8900 denied",
                "QueueID": FOREIGN_QUEUE,
                "StateID": 1,
                "PriorityID": 3,
            },
            "Article": {"Subject": "s", "Body": "b"},
        },
    )
    assert res["Error"] == {
        "ErrorCode": "TicketCreate.AccessDenied",
        "ErrorMessage": "TicketCreate: Can not create tickets in given Queue or QueueID!",
    }
    assert _query(sync_url, "SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=FOREIGN_QUEUE) == [
        (0,)
    ]


# --- 7. escalation at create ---------------------------------------------------------------


@pytest.mark.db
async def test_escalation_columns_are_set_when_the_ticket_is_created(sync_url: str) -> None:
    customer_mail = {
        "Subject": "s",
        "Body": "b",
        "HistoryType": "EmailCustomer",
        "SenderType": "customer",
    }
    res = await _create(sync_url, "GI8900 escalation", article=customer_mail)
    row = _ticket_row(sync_url, res["TicketID"])
    update_at, solution_at, combined = int(row[4]), int(row[5]), int(row[6])
    now = int(time.time())
    # queue: update after 60 min, solution after 120 min. This SysConfig has no working hours,
    # and the port then returns the start time unchanged (like DateTime->Add AsWorkingTime), so
    # a set column is "about now" -- the point is that it is not 0 any more.
    assert abs(update_at - now) < 300
    assert abs(solution_at - now) < 300
    assert combined == min(update_at, solution_at)


# --- 5. TicketGet / TicketHistoryGet ids ---------------------------------------------------


@pytest.mark.db
@pytest.mark.parametrize(
    ("ticket_id", "code", "message"),
    [
        (
            "Search",
            "TicketGet.WrongStructure",
            "TicketGet: Invalid ticket ID parameter(s) found!",
        ),
        ("1,x", "TicketGet.WrongStructure", "TicketGet: Invalid ticket ID parameter(s) found!"),
        (["0"], "TicketGet.WrongStructure", "TicketGet: Invalid ticket ID parameter(s) found!"),
        ("01", "TicketGet.WrongStructure", "TicketGet: Invalid ticket ID parameter(s) found!"),
        ([], "TicketGet.WrongStructure", "TicketGet: Structure for TicketID is not correct!"),
        ("", "TicketGet.MissingParameter", "TicketGet: TicketID parameter is missing!"),
        (None, "TicketGet.MissingParameter", "TicketGet: TicketID parameter is missing!"),
    ],
)
async def test_ticket_get_ids_are_checked_like_znuny(
    sync_url: str, ticket_id: Any, code: str, message: str
) -> None:
    res = await _call(sync_url, op_ticket_get, {"TicketID": ticket_id})
    assert res == {"Error": {"ErrorCode": code, "ErrorMessage": message}}


@pytest.mark.db
async def test_ticket_get_flags_are_perl_truthy_not_integers(sync_url: str) -> None:
    created = await _create(sync_url, "GI8900 flags")
    res = await _call(
        sync_url,
        op_ticket_get,
        {
            "TicketID": str(created["TicketID"]),
            "AllArticles": "yes",
            "Attachments": "",
            "DynamicFields": "1",
            "Extended": "no",
        },
    )
    assert "Error" not in res, res
    ticket = res["Ticket"][0] if isinstance(res["Ticket"], list) else res["Ticket"]
    assert str(ticket["TicketID"]) == str(created["TicketID"])
    assert len(ticket["Article"]) == 1


@pytest.mark.db
async def test_ticket_history_get_non_numeric_id_is_access_denied(sync_url: str) -> None:
    res = await _call(sync_url, op_ticket_history_get, {"TicketID": "abc"})
    assert res["Error"] == {
        "ErrorCode": "TicketHistoryGet.AccessDenied",
        "ErrorMessage": "TicketHistoryGet: User does not have access to the ticket abc!",
    }
    res = await _call(sync_url, op_ticket_history_get, {"TicketID": []})
    assert res["Error"]["ErrorCode"] == "TicketHistoryGet.WrongStructure"


# --- 4. OutOfOffice ------------------------------------------------------------------------


@pytest.mark.db
async def test_out_of_office_answers_success_only(sync_url: str) -> None:
    # agent 8900 must be in group admin for the operation; use a rw membership of group "admin"
    admin_gid = _query(sync_url, "SELECT id FROM permission_groups WHERE name = 'admin'")[0][0]
    _query(
        sync_url,
        "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
        " change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)",
        u=AGENT,
        g=admin_gid,
        t=T0,
    )
    try:
        res = await _call(
            sync_url,
            op_out_of_office,
            {
                "OutOfOfficeEntries": [
                    {"UserLogin": "gi8900.owner", "OutOfOffice": 1, "StartDate": "2026-03-01"},
                    {"UserLogin": "no.such.agent", "OutOfOffice": 1},
                    "garbage",
                ]
            },
        )
        assert res == {"Success": 1}  # OutOfOffice.pm:260-264, bad entries are skipped
        prefs = dict(
            _query(
                sync_url,
                "SELECT preferences_key, preferences_value FROM user_preferences"
                " WHERE user_id = :u",
                u=OWNER_TO_BE,
            )
        )
        raw = prefs["OutOfOffice"]
        assert (
            bytes(raw).decode() if isinstance(raw, (bytes, bytearray, memoryview)) else raw
        ) == "1"
        assert prefs["OutOfOfficeStartYear"] is not None
    finally:
        _query(
            sync_url,
            "DELETE FROM group_user WHERE user_id = :u AND group_id = :g",
            u=AGENT,
            g=admin_gid,
        )
