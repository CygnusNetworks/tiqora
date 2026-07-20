from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class Ticket(LegacyBase):
    """Znuny table `ticket`."""

    __tablename__ = "ticket"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    tn: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_lock_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    type_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    service_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sla_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    responsible_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_priority_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ticket_state_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    customer_user_id: Mapped[str | None] = mapped_column(String(250), nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False)
    until_time: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_time: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_update_time: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_response_time: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_solution_time: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_flag: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketHistory(LegacyBase):
    """Znuny table `ticket_history`."""

    __tablename__ = "ticket_history"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    history_type_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    type_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketHistoryType(LegacyBase):
    """Znuny table `ticket_history_type`."""

    __tablename__ = "ticket_history_type"

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


class TicketState(LegacyBase):
    """Znuny table `ticket_state`."""

    __tablename__ = "ticket_state"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    type_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketStateType(LegacyBase):
    """Znuny table `ticket_state_type`."""

    __tablename__ = "ticket_state_type"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketPriority(LegacyBase):
    """Znuny table `ticket_priority`."""

    __tablename__ = "ticket_priority"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketLockType(LegacyBase):
    """Znuny table `ticket_lock_type`."""

    __tablename__ = "ticket_lock_type"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketType(LegacyBase):
    """Znuny table `ticket_type`."""

    __tablename__ = "ticket_type"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketNumberCounter(LegacyBase):
    """Znuny table `ticket_number_counter`."""

    __tablename__ = "ticket_number_counter"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    counter: Mapped[int] = mapped_column(BigInteger, nullable=False)
    counter_uid: Mapped[str] = mapped_column(String(32), nullable=False)
    create_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TicketWatcher(LegacyBase):
    """Znuny table `ticket_watcher`."""

    __tablename__ = "ticket_watcher"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class TicketFlag(LegacyBase):
    """Znuny table `ticket_flag`."""

    __tablename__ = "ticket_flag"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    ticket_key: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    ticket_value: Mapped[str | None] = mapped_column(String(50), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)


class TicketIndex(LegacyBase):
    """Znuny table `ticket_index`."""

    __tablename__ = "ticket_index"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    queue: Mapped[str] = mapped_column(String(200), nullable=False)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    s_lock: Mapped[str] = mapped_column(String(200), nullable=False)
    s_state: Mapped[str] = mapped_column(String(200), nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TicketLockIndex(LegacyBase):
    """Znuny table `ticket_lock_index`."""

    __tablename__ = "ticket_lock_index"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)


class TicketLoopProtection(LegacyBase):
    """Znuny table `ticket_loop_protection`."""

    __tablename__ = "ticket_loop_protection"

    sent_to: Mapped[str] = mapped_column(String(250), primary_key=True, nullable=False)
    sent_date: Mapped[str] = mapped_column(String(150), primary_key=True, nullable=False)
