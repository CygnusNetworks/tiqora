"""Five-field cron expressions (``m h dom mon dow``), naive UTC, minute resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# (name, lowest, highest) per field; dow accepts 7 as an alias of 0 (Sunday).
_FIELDS: tuple[tuple[str, int, int], ...] = (
    ("Minute", 0, 59),
    ("Stunde", 0, 23),
    ("Tag des Monats", 1, 31),
    ("Monat", 1, 12),
    ("Wochentag", 0, 7),
)
_MAX_DAYS = 366 * 8


def _error(expr: str, reason: str) -> ValueError:
    return ValueError(f"Ungültiger Zeitplan „{expr}“: {reason}")


def _number(expr: str, field: str, text: str, low: int, high: int) -> int:
    if not text.isascii() or not text.isdigit():
        raise _error(expr, f"„{text}“ ist keine Zahl ({field})")
    value = int(text)
    if not low <= value <= high:
        raise _error(expr, f"{field} muss zwischen {low} und {high} liegen, nicht {value}")
    return value


def _parse_part(expr: str, field: str, part: str, low: int, high: int) -> set[int]:
    base, slash, step_text = part.partition("/")
    step = 1
    if slash:
        step = _number(expr, field, step_text, 1, high)
    if base == "*":
        start, end = low, high
    elif "-" in base:
        a, _, b = base.partition("-")
        start = _number(expr, field, a, low, high)
        end = _number(expr, field, b, low, high)
        if start > end:
            raise _error(expr, f"Bereich {start}-{end} in {field} ist rückwärts")
    else:
        start = _number(expr, field, base, low, high)
        end = high if slash else start
    return set(range(start, end + 1, step))


@dataclass(frozen=True, slots=True)
class CronExpr:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0-6, Sunday = 0
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expr: str) -> CronExpr:
        fields = expr.split()
        if len(fields) != 5:
            raise _error(expr, f"erwartet fünf Felder, gefunden {len(fields)}")
        sets: list[frozenset[int]] = []
        for text, (name, low, high) in zip(fields, _FIELDS, strict=True):
            values: set[int] = set()
            for part in text.split(","):
                if not part:
                    raise _error(expr, f"leerer Eintrag in {name}")
                values |= _parse_part(expr, name, part, low, high)
            if name == "Wochentag":
                values = {v % 7 for v in values}
            sets.append(frozenset(values))
        return cls(
            minutes=sets[0],
            hours=sets[1],
            days=sets[2],
            months=sets[3],
            weekdays=sets[4],
            dom_restricted=fields[2] != "*",
            dow_restricted=fields[4] != "*",
        )

    # "Restricted" means the field text is not exactly "*" (so "*/2" counts as restricted
    # and, with the other day field restricted too, matches by OR; real Vixie cron differs here).
    def _day_matches(self, day: datetime) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        dow_ok = (day.weekday() + 1) % 7 in self.weekdays
        if self.dom_restricted and self.dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def next_after(self, dt: datetime) -> datetime:
        """First matching minute strictly after ``dt`` (naive UTC)."""
        start = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        times = sorted((h, m) for h in self.hours for m in self.minutes)
        midnight = start.replace(hour=0, minute=0)
        for offset in range(_MAX_DAYS):
            day = midnight + timedelta(days=offset)
            if not self._day_matches(day):
                continue
            for hour, minute in times:
                if offset == 0 and (hour, minute) < (start.hour, start.minute):
                    continue
                return day.replace(hour=hour, minute=minute)
        raise ValueError("Zeitplan hat keinen nächsten Termin")
