"""``itsm_role`` and the first-level / ITSM solution measures of ``tickets`` / ``open_tickets``.

Both dialects. Seed ids 972100-972149, names contain ``ist972``: group, queue and agent
972100, tickets 972110-972116. ``ticket_type.id`` is SMALLINT on MariaDB, so the two types
take autoincrement ids and are found by name. Every row written here is deleted again.

June 2024 (created 2024-06-03 08:00, all closed):
  972110 incident, customer + agent articles, closed after 120 min      (first level)
  972111 request,  customer + agent articles, closed after 60 min       (first level)
  972112 incident, customer, agent, customer, closed after 240 min
  972113 request,  agent, customer, closed after 180 min
July 2024 (created 2024-07-01 08:00):
  972114 other type, open, customer article only                        (first level, no close)
  972115 incident, open, customer + agent articles, never closed
  972116 incident, closed after 60 min, reopened after 120, closed after 150 (open spans 90)
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter
from sqlalchemy import create_engine, delete, insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._approvals_helpers import to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.extensions.core import get_registry
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import ReportDefinition, TimeRange
from tiqora.reporting.engine import run_report
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.person_dimensions import NON_PERSON_DIMENSIONS, PERSON_DIMENSIONS
from tiqora.reporting.semantic import REPORT_SOURCES
from tiqora.reporting.settings import ReportingSettings

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
LO, HI = 972100, 972149
GROUP = QUEUE = AGENT = 972100
T_INC_FLS, T_REQ_FLS, T_INC_LONG, T_REQ_LATE = 972110, 972111, 972112, 972113
T_OTHER_OPEN, T_INC_OPEN, T_INC_REOPEN = 972114, 972115, 972116
ALL_TICKETS = tuple(range(972110, 972117))
JUNE_TICKETS = (T_INC_FLS, T_REQ_FLS, T_INC_LONG, T_REQ_LATE)
JUNE0 = datetime(2024, 6, 3, 8, 0)
JULY0 = datetime(2024, 7, 1, 8, 0)
BUILT = datetime(2024, 8, 1, 12, 0)
NOW = datetime(2024, 8, 2, 12, 0)
_BAND = {"lo": LO, "hi": HI}
_SETTINGS = {
    "itsm.incident_type": "ist972 Incident",
    "itsm.service_request_type": "ist972 Request",
    # configured but absent from ticket_type: no other type gets a role
    "itsm.major_incident_type": "ist972 no major",
    "itsm.problem_type": "ist972 no problem",
    "itsm.change_type": "ist972 no change",
    "itsm.change_task_type": "ist972 no task",
}
_CLEANUP = (
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_type WHERE name LIKE 'ist972 %'",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(list(_SETTINGS))))
            for stmt in _CLEANUP:
                conn.execute(text(stmt), _BAND)
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()
    _SEEDED.clear()


def _id(conn: Any, sql: str, **params: Any) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            _seed_rows(conn)
    finally:
        engine.dispose()


def _seed_rows(conn: Any) -> None:
    stamp = {"t": JUNE0}
    h_new = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
    h_state = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
    sender = {
        n: _id(conn, "SELECT id FROM article_sender_type WHERE name = :n", n=n)
        for n in ("customer", "agent")
    }
    channel = _id(conn, "SELECT id FROM communication_channel WHERE name = 'Email'")
    st = {
        k: _id(conn, "SELECT id FROM ticket_state WHERE name = :n", n=n)
        for k, n in (("new", "new"), ("open", "open"), ("closed", "closed successful"))
    }
    for name in ("ist972 Incident", "ist972 Request"):
        conn.execute(
            text(
                "INSERT INTO ticket_type (name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:n, 1, :t, 1, :t, 1)"
            ),
            {"n": name, **stamp},
        )
    types = dict(
        conn.execute(text("SELECT name, id FROM ticket_type WHERE name LIKE 'ist972 %'")).all()
    )
    other_type = _id(conn, "SELECT MIN(id) FROM ticket_type WHERE name NOT LIKE 'ist972 %'")
    conn.execute(insert(TiqoraSettings), [{"key": k, "value": v} for k, v in _SETTINGS.items()])
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, 'ist972.itsm', 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
        ),
        {"id": AGENT, **stamp},
    )
    conn.execute(
        text(
            "INSERT INTO permission_groups (id, name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, 'ist972-itsm-grp', 1, :t, 1, :t, 1)"
        ),
        {"id": GROUP, **stamp},
    )
    conn.execute(
        text(
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id,"
            " first_response_time, solution_time,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, 'ist972-itsm-q', :g, 1, 1, 1, 1, 0, 1, 60, 480, :t, 1, :t, 1)"
        ),
        {"id": QUEUE, "g": GROUP, **stamp},
    )
    conn.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
        ),
        {"u": AGENT, "g": GROUP, **stamp},
    )
    # ticket id, type id, created, articles (sender, minutes), history (state, minutes)
    inc, req = types["ist972 Incident"], types["ist972 Request"]
    cust, agent = "customer", "agent"
    spec: list[tuple[int, int, datetime, list[tuple[str, int]], list[tuple[str, int]]]] = [
        (T_INC_FLS, inc, JUNE0, [(cust, 0), (agent, 30)], [("closed", 120)]),
        (T_REQ_FLS, req, JUNE0, [(cust, 0), (agent, 30)], [("closed", 60)]),
        (T_INC_LONG, inc, JUNE0, [(cust, 0), (agent, 30), (cust, 60)], [("closed", 240)]),
        (T_REQ_LATE, req, JUNE0, [(agent, 0), (cust, 30)], [("closed", 180)]),
        (T_OTHER_OPEN, other_type, JULY0, [(cust, 0)], []),
        (T_INC_OPEN, inc, JULY0, [(cust, 0), (agent, 30)], []),
        (
            T_INC_REOPEN,
            inc,
            JULY0,
            [(cust, 0), (agent, 30)],
            [("closed", 60), ("open", 120), ("closed", 150)],
        ),
    ]
    for tid, type_id, created, articles, history in spec:
        final = history[-1][0] if history else "new"
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'ist972 ticket', :q, 1, :ty, :u, 1, 3, :s,"
                " 'ist972-cust', 'ist972.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                " :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"20240603{tid}",
                "q": QUEUE,
                "ty": type_id,
                "s": st[final],
                "u": AGENT,
                "t": created,
            },
        )
        for k, (kind, minutes) in enumerate(articles):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 1, :t, :by, :t, :by)"
                ),
                {
                    "id": tid * 10 + k,
                    "tid": tid,
                    "s": sender[kind],
                    "c": channel,
                    "t": created + timedelta(minutes=minutes),
                    "by": AGENT if kind == agent else 1,
                },
            )
        events = [(h_new, "new", 0), *((h_state, s, m) for s, m in history)]
        previous = "new"
        for k, (htype, state, minutes) in enumerate(events):
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, :ty, :q, :o, 3, :s, :t, :o, :t, :o)"
                ),
                {
                    "id": tid * 10 + k,
                    "n": f"%%{tid}%%ist972-itsm-q%%3 normal%%new%%{tid}"
                    if k == 0
                    else f"%%{previous}%%{state}",
                    "ht": htype,
                    "tid": tid,
                    "ty": type_id,
                    "q": QUEUE,
                    "o": AGENT,
                    "s": st[state],
                    "t": created + timedelta(minutes=minutes),
                },
            )
            previous = state


async def _factory(
    request: pytest.FixtureRequest, url_fixture: str
) -> async_sessionmaker[AsyncSession]:
    sync_url = request.getfixturevalue(url_fixture)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _URLS.add(sync_url)
        _clean(sync_url)
        _seed(sync_url)
        async with factory() as s:
            await rebuild_ticket_facts(s, list(ALL_TICKETS), now=BUILT)
            await s.commit()
        _SEEDED.add(sync_url)
    return factory


def _range(start: str, end: str) -> TimeRange:
    return TypeAdapter(TimeRange).validate_python({"kind": "absolute", "start": start, "end": end})


JUNE = _range("2024-06-01", "2024-06-30")
JULY = _range("2024-07-01", "2024-07-31")
BOTH = _range("2024-06-01", "2024-07-31")


def _defn(
    source: str,
    dims: list[str],
    measures: list[str],
    time_range: TimeRange | None,
    where: dict[str, Any] | None = None,
    time_field: str | None = None,
) -> ReportDefinition:
    return ReportDefinition.model_validate(
        {
            "source": source,
            "dimensions": [{"key": d} for d in dims],
            "measures": [{"key": m} for m in measures],
            "filter": {"version": 1, "where": where} if where else None,
            "time_field": time_field,
            "time_range": time_range.model_dump(mode="json") if time_range else None,
            "visualization": {"type": "table"},
        }
    )


async def _run(factory: async_sessionmaker[AsyncSession], defn: ReportDefinition) -> Any:
    async with factory() as s:
        return await run_report(
            s,
            defn=defn,
            params=RunParams(time_zone="UTC"),
            user_id=AGENT,
            report_key=None,
            reporting=ReportingSettings(),
            now=NOW,
            use_cache=False,
        )


def _where(field: str, value: list[str]) -> dict[str, Any]:
    return {"field": field, "op": "in", "value": value}


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_fls_measures(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    try:
        res = await _run(
            factory,
            _defn(
                "tickets",
                [],
                ["closed_tickets", "first_level_solved", "first_level_solution_rate"],
                JUNE,
                _where("state_type", ["closed"]),
            ),
        )
        assert res.rows == [[4, 2, pytest.approx(0.5)]]
    finally:
        await factory.kw["bind"].dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_fls_rate_ignores_rows_not_yet_upgraded(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    try:
        async with factory() as s:
            await s.execute(
                text(
                    "UPDATE tiqora_report_ticket_fact SET first_level_solved = NULL,"
                    " itsm_solution_working_minutes = NULL WHERE ticket_id = :t"
                ),
                {"t": T_INC_LONG},
            )
            await s.commit()
        defn = _defn(
            "tickets",
            [],
            ["first_level_solved", "first_level_solution_rate", "itsm_solution_avg"],
            JUNE,
        )
        res = await _run(factory, defn)
        # 110 and 111 are first level; 112 is not yet known and stays out of the denominator
        # (120 + 60 + 180) / 3 for the solution time
        assert res.rows == [[2, pytest.approx(2 / 3), pytest.approx(120.0)]]
    finally:
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_INC_LONG], now=BUILT)
            await s.commit()
        await factory.kw["bind"].dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_itsm_solution_avg_ignores_null(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    try:
        measures = ["tickets", "itsm_solution_avg", "itsm_solution_p50"]
        july = await _run(factory, _defn("tickets", [], measures, JULY))
        # 972114 and 972115 never closed (NULL, not 0); 972116 counts its open spans 60 + 30
        assert july.rows == [[3, pytest.approx(90.0), pytest.approx(90.0)]]
        june = await _run(factory, _defn("tickets", [], measures, JUNE))
        assert june.rows == [[4, pytest.approx(150.0), pytest.approx(150.0)]]
    finally:
        await factory.kw["bind"].dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_itsm_role_dimension_and_filter(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    try:
        res = await _run(factory, _defn("tickets", ["itsm_role"], ["tickets"], BOTH))
        by_role = {row[0]: row[1] for row in res.rows}
        assert by_role == {"incident": 4, "service_request": 2, None: 1}
        filtered = await _run(
            factory,
            _defn("tickets", ["state_type"], ["tickets"], BOTH, _where("itsm_role", ["incident"])),
        )
        assert sorted(filtered.rows) == [["closed", 3], ["new", 1]]
    finally:
        await factory.kw["bind"].dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_itsm_role_without_configured_types(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """No configured type exists: one NULL group, and the filter matches nothing."""
    factory = await _factory(request, url_fixture)
    try:
        async with factory() as s:
            for key in _SETTINGS:
                await s.execute(
                    update(TiqoraSettings)
                    .where(TiqoraSettings.key == key)
                    .values(value="ist972 absent")
                )
            await s.commit()
        res = await _run(factory, _defn("tickets", ["itsm_role"], ["tickets"], BOTH))
        assert res.rows == [[None, 7]]
        none = await _run(
            factory,
            _defn("tickets", [], ["tickets"], BOTH, _where("itsm_role", ["incident"])),
        )
        assert none.rows in ([], [[0]])
    finally:
        async with factory() as s:
            for key, value in _SETTINGS.items():
                await s.execute(
                    update(TiqoraSettings).where(TiqoraSettings.key == key).values(value=value)
                )
            await s.commit()
        await factory.kw["bind"].dispose()


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_itsm_role_on_open_tickets(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    try:
        res = await _run(factory, _defn("open_tickets", ["itsm_role"], ["tickets"], None))
        assert {row[0]: row[1] for row in res.rows} == {"incident": 1, None: 1}
        filtered = await _run(
            factory,
            _defn("open_tickets", [], ["tickets"], None, _where("itsm_role", ["incident"])),
        )
        assert filtered.rows == [[1]]
    finally:
        await factory.kw["bind"].dispose()


def test_new_dimension_keys_are_classified() -> None:
    assert "itsm_role" in NON_PERSON_DIMENSIONS
    assert "itsm_role" not in PERSON_DIMENSIONS
    sources = {src.key: src for src in get_registry().items(REPORT_SOURCES)}
    for key in ("tickets", "open_tickets"):
        assert "itsm_role" in {d.key for d in sources[key].dimensions}
        measures = {m.key for m in sources[key].measures}
        assert ("first_level_solved" in measures) == (key == "tickets")
