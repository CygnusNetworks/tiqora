"""DST-safe recurrence presets for report schedules (pure, no I/O).

Daily (optionally Monday-Friday), weekly (weekdays), monthly (day 1-31, clamped to
the month's last day) and yearly. All datetimes in and out are naive UTC; the
wall time is interpreted in the schedule's time zone.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tiqora.reporting.texts import ReportTexts

MAX_SEARCH_DAYS: Final = 800
_TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


class Recurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["daily", "weekly", "monthly", "yearly"]
    time: str
    workdays_only: bool = False
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    day_of_month: int | None = Field(None, ge=1, le=31)
    month: int | None = Field(None, ge=1, le=12)

    @field_validator("time")
    @classmethod
    def _check_time(cls, value: str) -> str:
        if not _TIME.match(value):
            raise ValueError("Bitte die Uhrzeit im Format HH:MM angeben (00:00 bis 23:59).")
        return value

    @model_validator(mode="after")
    def _check_kind(self) -> Self:
        kind = self.kind
        if self.workdays_only and kind != "daily":
            raise ValueError("„Nur Montag bis Freitag“ gilt nur für tägliche Zeitpläne.")
        if kind == "weekly":
            if not self.weekdays:
                raise ValueError("Bitte mindestens einen Wochentag wählen.")
            if any(d < 1 or d > 7 for d in self.weekdays):
                raise ValueError("Wochentage müssen zwischen 1 (Montag) und 7 (Sonntag) liegen.")
            if len(set(self.weekdays)) != len(self.weekdays):
                raise ValueError("Jeder Wochentag darf nur einmal vorkommen.")
        elif self.weekdays:
            raise ValueError("Wochentage gelten nur für wöchentliche Zeitpläne.")
        if kind in ("monthly", "yearly"):
            if self.day_of_month is None:
                raise ValueError("Bitte den Tag des Monats angeben.")
        elif self.day_of_month is not None:
            raise ValueError("Der Tag des Monats gilt nur für monatliche und jährliche Zeitpläne.")
        if kind == "yearly":
            if self.month is None:
                raise ValueError("Bitte den Monat angeben.")
        elif self.month is not None:
            raise ValueError("Der Monat gilt nur für jährliche Zeitpläne.")
        return self


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(f"Unbekannte Zeitzone: {name}") from exc


def _matches(rec: Recurrence, day: date) -> bool:
    if rec.kind == "daily":
        return not rec.workdays_only or day.isoweekday() <= 5
    if rec.kind == "weekly":
        return day.isoweekday() in rec.weekdays
    assert rec.day_of_month is not None
    last = monthrange(day.year, day.month)[1]
    if rec.kind == "yearly" and day.month != rec.month:
        return False
    return day.day == min(rec.day_of_month, last)


def next_occurrence(rec: Recurrence, time_zone: str, after_utc: datetime) -> datetime:
    """Smallest occurrence strictly after ``after_utc`` (naive UTC in and out)."""
    zone = _zone(time_zone)
    hour, minute = int(rec.time[:2]), int(rec.time[3:])
    wall = time(hour, minute)
    start = after_utc.replace(tzinfo=UTC).astimezone(zone).date() - timedelta(days=1)
    for offset in range(MAX_SEARCH_DAYS + 2):
        day = start + timedelta(days=offset)
        if not _matches(rec, day):
            continue
        # fold=0: a gap time takes the pre-transition offset (02:30 -> 03:30 local),
        # an ambiguous time takes the first instant, so a run happens once.
        local = datetime.combine(day, wall).replace(tzinfo=zone, fold=0)
        candidate = local.astimezone(UTC).replace(tzinfo=None)
        if candidate > after_utc:
            return candidate
    raise ValueError("Kein Termin innerhalb des Suchzeitraums gefunden.")


def upcoming(
    rec: Recurrence, time_zone: str, after_utc: datetime, count: int = 5
) -> list[datetime]:
    out: list[datetime] = []
    cursor = after_utc
    for _ in range(max(count, 0)):
        cursor = next_occurrence(rec, time_zone, cursor)
        out.append(cursor)
    return out


def _join(names: list[str], texts: ReportTexts) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {texts.fixed('recurrence.and')} {names[-1]}"


def describe(rec: Recurrence, texts: ReportTexts) -> str:
    when = rec.time
    if rec.kind == "daily":
        key = "recurrence.workdays" if rec.workdays_only else "recurrence.daily"
        return texts.fixed(key, time=when)
    if rec.kind == "weekly":
        days = _join([texts.fixed(f"weekday.{d}") for d in sorted(rec.weekdays)], texts)
        return texts.fixed("recurrence.weekly", days=days, time=when)
    assert rec.day_of_month is not None
    if rec.kind == "monthly":
        text = texts.fixed("recurrence.monthly", day=rec.day_of_month, time=when)
        if rec.day_of_month > 28:
            text += " " + texts.fixed("recurrence.clamp")
        return text
    assert rec.month is not None
    text = texts.fixed(
        "recurrence.yearly",
        day=rec.day_of_month,
        month=texts.fixed(f"month.{rec.month}"),
        time=when,
    )
    if rec.day_of_month > 28:
        text += " " + texts.fixed("recurrence.clamp")
    return text
