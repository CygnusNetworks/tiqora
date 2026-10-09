"""Dialect-aware SQL constructs for reporting (MariaDB/MySQL and PostgreSQL).

Time zones never use ``CONVERT_TZ``/``AT TIME ZONE`` (MariaDB's named-zone
tables are usually empty): naive UTC columns are shifted by the zone's UTC
offset, piecewise across DST transitions (``tz_segments`` + ``to_local``).

Callers must build a ``to_local`` expression ONCE per compile and reuse that
object in SELECT and GROUP BY: a rebuilt CASE gets distinct bind parameters,
which PostgreSQL rejects as a different expression (GroupingError).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import Date, DateTime, Integer, bindparam, case
from sqlalchemy import text as sa_text
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import FunctionElement

logger = structlog.get_logger(__name__)

Grain = Literal["day", "week", "month", "quarter", "year"]
DatePart = Literal["hour", "isodow"]


class shift_seconds(FunctionElement[datetime]):  # noqa: N801
    """``expr`` shifted by a constant number of seconds."""

    type = DateTime()
    inherit_cache = False
    name = "shift_seconds"

    def __init__(self, expr: ColumnElement[Any], seconds: int) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise ValueError(f"shift_seconds: seconds must be int, got {seconds!r}")
        self.seconds = seconds
        super().__init__(expr)


class date_bucket(FunctionElement[date]):  # noqa: N801
    """Start date of the ISO grain bucket containing ``expr``."""

    type = Date()
    inherit_cache = False
    name = "date_bucket"

    def __init__(self, expr: ColumnElement[Any], grain: Grain) -> None:
        if grain not in get_args(Grain):
            raise ValueError(f"date_bucket: unknown grain {grain!r}")
        self.grain = grain
        super().__init__(expr)


class date_part(FunctionElement[int]):  # noqa: N801
    """``hour`` (0-23) or ``isodow`` (1=Monday .. 7=Sunday) of ``expr``."""

    type = Integer()
    inherit_cache = False
    name = "date_part"

    def __init__(self, expr: ColumnElement[Any], part: DatePart) -> None:
        if part not in get_args(DatePart):
            raise ValueError(f"date_part: unknown part {part!r}")
        self.part = part
        super().__init__(expr)


def _inner(element: FunctionElement[Any], compiler: Any, **kw: Any) -> str:
    return str(compiler.process(element.clauses.clauses[0], **kw))


def _unsupported(construct: str, compiler: Any) -> CompileError:
    return CompileError(f"{construct}: unsupported dialect {compiler.dialect.name}")


@compiles(shift_seconds)
def _shift_default(element: shift_seconds, compiler: Any, **kw: Any) -> str:
    raise _unsupported("shift_seconds", compiler)


@compiles(shift_seconds, "mysql")
@compiles(shift_seconds, "mariadb")
def _shift_mysql(element: shift_seconds, compiler: Any, **kw: Any) -> str:
    inner = _inner(element, compiler, **kw)
    if element.seconds == 0:
        return inner
    return f"DATE_ADD({inner}, INTERVAL {element.seconds} SECOND)"


@compiles(shift_seconds, "postgresql")
def _shift_pg(element: shift_seconds, compiler: Any, **kw: Any) -> str:
    inner = _inner(element, compiler, **kw)
    if element.seconds == 0:
        return inner
    return f"({inner} + INTERVAL '{element.seconds} seconds')"


@compiles(date_bucket)
def _bucket_default(element: date_bucket, compiler: Any, **kw: Any) -> str:
    raise _unsupported("date_bucket", compiler)


@compiles(date_bucket, "mysql")
@compiles(date_bucket, "mariadb")
def _bucket_mysql(element: date_bucket, compiler: Any, **kw: Any) -> str:
    x = _inner(element, compiler, **kw)
    grain = element.grain
    if grain == "day":
        return f"DATE({x})"
    if grain == "week":
        return f"DATE(DATE_SUB({x}, INTERVAL WEEKDAY({x}) DAY))"
    if grain == "month":
        return f"DATE(DATE_SUB({x}, INTERVAL DAYOFMONTH({x}) - 1 DAY))"
    if grain == "quarter":
        return f"DATE_ADD(MAKEDATE(YEAR({x}), 1), INTERVAL QUARTER({x}) - 1 QUARTER)"
    return f"MAKEDATE(YEAR({x}), 1)"


@compiles(date_bucket, "postgresql")
def _bucket_pg(element: date_bucket, compiler: Any, **kw: Any) -> str:
    return f"CAST(date_trunc('{element.grain}', {_inner(element, compiler, **kw)}) AS DATE)"


@compiles(date_part)
def _part_default(element: date_part, compiler: Any, **kw: Any) -> str:
    raise _unsupported("date_part", compiler)


@compiles(date_part, "mysql")
@compiles(date_part, "mariadb")
def _part_mysql(element: date_part, compiler: Any, **kw: Any) -> str:
    x = _inner(element, compiler, **kw)
    return f"HOUR({x})" if element.part == "hour" else f"(WEEKDAY({x}) + 1)"


@compiles(date_part, "postgresql")
def _part_pg(element: date_part, compiler: Any, **kw: Any) -> str:
    field = "HOUR" if element.part == "hour" else "ISODOW"
    return f"CAST(EXTRACT({field} FROM {_inner(element, compiler, **kw)}) AS INTEGER)"


@dataclass(frozen=True, slots=True)
class TzSegment:
    until_utc: datetime | None  # exclusive upper bound (naive UTC); None = open end
    offset_seconds: int


def _offset_at(tz: ZoneInfo, naive_utc: datetime) -> int:
    off = naive_utc.replace(tzinfo=UTC).astimezone(tz).utcoffset()
    return int(off.total_seconds()) if off is not None else 0


def tz_segments(
    zone: str, start_utc: datetime, end_utc: datetime, *, margin_days: int = 400
) -> tuple[TzSegment, ...]:
    """UTC offsets of ``zone`` over ``[start - margin, end + margin]``.

    Transitions are found by daily probing, then bisected to the second.
    """
    tz = ZoneInfo(zone)
    first = start_utc - timedelta(days=margin_days)
    last = end_utc + timedelta(days=margin_days)
    segments: list[TzSegment] = []
    prev_t = first
    prev_off = _offset_at(tz, first)
    t = first
    while t < last:
        t = min(t + timedelta(days=1), last)
        off = _offset_at(tz, t)
        if off != prev_off:
            lo, hi = prev_t, t  # offset(lo) == prev_off, offset(hi) != prev_off
            while (hi - lo) > timedelta(seconds=1):
                mid = lo + timedelta(seconds=int((hi - lo).total_seconds()) // 2)
                if _offset_at(tz, mid) == prev_off:
                    lo = mid
                else:
                    hi = mid
            segments.append(TzSegment(hi, prev_off))
            prev_off = off
        prev_t = t
    segments.append(TzSegment(None, prev_off))
    return tuple(segments)


def to_local(expr: ColumnElement[Any], segments: Sequence[TzSegment]) -> ColumnElement[Any]:
    """Shift a naive UTC expression into local time (single object; reuse it)."""
    if not segments:
        raise ValueError("to_local: at least one segment is required")
    if len(segments) == 1:
        return shift_seconds(expr, segments[0].offset_seconds)
    whens = []
    for seg in segments[:-1]:
        if seg.until_utc is None:
            raise ValueError("to_local: only the last segment may be open-ended")
        whens.append(
            (
                expr < bindparam(None, seg.until_utc, type_=DateTime()),
                shift_seconds(expr, seg.offset_seconds),
            )
        )
    return case(*whens, else_=shift_seconds(expr, segments[-1].offset_seconds))


class ReportQueryTimeout(Exception):  # noqa: N818
    """A report statement exceeded its time budget."""


_TIMEOUT_CODES = {1969, 3024}  # MariaDB max_statement_time, MySQL max_execution_time


def is_timeout_error(exc: BaseException) -> bool:
    """MariaDB 1969, MySQL 3024, PostgreSQL SQLSTATE 57014 (also through wrappers)."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if getattr(cur, "sqlstate", None) == "57014" or getattr(cur, "pgcode", None) == "57014":
            return True
        args = getattr(cur, "args", ())
        if args and isinstance(args[0], int) and args[0] in _TIMEOUT_CODES:
            return True
        cur = getattr(cur, "orig", None) or cur.__cause__
    return False


