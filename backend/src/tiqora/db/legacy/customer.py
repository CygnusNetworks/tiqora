from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase


class CustomerUser(LegacyBase):
    """Znuny table `customer_user`."""

    __tablename__ = "customer_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    login: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(150), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(150), nullable=False)
    pw: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(50), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(150), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(150), nullable=True)
    mobile: Mapped[str | None] = mapped_column(String(150), nullable=True)
    street: Mapped[str | None] = mapped_column(String(150), nullable=True)
    zip: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class CustomerCompany(LegacyBase):
    """Znuny table `customer_company`."""

    __tablename__ = "customer_company"

    customer_id: Mapped[str] = mapped_column(String(150), primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    street: Mapped[str | None] = mapped_column(String(200), nullable=True)
    zip: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(String(200), nullable=True)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class CustomerUserCustomer(LegacyBase):
    """Znuny table `customer_user_customer`."""

    __tablename__ = "customer_user_customer"

    user_id: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(150), primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class CustomerPreferences(LegacyBase):
    """Znuny table `customer_preferences`."""

    __tablename__ = "customer_preferences"

    user_id: Mapped[str] = mapped_column(String(250), primary_key=True, nullable=False)
    preferences_key: Mapped[str] = mapped_column(String(150), primary_key=True, nullable=False)
    preferences_value: Mapped[str | None] = mapped_column(String(250), nullable=True)
