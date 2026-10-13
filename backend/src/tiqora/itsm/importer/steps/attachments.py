"""Step ``attachments``: CI attachments from Znuny's virtual file system (D5 step 6).

Runs after the CI passes, page by page over the Znuny CI ids. Per page the attachment metadata is
read without content; the bytes are loaded (CI by CI) only for attachments whose fingerprint
(size + name + creation time) differs from the map entry (kind ``attachment``), so a re-run reads
no content at all and counts every attachment ``unchanged``.

* the Znuny CI is not in the map (deleted, or skipped by the CI passes) -> skipped,
* the file lives in the file system backend (no bytes in the database) -> issue
  ``attachment_in_filesystem``,
* the file is larger than :data:`MAX_ATTACHMENT_BYTES` -> issue ``attachment_too_large``.

cmdb's ``add_imported_attachment`` is idempotent per CI on name + SHA-256, so a lost map row
never doubles a file. One commit and one cancellation check per page.
"""

from __future__ import annotations

import hashlib

from tiqora.itsm.importer.mapstore import MapEntry, MapStore, fingerprint
from tiqora.itsm.importer.ports import CmdbWritePort, ZnunyAttachment
from tiqora.itsm.importer.source_extra import MAX_ATTACHMENT_BYTES
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.steps.cis import _ci_targets
from tiqora.itsm.importer.steps.classes import ClassMaps

STEP = "attachments"
KIND_ATTACHMENT = "attachment"
PROGRESS_LABEL = "Anhänge werden übernommen …"
_SHOWN = 80
_MB = 1024 * 1024


def map_key(att: ZnunyAttachment) -> str:
    """Stable map key; the file name is hashed (it is Znuny-controlled and may be long)."""
    return f"{att.ci_id}:{hashlib.sha256(att.filename.encode()).hexdigest()[:32]}"


def _label(att: ZnunyAttachment) -> str:
    return f"CI {att.ci_id}, {att.filename[:_SHOWN]}"


async def run_attachments(ctx: StepContext, maps: ClassMaps | None = None) -> None:
    """Import every database-stored attachment of every imported CI.

    ``maps`` carries the dry-run CI ids from the CI passes; a real run does not need it.
    """
    ctx.report.start_step(STEP)
    maps = maps or ClassMaps()
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
            listed = await ports.source.attachments(ids, with_data=False)
            ci_ids = await _ci_targets(
                ctx, session, store, maps, sorted({str(a.ci_id) for a in listed})
            )
            known = await store.get_many(KIND_ATTACHMENT, [map_key(a) for a in listed])
            changed: list[tuple[ZnunyAttachment, str]] = []
            for att in listed:
                if str(att.ci_id) not in ci_ids:
                    ctx.report.count(STEP, "skipped")
                    continue
                fp = fingerprint((att.size, att.filename, att.created_at))
                hit = known.get(map_key(att))
                if hit is not None and hit.fingerprint == fp:
                    ctx.report.count(STEP, "unchanged")
                elif att.size > MAX_ATTACHMENT_BYTES:
                    ctx.report.issue(
                        STEP,
                        "attachment_too_large",
                        _label(att),
                        f"Anhang {_label(att)}: {-(-att.size // _MB)} MB überschreiten die "
                        f"Grenze von {MAX_ATTACHMENT_BYTES // _MB} MB",
                    )
                    ctx.report.count(STEP, "skipped")
                else:
                    changed.append((att, fp))
            # content only for the CIs that have something to import, one CI at a time
            for ci in sorted({a.ci_id for a, _ in changed}):
                wanted = {(a.filename, fp) for a, fp in changed if a.ci_id == ci}
                full = {
                    a.filename: a
                    for a in await ports.source.attachments([ci], with_data=True)
                    if a.ci_id == ci
                }
                for name, fp in sorted(wanted):
                    current = full.get(name)
                    if current is None:
                        continue  # removed in Znuny while the step ran
                    await _import_one(ctx, writer, store, known, ci_ids[str(ci)], current, fp)
            if not ctx.dry_run:
                await session.commit()
            done += len(ids)
            await ctx.progress(done, total, PROGRESS_LABEL)


async def _import_one(
    ctx: StepContext,
    writer: CmdbWritePort,
    store: MapStore,
    known: dict[str, MapEntry],
    tiqora_ci: int,
    att: ZnunyAttachment,
    fp: str,
) -> None:
    if att.data is None:
        ctx.report.issue(
            STEP,
            "attachment_in_filesystem",
            _label(att),
            f"Anhang {_label(att)}: liegt im Dateisystem von Znuny und wurde nicht übernommen",
        )
        ctx.report.count(STEP, "skipped")
        return
    attachment_id = await writer.add_imported_attachment(
        tiqora_ci,
        filename=att.filename,
        content_type=att.content_type,
        data=att.data,
        created_at=att.created_at,
        created_by=ctx.user_id,
    )
    key = map_key(att)
    await store.put(KIND_ATTACHMENT, key, attachment_id, fp)
    ctx.report.count(STEP, "created" if key not in known else "updated")
