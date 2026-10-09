"""Incremental fact loop (both dialects).

Seed ids: 47660-47699 (names contain ``rep47``). Group 47660, queue 47661, agent 47663,
tickets 47670-47679 created 2024-06-01 08:00 in state ``new`` (no history rows; every test
adds its own, with explicit ids above the current maximum). Every row written here is
deleted again; report rows are removed by ticket band / queue band.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraReportDailySnapshot,
    TiqoraReportState,
    TiqoraReportTicketEvent,
    TiqoraReportTicketFact,
)
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs import JobCancelled
from tiqora.reporting import backfill as backfill_module
from tiqora.reporting import incremental
from tiqora.reporting.backfill import ReportBackfillPayload, run_backfill
from tiqora.reporting.dirty import mark_tickets_dirty
from tiqora.reporting.incremental import GAP_TTL_SECONDS, run_report_facts_tick
from tiqora.reporting.state import load_state
from tiqora.worker import report_facts as worker_module

pytestmark = pytest.mark.db

LO, HI = 47660, 47699
GROUP, QUEUE, AGENT = 47660, 47661, 47663
TICKETS = tuple(range(47670, 47680))
NOW = datetime(2024, 6, 8, 10, 0, 0)
SETTINGS = Settings(environment="test")
WALL = 2_000_000_000.0

_URLS: set[str] = set()
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_daily_snapshot WHERE queue_id BETWEEN :lo AND :hi",
    # the backfill also builds the Znuny seed ticket (created "now")
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi OR ticket_id IN"
    " (SELECT id FROM ticket WHERE id < 47000 AND create_time >= '2024-05-31')",
    # the backfill also builds the Znuny seed ticket (created "now")
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi OR ticket_id IN"
    " (SELECT id FROM ticket WHERE id < 47000 AND create_time >= '2024-05-31')",
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


def _seed(sync_url: str) -> None:
    _URLS.add(sync_url)
    _clean(sync_url)
    t0 = datetime(2024, 6, 1, 8, 0, 0)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        new = conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47.incr', 'x', 'Rita', 'Report', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-incr-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": t0},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " first_response_time, solution_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-incr-q', :g, 1, 1, 1, 1, 0, 1, 60, 480,"
                " :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, "t": t0},
        )
        for tid in TICKETS:
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
                {"id": tid, "tn": f"20240601{tid}", "q": QUEUE, "s": new, "u": AGENT, "t": t0},
            )
    engine.dispose()


def _max_history(sync_url: str) -> int:
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT MAX(id) FROM ticket_history")).scalar_one()
    engine.dispose()
    return int(value or 0)


def _insert_history(sync_url: str, rows: list[tuple[int, int, str]]) -> None:
    """Rows of (history id, ticket id, history type name) at explicit ids."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        new = conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        for hid, tid, type_name in rows:
            ht = conn.execute(
                text("SELECT id FROM ticket_history_type WHERE name = :n"), {"n": type_name}
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, :o, :t, :o)"
                ),
                {
                    "id": hid,
                    "n": f"%%20240601{tid}%%rep47-incr-q%%3 normal%%new%%{tid}",
                    "ht": ht,
                    "tid": tid,
                    "q": QUEUE,
                    "o": AGENT,
                    "s": new,
                    "t": datetime(2024, 6, 1, 8, 0, 0),
                },
            )
        if conn.dialect.name == "postgresql":
            conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('ticket_history','id'),"
                    " (SELECT MAX(id) FROM ticket_history))"
                )
            )
    engine.dispose()


def _delete_ticket(sync_url: str, tid: int) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ticket_history WHERE ticket_id = :t"), {"t": tid})
        conn.execute(text("DELETE FROM ticket WHERE id = :t"), {"t": tid})
    engine.dispose()


def _factory(sync_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_to_async_url(sync_url))
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _registry() -> Registry:
    registry = Registry()
    register_core(registry)
    return registry


async def _tick(
    factory: async_sessionmaker[AsyncSession], *, wall: float = WALL, now: datetime = NOW
) -> dict[str, int]:
    return await run_report_facts_tick(
        settings=SETTINGS,
        session_factory=factory,
        now=now,
        wall_clock=lambda: wall,
        registry=_registry(),
    )


