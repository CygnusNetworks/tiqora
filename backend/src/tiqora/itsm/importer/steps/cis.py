"""Steps ``cis_pass1`` / ``cis_pass2``: configuration items and all their versions (D5 steps 3-4).

Pass 1 walks the Znuny CI ids page by page and creates every missing CI header (number, class,
creation time and author are kept; the name is provisional, the number, until pass 2 adds the
first version). Pass 2 walks the pages again and appends the versions that are not yet in the map
(kind ``version``), converting the Znuny ``XMLData`` against the definition version the Znuny
version was written with. Two passes make forward references work: when pass 2 converts a ``ci_ref``
attribute, every CI of the source already has its Tiqora id.

Both passes commit once per page and ask for cancellation once per page; they never hold more than
one page of source rows. A CI that was changed in Tiqora since the last import (its newest version
is none the importer wrote) is not touched again (``changed_in_tiqora``). A dry run counts like a
real run and writes nothing: the fake ids of the dry-run writer travel in :class:`ClassMaps`
(``dry_ci_ids``) from pass 1 to pass 2, because a dry-run map store has no memory across steps.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbNotFound
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.db.tiqora.models import TiqoraCmdbCi, TiqoraCmdbCiVersion
from tiqora.itsm.importer.facades import CmdbServiceWriter
from tiqora.itsm.importer.mapstore import MapEntry, MapStore, fingerprint
from tiqora.itsm.importer.ports import CmdbWritePort, ZnunyCi, ZnunyCiVersion
from tiqora.itsm.importer.steps import StepContext, mark_unfinished
from tiqora.itsm.importer.steps.classes import ClassMaps, DefinitionTarget
from tiqora.itsm.znuny_format.xmldata import RefResolver, xmldata_to_attributes

STEP_PASS1 = "cis_pass1"
STEP_PASS2 = "cis_pass2"
KIND_CI = "ci"
KIND_VERSION = "version"
PROGRESS_LABEL = "CIs werden übernommen …"
CHANGE_NOTE = "Import aus Znuny"
_DEFAULT_DEPLOYMENT_KEY = "production"
_DEFAULT_INCIDENT_KEY = "operational"
_SHOWN = 80


class MapResolver(RefResolver):
    """Znuny ids of ``ci_ref`` / ``catalog`` attribute values -> Tiqora ids, from preloaded maps."""

    def __init__(self, ci_ids: Mapping[str, int], catalog_ids: Mapping[str, int]) -> None:
        self._ci = ci_ids
        self._catalog = catalog_ids

    def ci(self, znuny_ci_id: str) -> int | None:
        return self._ci.get(znuny_ci_id.strip())

    def catalog(self, znuny_gc_id: str) -> int | None:
        return self._catalog.get(znuny_gc_id.strip())


async def _existing_cis(session: AsyncSession, ids: Sequence[int]) -> set[int]:
    """The ids of ``ids`` that are CI rows (dry-run fake ids never are)."""
    real = [i for i in ids if i > 0]
    if not real:
        return set()
    rows = await session.execute(select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.id.in_(real)))
    return {int(i) for i in rows.scalars()}


async def _existing_versions(session: AsyncSession, ids: Sequence[int]) -> set[int]:
    real = [i for i in ids if i > 0]
    if not real:
        return set()
    rows = await session.execute(
        select(TiqoraCmdbCiVersion.id).where(TiqoraCmdbCiVersion.id.in_(real))
    )
    return {int(i) for i in rows.scalars()}


def _alive(ctx: StepContext, entry: MapEntry | None, existing: set[int]) -> bool:
    """Is the map entry's Tiqora row still there (a dry run's fake ids count as there)."""
    if entry is None:
        return False
    return entry.target_id in existing or (ctx.dry_run and entry.target_id < 0)


def _tiqora_id(ctx: StepContext, entry: MapEntry, existing: set[int]) -> int | None:
    return entry.target_id if _alive(ctx, entry, existing) else None


async def _ci_targets(
    ctx: StepContext,
    session: AsyncSession,
    store: MapStore,
    maps: ClassMaps,
    znuny_ids: Sequence[str],
) -> dict[str, int]:
    """``{znuny ci id: tiqora ci id}`` of the CIs that exist (dry run: plus the fake ids)."""
    found = await store.get_many(KIND_CI, znuny_ids)
    existing = await _existing_cis(session, [e.target_id for e in found.values()])
    out: dict[str, int] = {}
    for sid, entry in found.items():
        tid = _tiqora_id(ctx, entry, existing)
        if tid is not None:
            out[sid] = tid
    if ctx.dry_run:
        for sid in znuny_ids:
            if sid not in out and sid.isdigit() and int(sid) in maps.dry_ci_ids:
                out[sid] = maps.dry_ci_ids[int(sid)]
    return out


# --- pass 1 -----------------------------------------------------------------------------------


async def run_cis_pass1(ctx: StepContext, maps: ClassMaps, catalog_ids: dict[str, int]) -> None:
    """Create the header of every CI that has no Tiqora counterpart yet."""
    ctx.report.start_step(STEP_PASS1)
    defined_classes = {t.tiqora_class_id for t in maps.definitions.values()}
    async with ctx.session_factory() as session:
        ports = ctx.ports_for(session)
        writer = ctx.writer_for(session)
        store = ctx.mapstore_for(session)
        total = await ports.source.ci_count()
        done = 0
        after = 0
        while True:
            ids = await ports.source.ci_ids_page(after, ctx.page_size)
            if not ids:
                break
            after = max(ids)
            await ctx.check_cancelled()
            rows = await ports.source.cis(ids)
            known = await store.get_many(KIND_CI, [str(c.id) for c in rows])
            existing = await _existing_cis(session, [e.target_id for e in known.values()])
            for ci in rows:
                await _pass1_ci(
                    ctx, writer, store, maps, catalog_ids, defined_classes, ci, known, existing
                )
            if not ctx.dry_run:
                await session.commit()
            done += len(ids)
            await ctx.progress(done, total, PROGRESS_LABEL)


async def _pass1_ci(
    ctx: StepContext,
    writer: CmdbWritePort,
    store: MapStore,
    maps: ClassMaps,
    catalog_ids: dict[str, int],
    defined_classes: set[int],
    ci: ZnunyCi,
    known: dict[str, MapEntry],
    existing: set[int],
) -> None:
    hit = known.get(str(ci.id))
    if hit is not None and _alive(ctx, hit, existing):
        if ctx.dry_run:
            maps.dry_ci_ids[ci.id] = hit.target_id
        ctx.report.count(STEP_PASS1, "unchanged")
        return
    class_tid = maps.class_ids.get(ci.class_id)
    if class_tid is None or class_tid not in defined_classes:
        ctx.report.issue(
            STEP_PASS1,
            "no_definition",
            ci.number,
            f"Konfigurationselement {ci.number}: Die Klasse hat keine verwendbare Definition",
        )
        ctx.report.count(STEP_PASS1, "skipped")
        return
    tid = await writer.create_imported_ci(
        class_id=class_tid,
        number=ci.number,
        name=ci.number,  # provisional: the first version of pass 2 brings the real name
        created_by=ci.created_by,
        created_at=ci.created_at,
        deployment_state_id=_state(catalog_ids, ci.deployment_state_id),
        incident_state_id=_state(catalog_ids, ci.incident_state_id),
    )
    await store.put(KIND_CI, str(ci.id), tid, fingerprint((ci.number, ci.class_id)))
    if ctx.dry_run:
        maps.dry_ci_ids[ci.id] = tid
    ctx.report.count(STEP_PASS1, "created" if hit is None else "updated")


def _state(catalog_ids: Mapping[str, int], znuny_id: int | None) -> int | None:
    return None if znuny_id is None else catalog_ids.get(str(znuny_id))


# --- pass 2 -----------------------------------------------------------------------------------


def _ci_ref_values(
    node: Mapping[str, Any], attrs: Sequence[Mapping[str, Any]], out: set[str]
) -> None:
    """Collect the ``Content`` strings of every ``ci_ref`` attribute (recursing into groups)."""
    for a in attrs:
        entries = node.get(str(a["key"]))
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if a.get("type") == "ci_ref":
                value = entry.get("Content")
                if value not in (None, ""):
                    out.add(str(value).strip())
            if a.get("sub"):
                _ci_ref_values(entry, a["sub"], out)


async def _default_state_id(
    ctx: StepContext, session: AsyncSession, cache: dict[str, int | None], cls: str, key: str
) -> int | None:
    if cls not in cache:
        view = await ctx.ports_for(session).reader.catalog_item(cls, key)
        cache[cls] = None if view is None else view.id
    return cache[cls]


async def run_cis_pass2(ctx: StepContext, maps: ClassMaps, catalog_ids: dict[str, int]) -> None:
    """Append every Znuny CI version that was not imported yet, oldest first."""
    ctx.report.start_step(STEP_PASS2)
    defaults: dict[str, int | None] = {}
    async with ctx.session_factory() as session:
        ports = ctx.ports_for(session)
        writer = ctx.writer_for(session)
        # a dry run must see Tiqora's real newest version (conflict check), the dry-run writer
        # only knows its fakes
        probe: CmdbWritePort = CmdbServiceWriter(session) if ctx.dry_run else writer
        store = ctx.mapstore_for(session)
        total = await ports.source.ci_count()
        done = 0
        after = 0
        while True:
            ids = await ports.source.ci_ids_page(after, ctx.page_size)
            if not ids:
                break
            after = max(ids)
            await ctx.check_cancelled()
            rows = await ports.source.cis(ids)
            versions = await ports.source.versions([c.id for c in rows])
            page = _Page(rows, versions)
            await _pass2_page(ctx, session, writer, probe, store, maps, catalog_ids, defaults, page)
            if not ctx.dry_run:
                await session.commit()
            done += len(ids)
            await ctx.progress(done, total, PROGRESS_LABEL)


class _Page:
    def __init__(self, cis: list[ZnunyCi], versions: dict[int, list[ZnunyCiVersion]]) -> None:
        self.cis = cis
        self.versions = versions

    def ci_ref_ids(self, maps: ClassMaps) -> list[str]:
        out: set[str] = set()
        for versions in self.versions.values():
            for v in versions:
                target = maps.definitions.get(v.definition_id)
                if target is not None:
                    _ci_ref_values(v.xml_data, target.schema.get("attributes", []), out)
        return sorted(out)


async def _pass2_page(
    ctx: StepContext,
    session: AsyncSession,
    writer: CmdbWritePort,
    probe: CmdbWritePort,
    store: MapStore,
    maps: ClassMaps,
    catalog_ids: dict[str, int],
    defaults: dict[str, int | None],
    page: _Page,
) -> None:
    ci_targets = await _ci_targets(
        ctx, session, store, maps, [str(c.id) for c in page.cis] + page.ci_ref_ids(maps)
    )
    resolver = MapResolver(ci_targets, catalog_ids)
    all_versions = [v for vs in page.versions.values() for v in vs]
    known = await store.get_many(KIND_VERSION, [str(v.id) for v in all_versions])
    existing = await _existing_versions(session, [e.target_id for e in known.values()])
    for ci in page.cis:
        tid = ci_targets.get(str(ci.id))
        if tid is None:
            continue  # pass 1 skipped the CI and said why
        versions = sorted(page.versions.get(ci.id, []), key=lambda v: v.id)
        mapped = {
            v.id: known[str(v.id)].target_id
            for v in versions
            if _alive(ctx, known.get(str(v.id)), existing)
        }
        new = [v for v in versions if v.id not in mapped]
        if not new:
            ctx.report.count(STEP_PASS2, "unchanged")
            continue
        missing = [v for v in new if v.definition_id not in maps.definitions]
        if missing:
            ctx.report.issue(
                STEP_PASS2,
                "no_definition",
                ci.number,
                f"Konfigurationselement {ci.number}: Version {missing[0].id} gehört zu einer "
                "nicht verwendbaren Klassendefinition",
            )
            ctx.report.count(STEP_PASS2, "skipped")
            continue
        latest = await (probe if tid > 0 else writer).latest_version_id(tid)
        if latest is not None and latest not in set(mapped.values()):
            ctx.report.issue(
                STEP_PASS2,
                "changed_in_tiqora",
                ci.number,
                f"Konfigurationselement {ci.number}: in Tiqora geändert, die neuen Versionen aus "
                "Znuny wurden nicht übernommen",
            )
            ctx.report.count(STEP_PASS2, "skipped")
            continue
        if await _import_versions(
            ctx, session, writer, store, maps, catalog_ids, defaults, resolver, ci, tid, new
        ):
            ctx.report.count(STEP_PASS2, "updated" if mapped else "created")
            await mark_unfinished(ctx, store, {tid})
        else:
            ctx.report.count(STEP_PASS2, "skipped")


async def _import_versions(
    ctx: StepContext,
    session: AsyncSession,
    writer: CmdbWritePort,
    store: MapStore,
    maps: ClassMaps,
    catalog_ids: dict[str, int],
    defaults: dict[str, int | None],
    resolver: MapResolver,
    ci: ZnunyCi,
    tid: int,
    new: list[ZnunyCiVersion],
) -> bool:
    """Convert and append ``new`` (oldest first); False when nothing could be imported."""
    imported = False
    for v in new:
        target: DefinitionTarget = maps.definitions[v.definition_id]
        try:
            attributes, attr_issues = xmldata_to_attributes(v.xml_data, target.schema, resolver)
        except ValueError as exc:
            ctx.report.issue(
                STEP_PASS2,
                "bad_value",
                ci.number,
                f"Konfigurationselement {ci.number}: Version {v.id} nicht lesbar ({exc})",
            )
            return imported
        for issue in attr_issues:
            ctx.report.issue(
                STEP_PASS2,
                issue.code,
                f"{ci.number}:{issue.path}",
                f"Konfigurationselement {ci.number}, {issue.path}: {issue.value[:_SHOWN]}",
            )
        deployment = _state(catalog_ids, v.deployment_state_id)
        if deployment is None:
            deployment = await _default_state_id(
                ctx, session, defaults, DEPLOYMENT_STATE, _DEFAULT_DEPLOYMENT_KEY
            )
        incident = _state(catalog_ids, v.incident_state_id)
        if incident is None:
            incident = await _default_state_id(
                ctx, session, defaults, INCIDENT_STATE, _DEFAULT_INCIDENT_KEY
            )
        if deployment is None or incident is None:
            ctx.report.issue(
                STEP_PASS2,
                "unresolved_reference",
                ci.number,
                f"Konfigurationselement {ci.number}: Version {v.id} hat keinen bekannten Status",
            )
            return imported
        vid = await writer.add_imported_version(
            tid,
            definition_id=target.definition_id,
            name=v.name,
            deployment_state_id=deployment,
            incident_state_id=incident,
            attributes=attributes,
            created_by=v.created_by,
            created_at=v.created_at,
            change_note=CHANGE_NOTE,
            write_index=False,
        )
        if vid is None:  # equals the current version: the Znuny version maps onto it
            vid = await writer.latest_version_id(tid)
        if vid is None:
            raise CmdbNotFound(f"version of ci {tid}")
        await store.put(KIND_VERSION, str(v.id), vid, fingerprint(v))
        imported = True
    return imported
