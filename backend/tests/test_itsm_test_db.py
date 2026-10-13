"""The ITSM test databases (``tests/_itsm_db.py``): Znuny core + ITSM packages, both dialects."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests._itsm_db import ITSM_PARAMS, snapshot_name, to_async_url
from tests._itsm_fixtures import seed_ci, seed_ci_link, xml_storage_rows
from tiqora.itsm.seed_definitions import for_profile

pytestmark = pytest.mark.db

PLAIN_URL_FIXTURES = ["mariadb_znuny_url", "postgres_znuny_url"]
ITSM_TABLES = {
    "general_catalog",
    "general_catalog_preferences",
    "cip_allocate",
    "configitem",
    "configitem_version",
    "configitem_definition",
    "configitem_history",
    "configitem_history_type",
    "configitem_counter",
}


def _url(request: pytest.FixtureRequest, dialect: str, profile: str) -> str:
    return request.getfixturevalue("itsm_db_url")(dialect, profile)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
def test_itsm_db_has_core_and_itsm_tables(dialect, profile, request) -> None:
    url = _url(request, dialect, profile)
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert {"ticket", "article", "queue", "link_relation", "xml_storage"} <= tables
        assert tables >= ITSM_TABLES
        # the Tiqora tables are part of the database (create_all), including the itsm1 ones
        assert {"tiqora_settings", "tiqora_itsm_link_type", "tiqora_itsm_problem"} <= tables
        # the one structural difference between the profiles
        prefs = {c["name"] for c in insp.get_columns("general_catalog_preferences")}
        assert ("id" in prefs) is (profile == "itsm-7")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM cip_allocate")).scalar_one() == 25
            classes = set(
                conn.execute(
                    text(
                        "SELECT name FROM general_catalog"
                        " WHERE general_catalog_class = 'ITSM::ConfigItem::Class'"
                    )
                ).scalars()
            )
            assert classes == {"Computer", "Hardware", "Location", "Network", "Software"}
            # the ITSM seed rows of the package fixtures arrived (ticket types, DFs)
            seed = for_profile(profile)
            types = set(conn.execute(text("SELECT name FROM ticket_type")).scalars())
            assert {t.name for t in seed.ticket_types} <= types
            # ITSM sysconfig defaults are loaded and readable
            direction = conn.execute(
                text(
                    "SELECT effective_value FROM sysconfig_default"
                    " WHERE name = 'ITSM::Core::IncidentLinkTypeDirection'"
                )
            ).scalar_one()
            text_value = direction.decode() if isinstance(direction, bytes) else direction
            assert "DependsOn" in text_value
    finally:
        engine.dispose()


@pytest.mark.parametrize("url_fixture", PLAIN_URL_FIXTURES)
def test_main_test_db_has_no_itsm_tables(url_fixture, request) -> None:
    dialect = "mysql" if url_fixture.startswith("mariadb") else "postgresql"
    # build the ITSM database in the same container first: the main database must stay clean
    _url(request, dialect, "itsm-6")
    engine = create_engine(request.getfixturevalue(url_fixture))
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert "ticket" in tables
    assert not tables & (ITSM_TABLES | {"cip_allocate", "configitem_number"})


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
def test_leak_in_itsm_db_is_reported(dialect, profile, request) -> None:
    """A row a module commits into the ITSM database is found and removed by the module restore.

    ``link_object`` is the table the link tests of this plan write to (Znuny creates its rows on
    first use), so it is the leak to simulate here.
    """
    from tests.conftest import _SNAPSHOTS, _restore

    url = _url(request, dialect, profile)
    snap = _SNAPSHOTS[snapshot_name(dialect, profile)]
    assert snap.sync_url == url
    assert "link_object" in snap.rows  # snapshotted: leaks in it are detected
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO link_object (name) VALUES ('LeakProbe')"))
        leaked = _restore(snap)
        assert leaked == "link_object +1"
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM link_object")).scalar_one() == 0
        assert _restore(snap) is None  # idempotent: nothing left, the module ends clean
    finally:
        engine.dispose()


def test_snapshots_of_other_databases_are_untouched_by_the_name_key(request) -> None:
    """Registering by name keeps the plain ``mysql`` / ``postgresql`` keys intact."""
    from tests.conftest import _SNAPSHOTS

    _url(request, "mysql", "itsm-6")
    request.getfixturevalue("mariadb_znuny_url")
    assert {"mysql", snapshot_name("mysql", "itsm-6")} <= set(_SNAPSHOTS)
    assert _SNAPSHOTS["mysql"].sync_url != _SNAPSHOTS[snapshot_name("mysql", "itsm-6")].sync_url
    assert _SNAPSHOTS["mysql"].dialect == "mysql"


def test_xml_storage_rows_follow_the_znuny_layout() -> None:
    rows = dict(
        xml_storage_rows(
            {
                "Hostname": "web01.example.org",
                "NIC": [{"IPAddress": "192.0.2.10"}, {"IPAddress": "192.0.2.11"}],
                "Note": None,
            }
        )
    )
    assert rows["[1]{'Version'}[1]{'Hostname'}[1]{'Content'}"] == "web01.example.org"
    assert rows["[1]{'Version'}[1]{'Hostname'}[1]{'TagKey'}"] == "[1]{'Version'}[1]{'Hostname'}[1]"
    assert rows["[1]{'Version'}[1]{'NIC'}[2]{'IPAddress'}[1]{'Content'}"] == "192.0.2.11"
    assert "[1]{'Version'}[1]{'Note'}[1]{'Content'}" not in rows  # None: no Content row
    assert "[1]{'Version'}[1]{'Note'}[1]{'TagKey'}" in rows


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_seed_ci_round_trip(dialect, profile, request) -> None:
    url = _url(request, dialect, profile)
    engine = create_async_engine(to_async_url(url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            try:
                ci_id = await seed_ci(
                    session,
                    profile,
                    number="1000001",
                    class_name="Computer",
                    name="web01",
                    depl_state="Production",
                    inci_state="Warning",
                    versions=[
                        {"name": "web00", "attributes": {"Description": "old"}},
                        {
                            "attributes": {
                                "Description": "front end",
                                "NIC": [{"IPAddress": "192.0.2.10"}, {"IPAddress": "192.0.2.11"}],
                            }
                        },
                    ],
                )
                other = await seed_ci(
                    session,
                    profile,
                    number="1000002",
                    class_name="Hardware",
                    name="sw01",
                    depl_state="Planned",
                    inci_state="Operational",
                )
                await seed_ci_link(session, source_key=ci_id, target_key=other)
                await _assert_znuny_shape(session, ci_id, other)
            finally:
                await session.rollback()  # nothing stays behind
    finally:
        await engine.dispose()


async def _assert_znuny_shape(session, ci_id: int, other: int) -> None:
    ci = (
        (
            await session.execute(
                text(
                    "SELECT configitem_number, class_id, last_version_id, cur_depl_state_id,"
                    " cur_inci_state_id FROM configitem WHERE id = :i"
                ),
                {"i": ci_id},
            )
        )
        .mappings()
        .one()
    )
    assert ci["configitem_number"] == "1000001"
    versions = (
        (
            await session.execute(
                text(
                    "SELECT id, name, definition_id, depl_state_id, inci_state_id"
                    " FROM configitem_version WHERE configitem_id = :i ORDER BY id"
                ),
                {"i": ci_id},
            )
        )
        .mappings()
        .all()
    )
    assert [v["name"] for v in versions] == ["web00", "web01"]
    assert ci["last_version_id"] == versions[-1]["id"]  # the highest version id is current
    assert ci["cur_depl_state_id"] == versions[-1]["depl_state_id"]
    assert ci["cur_inci_state_id"] == versions[-1]["inci_state_id"]
    names = dict(
        (
            await session.execute(
                text("SELECT id, name FROM general_catalog WHERE id IN (:a, :b)"),
                {"a": ci["cur_depl_state_id"], "b": ci["cur_inci_state_id"]},
            )
        ).all()
    )
    assert names == {ci["cur_depl_state_id"]: "Production", ci["cur_inci_state_id"]: "Warning"}
    definition = (
        await session.execute(
            text("SELECT class_id FROM configitem_definition WHERE id = :d"),
            {"d": versions[-1]["definition_id"]},
        )
    ).scalar_one()
    assert definition == ci["class_id"]

    stored = dict(
        (
            await session.execute(
                text(
                    "SELECT xml_content_key, xml_content_value FROM xml_storage"
                    " WHERE xml_type = :t AND xml_key = :k"
                ),
                {"t": f"ITSM::ConfigItem::{ci['class_id']}", "k": str(versions[-1]["id"])},
            )
        ).all()
    )
    assert stored["[1]{'Version'}[1]{'Description'}[1]{'Content'}"] == "front end"
    assert stored["[1]{'Version'}[1]{'NIC'}[2]{'IPAddress'}[1]{'Content'}"] == "192.0.2.11"
    old = (
        await session.execute(
            text(
                "SELECT xml_content_value FROM xml_storage"
                " WHERE xml_key = :k AND xml_content_key = :ck"
            ),
            {"k": str(versions[0]["id"]), "ck": "[1]{'Version'}[1]{'Description'}[1]{'Content'}"},
        )
    ).scalar_one()
    assert old == "old"

    history = [
        row[0]
        for row in (
            await session.execute(
                text(
                    "SELECT t.name FROM configitem_history h JOIN configitem_history_type t"
                    " ON t.id = h.type_id WHERE h.configitem_id = :i ORDER BY h.id"
                ),
                {"i": ci_id},
            )
        ).all()
    ]
    assert history == ["ConfigItemCreate", "VersionCreate", "VersionCreate"]

    # no version means one empty version; a CI without versions is never a CI without a current one
    bare = (
        await session.execute(
            text("SELECT last_version_id FROM configitem WHERE id = :i"), {"i": other}
        )
    ).scalar_one()
    assert bare is not None

    link = (
        (
            await session.execute(
                text(
                    "SELECT o.name AS obj, t.name AS typ, s.name AS state, r.source_key,"
                    " r.target_key FROM link_relation r"
                    " JOIN link_object o ON o.id = r.source_object_id"
                    " JOIN link_type t ON t.id = r.type_id"
                    " JOIN link_state s ON s.id = r.state_id"
                    " WHERE r.source_key = :s"
                ),
                {"s": str(ci_id)},
            )
        )
        .mappings()
        .one()
    )
    assert dict(link) == {
        "obj": "ITSMConfigItem",
        "typ": "DependsOn",
        "state": "Valid",
        "source_key": str(ci_id),
        "target_key": str(other),
    }
