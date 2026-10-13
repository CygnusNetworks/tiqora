"""Fixtures of the ITSM importer tests (band 56070-56300): an in-memory Znuny source and a context.

``FakeSource`` implements ``ImportSource`` from Python lists, so the step tests need no Znuny
database; the adapter itself is tested against real ITSM databases (``_itsm_db``). ``make_ctx``
builds a ``StepContext`` on the real CMDB services (writer, reader) and the real map store;
``cleanup_cmdb_rows`` removes what a test imported.

Znuny data shapes: ``xml_data`` is Znuny's ``XMLData`` (``{Key: [{"Content": v, Sub: [...]}]}``),
catalog ids in it are ``general_catalog.id`` strings.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.cmdb.seed import DEFAULT_CATALOG_ROWS
from tiqora.itsm.ci_provider import CiUnavailable
from tiqora.itsm.importer.facades import CmdbServiceReader, CmdbServiceWriter, ItsmModeFacade
from tiqora.itsm.importer.mapstore import MapStore
from tiqora.itsm.importer.ports import (
    EPOCH,
    CmdbWritePort,
    ImportSource,
    Ports,
    ZnunyAttachment,
    ZnunyCatalogItem,
    ZnunyCi,
    ZnunyCiVersion,
    ZnunyClassDefinition,
    ZnunyLink,
    ZnunySourceInfo,
)
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.writer import DryRunWriter
from tiqora.itsm.znuny_adapter.reader import CipRow, ServiceItsmRow

BAND_LO, BAND_HI = 56070, 56299
T0 = datetime(2026, 1, 5, 9, 0, 0)

DEPLOYMENT = "ITSM::ConfigItem::DeploymentState"
INCIDENT = "ITSM::Core::IncidentState"
YES_NO = "ITSM::ConfigItem::YesNo"
CLASS_CATALOG = "ITSM::ConfigItem::Class"

# general_catalog ids of the sample (the ones of Znuny's delivery state where known)
GC_PLANNED, GC_PRODUCTION, GC_RETIRED = 31, 32, 33
GC_OPERATIONAL, GC_WARNING, GC_INCIDENT = 41, 42, 43
GC_YES, GC_NO = 37, 38
GC_COMPUTER, GC_NETWORK = 22, 25

#: Znuny YAML class definitions (the NIC definition of task 4, ``Computer`` in two versions)
DEFINITION_COMPUTER_V1 = """---
- Key: Vendor
  Name: Vendor
  Searchable: 1
  Input:
    Type: Text
    Size: 50
  CountMin: 0
  CountMax: 1
- Key: NIC
  Name: Network Adapter
  Input:
    Type: Text
    Required: 1
  CountMin: 0
  CountMax: 10
  CountDefault: 1
  Sub:
  - Key: IPoverDHCP
    Name: IP over DHCP
    Input:
      Type: GeneralCatalog
      Class: ITSM::ConfigItem::YesNo
    CountMin: 1
    CountMax: 1
    CountDefault: 1
  - Key: IPAddress
    Name: IP Address
    Input:
      Type: Text
    CountMin: 0
    CountMax: 20
    CountDefault: 0
"""
DEFINITION_COMPUTER_V2 = (
    DEFINITION_COMPUTER_V1
    + """- Key: CPU
  Name: CPU
  Input:
    Type: Text
  CountMin: 0
  CountMax: 16
"""
)
DEFINITION_NETWORK_V1 = """---
- Key: Vendor
  Name: Vendor
  Searchable: 1
  Input:
    Type: Text
    Size: 50
  CountMin: 0
  CountMax: 1
- Key: Description
  Name: Description
  Input:
    Type: TextArea
  CountMin: 0
  CountMax: 1
