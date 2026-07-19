"""Znuny-compatible ticket number generation.

Behavioural port of:

- ``Kernel/System/Ticket/NumberBase.pm`` — lock-free counter algorithm over
  ``ticket_number_counter`` (insert 0 → 50 ms settle → idempotent fill-up with
  ``WHERE counter = 0`` → read-back) and the collision-retry wrapper
  ``TicketCreateNumber``.
- ``Kernel/System/Ticket/Number/AutoIncrement.pm``
- ``Kernel/System/Ticket/Number/Date.pm``
- ``Kernel/System/Ticket/Number/DateChecksum.pm``
- ``Kernel/System/Ticket/Number/Random.pm``

The counter algorithm deliberately uses SHORT separate transactions per step
(autocommit-like), never one long transaction — this is what makes it safe
under concurrent writers (including a parallel Znuny) on the same database.
"""

from __future__ import annotations

import asyncio
import os
import random
import secrets
import time
import zoneinfo
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.znuny.sysconfig import SysConfig

# Short generator names (last segment of the Perl package name).
GENERATOR_AUTO_INCREMENT = "AutoIncrement"
GENERATOR_DATE = "Date"
GENERATOR_DATE_CHECKSUM = "DateChecksum"
GENERATOR_RANDOM = "Random"

# Znuny NumberBase.pm loop protection limit for TicketCreateNumber.
_LOOP_PROTECTION_LIMIT = 16000


def generator_short_name(full_name: str) -> str:
    """Extract the short generator name, e.g. ``…::DateChecksum`` → ``DateChecksum``."""
    return full_name.rsplit("::", 1)[-1]


def is_date_based(generator: str) -> bool:
    """Mirror each generator's ``IsDateBased()``: Date and DateChecksum reset daily."""
    return generator_short_name(generator) in (GENERATOR_DATE, GENERATOR_DATE_CHECKSUM)


def _tzinfo(tz_name: str) -> zoneinfo.ZoneInfo | Any:
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return UTC


