"""ITSM fact columns (``first_level_solved``, ``itsm_solution_working_minutes``).

The pure part needs no database. The DB part (both dialects) uses seed ids
972000-972049 (names contain ``ist972``): group 972000, queue 972001, agent 972003,
tickets 972010 (customer article, agent reply, closed) and 972011 (same shape). Every
row written here is deleted again.
"""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._schema_reset import delete_migration_seed_rows, reset_tiqora_schema
from tests.test_reporting_facts import (
    NO_HOURS,
    STATE_TYPES,
    T0,
    WH_CAL,
    art,
    h,
    inputs,
)
from tiqora.cli.migrate import build_alembic_config
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraReportTicketFact
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.reporting.facts import (
    FACT_VERSION,
    ITSM_SOLUTION_CREATED_CLOSED_MINUTES,
    compute_fact,
    first_level_solved,
    open_span_minutes,
)
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.history_events import ArticleRow, EventRow
from tiqora.reporting.incremental import run_report_facts_tick
from tiqora.reporting.state import load_state


def wall(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


def _articles(*spec: tuple[str, bool]) -> list[ArticleRow]:
    return [art(i + 1, sender, visible, i * 10) for i, (sender, visible) in enumerate(spec)]


def test_fls_single_customer_article() -> None:
    assert first_level_solved(_articles(("customer", True))) is True


def test_fls_single_agent_article() -> None:
    assert first_level_solved(_articles(("agent", True))) is True


def test_fls_customer_then_agent() -> None:
    assert first_level_solved(_articles(("customer", True), ("agent", True))) is True


def test_fls_agent_then_customer_is_not() -> None:
    assert first_level_solved(_articles(("agent", True), ("customer", True))) is False


def test_fls_three_visible_is_not() -> None:
    spec = (("customer", True), ("agent", True), ("customer", True))
    assert first_level_solved(_articles(*spec)) is False


def test_fls_system_first_is_not() -> None:
    assert first_level_solved(_articles(("system", True))) is False
    assert first_level_solved(_articles(("system", True), ("agent", True))) is False


def test_fls_auto_reply_second_is_not() -> None:
    assert first_level_solved(_articles(("customer", True), ("system", True))) is False


def test_fls_internal_notes_ignored() -> None:
    spec = (("customer", True), ("agent", False), ("agent", True))
    assert first_level_solved(_articles(*spec)) is True


def test_fls_no_visible_article_is_not() -> None:
    assert first_level_solved([]) is False
    assert first_level_solved(_articles(("agent", False))) is False


def _ev(kind: str, minutes: int, hid: int) -> EventRow:
    return EventRow(
        ticket_id=1,
        kind=kind,
        occurred_at=T0 + timedelta(minutes=minutes),
        queue_id=1,
        history_id=hid,
    )


def test_span_closed_once() -> None:
    assert open_span_minutes(T0, [_ev("close", 120, 2)], wall) == 120


def test_span_reopened_counts_only_open_spans() -> None:
    events = [_ev("close", 120, 2), _ev("reopen", 23 * 60, 3), _ev("close", 23 * 60 + 30, 4)]
    assert open_span_minutes(T0, events, wall) == 150


def test_span_created_closed_is_three_minutes() -> None:
    assert open_span_minutes(T0, [_ev("close", 0, 1)], wall) == 3
    assert ITSM_SOLUTION_CREATED_CLOSED_MINUTES == 3


def test_span_created_closed_then_reopened() -> None:
    events = [_ev("close", 0, 1), _ev("reopen", 10, 2), _ev("close", 40, 3)]
    assert open_span_minutes(T0, events, wall) == 30


def test_span_never_closed_is_none() -> None:
    assert open_span_minutes(T0, [], wall) is None
    assert open_span_minutes(T0, [_ev("queue", 5, 1)], wall) is None


def test_span_trailing_open_span_not_counted() -> None:
    events = [_ev("close", 120, 2), _ev("reopen", 180, 3)]
    assert open_span_minutes(T0, events, wall) == 120


def test_span_uses_the_given_measure_and_event_order() -> None:
    # history ids run against the time order, so only sorting by time gives 100 + 30
    events = [_ev("close", 230, 1), _ev("close", 100, 9), _ev("reopen", 200, 5)]
    assert open_span_minutes(T0, events, lambda a, b: 2 * wall(a, b)) == 2 * (100 + 30)


def test_span_close_before_creation_is_clamped_to_zero() -> None:
    events = [_ev("close", -10, 1), _ev("reopen", 10, 2), _ev("close", 40, 3)]
    assert open_span_minutes(T0, events, wall) == 30


def test_span_reopen_while_open_does_not_restart_the_span() -> None:
    events = [_ev("reopen", 50, 2), _ev("close", 100, 3)]
    assert open_span_minutes(T0, events, wall) == 100


def test_compute_fact_sets_both_columns() -> None:
    arts = [art(1, "customer", True, 0), art(2, "agent", True, 30)]
    hist = [h(1, "NewTicket", 0), h(2, "StateUpdate", 120, state=2)]
    f = compute_fact(inputs(hist, arts, calendar=WH_CAL))
    assert f.first_level_solved is True
    assert f.itsm_solution_working_minutes == f.solution_working_minutes == 120
    assert f.close_count == 1 and f.reopen_count == 0


def test_compute_fact_without_hours_uses_wall_minutes() -> None:
    hist = [h(1, "NewTicket", 0), h(2, "StateUpdate", 90, state=2)]
    f = compute_fact(
        inputs(hist, [art(1, "agent", True, 5), art(2, "customer", True, 6)], calendar=NO_HOURS)
    )
    assert f.first_level_solved is False
    assert f.itsm_solution_working_minutes == 90


def test_compute_fact_open_ticket_has_no_solution_time() -> None:
    f = compute_fact(inputs([h(1, "NewTicket", 0)], [art(1, "customer", True, 0)]))
    assert f.itsm_solution_working_minutes is None and f.first_level_solved is True


def test_fact_version_is_3() -> None:
    assert FACT_VERSION == 3
    assert STATE_TYPES[2] == "closed"


# --- database ---------------------------------------------------------------

LO, HI = 972000, 972049
GROUP, QUEUE, AGENT = 972000, 972001, 972003
T_ONE, T_TWO = 972010, 972011
SEED_T0 = datetime(2024, 6, 3, 8, 0, 0)
BUILT = datetime(2024, 6, 10, 12, 0, 0)
NOW = datetime(2024, 6, 20, 12, 0, 0)
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
_URLS: set[str] = set()
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind = 'core.report_backfill')",
    "DELETE FROM tiqora_job WHERE kind = 'core.report_backfill'",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)


