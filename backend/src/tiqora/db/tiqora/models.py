"""SQLAlchemy models for tiqora_* tables (Alembic-managed)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase


class TiqoraApiKey(TiqoraBase):
    """API key for bearer authentication (``Authorization: Bearer``)."""

    __tablename__ = "tiqora_api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class TiqoraSettings(TiqoraBase):
    """Key/value store for indexer watermarks and runtime flags."""

    __tablename__ = "tiqora_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
