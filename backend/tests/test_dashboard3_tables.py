"""Dashboard phase-2 tables: kiosk link cascades, unique token hash, widget column defaults.

Runs on MariaDB and PostgreSQL. Seed ids stay in 61200-61209 and every row is deleted again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraDashboard,
    TiqoraDashboardKioskItem,
    TiqoraDashboardKioskToken,
)
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

LO, HI = 61200, 61209


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


async def _cleanup(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        # items go with their token or dashboard (ON DELETE CASCADE)
        await s.execute(
            delete(TiqoraDashboardKioskToken).where(
                TiqoraDashboardKioskToken.created_by.between(LO, HI)
            )
        )
        await s.execute(
            delete(TiqoraDashboard).where(TiqoraDashboard.owner_user_id.between(LO, HI))
        )
        await s.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
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


def _dash(name: str) -> TiqoraDashboard:
    now = utc_now()
    return TiqoraDashboard(
        owner_user_id=61200,
        name=name,
        kind="personal",
        sort=0,
        version=1,
        created_at=now,
        changed_at=now,
        filters="{}",
        locked=False,
    )


def _token(token_hash: str) -> TiqoraDashboardKioskToken:
    now = utc_now()
    return TiqoraDashboardKioskToken(
        token_hash=token_hash,
        token_hint="k3Qz",
        name="dash612 link",
        created_by=61200,
        rotate_seconds=60,
        show_details=False,
        expires_at=now,
        created_at=now,
    )


async def _items(s: AsyncSession, token_id: int) -> list[int]:
    rows = await s.execute(
        select(TiqoraDashboardKioskItem.dashboard_id).where(
            TiqoraDashboardKioskItem.token_id == token_id
        )
    )
    return sorted(rows.scalars().all())


async def test_kiosk_items_cascade_with_token_and_with_dashboard(
    env: async_sessionmaker[AsyncSession],
) -> None:
    async with env() as s:
        d1, d2 = _dash("dash612 a"), _dash("dash612 b")
        t = _token("a" * 64)
        s.add_all([d1, d2, t])
        await s.flush()
        s.add_all(
            [
                TiqoraDashboardKioskItem(token_id=t.id, dashboard_id=d1.id, position=0),
                TiqoraDashboardKioskItem(token_id=t.id, dashboard_id=d2.id, position=1),
            ]
        )
        await s.commit()
        assert await _items(s, t.id) == sorted([d1.id, d2.id])

        await s.execute(delete(TiqoraDashboard).where(TiqoraDashboard.id == d1.id))
        await s.commit()
        assert await _items(s, t.id) == [d2.id]  # only D2's item is left

        await s.execute(
            delete(TiqoraDashboardKioskToken).where(TiqoraDashboardKioskToken.id == t.id)
        )
        await s.commit()
        left = await s.execute(
            select(func.count())
            .select_from(TiqoraDashboardKioskItem)
            .where(TiqoraDashboardKioskItem.token_id == t.id)
        )
        assert left.scalar_one() == 0
        # the surviving dashboard is untouched by the token delete
        still = await s.execute(
            select(func.count()).select_from(TiqoraDashboard).where(TiqoraDashboard.id == d2.id)
        )
        assert still.scalar_one() == 1


async def test_token_hash_is_unique(env: async_sessionmaker[AsyncSession]) -> None:
    async with env() as s:
        s.add(_token("b" * 64))
        await s.commit()
        s.add(_token("b" * 64))
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()
        s.add(_token("c" * 64))
        await s.commit()  # a different hash is fine
        n = await s.execute(
            select(func.count())
            .select_from(TiqoraDashboardKioskToken)
            .where(TiqoraDashboardKioskToken.created_by == 61200)
        )
        assert n.scalar_one() == 2


def test_widget_columns_defaults() -> None:
    widget = TiqoraBase.metadata.tables["tiqora_dashboard_widget"]
    assert widget.c.pinned.nullable is False and widget.c.template_widget_id.type.length == 36
    assert widget.c.added_in_version.nullable is True
    assert widget.c.pinned.server_default is not None
    token = TiqoraBase.metadata.tables["tiqora_dashboard_kiosk_token"]
    assert token.c.token_hash.type.length == 64 and token.c.revoked_at.nullable is True
    assert str(token.c.rotate_seconds.server_default.arg) == "60"
