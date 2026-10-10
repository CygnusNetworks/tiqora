"""Dashboard tables: cascades, the first-visit unique key and the counter-sample key.

Runs on MariaDB and PostgreSQL. Seed ids stay in 49700-49709 and every row is deleted again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraDashboard,
    TiqoraDashboardCounterSample,
    TiqoraDashboardGroup,
    TiqoraDashboardRole,
    TiqoraDashboardWidget,
)
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

LO, HI = 49700, 49709


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        # widgets, roles and groups go with their dashboard (ON DELETE CASCADE)
        await s.execute(
            delete(TiqoraDashboard).where(TiqoraDashboard.owner_user_id.between(LO, HI))
        )
        await s.execute(
            delete(TiqoraDashboardCounterSample).where(
                TiqoraDashboardCounterSample.user_id.between(LO, HI)
            )
        )
        await s.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    sync_engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(_async_url(sync_url))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _cleanup(maker)
    try:
        yield maker
    finally:
        await _cleanup(maker)
        await engine.dispose()


def dash(
    owner: int | None, source_key: str | None = None, name: str = "dash497 x"
) -> TiqoraDashboard:
    now = utc_now()
    return TiqoraDashboard(
        owner_user_id=owner,
        name=name,
        kind="personal",
        sort=0,
        version=1,
        created_at=now,
        changed_at=now,
        filters="{}",
        locked=False,
        source_key=source_key,
    )


async def _count(s: AsyncSession, model: type, *where: object) -> int:
    stmt = select(func.count()).select_from(model).where(*where)  # type: ignore[arg-type]
    return int((await s.execute(stmt)).scalar_one())


async def test_widgets_roles_and_groups_cascade_with_the_dashboard(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        d = dash(49700, name="dash497 a")
        s.add(d)
        await s.flush()
        s.add_all(
            [
                TiqoraDashboardWidget(
                    id="w49700-aaaa",
                    dashboard_id=d.id,
                    type_key="core.counter",
                    type_version=1,
                    settings="{}",
                    layout='{"lg":{"x":0,"y":0,"w":3,"h":2}}',
                    position=0,
                ),
                TiqoraDashboardRole(dashboard_id=d.id, role_id=49701),
                TiqoraDashboardGroup(dashboard_id=d.id, group_id=49702),
            ]
        )
        await s.commit()
        children = (TiqoraDashboardWidget, TiqoraDashboardRole, TiqoraDashboardGroup)
        for model in children:
            assert await _count(s, model, model.dashboard_id == d.id) == 1  # type: ignore[attr-defined]
        await s.execute(delete(TiqoraDashboard).where(TiqoraDashboard.id == d.id))
        await s.commit()
        for model in children:
            assert await _count(s, model, model.dashboard_id == d.id) == 0  # type: ignore[attr-defined]


async def test_unique_source_key_blocks_a_second_first_visit_copy_but_not_null_keys(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s:
        s.add(dash(49700, "core.default"))
        await s.commit()
        s.add(dash(49700, "core.default"))
        with pytest.raises(IntegrityError):
            await s.commit()  # second first-visit copy for the same owner
        await s.rollback()
        s.add_all([dash(49700), dash(49700), dash(49701, "core.default")])
        await s.commit()  # NULL keys never collide; another owner may hold the same key
        n = await _count(s, TiqoraDashboard, TiqoraDashboard.owner_user_id.in_([49700, 49701]))
    assert n == 4


async def test_counter_sample_primary_key_is_user_counter_day(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    def sample(day: date, value: int) -> TiqoraDashboardCounterSample:
        return TiqoraDashboardCounterSample(
            user_id=49700,
            counter_key="preset:mine_open",
            sample_day=day,
            value=value,
            sampled_at=utc_now(),
        )

    async with factory() as s:
        s.add(sample(date(2026, 10, 1), 3))
        await s.commit()
        s.add(sample(date(2026, 10, 1), 4))
        with pytest.raises(IntegrityError):
            await s.commit()  # same (user, counter, day)
        await s.rollback()
        s.add(sample(date(2026, 10, 2), 4))
        await s.commit()  # a different day is fine
        assert (
            await _count(
                s, TiqoraDashboardCounterSample, TiqoraDashboardCounterSample.user_id == 49700
            )
            == 2
        )


def test_models_are_registered_with_the_expected_columns() -> None:
    tables = TiqoraBase.metadata.tables
    assert {
        "tiqora_dashboard",
        "tiqora_dashboard_role",
        "tiqora_dashboard_group",
        "tiqora_dashboard_widget",
        "tiqora_dashboard_counter_sample",
    } <= set(tables)
    widget = tables["tiqora_dashboard_widget"]
    assert [c.name for c in widget.primary_key.columns] == ["id"]
    assert widget.c.id.type.length == 36
    assert widget.c.dashboard_id.nullable is False
    assert widget.c.title_override.nullable is True
    sample_pk = tables["tiqora_dashboard_counter_sample"].primary_key.columns
    assert [c.name for c in sample_pk] == ["user_id", "counter_key", "sample_day"]
