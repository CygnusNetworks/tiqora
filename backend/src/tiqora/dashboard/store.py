"""Dashboard store: visibility, rights, copy, optimistic locking (D4).

Access control lives here. A dashboard the caller may not see raises
``DashboardNotFound``, exactly like an unknown id, so ids cannot be probed.

Role/group membership is read from Znuny tables first and applied as integer IN-lists;
``tiqora_*`` tables are never joined with Znuny tables (mixed collations, error 1267).
Functions flush but never commit (except where stated); the caller commits.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.dashboard.defaults import default_widgets
from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.layout import Rect, normalize_rects
from tiqora.dashboard.races import is_race_error
from tiqora.dashboard.schemas import (
    MAX_DASHBOARDS_PER_USER,
    DashboardCreateIn,
    DashboardOut,
    DashboardSaveIn,
    DashboardTabOut,
    DashboardTemplateIn,
    DashboardTemplateOut,
    WidgetIn,
    WidgetLayouts,
    WidgetOut,
    WidgetSettingsPatchIn,
)
from tiqora.dashboard.settings_models import (
    MAX_WIDGET_SETTINGS_BYTES,
    SettingsError,
    validate_widget_settings,
)
from tiqora.db.legacy.user import Roles, RoleUser, Users
from tiqora.db.tiqora.models import (
    TiqoraDashboard,
    TiqoraDashboardGroup,
    TiqoraDashboardRole,
    TiqoraDashboardWidget,
)
from tiqora.filters import Filter, FilterError
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import check_share_groups
from tiqora.reporting.definition import DefinitionError
from tiqora.reporting.saved import ReportGone, ReportNotFound, resolve_report_ref
from tiqora.saved_searches.store import SavedSearchNotFound, get_visible, validate_criteria

__all__ = [
    "DashboardConflict",
    "DashboardForbidden",
    "DashboardInvalid",
    "DashboardNotFound",
    "create_dashboard",
    "create_template",
    "delete_dashboard",
    "delete_template",
    "get_dashboard",
    "list_templates",
    "list_visible",
    "patch_widget_settings",
    "dashboard_filters",
    "save_dashboard",
    "set_filters",
    "validate_source_settings",
    "validate_sources",
    "set_order",
    "set_share",
    "update_template",
    "user_role_ids",
]

_log = logging.getLogger(__name__)

_OWNED_KINDS: Final = ("personal", "shared")
MSG_NOT_EDITABLE: Final = "Dieses Dashboard können Sie nicht bearbeiten."
MSG_LIMIT: Final = f"Es sind höchstens {MAX_DASHBOARDS_PER_USER} Dashboards möglich."
MSG_WIDGET_ID_TAKEN: Final = "Diese Widget-ID ist schon vergeben."
MSG_TEMPLATE_SHARE: Final = "Vorlagen werden über die Rollen verteilt."
MSG_LAST: Final = "Das letzte Dashboard bleibt bestehen."


class DashboardNotFound(LookupError):
    """Unknown id or not visible to the caller."""


class DashboardForbidden(PermissionError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DashboardConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__("Dieses Dashboard wurde inzwischen geändert.")
        self.current_version = current_version
        self.message = "Dieses Dashboard wurde inzwischen geändert."


class DashboardInvalid(ValueError):
    def __init__(self, message: str, path: str) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


# --------------------------------------------------------------------------- helpers


def dump_json(value: Any) -> str:
    """Serialise exactly as the settings size cap measures it (ensure_ascii=False)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _check_size(settings: Mapping[str, Any], path: str) -> str:
    text = dump_json(settings)
    if len(text.encode("utf-8")) > MAX_WIDGET_SETTINGS_BYTES:
        raise DashboardInvalid("Die Einstellungen sind zu groß.", path)
    return text


def _layout_text(layout: WidgetLayouts) -> str:
    return dump_json(layout.model_dump(mode="json", exclude_none=True))


async def user_role_ids(session: AsyncSession, user_id: int) -> list[int]:
    """Valid roles of the user (``role_user`` x ``roles.valid_id == 1``)."""
    rows = await session.execute(
        select(RoleUser.role_id)
        .join(Roles, Roles.id == RoleUser.role_id)
        .where(RoleUser.user_id == user_id, Roles.valid_id == 1)
        .order_by(RoleUser.role_id)
    )
    return [int(r) for r in rows.scalars().all()]


async def _load_row(session: AsyncSession, dashboard_id: int) -> TiqoraDashboard | None:
    res = await session.execute(
        select(TiqoraDashboard)
        .where(TiqoraDashboard.id == dashboard_id)
        .execution_options(populate_existing=True)
    )
    return res.scalar_one_or_none()


async def _group_ids(session: AsyncSession, dashboard_id: int) -> list[int]:
    res = await session.execute(
        select(TiqoraDashboardGroup.group_id)
        .where(TiqoraDashboardGroup.dashboard_id == dashboard_id)
        .order_by(TiqoraDashboardGroup.group_id)
    )
    return [int(g) for g in res.scalars().all()]


