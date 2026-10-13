"""Step ``links``: CI links from Znuny's ``link_relation`` (D5 step 5).

Runs after the CI passes. For each page of Znuny CI ids (the same paging as the CI passes, so a dry
run needs no map listing) the links with one of the CIs at either end are read, deduplicated by
``ZnunyLink.map_key`` (a link seen from both CIs' pages is stored once) and mapped:

* the CI ends become Tiqora CI ids (map kind ``ci``; a CI that is not in the map - deleted in
  Znuny or skipped by pass 1 - gives ``unresolved_reference``),
* ticket and service keys are kept (both live in Znuny's tables with their own ids),
* every other object (``FAQ``, ``ITSMChangeManagement`` ...) is ``unsupported_object``,
* links whose Znuny state is not ``Valid`` are skipped.

The link types come from SysConfig through itsm1's ``sync_link_type_rows`` (``ensure_link_types``,
once per run): the incident propagation direction (``ITSM::Core::IncidentLinkTypeDirection``) is
part of that row, so the importer does not carry a propagation flag of its own. A link whose type
Znuny's SysConfig does not define is reported (``unknown_link_type``), not fatal.

Kind ``link`` of the map holds ``map_key -> 0`` with a fingerprint; a re-run counts every link
``unchanged``. One commit and one cancellation check per page.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.links import LinkInvalid, UnknownLinkType
from tiqora.itsm.importer.mapstore import MapStore, fingerprint
from tiqora.itsm.importer.ports import CmdbWritePort, ZnunyLink
from tiqora.itsm.importer.steps import StepContext, mark_unfinished
from tiqora.itsm.importer.steps.cis import _ci_targets
from tiqora.itsm.importer.steps.classes import ClassMaps

STEP = "links"
KIND_LINK = "link"
PROGRESS_LABEL = "Verknüpfungen werden übernommen …"
ZNUNY_OBJECT_KINDS: Final[dict[str, str]] = {
    "ITSMConfigItem": "ci",
    "Ticket": "ticket",
    "Service": "service",
}
_VALID_STATE = "Valid"
_SHOWN = 80


def _end(link_object: str, key: str, ci_ids: Mapping[str, int]) -> tuple[str, str] | None:
    """``(kind, tiqora key)`` of one link end; None when a CI end is not in the map."""
    kind = ZNUNY_OBJECT_KINDS[link_object]
    if kind != "ci":
        return kind, key.strip()
    tid = ci_ids.get(key.strip())
    return None if tid is None else (kind, str(tid))


async def run_links(ctx: StepContext, maps: ClassMaps | None = None) -> None:
    """Import every valid link that has a CI at one end.

    ``maps`` carries the dry-run CI ids from the CI passes; a real run does not need it.
    """
    ctx.report.start_step(STEP)
    maps = maps or ClassMaps()
    async with ctx.session_factory() as session:
        ports = ctx.ports_for(session)
        writer = ctx.writer_for(session)
        store = ctx.mapstore_for(session)
        await writer.ensure_link_types(user_id=ctx.user_id)
        total = await ports.source.ci_count()
        done = 0
        after = 0
        refused: set[str] = set()  # links not imported: reported once, not once per end
        seen: set[str] = set()  # links handled in this run: counted once, not once per end
        while True:
            ids = await ports.source.ci_ids_page(after, ctx.page_size)
            if not ids:
                break
            after = max(ids)
            await ctx.check_cancelled()
            links = await ports.source.links_for_cis(ids)
            await _links_page(ctx, session, writer, store, maps, links, refused, seen)
            if not ctx.dry_run:
                await session.commit()
            done += len(ids)
            await ctx.progress(done, total, PROGRESS_LABEL)


async def _links_page(
    ctx: StepContext,
    session: AsyncSession,
    writer: CmdbWritePort,
    store: MapStore,
    maps: ClassMaps,
    links: list[ZnunyLink],
    refused: set[str],
    seen: set[str],
) -> None:
    candidates: list[ZnunyLink] = []
    ci_keys: set[str] = set()
    for link in links:
        if link.map_key in refused or link.map_key in seen:
            continue  # seen from the other end's page before
        if link.state_name != _VALID_STATE:
            refused.add(link.map_key)
            ctx.report.count(STEP, "skipped")
            continue
        unsupported = [
            o for o in (link.source_object, link.target_object) if o not in ZNUNY_OBJECT_KINDS
        ]
        if unsupported:
            refused.add(link.map_key)
            ctx.report.issue(
                STEP,
                "unsupported_object",
                link.map_key,
                f"Verknüpfung {link.map_key[:_SHOWN]}: Objekt {unsupported[0][:_SHOWN]} wird "
                "nicht übernommen",
            )
            ctx.report.count(STEP, "skipped")
            continue
        seen.add(link.map_key)
        candidates.append(link)
        for obj, key in (
            (link.source_object, link.source_key),
            (link.target_object, link.target_key),
        ):
            if ZNUNY_OBJECT_KINDS[obj] == "ci":
                ci_keys.add(key.strip())
    ci_ids = await _ci_targets(ctx, session, store, maps, sorted(ci_keys))
    known = await store.get_many(KIND_LINK, [link.map_key for link in candidates])
    for link in candidates:
        source = _end(link.source_object, link.source_key, ci_ids)
        target = _end(link.target_object, link.target_key, ci_ids)
        if source is None or target is None:
            refused.add(link.map_key)
            ctx.report.issue(
                STEP,
                "unresolved_reference",
                link.map_key,
                f"Verknüpfung {link.map_key[:_SHOWN]}: Das Konfigurationselement wurde nicht "
                "übernommen",
            )
            ctx.report.count(STEP, "skipped")
            continue
        fp = fingerprint((link, source, target))
        hit = known.get(link.map_key)
        if hit is not None and hit.fingerprint == fp:
            ctx.report.count(STEP, "unchanged")
            continue
        try:
            created = await writer.add_link(
                source_kind=source[0],
                source_key=source[1],
                target_kind=target[0],
                target_key=target[1],
                link_type=link.type_name,
                created_by=link.created_by,
                created_at=link.created_at,
            )
        except UnknownLinkType:
            refused.add(link.map_key)
            ctx.report.issue(
                STEP,
                "unknown_link_type",
                link.map_key,
                f"Verknüpfung {link.map_key[:_SHOWN]}: Der Verknüpfungstyp "
                f"{link.type_name[:_SHOWN]} ist in Znuny nicht konfiguriert",
            )
            ctx.report.count(STEP, "skipped")
            continue
        except LinkInvalid as exc:
            refused.add(link.map_key)
            ctx.report.issue(
                STEP,
                "bad_value",
                link.map_key,
                f"Verknüpfung {link.map_key[:_SHOWN]}: {str(exc)[:_SHOWN]}",
            )
            ctx.report.count(STEP, "skipped")
            continue
        await store.put(KIND_LINK, link.map_key, 0, fp)
        if not created:
            ctx.report.count(STEP, "unchanged")
        else:
            ctx.report.count(STEP, "created" if hit is None else "updated")
            # a new CI-CI link changes the incident propagation of both ends
            await mark_unfinished(
                ctx, store, {int(end[1]) for end in (source, target) if end[0] == "ci"}
            )
