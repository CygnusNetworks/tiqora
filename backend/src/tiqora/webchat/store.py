"""Loading chat configs and turning their raw columns into typed values."""

from __future__ import annotations

import json
from typing import Any, Final

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraChatConfig
from tiqora.webchat.models import ChatConfigValues, default_prechat_fields

logger = structlog.get_logger(__name__)

#: Columns holding JSON text; every other value column is a plain scalar.
_JSON_COLUMNS: Final = (
    "allowed_origins",
    "topics",
    "prechat_fields",
    "upload_policy",
    "theme",
    "rate_limits",
)
_SCALAR_COLUMNS: Final = (
    "greeting",
    "privacy_url",
    "offline_mode",
    "business_calendar",
    "wait_timeout_seconds",
    "default_customer_user",
    "show_agent_names",
    "retention_days",
)
#: A topic that always validates, so one column can be checked on its own.
_PROBE_TOPICS: Final = [{"key": "probe", "label": "probe", "queue_id": 1}]


async def load_config(db: AsyncSession, config_id: int) -> TiqoraChatConfig | None:
    return (
        await db.execute(select(TiqoraChatConfig).where(TiqoraChatConfig.id == config_id))
    ).scalar_one_or_none()


async def load_config_by_key(db: AsyncSession, public_key: str) -> TiqoraChatConfig | None:
    """The config for *public_key*, only while it is valid (``valid`` true)."""
    return (
        await db.execute(
            select(TiqoraChatConfig).where(
                TiqoraChatConfig.public_key == public_key,
                TiqoraChatConfig.valid.is_(True),
            )
        )
    ).scalar_one_or_none()


def _default_for(column: str) -> Any:
    if column == "topics":
        return []
    if column == "prechat_fields":
        return default_prechat_fields()
    field = ChatConfigValues.model_fields[column]
    return field.get_default(call_default_factory=True)


def _column_value(row: TiqoraChatConfig, column: str) -> Any:
    """Validate one column in isolation; invalid -> its default (and a warning)."""
    raw = getattr(row, column)
    try:
        value = json.loads(raw) if column in _JSON_COLUMNS else raw
        probe = ChatConfigValues.model_validate({"topics": _PROBE_TOPICS, column: value})
    except (ValueError, TypeError, ValidationError):
        logger.warning("webchat_config_column_invalid", config_id=row.id, column=column)
        return _default_for(column)
    return getattr(probe, column)


def config_values(row: TiqoraChatConfig) -> ChatConfigValues:
    """Typed values of *row*; each column falls back to its default on its own.

    ``topics`` ``min_length=1`` only binds admin input: a broken topics column
    yields ``[]`` (the config then counts as offline) via ``model_construct``.
    """
    parts: dict[str, Any] = {
        column: _column_value(row, column) for column in (*_JSON_COLUMNS, *_SCALAR_COLUMNS)
    }
    try:
        return ChatConfigValues.model_validate(parts)
    except ValidationError:
        if parts["topics"]:
            logger.warning("webchat_config_column_invalid", config_id=row.id, column="topics")
            parts["topics"] = []
        return ChatConfigValues.model_construct(**parts)
