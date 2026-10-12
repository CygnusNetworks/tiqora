"""Version archive (D10): old CI versions move from the hot table to ``..._version_archive``.

A version is archived when it is older than ``min_age_days`` **and** not among the newest
``keep`` versions of its CI (the current version is always the newest, so it never moves).
The archive keeps the original version id, so ids stay one id space and
:meth:`tiqora.cmdb.service.CiService.get_version` / ``list_versions(include_archived=True)``
read both tables. The move is idempotent: a version that is already in the archive is only
removed from the hot table, and every batch is one transaction (insert + delete), so a crash
leaves each version in at least one table.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

from pydantic import BaseModel
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraCmdbCiVersion, TiqoraCmdbCiVersionArchive
from tiqora.jobs.context import JobContext
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import utc_now

ARCHIVE_JOB_KIND: Final = "core.cmdb_archive"
_CI_CHUNK: Final = 500

Progress = Callable[[int, int | None], Awaitable[None]]

_V = TiqoraCmdbCiVersion
_A = TiqoraCmdbCiVersionArchive


async def _move(session: AsyncSession, ids: list[int], archived_at: datetime) -> int:
    """Copy the hot rows ``ids`` into the archive (skipping ids already there), then delete them."""
    already = set((await session.execute(select(_A.id).where(_A.id.in_(ids)))).scalars().all())
    rows = (await session.execute(select(_V).where(_V.id.in_(ids)).order_by(_V.id))).scalars().all()
    fresh = [
        {
            "id": r.id,
            "ci_id": r.ci_id,
            "definition_id": r.definition_id,
            "name": r.name,
            "deployment_state_id": r.deployment_state_id,
            "incident_state_id": r.incident_state_id,
            "attributes": r.attributes,
            "change_note": r.change_note,
            "created_by": r.created_by,
            "created_at": r.created_at,
            "archived_at": archived_at,
        }
        for r in rows
        if int(r.id) not in already
    ]
    if fresh:
        await session.execute(insert(_A), fresh)
    await session.execute(
        delete(_V).where(_V.id.in_(ids)).execution_options(synchronize_session=False)
    )
    return len(rows)


async def archive_old_versions(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    keep: int,
    min_age_days: int,
    batch: int = 500,
    progress: Progress | None = None,
) -> int:
    """Archive the old versions of every CI; returns how many versions moved.

    Only CIs with more than ``keep`` versions (``version_count``) are looked at. Per CI the
    ``keep`` newest versions and the current version stay hot, the rest moves once it is older
    than ``min_age_days``, at most ``batch`` versions per transaction.
    """
    keep = max(1, int(keep))
    batch = max(1, int(batch))
    now = utc_now()
    cutoff = now - timedelta(days=int(min_age_days))
    moved = 0
    done = 0
    last = 0
    while True:
        async with session_factory() as session:
            chunk = (
                await session.execute(
                    select(TiqoraCmdbCi.id, TiqoraCmdbCi.current_version_id)
                    .where(TiqoraCmdbCi.id > last, TiqoraCmdbCi.version_count > keep)
                    .order_by(TiqoraCmdbCi.id)
                    .limit(_CI_CHUNK)
                )
            ).all()
        if not chunk:
            break
        for ci_id, current in chunk:
            while True:
                async with session_factory() as session:
                    # the keep-th newest version is the oldest one that stays hot
                    border = (
                        await session.execute(
                            select(_V.id)
                            .where(_V.ci_id == ci_id)
                            .order_by(_V.id.desc())
                            .offset(keep - 1)
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if border is None:
                        break
                    stmt = (
                        select(_V.id)
                        .where(_V.ci_id == ci_id, _V.id < border, _V.created_at < cutoff)
                        .order_by(_V.id)
                        .limit(batch)
                    )
                    if current is not None:
                        stmt = stmt.where(_V.id != current)
                    ids = [int(i) for i in (await session.execute(stmt)).scalars().all()]
                    if not ids:
                        break
                    moved += await _move(session, ids, now)
                    await session.commit()
                if len(ids) < batch:
                    break
        last = int(chunk[-1][0])
        done += len(chunk)
        if progress is not None:
            await progress(done, None)
    return moved


class ArchivePayload(BaseModel):
    """No parameters: keep and age come from the ``cmdb`` settings when the job runs."""


async def run_archive_job(ctx: JobContext, payload: ArchivePayload) -> dict[str, int]:
    _ = payload
    async with ctx.session_factory() as session:
        settings = await load_cmdb_settings(session)

    async def report(done: int, total: int | None) -> None:
        await ctx.progress(done, total, "Alte CI-Versionen werden archiviert …")

    archived = await archive_old_versions(
        ctx.session_factory,
        keep=settings.version_archive_keep,
        min_age_days=settings.version_archive_min_age_days,
        progress=report,
    )
    return {"archived": archived}


CMDB_ARCHIVE_JOB = JobHandler(
    kind=ARCHIVE_JOB_KIND,
    payload_model=ArchivePayload,
    run=run_archive_job,
    title_key="jobs.kinds.core.cmdb_archive",
    timeout_seconds=3600,
    requeue_if_interrupted=True,  # idempotent: a requeue after a crash only moves what is left
    max_attempts=3,
)

__all__ = ["ARCHIVE_JOB_KIND", "CMDB_ARCHIVE_JOB", "archive_old_versions"]
