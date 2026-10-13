"""Saved-report store: visibility, create / update / delete, report-key resolution.

Visibility: the owner, ``all``, or ``groups`` shared with a group the viewer holds ``ro`` on.
Admins may open any saved report **by id** (intended, spec §3 "Admin can see all"); their
default list still hides other people's private reports (``include_others_private``).
An invisible report is indistinguishable from an unknown one (``ReportNotFound``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraReport, TiqoraReportGroup
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import ShareGroupError, check_share_groups
from tiqora.reporting.catalogue import BuiltinReport, active_builtin, is_catalogue_key
from tiqora.reporting.definition import DefinitionError, ReportDefinition, validate_definition
from tiqora.reporting.rights import ReportRights

Visibility = Literal["private", "groups", "all"]
#: ``report_key`` of a saved report is its decimal id
SAVED_KEY_RE: Final = re.compile(r"[1-9][0-9]{0,17}")
MAX_SAVED_REPORTS_PER_USER: Final = 500
DELETED_REPORT_RETENTION_DAYS: Final = 90
BROKEN_DEFINITION_MESSAGE: Final = "Der gespeicherte Bericht kann nicht mehr gelesen werden."
SAVE_RIGHT_MESSAGE: Final = "Für das Speichern von Berichten fehlt die Berechtigung."
SHARE_RIGHT_MESSAGE: Final = "Für das Teilen von Berichten fehlt die Berechtigung."
LIMIT_MESSAGE: Final = (
    f"Es sind höchstens {MAX_SAVED_REPORTS_PER_USER} Berichte pro Person möglich."
)


class SavedReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)  # stripped; blank after strip -> 422
    description: str = Field("", max_length=2000)
    definition: ReportDefinition
    visibility: Visibility = "private"
    group_ids: list[int] = Field(default_factory=list, max_length=50)
    copied_from: str | None = Field(None, max_length=120)


class SavedReportUpdateIn(SavedReportIn):
    """PUT is a full replace (every field is written); ``version`` is the optimistic lock."""

    version: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class SavedReport:
    id: int
    name: str
    description: str
    owner_user_id: int
    visibility: Visibility
    group_ids: tuple[int, ...]
    definition: ReportDefinition | None
    definition_error: str | None
    version: int
    valid: bool
    copied_from: str | None
    created_at: datetime
    changed_at: datetime


class ReportNotFound(LookupError):
    """Unknown id, or not visible to the viewer."""


class ReportGone(LookupError):
    """Deleted (``valid = false``) and was visible to the viewer."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


