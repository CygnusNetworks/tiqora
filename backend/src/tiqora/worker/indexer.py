"""Meilisearch bulk backfill and incremental ticket re-index tasks."""

from __future__ import annotations

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.search import SearchIndexService
from tiqora.domain.settings_store import (
    KEY_INDEX_REBUILD_STATUS,
    KEY_INDEX_REBUILD_WATERMARK,
    get_setting_int,
    set_setting,
)

logger = structlog.get_logger(__name__)


async def rebuild_index(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    batch_size: int | None = None,
    resume: bool = True,
) -> dict[str, int]:
    """Bulk-index all tickets into Meilisearch in batches (resumable).

    Watermark key ``index.rebuild.ticket_id`` stores the last fully processed id.
    """
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()
    size = batch_size or cfg.index_batch_size
    total_indexed = 0
    batches = 0

    async with factory() as session:
        await set_setting(session, KEY_INDEX_REBUILD_STATUS, "running")
        watermark = await get_setting_int(session, KEY_INDEX_REBUILD_WATERMARK, 0) if resume else 0
        max_id = (
            await session.execute(select(func.coalesce(func.max(Ticket.id), 0)))
        ).scalar_one()
        max_id = int(max_id)

        svc = SearchIndexService(session, cfg)
        try:
            await svc.ensure_index()
            current = watermark
            while current < max_id:
                rows = (
                    await session.execute(
                        select(Ticket.id)
                        .where(Ticket.id > current)
                        .order_by(Ticket.id)
                        .limit(size)
                    )
                ).scalars().all()
                if not rows:
                    break
                ids = list(rows)
                n = await svc.index_tickets(ids)
                total_indexed += n
                batches += 1
                current = ids[-1]
                await set_setting(session, KEY_INDEX_REBUILD_WATERMARK, str(current))
                logger.info(
                    "index_rebuild_batch",
                    batch=batches,
                    indexed=n,
                    watermark=current,
                    max_id=max_id,
                )
            await set_setting(session, KEY_INDEX_REBUILD_STATUS, "done")
        except Exception:
            await set_setting(session, KEY_INDEX_REBUILD_STATUS, "error")
            raise
        finally:
            await svc.close()

    final_wm = current if batches else watermark
    logger.info("index_rebuild_complete", total_indexed=total_indexed, batches=batches)
    return {"total_indexed": total_indexed, "batches": batches, "watermark": final_wm}


async def reindex_ticket_ids(
    ticket_ids: list[int],
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Re-index a set of tickets (used by the Znuny-write poller)."""
    if not ticket_ids:
        return 0
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()
    async with factory() as session:
        svc = SearchIndexService(session, cfg)
        try:
            return await svc.index_tickets(sorted(set(ticket_ids)))
        finally:
            await svc.close()
