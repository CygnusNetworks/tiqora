"""``ensure_default_link_types`` (step-0 ITSM contract) on MariaDB and PostgreSQL.

Insert-if-absent by ``name``, never updates an existing row, flushes only (the caller commits).
The table holds no other rows in the test databases; every test deletes the default names again.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._dashboard_helpers import to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.link_type_defaults import DEFAULT_LINK_TYPES, ensure_default_link_types
from tiqora.itsm.models import TiqoraItsmLinkType

pytestmark = pytest.mark.db

_NAMES = tuple(d.name for d in DEFAULT_LINK_TYPES)
_PARAMS = pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])


def _prepare(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine, tables=[TiqoraItsmLinkType.__table__])
        _cleanup_engine(engine)
    finally:
        engine.dispose()


def _cleanup_engine(engine) -> None:  # type: ignore[no-untyped-def]
    with engine.begin() as conn:
        conn.execute(
            delete(TiqoraItsmLinkType.__table__).where(TiqoraItsmLinkType.name.in_(_NAMES))
        )


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        _cleanup_engine(engine)
    finally:
        engine.dispose()


def test_defaults_shape() -> None:
    by_name = {d.name: d for d in DEFAULT_LINK_TYPES}
    assert set(by_name) == {"DependsOn", "Includes", "ConnectedTo", "AlternativeTo", "RelevantTo"}
    assert len(by_name) == len(DEFAULT_LINK_TYPES)
    assert by_name["DependsOn"].propagation == "both"
    assert {d.name for d in DEFAULT_LINK_TYPES if d.propagation != "none"} == {"DependsOn"}
    # Tiqora-origin types are never offered for ticket <-> ticket (Znuny cannot store them)
    assert not any(d.offer_ticket_ticket for d in DEFAULT_LINK_TYPES)
    # Znuny ITSM PossibleLink pairs (behaviour vector 6): ticket-CI and CI-service three types
    assert {d.name for d in DEFAULT_LINK_TYPES if d.offer_ticket_ci} == {
        "AlternativeTo",
        "DependsOn",
        "RelevantTo",
    }
    assert {d.name for d in DEFAULT_LINK_TYPES if d.offer_ci_service} == {
        "AlternativeTo",
        "DependsOn",
        "RelevantTo",
    }
    assert {d.name for d in DEFAULT_LINK_TYPES if d.offer_ci_ci} == set(_NAMES)
    assert {d.name for d in DEFAULT_LINK_TYPES if d.pointed} == {"DependsOn", "Includes"}
    assert [d.position for d in DEFAULT_LINK_TYPES] == sorted(
        {d.position for d in DEFAULT_LINK_TYPES}
    )


@_PARAMS
async def test_inserts_missing_then_nothing(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _prepare(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await ensure_default_link_types(session, user_id=7) == len(DEFAULT_LINK_TYPES)
            await session.commit()
        async with factory() as session:
            assert await ensure_default_link_types(session, user_id=7) == 0
            await session.commit()
            rows = (
                (
                    await session.execute(
                        select(TiqoraItsmLinkType).order_by(TiqoraItsmLinkType.position)
                    )
                )
                .scalars()
                .all()
            )
        assert [r.name for r in rows if r.name in _NAMES] == list(_NAMES)
        dep = next(r for r in rows if r.name == "DependsOn")
        assert (dep.origin, dep.propagation, dep.valid) == ("tiqora", "both", True)
        assert (dep.create_by, dep.change_by) == (7, 7)
        assert isinstance(dep.create_time, datetime) and dep.change_time == dep.create_time
        assert dep.source_label is None and dep.target_label is None
    finally:
        await engine.dispose()
        _cleanup(sync_url)


@_PARAMS
async def test_never_updates_existing_row(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _prepare(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            now = datetime(2026, 1, 2, 3, 4, 5)
            session.add(
                TiqoraItsmLinkType(
                    name="DependsOn",
                    origin="znuny",
                    source_label="custom",
                    pointed=False,
                    propagation="both",
                    valid=False,
                    create_time=now,
                    create_by=3,
                    change_time=now,
                    change_by=3,
                )
            )
            await session.commit()
        async with factory() as session:
            assert (
                await ensure_default_link_types(session, user_id=9) == len(DEFAULT_LINK_TYPES) - 1
            )
            await session.commit()
        async with factory() as session:
            dep = (
                await session.execute(
                    select(TiqoraItsmLinkType).where(TiqoraItsmLinkType.name == "DependsOn")
                )
            ).scalar_one()
        assert (dep.origin, dep.source_label, dep.propagation, dep.valid, dep.change_by) == (
            "znuny",
            "custom",
            "both",
            False,
            3,
        )
    finally:
        await engine.dispose()
        _cleanup(sync_url)


@_PARAMS
async def test_flushes_but_does_not_commit(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    _prepare(sync_url)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            assert await ensure_default_link_types(session, user_id=1) == len(DEFAULT_LINK_TYPES)
            # flushed: visible inside the same transaction
            inside = await session.execute(
                text("SELECT COUNT(*) FROM tiqora_itsm_link_type WHERE name = 'RelevantTo'")
            )
            assert inside.scalar_one() == 1
            await session.rollback()
        async with factory() as session:
            after = await session.execute(
                text("SELECT COUNT(*) FROM tiqora_itsm_link_type WHERE name = 'RelevantTo'")
            )
            assert after.scalar_one() == 0
    finally:
        await engine.dispose()
        _cleanup(sync_url)
