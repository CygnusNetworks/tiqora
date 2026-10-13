"""``tiqora.itsm.link_types``: Znuny catalogue, row sync and the offered lists (I6, C4).

Both dialects; the Znuny ITSM databases of ``_itsm_db`` (profiles itsm-6 and itsm-7) carry the
ITSM link types, the plain Znuny databases only Normal and ParentChild. Seed band 940600-940699
(sysconfig rows 940600-940609). The table is emptied before and after every test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select

from tests._itsm_db import ITSM_PARAMS
from tests._itsm_mode_helpers import itsm_url, make_factory, plain_url
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.link_types import (
    offered_link_types,
    sync_link_type_rows,
    ticket_ticket_link_types,
    znuny_link_types,
)
from tiqora.itsm.models import TiqoraItsmLinkType
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

SYSCONFIG_BASE = 940600
ITSM_TYPES = {
    "AlternativeTo",
    "ConnectedTo",
    "DependsOn",
    "Includes",
    "Normal",
    "ParentChild",
    "RelevantTo",
}
PLAIN_CONFIG: dict[str, Any] = {
    "LinkObject::Type###Normal": {"SourceName": "Normal", "TargetName": "Normal"},
    "LinkObject::Type###ParentChild": {"SourceName": "Parent", "TargetName": "Child"},
    "LinkObject::PossibleLink###0200": {"Object1": "Ticket", "Object2": "Ticket", "Type": "Normal"},
    "LinkObject::PossibleLink###0201": {
        "Object1": "Ticket",
        "Object2": "Ticket",
        "Type": "ParentChild",
    },
}
DIALECTS = ["mysql", "postgresql"]


def wipe_link_types(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine, tables=[TiqoraItsmLinkType.__table__])
        with engine.begin() as conn:
            conn.execute(delete(TiqoraItsmLinkType.__table__))
    finally:
        engine.dispose()


@dataclass
class Db:
    url: str
    factory: Any

    async def rows(self) -> dict[str, TiqoraItsmLinkType]:
        async with self.factory() as session:
            found = (await session.execute(select(TiqoraItsmLinkType))).scalars().all()
        return {r.name: r for r in found}

    async def sync(self) -> int:
        async with self.factory() as session:
            changed = await sync_link_type_rows(session, user_id=1)
            await session.commit()
        return changed

    async def offered(self, pair: str, mode: str) -> list[str]:
        async with self.factory() as session:
            return [o.name for o in await offered_link_types(session, pair=pair, mode=mode)]  # type: ignore[arg-type]

    def update(self, name: str, **values: Any) -> None:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    TiqoraItsmLinkType.__table__.update()
                    .where(TiqoraItsmLinkType.name == name)
                    .values(**values)
                )
        finally:
            engine.dispose()

    def insert(self, **values: Any) -> None:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                row: dict[str, Any] = {
                    "source_label": None,
                    "target_label": None,
                    "pointed": False,
                    "propagation": "none",
                    "position": 900,
                    "valid": True,
                    "create_time": "2026-10-01 08:00:00",
                    "create_by": 1,
                    "change_time": "2026-10-01 08:00:00",
                    "change_by": 1,
                    **values,
                }
                conn.execute(TiqoraItsmLinkType.__table__.insert().values(**row))
        finally:
            engine.dispose()


async def _db(url: str, request: pytest.FixtureRequest) -> AsyncIterator[Db]:
    wipe_link_types(url)
    request.addfinalizer(lambda: wipe_link_types(url))
    engine, factory = make_factory(url)
    try:
        yield Db(url, factory)
    finally:
        await engine.dispose()


@pytest.fixture(params=ITSM_PARAMS, ids=lambda p: f"{p[0]}-{p[1]}")
async def itsm(request: pytest.FixtureRequest) -> AsyncIterator[Db]:
    dialect, profile = request.param
    async for db in _db(itsm_url(request, dialect, profile), request):
        yield db


@pytest.fixture(params=DIALECTS)
async def plain(request: pytest.FixtureRequest) -> AsyncIterator[Db]:
    fixture = "mariadb_znuny_url" if request.param == "mysql" else "postgres_znuny_url"
    url = plain_url(request, fixture)

    def _sysconfig(values: dict[str, Any]) -> None:
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                set_sysconfig_rows(conn, SYSCONFIG_BASE, values)
        finally:
            engine.dispose()

    _sysconfig(PLAIN_CONFIG)
    request.addfinalizer(lambda: _sysconfig({}))
    async for db in _db(url, request):
        yield db


async def test_znuny_types_from_sysconfig_core(plain: Db) -> None:
    async with plain.factory() as session:
        types = await znuny_link_types(SysConfig(session))
    assert set(types) == {"Normal", "ParentChild"}
    assert (types["Normal"].source_name, types["Normal"].pointed) == ("Normal", False)
    assert (types["ParentChild"].target_name, types["ParentChild"].pointed) == ("Child", True)
    assert types["Normal"].possible == frozenset({("Ticket", "Ticket")})
    assert {t.propagation for t in types.values()} == {"none"}


async def test_znuny_types_from_sysconfig_itsm(itsm: Db) -> None:
    async with itsm.factory() as session:
        types = await znuny_link_types(SysConfig(session))
    assert set(types) == ITSM_TYPES
    depends = types["DependsOn"]
    assert (depends.source_name, depends.target_name) == ("Depends on", "Required for")
    assert depends.pointed and depends.propagation == "both"  # IncidentLinkTypeDirection
    assert depends.allows("Ticket", "ITSMConfigItem") and depends.allows("ITSMConfigItem", "Ticket")
    assert not depends.allows("Ticket", "Ticket")
    assert not types["ConnectedTo"].allows("Ticket", "ITSMConfigItem")  # not a ticket link type
    assert types["ConnectedTo"].allows("ITSMConfigItem", "ITSMConfigItem")
    assert types["Normal"].allows("Ticket", "Ticket") and not types["Normal"].pointed
    assert {n for n, t in types.items() if t.propagation != "none"} == {"DependsOn"}


async def test_fallback_when_sysconfig_has_no_link_type(plain: Db) -> None:
    engine = create_engine(plain.url)
    try:
        with engine.begin() as conn:
            set_sysconfig_rows(conn, SYSCONFIG_BASE, {})
    finally:
        engine.dispose()
    async with plain.factory() as session:
        types = await znuny_link_types(SysConfig(session))
    assert set(types) == {"Normal", "ParentChild"}
    assert await plain.offered("ticket_ticket", "znuny_readthrough") == ["Normal", "ParentChild"]


async def test_sync_inserts_missing_and_keeps_overrides(itsm: Db) -> None:
    changed = await itsm.sync()
    rows = await itsm.rows()
    assert changed == 7 and set(rows) == ITSM_TYPES
    assert {r.origin for r in rows.values()} == {
        "znuny"
    }  # Znuny's DependsOn etc. win over defaults
    normal, depends = rows["Normal"], rows["DependsOn"]
    assert normal.offer_ticket_ticket and not normal.offer_ticket_ci
    assert (depends.offer_ticket_ticket, depends.offer_ticket_ci) == (False, True)
    assert depends.pointed and depends.propagation == "both"
    assert not rows["ConnectedTo"].offer_ticket_ci and rows["ConnectedTo"].offer_ci_ci
    assert (rows["Normal"].position, rows["ParentChild"].position) == (1, 2)

    # admin overrides survive; drifting pointed/propagation follow SysConfig again
    itsm.update("Normal", source_label="Standard", valid=False, offer_ticket_ticket=False)
    itsm.update("DependsOn", pointed=False, propagation="none", offer_ci_ci=False)
    assert await itsm.sync() == 1
    rows = await itsm.rows()
    assert (rows["Normal"].source_label, rows["Normal"].valid) == ("Standard", False)
    assert rows["Normal"].offer_ticket_ticket is False
    assert rows["DependsOn"].pointed and rows["DependsOn"].propagation == "both"
    assert rows["DependsOn"].offer_ci_ci is False  # offer flags are the admin's
    assert await itsm.sync() == 0  # idempotent


async def test_sync_seeds_tiqora_defaults_where_znuny_has_none(plain: Db) -> None:
    assert await plain.sync() == 7
    rows = await plain.rows()
    assert {n for n, r in rows.items() if r.origin == "znuny"} == {"Normal", "ParentChild"}
    assert {n for n, r in rows.items() if r.origin == "tiqora"} == {
        "DependsOn",
        "Includes",
        "ConnectedTo",
        "AlternativeTo",
        "RelevantTo",
    }
    assert rows["DependsOn"].propagation == "both"  # the Tiqora default, not Znuny's
    assert rows["ParentChild"].pointed and rows["Normal"].offer_ticket_ticket


async def test_sync_never_deletes(itsm: Db) -> None:
    itsm.insert(name="Retired", origin="znuny", offer_ticket_ticket=True)
    itsm.insert(name="OnlyHere", origin="tiqora")
    await itsm.sync()
    rows = await itsm.rows()
    assert {"Retired", "OnlyHere"} <= set(rows) and set(rows) >= ITSM_TYPES
    assert rows["Retired"].offer_ticket_ticket is True  # not touched
    # a retired type is no longer storable by Znuny, so it is not offered either
    assert "Retired" not in await itsm.offered("ticket_ticket", "znuny_readthrough")


async def test_offered_ticket_ticket_only_storable_types(itsm: Db) -> None:
    await itsm.sync()
    itsm.insert(
        name="Custom",
        origin="tiqora",
        offer_ticket_ticket=True,  # even a (forced) flag cannot make Znuny store it
        offer_ticket_ci=True,
        offer_ci_ci=True,
        offer_ci_service=False,
    )
    for mode in ("znuny_readthrough", "tiqora", "off"):
        assert await itsm.offered("ticket_ticket", mode) == ["Normal", "ParentChild"]
    # a hidden or switched-off Znuny type disappears, a relabel does not
    itsm.update("ParentChild", valid=False)
    itsm.update("Normal", source_label="Standard")
    assert await itsm.offered("ticket_ticket", "znuny_readthrough") == ["Normal"]
    async with itsm.factory() as session:
        (normal,) = await offered_link_types(session, pair="ticket_ticket", mode="off")
        # the mode-free wrapper link create uses gives the same list (final review M5)
        assert await ticket_ticket_link_types(session) == [normal]
    assert (normal.source_label, normal.source_name, normal.target_name) == (
        "Standard",
        "Normal",
        "Normal",
    )


async def test_offered_ticket_ci_mode_a_uses_possible_links(itsm: Db) -> None:
    # F3: no row exists yet, Znuny's PossibleLink alone decides
    assert await itsm.offered("ticket_ci", "znuny_readthrough") == [
        "AlternativeTo",
        "DependsOn",
        "RelevantTo",
    ]
    assert await itsm.offered("ci_ci", "znuny_readthrough") == [
        "AlternativeTo",
        "ConnectedTo",
        "DependsOn",
        "Includes",
        "RelevantTo",
    ]
    assert await itsm.offered("ci_service", "znuny_readthrough") == [
        "AlternativeTo",
        "DependsOn",
        "RelevantTo",
    ]
    await itsm.sync()
    itsm.update("RelevantTo", offer_ticket_ci=False)  # a row can hide
    itsm.update("DependsOn", offer_ticket_ci=True)  # ... but never widen beyond PossibleLink
    itsm.update("ConnectedTo", offer_ticket_ci=True)
    assert await itsm.offered("ticket_ci", "znuny_readthrough") == ["AlternativeTo", "DependsOn"]
    assert await itsm.offered("ticket_ci", "off") == []


async def test_offered_mode_b_uses_the_table_flags(itsm: Db) -> None:
    await itsm.sync()
    itsm.insert(name="Custom", origin="tiqora", offer_ticket_ci=True, offer_ci_ci=True)
    itsm.insert(name="Hidden", origin="tiqora", offer_ticket_ci=True, valid=False)
    itsm.update("RelevantTo", offer_ticket_ci=False)
    assert await itsm.offered("ticket_ci", "tiqora") == [
        "AlternativeTo",
        "DependsOn",
        "Custom",
    ]
    assert "Custom" in await itsm.offered("ci_ci", "tiqora")
    assert await itsm.offered("ci_service", "tiqora") == [
        "AlternativeTo",
        "DependsOn",
        "RelevantTo",
    ]


async def test_mode_initialisation_syncs_the_rows_once(itsm: Db) -> None:
    from tiqora.itsm.mode import ensure_itsm_mode_initialised

    assert await itsm.rows() == {}
    await ensure_itsm_mode_initialised(itsm.factory)
    assert set(await itsm.rows()) == ITSM_TYPES
    itsm.update("Normal", valid=False)
    await ensure_itsm_mode_initialised(itsm.factory)  # already stored: no second sync
    assert (await itsm.rows())["Normal"].valid is False