def _get_uid() -> str:
    """Generate a unique 32-char counter UID.

    Znuny (``NumberBase::_GetUID``) concatenates PID + seconds + microseconds +
    NodeID and pads with random hex to 32 chars. We keep the same shape
    (time+pid prefix, hex padding) and truncate/pad to exactly 32 characters.
    """
    base = f"{os.getpid()}{time.time_ns()}"
    if len(base) >= 32:
        return base[:32]
    pad_len = 32 - len(base)
    pad = secrets.token_hex((pad_len + 1) // 2)[:pad_len]
    return base + pad


def _today_midnight(tz_name: str) -> datetime:
    """Return today's midnight (naive datetime) in the given OTRSTimeZone.

    Naive because Znuny DATETIME columns store wall-clock strings without zone.
    """
    now = datetime.now(_tzinfo(tz_name))
    return now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


async def ticket_number_counter_add(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    offset: int,
    is_date_based: bool,
    tz: str = "UTC",
) -> int:
    """Add a counter row and return its computed counter value.

    Port of ``NumberBase::TicketNumberCounterAdd``:

    1. INSERT row with ``counter = 0`` and a unique 32-char ``counter_uid`` → commit
    2. sleep 50 ms (settle window so concurrent sessions see the row)
    3. SELECT own id by ``counter_uid``
    4. SELECT all ids ``WHERE counter = 0 AND id <= own_id`` (plus
       ``create_time >= today's midnight`` for date-based generators), ascending
    5. For each: previous-max-counter + offset (first) / + 1 (subsequent), applied
       via ``UPDATE … WHERE id = ? AND counter = 0`` — idempotent under concurrency
    6. read back own counter by ``counter_uid``

    Every step commits on its own (autocommit-like short transactions).
    """
    if offset < 1:
        raise ValueError(f"Offset needs to be a positive integer, got {offset!r}")

    uid = _get_uid()
    # Znuny writes create_time as OTRSTimeZone wall-clock (DateTime->ToString).
    now_naive = datetime.now(_tzinfo(tz)).replace(microsecond=0, tzinfo=None)

    # Step 1: INSERT counter=0 row.
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO ticket_number_counter (counter, counter_uid, create_time)"
                " VALUES (0, :uid, :ct)"
            ),
            {"uid": uid, "ct": now_naive},
        )

    # Step 2: settle window (Znuny: Time::HiRes::sleep(0.05)).
    await asyncio.sleep(0.05)

    # Step 3: own id.
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT id FROM ticket_number_counter WHERE counter_uid = :uid LIMIT 1"),
                {"uid": uid},
            )
        ).first()
    if row is None:
        raise RuntimeError("ticket_number_counter row vanished after insert")
    counter_id = int(row[0])

    date_cond = ""
    date_params: dict[str, Any] = {}
    if is_date_based:
        date_cond = " AND create_time >= :midnight"
        date_params["midnight"] = _today_midnight(tz)

    # Step 4: all unset rows up to and including our own.
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id FROM ticket_number_counter"
                    f" WHERE counter = 0 AND id <= :cid{date_cond}"
                    " ORDER BY id ASC"
                ),
                {"cid": counter_id, **date_params},
            )
        ).fetchall()
    unset_ids = [int(r[0]) for r in rows]

    # Step 5: fill-up pass.
    set_offset_done = False
    for unset_id in unset_ids:
        async with session_factory() as session:
            prev_row = (
                await session.execute(
                    text(
                        "SELECT counter FROM ticket_number_counter"
                        f" WHERE id < :uid{date_cond}"
                        " ORDER BY id DESC LIMIT 1"
                    ),
                    {"uid": unset_id, **date_params},
                )
            ).first()
        previous_counter = int(prev_row[0]) if prev_row is not None and prev_row[0] else 0

        # Offset must only be applied once; subsequent rows are consecutive.
        if set_offset_done:
            new_counter = previous_counter + 1
        else:
            new_counter = previous_counter + offset
            set_offset_done = True

        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE ticket_number_counter SET counter = :nc WHERE id = :uid AND counter = 0"
                ),
                {"nc": new_counter, "uid": unset_id},
            )

    # Step 6: read back own counter.
    async with session_factory() as session:
        final_row = (
            await session.execute(
                text("SELECT counter FROM ticket_number_counter WHERE counter_uid = :uid LIMIT 1"),
                {"uid": uid},
            )
        ).first()
    if final_row is None:
        raise RuntimeError("ticket_number_counter row missing on read-back")
    return int(final_row[0])


# ---------------------------------------------------------------------------
# Formatters (pure functions; port each generator's TicketNumberBuild tail)
# ---------------------------------------------------------------------------


def format_auto_increment(counter: int, system_id: str, min_counter_size: int = 5) -> str:
    """AutoIncrement: SystemID + counter zero-padded to MinCounterSize."""
    return system_id + str(counter).zfill(min_counter_size)


def format_date(
    counter: int,
    system_id: str,
    year: int,
    month: int,
    day: int,
    *,
    use_formatted_counter: bool = False,
    min_counter_size: int = 5,
) -> str:
    """Date: yyyymmdd + SystemID + counter (zero-padded only if UseFormattedCounter)."""
    date_part = f"{year:04d}{month:02d}{day:02d}"
    counter_str = str(counter).zfill(min_counter_size) if use_formatted_counter else str(counter)
    return date_part + system_id + counter_str


def format_date_checksum(counter: int, system_id: str, year: int, month: int, day: int) -> str:
    """DateChecksum: yyyymmdd + SystemID + counter zero-padded to 5 + check digit.

    Checksum (Deutsche Bundesbahn vehicle numbering): multiply digits left to
    right alternately by 1 and 2 (starting with 1), sum, then
    ``10 - (sum % 10)``; a result of 10 becomes 1.
    """
    base = f"{year:04d}{month:02d}{day:02d}" + system_id + str(counter).zfill(5)

    checksum = 0
    multiply = 1
    for ch in base:
        checksum += multiply * int(ch)
        multiply += 1
        if multiply == 3:
            multiply = 1

    checksum %= 10
    checksum = 10 - checksum
    if checksum == 10:
        checksum = 1

    return base + str(checksum)


