"""DB tests: caller lookup (``GET /reference/caller``), phone matching in the
customer searches and the click-to-call scheme (``GET /reference/phone-config``).

Uses the 862xx id range; everything seeded is deleted again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
AGENT = 86201
GROUP_READ = 86230
GROUP_HIDDEN = 86231
QUEUE_READ = 86200
QUEUE_HIDDEN = 86201
T_OPEN_OLD = 86270
T_OPEN_NEW = 86271
T_CLOSED = 86272
T_HIDDEN = 86273
T_OTHER_CUSTOMER = 86274
TICKETS = (T_OPEN_OLD, T_OPEN_NEW, T_CLOSED, T_HIDDEN, T_OTHER_CUSTOMER)
# login, phone, mobile
CUSTOMERS = (
    ("caller.anna", "+49 (228) 555-0101", None),
    ("caller.bernd", None, "0228 5550101"),
    ("caller.clara", "+49 228 999 0000", None),
)
SETTING_KEY = "channel.phone.dial_scheme"


def _to_async_url(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(conn: Any) -> None:
    ids = ", ".join(str(t) for t in TICKETS)
    logins = ", ".join(f"'{c[0]}'" for c in CUSTOMERS)
    for sql in (
        f"DELETE FROM ticket WHERE id IN ({ids})",
        f"DELETE FROM queue WHERE id IN ({QUEUE_READ}, {QUEUE_HIDDEN})",
        f"DELETE FROM group_user WHERE user_id = {AGENT}",
        f"DELETE FROM permission_groups WHERE id IN ({GROUP_READ}, {GROUP_HIDDEN})",
        f"DELETE FROM users WHERE id = {AGENT}",
        f"DELETE FROM customer_user WHERE login IN ({logins})",
        f"DELETE FROM tiqora_settings WHERE `key` = '{SETTING_KEY}'",
    ):
        conn.execute(text(sql))


def _seed(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'caller.agent', 'x', 'Cal', 'Ler', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": NOW},
        )
        for gid, name in ((GROUP_READ, "caller-read"), (GROUP_HIDDEN, "caller-hidden")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": name, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": GROUP_READ, "t": NOW},
        )
        for qid, gid, name in (
            (QUEUE_READ, GROUP_READ, "CallerRead"),
            (QUEUE_HIDDEN, GROUP_HIDDEN, "CallerHidden"),
        ):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": name, "g": gid, "t": NOW},
            )
        for login, phone, mobile in CUSTOMERS:
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, phone, mobile, pw, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:l, :e, 'CALLERCO', :f, 'Caller', :p, :m, 'x', 1,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "l": login,
                    "e": f"{login}@example.com",
                    "f": login.split(".")[1].title(),
                    "p": phone,
                    "m": mobile,
                    "t": NOW,
                },
            )
        # (ticket, queue, state, customer, change_time)
        rows = (
            (T_OPEN_OLD, QUEUE_READ, 4, "caller.anna", datetime(2024, 1, 1)),
            (T_OPEN_NEW, QUEUE_READ, 1, "caller.bernd", datetime(2024, 5, 1)),
            (T_CLOSED, QUEUE_READ, 2, "caller.anna", datetime(2024, 6, 1)),
            (T_HIDDEN, QUEUE_HIDDEN, 4, "caller.anna", datetime(2024, 6, 1)),
            (T_OTHER_CUSTOMER, QUEUE_READ, 4, "caller.clara", datetime(2024, 6, 1)),
        )
        for tid, qid, sid, cust, changed in rows:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, :s, 'CALLERCO', :c,"
                    " 0, 0, 0, 0, 0, 0, 0, :ch, 1, :ch, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2024060186{tid}",
                    "title": f"Caller ticket {tid}",
                    "q": qid,
                    "s": sid,
                    "c": cust,
                    "ch": changed,
                },
            )
    engine.dispose()


def _teardown(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


@pytest.fixture
async def client(mariadb_znuny_url: str) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    _seed(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id=AGENT, login="caller.agent", first_name="Cal", last_name="Ler", auth_method="session"
    )
    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url)


@pytest.mark.asyncio
async def test_caller_lookup_returns_all_matches_and_readable_open_tickets(client: Any) -> None:
    resp = await client.get("/api/v1/reference/caller", params={"number": "+49228 5550101"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["number_normalized"] == "492285550101"
    by_login = {c["login"]: c for c in data["customers"]}
    assert set(by_login) == {"caller.anna", "caller.bernd"}
    assert by_login["caller.anna"]["name"] == "Anna Caller"
    assert by_login["caller.anna"]["email"] == "caller.anna@example.com"
    assert by_login["caller.bernd"]["mobile"] == "0228 5550101"
    # Open only, readable queues only, newest first.
    assert [t["id"] for t in data["open_tickets"]] == [T_OPEN_NEW, T_OPEN_OLD]
    first = data["open_tickets"][0]
    assert first["queue"] == "CallerRead"
    assert first["state"] == "new"
    assert first["customer_user_id"] == "caller.bernd"


@pytest.mark.asyncio
async def test_caller_lookup_short_or_unknown_number(client: Any) -> None:
    short = await client.get("/api/v1/reference/caller", params={"number": "0101"})
    assert short.status_code == 200
    assert short.json()["customers"] == []
    unknown = await client.get("/api/v1/reference/caller", params={"number": "+1 555 000 1111"})
    assert unknown.json() == {
        "number_normalized": "15550001111",
        "customers": [],
        "open_tickets": [],
    }


@pytest.mark.asyncio
async def test_customer_searches_match_phone_numbers(client: Any) -> None:
    picker = await client.get("/api/v1/reference/customers", params={"q": "555-0101"})
    assert {c["login"] for c in picker.json()} == {"caller.anna", "caller.bernd"}
    quick = await client.get("/api/v1/reference/customer-search", params={"q": "2289990"})
    assert [c["login"] for c in quick.json()["contacts"]] == ["caller.clara"]
    # Fewer than five digits never turns into a phone search.
    few = await client.get("/api/v1/reference/customers", params={"q": "0101"})
    assert {c["login"] for c in few.json()} & {"caller.anna", "caller.bernd"} == set()


@pytest.mark.asyncio
async def test_phone_config_dial_scheme(client: Any, mariadb_znuny_url: str) -> None:
    default = await client.get("/api/v1/reference/phone-config")
    assert default.status_code == 200
    assert default.json() == {"dial_scheme": "tel", "originate": False}
    engine = create_engine(mariadb_znuny_url)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO tiqora_settings (`key`, value) VALUES (:k, 'sip')"),
            {"k": SETTING_KEY},
        )
    engine.dispose()
    sip = await client.get("/api/v1/reference/phone-config")
    assert sip.json() == {"dial_scheme": "sip", "originate": False}
