"""Report scheduler loop, run purge and daemon catalogue (plan D13).

Both dialects. Band 52260-52299 (names contain ``rep52``): schedule owners 52260 and 52261
(no Znuny user rows needed - the tick only reads ``tiqora_report_schedule``). Everything
this module writes (schedules, runs and files, delivery jobs, daemon switches) is deleted
again.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraReportRun,
    TiqoraReportRunFile,
    TiqoraReportSchedule,
    TiqoraReportScheduleItem,
    TiqoraReportScheduleRecipient,
)
from tiqora.jobs.service import utc_now
from tiqora.reporting.scheduler import TICK_LIMIT, purge_report_runs, run_report_scheduler_tick
from tiqora.reporting.schedules import load_schedule
from tiqora.worker import report_scheduler as worker_module
from tiqora.worker.services import DAEMON_SERVICES

pytestmark = pytest.mark.db

LO, HI = 52260, 52299
OWNER, OTHER = 52260, 52261
NOW = datetime(2026, 10, 12, 5, 0, 30)  # Monday 07:00:30 Berlin
DAILY = '{"kind": "daily", "time": "07:00"}'
_BAND = {"lo": LO, "hi": HI}
_RESET = (
    "DELETE FROM tiqora_report_run_file WHERE run_id IN"
    " (SELECT id FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE created_by BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_job WHERE created_by BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_schedule_recipient WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule_item WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'daemon.report_%'",
)
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    key = "`key`" if engine.dialect.name == "mysql" else '"key"'
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.replace("{key}", key)), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _RESET)
    _URLS.clear()


class Env:
    def __init__(self, sync_url: str, factory: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.settings = Settings(environment="test")

    def sql(self, *statements: str) -> None:
        _run_sql(self.sync_url, statements)

    async def make_schedule(
        self,
        *,
        owner: int = OWNER,
        recurrence: dict[str, Any] | None = None,
        recurrence_raw: str | None = None,
        next_run_at: datetime | None,
        active: bool = True,
        time_zone: str = "Europe/Berlin",
    ) -> int:
        raw = recurrence_raw
        if raw is None:
            raw = DAILY if recurrence is None else _json(recurrence)
        async with self.factory() as s:
            row = TiqoraReportSchedule(
                owner_user_id=owner,
                name="rep52 Zeitplan",
                format="csv",
                recurrence=raw,
                time_zone=time_zone,
                language="de",
                subject="",
                body="",
                active=active,
                next_run_at=next_run_at,
                created_at=NOW,
                changed_at=NOW,
            )
            s.add(row)
            await s.flush()
            s.add(TiqoraReportScheduleItem(schedule_id=row.id, position=0, report_key="builtin.x"))
            s.add(TiqoraReportScheduleRecipient(schedule_id=row.id, kind="user", ref_id=owner))
            await s.commit()
            return int(row.id)

    async def load_schedule(self, sid: int) -> Any:
        async with self.factory() as s:
            schedule = await load_schedule(s, sid)
            assert schedule is not None
            return schedule

    async def load_runs(self, sid: int) -> list[TiqoraReportRun]:
        async with self.factory() as s:
            return list(
                (
                    await s.execute(
                        select(TiqoraReportRun)
                        .where(TiqoraReportRun.schedule_id == sid)
                        .order_by(TiqoraReportRun.id)
                    )
                ).scalars()
            )

    async def load_job(self, job_id: int | None) -> TiqoraJob:
        async with self.factory() as s:
            return (await s.execute(select(TiqoraJob).where(TiqoraJob.id == job_id))).scalar_one()

    async def count_jobs(self, *, kind: str, created_by: int) -> int:
        async with self.factory() as s:
            rows = (
                await s.execute(
                    select(TiqoraJob.id).where(
                        TiqoraJob.kind == kind, TiqoraJob.created_by == created_by
                    )
                )
            ).all()
            return len(rows)

    async def tick(self, now: datetime = NOW) -> dict[str, int]:
        return await run_report_scheduler_tick(
            settings=self.settings, session_factory=self.factory, now=now
        )


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    _run_sql(sync_url, _RESET)
    try:
        yield Env(sync_url, factory)
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


async def test_due_schedule_enqueues_and_advances(env: Env) -> None:
    sid = await env.make_schedule(next_run_at=datetime(2026, 10, 12, 5, 0))
    out = await env.tick()
    assert out["enqueued"] == 1 and out["due"] == 1 and out["errors"] == 0
    sched = await env.load_schedule(sid)
    assert sched.next_run_at == datetime(2026, 10, 13, 5, 0)
    runs = await env.load_runs(sid)
    assert len(runs) == 1
    assert runs[0].run_kind == "schedule" and runs[0].due_at == datetime(2026, 10, 12, 5, 0)
    assert runs[0].status == "queued" and runs[0].owner_user_id == OWNER
    job = await env.load_job(runs[0].job_id)
    assert job.kind == "core.report_delivery"
    assert job.dedupe_key == f"report_schedule:{sid}:20261012T0500"
    assert job.created_by == OWNER


async def test_downtime_runs_one_catch_up(env: Env) -> None:
    sid = await env.make_schedule(next_run_at=datetime(2026, 10, 7, 5, 0))  # 5 days missed
    await env.tick()
    assert len(await env.load_runs(sid)) == 1
    assert (await env.load_schedule(sid)).next_run_at == datetime(2026, 10, 13, 5, 0)
    await env.tick()
    assert len(await env.load_runs(sid)) == 1


async def test_concurrent_ticks_enqueue_once(env: Env) -> None:
    sid = await env.make_schedule(next_run_at=datetime(2026, 10, 12, 5, 0))
    await asyncio.gather(*(env.tick() for _ in range(3)))
    assert len(await env.load_runs(sid)) == 1
    assert await env.count_jobs(kind="core.report_delivery", created_by=OWNER) == 1


async def test_inactive_and_future_are_ignored(env: Env) -> None:
    inactive = await env.make_schedule(next_run_at=datetime(2026, 10, 12, 5, 0), active=False)
    future = await env.make_schedule(next_run_at=datetime(2026, 10, 12, 5, 1))
    out = await env.tick()
    assert out["due"] == 0 and out["enqueued"] == 0
    assert await env.load_runs(inactive) == [] and await env.load_runs(future) == []
    assert (await env.load_schedule(future)).next_run_at == datetime(2026, 10, 12, 5, 1)


async def test_broken_recurrence_pauses(env: Env) -> None:
    sid = await env.make_schedule(
        recurrence_raw='{"kind": "hourly"}', next_run_at=datetime(2026, 10, 12, 5, 0)
    )
    out = await env.tick()
    s = await env.load_schedule(sid)
    assert out["paused"] == 1 and out["enqueued"] == 0
    assert s.active is False and s.next_run_at is None
    assert s.paused_reason.startswith("Zeitplan ungültig")
    assert await env.load_runs(sid) == []


async def test_invalid_zone_pauses_and_does_not_stop_the_tick(env: Env) -> None:
    bad = await env.make_schedule(
        time_zone="Nowhere/Land", next_run_at=datetime(2026, 10, 12, 5, 0)
    )
    good = await env.make_schedule(owner=OTHER, next_run_at=datetime(2026, 10, 12, 5, 0))
    out = await env.tick()
    assert out["paused"] + out["errors"] == 1 and out["enqueued"] == 1
    assert len(await env.load_runs(good)) == 1
    assert len(await env.load_runs(bad)) == 0


async def test_tick_limit(env: Env) -> None:
    for _ in range(TICK_LIMIT + 2):
        await env.make_schedule(next_run_at=datetime(2026, 10, 12, 5, 0))
    out = await env.tick()
    assert out["due"] == TICK_LIMIT and out["enqueued"] == TICK_LIMIT
    out = await env.tick()
    assert out["due"] == 2 and out["enqueued"] == 2


async def _insert_run(
    env: Env, *, status: str, created: datetime, finished: datetime | None, with_file: bool
) -> int:
    async with env.factory() as s:
        run = TiqoraReportRun(
            schedule_id=None,
            schedule_name="rep52 purge",
            owner_user_id=OWNER,
            run_kind="schedule",
            owner_only=False,
            status=status,
            report_keys="[]",
            created_at=created,
            finished_at=finished,
        )
        s.add(run)
        await s.flush()
        if with_file:
            s.add(
                TiqoraReportRunFile(
                    run_id=run.id,
                    name="a.csv",
                    content_type="text/csv",
                    size_bytes=1,
                    data=b"x",
                    created_at=created,
                )
            )
        await s.commit()
        return int(run.id)


async def _existing_runs(env: Env, ids: list[int]) -> set[int]:
    async with env.factory() as s:
        rows = (
            await s.execute(select(TiqoraReportRun.id).where(TiqoraReportRun.id.in_(ids)))
        ).scalars()
        return {int(i) for i in rows}


async def _file_count(env: Env, ids: list[int]) -> int:
    async with env.factory() as s:
        rows = (
            await s.execute(
                select(TiqoraReportRunFile.id).where(TiqoraReportRunFile.run_id.in_(ids))
            )
        ).all()
        return len(rows)


async def test_purge_report_runs(env: Env) -> None:
    now = utc_now()
    old = now - timedelta(days=100)
    recent = now - timedelta(days=10)
    done_old = await _insert_run(env, status="succeeded", created=old, finished=old, with_file=True)
    done_recent = await _insert_run(
        env, status="succeeded", created=recent, finished=recent, with_file=True
    )
    stuck_old = await _insert_run(env, status="queued", created=old, finished=None, with_file=False)
    queued_recent = await _insert_run(
        env, status="queued", created=recent, finished=None, with_file=False
    )
    ids = [done_old, done_recent, stuck_old, queued_recent]
    async with env.factory() as s:
        purged = await purge_report_runs(s, older_than=now - timedelta(days=90))
        await s.commit()
    assert purged >= 2
    assert await _existing_runs(env, ids) == {done_recent, queued_recent}
    assert await _file_count(env, [done_old]) == 0
    assert await _file_count(env, [done_recent]) == 1


async def test_worker_wrappers_respect_enabled_flag(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker_module, "get_session_factory", lambda: env.factory)
    sid = await env.make_schedule(next_run_at=datetime(2026, 10, 12, 5, 0))
    env.sql(
        "INSERT INTO tiqora_settings ({key}, value)"
        " VALUES ('daemon.report_scheduler.enabled', '0')",
        "INSERT INTO tiqora_settings ({key}, value)"
        " VALUES ('daemon.report_runs_purge.enabled', '0')",
    )
    old = utc_now() - timedelta(days=400)
    run = await _insert_run(env, status="succeeded", created=old, finished=old, with_file=False)
    assert await worker_module.run_report_scheduler_worker_tick(env.settings) == {"skipped": 1}
    assert await worker_module.run_report_runs_purge_tick(env.settings) == {"skipped": 1}
    assert await env.load_runs(sid) == []
    assert await _existing_runs(env, [run]) == {run}
    env.sql("DELETE FROM tiqora_settings WHERE {key} LIKE 'daemon.report_%'")
    # default ON: the purge removes the 400-day-old run
    purged = await worker_module.run_report_runs_purge_tick(env.settings)
    assert purged["purged"] >= 1
    assert await _existing_runs(env, [run]) == set()


def test_daemon_catalogue_entries() -> None:
    slugs = {s.slug: s for s in DAEMON_SERVICES}
    assert slugs["report_scheduler"].schedule_kind == "interval"
    assert slugs["report_scheduler"].toggleable
    assert slugs["report_scheduler"].interval_settings_attr == "report_scheduler_interval_seconds"
    assert slugs["report_runs_purge"].schedule_kind == "daily"
    assert slugs["report_runs_purge"].daily_at == "03:45"
    assert slugs["report_runs_purge"].toggleable