"""

CI_NUMBER_BASE = 2026101256000


#: Znuny group ids of the class ``Permission`` preference in the sample
GROUP_COMPUTER, GROUP_NETWORK = 56100, 56101


def _item(
    gc_id: int,
    cls: str,
    name: str,
    functionality: str | None,
    *,
    valid: bool = True,
    permission_group: int | None = None,
) -> ZnunyCatalogItem:
    return ZnunyCatalogItem(gc_id, cls, name, valid, None, functionality, permission_group)


def sample_catalog() -> list[ZnunyCatalogItem]:
    return [
        _item(GC_COMPUTER, CLASS_CATALOG, "Computer", None, permission_group=GROUP_COMPUTER),
        _item(GC_NETWORK, CLASS_CATALOG, "Network", None, permission_group=GROUP_NETWORK),
        _item(GC_YES, YES_NO, "Yes", None),
        _item(GC_NO, YES_NO, "No", None),
        _item(GC_PLANNED, DEPLOYMENT, "Planned", "preproductive"),
        _item(GC_PRODUCTION, DEPLOYMENT, "Production", "productive"),
        _item(GC_RETIRED, DEPLOYMENT, "Retired", "postproductive"),
        _item(GC_OPERATIONAL, INCIDENT, "Operational", "operational"),
        _item(GC_WARNING, INCIDENT, "Warning", "warning"),
        _item(GC_INCIDENT, INCIDENT, "Incident", "incident"),
    ]


class FakeSource(ImportSource):
    """In-memory Znuny ITSM. Lists are public: tests may change them directly."""

    def __init__(
        self,
        *,
        catalog: Sequence[ZnunyCatalogItem] = (),
        definitions: Sequence[ZnunyClassDefinition] = (),
        cis: Sequence[ZnunyCi] = (),
        versions: Mapping[int, Sequence[ZnunyCiVersion]] | None = None,
        links: Sequence[ZnunyLink] = (),
        attachments: Sequence[ZnunyAttachment] = (),
        changes: Mapping[str, int] | None = None,
        profile: str | None = "itsm-6",
        cmdb_installed: bool = True,
        fail_once_after_pages: int | None = None,
        on_first_page: Callable[[], None] | None = None,
        changed_since_count: int = 0,
        links_changed_since_count: int = 0,
        cip: Sequence[CipRow] = (),
        services: Sequence[ServiceItsmRow] = (),
    ) -> None:
        self.catalog = list(catalog)
        self.definitions = list(definitions)
        self.ci_rows = list(cis)
        self.version_rows: dict[int, list[ZnunyCiVersion]] = {
            k: list(v) for k, v in (versions or {}).items()
        }
        self.links = list(links)
        self.attachment_rows = list(attachments)
        self.changes = dict(changes or {})
        self.profile_name = profile
        self.cmdb_installed = cmdb_installed
        self.fail_once_after_pages = fail_once_after_pages
        self.on_first_page = on_first_page
        self.changed_since_count = changed_since_count
        self.links_changed_since_count = links_changed_since_count
        self.cip = list(cip)
        self.services = list(services)
        self.changed_since_args: list[datetime] = []
        self.links_changed_since_args: list[datetime] = []
        self._page_calls = 0
        self._failed = False
        self._requested: list[int] = []

    # --- mutators for re-run tests ---------------------------------------------------------

    def remove_ci(self, ci_id: int) -> None:
        self.ci_rows = [c for c in self.ci_rows if c.id != ci_id]
        self.version_rows.pop(ci_id, None)
        key = str(ci_id)
        self.links = [
            link
            for link in self.links
            if not (
                (link.source_object == "ITSMConfigItem" and link.source_key == key)
                or (link.target_object == "ITSMConfigItem" and link.target_key == key)
            )
        ]
        self.attachment_rows = [a for a in self.attachment_rows if a.ci_id != ci_id]

    def add_version(self, ci_id: int, name: str, xml: Mapping[str, Any]) -> ZnunyCiVersion:
        """Append a version (next version id, the definition of the previous one)."""
        current = self.version_rows.get(ci_id, [])
        ci = next(c for c in self.ci_rows if c.id == ci_id)
        prev = current[-1] if current else None
        next_id = max((v.id for vs in self.version_rows.values() for v in vs), default=0) + 1
        created = (prev.created_at if prev else ci.created_at) + timedelta(days=1)
        version = ZnunyCiVersion(
            id=next_id,
            ci_id=ci_id,
            name=name,
            definition_id=prev.definition_id if prev else 0,
            deployment_state_id=prev.deployment_state_id if prev else ci.deployment_state_id,
            incident_state_id=prev.incident_state_id if prev else ci.incident_state_id,
            created_at=created,
            created_by=ci.changed_by,
            xml_data=dict(xml),
        )
        self.version_rows.setdefault(ci_id, []).append(version)
        self.ci_rows = [
            dataclasses.replace(c, last_version_id=version.id, changed_at=created)
            if c.id == ci_id
            else c
            for c in self.ci_rows
        ]
        return version

    def class_id(self, name: str) -> int:
        """``general_catalog.id`` of the CI class with this name."""
        return next(
            i.id for i in self.catalog if i.catalog_class == CLASS_CATALOG and i.name == name
        )

    def definition_id(self, class_name: str, version: int) -> int:
        """Znuny id of one definition version of a class."""
        cid = self.class_id(class_name)
        return next(d.id for d in self.definitions if d.class_id == cid and d.version == version)

    def add_definition(self, class_name: str, raw: str) -> ZnunyClassDefinition:
        """Append the next definition version of a class (next Znuny definition id)."""
        cid = self.class_id(class_name)
        version = max((d.version for d in self.definitions if d.class_id == cid), default=0) + 1
        row = ZnunyClassDefinition(
            max((d.id for d in self.definitions), default=0) + 1,
            cid,
            version,
            raw,
            T0 + timedelta(days=60 + version),
            1,
        )
        self.definitions.append(row)
        return row

    def rename_catalog_item(self, item_id: int, name: str) -> None:
        self.catalog = [
            dataclasses.replace(i, name=name) if i.id == item_id else i for i in self.catalog
        ]

    def _require_cmdb(self) -> None:
        """Like the adapter's reader: no CMDB tables without ITSMConfigurationManagement."""
        if not self.cmdb_installed:
            raise CiUnavailable("znuny_itsm_missing", "the CMDB package is not installed")

    def max_ids_requested_per_call(self) -> int:
        """Largest id list / page limit any reader call received so far."""
        return max(self._requested, default=0)

    # --- ImportSource ------------------------------------------------------------------------

    async def info(self) -> ZnunySourceInfo:
        return ZnunySourceInfo(
            core_installed=self.profile_name is not None,
            cmdb_installed=self.cmdb_installed,
            profile=self.profile_name,
            unsupported_reason=None if self.profile_name else "unsupported_znuny_itsm_version",
        )

    async def profile(self) -> str | None:
        return self.profile_name

    async def catalog_items(self) -> list[ZnunyCatalogItem]:
        return list(self.catalog)

    async def class_definitions(self) -> list[ZnunyClassDefinition]:
        return sorted(self.definitions, key=lambda d: (d.class_id, d.version))

    async def ci_count(self) -> int:
        self._require_cmdb()
        return len(self.ci_rows)

    async def class_count(self) -> int:
        self._require_cmdb()
        return len({d.class_id for d in self.definitions})

    async def ci_ids_page(self, after_id: int, limit: int) -> list[int]:
        self._requested.append(limit)
        if self._page_calls == 0 and self.on_first_page is not None:
            self.on_first_page()
        if (
            self.fail_once_after_pages is not None
            and not self._failed
            and self._page_calls >= self.fail_once_after_pages
        ):
            self._failed = True
            self._page_calls += 1
            raise RuntimeError("worker died")
        self._page_calls += 1
        return sorted(c.id for c in self.ci_rows if c.id > after_id)[:limit]

    async def cis(self, ids: Sequence[int]) -> list[ZnunyCi]:
        self._requested.append(len(ids))
        wanted = set(ids)
        return [c for c in sorted(self.ci_rows, key=lambda c: c.id) if c.id in wanted]

    async def versions(self, ci_ids: Sequence[int]) -> dict[int, list[ZnunyCiVersion]]:
        self._requested.append(len(ci_ids))
        return {i: list(self.version_rows.get(i, [])) for i in ci_ids if i in self.version_rows}

    async def links_for_cis(self, ci_ids: Sequence[int]) -> list[ZnunyLink]:
        self._requested.append(len(ci_ids))
        keys = {str(i) for i in ci_ids}
        out: dict[str, ZnunyLink] = {}
        for link in self.links:
            at_source = link.source_object == "ITSMConfigItem" and link.source_key in keys
            at_target = link.target_object == "ITSMConfigItem" and link.target_key in keys
            if at_source or at_target:
                out.setdefault(link.map_key, link)
        return list(out.values())

    async def attachments(self, ci_ids: Sequence[int], *, with_data: bool) -> list[ZnunyAttachment]:
        self._requested.append(len(ci_ids))
        wanted = set(ci_ids)
        return [
            a if with_data else dataclasses.replace(a, data=None)
            for a in self.attachment_rows
            if a.ci_id in wanted
        ]

    async def change_counts(self) -> dict[str, int]:
        return dict(self.changes)

    async def max_ci_change_time(self) -> datetime | None:
        return max((c.changed_at for c in self.ci_rows), default=None)

    async def count_cis_changed_since(self, since: datetime) -> int:
        self._require_cmdb()
        self.changed_since_args.append(since)
        return self.changed_since_count

    async def count_links_changed_since(self, since: datetime) -> int:
        self._require_cmdb()
        self.links_changed_since_args.append(since)
        return self.links_changed_since_count

    async def cip_rows(self) -> list[CipRow]:
        return list(self.cip)

    async def service_itsm_rows(self) -> list[ServiceItsmRow]:
        return list(self.services)


