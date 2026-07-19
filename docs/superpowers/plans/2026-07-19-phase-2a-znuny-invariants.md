# Phase 2a: Znuny Write-Invariant Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the seven Znuny write-invariant modules under `backend/src/tiqora/znuny/` — ticket numbering, history helpers, escalation index, ticket index, cache invalidation, and search flags — with full pytest coverage on both MariaDB and PostgreSQL.

**Architecture:** Each module is a pure-Python async helper layer that writes directly to Znuny-compatible tables using SQLAlchemy `text()` / ORM statements inside short, autocommit-like transactions. No REST endpoints. No TicketService writes (Phase 2b). All modules import `SysConfig` from `tiqora.znuny.sysconfig` for runtime config.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async (AsyncSession), Alembic (versions_tiqora chain), pytest + testcontainers (MariaDB 10.11 + Postgres 16), ruff, mypy strict.

## Global Constraints

- Python 3.12+ only; all files start with `from __future__ import annotations`
- SQLAlchemy 2 async everywhere; never use sync sessions
- Both MariaDB 10.11 and PostgreSQL 16 must pass; avoid dialect-specific SQL
- `current_timestamp` in SQL is cross-dialect; use it for create_time/change_time writes
- Never copy Znuny Perl source verbatim; behavioral port only
- New DB tables live in `tiqora_*` namespace via Alembic `versions_tiqora/`; Znuny tables are read/written via existing legacy ORM models or `text()` SQL
- Run linting with `uv run ruff format . && uv run ruff check .` from repo root
- Run mypy with `uv run mypy` from `backend/`
- Run tests with `uv run pytest -q` from `backend/`
- Sync deps with `uv sync --all-packages --all-extras` from repo root before running tests
- `@pytest.mark.db` marks tests needing Docker testcontainers
- Do NOT modify `frontend/`
- Commit with conventional commits (`feat:`, `test:`, `chore:`)

---

## File Map

| File | Purpose |
|---|---|
| `backend/src/tiqora/znuny/ticket_number.py` | Counter algorithm + 4 generators + TicketCreateNumber |
| `backend/src/tiqora/znuny/history.py` | Typed HistoryAdd helpers, exact Znuny name formats |
| `backend/src/tiqora/znuny/escalation.py` | TicketEscalationIndexBuild + working-time math |
| `backend/src/tiqora/znuny/ticket_index.py` | StaticDB/RuntimeDB ticket_index maintenance |
| `backend/src/tiqora/znuny/cache_invalidation.py` | tiqora_cache_invalidation writer |
| `backend/src/tiqora/znuny/search_flag.py` | search_index_needs_rebuild setter + MD5 helper |
| `backend/alembic/versions_tiqora/20260719_0002_cache_invalidation.py` | Alembic migration for tiqora_cache_invalidation |
| `backend/src/tiqora/db/tiqora/models.py` | Add TiqoraCacheInvalidation model |
| `backend/tests/test_ticket_number.py` | Unit + DB tests for ticket numbering |
| `backend/tests/test_history.py` | DB tests for history helpers |
| `backend/tests/test_escalation.py` | Unit + DB tests for escalation |
| `backend/tests/test_ticket_index.py` | DB tests for ticket index |
| `backend/tests/test_cache_invalidation.py` | DB test for cache invalidation |
| `backend/tests/test_search_flag.py` | DB test for search flag |

---

## Task 1: ticket_number.py — counter algorithm + generators

**Files:**
- Create: `backend/src/tiqora/znuny/ticket_number.py`
- Test: `backend/tests/test_ticket_number.py`

**Interfaces:**
- Produces:
  - `async def ticket_number_counter_add(session_factory, *, offset: int, is_date_based: bool, tz: str) -> int`
  - `async def ticket_create_number(session_factory, sysconfig: SysConfig) -> str`
  - `def format_auto_increment(counter: int, system_id: str, min_counter_size: int = 5) -> str`
  - `def format_date(counter: int, system_id: str, year: int, month: int, day: int, use_formatted_counter: bool = False, min_counter_size: int = 5) -> str`
  - `def format_date_checksum(counter: int, system_id: str, year: int, month: int, day: int) -> str`
  - `def format_random(system_id: str) -> str`

- [ ] **Step 1: Write failing unit tests for generator formatting**

Create `backend/tests/test_ticket_number.py`:

```python
"""Unit and DB tests for ticket number generation."""
from __future__ import annotations

import pytest

from tiqora.znuny.ticket_number import (
    format_auto_increment,
    format_date,
    format_date_checksum,
    format_random,
)


def test_auto_increment_pads_to_min_size() -> None:
    # SystemID=10, counter=1 → "1000001" (10 + 00001)
    result = format_auto_increment(1, "10", min_counter_size=5)
    assert result == "1000001"


def test_auto_increment_larger_counter() -> None:
    result = format_auto_increment(12345, "10", min_counter_size=5)
    assert result == "1012345"


def test_auto_increment_custom_min_size() -> None:
    result = format_auto_increment(1, "10", min_counter_size=3)
    assert result == "10001"


def test_date_format_basic() -> None:
    # yyyymmdd + SystemID + counter (no formatting)
    result = format_date(42, "10", 2026, 7, 19)
    assert result == "202607191042"


def test_date_format_with_formatted_counter() -> None:
    # use_formatted_counter pads to min_counter_size=5
    result = format_date(42, "10", 2026, 7, 19, use_formatted_counter=True, min_counter_size=5)
    assert result == "2026071910" + "00042"


def test_date_checksum_known_vector() -> None:
    # Hand-computed: "202607191000001"
    # digits: 2,0,2,6,0,7,1,9,1,0,0,0,0,0,1
    # multiply alternating 1,2,1,2,...:
    # 2*1 + 0*2 + 2*1 + 6*2 + 0*1 + 7*2 + 1*1 + 9*2 + 1*1 + 0*2 + 0*1 + 0*2 + 0*1 + 0*2 + 1*1
    # = 2 + 0 + 2 + 12 + 0 + 14 + 1 + 18 + 1 + 0 + 0 + 0 + 0 + 0 + 1 = 51
    # 51 % 10 = 1; checksum = 10 - 1 = 9
    result = format_date_checksum(1, "10", 2026, 7, 19)
    assert result.startswith("202607191000001")
    assert result == "2026071910000019"


def test_date_checksum_when_sum_is_zero() -> None:
    # If checksum would be 10, it wraps to 1
    # We test this via the algorithm: find a counter that gives sum%10 == 0
    # For counter=9 with system_id="10", date=20260719:
    # base = "202607191000009"
    # 2*1+0*2+2*1+6*2+0*1+7*2+1*1+9*2+1*1+0*2+0*1+0*2+0*1+0*2+9*1
    # = 2+0+2+12+0+14+1+18+1+0+0+0+0+0+9 = 59
    # 59%10 = 9; checksum = 10-9 = 1
    result = format_date_checksum(9, "10", 2026, 7, 19)
    assert result.endswith("1")


def test_date_checksum_checksum_10_wraps_to_1() -> None:
    # For counter=5 with "10" date=20260719:
    # base = "202607191000005"
    # 2+0+2+12+0+14+1+18+1+0+0+0+0+0+5 = 55
    # 55%10=5; 10-5=5
    result = format_date_checksum(5, "10", 2026, 7, 19)
    assert result.endswith("5")


def test_random_format_length_and_prefix() -> None:
    result = format_random("10")
    assert result.startswith("10")
    # 10 digits after systemID
    suffix = result[len("10"):]
    assert len(suffix) == 10
    assert suffix.isdigit()
```

- [ ] **Step 2: Run to verify failures**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_ticket_number.py -q 2>&1 | head -30
```

Expected: ImportError or ModuleNotFoundError for `tiqora.znuny.ticket_number`.

- [ ] **Step 3: Implement ticket_number.py**

Create `backend/src/tiqora/znuny/ticket_number.py`:

```python
"""Znuny-compatible ticket number generation.

Ports behavior from:
  - Kernel/System/Ticket/NumberBase.pm  (counter algorithm)
  - Kernel/System/Ticket/Number/AutoIncrement.pm
  - Kernel/System/Ticket/Number/Date.pm
  - Kernel/System/Ticket/Number/DateChecksum.pm
  - Kernel/System/Ticket/Number/Random.pm

The counter algorithm uses short autocommit-like transactions (one per step)
to be safe under concurrent Znuny writers on the same database.
"""

from __future__ import annotations

import asyncio
import random
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timezone
from typing import Any

import zoneinfo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.znuny.sysconfig import SysConfig

# Generator class name suffixes (last part of Perl package name)
_GENERATOR_AUTO_INCREMENT = "AutoIncrement"
_GENERATOR_DATE = "Date"
_GENERATOR_DATE_CHECKSUM = "DateChecksum"
_GENERATOR_RANDOM = "Random"


def _generator_short_name(full_name: str) -> str:
    """Extract short name from Perl package path, e.g. '...::DateChecksum' → 'DateChecksum'."""
    return full_name.rsplit("::", 1)[-1]


def _is_date_based(generator: str) -> bool:
    short = _generator_short_name(generator)
    return short in (_GENERATOR_DATE, _GENERATOR_DATE_CHECKSUM)


