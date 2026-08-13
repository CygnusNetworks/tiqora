"""Telegram Bot API HTTP surface, mounted at ``/api/v1/channels/telegram``.

Disabled by default — see ``channel.telegram.enabled`` and docs/channels.md.
Two transports exist for inbound updates (``channel.telegram.mode``,
default ``"polling"``): the long-poll daemon in
``tiqora.worker.telegram_poller`` and the webhook route below; exactly one
should be active for a given bot at a time (running both would double-process
every update). ``/webhook-register`` and ``/webhook-unregister`` let an admin
flip Telegram's own webhook subscription; they don't touch ``mode`` itself —
an operator still has to set ``channel.telegram.mode = "webhook"`` via
``PUT /api/v1/admin/channels/telegram`` before ``/webhook`` will accept
deliveries (409 otherwise).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.channels.common import channel_enabled, channel_setting, verify_shared_secret
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.service import CHANNEL_NAME, build_gateway, process_update
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import KEY_TELEGRAM_UPDATE_OFFSET, get_setting, set_setting
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/channels/telegram", tags=["channels:telegram"])

_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class TelegramWebhookResponse(BaseModel):
    ok: bool
    skipped: bool = False


class TelegramWebhookRegisterRequest(BaseModel):
    url: str | None = None


class TelegramWebhookRegisterResponse(BaseModel):
    ok: bool
    url: str


async def _require_enabled(session: DbSession) -> None:
    if not await channel_enabled(session, CHANNEL_NAME):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Telegram channel disabled"
        )


async def _require_mode(session: DbSession, expected: str) -> None:
    mode = await channel_setting(session, CHANNEL_NAME, "mode") or "polling"
    if mode != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"channel.telegram.mode is {mode!r}, expected {expected!r}",
        )


@router.post("/webhook", response_model=TelegramWebhookResponse)
async def receive_webhook(
    request: Request,
    session: DbSession,
    x_secret: str | None = Header(default=None, alias=_SECRET_HEADER),
) -> TelegramWebhookResponse:
    """Telegram's webhook delivery: one update per request."""
    await _require_enabled(session)
    await _require_mode(session, "webhook")

    expected_secret = await channel_setting(session, CHANNEL_NAME, "webhook_secret_token")
    if not verify_shared_secret(expected_secret, x_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret token")

    update: dict[str, Any] = await request.json()
    update_id = int(update["update_id"])

    raw_offset = await get_setting(session, KEY_TELEGRAM_UPDATE_OFFSET)
    offset = int(raw_offset) if raw_offset else None
    if offset is not None and update_id < offset:
        return TelegramWebhookResponse(ok=True, skipped=True)

    factory = get_session_factory()
    sysconfig = SysConfig(session)
    gateway = await build_gateway(session)
    await process_update(session, factory, sysconfig, gateway, update, user_id=1)
    # set_setting commits the whole session, so the article/ticket writes
    # above land atomically with the offset advance.
    await set_setting(session, KEY_TELEGRAM_UPDATE_OFFSET, str(update_id + 1))
    return TelegramWebhookResponse(ok=True)


@router.post("/webhook-register", response_model=TelegramWebhookRegisterResponse)
async def register_webhook(
    body: TelegramWebhookRegisterRequest,
    admin: AdminUser,
    session: DbSession,
) -> TelegramWebhookRegisterResponse:
    _ = admin
    bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
    url = body.url or await channel_setting(session, CHANNEL_NAME, "webhook_url")
    secret = await channel_setting(session, CHANNEL_NAME, "webhook_secret_token")
    required = (("bot_token", bot_token), ("webhook_url", url), ("webhook_secret_token", secret))
    missing = [name for name, value in required if not value]
    if missing or bot_token is None or url is None or secret is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Missing Telegram config: {', '.join(missing)}",
        )

    gateway = TelegramGateway(bot_token=bot_token)
    try:
        await gateway.set_webhook(url, secret_token=secret, allowed_updates=["message"])
    except TelegramApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return TelegramWebhookRegisterResponse(ok=True, url=url)


@router.post("/webhook-unregister", response_model=TelegramWebhookResponse)
async def unregister_webhook(admin: AdminUser, session: DbSession) -> TelegramWebhookResponse:
    _ = admin
    bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
    if not bot_token:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Missing Telegram config: bot_token"
        )
    gateway = TelegramGateway(bot_token=bot_token)
    try:
        await gateway.delete_webhook()
    except TelegramApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return TelegramWebhookResponse(ok=True)
