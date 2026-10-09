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
    " (SELECT id FROM ticket WHERE id < :lo AND create_time >= '2024-05-31')",
    # the backfill also builds the Znuny seed ticket (created "now")
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi OR ticket_id IN"
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


async def _tick(factory: async_sessionmaker[AsyncSession], *, wall: float = WALL) -> dict[str, int]:
    return await run_report_facts_tick(
        settings=SETTINGS,
        session_factory=factory,
        now=NOW,
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
async def test_old_gaps_expire(url_fixture: str, request: pytest.FixtureRequest) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        wm = await _prime(url, factory)
        old, fresh = wm + 5, wm + 6
        await _set_state(
            factory,
            gap_ids=json.dumps([[old, WALL - GAP_TTL_SECONDS - 1], [fresh, WALL - 10]]),
        )
        result = await _tick(factory)
        assert [g[0] for g in json.loads((await _state(factory)).gap_ids or "[]")] == [fresh]
        assert result["gaps"] == 1
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
async def test_failing_backfill_backs_off_and_cli_run_is_left_alone(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    url = request.getfixturevalue(url_fixture)
    _seed(url)
    engine, factory = _factory(url)
    try:
        await _prime(url, factory)
        async with factory() as s:
            await s.execute(text("DELETE FROM tiqora_job WHERE kind = 'core.report_backfill'"))
            await s.commit()
        # one fresh failure: wait the base delay (300 s)
        j1 = await _add_job(factory, "failed", NOW - timedelta(seconds=60))
        await _set_state(factory, backfill_job_id=j1)
        await _tick(factory)
        assert [j.id for j in await _jobs(factory)] == [j1]
        # ... which has passed 400 s later; with two failures the wait is 600 s
        j0 = await _add_job(factory, "cancelled", NOW - timedelta(seconds=500))
        await _set_state(factory, backfill_job_id=j1)
        await _tick(factory)  # NOW - 60 s + 600 s is still ahead
        assert {j.id for j in await _jobs(factory)} == {j0, j1}
        assert incremental._backfill_retry_delay(1) < incremental._backfill_retry_delay(2)
        assert incremental._backfill_retry_delay(99) == timedelta(
            seconds=incremental.BACKFILL_RETRY_MAX_SECONDS
        )
        async with factory() as s:
            await s.execute(text("DELETE FROM tiqora_job WHERE id IN (:a, :b)"), {"a": j0, "b": j1})
            await s.commit()
        j2 = await _add_job(factory, "failed", NOW - timedelta(seconds=400))
        await _set_state(factory, backfill_job_id=j2)
        await _tick(factory)  # single failure 400 s ago: due again
        assert [j.id for j in await _jobs(factory)][0] == j2
        assert len(await _jobs(factory)) == 2

        # a CLI run: running, no job id, state written a moment ago -> no job
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
        await _tick(factory)
        assert await _jobs(factory) == []
        # ... but a long-silent one is resumed
        await _set_state(factory, updated_at=NOW - timedelta(hours=1))
        await _tick(factory)
        assert len(await _jobs(factory)) == 1
    finally:
        await engine.dispose()


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
