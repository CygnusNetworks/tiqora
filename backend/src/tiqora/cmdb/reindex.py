"""``core.cmdb_reindex``: rebuild the search index of one class after its searchable set changed.

``ClassService.add_definition`` enqueues the job (dedupe ``class:<id>``) when a new current
definition marks other attribute paths as searchable (ruling F10). The job walks the class's CIs
in id order, ``BATCH`` at a time, and rebuilds each batch from the CI's current version and the
class's **current** definition (``rebuild_index``), committing per batch. It is idempotent, so a
requeue after a crash simply runs it again.

A second definition change while the job runs does not queue a new job: the enqueue joins the
running one (dedupe ``class:<id>``). Batches done before the change were built from the older
definition, so the job reads ``current_definition_id`` at the start and again at the end and runs
the walk again if it moved (ruling CMDB-FINAL-MA), at most :data:`MAX_PASSES` times; a definition
still moving after the last walk is logged as a warning (ruling CMDB-PM-I3, m5).
"""

from __future__ import annotations

from typing import Final

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.cmdb.classes import CMDB_REINDEX_KIND
from tiqora.cmdb.index import rebuild_index
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraCmdbClass
from tiqora.jobs.context import JobContext
from tiqora.jobs.registry import JobHandler

BATCH: Final = 1000
#: the walk plus up to two more while the definition moved meanwhile (CMDB-FINAL-MA, CMDB-PM-I3)
MAX_PASSES: Final = 3

logger = structlog.get_logger(__name__)


class ReindexPayload(BaseModel):
    class_id: int = Field(ge=1)


async def _current_definition_id(
    factory: async_sessionmaker[AsyncSession], class_id: int
) -> int | None:
    async with factory() as session:
        value = (
            await session.execute(
                select(TiqoraCmdbClass.current_definition_id).where(TiqoraCmdbClass.id == class_id)
            )
        ).scalar_one_or_none()
    return None if value is None else int(value)


async def reindex_class(
    factory: async_sessionmaker[AsyncSession],
    class_id: int,
    *,
    batch: int = BATCH,
    progress: JobContext | None = None,
) -> int:
    """Rebuild the index of every CI of ``class_id``; returns the number of CIs rebuilt.

    Walks the class again when its current definition changed during the walk, at most
    :data:`MAX_PASSES` walks in all."""
    done = 0
    for attempt in range(MAX_PASSES):
        started_with = await _current_definition_id(factory, class_id)
        done += await _walk(factory, class_id, batch=batch, progress=progress)
        ended_with = await _current_definition_id(factory, class_id)
        if ended_with == started_with:
            break
        fields = {
            "class_id": class_id,
            "pass_no": attempt + 1,
            "started_with": started_with,
            "ended_with": ended_with,
        }
        if attempt + 1 < MAX_PASSES:
            logger.info("cmdb_reindex_definition_moved", **fields)
        else:  # its enqueue joined this job, so nothing walks the class with it
            logger.warning("cmdb_reindex_definition_still_moving", **fields)
    return done


async def _walk(
    factory: async_sessionmaker[AsyncSession],
    class_id: int,
    *,
    batch: int,
    progress: JobContext | None,
) -> int:
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

__all__ = ["BATCH", "CMDB_REINDEX_JOB", "MAX_PASSES", "ReindexPayload", "reindex_class"]
