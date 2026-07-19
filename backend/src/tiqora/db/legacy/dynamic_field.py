from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase


class DynamicField(LegacyBase):
    """Znuny table `dynamic_field`."""

    __tablename__ = "dynamic_field"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    internal_field: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    field_order: Mapped[int] = mapped_column(Integer, nullable=False)
    field_type: Mapped[str] = mapped_column(String(200), nullable=False)
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    config: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class DynamicFieldValue(LegacyBase):
    """Znuny table `dynamic_field_value`."""

    __tablename__ = "dynamic_field_value"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    field_id: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    value_int: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class DynamicFieldObjIdName(LegacyBase):
    """Znuny table `dynamic_field_obj_id_name`."""

    __tablename__ = "dynamic_field_obj_id_name"

    object_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, nullable=False
    )
    object_name: Mapped[str] = mapped_column(String(200), nullable=False)
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
