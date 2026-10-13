"""The import pipeline: runs the steps of D5 in order and returns the report.

``run_import`` is what the import job calls. Every step opens its own sessions from the factory
and commits per page (a dry run writes nothing), so a run can be cancelled between steps and
between pages and be repeated: the map store makes each step idempotent. When the Znuny source has
no CMDB package (ITSMCore only) the class and CI steps appear in the report with zero counts; the
catalog, the priority matrix and the service meta are still imported, so such sites can switch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.itsm.importer import facades
from tiqora.itsm.importer.facades import CmdbServiceWriter
from tiqora.itsm.importer.mapstore import MapStore
from tiqora.itsm.importer.ports import CmdbWritePort, Ports
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.steps.attachments import run_attachments
from tiqora.itsm.importer.steps.catalog import run_catalog
from tiqora.itsm.importer.steps.cis import run_cis_pass1, run_cis_pass2
from tiqora.itsm.importer.steps.classes import DEFINITION_STEP, ClassMaps, run_classes
from tiqora.itsm.importer.steps.delegated import (
    run_changes,
    run_deleted_in_znuny,
    run_incident_states,
    run_priority_matrix,
    run_service_meta,
)
from tiqora.itsm.importer.steps.links import run_links
from tiqora.itsm.importer.writer import DryRunWriter

STEP_ORDER: tuple[str, ...] = (
    "catalog",
    "classes",
    "cis_pass1",
    "cis_pass2",
    "links",
    "attachments",
    "priority_matrix",
    "service_meta",
    "changes",
    "deleted_in_znuny",
    "incident_states",
)

#: ``report.steps`` holds ``DEFINITION_STEP`` (class definition counts) right after "classes",
#: besides the entries of ``STEP_ORDER``
_NOT_PRESENT = "In der Znuny-Datenbank wurde kein ITSM gefunden."
_UNSUPPORTED = "Die installierte Znuny-ITSM-Version wird vom Import nicht unterstützt."


class ImportBlocked(Exception):
    """The import cannot start; the message is shown to the admin (German)."""


async def run_import(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: int,
    user_id: int,
    dry_run: bool,
    page_size: int,
    progress: Callable[[int, int | None, str], Awaitable[None]],
    check_cancelled: Callable[[], Awaitable[None]],
) -> ImportReport:
    """Run all steps; raises :class:`ImportBlocked` when Znuny's ITSM is missing or unsupported."""
    dry_writer = DryRunWriter()

    def writer_for(session: AsyncSession) -> CmdbWritePort:
        return dry_writer if dry_run else CmdbServiceWriter(session)

    def ports_for(session: AsyncSession) -> Ports:
        base = facades.get_ports(session)
        return Ports(
            source=base.source, writer=writer_for(session), reader=base.reader, mode=base.mode
        )

    async with session_factory() as probe:
        source = ports_for(probe).source
        info = await source.info()
        if await source.profile() is None:
            raise ImportBlocked(_UNSUPPORTED if info.core_installed else _NOT_PRESENT)
    ctx = StepContext(
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
    for name in STEP_ORDER:  # every step shows up, in order, even when it counts nothing
        ctx.report.start_step(name)
        if name == "classes":
            ctx.report.start_step(DEFINITION_STEP)  # counter group of the classes step

    catalog_ids = await run_catalog(ctx)
    await check_cancelled()
    maps = ClassMaps()
    if info.cmdb_installed:
        maps = await run_classes(ctx, catalog_ids)
        await check_cancelled()
        await run_cis_pass1(ctx, maps, catalog_ids)
        await check_cancelled()
        await run_cis_pass2(ctx, maps, catalog_ids)
        await check_cancelled()
        await run_links(ctx, maps)
        await check_cancelled()
        await run_attachments(ctx, maps)
        await check_cancelled()
    for step in (run_priority_matrix, run_service_meta, run_changes):
        await step(ctx)
        await check_cancelled()
    if info.cmdb_installed:
        await run_deleted_in_znuny(ctx)
        await check_cancelled()
    await run_incident_states(ctx)
    return ctx.report
