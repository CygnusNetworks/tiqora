"""Report source ``config_items`` on the shared CMDB world (both dialects, band 972xxx names).

Classes ``ist972srv`` (GROUP_RW) and ``ist972net`` (GROUP_RO). User A reads both, user B only
``ist972net`` (``ro`` on GROUP_RO). ``_cmdb_rows_reset`` empties the CMDB rows before every
test, so the world comes from a function-scoped fixture that runs after it (F8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RO,
    GROUP_RW,
    USER_A,
    USER_B,
    CmdbWorld,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.gate import reset_mode_cache
from tiqora.cmdb.service import CiService, system_access
from tiqora.jobs.service import utc_now
from tiqora.plugins.report_points import source_tables
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import RelativeTimeRange, ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.person_dimensions import NON_PERSON_DIMENSIONS, PERSON_DIMENSIONS
from tiqora.reporting.schemas import ReportResultOut
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sources.config_items import config_items_source

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

DB, WEB, CORE = "srv-db-01.example.org", "srv-web-01.example.org", "sw-core-01.example.org"
MEASURES = ("config_items", "cis_in_incident", "productive_cis")


@dataclass
class World:
    db: CmdbWorld
    srv: int
    net: int
    ci_ids: dict[str, int]


@pytest.fixture
async def world(cmdb_db: CmdbWorld) -> Any:
    set_mode(cmdb_db, "tiqora")
    reset_mode_cache()
    async with cmdb_db.factory() as session:
        srv = await make_class(session, key="ist972srv", group_id=GROUP_RW)
        net = await make_class(session, key="ist972net", group_id=GROUP_RO)
        ids = {
            DB: await make_ci(session, srv, DB, deployment="production", incident="operational"),
            WEB: await make_ci(session, srv, WEB, deployment="production", incident="incident"),
            CORE: await make_ci(session, net, CORE, deployment="planned", incident="operational"),
        }
    yield World(cmdb_db, srv, net, ids)
    reset_mode_cache()


async def _run(
    world: World,
    user: int,
    *,
    dims: tuple[str, ...] = (),
    measures: tuple[str, ...] = MEASURES,
    filter_: dict[str, Any] | None = None,
    time_field: str | None = None,
    time_range: RelativeTimeRange | None = None,
) -> ReportResultOut:
    payload: dict[str, Any] = {
        "source": "config_items",
        "dimensions": [{"key": k} for k in dims],
        "measures": [{"key": k} for k in measures],
    }
    if filter_ is not None:
        payload["filter"] = filter_
    if time_field is not None:
        payload["time_field"] = time_field
    if time_range is not None:
        payload["time_range"] = time_range.model_dump()
    async with world.db.factory() as session:
        return await run_report(
            session,
            defn=ReportDefinition.model_validate(payload),
            params=RunParams(),
            user_id=user,
            report_key=None,
            reporting=ReportingSettings(),
            use_cache=False,
        )


def _labelled(res: ReportResultOut, ndims: int) -> dict[tuple[str, ...], list[Any]]:
    """Rows keyed by the dimension labels, measure cells as the value."""
    keys = [c.key for c in res.columns[:ndims]]
    out: dict[tuple[str, ...], list[Any]] = {}
    for row in res.rows:
        label = tuple(res.labels[k][str(v)] for k, v in zip(keys, row[:ndims], strict=True))
        out[label] = row[ndims:]
    return out


async def test_counts_by_class_and_state(world: World) -> None:
    res = await _run(world, USER_A, dims=("class", "deployment_state"))
    assert _labelled(res, 2) == {
        ("ist972srv", "Production"): [2, 1, 2],
        ("ist972net", "Planned"): [1, 0, 0],
    }


async def test_unreadable_class_is_invisible(world: World) -> None:
    res = await _run(world, USER_B, dims=("class", "deployment_state"))
    assert _labelled(res, 2) == {("ist972net", "Planned"): [1, 0, 0]}
    assert "ist972srv" not in {v for col in res.labels.values() for v in col.values()}
    total = await _run(world, USER_B)
    assert total.rows == [[1, 0, 0]]


async def test_no_period_means_all_cis(world: World) -> None:
    assert (await _run(world, USER_A, measures=("config_items",))).rows == [[3]]
    assert (await _run(world, USER_A, measures=("config_items",))).time_window is None

    old = utc_now() - timedelta(days=62)
    world.db.sql(
        "UPDATE tiqora_cmdb_ci SET created_at = :t WHERE id = :i",
        t=old,
        i=world.ci_ids[CORE],
    )
    res = await _run(
        world,
        USER_A,
        measures=("config_items",),
        time_field="created",
        time_range=RelativeTimeRange(unit="day", count=30, include_current=True),
    )
    assert res.rows == [[2]]


async def test_categories_are_enums(world: World) -> None:
    res = await _run(
        world,
        USER_A,
        measures=("config_items",),
        filter_={
            "version": 1,
            "where": {"field": "incident_category", "op": "in", "value": ["incident"]},
        },
    )
    assert res.rows == [[1]]
    res = await _run(
        world,
        USER_A,
        measures=("config_items",),
        filter_={
            "version": 1,
            "where": {"field": "deployment_category", "op": "in", "value": ["preproductive"]},
        },
    )
    assert res.rows == [[1]]


async def test_versions_avg(world: World) -> None:
    async with world.db.factory() as session:
        await CiService(session).update(
            system_access(USER_A),
            world.ci_ids[DB],
            expected_lock_version=None,
            name="srv-db-02.example.org",
            enforce_permissions=False,
        )
        await session.commit()
    res = await _run(world, USER_A, dims=("class",), measures=("versions_avg",))
    avg = {k: float(v[0]) for k, v in _labelled(res, 1).items()}
    assert avg == {("ist972srv",): 1.5, ("ist972net",): 1.0}


async def test_incident_state_dimension_uses_the_derived_state(world: World) -> None:
    res = await _run(world, USER_A, dims=("incident_state",), measures=("config_items",))
    assert _labelled(res, 1) == {("Operational",): [2], ("Incident",): [1]}


def test_source_reads_only_tiqora_tables() -> None:
    tables = source_tables(config_items_source())
    assert tables
    assert all(t.startswith("tiqora_") for t in tables), sorted(tables)


def test_source_is_itsm_only_and_class_scoped() -> None:
    src = config_items_source()
    assert src.scope_kind == "cmdb_class"
    assert src.availability == "itsm_tiqora"
    assert src.default_time_field is None
    assert src.requires_facts is False
    assert src.time_fields == ("created", "changed")
    assert src.common_fields == {}
    assert src.archive_exclusion is None


def test_dimensions_are_classified_for_outward_paths() -> None:
    keys = {d.key for d in config_items_source().dimensions}
    assert keys == {
        "class",
        "deployment_state",
        "incident_state",
        "deployment_category",
        "incident_category",
        "created",
        "changed",
        "created_by",
    }
    assert "created_by" in PERSON_DIMENSIONS
    assert not keys & (PERSON_DIMENSIONS & NON_PERSON_DIMENSIONS)
    assert keys - {"created_by"} <= NON_PERSON_DIMENSIONS


def test_picker_sources_follow_the_ruling() -> None:
    src = config_items_source()
    picks = {d.key: d.options_source for d in src.dimensions}
    assert picks["class"] == "cmdb_class"
    assert picks["deployment_state"] == "cmdb_deployment_state"
    assert picks["incident_state"] == "cmdb_incident_state"
