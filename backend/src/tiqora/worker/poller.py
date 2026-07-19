"""Znuny-write poller: watch ticket_history and article watermarks, re-index tickets."""

from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.article import Article
from tiqora.db.legacy.ticket import TicketHistory
from tiqora.domain.settings_store import (
    KEY_ARTICLE_WATERMARK,
    KEY_HISTORY_WATERMARK,
    get_setting_int,
    set_setting,
)
from tiqora.events.pubsub import get_pubsub_redis, publish_ticket_event
from tiqora.worker.indexer import reindex_ticket_ids

logger = structlog.get_logger(__name__)

POLLER_HISTORY_LAG = Gauge(
    "tiqora_poller_history_lag",
    "Difference between max ticket_history.id and watermark",
)
POLLER_ARTICLE_LAG = Gauge(
    "tiqora_poller_article_lag",
    "Difference between max article.id and watermark",
)
POLLER_RUNS = Counter(
    "tiqora_poller_runs_total",
    "Znuny-write poller runs",
    ["status"],
)
POLLER_TICKETS = Counter(
    "tiqora_poller_tickets_reindexed_total",
    "Tickets re-indexed by the Znuny-write poller",
)


async def poll_once(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """Advance watermarks and re-index tickets touched since last run."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()
    ticket_ids: set[int] = set()

    async with factory() as session:
        hist_wm = await get_setting_int(session, KEY_HISTORY_WATERMARK, 0)
        art_wm = await get_setting_int(session, KEY_ARTICLE_WATERMARK, 0)

        max_hist = int(
            (
                await session.execute(select(func.coalesce(func.max(TicketHistory.id), 0)))
            ).scalar_one()
        )
        max_art = int(
            (await session.execute(select(func.coalesce(func.max(Article.id), 0)))).scalar_one()
        )

        POLLER_HISTORY_LAG.set(max(0, max_hist - hist_wm))
        POLLER_ARTICLE_LAG.set(max(0, max_art - art_wm))

        if max_hist > hist_wm:
            rows = (
                await session.execute(
                    select(TicketHistory.ticket_id, TicketHistory.id).where(
                        TicketHistory.id > hist_wm
                    )
                )
            ).all()
            for tid, _hid in rows:
                ticket_ids.add(int(tid))
            await set_setting(session, KEY_HISTORY_WATERMARK, str(max_hist))

        if max_art > art_wm:
            rows = (
                await session.execute(
                    select(Article.ticket_id, Article.id).where(Article.id > art_wm)
                )
            ).all()
            for tid, _aid in rows:
                ticket_ids.add(int(tid))
            await set_setting(session, KEY_ARTICLE_WATERMARK, str(max_art))

    indexed = 0
    try:
        if ticket_ids:
            indexed = await reindex_ticket_ids(
                list(ticket_ids),
                settings=cfg,
                session_factory=factory,
            )
            POLLER_TICKETS.inc(indexed)
            # Notify SSE subscribers of Znuny-side writes the poller found.
            # Best-effort — the exact Znuny event type isn't cheaply
            # threaded through here, so event="poller" tells the frontend
            # "this ticket changed" without a precise cause.
            try:
                pubsub_client = get_pubsub_redis(cfg)
                for tid in sorted(ticket_ids):
                    await publish_ticket_event(pubsub_client, tid, "poller")
            except Exception:  # noqa: BLE001 — pub/sub notification must not fail the poller
                logger.exception("poller_pubsub_publish_error")
        POLLER_RUNS.labels(status="ok").inc()
    except Exception:
        POLLER_RUNS.labels(status="error").inc()
        logger.exception("poller_failed", ticket_ids=sorted(ticket_ids)[:20])
        raise

    logger.info(
        "poller_tick",
        tickets=len(ticket_ids),
        indexed=indexed,
        history_wm=max_hist,
        article_wm=max_art,
        history_lag=max(0, max_hist - hist_wm),
        article_lag=max(0, max_art - art_wm),
    )
    return {
        "ticket_ids": len(ticket_ids),
        "indexed": indexed,
        "history_wm": max_hist,
        "article_wm": max_art,
    }