async def _invalidate_quietly(session: AsyncSession) -> None:
    try:
        conn = await session.connection()
        await conn.invalidate()
    except BaseException as exc:  # noqa: BLE001 - best effort
        logger.warning("report_statement_timeout_invalidate_failed", error=str(exc))
        if isinstance(exc, asyncio.CancelledError):
            raise


@asynccontextmanager
async def statement_timeout(session: AsyncSession, seconds: int) -> AsyncIterator[None]:
    """Bound every statement in the block to ``seconds``.

    PostgreSQL: ``SET LOCAL`` (dies with the transaction, nothing to reset).
    MariaDB/MySQL: session variable, reset in a best-effort ``finally``; when
    the reset itself fails (connection broken by a cancelled call) the
    connection is invalidated so the pool never reuses it (a server-side abort
    leaves a clean connection; client-side cancellation is the caller's
    business). A timeout raises ``ReportQueryTimeout``. ``SET LOCAL`` ends with
    the transaction, so a commit inside the block drops the limit; MySQL's
    ``max_execution_time`` only bounds SELECT.
    """
    if seconds < 1:
        raise ValueError("statement_timeout: seconds must be >= 1")
    await session.connection()  # first connect initialises dialect.is_mariadb
    dialect = session.get_bind().dialect
    if dialect.name == "postgresql":
        set_sql = f"SET LOCAL statement_timeout = {int(seconds) * 1000}"
        reset_sql = None
    elif dialect.name in ("mysql", "mariadb"):
        if getattr(dialect, "is_mariadb", False):
            set_sql = f"SET SESSION max_statement_time = {int(seconds)}"
            reset_sql = "SET SESSION max_statement_time = 0"
        else:
            set_sql = f"SET SESSION max_execution_time = {int(seconds) * 1000}"
            reset_sql = "SET SESSION max_execution_time = 0"
    else:
        raise CompileError(f"statement_timeout: unsupported dialect {dialect.name}")
    await session.execute(sa_text(set_sql))
    timed_out: Exception | None = None
    try:
        yield
    except Exception as exc:
        if is_timeout_error(exc):
            timed_out = exc
        else:
            raise
    finally:
        if reset_sql is not None:
            try:
                await session.execute(sa_text(reset_sql))
            except asyncio.CancelledError:
                await _invalidate_quietly(session)
                raise
            except Exception as reset_exc:  # noqa: BLE001 - best effort, never mask
                logger.warning("report_statement_timeout_reset_failed", error=str(reset_exc))
                await _invalidate_quietly(session)
    if timed_out is not None:
        raise ReportQueryTimeout(f"statement exceeded {seconds}s") from timed_out
