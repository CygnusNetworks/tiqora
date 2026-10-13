"""``core.cmdb_reindex``: rebuild the search index of one class after its searchable set changed.

``ClassService.add_definition`` enqueues the job (dedupe ``class:<id>``) when a new current
definition marks other attribute paths as searchable (ruling F10). The job walks the class's CIs
in id order, ``BATCH`` at a time, and rebuilds each batch from the CI's current version and the
class's **current** definition (``rebuild_index``), committing per batch. It is idempotent, so a
requeue after a crash simply runs it again.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.cmdb.classes import CMDB_REINDEX_KIND
from tiqora.cmdb.index import rebuild_index
from tiqora.db.tiqora.models import TiqoraCmdbCi
from tiqora.jobs.context import JobContext
from tiqora.jobs.registry import JobHandler

BATCH: Final = 1000


class ReindexPayload(BaseModel):
    class_id: int = Field(ge=1)


async def reindex_class(
    factory: async_sessionmaker[AsyncSession],
    class_id: int,
    *,
    batch: int = BATCH,
    progress: JobContext | None = None,
) -> int:
    """Rebuild the index of every CI of ``class_id``; returns the number of CIs rebuilt."""
    async with factory() as session:
        total = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TiqoraCmdbCi)
                    .where(TiqoraCmdbCi.class_id == class_id)
                )
            ).scalar_one()
        )
    done = 0
    last = 0
    while True:
        async with factory() as session:
            ids = [
                int(i)
                for i in (
                    await session.execute(
                        select(TiqoraCmdbCi.id)
                        .where(TiqoraCmdbCi.class_id == class_id, TiqoraCmdbCi.id > last)
                        .order_by(TiqoraCmdbCi.id)
                        .limit(batch)
                    )
                )
                .scalars()
                .all()
            ]
            if not ids:
                break
            done += await rebuild_index(session, ids)
            await session.commit()
        last = ids[-1]
        if progress is not None:
            await progress.progress(done, max(total, done), "Suchindex wird neu aufgebaut …")
        if len(ids) < batch:
            break
    return done


async def run_reindex_job(ctx: JobContext, payload: ReindexPayload) -> dict[str, int]:
    rebuilt = await reindex_class(ctx.session_factory, payload.class_id, progress=ctx)
    return {"rebuilt": rebuilt}


CMDB_REINDEX_JOB = JobHandler(
    kind=CMDB_REINDEX_KIND,
    payload_model=ReindexPayload,
    run=run_reindex_job,
    title_key="jobs.kinds.core.cmdb_reindex",
    timeout_seconds=3600,
    requeue_if_interrupted=True,  # idempotent: a rerun rebuilds the same rows again
    max_attempts=3,
)

__all__ = ["BATCH", "CMDB_REINDEX_JOB", "ReindexPayload", "reindex_class"]