def _async_url(sync_url: str) -> str:
    return (
        sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
        .replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    )


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _id(conn: Any, sql: str, **params: Any) -> int:
    return int(conn.execute(text(sql), params).scalar_one())


def _seed(sync_url: str) -> None:
    _URLS.add(sync_url)
    _clean(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        new = _id(conn, "SELECT id FROM ticket_state WHERE name = 'new'")
        closed = _id(conn, "SELECT id FROM ticket_state WHERE name = 'closed successful'")
        sender = {
            n: _id(conn, "SELECT id FROM article_sender_type WHERE name = :n", n=n)
            for n in ("customer", "agent")
        }
        channel = _id(conn, "SELECT id FROM communication_channel WHERE name = 'Email'")
        h_new = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
        h_state = _id(conn, "SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
        stamp = {"t": SEED_T0}
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'ist972.agent', 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, **stamp},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'ist972-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, **stamp},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " first_response_time, solution_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'ist972-q', :g, 1, 1, 1, 1, 0, 1, 60, 480, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, **stamp},
        )
        for n, tid in enumerate((T_ONE, T_TWO)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'ist972 ticket', :q, 1, 1, :u, 1, 3, :s,"
                    " 'ist972-cust', 'ist972.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2024060397201{n}",
                    "q": QUEUE,
                    "s": closed,
                    "u": AGENT,
                    **stamp,
                },
            )
            for k, (kind, minutes) in enumerate((("customer", 0), ("agent", 30))):
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
                        "t": SEED_T0 + timedelta(minutes=minutes),
                        "by": AGENT if kind == "agent" else 1,
                    },
                )
            for k, (htype, state, minutes) in enumerate(((h_new, new, 0), (h_state, closed, 120))):
                conn.execute(
                    text(
                        "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                        " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, :o, :t, :o)"
                    ),
                    {
                        "id": tid * 10 + k,
                        "n": f"%%2024060397201{n}%%ist972-q%%3 normal%%new%%{tid}"
                        if k == 0
                        else "%%new%%closed successful",
                        "ht": htype,
                        "tid": tid,
                        "q": QUEUE,
                        "o": AGENT,
                        "s": state,
                        "t": SEED_T0 + timedelta(minutes=minutes),
                    },
                )
    engine.dispose()


