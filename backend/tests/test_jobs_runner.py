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
    JobRetry,
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


def _handler(kind: str, run: Run, timeout_seconds: int = 3600, **extra: Any) -> JobHandler:
    return JobHandler(
        kind=kind,
        payload_model=_Payload,
        run=run,
        title_key="x",
        timeout_seconds=timeout_seconds,
        **extra,
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
    queued_id = await _enqueue(factory, reg)
    out = await runner.run_once()
    assert out["started"] == 0
    assert (await _fetch(factory, queued_id)).status == "queued"


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


async def test_two_runners_claim_a_single_job_exactly_once(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    job_id = await _enqueue(factory, reg, n=5)
    a = _runner(factory, reg, "a:1")
    b = _runner(factory, reg, "b:1")
    out_a, out_b = await asyncio.gather(a.run_once(), b.run_once())
    await asyncio.gather(a.wait_idle(), b.wait_idle())
    assert out_a["started"] + out_b["started"] == 1
    job = await _fetch(factory, job_id)
    assert job.status == "succeeded"
    assert job.worker_id in ("a:1", "b:1")


async def test_running_job_without_heartbeat_is_reaped_by_started_at(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry()
    job_id = await _enqueue(factory, reg)
    old = utc_now() - timedelta(minutes=10)
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job_id)
            .values(status="running", worker_id="other:1", started_at=old, heartbeat_at=None)
        )
        await s.commit()
    out = await _runner(factory, reg).run_once()
    assert out["reaped"] == 1
    assert (await _fetch(factory, job_id)).status == "failed"


# --- retries, interruption requeue, checkpoint, lanes --------------------------------


async def _force_running(
    factory: async_sessionmaker[AsyncSession],
    job_id: int,
    *,
    attempts: int,
    checkpoint: str | None,
) -> None:
    """Make a queued job look like a claim of a dead worker (no heartbeat for 10 min)."""
    old = utc_now() - timedelta(minutes=10)
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job_id)
            .values(
                status="running",
                worker_id="dead:1",
                started_at=old,
                heartbeat_at=old,
                attempts=attempts,
                checkpoint=checkpoint,
            )
        )
        await s.commit()


async def test_job_retry_requeues_with_due_time_and_runs_again(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def flaky(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        if ctx.attempt == 1:
            raise JobRetry("Neuer Versuch später.", delay_seconds=3600)
        return {"attempt": ctx.attempt}

    reg = _registry(_handler("test.flaky", flaky, max_attempts=3))
    job_id = await _enqueue(factory, reg, kind="test.flaky")
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "queued"
    assert job.attempts == 1
    assert job.worker_id is None
    assert job.due_at is not None
    assert job.due_at > utc_now() + timedelta(seconds=3500)
    # not due yet: nothing starts
    assert (await runner.run_once())["started"] == 0
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job_id)
            .values(due_at=utc_now() - timedelta(seconds=1))
        )
        await s.commit()
    assert (await runner.run_once())["started"] == 1
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "succeeded"
    assert job.attempts == 2
    assert json.loads(job.result or "{}") == {"attempt": 2}