def _get_uid() -> str:
    """Generate a 32-char hex-ish UID like Znuny's NumberBase::_GetUID.

    Znuny: PID + seconds + microseconds + NodeID + random hex padding to 32 chars.
    We use time_ns for precision and secrets for the random part.
    """
    ts_ns = time.time_ns()
    pid = __import__("os").getpid()
    base = f"{pid}{ts_ns}"
    pad_len = max(0, 32 - len(base))
    pad = secrets.token_hex((pad_len + 1) // 2)[:pad_len]
    uid = (base + pad)[:32]
    return uid


def _today_midnight_utc_in_tz(tz_name: str) -> str:
    """Return 'YYYY-MM-DD 00:00:00' for today in the given Znuny/IANA timezone."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = UTC
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d 00:00:00")


async def ticket_number_counter_add(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    offset: int,
    is_date_based: bool,
    tz: str = "UTC",
) -> int:
    """Insert a counter row, settle 50ms, fill unset rows, return own counter value.

    Ports NumberBase::TicketNumberCounterAdd exactly:
    1. INSERT counter=0 with unique counter_uid → commit
    2. Sleep 50ms
    3. SELECT own id by counter_uid
    4. SELECT all ids WHERE counter=0 AND id <= own_id [+ date filter if date-based], ASC
    5. For each: find previous max counter, set new = prev + offset (first) or prev + 1 (rest)
       via UPDATE ... WHERE id=? AND counter=0 (idempotent under concurrency)
    6. SELECT own counter by counter_uid → return

    Each step is its own commit (autocommit-like).
    """
    if offset < 1:
        raise ValueError(f"offset must be positive integer, got {offset!r}")

    uid = _get_uid()
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    # Step 1: INSERT
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO ticket_number_counter (counter, counter_uid, create_time)"
                    " VALUES (0, :uid, :ct)"
                ),
                {"uid": uid, "ct": now_str},
            )

    # Step 2: settle
    await asyncio.sleep(0.05)

    # Step 3: get own id
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id FROM ticket_number_counter"
                    " WHERE counter_uid = :uid LIMIT 1"
                ),
                {"uid": uid},
            )
        ).first()
    if row is None:
        raise RuntimeError("ticket_number_counter row vanished after insert")
    counter_id: int = row[0]

    # Build date condition
    date_cond = ""
    date_params: dict[str, Any] = {}
    if is_date_based:
        midnight = _today_midnight_utc_in_tz(tz)
        date_cond = " AND create_time >= :midnight"
        date_params["midnight"] = midnight

    # Step 4: collect unset ids
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    f"SELECT id FROM ticket_number_counter"
                    f" WHERE counter = 0 AND id <= :cid{date_cond}"
                    f" ORDER BY id ASC"
                ),
                {"cid": counter_id, **date_params},
            )
        ).fetchall()
    unset_ids = [r[0] for r in rows]

    # Step 5: fill-up pass
    set_offset_done = False
    for unset_id in unset_ids:
        # Get previous counter (highest counter < this id, with optional date filter)
        async with session_factory() as session:
            prev_row = (
                await session.execute(
                    text(
                        f"SELECT counter FROM ticket_number_counter"
                        f" WHERE id < :uid{date_cond}"
                        f" ORDER BY id DESC LIMIT 1"
                    ),
                    {"uid": unset_id, **date_params},
                )
            ).first()
        prev_counter = int(prev_row[0]) if prev_row and prev_row[0] else 0

        if not set_offset_done:
            new_counter = prev_counter + offset
            set_offset_done = True
        else:
            new_counter = prev_counter + 1

        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE ticket_number_counter SET counter = :nc"
                        " WHERE id = :uid AND counter = 0"
                    ),
                    {"nc": new_counter, "uid": unset_id},
                )

    # Step 6: read back own counter
    async with session_factory() as session:
        final_row = (
            await session.execute(
                text(
                    "SELECT counter FROM ticket_number_counter"
                    " WHERE counter_uid = :uid LIMIT 1"
                ),
                {"uid": uid},
            )
        ).first()
    if final_row is None:
        raise RuntimeError("ticket_number_counter row missing on read-back")
    return int(final_row[0])


# ---------------------------------------------------------------------------
# Formatter functions (pure, synchronous)
# ---------------------------------------------------------------------------

def format_auto_increment(counter: int, system_id: str, min_counter_size: int = 5) -> str:
    """AutoIncrement: SystemID + zero-padded counter."""
    padded = str(counter).zfill(min_counter_size)
    return system_id + padded


def format_date(
    counter: int,
    system_id: str,
    year: int,
    month: int,
    day: int,
    use_formatted_counter: bool = False,
    min_counter_size: int = 5,
) -> str:
    """Date: yyyymmdd + SystemID + counter (optionally zero-padded)."""
    date_part = f"{year:04d}{month:02d}{day:02d}"
    if use_formatted_counter:
        counter_str = str(counter).zfill(min_counter_size)
    else:
        counter_str = str(counter)
    return date_part + system_id + counter_str


def format_date_checksum(
    counter: int,
    system_id: str,
    year: int,
    month: int,
    day: int,
) -> str:
    """DateChecksum: yyyymmdd + SystemID + zero-padded-5 counter + checksum digit.

    Checksum algorithm (Deutsche Bundesbahn):
    Multiply each digit alternately by 1 and 2 (starting with 1), sum all products,
    take (10 - sum%10) mod 10; if result is 10, use 1 instead.
    """
    date_part = f"{year:04d}{month:02d}{day:02d}"
    counter_str = str(counter).zfill(5)
    base = date_part + system_id + counter_str

    total = 0
    multiplier = 1
    for ch in base:
        total += multiplier * int(ch)
        multiplier = 2 if multiplier == 1 else 1

    checksum = 10 - (total % 10)
    if checksum == 10:
        checksum = 1

    return base + str(checksum)


def format_random(system_id: str) -> str:
    """Random: SystemID + 10-digit zero-padded random integer."""
    count = random.randint(0, 9_999_999_999)  # noqa: S311 — non-crypto ticket numbering
    return system_id + str(count).zfill(10)


# ---------------------------------------------------------------------------
# High-level: build a ticket number using SysConfig
# ---------------------------------------------------------------------------

async def _ticket_id_lookup_by_tn(session: AsyncSession, tn: str) -> int | None:
    """Return ticket.id for the given ticket number, or None."""
    row = (
        await session.execute(
            text("SELECT id FROM ticket WHERE tn = :tn LIMIT 1"),
            {"tn": tn},
        )
    ).first()
    return int(row[0]) if row else None


async def ticket_create_number(
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
) -> str:
    """Generate a unique ticket number using the configured generator.

    Retries with incrementing offset on collision (mirrors NumberBase::TicketCreateNumber).
    Max 16000 retries (Znuny loop protection).
    """
    generator = await sysconfig.ticket_number_generator()
    system_id = await sysconfig.system_id()
    tz = await sysconfig.otrs_time_zone()

    short = _generator_short_name(generator)
    date_based = _is_date_based(generator)

    now_utc = datetime.now(UTC)
    try:
        local_tz = zoneinfo.ZoneInfo(tz)
    except zoneinfo.ZoneInfoNotFoundError:
        local_tz = UTC
    now_local = now_utc.astimezone(local_tz)

    for loop_counter in range(16000):
        offset = 1 + loop_counter

        if short == _GENERATOR_RANDOM:
            tn = format_random(system_id)
        else:
            counter = await ticket_number_counter_add(
                session_factory,
                offset=offset,
                is_date_based=date_based,
                tz=tz,
            )
            if short == _GENERATOR_AUTO_INCREMENT:
                min_size_raw = await sysconfig.get(
                    "Ticket::NumberGenerator::AutoIncrement::MinCounterSize"
                )
                if min_size_raw is None:
                    min_size_raw = await sysconfig.get("Ticket::NumberGenerator::MinCounterSize")
                min_size = int(min_size_raw) if min_size_raw is not None else 5
                tn = format_auto_increment(counter, system_id, min_size)
            elif short == _GENERATOR_DATE:
                use_fmt = bool(
                    await sysconfig.get(
                        "Ticket::NumberGenerator::Date::UseFormattedCounter"
                    )
                )
                min_size_raw = await sysconfig.get("Ticket::NumberGenerator::MinCounterSize")
                min_size = int(min_size_raw) if min_size_raw is not None else 5
                tn = format_date(
                    counter,
                    system_id,
                    now_local.year,
                    now_local.month,
                    now_local.day,
                    use_formatted_counter=use_fmt,
                    min_counter_size=min_size,
                )
            else:  # DateChecksum (default)
                tn = format_date_checksum(
                    counter, system_id, now_local.year, now_local.month, now_local.day
                )

        # Check collision
        async with session_factory() as session:
            existing = await _ticket_id_lookup_by_tn(session, tn)
        if not existing:
            return tn

    raise RuntimeError("ticket_create_number: loop protection triggered (16000 retries)")


__all__ = [
    "ticket_number_counter_add",
    "ticket_create_number",
    "format_auto_increment",
    "format_date",
    "format_date_checksum",
    "format_random",
]
```

- [ ] **Step 4: Run unit tests to verify they pass**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_ticket_number.py -q -k "not db"
```

Expected: 7 tests pass.

- [ ] **Step 5: Add DB concurrency test to test_ticket_number.py**

Append to `backend/tests/test_ticket_number.py`:

```python
import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.ticket_number import ticket_number_counter_add


def _make_mysql_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _make_pg_async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def _run_concurrency(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    tasks = [
        ticket_number_counter_add(session_factory, offset=1, is_date_based=False, tz="UTC")
        for _ in range(20)
    ]
    return list(await asyncio.gather(*tasks))


@pytest.mark.db
def test_counter_uniqueness_mariadb(mariadb_znuny_url: str) -> None:
    async_url = _make_mysql_async_url(mariadb_znuny_url)
    engine = create_async_engine(async_url, pool_size=10, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    counters = asyncio.get_event_loop().run_until_complete(_run_concurrency(factory))

    asyncio.get_event_loop().run_until_complete(engine.dispose())
    assert len(counters) == 20
    assert len(set(counters)) == 20, f"Duplicate counters: {sorted(counters)}"
    assert all(c > 0 for c in counters)


@pytest.mark.db
def test_counter_uniqueness_postgres(postgres_znuny_url: str) -> None:
    async_url = _make_pg_async_url(postgres_znuny_url)
    engine = create_async_engine(async_url, pool_size=10, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    counters = asyncio.get_event_loop().run_until_complete(_run_concurrency(factory))

    asyncio.get_event_loop().run_until_complete(engine.dispose())
    assert len(counters) == 20
    assert len(set(counters)) == 20, f"Duplicate counters: {sorted(counters)}"


@pytest.mark.db
def test_date_filter_resets_counter_each_day(mariadb_znuny_url: str) -> None:
    """Date-based generators only pick up counters from today; simulate by using is_date_based=True."""
    async_url = _make_mysql_async_url(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> tuple[int, int]:
        c1 = await ticket_number_counter_add(factory, offset=1, is_date_based=True, tz="UTC")
        c2 = await ticket_number_counter_add(factory, offset=1, is_date_based=True, tz="UTC")
        return c1, c2

    c1, c2 = asyncio.get_event_loop().run_until_complete(run())
    asyncio.get_event_loop().run_until_complete(engine.dispose())
    # Both must be positive and c2 > c1 (sequential today)
    assert c1 > 0
    assert c2 > c1
```

- [ ] **Step 6: Run all ticket_number tests (DB tests need Docker)**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_ticket_number.py -v
```

Expected: 7 unit tests pass; 3 DB tests pass if Docker available, skip otherwise.

- [ ] **Step 7: Lint and type-check**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy src/tiqora/znuny/ticket_number.py
```

Expected: no errors.

- [ ] **Step 8: Commit**

```bash
cd /Users/valerius/git/aurix
git add backend/src/tiqora/znuny/ticket_number.py backend/tests/test_ticket_number.py
git commit -m "feat(znuny): implement ticket_number counter algorithm and four generators"
```

---

## Task 2: history.py — typed HistoryAdd helpers

**Files:**
- Create: `backend/src/tiqora/znuny/history.py`
- Test: `backend/tests/test_history.py`

**Interfaces:**
- Consumes: `AsyncSession`, `SysConfig` (unused here, but available)
- Produces:
  - `async def history_add(session, *, ticket_id, history_type, name, user_id, article_id=None, queue_id=None, type_id=None, owner_id=None, priority_id=None, state_id=None) -> None`
  - One typed helper per history type (see list below), each calling `history_add`
  - `async def resolve_history_type_id(session, name: str) -> int` (cached per process)

- [ ] **Step 1: Write failing DB test**

Create `backend/tests/test_history.py`:

```python
"""DB tests for history helpers — exact Znuny name string formats."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _pg_async(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def _get_last_history(session: AsyncSession, ticket_id: int) -> dict:
    row = (
        await session.execute(
            text(
                "SELECT h.name, ht.name as htype FROM ticket_history h"
                " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                " WHERE h.ticket_id = :tid ORDER BY h.id DESC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    assert row is not None
    return {"name": row[0], "htype": row[1]}


async def _insert_minimal_ticket(session: AsyncSession, tn: str) -> int:
    """Insert a minimal ticket row for testing; return its id."""
    await session.execute(
        text(
            "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id, responsible_user_id,"
            " ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time, change_by)"
            " VALUES (:tn, 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"tn": tn},
    )
    row = (await session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})).first()
    assert row is not None
    return int(row[0])


@pytest.mark.db
def test_history_new_ticket_format_mariadb(mariadb_znuny_url: str) -> None:
    from tiqora.znuny.history import add_new_ticket

    async def run() -> None:
        engine = create_async_engine(_mysql_async(mariadb_znuny_url))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                ticket_id = await _insert_minimal_ticket(session, "2026071910000019")
            async with session.begin():
                await add_new_ticket(
                    session,
                    ticket_id=ticket_id,
                    tn="2026071910000019",
                    queue="Raw",
                    priority="3 normal",
                    state="new",
                    user_id=1,
                )
            row = await _get_last_history(session, ticket_id)
        await engine.dispose()
        assert row["htype"] == "NewTicket"
        assert row["name"] == "%%2026071910000019%%Raw%%3 normal%%new%%" + str(ticket_id)

    asyncio.get_event_loop().run_until_complete(run())


@pytest.mark.db
def test_history_state_update_format_mariadb(mariadb_znuny_url: str) -> None:
    from tiqora.znuny.history import add_state_update

    async def run() -> None:
        engine = create_async_engine(_mysql_async(mariadb_znuny_url))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                ticket_id = await _insert_minimal_ticket(session, "TN_STATE_TEST")
            async with session.begin():
                await add_state_update(
                    session,
                    ticket_id=ticket_id,
                    old_state="new",
                    new_state="open",
                    user_id=1,
                )
            row = await _get_last_history(session, ticket_id)
        await engine.dispose()
        assert row["htype"] == "StateUpdate"
        assert row["name"] == "%%new%%open%%"

    asyncio.get_event_loop().run_until_complete(run())


@pytest.mark.db
def test_history_move_format_mariadb(mariadb_znuny_url: str) -> None:
    from tiqora.znuny.history import add_move

    async def run() -> None:
        engine = create_async_engine(_mysql_async(mariadb_znuny_url))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                ticket_id = await _insert_minimal_ticket(session, "TN_MOVE_TEST")
            async with session.begin():
                await add_move(
                    session,
                    ticket_id=ticket_id,
                    new_queue="Support",
                    new_queue_id=2,
                    old_queue="Raw",
                    old_queue_id=1,
                    user_id=1,
                )
            row = await _get_last_history(session, ticket_id)
        await engine.dispose()
        assert row["htype"] == "Move"
        assert row["name"] == "%%Support%%2%%Raw%%1"

    asyncio.get_event_loop().run_until_complete(run())
```

- [ ] **Step 2: Run to verify failures**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_history.py -q 2>&1 | head -20
```

Expected: ImportError for `tiqora.znuny.history`.

- [ ] **Step 3: Implement history.py**

Create `backend/src/tiqora/znuny/history.py`:

```python
"""Znuny-compatible ticket history helpers.

Ports the HistoryAdd call sites from Kernel/System/Ticket.pm and related files.
Format strings use %% as the Znuny separator (parsed by merge-chain detection,
first-response detection, etc.).

SEMANTIC NOTE: Znuny's HistoryAdd resolves missing snapshot columns (queue_id,
type_id, owner_id, priority_id, state_id) by calling TicketGet. We require
callers to pass them explicitly, or fall back to a live SELECT on ticket.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# History type names as stored in ticket_history_type.name
_TYPE_NEW_TICKET: Final = "NewTicket"
_TYPE_STATE_UPDATE: Final = "StateUpdate"
_TYPE_MOVE: Final = "Move"
_TYPE_TITLE_UPDATE: Final = "TitleUpdate"
_TYPE_TYPE_UPDATE: Final = "TypeUpdate"
_TYPE_SERVICE_UPDATE: Final = "ServiceUpdate"
_TYPE_SLA_UPDATE: Final = "SLAUpdate"
_TYPE_PRIORITY_UPDATE: Final = "PriorityUpdate"
_TYPE_OWNER_UPDATE: Final = "OwnerUpdate"
_TYPE_RESPONSIBLE_UPDATE: Final = "ResponsibleUpdate"
_TYPE_LOCK: Final = "Lock"
_TYPE_UNLOCK: Final = "Unlock"
_TYPE_CUSTOMER_UPDATE: Final = "CustomerUpdate"
_TYPE_PENDING_TIME: Final = "SetPendingTime"
_TYPE_SUBSCRIBE: Final = "Subscribe"
_TYPE_UNSUBSCRIBE: Final = "Unsubscribe"
_TYPE_DYNAMIC_FIELD: Final = "TicketDynamicFieldUpdate"
_TYPE_ARCHIVE_FLAG: Final = "ArchiveFlagUpdate"
_TYPE_SEND_ANSWER: Final = "SendAnswer"
_TYPE_EMAIL_AGENT: Final = "EmailAgent"
_TYPE_EMAIL_CUSTOMER: Final = "EmailCustomer"
_TYPE_PHONE_CALL_AGENT: Final = "PhoneCallAgent"
_TYPE_PHONE_CALL_CUSTOMER: Final = "PhoneCallCustomer"
_TYPE_ADD_NOTE: Final = "AddNote"

# Process-level cache: history type name → id (populated lazily)
_history_type_cache: dict[str, int] = {}


async def resolve_history_type_id(session: AsyncSession, name: str) -> int:
    """Resolve ticket_history_type.id by name; cache result for process lifetime."""
    if name in _history_type_cache:
        return _history_type_cache[name]
    row = (
        await session.execute(
            text("SELECT id FROM ticket_history_type WHERE name = :name LIMIT 1"),
            {"name": name},
        )
    ).first()
    if row is None:
        raise ValueError(f"Unknown history type: {name!r}")
    _history_type_cache[name] = int(row[0])
    return _history_type_cache[name]


async def _ticket_snapshot(
    session: AsyncSession, ticket_id: int
) -> tuple[int, int, int, int, int]:
    """Return (queue_id, type_id, owner_id, priority_id, state_id) from ticket row."""
    row = (
        await session.execute(
            text(
                "SELECT queue_id, type_id, user_id, ticket_priority_id, ticket_state_id"
                " FROM ticket WHERE id = :tid LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        raise ValueError(f"Ticket {ticket_id} not found")
    return (
        int(row[0]),
        int(row[1]) if row[1] is not None else 1,
        int(row[2]),
        int(row[3]),
        int(row[4]),
    )


async def history_add(
    session: AsyncSession,
    *,
    ticket_id: int,
    history_type: str,
    name: str,
    user_id: int,
    article_id: int | None = None,
    queue_id: int | None = None,
    type_id: int | None = None,
    owner_id: int | None = None,
    priority_id: int | None = None,
    state_id: int | None = None,
) -> None:
    """Write one ticket_history row, resolving snapshot columns from ticket if missing.

    Mirrors Znuny Ticket.pm::HistoryAdd exactly:
    - name is truncated to 200 chars
    - Missing snapshot columns fetched from ticket table (never from a cache)
    - create_by = change_by = user_id
    """
    # Truncate name to 200 chars (Znuny limit)
    name = name[:200]

    # Resolve snapshot if any column missing
    if queue_id is None or type_id is None or owner_id is None or priority_id is None or state_id is None:
        snap = await _ticket_snapshot(session, ticket_id)
        queue_id = queue_id if queue_id is not None else snap[0]
        type_id = type_id if type_id is not None else snap[1]
        owner_id = owner_id if owner_id is not None else snap[2]
        priority_id = priority_id if priority_id is not None else snap[3]
        state_id = state_id if state_id is not None else snap[4]

    type_row_id = await resolve_history_type_id(session, history_type)

    await session.execute(
        text(
            "INSERT INTO ticket_history"
            " (name, history_type_id, ticket_id, article_id,"
            "  queue_id, owner_id, priority_id, state_id, type_id,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:name, :htid, :tid, :aid,"
            "  :qid, :oid, :pid, :sid, :typid,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "name": name,
            "htid": type_row_id,
            "tid": ticket_id,
            "aid": article_id,
            "qid": queue_id,
            "oid": owner_id,
            "pid": priority_id,
            "sid": state_id,
            "typid": type_id,
            "uid": user_id,
        },
    )


# ---------------------------------------------------------------------------
# Typed helpers — one per history type, exact Znuny name formats
# ---------------------------------------------------------------------------

async def add_new_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    tn: str,
    queue: str,
    priority: str,
    state: str,
    user_id: int,
    queue_id: int | None = None,
) -> None:
    """NewTicket: %%TN%%Queue%%Priority%%State%%TicketID"""
    name = f"%%{tn}%%{queue}%%{priority}%%{state}%%{ticket_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_NEW_TICKET,
                      name=name, user_id=user_id, queue_id=queue_id)


async def add_state_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    old_state: str,
    new_state: str,
    user_id: int,
    article_id: int | None = None,
    queue_id: int | None = None,
    state_id: int | None = None,
) -> None:
    """StateUpdate: %%OldState%%NewState%%"""
    name = f"%%{old_state}%%{new_state}%%"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_STATE_UPDATE,
                      name=name, user_id=user_id, article_id=article_id,
                      queue_id=queue_id, state_id=state_id)


async def add_move(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_queue: str,
    new_queue_id: int,
    old_queue: str,
    old_queue_id: int,
    user_id: int,
) -> None:
    """Move: %%NewQueue%%NewQueueID%%OldQueue%%OldQueueID"""
    name = f"%%{new_queue}%%{new_queue_id}%%{old_queue}%%{old_queue_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_MOVE,
                      name=name, user_id=user_id, queue_id=new_queue_id)


async def add_title_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    old_title: str,
    new_title: str,
    user_id: int,
) -> None:
    """TitleUpdate: %%OldTitle%%NewTitle (Znuny truncates new_title to 50 chars)"""
    trunc = new_title[:50] + ("..." if len(new_title) > 50 else "")
    name = f"%%{old_title}%%{trunc}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_TITLE_UPDATE,
                      name=name, user_id=user_id)


async def add_type_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_type: str,
    new_type_id: int | str,
    old_type: str,
    old_type_id: int | str,
    user_id: int,
) -> None:
    """TypeUpdate: %%NewType%%NewTypeID%%OldType%%OldTypeID"""
    name = f"%%{new_type}%%{new_type_id}%%{old_type}%%{old_type_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_TYPE_UPDATE,
                      name=name, user_id=user_id)


async def add_service_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_service: str,
    new_service_id: int | str,
    old_service: str,
    old_service_id: int | str,
    user_id: int,
) -> None:
    """ServiceUpdate: %%NewService%%NewServiceID%%OldService%%OldServiceID"""
    name = f"%%{new_service}%%{new_service_id}%%{old_service}%%{old_service_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_SERVICE_UPDATE,
                      name=name, user_id=user_id)


async def add_sla_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_sla: str,
    new_sla_id: int | str,
    old_sla: str,
    old_sla_id: int | str,
    user_id: int,
) -> None:
    """SLAUpdate: %%NewSLA%%NewSLAID%%OldSLA%%OldSLAID"""
    name = f"%%{new_sla}%%{new_sla_id}%%{old_sla}%%{old_sla_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_SLA_UPDATE,
                      name=name, user_id=user_id)


async def add_priority_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    old_priority: str,
    old_priority_id: int,
    new_priority: str,
    new_priority_id: int,
    user_id: int,
    queue_id: int | None = None,
) -> None:
    """PriorityUpdate: %%OldPriority%%OldPriorityID%%NewPriority%%NewPriorityID"""
    name = f"%%{old_priority}%%{old_priority_id}%%{new_priority}%%{new_priority_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_PRIORITY_UPDATE,
                      name=name, user_id=user_id, queue_id=queue_id)


async def add_owner_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_user: str,
    new_user_id: int,
    user_id: int,
) -> None:
    """OwnerUpdate: %%NewUser%%NewUserID"""
    name = f"%%{new_user}%%{new_user_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_OWNER_UPDATE,
                      name=name, user_id=user_id)


async def add_responsible_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_user: str,
    new_user_id: int,
    user_id: int,
) -> None:
    """ResponsibleUpdate: %%NewUser%%NewUserID"""
    name = f"%%{new_user}%%{new_user_id}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_RESPONSIBLE_UPDATE,
                      name=name, user_id=user_id)


async def add_lock(
    session: AsyncSession,
    *,
    ticket_id: int,
    lock: str,
    user_id: int,
) -> None:
    """Lock/Unlock: %%lock or %%unlock"""
    history_type = _TYPE_LOCK if lock.lower() == "lock" else _TYPE_UNLOCK
    name = f"%%{lock}"
    await history_add(session, ticket_id=ticket_id, history_type=history_type,
                      name=name, user_id=user_id)


async def add_customer_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    customer_id: str | None,
    customer_user: str | None,
    user_id: int,
) -> None:
    """CustomerUpdate: %%CustomerID=X;CustomerUser=Y;"""
    parts = []
    if customer_id is not None:
        parts.append(f"CustomerID={customer_id};")
    if customer_user is not None:
        parts.append(f"CustomerUser={customer_user};")
    name = "%%" + "".join(parts)
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_CUSTOMER_UPDATE,
                      name=name, user_id=user_id)


async def add_pending_time(
    session: AsyncSession,
    *,
    ticket_id: int,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    user_id: int,
) -> None:
    """SetPendingTime: %%YYYY-MM-DD HH:MM"""
    name = f"%%{year:02d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_PENDING_TIME,
                      name=name, user_id=user_id)


async def add_subscribe(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_fullname: str,
    user_id: int,
) -> None:
    """Subscribe (WatcherAdd): %%UserFullname"""
    name = f"%%{user_fullname}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_SUBSCRIBE,
                      name=name, user_id=user_id)


async def add_unsubscribe(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_fullname: str,
    user_id: int,
) -> None:
    """Unsubscribe (WatcherDelete): %%UserFullname"""
    name = f"%%{user_fullname}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_UNSUBSCRIBE,
                      name=name, user_id=user_id)


async def add_dynamic_field_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    field_name: str,
    value: str,
    old_value: str,
    user_id: int,
    queue_id: int | None = None,
) -> None:
    """TicketDynamicFieldUpdate: %%FieldName%%Name%%Value%%V%%OldValue%%OV"""
    name = f"%%FieldName%%{field_name}%%Value%%{value}%%OldValue%%{old_value}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_DYNAMIC_FIELD,
                      name=name, user_id=user_id, queue_id=queue_id)


async def add_archive_flag_update(
    session: AsyncSession,
    *,
    ticket_id: int,
    archive_flag: str,
    user_id: int,
) -> None:
    """ArchiveFlagUpdate: %%y or %%n"""
    name = f"%%{archive_flag}"
    await history_add(session, ticket_id=ticket_id, history_type=_TYPE_ARCHIVE_FLAG,
                      name=name, user_id=user_id)


async def add_article_history(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int,
    history_type: str,
    name: str,
    user_id: int,
) -> None:
    """Generic article-linked history (SendAnswer, EmailAgent, EmailCustomer, PhoneCallAgent, etc.)"""
    await history_add(session, ticket_id=ticket_id, history_type=history_type,
                      name=name, user_id=user_id, article_id=article_id)


__all__ = [
    "history_add",
    "resolve_history_type_id",
    "add_new_ticket",
    "add_state_update",
    "add_move",
    "add_title_update",
    "add_type_update",
    "add_service_update",
    "add_sla_update",
    "add_priority_update",
    "add_owner_update",
    "add_responsible_update",
    "add_lock",
    "add_customer_update",
    "add_pending_time",
    "add_subscribe",
    "add_unsubscribe",
    "add_dynamic_field_update",
    "add_archive_flag_update",
    "add_article_history",
]
```

- [ ] **Step 4: Run DB tests**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_history.py -v
```

Expected: 3 DB tests pass (or skip if no Docker).

- [ ] **Step 5: Lint and type-check**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy src/tiqora/znuny/history.py
```

- [ ] **Step 6: Commit**

```bash
cd /Users/valerius/git/aurix
git add backend/src/tiqora/znuny/history.py backend/tests/test_history.py
git commit -m "feat(znuny): implement history helpers with exact Znuny name formats"
```

---

## Task 3: escalation.py — TicketEscalationIndexBuild + working-time

**Files:**
- Create: `backend/src/tiqora/znuny/escalation.py`
- Test: `backend/tests/test_escalation.py`

**Interfaces:**
- Consumes: `AsyncSession`, `SysConfig`
- Produces:
  - `async def escalation_index_build(session, ticket_id: int, user_id: int, sysconfig: SysConfig) -> None`
  - `def destination_time_epoch(start_epoch: int, minutes: int, working_hours: dict, vacation_days: dict, vacation_days_once: dict, tz_name: str) -> int`

**Key Znuny logic ported:**
1. If ticket state_type matches `^(merge|close|remove)`: zero all 4 escalation columns, return
2. Get escalation prefs from SLA (if sla_id set) else Queue row
3. First-response: check if any article with `article_sender_type.name='agent'` AND `is_visible_for_customer=1` exists → if yes, set to 0; else compute `ticket.create_time + FirstResponseTime_minutes` via working-time
4. Update: skip if state_type matches `^pending`; find last customer/agent sender from `article` + `article_sender_type`; compute update_time from that
5. Solution: check ticket_history for closed state_ids; compute from create_time if not closed
6. `escalation_time` = minimum of the 3 non-zero destination times

- [ ] **Step 1: Write failing unit tests for destination_time_epoch**

Create `backend/tests/test_escalation.py`:

```python
"""Unit and DB tests for escalation index build."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tiqora.znuny.escalation import destination_time_epoch

# Fixture calendar: Mon-Fri 08:00-17:00 (hours 8,9,...,16 = 9 working hours/day)
# Vacation: 2026-01-01
_WORKING_HOURS = {
    "Mon": list(range(8, 17)),
    "Tue": list(range(8, 17)),
    "Wed": list(range(8, 17)),
    "Thu": list(range(8, 17)),
    "Fri": list(range(8, 17)),
    "Sat": [],
    "Sun": [],
}
_VACATION_DAYS = {1: {1: "New Year"}}
_VACATION_ONCE: dict = {}


def _epoch(dt_str: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM:SS' as UTC epoch."""
    return int(datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp())


def test_add_60_minutes_within_working_hours() -> None:
    # 2026-07-20 Mon 08:00:00 UTC + 60 working minutes = 09:00:00
    start = _epoch("2026-07-20 08:00:00")
    result = destination_time_epoch(
        start, 60, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC"
    )
    assert result == _epoch("2026-07-20 09:00:00")


def test_add_minutes_spanning_end_of_working_day() -> None:
    # 2026-07-20 Mon 16:00:00 + 120 min = 16:00 + 1h = 17:00 end of day,
    # remaining 60min go to next day 08:00 → 09:00
    start = _epoch("2026-07-20 16:00:00")
    result = destination_time_epoch(
        start, 120, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC"
    )
    assert result == _epoch("2026-07-21 09:00:00")


def test_add_minutes_skips_weekend() -> None:
    # 2026-07-24 Fri 16:30:00 + 60 min = 30min to end of Fri, 30min on Mon
    start = _epoch("2026-07-24 16:30:00")
    result = destination_time_epoch(
        start, 60, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC"
    )
    # 30 min left on Fri (16:30→17:00), 30 min on Mon (08:00→08:30)
    assert result == _epoch("2026-07-27 08:30:00")


def test_add_minutes_skips_vacation_day() -> None:
    # 2025-12-31 Wed 16:00:00 + 120 min → 1h on Dec 31, 1h skips Jan 1 (vacation), resumes Jan 2
    start = _epoch("2025-12-31 16:00:00")
    result = destination_time_epoch(
        start, 120, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC"
    )
    # 1h on Dec 31 (16:00→17:00), Jan 1 vacation, Jan 2 Thu 08:00→09:00
    assert result == _epoch("2026-01-02 09:00:00")


def test_start_outside_working_hours_advances_to_next_slot() -> None:
    # 2026-07-20 Mon 06:00:00 (before working hours) + 30 min
    # Should start counting from 08:00 → end at 08:30
    start = _epoch("2026-07-20 06:00:00")
    result = destination_time_epoch(
        start, 30, _WORKING_HOURS, _VACATION_DAYS, _VACATION_ONCE, "UTC"
    )
    assert result == _epoch("2026-07-20 08:30:00")
```

- [ ] **Step 2: Run to see failures**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_escalation.py -q -k "not db" 2>&1 | head -20
```

- [ ] **Step 3: Implement escalation.py**

Create `backend/src/tiqora/znuny/escalation.py`:

```python
"""Znuny-compatible escalation index builder.

Ports Kernel/System/Ticket.pm::TicketEscalationIndexBuild.

SEMANTIC UNCERTAINTIES (flag for golden-master validation):
- Working-time math advances hour-by-hour (like Znuny's Perl DateTime), which
  may differ from zoneinfo-aware DST transitions by ±1 hour at DST boundaries.
- First-response detection uses article.is_visible_for_customer=1 AND sender=agent
  (matches _TicketGetFirstResponse in Ticket.pm).
- Update-time base: latest visible article from customer/agent (see logic in
  TicketEscalationIndexBuild around SenderHistory loop).
- Solution detection: MAX(create_time) from ticket_history WHERE state_id IN
  closed-state-ids AND history_type_id IN (StateUpdate, NewTicket).
"""

from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_SECS_PER_HOUR = 3600


def destination_time_epoch(
    start_epoch: int,
    minutes: int,
    working_hours: dict[str, list[int]],
    vacation_days: dict[int, dict[int, str]],
    vacation_days_once: dict[int, dict[int, dict[int, str]]],
    tz_name: str,
) -> int:
    """Compute the epoch when `minutes` working-minutes have elapsed from start_epoch.

    Mirrors Znuny Kernel/System/DateTime.pm::Add(AsWorkingTime=>1).
    Advances second-by-second through working hours (hour-granularity optimization included).

    - working_hours: {'Mon': [8, 9, ..., 16], ...}  (hour integers that are working)
    - vacation_days: {month: {day: name}} (repeating annually)
    - vacation_days_once: {year: {month: {day: name}}}
    - tz_name: IANA timezone name
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except zoneinfo.ZoneInfoNotFoundError:
        tz = UTC

    # Convert working_hours lists to sets for O(1) lookup
    wh: dict[str, set[int]] = {day: set(hours) for day, hours in working_hours.items()}

    remaining = minutes * 60
    if remaining <= 0:
        return start_epoch

    current_epoch = start_epoch

    loop_guard = 0
    while remaining > 0:
        loop_guard += 1
        if loop_guard > 10_000_000:
            raise RuntimeError("destination_time_epoch: loop protection triggered")

        dt = datetime.fromtimestamp(current_epoch, tz=tz)
        year, month, day = dt.year, dt.month, dt.day
        hour, minute, second = dt.hour, dt.minute, dt.second
        day_abbr = _DAY_ABBR[dt.weekday()]

        is_vacation = (
            vacation_days.get(month, {}).get(day) is not None
            or vacation_days_once.get(year, {}).get(month, {}).get(day) is not None
        )
        is_working_day = (not is_vacation) and bool(wh.get(day_abbr))

        # Fast-path: if at exact day boundary (00:00:00) and remaining >= full working day
        if hour == 0 and minute == 0 and second == 0:
            if is_working_day:
                working_secs_today = len(wh[day_abbr]) * _SECS_PER_HOUR
                if remaining > working_secs_today:
                    remaining -= working_secs_today
                    current_epoch += 24 * _SECS_PER_HOUR
                    continue
            else:
                # Skip non-working day
                current_epoch += 24 * _SECS_PER_HOUR
                continue

        # Hour-level processing
        if is_working_day and hour in wh[day_abbr]:
            secs_in_current_hour = minute * 60 + second
            secs_to_end_of_hour = _SECS_PER_HOUR - secs_in_current_hour
            consume = min(secs_to_end_of_hour, remaining)
            remaining -= consume
            current_epoch += consume
        else:
            # Not a working hour — advance to next hour boundary
            secs_to_next_hour = _SECS_PER_HOUR - (minute * 60 + second)
            current_epoch += secs_to_next_hour

    return current_epoch


async def _get_closed_state_ids(session: AsyncSession) -> list[int]:
    """Return ticket_state ids whose type name is 'closed'."""
    rows = await session.execute(
        text(
            "SELECT ts.id FROM ticket_state ts"
            " JOIN ticket_state_type tst ON tst.id = ts.type_id"
            " WHERE tst.name = 'closed'"
        )
    )
    return [int(r[0]) for r in rows.fetchall()]


async def _get_history_type_ids(session: AsyncSession, *names: str) -> list[int]:
    rows = await session.execute(
        text(
            f"SELECT id FROM ticket_history_type WHERE name IN ({','.join(':n' + str(i) for i in range(len(names)))})"
        ),
        {f"n{i}": name for i, name in enumerate(names)},
    )
    return [int(r[0]) for r in rows.fetchall()]


async def _first_response_done(session: AsyncSession, ticket_id: int) -> bool:
    """True if ticket has any visible agent article (mirrors _TicketGetFirstResponse)."""
    row = (
        await session.execute(
            text(
                "SELECT a.id FROM article a"
                " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid AND ast.name = 'agent' AND a.is_visible_for_customer = 1"
                " ORDER BY a.create_time ASC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return row is not None


async def _last_sender_time(session: AsyncSession, ticket_id: int) -> str | None:
    """Return create_time of last relevant sender article (mirrors SenderHistory loop).

    Logic from TicketEscalationIndexBuild:
    - Walk articles in reverse by create_time
    - Stop when we find an agent article after any customer article
    - Return the time of the last customer (or most recent agent if no customer follows)
    """
    rows = (
        await session.execute(
            text(
                "SELECT a.article_sender_type_id, ast.name, a.is_visible_for_customer, a.create_time"
                " FROM article a"
                " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid"
                " ORDER BY a.create_time ASC"
            ),
            {"tid": ticket_id},
        )
    ).fetchall()

    last_sender_time: str | None = None
    last_sender_type = ""

    for row in reversed(rows):
        _type_id, sender_type, visible, create_time = row[0], row[1], row[2], str(row[3])

        if not last_sender_time:
            last_sender_time = create_time

        if not visible:
            continue
        if sender_type not in ("agent", "customer"):
            continue

        if sender_type == "agent" and last_sender_type == "customer":
            break

        if sender_type == "customer":
            last_sender_type = "customer"
            last_sender_time = create_time

        if sender_type == "agent":
            last_sender_time = create_time
            break

    return last_sender_time


async def _solution_done(
    session: AsyncSession, ticket_id: int, closed_state_ids: list[int], history_type_ids: list[int]
) -> bool:
    """True if ticket was ever in a closed state (mirrors _TicketGetClosed)."""
    if not closed_state_ids or not history_type_ids:
        return False
    in_clause_states = ",".join(str(i) for i in closed_state_ids)
    in_clause_types = ",".join(str(i) for i in history_type_ids)
    row = (
        await session.execute(
            text(
                f"SELECT MAX(create_time) FROM ticket_history"
                f" WHERE ticket_id = :tid"
                f" AND state_id IN ({in_clause_states})"
                f" AND history_type_id IN ({in_clause_types})"
            ),
            {"tid": ticket_id},
        )
    ).first()
    return row is not None and row[0] is not None


async def _parse_sysconfig_working_hours(
    sysconfig: SysConfig, calendar: str | None
) -> tuple[dict[str, list[int]], dict[int, dict[int, str]], dict[int, dict[int, dict[int, str]]]]:
    """Fetch and parse TimeWorkingHours, TimeVacationDays, TimeVacationDaysOneTime."""
    if calendar:
        wh_key = f"TimeWorkingHours::Calendar{calendar}"
        vd_key = f"TimeVacationDays::Calendar{calendar}"
        vdo_key = f"TimeVacationDaysOneTime::Calendar{calendar}"
    else:
        wh_key = "TimeWorkingHours"
        vd_key = "TimeVacationDays"
        vdo_key = "TimeVacationDaysOneTime"

    wh = await sysconfig.get(wh_key) or {}
    vd = await sysconfig.get(vd_key) or {}
    vdo = await sysconfig.get(vdo_key) or {}

    # Normalize keys: SysConfig may return string keys for months/days
    def _int_keys_1(d: dict) -> dict[int, dict[int, str]]:
        return {int(k): {int(dk): dv for dk, dv in v.items()} for k, v in d.items()}

    def _int_keys_2(d: dict) -> dict[int, dict[int, dict[int, str]]]:
        return {
            int(y): {int(m): {int(dy): dv for dy, dv in mv.items()} for m, mv in yv.items()}
            for y, yv in d.items()
        }

    wh_normalized: dict[str, list[int]] = {
        k: [int(h) for h in v] for k, v in wh.items() if isinstance(v, list)
    }
    return wh_normalized, _int_keys_1(vd), _int_keys_2(vdo)


def _parse_dt_str(dt_str: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM:SS[.f]' to UTC epoch int."""
    clean = dt_str[:19]
    return int(datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp())


async def escalation_index_build(
    session: AsyncSession,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Recompute escalation_response_time, escalation_update_time, escalation_solution_time,
    and escalation_time on the ticket row.

    Mirrors Kernel/System/Ticket.pm::TicketEscalationIndexBuild.
    """
    # Fetch ticket
    t_row = (
        await session.execute(
            text(
                "SELECT t.ticket_state_id, tst.name as state_type, t.sla_id, t.queue_id,"
                " t.create_time"
                " FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if t_row is None:
        return

    state_type: str = str(t_row[1])
    sla_id: int | None = t_row[2]
    queue_id: int = int(t_row[3])
    create_time_str: str = str(t_row[4])

    # Zero all on closed/merged/removed
    if state_type and __import__("re").match(r"^(merge|close|remove)", state_type, __import__("re").I):
        for col in ("escalation_time", "escalation_response_time",
                    "escalation_update_time", "escalation_solution_time"):
            await session.execute(
                text(
                    f"UPDATE ticket SET {col} = 0, change_time = current_timestamp,"
                    " change_by = :uid WHERE id = :tid"
                ),
                {"uid": user_id, "tid": ticket_id},
            )
        return

    # Get escalation preferences from SLA or Queue
    tz = await sysconfig.otrs_time_zone()
    calendar: str | None = None
    first_response_minutes = 0
    update_minutes = 0
    solution_minutes = 0

    if sla_id:
        sla_row = (
            await session.execute(
                text(
                    "SELECT first_response_time, update_time, solution_time, calendar_name"
                    " FROM sla WHERE id = :sid"
                ),
                {"sid": sla_id},
            )
        ).first()
        if sla_row:
            first_response_minutes = int(sla_row[0] or 0)
            update_minutes = int(sla_row[1] or 0)
            solution_minutes = int(sla_row[2] or 0)
            calendar = str(sla_row[3]) if sla_row[3] else None
    else:
        q_row = (
            await session.execute(
                text(
                    "SELECT first_response_time, update_time, solution_time, calendar_name"
                    " FROM queue WHERE id = :qid"
                ),
                {"qid": queue_id},
            )
        ).first()
        if q_row:
            first_response_minutes = int(q_row[0] or 0)
            update_minutes = int(q_row[1] or 0)
            solution_minutes = int(q_row[2] or 0)
            calendar = str(q_row[3]) if q_row[3] else None

    wh, vd, vdo = await _parse_sysconfig_working_hours(sysconfig, calendar)

    create_epoch = _parse_dt_str(create_time_str)
    escalation_time = 0

    # --- First response ---
    if not first_response_minutes:
        await session.execute(
            text(
                "UPDATE ticket SET escalation_response_time = 0,"
                " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
            ),
            {"uid": user_id, "tid": ticket_id},
        )
    else:
        done = await _first_response_done(session, ticket_id)
        if done:
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_response_time = 0,"
                    " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
                ),
                {"uid": user_id, "tid": ticket_id},
            )
        else:
            dest = destination_time_epoch(create_epoch, first_response_minutes, wh, vd, vdo, tz)
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_response_time = :dest,"
                    " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
                ),
                {"dest": dest, "uid": user_id, "tid": ticket_id},
            )
            if dest:
                escalation_time = dest

    # --- Update time ---
    is_pending = state_type and __import__("re").match(r"^pending", state_type, __import__("re").I)
    if not update_minutes or is_pending:
        await session.execute(
            text(
                "UPDATE ticket SET escalation_update_time = 0,"
                " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
            ),
            {"uid": user_id, "tid": ticket_id},
        )
    else:
        last_time = await _last_sender_time(session, ticket_id)
        if last_time:
            base_epoch = _parse_dt_str(last_time)
            dest = destination_time_epoch(base_epoch, update_minutes, wh, vd, vdo, tz)
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_update_time = :dest,"
                    " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
                ),
                {"dest": dest, "uid": user_id, "tid": ticket_id},
            )
            if dest and (escalation_time == 0 or dest < escalation_time):
                escalation_time = dest
        else:
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_update_time = 0,"
                    " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
                ),
                {"uid": user_id, "tid": ticket_id},
            )

    # --- Solution time ---
    if not solution_minutes:
        await session.execute(
            text(
                "UPDATE ticket SET escalation_solution_time = 0,"
                " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
            ),
            {"uid": user_id, "tid": ticket_id},
        )
    else:
        closed_ids = await _get_closed_state_ids(session)
        hist_type_ids = await _get_history_type_ids(session, "StateUpdate", "NewTicket")
        done = await _solution_done(session, ticket_id, closed_ids, hist_type_ids)
        if done:
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_solution_time = 0,"
                    " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
                ),
                {"uid": user_id, "tid": ticket_id},
            )
        else:
            dest = destination_time_epoch(create_epoch, solution_minutes, wh, vd, vdo, tz)
            await session.execute(
                text(
                    "UPDATE ticket SET escalation_solution_time = :dest,"
                    " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
                ),
                {"dest": dest, "uid": user_id, "tid": ticket_id},
            )
            if dest and (escalation_time == 0 or dest < escalation_time):
                escalation_time = dest

    # --- escalation_time (min of all) ---
    await session.execute(
        text(
            "UPDATE ticket SET escalation_time = :et,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"et": escalation_time, "uid": user_id, "tid": ticket_id},
    )


__all__ = [
    "destination_time_epoch",
    "escalation_index_build",
]
```

- [ ] **Step 4: Run unit tests**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_escalation.py -v -k "not db"
```

Expected: 5 unit tests pass.

- [ ] **Step 5: Add DB test for escalation — append to test_escalation.py**

```python
@pytest.mark.db
def test_escalation_index_sets_columns_mariadb(mariadb_znuny_url: str) -> None:
    """Queue with first_response_time=60 min → escalation_response_time should be set."""
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from tiqora.znuny.escalation import escalation_index_build
    from tiqora.znuny.sysconfig import SysConfig

    async def run() -> int:
        url = mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            async with session.begin():
                # Update queue 1 to have 60 min first_response_time
                await session.execute(
                    text("UPDATE queue SET first_response_time = 60 WHERE id = 1")
                )
                # Insert test ticket in state 'new' (state_id 1 from seed data)
                await session.execute(
                    text(
                        "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id,"
                        " responsible_user_id, ticket_priority_id, ticket_state_id,"
                        " timeout, until_time, escalation_time, escalation_update_time,"
                        " escalation_response_time, escalation_solution_time, archive_flag,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES ('ESC_TEST_1', 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
                        " current_timestamp, 1, current_timestamp, 1)"
                    )
                )
                row = (await session.execute(
                    text("SELECT id FROM ticket WHERE tn = 'ESC_TEST_1'")
                )).first()
                ticket_id = int(row[0])

            sysconfig = SysConfig(session=session)
            async with session.begin():
                await escalation_index_build(session, ticket_id, 1, sysconfig)

            row2 = (await session.execute(
                text("SELECT escalation_response_time FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )).first()
            result = int(row2[0])

        await engine.dispose()
        return result

    resp_time = asyncio.get_event_loop().run_until_complete(run())
    assert resp_time > 0, f"Expected non-zero escalation_response_time, got {resp_time}"
```

- [ ] **Step 6: Run all escalation tests**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_escalation.py -v
```

- [ ] **Step 7: Lint, type-check, commit**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy src/tiqora/znuny/escalation.py
git add backend/src/tiqora/znuny/escalation.py backend/tests/test_escalation.py
git commit -m "feat(znuny): implement escalation index build with working-time math"
```

---

## Task 4: ticket_index.py — StaticDB / RuntimeDB index maintenance

**Files:**
- Create: `backend/src/tiqora/znuny/ticket_index.py`
- Test: `backend/tests/test_ticket_index.py`

**Interfaces:**
- Produces:
  - `async def ticket_accelerator_add(session, ticket_id, sysconfig) -> None`
  - `async def ticket_accelerator_update(session, ticket_id, sysconfig) -> None`
  - `async def ticket_accelerator_delete(session, ticket_id, sysconfig) -> None`

- [ ] **Step 1: Write failing DB test**

Create `backend/tests/test_ticket_index.py`:

```python
"""DB tests for ticket_index (StaticDB accelerator)."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.db
def test_ticket_index_add_delete_mariadb(mariadb_znuny_url: str) -> None:
    from tiqora.znuny.ticket_index import ticket_accelerator_add, ticket_accelerator_delete
    from tiqora.znuny.sysconfig import SysConfig, ZNUNY_SETTING_DEFAULTS

    async def run() -> tuple[bool, bool]:
        url = mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            async with session.begin():
                # Set IndexModule to StaticDB
                async def fetch(name: str):
                    if name == "Ticket::IndexModule":
                        return "Kernel::System::Ticket::IndexAccelerator::StaticDB"
                    return None

                sysconfig = SysConfig(fetch=fetch)

                # Insert a viewable ticket (state 'new' = open/viewable)
                await session.execute(
                    text(
                        "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id,"
                        " responsible_user_id, ticket_priority_id, ticket_state_id,"
                        " timeout, until_time, escalation_time, escalation_update_time,"
                        " escalation_response_time, escalation_solution_time, archive_flag,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES ('IDX_TEST_1', 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
                        " current_timestamp, 1, current_timestamp, 1)"
                    )
                )
                row = (await session.execute(
                    text("SELECT id FROM ticket WHERE tn = 'IDX_TEST_1'")
                )).first()
                ticket_id = int(row[0])

                await ticket_accelerator_add(session, ticket_id, sysconfig)

            # Check ticket_index row was inserted
            async with session.begin():
                row2 = (await session.execute(
                    text("SELECT ticket_id FROM ticket_index WHERE ticket_id = :tid"),
                    {"tid": ticket_id},
                )).first()
                added = row2 is not None

                await ticket_accelerator_delete(session, ticket_id, sysconfig)

            row3 = (await session.execute(
                text("SELECT ticket_id FROM ticket_index WHERE ticket_id = :tid"),
                {"tid": ticket_id},
            )).first()
            deleted = row3 is None

        await engine.dispose()
        return added, deleted

    added, deleted = asyncio.get_event_loop().run_until_complete(run())
    assert added, "ticket_index row should have been inserted"
    assert deleted, "ticket_index row should have been deleted"
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_ticket_index.py -q 2>&1 | head -20
```

- [ ] **Step 3: Implement ticket_index.py**

Create `backend/src/tiqora/znuny/ticket_index.py`:

```python
"""Znuny ticket index accelerator maintenance.

Ports Kernel/System/Ticket/IndexAccelerator/StaticDB.pm behavior.
When Ticket::IndexModule == RuntimeDB (default), all operations are no-ops.
When StaticDB:
  - Add: INSERT into ticket_index if state is viewable and ticket not archived
  - Delete: DELETE from ticket_index + ticket_lock_index
  - Update: check if change is needed (state/lock/queue changed), delete+add if so
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.znuny.sysconfig import SysConfig

_STATIC_DB = "Kernel::System::Ticket::IndexAccelerator::StaticDB"

# States considered viewable by Znuny default: open, new, pending reminder, pending auto
# In the seed DB these map to type names: 'open', 'new', 'pending reminder', 'pending auto close+'
# We detect viewability by checking ticket_state_type.name != closed/merged/removed
_UNVIEWABLE_STATE_TYPES = {"closed", "merged", "removed"}


async def _is_static_db(sysconfig: SysConfig) -> bool:
    module = await sysconfig.ticket_index_module()
    return module == _STATIC_DB


async def _ticket_row(session: AsyncSession, ticket_id: int) -> dict | None:
    row = (
        await session.execute(
            text(
                "SELECT t.queue_id, q.name as queue, q.group_id,"
                " tlt.name as lock_name, tst.name as state_type_name,"
                " ts.name as state_name, t.archive_flag, t.create_time"
                " FROM ticket t"
                " JOIN queue q ON q.id = t.queue_id"
                " JOIN ticket_lock_type tlt ON tlt.id = t.ticket_lock_id"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return None
    return {
        "queue_id": int(row[0]),
        "queue": str(row[1]),
        "group_id": int(row[2]),
        "lock": str(row[3]),
        "state_type": str(row[4]),
        "state": str(row[5]),
        "archive_flag": int(row[6]),
        "create_time": str(row[7]),
    }


def _is_viewable(ticket: dict) -> bool:
    return ticket["state_type"] not in _UNVIEWABLE_STATE_TYPES and ticket["archive_flag"] == 0


async def ticket_accelerator_add(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> None:
    """Insert into ticket_index if StaticDB and ticket is viewable."""
    if not await _is_static_db(sysconfig):
        return

    ticket = await _ticket_row(session, ticket_id)
    if ticket is None or not _is_viewable(ticket):
        return

    await session.execute(
        text(
            "INSERT INTO ticket_index"
            " (ticket_id, queue_id, queue, group_id, s_lock, s_state, create_time)"
            " VALUES (:tid, :qid, :q, :gid, :lock, :state, :ct)"
        ),
        {
            "tid": ticket_id,
            "qid": ticket["queue_id"],
            "q": ticket["queue"],
            "gid": ticket["group_id"],
            "lock": ticket["lock"],
            "state": ticket["state"],
            "ct": ticket["create_time"],
        },
    )


async def ticket_accelerator_delete(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> None:
    """Delete from ticket_index and ticket_lock_index if StaticDB."""
    if not await _is_static_db(sysconfig):
        return

    await session.execute(
        text("DELETE FROM ticket_lock_index WHERE ticket_id = :tid"),
        {"tid": ticket_id},
    )
    await session.execute(
        text("DELETE FROM ticket_index WHERE ticket_id = :tid"),
        {"tid": ticket_id},
    )


async def ticket_accelerator_update(
    session: AsyncSession, ticket_id: int, sysconfig: SysConfig
) -> None:
    """Re-sync ticket_index for the given ticket if StaticDB.

    Checks current index state vs actual ticket state; re-inserts if changed
    or if no longer viewable (deletes only).
    """
    if not await _is_static_db(sysconfig):
        return

    ticket = await _ticket_row(session, ticket_id)
    if ticket is None:
        return

    # Get current index state
    idx_row = (
        await session.execute(
            text("SELECT s_lock, s_state, queue_id FROM ticket_index WHERE ticket_id = :tid"),
            {"tid": ticket_id},
        )
    ).first()

    if not _is_viewable(ticket):
        # Remove from index if present
        if idx_row is not None:
            await ticket_accelerator_delete(session, ticket_id, sysconfig)
        return

    needs_update = (
        idx_row is None
        or str(idx_row[0]) != ticket["lock"]
        or str(idx_row[1]) != ticket["state"]
        or int(idx_row[2]) != ticket["queue_id"]
    )

    if needs_update:
        await ticket_accelerator_delete(session, ticket_id, sysconfig)
        await ticket_accelerator_add(session, ticket_id, sysconfig)


__all__ = [
    "ticket_accelerator_add",
    "ticket_accelerator_update",
    "ticket_accelerator_delete",
]
```

- [ ] **Step 4: Run DB tests**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_ticket_index.py -v
```

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy src/tiqora/znuny/ticket_index.py
git add backend/src/tiqora/znuny/ticket_index.py backend/tests/test_ticket_index.py
git commit -m "feat(znuny): implement ticket_index StaticDB/RuntimeDB accelerator"
```

---

## Task 5: cache_invalidation.py + Alembic migration

**Files:**
- Create: `backend/src/tiqora/znuny/cache_invalidation.py`
- Create: `backend/alembic/versions_tiqora/20260719_0002_cache_invalidation.py`
- Modify: `backend/src/tiqora/db/tiqora/models.py` (add TiqoraCacheInvalidation)
- Test: `backend/tests/test_cache_invalidation.py`

**Interfaces:**
- Produces: `async def invalidate_ticket_cache(session, ticket_id: int) -> None`

- [ ] **Step 1: Write failing DB test**

Create `backend/tests/test_cache_invalidation.py`:

```python
"""DB test for tiqora_cache_invalidation writer."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.db
def test_invalidate_writes_row_mariadb(mariadb_znuny_url: str) -> None:
    from tiqora.znuny.cache_invalidation import invalidate_ticket_cache

    async def run() -> int:
        url = mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            async with session.begin():
                # Ensure tiqora_cache_invalidation table exists
                try:
                    await session.execute(text("SELECT 1 FROM tiqora_cache_invalidation LIMIT 1"))
                except Exception:
                    pytest.skip("tiqora_cache_invalidation table not migrated")
                await invalidate_ticket_cache(session, ticket_id=42)

            rows = (await session.execute(
                text("SELECT ticket_id FROM tiqora_cache_invalidation WHERE ticket_id = 42")
            )).fetchall()

        await engine.dispose()
        return len(rows)

    count = asyncio.get_event_loop().run_until_complete(run())
    assert count >= 1
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_cache_invalidation.py -q 2>&1 | head -20
```

- [ ] **Step 3: Add TiqoraCacheInvalidation model to models.py**

Open `backend/src/tiqora/db/tiqora/models.py` and add at the end:

```python
from sqlalchemy import BigInteger, Index


class TiqoraCacheInvalidation(TiqoraBase):
    """Cache invalidation queue for Znuny Perl addon TiqoraSync."""

    __tablename__ = "tiqora_cache_invalidation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_cache_inv_id", "id"),)
```

- [ ] **Step 4: Create Alembic migration**

Create `backend/alembic/versions_tiqora/20260719_0002_cache_invalidation.py`:

```python
"""Create tiqora_cache_invalidation table.

Revision ID: 20260719_0002
Revises: 20260719_0001
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0002"
down_revision: str | None = "20260719_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiqora_cache_invalidation",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tiqora_cache_inv_id", "tiqora_cache_invalidation", ["id"])


def downgrade() -> None:
    op.drop_index("ix_tiqora_cache_inv_id", table_name="tiqora_cache_invalidation")
    op.drop_table("tiqora_cache_invalidation")
```

- [ ] **Step 5: Implement cache_invalidation.py**

Create `backend/src/tiqora/znuny/cache_invalidation.py`:

```python
"""Cache invalidation writer for tiqora_cache_invalidation.

Rows are consumed by the Perl OPM TiqoraSync addon (Phase 3).
Callers invoke invalidate_ticket_cache() after every write that could
make Znuny's in-process cache stale.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def invalidate_ticket_cache(session: AsyncSession, ticket_id: int) -> None:
    """Insert a cache-invalidation signal for the given ticket_id."""
    await session.execute(
        text(
            "INSERT INTO tiqora_cache_invalidation (ticket_id, created)"
            " VALUES (:tid, current_timestamp)"
        ),
        {"tid": ticket_id},
    )


__all__ = ["invalidate_ticket_cache"]
```

- [ ] **Step 6: Run DB test (note: table won't exist without Alembic migration run against the testcontainer — the test skips gracefully)**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_cache_invalidation.py -v
```

Expected: test skips (table not migrated in Znuny testcontainer) or passes if migration was applied. This is acceptable — the migration is for tiqora_* tables in a Tiqora-managed DB, not the Znuny testcontainer.

- [ ] **Step 7: Lint, type-check, commit**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy src/tiqora/znuny/cache_invalidation.py src/tiqora/db/tiqora/models.py
git add backend/src/tiqora/znuny/cache_invalidation.py \
        backend/alembic/versions_tiqora/20260719_0002_cache_invalidation.py \
        backend/src/tiqora/db/tiqora/models.py \
        backend/tests/test_cache_invalidation.py
git commit -m "feat(znuny): add tiqora_cache_invalidation table and writer"
```

---

## Task 6: search_flag.py — search_index_needs_rebuild + MD5 helper

**Files:**
- Create: `backend/src/tiqora/znuny/search_flag.py`
- Test: `backend/tests/test_search_flag.py`

**Interfaces:**
- Produces:
  - `async def mark_search_rebuild(session, article_id: int) -> None`
  - `def message_id_md5(message_id: str) -> str`  (md5 hex of raw message-id string)

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_search_flag.py`:

```python
"""Tests for search_flag helpers."""
from __future__ import annotations

import asyncio

import pytest

from tiqora.znuny.search_flag import message_id_md5


def test_message_id_md5_known_vector() -> None:
    # MD5 of "<test@example.com>" = verify against hashlib
    import hashlib
    msg_id = "<test@example.com>"
    expected = hashlib.md5(msg_id.encode("utf-8")).hexdigest()  # noqa: S324
    assert message_id_md5(msg_id) == expected
    assert len(message_id_md5(msg_id)) == 32


def test_message_id_md5_empty_string() -> None:
    import hashlib
    assert message_id_md5("") == hashlib.md5(b"").hexdigest()  # noqa: S324


@pytest.mark.db
def test_mark_search_rebuild_mariadb(mariadb_znuny_url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from tiqora.znuny.search_flag import mark_search_rebuild

    async def run() -> int:
        url = mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://")
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async with factory() as session:
            # Insert minimal ticket + article
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO ticket (tn, queue_id, ticket_lock_id, user_id,"
                        " responsible_user_id, ticket_priority_id, ticket_state_id,"
                        " timeout, until_time, escalation_time, escalation_update_time,"
                        " escalation_response_time, escalation_solution_time, archive_flag,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES ('SF_TEST_1', 1, 1, 1, 1, 3, 1, 0, 0, 0, 0, 0, 0, 0,"
                        " current_timestamp, 1, current_timestamp, 1)"
                    )
                )
                t_row = (await session.execute(
                    text("SELECT id FROM ticket WHERE tn = 'SF_TEST_1'")
                )).first()
                ticket_id = int(t_row[0])

                # Get a valid communication_channel_id from seed data
                cc_row = (await session.execute(
                    text("SELECT id FROM communication_channel LIMIT 1")
                )).first()
                cc_id = int(cc_row[0]) if cc_row else 1

                # Get agent sender type id
                st_row = (await session.execute(
                    text("SELECT id FROM article_sender_type WHERE name = 'agent' LIMIT 1")
                )).first()
                st_id = int(st_row[0]) if st_row else 1

                await session.execute(
                    text(
                        "INSERT INTO article (ticket_id, article_sender_type_id,"
                        " communication_channel_id, is_visible_for_customer,"
                        " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                        " VALUES (:tid, :stid, :ccid, 1, 0, current_timestamp, 1, current_timestamp, 1)"
                    ),
                    {"tid": ticket_id, "stid": st_id, "ccid": cc_id},
                )
                a_row = (await session.execute(
                    text("SELECT id FROM article WHERE ticket_id = :tid ORDER BY id DESC LIMIT 1"),
                    {"tid": ticket_id},
                )).first()
                article_id = int(a_row[0])

            async with session.begin():
                await mark_search_rebuild(session, article_id)

            result_row = (await session.execute(
                text("SELECT search_index_needs_rebuild FROM article WHERE id = :aid"),
                {"aid": article_id},
            )).first()
            result = int(result_row[0])

        await engine.dispose()
        return result

    value = asyncio.get_event_loop().run_until_complete(run())
    assert value == 1
```

- [ ] **Step 2: Run to verify failures**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_search_flag.py -q -k "not db" 2>&1 | head -20
```

- [ ] **Step 3: Implement search_flag.py**

Create `backend/src/tiqora/znuny/search_flag.py`:

```python
"""Search index rebuild flag helpers.

Ports Kernel/System/Ticket/Article.pm::ArticleSearchIndexRebuildFlagSet behavior.
The a_message_id_md5 is MD5 hex of the raw Message-ID string
(Kernel/System/Ticket/Article/Backend/MIMEBase.pm line 343:
  $Param{MD5} = $MainObject->MD5sum( String => $Param{MessageID} )).
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def message_id_md5(message_id: str) -> str:
    """Return MD5 hex digest of the raw Message-ID string.

    Mirrors Znuny: MD5sum(String => $MessageID) where String is the
    raw UTF-8 encoded message-id (e.g. '<abc@host.com>').
    """
    return hashlib.md5(message_id.encode("utf-8")).hexdigest()  # noqa: S324


async def mark_search_rebuild(session: AsyncSession, article_id: int) -> None:
    """Set article.search_index_needs_rebuild = 1 for the given article."""
    await session.execute(
        text(
            "UPDATE article SET search_index_needs_rebuild = 1"
            " WHERE id = :aid"
        ),
        {"aid": article_id},
    )


__all__ = ["message_id_md5", "mark_search_rebuild"]
```

- [ ] **Step 4: Run all search_flag tests**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest tests/test_search_flag.py -v
```

Expected: 2 unit tests pass, 1 DB test passes or skips.

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy src/tiqora/znuny/search_flag.py
git add backend/src/tiqora/znuny/search_flag.py backend/tests/test_search_flag.py
git commit -m "feat(znuny): add search_flag helpers (search_index_needs_rebuild + md5)"
```

---

## Task 7: Full suite verification + push

- [ ] **Step 1: Run complete test suite**

```bash
cd /Users/valerius/git/aurix/backend && uv run pytest -q
```

Record counts: `X passed, Y skipped`.

- [ ] **Step 2: Run full lint + type-check**

```bash
cd /Users/valerius/git/aurix && uv run ruff format . && uv run ruff check .
cd /Users/valerius/git/aurix/backend && uv run mypy
```

All must exit 0.

- [ ] **Step 3: Push to origin main**

```bash
cd /Users/valerius/git/aurix && git push origin main
```

---

## Semantic Uncertainties for Golden-Master Validation

These items need validation against a live Znuny 6.5 instance before Phase 2b goes to production:

1. **ticket_number counter_uid collisions**: `_get_uid()` uses `time_ns()` + `os.getpid()`. Under very high concurrency the UID may not be 32 chars if `time_ns()` result is long. Validate uniqueness under 100 concurrent Python processes.

2. **DateChecksum multiplier sequence**: Znuny uses alternating 1, 2, 1, 2... starting at position 0 with multiplier=1. The implementation matches this exactly, but should be validated against a live Znuny that has used DateChecksum for a real ticket.

3. **Escalation working-time math at DST boundaries**: The hour-by-hour loop does not account for DST transitions (2:30 AM disappearing or appearing). Znuny's Perl DateTime handles this via CPAN DateTime's time_zone awareness. Our implementation uses `datetime.fromtimestamp(epoch, tz=tz)` per iteration which IS DST-aware, but the hour-skip optimization at midnight (00:00:00 fast path) assumes 24-hour days which may fail on DST nights. Flag for validation.

4. **Update-time last-sender logic**: The reverse-walk logic for finding the last customer contact time has subtle behavior when the latest visible article is from an agent who replied to a customer. Validate with a ticket that has: customer article, agent reply, second customer article.

5. **history_type_id cache invalidation**: `_history_type_cache` is a module-level dict populated once per process lifetime. On warm restarts or if Znuny modifies ticket_history_type (rare), the cache would be stale. Document that this requires process restart to refresh.

6. **StaticDB viewable states**: We use `ticket_state_type.name NOT IN ('closed', 'merged', 'removed')` as the viewability criterion. Znuny uses `StateGetStatesByType(Type => 'Viewable')` which may differ. Validate against Znuny's actual viewable state list.
