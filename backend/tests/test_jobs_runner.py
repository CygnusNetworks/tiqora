"""Jobs: the worker-side runner (claim, heartbeat, cancel, timeout, reap, shutdown, purge)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import bindparam, create_engine, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import Registry
from tiqora.jobs import (
    JOB_HANDLERS,
    MAX_RESULT_CHARS,
    JobContext,
    JobFailed,
    JobHandler,
    enqueue_job,
    get_job,
    request_cancel,
    utc_now,
)
from tiqora.jobs.runner import (
    FAILED_MESSAGE,
    INTERRUPTED_MESSAGE,
    RESULT_TOO_LARGE_MESSAGE,
    TIMEOUT_MESSAGE,
    UNKNOWN_KIND_MESSAGE,
    JobRunner,
)
from tiqora.jobs.settings import JOBS_SECTION
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

_SETTINGS = Settings(environment="test")


class _Payload(BaseModel):
    n: int = 0


Run = Callable[[JobContext, Any], Awaitable[dict[str, Any] | None]]


async def _noop(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
    return {"n": payload.n}


def _handler(kind: str, run: Run, timeout_seconds: int = 3600) -> JobHandler:
    return JobHandler(
        kind=kind,
        payload_model=_Payload,
        run=run,
        title_key="x",
        timeout_seconds=timeout_seconds,
    )


def _registry(*extra: JobHandler) -> Registry:
    reg = Registry()
    reg.add(JOB_HANDLERS, _handler("test.noop", _noop))
    for h in extra:
        reg.add(JOB_HANDLERS, h)
    return reg


def _async_url(url: str) -> str:
    return (
        url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


_URLS: list[str] = []
_KEY_COL = {"mysql": "`key`", "mariadb": "`key`", "postgresql": '"key"'}


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
        conn.execute(
            text("DELETE FROM tiqora_settings WHERE " + _KEY_COL[conn.dialect.name] + " = :k"),
            {"k": JOBS_SECTION.storage_key("max_concurrency")},
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


def _runner(
    factory: async_sessionmaker[AsyncSession], reg: Registry, worker_id: str = "t:1"
) -> JobRunner:
    return JobRunner(session_factory=factory, settings=_SETTINGS, registry=reg, worker_id=worker_id)


async def _enqueue(
    factory: async_sessionmaker[AsyncSession], reg: Registry, kind: str = "test.noop", n: int = 0
) -> int:
    async with factory() as s:
        job = await enqueue_job(s, kind=kind, payload={"n": n}, created_by=1, registry=reg)
        await s.commit()
        return job.id


async def _fetch(factory: async_sessionmaker[AsyncSession], job_id: int) -> TiqoraJob:
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None
        return job


async def _wait_for(predicate: Callable[[], Awaitable[bool]], limit: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while not await predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.05)


async def test_runner_executes_job_and_stores_result(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    job_id = await _enqueue(factory, reg, n=3)
    runner = _runner(factory, reg)
    out = await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert out["started"] == 1
    assert job.status == "succeeded"
    assert job.result is not None
    assert json.loads(job.result) == {"n": 3}
    assert job.error is None
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.worker_id == "t:1"


async def test_handler_exception_marks_failed_and_runner_survives(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def boom(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        raise RuntimeError("boom")

    reg = _registry(_handler("test.boom", boom))
    bad = await _enqueue(factory, reg, kind="test.boom")
    good = await _enqueue(factory, reg, n=7)
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    bad_job = await _fetch(factory, bad)
    assert bad_job.status == "failed"
    assert bad_job.error == FAILED_MESSAGE
    assert "boom" not in (bad_job.error or "")
    assert (await _fetch(factory, good)).status == "succeeded"


async def test_job_failed_message_is_shown(factory: async_sessionmaker[AsyncSession]) -> None:
    async def nothing(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        raise JobFailed("Keine Tickets gefunden.")

    reg = _registry(_handler("test.nothing", nothing))
    job_id = await _enqueue(factory, reg, kind="test.nothing")
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == "Keine Tickets gefunden."


async def test_timeout_marks_failed(factory: async_sessionmaker[AsyncSession]) -> None:
    async def slow(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {}

    reg = _registry(_handler("test.slow", slow, timeout_seconds=1))
    job_id = await _enqueue(factory, reg, kind="test.slow")
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == TIMEOUT_MESSAGE


async def test_cancel_while_running(factory: async_sessionmaker[AsyncSession]) -> None:
    async def loop(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        for i in range(1000):
            await ctx.progress(i, 1000)
            await asyncio.sleep(0.05)
        return {}

    reg = _registry(_handler("test.loop", loop))
    job_id = await _enqueue(factory, reg, kind="test.loop")
    runner = _runner(factory, reg)
    await runner.run_once()

    async def has_progress() -> bool:
        return (await _fetch(factory, job_id)).progress_total == 1000

    await _wait_for(has_progress)
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None
        await request_cancel(s, job)
        await s.commit()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "cancelled"
    assert job.finished_at is not None


async def test_stale_running_job_is_failed_as_interrupted(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    job_id = await _enqueue(factory, reg)
    old = utc_now() - timedelta(minutes=10)
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job_id)
            .values(status="running", worker_id="other:1", started_at=old, heartbeat_at=old)
        )
        await s.commit()
    runner = _runner(factory, reg)
    out = await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert out["reaped"] == 1
    assert job.status == "failed"
    assert job.error == INTERRUPTED_MESSAGE
    assert job.finished_at is not None


async def test_reaped_job_is_not_overwritten_by_late_finish(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def blocked(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        started.set()
        await release.wait()
        return {"late": True}

    reg = _registry(_handler("test.blocked", blocked))
    job_id = await _enqueue(factory, reg, kind="test.blocked")
    runner_a = _runner(factory, reg, "a:1")
    await runner_a.run_once()
    await asyncio.wait_for(started.wait(), 5)
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job_id)
            .values(heartbeat_at=utc_now() - timedelta(minutes=10))
        )
        await s.commit()
    runner_b = _runner(factory, reg, "b:1")
    out = await runner_b.run_once()
    assert out["reaped"] == 1
    release.set()
    await runner_a.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == INTERRUPTED_MESSAGE
    assert job.result is None


async def test_concurrency_limit_from_settings(factory: async_sessionmaker[AsyncSession]) -> None:
    release = asyncio.Event()

    async def blocked(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        await release.wait()
        return {"n": payload.n}

    reg = _registry(_handler("test.blocked", blocked))
    async with factory() as s:
        await save_section_values(
            s, JOBS_SECTION, {"max_concurrency": 1}, secret_key=_SETTINGS.secret_key
        )
        await s.commit()
    ids = [await _enqueue(factory, reg, kind="test.blocked", n=i) for i in range(3)]
    runner = _runner(factory, reg)
    first = await runner.run_once()
    assert first["started"] == 1
    again = await runner.run_once()
    assert again["started"] == 0
    assert again["running"] == 1
    release.set()
    await runner.wait_idle()
    nxt = await runner.run_once()
    assert nxt["started"] == 1
    await runner.wait_idle()
    statuses = [(await _fetch(factory, i)).status for i in ids]
    assert statuses == ["succeeded", "succeeded", "queued"]


async def test_unknown_kind_fails_cleanly(factory: async_sessionmaker[AsyncSession]) -> None:
    reg = _registry()
    async with factory() as s:
        job = await enqueue_job(s, kind="test.noop", payload={"n": 1}, created_by=1, registry=reg)
        job.kind = "test.gone"
        await s.commit()
        job_id = job.id
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == UNKNOWN_KIND_MESSAGE


async def test_result_too_large(factory: async_sessionmaker[AsyncSession]) -> None:
    async def huge(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        return {"x": "a" * (MAX_RESULT_CHARS + 1)}

    reg = _registry(_handler("test.huge", huge))
    job_id = await _enqueue(factory, reg, kind="test.huge")
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == RESULT_TOO_LARGE_MESSAGE
    assert job.result is None


async def test_shutdown_marks_unfinished_jobs_interrupted(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    started = asyncio.Event()

    async def forever(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return {}

    reg = _registry(_handler("test.forever", forever))
    job_id = await _enqueue(factory, reg, kind="test.forever")
    queued_id = await _enqueue(factory, reg)
    runner = _runner(factory, reg)
    await runner.run_once()
    await asyncio.wait_for(started.wait(), 5)
    await runner.shutdown(grace_seconds=0.1)
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == INTERRUPTED_MESSAGE
    assert job.finished_at is not None
    assert runner.active_count == 0
    # a stopped runner claims nothing new
    out = await runner.run_once()
    assert out["started"] == 0
    assert (await _fetch(factory, queued_id)).status in ("queued", "succeeded")


async def test_purge_runs_from_run_once(factory: async_sessionmaker[AsyncSession]) -> None:
    reg = _registry()
    job_id = await _enqueue(factory, reg)
    old = utc_now() - timedelta(days=30)
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job_id)
            .values(status="succeeded", started_at=old, finished_at=old)
        )
        await s.commit()
    runner = _runner(factory, reg)
    out = await runner.run_once()
    assert out["purged"] == 1
    async with factory() as s:
        assert await get_job(s, job_id) is None
