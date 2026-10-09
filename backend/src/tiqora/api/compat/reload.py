"""Hot reload of GenericInterface webservice configs (no restart needed).

Every API process polls the ``gi_webservice_config`` fingerprint on its own; a Redis message on
:data:`COMPAT_WEBSERVICES_CHANNEL` only makes Tiqora-triggered reloads immediate. The channel is
deliberately NOT ``tiqora:events`` -- that one is relayed to browser SSE clients.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Final

import structlog

from tiqora.api.compat.webservices import get_registry

logger = structlog.get_logger(__name__)

COMPAT_WEBSERVICES_CHANNEL: Final = "tiqora:compat:webservices"


async def publish_webservices_changed(redis_client: Any) -> None:
    """Wake up the reloaders of all API processes. Best effort, never raises."""
    if redis_client is None:
        return
    try:
        await redis_client.publish(COMPAT_WEBSERVICES_CHANNEL, "reload")
    except Exception as exc:  # noqa: BLE001 - polling still picks the change up
        logger.warning("compat_reload_publish_failed", error=str(exc))


async def reload_tick(app: Any, *, force: bool) -> bool:
    """One registry refresh in its own session; True when the snapshot was replaced."""
    async with app.state.session_factory() as session:
        return await get_registry(app).refresh(session, force=force)


async def _safe_tick(app: Any, *, force: bool) -> None:
    try:
        await reload_tick(app, force=force)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any tick failure
        logger.warning("compat_webservice_reload_failed", error=str(exc))


async def _close_pubsub(pubsub: Any) -> None:
    with contextlib.suppress(Exception):
        await pubsub.unsubscribe(COMPAT_WEBSERVICES_CHANNEL)
    with contextlib.suppress(Exception):
        await pubsub.aclose()


async def _cancel(task: asyncio.Task[Any] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


async def _wait_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), max(seconds, 0))


async def _subscribe(app: Any) -> Any:
    """A subscribed pubsub, or None (no Redis / any failure). A failed pubsub is closed here so
    a flapping Redis never leaks pooled connections."""
    redis_client = getattr(app.state, "redis", None)
    if redis_client is None:
        return None
    candidate: Any = None
    try:
        candidate = redis_client.pubsub()
        await candidate.subscribe(COMPAT_WEBSERVICES_CHANNEL)
    except Exception as exc:  # noqa: BLE001 - fall back to plain polling
        logger.warning("compat_reload_pubsub_error", error=str(exc))
        if candidate is not None:
            await _close_pubsub(candidate)
        return None
    except BaseException:
        # cancelled (shutdown) halfway through: close the candidate, then let it propagate
        if candidate is not None:
            await asyncio.shield(_close_pubsub(candidate))
        raise
    return candidate


async def _drain(pubsub: Any) -> None:
    """Swallow already-queued wake-ups so a burst causes a single forced reload."""
    for _ in range(100):
        if await pubsub.get_message(ignore_subscribe_messages=True, timeout=0) is None:
            return


async def run_webservice_reloader(
    app: Any, *, interval_seconds: float, stop: asyncio.Event
) -> None:
    """Poll every ``interval_seconds`` (fingerprint check) and reload at once on a Redis message
    (forced). Returns promptly once ``stop`` is set; never leaves child tasks behind."""
    min_iteration = min(interval_seconds, 1.0)
    loop = asyncio.get_running_loop()
    pubsub: Any = None
    msg_task: asyncio.Task[Any] | None = None
    stop_task: asyncio.Task[Any] = asyncio.create_task(stop.wait())
    try:
        while not stop.is_set():
            started = loop.time()
            if pubsub is None:
                pubsub = await _subscribe(app)
            force = False
            if pubsub is not None:
                if msg_task is None:
                    msg_task = asyncio.create_task(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=interval_seconds)
                    )
                await asyncio.wait({msg_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                if stop.is_set():
                    break
                finished, msg_task = msg_task, None
                try:
                    force = finished.result() is not None
                    if force:
                        await _drain(pubsub)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("compat_reload_pubsub_error", error=str(exc))
                    await _close_pubsub(pubsub)
                    pubsub = None
                    await _wait_stop(stop, interval_seconds)  # no hot loop while Redis is down
                    if stop.is_set():
                        break
                else:
                    if not force:
                        # get_message may return None immediately; keep a minimum iteration time
                        await _wait_stop(stop, min_iteration - (loop.time() - started))
            else:
                await _wait_stop(stop, interval_seconds)
            if stop.is_set():
                break
            await _safe_tick(app, force=force)
    finally:
        await _cancel(msg_task)
        await _cancel(stop_task)
        if pubsub is not None:
            await _close_pubsub(pubsub)
