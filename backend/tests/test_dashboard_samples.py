"""Counter samples (D7): one row per user, counter and UTC day; delta and sparkline (both dialects).

Users 49790-49799 (T5 sub-band, no Znuny row needed: the sample table has no FK). The world of
``_dashboard_helpers`` is seeded and removed per test; ``cleanup_world`` also deletes every
sample row of the band.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any

import pytest

from tests._dashboard_helpers import Env, open_env
from tiqora.dashboard.samples import purge_old_samples, record_and_history

pytestmark = pytest.mark.db

U = 49790
U2 = 49791
T = date(2026, 10, 10)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        yield e


async def record(
    env: Env, *, value: int, today: date, user_id: int = U, key: str = "preset:mine_open"
) -> tuple[int | None, list[int]]:
    return await env.run(  # type: ignore[no-any-return]
        record_and_history, user_id=user_id, counter_key=key, value=value, today=today
    )


async def test_first_day_has_no_delta_and_one_point(env: Env) -> None:
    assert await record(env, value=5, today=T) == (None, [5])
    assert await env.sample_rows(U) == [("preset:mine_open", "2026-10-10", 5)]


async def test_delta_is_against_the_newest_earlier_day_within_seven_days(env: Env) -> None:
    for d, v in ((date(2026, 10, 3), 4), (date(2026, 10, 8), 6)):
        await record(env, value=v, today=d)
    # 9 - 6; Oct 3 is exactly 7 days back and still in the spark
    assert await record(env, value=9, today=T) == (3, [4, 6, 9])
    # Oct 20: the newest earlier day (Oct 10) is 10 days back -> no delta
    delta, spark = await record(env, value=9, today=date(2026, 10, 20))
    assert delta is None
    assert spark == [6, 9, 9]  # 14-day window: Oct 7..20 (Oct 3 dropped from the spark)


async def test_delta_uses_a_day_exactly_seven_days_back(env: Env) -> None:
    await record(env, value=4, today=T - timedelta(days=7))
    assert await record(env, value=1, today=T) == (-3, [4, 1])
    await record(env, value=4, today=T - timedelta(days=8), key="preset:mine_new")
    assert await record(env, value=1, today=T, key="preset:mine_new") == (None, [4, 1])


async def test_spark_has_at_most_fourteen_days_oldest_first(env: Env) -> None:
    for i in range(20):
        await record(env, value=i, today=T + timedelta(days=i))
    delta, spark = await record(env, value=100, today=T + timedelta(days=20))
    assert delta == 100 - 19
    assert spark == [*range(7, 20), 100] and len(spark) == 14


async def test_same_day_is_overwritten_not_duplicated_and_old_rows_pruned(env: Env) -> None:
    await record(env, value=1, today=T)
    await record(env, value=2, today=T)
    assert await env.sample_rows(U) == [("preset:mine_open", "2026-10-10", 2)]
    await record(env, value=7, today=T, key="preset:mine_new")
    await record(env, value=8, today=T, user_id=U2)
    later = T + timedelta(days=40)
    await record(env, value=3, today=later)  # > 35 days: older rows dropped
    assert await env.sample_rows(U) == [("preset:mine_open", later.isoformat(), 3)]


async def test_old_samples_of_every_user_are_purged_once_a_day(env: Env) -> None:
    """Users who stopped visiting do not keep history beyond the retention (global purge)."""
    await record(env, value=1, today=T - timedelta(days=40), user_id=U2)  # U2 never returns
    await record(env, value=2, today=T - timedelta(days=35), user_id=U2)
    await record(env, value=3, today=T)  # U's first write of the day purges for everybody
    assert await env.sample_rows(U2) == [
        ("preset:mine_open", (T - timedelta(days=35)).isoformat(), 2)
    ]
    # once per day: a stale row that appears later today stays until tomorrow's first write
    env._sql_write(
        "INSERT INTO tiqora_dashboard_counter_sample"
        " (user_id, counter_key, sample_day, value, sampled_at)"
        " VALUES (:u, 'preset:mine_new', :d, 9, :t)",
        u=U2,
        d=T - timedelta(days=50),
        t=env.now,
    )
    await record(env, value=4, today=T, key="preset:mine_new")
    assert len(await env.sample_rows(U2)) == 2
    await record(env, value=5, today=T + timedelta(days=1))
    assert await env.sample_rows(U2) == []  # T-35 is now 36 days old, T-50 gone too
    async with env.session() as s:
        assert await purge_old_samples(s, today=T + timedelta(days=100)) >= 1
        await s.rollback()


async def test_rows_within_keep_days_survive_the_prune(env: Env) -> None:
    await record(env, value=1, today=T)
    await record(env, value=2, today=T + timedelta(days=35))
    assert [r[1] for r in await env.sample_rows(U)] == [
        T.isoformat(),
        (T + timedelta(days=35)).isoformat(),
    ]


async def test_concurrent_first_write_of_a_day_does_not_raise(env: Env) -> None:
    await record(env, value=1, today=T - timedelta(days=50))  # something to prune

    async def one(v: int) -> Any:
        return await env.run(
            record_and_history, user_id=U, counter_key="preset:mine_new", value=v, today=T
        )

    results = await asyncio.gather(*(one(v) for v in (1, 2, 3, 4)))
    assert len(results) == 4 and all(r[0] is None for r in results)
    rows = await env.sample_rows(U)
    assert len(rows) == 1 and rows[0][:2] == ("preset:mine_new", "2026-10-10")
    assert rows[0][2] in (1, 2, 3, 4)


async def test_without_persist_history_is_read_but_nothing_written(env: Env) -> None:
    await record(env, value=4, today=T - timedelta(days=1))
    before = await env.sample_rows(U)
    out = await env.run(
        record_and_history,
        user_id=U,
        counter_key="preset:mine_open",
        value=6,
        today=T,
        persist=False,
    )
    assert out == (2, [4, 6])
    assert await env.sample_rows(U) == before
