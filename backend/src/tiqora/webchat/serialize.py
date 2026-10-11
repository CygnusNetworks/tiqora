"""Visitor-facing serializers shared by the public API and the visitor stream.

``message_out`` is the ONLY serializer of a transcript line for visitors (the REST endpoints
of :mod:`tiqora.webchat.public_api` and the SSE stream of :mod:`tiqora.webchat.stream` both
use it), so neither path can leak a user id, article id or ticket data.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatMessage, TiqoraChatSession
from tiqora.webchat.config_cache import CachedConfig
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.schemas import ChatAttachmentOut, ChatMessageOut


def fallback_config(chat: TiqoraChatSession) -> CachedConfig:
    """Default values for READS of a session whose config row is gone (R-DISABLED-SESSIONS)."""
    return CachedConfig(
        config=TiqoraChatConfig(id=int(chat.config_id), valid=False),
        # A config we no longer know hides agent names (the safe default).
        values=ChatConfigValues.model_construct(topics=[], show_agent_names=False),
    )


def _json_obj(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def attachments_out(raw: str | None) -> list[ChatAttachmentOut]:
    data = _json_obj(raw)
    out: list[ChatAttachmentOut] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                ChatAttachmentOut(
                    index=int(item["index"]),
                    filename=str(item["filename"]),
                    content_type=str(item["content_type"]),
                    size=int(item["size"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def message_out(m: TiqoraChatMessage) -> ChatMessageOut:
    """The ONLY visitor serializer of a transcript line.

    System lines carry their code (``body`` column) and params; agent and bot lines the
    display name the sending side stored in ``meta`` (already ``None`` when the config hides
    names); never a user id, article id or ticket data.
    """
    meta = _json_obj(m.meta)
    meta = meta if isinstance(meta, dict) else {}
    sender: Literal["visitor", "agent", "bot", "system"]
    if m.sender == "visitor":
        sender = "visitor"
    elif m.sender == "agent":
        sender = "agent"
    elif m.sender == "bot":
        sender = "bot"
    else:
        sender = "system"
    if sender == "system":
        body: str | None = None
        code: str | None = m.body if m.sender == "system" else None
        params = {str(k): v for k, v in meta.items() if v is None or isinstance(v, (str, bool))}
    else:
        body, code, params = m.body, None, {}
    agent_name = meta.get("agent_name") if sender in ("agent", "bot") else None
    return ChatMessageOut(
        seq=int(m.seq),
        sender=sender,
        body=body,
        code=code,
        params=params,
        agent_name=agent_name if isinstance(agent_name, str) and agent_name else None,
        created=m.created,
        attachments=attachments_out(m.attachments),
        client_id=m.client_id if sender == "visitor" else None,
    )


async def session_agent_name(
    db: AsyncSession, chat: TiqoraChatSession, values: ChatConfigValues
) -> str | None:
    """First name of the assigned agent, ``None`` when unassigned or names are hidden."""
    if chat.assigned_user_id is None or not values.show_agent_names:
        return None
    row = (
        await db.execute(
            text("SELECT first_name FROM users WHERE id = :u"),
            {"u": int(chat.assigned_user_id)},
        )
    ).first()
    name = str(row[0]).strip() if row is not None and row[0] else ""
    return name or None
