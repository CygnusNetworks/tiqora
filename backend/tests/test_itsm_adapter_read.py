"""Mode A reader: catalog, CI search / get, attribute decoding against the ITSM databases."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from tests._itsm_adapter_helpers import (
    count_statements,
    itsm_session,
    seed_company,
    seed_customer,
)
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fixtures import CLASS_CATALOG, catalog_id, seed_ci
from tests._itsm_mode_helpers import (
    PLAIN_URL_FIXTURES,
    itsm_url,
    make_factory,
    plain_url,
    profile_packages,
    seed_packages,
)
from tiqora.itsm.ci_provider import CiQuery, CiRef, CiSearchTooBroad, CiUnavailable
from tiqora.itsm.znuny_adapter import catalog
from tiqora.itsm.znuny_adapter import reader as reader_module
from tiqora.itsm.znuny_adapter.definitions import parse_definition
from tiqora.itsm.znuny_adapter.detect import ZnunyItsmInfo, clear_detect_cache
from tiqora.itsm.znuny_adapter.profiles import PROFILES
from tiqora.itsm.znuny_adapter.reader import (
    ATTRIBUTE_CANDIDATE_CAP,
    ZnunyItsmReader,
    attribute_key_pattern,
    get_reader,
    like_contains,
    reader_for_info,
    searchable_chains,
)
from tiqora.reporting.sql import ReportQueryTimeout

pytestmark = pytest.mark.db

# Seed band 940200-940399: CI numbers 9402xxxx, names/logins contain itsm94.
NIC_192 = {"NIC": {"Content": "eth0", "IPAddress": ["192.0.2.10", "192.0.2.11"]}}


def _ip(address: str) -> dict:
    return {"NIC": {"Content": "eth0", "IPAddress": address}}


async def _ids(session, reader, **query) -> list[str]:
    rows, _total = await reader.search(session, CiQuery(**query), class_ids=None)
    return [r.number for r in rows]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_catalog_states_with_functionality(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        prof = PROFILES[profile]
        incident = {v.name: v for v in await catalog.load_catalog(session, prof, "incident_state")}
        assert {n: v.functionality for n, v in incident.items()} == {
            "Operational": "operational",
            "Warning": "warning",
            "Incident": "incident",
        }
        deployment = {
            v.name: v for v in await catalog.load_catalog(session, prof, "deployment_state")
        }
        assert deployment["Production"].functionality == "productive"
        assert deployment["Planned"].functionality == "preproductive"
        assert deployment["Retired"].functionality == "postproductive"
        assert all(v.key.isdigit() for v in deployment.values())
        # an invalidated entry disappears from the list but stays resolvable by id
        gone = int(deployment["Review"].key)
        await session.execute(
            text("UPDATE general_catalog SET valid_id = 2 WHERE id = :i"), {"i": gone}
        )
        after = {v.name for v in await catalog.load_catalog(session, prof, "deployment_state")}
        assert "Review" not in after
        assert (await catalog.catalog_values_by_id(session, [gone]))[gone].name == "Review"


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_class_permission_groups(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        prof = PROFILES[profile]
        group = (
            await session.execute(
                text("SELECT id FROM permission_groups WHERE name = 'itsm-configitem'")
            )
        ).scalar_one()
        groups = await catalog.class_permission_groups(session, prof)
        names = await catalog.class_names(session, prof)
        assert set(names.values()) == {"Computer", "Hardware", "Location", "Network", "Software"}
        assert groups == dict.fromkeys(names, int(group))
        # a class without the preference has no group at all
        computer = next(i for i, n in names.items() if n == "Computer")
        await session.execute(
            text(
                "DELETE FROM general_catalog_preferences"
                " WHERE general_catalog_id = :i AND pref_key = 'Permission'"
            ),
            {"i": computer},
        )
        assert (await catalog.class_permission_groups(session, prof))[computer] is None


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_by_number_name_class_states(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        a = await seed_ci(
            session, profile, number="94020001", class_name="Computer", name="SRV-DB-01.example.org"
        )
        await seed_ci(
            session,
            profile,
            number="94020002",
            class_name="Computer",
            name="srv-web-01.example.org",
            depl_state="Pilot",
            inci_state="Warning",
        )
        c = await seed_ci(
            session,
            profile,
            number="94020003",
            class_name="Network",
            name="sw_core 100%",
            inci_state="Incident",
        )
        computer = await catalog_id(session, CLASS_CATALOG, "Computer")
        network = await catalog_id(session, CLASS_CATALOG, "Network")
        pilot = await catalog_id(session, "ITSM::ConfigItem::DeploymentState", "Pilot")
        warning = await catalog_id(session, "ITSM::Core::IncidentState", "Warning")

        # case-insensitive contains on the name, on both dialects
        assert await _ids(session, reader, text="srv-db") == ["94020001"]
        assert await _ids(session, reader, text="SRV-WEB") == ["94020002"]
        assert await _ids(session, reader, text="example.org") == ["94020001", "94020002"]
        # number contains
        assert await _ids(session, reader, text="9402000") == ["94020001", "94020002", "94020003"]
        assert await _ids(session, reader, text="020002") == ["94020002"]
        # LIKE metacharacters are literal
        assert await _ids(session, reader, text="sw_core") == ["94020003"]
        assert await _ids(session, reader, text="swXcore") == []
        assert await _ids(session, reader, text="100%") == ["94020003"]
        assert await _ids(session, reader, text="9402%") == []
        # filters
        assert await _ids(session, reader, text="9402", class_keys=(str(network),)) == ["94020003"]
        assert await _ids(session, reader, text="9402", deployment_state_keys=(str(pilot),)) == [
            "94020002"
        ]
        assert await _ids(session, reader, text="9402", incident_state_keys=(str(warning),)) == [
            "94020002"
        ]
        assert await _ids(session, reader, refs=(CiRef("znuny", a), CiRef("znuny", c))) == [
            "94020001",
            "94020003",
        ]
        assert await _ids(session, reader, refs=(CiRef("tiqora", a),)) == []
        assert await _ids(session, reader, class_keys=("not-a-number",)) == []

        # the permission filter comes from the caller; empty means nothing readable
        rows, total = await reader.search(session, CiQuery(text="9402"), class_ids=[computer])
        assert (total, [r.number for r in rows]) == (2, ["94020001", "94020002"])
        assert await reader.search(session, CiQuery(text="9402"), class_ids=[]) == ([], 0)
        rows, total = await reader.search(
            session, CiQuery(text="9402", class_keys=(str(network),)), class_ids=[computer]
        )
        assert (rows, total) == ([], 0)

        # paging: total is exact, limit is clamped to 100, offset skips
        rows, total = await reader.search(
            session, CiQuery(text="9402", limit=2, offset=1), class_ids=None
        )
        assert (total, [r.number for r in rows]) == (3, ["94020002", "94020003"])
        rows, _ = await reader.search(session, CiQuery(text="9402", limit=5000), class_ids=None)
        assert len(rows) == 3
        row = (await reader.search(session, CiQuery(refs=(CiRef("znuny", a),)), class_ids=None))[0][
            0
        ]
        assert (row.id, row.class_id, row.number) == (a, computer, "94020001")
        assert row.last_version_id is not None and row.cur_inci_state_id is not None


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_attribute_value_min_three_chars(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        await seed_ci(
            session,
            profile,
            number="94020011",
            class_name="Computer",
            name="itsm94 a",
            versions=[
                {
                    "attributes": {
                        **NIC_192,
                        "FQDN": "web.example.org",
                        "OperatingSystem": "Debian 13",
                    }
                }
            ],
        )
        await seed_ci(
            session,
            profile,
            number="94020012",
            class_name="Computer",
            name="itsm94 b",
            versions=[{"attributes": _ip("198.51.100.5")}],
        )
        # IP of a searchable sub attribute (NIC > IPAddress), found from the attribute value
        assert await _ids(session, reader, text="192.0.2.10") == ["94020011"]
        assert await _ids(session, reader, text="192.0.2.11") == ["94020011"]
        assert await _ids(session, reader, text="198.51.100") == ["94020012"]
        assert await _ids(session, reader, text="WEB.EXAMPLE") == ["94020011"]  # FQDN, any case
        assert await _ids(session, reader, text=".0.2.") == ["94020011"]
        # shorter than three characters: attribute values are not scanned (number/name only)
        assert await _ids(session, reader, text="19") == []
        assert await _ids(session, reader, text="  19 ") == []
        # a value of a non-searchable attribute is not found; neither is the NIC's own content
        assert await _ids(session, reader, text="Debian") == []
        assert await _ids(session, reader, text="eth0") == []
        # class scope: only the readable classes are scanned
        computer = await catalog_id(session, CLASS_CATALOG, "Computer")
        network = await catalog_id(session, CLASS_CATALOG, "Network")
        found, _ = await reader.search(session, CiQuery(text="192.0.2.10"), class_ids=[computer])
        assert [r.number for r in found] == ["94020011"]
        assert await reader.search(session, CiQuery(text="192.0.2.10"), class_ids=[network]) == (
            [],
            0,
        )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_reads_only_current_versions(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        ci = await seed_ci(
            session,
            profile,
            number="94020021",
            class_name="Computer",
            name="itsm94 moved",
            versions=[{"attributes": _ip("192.0.2.77")}, {"attributes": _ip("192.0.2.88")}],
        )
        computer = await catalog_id(session, CLASS_CATALOG, "Computer")
        old_version = int(
            (
                await session.execute(
                    text("SELECT MIN(id) FROM configitem_version WHERE configitem_id = :c"),
                    {"c": ci},
                )
            ).scalar_one()
        )
        # Znuny moves the attributes of a replaced version to the archive type
        await session.execute(
            text("UPDATE xml_storage SET xml_type = :t WHERE xml_key = :k"),
            {"t": PROFILES[profile].archive_xml_type(computer), "k": str(old_version)},
        )
        assert await _ids(session, reader, text="192.0.2.88") == ["94020021"]
        assert await _ids(session, reader, text="192.0.2.77") == []
        # ... while the archived version's attributes stay readable
        version = await reader.get_version(session, old_version)
        assert version is not None
        values = (await reader.version_attributes(session, [version]))[old_version]
        assert [v.key for v in values] == ["NIC"]
        assert values[0].subs[0].content == "192.0.2.77"


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_attribute_candidates_capped(dialect, profile, request) -> None:
    assert ATTRIBUTE_CANDIDATE_CAP == 500
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        small = ZnunyItsmReader(PROFILES[profile], candidate_cap=2)
        for i in range(4):
            await seed_ci(
                session,
                profile,
                number=f"9402003{i}",
                class_name="Computer",
                name=f"itsm94 cap {i}",
                versions=[{"attributes": _ip(f"203.0.113.{i}")}],
            )
        capped = await small.search_ex(session, CiQuery(text="203.0.113"), class_ids=None)
        assert capped.truncated is True
        assert len(capped.rows) == 2 == capped.total  # only the candidates found, flagged
        # a name/number search stays exact: the attribute cap does not touch it
        exact = await small.search_ex(session, CiQuery(text="itsm94 cap"), class_ids=None)
        assert (exact.truncated, exact.total, len(exact.rows)) == (False, 4, 4)
        exact_number = await small.search_ex(session, CiQuery(text="9402003"), class_ids=None)
        assert (exact_number.truncated, exact_number.total) == (False, 4)
        # candidates fit under the cap: exact total, not truncated
        roomy = await ZnunyItsmReader(PROFILES[profile], candidate_cap=4).search_ex(
            session, CiQuery(text="203.0.113"), class_ids=None
        )
        assert (roomy.truncated, roomy.total) == (False, 4)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_time_limit_maps_to_search_too_broad(
    dialect, profile, request, monkeypatch
) -> None:
    @asynccontextmanager
    async def timed_out(session, seconds):
        yield
        raise ReportQueryTimeout(f"statement exceeded {seconds}s")

    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        await seed_ci(
            session,
            profile,
            number="94020041",
            class_name="Computer",
            name="itsm94 slow",
            versions=[{"attributes": _ip("192.0.2.1")}],
        )
        monkeypatch.setattr(reader_module, "statement_timeout", timed_out)
        with pytest.raises(CiSearchTooBroad):
            await reader.search(session, CiQuery(text="192.0.2.1"), class_ids=None)
        # short texts never reach the scan, so they are not affected
        assert await _ids(session, reader, text="94") == ["94020041"]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_attribute_scan_leaves_session_usable(dialect, profile, request) -> None:
    """The time limit is lifted again: a following statement is not bounded or broken."""
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        await seed_ci(
            session,
            profile,
            number="94020051",
            class_name="Computer",
            name="itsm94 ok",
            versions=[{"attributes": _ip("192.0.2.9")}],
        )
        assert await _ids(session, reader, text="192.0.2.9") == ["94020051"]
        if dialect == "postgresql":
            shown = (await session.execute(text("SHOW statement_timeout"))).scalar_one()
            assert shown in ("0", "0ms")
        else:
            shown = (await session.execute(text("SELECT @@max_statement_time"))).scalar_one()
            assert float(shown) == 0.0


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_get_ci_current_version_attributes(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, engine):
        await seed_company(session, "itsm94-co", "Example Org GmbH")
        await seed_customer(session, "owner94@example.org", "Olga", "Owner", "itsm94-co")
        gc = await catalog_id(session, "ITSM::ConfigItem::YesNo", "Yes")
        attributes = {
            "Vendor": "Example Corp",
            "Model": "X1",
            "CustomerID": "itsm94-co",
            "Owner": "owner94@example.org",
            "NIC": [
                {"Content": "eth0", "IPoverDHCP": str(gc), "IPAddress": ["192.0.2.10"]},
                {"Content": "eth1", "IPAddress": ["192.0.2.11", "192.0.2.12"]},
            ],
        }
        ci_id = await seed_ci(
            session,
            profile,
            number="94020061",
            class_name="Computer",
            name="itsm94 old",
            versions=[
                {"name": "itsm94 old", "attributes": {"Vendor": "Before"}},
                {"name": "itsm94 current", "attributes": attributes},
            ],
        )
        ci = await reader.get_ci(session, ci_id)
        assert ci is not None and ci.number == "94020061"
        assert await reader.get_ci(session, 999_999_999) is None
        assert ci.last_version_id is not None
        version = await reader.get_version(session, ci.last_version_id)
        assert version is not None
        assert (version.ci_id, version.name) == (ci_id, "itsm94 current")  # current, not the first
        definition = (await reader.definitions(session, [version.definition_id]))[
            version.definition_id
        ]
        assert definition.class_id == ci.class_id and definition.version == 1
        assert [a.key for a in definition.attributes()][:3] == ["Vendor", "Model", "Description"]
        values = (await reader.version_attributes(session, [version]))[version.id]
        assert [v.key for v in values] == ["Vendor", "Model", "CustomerID", "Owner", "NIC", "NIC"]
        display = {a.key: a for a in await reader.display_attributes(session, version)}
        assert display["Vendor"].display == "Example Corp"
        assert display["CustomerID"].display == "Example Org GmbH"
        assert display["Owner"].display == '"Olga Owner" <owner94@example.org>'
        assert display["Owner"].kind == "customer"
        # the older version keeps its own attributes
        old_id = int(
            (
                await session.execute(
                    text("SELECT MIN(id) FROM configitem_version WHERE configitem_id = :c"),
                    {"c": ci_id},
                )
            ).scalar_one()
        )
        old = await reader.get_version(session, old_id)
        assert old is not None and old.id != version.id
        old_values = (await reader.version_attributes(session, [old]))[old.id]
        assert [(v.key, v.content) for v in old_values] == [("Vendor", "Before")]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_attributes_repeatable_and_nested(dialect, profile, request) -> None:
    """CountMax 10 network adapters, each with up to 20 IP addresses (Computer definition)."""
    async with itsm_session(request, dialect, profile) as (session, reader, engine):
        gc = await catalog_id(session, "ITSM::ConfigItem::YesNo", "No")
        nics = [
            {
                "Content": f"eth{n}",
                "IPoverDHCP": str(gc),
                "IPAddress": [f"192.0.2.{n * 10 + m}" for m in range(1, 4)],
            }
            for n in range(3)
        ]
        await seed_ci(
            session,
            profile,
            number="94020071",
            class_name="Computer",
            name="itsm94 nic",
            versions=[{"attributes": {"NIC": nics, "CPU": ["i1", "i2"]}}],
        )
        (row,) = (await reader.search(session, CiQuery(text="94020071"), class_ids=None))[0]
        version = await reader.get_version(session, row.last_version_id)
        with count_statements(engine) as statements:
            shown = await reader.display_attributes(session, version)
        assert [a.key for a in shown] == ["CPU", "CPU", "NIC", "NIC", "NIC"]
        nic = [a for a in shown if a.key == "NIC"]
        assert [n.display for n in nic] == ["eth0", "eth1", "eth2"]
        assert [(c.key, c.label) for c in nic[1].children][:2] == [
            ("IPoverDHCP", "IP over DHCP"),
            ("IPAddress", "IP Address"),
        ]
        assert [c.display for c in nic[2].children if c.key == "IPAddress"] == [
            "192.0.2.21",
            "192.0.2.22",
            "192.0.2.23",
        ]
        assert nic[0].children[0].kind == "catalog" and nic[0].children[0].display == "No"
        assert len(statements) <= 6  # version attributes (definition, xml) + one lookup per kind


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_unreadable_attribute_data_degrades_to_no_attributes(
    dialect, profile, request
) -> None:
    async with itsm_session(request, dialect, profile) as (session, reader, _engine):
        ci_id = await seed_ci(
            session,
            profile,
            number="94020081",
            class_name="Computer",
            name="itsm94 broken",
            versions=[{"attributes": {"Vendor": "x"}}],
        )
        ci = await reader.get_ci(session, ci_id)
        await session.execute(
            text(
                "UPDATE xml_storage SET xml_content_key = 'garbage'"
                " WHERE xml_key = :k AND xml_content_key LIKE '%Vendor%Content%'"
            ),
            {"k": str(ci.last_version_id)},
        )
        version = await reader.get_version(session, ci.last_version_id)
        assert (await reader.version_attributes(session, [version]))[version.id] == []
        assert await reader.display_attributes(session, version) == ()


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_unsupported_profile_raises(dialect, profile, request) -> None:
    url = itsm_url(request, dialect, profile)
    packages = profile_packages(profile)
    packages["ITSMConfigurationManagement"] = "6.4.1"  # a line the survey does not cover
    seed_packages(url, packages)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            with pytest.raises(CiUnavailable) as caught:
                await get_reader(session)
            assert caught.value.code == "unsupported_znuny_itsm_version"
            assert "not surveyed" in str(caught.value)
    finally:
        await engine.dispose()
        clear_detect_cache()
    # the supported versions give a reader for the matching profile
    seed_packages(url, profile_packages(profile))
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            assert (await get_reader(session)).profile.name == profile
    finally:
        await engine.dispose()
        clear_detect_cache()


@pytest.mark.parametrize("url_fixture", PLAIN_URL_FIXTURES)
async def test_missing_itsm_tables_raise(url_fixture, request) -> None:
    url = plain_url(request, url_fixture)
    engine, factory = make_factory(url)
    try:
        async with factory() as session:
            with pytest.raises(CiUnavailable) as caught:
                await get_reader(session)
            assert caught.value.code == "znuny_itsm_missing"
    finally:
        await engine.dispose()
        clear_detect_cache()


def test_reader_for_info_reasons() -> None:
    def info(**kw):
        base = dict(
            packages={},
            core_installed=True,
            cmdb_installed=True,
            change_installed=False,
            profile="itsm-6",
            unsupported_reason=None,
        )
        return ZnunyItsmInfo(**{**base, **kw})

    assert reader_for_info(info()).profile is PROFILES["itsm-6"]
    for kw, code in (
        ({"core_installed": False, "profile": None}, "znuny_itsm_missing"),
        ({"cmdb_installed": False}, "znuny_itsm_missing"),
        (
            {"profile": None, "unsupported_reason": "ITSMCore 6.0.1 not surveyed"},
            "unsupported_znuny_itsm_version",
        ),
    ):
        with pytest.raises(CiUnavailable) as caught:
            reader_for_info(info(**kw))
        assert caught.value.code == code


def test_like_contains_escapes_metacharacters() -> None:
    assert like_contains("a_b%c!d") == "%a!_b!%c!!d%"


def test_attribute_key_pattern_and_searchable_chains() -> None:
    defs = parse_definition(
        "---\n- Key: FQDN\n  Name: FQDN\n  Searchable: 1\n  Input:\n    Type: Text\n"
        "- Key: Plain\n  Name: Plain\n  Input:\n    Type: Text\n"
        "- Key: NIC\n  Name: NIC\n  Input:\n    Type: Text\n  Sub:\n"
        "  - Key: IP_Address\n    Name: IP\n    Searchable: 1\n    Input:\n      Type: Text\n",
        "yaml",
    )
    chains = searchable_chains(defs)
    assert chains == [("FQDN",), ("NIC", "IP_Address")]
    assert attribute_key_pattern(chains[0]) == "[1]{'Version'}[1]{'FQDN'}[%]{'Content'}"
    assert attribute_key_pattern(chains[1]) == (
        "[1]{'Version'}[1]{'NIC'}[%]{'IP!_Address'}[%]{'Content'}"
    )


def test_xml_hash_move_keeps_reporting_import() -> None:
    from tiqora.reporting.znuny_stats import xml_hash as old
    from tiqora.znuny import xml_hash as new

    assert set(old.__all__) == {
        "MAX_DEPTH",
        "MAX_FILE_BYTES",
        "MAX_STAT_ROWS",
        "Path_",
        "XmlHashError",
        "build_xml_hash",
        "parse_content_key",
        "xml_file_to_hash",
    }
    for name in old.__all__:
        assert getattr(old, name) is getattr(new, name)
    # the reporting consumers still import from the old path
    from tiqora.reporting.znuny_stats.reader import build_xml_hash
    from tiqora.reporting.znuny_stats.service import xml_file_to_hash

    assert build_xml_hash is new.build_xml_hash and xml_file_to_hash is new.xml_file_to_hash