async def test_job_retry_on_the_last_attempt_fails_with_its_message(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def flaky(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        raise JobRetry("Der Server war nicht erreichbar.", delay_seconds=60)

    reg = _registry(_handler("test.flaky", flaky))  # max_attempts defaults to 1
    job_id = await _enqueue(factory, reg, kind="test.flaky")
    runner = _runner(factory, reg)
    await runner.run_once()
    await runner.wait_idle()
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == "Der Server war nicht erreichbar."


async def test_shutdown_requeues_a_job_interrupted_before_its_checkpoint(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    started = asyncio.Event()
    hooked: list[int] = []

    async def forever(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return {}

    async def hook(ctx: JobContext, payload: _Payload) -> None:
        hooked.append(ctx.job_id)

    reg = _registry(
        _handler(
            "test.mail",
            forever,
            max_attempts=3,
            requeue_if_interrupted=True,
            on_interrupted=hook,
        )
    )
    job_id = await _enqueue(factory, reg, kind="test.mail")
    runner = _runner(factory, reg)
    await runner.run_once()
    await asyncio.wait_for(started.wait(), 5)
    await runner.shutdown(grace_seconds=0.1)
    job = await _fetch(factory, job_id)
    assert job.status == "queued"
    assert job.worker_id is None
    assert job.attempts == 1
    assert job.error is None
    assert hooked == []


async def test_shutdown_fails_a_job_interrupted_after_its_checkpoint_and_calls_the_hook(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    started = asyncio.Event()
    hooked: list[tuple[int, int]] = []

    async def sending(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        await ctx.checkpoint("sending")
        started.set()
        await asyncio.Event().wait()
        return {}

    async def hook(ctx: JobContext, payload: _Payload) -> None:
        hooked.append((ctx.job_id, payload.n))

    reg = _registry(
        _handler(
            "test.mail",
            sending,
            max_attempts=3,
            requeue_if_interrupted=True,
            on_interrupted=hook,
        )
    )
    job_id = await _enqueue(factory, reg, kind="test.mail", n=4)
    runner = _runner(factory, reg)
    await runner.run_once()
    await asyncio.wait_for(started.wait(), 5)
    await runner.shutdown(grace_seconds=0.1)
    job = await _fetch(factory, job_id)
    assert job.status == "failed"
    assert job.error == INTERRUPTED_MESSAGE
    assert job.checkpoint == "sending"
    assert hooked == [(job_id, 4)]


@pytest.mark.parametrize(
    ("attempts", "checkpoint", "expected"),
    [(1, None, "queued"), (1, "sending", "failed"), (3, None, "failed")],
)
async def test_reaper_requeues_only_before_checkpoint_and_below_max_attempts(
    factory: async_sessionmaker[AsyncSession],
    attempts: int,
    checkpoint: str | None,
    expected: str,
) -> None:
    hooked: list[int] = []

    async def hook(ctx: JobContext, payload: _Payload) -> None:
        hooked.append(ctx.attempt)

    reg = _registry(
        _handler(
            "test.mail", _noop, max_attempts=3, requeue_if_interrupted=True, on_interrupted=hook
        )
    )
    job_id = await _enqueue(factory, reg, kind="test.mail")
    await _force_running(factory, job_id, attempts=attempts, checkpoint=checkpoint)
    runner = _runner(factory, reg, "b:1")
    out = await runner._reap()
    job = await _fetch(factory, job_id)
    assert out == 1
    assert job.status == expected
    if expected == "queued":
        assert job.worker_id is None and job.started_at is None and hooked == []
    else:
        assert job.error == INTERRUPTED_MESSAGE
        assert hooked == [attempts]


async def test_checkpoint_of_a_reaped_claim_raises_and_the_step_never_runs(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    side_effects: list[int] = []

    async def mail(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        started.set()
        await release.wait()
        await ctx.checkpoint("sending")
        side_effects.append(ctx.attempt)
        return {}

    reg = _registry(_handler("test.mail", mail, max_attempts=3, requeue_if_interrupted=True))
    job_id = await _enqueue(factory, reg, kind="test.mail")
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
    assert await _runner(factory, reg, "b:1")._reap() == 1
    assert (await _fetch(factory, job_id)).status == "queued"
    release.set()
    await runner_a.wait_idle()
    job = await _fetch(factory, job_id)
    assert side_effects == []  # the old claim never reached its irreversible step
    assert job.status == "queued"  # and did not overwrite the requeued row


async def test_lane_job_is_not_blocked_by_a_full_default_lane(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    release = asyncio.Event()

    async def blocked(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        await release.wait()
        return {}

    reg = _registry(_handler("test.blocked", blocked), _handler("test.mail", _noop, lane="mail"))
    async with factory() as s:
        await save_section_values(
            s, JOBS_SECTION, {"max_concurrency": 1}, secret_key=_SETTINGS.secret_key
        )
        await s.commit()
    export_id = await _enqueue(factory, reg, kind="test.blocked")
    runner = _runner(factory, reg)
    assert (await runner.run_once())["started"] == 1
    mail_id = await _enqueue(factory, reg, kind="test.mail")
    assert (await runner.run_once())["started"] == 1
    await _wait_for(lambda: _status_is(factory, mail_id, "succeeded"))
    assert (await _fetch(factory, export_id)).status == "running"
    release.set()
    await runner.wait_idle()


async def _status_is(factory: async_sessionmaker[AsyncSession], job_id: int, status: str) -> bool:
    return (await _fetch(factory, job_id)).status == status


async def test_one_slot_lane_runs_its_jobs_one_after_the_other_in_id_order(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    order: list[int] = []
    release = asyncio.Event()

    async def mail(ctx: JobContext, payload: _Payload) -> dict[str, Any]:
        order.append(payload.n)
        await release.wait()
        return {}

    reg = _registry(_handler("test.mail", mail, lane="mail"))
    ids = [await _enqueue(factory, reg, kind="test.mail", n=i) for i in range(3)]
    runner = _runner(factory, reg)
    assert (await runner.run_once())["started"] == 1
    # a second runner (worker replica) sees the lane's running job too
    assert (await _runner(factory, reg, "b:1").run_once())["started"] == 0
    release.set()
    await runner.wait_idle()
    for _ in range(2):
        assert (await runner.run_once())["started"] == 1
        await runner.wait_idle()
    assert order == [0, 1, 2]
    assert [(await _fetch(factory, i)).status for i in ids] == ["succeeded"] * 3


async def test_a_retry_waiting_for_its_due_time_does_not_block_later_lane_jobs(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    reg = _registry(_handler("test.mail", _noop, lane="mail", max_attempts=3))
    first = await _enqueue(factory, reg, kind="test.mail", n=1)
    second = await _enqueue(factory, reg, kind="test.mail", n=2)
    async with factory() as s:
        await s.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == first)
            .values(due_at=utc_now() + timedelta(minutes=2), attempts=1)
        )
        await s.commit()
    runner = _runner(factory, reg)
    assert (await runner.run_once())["started"] == 1
    await runner.wait_idle()
    assert (await _fetch(factory, first)).status == "queued"
    assert (await _fetch(factory, second)).status == "succeeded"
