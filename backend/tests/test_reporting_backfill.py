"""Backfill job, status and ``load_state`` races (both dialects).

Seed ids: 47620-47659 (names contain ``rep47``). Layout: group 47620, queue 47621,
agent 47623, tickets 47630-47634 created 2024-06-01..05 08:00 (47631 is closed on 06-03),
ticket 47639 created 2099-01-02 (the only ticket a ``since`` in 2098 selects).
Every row this module writes is deleted again; the backfill also builds facts for any
other ticket above the first seeded id, so the cleanup of facts and events also covers that ticket.
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
from sqlalchemy.pool import NullPool

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
from tiqora.main import main
from tiqora.reporting import backfill
from tiqora.reporting import state as state_module
from tiqora.reporting.backfill import (
    REPORT_BACKFILL_JOB,
    SNAPSHOTS_MESSAGE,
    TICKETS_MESSAGE,
    ReportBackfillPayload,
    run_backfill,
)
from tiqora.reporting.dirty import mark_tickets_dirty
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
    "DELETE FROM tiqora_report_daily_snapshot WHERE queue_id BETWEEN :lo AND :hi",
    # the band tickets plus the Znuny seed ticket (created "now"), which every range includes
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id >= :lo OR ticket_id IN"
    " (SELECT id FROM ticket WHERE id < :lo AND create_time >= '2024-05-31')",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id >= :lo OR ticket_id IN"
    " (SELECT id FROM ticket WHERE id < :lo AND create_time >= '2024-05-31')",
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
        assert ticket_calls
        total = state.backfill_target_ticket_id - state.backfill_start_ticket_id
        # one bar over both phases: the snapshot days continue after the tickets
        assert snap_calls[-1][:2] == (total + 9, total + 9)
        assert ticket_calls[-1][:2] == (total, total)
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


async def _race(url: str, monkeypatch: pytest.MonkeyPatch, *, retry: bool) -> tuple[list[Any], int]:
    """Two sessions take FOR UPDATE on the missing row, then both insert it.

    Returns each worker's outcome and how often ``load_state`` ran into a deadlock
    (counted at its classification, so a pass cannot hide that the path never ran).
    """
    deadlocks = {"n": 0}
    real = state_module._deadlock_behind

    def counting(exc: DBAPIError) -> bool:
        hit = real(exc)
        deadlocks["n"] += int(hit)
        return hit

    monkeypatch.setattr(state_module, "_deadlock_behind", counting)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    _BarrierSession.barrier = asyncio.Barrier(2)
    factory = async_sessionmaker(engine, class_=_BarrierSession, expire_on_commit=False)

    async def worker() -> str:
        async with factory() as s:
            st = await load_state(s, for_update=True, retry_on_deadlock=retry)
            st.last_history_id = 3
            await s.commit()
            return st.name

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(worker(), worker(), return_exceptions=True), 60
        )
        async with factory() as s:
            count = (await s.execute(select(func.count()).select_from(TiqoraReportState))).scalar()
        assert count == 1
        return list(outcomes), deadlocks["n"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_first_creation_race_retries_when_opted_in(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MariaDB: the gap locks deadlock (1213, the victim also loses its savepoint, 1305)
    and the opted-in loser retries. PostgreSQL: the loser hits the unique key instead."""
    url = request.getfixturevalue(url_fixture)
    outcomes, deadlocks = await _race(url, monkeypatch, retry=True)
    assert outcomes == [STATE_NAME, STATE_NAME]
    assert deadlocks == (1 if url_fixture == "mariadb_znuny_url" else 0)


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_first_creation_race_raises_without_opt_in(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    outcomes, deadlocks = await _race(url, monkeypatch, retry=False)
    if url_fixture == "mariadb_znuny_url":
        assert deadlocks == 1
        failed = [o for o in outcomes if isinstance(o, BaseException)]
        assert len(failed) == 1 and isinstance(failed[0], OperationalError)
        assert [o for o in outcomes if not isinstance(o, BaseException)] == [STATE_NAME]
    else:  # no deadlock on PostgreSQL: the unique-key path serves both
        assert deadlocks == 0 and outcomes == [STATE_NAME, STATE_NAME]


class _FakeOrig(Exception):
    def __init__(self, *args: Any, sqlstate: str | None = None) -> None:
        super().__init__(*args)
        self.sqlstate = sqlstate


def _deadlock_error(orig: Exception) -> DBAPIError:
    return OperationalError("INSERT INTO tiqora_report_state", {}, orig)


def _raise_lost_savepoint() -> Any:
    """What MariaDB gives: the failed ROLLBACK TO SAVEPOINT (1305) after a deadlock (1213)."""
    try:
        raise _deadlock_error(_FakeOrig(1213, "Deadlock found when trying to get lock"))
    except OperationalError:
        raise _deadlock_error(_FakeOrig(1305, "SAVEPOINT sa_savepoint_1 does not exist")) from None


def test_is_deadlock_recognises_both_dialects() -> None:
    assert is_deadlock(_deadlock_error(_FakeOrig(1213, "Deadlock found when trying to get lock")))
    assert is_deadlock(_deadlock_error(_FakeOrig("deadlock detected", sqlstate="40P01")))
    assert not is_deadlock(_deadlock_error(_FakeOrig(1205, "Lock wait timeout exceeded")))
    assert not is_deadlock(_deadlock_error(_FakeOrig("other", sqlstate="23505")))


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_retry_counts_and_non_deadlock_errors(
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
            st = await load_state(s, for_update=True, retry_on_deadlock=True)
            assert st.name == STATE_NAME and attempts["n"] == 2

            # a second deadlock is raised, not retried forever
            await s.rollback()
            attempts["n"] = 0

            def always() -> Any:
                attempts["n"] += 1
                raise _deadlock_error(_FakeOrig("deadlock detected", sqlstate="40P01"))

            monkeypatch.setattr(s, "begin_nested", always)
            with pytest.raises(OperationalError):
                await load_state(s, for_update=True, retry_on_deadlock=True)
            assert attempts["n"] == 2  # exactly two attempts
            # without the opt-in a deadlock is raised at once
            attempts["n"] = 0
            with pytest.raises(OperationalError):
                await load_state(s, for_update=True)
            assert attempts["n"] == 1
            # a non-deadlock error is never retried
            attempts["n"] = 0

            def other() -> Any:
                attempts["n"] += 1
                raise _deadlock_error(_FakeOrig(1205, "Lock wait timeout"))

            monkeypatch.setattr(s, "begin_nested", other)
            with pytest.raises(OperationalError):
                await load_state(s, for_update=True, retry_on_deadlock=True)
            assert attempts["n"] == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_deadlock_never_loses_flushed_work_silently(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost savepoint (MariaDB aborted the whole transaction) after a flushed write:
    without the opt-in ``load_state`` raises instead of returning a row with the write
    gone; the session is rolled back and usable afterwards."""
    url = request.getfixturevalue(url_fixture)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            await mark_tickets_dirty(s, [47651])  # flushed, not committed
            calls = {"n": 0}

            def lost() -> Any:
                calls["n"] += 1
                _raise_lost_savepoint()

            monkeypatch.setattr(s, "begin_nested", lost)
            with pytest.raises(OperationalError):
                await load_state(s, for_update=True)
            assert calls["n"] == 1
            monkeypatch.undo()
            n = (
                await s.execute(
                    text("SELECT COUNT(*) FROM tiqora_report_dirty_ticket WHERE ticket_id = 47651")
                )
            ).scalar_one()
            assert n == 0  # rolled back, and the caller was told by the exception
            st = await load_state(s)  # the session works again
            assert st.name == STATE_NAME
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_resumed_backfill_uses_the_stored_since_for_snapshots(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-RESUME: the tick resumes with ``ReportBackfillPayload()`` (no ``since``). The
    snapshot days must still start at the ``since`` of the run, not at the oldest fact."""
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    monkeypatch.setattr(backfill, "FACT_CHUNK_SIZE", 2)
    engine, factory = _factory(url)
    try:
        first = Recorder(cancel_after=1)
        with pytest.raises(JobCancelled):
            await run_backfill(
                factory,
                SETTINGS,
                ReportBackfillPayload(since=SINCE),
                progress=first.progress,
                check_cancelled=first.check_cancelled,
                sleep_seconds=0,
                now=NOW,
            )
        monkeypatch.setattr(backfill, "FACT_CHUNK_SIZE", 200)
        second = Recorder()
        result = await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(),  # what the tick enqueues
            progress=second.progress,
            check_cancelled=second.check_cancelled,
            sleep_seconds=0,
            now=NOW,
        )
        assert result["snapshot_days"] == 9  # 06-01 .. 06-09, not from the oldest fact
        async with factory() as s:
            dates = {
                r
                for r in (
                    await s.execute(
                        select(TiqoraReportDailySnapshot.snapshot_date).where(
                            TiqoraReportDailySnapshot.queue_id == QUEUE
                        )
                    )
                )
                .scalars()
                .all()
            }
            state = await load_state(s)
        assert min(dates) == SINCE and max(dates) == date(2024, 6, 9)
        assert state.backfill_since == SINCE and state.last_snapshot_date == date(2024, 6, 9)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_superseded_run_cannot_move_the_cursor(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    monkeypatch.setattr(backfill, "FACT_CHUNK_SIZE", 2)
    engine, factory = _factory(url)
    payload = ReportBackfillPayload(since=SINCE, snapshots=False)
    try:
        # same run: the cursor never moves backwards
        run = await backfill._begin(factory, SETTINGS, payload)
        await backfill._save_cursor(factory, run, run.start + 5)
        await backfill._save_cursor(factory, run, run.start + 2)
        async with factory() as s:
            assert (await load_state(s)).backfill_cursor_ticket_id == run.start + 5

        # another run takes over mid-way (a restart job, the CLI): the old one stops
        async def take_over() -> None:
            async with factory() as s:
                st = await load_state(s, for_update=True)
                st.backfill_started_at = datetime(2030, 1, 1)
                st.backfill_cursor_ticket_id = 1
                await s.commit()

        calls = {"n": 0}

        async def check() -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                await take_over()

        with pytest.raises(JobCancelled):
            await run_backfill(
                factory,
                SETTINGS,
                ReportBackfillPayload(since=SINCE, snapshots=False, restart=True),
                progress=_noop_progress,
                check_cancelled=check,
                sleep_seconds=0,
                now=NOW,
            )
        async with factory() as s:
            st = await load_state(s)
        assert st.backfill_cursor_ticket_id == 1 and st.backfill_status == "running"
        assert st.backfill_started_at == datetime(2030, 1, 1)
        # finishing is refused as well
        with pytest.raises(JobCancelled):
            await backfill._save_cursor(factory, run, 99, finish=(None, "UTC"))
        async with factory() as s:
            assert (await load_state(s)).backfill_status == "running"
    finally:
        await engine.dispose()


def test_report_cli_through_main(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = mariadb_znuny_url
    _URLS.add(url)
    _clean(url)

    def make_factory() -> async_sessionmaker[AsyncSession]:
        engine = create_async_engine(_to_async_url(url), poolclass=NullPool)
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr("tiqora.cli.report.get_session_factory", make_factory)
    captured: list[ReportBackfillPayload] = []

    async def fake_run(
        factory: Any, settings: Any, payload: Any, *, progress: Any, check_cancelled: Any, **kw: Any
    ) -> dict[str, int]:
        captured.append(payload)
        await progress(1, 2, "Tickets werden ausgewertet …")
        return {"tickets": 1, "snapshot_days": 0}

    monkeypatch.setattr(backfill, "run_backfill", fake_run)

    with pytest.raises(SystemExit) as status_exit:
        main(["report", "status"])
    assert status_exit.value.code == 0
    status_json = json.loads(capsys.readouterr().out)
    assert status_json["ready"] is False and status_json["backfill_status"] == "pending"

    with pytest.raises(SystemExit) as run_exit:
        main(["report", "backfill", "--since", "2024-06-01", "--restart", "--no-snapshots"])
    assert run_exit.value.code == 0
    assert captured == [ReportBackfillPayload(since=SINCE, restart=True, snapshots=False)]
    out = capsys.readouterr().out
    assert "1/2 Tickets werden ausgewertet …" in out and "'tickets': 1" in out

    with pytest.raises(SystemExit) as bare_exit:
        main(["report"])
    assert bare_exit.value.code == 2

    # a queued backfill job blocks the CLI (two writers on one cursor)
    async def enqueue() -> None:
        factory = make_factory()
        async with factory() as s:
            await enqueue_job(
                s,
                kind=REPORT_BACKFILL_JOB.kind,
                payload=ReportBackfillPayload(),
                created_by=None,
                dedupe_key="report_backfill",
            )
            await s.commit()

    asyncio.run(enqueue())
    with pytest.raises(SystemExit) as blocked:
        main(["report", "backfill"])
    assert blocked.value.code == 1
    assert len(captured) == 1  # not run again
    assert "already queued" in capsys.readouterr().err
