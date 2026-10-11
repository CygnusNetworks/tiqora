"""Plugin job handlers run through the core job runner (both dialects)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import bindparam, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._plugins_state_helpers import (  # noqa: F401  (fixtures)
    db_url_fixture,
    factory_fixture,
)
from tests.plugins.helpers import Installer
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs import JobContext, UnknownJobKind, enqueue_job, get_job
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler
from tiqora.jobs.runner import FAILED_MESSAGE, TIMEOUT_MESSAGE, UNKNOWN_KIND_MESSAGE, JobRunner
from tiqora.jobs.schemas import job_out
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import discover_plugins
from tiqora.plugins.loader import commit_registrar, import_and_register
from tiqora.plugins.registrar import RegistrationRejected

pytestmark = pytest.mark.db

_SETTINGS = Settings(environment="test")
KIND = "org.example.hello.echo"


class _Payload(BaseModel):
    text: str


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        ids = [
            r[0]
            for r in (
                await s.execute(text("SELECT id FROM tiqora_job WHERE kind LIKE 'org.example.%'"))
            ).fetchall()
        ]
        if ids:
            await s.execute(delete(TiqoraJobFile).where(TiqoraJobFile.job_id.in_(ids)))
            await s.execute(
                text("DELETE FROM tiqora_job WHERE id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": ids},
            )
        await s.commit()


@pytest.fixture
async def f(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await _clean(factory)
    yield factory
    await _clean(factory)


def _registry(fixture_plugins: Installer) -> Registry:
    fixture_plugins()
    (p,) = discover_plugins()
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, import_and_register(p, make_plugin_context(p)))
    return reg


async def _fetch(factory: async_sessionmaker[AsyncSession], job_id: int) -> TiqoraJob:
    async with factory() as s:
        job = await get_job(s, job_id)
        assert job is not None
        return job


async def test_plugin_job_runs_through_the_core_runner(
    f: async_sessionmaker[AsyncSession], fixture_plugins: Installer
) -> None:
    reg = _registry(fixture_plugins)
    async with f() as s:
        job = await enqueue_job(s, kind=KIND, payload={"text": "hi"}, created_by=None, registry=reg)
        await s.commit()
        job_id = job.id
    runner = JobRunner(session_factory=f, settings=_SETTINGS, registry=reg, worker_id="t:1")
    out = await runner.run_once()
    await runner.wait_idle()
    done = await _fetch(f, job_id)
    assert out["started"] >= 1
    assert done.status == "succeeded"
    assert done.result is not None and json.loads(done.result) == {"echo": "hi"}


async def test_unknown_plugin_kind_is_refused(
    f: async_sessionmaker[AsyncSession], fixture_plugins: Installer
) -> None:
    reg = _registry(fixture_plugins)
    async with f() as s:
        with pytest.raises(UnknownJobKind):
            await enqueue_job(
                s,
                kind="org.example.hello.nope",
                payload={"text": "hi"},
                created_by=None,
                registry=reg,
            )


async def test_job_of_a_plugin_that_is_gone_fails_cleanly(
    f: async_sessionmaker[AsyncSession], fixture_plugins: Installer
) -> None:
    reg = _registry(fixture_plugins)
    async with f() as s:
        job = await enqueue_job(s, kind=KIND, payload={"text": "hi"}, created_by=None, registry=reg)
        await s.commit()
        job_id = job.id
    core_only = Registry()
    register_core(core_only)  # the plugin was switched off before the worker restarted
    runner = JobRunner(session_factory=f, settings=_SETTINGS, registry=core_only, worker_id="t:1")
    await runner.run_once()
    await runner.wait_idle()
    done = await _fetch(f, job_id)
    assert done.status == "failed" and done.error == UNKNOWN_KIND_MESSAGE


def test_plugin_handler_only_for_its_own_kind_namespace(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    ctx = make_plugin_context(p)
    registrar = import_and_register(p, ctx)

    async def run(jc: JobContext, payload: Any) -> None:
        return None

    for kind in ("core.ticket_export", "org.example.other.echo", "org.example.hello"):
        with pytest.raises(RegistrationRejected, match="Aufgabenart"):
            registrar.job_handler(
                JobHandler(kind=kind, payload_model=_Payload, run=run, title_key="x")
            )


async def test_plugin_result_privacy_follows_the_handler_flag(
    f: async_sessionmaker[AsyncSession], fixture_plugins: Installer
) -> None:
    reg = _registry(fixture_plugins)
    private = JobHandler(
        kind="org.example.hello.secret",
        payload_model=_Payload,
        run=reg.get(JOB_HANDLERS, KIND).run,  # type: ignore[union-attr]
        title_key="plugin.org.example.hello:jobs.secret",
        result_private=True,
    )
    reg2 = Registry()
    register_core(reg2)
    reg2.add(JOB_HANDLERS, private, owner="org.example.hello")
    reg2.add(JOB_HANDLERS, reg.get(JOB_HANDLERS, KIND), owner="org.example.hello")  # type: ignore[arg-type]
    ids = {}
    async with f() as s:
        for kind in (KIND, private.kind):
            job = await enqueue_job(
                s, kind=kind, payload={"text": "x"}, created_by=7, registry=reg2
            )
            ids[kind] = job.id
        await s.commit()
    runner = JobRunner(session_factory=f, settings=_SETTINGS, registry=reg2, worker_id="t:1")
    await runner.run_once()
    await runner.wait_idle()
    shown = {k: await _fetch(f, i) for k, i in ids.items()}
    for kind, other_result in ((KIND, {"echo": "x"}), (private.kind, None)):
        assert job_out(shown[kind], [], reg2, viewer_id=7).result == {"echo": "x"}  # creator
        assert job_out(shown[kind], [], reg2, viewer_id=8).result == other_result  # someone else


@pytest.mark.parametrize(
    "make_exc",
    [lambda: SystemExit(3), KeyboardInterrupt, GeneratorExit],
    ids=["SystemExit", "KeyboardInterrupt", "GeneratorExit"],
)
async def test_base_exception_in_plugin_job_is_a_job_error_not_a_crash(
    f: async_sessionmaker[AsyncSession],
    fixture_plugins: Installer,
    make_exc: Callable[[], BaseException],
) -> None:
    """sys.exit() & co. in a plugin job handler must not end the worker (B-I2)."""
    fixture_plugins()
    (p,) = discover_plugins()
    registrar = import_and_register(p, make_plugin_context(p))

    async def leave(jc: JobContext, payload: Any) -> None:
        raise make_exc()  # a fresh instance per run: no traceback carried across tests

    registrar.job_handler(
        JobHandler(
            kind="org.example.hello.leave",
            payload_model=_Payload,
            run=leave,
            title_key="plugin.org.example.hello:jobs.leave",
        )
    )
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, registrar)
    async with f() as s:
        bad = await enqueue_job(
            s, kind="org.example.hello.leave", payload={"text": "x"}, created_by=None, registry=reg
        )
        await s.commit()
        bad_id = bad.id
    runner = JobRunner(session_factory=f, settings=_SETTINGS, registry=reg, worker_id="t:1")
    await runner.run_once()
    await runner.wait_idle()
    failed = await _fetch(f, bad_id)
    assert failed.status == "failed" and failed.error == FAILED_MESSAGE
    # Still here: the event loop survived (before the fix the exception ended it), and the
    # runner has nothing left running. No second job on purpose: a second run_once/wait_idle
    # after a failed job can spin on a finished task (pre-existing JobRunner flake, also
    # without plugins' shield and with plain RuntimeError handlers; reported separately).
    await asyncio.sleep(0)
    assert runner.active_count == 0
    assert (await runner.run_once())["started"] == 0


async def test_system_exit_in_plugin_on_interrupted_does_not_end_the_worker(
    f: async_sessionmaker[AsyncSession], fixture_plugins: Installer
) -> None:
    """on_interrupted is plugin code too: sys.exit() there must not end the worker (B-I2)."""
    fixture_plugins()
    (p,) = discover_plugins()
    registrar = import_and_register(p, make_plugin_context(p))
    hooked: list[str] = []

    async def slow(jc: JobContext, payload: Any) -> None:
        await asyncio.sleep(5)

    async def leave(jc: JobContext, payload: Any) -> None:
        hooked.append(jc.interrupted_by or "")
        raise SystemExit(4)

    registrar.job_handler(
        JobHandler(
            kind="org.example.hello.slow",
            payload_model=_Payload,
            run=slow,
            title_key="plugin.org.example.hello:jobs.slow",
            timeout_seconds=1,
            on_interrupted=leave,
        )
    )
    reg = Registry()
    register_core(reg)
    commit_registrar(reg, registrar)
    async with f() as s:
        job = await enqueue_job(
            s, kind="org.example.hello.slow", payload={"text": "x"}, created_by=None, registry=reg
        )
        await s.commit()
        job_id = job.id
    runner = JobRunner(session_factory=f, settings=_SETTINGS, registry=reg, worker_id="t:1")
    await runner.run_once()
    await runner.wait_idle()
    assert hooked == ["timeout"]  # the hook ran ...
    done = await _fetch(f, job_id)
    assert done.status == "failed" and done.error == TIMEOUT_MESSAGE
    await asyncio.sleep(0)  # ... and the event loop survived its SystemExit
    assert runner.active_count == 0
