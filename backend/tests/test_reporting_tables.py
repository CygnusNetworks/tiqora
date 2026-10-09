"""Reporting fact tables, the state row and the reporting settings section.

Seed ids: 47500-47519 (names contain ``rep47``). The session-scoped containers
are shared, so every row this module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraReportDailySnapshot,
    TiqoraReportState,
    TiqoraReportTicketFact,
)
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.state import STATE_NAME, load_state
from tiqora.settings_sections.section import SETTINGS_SECTIONS, validate_section_schema

BAND_LO, BAND_HI = 47500, 47519
NOW = datetime(2024, 6, 1, 12, 0, 0)
_URLS: set[str] = set()

_CLEANUP = (
    ("DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi"),
    ("DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi"),
    ("DELETE FROM tiqora_report_daily_snapshot WHERE queue_id BETWEEN :lo AND :hi"),
    ("DELETE FROM tiqora_report_state"),
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), {"lo": BAND_LO, "hi": BAND_HI})
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _fact(ticket_id: int) -> TiqoraReportTicketFact:
    return TiqoraReportTicketFact(
        ticket_id=ticket_id,
        tn="2024060110000001",
        created_at=NOW,
        created_by=1,
        queue_id=BAND_LO,
        queue_id_at_create=BAND_LO,
        state_id=1,
        state_type="open",
        priority_id=3,
        owner_id=1,
        channel="email",
        accounted_time=Decimal("1.25"),
        last_event_at=NOW,
        fact_version=1,
        built_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_state_row_is_created_once(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            st = await load_state(s)
            await s.commit()
            assert st.name == STATE_NAME == "facts"
            assert st.last_history_id == 0
            assert st.backfill_status == "pending"
            assert st.updated_at.tzinfo is None
        async with factory() as s:
            again = await load_state(s)
            assert again.name == "facts"
            count = (await s.execute(select(func.count()).select_from(TiqoraReportState))).scalar()
            assert count == 1
            locked = await load_state(s, for_update=True)
            assert locked.name == "facts"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_state_race_keeps_pending_work(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Another session creates the row between our SELECT and INSERT: only the
    savepoint is rolled back, the caller's pending fact insert survives."""
    sync_url = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s, factory() as other:
            s.add(_fact(47501))  # caller's pending work
            real_execute = s.execute
            calls = {"n": 0}

            async def racing_execute(stmt, *a, **kw):  # type: ignore[no-untyped-def]
                result = await real_execute(stmt, *a, **kw)
                calls["n"] += 1
                if calls["n"] == 1:  # after our empty SELECT, a rival commits the row
                    other.add(
                        TiqoraReportState(
                            name=STATE_NAME,
                            last_history_id=7,
                            backfill_status="pending",
                            updated_at=NOW,
                        )
                    )
                    await other.commit()
                return result

            s.execute = racing_execute  # type: ignore[method-assign]
            st = await load_state(s)
            assert st.last_history_id == 7  # the rival's row, re-selected
            await s.commit()
        async with factory() as s:
            assert (await s.get(TiqoraReportTicketFact, 47501)) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_fact_and_snapshot_round_trip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as s:
            s.add(_fact(47502))
            s.add(
                TiqoraReportDailySnapshot(
                    snapshot_date=date(2024, 3, 31),
                    queue_id=47502,
                    state_type="open",
                    priority_id=3,
                    open_count=3,
                    age_0_1d=1,
                    age_1_3d=1,
                    age_3_7d=1,
                    age_7_30d=0,
                    age_30d_plus=0,
                    built_at=NOW,
                )
            )
            await s.commit()
        async with factory() as s:
            fact = await s.get(TiqoraReportTicketFact, 47502)
            assert fact is not None
            assert fact.accounted_time == Decimal("1.25")
            assert fact.update_breach_count == 0  # server default
            assert fact.archived is False and fact.queue_pingpong is False
            assert fact.first_response_breached is None
            assert fact.built_at == NOW and fact.channel == "email"
            snap = await s.get(TiqoraReportDailySnapshot, (date(2024, 3, 31), 47502, "open", 3))
            assert snap is not None
            assert (snap.open_count, snap.age_0_1d, snap.age_30d_plus) == (3, 1, 0)
    finally:
        await engine.dispose()
    _clean(sync_url)


def test_reporting_section_is_registered_and_valid() -> None:
    reg = Registry()
    register_core(reg)
    section = reg.get(SETTINGS_SECTIONS, "reporting")
    assert section is not None and section.model is ReportingSettings
    validate_section_schema(section)
    assert ReportingSettings().access == "all_agents"
    assert ReportingSettings().max_result_rows == 5_000
    with pytest.raises(ValueError):
        ReportingSettings(max_result_rows=10_001)
