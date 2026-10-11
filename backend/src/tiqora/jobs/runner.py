"""Worker-side job runner: claims queued jobs, runs handlers, heartbeats and reaps.

Lanes (see :mod:`tiqora.jobs.registry`): every lane is filled on its own, in job id order.
The ``default`` lane has ``jobs.max_concurrency`` slots per runner; every other lane has
:data:`~tiqora.jobs.registry.LANE_SLOTS` slots counted over ALL runners (running rows in
the database), so a one-slot lane runs its jobs one after the other even with several
worker replicas (best effort: two replicas claiming in the same instant can overlap once).

Retries and interruptions: a ``JobRetry`` puts the job back as ``queued`` with ``due_at``;
a job interrupted by shutdown or reaped (no heartbeat for 180 s) is requeued when its
handler allows it and had not passed ``ctx.checkpoint()``, otherwise it fails
("interrupted") and the handler's ``on_interrupted`` hook records the failure. Every
state change of a claimed row is conditional on ``attempts`` (the claim), so a late
finish of an older claim never overwrites a newer one.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import socket
import time
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import ColumnElement, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.extensions import Registry
from tiqora.jobs.context import (
    InterruptReason,
    JobCancelled,
    JobContext,
    JobFailed,
    JobRetry,
)
from tiqora.jobs.registry import DEFAULT_LANE, JOB_HANDLERS, lane_slots
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


def _requeue_values(*, due_at: datetime | None) -> dict[str, Any]:
    """Column values that put a claimed job back into the queue - or end it ``cancelled``
    when a cancel was requested meanwhile (a requeued row with ``cancel_requested`` would
    never be claimed again and stay ``queued`` forever)."""
    cancel = TiqoraJob.cancel_requested.is_(True)
    return {
        "status": case((cancel, "cancelled"), else_="queued"),
        "worker_id": None,
        "started_at": None,
        "heartbeat_at": None,
        "due_at": due_at,  # meaningless on a cancelled row
        "finished_at": case((cancel, utc_now()), else_=None),
    }


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
        self._lanes: dict[int, str] = {}
        self._stopping = False
        self._last_purge: float | None = None

    @property
    def active_count(self) -> int:
        return len(self._active)

    async def run_once(self) -> dict[str, int]:
        reaped = await self._reap()
        try:
            purged = await self._maybe_purge()
        except Exception:  # noqa: BLE001 — a failing purge must never stop job starts
            logger.warning("jobs_purge_failed", exc_info=True)
            purged = 0
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
        stale = func.coalesce(TiqoraJob.heartbeat_at, TiqoraJob.started_at) < cutoff
        rows = await self._interrupted_rows(TiqoraJob.status == "running", stale)
        reaped = 0
        for row in rows:
            # the stale condition is repeated: a job that heartbeated meanwhile is alive
            if await self._settle_interrupted(row, stale):
                reaped += 1
        return reaped

    async def _interrupted_rows(self, *conditions: ColumnElement[bool]) -> list[Any]:
        async with self._factory() as session:
            return list(
                (
                    await session.execute(
                        select(
                            TiqoraJob.id,
                            TiqoraJob.kind,
                            TiqoraJob.payload,
                            TiqoraJob.created_by,
                            TiqoraJob.attempts,
                            TiqoraJob.checkpoint,
                        )
                        .where(*conditions)
                        .order_by(TiqoraJob.id)
                    )
                ).all()
            )

    async def _settle_interrupted(
        self,
        row: Any,
        *conditions: ColumnElement[bool],
        interrupted_by: InterruptReason = "restart",
    ) -> bool:
        """Requeue or fail one interrupted ``running`` job; True when its row changed.

        ``interrupted_by`` is ``"restart"`` (shutdown, reaper) or ``"timeout"`` (the runner
        cut the handler off); it picks the error text and is handed to ``on_interrupted``. A
        requeue of a job whose cancel was requested ends it ``cancelled`` instead.
        """
        job_id, kind, payload, created_by, attempts, checkpoint = row
        handler = self._registry.get(JOB_HANDLERS, kind)
        claim = (
            TiqoraJob.id == job_id,
            TiqoraJob.status == "running",
            TiqoraJob.attempts == attempts,
            *conditions,
        )
        if (
            handler is not None
            and handler.requeue_if_interrupted
            and checkpoint is None
            and attempts < handler.max_attempts
        ):
            async with self._factory() as session:
                result = await session.execute(
                    update(TiqoraJob)
                    .where(*claim, TiqoraJob.checkpoint.is_(None))
                    .values(**_requeue_values(due_at=None))
                )
                await session.commit()
            if result.rowcount == 1:  # type: ignore[attr-defined]
                logger.warning(
                    "job_requeued_after_interrupt",
                    job_id=job_id,
                    kind=kind,
                    interrupted_by=interrupted_by,
                )
                return True
            # the handler reached its checkpoint meanwhile: fail it below
        message = TIMEOUT_MESSAGE if interrupted_by == "timeout" else INTERRUPTED_MESSAGE
        async with self._factory() as session:
            result = await session.execute(
                update(TiqoraJob)
                .where(*claim)
                .values(status="failed", error=message, finished_at=utc_now())
            )
            await session.commit()
        if result.rowcount != 1:  # type: ignore[attr-defined]
            return False  # settled elsewhere already: never recorded twice
        await self._call_on_interrupted(
            job_id, kind, payload, created_by, int(attempts), interrupted_by=interrupted_by
        )
        return True

    async def _call_on_interrupted(
        self,
        job_id: int,
        kind: str,
        payload: str,
        created_by: int | None,
        attempt: int,
        *,
        interrupted_by: InterruptReason,
    ) -> None:
        """Run the handler's ``on_interrupted`` hook for a job that was just failed."""
        handler = self._registry.get(JOB_HANDLERS, kind)
        if handler is None or handler.on_interrupted is None:
            return
        try:
            parsed = handler.payload_model.model_validate_json(payload)
            ctx = JobContext(
                job_id=job_id,
                kind=kind,
                created_by=created_by,
                settings=self._settings,
                session_factory=self._factory,
                attempt=attempt,
                max_attempts=handler.max_attempts,
                interrupted_by=interrupted_by,
            )
            await handler.on_interrupted(ctx, parsed)
        except Exception:  # noqa: BLE001 — recording must never stop the runner
            logger.exception("job_on_interrupted_failed", job_id=job_id, kind=kind)

    async def _load_settings(self, session: AsyncSession) -> JobsSettings:
        try:
            values = await load_section_values(
                session, JOBS_SECTION, secret_key=self._settings.secret_key
            )
            return JobsSettings.model_validate(values.model_dump())
        except Exception:  # noqa: BLE001 — fall back to defaults rather than skip claims
            logger.warning("jobs_settings_load_failed", exc_info=True)
            return JobsSettings.model_validate({})

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
        await self._purge_deleted_reports()
        self._last_purge = now
        return purged

    async def _purge_deleted_reports(self) -> None:
        """Hard-delete saved reports soft-deleted > 90 days ago (R-OWNDELETE)."""
        # lazy import: reporting imports the jobs package
        from tiqora.reporting.saved import purge_deleted_reports

        try:
            async with self._factory() as session:
                await purge_deleted_reports(session)
                await session.commit()
        except Exception:  # noqa: BLE001 — retention must never stop the runner
            logger.warning("saved_report_purge_failed", exc_info=True)

    def _lane_kinds(self) -> dict[str, list[str]]:
        """Kinds of every lane except ``default`` (which takes all other kinds)."""
        lanes: dict[str, list[str]] = {}
        for handler in self._registry.items(JOB_HANDLERS):
            if handler.lane != DEFAULT_LANE:
                lanes.setdefault(handler.lane, []).append(handler.kind)
        return lanes

    async def _claim_and_start(self) -> int:
        async with self._factory() as session:
            cfg = await self._load_settings(session)
        lanes = self._lane_kinds()
        laned = sorted(k for kinds in lanes.values() for k in kinds)
        started = 0
        for lane in [DEFAULT_LANE, *sorted(lanes)]:
            if lane == DEFAULT_LANE:
                free = cfg.max_concurrency - sum(
                    1 for j in self._active if self._lanes.get(j) == DEFAULT_LANE
                )
                kind_filter = TiqoraJob.kind.not_in(laned) if laned else None
            else:
                kind_filter = TiqoraJob.kind.in_(lanes[lane])
                free = lane_slots(lane, cfg.max_concurrency) - await self._running_in(kind_filter)
            if free > 0:
                started += await self._start_lane(lane, kind_filter, free)
        return started

    async def _running_in(self, kind_filter: ColumnElement[bool]) -> int:
        async with self._factory() as session:
            return int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(TiqoraJob)
                        .where(TiqoraJob.status == "running", kind_filter)
                    )
                ).scalar_one()
            )

    async def _start_lane(
        self, lane: str, kind_filter: ColumnElement[bool] | None, free: int
    ) -> int:
        now = utc_now()
        stmt = (
            select(TiqoraJob.id, TiqoraJob.kind, TiqoraJob.payload, TiqoraJob.created_by)
            .where(
                TiqoraJob.status == "queued",
                TiqoraJob.cancel_requested.is_(False),
                or_(TiqoraJob.due_at.is_(None), TiqoraJob.due_at <= now),
            )
            .order_by(TiqoraJob.id)
            .limit(free)
        )
        if kind_filter is not None:
            stmt = stmt.where(kind_filter)
        async with self._factory() as session:
            rows = (await session.execute(stmt)).all()
        started = 0
        for job_id, kind, payload, created_by in rows:
            if job_id in self._active:
                # an older claim of this job still runs here (reaped, then requeued): let it
                # end first, so the active set keeps one task per job
                continue
            attempt = await self._claim(job_id)
            if attempt:
                task = asyncio.create_task(
                    self._execute(job_id, kind, payload, created_by, attempt=attempt)
                )
                self._active[job_id] = task
                self._lanes[job_id] = lane
                task.add_done_callback(functools.partial(self._forget, job_id))
                started += 1
        return started

    def _forget(self, job_id: int, _task: asyncio.Task[None]) -> None:
        self._active.pop(job_id, None)
        self._lanes.pop(job_id, None)

    async def _claim(self, job_id: int) -> int:
        """Claim a queued job; returns its attempt number (1 = first claim), 0 when taken."""
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
                    status="running",
                    worker_id=self.worker_id,
                    started_at=now,
                    heartbeat_at=now,
                    attempts=TiqoraJob.attempts + 1,
                    checkpoint=None,
                    due_at=None,
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                await session.rollback()
                return 0
            attempt = (
                await session.execute(select(TiqoraJob.attempts).where(TiqoraJob.id == job_id))
            ).scalar_one()
            await session.commit()
            return int(attempt)

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

    async def _execute(
        self, job_id: int, kind: str, payload: str, created_by: int | None, *, attempt: int = 1
    ) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(job_id))
        try:
            status, error, result = await self._run_handler(
                job_id, kind, payload, created_by, attempt=attempt
            )
            if status == "retry":
                await self._requeue_for_retry(job_id, attempt, error, result)
            elif status == "timeout":
                # the runner cut the handler off: settled like an interruption (requeue before
                # the checkpoint when the handler allows it, else failed + on_interrupted)
                await self._settle_timeout(job_id, kind, payload, created_by, attempt)
            else:
                await self._finalize(job_id, status, error=error, result=result, attempt=attempt)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _run_handler(
        self, job_id: int, kind: str, payload: str, created_by: int | None, *, attempt: int = 1
    ) -> tuple[str, str | None, str | None]:
        """``(status, error, result)``; for ``"retry"`` the result is the delay in seconds."""
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
            attempt=attempt,
            max_attempts=handler.max_attempts,
            worker_id=self.worker_id,
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
            except JobRetry as exc:
                if attempt >= handler.max_attempts:
                    return "failed", exc.message, None
                return "retry", exc.message, str(exc.delay_seconds)
            except JobFailed as exc:
                return "failed", exc.message, None
            except Exception:  # noqa: BLE001 — never leak internals to the user
                logger.exception("job_failed", job_id=job_id, kind=kind)
                return "failed", FAILED_MESSAGE, None

        try:
            return await asyncio.wait_for(guarded(), timeout=handler.timeout_seconds)
        except TimeoutError:
            logger.warning("job_timeout", job_id=job_id, kind=kind)
            return "timeout", TIMEOUT_MESSAGE, None

    async def _settle_timeout(
        self, job_id: int, kind: str, payload: str, created_by: int | None, attempt: int
    ) -> None:
        try:
            async with self._factory() as session:
                checkpoint = (
                    await session.execute(
                        select(TiqoraJob.checkpoint).where(*self._own_claim(job_id, attempt))
                    )
                ).scalar_one_or_none()
            row = (job_id, kind, payload, created_by, attempt, checkpoint)
            await self._settle_interrupted(
                row, TiqoraJob.worker_id == self.worker_id, interrupted_by="timeout"
            )
        except Exception:  # noqa: BLE001 — the reaper settles the row later
            logger.exception("job_timeout_settle_failed", job_id=job_id)

    async def _requeue_for_retry(
        self, job_id: int, attempt: int, message: str | None, delay: str | None
    ) -> None:
        due = utc_now() + timedelta(seconds=float(delay or 0))
        try:
            async with self._factory() as session:
                await session.execute(
                    update(TiqoraJob)
                    .where(*self._own_claim(job_id, attempt))
                    .values(
                        **_requeue_values(due_at=due),
                        checkpoint=None,
                        progress_message=message[:500] if message else None,
                    )
                )
                await session.commit()
            logger.info("job_retry_scheduled", job_id=job_id, attempt=attempt, due_at=str(due))
        except Exception:  # noqa: BLE001 — the reaper settles the row later
            logger.exception("job_retry_requeue_failed", job_id=job_id)

    def _own_claim(self, job_id: int, attempt: int) -> tuple[ColumnElement[bool], ...]:
        return (
            TiqoraJob.id == job_id,
            TiqoraJob.status == "running",
            TiqoraJob.worker_id == self.worker_id,
            TiqoraJob.attempts == attempt,
        )

    async def _finalize(
        self,
        job_id: int,
        status: str,
        *,
        error: str | None,
        result: str | None,
        attempt: int = 1,
    ) -> None:
        # Conditional on still being this claim: a reaped or requeued job is never overwritten.
        try:
            async with self._factory() as session:
                await session.execute(
                    update(TiqoraJob)
                    .where(*self._own_claim(job_id, attempt))
                    .values(status=status, error=error, result=result, finished_at=utc_now())
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — the reaper will fail the row later
            logger.exception("job_finalize_failed", job_id=job_id)

    async def wait_idle(self) -> None:
        while self._active:
            await asyncio.gather(*list(self._active.values()), return_exceptions=True)
            # gather() of tasks that already finished returns without yielding, and their
            # ``_forget`` done-callbacks only run on the next loop iteration: without this
            # yield a finished task left in ``_active`` turns this loop into a busy spin.
            await asyncio.sleep(0)

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
        mine = TiqoraJob.worker_id == self.worker_id
        try:
            rows = await self._interrupted_rows(
                TiqoraJob.id.in_(list(tasks)), TiqoraJob.status == "running", mine
            )
            for row in rows:
                await self._settle_interrupted(row, mine)
        except Exception:  # noqa: BLE001 — the reaper settles the rows later
            logger.exception("job_shutdown_update_failed", job_ids=list(tasks))

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
