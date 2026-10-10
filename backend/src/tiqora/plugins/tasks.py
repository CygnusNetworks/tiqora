"""Scheduled plugin tasks: schedule value object and due-time calculation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from tiqora.util.cron import CronExpr

MIN_INTERVAL_SECONDS = 30
_DAILY_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True, slots=True)
class Schedule:
    every_seconds: int | None = None
    daily_at: str | None = None  # "HH:MM" UTC
    cron: str | None = None

    def validate(self) -> None:
        set_count = sum(v is not None for v in (self.every_seconds, self.daily_at, self.cron))
        if set_count != 1:
            raise ValueError(
                "Ungültiger Zeitplan: genau eine Angabe aus every_seconds, "
                "daily_at oder cron ist nötig"
            )
        if self.every_seconds is not None and self.every_seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(
                f"Ungültiger Zeitplan: das Intervall muss mindestens "
                f"{MIN_INTERVAL_SECONDS} Sekunden betragen"
            )
        if self.daily_at is not None and not _DAILY_RE.match(self.daily_at):
            raise ValueError(
                f"Ungültiger Zeitplan: „{self.daily_at}“ ist keine Uhrzeit im Format HH:MM"
            )
        if self.cron is not None:
            CronExpr.parse(self.cron)

    def describe(self) -> str:
        if self.every_seconds is not None:
            n = self.every_seconds
            if n == 60:
                return "jede Minute"
            if n == 3600:
                return "jede Stunde"
            if n % 3600 == 0:
                return f"alle {n // 3600} Stunden"
            if n % 60 == 0:
                return f"alle {n // 60} Minuten"
            return f"alle {n} Sekunden"
        if self.daily_at is not None:
            return f"täglich um {self.daily_at} UTC"
        return f"nach Zeitplan „{self.cron}“ (UTC)"


def next_due(schedule: Schedule, *, last_run_at: datetime | None, created_at: datetime) -> datetime:
    """Next run time (naive UTC) of a validated schedule."""
    if schedule.every_seconds is not None:
        if last_run_at is None:
            return created_at
        return last_run_at + timedelta(seconds=schedule.every_seconds)
    base = last_run_at or created_at
    if schedule.daily_at is not None:
        hh, mm = schedule.daily_at.split(":")
        return CronExpr.parse(f"{int(mm)} {int(hh)} * * *").next_after(base)
    if schedule.cron is None:
        raise ValueError("Ungültiger Zeitplan: keine Angabe gesetzt")
    return CronExpr.parse(schedule.cron).next_after(base)
