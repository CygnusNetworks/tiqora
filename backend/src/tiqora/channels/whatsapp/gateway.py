"""Meta WhatsApp Cloud API (Graph API) client: outbound send + media download.

All calls are async ``httpx``. Assumes a WhatsApp Business Cloud API app with
a permanent (or long-lived) access token and a phone-number-id — see
``docs/channels.md`` for the exact Meta app setup this driver targets.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v19.0"


class WhatsAppGateway:
    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        api_version: str = DEFAULT_API_VERSION,
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._token = access_token
        self._api_version = api_version
        self._client = client
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is not None:
            resp = await self._client.request(method, url, timeout=self._timeout, **kwargs)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, timeout=self._timeout, **kwargs)
        resp.raise_for_status()
        return resp

    async def send_text(self, *, to: str, body: str) -> str:
        """Send a free-form text message. Returns the provider message id."""
        url = f"{GRAPH_API_BASE}/{self._api_version}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        resp = await self._request("POST", url, json=payload, headers=self._headers())
        data = resp.json()
        logger.info("whatsapp_outbound_text_sent", to=to)
        return str(data.get("messages", [{}])[0].get("id", ""))

    async def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language_code: str = "en_US",
        components: list[dict[str, Any]] | None = None,
    ) -> str:
        """Send an approved template message (required to re-open a session
        outside the 24h customer-service window)."""
        url = f"{GRAPH_API_BASE}/{self._api_version}/{self._phone_number_id}/messages"
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language_code},
        }
        if components:
            template["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": template,
        }
        resp = await self._request("POST", url, json=payload, headers=self._headers())
        data = resp.json()
        logger.info("whatsapp_outbound_template_sent", to=to, template=template_name)
        return str(data.get("messages", [{}])[0].get("id", ""))

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Resolve *media_id* to a signed URL, then download it. Returns
        ``(content_bytes, mime_type)``."""
        meta_url = f"{GRAPH_API_BASE}/{self._api_version}/{media_id}"
        meta_resp = await self._request("GET", meta_url, headers=self._headers())
        meta = meta_resp.json()
        media_url = meta["url"]
        mime_type = str(meta.get("mime_type", "application/octet-stream"))

        content_resp = await self._request("GET", media_url, headers=self._headers())
        return content_resp.content, mime_type
