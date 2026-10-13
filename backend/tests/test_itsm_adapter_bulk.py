"""Mode A reader: bulk iterators and row dataclasses for the importer (Task 10, contract C3).

Seed band 940400-940499: CI numbers 9404xxxx, names contain itsm94.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fixtures import CLASS_CATALOG, catalog_id, seed_ci, seed_ci_link
from tiqora.itsm.znuny_adapter import reader as reader_module

pytestmark = pytest.mark.db

BEHAVIOUR = Path(__file__).resolve().parent / "fixtures" / "znuny-itsm" / "behaviour"


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_iter_config_items_keyset_paging(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        before = await reader.iter_config_items(session, after_id=0, limit=10_000)
        start = max((c.id for c in before), default=0)
        seeded = [
            await seed_ci(
                session,
                profile,
                number=f"9404010{n}",
                class_name="Computer",
                name=f"itsm94 page {n}",
            )
            for n in range(7)
        ]
        pages: list[list[int]] = []
        after = start
        while True:
            page = await reader.iter_config_items(session, after_id=after, limit=3)
            if not page:
                break
            pages.append([c.id for c in page])
            after = page[-1].id
        assert [len(p) for p in pages] == [3, 3, 1]
        flat = [i for p in pages for i in p]
        assert flat == sorted(seeded) and len(set(flat)) == 7  # no duplicates, id order
        first = (await reader.iter_config_items(session, after_id=start, limit=1))[0]
        assert first.number == "94040100" and first.last_version_id is not None


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_versions_for_returns_all_versions(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        a = await seed_ci(
            session,
            profile,
            number="94040201",
            class_name="Computer",
            name="itsm94 a",
            versions=[{"name": "a1"}, {"name": "a2"}, {"name": "a3"}],
        )
        b = await seed_ci(
            session, profile, number="94040202", class_name="Network", name="itsm94 b"
        )
        got = await reader.versions_for(session, [a, b, a, 999_999_999])
        assert set(got) == {a, b}
        assert [v.name for v in got[a]] == ["a1", "a2", "a3"]  # id order, oldest first
        assert [v.id for v in got[a]] == sorted(v.id for v in got[a])
        assert [v.name for v in got[b]] == ["itsm94 b"]
        assert await reader.versions_for(session, []) == {}


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_iter_ci_links_both_directions(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        a = await seed_ci(session, profile, number="94040301", class_name="Computer", name="a")
        b = await seed_ci(session, profile, number="94040302", class_name="Computer", name="b")
        c = await seed_ci(session, profile, number="94040303", class_name="Network", name="c")
        existing = await reader.iter_ci_links(session, after=None, limit=10_000)
        await seed_ci_link(session, source_key=a, target_key=b)  # CI -> CI
        await seed_ci_link(
            session, source_key=c, target_key=900_404, target_object="Service", link_type="Includes"
        )  # CI at the source end
        await seed_ci_link(
            session, source_key=900_404, source_object="Ticket", target_key=a, state="Temporary"
        )  # CI at the target end
        await seed_ci_link(
            session,
            source_key=900_404,
            source_object="Ticket",
            target_key=900_405,
            target_object="Ticket",
            link_type="Normal",
        )  # no CI involved: never returned

        links = await reader.iter_ci_links(session, after=None, limit=10_000)
        new = links[len(existing) :] if links[: len(existing)] == existing else None
        assert new is not None, "existing links keep their position"
        got = {
            (x.source_object, x.source_key, x.target_object, x.target_key, x.link_type, x.state)
            for x in new
        }
        assert got == {
            ("ITSMConfigItem", str(a), "ITSMConfigItem", str(b), "DependsOn", "Valid"),
            ("ITSMConfigItem", str(c), "Service", "900404", "Includes", "Valid"),
            ("Ticket", "900404", "ITSMConfigItem", str(a), "DependsOn", "Temporary"),
        }
        assert all(x.created_by == 1 and x.created_at is not None for x in new)

        # keyset paging: one row at a time through the cursor visits every link exactly once
        seen = []
        cursor = None
        while True:
            page = await reader.iter_ci_links(session, after=cursor, limit=1)
            if not page:
                break
            seen.extend(page)
            cursor = page[-1].cursor
        assert seen == links


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_cip_allocate_default_matrix(dialect, profile, request) -> None:
    doc = json.loads((BEHAVIOUR / f"{profile}.json").read_text(encoding="utf-8"))
    expected = {
        (c["criticality"], c["impact"]): c["priority_id"]
        for c in doc["questions"]["1"]["expected"]["cells"]
    }
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        rows = await reader.cip_allocate(session)
        assert len(expected) == 25
        assert {(r.criticality, r.impact): r.priority_id for r in rows} == expected
        assert len(rows) == 25 and all(isinstance(r.priority_id, int) for r in rows)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_service_and_sla_itsm_columns(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        end_user = await catalog_id(session, "ITSM::Service::Type", "End User Service")
        sla_type = (
            await session.execute(
                text(
                    "SELECT id FROM general_catalog WHERE general_catalog_class = 'ITSM::SLA::Type'"
                    " ORDER BY id LIMIT 1"
                )
            )
        ).scalar_one()
        sla_type_name = (
            await session.execute(
                text("SELECT name FROM general_catalog WHERE id = :i"), {"i": sla_type}
            )
        ).scalar_one()
        stamp = {"t": datetime(2026, 1, 1, 8, 0, 0)}  # noqa: DTZ001 - Znuny stores naive UTC
        await session.execute(
            text(
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by, type_id, criticality) VALUES (940401, 'itsm94 svc', 1, :t, 1, :t, 1,"
                " :ty, '3 normal'), (940402, 'itsm94 plain', 1, :t, 1, :t, 1, NULL, NULL)"
            ),
            {**stamp, "ty": end_user},
        )
        await session.execute(
            text(
                "INSERT INTO sla (id, name, first_response_time, update_time, solution_time,"
                " valid_id, create_time, create_by, change_time, change_by, type_id,"
                " min_time_bet_incidents) VALUES (940401, 'itsm94 sla', 10, 20, 30, 1, :t, 1,"
                " :t, 1, :ty, 120), (940402, 'itsm94 bare', 10, 20, 30, 1, :t, 1, :t, 1,"
                " NULL, NULL)"
            ),
            {**stamp, "ty": sla_type},
        )
        services = await reader.service_itsm_columns(session)
        slas = await reader.sla_itsm_columns(session)
        svc = services[940401]
        assert (svc.service_id, svc.type_id, svc.type_name, svc.criticality) == (
            940401,
            end_user,
            "End User Service",
            "3 normal",
        )
        plain = services[940402]
        assert (plain.type_id, plain.type_name, plain.criticality) == (None, None, None)
        sla = slas[940401]
        assert (sla.sla_id, sla.type_id, sla.type_name, sla.min_time_bet_incidents) == (
            940401,
            sla_type,
            sla_type_name,
            120,
        )
        bare = slas[940402]
        assert (bare.type_id, bare.type_name, bare.min_time_bet_incidents) == (None, None, None)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_general_catalog_and_definitions_are_complete(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        rows = await reader.iter_general_catalog(session)
        by_class = {(r.catalog_class, r.name): r for r in rows}
        incident = by_class[("ITSM::Core::IncidentState", "Incident")]
        assert incident.preferences["Functionality"] == "incident" and incident.valid_id == 1
        computer = by_class[(CLASS_CATALOG, "Computer")]
        assert computer.preferences["Permission"].isdigit()
        assert [r.id for r in rows] == sorted(r.id for r in rows)
        # invalid rows are part of the bulk read
        await session.execute(
            text("UPDATE general_catalog SET valid_id = 2 WHERE id = :i"), {"i": incident.id}
        )
        again = {r.id: r for r in await reader.iter_general_catalog(session)}
        assert again[incident.id].valid_id == 2

        definitions = await reader.iter_class_definitions(session)
        assert {d.class_id for d in definitions} >= {computer.id}
        keys = [(d.class_id, d.version) for d in definitions]
        assert keys == sorted(keys)
        assert all(d.fmt == "yaml" and d.attributes() for d in definitions)


def test_row_dataclass_field_names_are_the_contract() -> None:
    def names(cls: type) -> list[str]:
        return [f.name for f in dataclasses.fields(cls)]

    assert names(reader_module.GeneralCatalogRow) == [
        "id",
        "catalog_class",
        "name",
        "valid_id",
        "comments",
        "preferences",
    ]
    assert names(reader_module.DefinitionRow) == [
        "id",
        "class_id",
        "version",
        "fmt",
        "text",
        "created_at",
        "created_by",
    ]
    assert names(reader_module.CiRow) == [
        "id",
        "number",
        "class_id",
        "last_version_id",
        "cur_depl_state_id",
        "cur_inci_state_id",
        "created_at",
        "created_by",
        "changed_at",
        "changed_by",
    ]
    assert names(reader_module.VersionRow) == [
        "id",
        "ci_id",
        "name",
        "definition_id",
        "depl_state_id",
        "inci_state_id",
        "created_at",
        "created_by",
    ]
    # LinkRow: the eight contract fields first, then the additive paging cursor
    assert names(reader_module.LinkRow) == [
        "source_object",
        "source_key",
        "target_object",
        "target_key",
        "link_type",
        "state",
        "created_at",
        "created_by",
        "cursor",
    ]
    assert names(reader_module.CipRow) == ["criticality", "impact", "priority_id"]
    assert names(reader_module.ServiceItsmRow) == [
        "service_id",
        "type_id",
        "type_name",
        "criticality",
    ]
    assert names(reader_module.SlaItsmRow) == [
        "sla_id",
        "type_id",
        "type_name",
        "min_time_bet_incidents",
    ]
    for cls in (
        reader_module.GeneralCatalogRow,
        reader_module.DefinitionRow,
        reader_module.CiRow,
        reader_module.VersionRow,
        reader_module.LinkRow,
        reader_module.CipRow,
        reader_module.ServiceItsmRow,
        reader_module.SlaItsmRow,
    ):
        assert cls.__dataclass_params__.frozen and hasattr(cls, "__slots__")  # type: ignore[attr-defined]