async def _role_ids_of(session: AsyncSession, dashboard_id: int) -> list[int]:
    res = await session.execute(
        select(TiqoraDashboardRole.role_id).where(TiqoraDashboardRole.dashboard_id == dashboard_id)
    )
    return [int(r) for r in res.scalars().all()]


async def _visible_row(
    session: AsyncSession, dashboard_id: int, user_id: int, *, admin: bool
) -> TiqoraDashboard:
    row = await _load_row(session, dashboard_id)
    if row is None:
        raise DashboardNotFound(dashboard_id)
    if row.kind in _OWNED_KINDS and row.owner_user_id == user_id:
        return row
    if row.kind == "shared":
        groups = await PermissionEngine(session).groups_for_permission(user_id, "ro")
        if groups and set(await _group_ids(session, row.id)) & groups:
            return row
    elif row.kind == "template":
        if admin:
            return row
        if row.locked:
            roles = set(await user_role_ids(session, user_id))
            if roles and set(await _role_ids_of(session, row.id)) & roles:
                return row
    raise DashboardNotFound(dashboard_id)


async def is_visible(
    session: AsyncSession, dashboard_id: int, user_id: int, *, admin: bool
) -> bool:
    """True when ``user_id`` can open the dashboard (same rule as :func:`get_dashboard`)."""
    try:
        await _visible_row(session, dashboard_id, user_id, admin=admin)
    except DashboardNotFound:
        return False
    return True


def _can_edit(row: TiqoraDashboard, user_id: int, *, admin: bool) -> bool:
    if row.kind == "template":
        return admin
    return row.owner_user_id == user_id


def _require_edit(row: TiqoraDashboard, user_id: int, *, admin: bool) -> None:
    if not _can_edit(row, user_id, admin=admin):
        raise DashboardForbidden(MSG_NOT_EDITABLE)


async def _owner_names(session: AsyncSession, owner_ids: set[int]) -> dict[int, str]:
    if not owner_ids:
        return {}
    res = await session.execute(
        select(Users.id, Users.first_name, Users.last_name, Users.login).where(
            Users.id.in_(sorted(owner_ids))
        )
    )
    out: dict[int, str] = {}
    for uid, first, last, login in res.all():
        full = f"{first or ''} {last or ''}".strip()
        out[int(uid)] = full or str(login)
    return out


def _tab(
    row: TiqoraDashboard,
    user_id: int,
    *,
    admin: bool,
    group_ids: list[int],
    owner_name: str | None,
) -> DashboardTabOut:
    is_owner = row.owner_user_id == user_id and row.kind in _OWNED_KINDS
    return DashboardTabOut(
        id=row.id,
        name=row.name,
        icon=row.icon,
        kind=row.kind,  # type: ignore[arg-type]
        locked=bool(row.locked),
        is_owner=is_owner,
        can_edit=_can_edit(row, user_id, admin=admin),
        can_share=is_owner,
        owner_name=None if is_owner else owner_name,
        version=row.version,
        sort=row.sort,
        group_ids=group_ids if is_owner else [],
        source_template_id=row.source_template_id,
    )


async def _tabs_for(
    session: AsyncSession, rows: Sequence[TiqoraDashboard], user_id: int, *, admin: bool
) -> list[DashboardTabOut]:
    ids = [r.id for r in rows]
    groups: dict[int, list[int]] = {i: [] for i in ids}
    if ids:
        res = await session.execute(
            select(TiqoraDashboardGroup.dashboard_id, TiqoraDashboardGroup.group_id)
            .where(TiqoraDashboardGroup.dashboard_id.in_(ids))
            .order_by(TiqoraDashboardGroup.group_id)
        )
        for did, gid in res.all():
            groups[int(did)].append(int(gid))
    owners = {
        r.owner_user_id for r in rows if r.owner_user_id is not None and r.owner_user_id != user_id
    }
    names = await _owner_names(session, {int(o) for o in owners})
    return [
        _tab(
            r,
            user_id,
            admin=admin,
            group_ids=groups[r.id],
            owner_name=names.get(r.owner_user_id) if r.owner_user_id is not None else None,
        )
        for r in rows
    ]


def dashboard_filters(row: TiqoraDashboard) -> DashboardFilters:
    """The stored dashboard filters; ``'{}'`` or an unreadable value reads as empty."""
    try:
        return DashboardFilters.model_validate_json(row.filters or "{}")
    except ValidationError:
        _log.warning("dashboard %s: unreadable filters, treated as empty", row.id)
        return DashboardFilters()


def _widget_out(w: TiqoraDashboardWidget) -> WidgetOut:
    return WidgetOut(
        id=w.id,
        type_key=w.type_key,
        type_version=w.type_version,
        title_override=w.title_override,
        settings=json.loads(w.settings or "{}"),
        layout=WidgetLayouts.model_validate(json.loads(w.layout)),
        pinned=bool(w.pinned),
    )


