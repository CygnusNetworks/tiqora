"""Job queue service: enqueue, read, cancel and purge. Never commits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

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
        job.status = "cancelled"
        job.finished_at = utc_now()
    else:
        job.cancel_requested = True
    await session.flush()
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
