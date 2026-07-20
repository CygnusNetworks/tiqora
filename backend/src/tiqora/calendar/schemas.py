"""Pydantic v2 request/response models for the calendar REST API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tiqora.calendar.recurrence import RECUR_TYPES


class CalendarOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    group_id: int
    name: str
    color: str
    valid: bool = Field(validation_alias="valid_id")

    @field_validator("valid", mode="before")
    @classmethod
    def _coerce_valid(cls, v: object) -> bool:
        return v == 1 if isinstance(v, int) else bool(v)


class RecurrenceIn(BaseModel):
    """Common RRULE subset: FREQ + INTERVAL + (COUNT xor UNTIL)."""

    type: str = Field(description="Daily | Weekly | Monthly | Yearly")
    interval: int = Field(default=1, ge=1)
    count: int | None = Field(default=None, ge=1)
    until: datetime | None = None

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in RECUR_TYPES:
            raise ValueError(f"recurrence type must be one of {sorted(RECUR_TYPES)}")
        return v


class AppointmentIn(BaseModel):
    calendar_id: int
    title: str
    description: str | None = None
    location: str | None = None
    start_time: datetime
    end_time: datetime
    all_day: bool = False
    team_id: str | None = None
    resource_id: str | None = None
    recurrence: RecurrenceIn | None = None


class AppointmentUpdateIn(BaseModel):
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    all_day: bool | None = None
    team_id: str | None = None
    resource_id: str | None = None
    recurrence: RecurrenceIn | None = None
    clear_recurrence: bool = False


class AppointmentOut(BaseModel):
    id: int
    parent_id: int | None
    calendar_id: int
    unique_id: str
    title: str
    description: str | None
    location: str | None
    start_time: datetime
    end_time: datetime
    all_day: bool
    team_id: str | None
    resource_id: str | None
    recurring: bool
    recur_type: str | None
    recur_interval: int | None
    recur_count: int | None
    recur_until: datetime | None
    create_time: datetime | None
    change_time: datetime | None


class OccurrenceOut(BaseModel):
    """A single expanded occurrence within a queried date range."""

    appointment_id: int
    calendar_id: int
    title: str
    description: str | None
    location: str | None
    start_time: datetime
    end_time: datetime
    all_day: bool
    is_recurring: bool


class TicketLinkOut(BaseModel):
    appointment_id: int
    calendar_id: int
    ticket_id: int
    rule_id: str
