"""Golden: the Znuny ITSM importer against a Znuny ITSM peer (wave-4 import, Task 28).

The data is created in the peer through ``Kernel::System::ITSMConfigItem``
(``_itsm_import_support``): the custom class ``GoldImpAppliance`` (nested ``Sub`` attribute,
``CI`` reference, ``GeneralCatalog`` value, ``Integer``/``Date``/``DateTime``) next to the default
class ``Computer``, five CIs with 1-3 versions, CI<->CI and ticket->CI links and one binary
attachment. ``run_import`` then copies it
into the Tiqora CMDB of the SAME database and every case compares Tiqora's rows with what Znuny
itself returns (``VersionList`` / ``VersionGet(XMLDataGet => 1)`` / ``LinkList`` /
``ConfigItemAttachmentGet``).

    GOLDEN_PEER=znuny-6.5-itsm just golden-up && GOLDEN_PEER=znuny-6.5-itsm just golden-seed
    cd backend && GOLDEN=1 GOLDEN_PEER=znuny-6.5-itsm uv run python -m pytest -q -m golden \\
        ../tests/golden/test_itsm_import.py

The cases build on each other in file order (dry run -> first import -> second run -> a new Znuny
version -> third run); the module-scoped ``flow`` fixture carries the state. The module takes a
snapshot of every ``tiqora_*`` table the importer writes and restores it at the end, deletes its
Znuny CIs (``ConfigItemDelete``) and ticket; the general catalog item of the custom class cannot
be deleted in Znuny and stays (documented in ``README-multi-peer.md``).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from _itsm_import_support import (
    CLASS_NAME,
    TiqoraSnapshot,
    add_version,
    create_scenario,
    delete_scenario,
    dump_cis,
    run_importer,
    znuny_json,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import (
    TiqoraCmdbAttachment,
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiVersion,
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
    TiqoraItsmImportMap,
    TiqoraItsmLink,
)
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.itsm.znuny_format.xmldata import xmldata_to_attributes

pytestmark = [pytest.mark.golden, pytest.mark.itsm_peer]

PREFIX = "goldimp"
SessionFactory = async_sessionmaker[AsyncSession]
PAGE_SIZE = 2  # five CIs -> three pages: the paging of every step is exercised


@dataclass
class Flow:
    """State carried from case to case (the cases run in file order)."""

    znuny: dict[str, Any] = field(default_factory=dict)  # create_scenario result
    znuny_ci_ids: list[int] = field(default_factory=list)  # every CI of the peer, ascending
    first: ImportReport | None = None
    second: ImportReport | None = None
    third: ImportReport | None = None
    ci_map: dict[int, int] = field(default_factory=dict)  # znuny ci id -> tiqora ci id
    catalog_map: dict[int, int] = field(default_factory=dict)  # znuny gc id -> tiqora catalog id
    znuny_counts: dict[str, int] = field(default_factory=dict)  # Znuny rows before the first run
    #: what the importer has to find in the peer's tables (the peer may hold CIs of other golden
    #: modules; the counters of the report are checked against these, the content only for ours)
    expected_links: int = 0
    expected_attachments: int = 0


class MapResolver:
    """``RefResolver`` over the import map (independent of the importer's own resolver)."""

    def __init__(self, cis: dict[int, int], catalog: dict[int, int]) -> None:
        self._cis = cis
        self._catalog = catalog

    def ci(self, znuny_ci_id: str) -> int | None:
        return self._cis.get(int(znuny_ci_id)) if znuny_ci_id.strip().isdigit() else None

    def catalog(self, znuny_gc_id: str) -> int | None:
        return self._catalog.get(int(znuny_gc_id)) if znuny_gc_id.strip().isdigit() else None


# --- fixtures ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flow() -> Flow:
    return Flow()


@pytest.fixture(scope="module", autouse=True)
def _module_setup(golden_pymysql_dsn: dict[str, object], flow: Flow) -> Generator[None, None, None]:
    """Snapshot the Tiqora tables, create the Znuny scenario; undo both at the end."""
    snapshot = TiqoraSnapshot.take(golden_pymysql_dsn)  # type: ignore[arg-type]
    delete_scenario(PREFIX)  # leftovers of an aborted run
    clear_detect_cache()
    try:
        flow.znuny = create_scenario(PREFIX)
        flow.znuny_ci_ids = sorted(
            int(i) for i in znuny_json("Emit($CIObject->ConfigItemSearchExtended() || []);")
        )
        expected = _expected_counters()
        flow.expected_links = expected["links"]
        flow.expected_attachments = expected["attachments"]
        yield
    finally:
        try:
            delete_scenario(PREFIX)
        finally:
            snapshot.restore()
            clear_detect_cache()


# --- helpers ----------------------------------------------------------------------------------


async def _run(factory: SessionFactory, *, dry_run: bool) -> ImportReport:
    """One run of the importer the way the job runs it (own run row, page size 2)."""
    return await run_importer(factory, dry_run=dry_run, page_size=PAGE_SIZE)


async def _counts(factory: SessionFactory) -> dict[str, int]:
    """Row counts of the tables a run writes (a dry run must leave all of them alone)."""
    models = {
        "ci": TiqoraCmdbCi,
        "version": TiqoraCmdbCiVersion,
        "class": TiqoraCmdbClass,
        "definition": TiqoraCmdbClassDefinition,
        "catalog": TiqoraCmdbCatalog,
        "link": TiqoraItsmLink,
        "attachment": TiqoraCmdbAttachment,
        "map": TiqoraItsmImportMap,
    }
    async with factory() as session:
        return {
            name: int((await session.execute(select(func.count()).select_from(model))).scalar_one())
            for name, model in models.items()
        }


def _report(report: ImportReport | None) -> str:
    return json.dumps(report.summary() if report else None, indent=1, default=str)


def _step(report: ImportReport | None, name: str) -> dict[str, int]:
    assert report is not None
    counts = report.steps[name]
    return {
        "created": counts.created,
        "updated": counts.updated,
        "unchanged": counts.unchanged,
        "skipped": counts.skipped,
        "problems": counts.problems,
    }


async def _load_maps(factory: SessionFactory, flow: Flow) -> None:
    async with factory() as session:
        rows = (await session.execute(select(TiqoraItsmImportMap))).scalars().all()
    flow.ci_map = {int(r.source_id): int(r.target_id) for r in rows if r.source_kind == "ci"}
    flow.catalog_map = {
        int(r.source_id): int(r.target_id) for r in rows if r.source_kind == "catalog"
    }


def _expected_counters() -> dict[str, int]:
    """Links (valid, a CI at one end, only CI / ticket / service ends) and database-stored CI
    attachments the importer has to find in the peer."""
    raw: dict[str, int] = znuny_json(
        r"""
my $DB = $Kernel::OM->Get('Kernel::System::DB');
$DB->Prepare(
    SQL => 'SELECT COUNT(*) FROM link_relation r '
        . 'JOIN link_object so ON so.id = r.source_object_id '
        . 'JOIN link_object tobj ON tobj.id = r.target_object_id '
        . 'JOIN link_state s ON s.id = r.state_id '
        . "WHERE s.name = 'Valid' AND (so.name = 'ITSMConfigItem' OR tobj.name = 'ITSMConfigItem') "
        . "AND so.name IN ('ITSMConfigItem','Ticket','Service') "
        . "AND tobj.name IN ('ITSMConfigItem','Ticket','Service')",
);
my @Links = $DB->FetchrowArray();
$DB->Prepare(
    SQL => "SELECT COUNT(*) FROM virtual_fs WHERE filename LIKE 'ConfigItem/%' "
        . "AND backend LIKE '%::DB'",
);
my @Atts = $DB->FetchrowArray();
Emit( { links => $Links[0] + 0, attachments => $Atts[0] + 0 } );
"""
    )
    return raw


def _znuny_counts() -> dict[str, int]:
    """Row counts and a change fingerprint of the Znuny tables the importer reads."""
    raw: dict[str, int] = znuny_json(
        r"""
my $DB = $Kernel::OM->Get('Kernel::System::DB');
my %C;
for my $Table (qw(configitem configitem_version configitem_definition link_relation
                  general_catalog virtual_fs xml_storage)) {
    $DB->Prepare( SQL => "SELECT COUNT(*) FROM $Table" );
    my @Row = $DB->FetchrowArray();
    $C{$Table} = $Row[0] + 0;
}
$DB->Prepare( SQL => 'SELECT COALESCE(SUM(last_version_id), 0) FROM configitem' );
my @Row = $DB->FetchrowArray();
$C{configitem_last_versions} = $Row[0] + 0;
Emit( \%C );
"""
    )
    return raw


def _parse_znuny_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


# --- cases ------------------------------------------------------------------------------------


async def test_peer_has_the_scenario(flow: Flow) -> None:
    """Sanity: the data the later cases rely on is really in the peer."""
    cis = flow.znuny["cis"]
    assert sorted(cis) == ["app01", "app02", "db01", "fw01", "fw02"]
    assert [len(cis[k]["versions"]) for k in ("app01", "app02", "db01", "fw01", "fw02")] == [
        3,
        1,
        2,
        2,
        1,
    ]
    assert {cis[k]["class"] for k in cis} == {"Computer", CLASS_NAME}
    assert len(flow.znuny["attachment"]) == 256 + len(b"\xff\xfe golden attachment")
    assert set(cis[k]["id"] for k in cis) <= set(flow.znuny_ci_ids)


async def test_dry_run_creates_nothing(golden_session_factory: SessionFactory, flow: Flow) -> None:
    before = await _counts(golden_session_factory)
    report = await _run(golden_session_factory, dry_run=True)
    after = await _counts(golden_session_factory)
    assert after == before, f"a dry run wrote rows: {before} -> {after}"
    total = len(flow.znuny_ci_ids)
    assert _step(report, "cis_pass1")["created"] == total, _report(report)
    assert _step(report, "cis_pass2")["created"] == total, _report(report)
    assert _step(report, "attachments")["created"] == flow.expected_attachments, _report(report)
    assert report.steps["links"].created == flow.expected_links, _report(report)


async def test_first_import_matches_znuny(
    golden_session_factory: SessionFactory, flow: Flow
) -> None:
    flow.znuny_counts = _znuny_counts()
    flow.first = await _run(golden_session_factory, dry_run=False)
    report = flow.first
    total = len(flow.znuny_ci_ids)
    for step in ("cis_pass1", "cis_pass2"):
        counts = _step(report, step)
        assert counts["created"] == total, f"{step}: {counts}\n{_report(report)}"
        assert counts["skipped"] == 0, f"{step}: {counts}\n{_report(report)}"
    # Znuny's default class "Location" has the attribute key "E-Mail": the importer renames it and
    # reports it (IMPORT-DEFPARSE) instead of dropping the attribute silently
    assert ("definition_converted_with_issues", "class:Location:v1") in {
        (i.code, i.object_ref) for i in report.issues
    }, _report(report)
    await _load_maps(golden_session_factory, flow)
    assert sorted(flow.ci_map) == flow.znuny_ci_ids

    # the Znuny side now (VersionList order = creation order)
    z_cis = flow.znuny["cis"]
    resolver = MapResolver(flow.ci_map, flow.catalog_map)
    async with golden_session_factory() as session:
        class_rows = (await session.execute(select(TiqoraCmdbClass))).scalars().all()
        class_names = {c.id: c.name for c in class_rows}
        catalog_rows = (await session.execute(select(TiqoraCmdbCatalog))).scalars().all()
        catalog_names = {c.id: c.name for c in catalog_rows}
        definitions = {
            d.id: json.loads(d.attributes)
            for d in (await session.execute(select(TiqoraCmdbClassDefinition))).scalars()
        }
        for key, z in z_cis.items():
            tid = flow.ci_map[z["id"]]
            ci = await session.get(TiqoraCmdbCi, tid)
            assert ci is not None, key
            assert ci.number == z["number"], key
            assert class_names[ci.class_id] == z["class"], key
            assert ci.created_at == _parse_znuny_time(z["created"]), key
            versions = (
                (
                    await session.execute(
                        select(TiqoraCmdbCiVersion)
                        .where(TiqoraCmdbCiVersion.ci_id == tid)
                        .order_by(TiqoraCmdbCiVersion.id)
                    )
                )
                .scalars()
                .all()
            )
            # Tiqora version count == Znuny's VersionList count
            assert len(versions) == len(z["versions"]), key
            assert ci.version_count == len(z["versions"]), key
            assert ci.name == z["versions"][-1]["name"], key
            assert ci.current_version_id == versions[-1].id, key
            for tv, zv in zip(versions, z["versions"], strict=True):
                assert tv.name == zv["name"], (key, zv["id"])
                assert catalog_names[tv.deployment_state_id] == zv["depl_state"], (key, zv["id"])
                assert catalog_names[tv.incident_state_id] == zv["inci_state"], (key, zv["id"])
                assert tv.created_at == _parse_znuny_time(zv["created"]), (key, zv["id"])
                assert tv.created_by == zv["created_by"], (key, zv["id"])
                # the attributes == Znuny's XMLData converted with xmldata_to_attributes
                schema = {"attributes": definitions[tv.definition_id]}
                expected, issues = xmldata_to_attributes(zv["xml"], schema, resolver)
                assert issues == [], (key, zv["id"], issues)
                assert json.loads(tv.attributes) == expected, (key, zv["id"])
            # the cached incident/deployment state of the header is the newest version's
            assert catalog_names[ci.deployment_state_id] == z["versions"][-1]["depl_state"], key

        # --- the conversion itself, not just "equal to the same function" (literal checks) ----
        fw01 = z_cis["fw01"]
        fw01_versions = (
            (
                await session.execute(
                    select(TiqoraCmdbCiVersion)
                    .where(TiqoraCmdbCiVersion.ci_id == flow.ci_map[fw01["id"]])
                    .order_by(TiqoraCmdbCiVersion.id)
                )
            )
            .scalars()
            .all()
        )
        v1 = json.loads(fw01_versions[0].attributes)
        latest = json.loads(fw01_versions[-1].attributes)
        # the CI reference resolves to the Tiqora id of the referenced CI (not Znuny's id text)
        app01_tid = flow.ci_map[z_cis["app01"]["id"]]
        assert v1["Parent"] == [{"v": app01_tid}]
        assert latest["Parent"] == [{"v": app01_tid}]
        # catalog values are Tiqora catalog ids whose name is what Znuny's lookup says
        yes_no = {
            c.name: c.id for c in catalog_rows if c.catalog_class == "ITSM::ConfigItem::YesNo"
        }
        assert v1["Support"] == [{"v": yes_no["Yes"]}]
        assert latest["Support"] == [{"v": yes_no["No"]}]
        # nested Sub attribute, Integer, Date and DateTime
        assert v1["Port"] == [
            {"v": "p1", "s": {"Speed": [{"v": 1000}]}},
            {"v": "p2", "s": {"Speed": [{"v": 10000}]}},
        ]
        assert v1["Installed"] == [{"v": "2026-02-03"}]
        assert v1["LastCheck"] == [{"v": "2026-03-04T05:06:00"}]

    # the links: Znuny's link_relation / LinkList of every object == Tiqora's rows
    z_links, z_unordered, pointed = _znuny_links(flow)
    assert len(z_links) == 4
    assert pointed["DependsOn"] is True and pointed["RelevantTo"] is False
    assert {
        (*sorted([(k1, a), (k2, b)])[0], *sorted([(k1, a), (k2, b)])[1], t)
        for k1, a, k2, b, t in z_links
    } == z_unordered
    async with golden_session_factory() as session:
        rows = (await session.execute(select(TiqoraItsmLink))).scalars().all()
    reverse = {tid: zid for zid, tid in flow.ci_map.items()}

    def znuny_key(kind: str, key: str) -> str:
        return str(reverse[int(key)]) if kind == "ci" else key

    mine = {str(flow.ci_map[c["id"]]) for c in z_cis.values()}
    t_links = {
        (
            r.source_kind,
            znuny_key(r.source_kind, r.source_key),
            r.target_kind,
            znuny_key(r.target_kind, r.target_key),
            r.link_type,
        )
        for r in rows
        if (r.source_kind == "ci" and r.source_key in mine)
        or (r.target_kind == "ci" and r.target_key in mine)
    }
    assert {_canonical(e, pointed) for e in t_links} == {_canonical(e, pointed) for e in z_links}
    # the pointed type keeps Znuny's direction exactly
    assert {e for e in t_links if pointed[e[4]]} == {e for e in z_links if pointed[e[4]]}

    # the attachment: same bytes
    async with golden_session_factory() as session:
        atts = (
            (
                await session.execute(
                    select(TiqoraCmdbAttachment).where(
                        TiqoraCmdbAttachment.ci_id == flow.ci_map[z_cis["app01"]["id"]]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [a.filename for a in atts] == ["golden-binary.bin"]
    assert bytes(atts[0].content) == flow.znuny["attachment"]
    assert atts[0].content_type == "application/octet-stream"
    assert atts[0].size_bytes == len(flow.znuny["attachment"])


def _canonical(
    edge: tuple[str, str, str, str, str], pointed: dict[str, bool]
) -> tuple[str, str, str, str, str]:
    """An undirected link type has no direction: both ends in sorted order."""
    kind1, key1, kind2, key2, link_type = edge
    if pointed[link_type]:
        return edge
    (a, b), (c, d) = sorted([(kind1, key1), (kind2, key2)])
    return (a, b, c, d, link_type)


def _znuny_links(
    flow: Flow,
) -> tuple[set[tuple[str, str, str, str, str]], set[tuple[Any, ...]], dict[str, bool]]:
    """The scenario's links in the peer: ``(directed, unordered)``.

    * ``directed``: the rows of ``link_relation`` (source -> target, type) between the scenario's
      objects, the stored direction (a type with equal source and target name, like ``RelevantTo``,
      has no direction in Znuny: Tiqora stores it in its canonical orientation, see
      :func:`_canonical`);
    * ``unordered``: what Znuny's own ``LinkObject->LinkList`` reports for every object, reduced
      to ``(kinds, keys, type)`` without the direction (the ``Source`` / ``Target`` keys of a
      ``LinkList`` answer depend on the viewing object and on the link type, so the direction is
      checked against the table).
    """
    ids = [c["id"] for c in flow.znuny["cis"].values()]
    ticket_id = flow.znuny["ticket_id"]
    pairs = ",".join(f"['ITSMConfigItem',{i}]" for i in ids) + f",['Ticket',{ticket_id}]"
    raw = znuny_json(
        f"""
my %R;
for my $Pair ({pairs}) {{
    $R{{"$Pair->[0]:$Pair->[1]"}} = $LinkObject->LinkList(
        Object => $Pair->[0], Key => $Pair->[1], State => 'Valid', UserID => 1,
    ) || {{}};
}}
my $DB = $Kernel::OM->Get('Kernel::System::DB');
$DB->Prepare(
    SQL => 'SELECT so.name, r.source_key, tobj.name, r.target_key, t.name, s.name '
        . 'FROM link_relation r '
        . 'JOIN link_object so ON so.id = r.source_object_id '
        . 'JOIN link_object tobj ON tobj.id = r.target_object_id '
        . 'JOIN link_type t ON t.id = r.type_id JOIN link_state s ON s.id = r.state_id',
);
my @Rows;
while ( my @Row = $DB->FetchrowArray() ) {{ push @Rows, [ map {{ "$_" }} @Row ]; }}
my %Pointed;
for my $Name ( map {{ $_->[4] }} @Rows ) {{
    my $TypeID = $LinkObject->TypeLookup( Name => $Name, UserID => 1 );
    my %Type = $LinkObject->TypeGet( TypeID => $TypeID, UserID => 1 );
    $Pointed{{$Name}} = $Type{{Pointed}} ? 1 : 0;
}}
Emit({{ list => \\%R, rows => \\@Rows, pointed => \\%Pointed }});
"""
    )
    kinds = {"ITSMConfigItem": "ci", "Ticket": "ticket"}
    mine = {str(i) for i in ids}
    directed: set[tuple[str, str, str, str, str]] = set()
    for so, sk, to, tk, ltype, state in raw["rows"]:
        scenario_end = (so == "ITSMConfigItem" and sk in mine) or (
            to == "ITSMConfigItem" and tk in mine
        )
        if state == "Valid" and scenario_end:
            directed.add((kinds[so], sk, kinds[to], tk, ltype))
    unordered: set[tuple[Any, ...]] = set()
    for owner, by_object in raw["list"].items():
        obj, key = owner.split(":")
        for other_obj, by_type in by_object.items():
            for link_type, by_direction in by_type.items():
                for other_keys in by_direction.values():
                    for other_key in other_keys:
                        ends = sorted([(kinds[obj], key), (kinds[other_obj], other_key)])
                        unordered.add((*ends[0], *ends[1], link_type))
    pointed = {name: bool(flag) for name, flag in raw["pointed"].items()}
    return directed, unordered, pointed


async def test_second_run_reports_only_unchanged(
    golden_session_factory: SessionFactory, flow: Flow
) -> None:
    before = await _counts(golden_session_factory)
    flow.second = await _run(golden_session_factory, dry_run=False)
    report = flow.second
    after = await _counts(golden_session_factory)
    # the second run adds a run row only (the import map is idempotent)
    assert after == before, f"a repeated run wrote rows: {before} -> {after}"
    total = len(flow.znuny_ci_ids)
    for name, counts in report.steps.items():
        assert counts.created == 0, f"{name}: {_report(report)}"
        assert counts.updated == 0, f"{name}: {_report(report)}"
        assert counts.skipped == 0, f"{name}: {_report(report)}"
    assert _step(report, "cis_pass1")["unchanged"] == total, _report(report)
    assert _step(report, "cis_pass2")["unchanged"] == total, _report(report)
    assert _step(report, "links")["unchanged"] == flow.expected_links, _report(report)
    assert _step(report, "attachments")["unchanged"] == flow.expected_attachments, _report(report)


async def test_new_znuny_version_adds_exactly_one(
    golden_session_factory: SessionFactory, flow: Flow
) -> None:
    db01 = flow.znuny["cis"]["db01"]["id"]
    tid = flow.ci_map[db01]
    before = await _counts(golden_session_factory)
    new_version = add_version(db01, name=f"{PREFIX}-db01-v3.example.org", vendor="Vendor E")
    flow.znuny_counts = _znuny_counts()  # the new version is Znuny's own write, not the importer's

    flow.third = await _run(golden_session_factory, dry_run=False)
    report = flow.third
    after = await _counts(golden_session_factory)
    assert after["version"] == before["version"] + 1, _report(report)
    assert {k: v for k, v in after.items() if k not in ("version", "map")} == {
        k: v for k, v in before.items() if k not in ("version", "map")
    }
    assert _step(report, "cis_pass2")["updated"] == 1, _report(report)
    assert _step(report, "cis_pass2")["created"] == 0, _report(report)
    assert _step(report, "cis_pass1")["created"] == 0, _report(report)

    z = dump_cis([db01])[db01]
    assert [v["id"] for v in z["versions"]][-1] == new_version
    async with golden_session_factory() as session:
        versions = (
            (
                await session.execute(
                    select(TiqoraCmdbCiVersion)
                    .where(TiqoraCmdbCiVersion.ci_id == tid)
                    .order_by(TiqoraCmdbCiVersion.id)
                )
            )
            .scalars()
            .all()
        )
        ci = await session.get(TiqoraCmdbCi, tid)
    assert ci is not None
    assert len(versions) == len(z["versions"]) == 3
    assert versions[-1].name == f"{PREFIX}-db01-v3.example.org"
    assert ci.name == versions[-1].name
    assert ci.version_count == 3
    assert ci.current_version_id == versions[-1].id
    attrs = json.loads(versions[-1].attributes)
    assert attrs["Vendor"] == [{"v": "Vendor E"}]


async def test_nothing_was_written_to_znuny_tables(flow: Flow) -> None:
    """The importer reads Znuny's ITSM tables only: no row appears, none changes."""
    assert flow.znuny_counts, "the first import did not run"
    assert _znuny_counts() == flow.znuny_counts
    assert flow.znuny_counts["configitem"] == len(flow.znuny_ci_ids)
    assert flow.znuny_counts["link_relation"] >= 4