async def _widgets(session: AsyncSession, dashboard_id: int) -> list[TiqoraDashboardWidget]:
    res = await session.execute(
        select(TiqoraDashboardWidget)
        .where(TiqoraDashboardWidget.dashboard_id == dashboard_id)
        .order_by(TiqoraDashboardWidget.position, TiqoraDashboardWidget.id)
        .execution_options(populate_existing=True)
    )
    return list(res.scalars().all())


async def dashboard_out(
    session: AsyncSession, row: TiqoraDashboard, user_id: int, *, admin: bool
) -> DashboardOut:
    """Full ``DashboardOut`` for a row the caller is already allowed to see."""
    (tab,) = await _tabs_for(session, [row], user_id, admin=admin)
    widgets = [_widget_out(w) for w in await _widgets(session, row.id)]
    return DashboardOut(**tab.model_dump(), widgets=widgets, filters=dashboard_filters(row))


async def _own_rows(session: AsyncSession, user_id: int) -> list[TiqoraDashboard]:
    res = await session.execute(
        select(TiqoraDashboard)
        .where(TiqoraDashboard.owner_user_id == user_id, TiqoraDashboard.kind.in_(_OWNED_KINDS))
        .order_by(TiqoraDashboard.sort, TiqoraDashboard.id)
        .execution_options(populate_existing=True)
    )
    return list(res.scalars().all())


# --------------------------------------------------------------------------- reads


