"""Step ``catalog``: every Znuny general-catalog item becomes a Tiqora catalog item (D5 step 1).

Item keys: Tiqora's catalog rows are keyed by a lower-case slug of the Znuny name (the form of the
rows the migration seeds, ``Production`` -> ``production``, ``Test/QA`` -> ``test_qa``), so an
import adopts the seeded rows instead of failing on the case-insensitive unique key or adding a
second ``Production``. A re-run finds the key of an already imported item through the map, so a
rename in Znuny updates the row instead of creating a new one.
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.importer.mapstore import MapEntry, fingerprint
from tiqora.itsm.importer.ports import ZnunyCatalogItem
from tiqora.itsm.importer.steps import StepContext

STEP = "catalog"
KIND = "catalog"
_KEY_MAX = 100
_POSITION_STEP = 10
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def catalog_key(name: str, znuny_id: int) -> str:
    """Lower-case slug of a Znuny catalog name (``item_{id}`` when nothing is left)."""
    slug = _NON_WORD.sub("_", name.lower()).strip("_")[:_KEY_MAX].strip("_")
    return slug or f"item_{znuny_id}"


async def _stored_key(
    session: AsyncSession, ctx: StepContext, item: ZnunyCatalogItem, tid: int
) -> str | None:
    """Key of the Tiqora row ``tid`` (None when it is gone or the row is a dry-run fake)."""
    if tid <= 0:
        return None
    view = await ctx.ports_for(session).reader.catalog_item(item.catalog_class, tid)
    return view.key if view is not None and view.id == tid else None


async def run_catalog(ctx: StepContext) -> dict[str, int]:
    """Import all catalog items; returns ``{znuny_gc_id: tiqora_catalog_id}`` for the resolver."""
    ctx.report.start_step(STEP)
    await ctx.check_cancelled()
    async with ctx.session_factory() as session:
        ports = ctx.ports_for(session)
        writer = ctx.writer_for(session)
        store = ctx.mapstore_for(session)
        items = sorted(await ports.source.catalog_items(), key=lambda i: i.id)
        known: dict[str, MapEntry] = await store.get_many(KIND, [str(i.id) for i in items])
        mapped_targets: dict[str, set[int]] = defaultdict(set)
        for it in items:
            hit = known.get(str(it.id))
            if hit is not None:
                mapped_targets[it.catalog_class].add(hit.target_id)
        claimed: dict[str, set[str]] = defaultdict(set)  # keys taken in this run, per class
        rank: dict[str, int] = defaultdict(int)
        ids: dict[str, int] = {}
        for item in items:
            rank[item.catalog_class] += 1
            fp = fingerprint(item)
            hit = known.get(str(item.id))
            if hit is not None and hit.fingerprint == fp:
                ids[str(item.id)] = hit.target_id
                ctx.report.count(STEP, "unchanged")
                continue
            key: str | None = None
            if hit is not None:
                key = await _stored_key(session, ctx, item, hit.target_id)
            if key is None:
                key = catalog_key(item.name, item.id)
                existing = await ports.reader.catalog_item(item.catalog_class, key)
                taken = key in claimed[item.catalog_class] or (
                    existing is not None
                    and existing.key.casefold() == key
                    and existing.id in mapped_targets[item.catalog_class]
                    and (hit is None or existing.id != hit.target_id)
                )
                if taken:
                    key = f"{key[: _KEY_MAX - 12]}_{item.id}"
            claimed[item.catalog_class].add(key)
            tid = await writer.upsert_catalog_item(
                catalog_class=item.catalog_class,
                item_key=key,
                name=item.name,
                functionality=item.functionality,
                valid=item.valid,
                position=rank[item.catalog_class] * _POSITION_STEP,
            )
            if ctx.dry_run and hit is not None:
                tid = hit.target_id  # the row stays what it is; only a new item gets a fake id
            await store.put(KIND, str(item.id), tid, fp)
            ids[str(item.id)] = tid
            ctx.report.count(STEP, "created" if hit is None else "updated")
        if not ctx.dry_run:
            await session.commit()
    await ctx.progress(len(items), len(items), STEP)
    return ids
