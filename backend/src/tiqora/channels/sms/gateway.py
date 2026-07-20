"""Pluggable SMS gateway abstraction.

``SmsGateway`` is a minimal ``Protocol`` so operators can plug in a
provider-specific driver later (Twilio, Vonage, ...) without touching
:mod:`tiqora.channels.sms.service`. The only concrete driver shipped today is
:class:`GenericHttpSmsGateway`, which POSTs a JSON webhook to a configurable
URL — the lowest common denominator most SMS aggregators/on-prem modems
support, optionally HMAC-signed with a shared secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)


class SmsGateway(Protocol):
    async def send(self, *, to: str, body: str) -> None:
        """Send one outbound SMS. Raise on delivery failure."""
        ...


def sign_payload(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class GenericHttpSmsGateway:
    """POSTs ``{"to": ..., "body": ...}`` to a configurable webhook URL."""

    def __init__(
        self,
        webhook_url: str,
        *,
        shared_secret: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._url = webhook_url
        self._secret = shared_secret
        self._client = client
        self._timeout = timeout

    async def send(self, *, to: str, body: str) -> None:
        payload = {"to": to, "body": body}
        raw = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._secret:
            headers["X-Tiqora-Signature"] = sign_payload(self._secret, raw)

        if self._client is not None:
            resp = await self._client.post(
                self._url, content=raw, headers=headers, timeout=self._timeout
            )
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._url, content=raw, headers=headers, timeout=self._timeout
                )
        resp.raise_for_status()
        logger.info("sms_outbound_sent", to=to, status=resp.status_code)
