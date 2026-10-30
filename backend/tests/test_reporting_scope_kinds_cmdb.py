"""Semantic layer on the shared CMDB world (``tests/_cmdb_helpers.py``, both dialects):
class scope in the run plan, CMDB labels, source availability per ITSM mode."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Integer, column, delete, table

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RO,
    GROUP_RW,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    _dep,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.gate import reset_mode_cache
from tiqora.db.tiqora.models import TiqoraCmdbCatalog, TiqoraCmdbCi, TiqoraSettings
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.engine import RunPlan, _recompile, plan_report
from tiqora.reporting.itsm_roles import itsm_role_dim
from tiqora.reporting.labels import resolve_labels
from tiqora.reporting.scope import available_sources, source_available
from tiqora.reporting.semantic import REPORT_SOURCES, ReportSource, active_sources
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sources._common import col, measure

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

CI = table(
    "ist972_ci", column("id", Integer), column("class_id", Integer), column("type_id", Integer)
)
#: no group grants at all
USER_NOBODY = 972060


def _class_source() -> ReportSource:
    return ReportSource(
        key="ist972_cis",
        label_key="reports.sources.ist972_cis",
        from_clause=lambda ctx: CI,
        scope_column=col(CI, "class_id"),
        scope_kind="cmdb_class",
        dimensions=(itsm_role_dim("itsm_role", col(CI, "type_id")),),
        measures=(measure("cis", "count"),),
        requires_facts=False,
        availability="itsm_tiqora",
    )


def _registry(*sources: ReportSource) -> Registry:
    reg = Registry()
    register_core(reg)
    for src in sources:
        reg.add(REPORT_SOURCES, src)
    return reg


async def _plan(
    cmdb_db: CmdbWorld,
    reg: Registry,
    source: str,
    measure_key: str,
    user: int,
    dims: tuple[str, ...] = (),
) -> Any:
    async with cmdb_db.factory() as session:
        return await plan_report(
            session,
            defn=ReportDefinition.model_validate(
                {
                    "source": source,
                    "dimensions": [{"key": k} for k in dims],
                    "measures": [{"key": measure_key}],
                }
            ),
            params=RunParams(),
            user_id=user,
            reporting=ReportingSettings(),
            registry=reg,
        )


async def test_plan_report_loads_class_scope_only_when_needed(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as session:
        ro_class = await make_class(session, key="ist972ro", group_id=GROUP_RO)
        rw_class = await make_class(session, key="ist972rw", group_id=GROUP_RW)
    reg = _registry(_class_source())

    plan_b: RunPlan = await _plan(cmdb_db, reg, "ist972_cis", "cis", USER_B)
    assert plan_b.cmdb_class_ids == frozenset({ro_class})
    assert (await _plan(cmdb_db, reg, "tickets", "tickets", USER_B)).cmdb_class_ids is None

    # USER_B has no queue at all: the class-scoped source still compiles (F7)
    assert plan_b.scope.queue_ids == frozenset()
    assert plan_b.compiled.stmt is not None
    where = str(plan_b.compiled.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert f"ist972_ci.class_id IN ({ro_class})" in where

    # totals recompile with the same class scope
    totals = _recompile(plan_b, [], None)
    assert totals.stmt is not None
    assert f"IN ({ro_class})" in str(totals.stmt.compile(compile_kwargs={"literal_binds": True}))

    # different class rights never share a cache entry
    plan_a: RunPlan = await _plan(cmdb_db, reg, "ist972_cis", "cis", USER_A)
    assert plan_a.cmdb_class_ids == frozenset({ro_class, rw_class})
    assert plan_a.cache_key != plan_b.cache_key

    plan_admin: RunPlan = await _plan(cmdb_db, reg, "ist972_cis", "cis", USER_ADMIN)
    assert plan_admin.cmdb_class_ids == frozenset({ro_class, rw_class})

    # no readable class: no query
    nobody: RunPlan = await _plan(cmdb_db, reg, "ist972_cis", "cis", USER_NOBODY)
    assert nobody.cmdb_class_ids == frozenset()
    assert nobody.compiled.stmt is None


async def test_plan_report_carries_type_roles(cmdb_db: CmdbWorld) -> None:
    """Compile, cache key and totals recompile all see the same roles."""
    async with cmdb_db.factory() as session:
        await make_class(session, key="ist972ro", group_id=GROUP_RO)
    reg = _registry(_class_source())
    plan_without: RunPlan = await _plan(
        cmdb_db, reg, "ist972_cis", "cis", USER_B, dims=("itsm_role",)
    )
    assert "ist972 Incident" not in plan_without.type_roles.values()

    cmdb_db.sql(
        "INSERT INTO ticket_type (name, valid_id, create_time, create_by, change_time, change_by)"
        " VALUES ('ist972 Incident', 1, '2026-10-01 08:00:00', 1, '2026-10-01 08:00:00', 1)"
    )
    try:
        type_id = int(
            cmdb_db.sql("SELECT id FROM ticket_type WHERE name = 'ist972 Incident'")[0][0]
        )
        async with cmdb_db.factory() as session:
            session.add(TiqoraSettings(key="itsm.incident_type", value="ist972 Incident"))
            await session.commit()

        plan: RunPlan = await _plan(cmdb_db, reg, "ist972_cis", "cis", USER_B, dims=("itsm_role",))
        assert plan.type_roles[type_id] == "incident"
        case = f"CASE ist972_ci.type_id WHEN {type_id} THEN 'incident'"
        for compiled in (plan.compiled, _recompile(plan, [0], None)):
            assert compiled.stmt is not None
            assert case in str(compiled.stmt.compile(compile_kwargs={"literal_binds": True}))

        again: RunPlan = await _plan(cmdb_db, reg, "ist972_cis", "cis", USER_B, dims=("itsm_role",))
        assert again.cache_key == plan.cache_key
        assert plan_without.cache_key != plan.cache_key
    finally:
        async with cmdb_db.factory() as session:
            await session.execute(
                delete(TiqoraSettings).where(TiqoraSettings.key == "itsm.incident_type")
            )
            await session.commit()
        cmdb_db.sql("DELETE FROM ticket_type WHERE name = 'ist972 Incident'")


async def test_cmdb_labels(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as session:
        cls = await make_class(session, key="ist972lbl", group_id=GROUP_RO)
        ci = await make_ci(session, cls, "srv-db-01.example.org")
        dep = await _dep(session, "production")
        number = await session.get(TiqoraCmdbCi, ci)
        assert number is not None
        catalog = await session.get(TiqoraCmdbCatalog, dep)
        assert catalog is not None

        unknown = 972099
        assert await resolve_labels(session, "cmdb_class", [cls, unknown]) == {
            str(cls): "ist972lbl",
            str(unknown): str(unknown),
        }
        assert await resolve_labels(session, "cmdb_catalog", [dep, unknown]) == {
            str(dep): catalog.name,
            str(unknown): str(unknown),
        }
        assert await resolve_labels(session, "cmdb_ci", [ci, unknown]) == {
            str(ci): f"{number.number} srv-db-01.example.org",
            str(unknown): str(unknown),
        }


async def test_available_sources_follow_mode(cmdb_db: CmdbWorld) -> None:
    src = _class_source()
    reg = _registry(src)
    core = next(s for s in active_sources(reg) if s.key == "tickets")
    try:
        for mode, listed in (("tiqora", True), ("znuny_readthrough", False), ("off", False)):
            set_mode(cmdb_db, mode)
            reset_mode_cache()
            async with cmdb_db.factory() as session:
                keys = [s.key for s in await available_sources(session, reg)]
                assert ("ist972_cis" in keys) is listed, mode
                assert "tickets" in keys
                assert await source_available(session, src) is listed
                assert await source_available(session, core) is True
    finally:
        reset_mode_cache()
