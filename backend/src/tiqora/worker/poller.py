"""Znuny-write poller: watch ticket_history and article watermarks, re-index tickets."""

from __future__ import annotations

from typing import NamedTuple

import structlog
from prometheus_client import Counter, Gauge
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.article import Article, ArticleSenderType
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import Ticket, TicketHistory, TicketHistoryType
from tiqora.domain.settings_store import (
    KEY_ARTICLE_WATERMARK,
    KEY_HISTORY_WATERMARK,
    get_setting_int,
    set_setting,
)
from tiqora.events.pubsub import (
    get_pubsub_redis,
    publish_new_ticket_in_queue,
    publish_ticket_event,
)
from tiqora.worker.indexer import reindex_ticket_ids

#: History type that marks a brand-new ticket, and the article sender type
#: that marks a customer reply — both used to decide which touched tickets
#: warrant a "new mail in your queue" bell/toast (vs. any internal change).
_NEW_TICKET_HISTORY_TYPE = "NewTicket"
_CUSTOMER_SENDER_TYPE = "customer"


class _NotifyMeta(NamedTuple):
    """Payload for one ``ticket_new_in_queue`` notification."""

    ticket_id: int
    tn: str
    title: str
    queue_id: int
    queue_name: str


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
    # Tickets that warrant a "new mail in your queue" bell/toast: brand-new
    # tickets (NewTicket history) or new customer replies. A subset of
    # ``ticket_ids`` — an internal note-only change never lands here.
    notify_ticket_ids: set[int] = set()

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
                    select(
                        TicketHistory.ticket_id,
                        TicketHistory.id,
                        TicketHistoryType.name,
                    )
                    .join(
                        TicketHistoryType,
                        TicketHistoryType.id == TicketHistory.history_type_id,
                    )
                    .where(TicketHistory.id > hist_wm)
                )
            ).all()
            for tid, _hid, htype in rows:
                ticket_ids.add(int(tid))
                if htype == _NEW_TICKET_HISTORY_TYPE:
                    notify_ticket_ids.add(int(tid))
            await set_setting(session, KEY_HISTORY_WATERMARK, str(max_hist))

        if max_art > art_wm:
            rows = (
                await session.execute(
                    select(
                        Article.ticket_id,
                        Article.id,
                        ArticleSenderType.name,
                    )
                    .join(
                        ArticleSenderType,
                        ArticleSenderType.id == Article.article_sender_type_id,
                    )
                    .where(Article.id > art_wm)
                )
            ).all()
            for tid, _aid, sender in rows:
                ticket_ids.add(int(tid))
                if sender == _CUSTOMER_SENDER_TYPE:
                    notify_ticket_ids.add(int(tid))
            await set_setting(session, KEY_ARTICLE_WATERMARK, str(max_art))

        # Resolve tn/title/queue for the notify subset while the session is
        # still open, so the best-effort publish below needs no DB access.
        notify_meta: list[_NotifyMeta] = []
        if notify_ticket_ids:
            meta_rows = (
                await session.execute(
                    select(
                        Ticket.id,
                        Ticket.tn,
                        Ticket.title,
                        Ticket.queue_id,
                        Queue.name,
                    )
                    .join(Queue, Queue.id == Ticket.queue_id)
                    .where(Ticket.id.in_(notify_ticket_ids))
                )
            ).all()
            for tid, tn, title, qid, qname in meta_rows:
                notify_meta.append(
                    _NotifyMeta(int(tid), str(tn), str(title or ""), int(qid), str(qname))
                )

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
                # Richer per-queue notification for genuinely-new tickets /
                # customer replies (the SSE endpoint filters these to each
                # agent's readable queues).
                for meta in sorted(notify_meta, key=lambda m: m.ticket_id):
                    await publish_new_ticket_in_queue(
                        pubsub_client,
                        ticket_id=meta.ticket_id,
                        tn=meta.tn,
                        title=meta.title,
                        queue_id=meta.queue_id,
                        queue_name=meta.queue_name,
                    )
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