async def list_visible(
    session: AsyncSession, user_id: int, *, admin: bool
) -> list[DashboardTabOut]:
    """Own (sort, id), shared with me (name), locked templates of my roles (sort, id)."""
    own = await _own_rows(session, user_id)

    shared: list[TiqoraDashboard] = []
    groups = await PermissionEngine(session).groups_for_permission(user_id, "ro")
    if groups:
        ids = (
            (
                await session.execute(
                    select(TiqoraDashboardGroup.dashboard_id).where(
                        TiqoraDashboardGroup.group_id.in_(sorted(groups))
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            res = await session.execute(
                select(TiqoraDashboard)
                .where(
                    TiqoraDashboard.id.in_(sorted({int(i) for i in ids})),
                    TiqoraDashboard.kind == "shared",
                    TiqoraDashboard.owner_user_id != user_id,
                )
                .order_by(TiqoraDashboard.name, TiqoraDashboard.id)
                .execution_options(populate_existing=True)
            )
            shared = list(res.scalars().all())

    templates: list[TiqoraDashboard] = []
    roles = await user_role_ids(session, user_id)
    if roles:
        tids = (
            (
                await session.execute(
                    select(TiqoraDashboardRole.dashboard_id).where(
                        TiqoraDashboardRole.role_id.in_(roles)
                    )
                )
            )
            .scalars()
            .all()
        )
        if tids:
            res = await session.execute(
                select(TiqoraDashboard)
                .where(
                    TiqoraDashboard.id.in_(sorted({int(i) for i in tids})),
                    TiqoraDashboard.kind == "template",
                    TiqoraDashboard.locked.is_(True),
                )
                .order_by(TiqoraDashboard.sort, TiqoraDashboard.id)
                .execution_options(populate_existing=True)
            )
            templates = list(res.scalars().all())

    return await _tabs_for(session, [*own, *shared, *templates], user_id, admin=admin)


async def get_dashboard(
    session: AsyncSession, dashboard_id: int, user_id: int, *, admin: bool
) -> DashboardOut:
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    return await dashboard_out(session, row, user_id, admin=admin)


# --------------------------------------------------------------------------- writes


#: Attempts of an owner-set write (create, delete, reorder) that loses a lock race.
RACE_ATTEMPTS: Final = 3


async def _retry_races[T](session: AsyncSession, attempt: Callable[[], Awaitable[T]]) -> T:
    """Run one owner-set write; on a lock race roll back and run the whole unit again.

    On MariaDB (REPEATABLE READ) ``_lock_own`` holds next-key locks on the owner's range of
    ``ix_tiqora_dashboard_owner``, which include the gap to the neighbouring owner. Two
    neighbours that both insert into that gap (a create at the end of one range, a reorder
    to ``sort`` 0 at the start of the other) deadlock: InnoDB rolls one back with 1213 (RR-1).
    The loser's transaction is gone entirely, so nothing of it is applied twice; the next
    attempt re-reads everything under the lock (limit, last-dashboard guard, own ids).
    Like ``ensure_start`` this rolls back the caller's session: call it with no pending work.
    """
    for n in range(1, RACE_ATTEMPTS + 1):
        try:
            return await attempt()
        except DBAPIError as exc:
            if not is_race_error(exc):
                raise
            await session.rollback()
            if n == RACE_ATTEMPTS:
                raise
            await asyncio.sleep(random.uniform(0.01, 0.05) * n)  # jitter: no lock-step retry
    raise AssertionError("unreachable")


async def _lock_own(session: AsyncSession, user_id: int) -> list[int]:
    """Serialise per owner; the owner's dashboard ids on the latest committed data.

    Every writer of the owner's set (create, delete, ``set_order``) calls this first. Only
    ``tiqora_dashboard`` rows are locked (never Znuny tables), and they are locked by primary
    key in ``id`` order (statement 2), so a waiter queues on a primary-key record. Locking
    through the ``(owner, kind, sort)`` index instead would let a waiter's next-key lock
    deadlock with a holder that moves ``sort`` values into that gap (``set_order``,
    MariaDB 1213). Statement 3 re-reads the set as a locking read: PostgreSQL READ COMMITTED
    takes a fresh snapshot per statement, and a locking read is a current read on InnoDB
    (REPEATABLE READ), so it sees the rows committed by the transaction we waited for.
    No rows: nothing to lock, nothing to guard (an owner at the limit or at the
    last-dashboard guard always has rows). Returns the ids in tab order (``sort``, ``id``).
    """
    owned = select(TiqoraDashboard.id).where(
        TiqoraDashboard.owner_user_id == user_id, TiqoraDashboard.kind.in_(_OWNED_KINDS)
    )
    seen = list((await session.execute(owned)).scalars().all())
    if seen:
        await session.execute(
            select(TiqoraDashboard.id)
            .where(TiqoraDashboard.id.in_(seen))
            .order_by(TiqoraDashboard.id)
            .with_for_update()
        )
    res = await session.execute(
        owned.order_by(TiqoraDashboard.sort, TiqoraDashboard.id).with_for_update()
    )
    return [int(i) for i in res.scalars().all()]


async def _current_version(session: AsyncSession, dashboard_id: int) -> int:
    """Latest committed version (locking read: InnoDB would serve an old snapshot otherwise)."""
    res = await session.execute(
        select(TiqoraDashboard.version).where(TiqoraDashboard.id == dashboard_id).with_for_update()
    )
    version = res.scalar_one_or_none()
    if version is None:
        raise DashboardNotFound(dashboard_id)
    return int(version)


async def _next_sort(session: AsyncSession, user_id: int) -> int:
    res = await session.execute(
        select(func.max(TiqoraDashboard.sort)).where(
            TiqoraDashboard.owner_user_id == user_id, TiqoraDashboard.kind.in_(_OWNED_KINDS)
        )
    )
    top = res.scalar_one_or_none()
    return 0 if top is None else int(top) + 1


def widget_row(dashboard_id: int, w: WidgetIn, position: int) -> TiqoraDashboardWidget:
    return TiqoraDashboardWidget(
        id=w.id,
        dashboard_id=dashboard_id,
        type_key=w.type_key,
        type_version=w.type_version,
        title_override=w.title_override,
        settings=dump_json(w.settings),
        layout=_layout_text(w.layout),
        position=position,
        pinned=w.pinned,
    )


async def create_dashboard(
    session: AsyncSession, user_id: int, body: DashboardCreateIn, *, admin: bool
) -> DashboardOut:
    """A new personal dashboard (optionally a copy); retried on a lock race (RR-1)."""
    return await _retry_races(
        session, lambda: _create_dashboard_once(session, user_id, body, admin=admin)
    )


async def _create_dashboard_once(
    session: AsyncSession, user_id: int, body: DashboardCreateIn, *, admin: bool
) -> DashboardOut:
    if len(await _lock_own(session, user_id)) >= MAX_DASHBOARDS_PER_USER:
        raise DashboardInvalid(MSG_LIMIT, "name")
    sources: list[TiqoraDashboardWidget] = []
    filters = "{}"
    from_template: TiqoraDashboard | None = None
    if body.copy_from is not None:
        src = await _visible_row(session, body.copy_from, user_id, admin=admin)
        sources = await _widgets(session, src.id)
        filters = dashboard_filters(src).model_dump_json()
        if src.kind == "template":
            from_template = src
    now = utc_now()
    row = TiqoraDashboard(
        owner_user_id=user_id,
        name=body.name,
        icon=body.icon,
        sort=await _next_sort(session, user_id),
        kind="personal",
        locked=False,
        filters=filters,
        source_template_id=from_template.id if from_template is not None else None,
        source_template_version=from_template.version if from_template is not None else None,
        version=1,
        created_at=now,
        changed_at=now,
    )
    session.add(row)
    await session.flush()
    for i, w in enumerate(sources):
        session.add(
            TiqoraDashboardWidget(
                id=uuid.uuid4().hex,
                dashboard_id=row.id,
                type_key=w.type_key,
                type_version=w.type_version,
                title_override=w.title_override,
                settings=w.settings,
                layout=w.layout,
                position=i,
                pinned=w.pinned,
                template_widget_id=w.id if from_template is not None else None,
            )
        )
    await session.flush()
    return await dashboard_out(session, row, user_id, admin=admin)


_SOURCE_TYPES = frozenset({"core.ticket-list", "core.counter"})
MSG_SEARCH_UNKNOWN = "Die gespeicherte Suche gibt es nicht."


MSG_REPORT_UNKNOWN = "Diesen Bericht gibt es nicht oder er ist für Sie nicht freigegeben."


async def _validate_report_key(
    session: AsyncSession, user_id: int, settings: Mapping[str, Any], path: str
) -> None:
    """The saver must be able to see the report (same visibility as when the tile runs)."""
    where = f"{path}.report_key"
    try:
        admin = await PermissionEngine(session).is_admin(user_id)
        await resolve_report_ref(session, str(settings.get("report_key")), user_id, admin=admin)
    except ReportNotFound:
        raise DashboardInvalid(MSG_REPORT_UNKNOWN, where) from None
    except ReportGone as exc:
        raise DashboardInvalid(f"Der Bericht „{exc.name}“ wurde gelöscht.", where) from None
    except DefinitionError as exc:
        raise DashboardInvalid(exc.message, where) from None


async def validate_source_settings(
    session: AsyncSession, user_id: int, type_key: str, settings: Mapping[str, Any], *, path: str
) -> None:
    """Second validation pass (needs the database): own filters and saved searches.

    ``settings`` are already validated by :func:`validate_widget_settings`; ``path`` is the
    prefix of the settings object (``widgets[i].settings`` or ``settings``). The saver must
    be able to see a saved search (same visibility as when the tile runs).
    """
    if type_key == "core.report":
        await _validate_report_key(session, user_id, settings, path)
        return
    if type_key not in _SOURCE_TYPES:
        return
    source = settings.get("source")
    if not isinstance(source, Mapping):
        return
    kind = source.get("kind")
    if kind == "filter":
        where = f"{path}.source.filter"
        try:
            flt = Filter.model_validate(source["filter"])
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            raise DashboardInvalid(
                "Der Filter ist ungültig.", f"{where}.{loc}".rstrip(".")
            ) from None
        try:
            await validate_criteria(session, flt)
        except FilterError as exc:
            raise DashboardInvalid(exc.message, f"{where}.{exc.path}") from None
    elif kind == "saved_search":
        try:
            await get_visible(session, int(source["id"]), user_id, admin=False)
        except SavedSearchNotFound:
            raise DashboardInvalid(MSG_SEARCH_UNKNOWN, f"{path}.source.id") from None


async def validate_sources(
    session: AsyncSession, user_id: int, widgets: Sequence[WidgetIn]
) -> None:
    """:func:`validate_source_settings` for every widget of a save body."""
    for i, w in enumerate(widgets):
        await validate_source_settings(
            session, user_id, w.type_key, w.settings, path=f"widgets[{i}].settings"
        )


def _prepare_widgets(body: DashboardSaveIn) -> list[tuple[WidgetIn, str]]:
    """Normalise layouts, validate settings; returns (widget, settings_json)."""
    try:
        lg = normalize_rects([(w.id, w.layout.lg) for w in body.widgets])
        md = normalize_rects(
            [(w.id, w.layout.md) for w in body.widgets if w.layout.md is not None], cols=8
        )
    except SettingsError as exc:
        raise DashboardInvalid(exc.message, exc.path) from None
    out: list[tuple[WidgetIn, str]] = []
    for i, w in enumerate(body.widgets):
        try:
            settings = validate_widget_settings(w.type_key, w.settings)
        except SettingsError as exc:
            raise DashboardInvalid(exc.message, f"widgets[{i}].{exc.path}") from None
        text = _check_size(settings, f"widgets[{i}].settings")
        rect_md: Rect | None = md.get(w.id)
        fixed = w.model_copy(
            update={
                "settings": settings,
                "layout": WidgetLayouts(lg=lg[w.id], md=rect_md),
            }
        )
        out.append((fixed, text))
    return out


async def save_dashboard(
    session: AsyncSession,
    dashboard_id: int,
    user_id: int,
    body: DashboardSaveIn,
    *,
    admin: bool,
) -> DashboardOut:
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    _require_edit(row, user_id, admin=admin)
    if body.version != row.version:
        raise DashboardConflict(row.version)
    prepared = _prepare_widgets(body)
    await validate_sources(session, user_id, [w for w, _ in prepared])

    ids = [w.id for w, _ in prepared]
    if ids:
        taken = (
            await session.execute(
                select(TiqoraDashboardWidget.id).where(
                    TiqoraDashboardWidget.id.in_(ids),
                    TiqoraDashboardWidget.dashboard_id != dashboard_id,
                )
            )
        ).scalars()
        clash = set(taken)
        for i, wid in enumerate(ids):
            if wid in clash:
                raise DashboardInvalid(MSG_WIDGET_ID_TAKEN, f"widgets[{i}].id")

    try:
        async with session.begin_nested():
            res = await session.execute(
                update(TiqoraDashboard)
                .where(TiqoraDashboard.id == dashboard_id, TiqoraDashboard.version == body.version)
                .values(
                    name=body.name,
                    icon=body.icon,
                    version=TiqoraDashboard.version + 1,
                    changed_at=utc_now(),
                    **(
                        {"filters": body.filters.model_dump_json()}
                        if body.filters is not None
                        else {}
                    ),
                )
            )
            if res.rowcount == 0:  # type: ignore[attr-defined]
                raise DashboardConflict(-1)
            existing = set(
                (
                    await session.execute(
                        select(TiqoraDashboardWidget.id).where(
                            TiqoraDashboardWidget.dashboard_id == dashboard_id
                        )
                    )
                ).scalars()
            )
            stale = existing - set(ids)
            if stale:
                await session.execute(
                    delete(TiqoraDashboardWidget).where(
                        TiqoraDashboardWidget.dashboard_id == dashboard_id,
                        TiqoraDashboardWidget.id.in_(sorted(stale)),
                    )
                )
            for pos, (w, settings_text) in enumerate(prepared):
                values = {
                    "type_key": w.type_key,
                    "type_version": w.type_version,
                    "title_override": w.title_override,
                    "settings": settings_text,
                    "layout": _layout_text(w.layout),
                    "position": pos,
                    "pinned": w.pinned,
                }
                if w.id in existing:
                    await session.execute(
                        update(TiqoraDashboardWidget)
                        .where(
                            TiqoraDashboardWidget.id == w.id,
                            TiqoraDashboardWidget.dashboard_id == dashboard_id,
                        )
                        .values(**values)
                    )
                else:
                    if row.kind == "template":
                        # E8: the template version that first contained this widget
                        values["added_in_version"] = body.version + 1
                    await session.execute(
                        insert(TiqoraDashboardWidget).values(
                            id=w.id, dashboard_id=dashboard_id, **values
                        )
                    )
    except DashboardConflict:
        raise DashboardConflict(await _current_version(session, dashboard_id)) from None
    except IntegrityError:
        # another save claimed one of the widget ids between our check and the insert
        raise DashboardInvalid(MSG_WIDGET_ID_TAKEN, "widgets") from None

    fresh = await _load_row(session, dashboard_id)
    assert fresh is not None
    return await dashboard_out(session, fresh, user_id, admin=admin)


async def set_filters(
    session: AsyncSession,
    dashboard_id: int,
    user_id: int,
    filters: DashboardFilters,
    version: int,
    *,
    admin: bool,
) -> DashboardOut:
    """Store the dashboard-wide filters (same rights and optimistic lock as a save).

    Retried on a lock race, which rolls back the caller's session: call it with no pending work.
    """
    return await _retry_races(
        session, lambda: _set_filters_once(session, dashboard_id, user_id, filters, version, admin)
    )


async def _set_filters_once(
    session: AsyncSession,
    dashboard_id: int,
    user_id: int,
    filters: DashboardFilters,
    version: int,
    admin: bool,
) -> DashboardOut:
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    _require_edit(row, user_id, admin=admin)
    if version != row.version:
        raise DashboardConflict(row.version)
    res = await session.execute(
        update(TiqoraDashboard)
        .where(TiqoraDashboard.id == dashboard_id, TiqoraDashboard.version == version)
        .values(
            filters=filters.model_dump_json(),
            version=TiqoraDashboard.version + 1,
            changed_at=utc_now(),
        )
    )
    if res.rowcount == 0:  # type: ignore[attr-defined]
        raise DashboardConflict(await _current_version(session, dashboard_id))
    fresh = await _load_row(session, dashboard_id)
    assert fresh is not None
    return await dashboard_out(session, fresh, user_id, admin=admin)


async def patch_widget_settings(
    session: AsyncSession,
    dashboard_id: int,
    widget_id: str,
    user_id: int,
    body: WidgetSettingsPatchIn,
    *,
    admin: bool,
) -> WidgetOut:
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    res = await session.execute(
        select(TiqoraDashboardWidget)
        .where(
            TiqoraDashboardWidget.id == widget_id,
            TiqoraDashboardWidget.dashboard_id == dashboard_id,
        )
        .execution_options(populate_existing=True)
    )
    widget = res.scalar_one_or_none()
    if widget is None:
        raise DashboardNotFound(widget_id)
    _require_edit(row, user_id, admin=admin)
    # A stale client gets its 409 before any settings check (and without the source queries).
    if body.version != row.version:
        raise DashboardConflict(row.version)
    try:
        settings = validate_widget_settings(widget.type_key, body.settings)
    except SettingsError as exc:
        raise DashboardInvalid(exc.message, exc.path) from None
    text = _check_size(settings, "settings")
    await validate_source_settings(session, user_id, widget.type_key, settings, path="settings")

    async with session.begin_nested():
        upd = await session.execute(
            update(TiqoraDashboard)
            .where(TiqoraDashboard.id == dashboard_id, TiqoraDashboard.version == body.version)
            .values(version=TiqoraDashboard.version + 1, changed_at=utc_now())
        )
        if upd.rowcount == 0:  # type: ignore[attr-defined]
            conflict = True
        else:
            conflict = False
            await session.execute(
                update(TiqoraDashboardWidget)
                .where(TiqoraDashboardWidget.id == widget_id)
                .values(settings=text)
            )
    if conflict:
        raise DashboardConflict(await _current_version(session, dashboard_id))
    fresh = await session.execute(
        select(TiqoraDashboardWidget)
        .where(TiqoraDashboardWidget.id == widget_id)
        .execution_options(populate_existing=True)
    )
    return _widget_out(fresh.scalar_one())


async def delete_dashboard(
    session: AsyncSession, dashboard_id: int, user_id: int, *, admin: bool
) -> None:
    """Delete a dashboard and its rows; retried on a lock race (RR-1)."""
    await _retry_races(
        session, lambda: _delete_dashboard_once(session, dashboard_id, user_id, admin=admin)
    )


async def _delete_dashboard_once(
    session: AsyncSession, dashboard_id: int, user_id: int, *, admin: bool
) -> None:
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    _require_edit(row, user_id, admin=admin)
    if row.kind in _OWNED_KINDS and len(await _lock_own(session, user_id)) <= 1:
        raise DashboardInvalid(MSG_LAST, "id")
    if row.kind == "template":
        # dashboard row before child rows, like a template save or role edit
        await _current_version(session, dashboard_id)
    for model in (TiqoraDashboardWidget, TiqoraDashboardGroup, TiqoraDashboardRole):
        await session.execute(delete(model).where(model.dashboard_id == dashboard_id))
    await session.execute(delete(TiqoraDashboard).where(TiqoraDashboard.id == dashboard_id))
    session.expire_all()


async def set_share(
    session: AsyncSession,
    dashboard_id: int,
    user_id: int,
    group_ids: Sequence[int],
    *,
    admin: bool,
) -> DashboardOut:
    """Share with groups (owner only); an empty list makes the dashboard personal again.

    Raises ``ShareGroupError`` for a group the owner may not share with.
    """
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    if row.kind == "template":
        raise DashboardForbidden(MSG_TEMPLATE_SHARE)
    _require_edit(row, user_id, admin=admin)
    # Serialise concurrent share changes on this dashboard row (a vanished row is a clean 404).
    await _current_version(session, dashboard_id)
    wanted = await check_share_groups(session, user_id, group_ids)
    async with session.begin_nested():
        await session.execute(
            delete(TiqoraDashboardGroup).where(TiqoraDashboardGroup.dashboard_id == dashboard_id)
        )
        for gid in wanted:
            await session.execute(
                insert(TiqoraDashboardGroup).values(dashboard_id=dashboard_id, group_id=gid)
            )
        await session.execute(
            update(TiqoraDashboard)
            .where(TiqoraDashboard.id == dashboard_id)
            .values(
                kind="shared" if wanted else "personal",
                version=TiqoraDashboard.version + 1,
                changed_at=utc_now(),
            )
        )
    fresh = await _load_row(session, dashboard_id)
    assert fresh is not None
    return await dashboard_out(session, fresh, user_id, admin=admin)


async def set_order(session: AsyncSession, user_id: int, ids: Sequence[int]) -> None:
    """Order the caller's own personal/shared dashboards; any other id is forbidden.

    Takes the owner lock first, like create and delete: updating the rows one by one in the
    client's order would otherwise deadlock against them and against a second reorder.
    Retried on a lock race with a neighbouring owner (RR-1).
    """
    if len(set(ids)) != len(ids):
        raise DashboardInvalid("Doppelte Einträge.", "ids")
    await _retry_races(session, lambda: _set_order_once(session, user_id, ids))


async def _set_order_once(session: AsyncSession, user_id: int, ids: Sequence[int]) -> None:
    own_ids = await _lock_own(session, user_id)
    if not set(ids) <= set(own_ids):
        raise DashboardForbidden(MSG_NOT_EDITABLE)
    ordered = [*ids, *(i for i in own_ids if i not in set(ids))]
    for pos, did in enumerate(ordered):
        await session.execute(
            update(TiqoraDashboard)
            .where(TiqoraDashboard.id == did, TiqoraDashboard.owner_user_id == user_id)
            .values(sort=pos)
        )
    session.expire_all()


# ------------------------------------------------------------------ role templates
# Admin-only management (the route layer enforces ``AdminUser``). Templates have no owner;
# role membership is only stored here and applied on read (``list_visible``/``ensure_start``).


async def _check_roles(session: AsyncSession, role_ids: Sequence[int]) -> list[int]:
    """Deduplicated role ids; an unknown or invalid role is a 422 on ``role_ids``."""
    wanted = sorted(set(role_ids))
    if not wanted:
        return []
    res = await session.execute(select(Roles.id).where(Roles.id.in_(wanted), Roles.valid_id == 1))
    known = {int(r) for r in res.scalars().all()}
    for rid in wanted:
        if rid not in known:
            raise DashboardInvalid(f"Die Rolle {rid} gibt es nicht.", "role_ids")
    return wanted


async def _template_row(session: AsyncSession, template_id: int) -> TiqoraDashboard:
    row = await _load_row(session, template_id)
    if row is None or row.kind != "template":
        raise DashboardNotFound(template_id)
    return row


def _template_out(
    row: TiqoraDashboard, role_ids: list[int], widget_count: int
) -> DashboardTemplateOut:
    return DashboardTemplateOut(
        id=row.id,
        name=row.name,
        icon=row.icon,
        kind="template",
        locked=bool(row.locked),
        is_owner=False,
        can_edit=True,
        can_share=False,
        owner_name=None,
        version=row.version,
        sort=row.sort,
        group_ids=[],
        source_template_id=None,
        role_ids=sorted(role_ids),
        widget_count=widget_count,
    )


async def _templates_out(
    session: AsyncSession, rows: Sequence[TiqoraDashboard]
) -> list[DashboardTemplateOut]:
    """Role links and widget counts for all rows in two IN-list queries."""
    ids = [r.id for r in rows]
    roles: dict[int, list[int]] = {i: [] for i in ids}
    counts: dict[int, int] = {i: 0 for i in ids}
    if ids:
        res = await session.execute(
            select(TiqoraDashboardRole.dashboard_id, TiqoraDashboardRole.role_id).where(
                TiqoraDashboardRole.dashboard_id.in_(ids)
            )
        )
        for did, rid in res.all():
            roles[int(did)].append(int(rid))
        res = await session.execute(
            select(TiqoraDashboardWidget.dashboard_id, func.count())
            .where(TiqoraDashboardWidget.dashboard_id.in_(ids))
            .group_by(TiqoraDashboardWidget.dashboard_id)
        )
        for did, n in res.all():
            counts[int(did)] = int(n)
    return [_template_out(r, roles[r.id], counts[r.id]) for r in rows]


async def list_templates(session: AsyncSession) -> list[DashboardTemplateOut]:
    res = await session.execute(
        select(TiqoraDashboard)
        .where(TiqoraDashboard.kind == "template")
        .order_by(TiqoraDashboard.sort, TiqoraDashboard.id)
        .execution_options(populate_existing=True)
    )
    return await _templates_out(session, list(res.scalars().all()))


async def create_template(
    session: AsyncSession, admin_id: int, body: DashboardTemplateIn
) -> DashboardTemplateOut:
    """A new template with the default widgets; its layout is then edited with the
    dashboard PUT (admins only). ``admin_id`` is accepted for the audit trail only: the
    template itself has no owner."""
    _ = admin_id
    role_ids = await _check_roles(session, body.role_ids)
    now = utc_now()
    row = TiqoraDashboard(
        owner_user_id=None,
        name=body.name,
        icon=body.icon,
        sort=body.sort,
        kind="template",
        locked=body.locked,
        filters="{}",
        version=1,
        created_at=now,
        changed_at=now,
    )
    session.add(row)
    await session.flush()
    for i, w in enumerate(default_widgets()):
        session.add(widget_row(row.id, w, i))
    for rid in role_ids:
        session.add(TiqoraDashboardRole(dashboard_id=row.id, role_id=rid))
    await session.flush()
    return (await _templates_out(session, [await _template_row(session, row.id)]))[0]


async def update_template(
    session: AsyncSession,
    template_id: int,
    body: DashboardTemplateIn,
    *,
    version: int | None = None,
) -> DashboardTemplateOut:
    """Change name, icon, roles, lock and priority; bumps ``version``.

    With ``version`` the update is an optimistic-lock compare-and-set.
    """
    row = await _template_row(session, template_id)
    if version is not None and version != row.version:
        raise DashboardConflict(row.version)
    role_ids = await _check_roles(session, body.role_ids)
    stmt = update(TiqoraDashboard).where(TiqoraDashboard.id == template_id)
    if version is not None:
        stmt = stmt.where(TiqoraDashboard.version == version)
    res = await session.execute(
        stmt.values(
            name=body.name,
            icon=body.icon,
            sort=body.sort,
            locked=body.locked,
            version=TiqoraDashboard.version + 1,
            changed_at=utc_now(),
        )
    )
    if res.rowcount == 0:  # type: ignore[attr-defined]
        raise DashboardConflict(await _current_version(session, template_id))
    await session.execute(
        delete(TiqoraDashboardRole).where(TiqoraDashboardRole.dashboard_id == template_id)
    )
    for rid in role_ids:
        session.add(TiqoraDashboardRole(dashboard_id=template_id, role_id=rid))
    await session.flush()
    return (await _templates_out(session, [await _template_row(session, template_id)]))[0]


async def delete_template(session: AsyncSession, template_id: int) -> None:
    """Remove the template, its widgets and role rows. Copies users already made keep
    ``source_template_id`` and live on as their own dashboards."""
    await _template_row(session, template_id)
    # dashboard row before child rows, like a template save or role edit (lock order)
    await _current_version(session, template_id)
    for model in (TiqoraDashboardWidget, TiqoraDashboardRole, TiqoraDashboardGroup):
        await session.execute(delete(model).where(model.dashboard_id == template_id))
    await session.execute(delete(TiqoraDashboard).where(TiqoraDashboard.id == template_id))
    session.expire_all()
