"""Step ``classes``: every Znuny CI class and every definition version of it (D5 step 2).

A class is a general-catalog item of ``ITSM::ConfigItem::Class``. Its Tiqora key is the lower-case
slug of the Znuny name (``Computer`` -> ``computer``); its group is the class preference
``Permission`` (a Znuny group id, what Znuny's ``ItemClassGroupCheck`` reads). Without that
preference Znuny grants nobody access; Tiqora needs a group, so the class gets
:data:`FALLBACK_GROUP_ID` (Znuny's stock ``admin`` group, the most restrictive stock group) and
the report says so (``class_without_group``).

Definitions are imported in ascending version order, each Znuny definition id once (the map kind
``definition`` makes a re-run add nothing; a new Znuny version adds exactly one Tiqora version).
A definition that cannot be read leaves the class without that version; the class itself is
still created and its CIs are skipped later with ``no_definition`` (Task 9).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.classes import CLASS_KEY_RE
from tiqora.cmdb.definition import dump_definition_json
from tiqora.cmdb.errors import CmdbError
from tiqora.db.tiqora.models import TiqoraCmdbClass, TiqoraCmdbClassDefinition
from tiqora.itsm.importer.mapstore import MapEntry, fingerprint
from tiqora.itsm.importer.ports import ZnunyCatalogItem, ZnunyClassDefinition
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.znuny_format.definition import (
    DefinitionParseError,
    definition_format,
    parse_definition_with_issues,
)

STEP = "classes"
DEFINITION_STEP = "definitions"
KIND_CLASS = "class"
KIND_DEFINITION = "definition"
CLASS_CATALOG = "ITSM::ConfigItem::Class"
#: Znuny's stock ``admin`` group; used when a class has no ``Permission`` preference
FALLBACK_GROUP_ID = 2
_KEY_MAX = 50
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)
_ASCII_ONLY = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class DefinitionTarget:
    """One imported definition version: what CI import (Task 9) converts attributes against."""

    tiqora_class_id: int
    definition_id: int
    version: int
    schema: dict[str, Any]
    znuny_definition: list[dict[str, Any]]


@dataclass
class ClassMaps:
    #: Znuny class (``general_catalog.id``) -> Tiqora class id
    class_ids: dict[int, int] = field(default_factory=dict)
    #: Znuny definition id -> its Tiqora counterpart (unreadable definitions are absent)
    definitions: dict[int, DefinitionTarget] = field(default_factory=dict)


def class_key(name: str, znuny_id: int) -> str:
    """Tiqora class key of a Znuny class name (``class_{id}`` when the slug is not a valid key)."""
    slug = _ASCII_ONLY.sub("_", _NON_WORD.sub("_", name.lower()).strip("_")).strip("_")
    slug = slug[:_KEY_MAX].strip("_")
    return slug if CLASS_KEY_RE.match(slug) else f"class_{znuny_id}"[:_KEY_MAX]


def number_prefix(key: str) -> str:
    return key.upper()[:15] + "-"


async def _stored_classes(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    """``{id: class_key}`` of the Tiqora classes that still exist (fake dry-run ids never do)."""
    real = [i for i in ids if i > 0]
    if not real:
        return {}
    rows = await session.execute(
        select(TiqoraCmdbClass.id, TiqoraCmdbClass.class_key).where(TiqoraCmdbClass.id.in_(real))
    )
    return {int(i): str(k) for i, k in rows.all()}


async def _stored_versions(session: AsyncSession, ids: list[int]) -> dict[int, int]:
    """``{definition id: version}`` of the Tiqora definitions that still exist."""
    real = [i for i in ids if i > 0]
    if not real:
        return {}
    rows = await session.execute(
        select(TiqoraCmdbClassDefinition.id, TiqoraCmdbClassDefinition.version).where(
            TiqoraCmdbClassDefinition.id.in_(real)
        )
    )
    return {int(i): int(v) for i, v in rows.all()}


def _schema(attributes: list[Any]) -> dict[str, Any]:
    return {"attributes": json.loads(dump_definition_json(attributes))}


async def run_classes(ctx: StepContext, catalog_ids: dict[str, int]) -> ClassMaps:
    """Import all classes and definition versions; the result feeds the CI passes (Task 9+)."""
    ctx.report.start_step(STEP)
    ctx.report.start_step(DEFINITION_STEP)
    await ctx.check_cancelled()
    maps = ClassMaps()
    async with ctx.session_factory() as session:
        ports = ctx.ports_for(session)
        store = ctx.mapstore_for(session)
        items = sorted(
            (i for i in await ports.source.catalog_items() if i.catalog_class == CLASS_CATALOG),
            key=lambda i: i.id,
        )
        defs_by_class: dict[int, list[ZnunyClassDefinition]] = defaultdict(list)
        for d in await ports.source.class_definitions():
            defs_by_class[d.class_id].append(d)
        for versions in defs_by_class.values():
            versions.sort(key=lambda d: (d.version, d.id))
        total = len(items) + sum(len(v) for v in defs_by_class.values())

        known = await store.get_many(KIND_CLASS, [str(i.id) for i in items])
        stored = await _stored_classes(session, [h.target_id for h in known.values()])
        keys = await _upsert_classes(ctx, session, items, known, stored, maps)
        all_keys = frozenset(keys.values())
        await ctx.progress(len(items), total, STEP)

        done = len(items)
        for item in items:
            await ctx.check_cancelled()
            versions = defs_by_class.get(item.id, [])
            await _import_definitions(ctx, session, item, versions, maps, all_keys)
            done += len(versions)
            await ctx.progress(done, total, STEP)
        if not ctx.dry_run:
            await session.commit()
    return maps


async def _upsert_classes(
    ctx: StepContext,
    session: AsyncSession,
    items: list[ZnunyCatalogItem],
    known: dict[str, MapEntry],
    stored: dict[int, str],
    maps: ClassMaps,
) -> dict[int, str]:
    """Create or update the classes; returns ``{znuny class id: tiqora class key}``."""
    writer = ctx.writer_for(session)
    store = ctx.mapstore_for(session)
    claimed: set[str] = set()
    keys: dict[int, str] = {}
    for item in items:
        hit = known.get(str(item.id))
        if hit is not None and hit.target_id not in stored and hit.target_id > 0:
            hit = None  # the Tiqora class is gone: import it again
        if item.permission_group is None:
            group = FALLBACK_GROUP_ID
            ctx.report.issue(
                STEP,
                "class_without_group",
                f"class:{item.name}",
                f"Klasse {item.name}: keine Berechtigungsgruppe in Znuny, "
                f"Gruppe {FALLBACK_GROUP_ID} (admin) zugeordnet",
            )
        else:
            group = item.permission_group
        # a rename in Znuny keeps the Tiqora key of an already imported class
        key = stored.get(hit.target_id) if hit is not None else None
        if key is None:
            key = class_key(item.name, item.id)
            if key in claimed:
                key = f"{key[: _KEY_MAX - 12]}_{item.id}"
        claimed.add(key)
        keys[item.id] = key
        fp = fingerprint((item, group))
        if hit is not None and hit.fingerprint == fp:
            maps.class_ids[item.id] = hit.target_id
            ctx.report.count(STEP, "unchanged")
            continue
        tid = await writer.upsert_class(
            class_key=key,
            name=item.name[:200],
            group_id=group,
            number_prefix=number_prefix(key),
            user_id=ctx.user_id,
        )
        if ctx.dry_run and hit is not None:
            tid = hit.target_id
        await store.put(KIND_CLASS, str(item.id), tid, fp)
        maps.class_ids[item.id] = tid
        ctx.report.count(STEP, "created" if hit is None else "updated")
    return keys


async def _import_definitions(
    ctx: StepContext,
    session: AsyncSession,
    item: ZnunyCatalogItem,
    versions: list[ZnunyClassDefinition],
    maps: ClassMaps,
    all_keys: frozenset[str],
) -> None:
    writer = ctx.writer_for(session)
    store = ctx.mapstore_for(session)
    class_tid = maps.class_ids[item.id]
    known = await store.get_many(KIND_DEFINITION, [str(d.id) for d in versions])
    existing = await _stored_versions(session, [h.target_id for h in known.values()])
    fake_version = 0
    for d in versions:
        ref = f"class:{item.name}:v{d.version}"
        try:
            parsed = parse_definition_with_issues(d.raw, definition_format(d.raw))
        except DefinitionParseError as exc:
            ctx.report.issue(
                STEP, "definition_unreadable", ref, f"Klasse {item.name} v{d.version}: {exc}"
            )
            ctx.report.count(DEFINITION_STEP, "problems")
            continue
        for issue in parsed.issues:
            ctx.report.issue(STEP, issue.code, f"{ref}:{issue.path}", issue.message)
        if parsed.result.warnings:
            ctx.report.issue(
                STEP,
                "definition_converted_with_issues",
                ref,
                f"Klasse {item.name} v{d.version}: " + "; ".join(parsed.result.warnings),
            )
        schema = _schema(parsed.result.attributes)
        fp = fingerprint(d)
        hit = known.get(str(d.id))
        if (
            hit is not None
            and hit.fingerprint == fp
            and (hit.target_id in existing or (ctx.dry_run and hit.target_id < 0))
        ):
            version = existing.get(hit.target_id)
            if version is None:
                fake_version += 1
                version = fake_version
            maps.definitions[d.id] = DefinitionTarget(
                class_tid, hit.target_id, version, schema, parsed.znuny
            )
            ctx.report.count(DEFINITION_STEP, "unchanged")
            continue
        try:
            def_id = await writer.add_definition(
                class_id=class_tid,
                attributes=parsed.result.attributes,
                comment=f"Import aus Znuny (Version {d.version})",
                created_by=d.created_by,
                created_at=d.created_at,
                make_current=True,
                skip_if_equal=True,
                extra_class_keys=all_keys,
            )
        except CmdbError as exc:
            ctx.report.issue(
                STEP, "definition_unreadable", ref, f"Klasse {item.name} v{d.version}: {exc}"
            )
            ctx.report.count(DEFINITION_STEP, "problems")
            continue
        version = (await _stored_versions(session, [def_id])).get(def_id)
        if version is None:
            fake_version += 1
            version = fake_version
        await store.put(KIND_DEFINITION, str(d.id), def_id, fp)
        maps.definitions[d.id] = DefinitionTarget(class_tid, def_id, version, schema, parsed.znuny)
        ctx.report.count(DEFINITION_STEP, "created" if hit is None else "updated")