def _computer_xml(n: int, level: int) -> dict[str, Any]:
    xml: dict[str, Any] = {
        "Vendor": [{"Content": "Example Corp"}],
        "NIC": [
            {
                "Content": "eth0",
                "IPoverDHCP": [{"Content": str(GC_NO)}],
                "IPAddress": [{"Content": f"192.0.2.{n}"}],
            }
        ],
    }
    if level >= 2:
        xml["NIC"].append(
            {
                "Content": "eth1",
                "IPoverDHCP": [{"Content": str(GC_YES)}],
                "IPAddress": [{"Content": f"198.51.100.{n}"}],
            }
        )
    if level >= 3:
        xml["CPU"] = [{"Content": "8 cores"}]
    return xml


def sample_source(**knobs: Any) -> FakeSource:
    """Catalog + 2 classes + 10 CIs (1-3 versions each) + links + attachments; knobs: see class."""
    definitions = [
        ZnunyClassDefinition(1, GC_COMPUTER, 1, DEFINITION_COMPUTER_V1, T0, 1),
        ZnunyClassDefinition(2, GC_COMPUTER, 2, DEFINITION_COMPUTER_V2, T0 + timedelta(days=30), 1),
        ZnunyClassDefinition(3, GC_NETWORK, 1, DEFINITION_NETWORK_V1, T0, 1),
    ]
    cis: list[ZnunyCi] = []
    versions: dict[int, list[ZnunyCiVersion]] = {}
    version_id = itertools.count(1)
    for n in range(1, 11):
        is_computer = n <= 7
        class_id = GC_COMPUTER if is_computer else GC_NETWORK
        count = ((n - 1) % 3) + 1
        created = T0 + timedelta(days=n)
        rows: list[ZnunyCiVersion] = []
        for level in range(1, count + 1):
            xml = (
                _computer_xml(n, level)
                if is_computer
                else {"Vendor": [{"Content": "Example Net"}], "Description": [{"Content": "x" * n}]}
            )
            rows.append(
                ZnunyCiVersion(
                    id=next(version_id),
                    ci_id=n,
                    name=f"itim56-ci-{n:02d}",
                    definition_id=(2 if level >= 2 else 1) if is_computer else 3,
                    deployment_state_id=GC_PLANNED if level == 1 else GC_PRODUCTION,
                    incident_state_id=GC_OPERATIONAL if level < 3 else GC_WARNING,
                    created_at=created + timedelta(days=level),
                    created_by=1,
                    xml_data=xml,
                )
            )
        versions[n] = rows
        last = rows[-1]
        cis.append(
            ZnunyCi(
                id=n,
                number=f"{CI_NUMBER_BASE + n}",
                class_id=class_id,
                last_version_id=last.id,
                deployment_state_id=last.deployment_state_id,
                incident_state_id=last.incident_state_id,
                created_at=created,
                created_by=1,
                changed_at=last.created_at,
                changed_by=1,
            )
        )

    def link(src: int, tobj: str, tkey: str, type_name: str) -> ZnunyLink:
        return ZnunyLink(
            "ITSMConfigItem", str(src), tobj, tkey, type_name, "Valid", T0 + timedelta(days=20), 1
        )

    links = [
        link(1, "ITSMConfigItem", "2", "DependsOn"),
        link(2, "ITSMConfigItem", "3", "DependsOn"),
        link(4, "ITSMConfigItem", "5", "DependsOn"),
        link(1, "Ticket", "56071", "RelevantTo"),
        link(3, "FAQ", "5", "RelevantTo"),
    ]
    attachments = [
        ZnunyAttachment(1, "report.txt", "text/plain", 12, b"hello\x00world!", T0),
        ZnunyAttachment(2, "dump.bin", "application/octet-stream", 4096, None, T0),
    ]
    return FakeSource(
        catalog=sample_catalog(),
        definitions=definitions,
        cis=cis,
        versions=versions,
        links=links,
        attachments=attachments,
        changes={"change": 3, "workorder": 5},
        **knobs,
    )


