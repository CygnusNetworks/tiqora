"""Telegram Bot API HTTP client: long-poll updates, send, webhook management,
media download.

The bot token is part of every Bot API URL (``.../bot<TOKEN>/<method>``), so
unlike the WhatsApp/Meta driver (bearer header) it can leak into httpx
exception text (which echoes the request URL) or log lines if we're not
careful. Every place that might surface an exception or log message scrubs
the token first — never log ``self._token`` and never let a raw httpx/URL
exception escape unscrubbed.
"""

from __future__ import annotations

import mimetypes
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

API_BASE = "https://api.telegram.org"
FILE_API_BASE = "https://api.telegram.org/file"

_TOKEN_MASK = "***"


class TelegramApiError(Exception):
    """Telegram Bot API call failed. Message is always token-scrubbed."""


class TelegramGateway:
    def __init__(
        self,
        *,
        bot_token: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 25.0,
    ) -> None:
        self._token = bot_token
        self._client = client
        self._timeout = timeout

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, _TOKEN_MASK) if self._token else text

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self._token}/{method}"

    def _file_url(self, file_path: str) -> str:
        return f"{FILE_API_BASE}/bot{self._token}/{file_path}"

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            if self._client is not None:
                return await self._client.request(method, url, timeout=self._timeout, **kwargs)
            async with httpx.AsyncClient() as client:
                return await client.request(method, url, timeout=self._timeout, **kwargs)
        except httpx.HTTPError as exc:
            # httpx exception __str__ can embed the request URL (token!).
            raise TelegramApiError(self._scrub(str(exc))) from None

    async def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        """POST *method* with a JSON body; return the ``result`` payload.

        Raises :class:`TelegramApiError` (token-scrubbed) on transport errors,
        non-2xx responses, or ``{"ok": false, ...}``.
        """
        resp = await self._request("POST", self._url(method), json=payload or {})
        try:
            data = resp.json()
        except ValueError:
            raise TelegramApiError(
                f"{method}: non-JSON response (status {resp.status_code})"
            ) from None
        if not data.get("ok"):
            desc = str(data.get("description", "unknown error"))
            raise TelegramApiError(f"{method} failed: {self._scrub(desc)}")
        return data.get("result")

    async def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 20,  # noqa: ASYNC109 — Telegram long-poll wait, not asyncio.timeout
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Long-poll ``getUpdates``. *timeout* is the Telegram-side long-poll
        wait (seconds); the httpx request timeout must exceed it — callers
        polling with a non-default *timeout* should size the gateway's
        ``timeout=`` accordingly."""
        payload: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        result = await self._call("getUpdates", payload)
        return list(result or [])

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a plain-text message (no ``parse_mode`` — caller text is not
        Markdown/HTML-escaped). *reply_markup*, when given, is sent verbatim
        as the Bot API ``reply_markup`` field (e.g. an inline keyboard)."""
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._call("sendMessage", payload)
        return dict(result or {})

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        """Best-effort typing indicator — failures are logged, not raised."""
        try:
            await self._call("sendChatAction", {"chat_id": chat_id, "action": action})
        except TelegramApiError as exc:
            logger.warning("telegram_send_chat_action_failed", error=str(exc))

    async def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        """Best-effort acknowledgement of an inline-keyboard tap — failures
        are logged, not raised (the callback_query already happened; there's
        nothing to roll back)."""
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        try:
            await self._call("answerCallbackQuery", payload)
        except TelegramApiError as exc:
            logger.warning("telegram_answer_callback_query_failed", error=str(exc))

    async def set_webhook(
        self,
        url: str,
        secret_token: str,
        allowed_updates: list[str] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "url": url,
            "secret_token": secret_token,
            "allowed_updates": allowed_updates if allowed_updates is not None else ["message"],
        }
        await self._call("setWebhook", payload)

    async def delete_webhook(self) -> None:
        await self._call("deleteWebhook")

    async def get_webhook_info(self) -> dict[str, Any]:
        result = await self._call("getWebhookInfo")
        return dict(result or {})

    async def download_file(self, file_id: str) -> tuple[bytes, str | None]:
        """Resolve *file_id* via ``getFile``, then download the file body.

        Returns ``(content_bytes, mime_type_guess)`` — the guess is derived
        from the file's extension (Telegram doesn't return a MIME type), so
        it may be ``None`` for extensionless files.
        """
        meta = await self._call("getFile", {"file_id": file_id})
        file_path = str((meta or {}).get("file_path", ""))
        if not file_path:
            raise TelegramApiError("getFile: no file_path in response")

        resp = await self._request("GET", self._file_url(file_path))
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TelegramApiError(self._scrub(str(exc))) from None

        mime_type, _ = mimetypes.guess_type(file_path)
        return resp.content, mime_type
