"""``enqueue_job(..., due_at=)``: a job is not claimed before its due time; GI job lanes.

Job ids are autoincrement: the rows this module adds are deleted via ``tests._row_cleanup``
marks (band 51000-51019 holds no seeded rows here).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import Registry
from tiqora.jobs import JOB_HANDLERS, JobContext, JobHandler, enqueue_job, get_job, utc_now
from tiqora.jobs.registry import lane_slots
from tiqora.jobs.runner import JobRunner

_SETTINGS = Settings(environment="test")
KIND = "test.gi_due"


class _Payload(BaseModel):
    n: int = 0


async def _run(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
    return {"n": payload.n}


def _registry() -> Registry:
    reg = Registry()
    reg.add(
        JOB_HANDLERS,
        JobHandler(kind=KIND, payload_model=_Payload, run=_run, title_key="x", lane="gi"),
    )
    return reg


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_enqueue_with_due_at_is_not_claimed_early(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    sync_engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(sync_engine, tables=[TiqoraJob.__table__])
    finally:
        sync_engine.dispose()
    marks = snapshot_marks(sync_url, ("tiqora_job",))
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    reg = _registry()
    runner = JobRunner(session_factory=factory, settings=_SETTINGS, registry=reg, worker_id="t:gi")
    try:
        due = utc_now() + timedelta(seconds=60)
        async with factory() as session:
            job = await enqueue_job(
                session, kind=KIND, payload={"n": 5}, created_by=1, due_at=due, registry=reg
            )
            await session.commit()
            job_id = job.id

        async with factory() as session:
            row = await get_job(session, job_id)
            assert row is not None
            assert row.status == "queued"
            assert row.due_at is not None
            assert abs((row.due_at - due).total_seconds()) < 1

        # not due yet: the runner's claim query leaves it alone
        assert (await runner.run_once())["started"] == 0
        async with factory() as session:
            row = await get_job(session, job_id)
            assert row is not None and row.status == "queued"

        async with factory() as session:
            await session.execute(
                update(TiqoraJob)
                .where(TiqoraJob.id == job_id)
                .values(due_at=utc_now() - timedelta(seconds=1))
            )
            await session.commit()
        assert (await runner.run_once())["started"] == 1
        await runner.wait_idle()
        async with factory() as session:
            row = await get_job(session, job_id)
            assert row is not None
            assert row.status == "succeeded"
            assert json.loads(row.result or "{}") == {"n": 5}
    finally:
        await runner.wait_idle()
        await engine.dispose()
        delete_above_marks(sync_url, marks)


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_enqueue_due_at_tz_aware_is_stored_as_naive_utc(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    sync_engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(sync_engine, tables=[TiqoraJob.__table__])
    finally:
        sync_engine.dispose()
    marks = snapshot_marks(sync_url, ("tiqora_job",))
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    reg = _registry()
    try:
        from zoneinfo import ZoneInfo

        aware = datetime(2030, 7, 1, 14, 30, tzinfo=ZoneInfo("Europe/Berlin"))  # CEST = UTC+2
        async with factory() as session:
            job = await enqueue_job(
                session, kind=KIND, payload={}, created_by=1, due_at=aware, registry=reg
            )
            assert job.due_at == datetime(2030, 7, 1, 12, 30)
            await session.commit()
            job_id = job.id
        async with factory() as session:
            row = await get_job(session, job_id)
            assert row is not None
            assert row.due_at == datetime(2030, 7, 1, 12, 30)
            assert row.due_at.tzinfo is None
        # a naive value is taken as UTC unchanged
        async with factory() as session:
            naive = await enqueue_job(
                session,
                kind=KIND,
                payload={},
                created_by=1,
                due_at=datetime(2030, 7, 1, 12, 30, tzinfo=UTC).replace(tzinfo=None),
                registry=reg,
            )
            assert naive.due_at == datetime(2030, 7, 1, 12, 30)
            await session.rollback()
    finally:
        await engine.dispose()
        delete_above_marks(sync_url, marks)


def test_gi_lanes() -> None:
    assert lane_slots("gi", 4) == 2 and lane_slots("gi_sync", 4) == 1
