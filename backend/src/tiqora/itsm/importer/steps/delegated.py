"""Small steps of the pipeline (D5 steps 6-10): priority matrix, service meta, change archive,
CIs deleted in Znuny and the closing incident-state / search-index pass.

``priority_matrix`` and ``service_meta`` copy ITSMCore data (``cip_allocate`` and the ITSM columns
of ``service``) through itsm1's import functions (facades); they work without the CMDB package. A
dry run only counts the source rows (``skipped``). ``changes`` archives Znuny's changes with their
work orders read-only into Tiqora (``tiqora.itsm.change.legacy.archive``; created / updated /
unchanged per change) and reports the templates, conditions and notification rules it leaves in
Znuny (``not_archived``); under a changed tombstone key it archives nothing and reports
``archive_key_changed``. ``deleted_in_znuny`` only reports: a CI that was
imported and is gone from Znuny stays in Tiqora. ``incident_states`` runs cmdb's ``finish_import``
(search index, class counters, derived incident states) for every CI marked unfinished (map kind
``finish``): new versions or new CI links of this run or of an earlier run that ended early.
"""

from __future__ import annotations

from sqlalchemy import select

from tiqora.db.tiqora.models import TiqoraCmdbCi
from tiqora.itsm.change.legacy.archive import ArchiveReport, ErasedKeyChanged, archive_changes
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.mapstore import fingerprint
from tiqora.itsm.importer.steps import KIND_FINISH, StepContext
from tiqora.itsm.znuny_adapter.catalog import chunks

STEP_MATRIX = "priority_matrix"
STEP_SERVICE_META = "service_meta"
STEP_CHANGES = "changes"
STEP_DELETED = "deleted_in_znuny"
STEP_INCIDENT = "incident_states"
KIND_MATRIX = "priority_matrix"
KIND_SERVICE_META = "service_meta"
KIND_CI = "ci"
FINISH_BATCH = 500
_SHOWN = 80
_NOT_ARCHIVED_LABELS = {
    "templates": "Change-Vorlagen",
    "conditions": "Bedingungen",
    "notifications": "Benachrichtigungsregeln",
}


async def run_priority_matrix(ctx: StepContext) -> None:
    """Copy ``cip_allocate`` into Tiqora's matrix (all cells at once, as itsm1 stores it)."""
    ctx.report.start_step(STEP_MATRIX)
    await ctx.check_cancelled()
    async with ctx.session_factory() as session:
        rows = await ctx.ports_for(session).source.cip_rows()
        if ctx.dry_run or not rows:
            ctx.report.count(STEP_MATRIX, "skipped", len(rows))
            return
        store = ctx.mapstore_for(session)
        fp = fingerprint(rows)
        hit = await store.get(KIND_MATRIX, "0")
        if hit is not None and hit.fingerprint == fp:
            ctx.report.count(STEP_MATRIX, "unchanged", len(rows))
            return
        try:
            cells = await facades.import_priority_matrix(session, rows, user_id=ctx.user_id)
        except facades.InvalidMatrix as exc:
            await session.rollback()
            ctx.report.issue(
                STEP_MATRIX,
                "priority_matrix_invalid",
                "cip_allocate",
                f"Prioritätsmatrix: {str(exc)[:_SHOWN]} ({exc.reason})",
            )
            ctx.report.count(STEP_MATRIX, "problems")
            return
        await store.put(KIND_MATRIX, "0", 0, fp)
        ctx.report.count(STEP_MATRIX, "created" if hit is None else "updated", cells)
        await session.commit()
    await ctx.progress(1, 1, STEP_MATRIX)


async def run_service_meta(ctx: StepContext) -> None:
    """Copy the ITSM columns (type, criticality) of every Znuny service."""
    ctx.report.start_step(STEP_SERVICE_META)
    await ctx.check_cancelled()
    async with ctx.session_factory() as session:
        rows = await ctx.ports_for(session).source.service_itsm_rows()
        if ctx.dry_run:
            ctx.report.count(STEP_SERVICE_META, "skipped", len(rows))
            return
        store = ctx.mapstore_for(session)
        known = await store.get_many(KIND_SERVICE_META, [str(r.service_id) for r in rows])
        for row in rows:
            if not row.type_name and not row.criticality:
                ctx.report.count(STEP_SERVICE_META, "skipped")  # nothing ITSM about it
                continue
            fp = fingerprint(row)
            hit = known.get(str(row.service_id))
            if hit is not None and hit.fingerprint == fp:
                ctx.report.count(STEP_SERVICE_META, "unchanged")
                continue
            try:
                async with session.begin_nested():
                    await facades.import_service_meta(session, row, user_id=ctx.user_id)
            except facades.ServiceNotFound:
                ctx.report.issue(
                    STEP_SERVICE_META,
                    "service_unknown",
                    f"service:{row.service_id}",
                    f"Service {row.service_id}: nicht gefunden, die ITSM-Angaben wurden nicht "
                    "übernommen",
                )
                ctx.report.count(STEP_SERVICE_META, "problems")
                continue
            await store.put(KIND_SERVICE_META, str(row.service_id), row.service_id, fp)
            ctx.report.count(STEP_SERVICE_META, "created" if hit is None else "updated")
        await session.commit()
    await ctx.progress(1, 1, STEP_SERVICE_META)


