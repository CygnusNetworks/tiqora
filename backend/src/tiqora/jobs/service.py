"""Job queue service: enqueue, read, cancel and purge. Never commits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
from tiqora.extensions import Registry, get_registry
from tiqora.jobs.registry import JOB_HANDLERS

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
MAX_RESULT_CHARS = 1_000_000


class UnknownJobKind(ValueError):
    """No handler is registered for the requested kind."""


class JobNotCancellable(Exception):
    """The job already ended."""


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def enqueue_job(
    session: AsyncSession,
    *,
    kind: str,
    payload: BaseModel | Mapping[str, Any],
    created_by: int | None,
    dedupe_key: str | None = None,
    registry: Registry | None = None,
) -> TiqoraJob:
    reg = registry if registry is not None else get_registry()
    handler = reg.get(JOB_HANDLERS, kind)
    if handler is None:
        raise UnknownJobKind(kind)
    raw = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else dict(payload)
    validated = handler.payload_model.model_validate(raw)
    if dedupe_key is not None:
        existing = (
            (
                await session.execute(
                    select(TiqoraJob)
                    .where(
                        TiqoraJob.kind == kind,
                        TiqoraJob.dedupe_key == dedupe_key,
                        TiqoraJob.status.in_(ACTIVE_STATUSES),
                        # a job being cancelled is dying: a restart must not join it
                        TiqoraJob.cancel_requested.is_(False),
                    )
                    .order_by(TiqoraJob.id)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
    job = TiqoraJob(
        kind=kind,
        status="queued",
        payload=validated.model_dump_json(),
        progress_done=0,
        cancel_requested=False,
        dedupe_key=dedupe_key,
        created_by=created_by,
        created_at=utc_now(),
    )
    session.add(job)
    await session.flush()
    return job


async def lock_user_job_quota(session: AsyncSession, user_id: int) -> None:
    """Serialise per-user job caps: ``SELECT ... FOR UPDATE`` on the user's ``users`` row.

    Call it as the FIRST statement of the transaction that counts the user's active
    jobs (:func:`count_active_jobs`) and enqueues, then commit. A second request of the
    same user waits here until the first one committed, so check-then-insert cannot
    overshoot the cap. Only a row lock — nothing is written to the Znuny table. It must
    come first because MariaDB's REPEATABLE READ snapshot starts at the first plain read:
    a snapshot taken before the lock would not see the other request's committed job.

    PostgreSQL takes ``FOR NO KEY UPDATE`` (``key_share=True``): it still conflicts
    with itself (the cap stays serialised) but not with the ``FOR KEY SHARE`` locks
    foreign-key checks take, so inserts referencing the user (tickets, articles,
    history rows of that agent) are not blocked while a request holds the lock.
    MariaDB renders a plain ``FOR UPDATE`` (InnoDB has no weaker exclusive mode).
    """
    await session.execute(
        select(Users.id).where(Users.id == user_id).with_for_update(key_share=True)
    )


async def count_active_jobs(
    session: AsyncSession, *, kind: str, created_by: int, include_cancelling: bool = True
) -> int:
    """Queued or running jobs of ``kind`` started by ``created_by``.

    ``include_cancelling=False`` leaves out running jobs whose cancel was requested —
    only for kinds whose handler really stops on a cancel (report runs and exports).
    """
    conditions = [
        TiqoraJob.kind == kind,
        TiqoraJob.created_by == created_by,
        TiqoraJob.status.in_(ACTIVE_STATUSES),
    ]
    if not include_cancelling:
        conditions.append(TiqoraJob.cancel_requested.is_(False))
    return int(
        (
            await session.execute(select(func.count()).select_from(TiqoraJob).where(*conditions))
        ).scalar_one()
    )


async def find_active_job(session: AsyncSession, *, kind: str, dedupe_key: str) -> TiqoraJob | None:
    return (
        (
            await session.execute(
                select(TiqoraJob)
                .where(
                    TiqoraJob.kind == kind,
                    TiqoraJob.dedupe_key == dedupe_key,
                    TiqoraJob.status.in_(ACTIVE_STATUSES),
                    TiqoraJob.cancel_requested.is_(False),
                )
                .order_by(TiqoraJob.id)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def get_job(session: AsyncSession, job_id: int) -> TiqoraJob | None:
    return await session.get(TiqoraJob, job_id)


async def list_jobs(
    session: AsyncSession, *, created_by: int | None, limit: int = 50
) -> list[TiqoraJob]:
    stmt = select(TiqoraJob).order_by(TiqoraJob.id.desc()).limit(limit)
    if created_by is not None:
        stmt = stmt.where(TiqoraJob.created_by == created_by)
    return list((await session.execute(stmt)).scalars().all())


async def list_job_files(
    session: AsyncSession, job_ids: Sequence[int]
) -> dict[int, list[TiqoraJobFile]]:
    out: dict[int, list[TiqoraJobFile]] = {}
    if not job_ids:
        return out
    rows = (
        (
            await session.execute(
                select(TiqoraJobFile)
                .where(TiqoraJobFile.job_id.in_(list(job_ids)))
                .order_by(TiqoraJobFile.id)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        out.setdefault(row.job_id, []).append(row)
    return out


async def get_job_file(session: AsyncSession, job_id: int, file_id: int) -> TiqoraJobFile | None:
    row = (
        await session.execute(
            select(TiqoraJobFile)
            .options(undefer(TiqoraJobFile.data))
            .where(TiqoraJobFile.id == file_id, TiqoraJobFile.job_id == job_id)
        )
    ).scalar_one_or_none()
    return row


async def request_cancel(session: AsyncSession, job: TiqoraJob) -> TiqoraJob:
    if job.status in TERMINAL_STATUSES:
        raise JobNotCancellable(job.status)
    if job.status == "queued":
        # Guarded: a worker may claim the job between our read and this write.
        result = await session.execute(
            update(TiqoraJob)
            .where(TiqoraJob.id == job.id, TiqoraJob.status == "queued")
            .values(status="cancelled", finished_at=utc_now())
        )
        if result.rowcount == 1:  # type: ignore[attr-defined]
            await session.refresh(job)
            return job
    # Running (or claimed meanwhile). Guarded write, so a repeatable-read snapshot
    # (MariaDB) cannot hide a state change; rowcount 0 means the job is terminal.
    result = await session.execute(
        update(TiqoraJob)
        .where(TiqoraJob.id == job.id, TiqoraJob.status == "running")
        .values(cancel_requested=True)
    )
    await session.refresh(job)
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise JobNotCancellable(job.status)
    return job


async def purge_finished_jobs(session: AsyncSession, *, older_than: datetime) -> int:
    ids = [
        r[0]
        for r in (
            await session.execute(
                select(TiqoraJob.id).where(
                    TiqoraJob.status.in_(TERMINAL_STATUSES),
                    TiqoraJob.finished_at.is_not(None),
                    TiqoraJob.finished_at < older_than,
                )
            )
        ).all()
    ]
    if not ids:
        return 0
    await session.execute(delete(TiqoraJobFile).where(TiqoraJobFile.job_id.in_(ids)))
    await session.execute(delete(TiqoraJob).where(TiqoraJob.id.in_(ids)))
    await session.flush()
    return len(ids)


def can_view_job(job: TiqoraJob, *, user_id: int, is_admin: bool) -> bool:
    return is_admin or (job.created_by is not None and job.created_by == user_id)


def can_download_job_file(job: TiqoraJob, *, user_id: int) -> bool:
    """Job files are built from the creator's permissions: creator-only, admins included."""
    return job.created_by is not None and job.created_by == user_id
