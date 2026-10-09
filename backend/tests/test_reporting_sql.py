"""Reporting SQL constructs: golden SQL (pure) and execution on both dialects.

Executions use literal rows only (no seeded ids; band 47700-47709 unused).
"""

from __future__ import annotations

import time
from datetime import date, datetime

import pytest
from sqlalchemy import DateTime, cast, column, func, literal, select, text, union_all
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.reporting.sql import (
    ReportQueryTimeout,
    TzSegment,
    date_bucket,
    date_part,
    is_timeout_error,
    shift_seconds,
    statement_timeout,
    to_local,
    tz_segments,
)

col = column("created_at", DateTime())


def sql(expr, dialect) -> str:  # type: ignore[no-untyped-def]
    return str(select(expr).compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize(
    "grain,expected_mysql,expected_pg",
    [
        ("day", "DATE(created_at)", "CAST(date_trunc('day', created_at) AS DATE)"),
        (
            "week",
            "DATE(DATE_SUB(created_at, INTERVAL WEEKDAY(created_at) DAY))",
            "CAST(date_trunc('week', created_at) AS DATE)",
        ),
        (
            "month",
            "DATE(DATE_SUB(created_at, INTERVAL DAYOFMONTH(created_at) - 1 DAY))",
            "CAST(date_trunc('month', created_at) AS DATE)",
        ),
        (
            "quarter",
            "DATE_ADD(MAKEDATE(YEAR(created_at), 1), INTERVAL QUARTER(created_at) - 1 QUARTER)",
            "CAST(date_trunc('quarter', created_at) AS DATE)",
        ),
        ("year", "MAKEDATE(YEAR(created_at), 1)", "CAST(date_trunc('year', created_at) AS DATE)"),
    ],
)
def test_date_bucket_golden_sql(grain, expected_mysql, expected_pg) -> None:  # type: ignore[no-untyped-def]
    assert expected_mysql in sql(date_bucket(col, grain), mysql.dialect())
    assert expected_mysql in sql(date_bucket(col, grain), mysql.dialect(is_mariadb=True))
    assert expected_pg in sql(date_bucket(col, grain), postgresql.dialect())
    assert "%" not in sql(date_bucket(col, grain), mysql.dialect())


def test_shift_and_part_golden_sql() -> None:
    assert "DATE_ADD(created_at, INTERVAL 3600 SECOND)" in sql(
        shift_seconds(col, 3600), mysql.dialect()
    )
    assert "DATE_ADD(created_at, INTERVAL -7200 SECOND)" in sql(
        shift_seconds(col, -7200), mysql.dialect()
    )
    assert "(created_at + INTERVAL '3600 seconds')" in sql(
        shift_seconds(col, 3600), postgresql.dialect()
    )
    assert "DATE_ADD" not in sql(shift_seconds(col, 0), mysql.dialect())
    assert "INTERVAL" not in sql(shift_seconds(col, 0), postgresql.dialect())
    assert "HOUR(created_at)" in sql(date_part(col, "hour"), mysql.dialect())
    assert "(WEEKDAY(created_at) + 1)" in sql(date_part(col, "isodow"), mysql.dialect())
    assert "CAST(EXTRACT(HOUR FROM created_at) AS INTEGER)" in sql(
        date_part(col, "hour"), postgresql.dialect()
    )
    assert "CAST(EXTRACT(ISODOW FROM created_at) AS INTEGER)" in sql(
        date_part(col, "isodow"), postgresql.dialect()
    )


def test_invalid_arguments_raise_value_error() -> None:
    with pytest.raises(ValueError):
        date_bucket(col, "decade")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        date_part(col, "minute")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        shift_seconds(col, "3600")  # type: ignore[arg-type]


def test_unknown_dialect_fails_loudly() -> None:
    from sqlalchemy.dialects import sqlite

    with pytest.raises(CompileError, match="date_bucket"):
        sql(date_bucket(col, "day"), sqlite.dialect())
    with pytest.raises(CompileError, match="date_part"):
        sql(date_part(col, "hour"), sqlite.dialect())
    with pytest.raises(CompileError, match="shift_seconds"):
        sql(shift_seconds(col, 5), sqlite.dialect())


def test_tz_segments_berlin_2024() -> None:
    segs = tz_segments("Europe/Berlin", datetime(2024, 3, 1), datetime(2024, 11, 1), margin_days=0)
    assert [(s.until_utc, s.offset_seconds) for s in segs] == [
        (datetime(2024, 3, 31, 1, 0), 3600),
        (datetime(2024, 10, 27, 1, 0), 7200),
        (None, 3600),
    ]
    assert tz_segments("UTC", datetime(2024, 1, 1), datetime(2025, 1, 1)) == (TzSegment(None, 0),)


def test_tz_segments_default_margin_covers_neighbouring_years() -> None:
    segs = tz_segments("Europe/Berlin", datetime(2024, 6, 1), datetime(2024, 7, 1))
    untils = [s.until_utc for s in segs[:-1]]
    assert datetime(2023, 10, 29, 1, 0) in untils and datetime(2025, 3, 30, 1, 0) in untils
    assert untils == sorted(untils)  # type: ignore[type-var]
    assert {s.offset_seconds for s in segs} == {3600, 7200}


def test_to_local_single_segment_is_plain_shift() -> None:
    assert "CASE" not in sql(to_local(col, (TzSegment(None, 3600),)), mysql.dialect())
    segs = tz_segments("Europe/Berlin", datetime(2024, 3, 1), datetime(2024, 11, 1))
    assert "CASE" in sql(to_local(col, segs), mysql.dialect())


# --- execution on both dialects -------------------------------------------------


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


_BERLIN = tz_segments("Europe/Berlin", datetime(2024, 1, 1), datetime(2025, 1, 1))

_DAY_SAMPLES = [
    (datetime(2024, 3, 30, 23, 30), date(2024, 3, 31)),  # 00:30 CET
    (datetime(2024, 3, 31, 21, 59), date(2024, 3, 31)),  # 23:59 CEST
    (datetime(2024, 3, 31, 22, 0), date(2024, 4, 1)),
    (datetime(2024, 10, 26, 22, 30), date(2024, 10, 27)),  # 00:30 CEST
    (datetime(2024, 10, 27, 22, 59), date(2024, 10, 27)),  # 23:59 CET
]


async def _session_for(request, url_fixture):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_to_async_url(request.getfixturevalue(url_fixture)))
    session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()
    await session.connection()  # initialise the dialect (is_mariadb) before inspecting it
    return engine, session


