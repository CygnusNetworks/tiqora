"""Unit and DB tests for Znuny-compatible ticket number generation."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.znuny.ticket_number import (
    format_auto_increment,
    format_date,
    format_date_checksum,
    format_random,
    ticket_number_counter_add,
)

# ---------------------------------------------------------------------------
# Generator formatting (pure functions, no DB)
# ---------------------------------------------------------------------------


def test_auto_increment_pads_to_min_size() -> None:
    # SystemID=10, counter=1 → "10" + "00001"
    assert format_auto_increment(1, "10", min_counter_size=5) == "1000001"


def test_auto_increment_larger_counter() -> None:
    assert format_auto_increment(12345, "10", min_counter_size=5) == "1012345"


def test_auto_increment_counter_exceeding_min_size() -> None:
    # Counter longer than MinCounterSize is not truncated (sprintf %.*u semantics)
    assert format_auto_increment(1234567, "10", min_counter_size=5) == "101234567"


def test_auto_increment_custom_min_size() -> None:
    assert format_auto_increment(1, "10", min_counter_size=3) == "10001"


def test_date_format_basic() -> None:
    # yyyymmdd + SystemID + raw counter (no padding by default)
    assert format_date(42, "10", 2026, 7, 19) == "202607191042"


def test_date_format_with_formatted_counter() -> None:
    result = format_date(42, "10", 2026, 7, 19, use_formatted_counter=True, min_counter_size=5)
    assert result == "2026071910" + "00042"


def test_date_checksum_known_vector_counter_1() -> None:
    # base "202607191000001": digits weighted 1,2,1,2,...
    # 2*1+0*2+2*1+6*2+0*1+7*2+1*1+9*2+1*1+0*2+0*1+0*2+0*1+0*2+1*1 = 51
    # 51 % 10 = 1 → checksum = 10 - 1 = 9
    result = format_date_checksum(1, "10", 2026, 7, 19)
    assert result == "2026071910000019"


def test_date_checksum_known_vector_counter_9() -> None:
    # base "202607191000009": sum = 50 + 9 = 59 → 59%10=9 → checksum 1
    result = format_date_checksum(9, "10", 2026, 7, 19)
    assert result == "2026071910000091"


def test_date_checksum_checksum_10_wraps_to_1() -> None:
    # Find a base whose weighted sum % 10 == 0 → checksum would be 10 → becomes 1.
    # base "202607191000000": sum = 50 → 50%10=0 → checksum = 10 → wraps to 1
    result = format_date_checksum(0, "10", 2026, 7, 19)
    assert result == "2026071910000001"


def test_date_checksum_counter_padded_to_five() -> None:
    result = format_date_checksum(123, "10", 2026, 7, 19)
    assert result.startswith("20260719" + "10" + "00123")
    assert len(result) == len("20260719") + len("10") + 5 + 1


def test_random_format_length_and_prefix() -> None:
    result = format_random("10")
    assert result.startswith("10")
    suffix = result[len("10") :]
    assert len(suffix) == 10
    assert suffix.isdigit()


# ---------------------------------------------------------------------------
# DB tests: counter algorithm under concurrency (both dialects)
# ---------------------------------------------------------------------------


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _pg_async(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def _run_concurrent_counters(
    factory: async_sessionmaker[AsyncSession], n: int = 20
) -> list[int]:
    tasks = [
        ticket_number_counter_add(factory, offset=1, is_date_based=False, tz="UTC")
        for _ in range(n)
    ]
    return list(await asyncio.gather(*tasks))


async def _counter_table_consistent(factory: async_sessionmaker[AsyncSession]) -> bool:
    """All rows filled (counter > 0) and counter values unique."""
    async with factory() as session:
        rows = (await session.execute(text("SELECT counter FROM ticket_number_counter"))).fetchall()
    counters = [int(r[0]) for r in rows]
    return all(c > 0 for c in counters) and len(set(counters)) == len(counters)


@pytest.mark.db
async def test_counter_uniqueness_mariadb(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url), pool_size=10, max_overflow=15)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        counters = await _run_concurrent_counters(factory)
        assert len(counters) == 20
        assert len(set(counters)) == 20, f"Duplicate counters: {sorted(counters)}"
        assert all(c > 0 for c in counters)
        assert await _counter_table_consistent(factory)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_counter_uniqueness_postgres(postgres_znuny_url: str) -> None:
    engine = create_async_engine(_pg_async(postgres_znuny_url), pool_size=10, max_overflow=15)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        counters = await _run_concurrent_counters(factory)
        assert len(counters) == 20
        assert len(set(counters)) == 20, f"Duplicate counters: {sorted(counters)}"
        assert await _counter_table_consistent(factory)
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_date_based_filter_ignores_older_days(mariadb_znuny_url: str) -> None:
    """Date-based fill-up only considers rows created today: an old high-counter
    row from yesterday must not leak into today's sequence."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, session.begin():
            # Simulate yesterday's counter row with a high counter value.
            await session.execute(
                text(
                    "INSERT INTO ticket_number_counter (counter, counter_uid, create_time)"
                    " VALUES (99999, 'yesterday_high_counter_uid_0001',"
                    " '2000-01-01 12:00:00')"
                )
            )
        c1 = await ticket_number_counter_add(factory, offset=1, is_date_based=True, tz="UTC")
        c2 = await ticket_number_counter_add(factory, offset=1, is_date_based=True, tz="UTC")
        # Yesterday's 99999 must be invisible to the date-filtered previous-max
        # lookup: today's counters restart small.
        assert c1 < 99999
        assert c2 == c1 + 1
    finally:
        await engine.dispose()
