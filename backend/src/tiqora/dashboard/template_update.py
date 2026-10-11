"""Template update merge (E8): offer widgets a role template gained after the copy.

Only widgets are *added*. Changed or removed template widgets are not propagated, and a
row of the user's dashboard is never updated or deleted. All tables are ``tiqora_*``; the
only Znuny read is the viewer's role list (``store.user_role_ids``).
"""

from __future__ import annotations

import uuid
from typing import Final, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.dashboard import store
from tiqora.dashboard.layout import Rect, first_free_spot
from tiqora.dashboard.schemas import (
    MAX_WIDGETS_PER_DASHBOARD,
    DashboardOut,
    TemplateUpdateHint,
    TemplateUpdateOut,
    WidgetLayouts,
    WidgetOut,
)
from tiqora.db.tiqora.models import TiqoraDashboard, TiqoraDashboardWidget
from tiqora.jobs.service import utc_now

__all__ = ["MSG_TOO_MANY", "apply_update", "pending_update", "preview_update", "update_hint"]

MSG_TOO_MANY: Final = "Das Dashboard hätte dann mehr als 40 Widgets."


async def pending_update(
    session: AsyncSession, dashboard_row: TiqoraDashboard, viewer: int
) -> tuple[TiqoraDashboard, list[TiqoraDashboardWidget]] | None:
    """The source template and its widgets this copy lacks, or ``None``.

    Only the owner of a personal copy gets an offer; the template must still exist, be
    unlocked and assigned to a valid role of the viewer, and have a newer version.
    """
    row = dashboard_row
    if (
        row.kind != "personal"
        or row.owner_user_id != viewer
        or row.source_template_id is None
        or row.source_template_version is None
    ):
        return None
    template = await store._load_row(session, row.source_template_id)
    if (
        template is None
        or template.kind != "template"
        or template.locked
        or template.version <= row.source_template_version
    ):
        return None
    roles = set(await store.user_role_ids(session, viewer))
    if not roles or not (set(await store._role_ids_of(session, template.id)) & roles):
        return None
    copied = {
        wid
        for wid in (
            await session.execute(
                select(TiqoraDashboardWidget.template_widget_id).where(
                    TiqoraDashboardWidget.dashboard_id == row.id,
                    TiqoraDashboardWidget.template_widget_id.is_not(None),
                )
            )
        ).scalars()
    }
    new = [
        w
        for w in await store._widgets(session, template.id)
        if (w.added_in_version or 0) > row.source_template_version and w.id not in copied
    ]
    return (template, new) if new else None


async def update_hint(
    session: AsyncSession, dashboard_id: int, viewer: int
) -> TemplateUpdateHint | None:
    """The banner hint for ``GET /dashboards/{id}`` (never called on the data paths)."""
    row = await store._load_row(session, dashboard_id)
    if row is None or row.source_template_id is None:
        return None
    found = await pending_update(session, row, viewer)
    if found is None:
        return None
    template, new = found
    return TemplateUpdateHint(
        template_id=template.id,
        template_name=template.name,
        template_version=template.version,
        new_widget_count=len(new),
    )


async def _owned_row(
    session: AsyncSession, dashboard_id: int, viewer: int, *, admin: bool
) -> TiqoraDashboard:
    row = await store._visible_row(session, dashboard_id, viewer, admin=admin)
    if row.owner_user_id != viewer:
        raise store.DashboardNotFound(dashboard_id)
    return row


def _lg(w: TiqoraDashboardWidget) -> Rect:
    return WidgetLayouts.model_validate_json(w.layout).lg


async def preview_update(
    session: AsyncSession, dashboard_id: int, viewer: int, *, admin: bool
) -> TemplateUpdateOut:
    """The widgets a merge would add, one after another in the first free spots (``lg``)."""
    row = await _owned_row(session, dashboard_id, viewer, admin=admin)
    found = await pending_update(session, row, viewer)
    if found is None:
        raise store.DashboardNotFound(dashboard_id)
    template, new = found
    rects = [_lg(w) for w in await store._widgets(session, row.id)]
    out: list[WidgetOut] = []
    for w in new:
        size = _lg(w)
        x, y = first_free_spot(rects, size.w, size.h)
        placed = Rect(x=x, y=y, w=size.w, h=size.h)
        rects.append(placed)
        base = store._widget_out(w)
        out.append(
            base.model_copy(update={"id": uuid.uuid4().hex, "layout": WidgetLayouts(lg=placed)})
        )
    return TemplateUpdateOut(
        template_id=template.id,
        template_name=template.name,
        template_version=template.version,
        widgets=out,
    )


async def apply_update(
    session: AsyncSession,
    dashboard_id: int,
    viewer: int,
    action: Literal["merge", "dismiss"],
    version: int,
    *,
    admin: bool,
) -> DashboardOut:
    """Merge (append the new widgets) or dismiss; stale ``version`` is a 409.

    Retried on lock races; the caller commits.
    """
    return await store._retry_races(
        session, lambda: _apply_once(session, dashboard_id, viewer, action, version, admin=admin)
    )


async def _apply_once(
    session: AsyncSession,
    dashboard_id: int,
    viewer: int,
    action: Literal["merge", "dismiss"],
    version: int,
    *,
    admin: bool,
) -> DashboardOut:
    row = await _owned_row(session, dashboard_id, viewer, admin=admin)
    current = await store._current_version(session, dashboard_id)  # locking read
    if current != version:
        raise store.DashboardConflict(current)
    found = await pending_update(session, row, viewer)  # never trust the preview
    if found is None:
        raise store.DashboardNotFound(dashboard_id)
    template, new = found
    if action == "merge":
        existing = await store._widgets(session, row.id)
        if len(existing) + len(new) > MAX_WIDGETS_PER_DASHBOARD:
            raise store.DashboardInvalid(MSG_TOO_MANY, "widgets")
    res = await session.execute(
        update(TiqoraDashboard)
        .where(TiqoraDashboard.id == dashboard_id, TiqoraDashboard.version == version)
        .values(
            source_template_version=template.version,
            version=TiqoraDashboard.version + 1,
            changed_at=utc_now(),
        )
    )
    if res.rowcount == 0:  # type: ignore[attr-defined]
        raise store.DashboardConflict(await store._current_version(session, dashboard_id))
    if action == "merge":
        top = (
            await session.execute(
                select(func.max(TiqoraDashboardWidget.position)).where(
                    TiqoraDashboardWidget.dashboard_id == dashboard_id
                )
            )
        ).scalar_one_or_none()
        rects = [_lg(w) for w in existing]
        for pos, w in enumerate(new, start=0 if top is None else int(top) + 1):
            size = _lg(w)
            x, y = first_free_spot(rects, size.w, size.h)
            placed = Rect(x=x, y=y, w=size.w, h=size.h)
            rects.append(placed)
            session.add(
                TiqoraDashboardWidget(
                    id=uuid.uuid4().hex,
                    dashboard_id=dashboard_id,
                    type_key=w.type_key,
                    type_version=w.type_version,
                    title_override=w.title_override,
                    settings=w.settings,
                    layout=store._layout_text(WidgetLayouts(lg=placed)),
                    position=pos,
                    pinned=bool(w.pinned),
                    template_widget_id=w.id,
                )
            )
    await session.flush()
    fresh = await store._load_row(session, dashboard_id)
    assert fresh is not None
    return await store.dashboard_out(session, fresh, viewer, admin=admin)
