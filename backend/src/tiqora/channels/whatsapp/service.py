"""WhatsApp inbound webhook processing + outbound sending.

Webhook payload shape follows Meta's WhatsApp Cloud API
(``entry[].changes[].value.messages[]``); see
https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import (
    channel_setting,
    ensure_channel_row,
    resolve_customer_by_phone,
    resolve_ticket_for_inbound,
)
from tiqora.channels.whatsapp.gateway import WhatsAppGateway
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.sysconfig import SysConfig

CHANNEL_NAME = "whatsapp"
COMM_CHANNEL_NAME = "WhatsApp"
COMM_CHANNEL_MODULE = "Tiqora::CommunicationChannel::WhatsApp"

# WhatsApp media message types that carry a downloadable attachment.
_MEDIA_TYPES = {"image", "audio", "video", "document", "sticker"}


@dataclass(frozen=True, slots=True)
class InboundWhatsAppResult:
    ticket_id: int
    article_id: int
    created: bool
    wa_id: str
    message_type: str


def verify_webhook_signature(
    app_secret: str | None, raw_body: bytes, signature_header: str | None
) -> bool:
    """Verify Meta's ``X-Hub-Signature-256: sha256=<hex hmac>`` header."""
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header[len("sha256=") :]
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


async def build_gateway(session: AsyncSession) -> WhatsAppGateway | None:
    phone_number_id = await channel_setting(session, CHANNEL_NAME, "phone_number_id")
    access_token = await channel_setting(session, CHANNEL_NAME, "access_token")
    if not phone_number_id or not access_token:
        return None
    api_version = await channel_setting(session, CHANNEL_NAME, "api_version") or "v19.0"
    return WhatsAppGateway(
        phone_number_id=phone_number_id, access_token=access_token, api_version=api_version
    )


def _extract_text(message: dict[str, Any]) -> str:
    msg_type = message.get("type", "")
    if msg_type == "text":
        return str(message.get("text", {}).get("body", ""))
    if msg_type == "button":
        return str(message.get("button", {}).get("text", ""))
    if msg_type == "interactive":
        interactive = message.get("interactive", {})
        for key in ("button_reply", "list_reply"):
            if key in interactive:
                return str(interactive[key].get("title", ""))
    return ""


async def _process_one_message(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    gateway: WhatsAppGateway | None,
    message: dict[str, Any],
    *,
    user_id: int,
) -> InboundWhatsAppResult:
    await ensure_channel_row(session, COMM_CHANNEL_NAME, COMM_CHANNEL_MODULE)

    wa_id = str(message.get("from", ""))
    msg_type = str(message.get("type", "text"))

    customer_no, customer_user_id = await resolve_customer_by_phone(session, wa_id)
    if customer_user_id is None:
        default_cu = await channel_setting(session, CHANNEL_NAME, "default_customer_user")
        customer_user_id = default_cu
        customer_no = default_cu

    attachments: list[tuple[str, str, bytes]] = []
    if msg_type in _MEDIA_TYPES and gateway is not None:
        media_id = str(message.get(msg_type, {}).get("id", ""))
        if media_id:
            content, mime_type = await gateway.download_media(media_id)
            ext = mimetypes.guess_extension(mime_type) or ""
            attachments.append((f"{media_id}{ext}", mime_type, content))
        body_text = str(message.get(msg_type, {}).get("caption", "")) or f"[{msg_type} attachment]"
    else:
        body_text = _extract_text(message) or f"[{msg_type} message]"

    title = f"WhatsApp from {wa_id}"
    ticket_id, created = await resolve_ticket_for_inbound(
        session,
        session_factory,
        sysconfig,
        channel=CHANNEL_NAME,
        body_text=body_text,
        customer_no=customer_no,
        customer_user_id=customer_user_id,
        title=title,
        user_id=user_id,
    )

    article = ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=title if created else "WhatsApp reply",
        body=body_text,
        content_type="text/plain; charset=utf-8",
        from_address=wa_id,
        channel=CHANNEL_NAME,
        attachments=attachments,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    return InboundWhatsAppResult(
        ticket_id=ticket_id,
        article_id=article_id,
        created=created,
        wa_id=wa_id,
        message_type=msg_type,
    )


async def process_webhook_payload(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    gateway: WhatsAppGateway | None,
    payload: dict[str, Any],
    *,
    user_id: int,
) -> list[InboundWhatsAppResult]:
    """Process every message in a WhatsApp Cloud API webhook payload."""
    results: list[InboundWhatsAppResult] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                result = await _process_one_message(
                    session, session_factory, sysconfig, gateway, message, user_id=user_id
                )
                results.append(result)
    return results


async def send_outbound_text(
    session: AsyncSession,
    sysconfig: SysConfig,
    gateway: WhatsAppGateway,
    *,
    ticket_id: int,
    to: str,
    body: str,
    user_id: int,
) -> int:
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="WhatsApp reply",
        body=body,
        content_type="text/plain; charset=utf-8",
        to_address=to,
        channel=CHANNEL_NAME,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    await gateway.send_text(to=to, body=body)
    return article_id


async def send_outbound_template(
    session: AsyncSession,
    sysconfig: SysConfig,
    gateway: WhatsAppGateway,
    *,
    ticket_id: int,
    to: str,
    template_name: str,
    language_code: str,
    user_id: int,
) -> int:
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="WhatsApp template reply",
        body=f"[template: {template_name} ({language_code})]",
        content_type="text/plain; charset=utf-8",
        to_address=to,
        channel=CHANNEL_NAME,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    await gateway.send_template(to=to, template_name=template_name, language_code=language_code)
    return article_id
