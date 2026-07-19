from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase


class FormDraft(LegacyBase):
    """Znuny table `form_draft`."""

    __tablename__ = "form_draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Mention(LegacyBase):
    """Znuny table `mention`."""

    __tablename__ = "mention"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    article_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    create_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TimeAccounting(LegacyBase):
    """Znuny table `time_accounting`."""

    __tablename__ = "time_accounting"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    time_unit: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class LinkRelation(LegacyBase):
    """Znuny table `link_relation`."""

    __tablename__ = "link_relation"

    source_object_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, nullable=False)
    source_key: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    target_object_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, nullable=False)
    target_key: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    type_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, nullable=False)
    state_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)


class LinkType(LegacyBase):
    """Znuny table `link_type`."""

    __tablename__ = "link_type"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class LinkObject(LegacyBase):
    """Znuny table `link_object`."""

    __tablename__ = "link_object"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
