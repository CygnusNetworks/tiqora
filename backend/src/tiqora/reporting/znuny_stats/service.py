"""Preview and apply the import of Znuny statistics (plan D7, Task 5).

Read-only towards Znuny (``xml_storage``); writes only ``tiqora_report`` / ``tiqora_saved_search``
rows of the importing agent. Bounded inline work (A9): at most ``MAX_PREVIEW_STATS`` stats, the
reader's row budget, pure mapping, two IN-list queries for "already imported". No JOIN between
``tiqora_*`` and Znuny tables.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraReport, TiqoraSavedSearch
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.reporting.engine import QueryBudget
from tiqora.reporting.rights import ReportRights
from tiqora.reporting.run_service import RequestError
from tiqora.reporting.saved import (
    SAVE_RIGHT_MESSAGE,
    ReportForbidden,
    SavedReportIn,
    SavedReportUpdateIn,
    create_saved_report,
    get_saved_report,
    update_saved_report,
)
from tiqora.reporting.znuny_stats.mapper import map_stat
from tiqora.reporting.znuny_stats.mapping_common import ImportIssue, StatMapping
from tiqora.reporting.znuny_stats.model import ZnunyStat, stat_from_xml_hash
from tiqora.reporting.znuny_stats.reader import (
    StatLoadError,
    list_stat_ids_page,
    load_stat_lookups,
    load_stats,
)
from tiqora.reporting.znuny_stats.schemas import (
    ImportedStatOut,
    ImportIssueOut,
    StatLoadErrorOut,
    StatPreviewOut,
    ZnunyStatsApplyIn,
    ZnunyStatsApplyOut,
    ZnunyStatsPreviewOut,
)
from tiqora.reporting.znuny_stats.xml_hash import MAX_FILE_BYTES, XmlHashError, xml_file_to_hash

__all__ = ["apply_import", "decode_file", "preview_db", "preview_file", "visible_stat_ids"]

_CHUNK = 200
_UNREADABLE_FILE = "Die Datei konnte nicht gelesen werden."
_NOT_FOUND = "Die Statistik wurde nicht gefunden oder ist nicht sichtbar."
_NO_IMPORT = "Diese Statistik lässt sich nicht übernehmen."

_OBJECT_LABELS = {
    "Kernel::System::Stats::Dynamic::Ticket": "Ticket-Matrix",
    "Kernel::System::Stats::Dynamic::TicketList": "Ticketliste",
    "Kernel::System::Stats::Dynamic::TicketAccountedTime": "Zeiterfassung",
    "Kernel::System::Stats::Dynamic::TicketSolutionResponseTime": "Lösungs- und Reaktionszeit",
}


def _object_label(stat: ZnunyStat) -> str:
    if stat.stat_type == "static" and stat.static_file:
        return f"Statisch: {stat.static_file}"
    return _OBJECT_LABELS.get(stat.object_module, stat.object_module)


def _issue_out(issue: ImportIssue) -> ImportIssueOut:
    return ImportIssueOut(
        element=issue.element, value=issue.value, reason=issue.reason, severity=issue.severity
    )


def _chunks(values: Sequence[str]) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), _CHUNK):
        yield values[start : start + _CHUNK]


def _permission_ids(stat: ZnunyStat) -> set[int]:
    return {int(p) for p in stat.permission if p.isascii() and p.isdigit()}


async def _is_admin(session: AsyncSession, user_id: int) -> bool:
    return await PermissionEngine(session).is_admin(user_id)


async def visible_stat_ids(
    session: AsyncSession, user_id: int, *, admin: bool, stats: Sequence[ZnunyStat]
) -> set[int]:
    """Admins see every stat; others those whose Permission groups meet their ``ro`` groups.

    Znuny lets the stat's Permission groups decide who may run it; a stat without any
    Permission is therefore for admins only.
    """
    ids = {s.stat_id for s in stats if s.stat_id is not None}
    if admin:
        return ids
    groups = await PermissionEngine(session).groups_for_permission(user_id, "ro")
    return {s.stat_id for s in stats if s.stat_id is not None and _permission_ids(s) & groups}


@dataclass(frozen=True, slots=True)
class _Existing:
    reports: dict[str, int]
    searches: dict[str, int]


async def _existing(session: AsyncSession, user_id: int, keys: Sequence[str]) -> _Existing:
    reports: dict[str, int] = {}
    searches: dict[str, int] = {}
    for chunk in _chunks(list(keys)):
        rows = await session.execute(
            select(TiqoraReport.copied_from, TiqoraReport.id)
            .where(
                TiqoraReport.owner_user_id == user_id,
                TiqoraReport.valid.is_(True),
                TiqoraReport.copied_from.in_(chunk),
            )
            .order_by(TiqoraReport.id.desc())
        )
        for key, rid in rows.all():
            reports[str(key)] = int(rid)  # descending order: the oldest copy wins
        rows = await session.execute(
            select(TiqoraSavedSearch.source_name, TiqoraSavedSearch.id).where(
                TiqoraSavedSearch.owner_user_id == user_id,
                TiqoraSavedSearch.source == "znuny",
                TiqoraSavedSearch.source_name.in_(chunk),
            )
        )
        for key, sid in rows.all():
            searches[str(key)] = int(sid)
    return _Existing(reports, searches)


def _preview(mapping: StatMapping, existing: _Existing) -> StatPreviewOut:
    stat = mapping.stat
    key = stat.import_key
    already: str | None = None
    existing_id: int | None = None
    if mapping.target == "report" and key in existing.reports:
        already, existing_id = "report", existing.reports[key]
    elif mapping.target == "saved_search" and key in existing.searches:
        already, existing_id = "saved_search", existing.searches[key]
    return StatPreviewOut(
        import_key=key,
        stat_id=stat.stat_id,
        stat_number=stat.stat_number,
        title=stat.title,
        description=stat.description,
        object_label=_object_label(stat),
        target=mapping.target,
        issues=[_issue_out(i) for i in mapping.issues],
        definition=mapping.definition,
        already_imported=already,  # type: ignore[arg-type]
        existing_id=existing_id,
    )


def _skipped(stat_id: int | None, import_key: str, message: str, title: str = "") -> StatPreviewOut:
    return StatPreviewOut(
        import_key=import_key,
        stat_id=stat_id,
        stat_number=None,
        title=title,
        description="",
        object_label="",
        target="none",
        issues=[ImportIssueOut(element="Stat", value="", reason=message, severity="dropped")],
        definition=None,
        already_imported=None,
        existing_id=None,
    )


def _sort_key(mapping: StatMapping) -> tuple[bool, str, str]:
    stat = mapping.stat
    return (stat.stat_number is None, stat.stat_number or "", stat.title.casefold())


async def _map_all(
    session: AsyncSession, user_id: int, stats: Sequence[ZnunyStat], *, admin: bool
) -> list[StatMapping]:
    if not stats:
        return []
    lookups = await load_stat_lookups(session, user_id)
    return sorted((map_stat(s, lookups, is_admin=admin) for s in stats), key=_sort_key)


def _errors_out(errors: Sequence[StatLoadError], *, admin: bool) -> list[StatLoadErrorOut]:
    if admin:
        return [StatLoadErrorOut(stat_id=e.stat_id, message=e.message) for e in errors]
    if not errors:
        return []
    n = len(errors)
    message = (
        "Eine Statistik konnte nicht gelesen werden."
        if n == 1
        else f"{n} Statistiken konnten nicht gelesen werden."
    )
    return [StatLoadErrorOut(stat_id=None, message=message)]


async def preview_db(
    session: AsyncSession, user_id: int, *, budget: QueryBudget | None = None
) -> ZnunyStatsPreviewOut:
    admin = await _is_admin(session, user_id)
    groups = None if admin else await PermissionEngine(session).groups_for_permission(user_id, "ro")
    ids, more = await list_stat_ids_page(session, permission_group_ids=groups, budget=budget)
    stats, errors, truncated = await load_stats(session, ids, budget=budget)
    visible = await visible_stat_ids(session, user_id, admin=admin, stats=stats)
    stats = [s for s in stats if s.stat_id in visible]
    mappings = await _map_all(session, user_id, stats, admin=admin)
    existing = await _existing(session, user_id, [m.stat.import_key for m in mappings])
    return ZnunyStatsPreviewOut(
        stats=[_preview(m, existing) for m in mappings],
        errors=_errors_out(errors, admin=admin),
        truncated=more or truncated,
    )


def decode_file(content_base64: str, path: str) -> bytes:
    """Base64 -> bytes with the size check before any parsing; RequestError (422) otherwise."""
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise RequestError(_UNREADABLE_FILE, path=path) from None
    if len(raw) > MAX_FILE_BYTES:
        raise RequestError(
            f"Die Datei ist zu groß (höchstens {MAX_FILE_BYTES // 1_000_000} MB).", path=path
        )
    return raw


def _parse_file(raw: bytes, path: str) -> ZnunyStat:
    try:
        return stat_from_xml_hash(xml_file_to_hash(raw), origin="file", stat_id=None)
    except XmlHashError as exc:
        raise RequestError(exc.message, path=path) from None


async def preview_file(
    session: AsyncSession, user_id: int, content: bytes, *, path: str = "content_base64"
) -> ZnunyStatsPreviewOut:
    """Parse and map one export file; saves nothing. Raises RequestError (422) on bad files."""
    stat = _parse_file(content, path)
    admin = await _is_admin(session, user_id)
    mappings = await _map_all(session, user_id, [stat], admin=admin)
    existing = await _existing(session, user_id, [m.stat.import_key for m in mappings])
    return ZnunyStatsPreviewOut(
        stats=[_preview(m, existing) for m in mappings], errors=[], truncated=False
    )


async def _store_report(
    session: AsyncSession,
    user_id: int,
    rights: ReportRights,
    mapping: StatMapping,
    existing_id: int | None,
    *,
    registry: Registry,
) -> tuple[int, str]:
    stat = mapping.stat
    key = stat.import_key
    assert mapping.definition is not None
    name = stat.title.strip()[:200] or key
    description = stat.description[:2000]
    if existing_id is not None:
        # update path: sharing stays as the importer set it (A10); only the content changes
        current = await get_saved_report(session, existing_id, user_id, admin=rights.admin)
        saved = await update_saved_report(
            session,
            existing_id,
            user_id,
            rights,
            SavedReportUpdateIn(
                name=name,
                description=description,
                definition=mapping.definition,
                visibility=current.visibility,
                group_ids=list(current.group_ids),
                copied_from=key,
                version=current.version,
            ),
            registry=registry,
        )
        return saved.id, "updated"
    created = await create_saved_report(
        session,
        user_id,
        rights,
        SavedReportIn(
            name=name,
            description=description,
            definition=mapping.definition,
            visibility="private",
            copied_from=key,
        ),
        registry=registry,
    )
    return created.id, "created"


async def _store_search(
    session: AsyncSession, user_id: int, mapping: StatMapping
) -> tuple[int, str]:
    from tiqora.saved_searches.store import upsert_imported

    stat = mapping.stat
    search = mapping.search
    assert search is not None
    row_id, action = await upsert_imported(
        session,
        user_id,
        source_name=stat.import_key,
        criteria=search.criteria,
        include_archived=search.include_archived,
    )
    if action == "created":  # a re-import must not undo the user's renaming or sorting
        await session.execute(
            update(TiqoraSavedSearch)
            .where(TiqoraSavedSearch.id == row_id)
            .values(
                name=stat.title.strip()[:200] or stat.import_key,
                sort_by=search.sort_by,
                sort_order=search.sort_order,
                changed_at=utc_now(),
            )
        )
    return row_id, action


async def apply_import(
    session: AsyncSession,
    user_id: int,
    rights: ReportRights,
    body: ZnunyStatsApplyIn,
    *,
    registry: Registry,
    budget: QueryBudget | None = None,
) -> ZnunyStatsApplyOut:
    """Store the selected statistics as private reports / saved searches (one commit)."""
    if not rights.create:
        raise ReportForbidden(SAVE_RIGHT_MESSAGE)
    has_file = body.file_content_base64 is not None
    if has_file == bool(body.stat_ids):
        raise RequestError(
            "Bitte entweder Statistiken aus Znuny oder eine Datei angeben.", path="stat_ids"
        )
    admin = rights.admin
    skipped: list[StatPreviewOut] = []
    stats: list[ZnunyStat]
    if body.file_content_base64 is not None:
        raw = decode_file(body.file_content_base64, "file_content_base64")
        stats = [_parse_file(raw, "file_content_base64")]
    else:
        wanted = sorted(set(body.stat_ids))
        loaded, errors, _truncated = await load_stats(session, wanted, budget=budget)
        visible = await visible_stat_ids(session, user_id, admin=admin, stats=loaded)
        stats = [s for s in loaded if s.stat_id in visible]
        messages = {e.stat_id: e.message if admin else _NOT_FOUND for e in errors}
        for stat_id in wanted:
            if stat_id in visible:
                continue
            skipped.append(
                _skipped(stat_id, f"znuny.stat.{stat_id}", messages.get(stat_id, _NOT_FOUND))
            )
    mappings = await _map_all(session, user_id, stats, admin=admin)
    existing = await _existing(session, user_id, [m.stat.import_key for m in mappings])
    imported: list[ImportedStatOut] = []
    try:
        for mapping in mappings:
            if mapping.target == "none":
                skipped.append(_preview(mapping, existing))
                continue
            key = mapping.stat.import_key
            if mapping.target == "report":
                row_id, action = await _store_report(
                    session, user_id, rights, mapping, existing.reports.get(key), registry=registry
                )
            else:
                row_id, action = await _store_search(session, user_id, mapping)
            imported.append(
                ImportedStatOut(
                    import_key=key,
                    title=mapping.stat.title,
                    target=mapping.target,
                    id=row_id,
                    action=action,  # type: ignore[arg-type]
                    issues=[_issue_out(i) for i in mapping.issues],
                )
            )
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return ZnunyStatsApplyOut(imported=imported, skipped=skipped)
