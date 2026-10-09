"""Handler-side view of a running job: progress, cancellation and result files."""

from __future__ import annotations

import time
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
from tiqora.jobs.service import utc_now

MAX_JOB_FILE_BYTES = 200 * 1024 * 1024
PROGRESS_MIN_INTERVAL_SECONDS = 1.0


class JobCancelled(Exception):
    """The job was cancelled while it ran."""


class JobFailed(Exception):
    """The job failed; ``message`` is shown to the user as-is (German, plain words)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


InterruptReason = Literal["restart", "timeout"]


class JobRetry(Exception):
    """Put the job back into the queue; it is claimed again after ``delay_seconds``.

    ``message`` is shown when the job cannot be retried any more (last allowed attempt).
    """

    def __init__(self, message: str, *, delay_seconds: float) -> None:
        super().__init__(message)
        self.message = message
        self.delay_seconds = max(0.0, float(delay_seconds))


class JobContext:
    def __init__(
        self,
        *,
        job_id: int,
        kind: str,
        created_by: int | None,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        attempt: int = 1,
        max_attempts: int = 1,
        worker_id: str | None = None,
        interrupted_by: InterruptReason | None = None,
    ) -> None:
        self.job_id = job_id
        self.kind = kind
        self.created_by = created_by
        self.settings = settings
        self.session_factory = session_factory
        self.attempt = attempt  # 1 for the first claim, 2 for the first retry, ...
        self.max_attempts = max_attempts
        self.worker_id = worker_id
        # set for the on_interrupted hook: what ended the run
        self.interrupted_by = interrupted_by
        self._last_progress_write: float | None = None

    @property
    def last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts

    async def checkpoint(self, name: str) -> None:
        """Mark the job as past the point of no return (call right before e.g. SMTP).

        An interrupted job is only requeued while it has no checkpoint. The write is
        conditional on the job still being this claim; when it was reaped or requeued
        meanwhile, :class:`JobCancelled` is raised and the step must not run.
        """
        conditions = [TiqoraJob.id == self.job_id, TiqoraJob.status == "running"]
        if self.worker_id is not None:
            conditions += [
                TiqoraJob.worker_id == self.worker_id,
                TiqoraJob.attempts == self.attempt,
            ]
        async with self.session_factory() as session:
            result = await session.execute(
                update(TiqoraJob)
                .where(*conditions)
                .values(checkpoint=name[:50], heartbeat_at=utc_now())
            )
            await session.commit()
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise JobCancelled

    async def progress(
        self, done: int, total: int | None = None, message: str | None = None
    ) -> None:
        """Record progress (throttled to one write per second unless finished), then
        raise :class:`JobCancelled` when a cancel was requested."""
        now = time.monotonic()
        finished = total is not None and done == total
        last = self._last_progress_write
        if finished or last is None or now - last >= PROGRESS_MIN_INTERVAL_SECONDS:
            self._last_progress_write = now
            async with self.session_factory() as session:
                await session.execute(
                    update(TiqoraJob)
                    .where(TiqoraJob.id == self.job_id)
                    .values(
                        progress_done=done,
                        progress_total=total,
                        progress_message=message[:500] if message is not None else None,
                        heartbeat_at=utc_now(),
                    )
                )
                await session.commit()
        await self.check_cancelled()

    async def check_cancelled(self) -> None:
        async with self.session_factory() as session:
            requested = (
                await session.execute(
                    select(TiqoraJob.cancel_requested).where(TiqoraJob.id == self.job_id)
                )
            ).scalar_one_or_none()
        if requested:
            raise JobCancelled

    async def add_file(self, name: str, content_type: str, data: bytes) -> int:
        if len(data) > MAX_JOB_FILE_BYTES:
            raise JobFailed("Die Datei ist zu groß (mehr als 200 MB).")
        async with self.session_factory() as session:
            row = TiqoraJobFile(
                job_id=self.job_id,
                name=name[:255],
                content_type=content_type[:100],
                size_bytes=len(data),
                data=data,
                created_at=utc_now(),
            )
            session.add(row)
            await session.flush()
            file_id = row.id
            await session.commit()
        return file_id