def _factory(sync_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_async_url(sync_url))
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rebuild_writes_new_columns(url_fixture: str, request: pytest.FixtureRequest) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_ONE], now=BUILT)
            await s.commit()
        async with factory() as s:
            fact = await s.get(TiqoraReportTicketFact, T_ONE)
            assert fact is not None
            assert fact.first_level_solved is True
            assert fact.itsm_solution_working_minutes is not None
            assert fact.itsm_solution_working_minutes > 0
            # the seed queue has no calendar: effective minutes are wall minutes
            assert fact.itsm_solution_working_minutes == fact.solution_minutes == 120
            assert fact.fact_version == 3
    finally:
        await engine.dispose()
    _clean(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_version_upgrade_rebuilds_v2_rows(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    registry = Registry()
    register_core(registry)

    async def tick() -> dict[str, int]:
        return await run_report_facts_tick(
            settings=Settings(environment="test"),
            session_factory=factory,
            now=NOW,
            wall_clock=lambda: 2_000_000_000.0,
            registry=registry,
        )

    try:
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_TWO], now=BUILT)
            await s.execute(
                text(
                    "UPDATE tiqora_report_ticket_fact SET fact_version = 2,"
                    " first_level_solved = NULL, itsm_solution_working_minutes = NULL"
                    " WHERE ticket_id = :t"
                ),
                {"t": T_TWO},
            )
            await s.commit()
        await tick()  # first tick: watermark + backfill job
        async with factory() as s:
            state = await load_state(s, for_update=True)
            state.backfill_status = "done"
            await s.commit()
        assert (await tick())["upgraded"] >= 1
        async with factory() as s:
            fact = await s.get(TiqoraReportTicketFact, T_TWO)
            assert fact is not None
            assert fact.fact_version == 3
            assert fact.first_level_solved is True
            assert fact.itsm_solution_working_minutes is not None
            assert fact.itsm_solution_working_minutes > 0
    finally:
        await engine.dispose()
    _clean(url)


# --- migration (sync: Alembic's env.py runs its own event loop) -------------------

_PREVIOUS = "20261010_0084"
_THIS = "20261010_0085"
_COLUMNS = {"first_level_solved", "itsm_solution_working_minutes"}


@pytest.fixture
def alembic_env(request: pytest.FixtureRequest) -> Iterator[str]:
    url = request.getfixturevalue(request.param)
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _async_url(url)
    get_settings.cache_clear()
    try:
        yield url
    finally:
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old
        get_settings.cache_clear()


def _fact_columns(sync_url: str) -> dict[str, bool]:
    engine = create_engine(sync_url)
    try:
        return {
            c["name"]: bool(c["nullable"])
            for c in inspect(engine).get_columns("tiqora_report_ticket_fact")
            if c["name"] in _COLUMNS
        }
    finally:
        engine.dispose()


_DOWNGRADE_TICKET = 985085


def _insert_fact_v3(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_report_ticket_fact (ticket_id, tn, created_at, created_by,"
                    " queue_id, queue_id_at_create, state_id, state_type, priority_id, owner_id,"
                    " channel, last_event_at, built_at, fact_version, first_level_solved,"
                    " itsm_solution_working_minutes)"
                    " VALUES (:t, 'down', '2026-10-26 08:00:00', 1, 1, 1, 1, 'closed', 3, 1,"
                    " 'email', '2026-10-26 08:00:00', '2026-10-26 08:00:00', 3, :solved, 42)"
                ),
                {"t": _DOWNGRADE_TICKET, "solved": True},
            )
    finally:
        engine.dispose()


def _fact_version(sync_url: str) -> int:
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            return int(
                conn.execute(
                    text("SELECT fact_version FROM tiqora_report_ticket_fact WHERE ticket_id = :t"),
                    {"t": _DOWNGRADE_TICKET},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _delete_fact(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tiqora_report_ticket_fact WHERE ticket_id = :t"),
                {"t": _DOWNGRADE_TICKET},
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("alembic_env", URLS, indirect=True)
def test_migration_adds_nullable_columns(alembic_env: str) -> None:
    url = alembic_env
    engine = create_engine(url)
    try:
        had_version_row = "tiqora_alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    cfg = build_alembic_config(include_owned=False)
    reset_tiqora_schema(url)
    try:
        command.upgrade(cfg, _PREVIOUS)
        assert _fact_columns(url) == {}
        command.upgrade(cfg, _THIS)
        assert _fact_columns(url) == {
            "first_level_solved": True,
            "itsm_solution_working_minutes": True,
        }
        _insert_fact_v3(url)
        command.downgrade(cfg, _PREVIOUS)
        assert _fact_columns(url) == {}
        # on the way forward the row must count as outdated, or its new columns stay empty
        assert _fact_version(url) == 2
        command.upgrade(cfg, _THIS)
        assert _fact_version(url) == 2
        command.downgrade(cfg, _PREVIOUS)
    finally:
        _delete_fact(url)
        command.upgrade(cfg, "head")
        delete_migration_seed_rows(url)
        if not had_version_row:
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM tiqora_alembic_version"))
            engine.dispose()
