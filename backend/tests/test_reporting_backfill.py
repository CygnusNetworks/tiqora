"""Backfill job, status and ``load_state`` races (both dialects).

Seed ids: 47620-47659 (names contain ``rep47``). Layout: group 47620, queue 47621,
agent 47623, tickets 47630-47634 created 2024-06-01..05 08:00 (47631 is closed on 06-03),
ticket 47639 created 2099-01-02 (the only ticket a ``since`` in 2098 selects).
Every row this module writes is deleted again; the backfill also builds facts for any
other ticket above the first seeded id, so the cleanup of facts and events is unconditional.
The Znuny seed ticket is created "now", i.e. after every ``since`` used here: it counts as
"created on/after since" and starts every range at its (lower) id.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraReportDailySnapshot,
    TiqoraReportState,
    TiqoraReportTicketEvent,
    TiqoraReportTicketFact,
)
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs import JobCancelled, enqueue_job, get_job
from tiqora.jobs.runner import JobRunner
from tiqora.reporting import backfill
from tiqora.reporting.backfill import (
    REPORT_BACKFILL_JOB,
    SNAPSHOTS_MESSAGE,
    TICKETS_MESSAGE,
    ReportBackfillPayload,
    run_backfill,
)
from tiqora.reporting.state import (
    STATE_NAME,
    facts_status,
    is_deadlock,
    load_state,
    reporting_status,
)

pytestmark = pytest.mark.db

LO, HI = 47620, 47659
GROUP, QUEUE, AGENT = 47620, 47621, 47623
TICKETS = (47630, 47631, 47632, 47633, 47634)
FUTURE = 47639
SINCE = date(2024, 6, 1)
NOW = datetime(2024, 6, 10, 12, 0, 0)
SETTINGS = Settings(environment="test")

_URLS: set[str] = set()
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_daily_snapshot"
    " WHERE snapshot_date BETWEEN '2024-05-01' AND '2024-06-30'",
    "DELETE FROM tiqora_report_ticket_event",
    "DELETE FROM tiqora_report_ticket_fact",
    "DELETE FROM tiqora_report_dirty_ticket",
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


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


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


def _created(ticket_id: int) -> datetime:
    return datetime(2024, 6, 1 + ticket_id - TICKETS[0], 8, 0, 0)


def _seed(sync_url: str) -> None:
    """Five tickets with a NewTicket history each; 47631 is closed on 06-03 12:00."""
    _URLS.add(sync_url)
    _clean(sync_url)
    t0 = datetime(2024, 6, 1, 8, 0, 0)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        new = conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        closed = conn.execute(
            text("SELECT id FROM ticket_state WHERE name = 'closed successful'")
        ).scalar_one()
        h_new = conn.execute(
            text("SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
        ).scalar_one()
        h_state = conn.execute(
            text("SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47.backfill', 'x', 'Rita', 'Report', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-backfill-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " first_response_time, solution_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-backfill-q', :g, 1, 1, 1, 1, 0, 1, 60, 480,"
                " :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, "t": t0},
        )
        for tid in (*TICKETS, FUTURE):
            state = closed if tid == 47631 else new
            created = datetime(2099, 1, 2, 8, 0, 0) if tid == FUTURE else _created(tid)
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'rep47 ticket', :q, 1, 1, :u, 1, 3, :s,"
                    " 'rep47-cust', 'rep47.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"20240601{tid}",
                    "q": QUEUE,
                    "s": state,
                    "u": AGENT,
                    "t": created,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, :o, :t, :o)"
                ),
                {
                    "id": tid,
                    "n": f"%%20240601{tid}%%rep47-backfill-q%%3 normal%%new%%{tid}",
                    "ht": h_new,
                    "tid": tid,
                    "q": QUEUE,
                    "o": AGENT,
                    "s": new,
                    "t": created,
                },
            )
        conn.execute(
            text(
                "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (47640, '%%new%%closed successful', :ht, 47631, NULL, 1, :q, :o, 3,"
                " :s, :t, :o, :t, :o)"
            ),
            {
                "ht": h_state,
                "q": QUEUE,
                "o": AGENT,
                "s": closed,
                "t": datetime(2024, 6, 3, 12, 0, 0),
            },
        )
    engine.dispose()


async def _first_id_on_or_after(factory: async_sessionmaker[AsyncSession], moment: datetime) -> int:
    async with factory() as s:
        value = (
            await s.execute(
                text("SELECT MIN(id) FROM ticket WHERE create_time >= :t"), {"t": moment}
            )
        ).scalar_one()
    return int(value)


async def _ids_from(factory: async_sessionmaker[AsyncSession], first: int) -> list[int]:
    async with factory() as s:
        rows = await s.execute(
            text("SELECT id FROM ticket WHERE id >= :f ORDER BY id"), {"f": first}
        )
        return [int(r[0]) for r in rows.all()]


class Recorder:
    def __init__(self, cancel_after: int | None = None) -> None:
        self.calls: list[tuple[int, int | None, str | None]] = []
        self.checks = 0
        self.cancel_after = cancel_after

    async def progress(self, done: int, total: int | None, message: str | None) -> None:
        self.calls.append((done, total, message))

    async def check_cancelled(self) -> None:
        self.checks += 1
        if self.cancel_after is not None and self.checks >= self.cancel_after:
            raise JobCancelled


async def _noop() -> None:
    return None


def _factory(sync_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_to_async_url(sync_url))
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _event_rows(factory: async_sessionmaker[AsyncSession]) -> list[tuple[Any, ...]]:
    async with factory() as s:
        rows = await s.execute(
            select(
                TiqoraReportTicketEvent.ticket_id,
                TiqoraReportTicketEvent.kind,
                TiqoraReportTicketEvent.occurred_at,
                TiqoraReportTicketEvent.from_id,
                TiqoraReportTicketEvent.to_id,
            )
            .where(TiqoraReportTicketEvent.ticket_id.in_((*TICKETS, FUTURE)))
            .order_by(
                TiqoraReportTicketEvent.ticket_id,
                TiqoraReportTicketEvent.occurred_at,
                TiqoraReportTicketEvent.kind,
            )
        )
        return [tuple(r) for r in rows.all()]


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_backfill_builds_all_tickets_and_snapshots(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    rec = Recorder()
    try:
        result = await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(since=SINCE),
            progress=rec.progress,
            check_cancelled=rec.check_cancelled,
            sleep_seconds=0,
            now=NOW,
        )
        async with factory() as s:
            fact_ids = set(
                (
                    await s.execute(
                        select(TiqoraReportTicketFact.ticket_id).where(
                            TiqoraReportTicketFact.ticket_id >= TICKETS[0]
                        )
                    )
                )
                .scalars()
                .all()
            )
            snaps = {
                r.snapshot_date: r.open_count
                for r in (
                    await s.execute(
                        select(TiqoraReportDailySnapshot).where(
                            TiqoraReportDailySnapshot.queue_id == QUEUE
                        )
                    )
                )
                .scalars()
                .all()
            }
            state = await load_state(s)
            max_history = (await s.execute(text("SELECT MAX(id) FROM ticket_history"))).scalar_one()
        # every ticket from the first one created on/after `since` up to the highest id
        first = await _first_id_on_or_after(factory, datetime(2024, 5, 31, 12, 0))
        assert result == {"tickets": len(await _ids_from(factory, first)), "snapshot_days": 9}
        assert {*TICKETS, FUTURE} <= fact_ids
        # open stock at the end of each local day (47631 closes on 06-03 12:00)
        assert snaps == {
            date(2024, 6, 1): 1,
            date(2024, 6, 2): 2,
            date(2024, 6, 3): 2,
            date(2024, 6, 4): 3,
            date(2024, 6, 5): 4,
            date(2024, 6, 6): 4,
            date(2024, 6, 7): 4,
            date(2024, 6, 8): 4,
            date(2024, 6, 9): 4,
        }
        assert state.backfill_status == "done"
        assert state.last_snapshot_date == date(2024, 6, 9)
        assert state.snapshot_time_zone
        assert state.last_history_id == max_history > 0
        assert state.backfill_finished_at is not None
        assert state.backfill_cursor_ticket_id == state.backfill_target_ticket_id
        assert state.backfill_since == SINCE
        ticket_calls = [c for c in rec.calls if c[2] == TICKETS_MESSAGE]
        snap_calls = [c for c in rec.calls if c[2] == SNAPSHOTS_MESSAGE]
        assert ticket_calls and ticket_calls[-1][0] == ticket_calls[-1][1]
        assert snap_calls[-1][:2] == (9, 9)
        assert rec.checks == len(rec.calls)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_backfill_since_starts_at_first_ticket_created_on_or_after(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    rec = Recorder()
    try:
        result = await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(since=date(2098, 12, 31), snapshots=False),
            progress=rec.progress,
            check_cancelled=rec.check_cancelled,
            sleep_seconds=0,
            now=NOW,
        )
        async with factory() as s:
            built = set((await s.execute(select(TiqoraReportTicketFact.ticket_id))).scalars().all())
            state = await load_state(s)
        # only the 2099 ticket is on/after `since`; every lower id is skipped
        assert built == {FUTURE}
        assert result == {"tickets": 1, "snapshot_days": 0}
        assert state.backfill_start_ticket_id == FUTURE - 1
        assert state.backfill_target_ticket_id == FUTURE
        assert state.backfill_since == date(2098, 12, 31)
        assert state.last_snapshot_date is None  # snapshots were switched off
        assert rec.calls == [(1, 1, TICKETS_MESSAGE)]

        # nothing created on/after `since`: empty range, still ends "done"
        empty = await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(since=date(2100, 1, 1), snapshots=False),
            progress=rec.progress,
            check_cancelled=rec.check_cancelled,
            sleep_seconds=0,
            now=NOW,
        )
        assert empty == {"tickets": 0, "snapshot_days": 0}
        async with factory() as s:
            assert (await load_state(s)).backfill_status == "done"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_backfill_resume_and_idempotent(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    monkeypatch.setattr(backfill, "FACT_CHUNK_SIZE", 2)
    seen: list[list[int]] = []
    real = backfill.rebuild_and_commit

    async def spy(factory: Any, ids: Any, **kw: Any) -> Any:
        seen.append(list(ids))
        return await real(factory, ids, **kw)

    monkeypatch.setattr(backfill, "rebuild_and_commit", spy)
    engine, factory = _factory(url)
    payload = ReportBackfillPayload(since=SINCE, snapshots=False)
    try:
        first = Recorder(cancel_after=1)
        with pytest.raises(JobCancelled):
            await run_backfill(
                factory,
                SETTINGS,
                payload,
                progress=first.progress,
                check_cancelled=first.check_cancelled,
                sleep_seconds=0,
                now=NOW,
            )
        # the range starts at the Znuny seed ticket (created "now"), then our tickets
        first = await _first_id_on_or_after(factory, datetime(2024, 5, 31, 12, 0))
        all_ids = await _ids_from(factory, first)
        assert len(all_ids) >= 6 and all_ids[-6:] == [*TICKETS, FUTURE]
        assert seen == [all_ids[:2]]
        async with factory() as s:
            state = await load_state(s)
        assert state.backfill_status == "running"
        assert state.backfill_cursor_ticket_id == all_ids[1]  # the 2nd id of the range
        assert state.backfill_start_ticket_id == all_ids[0] - 1

        seen.clear()
        second = Recorder()
        await run_backfill(
            factory,
            SETTINGS,
            payload,
            progress=second.progress,
            check_cancelled=second.check_cancelled,
            sleep_seconds=0,
            now=NOW,
        )
        # continued after the cursor: nothing of the first chunk again, everything else once
        assert [i for chunk in seen for i in chunk] == all_ids[2:]
        assert all(len(chunk) <= 2 for chunk in seen)
        async with factory() as s:
            assert (await load_state(s)).backfill_status == "done"
        resumed = await _event_rows(factory)
        assert {r[0] for r in resumed} == {*TICKETS, FUTURE}  # events of every seeded ticket

        seen.clear()
        await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(since=SINCE, snapshots=False, restart=True),
            progress=_noop_progress,
            check_cancelled=_noop,
            sleep_seconds=0,
            now=NOW,
        )
        assert [i for chunk in seen for i in chunk] == all_ids  # from the start again
        assert await _event_rows(factory) == resumed
    finally:
        await engine.dispose()


async def _noop_progress(done: int, total: int | None, message: str | None) -> None:
    return None


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_backfill_job_handler_runs_through_job_runner(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    registry = Registry()
    register_core(registry)
    try:
        async with factory() as s:
            job = await enqueue_job(
                s,
                kind=REPORT_BACKFILL_JOB.kind,
                payload=ReportBackfillPayload(since=SINCE, snapshots=False),
                created_by=AGENT,
                registry=registry,
            )
            await s.commit()
            job_id = job.id
        runner = JobRunner(
            session_factory=factory, settings=SETTINGS, registry=registry, worker_id="rep47:1"
        )
        await runner.run_once()
        await runner.wait_idle()
        async with factory() as s:
            done = await get_job(s, job_id)
            assert done is not None
            state = await load_state(s)
        first = await _first_id_on_or_after(factory, datetime(2024, 5, 31, 12, 0))
        expected = len(await _ids_from(factory, first))
        assert done.status == "succeeded", done.error
        assert done.result is not None
        assert json.loads(done.result) == {"tickets": expected, "snapshot_days": 0}
        assert done.progress_total is not None and done.progress_done == done.progress_total
        assert state.backfill_status == "done"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_facts_status(url_fixture: str, request: pytest.FixtureRequest) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        async with factory() as s:
            st = await facts_status(s)
            assert (st.ready, st.backfill_status, st.backfill_progress) == (False, "pending", None)
            assert st.backfill_job_id is None and st.as_of is None
            await s.commit()
        async with factory() as s:
            state = await load_state(s, for_update=True)
            state.backfill_status = "running"
            state.backfill_job_id = 5
            state.backfill_start_ticket_id = 10
            state.backfill_cursor_ticket_id = 15
            state.backfill_target_ticket_id = 20
            await s.commit()
        async with factory() as s:
            st = await facts_status(s)
            assert (st.ready, st.backfill_status) == (False, "running")
            assert st.backfill_progress == 0.5 and st.backfill_job_id == 5
        async with factory() as s:
            state = await load_state(s, for_update=True)
            state.backfill_status = "done"
            state.last_tick_at = datetime(2024, 6, 10, 11, 59)
            state.last_history_id = 1
            state.gap_ids = json.dumps([[3, 1.0], [4, 2.0]])
            await s.commit()
        async with factory() as s:
            st = await facts_status(s)
            assert (st.ready, st.backfill_status, st.backfill_progress) == (True, "done", None)
            assert st.as_of == datetime(2024, 6, 10, 11, 59)
            full = await reporting_status(s)
            assert full.ready is True and full.last_history_id == 1
            assert full.gap_count == 2 and full.dirty_count == 0
            assert full.history_lag > 0  # the seeded history ids are far above 1
            assert full.fact_rows == 0 and full.event_rows == 0
    finally:
        await engine.dispose()


class _BarrierSession(AsyncSession):
    """Holds each session's first locking read of the state row until both have read."""

    barrier: asyncio.Barrier
    _waited = False

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await super().execute(statement, *args, **kwargs)
        if not self._waited and getattr(statement, "_for_update_arg", None) is not None:
            self._waited = True
            await asyncio.wait_for(self.barrier.wait(), 10)
        return result


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_for_update_first_creation_race(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Both sessions find no row under FOR UPDATE and insert it: on MariaDB the gap locks
    deadlock (1213), on PostgreSQL the loser hits the unique key. Either way both end
    up with the same single row and the loser's savepoint handling keeps the session usable."""
    url = request.getfixturevalue(url_fixture)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    _BarrierSession.barrier = asyncio.Barrier(2)
    factory = async_sessionmaker(engine, class_=_BarrierSession, expire_on_commit=False)

    async def worker() -> str:
        async with factory() as s:
            st = await load_state(s, for_update=True)
            st.last_history_id = 3
            await s.commit()
            return st.name

    try:
        names = await asyncio.wait_for(asyncio.gather(worker(), worker()), 30)
        assert names == [STATE_NAME, STATE_NAME]
        async with factory() as s:
            count = (await s.execute(select(func.count()).select_from(TiqoraReportState))).scalar()
            assert count == 1
    finally:
        await engine.dispose()


class _FakeOrig(Exception):
    def __init__(self, *args: Any, sqlstate: str | None = None) -> None:
        super().__init__(*args)
        self.sqlstate = sqlstate


def _deadlock_error(orig: Exception) -> DBAPIError:
    return OperationalError("INSERT INTO tiqora_report_state", {}, orig)


def test_is_deadlock_recognises_both_dialects() -> None:
    assert is_deadlock(_deadlock_error(_FakeOrig(1213, "Deadlock found when trying to get lock")))
    assert is_deadlock(_deadlock_error(_FakeOrig("deadlock detected", sqlstate="40P01")))
    assert not is_deadlock(_deadlock_error(_FakeOrig(1205, "Lock wait timeout exceeded")))
    assert not is_deadlock(_deadlock_error(_FakeOrig("other", sqlstate="23505")))


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_retries_once_after_deadlock(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            real = s.begin_nested
            attempts = {"n": 0}

            def flaky() -> Any:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise _deadlock_error(_FakeOrig(1213, "Deadlock found"))
                return real()

            monkeypatch.setattr(s, "begin_nested", flaky)
            st = await load_state(s, for_update=True)
            assert st.name == STATE_NAME and attempts["n"] == 2

            # a second deadlock is raised, not retried forever
            await s.rollback()
            attempts["n"] = -10

            def always() -> Any:
                attempts["n"] += 1
                raise _deadlock_error(_FakeOrig("deadlock detected", sqlstate="40P01"))

            monkeypatch.setattr(s, "begin_nested", always)
            with pytest.raises(OperationalError):
                await load_state(s, for_update=True)
            assert attempts["n"] == -8  # exactly two attempts
            # a non-deadlock error is not retried at all
            attempts["n"] = 0

            def other() -> Any:
                attempts["n"] += 1
                raise _deadlock_error(_FakeOrig(1205, "Lock wait timeout"))

            monkeypatch.setattr(s, "begin_nested", other)
            with pytest.raises(OperationalError):
                await load_state(s, for_update=True)
            assert attempts["n"] == 1
    finally:
        await engine.dispose()
