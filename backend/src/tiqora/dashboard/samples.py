"""Counter samples (D7): one row per user, counter and UTC day → delta and sparkline.

History starts when a user first opens a dashboard with that counter (no invented history).
Today's row is written with the dialect's native upsert (MariaDB/MySQL ``ON DUPLICATE KEY
UPDATE``, PostgreSQL ``ON CONFLICT DO UPDATE``), so concurrent first writes of a day neither
raise nor deadlock (a plain INSERT that hits a duplicate takes a shared lock on MariaDB, and
two of those upgrading to an UPDATE deadlock).

Retention: every row older than ``keep_days`` (all users, by the indexed ``sample_day``) is
deleted by the first sample write of a UTC day in this process (:func:`purge_old_samples`), so
users who stopped visiting do not keep history forever. The purge runs before the upsert, in the
same transaction, once per process, engine and day.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final
from weakref import WeakKeyDictionary

from sqlalchemy import delete, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraDashboardCounterSample as Sample
from tiqora.jobs.service import utc_now

SPARK_DAYS: Final = 14
DELTA_DAYS: Final = 7
KEEP_DAYS: Final = 35

#: last UTC day the global purge ran, per engine
_PURGED_ON: WeakKeyDictionary[Any, date] = WeakKeyDictionary()


async def purge_old_samples(
    session: AsyncSession, *, today: date, keep_days: int = KEEP_DAYS
) -> int:
    """Delete the samples of every user older than ``keep_days``; the caller commits."""
    result = await session.execute(
        delete(Sample).where(Sample.sample_day < today - timedelta(days=keep_days))
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _upsert(dialect: str, values: dict[str, Any]) -> Any:
    update = {"value": values["value"], "sampled_at": values["sampled_at"]}
    if dialect == "postgresql":
        stmt = pg_insert(Sample).values(**values)
        return stmt.on_conflict_do_update(
            index_elements=[Sample.user_id, Sample.counter_key, Sample.sample_day], set_=update
        )
    if dialect in ("mysql", "mariadb"):
        return mysql_insert(Sample).values(**values).on_duplicate_key_update(**update)
    raise CompileError(f"counter samples: unsupported dialect {dialect}")


async def record_and_history(
    session: AsyncSession,
    *,
    user_id: int,
    counter_key: str,
    value: int,
    today: date,
    keep_days: int = KEEP_DAYS,
    persist: bool = True,
) -> tuple[int | None, list[int]]:
    """Store today's ``value`` and return ``(delta, spark)``; the caller commits.

    ``delta`` = ``value`` minus the newest earlier day within the last 7 days, else ``None``;
    ``spark`` = the values of the last ≤ 14 days oldest first, today's value last (gap days
    are simply absent). ``persist=False`` (previews) reads the history and writes nothing.
    The first write of a UTC day also purges old rows of all users (module docstring).
    """
    rows = (
        await session.execute(
            select(Sample.sample_day, Sample.value)
            .where(
                Sample.user_id == user_id,
                Sample.counter_key == counter_key,
                Sample.sample_day >= today - timedelta(days=SPARK_DAYS - 1),
                Sample.sample_day <= today,
            )
            .order_by(Sample.sample_day)
        )
    ).all()
    earlier = [(d, int(v)) for d, v in rows if d < today]
    if persist:
        engine = session.get_bind()
        if _PURGED_ON.get(engine) != today:
            _PURGED_ON[engine] = today  # claimed first: concurrent first writes purge once
            await purge_old_samples(session, today=today, keep_days=keep_days)
        values = {
            "user_id": user_id,
            "counter_key": counter_key,
            "sample_day": today,
            "value": value,
            "sampled_at": utc_now(),
        }
        await session.execute(_upsert(engine.dialect.name, values))
    recent = [v for d, v in earlier if d >= today - timedelta(days=DELTA_DAYS)]
    delta = value - recent[-1] if recent else None
    return delta, [v for _, v in earlier] + [value]
