"""Freeze windows, "Sperrzeiträume" (design I10): admin CRUD and the expansion into occurrences.

A window is a one-off span or a recurring one (Daily/Weekly/Monthly/Yearly, the calendar's subset),
with ``enforcement`` ``block|warn`` and a scope: every change, or only changes that touch one of
the listed services. All times are naive UTC (ruling F35): a weekly window does not follow
daylight saving, the UI and the docs say "Zeiten in UTC". The note is plain text (ruling F25).

Services never commit; the route does. :func:`occurrences` is what the conflict check and the
calendar read: valid windows only, never more than ``MAX_OCCURRENCES`` in total.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.calendar.recurrence import MAX_OCCURRENCES, expand_occurrences, to_utc_naive
from tiqora.calendar.schemas import RecurrenceIn
from tiqora.domain.ticket_access import CHUNK
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import TiqoraItsmChangeFreeze, TiqoraItsmChangeFreezeService
from tiqora.itsm.change.service import _check_services
from tiqora.jobs.service import utc_now

logger = logging.getLogger(__name__)

MAX_FREEZE_SERVICES = 200

Enforcement = Literal["block", "warn"]
Scope = Literal["all", "services"]


class FreezeIn(BaseModel):
    """Body of the admin create/update."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    note: str | None = Field(default=None, max_length=5000)
    enforcement: Enforcement = "block"
    allow_emergency: bool = True
    scope: Scope = "all"
    service_ids: list[int] = Field(default_factory=list, max_length=MAX_FREEZE_SERVICES)
    starts_at: datetime
    ends_at: datetime
    recurrence: RecurrenceIn | None = None
    valid: bool = True

    @model_validator(mode="after")
    def _window(self) -> Self:
        if to_utc_naive(self.ends_at) <= to_utc_naive(self.starts_at):
            raise ValueError("Das Ende des Sperrzeitraums muss nach dem Beginn liegen.")
        return self


class FreezeRecurrence(BaseModel):
    type: str
    interval: int
    count: int | None
    until: datetime | None


class FreezeView(BaseModel):
    id: int
    name: str
    note: str | None
    enforcement: Enforcement
    allow_emergency: bool
    scope: Scope
    service_ids: list[int]
    starts_at: datetime
    ends_at: datetime
    recurrence: FreezeRecurrence | None
    valid: bool
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int


@dataclass(frozen=True, slots=True)
class FreezeOccurrence:
    freeze_id: int
    name: str
    enforcement: Enforcement
    allow_emergency: bool
    scope: Scope
    service_ids: frozenset[int]
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class FreezeOccurrences:
    """``truncated``: the result hit ``MAX_OCCURRENCES`` (one series or all together), so later
    occurrences are missing; callers narrow the range or treat the answer as incomplete."""

    items: list[FreezeOccurrence]
    truncated: bool