class ReportForbidden(PermissionError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReportVersionConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__(current_version)
        self.current_version = current_version


def load_definition(raw: str) -> tuple[ReportDefinition | None, str | None]:
    """Parse a stored definition (the single place where a later schema upgrade goes)."""
    try:
        return ReportDefinition.model_validate_json(raw), None
    except ValidationError:
        return None, BROKEN_DEFINITION_MESSAGE


def dump_definition(defn: ReportDefinition) -> str:
    return json.dumps(
        defn.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True
    )


def _to_saved(row: TiqoraReport, group_ids: tuple[int, ...]) -> SavedReport:
    defn, err = load_definition(row.definition)
    return SavedReport(
        id=int(row.id),
        name=row.name,
        description=row.description or "",
        owner_user_id=int(row.owner_user_id),
        visibility=row.visibility,  # type: ignore[arg-type]
        group_ids=group_ids,
        definition=defn,
        definition_error=err,
        version=int(row.version),
        valid=bool(row.valid),
        copied_from=row.copied_from,
        created_at=row.created_at,
        changed_at=row.changed_at,
    )


async def _groups_of(session: AsyncSession, report_ids: list[int]) -> dict[int, tuple[int, ...]]:
    if not report_ids:
        return {}
    rows = await session.execute(
        select(TiqoraReportGroup.report_id, TiqoraReportGroup.group_id).where(
            TiqoraReportGroup.report_id.in_(report_ids)
        )
    )
    out: dict[int, list[int]] = {}
    for rid, gid in rows.all():
        out.setdefault(int(rid), []).append(int(gid))
    return {rid: tuple(sorted(g)) for rid, g in out.items()}


async def _load(session: AsyncSession, report_id: int) -> SavedReport | None:
    row = (
        await session.execute(
            select(TiqoraReport)
            .where(TiqoraReport.id == report_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    groups = await _groups_of(session, [report_id])
    return _to_saved(row, groups.get(report_id, ()))


async def visible_saved_reports(
    session: AsyncSession,
    user_id: int,
    *,
    admin: bool,
    include_others_private: bool = False,
) -> list[SavedReport]:
    stmt = (
        select(TiqoraReport)
        .where(TiqoraReport.valid.is_(True))
        .order_by(TiqoraReport.name, TiqoraReport.id)
        .execution_options(populate_existing=True)
    )
    if not (admin and include_others_private):
        member_groups = await PermissionEngine(session).groups_for_permission(user_id, "ro")
        conditions = [TiqoraReport.owner_user_id == user_id, TiqoraReport.visibility == "all"]
        if member_groups:
            shared = select(TiqoraReportGroup.report_id).where(
                TiqoraReportGroup.group_id.in_(member_groups)
            )
            conditions.append(
                and_(TiqoraReport.visibility == "groups", TiqoraReport.id.in_(shared))
            )
        stmt = stmt.where(or_(*conditions))
    rows = (await session.execute(stmt)).scalars().all()
    groups = await _groups_of(session, [int(r.id) for r in rows])
    return [_to_saved(r, groups.get(int(r.id), ())) for r in rows]


async def get_saved_report(
    session: AsyncSession, report_id: int, user_id: int, *, admin: bool
) -> SavedReport:
    report = await _load(session, report_id)
    if report is None:
        raise ReportNotFound(str(report_id))
    if not admin and not await _is_visible(session, report, user_id):
        raise ReportNotFound(str(report_id))
    if not report.valid:
        raise ReportGone(report.name)
    return report


async def _is_visible(session: AsyncSession, report: SavedReport, user_id: int) -> bool:
    if report.owner_user_id == user_id or report.visibility == "all":
        return True
    if report.visibility == "groups":
        member = await PermissionEngine(session).groups_for_permission(user_id, "ro")
        return bool(member & set(report.group_ids))
    return False


def can_edit_report(report: SavedReport, user_id: int, rights: ReportRights) -> bool:
    return (report.owner_user_id == user_id and rights.create) or rights.admin


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise DefinitionError("Bitte einen Namen angeben.", path="name")
    return cleaned


def _validate(defn: ReportDefinition, registry: Registry) -> None:
    try:
        validate_definition(defn, registry)
    except DefinitionError as exc:
        raise DefinitionError(exc.message, path="definition." + exc.path) from exc


async def _replace_groups(
    session: AsyncSession, report_id: int, group_ids: tuple[int, ...]
) -> None:
    await session.execute(delete(TiqoraReportGroup).where(TiqoraReportGroup.report_id == report_id))
    for gid in group_ids:
        session.add(TiqoraReportGroup(report_id=report_id, group_id=gid))
    await session.flush()


async def create_saved_report(
    session: AsyncSession,
    user_id: int,
    rights: ReportRights,
    body: SavedReportIn,
    *,
    registry: Registry,
) -> SavedReport:
    if not rights.create:
        raise ReportForbidden(SAVE_RIGHT_MESSAGE)
    name = _clean_name(body.name)
    _validate(body.definition, registry)
    groups: tuple[int, ...] = ()
    if body.visibility != "private" and not rights.share:
        raise ReportForbidden(SHARE_RIGHT_MESSAGE)
    if body.visibility == "groups":
        if not body.group_ids:
            raise ShareGroupError("Bitte mindestens eine Gruppe auswählen.")
        groups = await check_share_groups(session, user_id, body.group_ids)
    owned = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraReport)
            .where(TiqoraReport.owner_user_id == user_id, TiqoraReport.valid.is_(True))
        )
    ).scalar_one()
    if int(owned) >= MAX_SAVED_REPORTS_PER_USER:
        # 422 with path ``name``, the same answer as the saved-search limit
        raise DefinitionError(LIMIT_MESSAGE, path="name")
    now = utc_now()
    row = TiqoraReport(
        name=name,
        description=body.description,
        owner_user_id=user_id,
        visibility=body.visibility,
        definition=dump_definition(body.definition),
        schema_version=body.definition.schema_version,
        version=1,
        valid=True,
        copied_from=body.copied_from,
        created_at=now,
        created_by=user_id,
        changed_at=now,
        changed_by=user_id,
    )
    session.add(row)
    await session.flush()
    await _replace_groups(session, int(row.id), groups)
    return _to_saved(row, groups)


async def update_saved_report(
    session: AsyncSession,
    report_id: int,
    user_id: int,
    rights: ReportRights,
    body: SavedReportUpdateIn,
    *,
    registry: Registry,
) -> SavedReport:
    """Full replace. Invisible -> ReportNotFound; visible but not editable -> ReportForbidden."""
    row = await get_saved_report(session, report_id, user_id, admin=rights.admin)
    if not can_edit_report(row, user_id, rights):
        if row.owner_user_id == user_id:  # the owner, but without the save right
            raise ReportForbidden(SAVE_RIGHT_MESSAGE)
        raise ReportForbidden("Dieser Bericht darf nur von seinem Besitzer geändert werden.")
    if body.version != row.version:
        raise ReportVersionConflict(row.version)
    name = _clean_name(body.name)
    _validate(body.definition, registry)

    # R-SHAREKEEP: keeping or narrowing the stored sharing needs no share right; only widening
    # it does (a group added to a group share, or private -> groups/all, groups -> all), as for
    # saved searches. Groups not stored yet always go through ``check_share_groups``.
    groups: tuple[int, ...] = ()
    if body.visibility == "groups":
        wanted = tuple(sorted(set(body.group_ids)))
        if not wanted:
            raise ShareGroupError("Bitte mindestens eine Gruppe auswählen.")
        stored = row.group_ids if row.visibility == "groups" else ()
        added = [g for g in wanted if g not in stored]
        if added:
            widening = row.visibility != "all"  # all -> some groups is narrowing
            if widening and not rights.share:
                raise ReportForbidden(SHARE_RIGHT_MESSAGE)
            await check_share_groups(session, user_id, added)
        groups = wanted
    elif body.visibility == "all" and row.visibility != "all" and not rights.share:
        raise ReportForbidden(SHARE_RIGHT_MESSAGE)

    result = await session.execute(
        update(TiqoraReport)
        .where(
            TiqoraReport.id == report_id,
            TiqoraReport.version == body.version,
            TiqoraReport.valid.is_(True),
        )
        .values(
            name=name,
            description=body.description,
            visibility=body.visibility,
            definition=dump_definition(body.definition),
            schema_version=body.definition.schema_version,
            version=TiqoraReport.version + 1,
            changed_at=utc_now(),
            changed_by=user_id,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]  # two concurrent saves
        current = await _load(session, report_id)
        if current is None or not current.valid:  # deleted between load and write
            raise ReportNotFound(str(report_id))
        raise ReportVersionConflict(current.version if current else body.version)
    await _replace_groups(session, report_id, groups)
    fresh = await _load(session, report_id)
    assert fresh is not None
    return fresh


async def delete_saved_report(
    session: AsyncSession, report_id: int, user_id: int, rights: ReportRights
) -> None:
    """Soft delete. An owner may always delete their own report (R-OWNDELETE), admins any."""
    row = await get_saved_report(session, report_id, user_id, admin=rights.admin)
    if row.owner_user_id != user_id and not rights.admin:
        raise ReportForbidden("Dieser Bericht darf nur von seinem Besitzer gelöscht werden.")
    result = await session.execute(
        update(TiqoraReport)
        .where(TiqoraReport.id == report_id, TiqoraReport.valid.is_(True))
        .values(valid=False, changed_at=utc_now(), changed_by=user_id)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]  # deleted concurrently
        raise ReportNotFound(str(report_id))
    await session.flush()


async def purge_deleted_reports(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    older_than_days: int = DELETED_REPORT_RETENTION_DAYS,
) -> int:
    """Hard-delete soft-deleted reports older than ``older_than_days`` (group rows cascade)."""
    cutoff = (now or utc_now()) - timedelta(days=older_than_days)
    ids = (
        (
            await session.execute(
                select(TiqoraReport.id).where(
                    TiqoraReport.valid.is_(False), TiqoraReport.changed_at < cutoff
                )
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        return 0
    await session.execute(delete(TiqoraReportGroup).where(TiqoraReportGroup.report_id.in_(ids)))
    await session.execute(delete(TiqoraReport).where(TiqoraReport.id.in_(ids)))
    return len(ids)


@dataclass(frozen=True, slots=True)
class ResolvedReport:
    key: str  # "builtin.<name>" or str(id)
    definition: ReportDefinition
    builtin: BuiltinReport | None
    saved: SavedReport | None


async def resolve_report_ref(
    session: AsyncSession,
    key: str,
    user_id: int,
    *,
    admin: bool,
    registry: Registry | None = None,
) -> ResolvedReport:
    """``builtin.<name>`` or a saved report's decimal id; anything else is ReportNotFound.

    A saved report whose stored JSON no longer parses raises :class:`DefinitionError`
    (callers answer 422 with the German message).
    """
    reg = registry or get_registry()
    if is_catalogue_key(key):
        builtin = active_builtin(reg, key)
        if builtin is None:
            raise ReportNotFound(key)
        return ResolvedReport(key=key, definition=builtin.definition, builtin=builtin, saved=None)
    if not SAVED_KEY_RE.fullmatch(key):
        raise ReportNotFound(key)
    saved = await get_saved_report(session, int(key), user_id, admin=admin)
    if saved.definition is None:
        raise DefinitionError(
            saved.definition_error or BROKEN_DEFINITION_MESSAGE, path="definition"
        )
    return ResolvedReport(key=key, definition=saved.definition, builtin=None, saved=saved)