_RUN_IDS = itertools.count(BAND_LO)


def make_ctx(
    session_factory: async_sessionmaker[AsyncSession],
    source: ImportSource,
    *,
    dry_run: bool = False,
    page_size: int = 3,
    user_id: int = 56070,
) -> StepContext:
    """A step context on the real CMDB services; a dry run gets the in-memory dry-run writer.

    Every call takes the next run id of the band (it wraps), so a test that makes several
    contexts sees several runs.
    """
    run_id = BAND_LO + (next(_RUN_IDS) - BAND_LO) % (BAND_HI - BAND_LO + 1)
    dry_writer = DryRunWriter()

    def writer_for(session: AsyncSession) -> CmdbWritePort:
        return dry_writer if dry_run else CmdbServiceWriter(session)

    def ports_for(session: AsyncSession) -> Ports:
        return Ports(
            source=source,
            writer=writer_for(session),
            reader=CmdbServiceReader(session),
            mode=ItsmModeFacade(session),
        )

    async def progress(done: int, total: int | None, label: str) -> None:
        return None

    async def check_cancelled() -> None:
        return None

    return StepContext(
        session_factory=session_factory,
        ports_for=ports_for,
        run_id=run_id,
        dry_run=dry_run,
        page_size=page_size,
        report=ImportReport(),
        progress=progress,
        check_cancelled=check_cancelled,
        user_id=user_id,
        writer_for=writer_for,
        mapstore_for=lambda s: MapStore(s, run_id=run_id, read_only=dry_run),
    )