def _view(row: TiqoraItsmChangeFreeze, service_ids: Sequence[int]) -> FreezeView:
    recurrence = None
    if row.recur_type:
        recurrence = FreezeRecurrence(
            type=row.recur_type,
            interval=row.recur_interval or 1,
            count=row.recur_count,
            until=row.recur_until,
        )
    return FreezeView(
        id=row.id,
        name=row.name,
        note=row.note,
        enforcement=cast(Enforcement, row.enforcement),
        allow_emergency=row.allow_emergency,
        scope=cast(Scope, row.scope),
        service_ids=sorted(service_ids),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        recurrence=recurrence,
        valid=row.valid,
        created_at=row.created_at,
        created_by=row.created_by,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


async def _services_of(session: AsyncSession, freeze_ids: Sequence[int]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = defaultdict(list)
    ids = list(freeze_ids)
    for start in range(0, len(ids), CHUNK):
        rows = await session.execute(
            select(
                TiqoraItsmChangeFreezeService.freeze_id, TiqoraItsmChangeFreezeService.service_id
            ).where(TiqoraItsmChangeFreezeService.freeze_id.in_(ids[start : start + CHUNK]))
        )
        for freeze_id, service_id in rows:
            out[int(freeze_id)].append(int(service_id))
    return out


async def _validated(session: AsyncSession, data: FreezeIn) -> list[int]:
    """The service ids to store: empty for scope ``all``; checked to exist for ``services``."""
    until = data.recurrence.until if data.recurrence else None
    if until is not None and to_utc_naive(until) < to_utc_naive(data.starts_at):
        raise ChangeError("freeze_recurrence_invalid", 422)
    if data.scope == "all":
        return []
    service_ids = sorted(set(data.service_ids))
    if not service_ids:
        raise ChangeError("freeze_services_required", 422)
    await _check_services(session, service_ids)
    return service_ids


def _apply(row: TiqoraItsmChangeFreeze, data: FreezeIn) -> None:
    row.name = data.name
    row.note = data.note
    row.enforcement = data.enforcement
    row.allow_emergency = data.allow_emergency
    row.scope = data.scope
    row.starts_at = to_utc_naive(data.starts_at)
    row.ends_at = to_utc_naive(data.ends_at)
    rec = data.recurrence
    row.recur_type = rec.type if rec else None
    row.recur_interval = rec.interval if rec else None
    row.recur_count = rec.count if rec else None
    row.recur_until = to_utc_naive(rec.until) if rec and rec.until is not None else None
    row.valid = data.valid


async def list_freezes(session: AsyncSession, *, include_invalid: bool = True) -> list[FreezeView]:
    stmt = select(TiqoraItsmChangeFreeze).order_by(
        TiqoraItsmChangeFreeze.starts_at, TiqoraItsmChangeFreeze.id
    )
    if not include_invalid:
        stmt = stmt.where(TiqoraItsmChangeFreeze.valid.is_(True))
    rows = list((await session.execute(stmt)).scalars())
    services = await _services_of(session, [r.id for r in rows])
    return [_view(r, services.get(r.id, [])) for r in rows]


async def get_freeze(session: AsyncSession, freeze_id: int) -> FreezeView:
    row = await session.get(TiqoraItsmChangeFreeze, freeze_id)
    if row is None:
        raise ChangeError("freeze_not_found", 404)
    return _view(row, (await _services_of(session, [freeze_id])).get(freeze_id, []))


async def create_freeze(session: AsyncSession, *, data: FreezeIn, user_id: int) -> FreezeView:
    service_ids = await _validated(session, data)
    now = utc_now()
    row = TiqoraItsmChangeFreeze(
        created_at=now, created_by=user_id, updated_at=now, updated_by=user_id
    )
    _apply(row, data)
    session.add(row)
    await session.flush()
    session.add_all(
        TiqoraItsmChangeFreezeService(freeze_id=row.id, service_id=s) for s in service_ids
    )
    await session.flush()
    return _view(row, service_ids)


async def update_freeze(
    session: AsyncSession, *, freeze_id: int, data: FreezeIn, user_id: int
) -> FreezeView:
    """Replaces the whole window."""
    row = await session.get(TiqoraItsmChangeFreeze, freeze_id)
    if row is None:
        raise ChangeError("freeze_not_found", 404)
    service_ids = await _validated(session, data)
    _apply(row, data)
    row.updated_at = utc_now()
    row.updated_by = user_id
    await session.execute(
        delete(TiqoraItsmChangeFreezeService).where(
            TiqoraItsmChangeFreezeService.freeze_id == freeze_id
        )
    )
    session.add_all(
        TiqoraItsmChangeFreezeService(freeze_id=freeze_id, service_id=s) for s in service_ids
    )
    await session.flush()
    return _view(row, service_ids)


async def delete_freeze(session: AsyncSession, *, freeze_id: int) -> None:
    row = await session.get(TiqoraItsmChangeFreeze, freeze_id)
    if row is None:
        raise ChangeError("freeze_not_found", 404)
    await session.execute(
        delete(TiqoraItsmChangeFreezeService).where(
            TiqoraItsmChangeFreezeService.freeze_id == freeze_id
        )
    )
    await session.delete(row)
    await session.flush()


def _fast_forward(
    row: TiqoraItsmChangeFreeze, range_start: datetime
) -> tuple[datetime, datetime, int | None] | None:
    """Start, end and remaining count of the first occurrence that can reach the range.

    ``expand_occurrences`` stops after ``MAX_OCCURRENCES`` steps counted from the series start, so
    a daily window that began years ago would never reach the range. Daily and weekly steps are
    fixed spans of UTC time (ruling F35), so they are skipped arithmetically. Monthly and yearly
    steps clamp the day of the month one step at a time and keep their start (166 years of reach).
    ``None``: the series is used up before the range.
    """
    count = row.recur_count
    if row.recur_type not in ("Daily", "Weekly"):
        return row.starts_at, row.ends_at, count
    unit = timedelta(days=1) if row.recur_type == "Daily" else timedelta(weeks=1)
    step = unit * max(row.recur_interval or 1, 1)
    skip = max((range_start - row.ends_at) // step, 0)
    if count is not None and skip >= count:
        return None
    return (
        row.starts_at + skip * step,
        row.ends_at + skip * step,
        None if count is None else count - skip,
    )


async def occurrences(
    session: AsyncSession, *, start: datetime, end: datetime
) -> FreezeOccurrences:
    """Occurrences of the valid windows that overlap ``[start, end)`` (naive UTC), by start.

    Never more than ``MAX_OCCURRENCES`` in total; ``truncated`` says when that cut something off.
    """
    range_start, range_end = to_utc_naive(start), to_utc_naive(end)
    rows = list(
        (
            await session.execute(
                select(TiqoraItsmChangeFreeze)
                .where(TiqoraItsmChangeFreeze.valid.is_(True))
                .order_by(TiqoraItsmChangeFreeze.id)
            )
        ).scalars()
    )
    services = await _services_of(session, [r.id for r in rows])
    out: list[FreezeOccurrence] = []
    truncated = False
    for row in rows:
        forwarded = _fast_forward(row, range_start)
        if forwarded is None:
            continue
        first_start, first_end, count = forwarded
        sids = frozenset(services.get(row.id, ()))
        found = 0
        for occ in expand_occurrences(
            start_time=first_start,
            end_time=first_end,
            recur_type=row.recur_type,
            recur_interval=row.recur_interval,
            recur_count=count,
            recur_until=row.recur_until,
            exclude=None,
            range_start=range_start,
            range_end=range_end,
        ):
            found += 1
            out.append(
                FreezeOccurrence(
                    freeze_id=row.id,
                    name=row.name,
                    enforcement=cast(Enforcement, row.enforcement),
                    allow_emergency=row.allow_emergency,
                    scope=cast(Scope, row.scope),
                    service_ids=sids,
                    start=occ.start,
                    end=occ.end,
                )
            )
        truncated = truncated or found >= MAX_OCCURRENCES
    out.sort(key=lambda o: (o.start, o.freeze_id))
    if len(out) > MAX_OCCURRENCES:
        del out[MAX_OCCURRENCES:]
        truncated = True
    if truncated:
        logger.warning("freeze occurrences truncated to %d", MAX_OCCURRENCES)
    return FreezeOccurrences(out, truncated)
