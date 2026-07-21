"""taskiq broker and scheduled tasks (indexer poller)."""

from __future__ import annotations

from typing import Any

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from tiqora.config import get_settings

settings = get_settings()

result_backend: Any = RedisAsyncResultBackend(redis_url=settings.redis_url)
broker = ListQueueBroker(url=settings.redis_url).with_result_backend(result_backend)


@broker.task(
    schedule=[{"cron": f"*/{max(1, settings.poller_interval_seconds // 60) or 1} * * * *"}]
    if settings.poller_interval_seconds >= 60
    else [{"cron": "* * * * *"}],  # every minute minimum for cron; see run_poller_loop
)
async def poller_task() -> dict[str, int]:
    """Scheduled poller entry (also invokable ad-hoc)."""
    from tiqora.worker.poller import poll_once

    return await poll_once()


@broker.task
async def rebuild_index_task(resume: bool = True) -> dict[str, int]:
    from tiqora.worker.indexer import rebuild_index

    return await rebuild_index(resume=resume)


@broker.task(
    schedule=[{"cron": "* * * * *"}],
)
async def drain_outbox_task() -> dict[str, int]:
    """Drain tiqora_event_outbox: re-index affected tickets in Meilisearch."""
    from tiqora.worker.outbox_drain import drain_outbox

    return await drain_outbox()


@broker.task(
    schedule=[{"cron": "0 3 * * *"}],  # once daily at 03:00 — a slow, batchy sweep
)
async def gdpr_retention_task() -> dict[str, int]:
    """Feature-flagged GDPR retention sweep; no-op unless both the
    ``gdpr.retention.enabled`` flag and the schema-ownership gate are active.
    """
    from tiqora.worker.gdpr_retention import run_gdpr_retention_tick

    return await run_gdpr_retention_tick()


@broker.task(
    schedule=[{"cron": "30 3 * * *"}],  # daily after retention — purge expired backups
)
async def gdpr_erasure_purge_task() -> dict[str, int]:
    """Purge GDPR erasure backups past the 30-day window. Default ON
    (``gdpr.erasure.purge_enabled``); no ownership gate — only touches tiqora_*.
    """
    from tiqora.worker.gdpr_erasure_purge import run_gdpr_erasure_purge_tick

    return await run_gdpr_erasure_purge_tick()


scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
