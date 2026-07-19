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


scheduler = TaskiqScheduler(broker=broker, sources=[LabelScheduleSource(broker)])
