"""Znuny tables `calendar`, `calendar_appointment`, `calendar_appointment_ticket`.

These are read/write ORM models for the calendar/appointment feature. They
map the *existing* Znuny 6.5 schema (see ``schema.xml``) verbatim — no new
tables or columns — so rows written here are visible unmodified to a running
Znuny instance and vice versa (parallel-operation compatible, same as the
rest of ``tiqora.db.legacy``).

``calendar_appointment_plugin`` (arbitrary per-appointment JSON payload used
by Znuny's notification/ticket-appointment plugin framework) is intentionally
not mapped: Tiqora does not use the plugin system, and it is not required for
CRUD/recurrence/ICS/ticket-linking.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase


class Calendar(LegacyBase):
    """Znuny table `calendar`."""

    __tablename__ = "calendar"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    group_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    salt_string: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    ticket_appointments: Mapped[bytes | None] = mapped_column(nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class CalendarAppointment(LegacyBase):
    """Znuny table `calendar_appointment`.

    Recurrence: Tiqora stores the recurrence rule on the parent row
    (``recur_type``/``recur_interval``/``recur_count``/``recur_until``) and
    expands occurrences on read (see ``tiqora.calendar.recurrence``) rather
    than materialising one child row per occurrence the way Znuny's Perl
    ``AppointmentCreate`` does. ``parent_id``/``recur_id`` stay unused by
    Tiqora writes but are read/preserved so rows created by a real Znuny
    stay intact (see docs/architecture.md Calendar section).
    """

    __tablename__ = "calendar_appointment"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    calendar_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unique_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(3800), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime] = mapped_column(nullable=False)
    end_time: Mapped[datetime] = mapped_column(nullable=False)
    all_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    notify_time: Mapped[datetime | None] = mapped_column(nullable=True)
    notify_template: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_custom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_custom_unit_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notify_custom_unit: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_custom_unit_point: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_custom_date: Mapped[datetime | None] = mapped_column(nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(3800), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(3800), nullable=True)
    recurring: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    recur_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recur_freq: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recur_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recur_interval: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recur_until: Mapped[datetime | None] = mapped_column(nullable=True)
    recur_id: Mapped[datetime | None] = mapped_column(nullable=True)
    recur_exclude: Mapped[str | None] = mapped_column(String(3800), nullable=True)
    ticket_appointment_rule_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    create_time: Mapped[datetime | None] = mapped_column(nullable=True)
    create_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_time: Mapped[datetime | None] = mapped_column(nullable=True)
    change_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CalendarAppointmentTicket(LegacyBase):
    """Znuny table `calendar_appointment_ticket` (appointment <-> ticket link).

    Composite-key-only table (no surrogate id in Znuny's schema): a
    ``(calendar_id, ticket_id, rule_id)`` tuple is unique. Tiqora always
    writes ``rule_id='manual'`` for links created via the UI/API (Znuny's own
    "ticket appointment rules" automation, which derives ``rule_id`` from a
    configured rule name, is out of scope — see docs/architecture.md).
    """

    __tablename__ = "calendar_appointment_ticket"

    calendar_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    appointment_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
