from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Integer,
    LargeBinary,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class Users(LegacyBase):
    """Znuny table `users`."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    login: Mapped[str] = mapped_column(String(200), nullable=False)
    pw: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(50), nullable=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class UserPreferences(LegacyBase):
    """Znuny table `user_preferences`."""

    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    preferences_key: Mapped[str] = mapped_column(String(150), primary_key=True, nullable=False)
    preferences_value: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class PermissionGroups(LegacyBase):
    """Znuny table `permission_groups`."""

    __tablename__ = "permission_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Roles(LegacyBase):
    """Znuny table `roles`."""

    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class RoleUser(LegacyBase):
    """Znuny table `role_user`."""

    __tablename__ = "role_user"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class GroupUser(LegacyBase):
    """Znuny table `group_user`."""

    __tablename__ = "group_user"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    permission_key: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class GroupRole(LegacyBase):
    """Znuny table `group_role`."""

    __tablename__ = "group_role"

    role_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    permission_key: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    permission_value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class GroupCustomerUser(LegacyBase):
    """Znuny table `group_customer_user`."""

    __tablename__ = "group_customer_user"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    permission_key: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    permission_value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class GroupCustomer(LegacyBase):
    """Znuny table `group_customer`."""

    __tablename__ = "group_customer"

    customer_id: Mapped[str] = mapped_column(String(150), primary_key=True, nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    permission_key: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    permission_value: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    permission_context: Mapped[str] = mapped_column(String(100), primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