def cleanup_cmdb_rows(sync_url: str) -> None:
    """Delete what the importer created: map rows of the band and the CMDB rows they point to.

    Seed catalog rows an import adopted are put back to their seed values, not deleted.
    """
    seeds = {(r.catalog_class, r.item_key): r for r in DEFAULT_CATALOG_ROWS}
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            targets: dict[str, list[int]] = {}
            for kind, tid in conn.execute(
                text(
                    "SELECT source_kind, target_id FROM tiqora_itsm_import_map"
                    " WHERE run_id BETWEEN :lo AND :hi"
                ),
                {"lo": BAND_LO, "hi": BAND_HI},
            ).all():
                targets.setdefault(str(kind), []).append(int(tid))
            ci_ids = [i for i in targets.get("ci", []) if i > 0]
            for ci in ci_ids:
                key = {"k": str(ci)}
                conn.execute(
                    text(
                        "DELETE FROM tiqora_itsm_link"
                        " WHERE (source_kind = 'ci' AND source_key = :k)"
                        " OR (target_kind = 'ci' AND target_key = :k)"
                    ),
                    key,
                )
                for table in (
                    "tiqora_cmdb_ci_attr",
                    "tiqora_cmdb_ci_history",
                    "tiqora_cmdb_attachment",
                    "tiqora_cmdb_ci_version_archive",
                    "tiqora_cmdb_ci_version",
                ):
                    conn.execute(text(f"DELETE FROM {table} WHERE ci_id = :i"), {"i": ci})
                conn.execute(text("DELETE FROM tiqora_cmdb_ci WHERE id = :i"), {"i": ci})
            for cid in (i for i in targets.get("class", []) if i > 0):
                conn.execute(
                    text("DELETE FROM tiqora_cmdb_class_definition WHERE class_id = :i"), {"i": cid}
                )
                conn.execute(text("DELETE FROM tiqora_cmdb_class WHERE id = :i"), {"i": cid})
            for tid in (i for i in targets.get("catalog", []) if i > 0):
                row = conn.execute(
                    text("SELECT catalog_class, item_key FROM tiqora_cmdb_catalog WHERE id = :i"),
                    {"i": tid},
                ).first()
                if row is None:
                    continue
                seed = seeds.get((str(row[0]), str(row[1])))
                if seed is None:
                    conn.execute(text("DELETE FROM tiqora_cmdb_catalog WHERE id = :i"), {"i": tid})
                else:
                    conn.execute(
                        text(
                            "UPDATE tiqora_cmdb_catalog SET name = :n, functionality = :f,"
                            " position = :p, valid = :v WHERE id = :i"
                        ),
                        {
                            "n": seed.name,
                            "f": seed.functionality,
                            "p": seed.position,
                            "v": True,
                            "i": tid,
                        },
                    )
            conn.execute(
                text("DELETE FROM tiqora_itsm_import_map WHERE run_id BETWEEN :lo AND :hi"),
                {"lo": BAND_LO, "hi": BAND_HI},
            )
    finally:
        engine.dispose()


__all__ = [
    "BAND_HI",
    "BAND_LO",
    "EPOCH",
    "FakeSource",
    "cleanup_cmdb_rows",
    "make_ctx",
    "sample_catalog",
    "sample_source",
]
