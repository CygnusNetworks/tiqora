"""Telegram long-poll daemon — feature-flagged takeover of Telegram Bot API
``getUpdates``.

Gated by ``daemon.telegram_poller.enabled`` (default OFF — see
``tiqora.domain.settings_store``), the channel's own switch
(``channel.telegram.enabled``), and ``channel.telegram.mode`` (must be
``"polling"``, the default; a ``"webhook"`` deployment must not also poll or
every update would be delivered twice).

Each update gets its own session/transaction: :func:`process_update` and the
``channel.telegram.update_offset`` advance share one commit (``set_setting``
commits its session — see ``tiqora.domain.settings_store``), so the article
write and the offset advance are atomic. On a per-update failure the tick
aborts immediately rather than skipping ahead, so the offset never advances
past a message that was never actually processed (Telegram has no redelivery
once ``getUpdates`` acknowledges an offset) — the next tick retries it.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import channel_enabled, channel_setting
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.service import CHANNEL_NAME, process_update
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_TELEGRAM_POLLER_ENABLED,
    KEY_TELEGRAM_UPDATE_OFFSET,
    get_setting,
    get_setting_bool,
    set_setting,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)


async def run_telegram_poller_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    gateway: TelegramGateway | None = None,
) -> dict[str, Any]:
    """One scheduler tick: check the feature flags, ``getUpdates``, then
    dispatch every update in order, aborting on the first failure.

    *gateway* is normally built from the stored ``bot_token`` after the gates
    pass, but can be injected (tests: an ``httpx.MockTransport``-backed fake)
    — when a gate fails, the injected fake is never called, same as
    ``mail_sender`` in ``run_postmaster_tick``.
    """
    _ = settings or get_settings()  # reserved for future config-driven tuning
    factory = session_factory or get_session_factory()

    async with factory() as session:
        if not await get_setting_bool(session, KEY_TELEGRAM_POLLER_ENABLED, False):
            logger.debug("telegram_poller_disabled")
            return {"enabled": 0}
        if not await channel_enabled(session, CHANNEL_NAME):
            return {"channel_disabled": 1}
        mode = await channel_setting(session, CHANNEL_NAME, "mode") or "polling"
        if mode != "polling":
            return {"skipped_mode_webhook": 1}
        bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
        if not bot_token:
            return {"no_token": 1}
        raw_offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
        offset = int(raw_offset) if raw_offset else None

    gateway = gateway or TelegramGateway(bot_token=bot_token)
    try:
        updates = await gateway.get_updates(
            offset=offset, timeout=20, allowed_updates=["message", "callback_query"]
        )
    except TelegramApiError as exc:
        logger.warning("telegram_poller_get_updates_failed", error=str(exc))
        return {"updates": 0, "articles": 0, "tickets_created": 0, "skipped": 0}

    totals = {"updates": len(updates), "articles": 0, "tickets_created": 0, "skipped": 0}

    for update in updates:
        update_id = int(update["update_id"])
        if offset is not None and update_id < offset:
            totals["skipped"] += 1
            continue
        try:
            async with factory() as session:
                sysconfig = SysConfig(session)
                result = await process_update(
                    session, factory, sysconfig, gateway, update, user_id=1
                )
                if "skipped" in result:
                    totals["skipped"] += 1
                else:
                    totals["articles"] += 1
                    if result.get("created_ticket"):
                        totals["tickets_created"] += 1
                # set_setting commits the whole session, so this advance and
                # the article/ticket writes above land in one transaction.
                await set_setting(session, KEY_TELEGRAM_UPDATE_OFFSET, str(update_id + 1))
        except Exception:  # noqa: BLE001 — abort the tick, never skip past a failed update
            logger.exception("telegram_poller_update_failed", update_id=update_id)
            break

    logger.info("telegram_poller_tick", **totals)
    return totals


__all__ = ["run_telegram_poller_tick"]
