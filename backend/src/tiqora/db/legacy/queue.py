from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class Queue(LegacyBase):
    """Znuny table `queue`."""

    __tablename__ = "queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    unlock_timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_response_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_response_notify: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    update_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    update_notify: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    solution_time: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solution_notify: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    system_address_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    calendar_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    default_sign_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    salutation_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    signature_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    follow_up_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    follow_up_lock: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class QueueAutoResponse(LegacyBase):
    """Znuny table `queue_auto_response`."""

    __tablename__ = "queue_auto_response"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_response_id: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class QueueStandardTemplate(LegacyBase):
    """Znuny table `queue_standard_template`."""

    __tablename__ = "queue_standard_template"

    queue_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    standard_template_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class StandardTemplate(LegacyBase):
    """Znuny table `standard_template`."""

    __tablename__ = "standard_template"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    template_type: Mapped[str] = mapped_column(String(100), nullable=False, server_default="Answer")
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class StandardAttachment(LegacyBase):
    """Znuny table `standard_attachment`."""

    __tablename__ = "standard_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(250), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    filename: Mapped[str] = mapped_column(String(250), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class StandardTemplateAttachment(LegacyBase):
    """Znuny table `standard_template_attachment`."""

    __tablename__ = "standard_template_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    standard_attachment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    standard_template_id: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Salutation(LegacyBase):
    """Znuny table `salutation`."""

    __tablename__ = "salutation"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Signature(LegacyBase):
    """Znuny table `signature`."""

    __tablename__ = "signature"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class AutoResponse(LegacyBase):
    """Znuny table `auto_response`."""

    __tablename__ = "auto_response"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    text0: Mapped[str | None] = mapped_column(Text, nullable=True)
    text1: Mapped[str | None] = mapped_column(Text, nullable=True)
    type_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    system_address_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class AutoResponseType(LegacyBase):
    """Znuny table `auto_response_type`."""

    __tablename__ = "auto_response_type"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class SystemAddress(LegacyBase):
    """Znuny table `system_address`."""

    __tablename__ = "system_address"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    value0: Mapped[str] = mapped_column(String(200), nullable=False)
    value1: Mapped[str] = mapped_column(String(200), nullable=False)
    value2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    value3: Mapped[str | None] = mapped_column(String(200), nullable=True)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class FollowUpPossible(LegacyBase):
    """Znuny table `follow_up_possible`."""

    __tablename__ = "follow_up_possible"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Sla(LegacyBase):
    """Znuny table `sla`."""

    __tablename__ = "sla"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    calendar_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_response_time: Mapped[int] = mapped_column(Integer, nullable=False)
    first_response_notify: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    update_time: Mapped[int] = mapped_column(Integer, nullable=False)
    update_notify: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    solution_time: Mapped[int] = mapped_column(Integer, nullable=False)
    solution_notify: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Service(LegacyBase):
    """Znuny table `service`."""

    __tablename__ = "service"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class ServiceSla(LegacyBase):
    """Znuny table `service_sla`."""

    __tablename__ = "service_sla"

    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    sla_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)


class ServiceCustomerUser(LegacyBase):
    """Znuny table `service_customer_user`."""

    __tablename__ = "service_customer_user"

    customer_user_login: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    service_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
