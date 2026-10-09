"""Jobs: enqueue/dedupe/cancel/purge service and the JobContext helpers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import bindparam, create_engine, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
from tiqora.extensions import Registry
from tiqora.jobs import (
    JOB_HANDLERS,
    MAX_RESULT_CHARS,
    JobCancelled,
    JobContext,
    JobFailed,
    JobHandler,
    JobNotCancellable,
    UnknownJobKind,
    can_view_job,
    enqueue_job,
    get_job,
    get_job_file,
    list_job_files,
    list_jobs,
    purge_finished_jobs,
    request_cancel,
    utc_now,
)
from tiqora.jobs.context import MAX_JOB_FILE_BYTES


class _Payload(BaseModel):
    n: int


async def _noop(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
    return {"n": payload.n}


def _registry() -> Registry:
    reg = Registry()
    reg.add(
        JOB_HANDLERS,
        JobHandler(kind="test.noop", payload_model=_Payload, run=_noop, title_key="x"),
    )
    return reg


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


_URLS: list[str] = []


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        ids = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM tiqora_job WHERE kind LIKE 'test.%'")
            ).fetchall()
        ]
        if ids:
            conn.execute(
                text("DELETE FROM tiqora_job_file WHERE job_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
            conn.execute(
                text("DELETE FROM tiqora_job WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
    engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in set(_URLS):
        _clean(url)
    _URLS.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url: str = request.getfixturevalue(request.param)
    _URLS.append(url)
    _clean(url)
    engine = create_async_engine(_async_url(url))
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()
        _clean(url)


pytestmark = pytest.mark.db


async def test_enqueue_validates_kind_and_payload(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    async with factory() as s:
        with pytest.raises(UnknownJobKind):
            await enqueue_job(s, kind="test.unknown", payload={}, created_by=None, registry=reg)
        with pytest.raises(ValidationError):
            await enqueue_job(
                s, kind="test.noop", payload={"n": "x"}, created_by=None, registry=reg
            )
        job = await enqueue_job(
            s, kind="test.noop", payload=_Payload(n=1), created_by=1, registry=reg
        )
        assert job.id is not None
        assert job.status == "queued"
        assert json.loads(job.payload) == {"n": 1}
        assert job.progress_done == 0
        assert job.cancel_requested is False
        assert job.created_at.tzinfo is None
        assert abs(utc_now() - job.created_at) < timedelta(seconds=5)
        await s.rollback()


async def test_enqueue_does_not_commit(factory: async_sessionmaker[AsyncSession]) -> None:
    reg = _registry()
    async with factory() as s:
        job = await enqueue_job(s, kind="test.noop", payload={"n": 2}, created_by=1, registry=reg)
        job_id = job.id
        await s.rollback()
    async with factory() as s:
        assert await get_job(s, job_id) is None


async def test_dedupe_returns_active_job_but_not_finished_one(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    async with factory() as s:
        a = await enqueue_job(
            s, kind="test.noop", payload={"n": 1}, created_by=1, dedupe_key="k", registry=reg
        )
        b = await enqueue_job(
            s, kind="test.noop", payload={"n": 1}, created_by=1, dedupe_key="k", registry=reg
        )
        assert a.id == b.id
        a.status = "succeeded"
        await s.flush()
        c = await enqueue_job(
            s, kind="test.noop", payload={"n": 1}, created_by=1, dedupe_key="k", registry=reg
        )
        assert c.id != a.id
        await s.rollback()


async def test_request_cancel(factory: async_sessionmaker[AsyncSession]) -> None:
    reg = _registry()
    async with factory() as s:
        queued = await enqueue_job(
            s, kind="test.noop", payload={"n": 1}, created_by=1, registry=reg
        )
        await request_cancel(s, queued)
        assert queued.status == "cancelled"
        assert queued.finished_at is not None

        running = await enqueue_job(
            s, kind="test.noop", payload={"n": 2}, created_by=1, registry=reg
        )
        running.status = "running"
        await request_cancel(s, running)
        assert running.cancel_requested is True
        assert running.status == "running"

        done = await enqueue_job(s, kind="test.noop", payload={"n": 3}, created_by=1, registry=reg)
        done.status = "succeeded"
        with pytest.raises(JobNotCancellable):
            await request_cancel(s, done)
        await s.rollback()


async def test_list_jobs_and_files(factory: async_sessionmaker[AsyncSession]) -> None:
    reg = _registry()
    async with factory() as s:
        mine = await enqueue_job(
            s, kind="test.noop", payload={"n": 1}, created_by=4242, registry=reg
        )
        other = await enqueue_job(
            s, kind="test.noop", payload={"n": 2}, created_by=4243, registry=reg
        )
        s.add(
            TiqoraJobFile(
                job_id=mine.id,
                name="a.csv",
                content_type="text/csv",
                size_bytes=3,
                data=b"a,b",
                created_at=utc_now(),
            )
        )
        await s.flush()
        own = await list_jobs(s, created_by=4242)
        assert [j.id for j in own] == [mine.id]
        everyone = {j.id for j in await list_jobs(s, created_by=None, limit=500)}
        assert {mine.id, other.id} <= everyone
        files = await list_job_files(s, [mine.id, other.id])
        assert [f.name for f in files[mine.id]] == ["a.csv"]
        assert other.id not in files or files[other.id] == []
        fetched = await get_job_file(s, mine.id, files[mine.id][0].id)
        assert fetched is not None and fetched.data == b"a,b"
        assert await get_job_file(s, other.id, files[mine.id][0].id) is None
        await s.rollback()


async def test_purge_finished_jobs_deletes_files_first(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    now = utc_now()
    async with factory() as s:
        old = await enqueue_job(s, kind="test.noop", payload={"n": 1}, created_by=1, registry=reg)
        old.status = "succeeded"
        old.finished_at = now - timedelta(days=20)
        recent = await enqueue_job(
            s, kind="test.noop", payload={"n": 2}, created_by=1, registry=reg
        )
        recent.status = "succeeded"
        recent.finished_at = now - timedelta(days=1)
        running = await enqueue_job(
            s, kind="test.noop", payload={"n": 3}, created_by=1, registry=reg
        )
        running.status = "running"
        await s.flush()
        s.add(
            TiqoraJobFile(
                job_id=old.id,
                name="x.bin",
                content_type="application/octet-stream",
                size_bytes=1,
                data=b"x",
                created_at=now,
            )
        )
        await s.flush()
        ids = (old.id, recent.id, running.id)
        # Other test rows from earlier runs must not count: scope via the ids.
        deleted = await purge_finished_jobs(s, older_than=now - timedelta(days=14))
        assert deleted == 1
        s.expire_all()
        assert await get_job(s, ids[0]) is None
        assert await get_job(s, ids[1]) is not None
        assert await get_job(s, ids[2]) is not None
        assert (await list_job_files(s, [ids[0]])).get(ids[0], []) == []
        await s.rollback()


def test_can_view_job() -> None:
    mine = TiqoraJob(created_by=7)
    system = TiqoraJob(created_by=None)
    assert can_view_job(mine, user_id=7, is_admin=False)
    assert not can_view_job(mine, user_id=8, is_admin=False)
    assert can_view_job(mine, user_id=8, is_admin=True)
    assert not can_view_job(system, user_id=7, is_admin=False)
    assert can_view_job(system, user_id=7, is_admin=True)


def test_constants() -> None:
    assert MAX_RESULT_CHARS == 1_000_000
    assert MAX_JOB_FILE_BYTES == 200 * 1024 * 1024


async def _ctx(
    factory: async_sessionmaker[AsyncSession], job_id: int, *, created_by: int | None = 1
) -> JobContext:
    return JobContext(
        job_id=job_id,
        kind="test.noop",
        created_by=created_by,
        settings=get_settings(),
        session_factory=factory,
    )


async def _committed_job(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as s:
        job = await enqueue_job(
            s, kind="test.noop", payload={"n": 1}, created_by=1, registry=_registry()
        )
        job.status = "running"
        await s.commit()
        return job.id


async def test_context_progress_throttles_and_writes_heartbeat(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _committed_job(factory)
    ctx = await _ctx(factory, job_id)
    await ctx.progress(1, 10, "eins")
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None
        assert (job.progress_done, job.progress_total, job.progress_message) == (1, 10, "eins")
        assert job.heartbeat_at is not None
    await ctx.progress(2, 10, "zwei")  # within 1 s, done < total: dropped
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None
        assert (job.progress_done, job.progress_message) == (1, "eins")
    await ctx.progress(10, 10, "fertig")  # done == total always writes
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None
        assert (job.progress_done, job.progress_message) == (10, "fertig")


async def test_context_check_cancelled(factory: async_sessionmaker[AsyncSession]) -> None:
    job_id = await _committed_job(factory)
    ctx = await _ctx(factory, job_id)
    await ctx.check_cancelled()
    async with factory() as s:
        await s.execute(
            update(TiqoraJob).where(TiqoraJob.id == job_id).values(cancel_requested=True)
        )
        await s.commit()
    with pytest.raises(JobCancelled):
        await ctx.check_cancelled()
    with pytest.raises(JobCancelled):
        await ctx.progress(1, 5)


async def test_context_add_file(factory: async_sessionmaker[AsyncSession]) -> None:
    job_id = await _committed_job(factory)
    ctx = await _ctx(factory, job_id)
    file_id = await ctx.add_file("r.txt", "text/plain", b"hello")
    async with factory() as s:
        row = await get_job_file(s, job_id, file_id)
        assert row is not None
        assert (row.name, row.content_type, row.size_bytes, row.data) == (
            "r.txt",
            "text/plain",
            5,
            b"hello",
        )
    with pytest.raises(JobFailed):
        await ctx.add_file("big", "x/y", b"\0" * (MAX_JOB_FILE_BYTES + 1))


async def test_request_cancel_after_concurrent_claim_sets_cancel_requested(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _committed_job(factory)  # committed as running by helper
    async with factory() as other:
        await other.execute(update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="queued"))
        await other.commit()
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None and job.status == "queued"  # stale view
        async with factory() as claimer:
            await claimer.execute(
                update(TiqoraJob).where(TiqoraJob.id == job_id).values(status="running")
            )
            await claimer.commit()
        await request_cancel(s, job)
        assert job.status == "running"
        assert job.cancel_requested is True
        await s.commit()
    async with factory() as s:
        row = await get_job(s, job_id)
        assert row is not None
        assert (row.status, row.cancel_requested) == ("running", True)


async def test_user_job_quota_lock_serialises_but_lets_fk_checks_through(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """T16: the cap lock conflicts with itself; on PostgreSQL it is FOR NO KEY UPDATE, so
    the FOR KEY SHARE lock of a foreign-key check on that user is not blocked."""
    from sqlalchemy import event

    from tiqora.jobs.service import lock_user_job_quota

    engine = factory.kw["bind"]
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with factory() as holder:
            await lock_user_job_quota(holder, 1)  # Znuny seed admin
            lock_sql = next(st for st in seen if "FROM users" in st)
            if engine.dialect.name != "postgresql":
                assert lock_sql.rstrip().endswith("FOR UPDATE")
                return
            assert lock_sql.rstrip().endswith("FOR NO KEY UPDATE")
            async with factory() as other:
                await other.execute(text("SET LOCAL lock_timeout = '2s'"))
                # what PostgreSQL's FK trigger takes on users(id) for an insert
                await other.execute(text("SELECT id FROM users WHERE id = 1 FOR KEY SHARE"))
                await other.rollback()
            async with factory() as other:
                await other.execute(text("SET LOCAL lock_timeout = '1s'"))
                with pytest.raises(Exception, match="lock timeout"):
                    await lock_user_job_quota(other, 1)  # a second cap check waits
                await other.rollback()
            await holder.rollback()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_count_active_jobs_can_leave_out_cancelling_jobs(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    from tiqora.jobs.service import count_active_jobs

    reg = _registry()
    async with factory() as s:
        ids = [
            (
                await enqueue_job(s, kind="test.noop", payload={"n": i}, created_by=1, registry=reg)
            ).id
            for i in range(3)
        ]
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == ids[0])
            .values(status="running", cancel_requested=True)
        )
        await s.commit()
        assert await count_active_jobs(s, kind="test.noop", created_by=1) == 3
        assert (
            await count_active_jobs(s, kind="test.noop", created_by=1, include_cancelling=False)
            == 2
        )