async def run_changes(ctx: StepContext) -> None:
    """Archive the Znuny changes (read-only in Tiqora); a Znuny without Change Management counts
    nothing."""
    ctx.report.start_step(STEP_CHANGES)
    await ctx.check_cancelled()
    try:
        archived = await archive_changes(
            ctx.session_factory,
            run_id=ctx.run_id,
            dry_run=ctx.dry_run,
            page_size=ctx.page_size,
            progress=ctx.progress,
            check_cancelled=ctx.check_cancelled,
            report=ArchiveReport(),
            settings=ctx.settings,
        )
    except ErasedKeyChanged:
        # nothing more is written (a run under way keeps its finished pages); the other steps
        # of the run go on
        ctx.report.issue(
            STEP_CHANGES,
            "archive_key_changed",
            "TIQORA_ITSM_LEGACY_ERASED_KEY",
            "Change-Archiv nicht aktualisiert: der Schlüssel für gelöschte Kunden hat sich "
            "geändert. Bisheriger Schlüssel vorhanden und seit dem Wechsel keine Löschung: "
            "setzen Sie TIQORA_ITSM_LEGACY_ERASED_KEY auf den bisherigen TIQORA_SECRET_KEY. "
            "Sonst sind keine weiteren Archiv-Läufe möglich; das Archiv bleibt lesbar",
        )
        ctx.report.count(STEP_CHANGES, "problems")
        await ctx.progress(1, 1, STEP_CHANGES)
        return
    for name in ("created", "updated", "unchanged", "skipped"):
        ctx.report.count(STEP_CHANGES, name, getattr(archived, name))
    for kind in sorted(archived.not_archived):
        label = _NOT_ARCHIVED_LABELS.get(kind, kind[:_SHOWN])
        ctx.report.issue(
            STEP_CHANGES,
            "not_archived",
            kind,
            f"{archived.not_archived[kind]} {label} in Znuny werden nicht übernommen; sie wurden "
            "nur gezählt",
        )
    await ctx.progress(1, 1, STEP_CHANGES)


async def run_deleted_in_znuny(ctx: StepContext) -> None:
    """Report imported CIs that no longer exist in Znuny; they stay in Tiqora."""
    ctx.report.start_step(STEP_DELETED)
    await ctx.check_cancelled()
    async with ctx.session_factory() as session:
        ports = ctx.ports_for(session)
        store = ctx.mapstore_for(session)
        mapped = [int(i) for i in await store.all_source_ids(KIND_CI) if i.isdigit()]
        gone: list[int] = []
        for part in chunks(sorted(mapped), FINISH_BATCH):
            present = {c.id for c in await ports.source.cis(list(part))}
            missing = [i for i in part if i not in present]
            gone.extend(missing)
            ctx.report.count(STEP_DELETED, "unchanged", len(part) - len(missing))
        entries = await store.get_many(KIND_CI, [str(i) for i in gone])
        targets = [e.target_id for e in entries.values() if e.target_id > 0]
        numbers: dict[int, str] = {}
        for part in chunks(sorted(set(targets))):
            rows = await session.execute(
                select(TiqoraCmdbCi.id, TiqoraCmdbCi.number).where(TiqoraCmdbCi.id.in_(part))
            )
            numbers.update({int(i): str(n) for i, n in rows.all()})
        for znuny_id in gone:
            entry = entries.get(str(znuny_id))
            number = None if entry is None else numbers.get(entry.target_id)
            if number is None:
                continue  # also gone from Tiqora: nothing to tell
            ctx.report.issue(
                STEP_DELETED,
                "deleted_in_znuny",
                number,
                f"Konfigurationselement {number[:_SHOWN]} (Znuny-ID {znuny_id}) existiert in "
                "Znuny nicht mehr; es bleibt in Tiqora unverändert bestehen",
            )
            ctx.report.count(STEP_DELETED, "problems")
    await ctx.progress(1, 1, STEP_DELETED)


async def run_incident_states(ctx: StepContext) -> None:
    """Finish the import for every unfinished CI: index, counters, derived states.

    Unfinished are the CIs of this run's new versions and new CI links, and those an earlier run
    left behind when it ended before this step (map kind ``finish``). Each batch is finished and
    unmarked in one transaction.
    """
    ctx.report.start_step(STEP_INCIDENT)
    if ctx.dry_run:
        await ctx.progress(0, 0, STEP_INCIDENT)
        return
    async with ctx.session_factory() as session:
        marked = await ctx.mapstore_for(session).all_source_ids(KIND_FINISH)
    pending = sorted(ctx.touched_ci_ids | {int(i) for i in marked if i.isdigit()})
    done = 0
    for part in chunks(pending, FINISH_BATCH):
        await ctx.check_cancelled()
        async with ctx.session_factory() as session:
            changed = await ctx.writer_for(session).finish_import(list(part))
            await ctx.mapstore_for(session).delete_many(KIND_FINISH, [str(i) for i in part])
            await session.commit()
        ctx.report.count(STEP_INCIDENT, "updated", changed)
        ctx.report.count(STEP_INCIDENT, "unchanged", len(part) - changed)
        done += len(part)
        await ctx.progress(done, len(pending), STEP_INCIDENT)
    await ctx.progress(len(pending), len(pending), STEP_INCIDENT)