def format_random(system_id: str) -> str:
    """Random: SystemID + 10-digit zero-padded random integer (int rand 9999999999)."""
    count = random.randint(0, 9_999_999_998)  # noqa: S311 — non-crypto ticket numbering
    return system_id + str(count).zfill(10)


# ---------------------------------------------------------------------------
# High-level TicketCreateNumber with collision retry
# ---------------------------------------------------------------------------


async def _ticket_id_lookup_by_tn(session: AsyncSession, tn: str) -> int | None:
    row = (
        await session.execute(text("SELECT id FROM ticket WHERE tn = :tn LIMIT 1"), {"tn": tn})
    ).first()
    return int(row[0]) if row is not None else None


async def _min_counter_size(sysconfig: SysConfig, generator_specific_key: str | None) -> int:
    """Resolve MinCounterSize: generator-specific key wins, then generic, then 5."""
    if generator_specific_key is not None:
        specific = await sysconfig.get(generator_specific_key)
        if specific:
            return int(specific)
    generic = await sysconfig.get("Ticket::NumberGenerator::MinCounterSize")
    if generic:
        return int(generic)
    return 5


async def ticket_number_build(
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    offset: int = 0,
) -> str:
    """Build one ticket number using the configured generator (no collision check)."""
    generator = await sysconfig.ticket_number_generator()
    system_id = await sysconfig.system_id()
    short = generator_short_name(generator)

    if short == GENERATOR_RANDOM:
        return format_random(system_id)

    tz = await sysconfig.otrs_time_zone()
    date_based = is_date_based(generator)
    counter = await ticket_number_counter_add(
        session_factory, offset=1 + offset, is_date_based=date_based, tz=tz
    )

    if short == GENERATOR_AUTO_INCREMENT:
        min_size = await _min_counter_size(
            sysconfig, "Ticket::NumberGenerator::AutoIncrement::MinCounterSize"
        )
        return format_auto_increment(counter, system_id, min_size)

    now_local = datetime.now(_tzinfo(tz))
    if short == GENERATOR_DATE:
        use_fmt = bool(await sysconfig.get("Ticket::NumberGenerator::Date::UseFormattedCounter"))
        min_size = await _min_counter_size(sysconfig, None)
        return format_date(
            counter,
            system_id,
            now_local.year,
            now_local.month,
            now_local.day,
            use_formatted_counter=use_fmt,
            min_counter_size=min_size,
        )

    if short != GENERATOR_DATE_CHECKSUM:
        raise ValueError(f"Unsupported ticket number generator: {generator!r}")
    return format_date_checksum(counter, system_id, now_local.year, now_local.month, now_local.day)


async def ticket_create_number(
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
) -> str:
    """Create a unique ticket number (port of ``NumberBase::TicketCreateNumber``).

    On collision with an existing ticket the counter offset is incremented and
    the build retried, capped at 16000 attempts (Znuny loop protection).
    """
    for loop_counter in range(_LOOP_PROTECTION_LIMIT):
        tn = await ticket_number_build(session_factory, sysconfig, offset=loop_counter)
        async with session_factory() as session:
            existing = await _ticket_id_lookup_by_tn(session, tn)
        if existing is None:
            return tn
    raise RuntimeError(
        f"CounterLoopProtection reached {_LOOP_PROTECTION_LIMIT}; stopped ticket_create_number"
    )


__all__ = [
    "GENERATOR_AUTO_INCREMENT",
    "GENERATOR_DATE",
    "GENERATOR_DATE_CHECKSUM",
    "GENERATOR_RANDOM",
    "format_auto_increment",
    "format_date",
    "format_date_checksum",
    "format_random",
    "generator_short_name",
    "is_date_based",
    "ticket_create_number",
    "ticket_number_build",
    "ticket_number_counter_add",
]