async def _fact_ids(factory: async_sessionmaker[AsyncSession]) -> set[int]:
    async with factory() as s:
        return {
            int(i)
            for i in (
                await s.execute(
                    select(TiqoraReportTicketFact.ticket_id).where(
                        TiqoraReportTicketFact.ticket_id.between(LO, HI)
                    )
                )
            )
            .scalars()
            .all()
        }


async def _state(factory: async_sessionmaker[AsyncSession]) -> TiqoraReportState:
    async with factory() as s:
        return await load_state(s)


async def _jobs(factory: async_sessionmaker[AsyncSession]) -> list[TiqoraJob]:
    async with factory() as s:
        return list(
            (
                await s.execute(
                    select(TiqoraJob)
                    .where(TiqoraJob.kind == "core.report_backfill")
                    .order_by(TiqoraJob.id)
                )
            )
            .scalars()
            .all()
        )


async def _set_state(factory: async_sessionmaker[AsyncSession], **values: Any) -> None:
    async with factory() as s:
        st = await load_state(s, for_update=True)
        for key, value in values.items():
            setattr(st, key, value)
        await s.commit()


async def _prime(url: str, factory: async_sessionmaker[AsyncSession]) -> int:
    """Run a first tick (sets the watermark at the current maximum); returns the watermark."""
    await _tick(factory)
    wm = (await _state(factory)).last_history_id
    assert wm == _max_history(url)
    return wm


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_first_tick_sets_watermark_and_enqueues_backfill(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    _insert_history(url, [(_max_history(url) + 1, TICKETS[0], "NewTicket")])
    engine, factory = _factory(url)
    try:
        result = await _tick(factory)
        state = await _state(factory)
        assert state.last_history_id == _max_history(url) == result["watermark"]
        jobs = await _jobs(factory)
        assert len(jobs) == 1 and jobs[0].status == "queued"
        assert state.backfill_job_id == jobs[0].id
        assert json.loads(jobs[0].payload)["restart"] is False
        await _tick(factory)
        assert [j.id for j in await _jobs(factory)] == [jobs[0].id]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_new_history_rows_rebuild_touched_tickets(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        assert await _fact_ids(factory) == set()
        _insert_history(url, [(wm + 1, 47671, "NewTicket"), (wm + 2, 47671, "Move")])
        result = await _tick(factory)
        assert result["tickets"] == 1 and result["history_rows"] == 2
        assert result["watermark"] == wm + 2
        assert await _fact_ids(factory) == {47671}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_gap_row_committed_later_is_processed(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        _insert_history(url, [(wm + 1, 47672, "NewTicket"), (wm + 3, 47672, "Move")])
        result = await _tick(factory)
        assert result["gaps"] == 1 and result["watermark"] == wm + 3
        gaps = json.loads((await _state(factory)).gap_ids or "[]")
        assert [g[0] for g in gaps] == [wm + 2]
        assert await _fact_ids(factory) == {47672}

        _insert_history(url, [(wm + 2, 47673, "NewTicket")])
        result = await _tick(factory)
        assert result["gaps"] == 0 and result["history_rows"] == 1
        assert json.loads((await _state(factory)).gap_ids or "[]") == []
        assert await _fact_ids(factory) == {47672, 47673}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_old_gaps_expire(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    warnings: list[tuple[str, dict[str, Any]]] = []

    class _Log:
        def warning(self, event: str, **kw: Any) -> None:
            warnings.append((event, kw))

        def __getattr__(self, name: str) -> Any:
            return lambda *a, **k: None

    monkeypatch.setattr(incremental, "logger", _Log())
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        # gaps below the watermark whose rows never showed up (ids that do not exist)
        high = wm + 100
        old, fresh = wm + 50, wm + 60
        await _set_state(
            factory,
            last_history_id=high,
            gap_ids=json.dumps([[old, WALL - GAP_TTL_SECONDS - 1], [fresh, WALL - 10]]),
        )
        result = await _tick(factory)
        assert [g[0] for g in json.loads((await _state(factory)).gap_ids or "[]")] == [fresh]
        assert result["gaps"] == 1
        assert ("report_facts_gaps_expired", {"count": 1, "ids": [old]}) in warnings
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_dirty_tickets_are_rebuilt(url_fixture: str, request: pytest.FixtureRequest) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        async with factory() as s:
            await mark_tickets_dirty(s, [47674])
            await s.commit()
        result = await _tick(factory)
        assert result["dirty"] == 1
        assert await _fact_ids(factory) == {47674}
        async with factory() as s:
            left = (
                await s.execute(
                    text(
                        "SELECT COUNT(*) FROM tiqora_report_dirty_ticket"
                        " WHERE ticket_id BETWEEN :lo AND :hi"
                    ),
                    _BAND,
                )
            ).scalar_one()
        assert left == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_orphan_sweep_removes_deleted_tickets(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        async with factory() as s:
            await mark_tickets_dirty(s, [47675, 47676])
            await s.commit()
        await _tick(factory)  # builds the facts (and sweeps: nothing orphaned yet)
        assert await _fact_ids(factory) == {47675, 47676}
        await _set_state(factory, last_orphan_sweep_at=None)
        _delete_ticket(url, 47675)
        result = await _tick(factory)
        assert result["orphans"] == 1
        assert await _fact_ids(factory) == {47676}
        assert (await _state(factory)).last_orphan_sweep_at == NOW
        _delete_ticket(url, 47676)
        result = await _tick(factory)  # swept a moment ago: no new sweep
        assert result["orphans"] == 0
        assert await _fact_ids(factory) == {47676}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_snapshots_only_after_backfill_and_fill_missing_days(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)

    async def snapshot_days() -> set[date]:
        async with factory() as s:
            return {
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

    try:
        await _prime(url, factory)
        async with factory() as s:
            await mark_tickets_dirty(s, [47677])
            await s.commit()
        await _set_state(factory, last_snapshot_date=date(2024, 6, 5))
        await _tick(factory)  # backfill still pending
        assert await _fact_ids(factory) == {47677}
        assert await snapshot_days() == set()
        assert (await _state(factory)).last_snapshot_date == date(2024, 6, 5)

        await _set_state(factory, backfill_status="done")
        result = await _tick(factory)
        assert result["snapshot_days"] == 2
        assert await snapshot_days() == {date(2024, 6, 6), date(2024, 6, 7)}
        state = await _state(factory)
        assert state.last_snapshot_date == date(2024, 6, 7) and state.snapshot_time_zone
        assert (await _tick(factory))["snapshot_days"] == 0
    finally:
        await engine.dispose()


async def _events(factory: async_sessionmaker[AsyncSession]) -> list[tuple[Any, ...]]:
    async with factory() as s:
        rows = await s.execute(
            select(
                TiqoraReportTicketEvent.ticket_id,
                TiqoraReportTicketEvent.kind,
                TiqoraReportTicketEvent.occurred_at,
                TiqoraReportTicketEvent.from_id,
                TiqoraReportTicketEvent.to_id,
            )
            .where(TiqoraReportTicketEvent.ticket_id.between(LO, HI))
            .order_by(
                TiqoraReportTicketEvent.ticket_id,
                TiqoraReportTicketEvent.occurred_at,
                TiqoraReportTicketEvent.kind,
            )
        )
        return [tuple(r) for r in rows.all()]


async def _noop_progress(done: int, total: int | None, message: str | None) -> None:
    return None


async def _noop() -> None:
    return None


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_backfill_and_tick_concurrently(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    base = _max_history(url)
    _insert_history(url, [(base + 1 + i, tid, "NewTicket") for i, tid in enumerate(TICKETS)])
    engine, factory = _factory(url)
    try:
        await _set_state(factory, last_history_id=base)  # the tick follows the new rows
        results = await asyncio.gather(
            run_backfill(
                factory,
                SETTINGS,
                ReportBackfillPayload(restart=True, snapshots=False),
                progress=_noop_progress,
                check_cancelled=_noop,
                sleep_seconds=0,
                now=NOW,
            ),
            _tick(factory),
            return_exceptions=True,
        )
        assert not [r for r in results if isinstance(r, BaseException)], results
        concurrent = await _events(factory)
        assert concurrent
        async with factory() as s:
            await s.execute(
                text("DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi"),
                _BAND,
            )
            await s.commit()
        from tiqora.reporting.facts_store import rebuild_and_commit

        await rebuild_and_commit(factory, TICKETS, now=NOW)
        assert concurrent == await _events(factory)  # exactly the events of a fresh rebuild
    finally:
        await engine.dispose()


async def _add_job(
    factory: async_sessionmaker[AsyncSession], status: str, finished_at: datetime | None
) -> int:
    async with factory() as s:
        job = TiqoraJob(
            kind="core.report_backfill",
            status=status,
            payload="{}",
            progress_done=0,
            cancel_requested=False,
            dedupe_key="report_backfill",
            created_at=(finished_at or NOW) - timedelta(minutes=5),
            finished_at=finished_at,
        )
        s.add(job)
        await s.flush()
        job_id = int(job.id)
        await s.commit()
    return job_id


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_reaped_backfill_is_requeued_with_backoff_and_resumes(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        first = (await _jobs(factory))[0].id
        # the job was reaped mid-run: state "running" at ticket 47673, job failed long ago
        async with factory() as s:
            await s.execute(text("DELETE FROM tiqora_job WHERE id = :i"), {"i": first})
            await s.commit()
        dead = await _add_job(factory, "failed", NOW - timedelta(hours=1))
        await _set_state(
            factory,
            backfill_status="running",
            backfill_job_id=dead,
            backfill_run_token="run-1",
            backfill_started_at=NOW - timedelta(hours=2),
            backfill_start_ticket_id=47669,
            backfill_cursor_ticket_id=47673,
            backfill_target_ticket_id=47679,
            updated_at=NOW - timedelta(hours=1),
        )
        await _tick(factory)
        state = await _state(factory)
        jobs = await _jobs(factory)
        assert len(jobs) == 2 and state.backfill_job_id == jobs[1].id != dead
        assert json.loads(jobs[1].payload)["restart"] is False
        # the new job resumes from the stored cursor: tickets up to 47673 are not touched
        await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(snapshots=False),
            progress=_noop_progress,
            check_cancelled=_noop,
            sleep_seconds=0,
            now=NOW,
        )
        assert await _fact_ids(factory) == set(range(47674, 47680))
        assert (await _state(factory)).backfill_status == "done"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_backfill_backoff_doubles_with_recent_failures(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Delay is 300 s * 2^(n-1) after the last failure; only failures of the last 24 h count."""
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)

    async def queued(ages: list[int], status: str = "failed") -> bool:
        """Failed jobs that ended ``ages`` seconds before NOW (last one is the stored job)."""
        async with factory() as s:
            await s.execute(text("DELETE FROM tiqora_job WHERE kind = 'core.report_backfill'"))
            await s.commit()
        last = 0
        for age in sorted(ages, reverse=True):
            last = await _add_job(factory, status, NOW - timedelta(seconds=age))
        await _set_state(factory, backfill_job_id=last, backfill_status="pending")
        await _tick(factory)
        return len(await _jobs(factory)) > len(ages)

    try:
        await _prime(url, factory)
        assert await queued([100]) is False  # 1 failure: wait 300 s
        assert await queued([400]) is True
        assert await queued([500, 400]) is False  # 2 failures: wait 600 s
        assert await queued([700, 650]) is True
        assert await queued([90_000, 400]) is True  # the 25 h old failure does not count
        assert await queued([3000, 2000, 1000, 900]) is False  # 4 failures: 2400 s
        six = [80_000, 70_000, 60_000, 50_000, 40_000]
        assert await queued([*six, 3650]) is True  # 6 failures: capped at 3600 s
        assert await queued([*six, 3500]) is False
        # an admin cancelled it: never queued again by the tick
        assert await queued([86_000], status="cancelled") is False
        assert incremental._backfill_retry_delay(1) == timedelta(seconds=300)
        assert incremental._backfill_retry_delay(2) == timedelta(seconds=600)
        assert incremental._backfill_retry_delay(99) == timedelta(seconds=3600)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_cli_run_is_left_alone(url_fixture: str, request: pytest.FixtureRequest) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        async with factory() as s:
            await s.execute(text("DELETE FROM tiqora_job WHERE kind = 'core.report_backfill'"))
            await s.commit()
        await _set_state(
            factory,
            backfill_status="running",
            backfill_job_id=None,
            backfill_run_token="cli",
            backfill_cursor_ticket_id=47672,
            updated_at=NOW - timedelta(seconds=30),
        )
        stamp = NOW - timedelta(seconds=30)
        await _tick(factory)
        assert await _jobs(factory) == []
        assert (await _state(factory)).updated_at == stamp  # ticks never refresh a CLI run
        # ticks keep running while the CLI is silent; they do not keep it "alive"
        await _tick(factory, now=NOW + timedelta(minutes=5))
        assert await _jobs(factory) == []
        assert (await _state(factory)).updated_at == stamp
        await _tick(factory, now=NOW + timedelta(minutes=11))  # 10 min without a write: dead
        jobs = await _jobs(factory)
        assert len(jobs) == 1 and (await _state(factory)).backfill_job_id == jobs[0].id
        assert (await _state(factory)).updated_at == stamp
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_snapshot_phase_keeps_the_run_alive_and_resume_rotates_the_token(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    monkeypatch.setattr(backfill_module, "SNAPSHOT_WINDOW_DAYS", 1)
    engine, factory = _factory(url)
    long_ago = datetime(2000, 1, 1)
    fresh: list[bool] = []

    async def progress(done: int, total: int | None, message: str | None) -> None:
        if message != backfill_module.SNAPSHOTS_MESSAGE:
            return
        async with factory() as s:
            st = await load_state(s, for_update=True)
            fresh.append(st.updated_at > datetime(2001, 1, 1))
            st.updated_at = long_ago  # the next window has to touch the state again
            await s.commit()

    try:
        await run_backfill(
            factory,
            SETTINGS,
            ReportBackfillPayload(since=date(2024, 6, 1)),
            progress=progress,
            check_cancelled=_noop,
            sleep_seconds=0,
            now=NOW,
        )
        assert len(fresh) == 7 and all(fresh)  # 06-01 .. 06-07, one state write per window

        # resume: the token rotates, the previous owner stops, updated_at is fresh
        await _set_state(factory, backfill_status="running", updated_at=long_ago)
        payload = ReportBackfillPayload()
        await _set_state(factory, backfill_run_token="old-owner")
        old = await backfill_module._begin(factory, SETTINGS, ReportBackfillPayload(restart=True))
        new = await backfill_module._begin(factory, SETTINGS, payload)
        assert new.token != old.token
        assert (await _state(factory)).updated_at > datetime(2001, 1, 1)
        with pytest.raises(JobCancelled):
            await backfill_module._save_cursor(factory, old, old.start + 1)
        await backfill_module._save_cursor(factory, new, new.start + 1)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_failing_ticket_is_isolated_and_retried_with_backoff(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    incremental._failing.clear()
    real = incremental.rebuild_and_commit
    attempts: list[list[int]] = []
    broken = {"on": True}

    async def flaky(factory: Any, ids: Any, **kwargs: Any) -> Any:
        ids = list(ids)
        attempts.append(ids)
        if broken["on"] and 47678 in ids:
            raise RuntimeError("poison")
        return await real(factory, ids, **kwargs)

    monkeypatch.setattr(incremental, "rebuild_and_commit", flaky)
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        _insert_history(url, [(wm + 1, 47678, "NewTicket"), (wm + 2, 47679, "NewTicket")])
        result = await _tick(factory)
        assert result["watermark"] == wm + 2  # the poison ticket does not stall the watermark
        assert await _fact_ids(factory) == {47679}
        assert 47678 in incremental._failing

        async def dirty_count() -> int:
            async with factory() as s:
                return int(
                    (
                        await s.execute(
                            text(
                                "SELECT COUNT(*) FROM tiqora_report_dirty_ticket"
                                " WHERE ticket_id = 47678"
                            )
                        )
                    ).scalar_one()
                )

        assert await dirty_count() == 1  # queued for a retry
        attempts.clear()
        await _tick(factory)  # within the back-off: not tried again
        assert [a for a in attempts if 47678 in a] == [] and await dirty_count() == 1
        broken["on"] = False
        await _tick(factory, wall=WALL + 3600)  # back-off over, ticket healthy again
        assert await _fact_ids(factory) == {47678, 47679}
        assert await dirty_count() == 0 and 47678 not in incremental._failing
    finally:
        incremental._failing.clear()
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_failing_dirty_ticket_moves_back_and_failing_upgrade_is_isolated(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.reporting.facts import FACT_VERSION

    url = request.getfixturevalue(url_fixture)
    _seed(url)
    incremental._failing.clear()
    real = incremental.rebuild_and_commit
    broken = {"on": False}

    async def flaky(factory: Any, ids: Any, **kwargs: Any) -> Any:
        ids = list(ids)
        if broken["on"] and 47678 in ids:
            raise RuntimeError("poison")
        return await real(factory, ids, **kwargs)

    monkeypatch.setattr(incremental, "rebuild_and_commit", flaky)
    engine, factory = _factory(url)

    async def queued_at(tid: int) -> datetime | None:
        async with factory() as s:
            return (
                await s.execute(
                    text("SELECT queued_at FROM tiqora_report_dirty_ticket WHERE ticket_id = :t"),
                    {"t": tid},
                )
            ).scalar_one_or_none()

    async def version(tid: int) -> int:
        async with factory() as s:
            return int(
                (
                    await s.execute(
                        text(
                            "SELECT fact_version FROM tiqora_report_ticket_fact"
                            " WHERE ticket_id = :t"
                        ),
                        {"t": tid},
                    )
                ).scalar_one()
            )

    try:
        await _prime(url, factory)
        old_mark = datetime(2024, 6, 1, 9, 0, 0)
        async with factory() as s:
            await mark_tickets_dirty(s, [47671, 47678], now=old_mark)
            await s.commit()
        await _tick(factory)  # healthy: both facts exist, queue empty
        assert await _fact_ids(factory) == {47671, 47678} and await queued_at(47678) is None

        # dirty path: the poison ticket fails again -> fresh queued_at, the other is done
        broken["on"] = True
        async with factory() as s:
            await mark_tickets_dirty(s, [47671, 47678], now=old_mark)
            await s.commit()
        await _tick(factory)
        moved = await queued_at(47678)
        assert moved is not None and moved > old_mark + timedelta(days=1)  # re-marked just now
        assert await queued_at(47671) is None

        # upgrade path: an old fact whose rebuild fails does not stop the others
        incremental._failing.clear()
        await _set_state(factory, backfill_status="done")
        async with factory() as s:
            await s.execute(
                text(
                    "UPDATE tiqora_report_ticket_fact SET fact_version = 0"
                    " WHERE ticket_id IN (47671, 47678)"
                )
            )
            await s.execute(text("DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id = 47678"))
            await s.commit()
        result = await _tick(factory)
        assert result["upgraded"] == 1
        assert await version(47671) == FACT_VERSION and await version(47678) == 0
        assert 47678 in incremental._failing
    finally:
        incremental._failing.clear()
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_version_upgrade_rebuilds_old_facts(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    from tiqora.reporting.facts import FACT_VERSION

    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        async with factory() as s:
            await mark_tickets_dirty(s, [47671])
            await s.commit()
        await _tick(factory)
        async with factory() as s:
            await s.execute(
                text(
                    "UPDATE tiqora_report_ticket_fact SET fact_version = 0 WHERE ticket_id = 47671"
                )
            )
            await s.commit()
        assert (await _tick(factory))["upgraded"] == 0  # backfill not done yet
        await _set_state(factory, backfill_status="done")
        assert (await _tick(factory))["upgraded"] == 1
        async with factory() as s:
            version = (
                await s.execute(
                    text(
                        "SELECT fact_version FROM tiqora_report_ticket_fact WHERE ticket_id = 47671"
                    )
                )
            ).scalar_one()
        assert version == FACT_VERSION
        assert (await _tick(factory))["upgraded"] == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_history_is_read_in_batches_over_several_ticks(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    monkeypatch.setattr(incremental, "HISTORY_BATCH", 2)
    monkeypatch.setattr(incremental, "MAX_BATCHES_PER_TICK", 2)
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        _insert_history(url, [(wm + 1 + i, 47670 + i, "NewTicket") for i in range(5)])
        first = await _tick(factory)
        assert first["history_rows"] == 4 and first["watermark"] == wm + 4
        assert await _fact_ids(factory) == {47670, 47671, 47672, 47673}
        second = await _tick(factory)
        assert second["history_rows"] == 1 and second["watermark"] == wm + 5
        assert await _fact_ids(factory) == {47670, 47671, 47672, 47673, 47674}
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_gap_list_is_capped_at_max_gaps(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    monkeypatch.setattr(incremental, "MAX_GAPS", 3)
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        _insert_history(url, [(wm + 10, 47670, "NewTicket")])
        result = await _tick(factory)
        assert result["gaps"] == 3
        gaps = json.loads((await _state(factory)).gap_ids or "[]")
        assert [g[0] for g in gaps] == [wm + 7, wm + 8, wm + 9]  # the highest ones are kept
        # a second batch cannot grow the list beyond the cap: oldest first_seen goes
        _insert_history(url, [(wm + 14, 47671, "NewTicket")])
        await _tick(factory, wall=WALL + 5)
        gaps = json.loads((await _state(factory)).gap_ids or "[]")
        assert len(gaps) == 3 and {g[0] for g in gaps} == {wm + 11, wm + 12, wm + 13}
    finally:
        await engine.dispose()


def test_missing_ids_honour_auto_increment_increment() -> None:
    # step 3, offset 2: ids 2, 5, 8, 11 are expected; 5 and 11 were returned
    assert incremental._missing_ids(1, 11, {5, 11}, step=3, offset=2) == [8, 2]
    assert incremental._missing_ids(1, 11, {5, 11}) == [10, 9, 8, 7, 6, 4, 3, 2]
    assert incremental._missing_ids(1, 12, {5}, step=3, offset=2) == [11, 8, 2]


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_id_step_is_one_on_the_test_servers(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    engine, factory = _factory(url)
    try:
        assert await incremental._id_step(factory) == (1, 1)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_snapshot_pass_rewrites_the_day_before_yesterday(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        async with factory() as s:
            await mark_tickets_dirty(s, [47677])
            await s.commit()
        await _set_state(factory, backfill_status="done", last_snapshot_date=date(2024, 6, 6))
        async with factory() as s:  # a stale value of the day that is written again
            s.add(
                TiqoraReportDailySnapshot(
                    snapshot_date=date(2024, 6, 6),
                    queue_id=QUEUE,
                    state_type="new",
                    priority_id=3,
                    open_count=99,
                    age_0_1d=0,
                    age_1_3d=0,
                    age_3_7d=99,
                    age_7_30d=0,
                    age_30d_plus=0,
                    built_at=NOW,
                )
            )
            await s.commit()
        result = await _tick(factory)
        assert result["snapshot_days"] == 2  # 06-06 again (late rows) and 06-07
        async with factory() as s:
            counts = {
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
        assert counts == {date(2024, 6, 6): 1, date(2024, 6, 7): 1}
        assert (await _tick(factory))["snapshot_days"] == 0  # not rewritten on every tick
    finally:
        await engine.dispose()


async def test_concurrent_ticks_are_serialised(monkeypatch: pytest.MonkeyPatch) -> None:
    running = {"now": 0, "max": 0}

    async def slow(*args: Any) -> dict[str, int]:
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.05)
        running["now"] -= 1
        return {}

    monkeypatch.setattr(incremental, "_run_tick", slow)
    await asyncio.gather(*(run_report_facts_tick() for _ in range(3)))
    assert running["max"] == 1


async def test_disabled_service_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class _Ctx:
        async def __aenter__(self) -> _Ctx:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    async def disabled(session: object, key: str, default: bool) -> bool:
        seen.append(key)
        assert default is True
        return False

    async def must_not_run(**kwargs: object) -> dict[str, int]:
        raise AssertionError("tick ran although the service is disabled")

    monkeypatch.setattr(worker_module, "get_session_factory", lambda: lambda: _Ctx())
    monkeypatch.setattr(worker_module, "get_setting_bool", disabled)
    monkeypatch.setattr(worker_module, "run_report_facts_tick", must_not_run)
    assert await worker_module.run_report_facts_worker_tick(SETTINGS) == {"skipped": 1}
    assert seen == ["daemon.report_facts.enabled"]
