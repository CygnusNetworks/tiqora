"""Worker-side job runner: claims queued jobs, runs handlers, heartbeats and reaps."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import socket
import time
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import Registry
from tiqora.jobs.context import JobCancelled, JobContext, JobFailed
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import MAX_RESULT_CHARS, purge_finished_jobs, utc_now
from tiqora.jobs.settings import JOBS_SECTION, JobsSettings
from tiqora.settings_sections.store import load_section_values
from tiqora.worker.status import record_tick_status

logger = structlog.get_logger(__name__)

HEARTBEAT_SECONDS = 30
STALE_AFTER_SECONDS = 180
PURGE_EVERY_SECONDS = 3600
STATUS_EVERY_SECONDS = 30
INTERRUPTED_MESSAGE = (
    "Die Aufgabe wurde unterbrochen, weil der Hintergrunddienst neu gestartet "
    "wurde. Bitte erneut starten."
)
TIMEOUT_MESSAGE = "Die Aufgabe hat zu lange gedauert und wurde abgebrochen."
FAILED_MESSAGE = (
    "Die Aufgabe ist mit einem Fehler abgebrochen. Details stehen im Protokoll "
    "des Hintergrunddienstes."
)
UNKNOWN_KIND_MESSAGE = "Diese Aufgabe kennt der Hintergrunddienst nicht (mehr)."
RESULT_TOO_LARGE_MESSAGE = "Das Ergebnis ist zu groß."

_SHUTDOWN_GRACE_SECONDS = 20


class JobRunner:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        registry: Registry,
        worker_id: str | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._registry = registry
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._active: dict[int, asyncio.Task[None]] = {}
        self._stopping = False
        self._last_purge: float | None = None

    @property
    def active_count(self) -> int:
        return len(self._active)

    async def run_once(self) -> dict[str, int]:
        reaped = await self._reap()
        purged = await self._maybe_purge()
        started = 0
        if not self._stopping:
            started = await self._claim_and_start()
        return {
            "reaped": reaped,
            "purged": purged,
            "started": started,
            "running": len(self._active),
        }

    async def _reap(self) -> int:
        cutoff = utc_now() - timedelta(seconds=STALE_AFTER_SECONDS)
        async with self._factory() as session:
            result = await session.execute(
                update(TiqoraJob)
                .where(TiqoraJob.status == "running", TiqoraJob.heartbeat_at < cutoff)
                .values(status="failed", error=INTERRUPTED_MESSAGE, finished_at=utc_now())
            )
            await session.commit()
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def _load_settings(self, session: AsyncSession) -> JobsSettings:
        values = await load_section_values(
            session, JOBS_SECTION, secret_key=self._settings.secret_key
        )
        return JobsSettings.model_validate(values.model_dump())

    async def _maybe_purge(self) -> int:
        now = time.monotonic()
        if self._last_purge is not None and now - self._last_purge < PURGE_EVERY_SECONDS:
            return 0
        async with self._factory() as session:
            cfg = await self._load_settings(session)
            purged = await purge_finished_jobs(
                session, older_than=utc_now() - timedelta(days=cfg.retention_days)
            )
            await session.commit()
        self._last_purge = now
        return purged

    async def _claim_and_start(self) -> int:
        async with self._factory() as session:
            cfg = await self._load_settings(session)
        free = cfg.max_concurrency - len(self._active)
        if free <= 0:
            return 0
        async with self._factory() as session:
            rows = (
                await session.execute(
                    select(TiqoraJob.id, TiqoraJob.kind, TiqoraJob.payload, TiqoraJob.created_by)
                    .where(TiqoraJob.status == "queued", TiqoraJob.cancel_requested.is_(False))
                    .order_by(TiqoraJob.id)
                    .limit(free)
                )
            ).all()
        started = 0
        for job_id, kind, payload, created_by in rows:
            if await self._claim(job_id):
                task = asyncio.create_task(self._execute(job_id, kind, payload, created_by))
                self._active[job_id] = task
                task.add_done_callback(functools.partial(self._forget, job_id))
                started += 1
        return started

    def _forget(self, job_id: int, _task: asyncio.Task[None]) -> None:
        self._active.pop(job_id, None)

    async def _claim(self, job_id: int) -> bool:
        now = utc_now()
        async with self._factory() as session:
            result = await session.execute(
                update(TiqoraJob)
                .where(
                    TiqoraJob.id == job_id,
                    TiqoraJob.status == "queued",
                    TiqoraJob.cancel_requested.is_(False),
                )
                .values(
                    status="running", worker_id=self.worker_id, started_at=now, heartbeat_at=now
                )
            )
            await session.commit()
            return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def _heartbeat(self, job_id: int) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                async with self._factory() as session:
                    await session.execute(
                        update(TiqoraJob)
                        .where(
                            TiqoraJob.id == job_id,
                            TiqoraJob.status == "running",
                            TiqoraJob.worker_id == self.worker_id,
                        )
                        .values(heartbeat_at=utc_now())
                    )
                    await session.commit()
            except Exception:  # noqa: BLE001 — a missed beat must not kill the job
                logger.exception("job_heartbeat_failed", job_id=job_id)

    async def _execute(self, job_id: int, kind: str, payload: str, created_by: int | None) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job_id))
        try:
            status, error, result = await self._run_handler(job_id, kind, payload, created_by)
            await self._finalize(job_id, status, error=error, result=result)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _run_handler(
        self, job_id: int, kind: str, payload: str, created_by: int | None
    ) -> tuple[str, str | None, str | None]:
        handler = self._registry.get(JOB_HANDLERS, kind)
        if handler is None:
            return "failed", UNKNOWN_KIND_MESSAGE, None
        try:
            parsed = handler.payload_model.model_validate_json(payload)
        except Exception:  # noqa: BLE001
            logger.exception("job_payload_invalid", job_id=job_id, kind=kind)
            return "failed", FAILED_MESSAGE, None
        ctx = JobContext(
            job_id=job_id,
            kind=kind,
            created_by=created_by,
            settings=self._settings,
            session_factory=self._factory,
        )

        async def guarded() -> tuple[str, str | None, str | None]:
            # Handler errors are mapped here so a TimeoutError raised by the handler
            # itself is not mistaken for the runner's own timeout below.
            try:
                out: dict[str, Any] | None = await handler.run(ctx, parsed)
                if out is None:
                    return "succeeded", None, None
                encoded = json.dumps(out)
                if len(encoded) > MAX_RESULT_CHARS:
                    return "failed", RESULT_TOO_LARGE_MESSAGE, None
                return "succeeded", None, encoded
            except JobCancelled:
                return "cancelled", None, None
            except JobFailed as exc:
                return "failed", exc.message, None
            except Exception:  # noqa: BLE001 — never leak internals to the user
                logger.exception("job_failed", job_id=job_id, kind=kind)
                return "failed", FAILED_MESSAGE, None

        try:
            return await asyncio.wait_for(guarded(), timeout=handler.timeout_seconds)
        except TimeoutError:
            logger.warning("job_timeout", job_id=job_id, kind=kind)
            return "failed", TIMEOUT_MESSAGE, None

    async def _finalize(
        self, job_id: int, status: str, *, error: str | None, result: str | None
    ) -> None:
        # Conditional on still being ours and running: a reaped job is never overwritten.
        try:
            async with self._factory() as session:
                await session.execute(
                    update(TiqoraJob)
                    .where(
                        TiqoraJob.id == job_id,
                        TiqoraJob.status == "running",
                        TiqoraJob.worker_id == self.worker_id,
                    )
                    .values(status=status, error=error, result=result, finished_at=utc_now())
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — the reaper will fail the row later
            logger.exception("job_finalize_failed", job_id=job_id)

    async def wait_idle(self) -> None:
        while self._active:
            await asyncio.gather(*list(self._active.values()), return_exceptions=True)

    async def shutdown(self, grace_seconds: float) -> None:
        self._stopping = True
        tasks = dict(self._active)
        if not tasks:
            return
        _done, pending = await asyncio.wait(tasks.values(), timeout=grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        async with self._factory() as session:
            await session.execute(
                update(TiqoraJob)
                .where(
                    TiqoraJob.id.in_(list(tasks)),
                    TiqoraJob.status == "running",
                    TiqoraJob.worker_id == self.worker_id,
                )
                .values(status="failed", error=INTERRUPTED_MESSAGE, finished_at=utc_now())
            )
            await session.commit()

    async def run_forever(self, stop: asyncio.Event, *, poll_seconds: float) -> None:
        logger.info("jobs_loop_started", worker_id=self.worker_id, poll_seconds=poll_seconds)
        last_status: float | None = None
        while not stop.is_set():
            ok, result, error = True, None, None
            try:
                result = await self.run_once()
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                logger.exception("jobs_loop_error")
                ok, error = False, str(exc)
            now = time.monotonic()
            if last_status is None or now - last_status >= STATUS_EVERY_SECONDS or not ok:
                last_status = now
                await record_tick_status(
                    "jobs", ok=ok, result=result, error=error, session_factory=self._factory
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                continue
        await self.shutdown(grace_seconds=_SHUTDOWN_GRACE_SECONDS)
        logger.info("jobs_loop_stopped")
