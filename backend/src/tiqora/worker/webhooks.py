"""Webhook delivery: HMAC-signed POST fan-out driven by the event outbox drain.

For each outbox row, every ``valid`` webhook whose ``events`` filter matches
the row's ``event_type`` (empty list / ``["*"]`` = all events) receives
``POST {url}`` with body ``{schema_version, event, ticket_id, payload,
timestamp}`` and an ``X-Tiqora-Signature: sha256=<hex hmac>`` header.
``schema_version`` is the versioned envelope contract documented in
``docs/ai-integration.md`` — bump it only on a breaking change to this body
shape. Delivery retries up to ``settings.webhook_max_attempts`` times with
exponential backoff; a row that exhausts retries is logged and counted,
never raised (must not block the outbox drain).

Target URLs are validated and IP-pinned via
:mod:`tiqora.security.outbound` (SSRF guard; no redirect following).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Sequence

import httpx
import structlog
from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.tiqora.models import TiqoraWebhook
from tiqora.security.outbound import OutboundURLError, pin_outbound_url

logger = structlog.get_logger(__name__)

WEBHOOK_DELIVERIES = Counter(
    "tiqora_webhook_deliveries_total",
    "Webhook delivery attempts by final outcome",
    ["status"],  # "success" | "failure"
)

OutboxRow = tuple[str, int, str | None]  # (event_type, ticket_id, payload_json)


def sign_payload(secret: str, body: bytes) -> str:
    """HMAC-SHA256 signature header value for *body*, signed with *secret*."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def webhook_matches_event(events_json: str, event_type: str) -> bool:
    """Empty list or ``"*"`` in the list means "subscribe to everything"."""
    try:
        events = json.loads(events_json) if events_json else []
    except (ValueError, TypeError):
        return False
    if not isinstance(events, list) or not events or "*" in events:
        return True
    return event_type in events


async def _deliver_one(
    client: httpx.AsyncClient,
    webhook: TiqoraWebhook,
    body: bytes,
    base_headers: dict[str, str],
    *,
    max_attempts: int,
    timeout: float,  # noqa: ASYNC109 — httpx per-request timeout, not asyncio.timeout
) -> bool:
    try:
        pinned = pin_outbound_url(webhook.url)
    except OutboundURLError as exc:
        WEBHOOK_DELIVERIES.labels(status="failure").inc()
        logger.error(
            "webhook_delivery_url_blocked",
            url=webhook.url,
            name=webhook.name,
            error=str(exc),
        )
        return False

    headers = pinned.request_headers(base_headers)
    extensions = pinned.request_extensions()
    request_url = pinned.request_url

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.post(
                request_url,
                content=body,
                headers=headers,
                timeout=timeout,
                extensions=extensions,
            )
            if resp.status_code < 300:
                WEBHOOK_DELIVERIES.labels(status="success").inc()
                return True
            logger.warning(
                "webhook_delivery_bad_status",
                url=webhook.url,
                attempt=attempt,
                status=resp.status_code,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "webhook_delivery_error", url=webhook.url, attempt=attempt, error=str(exc)
            )
        if attempt < max_attempts:
            await asyncio.sleep(2 ** (attempt - 1))
    WEBHOOK_DELIVERIES.labels(status="failure").inc()
    logger.error("webhook_delivery_failed", url=webhook.url, name=webhook.name)
    return False


async def dispatch_webhooks(
    rows: Sequence[OutboxRow],
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, int]:
    """Fan out *rows* (event_type, ticket_id, payload_json) to matching webhooks."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()

    if not rows:
        return {"delivered": 0, "failed": 0}

    async with factory() as session:
        result = await session.execute(select(TiqoraWebhook).where(TiqoraWebhook.valid.is_(True)))
        webhooks = list(result.scalars().all())

    if not webhooks:
        return {"delivered": 0, "failed": 0}

    delivered = 0
    failed = 0
    # Never follow redirects: a public host must not 302 into RFC1918/metadata.
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        for event_type, ticket_id, payload in rows:
            for webhook in webhooks:
                if not webhook_matches_event(webhook.events, event_type):
                    continue
                body_obj = {
                    "schema_version": 1,
                    "event": event_type,
                    "ticket_id": ticket_id,
                    "payload": json.loads(payload) if payload else None,
                    "timestamp": time.time(),
                }
                body = json.dumps(body_obj).encode("utf-8")
                base_headers = {
                    "Content-Type": "application/json",
                    "X-Tiqora-Signature": sign_payload(webhook.secret, body),
                }
                ok = await _deliver_one(
                    client,
                    webhook,
                    body,
                    base_headers,
                    max_attempts=cfg.webhook_max_attempts,
                    timeout=cfg.webhook_timeout_seconds,
                )
                if ok:
                    delivered += 1
                else:
                    failed += 1

    logger.info("webhook_dispatch", delivered=delivered, failed=failed)
    return {"delivered": delivered, "failed": failed}
