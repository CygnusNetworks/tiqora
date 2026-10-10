"""Dashboard store: visibility, rights, copy, optimistic locking (D4).

Access control lives here. A dashboard the caller may not see raises
``DashboardNotFound``, exactly like an unknown id, so ids cannot be probed.

Role/group membership is read from Znuny tables first and applied as integer IN-lists;
``tiqora_*`` tables are never joined with Znuny tables (mixed collations, error 1267).
Functions flush but never commit (except where stated); the caller commits.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Final

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.dashboard.layout import Rect, normalize_rects
from tiqora.dashboard.schemas import (
    MAX_DASHBOARDS_PER_USER,
    DashboardCreateIn,
    DashboardOut,
    DashboardSaveIn,
    DashboardTabOut,
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
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine

__all__ = [
    "DashboardConflict",
    "DashboardForbidden",
    "DashboardInvalid",
    "DashboardNotFound",
    "create_dashboard",
    "delete_dashboard",
    "get_dashboard",
    "list_visible",
    "patch_widget_settings",
    "save_dashboard",
    "set_order",
    "user_role_ids",
]

_OWNED_KINDS: Final = ("personal", "shared")
MSG_NOT_EDITABLE: Final = "Dieses Dashboard kannst du nicht bearbeiten."
MSG_LIMIT: Final = f"Es sind höchstens {MAX_DASHBOARDS_PER_USER} Dashboards möglich."
MSG_WIDGET_ID_TAKEN: Final = "Diese Widget-ID ist schon vergeben."
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


def _widget_out(w: TiqoraDashboardWidget) -> WidgetOut:
    return WidgetOut(
        id=w.id,
        type_key=w.type_key,
        type_version=w.type_version,
        title_override=w.title_override,
        settings=json.loads(w.settings or "{}"),
        layout=WidgetLayouts.model_validate(json.loads(w.layout)),
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
    return DashboardOut(**tab.model_dump(), widgets=widgets)


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


async def _lock_own(session: AsyncSession, user_id: int) -> int:
    """Serialise per owner and count the owner's dashboards on the latest committed data.

    Only ``tiqora_dashboard`` rows are locked (never Znuny tables). Statement 1 locks the
    owner's existing rows, so concurrent creators/deleters queue up (an owner at the limit
    or at the last-dashboard guard always has rows). Statement 2 counts in a separate
    statement: PostgreSQL READ COMMITTED takes a fresh snapshot per statement, and the
    locking read is a current read on InnoDB (REPEATABLE READ), so both see the rows
    committed by the transaction we waited for. No rows: nothing to lock, nothing to guard.
    """
    stmt = (
        select(TiqoraDashboard.id)
        .where(TiqoraDashboard.owner_user_id == user_id, TiqoraDashboard.kind.in_(_OWNED_KINDS))
        .with_for_update()
    )
    await session.execute(stmt)
    return len((await session.execute(stmt)).scalars().all())


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
    )


async def create_dashboard(
    session: AsyncSession, user_id: int, body: DashboardCreateIn, *, admin: bool
) -> DashboardOut:
    if await _lock_own(session, user_id) >= MAX_DASHBOARDS_PER_USER:
        raise DashboardInvalid(MSG_LIMIT, "name")
    sources: list[TiqoraDashboardWidget] = []
    if body.copy_from is not None:
        src = await _visible_row(session, body.copy_from, user_id, admin=admin)
        sources = await _widgets(session, src.id)
    now = utc_now()
    row = TiqoraDashboard(
        owner_user_id=user_id,
        name=body.name,
        icon=body.icon,
        sort=await _next_sort(session, user_id),
        kind="personal",
        locked=False,
        filters="{}",
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
            )
        )
    await session.flush()
    return await dashboard_out(session, row, user_id, admin=admin)


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
    try:
        settings = validate_widget_settings(widget.type_key, body.settings)
    except SettingsError as exc:
        raise DashboardInvalid(exc.message, exc.path) from None
    text = _check_size(settings, "settings")
    if body.version != row.version:
        raise DashboardConflict(row.version)

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
    row = await _visible_row(session, dashboard_id, user_id, admin=admin)
    _require_edit(row, user_id, admin=admin)
    if row.kind in _OWNED_KINDS and await _lock_own(session, user_id) <= 1:
        raise DashboardInvalid(MSG_LAST, "id")
    for model in (TiqoraDashboardWidget, TiqoraDashboardGroup, TiqoraDashboardRole):
        await session.execute(delete(model).where(model.dashboard_id == dashboard_id))
    await session.execute(delete(TiqoraDashboard).where(TiqoraDashboard.id == dashboard_id))
    session.expire_all()


async def set_order(session: AsyncSession, user_id: int, ids: Sequence[int]) -> None:
    """Order the caller's own personal/shared dashboards; any other id is forbidden."""
    if len(set(ids)) != len(ids):
        raise DashboardInvalid("Doppelte Einträge.", "ids")
    own = await _own_rows(session, user_id)
    own_ids = [r.id for r in own]
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
