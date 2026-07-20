"""Recurrence expansion for calendar appointments.

Znuny's Perl ``AppointmentCreate`` *materialises* one ``calendar_appointment``
row per occurrence (``recur_id`` chains them to the parent). Tiqora instead
keeps the single parent row (as written) and expands occurrences on read from
``recur_type``/``recur_interval``/``recur_count``/``recur_until``. This
supports the common RRULE subset Znuny's UI exposes — ``Daily``, ``Weekly``,
``Monthly``, ``Yearly`` with an interval, an optional occurrence ``COUNT``,
and/or an ``UNTIL`` bound — plus per-occurrence deletion via an exclusion
list. It intentionally does not support editing a single occurrence's
title/time independently of the series (Znuny's "this occurrence only" edit,
which *does* materialise a divergent child row) — see
docs/architecture.md.
"""

from __future__ import annotations

import calendar as _calendar_module
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

RECUR_TYPES = frozenset({"Daily", "Weekly", "Monthly", "Yearly"})

# Safety bound: never expand more than this many occurrences for a single
# query, regardless of range/count (protects against pathological intervals).
MAX_OCCURRENCES = 2000


@dataclass(frozen=True, slots=True)
class Occurrence:
    start: datetime
    end: datetime
    is_recurring: bool


def _add_months(dt: datetime, months: int) -> datetime:
    """Add *months* to *dt*, clamping the day to the target month's length."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, _calendar_module.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _step(dt: datetime, recur_type: str, interval: int) -> datetime:
    if recur_type == "Daily":
        return dt + timedelta(days=interval)
    if recur_type == "Weekly":
        return dt + timedelta(weeks=interval)
    if recur_type == "Monthly":
        return _add_months(dt, interval)
    if recur_type == "Yearly":
        return _add_months(dt, interval * 12)
    raise ValueError(f"Unsupported recur_type: {recur_type!r}")


def expand_occurrences(
    *,
    start_time: datetime,
    end_time: datetime,
    recur_type: str | None,
    recur_interval: int | None,
    recur_count: int | None,
    recur_until: datetime | None,
    exclude: list[datetime] | None,
    range_start: datetime,
    range_end: datetime,
) -> Iterator[Occurrence]:
    """Yield occurrences of an appointment overlapping ``[range_start, range_end]``.

    A non-recurring appointment yields itself (at most once) if it overlaps.
    """
    duration = end_time - start_time
    exclude_set = {e for e in (exclude or [])}

    if not recur_type or recur_type not in RECUR_TYPES:
        if start_time < range_end and end_time > range_start:
            yield Occurrence(start_time, end_time, is_recurring=False)
        return

    interval = recur_interval or 1
    if interval < 1:
        interval = 1

    occ_start = start_time
    n = 0
    while n < MAX_OCCURRENCES:
        if recur_until is not None and occ_start > recur_until:
            break
        if recur_count is not None and n >= recur_count:
            break
        if occ_start >= range_end:
            break
        occ_end = occ_start + duration
        if occ_end > range_start and occ_start not in exclude_set:
            yield Occurrence(occ_start, occ_end, is_recurring=True)
        occ_start = _step(occ_start, recur_type, interval)
        n += 1


def parse_exclude_list(raw: str | None) -> list[datetime]:
    """Parse the JSON-encoded ISO8601 list stored in ``recur_exclude``."""
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    out: list[datetime] = []
    for v in values:
        if not isinstance(v, str):
            continue
        try:
            out.append(datetime.fromisoformat(v))
        except ValueError:
            continue
    return out


def serialize_exclude_list(values: list[datetime]) -> str:
    """Serialise a list of occurrence start times to the ``recur_exclude`` format."""
    return json.dumps([v.isoformat() for v in values])


def to_utc_naive(dt: datetime) -> datetime:
    """Normalise a datetime to naive UTC (legacy Znuny DATE columns are naive)."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt
