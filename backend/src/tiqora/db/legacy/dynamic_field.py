from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class DynamicField(LegacyBase):
    """Znuny table `dynamic_field`.

    ``config`` stores YAML text (read via Perl ``YAML::Load``). MySQL DDL
    uses LONGBLOB; PostgreSQL maps it to TEXT — the same LONGBLOB→TEXT
    convention documented on :class:`tiqora.db.legacy.config.Acl` for
    YAML/config columns. Mapped as :class:`~sqlalchemy.Text` (not
    LargeBinary) so both dialects round-trip as Unicode strings; a prior
    LargeBinary mapping caused PostgreSQL to implicitly cast the bytea bind
    parameter to its ``\\x..``-hex text form on INSERT into the TEXT column,
    silently corrupting stored config.
    """

    __tablename__ = "dynamic_field"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    internal_field: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    field_order: Mapped[int] = mapped_column(Integer, nullable=False)
    field_type: Mapped[str] = mapped_column(String(200), nullable=False)
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    config: Mapped[str | None] = mapped_column(Text, nullable=True)
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
