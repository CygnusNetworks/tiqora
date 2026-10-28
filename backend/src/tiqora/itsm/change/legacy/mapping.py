"""Pure rules of the Znuny change data (no database, no I/O).

* Work order times are never NULL in Znuny: "not set" is the sentinel ``9999-01-01 00:00:00``
  (:func:`znuny_time` maps it to ``None``).
* ``*_plain`` columns are ``HTMLUtils::ToAscii`` of the text and differ between 6.5 and 7.3 in
  their surrounding newlines, so they are stripped (:func:`plain_text`, survey Q6, ruling S3).
* A change has no time columns; :func:`change_window` derives them from its work orders exactly
  like ``WorkOrderChangeTimeGet`` (survey Q2, ruling S2): MIN of the starts, MAX of the ends with
  the sentinel as the largest value, so one work order without an end blanks the change's end,
  and the planned start is set only when every work order has one.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Final

#: Znuny's "time not set" (column default of the ``change_workorder`` times)
SENTINEL: Final = datetime(9999, 1, 1, 0, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC

_TAG_RE: Final = re.compile(r"<[^>]*>")
_BREAK_RE: Final = re.compile(r"<\s*(?:br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", re.IGNORECASE)


def znuny_time(value: datetime | None) -> datetime | None:
    """``None`` for NULL and for the sentinel (also a sentinel with microseconds/tz stripped)."""
    if value is None:
        return None
    naive = value.replace(tzinfo=None, microsecond=0)
    return None if naive >= SENTINEL else naive


def plain_text(plain: str | None, markup: str | None = None) -> str | None:
    """The stripped ``*_plain`` column; only when it is empty the text column without tags.

    HTML is never rendered: tags are dropped (block ends become line breaks), entities decoded.
    Empty -> ``None``.
    """
    text = (plain or "").strip()
    if not text and markup:
        text = html.unescape(_TAG_RE.sub("", _BREAK_RE.sub("\n", markup))).strip()
    return text or None


def _raw(value: datetime | None) -> datetime:
    """A work order time as Znuny compares it: unset is the sentinel."""
    shown = znuny_time(value)
    return SENTINEL if shown is None else shown


def change_window(
    times: Sequence[tuple[datetime | None, datetime | None, datetime | None, datetime | None]],
) -> tuple[datetime | None, datetime | None, datetime | None, datetime | None]:
    """``(planned_start, planned_end, actual_start, actual_end)`` of a change from the
    ``(planned_start, planned_end, actual_start, actual_end)`` of its work orders.

    Each value may be ``None`` or the sentinel for "not set". No work orders: nothing is set.
    """
    if not times:
        return None, None, None, None
    planned_starts = [_raw(t[0]) for t in times]
    planned_start = None if SENTINEL in planned_starts else min(planned_starts)
    planned_end = znuny_time(max(_raw(t[1]) for t in times))
    actual_start = znuny_time(min(_raw(t[2]) for t in times))
    actual_end = znuny_time(max(_raw(t[3]) for t in times))
    return planned_start, planned_end, actual_start, actual_end


def dynamic_field_text(
    value_text: str | None, value_date: datetime | None, value_int: int | None
) -> str | None:
    """One ``dynamic_field_value`` row as text (``YYYY-MM-DD HH:MM:SS`` for dates, like Znuny)."""
    if value_text is not None:
        return str(value_text)
    if value_date is not None:
        return value_date.replace(tzinfo=None, microsecond=0).isoformat(sep=" ")
    if value_int is not None:
        return str(int(value_int))
    return None