def _rows(samples: list[datetime]):  # type: ignore[no-untyped-def]
    return union_all(
        *(select(cast(literal(dt, DateTime()), DateTime()).label("x")) for dt in samples)
    ).subquery()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_local_day_buckets_across_dst_both_dialects(url_fixture, request) -> None:  # type: ignore[no-untyped-def]
    engine, session = await _session_for(request, url_fixture)
    try:
        sub = _rows([dt for dt, _ in _DAY_SAMPLES])
        local = to_local(sub.c.x, _BERLIN)
        rows = (await session.execute(select(sub.c.x, date_bucket(local, "day")))).all()
        got = {r[0].replace(tzinfo=None): r[1] for r in rows}
        assert got == {dt: d for dt, d in _DAY_SAMPLES}
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_week_month_quarter_year_buckets_both_dialects(url_fixture, request) -> None:  # type: ignore[no-untyped-def]
    samples = {
        # 2024-03-31 21:59 UTC is Sunday 23:59 local -> week of Monday 2024-03-25
        datetime(2024, 3, 31, 21, 59): (
            date(2024, 3, 25),
            date(2024, 3, 1),
            date(2024, 1, 1),
            date(2024, 1, 1),
        ),
        # 2024-03-31 22:00 UTC is Monday 2024-04-01 00:00 local
        datetime(2024, 3, 31, 22, 0): (
            date(2024, 4, 1),
            date(2024, 4, 1),
            date(2024, 4, 1),
            date(2024, 1, 1),
        ),
        # 2024-12-31 23:30 UTC is 2025-01-01 00:30 local (Wednesday)
        datetime(2024, 12, 31, 23, 30): (
            date(2024, 12, 30),
            date(2025, 1, 1),
            date(2025, 1, 1),
            date(2025, 1, 1),
        ),
        datetime(2024, 8, 15, 12, 0): (
            date(2024, 8, 12),
            date(2024, 8, 1),
            date(2024, 7, 1),
            date(2024, 1, 1),
        ),
    }
    engine, session = await _session_for(request, url_fixture)
    try:
        sub = _rows(list(samples))
        local = to_local(sub.c.x, _BERLIN)
        rows = (
            await session.execute(
                select(
                    sub.c.x,
                    date_bucket(local, "week"),
                    date_bucket(local, "month"),
                    date_bucket(local, "quarter"),
                    date_bucket(local, "year"),
                )
            )
        ).all()
        got = {r[0].replace(tzinfo=None): tuple(r[1:]) for r in rows}
        assert got == samples
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_date_part_hour_and_isodow_across_dst_both_dialects(url_fixture, request) -> None:  # type: ignore[no-untyped-def]
    samples = {
        datetime(2024, 10, 27, 0, 30): (2, 7),  # 02:30 CEST (before the switch at 01:00 UTC)
        datetime(2024, 10, 27, 1, 30): (2, 7),  # 02:30 CET (after it)
        datetime(2024, 3, 31, 21, 59): (23, 7),  # Sunday 23:59 local
        datetime(2024, 3, 31, 22, 0): (0, 1),  # Monday 00:00 local
    }
    engine, session = await _session_for(request, url_fixture)
    try:
        sub = _rows(list(samples))
        local = to_local(sub.c.x, _BERLIN)
        rows = (
            await session.execute(
                select(sub.c.x, date_part(local, "hour"), date_part(local, "isodow"))
            )
        ).all()
        assert {r[0].replace(tzinfo=None): (r[1], r[2]) for r in rows} == samples
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_group_by_multi_segment_local_day_and_totals(url_fixture, request) -> None:  # type: ignore[no-untyped-def]
    """R-GROUPBY: one expression object reused in SELECT and GROUP BY (PostgreSQL!)."""
    stamps = [dt for dt, _ in _DAY_SAMPLES] + [datetime(2024, 3, 31, 21, 0)]
    engine, session = await _session_for(request, url_fixture)
    try:
        sub = _rows(stamps)
        bucket = date_bucket(to_local(sub.c.x, _BERLIN), "day")
        rows = (
            await session.execute(
                select(bucket.label("d0"), func.count().label("n"))
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()
        assert [(r[0], r[1]) for r in rows] == [
            (date(2024, 3, 31), 3),
            (date(2024, 4, 1), 1),
            (date(2024, 10, 27), 2),
        ]
        total = (await session.execute(select(func.count()).select_from(sub))).scalar_one()
        assert total == 6
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_statement_timeout(url_fixture, request) -> None:  # type: ignore[no-untyped-def]
    engine, session = await _session_for(request, url_fixture)
    try:
        dialect = session.get_bind().dialect
        mysql_like = dialect.name in ("mysql", "mariadb")
        # CPU-bound on MariaDB (SLEEP is ended early without an error there)
        slow = (
            (
                "SELECT COUNT(*) FROM information_schema.columns a, "
                "information_schema.columns b, information_schema.columns c"
            )
            if mysql_like
            else "SELECT pg_sleep(30)"
        )
        started = time.monotonic()
        with pytest.raises(ReportQueryTimeout) as info:
            async with statement_timeout(session, 1):
                await session.execute(text(slow))
        assert time.monotonic() - started < 10
        assert is_timeout_error(info.value.__cause__)  # type: ignore[arg-type]
        if not mysql_like:
            await session.rollback()  # the aborted transaction owns the SET LOCAL
        assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        if mysql_like:
            var = "max_statement_time" if dialect.is_mariadb else "max_execution_time"
            value = (await session.execute(text(f"SELECT @@{var}"))).scalar_one()
            assert float(value) == 0
        else:
            assert (await session.execute(text("SHOW statement_timeout"))).scalar_one() == "0"
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_reset_failure_invalidates_connection(cancel, request) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    engine, session = await _session_for(request, "mariadb_znuny_url")
    try:
        conn = await session.connection()
        fairy = await conn.get_raw_connection()
        real_execute = session.execute

        async def failing(stmt, *a, **kw):  # type: ignore[no-untyped-def]
            if "= 0" in str(stmt):
                raise asyncio.CancelledError() if cancel else RuntimeError("reset boom")
            return await real_execute(stmt, *a, **kw)

        session.execute = failing  # type: ignore[method-assign]
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                async with statement_timeout(session, 5):
                    pass
        else:
            async with statement_timeout(session, 5):
                pass
        session.execute = real_execute  # type: ignore[method-assign]
        assert fairy.is_valid is False
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_statement_timeout_restores_setting_on_success_and_other_errors(  # type: ignore[no-untyped-def]
    url_fixture, request
) -> None:
    engine, session = await _session_for(request, url_fixture)
    try:
        dialect = session.get_bind().dialect
        mysql_like = dialect.name in ("mysql", "mariadb")
        var = (
            ("max_statement_time" if dialect.is_mariadb else "max_execution_time")
            if mysql_like
            else None
        )

        async def current() -> float:
            if var is not None:
                return float((await session.execute(text(f"SELECT @@{var}"))).scalar_one())
            raw = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
            return 0.0 if raw == "0" else 1.0

        async with statement_timeout(session, 5):
            assert await current() > 0
            assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
        if not mysql_like:
            await session.commit()  # SET LOCAL ends with the transaction
        assert await current() == 0

        with pytest.raises(ZeroDivisionError):
            async with statement_timeout(session, 5):
                raise ZeroDivisionError
        if not mysql_like:
            await session.rollback()
        assert await current() == 0
    finally:
        await session.close()
        await engine.dispose()


def test_is_timeout_error_codes() -> None:
    class E(Exception):
        pass

    assert is_timeout_error(E(1969, "Query execution was interrupted"))
    assert is_timeout_error(E(3024, "maximum statement execution time exceeded"))
    assert not is_timeout_error(E(1064, "syntax"))
    pg = E("canceling statement due to statement timeout")
    pg.sqlstate = "57014"  # type: ignore[attr-defined]
    wrapper = RuntimeError("wrapped")
    wrapper.__cause__ = pg
    assert is_timeout_error(wrapper)
    assert not is_timeout_error(ValueError("x"))


async def test_statement_timeout_rejects_unsupported_dialect_and_zero() -> None:
    class _Dialect:
        name = "sqlite"

    class _Session:
        async def connection(self) -> None:
            return None

        def get_bind(self):  # type: ignore[no-untyped-def]
            class B:
                dialect = _Dialect()

            return B()

    with pytest.raises(CompileError, match="statement_timeout"):
        async with statement_timeout(_Session(), 1):  # type: ignore[arg-type]
            pass
    with pytest.raises(ValueError):
        async with statement_timeout(_Session(), 0):  # type: ignore[arg-type]
            pass
