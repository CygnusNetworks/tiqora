"""Mode A reader: versions of one CI and the raw attribute hash (importer contract, Task 10).

Seed band 940400-940499: CI numbers 9404xxxx, names contain itsm94.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests._itsm_adapter_helpers import itsm_session
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fixtures import CLASS_CATALOG, catalog_id, seed_ci, xml_storage_rows
from tiqora.itsm.znuny_adapter.reader import VersionRow

pytestmark = pytest.mark.db

_DEFINITION_V2 = (
    "---\n- Key: Hostname\n  Name: Hostname\n  Searchable: 1\n  Input:\n    Type: Text\n"
    "- Key: Vendor\n  Name: Vendor\n  Input:\n    Type: Text\n"
)


async def _add_version_on_new_definition(session, ci_id: int, class_id: int, name: str) -> int:
    """A second class definition and a CI version stored under it (Znuny keeps both)."""
    await session.execute(
        text(
            "INSERT INTO configitem_definition (class_id, configitem_definition, version,"
            " create_time, create_by) VALUES (:c, :d, 2, '2026-02-01 08:00:00', 1)"
        ),
        {"c": class_id, "d": _DEFINITION_V2},
    )
    definition_id = int(
        (
            await session.execute(
                text("SELECT id FROM configitem_definition WHERE class_id = :c AND version = 2"),
                {"c": class_id},
            )
        ).scalar_one()
    )
    depl = await catalog_id(session, "ITSM::ConfigItem::DeploymentState", "Production")
    inci = await catalog_id(session, "ITSM::Core::IncidentState", "Operational")
    await session.execute(
        text(
            "INSERT INTO configitem_version (configitem_id, name, definition_id, depl_state_id,"
            " inci_state_id, create_time, create_by) VALUES (:ci, :n, :d, :depl, :inci,"
            " '2026-02-01 08:00:00', 1)"
        ),
        {"ci": ci_id, "n": name, "d": definition_id, "depl": depl, "inci": inci},
    )
    version_id = int(
        (
            await session.execute(
                text("SELECT MAX(id) FROM configitem_version WHERE configitem_id = :ci"),
                {"ci": ci_id},
            )
        ).scalar_one()
    )
    await session.execute(
        text(
            "INSERT INTO xml_storage (xml_type, xml_key, xml_content_key, xml_content_value)"
            " VALUES (:t, :k, :ck, :cv)"
        ),
        [
            {
                "t": f"ITSM::ConfigItem::{class_id}",
                "k": str(version_id),
                "ck": key,
                "cv": value,
            }
            for key, value in xml_storage_rows({"Hostname": "host94.example.org"})
        ],
    )
    await session.flush()
    return version_id


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_versions_paged_newest_first(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        ci_id = await seed_ci(
            session,
            profile,
            number="94040001",
            class_name="Computer",
            name="itsm94 v1",
            versions=[{"name": f"itsm94 v{n}"} for n in range(1, 6)],
        )
        page1, total = await reader.versions(session, ci_id, limit=2, offset=0)
        page2, _ = await reader.versions(session, ci_id, limit=2, offset=2)
        page3, _ = await reader.versions(session, ci_id, limit=2, offset=4)
        assert total == 5
        assert [v.name for v in page1] == ["itsm94 v5", "itsm94 v4"]
        assert [v.name for v in page2] == ["itsm94 v3", "itsm94 v2"]
        assert [v.name for v in page3] == ["itsm94 v1"]
        assert all(isinstance(v, VersionRow) and v.ci_id == ci_id for v in page1 + page2 + page3)
        ids = [v.id for v in page1 + page2 + page3]
        assert ids == sorted(ids, reverse=True) and len(set(ids)) == 5
        # an unknown CI has no versions
        assert await reader.versions(session, 999_999_999, limit=5, offset=0) == ([], 0)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_version_detail_uses_its_definition_version(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        ci_id = await seed_ci(
            session,
            profile,
            number="94040010",
            class_name="Computer",
            name="itsm94 old",
            versions=[{"name": "itsm94 old", "attributes": {"Vendor": "Old Vendor"}}],
        )
        class_id = await catalog_id(session, CLASS_CATALOG, "Computer")
        new_version_id = await _add_version_on_new_definition(
            session, ci_id, class_id, "itsm94 new"
        )
        versions, total = await reader.versions(session, ci_id, limit=10, offset=0)
        assert total == 2
        new, old = versions
        assert new.id == new_version_id and old.definition_id != new.definition_id
        decoded = await reader.version_attributes(session, [new, old])
        # the old version is read with definition 1, the new one with definition 2
        assert [(v.key, v.content) for v in decoded[old.id]] == [("Vendor", "Old Vendor")]
        assert [(v.key, v.content) for v in decoded[new.id]] == [("Hostname", "host94.example.org")]
        definitions = await reader.definitions(session, [old.definition_id, new.definition_id])
        assert (definitions[old.definition_id].version, definitions[new.definition_id].version) == (
            1,
            2,
        )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_version_xml_data_is_the_raw_hash(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        ci_id = await seed_ci(
            session,
            profile,
            number="94040020",
            class_name="Computer",
            name="itsm94 raw",
            versions=[
                {"attributes": {"Vendor": "First"}},
                {"attributes": {"Vendor": "Second", "Model": "M2"}},
            ],
        )
        versions, _ = await reader.versions(session, ci_id, limit=10, offset=0)
        raw = await reader.version_xml_data(session, versions)
        assert set(raw) == {v.id for v in versions}
        current = raw[versions[0].id]
        assert current[0] is None  # Znuny's @XMLHash: index 0 is unused
        version_node = current[1]["Version"][1]
        assert version_node["Vendor"][1]["Content"] == "Second"
        assert version_node["Model"][1]["Content"] == "M2"
        assert raw[versions[1].id][1]["Version"][1]["Vendor"][1]["Content"] == "First"
        assert await reader.version_xml_data(session, []) == {}
